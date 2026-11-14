"""الأخبار والتقويم من نقاط الدخول الحقيقية (fetch_news.main وfetch_macro.main) بشبكة ونموذج مزيّفين: لا شبكة ولا API.

ما يُثبت هنا: لا FinancialJuice ولا مفتاح لأي مصدر؛ فشل مصدر لا يمحو شيئاً ولا يُعدّ نجاحاً؛ التقويم يُجلب مرة في الساعة
ويبقى أسبوعه؛ عقد news.json الذي تقرؤه منصة الذهب بلا تغيير؛ وتقرير Claude يعرف المصدر وحدوده.
ما لا يُختبر هنا: التزام النموذج نفسه بالتعليمات، ومحتوى المصادر الحقيقي (يُفحص من خوادم GitHub في الطلب).
"""
import json
import re
from datetime import timedelta
from pathlib import Path

import pytest

import fetch_macro
import fetch_news
from macro_platform import ai_report, news
from macro_platform.demo import demo_series
from _newsfake import CNBC_ECON, CNBC_TOP, FF_THIS, MW, Net, default_routes, ff, ff_row, iso, now

ROOT = Path(__file__).resolve().parent.parent
REPORT = {"title": "t", "summary": ["s"], "bias": {"direction": "neutral", "confidence": 50, "reason": "r"},
          "scenarios": [{"label": x, "name": x, "prob": p, "trigger": "", "reason": ""}
                        for x, p in (("A", 50), ("B", 30), ("C", 20))], "one_liner": "o"}


def run_news(monkeypatch, tmp_path, prev, routes=None, argv=()):
    """fetch_news.main كاملاً: news.json المنشور = prev، والمصادر = routes (الافتراضي: كلها تعمل)."""
    net = Net(default_routes() if routes is None else routes)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("FJ_API_KEY", raising=False)
    monkeypatch.setattr(fetch_news, "load_live", lambda url: json.loads(json.dumps(prev)))
    monkeypatch.setattr(news, "http_get", net)
    site = tmp_path / f"site{len(list(tmp_path.glob('site*')))}"
    assert fetch_news.main(["--site", str(site), *argv]) == 0
    return json.loads((site / "news.json").read_text(encoding="utf-8")), net


def run_report(monkeypatch, tmp_path, live):
    """fetch_macro.main كاملاً: FRED مزيّف، وnews.json المنشور = live، والنموذج مزيّف يلتقط ما يُرسل إليه."""
    seen = {}

    def caller(user, system, model, effort):
        seen["system"], seen["ctx"] = system, json.loads(user.split("\n", 1)[1])
        return "<report>" + json.dumps(REPORT) + "</report>", [], {k: 0 for k in ai_report.USAGE_KEYS}, model

    orig = ai_report.write_report
    monkeypatch.setattr(ai_report, "write_report", lambda *a, **k: orig(*a, caller=caller, **k))
    monkeypatch.setattr(fetch_macro, "fetch_all",
                        lambda raw, start, log=None: (demo_series(start="2015-01-01", end="2026-06-30"), {}))
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fetch_macro, "DATA", data)
    monkeypatch.setattr(fetch_news, "load_live", lambda url: live)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("AI_FORCE", "true")
    assert fetch_macro.main(["--out", str(tmp_path / "data.json")]) == 0
    return seen, json.loads((data / "ai_history.json").read_text(encoding="utf-8"))[0]


def age_min(s):
    return (now() - news._parse_t(s)).total_seconds() / 60


# ---------------------------------------------------------------- 1. a normal tick, from nothing, with no keys at all
def test_first_tick_fills_headlines_and_calendar_without_any_key(monkeypatch, tmp_path):
    out, net = run_news(monkeypatch, tmp_path, {})
    titles = [i["title_en"] for i in out["items"]]
    assert titles == ["Stocks fall as Treasury yields jump after hot inflation data", "Nvidia shares rise ahead of earnings",
                      "Federal Reserve issues FOMC statement", "Waller, Outlook for the Economy and Policy",
                      "Personal Income and Outlays, September 2026"]
    assert [e["title"] for e in out["calendar"]] == ["Core CPI m/m", "Unemployment Claims", "FOMC Member Speaks"]
    assert age_min(out["last_success_utc"]["items"]) < 1 and age_min(out["last_success_utc"]["calendar"]) < 1
    assert out["errors"] == [] and out["source"] == "Fed · BEA · CNBC · MarketWatch · ForexFactory"
    assert all("financialjuice" not in u for u in net.calls) and net.count(FF_THIS) == 1
    feeds = {f["url"]: f for f in out["feeds"]}
    assert feeds[FF_THIS]["ok"] and not feeds[news.DEFAULT_CALENDAR[1]["url"]]["ok"]      # الأسبوع القادم: 404 اختياري


# ---------------------------------------------------------------- 2. failures: nothing is wiped, nothing counts as success
def test_failing_media_feed_is_reported_and_the_rest_still_publishes(monkeypatch, tmp_path):
    first, _ = run_news(monkeypatch, tmp_path, {})
    routes = default_routes()
    routes[CNBC_TOP] = routes[CNBC_ECON] = news.NewsError("HTTP 403")
    out, _ = run_news(monkeypatch, tmp_path, first, routes)
    assert out["errors"] == ["CNBC: HTTP 403", "CNBC: HTTP 403"]
    assert "Stocks fall as Treasury yields jump after hot inflation data" in [i["title_en"] for i in out["items"]]  # يبقى
    cnbc = next(f for f in out["feeds"] if f["url"] == CNBC_TOP)
    assert cnbc["ok"] is False and cnbc["last_success_utc"] == next(f for f in first["feeds"] if f["url"] == CNBC_TOP)["last_success_utc"]


def test_all_sources_down_keeps_everything_and_last_success(monkeypatch, tmp_path):
    first, _ = run_news(monkeypatch, tmp_path, {})
    first["calendar_attempt_utc"] = first["last_success_utc"]["calendar"] = iso(now() - timedelta(hours=2))
    down = {u: news.NewsError("HTTP 503") for u in default_routes()}
    out, net = run_news(monkeypatch, tmp_path, first, down)
    assert [i["id"] for i in out["items"]] == [i["id"] for i in first["items"]]
    assert [e["id"] for e in out["calendar"]] == [e["id"] for e in first["calendar"]]
    assert out["last_success_utc"] == {"items": first["last_success_utc"]["items"], "calendar": first["last_success_utc"]["calendar"]}
    assert "التقويم: ForexFactory this week: HTTP 503" in out["errors"] and len(out["errors"]) == len(news.DEFAULT_SOURCES) + 1
    assert net.count(FF_THIS) == 1
    again, net = run_news(monkeypatch, tmp_path, out, down)            # الدورة التالية تعيد المحاولة، لا تنتظر ساعة
    assert net.count(FF_THIS) == 1


def test_malformed_feed_content_is_not_a_successful_fetch(monkeypatch, tmp_path):
    first, _ = run_news(monkeypatch, tmp_path, {})
    first["last_success_utc"]["items"] = iso(now() - timedelta(hours=3))
    bad = {u: b"<html>maintenance</html>" for u in default_routes()}
    bad[FF_THIS] = b'{"error": "rate limited"}'
    first["calendar_attempt_utc"] = iso(now() - timedelta(hours=2))
    out, _ = run_news(monkeypatch, tmp_path, first, bad)
    assert out["last_success_utc"]["items"] == first["last_success_utc"]["items"]
    assert out["last_success_utc"]["calendar"] == first["last_success_utc"]["calendar"]
    assert "التقويم: ForexFactory this week: no rows" in out["errors"]


# ---------------------------------------------------------------- 3. calendar: hourly, and the week is kept
def test_calendar_is_not_requested_again_within_the_hour(monkeypatch, tmp_path):
    first, _ = run_news(monkeypatch, tmp_path, {})
    out, net = run_news(monkeypatch, tmp_path, first)
    assert net.count(FF_THIS) == 0 and out["calendar"] == first["calendar"]
    assert out["last_success_utc"]["calendar"] == first["last_success_utc"]["calendar"]
    assert out["calendar_attempt_utc"] == first["calendar_attempt_utc"]
    assert next(f for f in out["feeds"] if f["url"] == FF_THIS) == next(f for f in first["feeds"] if f["url"] == FF_THIS)


def test_this_weeks_past_events_stay_when_the_source_moves_on(monkeypatch, tmp_path):
    """شكوى 13 نوفمبر: نهاية الأسبوع لا تعني تقويماً فارغاً. حدث مضى هذا الأسبوع ولم يعد المصدر يذكره يبقى."""
    t = now()
    earlier = news.ff_event(ff_row("ISM Services PMI", t - timedelta(hours=50)))
    prev = {"calendar": [earlier], "items": [], "calendar_attempt_utc": iso(t - timedelta(hours=2)),
            "last_success_utc": {"items": None, "calendar": iso(t - timedelta(hours=2))}}
    routes = default_routes()
    routes[FF_THIS] = ff(ff_row("Unemployment Claims", t + timedelta(hours=20), impact="Medium"))   # المصدر انتقل
    out, _ = run_news(monkeypatch, tmp_path, prev, routes)
    assert [e["title"] for e in out["calendar"]] == ["ISM Services PMI", "Unemployment Claims"]


# ---------------------------------------------------------------- 4. news.json contract read by gold-dashboard (invariant)
OLD_TOP = {"updated", "source", "delay_minutes", "translator", "errors", "items", "calendar"}
ITEM_KEYS = {"id", "utc", "title_en", "labels", "breaking", "level", "link", "us"}
GOLD_CAL = ("id", "title", "country", "timeUtc", "impact", "actual", "forecast", "previous", "revision")


def test_news_json_contract_is_unchanged(monkeypatch, tmp_path):
    out, _ = run_news(monkeypatch, tmp_path, {})
    assert OLD_TOP <= set(out) and set(out) - OLD_TOP <= {"last_success_utc", "feeds", "calendar_attempt_utc", "keep_since_utc"}
    assert all(set(i) == ITEM_KEYS for i in out["items"])
    assert all(isinstance(i["id"], int) and i["id"] < 2 ** 53 for i in out["items"])
    e = out["calendar"][0]
    assert all(k in e for k in GOLD_CAL) and set(e) - set(news.CAL_FIELDS) == {"us"}
    assert e["country"] == "US" and e["impact"] == 1 and e["timeUtc"].endswith("Z")


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
    out, _ = run_news(monkeypatch, tmp_path, {})
    got = gold_macro_wire(out)
    fed = next(v for v in got["news"].values() if v["title"] == "Federal Reserve issues FOMC statement")
    assert fed["labels"] == ["Fed", "official"] and fed["link"].startswith("https://www.federalreserve.gov/")
    assert got["lines"] == len(out["items"]) and got["last_recv"] == out["updated"]


def test_deploy_rebuild_republishes_the_last_news_json_unchanged(monkeypatch, tmp_path):
    first, _ = run_news(monkeypatch, tmp_path, {})
    out, net = run_news(monkeypatch, tmp_path, first, argv=("--no-fetch",))
    assert out == first and net.calls == []


# ---------------------------------------------------------------- 5. Claude's report: knows the sources and their limits
def test_report_gets_headlines_with_their_source_and_calendar_without_actuals(monkeypatch, tmp_path):
    live, _ = run_news(monkeypatch, tmp_path, {})
    seen, rep = run_report(monkeypatch, tmp_path, live)
    feed = seen["ctx"]["news_feed"]
    assert "financialjuice" not in json.dumps(seen["ctx"]).lower()
    assert feed["headlines_current"] is True and feed["calendar_current"] is True
    src = {h["title"]: h["source"] for h in feed["headlines"]}
    assert src["Federal Reserve issues FOMC statement"] == "Fed official"
    assert src["Stocks fall as Treasury yields jump after hot inflation data"] == "CNBC media"
    assert {e["title"]: e["status"] for e in feed["calendar"]} == {
        "Core CPI m/m": "past_no_value_in_source", "Unemployment Claims": "upcoming", "FOMC Member Speaks": "upcoming"}
    assert "never carries actual figures" in feed["note"] and "media' = CNBC or MarketWatch reported it" in feed["note"]
    assert '"news_feed" block' in seen["system"] and "Never state reported news as an official fact" in seen["system"]
    meta = rep["meta"]["news_input"]
    assert meta["headlines"] == len(feed["headlines"]) and meta["calendar"] == {"past_no_value_in_source": 1, "upcoming": 2}
    assert "Treasury yields" not in json.dumps(rep["meta"])               # لا نص عناوين في السجل العام


@pytest.mark.parametrize("live", [None, {}], ids=["news-json-unreadable", "news-json-empty"])
def test_outage_report_is_written_and_states_the_coverage_gap(monkeypatch, tmp_path, live):
    seen, rep = run_report(monkeypatch, tmp_path, live)
    feed = seen["ctx"]["news_feed"]
    assert rep["title"] == "t" and feed["headlines_current"] is False and feed["calendar_current"] is False
    assert "state the gap in data_audit" in feed["note"] and rep["meta"]["news_input"]["headlines_current"] is False


def test_news_does_not_become_a_numeric_probability(monkeypatch, tmp_path):
    live, _ = run_news(monkeypatch, tmp_path, {})
    seen, _ = run_report(monkeypatch, tmp_path, live)
    assert "subjective weights, not yet calibrated" in seen["system"] and "no single headline sets them" in seen["system"]
    feed = seen["ctx"]["news_feed"]
    assert {k for h in feed["headlines"] for k in h} == {"utc", "title", "source", "link"}   # لا درجة أهمية ولا احتمال
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "تقدير ذاتي من النموذج، غير معاير" in html and "غير معايرة على نتائج سابقة" in html


# ---------------------------------------------------------------- 6. FinancialJuice is gone from the platform
def test_no_financialjuice_left_in_code_workflows_or_page():
    files = [ROOT / "fetch_news.py", ROOT / "fetch_macro.py", ROOT / "index.html", ROOT / "macro_platform" / "ai_report.py",
             *(ROOT / ".github" / "workflows").glob("*.yml")]
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"financialjuice|FJ_API_KEY", text, re.I), f.name
    code = (ROOT / "macro_platform" / "news.py").read_text(encoding="utf-8")
    assert "stream.financialjuice.com" not in code and "X-API-Key" not in code
