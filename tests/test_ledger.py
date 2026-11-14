"""سجل التوقعات: ما يلتزم به التقرير قابل للقياس، ويحسمه الكود من سلاسل FRED نفسها، ولا يحسم قبل وصول البيانات.

الدليل الذي بُني عليه (data/ai_history.json، 4 إلى 13 نوفمبر 2026): الثقة 55% في التقارير السبعة كلها، وشروط
السيناريوهات نص مركّب بلا تاريخ حسم، ولا سجل للنتائج. فلا دقة ولا معايرة ولا مقارنة بمرجع بسيط يمكن قياسها.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

import fetch_macro
import fetch_news
from macro_platform import ai_report, ledger
from macro_platform.build import load_config
from macro_platform.demo import demo_series

ROOT = Path(__file__).resolve().parent.parent
CFG = load_config(ROOT / "config.json")["ai"]


def sc(label, prob, low, high, series="DGS10", by="2026-11-20"):
    return {"label": label, "name": label, "prob": prob, "trigger": "t", "reason": "r",
            "check": {"series": series, "by": by, "low": low, "high": high}}


GOOD = [sc("A", 45, 4.95, 5.10), sc("B", 35, None, 4.95), sc("C", 20, 5.10, None)]


def rep(scen=GOOD, direction="bearish", confidence=60, at="2026-11-16T13:46:00Z", day="2026-11-16"):
    return {"title": "t", "summary": ["s"], "bias": {"direction": direction, "confidence": confidence, "reason": "r"},
            "scenarios": scen, "one_liner": "o",
            "meta": {"ny_date": day, "completed_at": at, "written_at": at, "model": "m", "run_id": "1"}}


def daily(points):
    return pd.Series({pd.Timestamp(d): v for d, v in points})


# ---------------------------------------------------------------- what the report commits to
def test_valid_scenario_checks_form_a_full_distribution():
    got, why = ledger.check_scenarios(GOOD, "2026-11-16")
    assert why is None and got["series"] == "DGS10" and got["by"] == "2026-11-20"
    assert [(r["label"], r["low"], r["high"], r["prob"]) for r in got["ranges"]] == [
        ("B", None, 4.95, 35), ("A", 4.95, 5.10, 45), ("C", 5.10, None, 20)]


@pytest.mark.parametrize("scen, why", [
    ([{**s, "check": None} if s["label"] == "A" else s for s in GOOD], "missing check"),
    ([sc("A", 45, 4.95, 5.10), sc("B", 35, None, 4.95, series="DGS2"), sc("C", 20, 5.10, None)], "one allowed series"),
    ([sc("A", 45, 4.95, 5.1, series="AAPL"), sc("B", 35, None, 4.95, series="AAPL"), sc("C", 20, 5.1, None, series="AAPL")], "one allowed series"),
    ([sc("A", 45, 4.95, 5.10), sc("B", 35, None, 4.95, by="2026-11-21"), sc("C", 20, 5.10, None)], "share one date"),
    ([sc("A", 45, 4.95, 5.1, by="2026-11-16"), sc("B", 35, None, 4.95, by="2026-11-16"), sc("C", 20, 5.1, None, by="2026-11-16")], "share one date"),
    ([sc("A", 45, 4.95, 5.1, by="2026-12-08"), sc("B", 35, None, 4.95, by="2026-12-08"), sc("C", 20, 5.1, None, by="2026-12-08")], "share one date"),
    ([sc("A", 45, 4.95, 5.10), sc("B", 35, None, 4.90), sc("C", 20, 5.10, None)], "no gap or overlap"),        # فجوة
    ([sc("A", 45, 4.95, 5.10), sc("B", 35, None, 5.00), sc("C", 20, 5.10, None)], "no gap or overlap"),        # تداخل
    ([sc("A", 45, 4.95, 5.10), sc("B", 35, 4.80, 4.95), sc("C", 20, 5.10, None)], "open at both ends"),
    ([sc("A", 45, 5.10, 4.95), sc("B", 35, None, 4.95), sc("C", 20, 5.10, None)], "low must be below high"),
    ([sc("A", 45, "4.95", 5.10), sc("B", 35, None, 4.95), sc("C", 20, 5.10, None)], "numbers or null"),
    ([sc("A", 45, 4.95, 5.10), sc("B", 35, None, 4.95)], "missing check"),
])
def test_unmeasurable_checks_are_recorded_with_the_reason(scen, why):
    got, reason = ledger.check_scenarios(scen, "2026-11-16")
    assert got is None and why in reason


@pytest.mark.parametrize("bias, p, why", [
    ({"direction": "bullish", "confidence": 70}, 0.7, None),
    ({"direction": "bearish", "confidence": 70}, 0.3, None),
    ({"direction": "bearish", "confidence": 55}, 0.45, None),
    ({"direction": "neutral", "confidence": 55}, 0.5, None),
    ({"direction": "bullish", "confidence": 40}, None, "confidence outside 50-100"),
    ({"direction": "bullish", "confidence": "70"}, None, "confidence outside 50-100"),
    ({"direction": "up", "confidence": 70}, None, "unknown direction"),
])
def test_bias_confidence_is_a_probability_of_the_session_direction(bias, p, why):
    assert ledger.p_up(bias) == (p, why)


@pytest.mark.parametrize("written, session", [
    ("2026-11-16T13:46:00Z", "2026-11-16"),     # الاثنين 08:46 EST: جلسة اليوم
    ("2026-11-16T20:59:00Z", "2026-11-16"),     # 15:59 EST
    ("2026-11-16T21:00:00Z", "2026-11-17"),     # 16:00 EST: الجلسة التالية
    ("2026-11-13T22:00:00Z", "2026-11-16"),     # الجمعة بعد الإغلاق: الاثنين
    ("2026-11-14T15:00:00Z", "2026-11-16"),     # السبت
    ("2026-11-25T22:00:00Z", "2026-11-27"),     # الأربعاء قبل Thanksgiving (ai.market_holidays)
    ("2026-12-31T22:00:00Z", "2027-01-04"),     # 1 يناير 2027 عطلة، ثم عطلة نهاية الأسبوع
    ("garbage", None),
])
def test_session_is_the_first_trading_session_after_the_report(written, session):
    assert ledger.session_date(written, CFG) == session


def test_entry_keeps_numbers_only():
    e = ledger.entry(rep(), CFG)
    assert e["bias"] == {"direction": "bearish", "confidence": 60.0, "p_up": 0.4, "session": "2026-11-16",
                         "invalid": None, "outcome": None}
    assert e["scenarios"]["series"] == "DGS10" and e["scenarios"]["outcome"] is None
    text = json.dumps(e)
    assert '"t"' not in text and '"r"' not in text and "trigger" not in text and "reason" not in text


def test_a_second_report_for_the_same_day_replaces_the_first():
    led = {"version": 1, "entries": []}
    ledger.add(led, ledger.entry(rep(confidence=60), CFG))
    ledger.add(led, ledger.entry(rep(confidence=75), CFG))
    ledger.add(led, ledger.entry(rep(day="2026-11-13", at="2026-11-13T13:46:00Z"), CFG))
    assert [(e["ny_date"], e["bias"]["confidence"]) for e in led["entries"]] == [("2026-11-13", 60.0), ("2026-11-16", 75.0)]


# ---------------------------------------------------------------- outcomes
NDX = [("2026-11-13", 20000.0), ("2026-11-16", 20200.0), ("2026-11-17", 19900.0)]


def test_nothing_is_settled_before_the_data_reaches_the_date():
    led = {"version": 1, "entries": [ledger.entry(rep(), CFG)]}
    assert ledger.resolve(led, {"NASDAQ100": daily(NDX[:1]), "DGS10": daily([("2026-11-16", 5.0), ("2026-11-19", 5.2)])}) == 0
    assert led["entries"][0]["bias"]["outcome"] is None and led["entries"][0]["scenarios"]["outcome"] is None


def test_bias_is_scored_on_the_session_close_against_the_previous_close():
    led = {"version": 1, "entries": [ledger.entry(rep(direction="bearish", confidence=60), CFG)]}
    assert ledger.resolve(led, {"NASDAQ100": daily(NDX)}) == 1
    o = led["entries"][0]["bias"]["outcome"]
    assert o == {"scorable": True, "close": 20200.0, "prev_close": 20000.0, "prev_date": "2026-11-13", "change_pct": 1.0,
                 "up": True, "hit": False, "brier": 0.36}                        # p_up 0.4، والسوق صعد: (0.4−1)²
    assert ledger.resolve(led, {"NASDAQ100": daily(NDX)}) == 0                  # لا يُعاد الحسم


def test_missing_session_close_is_unscorable_not_a_guess():
    led = {"version": 1, "entries": [ledger.entry(rep(), CFG)]}
    ledger.resolve(led, {"NASDAQ100": daily([NDX[0], NDX[2]])})                # لا إغلاق ليوم الجلسة
    assert led["entries"][0]["bias"]["outcome"] == {"scorable": False, "why": "no close for the session"}


def test_scenarios_are_scored_on_the_value_at_the_date_with_bounds_going_up():
    led = {"version": 1, "entries": [ledger.entry(rep(), CFG)]}
    rates = daily([("2026-11-16", 5.00), ("2026-11-19", 5.30), ("2026-11-20", 5.10)])
    ledger.resolve(led, {"DGS10": rates})
    o = led["entries"][0]["scenarios"]["outcome"]
    assert (o["label"], o["value"], o["date"]) == ("C", 5.10, "2026-11-20")      # 5.10 بالضبط ينتمي للنطاق الأعلى
    assert o["brier"] == round(0.45 ** 2 + 0.35 ** 2 + 0.8 ** 2, 4)


def test_a_date_without_an_observation_uses_the_last_one_before_it_once_data_passes_it():
    led = {"version": 1, "entries": [ledger.entry(rep(), CFG)]}
    rates = daily([("2026-11-16", 5.0), ("2026-11-19", 4.90)])
    ledger.resolve(led, {"DGS10": rates})
    assert led["entries"][0]["scenarios"]["outcome"] is None                    # البيانات لم تتجاوز 20 نوفمبر بعد
    ledger.resolve(led, {"DGS10": pd.concat([rates, daily([("2026-11-23", 5.2)])])})
    o = led["entries"][0]["scenarios"]["outcome"]
    assert (o["label"], o["date"]) == ("B", "2026-11-19")


def test_invalid_commitments_are_never_scored():
    led = {"version": 1, "entries": [ledger.entry(rep(scen=[{**s, "check": None} for s in GOOD], confidence=40), CFG)]}
    assert ledger.resolve(led, {"NASDAQ100": daily(NDX), "DGS10": daily([("2026-11-23", 5.0)])}) == 0
    e = led["entries"][0]
    assert e["bias"]["invalid"] and e["scenarios"] == {"invalid": "missing check"}


def test_summary_says_when_the_sample_is_too_small():
    led = {"version": 1, "entries": [ledger.entry(rep(), CFG)]}
    ledger.resolve(led, {"NASDAQ100": daily(NDX), "DGS10": daily([("2026-11-16", 5.0), ("2026-11-20", 5.0)])})
    s = ledger.summary(led)
    assert s["bias"] == {"n": 1, "directional": 1, "hits": 0, "brier": 0.36, "baseline": 0.25}
    assert s["scenarios"]["n"] == 1 and s["scenarios"]["baseline"] == 0.6667 and s["meaningful"] is False
    big = {"version": 1, "entries": [json.loads(json.dumps(led["entries"][0])) | {"ny_date": f"d{k}"} for k in range(30)]}
    assert ledger.summary(big)["meaningful"] is True


def test_bad_ledger_file_starts_empty(tmp_path):
    p = tmp_path / "l.json"
    p.write_text("[1, 2]", encoding="utf-8")
    assert ledger.load(p) == {"version": 1, "entries": []}
    assert ledger.load(tmp_path / "missing.json") == {"version": 1, "entries": []}


# ---------------------------------------------------------------- the prompt and the real entry point
def test_the_prompt_states_the_same_rules_the_code_scores():
    s = ai_report.SYSTEM
    assert all(x in s for x in ledger.ALLOWED) and "within 21 days" in s and str(ledger.MAX_DAYS) in s
    assert "from 50 to 100" in s and "FRED NASDAQ100" in s and "belongs to the higher range" in s
    assert '"check": {"series": "DGS10", "by": "YYYY-MM-DD", "low": null, "high": 4.9}' in s


def _run(monkeypatch, tmp_path, argv, body=None, series_extra=None):
    def caller(user, system, model, effort):
        return "<report>" + json.dumps(body) + "</report>", [], {k: 0 for k in ai_report.USAGE_KEYS}, model

    orig = ai_report.write_report
    monkeypatch.setattr(ai_report, "write_report", lambda *a, **k: orig(*a, caller=caller, **k))
    series = demo_series(start="2015-01-01", end="2026-06-30") | (series_extra or {})
    monkeypatch.setattr(fetch_macro, "fetch_all", lambda raw, start, log=None: (series, {}))
    monkeypatch.setattr(fetch_macro, "load_saved_series", lambda raw: series)
    monkeypatch.setattr(fetch_macro, "load_status", lambda p: ({}, []))
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fetch_macro, "DATA", data)
    monkeypatch.setattr(fetch_news, "load_live", lambda url: {})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("AI_FORCE", "true")
    out = tmp_path / "data.json"
    assert fetch_macro.main(["--out", str(out), *argv]) == 0
    return data, json.loads(out.read_text(encoding="utf-8"))


def test_report_run_records_its_commitment_and_later_runs_settle_it(monkeypatch, tmp_path):
    today = datetime.now(timezone.utc).astimezone(ai_report.NY).date()
    by = today + timedelta(days=7)
    body = {k: v for k, v in rep().items() if k != "meta"}
    body["scenarios"] = [sc("A", 50, 4.9, 5.1, by=str(by)), sc("B", 30, None, 4.9, by=str(by)), sc("C", 20, 5.1, None, by=str(by))]
    data, payload = _run(monkeypatch, tmp_path, [], body)
    led = json.loads((data / "forecast_ledger.json").read_text(encoding="utf-8"))
    e = led["entries"][-1]
    assert e["ny_date"] == str(today) and e["scenarios"]["series"] == "DGS10" and e["scenarios"]["outcome"] is None
    assert "reason" not in json.dumps(led) and payload["ledger"]["by_date"][str(today)]["bias"]["p_up"] == 0.4
    # تشغيل لاحق (offline كدورة الأخبار): البيانات تجاوزت التاريخ، فيُحسم في data.json ولا يُكتب الملف
    later = {"DGS10": daily([(str(today), 5.0), (str(by), 5.25), (str(by + timedelta(days=1)), 5.3)])}
    monkeypatch.delenv("AI_FORCE")
    before = (data / "forecast_ledger.json").read_text(encoding="utf-8")
    _, payload = _run(monkeypatch, tmp_path, ["--offline"], body, later)
    assert payload["ledger"]["by_date"][str(today)]["scenarios"]["outcome"]["label"] == "C"
    assert (data / "forecast_ledger.json").read_text(encoding="utf-8") == before


def test_a_report_without_checks_is_still_published(monkeypatch, tmp_path):
    body = {k: v for k, v in rep().items() if k != "meta"}
    body["scenarios"] = [{k: v for k, v in s.items() if k != "check"} for s in GOOD]
    data, payload = _run(monkeypatch, tmp_path, [], body)
    assert json.loads((data / "ai_history.json").read_text(encoding="utf-8"))[0]["title"] == "t"
    e = json.loads((data / "forecast_ledger.json").read_text(encoding="utf-8"))["entries"][-1]
    assert e["scenarios"] == {"invalid": "missing check"} and e["bias"]["p_up"] == 0.4


def test_update_workflow_saves_the_ledger_with_the_data():
    wf = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
    assert re.search(r"git add -A data\b", wf)


@pytest.mark.parametrize("day", ["2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
                                 "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"])
def test_2027_nyse_closures_are_not_trading_days(day):
    # NYSE: 10 أيام إغلاق في 2027 (إعلان NYSE Group، ديسمبر 2025)؛ 19 يونيو و25 ديسمبر سبت فيُغلق الجمعة قبلهما
    assert not ai_report.is_trading_day(datetime.fromisoformat(day + "T12:00:00"), CFG)
    assert ai_report.dispatch_decision(datetime.fromisoformat(day + "T14:00:00+00:00"), None, [], CFG)[0] is False


def test_a_value_from_before_the_report_never_settles_a_scenario():
    led = {"version": 1, "entries": [ledger.entry(rep(), CFG)]}
    ledger.resolve(led, {"DGS10": daily([("2026-11-13", 4.5), ("2026-11-23", 5.2)])})   # لا قراءة بين 16 و20 نوفمبر
    assert led["entries"][0]["scenarios"]["outcome"] == {"scorable": False,
                                                         "why": "no observation between the report and the date"}
