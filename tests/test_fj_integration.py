"""تكامل FinancialJuice مع تقرير Claude: حالة التغطية بدل الغياب الصامت، ولا قيمة غائبة تُقرأ «لم يصدر».

الاختبارات تمر عبر نقاط الدخول الحقيقية (fetch_macro.main وfetch_news.main) بمزوّد ونموذج مزيّفين: لا شبكة ولا API.
ما لا يُختبر هنا: التزام النموذج نفسه بالتعليمات؛ يُختبر فقط ما يُرسل إليه وما تعرضه الصفحة.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import fetch_macro
import fetch_news
from macro_platform import ai_report, news
from macro_platform.demo import demo_series


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(**kw):
    return iso(datetime.now(timezone.utc) - timedelta(**kw))


def ahead(**kw):
    return iso(datetime.now(timezone.utc) + timedelta(**kw))


def ev(i, title, t, actual=None, forecast="0.3%", previous="0.4%", checked=None, impact=1):
    e = {"id": i, "title": title, "country": "US", "timeUtc": t, "impact": impact, "type": 2, "actual": actual,
         "forecast": forecast, "previous": previous, "revision": None, "unit": "%", "breaking": False, "us": True}
    if checked:
        e["checked_utc"] = checked
    return e


def outage_live():
    """news.json كما يُنشر أثناء انقطاع المزود: updated حديث (وقت البناء) والجلب الناجح الأخير قبل 20 ساعة."""
    return {"updated": ago(minutes=2), "source": "FinancialJuice", "delay_minutes": 10, "translator": None,
            "errors": ["FinancialJuice HTTP 401 invalid_key", "التقويم: FinancialJuice HTTP 401 invalid_key"],
            "last_success_utc": {"items": ago(hours=20), "calendar": ago(hours=20)},
            "items": [{"id": 1, "utc": ago(hours=20, minutes=5), "title_en": "Fed's Waller: further cuts appropriate",
                       "labels": ["FED"], "breaking": False, "level": 0, "link": None, "us": True}],
            "calendar": [
                ev(10, "CPI m/m", ago(hours=6), checked=ago(hours=20)),                       # صدر أثناء الانقطاع
                ev(11, "Retail Sales m/m", ago(hours=22), actual="9.9%", checked=ago(hours=20)),
                ev(12, "Fed Chair Speaks", ago(hours=21), forecast=None, previous=None, checked=ago(hours=20)),
                ev(13, "PPI m/m", ahead(hours=30)),
            ]}


REPORT = {"title": "t", "summary": ["s"], "bias": {"direction": "neutral", "confidence": 50, "reason": "r"},
          "scenarios": [{"label": x, "name": x, "prob": p, "trigger": "", "reason": ""}
                        for x, p in (("A", 50), ("B", 30), ("C", 20))], "one_liner": "o"}


def run_report(monkeypatch, tmp_path, live):
    """fetch_macro.main كاملاً: FRED مزيّف، وnews.json المنشور مزيّف، والنموذج مزيّف يلتقط ما يُرسل إليه."""
    seen = {}

    def caller(user, system, model, effort):
        seen["system"], seen["ctx"] = system, json.loads(user.split("\n", 1)[1])
        return "<report>" + json.dumps(REPORT) + "</report>", [], {k: 0 for k in ai_report.USAGE_KEYS}, model

    orig = ai_report.write_report
    monkeypatch.setattr(ai_report, "write_report", lambda *a, **k: orig(*a, caller=caller, **k))
    monkeypatch.setattr(fetch_macro, "fetch_all",
                        lambda raw, start, log=None: (demo_series(start="2015-01-01", end="2026-06-30"), {}))
    data = tmp_path / "data"
    data.mkdir(parents=True)
    monkeypatch.setattr(fetch_macro, "DATA", data)
    monkeypatch.setattr(fetch_news, "load_live", lambda url: live)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("AI_FORCE", "true")
    assert fetch_macro.main(["--out", str(tmp_path / "data.json")]) == 0
    hist = json.loads((data / "ai_history.json").read_text(encoding="utf-8"))
    return seen, hist[0]


def run_news(monkeypatch, tmp_path, prev, fetch=None, fetch_cal=None, key="fj_test", argv=()):
    """fetch_news.main كاملاً بمزوّد مزيّف؛ fetch/fetch_cal = None يعني أن المسار يفشل بـ401."""
    def fail(*a, **k):
        raise news.NewsError("FinancialJuice HTTP 401 invalid_key")

    if key:
        monkeypatch.setenv("FJ_API_KEY", key)
    else:
        monkeypatch.delenv("FJ_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(fetch_news, "load_live", lambda url: json.loads(json.dumps(prev)))
    monkeypatch.setattr(news, "fetch", (lambda key, since: fetch) if fetch is not None else fail)
    monkeypatch.setattr(news, "fetch_calendar", (lambda key, now: fetch_cal) if fetch_cal is not None else fail)
    site = tmp_path / f"site{len(list(tmp_path.glob('site*')))}"
    assert fetch_news.main(["--site", str(site), *argv]) == 0
    return json.loads((site / "news.json").read_text(encoding="utf-8"))


def fresh_item(i, mins_ago, title="Treasury yields climb"):
    return {"id": i, "datePublished": ago(minutes=mins_ago), "title": title, "labels": ["USD"], "breaking": False,
            "level": 0, "link": None}


def age_min(s):
    return (datetime.now(timezone.utc) - datetime.fromisoformat(s.replace("Z", "+00:00"))).total_seconds() / 60


# ---------------------------------------------------------------- 1. outage: the report is still written, with the gap stated
@pytest.mark.parametrize("live", [outage_live(), {}], ids=["provider-401", "news-json-unreadable"])
def test_outage_report_is_written_and_states_the_coverage_gap(monkeypatch, tmp_path, live):
    seen, rep = run_report(monkeypatch, tmp_path, live)
    assert rep["title"] == "t"                                      # التقرير يُكتب رغم توقف المزود
    fj = seen["ctx"].get("financialjuice")
    assert fj is not None, "the model is not told that the wire exists or is down"
    assert fj["headlines_current"] is False and fj["calendar_current"] is False
    assert "state the gap in data_audit" in fj["note"]
    assert rep["meta"]["fj_input"]["headlines_current"] is False     # أعداد وحالة فقط في السجل العام
    assert "Waller" not in json.dumps(rep["meta"])


# ---------------------------------------------------------------- 2. released event with actual=null
def test_released_event_with_null_actual_is_not_read_as_unreleased(monkeypatch, tmp_path):
    seen, _ = run_report(monkeypatch, tmp_path, outage_live())
    sent = json.dumps(seen["ctx"])
    assert "actual is null until released" not in sent and "instead of searching" not in sent
    status = {e["title"]: e["status"] for e in seen["ctx"]["financialjuice"]["calendar"]}
    assert status == {"CPI m/m": "not_checked_since_release", "Retail Sales m/m": "reported",
                      "Fed Chair Speaks": "no_value_in_feed", "PPI m/m": "upcoming"}
    assert "never means the release did not happen" in seen["ctx"]["financialjuice"]["note"]


# ---------------------------------------------------------------- 3. old headlines are not presented as new
def test_old_headlines_keep_their_time_and_are_marked_not_current(monkeypatch, tmp_path):
    live = outage_live()
    seen, _ = run_report(monkeypatch, tmp_path, live)
    fj = seen["ctx"]["financialjuice"]
    assert fj["headlines"][0]["utc"] == live["items"][0]["utc"]
    assert fj["headlines_current"] is False
    assert fj["headlines_last_success_utc"] == live["last_success_utc"]["items"]
    assert "treat older items as old" in fj["note"]


# ---------------------------------------------------------------- 4. recovery: per stream, no false success, no false "checked"
def test_recovery_updates_only_the_stream_that_succeeded(monkeypatch, tmp_path):
    prev = outage_live()
    old = prev["last_success_utc"]
    out = run_news(monkeypatch, tmp_path, prev, fetch=[fresh_item(5, 3)], fetch_cal=None)    # العناوين عادت فقط
    assert age_min(out["last_success_utc"]["items"]) < 1
    assert out["last_success_utc"]["calendar"] == old["calendar"]
    out = run_news(monkeypatch, tmp_path, prev)                                              # الاثنان يفشلان
    assert out["last_success_utc"] == old and len(out["errors"]) == 2
    out = run_news(monkeypatch, tmp_path, prev, key="")                                       # بلا مفتاح
    assert out["last_success_utc"] == old


def test_malformed_provider_data_is_not_a_successful_fetch(monkeypatch, tmp_path):
    prev = outage_live()
    bad = {**fresh_item(6, 2), "labels": 5}                       # 200 من المزود لكن الدمج يفشل
    out = run_news(monkeypatch, tmp_path, prev, fetch=[bad], fetch_cal=None)
    assert out["last_success_utc"]["items"] == prev["last_success_utc"]["items"]
    assert any("labels" in e or "TypeError" in e or "int" in e for e in out["errors"]), out["errors"]


def test_recovered_calendar_does_not_mark_events_outside_the_fetch_window_as_checked(monkeypatch, tmp_path):
    prev = outage_live()
    prev["calendar"] = [ev(20, "JOLTS", ago(hours=30))]          # وقع أثناء الانقطاع، أقدم من نافذة 23 ساعة
    returned = ev(21, "Jobless Claims", ago(hours=2))             # ما يعيده الجلب بعد العودة
    out = run_news(monkeypatch, tmp_path, prev, fetch=[], fetch_cal=[returned])
    assert age_min(out["last_success_utc"]["calendar"]) < 1
    by_id = {e["id"]: e for e in out["calendar"]}
    assert "checked_utc" not in by_id[20] and age_min(by_id[21]["checked_utc"]) < 1
    now = datetime.now(timezone.utc)
    assert news.event_status(by_id[20], now) == "not_checked_since_release"   # لا «بلا قيمة» لأن الجلب العام نجح
    assert news.event_status(by_id[21], now) == "no_value_in_feed"


def test_invalid_or_future_last_success_is_unknown_not_success(monkeypatch, tmp_path):
    prev = outage_live()
    prev["last_success_utc"] = {"items": ahead(hours=3), "calendar": "garbage"}
    out = run_news(monkeypatch, tmp_path, prev)
    assert out["last_success_utc"] == {"items": None, "calendar": None}


# ---------------------------------------------------------------- 5. news.json contract (invariant: passes before and after)
OLD_TOP = {"updated", "source", "delay_minutes", "translator", "errors", "items", "calendar"}
ITEM_KEYS = {"id", "utc", "title_en", "labels", "breaking", "level", "link", "us"}
FJ_CAL = ("id", "title", "country", "timeUtc", "impact", "actual", "forecast", "previous", "revision")


def test_news_json_contract_is_unchanged(monkeypatch, tmp_path):
    cal = {k: v for k, v in ev(30, "Non-Farm Payrolls", ago(hours=1), actual="85K").items() if k != "us"}
    out = run_news(monkeypatch, tmp_path, {}, fetch=[fresh_item(7, 4)], fetch_cal=[cal])
    assert OLD_TOP <= set(out) and set(out) - OLD_TOP <= {"last_success_utc"}
    assert set(out["items"][0]) == ITEM_KEYS
    e = out["calendar"][0]
    assert all(e[k] == cal[k] for k in FJ_CAL) and e["us"] is True
    assert set(e) - set(news.CAL_FIELDS) - {"us", "title_ar"} <= {"checked_utc"}


def test_deploy_rebuild_republishes_the_last_news_json_unchanged(monkeypatch, tmp_path):
    prev = outage_live()                                          # deploy.yml وupdate.yml: --no-fetch
    out = run_news(monkeypatch, tmp_path, prev, argv=("--no-fetch",))
    assert out == prev


# ---------------------------------------------------------------- 6. gold-dashboard (invariant)
def gold_macro_wire(j):
    """نسخة حرفية من macro_wire() في gold-dashboard (fetch_gold.py عند 918b0f1) بلا طلب الشبكة."""
    news_ = {}
    for x in j.get("items") or []:
        if x.get("id") is None or not x.get("title_en"):
            continue
        news_[str(x["id"])] = {"newsId": x["id"], "datePublished": x.get("utc"), "title": x["title_en"],
                               "labels": x.get("labels") or [], "breaking": x.get("breaking"), "link": x.get("link")}
    return {"news": news_, "cal": {}, "lines": len(news_), "last_recv": j.get("updated"), "deleted": set()}


def test_gold_dashboard_reads_the_same_wire(monkeypatch, tmp_path):
    item = fresh_item(8, 6, "Fed's Logan: no urgency")
    out = run_news(monkeypatch, tmp_path, {}, fetch=[item], fetch_cal=[ev(31, "CPI m/m", ago(hours=1), actual="0.2%")])
    got = gold_macro_wire(out)
    assert got["news"] == {"8": {"newsId": 8, "datePublished": item["datePublished"], "title": "Fed's Logan: no urgency",
                                 "labels": ["USD"], "breaking": False, "link": None}}
    assert got["cal"] == {} and got["last_recv"] == out["updated"]


# ---------------------------------------------------------------- 7. unverified wire figures stay "reported"
def _paths(obj, needle, path=""):
    if isinstance(obj, dict):
        return [p for k, v in obj.items() for p in _paths(v, needle, f"{path}/{k}")]
    if isinstance(obj, list):
        return [p for k, v in enumerate(obj) for p in _paths(v, needle, f"{path}/{k}")]
    return [path] if isinstance(obj, str) and needle in obj else []


def test_wire_figures_stay_reported_and_out_of_platform_data(monkeypatch, tmp_path):
    seen, _ = run_report(monkeypatch, tmp_path, outage_live())
    where = _paths(seen["ctx"], "9.9%")                            # رقم المزود لمبيعات التجزئة
    assert where and all(p.startswith("/financialjuice/") for p in where), where
    fj = seen["ctx"]["financialjuice"]
    assert "third-party" in fj["source"] and "check the official release" in fj["note"]
    assert "Never state reported news as an official fact" in seen["system"]


# ---------------------------------------------------------------- 8. news does not become a probability
def test_news_does_not_become_a_numeric_probability(monkeypatch, tmp_path):
    seen, _ = run_report(monkeypatch, tmp_path, outage_live())
    assert "subjective weights, not calibrated" in seen["system"] and "no single headline sets them" in seen["system"]
    fj = seen["ctx"]["financialjuice"]
    assert {k for h in fj["headlines"] for k in h} == {"utc", "title", "breaking"}      # لا درجة أهمية ولا احتمال
    assert {k for e in fj["calendar"] for k in e} == {"timeUtc", "title", "impact", "actual", "forecast", "previous",
                                                      "revision", "status"}
    html = (fetch_news.ROOT / "index.html").read_text(encoding="utf-8")
    assert "تقدير ذاتي من النموذج، غير معاير" in html and "غير معايرة على نتائج سابقة" in html


# ---------------------------------------------------------------- event_status / report_feed edge cases
NOW = datetime(2026, 11, 13, 15, 0, tzinfo=timezone.utc)
T = "2026-11-13T13:30:00Z"


@pytest.mark.parametrize("e, want", [
    ({"timeUtc": None}, "unknown_time"),
    ({"timeUtc": "not a date", "actual": "1%"}, "unknown_time"),
    ({"timeUtc": T, "actual": "0.3%"}, "reported"),
    ({"timeUtc": T, "actual": 0}, "reported"),                     # الصفر قيمة
    ({"timeUtc": T, "actual": "  "}, "not_checked_since_release"),  # الفراغ ليس قيمة
    ({"timeUtc": "2026-11-13T15:30:00Z"}, "upcoming"),
    ({"timeUtc": "2026-11-13T15:05:00Z"}, "upcoming"),                 # بعد خمس دقائق
    ({"timeUtc": "2026-11-13T15:00:00Z"}, "not_checked_since_release"),  # وقته الآن بالضبط
    ({"timeUtc": T}, "not_checked_since_release"),
    ({"timeUtc": T, "checked_utc": "2026-11-13T13:45:00Z"}, "not_checked_since_release"),  # قبل انتهاء المهلة
    ({"timeUtc": T, "checked_utc": "2026-11-13T13:50:00Z"}, "no_value_in_feed"),
    ({"timeUtc": T, "checked_utc": "2026-11-13T18:00:00Z"}, "not_checked_since_release"),  # في المستقبل: غير صالح
    ({"timeUtc": T, "checked_utc": "garbage"}, "not_checked_since_release"),
])
def test_event_status(e, want):
    assert news.event_status(e, NOW) == want


@pytest.mark.parametrize("mins, current", [(30, True), (31, False)])
def test_current_boundary(mins, current):
    fj = news.report_feed({"last_success_utc": {"items": iso(NOW - timedelta(minutes=mins))}}, NOW)
    assert fj["headlines_current"] is current and fj["calendar_current"] is False


@pytest.mark.parametrize("live", [None, [], {"items": None, "calendar": "x", "last_success_utc": "x"},
                                  {"items": ["x", {"id": 1}], "calendar": [None]}])
def test_report_feed_survives_malformed_news_json(live):
    fj = news.report_feed(live, NOW)
    assert fj["headlines"] == [] and fj["calendar"] == [] and fj["headlines_last_success_utc"] is None
