"""news.json المنشور هو المخزن الوحيد لتراكم التقويم والعناوين، فقراءة فاشلة لا تُعامل كملف فارغ.

الحادثة: 13 نوفمبر 2026، دورة 15:11Z: «no live news.json yet: HTTPError» مع 401 من المزود، فبُني الملف من لا شيء
ونُشر فوق الأصل (107 أحداث ← 0)، وقرأت كل دورة بعدها الملف الفارغ. المسار نفسه في deploy.yml وupdate.yml (--no-fetch).
"""
import io
import json
import re
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import fetch_news
from macro_platform import news

ROOT = Path(__file__).resolve().parent.parent


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.now(timezone.utc)
EVENT = {"id": 77, "title": "US PPI MoM", "country": "US", "timeUtc": _iso(NOW + timedelta(days=2)), "impact": 1,
         "type": 2, "actual": None, "forecast": "0.4%", "previous": "1.3%", "revision": None, "unit": "%",
         "breaking": False, "us": True}
PREV = {"updated": _iso(NOW - timedelta(minutes=10)), "source": "FinancialJuice", "delay_minutes": 10,
        "translator": None, "errors": ["FinancialJuice HTTP 401 invalid_key"], "items": [], "calendar": [EVENT]}
HTTP_503 = urllib.error.HTTPError("https://example.invalid/news.json", 503, "Service Unavailable", None, None)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def serve(monkeypatch, outcomes):
    """urlopen مزيّف: الاستدعاء رقم n يأخذ outcomes[n] (استثناء يُرفع، أو كائن يُعاد JSON)، والأخير يتكرر."""
    calls = []

    def urlopen(req, timeout=None):
        calls.append(getattr(req, "full_url", req))
        o = outcomes[min(len(calls), len(outcomes)) - 1]
        if isinstance(o, Exception):
            raise o
        return _Resp(json.dumps(o).encode("utf-8"))

    monkeypatch.setattr(fetch_news.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr("time.sleep", lambda s: None)
    return calls


def provider_down(monkeypatch):
    def fail(*a, **k):
        raise news.NewsError("FinancialJuice HTTP 401 invalid_key")

    monkeypatch.setenv("FJ_API_KEY", "fj_test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(news, "fetch", fail)
    monkeypatch.setattr(news, "fetch_calendar", fail)


def site_root(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    for f in ("index.html", "data.json", ".nojekyll", "data/ai_history.json"):
        (root / f).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(fetch_news, "ROOT", root)
    return tmp_path / "_site"


@pytest.mark.parametrize("argv", [[], ["--no-fetch"]], ids=["news-tick", "deploy-rebuild"])
def test_unreadable_live_news_json_is_never_replaced_by_an_empty_one(tmp_path, monkeypatch, capsys, argv):
    provider_down(monkeypatch)
    calls = serve(monkeypatch, [HTTP_503])
    site = site_root(tmp_path, monkeypatch)
    rc = fetch_news.main(["--site", str(site), *argv])
    assert rc != 0, "a failed read must fail the step so the live site is not redeployed"
    assert not (site / "news.json").exists()
    assert len(calls) == 3                                     # ثلاث محاولات قبل التوقف
    assert "503" in capsys.readouterr().out                    # رمز الخطأ في السجل، لا اسم الاستثناء وحده


def test_transient_read_failure_is_retried_and_the_calendar_survives(tmp_path, monkeypatch):
    provider_down(monkeypatch)
    calls = serve(monkeypatch, [HTTP_503, PREV])
    site = site_root(tmp_path, monkeypatch)
    assert fetch_news.main(["--site", str(site)]) == 0
    out = json.loads((site / "news.json").read_text(encoding="utf-8"))
    assert [e["id"] for e in out["calendar"]] == [77] and len(calls) == 2


def test_live_file_that_is_not_a_json_object_counts_as_unreadable(tmp_path, monkeypatch):
    provider_down(monkeypatch)
    serve(monkeypatch, [["not", "an", "object"]])
    site = site_root(tmp_path, monkeypatch)
    assert fetch_news.main(["--site", str(site)]) != 0 and not (site / "news.json").exists()


def test_first_deploy_without_a_live_file_uses_an_empty_seed(tmp_path, monkeypatch):
    provider_down(monkeypatch)
    calls = serve(monkeypatch, [HTTP_503])
    site = site_root(tmp_path, monkeypatch)
    assert fetch_news.main(["--site", str(site), "--seed-url", ""]) == 0
    out = json.loads((site / "news.json").read_text(encoding="utf-8"))
    assert out["calendar"] == [] and calls == []


def test_news_loop_keeps_its_cadence_and_its_chain_when_a_tick_fails():
    wf = (ROOT / ".github" / "workflows" / "news.yml").read_text(encoding="utf-8")
    # فشل دورة (ومنه رفض النشر أعلاه) لا يُسقط الانتظار ولا إطلاق التشغيل التالي، والإلغاء اليدوي يبقى إلغاءً
    assert re.search(r"name: انتظار حتى تكتمل 10 دقائق\n(?:\s+#.*\n)?\s+if: \$\{\{ !cancelled\(\) && matrix\.i != 30 \}\}", wf)
    assert re.search(r"name: إطلاق التشغيل التالي\n(?:\s+#.*\n)?\s+if: \$\{\{ !cancelled\(\) && matrix\.i == 30 \}\}", wf)
