"""ربط إصدارات التقويم بسلاسل المنصة، وعلَم «صدر اليوم ولم يصل إلى بيانات المنصة».

لماذا (قياس 6 إلى 13 نوفمبر 2026، آخر تحديث في FRED بعد إصدار 08:30 بتوقيت نيويورك): CPI ظهر 09:12، والوظائف
والأجور 10:42، وPPI 10:20، والمطالبات 08:37. تقرير 09:00 لا يجد إذن أغلب أرقام صباحه في بيانات المنصة؛ هذا العلَم
يقول له ذلك صراحة فيأخذها من الإصدار الرسمي عبر البحث، مصنَّفة كذلك، لا بيانات منصة.

القاعدة حتمية: حدث أمريكي في تقويم اليوم (بتوقيت نيويورك) مضى وقته، وعنوانه في الجدول، وسلسلته لم تُسجَّل لها قراءة
جديدة منذ وقت الحدث (last_new_obs_utc من fetch_status.json). الجدول نفسه يخدم قواعد الخطورة في الواجهة لاحقاً.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

# عناوين تقويم ForexFactory (USD) كما تصل، مقابل سلاسل المنصة التي يغيّرها الإصدار. ما ليس هنا لا يُعلَّم.
RELEASES: dict[str, tuple[str, ...]] = {
    "CPI m/m": ("CPIAUCSL", "CPILFESL"), "CPI y/y": ("CPIAUCSL", "CPILFESL"), "Core CPI m/m": ("CPIAUCSL", "CPILFESL"),
    "Non-Farm Employment Change": ("PAYEMS", "UNRATE", "CES0500000003"),
    "Unemployment Rate": ("PAYEMS", "UNRATE", "CES0500000003"),
    "Average Hourly Earnings m/m": ("PAYEMS", "UNRATE", "CES0500000003"),
    "Unemployment Claims": ("ICSA", "CCSA"),
    "Core PCE Price Index m/m": ("PCEPILFE",), "Core PCE Price Index y/y": ("PCEPILFE",),
    "Retail Sales m/m": ("RSAFS",), "Core Retail Sales m/m": ("RSAFS",),
    "JOLTS Job Openings": ("JTSJOL",),
    "Advance GDP q/q": ("GDPC1",), "Prelim GDP q/q": ("GDPC1",), "Final GDP q/q": ("GDPC1",),
    "Industrial Production m/m": ("INDPRO",),
    "Housing Starts": ("HOUST",),
    "Prelim UoM Consumer Sentiment": ("UMCSENT",), "Revised UoM Consumer Sentiment": ("UMCSENT",),
}
_KEY = {k.lower(): v for k, v in RELEASES.items()}


def _t(s) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def series_for(title) -> tuple[str, ...]:
    return _KEY.get(str(title or "").strip().lower(), ())


def not_in_platform(calendar: list[dict] | None, indicators: list[dict], now_utc: datetime) -> list[dict]:
    """إصدارات اليوم التي مضى وقتها ولم تصل قراءتها إلى بيانات المنصة بعد."""
    by_id = {i.get("id"): i for i in indicators or [] if isinstance(i, dict)}
    today = now_utc.astimezone(NY).date()
    out, seen = [], set()
    for e in calendar or []:
        t = _t((e or {}).get("timeUtc"))
        ids = series_for((e or {}).get("title"))
        if t is None or not ids or t > now_utc or t.astimezone(NY).date() != today:
            continue
        late = []
        for sid in ids:
            ind = by_id.get(sid)
            if ind is None:
                continue
            seen_at = _t(ind.get("last_new_obs_utc"))
            if seen_at is None or seen_at < t:
                late.append({"id": sid, "platform_as_of": ind.get("as_of")})
        key = (e.get("title"), e.get("timeUtc"))
        if late and key not in seen:
            seen.add(key)
            out.append({"event": e.get("title"), "time_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "time_ny": t.astimezone(NY).strftime("%H:%M"), "series_not_updated": late})
    return out
