import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from macro_platform import ai_report
from macro_platform.analytics import SAHM_TRIGGER, growth_inflation_quadrant, sahm_rule, signals, snapshot, transform
from macro_platform.build import (build_payload, chart_points, fetch_all, load_config, nq_gates, validate,
                                  write_json)
from macro_platform.catalog import BY_ID, INDICATORS, validate_catalog
from macro_platform.demo import demo_series
from macro_platform.fred import FredError, parse_csv

NOW = datetime(2026, 6, 30, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def demo():
    return demo_series(start="2015-01-01", end="2026-06-30")


@pytest.fixture(scope="module")
def cfg():
    return load_config("config.json")


# ---------------------------------------------------------------- catalog & transforms
def test_catalog_valid_and_unique():
    validate_catalog()
    assert len({i.id for i in INDICATORS}) == len(INDICATORS) >= 25


def test_yoy_of_geometric_series_is_growth_rate():
    idx = pd.date_range("2020-01-01", periods=30, freq="MS")
    s = pd.Series(100 * 1.01 ** np.arange(30), index=idx)
    assert transform(s, "yoy", "M").dropna().iloc[-1] == pytest.approx((1.01 ** 12 - 1) * 100)


def test_diff_avg3():
    idx = pd.date_range("2020-01-01", periods=6, freq="MS")
    s = pd.Series([0, 100, 200, 300, 400, 500], index=idx, dtype=float)
    assert transform(s, "diff_avg3", "M").dropna().iloc[-1] == pytest.approx(100.0)


def test_sahm_rule_triggers_on_sharp_rise():
    idx = pd.date_range("2019-01-01", periods=24, freq="MS")
    vals = [3.6] * 18 + [3.7, 3.9, 4.1, 4.3, 4.5, 4.7]
    assert sahm_rule(pd.Series(vals, index=idx)).dropna().iloc[-1] >= SAHM_TRIGGER
    assert sahm_rule(pd.Series([4.0] * 24, index=idx)).dropna().abs().max() < 1e-9


def test_quadrant_labels():
    idx = pd.date_range("2022-01-01", periods=36, freq="MS")
    series = {"INDPRO": pd.Series(100 * 1.02 ** np.arange(36), index=idx),
              "PAYEMS": pd.Series(np.arange(36) * 100.0 + 1000, index=idx),
              "PCEPILFE": pd.Series(100 * 1.04 ** (np.arange(36) / 12), index=idx)}
    assert growth_inflation_quadrant(series)["label_ar"].startswith("تسخين")
    series["PCEPILFE"] = pd.Series(100 * 1.015 ** (np.arange(36) / 12), index=idx)
    assert growth_inflation_quadrant(series)["label_ar"].startswith("نمو بلا تضخم")
    series["INDPRO"] = pd.Series(100 - np.arange(36) * 0.5, index=idx)
    assert growth_inflation_quadrant(series)["growth_up"] is False


# ---------------------------------------------------------------- FRED CSV
def test_parse_csv_drops_missing_and_sorts():
    s = parse_csv("observation_date,DGS10\n2024-01-03,4.3\n2024-01-01,4.1\n2024-01-02,.\n", "DGS10")
    assert list(s.values) == [4.1, 4.3] and s.index.is_monotonic_increasing


def test_parse_csv_rejects_html_block_page():
    with pytest.raises(FredError):
        parse_csv("<!doctype html><html>Access denied</html>", "DGS10")


# ---------------------------------------------------------------- validation & last-good fallback
def _daily(n=400, start="2024-01-01", base=4.0):
    idx = pd.bdate_range(start, periods=n)
    return pd.Series(base + np.sin(np.arange(n) / 20) * 0.1, index=idx)


def test_validate_rejects_short_and_regressing_series():
    ind = BY_ID["DGS10"]
    ok, _ = validate(ind, _daily(50), None)
    assert not ok
    old = _daily(400)
    ok, issues = validate(ind, old.iloc[:-10], old)
    assert not ok and "أقدم" in issues[0]


def test_validate_flags_unusual_jump_but_accepts():
    s = _daily(400)
    s.iloc[-1] += 5
    ok, issues = validate(BY_ID["DGS10"], s, None)
    assert ok and any("قفزة" in i for i in issues)


def test_fetch_all_keeps_last_good_on_failure(tmp_path):
    good = _daily(400)
    inds = [BY_ID["DGS10"]]
    series, status = fetch_all(tmp_path, "2000-01-01", fetcher=lambda sid, start: good, indicators=inds,
                               log=lambda *a: None)
    assert status["DGS10"]["state"] == "ok" and (tmp_path / "DGS10.csv").exists()

    def broken(sid, start):
        raise FredError("blocked")

    series, status = fetch_all(tmp_path, "2000-01-01", fetcher=broken, indicators=inds, log=lambda *a: None)
    assert status["DGS10"]["state"] == "error"
    assert len(series["DGS10"]) == len(good)  # آخر نسخة سليمة ما زالت معروضة
    assert any("آخر نسخة سليمة" in i for i in status["DGS10"]["issues"])


# ---------------------------------------------------------------- payload
def test_chart_points_daily_is_weekly_then_daily():
    ind = BY_ID["VIXCLS"]
    s = pd.Series(np.linspace(10, 30, 3000), index=pd.bdate_range("2014-01-01", periods=3000))
    pts = chart_points(ind, s)
    assert len(pts) < 3000 and pts[-1][0] == s.index[-1].strftime("%Y-%m-%d")


def test_gates_trigger_logic(cfg):
    idx = pd.bdate_range("2026-01-01", periods=60)
    series = {"DGS10": pd.Series(np.full(60, cfg["gates"]["us10y_trigger"] + 0.20), index=idx),
              "VIXCLS": pd.Series(np.full(60, 30.0), index=idx)}
    g = {x["key"]: x for x in nq_gates(series, cfg)}
    assert g["us10y"]["tone"] == "bad" and "+20 bp" in g["us10y"]["detail"]
    assert g["vix"]["tone"] == "bad"


def test_build_payload_is_strict_json_and_complete(demo, cfg, tmp_path):
    payload = build_payload(demo, {}, cfg, now=NOW, demo=True)
    out = tmp_path / "data.json"
    write_json(out, payload)  # allow_nan=False: يفشل إن تسرّب NaN
    back = json.loads(out.read_text(encoding="utf-8"))
    assert back["status"]["total"] == len(INDICATORS)
    assert len(back["kpis"]) >= 6 and back["gates"] and back["signals"]
    assert all(i["points"] for i in back["indicators"])
    assert back["quadrant"]["label_ar"]


def test_payload_marks_stale_series(demo, cfg):
    later = datetime(2026, 12, 31, tzinfo=timezone.utc)
    payload = build_payload(demo, {}, cfg, now=later)
    vix = next(i for i in payload["indicators"] if i["id"] == "VIXCLS")
    assert vix["state"] == "stale" and payload["status"]["warnings"]


def test_snapshot_and_signals_on_demo(demo):
    assert len(snapshot(demo)) == len(INDICATORS)
    assert {"Sahm Rule", "مؤشر VIX"} <= {c["title"] for c in signals(demo)}


# ---------------------------------------------------------------- AI report
GOOD = {"title": "t", "summary": ["a"], "bias": {"direction": "neutral", "confidence": 50, "reason": "r"},
        "scenarios": [{"label": "A", "name": "a", "prob": 50, "trigger": "x", "reason": "y"},
                      {"label": "B", "name": "b", "prob": 30, "trigger": "x", "reason": "y"},
                      {"label": "C", "name": "c", "prob": 19, "trigger": "x", "reason": "y"}],
        "one_liner": "o"}


def test_parse_report_extracts_last_block_and_fixes_rounding():
    text = ("بحث...<report>{\"title\": \"old\"}</report> ثم <report>"
            + json.dumps(GOOD, ensure_ascii=False) + "</report>")
    rep = ai_report.parse_report(text)
    assert rep["title"] == "t" and sum(s["prob"] for s in rep["scenarios"]) == 100
    assert rep["sections"] == [] and rep["events"] == []


def test_parse_report_rejects_bad_output():
    with pytest.raises(ai_report.ReportError):
        ai_report.parse_report("no tags here")
    bad = dict(GOOD, scenarios=GOOD["scenarios"][:2])
    with pytest.raises(ai_report.ReportError):
        ai_report.parse_report("<report>" + json.dumps(bad) + "</report>")


def test_should_run_schedule():
    cfg = {"ny_hour": 8, "ny_minute": 30}
    # الأربعاء 2026-11-04: 13:40 UTC = 8:40 EST
    assert ai_report.should_run(datetime(2026, 11, 4, 13, 40, tzinfo=timezone.utc), "2026-11-03", cfg)
    assert not ai_report.should_run(datetime(2026, 11, 4, 13, 40, tzinfo=timezone.utc), "2026-11-04", cfg)
    assert not ai_report.should_run(datetime(2026, 11, 4, 13, 20, tzinfo=timezone.utc), None, cfg)  # 8:20 قبل الموعد
    assert not ai_report.should_run(datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc), None, cfg)   # السبت
    assert ai_report.should_run(datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc), None, cfg, force=True)


def test_write_report_with_fake_model_and_history(demo, cfg, tmp_path):
    payload = build_payload(demo, {}, cfg, now=NOW)
    seen = {}

    def fake(user, system, model, effort):
        seen.update(user=user, system=system, model=model)
        return ("<report>" + json.dumps(GOOD) + "</report>", [{"url": "https://x.test", "title": "x"}],
                {"input_tokens": 1, "output_tokens": 1}, model)

    rep = ai_report.write_report(payload, cfg, now_utc=NOW, caller=fake)
    assert seen["model"] == cfg["ai"]["model"]
    assert '"points"' not in seen["user"]  # السياق مضغوط بلا السلاسل الكاملة
    assert "أنت محلل" in seen["system"]  # تعليمات prompts/analyst.md وصلت
    assert "<!--" not in seen["system"]
    hist_p = tmp_path / "h.json"
    ai_report.update_history(hist_p, rep, keep=2)
    ai_report.update_history(hist_p, rep, keep=2)  # نفس اليوم: يستبدل ولا يكرر
    assert len(json.loads(hist_p.read_text(encoding="utf-8"))) == 1
