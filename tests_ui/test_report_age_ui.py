"""عمر تقرير Claude في الصفحة: وقت كتابته بتوقيت نيويورك، وتحذير صريح إن تأخر تقرير يوم التداول أو فشل.
ساعة المتصفح مثبّتة (Playwright clock) فلا تعتمد النتيجة على وقت تشغيل الاختبار."""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cpi_ui_case", ROOT / "tests_ui" / "test_cpi_ui.py")
cpi_ui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cpi_ui)


def rep(ny_date, completed):
    return {"title": "عنوان تقرير", "summary": ["s"], "bias": {"direction": "neutral", "confidence": 50, "reason": "r"},
            "scenarios": [{"label": x, "name": x, "prob": p, "trigger": "", "reason": ""}
                          for x, p in (("A", 50), ("B", 30), ("C", 20))], "one_liner": "o",
            "meta": {"ny_date": ny_date, "model": "m", "written_at": completed, "completed_at": completed}}


def open_ai(ui, hist, now, warnings=()):
    payload = cpi_ui._payload()
    payload["ai_available"] = True
    payload["status"]["warnings"] = list(payload["status"]["warnings"]) + list(warnings)
    page = ui.open(payload, {"data/ai_history.json": hist}, now=datetime.fromisoformat(now).replace(tzinfo=timezone.utc))
    page.click('button.tab[data-k="ai"]')
    return page.locator("#p-ai")


def test_on_time_report_shows_new_york_time_before_the_open(ui):
    ai = open_ai(ui, [rep("2026-11-16", "2026-11-16T13:46:00Z")], "2026-11-16T15:00:00")   # 10:00 EST
    assert "كُتب 2026-11-16 08:46 بتوقيت نيويورك · قبل الافتتاح" in ai.inner_text()
    assert ai.locator(".ai-late").count() == 0


def test_report_written_after_the_open_says_how_late(ui):
    ai = open_ai(ui, [rep("2026-11-13", "2026-11-13T19:31:00Z")], "2026-11-13T20:00:00")   # 14:31 EST
    assert "كُتب 2026-11-13 14:31 بتوقيت نيويورك · بعد الافتتاح بـ301 د" in ai.inner_text()


def test_missing_report_after_the_open_on_a_trading_day_is_flagged(ui):
    ai = open_ai(ui, [rep("2026-11-13", "2026-11-13T12:31:00Z")], "2026-11-16T15:00:00")   # الاثنين 10:00 EST
    warn = ai.locator(".ai-late").inner_text()
    assert "لا تقرير لليوم (2026-11-16) حتى الآن بعد افتتاح نيويورك. آخر تقرير: 2026-11-13." in warn


def test_no_flag_before_the_open_on_weekends_or_holidays(ui):
    hist = [rep("2026-11-13", "2026-11-13T12:31:00Z")]
    for now in ("2026-11-16T14:00:00",      # الاثنين 09:00 EST: قبل الافتتاح
                "2026-11-14T16:00:00",      # السبت
                "2026-11-26T16:00:00"):     # Thanksgiving (ai.market_holidays)
        assert open_ai(ui, hist, now).locator(".ai-late").count() == 0, now


def test_failed_report_attempt_is_shown_in_the_report_panel(ui):
    ai = open_ai(ui, [rep("2026-11-16", "2026-11-16T13:46:00Z")], "2026-11-16T15:00:00",
                 warnings=["فشل كتابة تقرير Claude: Error code: 400 - credit balance is too low"])
    assert "فشل كتابة تقرير Claude: Error code: 400" in ai.locator(".ai-late").inner_text()
