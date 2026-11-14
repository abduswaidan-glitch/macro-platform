"""تقرير Claude اليومي (الصيغة 2): تقييم اقتصادي يُكتب أولاً، ثم ترجمة سوقية قصيرة مشتقة منه، في استدعاء واحد.

الجدولة (قرار Abdu، 14 نوفمبر 2026): تقرير واحد لكل يوم تداول. المحاولة 1 عند 09:00 بتوقيت نيويورك؛ إن نجحت فلا
تقرير آخر ذلك اليوم. إن فشلت فشلاً تقنياً (لا تقرير صالح للنشر) فمحاولة 2 واحدة عند 12:00، ثم توقف. لا محاولة تبدأ
بعد 13:00. أي محاولة ثالثة يدوية فقط وبإذن Abdu (report_attempt=manual).

دورة الأخبار (news.yml) تقرر بـdispatch_decision وتطلب update.yml بـreport_attempt=1|2. تشغيلات cron في update.yml
تحدّث البيانات فقط ولا تكتب تقريراً. لا Cross-model fallback: فشل النموذج نفسه فشل محاولة على النموذج نفسه.
يحتاج السر ANTHROPIC_API_KEY في GitHub.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
MARKET_IDS = ("NASDAQ100", "SP500")      # مؤشرات الأسهم: للترجمة السوقية فقط، لا تدخل التقييم الاقتصادي
DEFAULT_SCHEDULE = {"attempt_1": "09:00", "attempt_2": "12:00", "last_start": "13:00", "tz": "America/New_York"}
SCHEDULED = ("1", "2")
OPEN_MIN = 9 * 60 + 30                    # افتتاح نيويورك
CHECK_SERIES = ("DGS2", "DGS10", "DFII10", "T10YIE", "T10Y2Y", "BAMLH0A0HYM2", "DTWEXBGS", "DCOILWTICO", "VIXCLS")

SYSTEM = """You write the daily US-macro report for an Arabic-language macro dashboard. It is an economic assessment first. A short market translation follows it and is derived from it.

Order of work (strict):
1. Assess the US economy from "economy_inputs" and your research: growth, inflation, labor, the Fed and rates, financial conditions. "economy_inputs" deliberately holds no equity index. Equity moves do not redefine the state of the economy.
2. Only after the economic assessment is complete, translate it for equities using "market_inputs", which is for the market translation only. The gates are derived from "economy"; they never change it.

Data rules:
- The platform data (source: FRED) is the only source for numbers about indicators. Never invent a number. If you cite a market estimate you found on the web (consensus, FedWatch, nowcasts), label it as an estimate and link it.
- Every indicator carries "as_of" and "age_days". Monthly data lags; say how old a number is when it matters.
- Use web search for today's events: the US data calendar, Fed speakers and decisions, Treasury auctions, trade/tariff and political headlines, geopolitics. Cite a URL for each event.
- Keep five kinds of statement apart: platform data; official releases you checked, with the official URL; reported news (the "news_feed" block: media headlines; official Fed/BEA titles are announcements you open before citing their content), which you call reported; your hypotheses; and qualitative market implications. Never state reported news as an official fact.
- "released_today_not_in_platform_data" lists official releases that came out today by the calendar while the platform's series have not been updated yet (FRED often updates 10 minutes to 2 hours after an 08:30 release). Take those figures from the official release through web search, with its URL, and label them "official release checked via web", never platform data. If you cannot find the release, say so.
- changes_since_previous_report, revisions_recent, previous_report and each indicator's momentum (MoM and 3-month annualized) are computed by the platform's code: they say what changed, what was revised and what the previous report said. Decide which of these matter economically and say so in "what_changed"; do not recompute or guess them, and say when there is no previous snapshot.
- For a headline that matters, reason about it: is it new, is a primary source confirming it, how large and from when, through which channel it reaches the economy (rates, inflation, growth, earnings, financial conditions), over what horizon, and what would contradict it. Say what is unknown.

The economic assessment:
- dominant_driver: the driver that matters most today, whether a number or an event (an FOMC decision, a Powell speech, tariffs, a financial event, a government shutdown, a geopolitical development). Do not prefer a numeric driver because it is easier to cite. "No single dominant driver today" is a valid driver. "kind" is "data", "event" or "mixed": "data" needs at least one indicator id from economy_inputs in "evidence"; "event" needs "event_ids" that point to entries in "events" with a URL ("evidence" may be []); "mixed" needs both.
- key_conflicts: 0 to 3 genuine conflicts between inputs, with the same kind/evidence/event_ids rules. [] is valid; never invent a conflict.
- Scenario probabilities are your subjective weights, not yet calibrated, and no single headline sets them. The platform records them and scores them against what happens, so give the probability you actually hold, from the evidence, not a habitual number; close probabilities are fine when the picture is mixed. The three scenario probabilities are whole numbers that sum to exactly 100. Give no other numeric probability unless it comes from the platform data or a cited market source, labelled as such.
- Each scenario carries one check: a near-term market signature linked to the scenario, not proof that the whole economic scenario happened. The three checks use the same series (one of DGS2, DGS10, DFII10, T10YIE, T10Y2Y, BAMLH0A0HYM2, DTWEXBGS, DCOILWTICO, VIXCLS) and the same date "by" (a trading day - not a weekend or NYSE holiday - within 21 days after today), and their ranges cover every value with no gap or overlap: one range has low null, one has high null, and each high equals the next low (a value equal to a bound belongs to the higher range). A scenario's prob is then your probability that the series' value on that date falls in its range. Choose the series and bounds that best separate your scenarios, starting from the latest values in the data.
- conviction is one descriptive sentence, with no number.

The market translation (after the economy, derived from it):
- impact is a whole number from -2 to +2 for the coming sessions: -2 strongly negative, -1 negative, 0 balanced, +1 positive, +2 strongly positive. There is no "uncertain" level; when the picture is balanced, use 0 and say why.
- Nasdaq-100 (nq): real yields, the 2Y yield, Fed expectations, the dollar, valuation and duration sensitivity, growth, financial conditions, earnings concentration.
- S&P 500 (spx): the balance of growth, the earnings backdrop, rates, credit, financial conditions, risk appetite.
- Dow (dow): the industrial cycle, manufacturing, cyclicals, financials (curve steepness), domestic growth, yields, the dollar. Energy has a small weight in the Dow. If there is no real macro difference from the S&P 500 today, say that the effect is largely similar to the S&P 500, and do not invent a difference.
- nq_vs_spx_10d.prob: your probability, a whole number from 0 to 100, that the Nasdaq-100 outperforms the S&P 500 over the next 10 trading days, measured from the last close before this report (from today's close for a report written after the open). 50 means no view and is valid. The platform scores it apart from the economic scenarios; it does not measure the economic analysis.
- No entries, stops, targets, or buy/sell recommendations, anywhere.

Write everything in Modern Standard Arabic (فصحى), keeping technical terms in English (CPI, PCE, Fed, FOMC, yield, bp, VIX, NQ, ...).

Output: after any research, end your answer with exactly one JSON object between <report> and </report>, no markdown fences, with "economy" first, then "market_translation", then "events":
{
  "economy": {
    "executive": {"headline": "one-line headline of today's economic read", "read": "2-4 sentences", "direction": "improving" | "stable" | "weakening" | "mixed", "biggest_change": "..."},
    "regime": {"growth": "...", "inflation": "...", "labor": "...", "fed_rates": "...", "financial": "...", "label": "a short regime name"},
    "dominant_driver": {"driver": "...", "kind": "data" | "event" | "mixed", "evidence": ["DFII10"], "event_ids": ["e1"]},
    "key_conflicts": [{"text": "...", "kind": "data" | "event" | "mixed", "evidence": [], "event_ids": []}],
    "what_changed": ["the economic meaning of what the platform computed as changed"],
    "sections": {"growth": "a paragraph", "inflation": "...", "labor": "...", "fed_rates": "...", "financial_conditions": "..."},
    "news": [{"item": "...", "effect": "...", "changes_picture": true, "event_ids": ["e2"]}],
    "data_uncertainty": ["stale, conflicting or estimate-based inputs and how you treated them"],
    "scenarios": [{"label": "A", "name": "...", "prob": integer, "horizon": "...", "thesis": "...", "evidence": ["..."],
                   "confirm": "...", "invalidate": "...", "moves_probability": "what would move this probability",
                   "check": {"series": "DGS2", "by": "YYYY-MM-DD", "low": null, "high": 4.1}}, B, C],
    "conviction": "one descriptive sentence",
    "bottom_line": "the closing synthesis"
  },
  "market_translation": {
    "gates": {"nq": {"impact": integer, "drivers": ["..."], "positive": ["..."], "negative": ["..."], "matters_now": "...", "would_flip": "...", "horizon": "..."},
              "spx": {same keys}, "dow": {same keys}},
    "nq_vs_spx_10d": {"prob": integer}
  },
  "events": [{"id": "e1", "title": "...", "date": "YYYY-MM-DD", "url": "https://..."}]
}"""

SECTION_AR = {"growth": "النمو", "inflation": "التضخم", "labor": "سوق العمل", "fed_rates": "الفيدرالي والفائدة",
              "financial_conditions": "الأوضاع المالية"}
SECONDARY = ("regime", "dominant_driver", "key_conflicts", "what_changed", "sections", "news", "data_uncertainty",
             "scenarios", "conviction")
GATES = ("nq", "spx", "dow")


class ReportError(RuntimeError):
    """فشل تقني يمنع نشر التقرير: قابل لإعادة المحاولة عند 12:00 (البند 4.6). يحمل الاستخدام إن عُرف."""

    def __init__(self, reason: str, usage: dict | None = None, info: dict | None = None):
        super().__init__(reason)
        self.usage, self.info = usage, info or {}


def analyst_notes(path: str | Path = "prompts/analyst.md") -> str:
    p = Path(path)
    if not p.exists():
        return ""
    return re.sub(r"<!--.*?-->", "", p.read_text(encoding="utf-8"), flags=re.S).strip()


# ---------------------------------------------------------------- trading days and the schedule
def is_trading_day(ny_day: datetime, cfg: dict) -> bool:
    """يوم عمل غير مدرج في ai.market_holidays (قائمة يدوية في config.json)."""
    return ny_day.weekday() < 5 and ny_day.strftime("%Y-%m-%d") not in set(cfg.get("market_holidays", []))


def _minutes(hhmm: str) -> int:
    h, m = str(hhmm).split(":")
    return int(h) * 60 + int(m)


def _ny_date(iso: str | None) -> str | None:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(NY).strftime("%Y-%m-%d")
    except Exception:
        return None


def schedule(cfg: dict) -> dict:
    return {**DEFAULT_SCHEDULE, **(cfg.get("schedule") or {})}


def scheduled_for(attempt: str, cfg: dict) -> str | None:
    s = schedule(cfg)
    return f"{s['attempt_' + attempt]} {s['tz']}" if attempt in SCHEDULED else None


ACTIVE_RUN = {"queued", "in_progress", "waiting", "requested", "pending"}
ATTEMPT_TAG = re.compile(r"report-attempt-(1|2|manual)\b")


def attempts_today(state: dict, runs: list[dict], today: str) -> dict[str, dict]:
    """المحاولات المجدولة لليوم: من سجل state.json أولاً، ثم من تشغيلات update.yml التي انتهت بلا تسجيل
    (مثلاً مات الـjob قبل الحفظ): تُعد محاولة فاشلة قابلة لإعادة المحاولة."""
    out: dict[str, dict] = {}
    for a in (state or {}).get("ai_attempts") or []:
        if isinstance(a, dict) and a.get("ny_date") == today and str(a.get("attempt")) in SCHEDULED:
            out[str(a["attempt"])] = a
    for r in runs or []:
        m = ATTEMPT_TAG.search(str(r.get("displayTitle") or ""))
        if not m or m.group(1) not in SCHEDULED or _ny_date(r.get("createdAt")) != today:
            continue
        if r.get("status") == "completed" and m.group(1) not in out:
            out[m.group(1)] = {"attempt": m.group(1), "ny_date": today, "ok": False, "failure_class": "retryable",
                               "reason": f"run ended ({r.get('conclusion') or 'unknown'}) without recording the attempt"}
    return out


def dispatch_decision(now_utc: datetime, state: dict, runs: list[dict], cfg: dict) -> tuple[int | None, str]:
    """هل تطلب دورة الأخبار تشغيل update.yml بمحاولة تقرير الآن؟ يعيد (1 أو 2 أو None، السبب).
    runs: مخرج `gh run list --json status,event,createdAt,displayTitle,conclusion`؛ state: data/state.json."""
    s = schedule(cfg)
    ny = now_utc.astimezone(ZoneInfo(s["tz"]))
    today, hm = ny.strftime("%Y-%m-%d"), f"{ny:%H:%M}"
    if not is_trading_day(ny, cfg):
        return None, f"not a trading day ({today})"
    m = ny.hour * 60 + ny.minute
    if m < _minutes(s["attempt_1"]):
        return None, f"before {s['attempt_1']} New York ({hm})"
    if any(r.get("status") in ACTIVE_RUN for r in runs or []):
        return None, "an update run is already queued or running"
    att = attempts_today(state, runs, today)
    if any(a.get("ok") for a in att.values()) or (state or {}).get("ai_last_date") == today:
        return None, f"today's report is already published ({today})"
    if m >= _minutes(s["last_start"]):
        return None, f"no attempt starts after {s['last_start']} New York ({hm})"
    if "1" not in att:
        return 1, f"attempt 1 at {hm} New York"
    if "2" in att:
        return None, "both scheduled attempts are used; a third attempt is manual only"
    why = att["1"].get("reason") or "failed"
    if m < _minutes(s["attempt_2"]):
        return None, f"attempt 1 failed ({why}); retry at {s['attempt_2']} New York"
    return 2, f"attempt 2 at {hm} New York after attempt 1 failed ({why})"


def attempt_allowed(attempt: str, now_utc: datetime, state: dict, cfg: dict) -> tuple[bool, str]:
    """حارس داخل update.yml نفسه: محاولة مجدولة تُكتب فقط في يوم تداول، ولم يُنشر تقرير اليوم، ولم تُسجَّل
    المحاولة نفسها بعد. manual يُكتب دائماً (بإذن Abdu) ولا يدخل سجل التوقعات."""
    if attempt == "manual":
        return True, "manual"
    if attempt not in SCHEDULED:
        return False, "no report attempt requested (data update only)"
    ny = now_utc.astimezone(NY)
    today = ny.strftime("%Y-%m-%d")
    if not is_trading_day(ny, cfg):
        return False, f"not a trading day ({today})"
    att = attempts_today(state, [], today)
    if any(a.get("ok") for a in att.values()) or (state or {}).get("ai_last_date") == today:
        return False, f"today's report is already published ({today})"
    if attempt in att:
        return False, f"attempt {attempt} is already recorded for {today}"
    return True, f"attempt {attempt}"


def status_view(state: dict, now_utc: datetime, cfg: dict) -> dict:
    """ما تعرضه الصفحة عن محاولات اليوم (من state.json): أوقات وأسباب، بلا نص من التقرير."""
    today = now_utc.astimezone(NY).strftime("%Y-%m-%d")
    keep = ("attempt", "scheduled_for", "started_at", "finished_at", "ok", "failure_class", "reason", "warnings")
    att = [{k: a.get(k) for k in keep} for a in (state or {}).get("ai_attempts") or []
           if isinstance(a, dict) and a.get("ny_date") == today]
    return {"ny_date": today, "attempts": att, "schedule": schedule(cfg)}


# ---------------------------------------------------------------- what the model reads
def _compact(i: dict, tail: int) -> dict:
    if i.get("state") == "error" and "last" not in i:
        return {"id": i["id"], "name": i["name_en"], "state": "missing"}
    return ({k: i.get(k) for k in ("id", "name_en", "category", "freq", "transform", "unit", "last", "prev",
                                   "change", "z5", "pct5", "as_of", "age_days", "state", "issues",
                                   "source_as_of", "unavailable", "momentum", "revisions")}
            | {"recent": i.get("points", [])[-tail:]})


def context(payload: dict, tail: int = 8, feed: dict | None = None, extra: dict | None = None,
            now_utc: datetime | None = None) -> dict:
    """نسخة مضغوطة من data.json بقسمين منفصلين: economy_inputs بلا أي مؤشر أسهم، وmarket_inputs للترجمة فقط."""
    from macro_platform import releases
    extra = dict(extra or {})
    inds = payload["indicators"]
    ch = extra.pop("changes_since_previous_report", None)
    eco_ch, mkt_ch = ch, None
    if isinstance(ch, dict) and isinstance(ch.get("items"), list):
        eco_ch = {**ch, "items": [x for x in ch["items"] if x.get("id") not in MARKET_IDS]}
        mkt_ch = [x for x in ch["items"] if x.get("id") in MARKET_IDS]
    revs = [r for r in extra.pop("revisions_recent", []) or [] if r.get("id") not in MARKET_IDS]
    economy = {
        "kpis": [k for k in payload["kpis"] if k.get("id") not in MARKET_IDS],
        "signals": payload["signals"], "quadrant": payload["quadrant"], "sahm_recent": payload["sahm"]["points"][-6:],
        "indicators": [_compact(i, tail) for i in inds if i["id"] not in MARKET_IDS],
        "data_warnings": [w for w in payload["status"]["warnings"] if not any(m in w for m in MARKET_IDS)],
        "released_today_not_in_platform_data": releases.not_in_platform(
            (feed or {}).get("calendar"), inds, now_utc or datetime.now(timezone.utc)),
        **({"news_feed": feed} if feed else {}),     # عناوين ومواعيد من مصادر عامة، منفصلة عن بيانات المنصة
        "changes_since_previous_report": eco_ch, "revisions_recent": revs, **extra}
    market = {"note": "For the market translation only, after the economic assessment. Equity index levels and "
                      "relative performance never redefine the state of the economy.",
              "indicators": [_compact(i, tail) for i in inds if i["id"] in MARKET_IDS],
              "relative_performance": payload.get("market_relative"),
              "nq_gates": payload["gates"], "gate_levels": payload["config"]["gates"],
              "changes_since_previous_report": mkt_ch}
    return {"generated": payload["generated"], "economy_inputs": economy, "market_inputs": market}


# ---------------------------------------------------------------- parsing: retry only on what blocks publication
def _txt(x) -> bool:
    return isinstance(x, str) and bool(x.strip())


def _int_in(x, lo: int, hi: int) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and lo <= x <= hi


def _check_support(name: str, d, known: set[str], events: dict, warn: list[str]) -> None:
    """kind/evidence/event_ids لمحرك أو تعارض: المعرّفات غير الموجودة تُحذف وتُسجَّل، والمخالفة تحذير لا إعادة."""
    if not isinstance(d, dict):
        return
    ev = d.get("evidence") if isinstance(d.get("evidence"), list) else []
    bad = [x for x in ev if x not in known]
    if bad:
        warn.append(f"{name}: unknown evidence ids removed: {', '.join(map(str, bad))}")
        d["evidence"] = [x for x in ev if x in known]
    ids = d.get("event_ids") if isinstance(d.get("event_ids"), list) else []
    bad_e = [x for x in ids if x not in events]
    if bad_e:
        warn.append(f"{name}: event_ids without a sourced event: {', '.join(map(str, bad_e))}")
    kind = d.get("kind")
    has_data, has_event = bool(d.get("evidence")), any(x in events for x in ids)
    need = {"data": has_data, "event": has_event, "mixed": has_data and has_event}
    if kind not in need:
        warn.append(f"{name}: kind must be data, event or mixed")
    elif not need[kind]:
        warn.append(f"{name}: kind {kind} without the evidence it requires")


def parse_report(text: str, known_ids: set[str] | None = None) -> dict:
    """يرفض (فشل قابل لإعادة المحاولة) فقط ما يمنع النشر: لا <report>، أو JSON مكسور، أو غياب economy أو
    executive.read أو bottom_line. كل ما عداه يُنشر كما كتبه النموذج مع تحذير في rep["warnings"]: لا تعديل صامت."""
    m = re.findall(r"<report>\s*(\{.*?\})\s*</report>", text or "", flags=re.S)
    if not m:
        raise ReportError("no <report> block in the model output")
    try:
        rep = json.loads(m[-1])
    except json.JSONDecodeError as exc:
        raise ReportError(f"broken JSON in <report>: {exc.msg} at {exc.pos}")
    if not isinstance(rep, dict) or not isinstance(rep.get("economy"), dict):
        raise ReportError("the report has no economy object")
    eco = rep["economy"]
    exe = eco.get("executive") if isinstance(eco.get("executive"), dict) else {}
    if not _txt(exe.get("read")) or not _txt(eco.get("bottom_line")):
        raise ReportError("economy is missing executive.read or bottom_line")
    warn: list[str] = []
    for k in SECONDARY:
        if k not in eco:
            warn.append(f"missing field: economy.{k}")
    known = set(known_ids or ())
    events = {e.get("id"): e for e in rep.get("events") or [] if isinstance(e, dict) and e.get("id")
              and re.match(r"https?://", str(e.get("url") or ""))}
    if known_ids is not None:
        _check_support("dominant_driver", eco.get("dominant_driver"), known, events, warn)
        conflicts = eco.get("key_conflicts") if isinstance(eco.get("key_conflicts"), list) else []
        for k, c in enumerate(conflicts):
            _check_support(f"key_conflicts[{k}]", c, known, events, warn)
    scen = eco.get("scenarios") if isinstance(eco.get("scenarios"), list) else []
    from macro_platform.ledger import probs_issue
    why = probs_issue(scen)
    if why and "scenarios" in eco:
        warn.append(f"scenarios: {why}")
    mt = rep.get("market_translation")
    if not isinstance(mt, dict):
        warn.append("market_translation is missing: the market gates are unavailable today")
    else:
        gates = mt.get("gates") if isinstance(mt.get("gates"), dict) else {}
        for g in GATES:
            if not isinstance(gates.get(g), dict):
                warn.append(f"market gate {g} is missing")
            elif not _int_in(gates[g].get("impact"), -2, 2):
                warn.append(f"market gate {g}: impact must be a whole number from -2 to +2")
        tr = mt.get("nq_vs_spx_10d") if isinstance(mt.get("nq_vs_spx_10d"), dict) else {}
        if not _int_in(tr.get("prob"), 0, 100):
            warn.append("nq_vs_spx_10d: prob must be a whole number from 0 to 100")
    # حقول توافق مشتقة: لو تراجعنا عن هذا التغيير تعرض الواجهة القديمة التقرير الجديد مختصراً لا مكسوراً
    rep["title"] = exe.get("headline") if _txt(exe.get("headline")) else exe["read"]
    rep["summary"] = [x for x in (exe.get("read"), exe.get("biggest_change")) if _txt(x)]
    rep["scenarios"] = [{**s, "trigger": s.get("confirm"), "reason": s.get("thesis")} if isinstance(s, dict) else s
                        for s in scen]
    rep["sections"] = [{"heading": SECTION_AR.get(k, k), "body": v} for k, v in (eco.get("sections") or {}).items()
                       if _txt(v)] if isinstance(eco.get("sections"), dict) else []
    rep["one_liner"] = eco["bottom_line"]
    rep.setdefault("events", [])
    rep["warnings"] = warn
    return rep


# ---------------------------------------------------------------- the model call
# أسعار Opus 5.5 لكل مليون رمز: إدخال، إخراج، كتابة cache لـ5 دقائق، قراءة cache؛ والبحث 10$ لكل 1000 عملية
PRICES = {"claude-opus-5-5": (4.0, 20.0, 5.0, 0.20)}
SEARCH_PRICE = 10.0 / 1000
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
MAX_TOKENS = 128000
PAUSE_LOOPS = 5


def estimate_cost(model: str, usage: dict) -> float | None:
    p = PRICES.get(model)
    if not p or not isinstance(usage, dict):
        return None
    tokens = sum((usage.get(k) or 0) * price for k, price in zip(USAGE_KEYS, p)) / 1e6
    return round(tokens + (usage.get("web_search_requests") or 0) * SEARCH_PRICE, 4)


def call_claude(user_text: str, system: str, model: str, effort: str):
    """Streaming (128000 رمز، فلا مهلة HTTP)، وeffort صريح، وبحث بحد 8، بلا fallbacks ولا betas.
    Prompt caching: نقطة صريحة على التعليمات الثابتة + caching تلقائي يتقدّم مع المحادثة.
    إعادات الـSDK الداخلية (max_retries الافتراضي) تبقى: إعادة اتصال قبل التوليد على النموذج نفسه.
    يعيد (النص، المصادر، الاستخدام، النموذج المخدِّم، معلومات: stop_reason والمدة وعدد عمليات البحث)."""
    import anthropic  # يُثبَّت في GitHub Actions فقط

    client = anthropic.Anthropic()
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    messages = [{"role": "user", "content": user_text}]
    texts, sources, served, stop = [], {}, model, None
    usage = {k: 0 for k in USAGE_KEYS} | {"web_search_requests": 0}
    t0 = datetime.now(timezone.utc)
    info = lambda: {"stop_reason": stop, "duration_s": round((datetime.now(timezone.utc) - t0).total_seconds(), 1)}
    for _ in range(PAUSE_LOOPS):  # pause_turn: أدوات الخادم قد توقف الدور مؤقتاً فنكمل
        with client.messages.stream(
            model=model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            system=system_blocks,
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}],
            messages=messages,
            cache_control={"type": "ephemeral"},
        ) as stream:
            msg = stream.get_final_message()
        served, stop = msg.model, msg.stop_reason
        for k in USAGE_KEYS:
            usage[k] += getattr(msg.usage, k, 0) or 0
        stu = getattr(msg.usage, "server_tool_use", None)
        usage["web_search_requests"] += (getattr(stu, "web_search_requests", 0) or 0) if stu else 0
        if stop == "refusal":
            raise ReportError("the model declined the request (refusal; no cross-model fallback)", usage, info())
        if stop == "max_tokens":
            raise ReportError(f"output reached max_tokens ({MAX_TOKENS}) before the report ended", usage, info())
        for b in msg.content:
            if b.type == "text":
                texts.append(b.text)
            elif b.type == "web_search_tool_result" and isinstance(b.content, list):
                for r in b.content:
                    url = getattr(r, "url", None)
                    if url:
                        sources[url] = getattr(r, "title", "") or url
        if stop != "pause_turn":
            break
        messages.append({"role": "assistant", "content": msg.content})
    else:
        raise ReportError(f"pause_turn did not end after {PAUSE_LOOPS} continuations", usage, info())
    return "".join(texts), [{"url": u, "title": t} for u, t in sources.items()], usage, served, info()


def feed_meta(feed: dict | None) -> dict | None:
    """أعداد وحالة مدخل الأخبار والتقويم في سجل التقرير، بلا نص العناوين (ai_history.json في المستودع العام)."""
    if not feed:
        return None
    st: dict[str, int] = {}
    for e in feed.get("calendar", []):
        st[e.get("status")] = st.get(e.get("status"), 0) + 1
    return {"headlines": len(feed.get("headlines", [])), "calendar": st,
            **{k: feed.get(k) for k in ("headlines_current", "calendar_current",
                                      "headlines_last_success_utc", "calendar_last_success_utc")}}


def previous_summary(previous: dict | None, ledger_view: dict | None) -> dict | None:
    """ما قاله التقرير السابق باختصار (أرقام وعناوين)، ونتيجة شروط قياسه إن حُسمت."""
    if not previous:
        return None
    m = previous.get("meta") or {}
    chk = (((ledger_view or {}).get("by_date") or {}).get(m.get("ny_date")) or {}).get("scenario_check") or {}
    return {"ny_date": m.get("ny_date"), "title": previous.get("title"),
            "scenarios": [{k: s.get(k) for k in ("label", "name", "prob")} for s in previous.get("scenarios") or []
                          if isinstance(s, dict)],
            "scenario_check_result": chk.get("check_result"), "scenario_check_unscorable": chk.get("unscorable")}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def write_report(payload: dict, cfg: dict, now_utc: datetime | None = None, caller=call_claude,
                 feed: dict | None = None, previous: dict | None = None, attempt: str = "manual",
                 clock=None) -> dict:
    clock = clock or _now
    now_utc = now_utc or clock()
    ai = cfg.get("ai", {})
    model = ai.get("model", "claude-opus-5-5")
    notes = analyst_notes()
    system = SYSTEM + ("\n\nThe analyst's standing instructions (Arabic):\n" + notes if notes else "")
    ny_now = now_utc.astimezone(NY)
    from macro_platform import changes                    # يحسب الكود ما تغيّر؛ النموذج يفسّر معناه
    snap = changes.snapshot(payload)
    recent_revs = payload.get("revisions") or []
    extra = {"changes_since_previous_report": changes.diff(previous, snap, recent_revs),
             "previous_report": previous_summary(previous, payload.get("ledger")),
             "revisions_recent": recent_revs[-20:]}
    user = (f"Today is {ny_now:%A %Y-%m-%d}, {ny_now:%H:%M} New York time. Platform data:\n"
            + json.dumps(context(payload, feed=feed, extra=extra, now_utc=now_utc), ensure_ascii=False))
    started = clock()
    res = caller(user, system, model, ai.get("effort", "xhigh"))
    text, sources, usage, served = res[:4]
    info = dict(res[4]) if len(res) > 4 and isinstance(res[4], dict) else {}
    info["model_served"] = served
    if served != model:                                   # لا fallback متوقع؛ وإن حدث فهو فشل لا تقرير بنموذج آخر
        raise ReportError(f"served by {served}, not {model}", usage, info)
    known = {i["id"] for i in payload["indicators"] if i["id"] not in MARKET_IDS}
    try:
        rep = parse_report(text, known)
    except ReportError as exc:
        exc.usage, exc.info = usage, info
        raise
    completed = clock()
    rep["sources"] = sources
    iso = "%Y-%m-%dT%H:%M:%SZ"
    ny_date = ny_now.strftime("%Y-%m-%d")
    done_ny = completed.astimezone(NY)
    pre_open = done_ny.strftime("%Y-%m-%d") == ny_date and done_ny.hour * 60 + done_ny.minute < OPEN_MIN
    ps = [x.get("prob") for x in rep["scenarios"] if isinstance(x, dict)]
    rep["meta"] = {"schema": 2, "written_at": now_utc.strftime(iso), "ny_date": ny_date,
                   "prob_sum": sum(p for p in ps if isinstance(p, (int, float)) and not isinstance(p, bool)),
                   "scenario_count": len(rep["scenarios"]), "inputs_snapshot": snap,
                   "model": served, "effort": ai.get("effort", "xhigh"), "usage": usage,
                   "stop_reason": info.get("stop_reason"), "duration_s": info.get("duration_s"),
                   "cost_estimate": estimate_cost(served, usage), "news_input": feed_meta(feed),
                   # بدء طلب النموذج واكتمال التقرير (حد المعلومات)؛ وقت النشر يسجّله update.yml في ملخص التشغيل
                   "started_at": started.strftime(iso), "completed_at": completed.strftime(iso),
                   "attempt": attempt, "scheduled_for": scheduled_for(attempt, ai),
                   "pre_open": pre_open, "delayed": attempt == "2" or not pre_open,
                   "trigger": "schedule" if attempt in SCHEDULED else "manual",
                   "run_id": os.environ.get("GITHUB_RUN_ID")}
    return rep


def attempt_record(attempt: str, cfg: dict, now_utc: datetime, started: datetime, finished: datetime,
                   rep: dict | None = None, exc: Exception | None = None) -> dict:
    """سطر سجل المحاولات في state.json (ai_attempts): أوقات وأسباب واستخدام وكلفة، بلا نص التقرير."""
    ai = cfg.get("ai", {})
    iso = "%Y-%m-%dT%H:%M:%SZ"
    m = (rep or {}).get("meta") or {}
    info = getattr(exc, "info", None) or {}
    usage = m.get("usage") if rep else getattr(exc, "usage", None)
    served = m.get("model") if rep else info.get("model_served")
    warns = len((rep or {}).get("warnings") or [])
    return {"ny_date": now_utc.astimezone(NY).strftime("%Y-%m-%d"), "attempt": attempt,
            "scheduled_for": scheduled_for(attempt, ai), "run_id": os.environ.get("GITHUB_RUN_ID"),
            "started_at": started.strftime(iso), "finished_at": finished.strftime(iso),
            "duration_s": round((finished - started).total_seconds(), 1),
            "ok": rep is not None,
            "failure_class": ("published_with_warnings" if warns else None) if rep else "retryable",
            "reason": None if rep else f"{type(exc).__name__}: {str(exc)[:200]}",
            "warnings": warns, "model_requested": ai.get("model", "claude-opus-5-5"), "model_served": served,
            "stop_reason": m.get("stop_reason") if rep else info.get("stop_reason"),
            # انقطاع البث أو فشل الطلب: الاستخدام غير معروف، ولا نخترع رقماً
            "usage": usage if isinstance(usage, dict) else "unknown (request failed or stream interrupted; may still be billed)",
            "cost_estimate": estimate_cost(served or "", usage) if isinstance(usage, dict) else None}


def update_history(path: Path, report: dict, keep: int = 14) -> list[dict]:
    hist = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    hist = [h for h in hist if h.get("meta", {}).get("ny_date") != report["meta"]["ny_date"]]
    hist.insert(0, report)
    hist = hist[:keep]
    path.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")
    return hist
