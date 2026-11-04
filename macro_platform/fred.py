"""جلب سلاسل FRED من fredgraph.csv: نفس الرابط العام الذي تستخدمه منصة الذهب، ولا يحتاج مفتاح API.

خوادم FRED تحجب أحياناً طلبات GitHub Actions العادية، فنجرّب أولاً curl_cffi (بصمة متصفح Chrome)
ثم urllib كخيار احتياطي.
"""

from __future__ import annotations

import io
import time
import urllib.request

import pandas as pd

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"
TIMEOUT = 40
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


class FredError(RuntimeError):
    pass


def parse_csv(text: str, series_id: str = "") -> pd.Series:
    """CSV من FRED: عمودان (التاريخ، القيمة). '.' أو فراغ = لا قيمة (عطلة) فتُحذف."""
    if text.lstrip().startswith("<"):
        raise FredError(f"{series_id}: الرد صفحة HTML وليس CSV (حجب أو خطأ)")
    df = pd.read_csv(io.StringIO(text))
    if df.shape[1] < 2 or df.empty:
        raise FredError(f"{series_id}: CSV فارغ أو غير متوقع")
    idx = pd.to_datetime(df.iloc[:, 0], errors="coerce")
    vals = pd.to_numeric(df.iloc[:, 1], errors="coerce")
    s = pd.Series(vals.to_numpy(dtype="float64"), index=pd.DatetimeIndex(idx), name=series_id)
    s = s[s.index.notna()].dropna().sort_index()
    return s[~s.index.duplicated(keep="last")]


def _get(url: str) -> str:
    errors = []
    try:
        from curl_cffi import requests as creq  # اختياري: مثبّت في GitHub Actions

        for imp in ("chrome", "safari"):
            try:
                r = creq.get(url, impersonate=imp, timeout=TIMEOUT, headers={"Accept": "text/csv"})
                if r.status_code == 200:
                    return r.text
                errors.append(f"{imp}: HTTP {r.status_code}")
            except Exception as exc:
                errors.append(f"{imp}: {type(exc).__name__}")
    except ImportError:
        pass
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/csv"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception as exc:
        errors.append(f"urllib: {type(exc).__name__}: {str(exc)[:80]}")
    raise FredError(" | ".join(errors)[:300])


def fetch_series(series_id: str, start: str = "2000-01-01", retries: int = 3) -> pd.Series:
    url = FRED_CSV.format(sid=series_id, start=start)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return parse_csv(_get(url), series_id)
        except Exception as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise FredError(f"{series_id}: {last}")
