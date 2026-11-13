"""جدولة تقرير Claude: دورة الأخبار (كل 10 دقائق) تطلب update.yml مرة واحدة بعد بيانات 08:30 بتوقيت نيويورك.

القياس الذي بُني عليه (سجل GitHub Actions، 4 إلى 13 نوفمبر 2026): مواعيد update.yml الصباحية الستة لم تُنفَّذ مرة
(0 من 48)، وموعدا بعد الظهر نُفّذا متأخرين ساعة إلى خمس ساعات، بينما دارت حلقة الأخبار كل 10 دقائق.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import report_trigger
from macro_platform import ai_report
from macro_platform.build import load_config

ROOT = Path(__file__).resolve().parent.parent
CFG = load_config(ROOT / "config.json")["ai"]


def utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def run(status="completed", event="workflow_dispatch", created="2026-11-16T13:41:00Z"):
    return {"status": status, "event": event, "createdAt": created}


# الاثنين 16 نوفمبر 2026 (توقيت شتوي EST = UTC−5) والأربعاء 15 يوليو 2026 (صيفي EDT = UTC−4)
@pytest.mark.parametrize("now, want", [
    ("2026-11-16T13:39:00", False),   # 08:39 EST: قبل النافذة
    ("2026-11-16T13:40:00", True),    # 08:40 EST
    ("2026-11-16T14:29:00", True),    # 09:29 EST: ما زال قبل الافتتاح
    ("2026-11-16T16:29:00", True),    # 11:29 EST: آخر محاولة
    ("2026-11-16T16:30:00", False),   # 11:30 EST: انتهت النافذة
    ("2026-11-16T12:45:00", False),   # 07:45 EST، وكانت 08:45 لو حُسبت بإزاحة صيفية ثابتة
    ("2026-07-15T12:45:00", True),    # 08:45 EDT
    ("2026-07-15T13:45:00", True),    # 09:45 EDT
    ("2026-07-15T12:35:00", False),   # 08:35 EDT
])
def test_window_is_new_york_time_in_summer_and_winter(now, want):
    ok, why = ai_report.dispatch_decision(utc(now), "2026-11-13", [], CFG)
    assert ok is want, why


@pytest.mark.parametrize("now, reason", [
    ("2026-11-14T14:00:00", "not a trading day"),     # السبت
    ("2026-11-15T14:00:00", "not a trading day"),     # الأحد
    ("2026-11-26T14:00:00", "not a trading day"),     # Thanksgiving في ai.market_holidays
])
def test_no_request_outside_trading_days(now, reason):
    ok, why = ai_report.dispatch_decision(utc(now), None, [], CFG)
    assert not ok and reason in why


def test_no_request_once_todays_report_exists():
    ok, why = ai_report.dispatch_decision(utc("2026-11-16T14:00:00"), "2026-11-16", [], CFG)
    assert not ok and "already written" in why


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "requested", "pending"])
def test_no_request_while_an_update_run_is_waiting_or_running(status):
    ok, why = ai_report.dispatch_decision(utc("2026-11-16T14:00:00"), "2026-11-13", [run(status)], CFG)
    assert not ok and "queued or running" in why


def test_daily_request_limit_counts_new_york_days_only():
    now = utc("2026-11-16T15:00:00")                       # 10:00 EST
    yesterday_ny = run(created="2026-11-16T04:30:00Z")     # 23:30 الأحد بتوقيت نيويورك
    two_today = [run(created="2026-11-16T13:41:00Z"), run(created="2026-11-16T14:01:00Z")]
    assert ai_report.dispatch_decision(now, "2026-11-13", two_today + [yesterday_ny], CFG)[0] is True
    three_today = two_today + [run(created="2026-11-16T14:21:00Z")]
    ok, why = ai_report.dispatch_decision(now, "2026-11-13", three_today, CFG)
    assert not ok and "limit" in why
    sched = [run(event="schedule", created="2026-11-16T14:2%d:00Z" % k) for k in range(5)]
    assert ai_report.dispatch_decision(now, "2026-11-13", two_today + sched, CFG)[0] is True  # المجدولة لا تُحسب


def test_update_side_also_skips_holidays_and_weekends():
    assert ai_report.should_run(utc("2026-11-16T14:00:00"), "2026-11-13", CFG)
    assert not ai_report.should_run(utc("2026-11-26T14:00:00"), "2026-11-25", CFG)
    assert not ai_report.should_run(utc("2026-11-14T14:00:00"), "2026-11-13", CFG)


def test_trigger_cli_prints_one_decision_and_never_fails(tmp_path, capsys):
    runs, state = tmp_path / "runs.json", tmp_path / "state.json"
    runs.write_text("[]", encoding="utf-8")
    state.write_text(json.dumps({"ai_last_date": "2026-11-13"}), encoding="utf-8")
    now = utc("2026-11-16T13:45:00")
    assert report_trigger.main(["--runs", str(runs), "--state", str(state)], now=now) == 0
    assert capsys.readouterr().out.startswith("dispatch: attempt 1 today at 08:45 New York")
    runs.write_text("", encoding="utf-8")                  # فشل gh run list: لا طلب
    assert report_trigger.main(["--runs", str(runs), "--state", str(state)], now=now) == 0
    assert capsys.readouterr().out.startswith("skip: could not read")
    assert report_trigger.main(["--runs", str(runs), "--state", str(tmp_path / "missing.json")], now=now) == 0
    assert capsys.readouterr().out.startswith("skip:")


def test_report_records_start_completion_and_trigger(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    body = {"title": "t", "summary": ["s"], "bias": {"direction": "neutral", "confidence": 50, "reason": "r"},
            "scenarios": [{"label": x, "name": x, "prob": p, "trigger": "", "reason": ""}
                          for x, p in (("A", 50), ("B", 30), ("C", 20))], "one_liner": "o"}

    def caller(user, system, model, effort):
        return "<report>" + json.dumps(body) + "</report>", [], {k: 0 for k in ai_report.USAGE_KEYS}, model

    from macro_platform.demo import demo_series
    from macro_platform.build import build_payload
    payload = build_payload(demo_series(start="2020-01-01", end="2026-06-30"), {}, load_config(ROOT / "config.json"),
                            now=utc("2026-11-16T13:45:00"))
    m = ai_report.write_report(payload, load_config(ROOT / "config.json"), now_utc=utc("2026-11-16T13:45:00"),
                               caller=caller)["meta"]
    assert m["started_at"] <= m["completed_at"] and m["trigger"] == "workflow_dispatch" and m["run_id"] == "123"
    assert payload["config"]["ai_schedule"]["market_holidays"] == CFG["market_holidays"]


NEWS = (ROOT / ".github" / "workflows" / "news.yml").read_text(encoding="utf-8")
UPDATE = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")


def _step(wf, name):
    m = re.search(r"- name: " + re.escape(name) + r"\n(.*?)(?=\n      - |\Z)", wf, re.S)
    assert m, name
    return m.group(1)


def test_news_tick_requests_the_report_independently_of_the_news_fetch():
    step = _step(NEWS, "طلب تقرير اليوم عند الحاجة (update.yml)")
    assert "if: ${{ !cancelled() }}" in step and "continue-on-error: true" in step
    assert "secrets." not in step and "ANTHROPIC_API_KEY" not in step
    assert "python report_trigger.py" in step and "gh workflow run update.yml" in step and "--ref main" in step
    assert 'case "$d" in dispatch*)' in step                  # يطلب فقط عند قرار dispatch
    assert NEWS.index("طلب تقرير اليوم") < NEWS.index("جلب الأخبار وترجمتها")   # قبل الجلب، فلا يتأثر بفشله
    assert re.search(r"permissions:\n(?:  .*\n)*  actions: write", NEWS)


def test_update_run_records_publication_time_and_keeps_its_own_lock():
    step = _step(UPDATE, "توقيت التقرير (بدء، اكتمال، نشر)")
    assert "GITHUB_STEP_SUMMARY" in step and "published" in step and 'm.get("run_id") == os.environ["GITHUB_RUN_ID"]' in step
    assert UPDATE.index("توقيت التقرير") > UPDATE.index("deploy-pages")
    assert "group: macro-data" in UPDATE and "cancel-in-progress: false" in UPDATE
