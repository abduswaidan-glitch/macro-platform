"""التقرير الاقتصادي ثم Market Gates: خطوتان منفصلتان، وكل منهما يُقاس منفصلاً.

قرار Abdu (15 نوفمبر 2026): جامع مستقل لكل قسم يسلّم ملفه للنموذج قبل التقرير؛ وMarket Gates معكوس: بعد اكتمال
التقرير الاقتصادي يُكتب انحياز NQ وS&P وDow من التقرير المكتمل، في استدعاء ثانٍ. Translation-check لا يُجمع مع
Scenario-check في أي رقم.
"""
import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from _report import body, caller_for, gate, is_market, text
from macro_platform import ai_report, ledger, releases
from macro_platform.build import build_payload, load_config
from macro_platform.demo import demo_series

CFG = load_config("config.json")
NOW = datetime(2026, 11, 16, 14, 5, tzinfo=timezone.utc)          # الاثنين 09:05 بتوقيت نيويورك
KNOWN = {"DGS10", "DFII10", "PAYEMS", "CPIAUCSL"}


@pytest.fixture(scope="module")
def payload():
    return build_payload(demo_series(start="2018-01-01", end="2026-06-30"), {}, CFG, now=NOW)


# ---------------------------------------------------------------- one brief per section, no market data in the economy step
ECON = ("growth", "inflation", "labor", "fed_rates", "financial")


def test_the_model_reads_one_brief_per_section_and_no_equity_data(payload):
    ctx = ai_report.context(payload, feed={"calendar": [], "headlines": []}, now_utc=NOW)
    eco = ctx["economy_inputs"]
    assert set(eco["sections"]) == set(ECON) | {"news"}
    cat = {i["id"]: i["category"] for i in payload["indicators"]}
    for k in ECON:
        b = eco["sections"][k]
        assert b["ok"] and b["section"] == k and b["indicators"], k
        assert {i["id"] for i in b["indicators"]} == {i for i, c in cat.items() if c == k}, k   # قسمه وحده
    dumped = json.dumps(eco, ensure_ascii=False)
    assert not any(x in dumped for x in ("NASDAQ100", "SP500", "nq_limits", "relative_performance", "leadership"))
    assert set(eco["cross_section"]) == {"quadrant", "sahm_recent"}
    mk = ctx["market_inputs"]
    assert mk["ok"] and set(mk["relative_performance"]) == {"as_of", "d5", "d10", "d20"}
    assert '"last"' not in json.dumps(mk) and "points" not in json.dumps(mk)          # مشتق فقط، بلا مستويات


def test_signals_go_to_the_section_their_series_belongs_to(payload):
    eco = ai_report.context(payload, now_utc=NOW)["economy_inputs"]["sections"]
    keys = {k: [s["key"] for s in eco[k]["signals"]] for k in ECON}
    assert keys["labor"] == ["sahm"] and keys["fed_rates"] == ["curve", "real10", "breakeven"]
    assert keys["financial"] == ["hy", "vix"] and keys["growth"] == [] and keys["inflation"] == []


# ---------------------------------------------------------------- the instructions: two separate steps
def test_the_economic_prompt_has_no_market_step_and_the_market_prompt_derives_from_the_report():
    s, m = ai_report.SYSTEM, ai_report.SYSTEM_MARKETS
    assert "one brief per section" in s and "Read every section" in s and '"ok": false is unavailable' in s
    assert "does not translate it into any one market" in s and "Do not write about any specific equity index" in s
    for x in ("market_translation", "impact", "nq_vs_spx_10d", "Nasdaq", "Dow", "NQ"):
        assert x not in s, x
    out = s[s.index("Output:"):]
    assert out.index('"economy"') < out.index('"events"')
    assert "economic report in \"economy_report\" is final" in m and "Do not re-assess the economy" in m
    assert "largely similar to the S&P 500" in m and "Energy has a small weight in the Dow" in m
    assert "from -2 to +2" in m and 'no "uncertain" level' in m and "50 means no view" in m
    assert "never quote an index level" in m and "No entries, stops, targets, or buy/sell recommendations" in m
    assert "No single dominant driver today" in s and "never invent a conflict" in s
    assert "official release checked via web" in s


def test_the_market_step_runs_after_the_report_from_the_finished_report(payload):
    seen, calls = {}, []
    rep = ai_report.write_report(payload, CFG, now_utc=NOW, caller=caller_for(seen=seen, calls=calls), attempt="1")
    assert calls == ["economy", "market"]                                        # بعد اكتمال التقرير
    assert "market_brief" not in seen["user"] and "nq_limits" not in seen["user"]
    mk = json.loads(seen["market"]["user"])
    assert set(mk) == {"economy_report", "market_brief"} and mk["economy_report"]["economy"] == rep["economy"]
    assert seen["market"]["system"] == ai_report.SYSTEM_MARKETS and rep["market_translation"]["gates"]["nq"]["impact"] == -1
    m = rep["meta"]
    assert m["market"]["ok"] and m["market"]["started_at"] >= m["completed_at"]
    assert m["cost_total"] == round(m["cost_estimate"] + m["market"]["cost_estimate"], 4)


def test_a_failed_market_step_never_takes_the_economic_report_down(payload):
    for market in ("no tags", text({"economy": {}})):
        rep = ai_report.write_report(payload, CFG, now_utc=NOW, caller=caller_for(market=market), attempt="1")
        assert "market_translation" not in rep and rep["meta"]["market"]["ok"] is False
        assert any(w.startswith("market translation failed: MarketError") for w in rep["warnings"])
        assert rep["one_liner"] == "الخلاصة"                                    # التقرير الاقتصادي منشور


def test_a_failed_economic_step_never_calls_the_market_step(payload):
    calls = []
    with pytest.raises(ai_report.ReportError):
        ai_report.write_report(payload, CFG, now_utc=NOW, caller=caller_for(b={"economy": {}}, calls=calls), attempt="1")
    assert calls == ["economy"]


def test_a_failed_collector_is_marked_unavailable_and_the_others_still_go_to_the_model(payload):
    from macro_platform import collectors
    briefs = collectors.from_payload(payload, None, None, NOW)
    briefs["labor"] = collectors.failed("labor", NOW, RuntimeError("FRED down"))
    seen = {}
    from datetime import timedelta
    rep = ai_report.write_report(payload, CFG, now_utc=NOW, caller=caller_for(seen=seen), briefs=briefs, attempt="1",
                                 clock=lambda: NOW + timedelta(minutes=2))
    sent = json.loads(seen["user"].split("\n", 1)[1])["sections"]
    assert sent["labor"] == {"section": "labor", "title": "سوق العمل", "collected_at": "2026-11-16T14:05:00Z",
                             "ok": False, "error": "RuntimeError: FRED down"}
    assert all(sent[k]["ok"] for k in ECON if k != "labor")
    r = rep["meta"]["inputs"]["sections"]
    assert r["labor"]["ok"] is False and r["growth"]["ok"] and r["growth"]["before_model_call"]


# ---------------------------------------------------------------- what is retried and what is published with a warning
def _econ(b):
    return {k: v for k, v in b.items() if k != "market_translation"}       # الخطوة الأولى بلا Market Gates


def _warn(b):
    return ai_report.parse_report(text(_econ(b)), KNOWN)["warnings"]


def test_a_complete_report_has_no_warnings():
    assert _warn(body()) == []


@pytest.mark.parametrize("mutate, expect", [
    (lambda mt: mt["gates"].update(nq=gate(3)), "market gate nq: impact must be a whole number"),
    (lambda mt: mt["gates"].update(dow=gate(0.5)), "market gate dow: impact"),
    (lambda mt: mt["gates"].pop("spx"), "market gate spx is missing"),
    (lambda mt: mt.update(nq_vs_spx_10d={"prob": 101}), "nq_vs_spx_10d: prob"),
])
def test_market_step_issues_are_warnings(mutate, expect):
    mt = body()["market_translation"]
    mutate(mt)
    got, warn = ai_report.parse_market(text({"market_translation": mt}))
    assert got == mt and any(expect in w for w in warn), warn


def test_an_unreadable_market_output_is_a_market_error_not_a_report_error():
    for raw in ("no tags", "<report>{bad</report>", text({"economy": {}})):
        with pytest.raises(ai_report.MarketError):
            ai_report.parse_market(raw)
    assert not issubclass(ai_report.MarketError, ai_report.ReportError)          # لا إعادة للتقرير بسببها


def test_market_translation_in_the_economic_output_is_ignored_with_a_warning():
    rep = ai_report.parse_report(text(body()), KNOWN)
    assert "market_translation" not in rep and rep["warnings"] == [
        "the economic output contained market_translation; ignored (written in the second step)"]


@pytest.mark.parametrize("mutate, expect", [
    (lambda b: b["economy"]["scenarios"][0].update(prob=46), "probabilities sum to 101"),
    (lambda b: b["economy"].pop("regime"), "missing field: economy.regime"),
    (lambda b: b["economy"].pop("key_conflicts"), "missing field: economy.key_conflicts"),
    (lambda b: b["economy"]["dominant_driver"].update(evidence=["DFII10", "MADE_UP"]), "unknown evidence ids removed: MADE_UP"),
    (lambda b: b["economy"]["dominant_driver"].update(kind="event", evidence=[], event_ids=["e9"]), "without a sourced event"),
    (lambda b: b["economy"]["dominant_driver"].update(kind="data", evidence=[]), "kind data without the evidence"),
    (lambda b: b["economy"]["dominant_driver"].update(kind="vibes"), "kind must be data, event or mixed"),
])
def test_non_blocking_issues_publish_with_a_visible_warning(mutate, expect):
    b = _econ(body())
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
    b = _econ(body())
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
    sec = ai_report.context(p, feed=feed, now_utc=NOW)["economy_inputs"]["sections"]
    assert [f["event"] for f in sec["inflation"]["released_today_not_in_platform_data"]] == ["Core CPI m/m"]
    assert sec["labor"]["released_today_not_in_platform_data"] == []              # في قسم سلسلته وحده


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
