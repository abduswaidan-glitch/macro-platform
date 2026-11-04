"""عميل FRED: يجلب السلاسل ويخزنها محلياً كـCSV حتى لا يُعاد الجلب كل مرة.

يحتاج مفتاح API مجاني من FRED في المتغير البيئي FRED_API_KEY.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


class FredError(RuntimeError):
    pass


def get_api_key() -> str:
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        raise FredError("لم يُضبط FRED_API_KEY. احصل على مفتاح مجاني من fred.stlouisfed.org ثم صدّره في البيئة.")
    return key


def parse_observations(payload: dict) -> pd.Series:
    """يحوّل ردّ FRED JSON إلى سلسلة زمنية؛ القيمة '.' تعني لا بيانات (عطلة/تأخير) فتُحذف."""
    rows = payload.get("observations")
    if rows is None:
        raise FredError("ردّ غير متوقع من FRED")
    idx = pd.to_datetime([r["date"] for r in rows])
    vals = [None if r["value"] in (".", "") else float(r["value"]) for r in rows]
    return pd.Series(vals, index=idx, dtype="float64").dropna()


def fetch_series(series_id: str, api_key: str, start: str = "1990-01-01",
                 timeout: float = 30.0, retries: int = 3) -> pd.Series:
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start,
    }
    url = FRED_URL + "?" + urllib.parse.urlencode(params)
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return parse_observations(payload)
        except Exception as exc:  # لا نطبع الرابط لأنه يحمل المفتاح
            last_err = exc
            if attempt < retries - 1:
                time.sleep(2**attempt)
    raise FredError(f"{series_id}: فشل الجلب ({type(last_err).__name__})")


class SeriesStore:
    """تخزين محلي: cache_dir/<SERIES>.csv"""

    def __init__(self, cache_dir: str | Path, api_key: str | None = None):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._api_key = api_key

    def path(self, series_id: str) -> Path:
        return self.cache_dir / f"{series_id}.csv"

    def load_cached(self, series_id: str) -> pd.Series | None:
        p = self.path(series_id)
        if not p.exists():
            return None
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        return df.iloc[:, 0].astype("float64")

    def get(self, series_id: str, start: str = "1990-01-01", refresh: bool = False) -> pd.Series:
        if not refresh:
            cached = self.load_cached(series_id)
            if cached is not None:
                return cached
        if self._api_key is None:
            self._api_key = get_api_key()
        s = fetch_series(series_id, self._api_key, start=start)
        s.rename(series_id).to_csv(self.path(series_id), header=[series_id])
        return s

    def get_all(self, series_ids, start: str = "1990-01-01", refresh: bool = False,
                on_error=None) -> dict[str, pd.Series]:
        out: dict[str, pd.Series] = {}
        for sid in series_ids:
            try:
                out[sid] = self.get(sid, start=start, refresh=refresh)
            except FredError as exc:
                if on_error is None:
                    raise
                on_error(sid, exc)
        return out
