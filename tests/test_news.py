"""وحدة الأخبار والتقويم: قراءة RSS، ومرشّحات الأهمية، والاحتفاظ حتى افتتاح الأسبوع التالي، وتقويم ForexFactory."""
from datetime import datetime, timedelta, timezone

import pytest

from macro_platform import news
from _newsfake import (BEA, CNBC_TOP, FED_PRESS, FF_NEXT, FF_THIS, MW, Net, default_routes, ff, ff_row, iso, rss)

NOW = datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc)          # الجمعة 13:00 بتوقيت نيويورك
SINCE = news.keep_since(NOW)
SRC = {s["url"]: s for s in news.DEFAULT_SOURCES}


def fetch(url, routes=None):
    return news.fetch_source(SRC[url], NOW, SINCE, get=Net(routes or default_routes(NOW)))


# ---------------------------------------------------------------- importance filters, per source
def test_fed_press_keeps_policy_and_drops_bank_enforcement():
    items, st = fetch(FED_PRESS)
    assert [i["title_en"] for i in items] == ["Federal Reserve issues FOMC statement"]
    assert st == {"seen": 2, "kept": 1}
    i = items[0]
    assert i["labels"] == ["Fed", "official"] and i["us"] is True and i["level"] == 3 and i["breaking"] is False
    assert i["link"].startswith("https://www.federalreserve.gov/") and isinstance(i["id"], int) and i["id"] < 2 ** 53


def test_bea_keeps_national_releases_only():
    items, _ = fetch(BEA)
    assert [i["title_en"] for i in items] == ["Personal Income and Outlays, September 2026"]
    assert items[0]["level"] == 2                                     # رسمي، وليس من الصف الأول


@pytest.mark.parametrize("title, kept, level, us", [
    ("Stocks fall as Treasury yields jump after hot inflation data", True, 3, True),
    ("Nvidia shares rise ahead of earnings", True, 1, True),         # سهم كبير في NQ: أمريكي
    ("European shares edge higher", False, None, None),                # لا كلمة سوق مؤثرة في NQ
    ("Cramer's lightning round: I like this stock", False, None, None),
    ("5 tips to pay off credit cards faster", False, None, None),
    ("China retaliates with new tariffs on U.S. farm goods", True, 3, True),
    ("Oil prices climb as OPEC weighs cuts", True, 1, False),
    # أنماط من فحص المصادر الحقيقية من خوادم GitHub (13 نوفمبر)، بصياغة الاختبار
    ("Wholesale prices rose 0.4% in October, the most in a year", True, 3, True),     # PPI بلا «U.S.»
    ("China producer inflation hits a four-year high", True, 3, False),
    ("Euro zone inflation climbs to 3%", True, 3, False),
    ("Three reasons the October jobs report beat forecasts", True, 3, True),
    ("Fed's Warsh signals patience on further moves", True, 3, True),
    ("A startup launches AI agents that shop for you", False, None, None),             # «AI» وحدها ليست خبر سوق
    ("Two chip stocks to buy on the dip, says analyst", False, None, None),
    ("This software stock is down 20% this month. A broker says it's a buy", False, None, None),
    ("Here are Friday's biggest analyst calls: Nvidia, Apple and more", False, None, None),
    ("How cheap does Nvidia need to get before it's a screaming bargain?", False, None, None),
    ("Watch these levels to know if the Nasdaq-100 breaks out", False, None, None),
    ("Nvidia stock extends its longest losing streak in years", True, 1, True),
])
def test_media_filter_keeps_market_moving_headlines(title, kept, level, us):
    routes = {CNBC_TOP: rss((title, "https://www.cnbc.com/x.html", NOW - timedelta(minutes=30)))}
    items, _ = fetch(CNBC_TOP, routes)
    assert bool(items) is kept
    if kept:
        assert items[0]["level"] == level and items[0]["us"] is us and items[0]["labels"] == ["CNBC", "media"]


def test_same_story_in_two_feeds_has_one_id():
    a, _ = fetch(CNBC_TOP)
    b, _ = news.fetch_source(SRC[news.DEFAULT_SOURCES[4]["url"]], NOW, SINCE, get=Net(default_routes(NOW)))
    assert a[0]["id"] == b[0]["id"]
    store = {}
    news.merge(store, a + b)
    assert len(store) == len(a) + len(b) - 1                          # القصة المشتركة مرة واحدة


def test_items_outside_the_window_and_unsafe_links_are_dropped():
    routes = {CNBC_TOP: rss(("Fed's Powell speaks on rates", "javascript:alert(1)", NOW - timedelta(minutes=5)),
                            ("Treasury yields fall", "https://www.cnbc.com/a.html", SINCE - timedelta(minutes=1)),
                            ("Treasury yields rise", "https://www.cnbc.com/b.html", NOW + timedelta(hours=1)))}
    items, _ = fetch(CNBC_TOP, routes)
    assert [i["title_en"] for i in items] == ["Fed's Powell speaks on rates"] and items[0]["link"] is None


def test_eastern_time_pubdate_is_read_as_utc():
    est = timezone(timedelta(hours=-5))
    routes = {BEA: rss(("GDP (Advance Estimate), 3rd Quarter 2026", "https://www.bea.gov/gdp", NOW - timedelta(hours=2)),
                       rfc_tz=est)}
    assert fetch(BEA, routes)[0][0]["utc"] == iso(NOW - timedelta(hours=2))


@pytest.mark.parametrize("body, msg", [
    (b"<html><body>Treasury</body></html>", "feed has no items"),
    (b"not xml at all <", "not RSS/XML"),
    (b'<?xml version="1.0"?><rss><channel></channel></rss>', "feed has no items"),
])
def test_feed_that_is_not_usable_rss_is_an_error(body, msg):
    with pytest.raises(news.NewsError, match=msg):
        fetch(CNBC_TOP, {CNBC_TOP: body})


def test_stale_media_feed_is_an_error_but_a_quiet_official_feed_is_not():
    old = NOW - timedelta(days=3)
    with pytest.raises(news.NewsError, match="stale"):
        fetch(MW, {MW: rss(("Stocks rally", "https://www.marketwatch.com/s", old))})
    items, st = fetch(BEA, {BEA: rss(("GDP (Advance Estimate)", "https://www.bea.gov/g", NOW - timedelta(days=9)))})
    assert items == [] and st["seen"] == 1                           # BEA ينشر كل بضعة أيام: ليس خللاً


def test_one_failing_source_does_not_stop_the_others():
    routes = default_routes(NOW)
    routes[CNBC_TOP] = news.NewsError("HTTP 403")
    items, status, errors = news.fetch_headlines(news.DEFAULT_SOURCES, NOW, SINCE, get=Net(routes))
    assert errors == ["CNBC: HTTP 403"]
    assert [s["ok"] for s in status] == [True, True, True, False, True, True]
    assert {i["labels"][0] for i in items} == {"Fed", "BEA", "CNBC"}


def test_http_get_sends_an_honest_user_agent(monkeypatch):
    seen = {}

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b"ok"

    def urlopen(req, timeout=None):
        seen.update(dict(req.header_items()))
        return R()

    monkeypatch.setattr(news.urllib.request, "urlopen", urlopen)
    assert news.http_get("https://www.federalreserve.gov/feeds/press_all.xml") == b"ok"
    ua = seen["User-agent"]
    assert ua.startswith("macro-platform/1.0") and "Mozilla" not in ua and "Chrome" not in ua


# ---------------------------------------------------------------- week retention
@pytest.mark.parametrize("now, start, since", [
    ("2026-11-14T17:00:00", "2026-11-08T23:00:00", "2026-11-08T23:00:00"),   # السبت: الأسبوع كله باقٍ
    ("2026-11-15T22:59:00", "2026-11-08T23:00:00", "2026-11-08T23:00:00"),   # الأحد 17:59 نيويورك
    ("2026-11-15T23:00:00", "2026-11-15T23:00:00", "2026-11-12T23:00:00"),   # الأحد 18:00: أسبوع جديد، و72 ساعة
    ("2026-11-16T14:00:00", "2026-11-15T23:00:00", "2026-11-13T14:00:00"),   # الاثنين 09:00: منذ الجمعة 09:00
    ("2026-03-09T14:00:00", "2026-03-08T22:00:00", "2026-03-06T14:00:00"),   # بعد بدء التوقيت الصيفي
    ("2026-03-07T17:00:00", "2026-03-01T23:00:00", "2026-03-01T23:00:00"),   # قبله
])
def test_items_stay_until_the_next_trading_week_opens(now, start, since):
    n = datetime.fromisoformat(now).replace(tzinfo=timezone.utc)
    assert iso(news.week_start(n)) == start + "Z" and iso(news.keep_since(n)) == since + "Z"


def test_prune_keeps_the_week_and_caps_the_list():
    sat = datetime(2026, 11, 14, 17, 0, tzinfo=timezone.utc)
    store = {str(k): {"id": k, "utc": iso(sat - timedelta(hours=k)), "title_en": "x"} for k in range(0, 200, 10)}
    kept = news.prune(store, sat)
    assert max(int(k) for k in kept) == 130                             # الأحد 18:00 الماضي قبل 138 ساعة
    big = {str(k): {"id": k, "utc": iso(sat - timedelta(minutes=k)), "title_en": "x"} for k in range(500)}
    assert len(news.prune(big, sat)) == news.MAX_ITEMS


def test_merge_keeps_translation_unless_title_changes():
    store = {"1": {"id": 1, "utc": iso(NOW), "title_en": "Fed holds", "title_ar": "الفيدرالي يثبت", "labels": ["Fed", "official"],
                   "breaking": False, "level": 2, "link": None, "us": True}}
    same = {**store["1"]}
    same.pop("title_ar")
    assert news.merge(store, [same]) == 0 and store["1"]["title_ar"] == "الفيدرالي يثبت"
    assert news.merge(store, [{**same, "title_en": "Fed cuts"}]) == 1 and "title_ar" not in store["1"]


def test_translate_only_what_needs_it():
    store = {"1": {"id": 1, "title_en": "Fed cuts rates"}, "2": {"id": 2, "title_en": "3.2%"},
             "3": {"id": 3, "title_en": "Old", "title_ar": "قديم"}}
    sent = []

    def tr(batch):
        sent.extend(b["id"] for b in batch)
        return {b["id"]: "مترجم" for b in batch}

    done, errors = news.translate_missing(store, tr)
    assert sent == [1] and done == 1 and not errors
    assert store["2"]["title_ar"] == "3.2%" and store["3"]["title_ar"] == "قديم"


def test_translation_failure_keeps_english():
    store = {"1": {"id": 1, "title_en": "Fed cuts rates"}}

    def boom(batch):
        raise RuntimeError("overloaded")

    done, errors = news.translate_missing(store, boom)
    assert done == 0 and "title_ar" not in store["1"] and "RuntimeError" in errors[0]


# ---------------------------------------------------------------- calendar (ForexFactory export)
def test_ff_rows_map_to_the_contract_fields():
    t = datetime(2026, 11, 12, 13, 30, tzinfo=timezone.utc)
    e = news.ff_event(ff_row("Core CPI m/m", t))
    assert e == {"id": e["id"], "title": "Core CPI m/m", "country": "US", "timeUtc": "2026-11-12T13:30:00Z", "impact": 1,
                 "type": None, "actual": None, "forecast": "0.3%", "previous": "0.2%", "revision": None, "unit": None,
                 "breaking": False, "us": True}
    assert news.ff_event(ff_row("Core CPI m/m", t))["id"] == e["id"] and isinstance(e["id"], int)
    assert news.ff_event(ff_row("JOLTS", t, impact="Medium"))["impact"] == 2
    assert news.ff_event(ff_row("FOMC Member Speaks", t, forecast="", previous=""))["forecast"] is None
    for bad in (ff_row("x", t, impact="Low"), ff_row("x", t, country="EUR"), ff_row("x", t, impact="Holiday"),
                {**ff_row("x", t), "date": "soon"}, {**ff_row("", t)}, None, "row"):
        assert news.ff_event(bad) is None, bad


def test_calendar_next_week_is_optional_this_week_is_required():
    events, window, status = news.fetch_calendar(news.DEFAULT_CALENDAR, get=Net(default_routes(NOW)))
    assert sorted(e["title"] for e in events) == ["Core CPI m/m", "FOMC Member Speaks", "Unemployment Claims"]
    assert [s["ok"] for s in status] == [True, False] and status[1]["error"] == "HTTP 404"
    assert window == (NOW - timedelta(hours=40), NOW + timedelta(hours=44))   # يشمل الدول الأخرى والأحداث المنخفضة
    routes = default_routes(NOW)
    routes[FF_THIS] = news.NewsError("HTTP 503")
    with pytest.raises(news.NewsError, match="this week: HTTP 503"):
        news.fetch_calendar(news.DEFAULT_CALENDAR, get=Net(routes))
    routes[FF_THIS] = b"[]"
    with pytest.raises(news.NewsError, match="no rows"):
        news.fetch_calendar(news.DEFAULT_CALENDAR, get=Net(routes))


def test_next_week_is_added_when_the_source_publishes_it():
    routes = default_routes(NOW)
    routes[FF_NEXT] = ff(ff_row("Non-Farm Employment Change", NOW + timedelta(days=7)))
    events, window, status = news.fetch_calendar(news.DEFAULT_CALENDAR, get=Net(routes))
    assert "Non-Farm Employment Change" in {e["title"] for e in events} and window[1] == NOW + timedelta(days=7)


def test_merge_calendar_drops_moved_events_inside_the_source_window_only():
    t = NOW
    store = {}
    first = [news.ff_event(ff_row("Retail Sales m/m", t + timedelta(hours=3)))]
    news.merge_calendar(store, first, (t - timedelta(days=1), t + timedelta(days=2)))
    store[str(first[0]["id"])]["title_ar"] = "مبيعات التجزئة"
    old_week = news.ff_event(ff_row("PPI m/m", t - timedelta(days=4)))
    store[str(old_week["id"])] = old_week                                           # من الأسبوع الماضي، خارج المدى
    moved = [news.ff_event(ff_row("Retail Sales m/m", t + timedelta(hours=27)))]   # نُقل موعده يوماً
    news.merge_calendar(store, moved, (t - timedelta(days=1), t + timedelta(days=2)))
    assert {e["title"] for e in store.values()} == {"Retail Sales m/m", "PPI m/m"}
    assert [e["timeUtc"] for e in store.values() if e["title"] == "Retail Sales m/m"] == [iso(t + timedelta(hours=27))]
    news.merge_calendar(store, moved, (t - timedelta(days=1), t + timedelta(days=2)))
    store[str(moved[0]["id"])]["title_ar"] = "مبيعات"
    assert news.merge_calendar(store, moved, None) == 0 and store[str(moved[0]["id"])]["title_ar"] == "مبيعات"


def test_prune_calendar_keeps_the_week_until_sunday_evening():
    sat = datetime(2026, 11, 14, 17, 0, tzinfo=timezone.utc)
    mon = news.ff_event(ff_row("ISM", datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)))
    last = news.ff_event(ff_row("PPI", datetime(2026, 11, 6, 13, 30, tzinfo=timezone.utc)))
    far = news.ff_event(ff_row("GDP", sat + timedelta(days=30)))
    store = {str(e["id"]): e for e in (mon, last, far)}
    assert [e["title"] for e in news.prune_calendar(store, sat).values()] == ["ISM"]
    sun_open = datetime(2026, 11, 15, 23, 0, tzinfo=timezone.utc)
    assert news.prune_calendar(store, sun_open) == {}                   # الأسبوع الجديد بدأ، وISM أقدم من 72 ساعة


@pytest.mark.parametrize("prev, due", [
    ({}, True),
    ({"calendar_attempt_utc": iso(NOW - timedelta(minutes=20)), "last_success_utc": {"calendar": iso(NOW - timedelta(minutes=20))}}, False),
    ({"calendar_attempt_utc": iso(NOW - timedelta(minutes=55)), "last_success_utc": {"calendar": iso(NOW - timedelta(minutes=55))}}, True),
    ({"calendar_attempt_utc": iso(NOW - timedelta(minutes=20)), "last_success_utc": {"calendar": iso(NOW - timedelta(hours=2))}}, True),
    ({"calendar_attempt_utc": iso(NOW - timedelta(minutes=20)), "last_success_utc": None}, True),
    ({"calendar_attempt_utc": iso(NOW + timedelta(hours=2)), "last_success_utc": {"calendar": iso(NOW)}}, True),
    (None, True),
])
def test_calendar_is_fetched_hourly_and_retried_after_a_failure(prev, due):
    assert news.calendar_due(prev, NOW) is due


# ---------------------------------------------------------------- status and the report's input
@pytest.mark.parametrize("e, want", [
    ({"timeUtc": None}, "unknown_time"),
    ({"timeUtc": "soon", "actual": "1%"}, "unknown_time"),
    ({"timeUtc": "2026-11-13T13:30:00Z", "actual": "0.3%"}, "reported"),
    ({"timeUtc": "2026-11-13T13:30:00Z", "actual": 0}, "reported"),
    ({"timeUtc": "2026-11-13T13:30:00Z", "actual": " "}, "past_no_value_in_source"),
    ({"timeUtc": "2026-11-13T13:30:00Z"}, "past_no_value_in_source"),
    ({"timeUtc": "2026-11-13T18:00:00Z"}, "past_no_value_in_source"),      # وقته الآن بالضبط
    ({"timeUtc": "2026-11-13T18:05:00Z"}, "upcoming"),
])
def test_event_status(e, want):
    assert news.event_status(e, NOW) == want


@pytest.mark.parametrize("k, mins, current", [("items", 30, True), ("items", 31, False),
                                               ("calendar", 130, True), ("calendar", 131, False)])
def test_current_boundaries_follow_each_stream_cadence(k, mins, current):
    feed = news.report_feed({"last_success_utc": {k: iso(NOW - timedelta(minutes=mins))}}, NOW)
    name = "headlines" if k == "items" else "calendar"
    assert feed[f"{name}_current"] is current


@pytest.mark.parametrize("live", [None, [], {"items": None, "calendar": "x", "last_success_utc": "x"},
                                  {"items": ["x", {"id": 1}], "calendar": [None]}])
def test_report_feed_survives_malformed_news_json(live):
    feed = news.report_feed(live, NOW)
    assert feed["headlines"] == [] and feed["calendar"] == [] and feed["headlines_last_success_utc"] is None


def test_feed_status_keeps_last_success_of_a_failing_feed_and_carries_the_calendar():
    prev = {"feeds": [{"name": "CNBC", "url": CNBC_TOP, "ok": True, "last_success_utc": iso(NOW - timedelta(hours=1))},
                      {"name": "ForexFactory this week", "url": FF_THIS, "ok": True, "last_success_utc": iso(NOW - timedelta(minutes=30))},
                      {"name": "gone", "url": "https://example.invalid/old.xml", "ok": True}]}
    out = news.feed_status(prev, NOW, [{"name": "CNBC", "url": CNBC_TOP, "ok": False, "error": "HTTP 403"}], carry=(FF_THIS,))
    assert out[0]["last_success_utc"] == iso(NOW - timedelta(hours=1)) and out[0]["error"] == "HTTP 403"
    assert out[1]["url"] == FF_THIS and len(out) == 2                   # رابط أُزيل من الإعداد لا يبقى
