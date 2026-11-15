"""PR 2 — Deterministic inputs: الكود يحدد ما تغيّر وما رُوجع وزخم التضخم، والنموذج يفسّر معناه.

القائمة المجمّدة (قرار Abdu، 14 نوفمبر 2026): DFF بدل FEDFUNDS، وCCSA، وCES0500000003، وزخم محسوب بالكود.
لا سلسلة أخرى بلا قرار؛ الاختبار الأول يمنع ذلك.
"""
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from macro_platform import ai_report, changes
from macro_platform.analytics import momentum
from macro_platform.build import (REVISION_BULK, build_payload, detect_revisions, fetch_all, is_noise, load_config,
                                  load_raw, merge_status, save_raw)
from macro_platform.catalog import BY_ID, INDICATORS, MOMENTUM_IDS
from macro_platform.demo import demo_series

FROZEN = {"GDPC1", "INDPRO", "RSAFS", "HOUST", "PAYEMS", "UNRATE", "ICSA", "CCSA", "JTSJOL", "CES0500000003",
          "CPIAUCSL", "CPILFESL", "PCEPILFE", "DFF", "DGS2", "DGS10", "T10Y2Y", "DFII10", "T10YIE", "MORTGAGE30US",
          "M2SL", "WALCL", "BAMLH0A0HYM2", "VIXCLS", "DTWEXBGS", "DCOILWTICO", "NASDAQ100", "SP500", "UMCSENT"}
NOW = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)


def monthly(values, start="2026-01-01", day=1):
    idx = pd.date_range(start, periods=len(values), freq="MS") + pd.Timedelta(days=day - 1)
    return pd.Series(values, index=idx, dtype=float)


def test_catalog_is_exactly_the_frozen_list():
    assert {i.id for i in INDICATORS} == FROZEN
    assert BY_ID["DFF"].freq == "D" and "FEDFUNDS" not in BY_ID
    assert set(MOMENTUM_IDS) == {"CPIAUCSL", "CPILFESL", "PCEPILFE", "CES0500000003"}


# ---------------------------------------------------------------- momentum
def test_momentum_mom_and_three_month_annualized():
    m = momentum(BY_ID["CPILFESL"], monthly([100, 100.2, 100.4, 100.6, 101.0]))
    assert m["label"] == "مايو 2026" and m["unavailable"] is None
    assert m["mom"] == round((101.0 / 100.6 - 1) * 100, 3)
    assert m["ann3m"] == round(((101.0 / 100.2) ** 4 - 1) * 100, 2)


def test_momentum_is_unavailable_when_a_month_is_missing_not_filled():
    s = monthly([100, 100.2, 100.4, 100.6, 101.0]).drop(pd.Timestamp("2026-02-01"))   # فبراير غير منشور
    m = momentum(BY_ID["CPILFESL"], s)
    assert m["mom"] is not None and m["ann3m"] is None and "فبراير 2026" in m["unavailable"]


def test_momentum_ignores_the_day_within_the_month():
    a = momentum(BY_ID["CPILFESL"], monthly([100, 101, 102, 103], day=1))
    b = momentum(BY_ID["CPILFESL"], monthly([100, 101, 102, 103], day=15))
    assert (a["mom"], a["ann3m"]) == (b["mom"], b["ann3m"])


def test_momentum_only_for_monthly_series():
    assert momentum(BY_ID["DGS10"], pd.Series([4.0, 4.1], index=pd.bdate_range("2026-01-01", periods=2))) is None


# ---------------------------------------------------------------- revisions
W = "2026-11-16T14:00:00Z"


def test_a_revised_monthly_value_is_detected_with_old_and_new():
    old = monthly([150000, 150100, 150200])
    new = monthly([150000, 150050, 150200, 150300])                     # فبراير رُوجع، وأبريل جديد
    assert detect_revisions(BY_ID["PAYEMS"], old, new, W) == [
        {"detected_utc": W, "date": "2026-02-01", "old": 150100.0, "new": 150050.0}]


def test_representation_noise_daily_series_and_first_fetch_are_not_revisions():
    old = monthly([100.0, 101.0])
    assert detect_revisions(BY_ID["PAYEMS"], old, old + 1e-12, W) == []
    d = pd.Series([4.0, 4.1], index=pd.bdate_range("2026-01-01", periods=2))
    assert detect_revisions(BY_ID["DGS10"], d, d + 0.5, W) == []          # اليومي مستثنى
    assert detect_revisions(BY_ID["PAYEMS"], None, old, W) == []


def test_bulk_revisions_are_summarised_in_one_line():
    old = monthly([100.0 + k for k in range(20)])
    new = old * 1.01
    got = detect_revisions(BY_ID["PAYEMS"], old, new, W)
    assert len(got) == 1 and got[0]["bulk"] and got[0]["count"] == 20 > REVISION_BULK
    assert got[0]["range"] == ["2026-01-01", "2027-08-01"]


def test_merge_status_keeps_45_days_and_the_last_new_observation_time():
    prev = {"PAYEMS": {"revisions": [{"detected_utc": "2026-09-01T00:00:00Z", "date": "x"},
                                     {"detected_utc": "2026-11-10T00:00:00Z", "date": "y"}],
                       "last_new_obs_utc": "2026-11-06T15:00:00Z"},
            "CPIAUCSL": {"last_new_obs_utc": "2026-11-10T14:12:00Z"}}
    status = {"PAYEMS": {"state": "ok", "issues": [], "revisions_new": [{"detected_utc": W, "date": "z"}], "new_obs": False},
              "CPIAUCSL": {"state": "ok", "issues": [], "revisions_new": [], "new_obs": True}}
    out = merge_status(prev, status, NOW)
    assert [r["date"] for r in out["PAYEMS"]["revisions"]] == ["y", "z"]
    assert out["PAYEMS"]["last_new_obs_utc"] == "2026-11-06T15:00:00Z"
    assert out["CPIAUCSL"]["last_new_obs_utc"] == "2026-11-16T15:00:00Z"
    assert "revisions_new" not in out["PAYEMS"] and "new_obs" not in out["CPIAUCSL"]


def test_fetch_all_compares_the_saved_copy_with_the_new_fetch(tmp_path):
    ind = BY_ID["PAYEMS"]
    idx = pd.date_range("2018-01-01", periods=100, freq="MS")
    old = pd.Series(range(100000, 100100), index=idx, dtype=float)
    save_raw(tmp_path, "PAYEMS", old)
    new = old.copy()
    new.iloc[-2] -= 40
    new = pd.concat([new, pd.Series([100200.0], index=[idx[-1] + pd.offsets.MonthBegin()])])
    _, status = fetch_all(tmp_path, "2018-01-01", fetcher=lambda sid, start: new, indicators=(ind,), log=lambda *a: None)
    st = status["PAYEMS"]
    assert st["new_obs"] is True and [(r["date"], r["old"], r["new"]) for r in st["revisions_new"]] == [
        (idx[-2].strftime("%Y-%m-%d"), float(old.iloc[-2]), float(old.iloc[-2] - 40))]
    assert load_raw(tmp_path, "PAYEMS").iloc[-1] == 100200.0


# أول تشغيل حي (14 نوفمبر 2026): 18 «مراجعة» كلها فرق دقة الحفظ (data/raw بست خانات معنوية، والجلب بدقة FRED الكاملة)
LIVE_NOISE = [{"detected_utc": "2026-11-14T19:49:56Z", "date": "2024-10-01", "old": 23641.8, "new": 23641.759},
              {"detected_utc": "2026-11-14T19:50:01Z", "date": "2026-09-09", "old": 6740620.0, "new": 6740619.0},
              {"detected_utc": "2026-11-14T19:49:57Z", "bulk": True, "count": 19, "range": ["2024-12-01", "2026-09-01"],
               "largest": {"date": "2025-05-01", "old": 100.966, "new": 100.9655}}]


def test_values_with_more_than_six_digits_are_compared_at_the_stored_precision(tmp_path):
    idx = pd.date_range("2000-01-01", periods=107, freq="QS")
    full = pd.Series([13878.123 + 101.789 * k for k in range(107)], index=idx)          # مثل GDPC1: ثماني خانات
    save_raw(tmp_path, "GDPC1", full)
    for _ in range(2):                                                                  # ولا تتكرر في التشغيل التالي
        _, st = fetch_all(tmp_path, "2000-01-01", fetcher=lambda sid, start: full, indicators=(BY_ID["GDPC1"],),
                          log=lambda *a: None)
        assert st["GDPC1"]["revisions_new"] == []
    revised = full.copy()
    revised.iloc[-2] += 25.0                                                            # مراجعة حقيقية
    _, st = fetch_all(tmp_path, "2000-01-01", fetcher=lambda sid, start: revised, indicators=(BY_ID["GDPC1"],),
                      log=lambda *a: None)
    assert [r["date"] for r in st["GDPC1"]["revisions_new"]] == [idx[-2].strftime("%Y-%m-%d")]


def test_precision_noise_already_recorded_is_dropped_and_real_revisions_are_kept(demo_cfg=None):
    real = {"detected_utc": "2026-11-14T19:49:56Z", "date": "2026-09-01", "old": 150100.0, "new": 150050.0}
    assert all(is_noise(r) for r in LIVE_NOISE) and not is_noise(real)
    out = merge_status({"GDPC1": {"revisions": LIVE_NOISE + [real]}}, {"GDPC1": {"state": "ok", "issues": []}}, NOW)
    assert out["GDPC1"]["revisions"] == [real]
    cfg = load_config("config.json")
    p = build_payload(demo_series(start="2015-01-01", end="2026-06-30"),
                      {"GDPC1": {"state": "ok", "issues": [], "revisions": LIVE_NOISE + [real]}}, cfg, now=NOW)
    assert p["revisions"] == [{"id": "GDPC1", "name_ar": BY_ID["GDPC1"].name_ar, **real}]   # الصفحة وسياق التقرير


def test_a_failed_fetch_produces_no_revision(tmp_path):
    save_raw(tmp_path, "PAYEMS", monthly([1.0, 2.0, 3.0]))

    def boom(sid, start):
        raise RuntimeError("down")

    _, status = fetch_all(tmp_path, "2018-01-01", fetcher=boom, indicators=(BY_ID["PAYEMS"],), log=lambda *a: None)
    assert "revisions_new" not in status["PAYEMS"] and status["PAYEMS"]["state"] == "error"


# ---------------------------------------------------------------- what changed since the previous report
def snap(**kw):
    base = {"last": 4.0, "as_of": "2026-11-13", "source_as_of": "2026-11-13", "state": "ok", "pct5": 50.0}
    return {k: {**base, **v} for k, v in kw.items()}


def test_changes_need_a_previous_report_with_a_snapshot():
    assert changes.diff(None, snap(DGS10={}), []) == {"available": False, "reason": "no previous report"}
    got = changes.diff({"meta": {"ny_date": "2026-11-13"}}, snap(DGS10={}), [])
    assert got["available"] is False and got["reason"] == "previous report has no snapshot"


def test_changes_list_every_kind_without_thresholds():
    prev = {"meta": {"ny_date": "2026-11-13", "completed_at": "2026-11-13T13:50:00Z",
                     "inputs_snapshot": snap(DGS10={}, PAYEMS={"as_of": "2026-09-01", "last": 120.0},
                                             UNRATE={}, VIXCLS={}, ICSA={})}}
    now = snap(DGS10={"as_of": "2026-11-16", "last": 4.01},              # قراءة جديدة ولو صغيرة
               PAYEMS={"as_of": "2026-09-01", "last": 118.0},            # القيمة نفسها التاريخ تغيّرت
               UNRATE={"state": "stale"}, VIXCLS={}, ICSA={}, CCSA={})
    revs = [{"detected_utc": "2026-11-12T00:00:00Z", "date": "old"}, {"detected_utc": "2026-11-14T00:00:00Z", "date": "new"}]
    got = changes.diff(prev, now, revs)
    kinds = sorted((i["kind"], i.get("id") or i.get("date")) for i in got["items"])
    assert kinds == [("new_indicator", "CCSA"), ("new_observation", "DGS10"), ("revision", "new"),
                     ("state_change", "UNRATE"), ("value_change", "PAYEMS")]
    assert got["unchanged_count"] == 2 and got["previous_ny_date"] == "2026-11-13"


def test_snapshot_is_numbers_and_dates_only():
    payload = build_payload(demo_series(start="2020-01-01", end="2026-06-30"), {}, load_config("config.json"), now=NOW)
    s = changes.snapshot(payload)
    # مؤشرا الأسهم (ترخيص S&P DJI وNasdaq) لا يدخلان اللقطة العامة في ai_history.json
    assert set(s) == {i["id"] for i in payload["indicators"]} - {"NASDAQ100", "SP500"}
    assert all(set(v) == {"last", "as_of", "source_as_of", "state", "pct5"} for v in s.values())


# ---------------------------------------------------------------- the report sees them, and stores its snapshot
from _report import body as _body  # noqa: E402


def test_report_context_carries_changes_revisions_momentum_and_previous_report():
    cfg = load_config("config.json")
    payload = build_payload(demo_series(start="2020-01-01", end="2026-06-30"), {}, cfg, now=NOW)
    next(i for i in payload["indicators"] if i["id"] == "PAYEMS")["revisions"] = [
        {"detected_utc": "2026-11-15T00:00:00Z", "date": "2026-09-01", "old": 1, "new": 2}]
    seen = {}

    def caller(user, system, model, effort):
        if system != ai_report.SYSTEM_MARKETS:                         # الاستدعاء الاقتصادي، لا Market Gates بعده
            seen["ctx"], seen["system"] = json.loads(user.split("\n", 1)[1]), system
        return "<report>" + json.dumps(_body()) + "</report>", [], {k: 0 for k in ai_report.USAGE_KEYS}, model

    first = ai_report.write_report(payload, cfg, now_utc=NOW, caller=caller)
    assert all(seen["ctx"]["sections"][k]["changes_since_previous_report"] == {"available": False, "reason": "no previous report"}
               for k in ("growth", "inflation", "labor", "fed_rates", "financial"))
    snap0 = first["meta"]["inputs_snapshot"]
    assert snap0 and all(set(v) == {"last", "as_of", "source_as_of", "state", "pct5"} for v in snap0.values())
    first["meta"]["completed_at"] = "2026-11-14T00:00:00Z"
    ai_report.write_report(payload, cfg, now_utc=NOW + timedelta(days=1), caller=caller, previous=first)
    ctx = seen["ctx"]
    labor = ctx["sections"]["labor"]["changes_since_previous_report"]          # كل قسم يحسب ما تغيّر فيه
    assert labor["available"] is True and any(i["kind"] == "revision" for i in labor["items"])
    assert not any(i["kind"] == "revision" for i in ctx["sections"]["growth"]["changes_since_previous_report"]["items"])
    assert ctx["previous_report"]["scenarios"][0] == {"label": "A", "name": "سيناريو A", "prob": 45}
    cpi = next(i for i in ctx["sections"]["inflation"]["indicators"] if i["id"] == "CPILFESL")
    assert "momentum" in cpi and "revisions" in cpi
    assert "computed by the platform's code" in seen["system"] and "do not recompute or guess them" in seen["system"]


def test_daily_effective_fed_funds_drives_the_pricing_gate():
    s = demo_series(start="2020-01-01", end="2026-06-30")
    assert "DFF" in s and "FEDFUNDS" not in s
    payload = build_payload(s, {}, load_config("config.json"), now=NOW)
    gate = next(g for g in payload["gates"] if g["key"] == "pricing")
    assert "FF" in gate["extra"] and any(k["id"] == "DFF" for k in payload["kpis"])
