"""لوحة التقرير تعرض ما التزم به كل تقرير ونتيجته بعد حسمه، وتقول صراحة إن العينة صغيرة حين تكون صغيرة."""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from macro_platform import ledger
from macro_platform.build import load_config

ROOT = Path(__file__).resolve().parent.parent
CFG = load_config(ROOT / "config.json")["ai"]
_spec = importlib.util.spec_from_file_location("cpi_ui_case", ROOT / "tests_ui" / "test_cpi_ui.py")
cpi_ui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cpi_ui)


def sc(label, prob, low, high, series="DGS10"):
    return {"label": label, "name": f"سيناريو {label}", "prob": prob, "trigger": "t", "reason": "r",
            "check": {"series": series, "by": "2026-11-20", "low": low, "high": high}}


def report(scen=None, day="2026-11-16"):
    scen = scen if scen is not None else [sc("A", 45, 4.95, 5.10), sc("B", 35, None, 4.95), sc("C", 20, 5.10, None)]
    return {"title": "عنوان", "summary": ["s"], "bias": {"direction": "bearish", "confidence": 60, "reason": "r"},
            "scenarios": scen, "one_liner": "o",
            "meta": {"ny_date": day, "model": "m", "completed_at": "2026-11-16T13:46:00Z", "written_at": "2026-11-16T13:46:00Z"}}


def view(rep, series=None, copies=1):
    led = {"version": 1, "entries": []}
    for k in range(copies):
        e = ledger.entry(rep, CFG)
        if k:
            e["ny_date"] = f"2026-10-{k:02d}"
        ledger.add(led, e)
    ledger.resolve(led, series or {})
    return ledger.view(led)


def ai_panel(ui, hist, led):
    payload = cpi_ui._payload()
    payload["ai_available"] = True
    if led is not None:
        payload["ledger"] = led
    page = ui.open(payload, {"data/ai_history.json": hist},
                   now=datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc))
    page.click('button.tab[data-k="ai"]')
    return page.locator("#p-ai")


def d(points):
    return pd.Series({pd.Timestamp(k): v for k, v in points})


def test_pending_commitments_are_shown_as_not_settled(ui):
    r = report()
    ai = ai_panel(ui, [r], view(r))
    text = ai.inner_text()
    assert "يُقاس بإغلاق Nasdaq 100 في جلسة 2026-11-16 مقابل الإغلاق السابق · لم يُحسم بعد" in text
    a = ai.locator(".sc", has_text="سيناريو A").locator(".chk").inner_text()
    assert a == "القياس: DGS10 من 4.95 إلى أقل من 5.1 في 2026-11-20 · لم يُحسم بعد"
    assert "B" in text and "أقل من 4.95" in ai.locator(".sc", has_text="سيناريو B").inner_text()
    assert "أقل من 30 حالة محسومة: لا دلالة إحصائية بعد." in ai.locator(".led-sum").inner_text()


def test_settled_outcomes_name_the_scenario_that_happened(ui):
    r = report()
    led = view(r, {"NASDAQ100": d([("2026-11-13", 20000.0), ("2026-11-16", 20200.0)]),
                   "DGS10": d([("2026-11-17", 5.0), ("2026-11-20", 5.12), ("2026-11-23", 5.2)])})
    ai = ai_panel(ui, [r], led)
    assert "صعد 1%" in ai.inner_text() and ai.locator(".ai-grid .miss", has_text="أخطأ الاتجاه").count() == 1
    assert "تحقق (5.12 في 2026-11-20)" in ai.locator(".sc", has_text="سيناريو C").inner_text()
    assert ai.locator(".sc", has_text="سيناريو A").locator(".miss").inner_text() == "لم يتحقق"
    assert "سجل القياس: 1 انحيازاً محسوماً (أصاب الاتجاه 0 من 1)، و1 مجموعة سيناريوهات محسومة." in ai.inner_text()


def test_brier_scores_appear_only_with_enough_cases(ui):
    r = report()
    led = view(r, {"NASDAQ100": d([("2026-11-13", 20000.0), ("2026-11-16", 20200.0)]),
                   "DGS10": d([("2026-11-17", 5.0), ("2026-11-20", 5.12), ("2026-11-23", 5.2)])}, copies=30)
    s = ai_panel(ui, [r], led).locator(".led-sum").inner_text()
    assert "Brier (الأقل أفضل): الانحياز 0.36 مقابل 0.25 لمن يقول 50% دائماً" in s and "0.6667" in s


def test_older_reports_without_checks_render_as_before(ui):
    old = report(scen=[{k: v for k, v in s.items() if k != "check"} for s in report()["scenarios"]], day="2026-11-13")
    ai = ai_panel(ui, [old], None)
    assert ai.locator(".chk").count() == 0 and ai.locator(".led-sum").count() == 0
    assert "سيناريو A" in ai.inner_text()


def test_unmeasurable_checks_say_why_and_text_is_escaped(ui):
    bad = "<img src=x onerror=alert(1)>"
    r = report(scen=[sc("A", 45, 4.95, 5.10, series=bad), sc("B", 35, None, 4.95, series=bad), sc("C", 20, 5.10, None, series=bad)])
    ai = ai_panel(ui, [r], view(r))
    assert "شروط السيناريوهات غير قابلة للقياس: checks must use one allowed series" in ai.inner_text()
    assert ai.locator("img").count() == 0 and bad in ai.locator(".sc", has_text="سيناريو A").inner_text()
