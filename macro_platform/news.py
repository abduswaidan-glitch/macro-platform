"""الأخبار المؤثرة وتقويم البيانات الأمريكية من مصادر عامة مجانية، بلا مفاتيح ولا انتحال متصفح.

- العناوين: خلاصات RSS عامة، والقائمة في config.json → news.sources.
  رسمية: Fed (البيانات الصحفية والخطابات) وBEA (الناتج والدخل والإنفاق والتجارة). إعلامية: CNBC وMarketWatch.
  كل مصدر يمر بمرشّح أهمية (FILTERS): ما يحرّك NQ يبقى، وما لا يحركه (الإنفاذ المصرفي، التمويل الشخصي...) يُسقط.
- التقويم: تصدير ForexFactory العام (هذا الأسبوع، والقادم حين ينشره)، الأحداث الأمريكية مرتفعة ومتوسطة التأثير،
  بأسماء حقول FinancialJuice القديمة نفسها (عقد news.json). التصدير لا يحمل القيمة الفعلية: الحدث الماضي يُعرض
  بموعده والتوقع والسابق، والفعلي «لا يحمله المصدر»، لا «لم يصدر».
- الاحتفاظ: عناوين الأسبوع وأحداثه تبقى حتى افتتاح الأسبوع التالي (الأحد 18:00 بتوقيت نيويورك، افتتاح Globex)،
  ولا يقل ما يبقى عن آخر 72 ساعة، فأخبار نهاية الأسبوع تظهر صباح الاثنين.
- الترجمة: Claude Haiku للعناوين الجديدة فقط. نص العناوين لا يُحفظ في المستودع: news.json يُنشر مع الصفحة.
"""

from __future__ import annotations

import email.utils
import hashlib
import html
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UA = "macro-platform/1.0 (+https://abduswaidan-glitch.github.io/macro-platform/)"   # هوية صريحة، لا متصفح
TRANSLATOR = "claude-haiku-5-5"
MAX_ITEMS = 400
MAX_BYTES = 4_000_000
BATCH = 40
MIN_KEEP = timedelta(hours=72)
CAL_REFRESH = timedelta(minutes=55)   # تصدير ForexFactory يتحدث كل ساعة تقريباً: طلب واحد في الساعة يكفي ولا يُثقل عليه
DELAY_MINUTES = 10                    # دورة news.yml

DEFAULT_SOURCES = [
    {"name": "Fed", "kind": "official", "filter": "fed_press", "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
    {"name": "Fed", "kind": "official", "filter": "all", "url": "https://www.federalreserve.gov/feeds/speeches.xml"},
    {"name": "BEA", "kind": "official", "filter": "bea", "url": "https://apps.bea.gov/rss/rss.xml"},
    {"name": "CNBC", "kind": "media", "filter": "market", "stale_days": 2,
     "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"},
    {"name": "CNBC", "kind": "media", "filter": "market", "stale_days": 2,
     "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"},
    {"name": "MarketWatch", "kind": "media", "filter": "market", "stale_days": 2,
     "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories"},
]
DEFAULT_CALENDAR = [
    {"name": "ForexFactory this week", "url": "https://nfs.faireconomy.media/ff_calendar_thisweek.json", "required": True},
    {"name": "ForexFactory next week", "url": "https://nfs.faireconomy.media/ff_calendar_nextweek.json", "required": False},
]

# ---------------------------------------------------------------- importance filters
# ما يحرّك NQ: الفائدة والتضخم والعمل والنمو والعوائد والسياسة التجارية والمالية، وحركة السوق والشركات الكبرى في المؤشر.
MARKET = re.compile(
    r"\b(Fed|Federal Reserve|FOMC|Powell|Warsh|Hassett|rate cuts?|rate hikes?|interest rates?|monetary policy|"
    r"inflation|CPI|PCE|PPI|consumer prices|wholesale prices|jobs report|payrolls?|nonfarm|unemployment|jobless|"
    r"labor market|layoffs|GDP|recession|economy|economic|retail sales|consumer (?:spending|sentiment|confidence)|ISM|PMI|"
    r"Treasur(?:y|ies)|yields?|bonds?|debt ceiling|shutdown|deficit|tariffs?|trade (?:war|deal|talks)|export controls?|"
    r"sanctions|Trump|White House|Bessent|Lutnick|Supreme Court|Congress|"
    r"stocks?|stock market|S&P 500|S&P|Nasdaq|Dow|Wall Street|futures|sell-?off|rally|correction|VIX|volatility|"
    r"Nvidia|Apple|Microsoft|Alphabet|Google|Amazon|Meta|Tesla|Broadcom|AMD|semiconductors?|chipmakers?|earnings|"
    r"oil|crude|OPEC|dollar|gold|bitcoin|China|Iran|Russia|Ukraine|Israel|Taiwan|war|missiles?)\b", re.I)
NOISE = re.compile(
    r"\b(Cramer|Investing Club|CNBC Select|Make It|personal finance|credit cards?|savings accounts?|mortgage rates? today|"
    r"how to|tips|retire(?:ment|es)?|401\(k\)|Social Security|celebrit\w*|recipes?|vacation|wedding|student loans?|"
    r"podcast|quiz|sponsored|newsletter|horoscope|"
    # أعمدة اختيار الأسهم والتوصيات (فحص 13 نوفمبر: أغلب خلاصة CNBC للأسواق من هذا النوع)
    r"stocks? to buy|these \d+ [\w ]{0,25}stocks|best stock|analyst calls|says (?:it's )?a buy|keep buying|"
    r"upgrades this|downgrades this|dividend stocks|we want to make|still looks attractive|why we still see|"
    r"how to trade|charts to watch|levels to (?:know|watch)|dip buyers|screaming bargain|financial advisors are doing)\b",
    re.I)
FED_NOISE = re.compile(
    r"(enforcement action|termination of|approval of (?:the )?(?:application|proposal)|application by|"
    r"announces approval|civil money penalt|prohibition|Community Reinvestment|minority depository|reporting form|"
    r"paperwork)", re.I)
BEA_KEEP = re.compile(r"(GDP|Gross Domestic Product|Personal Income and Outlays|Personal Consumption|PCE|"
                      r"International Trade in Goods and Services|Trade in Goods|Current Account|Corporate Profits)", re.I)
BEA_NOISE = re.compile(r"(by State|by County|Metropolitan|States and|Territor|Puerto Rico|Arts|Outdoor Recreation|"
                       r"Travel and Tourism|Digital Economy|Marine|by Country|by Industry|Health Care)", re.I)
FILTERS = {
    "all": lambda t: True,
    "fed_press": lambda t: not FED_NOISE.search(t),
    "bea": lambda t: bool(BEA_KEEP.search(t)) and not BEA_NOISE.search(t),
    "market": lambda t: bool(MARKET.search(t)) and not NOISE.search(t),
}
# الأشد أثراً: يظهر في «الأكثر تأثيراً» (level 3)
TIER1 = re.compile(
    r"\b(FOMC|Powell|Warsh|Fed (?:chair|governor)|rate (?:cut|hike|decision)s?|"
    r"(?:holds?|keeps?|raises?|lowers?|cuts?) (?:interest )?rates|CPI|PPI|inflation|(?:consumer|producer|wholesale) prices|"
    r"payrolls|nonfarm|jobs report|unemployment rate|jobless claims|retail sales|PCE|GDP|recession|tariffs?|shutdown|"
    r"Treasury yields?|10-year|default|"
    r"downgrade|sell-?off|plunges?|tumbles?|crash\w*|soars?|surges?|war|missiles?|invasion|sanctions|emergency)\b", re.I)
# أمريكي صراحة؛ أو بيان اقتصادي بلا ذكر بلد آخر (العنوان الأمريكي لا يقول «U.S.» عادة: «Wholesale prices rose 0.3%»)
US_WORDS = re.compile(r"\bU\.S\.|\b(US|American|Fed|FOMC|Powell|Warsh|Treasur\w*|Trump|White House|Nasdaq|S&P|Dow|"
                      r"Bessent|Lutnick|Wall Street|Congress|Senate|"
                      r"Nvidia|Apple|Microsoft|Alphabet|Google|Amazon|Meta|Tesla|Broadcom)\b")
US_DATA = re.compile(r"\b(inflation|CPI|PCE|PPI|(?:consumer|producer|wholesale) prices|jobs|payrolls?|NFP|unemployment|"
                     r"jobless|labor market|GDP|retail sales|ISM|tariffs?|stocks?|stock market)\b", re.I)
FOREIGN = re.compile(r"\b(China|Chinese|Beijing|Japan|Japanese|BOJ|Euro ?zone|Europe|European|ECB|UK|Britain|British|"
                     r"Bank of England|Germany|German|France|French|Canada|Canadian|Australia|India|Korea|Russia|Russian|"
                     r"Mexico|Brazil|Swiss|Turkey)\b", re.I)


class NewsError(RuntimeError):
    pass


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_t(s) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t.astimezone(timezone.utc) if t.tzinfo else None
    except Exception:
        return None


def _id(key: str) -> int:
    """معرّف عددي ثابت (52 بت: آمن في JavaScript، ومخطط الترجمة يطلب integer)."""
    return int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:13], 16)


def _clean(s) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(s or ""))).strip()


def load_config(cfg: dict | None) -> dict:
    n = (cfg or {}).get("news") or {}
    return {"sources": n.get("sources") or DEFAULT_SOURCES, "calendar": n.get("calendar") or DEFAULT_CALENDAR}


def source_names(cfg: dict) -> list[str]:
    names = list(dict.fromkeys(s["name"] for s in cfg["sources"]))
    return names + ["ForexFactory"]


def http_get(url: str, timeout: float = 25) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, "
                                               "application/json;q=0.9, */*;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise NewsError(f"HTTP {e.code}") from None
    except Exception as e:
        raise NewsError(type(e).__name__) from None
    if len(body) > MAX_BYTES:
        raise NewsError("response too large")
    return body


# ---------------------------------------------------------------- week retention
def week_start(now: datetime) -> datetime:
    """افتتاح أسبوع التداول الحالي: آخر أحد 18:00 بتوقيت نيويورك (Globex) لا يتجاوز now."""
    ny = now.astimezone(NY)
    s = (ny - timedelta(days=(ny.weekday() + 1) % 7)).replace(hour=18, minute=0, second=0, microsecond=0)
    if s > ny:
        s -= timedelta(days=7)
    return s.astimezone(timezone.utc)


def keep_since(now: datetime) -> datetime:
    """ما بعده يبقى: الأسبوع الحالي كاملاً، وآخر 72 ساعة على الأقل (أخبار نهاية الأسبوع صباح الاثنين)."""
    return min(week_start(now), now - MIN_KEEP)


# ---------------------------------------------------------------- headlines (RSS)
def parse_rss(raw: bytes) -> list[dict]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise NewsError("not RSS/XML") from None
    out = []
    for it in root.iter("item"):
        out.append({"title": _clean(it.findtext("title")), "link": _clean(it.findtext("link")),
                    "guid": _clean(it.findtext("guid")),
                    "pub": it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date")})
    return out


def _rss_time(s) -> datetime | None:
    if not s:
        return None
    try:
        t = email.utils.parsedate_to_datetime(str(s).strip())
    except Exception:
        t = None
    if t is None:
        return _parse_t(s)
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def is_us(item: dict) -> bool:
    t = item.get("title_en") or ""
    return ("official" in (item.get("labels") or []) or bool(US_WORDS.search(t))
            or bool(US_DATA.search(t) and not FOREIGN.search(t)))


def to_item(src: dict, r: dict, t: datetime) -> dict:
    title = r["title"][:300]
    link = r["link"] if re.match(r"^https?://", r["link"] or "", re.I) else None
    item = {"id": _id(link or r["guid"] or (src["name"] + "|" + title)), "utc": _iso(t), "title_en": title,
            "labels": [src["name"], src.get("kind", "media")], "breaking": False,
            "level": 3 if TIER1.search(title) else 2 if src.get("kind") == "official" else 1, "link": link}
    item["us"] = is_us(item)
    return item


def fetch_source(src: dict, now: datetime, since: datetime, get=None) -> tuple[list[dict], dict]:
    """عناوين مصدر واحد بعد since، مصفّاة بمرشّح المصدر. يرفع NewsError إن تعذر الجلب أو كانت الخلاصة فارغة أو قديمة."""
    rows = parse_rss((get or http_get)(src["url"]))
    if not rows:
        raise NewsError("feed has no items")
    times = [t for t in (_rss_time(r["pub"]) for r in rows) if t]
    if src.get("stale_days") and (not times or now - max(times) > timedelta(days=src["stale_days"])):
        raise NewsError("feed is stale (newest item " + (_iso(max(times)) if times else "undated") + ")")
    keep = FILTERS[src.get("filter", "all")]
    out = []
    for r in rows:
        t = _rss_time(r["pub"])
        if not r["title"] or t is None or t < since or t > now + timedelta(minutes=10):
            continue
        if keep(r["title"]):
            out.append(to_item(src, r, t))
    return out, {"seen": len(rows), "kept": len(out)}


def fetch_headlines(sources: list[dict], now: datetime, since: datetime, get=None):
    """كل المصادر؛ فشل مصدر لا يوقف البقية. يعيد (العناوين، حالة كل رابط، الأخطاء)."""
    items, status, errors = [], [], []
    for src in sources:
        try:
            got, st = fetch_source(src, now, since, get)
            items += got
            status.append({"name": src["name"], "url": src["url"], "ok": True, **st})
        except Exception as e:
            msg = str(e)[:120] if isinstance(e, NewsError) else type(e).__name__
            status.append({"name": src["name"], "url": src["url"], "ok": False, "error": msg})
            errors.append(f"{src['name']}: {msg}")
    return items, status, errors


def needs_translation(text: str) -> bool:
    """عنوان فيه كلمات لاتينية يحتاج ترجمة؛ العنوان العربي أو الأرقام وحدها لا تحتاج."""
    return bool(re.search(r"[A-Za-z]{2,}", text or ""))


def merge(store: dict[str, dict], fresh: list[dict]) -> int:
    """يدمج العناوين الجديدة؛ إن تغيّر نص عنوان قديم تُلغى ترجمته ليُترجم من جديد."""
    changed = 0
    for d in fresh:
        sid = str(d["id"])
        old = store.get(sid)
        item = dict(d)
        if old and old.get("title_en") == item["title_en"] and old.get("title_ar"):
            item["title_ar"] = old["title_ar"]
        if old != item:
            changed += 1
        store[sid] = item
    return changed


def prune(store: dict[str, dict], now: datetime) -> dict[str, dict]:
    cut = keep_since(now)
    items = [i for i in store.values() if (t := _parse_t(i.get("utc"))) and cut <= t <= now + timedelta(minutes=10)]
    items.sort(key=lambda i: i.get("utc") or "", reverse=True)
    return {str(i["id"]): i for i in items[:MAX_ITEMS]}


# ---------------------------------------------------------------- translation (Claude Haiku)
TRANSLATE_SYSTEM = (
    "You translate financial-market headlines into concise Modern Standard Arabic for traders. "
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


# ---------------------------------------------------------------- economic calendar (ForexFactory export)
# حقول كائن calendar كما كانت من FinancialJuice بلا تغيير (عقد news.json الذي تقرؤه منصة الذهب).
CAL_FIELDS = ("id", "title", "country", "timeUtc", "impact", "type", "actual", "forecast", "previous",
              "revision", "unit", "breaking")
IMPACT = {"High": 1, "Medium": 2}


def ff_event(r: dict) -> dict | None:
    """صف من تصدير ForexFactory ← حدث بحقول العقد. الدولار الأمريكي، مرتفع ومتوسط التأثير فقط."""
    if not isinstance(r, dict) or str(r.get("country") or "").upper() != "USD" or r.get("impact") not in IMPACT:
        return None
    t, title = _parse_t(r.get("date")), _clean(r.get("title"))[:200]
    if t is None or not title:
        return None
    val = lambda k: _clean(r.get(k)) or None   # noqa: E731
    return {"id": _id(f"ff|{_iso(t)}|{title}"), "title": title, "country": "US", "timeUtc": _iso(t),
            "impact": IMPACT[r["impact"]], "type": None, "actual": val("actual"), "forecast": val("forecast"),
            "previous": val("previous"), "revision": None, "unit": None, "breaking": False, "us": True}


def fetch_calendar(feeds: list[dict], get=None) -> tuple[list[dict], tuple[datetime, datetime] | None, list[dict]]:
    """الأحداث من كل الروابط، والمدى الزمني الذي يغطيه المصدر (كل الدول)، وحالة كل رابط.
    فشل رابط مطلوب (هذا الأسبوع) يرفع NewsError؛ الأسبوع القادم اختياري لأن المصدر لا ينشره دائماً."""
    events, times, status = {}, [], []
    for f in feeds:
        try:
            rows = json.loads((get or http_get)(f["url"]).decode("utf-8"))
            if not isinstance(rows, list) or not rows:
                raise NewsError("no rows")
        except Exception as e:
            msg = str(e)[:120] if isinstance(e, NewsError) else type(e).__name__
            status.append({"name": f["name"], "url": f["url"], "ok": False, "error": msg})
            if f.get("required"):
                raise NewsError(f"{f['name']}: {msg}") from None
            continue
        times += [t for t in (_parse_t(r.get("date")) for r in rows if isinstance(r, dict)) if t]
        got = [e for e in map(ff_event, rows) if e]
        events.update({str(e["id"]): e for e in got})
        status.append({"name": f["name"], "url": f["url"], "ok": True, "seen": len(rows), "kept": len(got)})
    return list(events.values()), ((min(times), max(times)) if times else None), status


def merge_calendar(store: dict[str, dict], fresh: list[dict], window=None) -> int:
    """يدمج الأحداث؛ الترجمة تبقى ما لم يتغير العنوان. المصدر مرجع لمداه: حدث محفوظ داخل المدى ولم يعد فيه
    (نُقل موعده أو أُلغي) يُحذف، وما قبل المدى (الأسبوع الماضي) يبقى حتى ينتهي وقت الاحتفاظ."""
    ids = {str(e["id"]) for e in fresh}
    changed = 0
    if window:
        lo, hi = window
        for k in [k for k, e in store.items() if k not in ids and (t := _parse_t(e.get("timeUtc"))) and lo <= t <= hi]:
            del store[k]
            changed += 1
    for e in fresh:
        item = dict(e)
        old = store.get(str(e["id"]))
        if old and old.get("title") == item["title"] and old.get("title_ar"):
            item["title_ar"] = old["title_ar"]
        if old != item:
            changed += 1
        store[str(e["id"])] = item
    return changed


def prune_calendar(store: dict[str, dict], now: datetime) -> dict[str, dict]:
    lo, hi = keep_since(now), now + timedelta(days=15)
    keep = [e for e in store.values() if (t := _parse_t(e.get("timeUtc"))) and lo <= t <= hi]
    keep.sort(key=lambda e: e.get("timeUtc") or "")
    return {str(e["id"]): e for e in keep}


def calendar_due(prev: dict, now: datetime) -> bool:
    """جلب في الساعة، وفي كل دورة ما دامت آخر محاولة فشلت (آخر نجاح أقدم منها)."""
    prev = prev if isinstance(prev, dict) else {}
    t = _past(prev.get("calendar_attempt_utc"), now)
    ls = prev.get("last_success_utc")
    ok = _past(ls.get("calendar"), now) if isinstance(ls, dict) else None
    return t is None or now - t >= CAL_REFRESH or ok is None or ok < t


# ---------------------------------------------------------------- feed health and the report's input
STREAMS = ("items", "calendar")
CURRENT_FOR = {"items": timedelta(minutes=30), "calendar": timedelta(minutes=130)}   # 3 دورات أخبار / ساعتان للتقويم


def _past(s, now: datetime) -> datetime | None:
    """طابع زمني صالح لا يقع في المستقبل؛ غير ذلك None، أي «غير معروف»، لا «سليم»."""
    t = _parse_t(s)
    return t if t is not None and t <= now + timedelta(minutes=5) else None


def last_success(prev: dict, now: datetime, ok: dict[str, bool]) -> dict[str, str | None]:
    """آخر جلب ناجح لكل مسار: الآن إن نجح في هذه الدورة، وإلا القيمة المنشورة سابقاً كما هي. لا نجاح مفترض."""
    old = prev.get("last_success_utc") if isinstance(prev, dict) else None
    old = old if isinstance(old, dict) else {}
    out = {}
    for k in STREAMS:
        t = now if ok.get(k) else _past(old.get(k), now)
        out[k] = _iso(t) if t else None
    return out


def feed_status(prev: dict, now: datetime, status: list[dict], carry: tuple = ()) -> list[dict]:
    """حالة كل رابط مع آخر نجاح له (يبقى من الدورة السابقة إن فشل الآن). carry: روابط لم تُطلب هذه الدورة
    (التقويم بين جلبين) فتبقى حالتها السابقة كما هي."""
    old = prev.get("feeds") if isinstance(prev, dict) else None
    old = {f.get("url"): f for f in old if isinstance(f, dict)} if isinstance(old, list) else {}
    out = []
    for s in status:
        t = now if s["ok"] else _past((old.get(s["url"]) or {}).get("last_success_utc"), now)
        out.append({**s, "last_success_utc": _iso(t) if t else None})
    return out + [old[u] for u in carry if u in old]


def event_status(e: dict, now: datetime) -> str:
    """reported: المصدر يحمل قيمة · upcoming: لم يحن وقته · past_no_value_in_source: مضى وقته والمصدر لا يحمل
    قيماً فعلية أصلاً (تصدير ForexFactory)، فلا يُستنتج منه أن الرقم صدر ولا أنه لم يصدر."""
    t = _parse_t(e.get("timeUtc"))
    if t is None:
        return "unknown_time"
    a = e.get("actual")
    if a is not None and str(a).strip():
        return "reported"
    return "upcoming" if now < t else "past_no_value_in_source"


FEED_NOTE = (
    "Headlines are RSS titles, timed by publication (UTC): source 'official' = the Fed or BEA announced it (title only; "
    "open the link for the content); 'media' = CNBC or MarketWatch reported it, not verified. The calendar comes from the "
    "ForexFactory public export: times, forecasts and previous figures only. It never carries actual figures, so a past "
    "event has status past_no_value_in_source: that says nothing about whether the release happened or was postponed; "
    "take released figures from the platform data or the official release. *_current=false means nothing after "
    "*_last_success_utc is covered (null = unknown): treat older items as old, search official sources for the gap, "
    "and state the gap in data_audit.")


def report_feed(live: dict, now: datetime, limit: int = 60) -> dict:
    """مدخل تقرير Claude: عناوين وأحداث بمصدرها، مع حالة التغطية لكل مسار.
    يُبنى دائماً، ولو تعذرت قراءة news.json، حتى يعرف النموذج أن التغطية ناقصة بدل أن يغيب المصدر بصمت."""
    live = live if isinstance(live, dict) else {}
    ls = live.get("last_success_utc")
    ls = ls if isinstance(ls, dict) else {}
    out: dict = {"source": "public RSS feeds (Fed, BEA, CNBC, MarketWatch) and the ForexFactory calendar export, "
                           "refreshed about every 10 minutes (calendar hourly)"}
    for k, name in (("items", "headlines"), ("calendar", "calendar")):
        t = _past(ls.get(k), now)
        out[f"{name}_last_success_utc"] = _iso(t) if t else None
        out[f"{name}_current"] = bool(t and now - t <= CURRENT_FOR[k])
    heads = []
    for i in live.get("items") or []:
        if not isinstance(i, dict) or not (i.get("us") or (i.get("level") or 0) >= 3):
            continue
        lb = i.get("labels") if isinstance(i.get("labels"), list) else []
        heads.append({"utc": i.get("utc"), "title": i.get("title_en"),
                      "source": " ".join(str(x) for x in lb[:2]) or None, "link": i.get("link")})
    out["headlines"] = heads[:limit]
    out["calendar"] = [{k: e.get(k) for k in ("timeUtc", "title", "impact", "actual", "forecast", "previous", "revision")}
                       | {"status": event_status(e, now)}
                       for e in live.get("calendar") or [] if isinstance(e, dict) and e.get("us")
                       and e.get("impact") in (1, 2)][:limit]
    out["note"] = FEED_NOTE
    return out


def payload(store: dict[str, dict], now: datetime, translator: str | None, errors: list[str],
            calendar: dict[str, dict] | None = None, last_success: dict | None = None, source: str = "",
            feeds: list[dict] | None = None, calendar_attempt: datetime | None = None) -> dict:
    items = sorted(store.values(), key=lambda i: i.get("utc") or "", reverse=True)
    out = {"updated": _iso(now), "source": source, "delay_minutes": DELAY_MINUTES, "translator": translator,
           "errors": errors, "items": items}
    if calendar is not None:
        out["calendar"] = sorted(calendar.values(), key=lambda e: e.get("timeUtc") or "")
    if last_success is not None:  # updated وقت البناء، وهذا وقت آخر جلب ناجح لكل مسار
        out["last_success_utc"] = last_success
    if feeds is not None:
        out["feeds"] = feeds
    if calendar_attempt is not None:
        out["calendar_attempt_utc"] = _iso(calendar_attempt)
    out["keep_since_utc"] = _iso(keep_since(now))
    return out
