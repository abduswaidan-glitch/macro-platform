"""سجل التوقعات (Scenario-check): ما يلتزم به التقرير قابل للقياس، ويحسمه الكود من سلاسل FRED نفسها، ولا يحسم
قبل وصول البيانات، ولا يدّعي أكثر مما يقيس.

الدليل الذي بُني عليه (data/ai_history.json، 4 إلى 13 نوفمبر 2026): الثقة 55% في التقارير السبعة كلها، وشروط
السيناريوهات نص مركّب بلا تاريخ حسم، ولا سجل للنتائج.
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
from _report import body, fake_fetch, freeze

ROOT = Path(__file__).resolve().parent.parent
CFG = load_config(ROOT / "config.json")["ai"]


def sc(label, prob, low, high, series="DGS10", by="2026-11-20"):
    return {"label": label, "name": label, "prob": prob, "trigger": "t", "reason": "r",
            "check": {"series": series, "by": by, "low": low, "high": high}}


GOOD = [sc("A", 45, 4.95, 5.10), sc("B", 35, None, 4.95), sc("C", 20, 5.10, None)]


def rep(scen=GOOD, at="2026-11-16T14:16:00Z", day="2026-11-16", prob=45, delayed=False, attempt="1"):
    return {"title": "t", "summary": ["s"], "scenarios": scen, "one_liner": "o",
            "market_translation": {"gates": {}, "nq_vs_spx_10d": {"prob": prob}},
            "meta": {"ny_date": day, "completed_at": at, "written_at": at, "model": "m", "run_id": "1",
                     "attempt": attempt, "scheduled_for": "09:00 America/New_York", "started_at": at,
                     "pre_open": not delayed, "delayed": delayed}}


def daily(points):
    return pd.Series({pd.Timestamp(d): v for d, v in points})


# ---------------------------------------------------------------- what the report commits to
def test_valid_scenario_checks_form_a_full_distribution():
    got, why = ledger.check_scenarios(GOOD, "2026-11-16", CFG)
    assert why is None and got["series"] == "DGS10" and got["by"] == "2026-11-20" and got["check_result"] is None
    assert [(r["label"], r["low"], r["high"], r["prob"]) for r in got["ranges"]] == [
        ("B", None, 4.95, 35), ("A", 4.95, 5.10, 45), ("C", 5.10, None, 20)]


def _by(d):
    return [sc("A", 45, 4.95, 5.1, by=d), sc("B", 35, None, 4.95, by=d), sc("C", 20, 5.1, None, by=d)]


@pytest.mark.parametrize("scen, why", [
    ([{**s, "check": None} if s["label"] == "A" else s for s in GOOD], "missing check"),
    ([sc("A", 45, 4.95, 5.10), sc("B", 35, None, 4.95, series="DGS2"), sc("C", 20, 5.10, None)], "one allowed series"),
    # مؤشرات الأسهم ليست شرطاً للتقرير الاقتصادي
    ([sc("A", 45, 4.95, 5.1, series="NASDAQ100"), sc("B", 35, None, 4.95, series="NASDAQ100"),
      sc("C", 20, 5.1, None, series="NASDAQ100")], "one allowed series"),
    ([sc("A", 45, 4.95, 5.10), sc("B", 35, None, 4.95, by="2026-11-19"), sc("C", 20, 5.10, None)], "share one date"),
    (_by("2026-11-16"), "share one date"),
    (_by("2026-12-08"), "share one date"),
    (_by("2026-11-21"), "not a trading day"),            # السبت
    (_by("2026-11-26"), "not a trading day"),            # Thanksgiving
    ([sc("A", 45, 4.95, 5.10), sc("B", 35, None, 4.90), sc("C", 20, 5.10, None)], "no gap or overlap"),
    ([sc("A", 45, 4.95, 5.10), sc("B", 35, None, 5.00), sc("C", 20, 5.10, None)], "no gap or overlap"),
    ([sc("A", 45, 4.95, 5.10), sc("B", 35, 4.80, 4.95), sc("C", 20, 5.10, None)], "open at both ends"),
    ([sc("A", 45, 5.10, 4.95), sc("B", 35, None, 4.95), sc("C", 20, 5.10, None)], "low must be below high"),
    ([sc("A", 45, "4.95", 5.10), sc("B", 35, None, 4.95), sc("C", 20, 5.10, None)], "numbers or null"),
    ([sc("A", 45, 4.95, 5.10), sc("B", 35, None, 4.95)], "expected 3 scenarios"),
    ([sc("A", 45, 4.95, 5.10), sc("B", 34, None, 4.95), sc("C", 20, 5.10, None)], "sum to 99, not 100"),
    ([sc("A", 45, 4.95, 5.10), sc("B", 36, None, 4.95), sc("C", 20, 5.10, None)], "sum to 101, not 100"),
    ([sc("A", 45.5, 4.95, 5.10), sc("B", 34.5, None, 4.95), sc("C", 20, 5.10, None)], "whole numbers"),
])
def test_unmeasurable_checks_are_recorded_with_the_reason(scen, why):
    got, reason = ledger.check_scenarios(scen, "2026-11-16", CFG)
    assert got is None and why in reason


def test_a_date_beyond_the_known_holiday_calendar_is_refused():
    cfg = {**CFG, "market_holidays": ["2026-11-26", "2026-12-25"]}
    got, why = ledger.check_scenarios(_by("2027-01-04"), "2026-12-28", cfg)
    assert got is None and "holidays are not known" in why


def test_no_nasdaq_series_in_the_economic_check_list():
    assert "NASDAQ100" not in ledger.ALLOWED and "SP500" not in ledger.ALLOWED


def test_entry_keeps_numbers_only_and_names_the_result_check_result():
    e = ledger.entry(rep(), CFG)
    assert set(e) == {"ny_date", "written_at", "model", "run_id", "timing", "scenario_check", "translation_check"}
    assert e["scenario_check"]["check_result"] is None and "outcome" not in json.dumps(e)
    text = json.dumps(e)
    assert '"t"' not in text and '"r"' not in text and "trigger" not in text and "reason" not in text


def test_a_second_report_for_the_same_day_replaces_the_first():
    led = {"version": 2, "entries": []}
    ledger.add(led, ledger.entry(rep(), CFG))
    ledger.add(led, ledger.entry(rep(scen=GOOD[:2]), CFG))
    ledger.add(led, ledger.entry(rep(day="2026-11-13", at="2026-11-13T13:46:00Z"), CFG))
    assert [e["ny_date"] for e in led["entries"]] == ["2026-11-13", "2026-11-16"]
    assert "unscorable" in led["entries"][1]["scenario_check"]


# ---------------------------------------------------------------- settling
def test_nothing_is_settled_before_the_data_reaches_the_date():
    led = {"version": 2, "entries": [ledger.entry(rep(), CFG)]}
    assert ledger.resolve(led, {"DGS10": daily([("2026-11-16", 5.0), ("2026-11-19", 5.2)])}) == 0
    assert led["entries"][0]["scenario_check"]["check_result"] is None


def test_checks_are_settled_on_the_value_at_the_date_with_bounds_going_up():
    led = {"version": 2, "entries": [ledger.entry(rep(), CFG)]}
    ledger.resolve(led, {"DGS10": daily([("2026-11-16", 5.00), ("2026-11-19", 5.30), ("2026-11-20", 5.10)])})
    o = led["entries"][0]["scenario_check"]["check_result"]
    assert (o["range_hit"], o["value"], o["date"]) == ("C", 5.10, "2026-11-20")     # 5.10 بالضبط للنطاق الأعلى
    assert o["brier"] == round(0.45 ** 2 + 0.35 ** 2 + 0.8 ** 2, 4)
    assert ledger.resolve(led, {"DGS10": daily([("2026-11-23", 1.0)])}) == 0         # لا يُعاد الحسم


def test_a_missing_date_uses_the_last_value_before_it_once_data_passes_it():
    led = {"version": 2, "entries": [ledger.entry(rep(), CFG)]}
    rates = daily([("2026-11-16", 5.0), ("2026-11-19", 4.90)])
    ledger.resolve(led, {"DGS10": rates})
    assert led["entries"][0]["scenario_check"]["check_result"] is None
    ledger.resolve(led, {"DGS10": pd.concat([rates, daily([("2026-11-23", 5.2)])])})
    o = led["entries"][0]["scenario_check"]["check_result"]
    assert (o["range_hit"], o["date"]) == ("B", "2026-11-19")


def test_a_value_from_before_the_report_never_settles_a_check():
    led = {"version": 2, "entries": [ledger.entry(rep(), CFG)]}
    ledger.resolve(led, {"DGS10": daily([("2026-11-13", 4.5), ("2026-11-23", 5.2)])})
    assert led["entries"][0]["scenario_check"]["check_result"] == {
        "scorable": False, "why": "no observation between the report and the date"}


def test_unmeasurable_commitments_are_never_scored():
    led = {"version": 2, "entries": [ledger.entry(rep(scen=[{**s, "check": None} for s in GOOD]), CFG)]}
    assert ledger.resolve(led, {"DGS10": daily([("2026-11-23", 5.0)])}) == 0
    assert led["entries"][0]["scenario_check"] == {"unscorable": "missing check"}


def test_summary_says_small_sample_never_statistical_significance():
    led = {"version": 2, "entries": [ledger.entry(rep(), CFG)]}
    ledger.resolve(led, {"DGS10": daily([("2026-11-16", 5.0), ("2026-11-20", 5.0)])})
    s = ledger.summary(led)
    pre = s["scenario_check"]["pre_open"]
    assert pre["n"] == 1 and pre["baseline"] == 0.6667 and pre["sample"] == "small"
    big = {"version": 2, "entries": [json.loads(json.dumps(led["entries"][0])) | {"ny_date": f"d{k}"} for k in range(30)]}
    assert ledger.summary(big)["scenario_check"]["pre_open"]["sample"] == "preliminary"
    assert "significan" not in json.dumps(s).lower()


def test_bad_ledger_file_starts_empty(tmp_path):
    p = tmp_path / "l.json"
    p.write_text("[1, 2]", encoding="utf-8")
    assert ledger.load(p) == {"version": 2, "entries": []}
    assert ledger.load(tmp_path / "missing.json") == {"version": 2, "entries": []}


# ---------------------------------------------------------------- no silent probability changes
def _parse(scen):
    return ai_report.parse_report("<report>" + json.dumps(body(scenarios=scen)) + "</report>")


def test_parse_report_keeps_the_models_probabilities_exactly():
    got = _parse([{**s, "prob": p} for s, p in zip(GOOD, (50, 30, 21))])
    assert [s["prob"] for s in got["scenarios"]] == [50, 30, 21]                     # لا تعديل على أكبر احتمال
    assert _parse([{**GOOD[0], "prob": 90}])["scenarios"][0]["prob"] == 90            # كان يُسقط التقرير المدفوع
    four = [{**GOOD[0], "label": x, "prob": 25} for x in "ABCD"]                      # أربعة: تُعرض كلها، لا قصّ
    assert len(_parse(four)["scenarios"]) == 4
    for broken in ("no report tag", "<report>{bad json</report>"):
        with pytest.raises(Exception):
            ai_report.parse_report(broken)


# ---------------------------------------------------------------- the prompt and the real entry point
def test_the_prompt_states_the_same_rules_the_code_scores():
    s = ai_report.SYSTEM
    assert all(x in s for x in ledger.ALLOWED) and "NASDAQ100" not in s
    assert "within 21 days" in s and "not a weekend or NYSE holiday" in s and "sum to exactly 100" in s
    assert "not proof that the whole economic scenario happened" in s and "bias" not in s.lower()


MONDAY = "2026-11-16T14:00:00"     # 09:00 EST، يوم تداول


def _run(monkeypatch, tmp_path, argv, body=None, series_extra=None, attempt="1"):
    def caller(user, system, model, effort):
        return "<report>" + json.dumps(body) + "</report>", [], {k: 0 for k in ai_report.USAGE_KEYS}, model

    orig = ai_report.write_report
    monkeypatch.setattr(ai_report, "write_report", lambda *a, **k: orig(*a, caller=caller, **k))
    series = demo_series(start="2015-01-01", end="2026-06-30") | (series_extra or {})
    monkeypatch.setattr(fetch_macro, "fetch_all", fake_fetch(series))
    monkeypatch.setattr(fetch_macro, "load_saved_series", lambda raw: series)
    monkeypatch.setattr(fetch_macro, "load_status", lambda p: ({}, []))
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fetch_macro, "DATA", data)
    monkeypatch.setattr(fetch_news, "load_live", lambda url: {})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    freeze(monkeypatch, MONDAY)
    if attempt:
        monkeypatch.setenv("REPORT_ATTEMPT", attempt)
    else:
        monkeypatch.delenv("REPORT_ATTEMPT", raising=False)
    out = tmp_path / "data.json"
    assert fetch_macro.main(["--out", str(out), *argv]) == 0
    return data, json.loads(out.read_text(encoding="utf-8"))


def _next_trading_day(d, n):
    while n:
        d += timedelta(days=1)
        if ai_report.is_trading_day(datetime(d.year, d.month, d.day, 12), CFG):
            n -= 1
    return d


def test_report_run_records_its_commitment_and_later_runs_settle_it(monkeypatch, tmp_path):
    today = datetime.fromisoformat(MONDAY).date()
    by = _next_trading_day(today, 5)
    b = body(scenarios=[sc("A", 50, 4.9, 5.1, by=str(by)), sc("B", 30, None, 4.9, by=str(by)),
                        sc("C", 20, 5.1, None, by=str(by))])
    data, payload = _run(monkeypatch, tmp_path, [], b)
    led = json.loads((data / "forecast_ledger.json").read_text(encoding="utf-8"))
    e = led["entries"][-1]
    assert e["ny_date"] == str(today) and e["scenario_check"]["series"] == "DGS10", e
    assert "reason" not in json.dumps(led) and led["version"] == 2
    later = {"DGS10": daily([(str(today), 5.0), (str(by), 5.25), (str(by + timedelta(days=1)), 5.3)])}
    before = (data / "forecast_ledger.json").read_text(encoding="utf-8")
    _, payload = _run(monkeypatch, tmp_path, ["--offline"], b, later, attempt=None)
    assert payload["ledger"]["by_date"][str(today)]["scenario_check"]["check_result"]["range_hit"] == "C"
    assert (data / "forecast_ledger.json").read_text(encoding="utf-8") == before   # offline لا يكتب السجل


def test_a_report_with_unmeasurable_checks_is_still_published(monkeypatch, tmp_path):
    scen = [{k: v for k, v in s.items() if k != "check"} for s in GOOD]
    scen[0]["prob"] = 46                                                               # المجموع 101
    data, payload = _run(monkeypatch, tmp_path, [], body(scenarios=scen))
    h = json.loads((data / "ai_history.json").read_text(encoding="utf-8"))[0]
    assert h["title"] == "عنوان اليوم" and h["meta"]["prob_sum"] == 101 and [s["prob"] for s in h["scenarios"]] == [46, 35, 20]
    e = json.loads((data / "forecast_ledger.json").read_text(encoding="utf-8"))["entries"][-1]
    assert e["scenario_check"] == {"unscorable": "probabilities sum to 101, not 100"}


def test_update_workflow_saves_the_ledger_with_the_data():
    wf = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
    assert re.search(r"git add -A data\b", wf)


@pytest.mark.parametrize("day", ["2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
                                 "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"])
def test_2027_nyse_closures_are_not_trading_days(day):
    # NYSE: 10 أيام إغلاق في 2027؛ 19 يونيو و25 ديسمبر سبت فيُغلق الجمعة قبلهما
    assert not ai_report.is_trading_day(datetime.fromisoformat(day + "T12:00:00"), CFG)
