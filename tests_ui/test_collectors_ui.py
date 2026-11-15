"""جامعو الأقسام في الصفحة: متى جُمعت مدخلات التقرير وهل سبقت استدعاء النموذج، وأن Market Gates كُتبت بعد اكتمال
التقرير (أو لماذا لم تُكتب)، وجدول الجامعين في حالة البيانات، وتنبيه حين يتعذّر جامع."""
import copy
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("nav_ui_case", ROOT / "tests_ui" / "test_navigation_ui.py")
nav = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nav)
ECON = ("growth", "inflation", "labor", "fed_rates", "financial")


def report(source="pre_report_collection", at="2026-11-16T13:41:00Z", bad=None, market_ok=True):
    r = nav.eco.report()
    secs = {k: {"ok": k != bad, "collected_at": at, "before_model_call": True, "error": None if k != bad else "RuntimeError: down"}
            for k in ECON}
    secs["news"] = {"ok": True, "collected_at": "2026-11-16T14:01:00Z", "before_model_call": True}
    secs["markets"] = {"ok": True, "collected_at": at, "before_model_call": True}
    r["meta"]["inputs"] = {"source": source, "model_call_started_at": "2026-11-16T14:01:00Z", "sections": secs}
    r["meta"]["market"] = ({"ok": True, "completed_at": "2026-11-16T14:18:00Z"} if market_ok
                           else {"ok": False, "reason": "MarketError: no <report> block in the market output",
                                 "completed_at": "2026-11-16T14:18:00Z"})
    if not market_ok:
        r.pop("market_translation", None)
    return r


def collection(bad=None):
    secs = {k: {"ok": k != bad, "collected_at": "2026-11-16T13:41:00Z", "available": 0 if k == bad else 5, "total": 5,
                "error": "RuntimeError: down" if k == bad else None} for k in ECON + ("markets",)}
    return {"collected_at": "2026-11-16T13:41:00Z", "ny_date": "2026-11-16", "trigger": "pre_report_collection", "sections": secs}


def test_the_report_says_when_its_inputs_were_collected_and_that_it_was_before_the_model_call(ui):
    page = nav.open_page(ui, nav.healthy(), hist=[report()])
    page.click('button.tab[data-k="ai"]')
    assert page.locator("#p-ai .inputs-line").inner_text() == (
        "المدخلات: جمعها جامعو الأقسام قبل موعد التقرير عند 08:41 نيويورك (14:41 فيينا)، قبل استدعاء النموذج · 5 من 5 أقسام والأخبار")


def test_an_unavailable_section_is_named_in_the_report(ui):
    page = nav.open_page(ui, nav.healthy(), hist=[report(source="report_run", at="2026-11-16T14:01:00Z", bad="labor")])
    page.click('button.tab[data-k="ai"]')
    line = page.locator("#p-ai .inputs-line").inner_text()
    assert "في تشغيل التقرير نفسه عند 09:01 نيويورك" in line and "4 من 5 أقسام" in line
    assert "غير متاح: سوق العمل (RuntimeError: down)" in line


def test_market_gates_say_they_were_written_after_the_report_or_why_not(ui):
    page = nav.open_page(ui, nav.healthy(), hist=[report()])
    page.click('button.tab[data-k="gates"]')
    assert "كُتبت بعد اكتمال التقرير الاقتصادي، عند 09:18 نيويورك (15:18 فيينا)" in page.locator("#p-gates .gates-after").inner_text()
    page = nav.open_page(ui, nav.healthy(), hist=[report(market_ok=False)])
    page.click('button.tab[data-k="gates"]')
    assert page.locator("#p-gates .gates-na").inner_text() == (
        "بوابات السوق غير متاحة اليوم: MarketError: no <report> block in the market output")
    page.click('button.tab[data-k="ai"]')
    assert page.locator("#p-ai .exec").count() == 1                     # التقرير الاقتصادي منشور رغم فشل البوابات


def test_the_data_status_page_lists_every_collector_and_a_failure_raises_an_alert(ui):
    p = nav.healthy()
    p["collection"] = collection()
    page = nav.open_page(ui, p)
    assert page.locator("#alerts").is_hidden()
    page.evaluate("location.hash = 'method'")
    page.wait_for_selector("#p-method:not([hidden])")
    rows = page.locator("#p-method .collectors tbody tr")
    assert rows.count() == 6 and "سليم" in rows.first.inner_text()
    bad = copy.deepcopy(p)
    bad["collection"] = collection(bad="inflation")
    page = nav.open_page(ui, bad)
    got = nav.alerts(page)
    assert got == [("warning", "تنبيه: تعذّر جامع التضخم (RuntimeError: down) في آخر جمع للأقسام.")]
