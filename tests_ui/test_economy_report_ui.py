"""التقرير الاقتصادي (الصيغة 2) في الصفحة: متصل في تبويب واحد، ثم بوابات السوق الثلاث بمقياس -2..+2 بكلماته،
وقياس الترجمة تحت NQ فقط، ورسائل المحاولات حين تفشل، ووسم التقرير المتأخر. كل نص من النموذج يمر عبر esc()."""
import copy
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from _report import body, gate  # noqa: E402

from macro_platform import ledger  # noqa: E402
from macro_platform.build import load_config  # noqa: E402

CFG = load_config(ROOT / "config.json")["ai"]
_spec = importlib.util.spec_from_file_location("cpi_ui_case", ROOT / "tests_ui" / "test_cpi_ui.py")
cpi_ui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cpi_ui)


def report(b=None, delayed=False, attempt="1", completed="2026-11-16T14:16:00Z"):
    r = copy.deepcopy(b or body())
    eco = r["economy"]
    r.update(title=eco["executive"]["headline"], summary=[eco["executive"]["read"]], scenarios=eco["scenarios"],
             one_liner=eco["bottom_line"], warnings=r.get("warnings", []), sources=[])
    r["meta"] = {"schema": 2, "ny_date": "2026-11-16", "model": "claude-opus-5-5", "completed_at": completed,
                 "written_at": "2026-11-16T14:00:00Z", "prob_sum": sum(s["prob"] for s in eco["scenarios"]),
                 "attempt": attempt, "delayed": delayed, "pre_open": not delayed}
    return r


def page(ui, hist, now="2026-11-16T15:00:00", attempts=(), led=True):
    payload = cpi_ui._payload()
    payload["ai_available"] = True
    payload["ai_status"] = {"ny_date": "2026-11-16", "attempts": list(attempts),
                            "schedule": {"attempt_1": "09:00", "attempt_2": "12:00", "last_start": "13:00"}}
    if led and hist:
        L = {"version": 2, "entries": [ledger.entry(hist[0], CFG)]}
        payload["ledger"] = ledger.view(L)
    p = ui.open(payload, {"data/ai_history.json": hist}, now=datetime.fromisoformat(now).replace(tzinfo=timezone.utc))
    p.click('button.tab[data-k="ai"]')
    return p.locator("#p-ai")


def test_the_economy_report_reads_as_one_flow_before_the_market_gates(ui):
    ai = page(ui, [report()])
    t = ai.inner_text()
    order = ["الخلاصة التنفيذية", "النظام الاقتصادي: تباطؤ معتدل", "المحرك المهيمن", "التعارضات الرئيسية",
             "ما الذي تغيّر منذ التقرير السابق", "النمو", "التضخم", "سوق العمل", "الفيدرالي والفائدة", "الأوضاع المالية",
             "الأخبار وأثرها", "عدم اليقين في البيانات", "السيناريوهات A وB وC", "القناعة", "الخلاصة", "بوابات السوق"]
    at = t.index("الخلاصة التنفيذية")                       # بعد المقدمة: كل عنوان بعد سابقه
    for x in order:
        at = t.index(x, at)                                  # ValueError إن جاء قبل سابقه أو غاب
    eco = ai.locator(".eco").inner_text()
    assert "انحياز NQ" not in eco and "قياس الترجمة السوقية" not in eco and "Nasdaq" not in eco
    assert "لا تعارض جوهري اليوم." in eco and "DFII10" in ai.locator(".drv").inner_text()
    assert ai.locator(".sc", has_text="سيناريو A").locator(".chk").inner_text().startswith("شرط القياس: DGS10")


def test_gates_use_the_minus_two_to_plus_two_words_and_show_text_as_written(ui):
    b = body(gates={"nq": gate(-1), "spx": gate(0), "dow": gate(3, matters_now="الأثر مماثل إلى حد كبير لـS&P 500")})
    ai = page(ui, [report(b)])
    assert ai.locator('[data-g="nq"] .impact').inner_text() == "سلبي (-1)"
    assert ai.locator('[data-g="nq"] .impact bdi').get_attribute("dir") == "ltr"     # لا يُعرض (1-) في سياق RTL
    assert ai.locator('[data-g="spx"] .impact').inner_text() == "متوازن (0)"
    assert ai.locator('[data-g="dow"] .impact').inner_text() == "غير صالح"          # 3 خارج المجال: لا يُعرض رقماً
    assert "الأثر مماثل إلى حد كبير لـS&P 500" in ai.locator('[data-g="dow"]').inner_text()
    assert "Entry" in ai.inner_text() and "توصية شراء أو بيع" in ai.inner_text()     # النفي المكتوب فقط


def test_translation_check_appears_under_nq_only_and_says_it_is_not_the_economic_analysis(ui):
    ai = page(ui, [report()])
    assert ai.locator(".trans").count() == 1 and ai.locator('[data-g="nq"] .trans').count() == 1
    tr = ai.locator(".trans").inner_text()
    assert "قياس الترجمة السوقية: NQ مقابل S&P 500 خلال 10 أيام" in tr
    assert "قياس للترجمة السوقية، وليس قياساً لصحة التحليل الاقتصادي." in tr
    assert "احتمال تفوق Nasdaq-100 على S&P 500: 45%" in tr and "لم يُحسم بعد" in tr
    assert "Translation-check" in tr and "Translation-check" not in ai.locator(".led-sum").inner_text()


def test_missing_market_translation_and_secondary_fields_do_not_break_the_page(ui):
    b = body()
    del b["market_translation"]
    del b["economy"]["regime"]
    del b["economy"]["conviction"]
    ai = page(ui, [report(b)])
    assert "بوابات السوق غير متاحة اليوم." in ai.inner_text()
    assert ai.locator(".conv").inner_text().endswith("غير محدد") and ai.locator(".reg .na").count() == 5


def test_a_failed_09_00_attempt_says_when_it_retries(ui):
    att = [{"attempt": "1", "started_at": "2026-11-16T14:02:00Z", "ok": False, "failure_class": "retryable",
            "reason": "ReportError: no <report> block in the model output"}]
    ai = page(ui, [], attempts=att, now="2026-11-16T15:30:00")
    msg = ai.locator(".ai-fail").inner_text()
    assert msg == "فشلت محاولة 09:02 (ReportError: no <report> block in the model output)، وإعادة المحاولة 12:00 بتوقيت نيويورك."
    assert ai.locator(".ai-late").count() == 0                     # رسالة واحدة، لا «لا تقرير» فوقها


def test_two_failures_or_no_retry_before_13_00_mean_no_report_today(ui):
    att = [{"attempt": "1", "started_at": "2026-11-16T14:02:00Z", "ok": False, "reason": "APIConnectionError: reset"},
           {"attempt": "2", "started_at": "2026-11-16T17:01:00Z", "ok": False, "reason": "ReportError: max_tokens"}]
    msg = page(ui, [], attempts=att, now="2026-11-16T17:40:00").locator(".ai-fail").inner_text()
    assert msg.startswith("التقرير الاقتصادي غير متاح اليوم بسبب فشل التوليد.")
    assert "المحاولة 1 (09:02 بتوقيت نيويورك): APIConnectionError: reset" in msg and "المحاولة 2 (12:01" in msg
    one = page(ui, [], attempts=att[:1], now="2026-11-16T18:30:00").locator(".ai-fail").inner_text()   # 13:30
    assert one.startswith("التقرير الاقتصادي غير متاح اليوم")


def test_success_shows_no_attempt_banner_and_a_late_report_is_labelled(ui):
    att = [{"attempt": "1", "started_at": "2026-11-16T14:02:00Z", "ok": False, "reason": "x"},
           {"attempt": "2", "started_at": "2026-11-16T17:01:00Z", "ok": True, "reason": None}]
    ai = page(ui, [report(delayed=True, attempt="2", completed="2026-11-16T17:16:00Z")], attempts=att,
              now="2026-11-16T18:00:00")
    assert ai.locator(".ai-fail").count() == 0
    assert ai.locator(".late-tag").inner_text() == "تقرير متأخر: كُتب الساعة 12:16 بعد افتتاح السوق (المحاولة الثانية)"
    assert page(ui, [report()]).locator(".late-tag").count() == 0


def test_model_text_is_escaped_and_bad_links_are_neutralised(ui):
    bad = "<img src=x onerror=alert(1)>"
    b = body()
    b["economy"]["executive"]["read"] = bad
    b["economy"]["dominant_driver"]["evidence"] = [bad]
    b["events"][0]["url"] = "javascript:alert(1)"
    b["market_translation"]["gates"]["nq"]["matters_now"] = bad
    r = report(b)
    r["warnings"] = [bad]
    ai = page(ui, [r])
    assert ai.locator("img").count() == 0 and bad in ai.locator(".exec").inner_text()
    assert all(h in ("#", None) for h in ai.locator(".events a").evaluate_all("as => as.map(a => a.getAttribute('href'))"))
    assert "ملاحظات التحقق الآلي (1)" in ai.inner_text()
