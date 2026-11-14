"""PR 3 — Analytical core: تقييم اقتصادي أولاً، وترجمة سوقية منفصلة مشتقة منه، وكل منهما يُقاس منفصلاً.

ثلاثة أقفال للترتيب: المدخلات منفصلة (economy_inputs بلا أي مؤشر أسهم)، والتعليمات تنص على الترتيب، وصيغة المخرجات
تبدأ بـeconomy. Translation-check لا يُجمع مع Scenario-check في أي رقم.
"""
import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from _report import body, caller_for, gate, text
from macro_platform import ai_report, ledger, releases
from macro_platform.build import build_payload, load_config
from macro_platform.demo import demo_series

CFG = load_config("config.json")
NOW = datetime(2026, 11, 16, 14, 5, tzinfo=timezone.utc)          # الاثنين 09:05 بتوقيت نيويورك
KNOWN = {"DGS10", "DFII10", "PAYEMS", "CPIAUCSL"}


@pytest.fixture(scope="module")
def payload():
    return build_payload(demo_series(start="2018-01-01", end="2026-06-30"), {}, CFG, now=NOW)


# ---------------------------------------------------------------- lock 1: separate inputs
def test_economy_inputs_hold_no_equity_index_and_market_inputs_are_labelled(payload):
    ctx = ai_report.context(payload, now_utc=NOW, extra={
        "changes_since_previous_report": {"available": True, "items": [{"id": "SP500", "kind": "new_observation"},
                                                                       {"id": "DGS10", "kind": "new_observation"}]},
        "revisions_recent": [{"id": "NASDAQ100", "date": "x"}, {"id": "PAYEMS", "date": "y"}]})
    eco = json.dumps(ctx["economy_inputs"], ensure_ascii=False)
    assert "NASDAQ100" not in eco and "SP500" not in eco and "nq_gates" not in eco
    assert [i["id"] for i in ctx["market_inputs"]["indicators"]] == ["NASDAQ100", "SP500"]
    assert ctx["market_inputs"]["changes_since_previous_report"] == [{"id": "SP500", "kind": "new_observation"}]
    assert ctx["economy_inputs"]["revisions_recent"] == [{"id": "PAYEMS", "date": "y"}]
    assert ctx["market_inputs"]["nq_gates"] == payload["gates"] and "market translation only" in ctx["market_inputs"]["note"]
    rel = ctx["market_inputs"]["relative_performance"]
    assert set(rel) == {"as_of", "d5", "d10", "d20"} and all(isinstance(rel[k], float) for k in ("d5", "d10", "d20"))


# ---------------------------------------------------------------- lock 2 and 3: the instructions and the output order
def test_prompt_orders_economy_first_and_keeps_the_market_translation_out_of_it():
    s = ai_report.SYSTEM
    assert s.index("Assess the US economy") < s.index("translate it for equities")
    assert "Equity moves do not redefine the state of the economy" in s and "derived from \"economy\"" in s
    out = s[s.index("Output:"):]
    assert out.index('"economy"') < out.index('"market_translation"') < out.index('"events"')
    economy_schema = out[out.index('"economy": {'):out.index('"market_translation": {')]
    assert "NQ" not in economy_schema and "bias" not in economy_schema.lower() and "nq" not in economy_schema
    assert "largely similar to the S&P 500" in s and "Energy has a small weight in the Dow" in s
    assert "from -2 to +2" in s and 'no "uncertain" level' in s and "50 means no view" in s
    assert "No entries, stops, targets, or buy/sell recommendations" in s
    assert "No single dominant driver today" in s and "never invent a conflict" in s
    assert "official release checked via web" in s


# ---------------------------------------------------------------- what is retried and what is published with a warning
def _warn(b):
    return ai_report.parse_report(text(b), KNOWN)["warnings"]


def test_a_complete_report_has_no_warnings():
    assert _warn(body()) == []


@pytest.mark.parametrize("mutate, expect", [
    (lambda b: b.pop("market_translation"), "market_translation is missing"),
    (lambda b: b["market_translation"]["gates"].update(nq=gate(3)), "market gate nq: impact must be a whole number"),
    (lambda b: b["market_translation"]["gates"].update(dow=gate(0.5)), "market gate dow: impact"),
    (lambda b: b["market_translation"]["gates"].pop("spx"), "market gate spx is missing"),
    (lambda b: b["market_translation"].update(nq_vs_spx_10d={"prob": 101}), "nq_vs_spx_10d: prob"),
    (lambda b: b["economy"]["scenarios"][0].update(prob=46), "probabilities sum to 101"),
    (lambda b: b["economy"].pop("regime"), "missing field: economy.regime"),
    (lambda b: b["economy"].pop("key_conflicts"), "missing field: economy.key_conflicts"),
    (lambda b: b["economy"]["dominant_driver"].update(evidence=["DFII10", "MADE_UP"]), "unknown evidence ids removed: MADE_UP"),
    (lambda b: b["economy"]["dominant_driver"].update(kind="event", evidence=[], event_ids=["e9"]), "without a sourced event"),
    (lambda b: b["economy"]["dominant_driver"].update(kind="data", evidence=[]), "kind data without the evidence"),
    (lambda b: b["economy"]["dominant_driver"].update(kind="vibes"), "kind must be data, event or mixed"),
])
def test_non_blocking_issues_publish_with_a_visible_warning(mutate, expect):
    b = body()
    mutate(b)
    rep = ai_report.parse_report(text(b), KNOWN)
    assert any(expect in w for w in rep["warnings"]), rep["warnings"]
    assert rep["one_liner"] == "الخلاصة"                                   # نُشر


def test_event_evidence_and_an_honest_no_driver_and_empty_conflicts_are_valid():
    b = body()
    b["economy"]["dominant_driver"] = {"driver": "قرار FOMC", "kind": "event", "evidence": [], "event_ids": ["e1"]}
    b["economy"]["key_conflicts"] = []
    assert _warn(b) == []
    b["economy"]["dominant_driver"] = {"driver": "لا محرك واحد مهيمن اليوم", "kind": "mixed", "evidence": ["PAYEMS"],
                                       "event_ids": ["e1"]}
    assert _warn(b) == []
    b["events"][0]["url"] = "javascript:alert(1)"                          # حدث بلا رابط http(s) ليس دليلاً
    assert any("without a sourced event" in w for w in _warn(b))


def test_unknown_evidence_ids_are_removed_and_logged_not_silently():
    b = body()
    b["economy"]["dominant_driver"]["evidence"] = ["DFII10", "NASDAQ100"]  # مؤشر أسهم ليس دليلاً اقتصادياً
    rep = ai_report.parse_report(text(b), KNOWN)
    assert rep["economy"]["dominant_driver"]["evidence"] == ["DFII10"]
    assert rep["warnings"] == ["dominant_driver: unknown evidence ids removed: NASDAQ100"]


@pytest.mark.parametrize("raw, why", [
    ("no report at all", "no <report> block"),
    ("<report>{\"economy\": {\"executive\": </report>", "no <report> block"),   # لا JSON مغلق
    ("<report>{\"economy\": [1,}</report>", "broken JSON"),
    (text({"market_translation": {}}), "no economy object"),
    (text(body(bottom_line="")), "missing executive.read or bottom_line"),
])
def test_what_blocks_publication_is_a_retryable_failure(raw, why):
    with pytest.raises(ai_report.ReportError) as e:
        ai_report.parse_report(raw, KNOWN)
    assert why in str(e.value)


def test_attempt_2_is_always_delayed_and_attempt_1_only_after_the_open(payload):
    at = lambda h, m: datetime(2026, 11, 16, h, m, tzinfo=timezone.utc)
    meta = lambda attempt, done: ai_report.write_report(payload, CFG, now_utc=at(14, 0), caller=caller_for(),
                                                        attempt=attempt, clock=lambda: done)["meta"]
    assert (meta("1", at(14, 29))["delayed"], meta("1", at(14, 30))["delayed"]) == (False, True)   # 09:29 / 09:30
    assert meta("2", at(14, 20))["delayed"] is True                        # الثانية متأخرة بتعريفها، أياً كان وقتها


def test_write_report_records_usage_on_a_parse_failure(payload):
    def bad(user, system, model, effort):
        return "nothing", [], {"output_tokens": 7}, model, {"stop_reason": "end_turn", "duration_s": 3.0}
    with pytest.raises(ai_report.ReportError) as e:
        ai_report.write_report(payload, CFG, now_utc=NOW, caller=bad, attempt="1")
    assert e.value.usage == {"output_tokens": 7} and e.value.info["stop_reason"] == "end_turn"


def test_the_market_index_is_never_accepted_as_economic_evidence_in_the_real_call(payload):
    b = body()
    b["economy"]["dominant_driver"]["evidence"] = ["SP500"]
    rep = ai_report.write_report(payload, CFG, now_utc=NOW, caller=caller_for(b), attempt="1")
    assert "unknown evidence ids removed: SP500" in rep["warnings"][0]
    assert rep["meta"]["schema"] == 2 and rep["meta"]["effort"] == "xhigh"


# ---------------------------------------------------------------- released today, not yet in the platform's data
IND = [{"id": "CPIAUCSL", "as_of": "2026-09-01", "last_new_obs_utc": "2026-10-24T14:12:00Z"},
       {"id": "CPILFESL", "as_of": "2026-09-01", "last_new_obs_utc": "2026-11-10T14:12:00Z"},
       {"id": "ICSA", "as_of": "2026-11-07", "last_new_obs_utc": "2026-11-13T13:37:00Z"},
       {"id": "CCSA", "as_of": "2026-10-31", "last_new_obs_utc": None},
       {"id": "PCEPILFE", "as_of": "2026-09-01", "last_new_obs_utc": "2026-10-31T13:00:00Z"}]


def ev(title, t):
    return {"title": title, "timeUtc": t, "impact": 1}


def test_a_release_out_today_whose_series_has_not_moved_is_flagged():
    now = datetime(2026, 11, 13, 14, 5, tzinfo=timezone.utc)                # 09:05 نيويورك
    got = releases.not_in_platform([ev("CPI m/m", "2026-11-13T13:30:00Z")], IND, now)
    assert got == [{"event": "CPI m/m", "time_utc": "2026-11-13T13:30:00Z", "time_ny": "08:30",
                    "series_not_updated": [{"id": "CPIAUCSL", "platform_as_of": "2026-09-01"},
                                           {"id": "CPILFESL", "platform_as_of": "2026-09-01"}]}]


def test_updated_series_future_events_other_days_and_unknown_titles_are_not_flagged():
    now = datetime(2026, 11, 13, 14, 5, tzinfo=timezone.utc)
    cal = [ev("Unemployment Claims", "2026-11-13T13:30:00Z"),               # ICSA وصل 08:37: يبقى CCSA فقط
           ev("Core PCE Price Index m/m", "2026-11-13T15:00:00Z"),          # لم يحن
           ev("CPI m/m", "2026-11-12T13:30:00Z"),                           # أمس
           ev("FOMC Member Waller Speaks", "2026-11-13T13:00:00Z"),         # ليس إصداراً لسلسلة
           ev("PPI m/m", "2026-11-13T13:30:00Z")]                           # PPI ليس في القائمة المجمّدة
    got = releases.not_in_platform(cal, IND, now)
    assert [(g["event"], [s["id"] for s in g["series_not_updated"]]) for g in got] == [("Unemployment Claims", ["CCSA"])]
    assert releases.not_in_platform(None, IND, now) == []


def test_the_same_event_is_flagged_once_its_time_has_passed():
    cal = [ev("Core PCE Price Index m/m", "2026-11-13T15:00:00Z")]
    assert releases.not_in_platform(cal, IND, datetime(2026, 11, 13, 14, 59, tzinfo=timezone.utc)) == []
    got = releases.not_in_platform(cal, IND, datetime(2026, 11, 13, 15, 0, tzinfo=timezone.utc))
    assert [g["series_not_updated"][0]["id"] for g in got] == ["PCEPILFE"]


def test_the_report_context_carries_the_flag(payload):
    p = json.loads(json.dumps(payload))
    for i in p["indicators"]:
        i["last_new_obs_utc"] = "2026-11-01T00:00:00Z"
    feed = {"calendar": [ev("Core CPI m/m", "2026-11-16T13:30:00Z")], "headlines": []}
    flag = ai_report.context(p, feed=feed, now_utc=NOW)["economy_inputs"]["released_today_not_in_platform_data"]
    assert [f["event"] for f in flag] == ["Core CPI m/m"]


# ---------------------------------------------------------------- Translation-check: NQ vs S&P 500 over 10 trading days
def rep(day="2026-11-20", prob=60, delayed=False):
    return {"scenarios": [], "market_translation": {"nq_vs_spx_10d": {"prob": prob}},
            "meta": {"ny_date": day, "completed_at": f"{day}T14:20:00Z", "delayed": delayed, "pre_open": not delayed,
                     "attempt": "2" if delayed else "1"}}


def closes(start, values):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)))


def test_window_counts_ten_trading_days_and_skips_thanksgiving():
    tc = ledger.translation(rep(), CFG["ai"], delayed=False)
    assert (tc["start"], tc["end"], tc["prob"]) == ("2026-11-19", "2026-12-04", 60)   # 26 نوفمبر عطلة
    late = ledger.translation(rep(delayed=True), CFG["ai"], delayed=True)
    assert (late["start"], late["end"]) == ("2026-11-20", "2026-12-07")               # المتأخر: من إغلاق يومه


@pytest.mark.parametrize("prob", [None, 101, 50.5, True, "60"])
def test_a_missing_or_invalid_probability_is_unscorable(prob):
    assert "unscorable" in ledger.translation(rep(prob=prob), CFG["ai"], delayed=False)


def test_settled_from_closes_after_the_window_with_derived_numbers_only():
    led = {"version": 2, "entries": [ledger.entry(rep(), CFG["ai"])]}
    ndx = closes("2026-11-16", [100.0] * 4 + [101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115])
    spx = closes("2026-11-16", [100.0] * 4 + [100.5] * 15)
    assert ledger.resolve(led, {"NASDAQ100": ndx[ndx.index <= "2026-12-03"], "SP500": spx}) == 0   # لم تصل النهاية
    assert ledger.resolve(led, {"NASDAQ100": ndx, "SP500": spx}) == 1
    r = led["entries"][0]["translation_check"]["check_result"]
    assert r["nq_outperformed"] is True and r["from"] == "2026-11-19" and r["to"] == "2026-12-04"
    assert r["brier"] == round((0.6 - 1) ** 2, 4)
    assert set(r) == {"scorable", "nq_minus_spx_pct", "nq_outperformed", "from", "to", "brier"}   # لا مستويات خام
    assert "115" not in json.dumps(led) and "100.5" not in json.dumps(led)


def test_translation_and_scenario_summaries_are_never_combined_and_split_by_timing():
    a, b = ledger.entry(rep(prob=60), CFG["ai"]), ledger.entry(rep(day="2026-11-23", prob=50, delayed=True), CFG["ai"])
    a["translation_check"]["check_result"] = {"scorable": True, "brier": 0.16}
    b["translation_check"]["check_result"] = {"scorable": True, "brier": 0.25}
    s = ledger.summary({"entries": [a, b]})
    assert s["translation_check"]["pre_open"]["n"] == 1 and s["translation_check"]["pre_open"]["brier"] == 0.16
    assert s["translation_check"]["delayed"]["n"] == 1 and s["translation_check"]["delayed"]["baseline"] == 0.25
    assert s["scenario_check"]["pre_open"]["n"] == 0 and s["scenario_check"]["delayed"]["n"] == 0
    assert "combined" not in json.dumps(s) and set(s) == {"reports", "min_n", "scenario_check", "translation_check"}
