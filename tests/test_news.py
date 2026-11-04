import json
from datetime import datetime, timedelta, timezone

import pytest

import fetch_news
from macro_platform import news

NOW = datetime(2026, 11, 4, 14, 0, tzinfo=timezone.utc)


def raw(i, mins_ago, title, labels=("USD",), breaking=False, level=0):
    return {"id": i, "datePublished": (NOW - timedelta(minutes=mins_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "title": title, "labels": list(labels), "breaking": breaking, "level": level,
            "link": f"https://www.financialjuice.com/news/{i}/x"}


def test_fetch_follows_pages_and_sends_since():
    calls = []

    def get(url, key):
        calls.append(url)
        if len(calls) == 1:
            return {"data": [raw(2, 1, "b")], "next": "/v1/news?limit=200&before=2"}
        return {"data": [raw(1, 2, "a")], "next": None}

    out = news.fetch("k", NOW - timedelta(hours=1), get=get)
    assert [d["id"] for d in out] == [2, 1]
    assert "updated_since=2026-11-04T13%3A00%3A00Z" in calls[0]
    assert calls[1].startswith("https://stream.financialjuice.com/v1/news?")


def test_merge_keeps_translation_unless_title_changes():
    store = {}
    news.merge(store, [raw(1, 5, "Fed holds rates")])
    store["1"]["title_ar"] = "الفيدرالي يثبّت الفائدة"
    news.merge(store, [raw(1, 5, "Fed holds rates")])
    assert store["1"]["title_ar"] == "الفيدرالي يثبّت الفائدة"
    news.merge(store, [raw(1, 5, "Fed holds rates, signals cut")])  # تعديل العنوان
    assert "title_ar" not in store["1"]


def test_us_detection_by_label_or_words():
    store = {}
    news.merge(store, [raw(1, 1, "ECB's Lagarde speaks", labels=("EUR",)),
                       raw(2, 1, "Powell: inflation still elevated", labels=("Central Banks",)),
                       raw(3, 1, "Oil rises", labels=("US Indexes",))])
    assert [store[k]["us"] for k in "123"] == [False, True, True]


def test_unsafe_link_is_dropped():
    store = {}
    d = raw(1, 1, "x"); d["link"] = "javascript:alert(1)"
    news.merge(store, [d])
    assert store["1"]["link"] is None


def test_translate_only_what_needs_it():
    store = {}
    news.merge(store, [raw(1, 1, "US CPI rises 0.3% M/M"), raw(2, 1, "3.2% / 3.1%"), raw(3, 1, "Fed holds")])
    store["3"]["title_ar"] = "الفيدرالي يثبّت"   # مترجم سابقاً
    sent = []

    def fake(batch):
        sent.extend(i["id"] for i in batch)
        return {i["id"]: "ترجمة " + str(i["id"]) for i in batch}

    done, errors = news.translate_missing(store, translator=fake)
    assert sent == [1] and done == 1 and not errors          # Haiku لم يتلقَّ إلا ما يحتاج ترجمة
    assert store["2"]["title_ar"] == "3.2% / 3.1%"            # لا يحتاج ترجمة: بلا Haiku
    assert store["3"]["title_ar"] == "الفيدرالي يثبّت"

    sent.clear()
    news.translate_missing(store, translator=fake)
    assert sent == []                                          # لا إعادة ترجمة


def test_translation_failure_keeps_english():
    store = {}
    news.merge(store, [raw(1, 1, "Fed holds")])

    def boom(batch):
        raise RuntimeError("down")

    done, errors = news.translate_missing(store, translator=boom)
    assert done == 0 and errors and "title_ar" not in store["1"]


def test_prune_and_since():
    store = {}
    news.merge(store, [raw(1, 60 * 30, "old"), raw(2, 30, "new")])
    store = news.prune(store, NOW)
    assert list(store) == ["2"]
    assert news.since_for(store, NOW) == NOW - timedelta(minutes=45)
    assert news.since_for({}, NOW) == NOW - timedelta(hours=24)


def test_fetch_news_builds_site_without_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("FJ_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(fetch_news, "load_live", lambda url: {})
    root = tmp_path / "repo"                      # data.json مخرَج بناء، فنصنع شجرة مستقلة بدل الاعتماد على ملف محلي
    (root / "data").mkdir(parents=True)
    for f in ("index.html", "data.json", ".nojekyll", "data/ai_history.json"):
        (root / f).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(fetch_news, "ROOT", root)
    site = tmp_path / "_site"
    assert fetch_news.main(["--site", str(site)]) == 0
    out = json.loads((site / "news.json").read_text(encoding="utf-8"))
    assert out["items"] == [] and any("FJ_API_KEY" in e for e in out["errors"])
    assert all((site / f).exists() for f in ("index.html", "data.json", ".nojekyll", "data/ai_history.json"))


def test_fetch_news_end_to_end_with_fakes(tmp_path, monkeypatch):
    monkeypatch.setenv("FJ_API_KEY", "fj_test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(fetch_news, "load_live", lambda url: {"items": [], "translator": None})
    item = raw(9, 1, "Treasury yields climb")
    item["datePublished"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    monkeypatch.setattr(news, "fetch", lambda key, since: [item])
    monkeypatch.setattr(news, "translate_missing",
                        lambda store, translator=None: (store["9"].update(title_ar="ترتفع عوائد السندات") or 1, []))
    site = tmp_path / "_site"
    fetch_news.main(["--site", str(site)])
    out = json.loads((site / "news.json").read_text(encoding="utf-8"))
    assert out["translator"] == news.TRANSLATOR and out["items"][0]["title_ar"] == "ترتفع عوائد السندات"


def test_key_with_quotes_and_newline_is_cleaned(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setenv("FJ_API_KEY", ' "fj_abc123"\n')
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(fetch_news, "load_live", lambda url: {})
    monkeypatch.setattr(news, "fetch", lambda key, since: seen.setdefault("key", key) and [])
    fetch_news.main(["--site", str(tmp_path / "s")])
    assert seen["key"] == "fj_abc123"
