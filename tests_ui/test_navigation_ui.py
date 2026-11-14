"""PR 4 — التنقل والتنبيهات ووقت فيينا وMarket Gates في الصفحة الحقيقية (Chromium، ساعة مثبّتة).

تسعة تبويبات بالضبط بلا المنهجية وحالة البيانات (يُوصل إليها من التذييل ومن التنبيه)؛ والروابط القديمة تُحوَّل؛
والتنبيه يظهر فقط حين توجد مشكلة، بمستواه، وبقاعدة أهمية الإصدار لا عدد السلاسل؛ والفجوات المعروفة بجانب المؤشر فقط.
"""
import copy
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from macro_platform.build import build_payload, load_config  # noqa: E402
from macro_platform.demo import demo_series  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


eco = _load("eco_ui_case", ROOT / "tests_ui" / "test_economy_report_ui.py")
CFG = load_config(ROOT / "config.json")
WINTER = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)          # الاثنين 10:00 نيويورك، 16:00 فيينا
SUMMER = datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc)           # 09:00 نيويورك (EDT)، 15:00 فيينا (CEST)
TABS = ["overview", "ai", "news", "cat-growth", "cat-inflation", "cat-labor", "cat-fed_rates", "cat-financial", "gates",
        "learn"]                                                      # التعليم: العاشر (PR 5)
_BASE = {}


def healthy(now=WINTER):
    """حمولة سليمة: كل المؤشرات بحالة ok (السلاسل التجريبية حتى تاريخ اليوم)."""
    key = now.date()
    if key not in _BASE:
        _BASE[key] = build_payload(demo_series(start="2016-01-01", end=now.strftime("%Y-%m-%d")), {}, CFG, now=now)
    p = copy.deepcopy(_BASE[key])
    for i in p["indicators"]:
        i["state"], i["issues"] = "ok", []
    p["ai_status"] = {"ny_date": now.strftime("%Y-%m-%d"), "attempts": [], "schedule": CFG["ai"]["schedule"]}
    return p


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def news(now=WINTER, calendar=(), items_age=4, cal_age=40):
    return {"updated": iso(now), "source": "x", "delay_minutes": 10, "translator": None, "errors": [], "items": [],
            "keep_since_utc": iso(now - timedelta(days=3)), "calendar": list(calendar),
            "last_success_utc": {"items": iso(now - timedelta(minutes=items_age)),
                                 "calendar": iso(now - timedelta(minutes=cal_age))}}


def cpi_event(now=WINTER, impact=1):
    t = now.replace(hour=13, minute=30)
    return {"id": 1, "title": "CPI m/m", "timeUtc": iso(t), "impact": impact, "us": True, "actual": None,
            "forecast": "0.3%", "previous": "0.2%", "revision": None}


def open_page(ui, payload, nj="default", hist=None, now=WINTER, hash_=""):
    files = {"data/ai_history.json": hist or []}
    if nj is not None:
        files["news.json"] = news(now) if nj == "default" else nj
    payload = dict(payload, ai_available=bool(hist))
    page = ui.open(payload, files, now=now)
    if hash_:
        page.evaluate(f"location.hash = '{hash_}'")
    page.wait_for_selector("body[data-news-loaded]", state="attached")
    return page


# ---------------------------------------------------------------- navigation
def test_exactly_ten_tabs_in_order_and_methodology_only_by_link(ui):
    page = open_page(ui, healthy())
    assert page.locator("#tabs .tab").evaluate_all("bs => bs.map(b => b.dataset.k)") == TABS
    assert page.locator('#tabs .tab[data-k="method"]').count() == 0
    page.click("footer a.method-link")
    page.wait_for_selector("#p-method:not([hidden])")
    assert "المنهجية وحالة البيانات" in page.locator("#p-method h2").inner_text()


def test_old_links_and_the_saved_tab_go_to_the_new_places(ui):
    page = open_page(ui, healthy())
    for old, new in (("cat-housing", "cat-growth"), ("cat-rates", "cat-fed_rates"), ("cat-money", "cat-financial"),
                     ("cat-markets", "gates"), ("method", "method")):
        page.evaluate(f"location.hash = '{old}'")
        page.wait_for_selector(f"#p-{new}:not([hidden])")
    page.evaluate("localStorage.setItem('macro-tab', 'cat-housing'); location.hash = ''")
    page.reload(wait_until="networkidle")
    page.wait_for_selector("#kpis .kpi")
    assert page.locator("#p-cat-growth").is_visible()


def test_housing_indicators_live_in_growth_and_fed_rates_and_indices_have_no_tab(ui):
    page = open_page(ui, healthy())
    page.click('button.tab[data-k="cat-growth"]')
    assert page.locator("#p-cat-growth tbody tr", has_text="HOUST").count() == 1
    page.click('button.tab[data-k="cat-fed_rates"]')
    assert page.locator("#p-cat-fed_rates tbody tr", has_text="MORTGAGE30US").count() == 1
    assert page.locator("#p-cat-markets").count() == 0 and "Nasdaq" not in page.locator("#kpis").inner_text()


def test_new_york_and_vienna_clocks_in_winter_and_summer(ui):
    assert open_page(ui, healthy()).locator("#clocks").inner_text() == "نيويورك 10:00 · فيينا 16:00"
    assert open_page(ui, healthy(SUMMER), nj=news(SUMMER), now=SUMMER).locator("#clocks").inner_text() == "نيويورك 09:00 · فيينا 15:00"


def test_report_time_shows_vienna_next_to_new_york(ui):
    page = open_page(ui, healthy(), hist=[eco.report()])
    page.click('button.tab[data-k="ai"]')
    assert "كُتب 2026-11-16 09:16 بتوقيت نيويورك (15:16 فيينا) · قبل الافتتاح" in page.locator("#p-ai").inner_text()


def test_no_horizontal_scroll_on_a_phone(ui):
    page = open_page(ui, healthy(), hist=[eco.report()])
    page.set_viewport_size({"width": 390, "height": 800})
    for k in TABS:
        page.click(f'button.tab[data-k="{k}"]')
        page.wait_for_timeout(80)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), k


# ---------------------------------------------------------------- Market Gates
def test_market_gates_have_three_sub_tabs_and_the_translation_check_only_under_nq(ui):
    page = open_page(ui, healthy(), hist=[eco.report()])
    assert page.locator("#p-ai .gate").count() == 0 and page.locator('#p-ai a[href="#gates"]').count() == 1
    page.click('button.tab[data-k="gates"]')
    g = page.locator("#p-gates")
    assert g.locator(".subtab").all_inner_texts() == ["NQ", "S&P 500", "Dow"]
    assert g.locator('.gate-pane[data-g="nq"] .trans').is_visible() and g.locator(".trans").count() == 1
    assert "بوابات NQ من بيانات المنصة" in g.inner_text() and g.locator('.gate-pane[data-g="nq"] .cards .card').count() > 0
    g.locator('.subtab[data-g="spx"]').click()
    assert g.locator('.gate-pane[data-g="spx"] .impact').inner_text() == "متوازن (0)"
    assert not g.locator('.gate-pane[data-g="nq"]').is_visible()
    g.locator('.subtab[data-g="dow"]').click()
    assert g.locator('.gate-pane[data-g="dow"] .gate h4').inner_text().startswith("Dow Jones")


def test_gates_say_unavailable_without_a_new_style_report(ui):
    old = {"title": "قديم", "summary": ["s"], "scenarios": [], "one_liner": "o", "meta": {"ny_date": "2026-11-13"}}
    page = open_page(ui, healthy(), hist=[old])
    page.click('button.tab[data-k="gates"]')
    assert "لا ترجمة سوقية في آخر تقرير." in page.locator("#p-gates").inner_text()
    assert page.locator('#p-gates .gate-pane[data-g="nq"] .cards .card').count() > 0     # بوابات المنصة باقية


# ---------------------------------------------------------------- alerts: only when there is a problem
def alerts(page):
    return [(a.get_attribute("class").split()[-1], a.inner_text()) for a in page.locator("#alerts .alert").all()]


def test_no_alert_when_everything_is_healthy(ui):
    page = open_page(ui, healthy())
    assert page.locator("#alerts").is_hidden() and alerts(page) == []


def test_a_series_in_error_on_its_release_day_is_critical_and_marks_the_report(ui):
    p = healthy()
    next(i for i in p["indicators"] if i["id"] == "CPIAUCSL")["state"] = "error"
    page = open_page(ui, p, nj=news(calendar=[cpi_event()]), hist=[eco.report()])
    got = alerts(page)
    assert got[0][0] == "critical" and "CPI الرئيسي (تغير سنوي) (CPIAUCSL) يوم صدور CPI m/m" in got[0][1]
    page.click('button.tab[data-k="ai"]')
    assert page.locator("#alerts").is_visible() and "مبنيان على بيانات ناقصة" in page.locator("#alerts").inner_text()
    assert page.locator("#alerts a.alert-more").get_attribute("href") == "#method"


def test_the_same_error_without_a_release_today_is_only_a_warning(ui):
    p = healthy()
    next(i for i in p["indicators"] if i["id"] == "CPIAUCSL")["state"] = "error"
    medium = cpi_event(impact=2)
    yesterday = dict(cpi_event(), timeUtc=iso(WINTER.replace(hour=13, minute=30) - timedelta(days=1)))
    for cal in ([], [medium], [yesterday]):                               # لا إصدار، أو متوسط التأثير، أو أمس
        got = alerts(open_page(ui, p, nj=news(calendar=cal)))
        assert [g[0] for g in got] == ["warning"] and "تعذّر جلب 1 من المؤشرات" in got[0][1]


def test_report_failures_feeds_and_unreadable_news(ui):
    one = [{"attempt": "1", "started_at": "2026-11-16T14:02:00Z", "ok": False, "reason": "x"}]
    p = healthy()
    p["ai_status"]["attempts"] = one
    assert alerts(open_page(ui, p)) == [("warning", "تنبيه: فشلت المحاولة الأولى لتقرير اليوم، وإعادة المحاولة 12:00 بتوقيت نيويورك.")]
    p["ai_status"]["attempts"] = one + [{"attempt": "2", "started_at": "2026-11-16T17:01:00Z", "ok": False, "reason": "y"}]
    assert alerts(open_page(ui, p)) == [("critical", "حرج: التقرير الاقتصادي غير متاح اليوم بسبب فشل التوليد.")]
    p["ai_status"]["attempts"] = one + [{"attempt": "2", "started_at": "2026-11-16T17:01:00Z", "ok": True, "reason": None}]
    assert alerts(open_page(ui, p)) == []
    stale = alerts(open_page(ui, healthy(), nj=news(items_age=45, cal_age=200)))
    assert [g[0] for g in stale] == ["warning", "warning"] and "العناوين غير محدّثة" in stale[0][1]
    assert alerts(open_page(ui, healthy(), nj=None)) == [("critical", "حرج: تعذّرت قراءة الأخبار والتقويم (news.json).")]


def test_every_failing_series_is_listed_and_known_gaps_stay_next_to_the_indicator(ui):
    p = healthy()
    bad = [i for i in p["indicators"] if not i.get("internal_only")][:12]
    for i in bad:
        i["state"] = "error"
    cpi = next(i for i in p["indicators"] if i["id"] == "CPILFESL")
    cpi["unavailable"] = {"label": "أكتوبر 2026", "reason": "غير منشور في المصدر"}
    text = alerts(open_page(ui, p))[0][1]
    assert "تعذّر جلب 12 من المؤشرات" in text and all(f"({i['id']})" in text for i in bad)   # لا قائمة مقطوعة عند 8
    assert "أكتوبر 2026" not in text
