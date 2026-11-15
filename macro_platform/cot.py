"""COT (CFTC Commitments of Traders) لعقود مؤشر الدولار USD INDEX - ICE FUTURES U.S. (رمز 098662)، من صفحات
Tradingster العامة (قرار Abdu، 15 نوفمبر 2026: «خذها من مواقع عادية مثل tradingster»).

- الأرقام أرقام CFTC نفسها (ملك عام). كل صفحة تحمل التاريخ الأسبوعي كاملاً منذ نوفمبر 2016 في بيانات رسمها
  (var dataNet)، فجلب واحد لكل صفحة يكفي: Legacy للمضاربين الكبار (Non-Commercial)، وTFF لـLeveraged Funds
  وAsset Managers.
- فحصان قبل القبول: تاريخ آخر صف في بيانات الرسم = «AS OF» في رأس الصفحة، وفي Legacy صافي الجدول (Long − Short)
  = صافي آخر صف. أي تغيّر في تصميم الصفحة يُرفض بخطأ، فتبقى آخر نسخة سليمة في data/raw.
- بلا انتحال متصفح: معرّف بوت صريح، ولا معلومات شخصية فيه.
- البيانات أسبوعية (مراكز الثلاثاء تُنشر الجمعة 15:30 نيويورك). لا داعي لطلب الصفحة في كل تشغيل: due() يقول متى
  يمكن أن يكون إصدار جديد قد صدر، وقبله تُستعمل النسخة المحفوظة.
"""

from __future__ import annotations

import html as htmllib
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import pandas as pd

CODE = "098662"
MARKET = "USD INDEX - ICE FUTURES U.S."
PAGES = {
    "legacy": f"https://www.tradingster.com/cot/legacy-futures/{CODE}",
    "tff": f"https://www.tradingster.com/cot/futures/fin/{CODE}",
}
# معرّف المنصة -> (الصفحة، الحقل في بيانات الرسم)
SERIES = {
    "COT_DX_NC": ("legacy", "NonCommercial"),
    "COT_DX_LEV": ("tff", "Lev_Money"),
    "COT_DX_AM": ("tff", "Asset_Mgr"),
}
UA = "macro-dashboard-bot/1.0 (weekly COT reader)"
TIMEOUT = 40
# مراكز الثلاثاء T تُنشر عادة الجمعة T+3 الساعة 15:30 نيويورك (20:30 UTC شتاءً، 19:30 صيفاً)، فالإصدار التالي
# (مراكز T+7) لا يصدر قبل T+10 الساعة 19:30 UTC. بعدها نطلب الصفحة في كل تشغيل حتى يظهر (تأخير العطل يؤخره).
# إن انتقلت CFTC إلى النشر مرتين أسبوعياً (أعلنته في 27 أكتوبر 2026) يُقصَّر هذا الحد.
DUE_AFTER = timedelta(days=10, hours=19, minutes=30)

_ROW = re.compile(r"\{\s*date:\s*new Date\('(\d{4}-\d{2}-\d{2})'\)\s*,([^{}]*)\}")
_NUM = re.compile(r"(\w+)\s*:\s*(-?\d+(?:\.\d+)?)")


class CotError(RuntimeError):
    pass


def _int(cell: str) -> int:
    return int(re.sub(r"<[^>]+>", "", cell).replace(",", "").replace("+", "").strip())


def parse_page(text: str, kind: str) -> dict[str, pd.Series]:
    """بيانات الرسم (صافي كل فئة لكل أسبوع) من صفحة Tradingster، بعد التحقق من العقد والتاريخ."""
    if CODE not in text or MARKET not in htmllib.unescape(text):
        raise CotError(f"{kind}: الصفحة ليست لعقد {MARKET} ({CODE})")
    m = re.search(r"var\s+dataNet\s*=\s*\[(.*?)\]\s*;", text, re.S)
    if not m:
        raise CotError(f"{kind}: لا بيانات رسم (dataNet) في الصفحة؛ تغيّر تصميمها")
    cols: dict[str, dict[pd.Timestamp, float]] = {}
    for day, rest in _ROW.findall(m.group(1)):
        for key, val in _NUM.findall(rest):
            cols.setdefault(key, {})[pd.Timestamp(day)] = float(val)
    wanted = {f for k, f in SERIES.values() if k == kind}
    if not wanted <= set(cols):
        raise CotError(f"{kind}: حقول ناقصة في بيانات الرسم: {sorted(wanted - set(cols))}")
    out = {f: pd.Series(cols[f], dtype="float64").sort_index() for f in wanted}
    last = max(s.index[-1] for s in out.values())
    asof = re.search(r"AS OF:\s*(\d{4}-\d{2}-\d{2})", text)
    if not asof or pd.Timestamp(asof.group(1)) != last:
        raise CotError(f"{kind}: تاريخ آخر صف ({last:%Y-%m-%d}) لا يطابق AS OF في رأس الصفحة "
                       f"({asof.group(1) if asof else 'غير موجود'})")
    if kind == "legacy":
        _check_legacy_table(text, out["NonCommercial"])
    return out


def _check_legacy_table(text: str, nc: pd.Series) -> None:
    """أول صف أرقام في جدول Legacy: Non-Commercial Long، Short، ثم البقية. صافيه يجب أن يساوي آخر صف في الرسم."""
    body = re.search(r"Open Interest:.*?</tr>\s*<tr>(.*?)</tr>", text, re.S)
    cells = re.findall(r"<td[^>]*>(.*?)</td>", body.group(1), re.S) if body else []
    try:
        net = _int(cells[0]) - _int(cells[1])
    except (IndexError, ValueError):
        raise CotError("legacy: جدول المراكز غير متوقع؛ تغيّر تصميم الصفحة") from None
    if net != int(nc.iloc[-1]):
        raise CotError(f"legacy: صافي الجدول ({net}) لا يساوي صافي الرسم ({int(nc.iloc[-1])})")


def _get(url: str, retries: int = 3) -> str:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise CotError(f"تعذّر جلب {url}: {type(last).__name__}: {str(last)[:120]}")


_CACHE: dict[str, dict[str, pd.Series]] = {}   # صفحة واحدة لكل تشغيل، تتقاسمها سلاسلها


def clear_cache() -> None:
    _CACHE.clear()


def fetch_series(series_id: str, start: str = "2000-01-01", getter=_get) -> pd.Series:
    if series_id not in SERIES:
        raise CotError(f"{series_id}: ليست سلسلة COT معروفة")
    kind, field = SERIES[series_id]
    if kind not in _CACHE:
        _CACHE[kind] = parse_page(getter(PAGES[kind]), kind)
    s = _CACHE[kind][field]
    return s[s.index >= pd.Timestamp(start)].rename(series_id)


def due(last_as_of: pd.Timestamp, now: datetime | None = None) -> bool:
    """هل يمكن أن يكون إصدار أحدث من last_as_of قد صدر؟ قبل ذلك لا داعي لطلب الصفحة."""
    now = now or datetime.now(timezone.utc)
    t = pd.Timestamp(last_as_of).tz_localize(None).normalize().to_pydatetime().replace(tzinfo=timezone.utc)
    return now >= t + DUE_AFTER
