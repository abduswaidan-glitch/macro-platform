"""صندوقا الأخبار والتقويم في الصفحة: المصدر ونوعه (رسمي/إعلامي) والرابط، وآخر جلب ناجح لا وقت البناء،
والتقويم يعرض ما مضى هذا الأسبوع وما هو قادم، ولا يُفرغ في نهاية الأسبوع (شكوى 13 نوفمبر 2026)."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cpi_ui_case", ROOT / "tests_ui" / "test_cpi_ui.py")
cpi_ui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cpi_ui)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(**kw):
    return iso(datetime.now(timezone.utc) - timedelta(**kw))


def ahead(**kw):
    return iso(datetime.now(timezone.utc) + timedelta(**kw))


def item(i, title, src, kind, mins, level=1, link=None, us=True):
    return {"id": i, "utc": ago(minutes=mins), "title_en": title, "labels": [src, kind], "breaking": False,
            "level": level, "link": link, "us": us}


def event(i, title, t, impact=1, actual=None):
    return {"id": i, "title": title, "country": "US", "timeUtc": t, "impact": impact, "type": None, "actual": actual,
            "forecast": "0.3%", "previous": "0.2%", "revision": None, "unit": None, "breaking": False, "us": True}


def news_json(items_ok=None, cal_ok=None, calendar=None, errors=()):
    items_ok = ago(minutes=4) if items_ok is None else items_ok
    cal_ok = ago(minutes=40) if cal_ok is None else cal_ok
    return {"updated": ago(minutes=2), "source": "Fed · BEA · CNBC · MarketWatch · ForexFactory", "delay_minutes": 10,
            "translator": None, "errors": list(errors), "keep_since_utc": ago(days=4),
            "last_success_utc": {"items": items_ok or None, "calendar": cal_ok or None},
            "items": [item(1, "Federal Reserve issues FOMC statement", "Fed", "official", 30, level=3,
                           link="https://www.federalreserve.gov/newsevents/pressreleases/monetary20261029a.htm"),
                      item(2, "Nvidia shares rise ahead of earnings", "CNBC", "media", 90,
                           link="https://www.cnbc.com/2026/11/13/nvidia-shares.html"),
                      item(3, "Oil prices climb as OPEC weighs cuts", "MarketWatch", "media", 120, us=False,
                           link="javascript:alert(1)")],
            "calendar": calendar if calendar is not None else [
                event(10, "Core CPI m/m", ago(hours=20)), event(11, "Unemployment Claims", ahead(hours=20), impact=2)]}


def overview(ui, nj, wait="#calBox .news-meta"):
    page = ui.open(cpi_ui._payload(), {"news.json": nj} if nj is not None else None)
    page.click('button.tab[data-k="overview"]')
    page.wait_for_selector("#newsBox .news, #newsBox .empty")
    page.wait_for_selector(wait)
    return page


def test_headlines_show_source_official_badge_and_safe_links(ui):
    page = overview(ui, news_json())
    box = page.locator("#newsBox")
    meta = box.locator(".news-meta").inner_text()
    assert "Fed · BEA · CNBC · MarketWatch · ForexFactory" in meta and "آخر جلب ناجح: منذ 4 د" in meta
    assert "FinancialJuice" not in page.content()
    fed = box.locator("li", has_text="Federal Reserve issues FOMC statement")
    assert fed.locator(".off").inner_text() == "رسمي" and fed.locator(".lb").inner_text() == "Fed"
    a = fed.locator("a.t")
    assert a.get_attribute("href").startswith("https://www.federalreserve.gov/") and a.get_attribute("target") == "_blank"
    assert "noopener" in a.get_attribute("rel")
    assert box.locator("li", has_text="Nvidia").locator(".off").count() == 0
    assert box.locator(".feed-gap").count() == 0 and "حتى افتتاح الأسبوع التالي" in box.inner_text()
    page.click('#newsBox .chip-btn[data-f="all"]')
    oil = box.locator("li", has_text="Oil prices")
    assert oil.locator("a.t").get_attribute("href") == "#"              # javascript: لا يصبح رابطاً
    page.click('#newsBox .chip-btn[data-f="hot"]')
    assert box.locator("li").count() == 1 and "FOMC" in box.locator("li").inner_text()


def test_stale_or_unknown_headline_fetch_is_flagged(ui):
    page = overview(ui, news_json(items_ok=ago(hours=20)))
    assert "آخر جلب ناجح: منذ 20 س" in page.locator("#newsBox .news-meta").inner_text()
    assert "ليست جديدة" in page.locator("#newsBox .feed-gap").inner_text()
    page = overview(ui, {**news_json(), "last_success_utc": None})
    assert "آخر جلب ناجح: غير معروف" in page.locator("#newsBox .news-meta").inner_text()
    assert page.locator(".feed-gap").count() == 2


def test_failed_sources_are_named(ui):
    page = overview(ui, news_json(errors=["CNBC: HTTP 403"]))
    assert "CNBC: HTTP 403" in page.locator("#newsBox .warn-box").first.inner_text()


def test_calendar_shows_past_and_upcoming_and_says_actuals_are_not_in_the_source(ui):
    page = overview(ui, news_json())
    cal = page.locator("#calBox")
    meta = cal.locator(".news-meta").inner_text()
    assert "ForexFactory" in meta and "آخر جلب ناجح: منذ 40 د" in meta
    assert cal.locator(".feed-gap").count() == 0                      # التقويم يُجلب كل ساعة: 40 دقيقة ليست تأخراً
    up = cal.locator("table", has_text="القادمة")
    assert "Unemployment Claims" in up.inner_text() and "متوسط" in up.inner_text()
    past = cal.locator("table", has_text="مضى موعدها هذا الأسبوع")
    assert "Core CPI m/m" in past.inner_text() and "لا يحمله المصدر" in past.inner_text()
    assert "غياب القيمة هنا لا يعني أن الرقم صدر أو لم يصدر" in cal.inner_text()


def test_week_over_calendar_still_shows_the_week(ui):
    """السبت: كل أحداث الأسبوع مضت والأسبوع القادم لم يُنشر بعد. التقويم يعرض الأسبوع ويقول لماذا لا قادم."""
    page = overview(ui, news_json(calendar=[event(10, "Core CPI m/m", ago(hours=70)), event(12, "Retail Sales m/m", ago(hours=30))]))
    cal = page.locator("#calBox")
    assert "لا أحداث قادمة معروفة بعد" in cal.locator(".cal-next").inner_text()
    rows = cal.locator("table", has_text="مضى موعدها").locator("tbody tr")
    assert rows.count() == 2 and "Retail Sales" in rows.first.inner_text()       # الأحدث أولاً


def test_calendar_older_than_two_hourly_fetches_is_flagged(ui):
    page = overview(ui, news_json(cal_ok=ago(hours=3)))
    assert "التقويم غير محدّث" in page.locator("#calBox .feed-gap").inner_text()


def test_empty_calendar_says_why(ui):
    page = overview(ui, news_json(cal_ok="", calendar=[]), wait="#calBox .feed-gap")
    gap = page.locator("#calBox .feed-gap").inner_text()
    assert "التقويم فارغ" in gap and "لا لأنه لا أحداث" in gap
    page = overview(ui, news_json(calendar=[]), wait="#calBox .empty")
    assert page.locator("#calBox .feed-gap").count() == 0
    assert "لا أحداث أمريكية مرتفعة أو متوسطة التأثير في المصدر لهذا الأسبوع" in page.locator("#calBox").inner_text()


def test_report_probabilities_are_labelled_uncalibrated(ui):
    rep = {"title": "t", "summary": ["s"], "bias": {"direction": "neutral", "confidence": 55, "reason": "r"},
           "scenarios": [{"label": x, "name": x, "prob": p, "trigger": "", "reason": ""}
                         for x, p in (("A", 50), ("B", 30), ("C", 20))], "one_liner": "o",
           "meta": {"ny_date": "2026-11-13", "model": "m"}}
    payload = cpi_ui._payload()
    payload["ai_available"] = True
    page = ui.open(payload, {"data/ai_history.json": [rep], "news.json": news_json()})
    page.click('button.tab[data-k="ai"]')
    text = page.locator("#p-ai").inner_text()
    assert "الثقة 55% · تقدير ذاتي من النموذج، غير معاير" in text
    assert "الاحتمالات أوزان ذاتية من النموذج، غير معايرة على نتائج سابقة." in text
