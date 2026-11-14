"""لوحة التقرير تعرض شرط قياس كل سيناريو ونتيجته بعد حسمه، ولا تقول «تحقق السيناريو»، وتقول إن العينة صغيرة
حين تكون صغيرة، ولا تدّعي «دلالة إحصائية»."""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from _report import body  # noqa: E402
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


def report(scen=None, day="2026-11-16", prob_sum=None):
    scen = scen if scen is not None else [sc("A", 45, 4.95, 5.10), sc("B", 35, None, 4.95), sc("C", 20, 5.10, None)]
    meta = {"ny_date": day, "model": "m", "completed_at": "2026-11-16T13:46:00Z", "written_at": "2026-11-16T13:46:00Z",
            "prob_sum": prob_sum if prob_sum is not None else sum(s["prob"] for s in scen)}
    return {**body(scenarios=scen), "title": "عنوان", "scenarios": scen, "meta": meta}


def view(rep, series=None, copies=1):
    led = {"version": 2, "entries": []}
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
    page = ui.open(payload, {"data/ai_history.json": hist}, now=datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc))
    page.click('button.tab[data-k="ai"]')
    return page.locator("#p-ai")


def d(points):
    return pd.Series({pd.Timestamp(k): v for k, v in points})


SETTLED = {"DGS10": d([("2026-11-17", 5.0), ("2026-11-20", 5.12), ("2026-11-23", 5.2)])}


def test_pending_checks_are_shown_as_not_settled_and_the_sample_as_small(ui):
    r = report()
    ai = ai_panel(ui, [r], view(r))
    a = ai.locator(".sc", has_text="سيناريو A").locator(".chk").inner_text()
    assert a == "شرط القياس: DGS10 من 4.95 إلى أقل من 5.1 في 2026-11-20 · لم يُحسم بعد"
    s = ai.locator(".led-sum").inner_text()
    assert "العينة ما زالت صغيرة ولا تكفي للحكم" in s and "لا صحة السرد الاقتصادي كله" in s
    scen = " ".join(ai.locator(".sc").all_inner_texts()) + s                  # شروط القياس اقتصادية: لا مؤشر أسهم
    assert "Nasdaq" not in scen and "NASDAQ" not in scen and "الانحياز غير قابل" not in ai.inner_text()


def test_settled_checks_say_the_check_was_met_never_the_scenario(ui):
    r = report()
    ai = ai_panel(ui, [r], view(r, SETTLED))
    c = ai.locator(".sc", has_text="سيناريو C").inner_text()
    assert "تحقق شرط قياس السيناريو (5.12 في 2026-11-20)" in c
    assert ai.locator(".sc", has_text="سيناريو A").locator(".miss").inner_text() == "لم يتحقق شرط قياس السيناريو"
    text = ai.inner_text()
    assert "تحقق السيناريو" not in text.replace("تحقق شرط قياس السيناريو", "")
    assert "دلالة إحصائية" not in text


def test_preliminary_assessment_appears_only_with_enough_cases(ui):
    r = report()
    s = ai_panel(ui, [r], view(r, SETTLED, copies=30)).locator(".led-sum").inner_text()
    assert "تقييم أولي متاح: Scenario-check Brier" in s and "0.6667" in s and "دلالة إحصائية" not in s


def test_probabilities_not_summing_to_100_are_shown_as_written_and_not_measured(ui):
    scen = [sc("A", 46, 4.95, 5.10), sc("B", 35, None, 4.95), sc("C", 20, 5.10, None)]
    r = report(scen=scen)
    ai = ai_panel(ui, [r], view(r))
    assert "مجموع احتمالات السيناريوهات 101 لا 100: عُرضت كما كتبها النموذج، ولا تُقاس." in ai.inner_text()
    assert "46%" in ai.locator(".sc", has_text="سيناريو A").inner_text()


def test_older_nq_reports_leave_the_report_tab_for_the_market_gates_archive(ui):
    # التقارير قبل 16 نوفمبر كُتبت بتعليمات موجهة لمتداول NQ: مكانها Market Gates ← NQ، لا تبويب التقرير الاقتصادي
    old = {"title": "انحياز سلبي حذر لـNQ", "summary": ["s"], "bias": {"direction": "bearish", "confidence": 60, "reason": "سبب NQ"},
           "scenarios": [{"label": "A", "name": "سيناريو A", "prob": 100}], "gates_read": "قراءة بوابات NQ", "one_liner": "o",
           "meta": {"ny_date": "2026-11-13", "model": "m", "completed_at": "2026-11-13T13:46:00Z"}}
    ai = ai_panel(ui, [old], None)
    assert "NQ" not in ai.inner_text() and ai.locator(".no-eco").count() == 1
    page = ai.page
    page.click('button.tab[data-k="gates"]')
    g = page.locator('#p-gates .gate-pane[data-g="nq"]')
    assert "انحياز NQ في آخر تقرير بالصيغة القديمة (2026-11-13)" in g.locator(".legacy-bias").inner_text()
    assert "هابط" in g.locator(".legacy-bias").inner_text() and "قراءة بوابات NQ" in g.locator(".legacy-bias").inner_text()
    g.locator(".legacy-archive summary").click()
    assert "انحياز سلبي حذر لـNQ" in g.locator(".legacy-archive").inner_text()


def test_unmeasurable_checks_say_why_and_text_is_escaped(ui):
    bad = "<img src=x onerror=alert(1)>"
    r = report(scen=[sc("A", 45, 4.95, 5.10, series=bad), sc("B", 35, None, 4.95, series=bad), sc("C", 20, 5.10, None, series=bad)])
    ai = ai_panel(ui, [r], view(r))
    assert "شروط السيناريوهات غير قابلة للقياس: checks must use one allowed series" in ai.inner_text()
    assert ai.locator("img").count() == 0 and bad in ai.locator(".sc", has_text="سيناريو A").inner_text()
