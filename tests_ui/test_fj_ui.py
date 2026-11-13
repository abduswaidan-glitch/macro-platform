"""ما تعرضه الصفحة عن حالة FinancialJuice: آخر جلب ناجح لا وقت بناء الملف، وتحذير فجوة التغطية، ووسم الاحتمالات."""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cpi_ui_case", ROOT / "tests_ui" / "test_cpi_ui.py")
cpi_ui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cpi_ui)


def ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


def news_json(last_success):
    return {"updated": ago(minutes=2), "source": "FinancialJuice", "delay_minutes": 10, "translator": None,
            "errors": [], "last_success_utc": {"items": last_success, "calendar": last_success},
            "items": [{"id": 1, "utc": ago(hours=20, minutes=5), "title_en": "Fed's Waller: further cuts appropriate",
                       "labels": ["FED"], "breaking": False, "level": 0, "link": None, "us": True}],
            "calendar": [{"id": 10, "title": "CPI m/m", "country": "US", "timeUtc": ago(hours=22), "impact": 1,
                          "type": 2, "actual": "0.3%", "forecast": "0.3%", "previous": "0.4%", "revision": None,
                          "unit": "%", "breaking": False, "us": True}]}


def overview(ui, files):
    page = ui.open(cpi_ui._payload(), files)
    page.click('button.tab[data-k="overview"]')
    page.wait_for_selector("#newsBox .news-meta")
    page.wait_for_selector("#calBox .news-meta")
    return page


def test_news_meta_shows_last_successful_fetch_not_build_time(ui):
    page = overview(ui, {"news.json": news_json(ago(hours=20))})
    meta = page.locator("#newsBox .news-meta").inner_text()
    assert "آخر جلب ناجح: منذ 20 س" in meta, meta                       # updated قبل دقيقتين، والجلب الناجح قبل 20 ساعة
    assert page.locator("#newsBox .fj-gap").count() == 1
    gap = page.locator("#calBox .fj-gap").inner_text()
    assert "غياب القيمة لا يعني أن الرقم لم يصدر" in gap
    assert "آخر جلب ناجح: منذ 20 س" in page.locator("#calBox .news-meta").inner_text()


def test_unknown_last_success_is_shown_as_unknown(ui):
    page = overview(ui, {"news.json": {**news_json(None), "last_success_utc": None}})
    assert "آخر جلب ناجح: غير معروف" in page.locator("#newsBox .news-meta").inner_text()
    assert page.locator(".fj-gap").count() == 2


def test_current_feed_has_no_gap_warning(ui):
    page = overview(ui, {"news.json": news_json(ago(minutes=3))})
    assert "آخر جلب ناجح: منذ 3 د" in page.locator("#newsBox .news-meta").inner_text()
    assert page.locator(".fj-gap").count() == 0


def test_report_probabilities_are_labelled_uncalibrated(ui):
    rep = {"title": "t", "summary": ["s"], "bias": {"direction": "neutral", "confidence": 55, "reason": "r"},
           "scenarios": [{"label": x, "name": x, "prob": p, "trigger": "", "reason": ""}
                         for x, p in (("A", 50), ("B", 30), ("C", 20))], "one_liner": "o",
           "meta": {"ny_date": "2026-11-13", "model": "m"}}
    payload = cpi_ui._payload()
    payload["ai_available"] = True
    page = ui.open(payload, {"data/ai_history.json": [rep], "news.json": news_json(ago(minutes=3))})
    page.click('button.tab[data-k="ai"]')
    text = page.locator("#p-ai").inner_text()
    assert "الثقة 55% · تقدير ذاتي من النموذج، غير معاير" in text
    assert "الاحتمالات أوزان ذاتية من النموذج، غير معايرة على نتائج سابقة." in text


def overview_empty_calendar(ui, files):
    page = ui.open(cpi_ui._payload(), files)
    page.click('button.tab[data-k="overview"]')
    page.wait_for_selector("#calBox .fj-gap, #calBox .empty")
    return page


def test_empty_calendar_during_an_outage_says_why_it_is_empty(ui):
    # 13 نوفمبر: بعد محو التقويم كان الصندوق يقول «يمتلئ مع الدورات القادمة» والمصدر متوقف
    page = overview_empty_calendar(ui, {"news.json": {**news_json(None), "last_success_utc": {"items": None, "calendar": None},
                                       "calendar": []}})
    gap = page.locator("#calBox .fj-gap").inner_text()
    assert "التقويم فارغ" in gap and "لا لأنه لا أحداث" in gap


def test_empty_calendar_with_a_current_feed_keeps_the_plain_message(ui):
    page = overview_empty_calendar(ui, {"news.json": {**news_json(ago(minutes=3)), "calendar": []}})
    assert page.locator("#calBox .fj-gap").count() == 0
    assert "لا أحداث بعد" in page.locator("#calBox").inner_text()
