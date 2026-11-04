import json
import math

import numpy as np
import pandas as pd
import pytest

from macro_platform import fred
from macro_platform.analytics import (SAHM_TRIGGER, growth_inflation_quadrant, sahm_rule, signals,
                                      snapshot, transform)
from macro_platform.catalog import INDICATORS, validate_catalog
from macro_platform.cli import main
from macro_platform.dashboard import build_html
from macro_platform.demo import demo_series
from macro_platform.fred import FredError, SeriesStore, parse_observations


def test_catalog_valid_and_unique():
    validate_catalog()
    assert len({i.id for i in INDICATORS}) == len(INDICATORS) >= 20


def test_yoy_of_geometric_series_is_growth_rate():
    idx = pd.date_range("2020-01-01", periods=30, freq="MS")
    s = pd.Series(100 * 1.01 ** np.arange(30), index=idx)
    yoy = transform(s, "yoy", "M").dropna()
    assert yoy.iloc[-1] == pytest.approx((1.01 ** 12 - 1) * 100, rel=1e-9)


def test_diff_avg3_is_three_month_average_change():
    idx = pd.date_range("2020-01-01", periods=6, freq="MS")
    s = pd.Series([0, 100, 200, 300, 400, 500], index=idx, dtype=float)
    out = transform(s, "diff_avg3", "M").dropna()
    assert out.iloc[-1] == pytest.approx(100.0)


def test_parse_observations_drops_missing_dots():
    payload = {"observations": [
        {"date": "2024-01-01", "value": "4.1"},
        {"date": "2024-01-02", "value": "."},
        {"date": "2024-01-03", "value": "4.3"},
    ]}
    s = parse_observations(payload)
    assert list(s.values) == [4.1, 4.3]


def test_fetch_retries_and_error_does_not_leak_key(monkeypatch):
    calls = {"n": 0}

    def boom(url, timeout):
        calls["n"] += 1
        raise OSError("network down")

    monkeypatch.setattr(fred.time, "sleep", lambda s: None)
    monkeypatch.setattr(fred.urllib.request, "urlopen", boom)
    with pytest.raises(FredError) as exc:
        fred.fetch_series("GDP", "SECRETKEY123", retries=3)
    assert calls["n"] == 3
    assert "SECRETKEY123" not in str(exc.value)


def test_missing_api_key_gives_clear_error(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(FredError, match="FRED_API_KEY"):
        fred.get_api_key()


def test_store_uses_cache_without_network(tmp_path, monkeypatch):
    store = SeriesStore(tmp_path)
    idx = pd.date_range("2024-01-01", periods=3, freq="MS")
    pd.Series([1.0, 2.0, 3.0], index=idx, name="UNRATE").to_csv(tmp_path / "UNRATE.csv", header=["UNRATE"])

    def no_net(*a, **k):
        raise AssertionError("network must not be called when cached")

    monkeypatch.setattr(fred, "fetch_series", no_net)
    s = store.get("UNRATE")
    assert list(s.values) == [1.0, 2.0, 3.0]


def test_sahm_rule_triggers_on_sharp_rise():
    idx = pd.date_range("2019-01-01", periods=24, freq="MS")
    vals = [3.6] * 18 + [3.7, 3.9, 4.1, 4.3, 4.5, 4.7]
    gap = sahm_rule(pd.Series(vals, index=idx)).dropna()
    assert gap.iloc[-1] >= SAHM_TRIGGER
    flat = sahm_rule(pd.Series([4.0] * 24, index=idx)).dropna()
    assert flat.abs().max() < 1e-9


def test_quadrant_labels():
    idx = pd.date_range("2022-01-01", periods=36, freq="MS")
    up = pd.Series(100 * 1.02 ** np.arange(36), index=idx)  # industrial growth positive
    jobs = pd.Series(np.arange(36) * 100.0 + 1000, index=idx)  # payrolls rising
    hot = pd.Series(100 * 1.04 ** (np.arange(36) / 12), index=idx)  # core PCE ~4% yoy
    cool = pd.Series(100 * 1.015 ** (np.arange(36) / 12), index=idx)  # ~1.5% yoy
    series = {"INDPRO": up, "PAYEMS": jobs, "PCEPILFE": hot}
    assert growth_inflation_quadrant(series)["label_ar"].startswith("تسخين")
    series["PCEPILFE"] = cool
    assert growth_inflation_quadrant(series)["label_ar"].startswith("نمو بلا تضخم")
    series["INDPRO"] = pd.Series(100 - np.arange(36) * 0.5, index=idx)
    assert growth_inflation_quadrant(series)["growth_up"] is False


def test_snapshot_and_signals_on_demo_data():
    series = demo_series(start="2015-01-01", end="2025-12-31")
    snap = snapshot(series)
    assert len(snap) == len(INDICATORS)
    assert snap["last"].notna().all()
    tones = {c["title"]: c["tone"] for c in signals(series)}
    assert "Sahm Rule" in tones and "مؤشر VIX" in tones


def test_html_is_rtl_arabic_and_escapes(tmp_path):
    series = demo_series(start="2015-01-01", end="2025-12-31")
    page = build_html(series, demo=True)
    assert 'dir="rtl"' in page and 'lang="ar"' in page
    assert "<script" not in page  # لا سكربتات خارجية
    assert "بيانات تجريبية" in page
    assert page.count("<svg") >= len(INDICATORS)


def test_cli_demo_report_writes_file(tmp_path, capsys):
    out = tmp_path / "index.html"
    assert main(["report", "--demo", "--out", str(out)]) == 0
    assert out.exists() and out.stat().st_size > 5000
    assert main(["list"]) == 0
