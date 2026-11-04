"""الأخبار اللحظية من FinancialJuice (REST، الخطة المجانية: تأخير 10 دقائق).

- الجلب كود عادي بلا نموذج: GET /v1/news?updated_since=... بالمفتاح FJ_API_KEY.
  REST لا يستهلك مقعد WebSocket، فلا يتعارض مع مستمع منصة الذهب (اتصال واحد للحساب).
- الترجمة فقط عند الحاجة: Claude Haiku يترجم العناوين الإنجليزية الجديدة دفعة واحدة. العنوان المترجم سابقاً
  لا يُرسل مرة أخرى، والعنوان الذي لا يحتاج ترجمة لا يمر على Haiku. بلا مفتاح Anthropic يُعرض الأصل الإنجليزي.
- نص العناوين لا يُحفظ في المستودع: news.json يُنشر مع الصفحة مباشرة (GitHub Pages عبر Actions).
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

FJ_NEWS = "https://stream.financialjuice.com/v1/news"
TRANSLATOR = "claude-haiku-5-5"
KEEP_HOURS = 24
MAX_ITEMS = 250
BATCH = 40

US_LABELS = {"US", "USA", "USD", "FED", "FOMC", "US INDEXES", "US DATA", "TREASURIES", "BONDS", "US POLITICS",
             "US EQUITIES", "WALL STREET"}
US_WORDS = re.compile(r"\b(US|U\.S\.|Fed|FOMC|Powell|Treasur\w*|Trump|White House|Nasdaq|S&P|Dow|tariffs?|Bessent|"
                      r"payrolls?|NFP|CPI|PCE|PPI|ISM|jobless|Wall Street|Congress|Senate)\b")


class NewsError(RuntimeError):
    pass


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_t(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def http_get_json(url: str, key: str, timeout: float = 30) -> dict:
    req = urllib.request.Request(url, headers={"X-API-Key": key, "Accept": "application/json",
                                               "User-Agent": "macro-platform/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # لا نطبع الرابط ولا المفتاح
        try:
            err = json.loads(e.read().decode("utf-8")).get("error", {})
        except Exception:
            err = {}
        raise NewsError(f"FinancialJuice HTTP {e.code} {err.get('code', '')}".strip()) from None
    except Exception as e:
        raise NewsError(f"FinancialJuice: {type(e).__name__}") from None


def fetch(key: str, since: datetime, get=http_get_json, max_pages: int = 5) -> list[dict]:
    """كل العناوين المنشورة أو المعدّلة منذ since (الأحدث أولاً)."""
    url = FJ_NEWS + "?" + urllib.parse.urlencode({"updated_since": _iso(since), "limit": 200})
    out: list[dict] = []
    for _ in range(max_pages):
        page = get(url, key)
        out += [d for d in page.get("data", []) if isinstance(d, dict) and d.get("id") is not None]
        nxt = page.get("next")
        if not nxt:
            break
        url = urllib.parse.urljoin("https://stream.financialjuice.com", nxt)
    return out


def is_us(item: dict) -> bool:
    labels = {str(x).upper() for x in item.get("labels") or []}
    return bool(labels & US_LABELS) or bool(US_WORDS.search(item.get("title_en") or ""))


def needs_translation(text: str) -> bool:
    """عنوان فيه كلمات لاتينية يحتاج ترجمة؛ العنوان العربي أو الأرقام وحدها لا تحتاج."""
    return bool(re.search(r"[A-Za-z]{2,}", text or ""))


def merge(store: dict[str, dict], fresh: list[dict]) -> int:
    """يدمج العناوين الجديدة؛ إن تغيّر نص عنوان قديم تُلغى ترجمته ليُترجم من جديد."""
    changed = 0
    for d in fresh:
        title = (d.get("title") or "").strip()
        if not title:
            continue
        sid = str(d["id"])
        old = store.get(sid)
        item = {
            "id": d["id"], "utc": d.get("datePublished"), "title_en": title[:300],
            "labels": [str(x) for x in (d.get("labels") or [])][:6],
            "breaking": bool(d.get("breaking")), "level": int(d.get("level") or 0),
            "link": d.get("link") if str(d.get("link", "")).startswith("https://") else None,
        }
        if old and old.get("title_en") == item["title_en"] and old.get("title_ar"):
            item["title_ar"] = old["title_ar"]
        item["us"] = is_us(item)
        if old != item:
            changed += 1
        store[sid] = item
    return changed


def prune(store: dict[str, dict], now: datetime) -> dict[str, dict]:
    cut = now - timedelta(hours=KEEP_HOURS)
    items = [i for i in store.values() if (_parse_t(i.get("utc")) or cut) > cut]
    items.sort(key=lambda i: i.get("utc") or "", reverse=True)
    return {str(i["id"]): i for i in items[:MAX_ITEMS]}


TRANSLATE_SYSTEM = (
    "You translate live financial-market headlines into concise Modern Standard Arabic for traders. "
    "Keep tickers, index and data names, central-bank and policy acronyms, numbers, percentages and units in their "
    "original form (e.g. NQ, S&P 500, CPI, PCE, NFP, Fed, FOMC, ECB, bp, Y/Y, M/M, 3.2%). Keep person names in Arabic "
    "script. Do not add, drop or soften any information; no commentary. Return one translation per id."
)
SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "properties": {"id": {"type": "integer"}, "ar": {"type": "string"}},
        "required": ["id", "ar"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False,
}


def haiku_translate(batch: list[dict]) -> dict[int, str]:
    import anthropic  # يُثبَّت في GitHub Actions فقط

    client = anthropic.Anthropic()
    lines = "\n".join(json.dumps({"id": i["id"], "en": i["title_en"]}, ensure_ascii=False) for i in batch)
    resp = client.messages.create(
        model=TRANSLATOR,
        max_tokens=8000,
        system=TRANSLATE_SYSTEM,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": "Translate each headline:\n" + lines}],
    )
    if resp.stop_reason == "refusal":
        raise NewsError("translator refused the batch")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return {int(x["id"]): x["ar"].strip() for x in json.loads(text).get("items", []) if x.get("ar")}


def translate_missing(store: dict[str, dict], translator=haiku_translate, field: str = "title_en") -> tuple[int, list[str]]:
    """يترجم فقط ما يحتاج ترجمة ولم يُترجم بعد. يعيد (عدد المترجم، الأخطاء)."""
    todo = [i for i in store.values() if not i.get("title_ar") and needs_translation(i.get(field) or "")]
    for i in store.values():  # لا يحتاج ترجمة: يُعرض كما هو بلا Haiku
        if not i.get("title_ar") and i.get(field) and not needs_translation(i[field]):
            i["title_ar"] = i[field]
    done, errors = 0, []
    for k in range(0, len(todo), BATCH):
        batch = todo[k:k + BATCH]
        try:
            got = translator([{"id": i["id"], "title_en": i[field]} for i in batch])
        except Exception as e:
            errors.append(f"الترجمة: {type(e).__name__}: {str(e)[:120]}")
            break
        for i in batch:
            if i["id"] in got:
                i["title_ar"] = got[i["id"]]
                done += 1
    return done, errors


def payload(store: dict[str, dict], now: datetime, translator: str | None, errors: list[str],
            calendar: dict[str, dict] | None = None) -> dict:
    items = sorted(store.values(), key=lambda i: i.get("utc") or "", reverse=True)
    out = {"updated": _iso(now), "source": "FinancialJuice", "delay_minutes": 10, "translator": translator,
           "errors": errors, "items": items}
    if calendar is not None:
        out["calendar"] = sorted(calendar.values(), key=lambda e: e.get("timeUtc") or "")
    return out


# ---------------------------------------------------------------- economic calendar (released figures)
# نفس حقول كائن calendar في FinancialJuice بلا تغيير، فتقرؤه منصة الذهب كما تقرأ مستمعها (fj_calendar_apply).
FJ_CAL = "https://stream.financialjuice.com/v1/calendar"
CAL_FIELDS = ("id", "title", "country", "timeUtc", "impact", "type", "actual", "forecast", "previous",
              "revision", "unit", "breaking")
CAL_BACK_DAYS = 3      # الخطة المجانية تصل 24 ساعة للخلف، فنراكم بين الدورات لنبقي 3 أيام من البيانات الصادرة
CAL_AHEAD_DAYS = 7
US_CODES = {"US", "USA", "UNITED STATES"}


def fetch_calendar(key: str, now: datetime, get=http_get_json, max_pages: int = 5) -> list[dict]:
    """أحداث التقويم من 23 ساعة مضت (حد الخطة المجانية 24 ساعة) إلى 7 أيام قادمة."""
    url = FJ_CAL + "?" + urllib.parse.urlencode({"from": _iso(now - timedelta(hours=23)),
                                                 "to": _iso(now + timedelta(days=CAL_AHEAD_DAYS)), "limit": 500})
    out: list[dict] = []
    for _ in range(max_pages):
        page = get(url, key)
        out += [d for d in page.get("data", []) if isinstance(d, dict) and d.get("id") is not None]
        nxt = page.get("next")
        if not nxt:
            break
        url = urllib.parse.urljoin("https://stream.financialjuice.com", nxt)
    return out


def merge_calendar(store: dict[str, dict], fresh: list[dict]) -> int:
    """كل الأحداث الأمريكية، ومن غيرها المرتفع والمتوسط التأثير فقط. الترجمة تبقى ما لم يتغير العنوان."""
    changed = 0
    for d in fresh:
        country = str(d.get("country") or "").upper()
        if country not in US_CODES and d.get("impact") not in (1, 2):
            continue
        item = {k: d.get(k) for k in CAL_FIELDS}
        item["title"] = str(item.get("title") or "").strip()[:200]
        if not item["title"]:
            continue
        item["us"] = country in US_CODES
        old = store.get(str(d["id"]))
        if old and old.get("title") == item["title"] and old.get("title_ar"):
            item["title_ar"] = old["title_ar"]
        if old != item:
            changed += 1
        store[str(d["id"])] = item
    return changed


def prune_calendar(store: dict[str, dict], now: datetime) -> dict[str, dict]:
    lo, hi = now - timedelta(days=CAL_BACK_DAYS), now + timedelta(days=CAL_AHEAD_DAYS + 1)
    keep = [e for e in store.values() if (t := _parse_t(e.get("timeUtc"))) and lo <= t <= hi]
    keep.sort(key=lambda e: e.get("timeUtc") or "")
    return {str(e["id"]): e for e in keep}


def since_for(store: dict[str, dict], now: datetime) -> datetime:
    """من آخر عنوان معروف ناقص 15 دقيقة (لالتقاط التعديلات)، وإلا آخر 24 ساعة."""
    ts = [t for t in (_parse_t(i.get("utc")) for i in store.values()) if t]
    floor = now - timedelta(hours=KEEP_HOURS)
    return max(floor, max(ts) - timedelta(minutes=15)) if ts else floor
