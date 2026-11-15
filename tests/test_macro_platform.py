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


# ---------------------------------------------------------------- AI report (schema 2)
from _report import body, caller_for, text  # noqa: E402


def test_parse_report_extracts_last_block_and_keeps_probabilities_as_written():
    b = {k: v for k, v in body().items() if k != "market_translation"}     # مخرج الخطوة الاقتصادية وحدها
    b["economy"]["scenarios"][0]["prob"] = 46                              # المجموع 101: يُعرض كما هو، لا تعديل صامت
    rep = ai_report.parse_report("بحث...<report>{\"economy\": {}}</report> ثم " + text(b))
    assert [s["prob"] for s in rep["scenarios"]] == [46, 35, 20]
    assert rep["warnings"] == ["scenarios: probabilities sum to 101, not 100"]
    # حقول توافق مشتقة، فالواجهة القديمة تعرض التقرير الجديد مختصراً لا مكسوراً
    assert rep["title"] == "عنوان اليوم" and rep["summary"] == ["القراءة الاقتصادية", "أكبر تغير"]
    assert rep["one_liner"] == "الخلاصة" and rep["scenarios"][0]["trigger"] == "يؤكده A"
    assert rep["sections"][0] == {"heading": "النمو", "body": "فقرة النمو"}


def test_parse_report_rejects_only_broken_output():
    b = body()
    no_read = body(); no_read["economy"]["executive"]["read"] = ""
    no_bottom = body(); del no_bottom["economy"]["bottom_line"]
    legacy = {"title": "t", "summary": ["s"], "scenarios": [], "one_liner": "o"}
    for broken in ("no tags here", "<report>{bad json</report>", text(legacy), text(no_read), text(no_bottom)):
        with pytest.raises(ai_report.ReportError):
            ai_report.parse_report(broken)
    two = body(scenarios=b["economy"]["scenarios"][:2])                    # يُنشر ولا يُقاس
    assert len(ai_report.parse_report(text(two))["scenarios"]) == 2


def test_write_report_with_fake_model_and_history(demo, cfg, tmp_path):
    payload = build_payload(demo, {}, cfg, now=NOW)
    seen = {}
    rep = ai_report.write_report(payload, cfg, now_utc=NOW, caller=caller_for(seen=seen))
    assert seen["model"] == cfg["ai"]["model"] and seen["effort"] == "xhigh"
    assert '"points"' not in seen["user"]  # السياق مضغوط بلا السلاسل الكاملة
    assert "أنت محلل" in seen["system"]  # تعليمات prompts/analyst.md وصلت
    assert "<!--" not in seen["system"] and "انحيازك لـNQ" not in seen["system"]
    assert rep["meta"]["schema"] == 2 and rep["meta"]["trigger"] == "manual" and rep["meta"]["attempt"] == "manual"
    hist_p = tmp_path / "h.json"
    ai_report.update_history(hist_p, rep, keep=2)
    ai_report.update_history(hist_p, rep, keep=2)  # نفس اليوم: يستبدل ولا يكرر
    assert len(json.loads(hist_p.read_text(encoding="utf-8"))) == 1


# ---------------------------------------------------------------- offline rebuild at deploy time
def test_offline_rebuild_uses_saved_series_and_keeps_fetch_warnings(tmp_path, monkeypatch, demo):
    import fetch_macro
    from macro_platform.build import save_raw

    data = tmp_path / "data"
    for sid in ("DGS10", "VIXCLS", "UNRATE"):
        save_raw(data / "raw", sid, demo[sid])
    fetch_macro.save_status(data / "fetch_status.json",
                            {"DGS10": {"state": "error", "issues": ["فشل الجلب: blocked"]}},
                            ["فشل كتابة تقرير Claude: x"], NOW)
    monkeypatch.setattr(fetch_macro, "DATA", data)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-be-used")
    monkeypatch.setattr(fetch_macro.ai_report, "write_report",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no AI in offline mode")))
    out = tmp_path / "data.json"
    assert fetch_macro.main(["--offline", "--out", str(out)]) == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    ids = {i["id"]: i for i in d["indicators"] if "points" in i}
    assert set(ids) == {"DGS10", "VIXCLS", "UNRATE"}
    assert ids["DGS10"]["state"] == "error"
    assert any("فشل كتابة تقرير Claude" in w for w in d["status"]["warnings"])
    assert not (data / "state.json").exists()


# ---------------------------------------------------------------- the Opus report call: streaming, caching, no fallback
class _Usage:
    def __init__(self, i, o, cw, cr, searches=0):
        self.input_tokens, self.output_tokens = i, o
        self.cache_creation_input_tokens, self.cache_read_input_tokens = cw, cr
        self.server_tool_use = type("S", (), {"web_search_requests": searches})()


class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Msg:
    def __init__(self, stop, usage, text, model="claude-opus-5-5"):
        self.model, self.stop_reason, self.usage, self.content = model, stop, usage, [_Block(text)]


def _fake_anthropic(monkeypatch, replies, calls):
    import sys
    import types

    class Stream:
        def __init__(self, msg):
            self.msg = msg

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return self.msg

    class Client:
        def __init__(self):
            self.messages = types.SimpleNamespace(stream=self.stream)   # المسار العادي، لا beta

        def stream(self, **kw):
            calls.append(kw)
            return Stream(replies[len(calls) - 1])

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Client))


def test_call_claude_streams_with_caching_xhigh_and_no_fallback(monkeypatch):
    calls = []
    _fake_anthropic(monkeypatch, [_Msg("pause_turn", _Usage(100, 50, 20000, 0, 2), "part1 "),
                                  _Msg("end_turn", _Usage(80, 900, 500, 19000, 3), "part2")], calls)
    text, sources, usage, served, info = ai_report.call_claude("data", "SYSTEM", "claude-opus-5-5", "xhigh")

    assert text == "part1 part2" and len(calls) == 2 and served == "claude-opus-5-5"   # pause_turn ثم إكمال
    for kw in calls:
        assert kw["cache_control"] == {"type": "ephemeral"}       # caching تلقائي للمحادثة
        assert kw["system"][-1]["cache_control"] == {"type": "ephemeral"}  # نقطة صريحة على التعليمات الثابتة
        assert kw["system"][-1]["text"] == "SYSTEM"
        assert kw["max_tokens"] == 128000 and kw["output_config"] == {"effort": "xhigh"}
        assert kw["thinking"] == {"type": "adaptive"} and kw["tools"][0]["max_uses"] == 8
        assert "fallbacks" not in kw and "betas" not in kw         # قرار Abdu: لا Cross-model fallback
    assert usage == {"input_tokens": 180, "output_tokens": 950, "cache_creation_input_tokens": 20500,
                     "cache_read_input_tokens": 19000, "web_search_requests": 5}
    assert info["stop_reason"] == "end_turn" and info["duration_s"] >= 0
    # 180*4 + 950*20 + 20500*5 + 19000*0.20 = 126020 / 1e6، + 5 عمليات بحث × 0.01$
    assert ai_report.estimate_cost("claude-opus-5-5", usage) == round(126020 / 1e6 + 0.05, 4)


def test_no_fallback_anywhere_in_the_report_code():
    code = open("macro_platform/ai_report.py", encoding="utf-8").read()
    assert "fallbacks=" not in code and "server-side-fallback" not in code and "betas=" not in code
    assert "client.messages.stream(" in code and "client.beta" not in code


@pytest.mark.parametrize("replies, why", [
    ([_Msg("refusal", _Usage(10, 0, 0, 0), "")], "refusal"),
    ([_Msg("max_tokens", _Usage(10, 128000, 0, 0), "<report>{")], "max_tokens"),
    ([_Msg("pause_turn", _Usage(1, 1, 0, 0), "x")] * 5, "pause_turn did not end"),
])
def test_stop_reasons_that_block_publication_are_retryable_failures_with_usage(monkeypatch, replies, why):
    calls = []
    _fake_anthropic(monkeypatch, replies, calls)
    with pytest.raises(ai_report.ReportError) as e:
        ai_report.call_claude("data", "S", "claude-opus-5-5", "xhigh")
    assert why in str(e.value) and isinstance(e.value.usage, dict) and e.value.info["stop_reason"]
