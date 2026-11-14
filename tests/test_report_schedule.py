"""جدولة تقرير Claude (قرار Abdu، 14 نوفمبر 2026): تقرير واحد لكل يوم تداول.

المحاولة 1 عند 09:00 بتوقيت نيويورك؛ إن نجحت فلا تقرير آخر. إن فشلت فشلاً تقنياً فمحاولة 2 واحدة عند 12:00، ثم
توقف. لا محاولة تبدأ بعد 13:00. الثالثة يدوية فقط. تشغيلات cron في update.yml تحدّث البيانات فقط.

القياس الذي بُني عليه الطلب من دورة الأخبار (سجل GitHub Actions، 4 إلى 13 نوفمبر 2026): مواعيد update.yml
الصباحية الستة لم تُنفَّذ مرة (0 من 48)، بينما دارت حلقة الأخبار كل 10 دقائق.
"""
import json
import re
from pathlib import Path

import pytest

import fetch_macro
import fetch_news
import report_trigger
from _report import body, caller_for, freeze, utc
from macro_platform import ai_report, ledger
from macro_platform.build import load_config
from macro_platform.demo import demo_series

ROOT = Path(__file__).resolve().parent.parent
CFG = load_config(ROOT / "config.json")["ai"]
WRITE = ai_report.write_report          # الأصل، قبل أي monkeypatch


def run(status="completed", created="2026-11-16T14:05:00Z", attempt=None, conclusion="success",
        event="workflow_dispatch"):
    title = f"تحديث البيانات وتقرير Claude (report-attempt-{attempt})" if attempt else "تحديث بيانات الاقتصاد الكلي"
    return {"status": status, "event": event, "createdAt": created, "displayTitle": title, "conclusion": conclusion}


def rec(attempt, ok, day="2026-11-16", reason=None, cls=None):
    return {"ny_date": day, "attempt": attempt, "ok": ok, "reason": reason,
            "failure_class": cls if cls is not None else (None if ok else "retryable")}


FAILED_1 = {"ai_attempts": [rec("1", False, reason="APIConnectionError: reset")]}


# ---------------------------------------------------------------- the schedule, in New York time
# الاثنين 16 نوفمبر 2026 (شتوي EST = UTC−5) والأربعاء 15 يوليو 2026 (صيفي EDT = UTC−4)
@pytest.mark.parametrize("now, state, runs, want, why", [
    ("2026-11-16T13:59:00", {}, [], None, "before 09:00"),                      # 08:59
    ("2026-11-16T14:00:00", {}, [], 1, "attempt 1 at 09:00"),                    # 09:00
    ("2026-11-16T14:07:00", {}, [], 1, "attempt 1 at 09:07"),
    ("2026-11-16T13:00:00", {}, [], None, "before 09:00"),                       # 08:00 EST، وكانت 09:00 بإزاحة صيفية ثابتة
    ("2026-07-15T12:59:00", {}, [], None, "before 09:00"),                       # 08:59 EDT
    ("2026-07-15T13:00:00", {}, [], 1, "attempt 1 at 09:00"),                    # 09:00 EDT
    ("2026-11-16T16:59:00", FAILED_1, [], None, "retry at 12:00"),               # 11:59 بعد فشل
    ("2026-11-16T17:00:00", FAILED_1, [], 2, "attempt 2 at 12:00"),              # 12:00 بعد فشل تقني
    ("2026-11-16T17:00:00", {"ai_attempts": [rec("1", True)]}, [], None, "already published"),
    ("2026-11-16T17:00:00", {"ai_attempts": [rec("1", True, cls="published_with_warnings")]}, [], None,
     "already published"),                                                        # نُشر مع تحذير: لا إعادة
    ("2026-11-16T21:00:00", {"ai_attempts": [rec("1", True)]}, [], None, "already published"),   # المساء
    ("2026-11-16T17:30:00", {"ai_attempts": [rec("1", False), rec("2", False)]}, [], None, "manual only"),
    ("2026-11-16T18:01:00", {}, [], None, "no attempt starts after 13:00"),      # 13:01 بلا محاولة
    ("2026-11-16T18:01:00", FAILED_1, [], None, "no attempt starts after 13:00"),
    ("2026-11-16T17:30:00", {}, [], 1, "attempt 1 at 12:30"),                    # الحلقة كانت متوقفة صباحاً
    ("2026-11-16T14:20:00", {}, [run("in_progress", attempt=1)], None, "queued or running"),
    ("2026-11-16T17:00:00", FAILED_1, [run("queued")], None, "queued or running"),   # محاولة جارية عند 12:00
    ("2026-11-16T14:20:00", {"ai_last_date": "2026-11-16"}, [], None, "already published"),
])
def test_schedule_09_00_one_retry_at_12_00_and_nothing_after_13_00(now, state, runs, want, why):
    got, reason = ai_report.dispatch_decision(utc(now), state, runs, CFG)
    assert got == want and why in reason, reason


@pytest.mark.parametrize("now", ["2026-11-14T15:00:00", "2026-11-15T15:00:00", "2026-11-26T15:00:00"])
def test_no_request_on_weekends_or_nyse_holidays(now):
    got, why = ai_report.dispatch_decision(utc(now), {}, [], CFG)
    assert got is None and "not a trading day" in why


def test_a_run_that_died_before_recording_counts_as_a_failed_attempt():
    died = [run(created="2026-11-16T14:01:00Z", attempt=1, conclusion="cancelled")]
    got, why = ai_report.dispatch_decision(utc("2026-11-16T16:00:00"), {}, died, CFG)
    assert got is None and "retry at 12:00" in why and "without recording" in why
    got, why = ai_report.dispatch_decision(utc("2026-11-16T17:05:00"), {}, died, CFG)
    assert got == 2 and "run ended (cancelled)" in why
    yesterday = [run(created="2026-11-14T04:30:00Z", attempt=1, conclusion="failure")]   # 23:30 الجمعة بتوقيت نيويورك
    assert ai_report.dispatch_decision(utc("2026-11-16T14:00:00"), {}, yesterday, CFG)[0] == 1
    data_only = [run(created="2026-11-16T13:37:00Z", event="schedule")]                  # تشغيل cron: ليس محاولة
    assert ai_report.dispatch_decision(utc("2026-11-16T14:00:00"), {}, data_only, CFG)[0] == 1


def test_trigger_cli_prints_one_decision_and_never_fails(tmp_path, capsys):
    runs, state = tmp_path / "runs.json", tmp_path / "state.json"
    runs.write_text("[]", encoding="utf-8")
    state.write_text(json.dumps({"ai_last_date": "2026-11-13"}), encoding="utf-8")
    assert report_trigger.main(["--runs", str(runs), "--state", str(state)], now=utc("2026-11-16T14:05:00")) == 0
    assert capsys.readouterr().out.strip() == "dispatch 1: attempt 1 at 09:05 New York"
    state.write_text(json.dumps(FAILED_1), encoding="utf-8")
    report_trigger.main(["--runs", str(runs), "--state", str(state)], now=utc("2026-11-16T17:02:00"))
    assert capsys.readouterr().out.startswith("dispatch 2: attempt 2 at 12:02 New York")
    runs.write_text("", encoding="utf-8")                  # فشل gh run list: لا طلب
    assert report_trigger.main(["--runs", str(runs), "--state", str(state)], now=utc("2026-11-16T17:02:00")) == 0
    assert capsys.readouterr().out.startswith("skip: could not read")
    assert report_trigger.main(["--runs", str(runs), "--state", str(tmp_path / "missing.json")]) == 0
    assert capsys.readouterr().out.startswith("skip:")


# ---------------------------------------------------------------- the only paths that write a paid report
@pytest.mark.parametrize("attempt, now, state, ok, why", [
    ("", "2026-11-16T19:35:00", {"ai_last_date": "2026-11-13"}, False, "data update only"),   # مسار 19:35 UTC القديم
    ("1", "2026-11-14T15:00:00", {}, False, "not a trading day"),
    ("1", "2026-11-16T14:00:00", {"ai_attempts": [rec("1", True)]}, False, "already published"),
    ("1", "2026-11-16T14:00:00", FAILED_1, False, "already recorded"),
    ("2", "2026-11-16T17:00:00", FAILED_1, True, "attempt 2"),
    ("manual", "2026-11-14T15:00:00", {"ai_attempts": [rec("1", True, day="2026-11-14")]}, True, "manual"),
])
def test_update_run_guard(attempt, now, state, ok, why):
    got, reason = ai_report.attempt_allowed(attempt, utc(now), state, CFG)
    assert got is ok and why in reason


def _main(monkeypatch, tmp_path, at, attempt, caller, done=None):
    """fetch_macro.main كاملاً بساعة مثبتة، وFRED وnews.json ونموذج مزيفين."""
    freeze(monkeypatch, at, done)
    monkeypatch.setattr(ai_report, "write_report", lambda *a, **k: WRITE(*a, caller=caller, **k))
    series = demo_series(start="2015-01-01", end="2026-06-30")
    monkeypatch.setattr(fetch_macro, "fetch_all", lambda raw, start, log=None: (series, {}))
    monkeypatch.setattr(fetch_macro, "load_status", lambda p: ({}, []))
    monkeypatch.setattr(fetch_news, "load_live", lambda url: {})
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fetch_macro, "DATA", data)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    if attempt is None:
        monkeypatch.delenv("REPORT_ATTEMPT", raising=False)
    else:
        monkeypatch.setenv("REPORT_ATTEMPT", attempt)
    assert fetch_macro.main(["--out", str(tmp_path / "data.json")]) == 0
    read = lambda n: json.loads((data / n).read_text(encoding="utf-8")) if (data / n).exists() else None
    return read("state.json"), read("ai_history.json"), read("forecast_ledger.json"), \
        json.loads((tmp_path / "data.json").read_text(encoding="utf-8"))


def boom(*a):
    raise ai_report.ReportError("no <report> block in the model output", {"output_tokens": 9}, {"stop_reason": "end_turn"})


def test_a_cron_data_run_never_writes_a_report(monkeypatch, tmp_path):
    calls = []
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "state.json").write_text(json.dumps({"ai_last_date": "2026-11-13"}), encoding="utf-8")
    state, hist, led, _ = _main(monkeypatch, tmp_path, "2026-11-16T19:35:00", None, caller_for(calls=calls))
    assert calls == [] and hist is None and led is None and "ai_attempts" not in state


def test_failure_at_09_then_success_at_12_is_one_forecast_and_two_logged_attempts(monkeypatch, tmp_path):
    state, hist, led, page = _main(monkeypatch, tmp_path, "2026-11-16T14:02:00", "1", boom)
    a1 = state["ai_attempts"][0]
    assert (a1["attempt"], a1["ok"], a1["failure_class"]) == ("1", False, "retryable")
    assert "no <report> block" in a1["reason"] and a1["usage"] == {"output_tokens": 9} and a1["stop_reason"] == "end_turn"
    assert a1["scheduled_for"] == "09:00 America/New_York" and hist is None and led is None
    assert page["ai_status"]["attempts"][0]["reason"].startswith("ReportError")
    assert ai_report.dispatch_decision(utc("2026-11-16T17:00:00"), state, [], CFG)[0] == 2

    state, hist, led, _ = _main(monkeypatch, tmp_path, "2026-11-16T17:01:00", "2", caller_for())
    assert [(a["attempt"], a["ok"]) for a in state["ai_attempts"]] == [("1", False), ("2", True)]
    m = hist[0]["meta"]
    assert (m["attempt"], m["scheduled_for"], m["trigger"], m["pre_open"], m["delayed"]) == \
        ("2", "12:00 America/New_York", "schedule", False, True)
    assert m["model"] == "claude-opus-5-5" and m["cost_estimate"] is not None and m["usage"]["web_search_requests"] == 3
    assert len(led["entries"]) == 1 and led["entries"][0]["timing"]["attempt"] == "2"
    assert led["entries"][0]["timing"]["delayed"] is True and state["ai_last_date"] == "2026-11-16"
    got, why = ai_report.dispatch_decision(utc("2026-11-16T17:30:00"), state, [], CFG)
    assert got is None and "already published" in why


def test_a_pre_open_success_is_marked_pre_open(monkeypatch, tmp_path):
    state, hist, led, _ = _main(monkeypatch, tmp_path, "2026-11-16T14:01:00", "1", caller_for(),
                                done="2026-11-16T14:21:00")      # اكتمل 09:21 قبل الافتتاح
    m = hist[0]["meta"]
    assert (m["pre_open"], m["delayed"], m["completed_at"]) == (True, False, "2026-11-16T14:21:00Z")
    assert state["ai_attempts"][0]["failure_class"] is None
    late = _main(monkeypatch, tmp_path / "b", "2026-11-16T14:01:00", "1", caller_for(), done="2026-11-16T14:41:00")
    assert late[1][0]["meta"]["delayed"] is True                  # المحاولة 1 نفسها، لكنها اكتملت 09:41


def test_manual_writes_a_report_but_never_the_forecast_ledger(monkeypatch, tmp_path):
    state, hist, led, _ = _main(monkeypatch, tmp_path, "2026-11-14T15:00:00", "manual", caller_for())
    assert hist[0]["meta"]["trigger"] == "manual" and hist[0]["meta"]["scheduled_for"] is None
    assert led is None and "ai_last_date" not in state and state["ai_attempts"][0]["attempt"] == "manual"


def test_a_different_served_model_is_a_failure_not_a_report(monkeypatch, tmp_path):
    state, hist, _, _ = _main(monkeypatch, tmp_path, "2026-11-16T14:02:00", "1", caller_for(served="claude-opus-5"))
    a = state["ai_attempts"][0]
    assert hist is None and a["ok"] is False and "served by claude-opus-5" in a["reason"]
    assert a["model_served"] == "claude-opus-5" and a["model_requested"] == "claude-opus-5-5"


def test_published_with_warnings_is_logged_and_not_retried(monkeypatch, tmp_path):
    b = body(prob=101, gates={"nq": {"impact": 3}})
    state, hist, led, _ = _main(monkeypatch, tmp_path, "2026-11-16T14:02:00", "1", caller_for(b))
    a = state["ai_attempts"][0]
    assert a["ok"] is True and a["failure_class"] == "published_with_warnings" and a["warnings"] == len(hist[0]["warnings"])
    assert led["entries"][0]["translation_check"] == {"unscorable": "probability missing or not a whole number from 0 to 100"}
    assert ai_report.dispatch_decision(utc("2026-11-16T17:00:00"), state, [], CFG)[0] is None


# ---------------------------------------------------------------- the workflows
NEWS = (ROOT / ".github" / "workflows" / "news.yml").read_text(encoding="utf-8")
UPDATE = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")


def _step(wf, name):
    m = re.search(r"- name: " + re.escape(name) + r"\n(.*?)(?=\n      - |\Z)", wf, re.S)
    assert m, name
    return m.group(1)


def test_news_tick_requests_the_attempt_number_independently_of_the_news_fetch():
    step = _step(NEWS, "طلب تقرير اليوم عند الحاجة (update.yml)")
    assert "if: ${{ !cancelled() }}" in step and "continue-on-error: true" in step
    assert "secrets." not in step and "ANTHROPIC_API_KEY" not in step
    assert "status,event,createdAt,displayTitle,conclusion" in step
    assert '[[ "$d" =~ ^dispatch\\ ([12]): ]]' in step and '-f report_attempt="${BASH_REMATCH[1]}"' in step
    assert "--ref main" in step and "manual" not in step                   # الدورة لا تطلب محاولة يدوية أبداً
    assert NEWS.index("طلب تقرير اليوم") < NEWS.index("جلب الأخبار وترجمتها")   # قبل الجلب، فلا يتأثر بفشله
    assert re.search(r"permissions:\n(?:  .*\n)*  actions: write", NEWS)


def test_update_workflow_writes_a_report_only_for_a_requested_attempt():
    assert "ai_force" not in UPDATE and "AI_FORCE" not in UPDATE
    assert "REPORT_ATTEMPT: ${{ inputs.report_attempt }}" in UPDATE
    assert "report-attempt-{0}" in UPDATE.split("on:")[0]                  # run-name يحمل رقم المحاولة
    assert "AI_FORCE" not in (ROOT / "fetch_macro.py").read_text(encoding="utf-8")


def test_update_run_records_publication_time_and_the_attempt_and_keeps_its_own_lock():
    step = _step(UPDATE, "توقيت التقرير (بدء، اكتمال، نشر)")
    assert "GITHUB_STEP_SUMMARY" in step and "published" in step and 'm.get("run_id") == os.environ["GITHUB_RUN_ID"]' in step
    assert "ai_attempts" in step
    assert UPDATE.index("توقيت التقرير") > UPDATE.index("deploy-pages")
    assert "group: macro-data" in UPDATE and "cancel-in-progress: false" in UPDATE


def test_page_config_carries_the_schedule_and_holidays():
    from macro_platform.build import build_payload
    payload = build_payload(demo_series(start="2020-01-01", end="2026-06-30"), {}, load_config(ROOT / "config.json"),
                            now=utc("2026-11-16T13:45:00"))
    assert payload["config"]["ai_schedule"]["market_holidays"] == CFG["market_holidays"]
    assert payload["config"]["ai_schedule"]["schedule"]["attempt_2"] == "12:00"
    assert CFG["effort"] == "xhigh" and "trigger_window" not in CFG and "max_dispatches" not in CFG
