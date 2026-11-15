"""جامعو الأقسام: لكل قسم كود مستقل يجمع معلوماته بنفسه ويسلّم ملفه للنموذج.

قرار Abdu (15 نوفمبر 2026): لا كود واحد يتولى كل شيء.
- ستة جامعين للتقرير الاقتصادي: النمو، والتضخم، وسوق العمل، والفيدرالي والفائدة، والأوضاع المالية (كل منهم يجلب
  سلاسل قسمه من FRED بنفسه)، والأخبار والتقويم (من news.json الذي تجمعه حلقة الأخبار كل 10 دقائق).
- يعملون **قبل موعد التقرير**: تشغيل الجمع 08:40 بتوقيت نيويورك يحفظ ملفات الأقسام في data/sections، وتشغيل
  التقرير 09:00 يتحقق من جاهزيتها ثم يسلّمها للنموذج. إن لم تكن جاهزة يجمعها تشغيل التقرير نفسه قبل الاستدعاء.
- الجامع السابع لـMarket Gates معكوس الترتيب: ملفه (أرقام مشتقة فقط، بلا مستويات المؤشرات) يُسلَّم للنموذج **بعد**
  اكتمال التقرير الاقتصادي، في استدعاء ثانٍ يكتب انحياز NQ وS&P وDow من التقرير المكتمل.
- العزل: جامع يتعطل يُعلَّم قسمه «غير متاح» مع السبب، وتكمل الأقسام الأخرى.

ملفات data/sections عامة (المستودع عام): أرقام FRED ومشتقات فقط. لا نص عناوين الأخبار ولا مستويات Nasdaq وS&P.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from macro_platform import changes, releases
from macro_platform.catalog import CATEGORIES, INDICATORS

NY = ZoneInfo("America/New_York")
ECONOMY = ("growth", "inflation", "labor", "fed_rates", "financial")
NEWS, MARKETS = "news", "markets"
ALL = ECONOMY + (MARKETS,)
TITLES = {**{k: CATEGORIES[k][0] for k in ECONOMY}, NEWS: "الأخبار والتقويم", MARKETS: "Market Gates"}
# إشارات المنصة بحسب القسم الذي تنتمي سلسلتها إليه؛ «المربع» يجمع ثلاثة أقسام فيُسلَّم مستقلاً (cross_section)
SIGNAL_SECTION = {"sahm": "labor", "curve": "fed_rates", "real10": "fed_rates", "breakeven": "fed_rates",
                  "hy": "financial", "vix": "financial"}
COLLECT_FROM = "08:30"          # ملفات جُمعت بعد إصدارات 08:30 نيويورك تُعد جاهزة لتقرير اليوم
TAIL = 8
ISO = "%Y-%m-%dT%H:%M:%SZ"


def section_indicators(key: str) -> tuple:
    return tuple(i for i in INDICATORS if i.category == key)


def compact(i: dict, tail: int = TAIL) -> dict:
    if i.get("state") == "error" and "last" not in i:
        return {"id": i["id"], "name": i.get("name_en"), "state": "missing", "issues": i.get("issues")}
    return ({k: i.get(k) for k in ("id", "name_en", "freq", "transform", "unit", "last", "prev", "change", "z5",
                                   "pct5", "as_of", "age_days", "state", "issues", "source_as_of", "unavailable",
                                   "momentum", "revisions")}
            | {"recent": (i.get("points") or [])[-tail:]})


def brief(key: str, entries: list[dict], signals: list[dict], calendar: list | None, previous: dict | None,
          now: datetime) -> dict:
    """ملف قسم واحد للنموذج، من مؤشرات القسم وحده: قيمها وأعمارها وجودتها، وإشاراته، وما صدر اليوم ولم يصل،
    وما تغيّر منذ التقرير السابق، ومراجعاته. حقائق يحسبها الكود؛ النموذج يفسّرها."""
    entries = [e for e in entries if not e.get("internal_only")]
    revs = sorted(({"id": e["id"], **r} for e in entries for r in e.get("revisions") or []),
                  key=lambda r: r.get("detected_utc", ""))[-20:]
    quality = [f"{e['id']}: {x}" for e in entries for x in e.get("issues") or []]
    quality += [f"{e['id']}: {e['unavailable'].get('reason')}" for e in entries if isinstance(e.get("unavailable"), dict)]
    return {"section": key, "title": TITLES[key], "collected_at": now.strftime(ISO), "ok": True,
            "available": sum(1 for e in entries if "last" in e), "total": len(entries),
            "indicators": [compact(e) for e in entries],
            "signals": [s for s in signals or [] if SIGNAL_SECTION.get(s.get("key")) == key],
            "released_today_not_in_platform_data": releases.not_in_platform(calendar, entries, now),
            "changes_since_previous_report": changes.diff(previous, changes.snapshot({"indicators": entries}), revs),
            "revisions_recent": revs, "data_quality": quality}


def failed(key: str, now: datetime, exc: BaseException) -> dict:
    return {"section": key, "title": TITLES[key], "collected_at": now.strftime(ISO), "ok": False,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def news_brief(feed: dict | None, now: datetime) -> dict:
    """جامع الأخبار والتقويم: يقرأ ما جمعته حلقة الأخبار (news.json المنشور)، مع حداثة كل مسار."""
    if not isinstance(feed, dict):
        return failed(NEWS, now, RuntimeError("news.json could not be read"))
    return {"section": NEWS, "title": TITLES[NEWS], "collected_at": now.strftime(ISO), "ok": True,
            "headlines_current": feed.get("headlines_current"), "calendar_current": feed.get("calendar_current"),
            "feed": feed}


def markets_brief(gates: list[dict], relative: dict | None, cfg: dict, now: datetime) -> dict:
    """جامع Market Gates: أرقام مشتقة فقط (الأداء النسبي، وحدود NQ الحتمية، وبوابة القيادة). لا مستويات
    للمؤشرين (ترخيص طرف ثالث). يُسلَّم للنموذج بعد اكتمال التقرير الاقتصادي، لا معه."""
    return {"section": MARKETS, "title": TITLES[MARKETS], "collected_at": now.strftime(ISO), "ok": True,
            "relative_performance": relative, "nq_limits": gates, "limit_levels": cfg.get("gates"),
            "note": "Derived numbers only; equity index levels are licensed for internal use and are not provided."}


def from_payload(payload: dict, feed: dict | None, previous: dict | None, now: datetime) -> dict:
    """الطريق نفسه حين تكون الحمولة مبنية كاملة (اختبارات، وتشغيل يدوي بلا جمع مسبق): كل قسم في عزله."""
    out = {}
    for key in ECONOMY:
        try:
            out[key] = brief(key, [i for i in payload["indicators"] if i.get("category") == key],
                             payload.get("signals"), (feed or {}).get("calendar"), previous, now)
        except Exception as exc:
            out[key] = failed(key, now, exc)
    out[NEWS] = news_brief(feed, now) if feed is not None else failed(NEWS, now, RuntimeError("no news feed"))
    try:
        out[MARKETS] = markets_brief(payload.get("gates") or [], payload.get("market_relative"), payload.get("config", {}), now)
    except Exception as exc:
        out[MARKETS] = failed(MARKETS, now, exc)
    return out


# ---------------------------------------------------------------- storage and readiness (data/sections)
def manifest(briefs: dict, now: datetime, trigger: str, run_id: str | None) -> dict:
    return {"collected_at": now.strftime(ISO), "ny_date": now.astimezone(NY).strftime("%Y-%m-%d"),
            "trigger": trigger, "run_id": run_id,
            "sections": {k: {"ok": bool(b.get("ok")), "collected_at": b.get("collected_at"),
                             "available": b.get("available"), "total": b.get("total"), "error": b.get("error")}
                         for k, b in briefs.items() if k != NEWS}}


def save(folder: Path, briefs: dict, man: dict) -> None:
    """يحفظ ملفات الأقسام الاقتصادية وملف Market Gates والفهرس. ملف الأخبار لا يُحفظ (نص العناوين لا يدخل المستودع)."""
    folder.mkdir(parents=True, exist_ok=True)
    for k, b in briefs.items():
        if k in ALL:
            (folder / f"{k}.json").write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")
    (folder / "index.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")


def load(folder: Path) -> tuple[dict | None, dict]:
    def read(p: Path):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    man = read(folder / "index.json")
    briefs = {k: b for k in ALL if isinstance(b := read(folder / f"{k}.json"), dict)}
    return (man if isinstance(man, dict) else None), briefs


def _ny_minutes(iso: str | None) -> tuple[str | None, int | None]:
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(NY)
        return t.strftime("%Y-%m-%d"), t.hour * 60 + t.minute
    except Exception:
        return None, None


def fresh(man: dict | None, briefs: dict, now: datetime) -> tuple[bool, str]:
    """جاهزة لتقرير اليوم: جُمعت اليوم بعد إصدارات 08:30 نيويورك وقبل الآن، وكل الأقسام الاقتصادية سليمة."""
    if not man:
        return False, "no pre-report collection found"
    day, mins = _ny_minutes(man.get("collected_at"))
    today = now.astimezone(NY).strftime("%Y-%m-%d")
    h, m = COLLECT_FROM.split(":")
    if day != today or mins is None or mins < int(h) * 60 + int(m):
        return False, f"last collection {man.get('collected_at')} is not from today after {COLLECT_FROM} New York"
    if man.get("collected_at", "") > now.strftime(ISO):
        return False, "collection time is after now"
    bad = [k for k in ECONOMY if not (briefs.get(k) or {}).get("ok")]
    if bad:
        return False, f"sections not ready: {', '.join(bad)}"
    return True, f"collected {man.get('collected_at')} ({man.get('trigger')})"


def readiness(briefs: dict, source: str, started: datetime) -> dict:
    """ما يُسجَّل في meta التقرير: لكل قسم وقت جمعه وسلامته، وأن الجمع سبق استدعاء النموذج."""
    out = {"source": source, "model_call_started_at": started.strftime(ISO), "sections": {}}
    for k in ECONOMY + (NEWS, MARKETS):
        b = briefs.get(k) or {}
        out["sections"][k] = {"ok": bool(b.get("ok")), "collected_at": b.get("collected_at"),
                              "before_model_call": bool(b.get("collected_at")) and b["collected_at"] <= started.strftime(ISO),
                              "error": b.get("error"), "available": b.get("available"), "total": b.get("total")}
    return out


def economy_input(briefs: dict, cross: dict, previous_report: dict | None, generated: str) -> dict:
    """ما يستلمه النموذج للتقرير الاقتصادي: ملف لكل قسم، بلا ملف Market Gates."""
    return {"generated": generated,
            "sections": {k: briefs.get(k) or {"section": k, "ok": False, "error": "missing"} for k in ECONOMY + (NEWS,)},
            "cross_section": cross, "previous_report": previous_report}


def cross_section(payload: dict) -> dict:
    """تصنيفات تجمع أكثر من قسم (يحسبها الكود): مربع النمو × التضخم، وآخر قراءات Sahm."""
    return {"quadrant": payload.get("quadrant"), "sahm_recent": ((payload.get("sahm") or {}).get("points") or [])[-6:]}


def page_view(man: dict | None) -> dict | None:
    """ما تعرضه الصفحة عن آخر جمع: الوقت والمصدر وحالة كل قسم، بلا محتوى الملفات."""
    return man if isinstance(man, dict) else None


__all__ = ["ECONOMY", "NEWS", "MARKETS", "ALL", "brief", "failed", "news_brief", "markets_brief", "from_payload",
           "manifest", "save", "load", "fresh", "readiness", "economy_input", "cross_section", "section_indicators"]
