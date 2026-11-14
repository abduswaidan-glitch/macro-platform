"""PR 4 — ترخيص مؤشري الأسهم وتوزيع الفئات.

Nasdaq-100 وS&P 500 في FRED عليهما إشعار ملكية لطرف ثالث (S&P DJI: «Reproduction ... prohibited except with the prior
written permission»). قرار Abdu (14 نوفمبر 2026): استخدام داخلي فقط. يُجلبان ويُستعملان في الحساب (البوابات وقياس
الترجمة ومدخل التقرير)، ولا تُنشر مستوياتهما ولا نقاطهما، ولا يُحفظان في data/raw، ولا يدخلان لقطة التقرير العامة.
حدّ صريح: تاريخ git القديم يبقى كما هو.
"""
import json
from datetime import datetime, timezone

import pandas as pd

import fetch_macro
import fetch_news
from _report import caller_for, freeze
from macro_platform import ai_report
from macro_platform.build import (build_payload, fetch_all, load_config, public_payload, raw_path, save_raw)
from macro_platform.catalog import BY_ID, CATEGORIES, INDICATORS, INTERNAL_IDS
from macro_platform.demo import demo_series
from macro_platform.releases import RELEASES

CFG = load_config("config.json")
NOW = datetime(2026, 11, 16, 14, 0, tzinfo=timezone.utc)
LEVEL_KEYS = {"last", "prev", "change", "z5", "pct5", "points", "momentum", "revisions"}
WRITE = ai_report.write_report          # الأصل، قبل أي monkeypatch


def test_categories_follow_the_new_tabs_and_housing_is_redistributed():
    assert list(CATEGORIES) == ["growth", "inflation", "labor", "fed_rates", "financial", "markets"]
    cat = {i.id: i.category for i in INDICATORS}
    assert cat["HOUST"] == "growth" and cat["UMCSENT"] == "growth" and cat["MORTGAGE30US"] == "fed_rates"
    assert {k for k, v in cat.items() if v == "financial"} == {"VIXCLS", "DTWEXBGS", "DCOILWTICO", "BAMLH0A0HYM2", "M2SL", "WALCL"}
    assert {k for k, v in cat.items() if v == "markets"} == set(INTERNAL_IDS) == {"NASDAQ100", "SP500"}
    assert "housing" not in CATEGORIES and "rates" not in CATEGORIES and "money" not in CATEGORIES


def test_published_payload_has_no_index_levels_points_or_kpis():
    full = build_payload(demo_series(start="2018-01-01", end="2026-06-30"), {}, CFG, now=NOW)
    pub = public_payload(full)
    for i in pub["indicators"]:
        if i["id"] in INTERNAL_IDS:
            assert i["internal_only"] is True and not (LEVEL_KEYS & set(i)), i
    assert not any(k["id"] in INTERNAL_IDS for k in pub["kpis"])
    assert full["indicators"][-2]["id"] in INTERNAL_IDS and "points" in next(i for i in full["indicators"] if i["id"] == "SP500")
    assert set(pub["market_relative"]) == {"as_of", "d5", "d10", "d20"}          # مشتق فقط
    assert pub["config"]["releases"] == {k: list(v) for k, v in RELEASES.items()}


def test_fetch_does_not_store_internal_series_and_removes_old_copies(tmp_path):
    s = pd.Series(range(500), index=pd.bdate_range("2024-01-01", periods=500), dtype=float)
    save_raw(tmp_path, "SP500", s)                                              # نسخة قديمة في المستودع
    series, st = fetch_all(tmp_path, "2018-01-01", fetcher=lambda sid, start: s * 1.0001,
                           indicators=(BY_ID["SP500"], BY_ID["DGS10"]), log=lambda *a: None)
    assert not raw_path(tmp_path, "SP500").exists() and raw_path(tmp_path, "DGS10").exists()
    assert "SP500" in series                                                    # يُستعمل في الحساب داخل التشغيل

    def down(sid, start):
        raise RuntimeError("down")
    save_raw(tmp_path, "SP500", s)
    series, st = fetch_all(tmp_path, "2018-01-01", fetcher=down, indicators=(BY_ID["SP500"],), log=lambda *a: None)
    assert "SP500" not in series                                                # لا عودة إلى نسخة محفوظة لمؤشر داخلي


def _run(monkeypatch, tmp_path, argv, series, attempt=None, data=None):
    freeze(monkeypatch, "2026-11-16T14:00:00")
    monkeypatch.setattr(ai_report, "write_report", lambda *a, **k: WRITE(*a, caller=caller_for(), **k))
    monkeypatch.setattr(fetch_macro, "fetch_all", lambda raw, start, log=None: (series, {}))
    monkeypatch.setattr(fetch_macro, "load_saved_series", lambda raw: {k: v for k, v in series.items() if k not in INTERNAL_IDS})
    monkeypatch.setattr(fetch_macro, "load_status", lambda p: ({}, []))
    monkeypatch.setattr(fetch_news, "load_live", lambda url: {})
    data = data or tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fetch_macro, "DATA", data)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    if attempt:
        monkeypatch.setenv("REPORT_ATTEMPT", attempt)
    else:
        monkeypatch.delenv("REPORT_ATTEMPT", raising=False)
    out = tmp_path / f"data{len(list(tmp_path.glob('data*.json')))}.json"
    assert fetch_macro.main(["--out", str(out), *argv]) == 0
    return data, json.loads(out.read_text(encoding="utf-8"))


def test_offline_builds_publish_the_derived_numbers_without_the_indices(monkeypatch, tmp_path):
    series = demo_series(start="2018-01-01", end="2026-06-30")
    data, page = _run(monkeypatch, tmp_path, [], series, attempt="1")
    derived = json.loads((data / "market_derived.json").read_text(encoding="utf-8"))
    assert set(derived) == {"generated", "market_relative", "leadership_gate"}
    lead = derived["leadership_gate"]
    assert lead["key"] == "leadership" and lead["value"].endswith("%")         # نسبة مشتقة، لا مستوى
    text = (data / "market_derived.json").read_text(encoding="utf-8")
    last_ndx, last_spx = series["NASDAQ100"].iloc[-1], series["SP500"].iloc[-1]
    assert f"{last_ndx:.2f}"[:6] not in text and f"{last_spx:.2f}"[:6] not in text
    for i in page["indicators"]:                    # تشغيل البيانات نفسه، والمؤشران في ذاكرته: data.json بلا مستوياتهما
        if i["id"] in INTERNAL_IDS:
            assert i["internal_only"] is True and not (LEVEL_KEYS & set(i)), i
    assert not any(k["id"] in INTERNAL_IDS for k in page["kpis"])
    hist = json.loads((data / "ai_history.json").read_text(encoding="utf-8"))
    assert not set(hist[0]["meta"]["inputs_snapshot"]) & set(INTERNAL_IDS)   # اللقطة العامة بلا المؤشرين
    # بناء دورة الأخبار: بلا السلسلتين الخامتين، ومع المشتق
    _, off = _run(monkeypatch, tmp_path, ["--offline"], series, data=data)
    assert off["market_relative"] == derived["market_relative"]
    assert any(g.get("key") == "leadership" for g in off["gates"])
    dumped = json.dumps(off, ensure_ascii=False)
    assert not any("points" in i for i in off["indicators"] if i["id"] in INTERNAL_IDS)
    assert f"{last_ndx:.2f}"[:6] not in dumped


def test_the_report_is_told_not_to_quote_index_levels():
    assert "Do not quote equity index levels" in ai_report.SYSTEM


def test_offline_builds_report_the_last_real_fetch_state_not_a_false_error():
    # أول بناء حي بعد PR 4 (14 نوفمبر 2026): المؤشران غائبان عمداً عن البناء offline فظهرا «خطأ / لا بيانات»،
    # وظهر تنبيه «تعذّر جلب 2 من المؤشرات» في كل بناء
    series = {k: v for k, v in demo_series(start="2018-01-01", end="2026-06-30").items() if k not in INTERNAL_IDS}
    ok = build_payload(series, {"SP500": {"state": "ok", "issues": []}, "NASDAQ100": {"state": "ok", "issues": []}},
                       CFG, now=NOW)
    got = {i["id"]: i for i in ok["indicators"] if i["id"] in INTERNAL_IDS}
    assert {k: (v["state"], v["issues"], v["internal_only"]) for k, v in got.items()} == {
        "NASDAQ100": ("ok", [], True), "SP500": ("ok", [], True)}
    assert not any("Nasdaq" in w or "S&P" in w for w in ok["status"]["warnings"])
    failed = build_payload(series, {"SP500": {"state": "error", "issues": ["فشل الجلب: down"]}}, CFG, now=NOW)
    sp = next(i for i in failed["indicators"] if i["id"] == "SP500")
    assert sp["state"] == "error" and any("SP500" in w for w in failed["status"]["warnings"])   # فشل حقيقي يبقى ظاهراً


def test_a_jump_warning_on_an_index_carries_no_number():
    from macro_platform.build import validate
    s = pd.Series([100.0 + 0.1 * k for k in range(600)] + [400.0], index=pd.bdate_range("2024-01-01", periods=601))
    _, issues = validate(BY_ID["NASDAQ100"], s, None)
    assert issues == ["قفزة غير معتادة في آخر قيمة"]
    _, issues = validate(BY_ID["DGS10"], s, None)
    assert issues[0].startswith("قفزة غير معتادة في آخر قيمة (+")                  # غير الداخلي كما كان
