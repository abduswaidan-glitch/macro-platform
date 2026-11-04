"""بيانات تجريبية مولّدة (ليست بيانات حقيقية). تُستخدم لتجربة اللوحة بدون مفتاح FRED ولاختبارات المنطق فقط."""

from __future__ import annotations

import numpy as np
import pandas as pd

from macro_platform.catalog import INDICATORS, PERIODS_PER_YEAR

FREQ_RULE = {"D": "B", "W": "W-FRI", "M": "MS", "Q": "QS"}

# id -> (القيمة الأساسية، نمو سنوي، تذبذب لوغاريتمي لكل فترة)
PARAMS = {
    "GDPC1": (20000, 0.02, 0.006), "INDPRO": (100, 0.01, 0.004), "RSAFS": (650000, 0.04, 0.008),
    "HOUST": (1400, 0.0, 0.05), "PAYEMS": (150000, 0.012, 0.0015), "UNRATE": (4.0, 0.0, 0.05),
    "ICSA": (220000, 0.0, 0.05), "JTSJOL": (8000, 0.0, 0.03), "CPIAUCSL": (300, 0.03, 0.0015),
    "CPILFESL": (300, 0.03, 0.0012), "PCEPILFE": (120, 0.028, 0.0012), "FEDFUNDS": (4.0, 0.0, 0.02),
    "DGS2": (4.0, 0.0, 0.01), "DGS10": (4.2, 0.0, 0.008), "T10Y2Y": (0.2, 0.0, 0.03),
    "DFII10": (1.8, 0.0, 0.01), "T10YIE": (2.3, 0.0, 0.006), "MORTGAGE30US": (6.5, 0.0, 0.01),
    "M2SL": (21000, 0.04, 0.003), "WALCL": (7.5e6, 0.0, 0.003), "BAMLH0A0HYM2": (3.5, 0.0, 0.03),
    "VIXCLS": (18.0, 0.0, 0.04), "DTWEXBGS": (120, 0.0, 0.004), "DCOILWTICO": (70, 0.0, 0.02),
    "UMCSENT": (70, 0.0, 0.03),
}


def generate_series(indicator_id: str, freq: str, start: str, end: str, seed: int) -> pd.Series:
    base, growth, vol = PARAMS[indicator_id]
    idx = pd.date_range(start, end, freq=FREQ_RULE[freq])
    rng = np.random.default_rng(seed)
    n = len(idx)
    per = 1 / PERIODS_PER_YEAR[freq]
    y = np.zeros(n)
    for t in range(1, n):
        y[t] = 0.97 * y[t - 1] + rng.normal(0, vol)  # انعكاس نحو المتوسط
    trend = np.log1p(growth) * per * np.arange(n)
    values = base * np.exp(y + trend)
    return pd.Series(values, index=idx, name=indicator_id)


def demo_series(start: str = "2000-01-01", end: str = "2026-06-30") -> dict[str, pd.Series]:
    out = {}
    for seed, ind in enumerate(INDICATORS):
        out[ind.id] = generate_series(ind.id, ind.freq, start, end, seed=seed + 1)
    return out
