"""التغير السنوي على التقويم، وفصل حالة الجلب عن غياب التغير السنوي.

الحالة الحقيقية: FRED لم ينشر CPI لشهر أكتوبر 2025. الكود السابق (pct_change(12) الموضعي) حسب أكتوبر 2026 مقابل
سبتمبر 2025 (13 شهراً) ونشر 4.0806% للرئيسي و2.8697% للأساسي. القيم أدناه منسوخة من data/raw/CPIAUCSL.csv
وCPILFESL.csv (commit 667259b) ومجمّدة هنا كي لا يتغير الاختبار حين يضيف البوت بيانات جديدة.
"""
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from macro_platform import ai_report
from macro_platform.analytics import snapshot, transform, transform_indicator
from macro_platform.build import build_payload, kpis, load_config
from macro_platform.catalog import BY_ID

NOW = datetime(2026, 11, 12, 21, 0, tzinfo=timezone.utc)
WRONG = ("4.0806", "4.0233", "2.8697", "2.7892")          # القيم المنشورة الخاطئة (آخر قيمتين لكل سلسلة)

CPI = """2024-08-01,314.062 2024-09-01,314.732 2024-10-01,315.631 2024-11-01,316.528 2024-12-01,317.604
2025-01-01,318.961 2025-02-01,319.679 2025-03-01,319.785 2025-04-01,320.302 2025-05-01,320.62 2025-06-01,321.435
2025-07-01,322.169 2025-08-01,323.291 2025-09-01,324.245 2025-11-01,325.063 2025-12-01,326.031 2026-01-01,326.588
2026-02-01,327.46 2026-03-01,330.293 2026-04-01,332.407 2026-05-01,333.979 2026-06-01,332.568 2026-07-01,332.813
2026-08-01,334.131 2026-09-01,336.298 2026-10-01,337.476"""
CORE = """2024-08-01,319.75 2024-09-01,320.732 2024-10-01,321.731 2024-11-01,322.657 2024-12-01,323.259
2025-01-01,324.638 2025-02-01,325.465 2025-03-01,325.69 2025-04-01,326.467 2025-05-01,326.893 2025-06-01,327.658
2025-07-01,328.682 2025-08-01,329.7 2025-09-01,330.418 2025-11-01,331.043 2025-12-01,331.814 2026-01-01,332.793
2026-02-01,333.512 2026-03-01,334.165 2026-04-01,335.423 2026-05-01,336.121 2026-06-01,336.065 2026-07-01,336.789
2026-08-01,337.765 2026-09-01,338.896 2026-10-01,339.9"""


def series(text: str, name: str) -> pd.Series:
    pairs = [p.split(",") for p in text.split()]
    return pd.Series([float(v) for _, v in pairs], index=pd.DatetimeIndex([d for d, _ in pairs]), name=name)


def cpi_payload(status=None, now=NOW):
    return build_payload({"CPIAUCSL": series(CPI, "CPIAUCSL"), "CPILFESL": series(CORE, "CPILFESL")},
                         status or {}, load_config("config.json"), now=now)


def by_id(payload):
    return {i["id"]: i for i in payload["indicators"]}


def _yoy(s, date, base):
    return (s[date] / s[base] - 1) * 100


@pytest.fixture()
def cpi():
    return series(CPI, "CPIAUCSL")


@pytest.fixture()
def core():
    return series(CORE, "CPILFESL")


# ---------------------------------------------------------------- الحساب (YoY فقط)
def test_fixture_reproduces_the_source_gap(cpi, core):
    for s in (cpi, core):
        assert pd.Timestamp("2025-10-01") not in s.index and s.index[-1] == pd.Timestamp("2026-10-01")


def test_headline_october_2026_not_computed_against_september_2025(cpi):
    t = transform_indicator(BY_ID["CPIAUCSL"], cpi)
    assert pd.Timestamp("2026-10-01") not in t.index
    assert t.index[-1] == pd.Timestamp("2026-09-01")
    assert t.iloc[-1] == pytest.approx(_yoy(cpi, "2026-09-01", "2025-09-01"))
    assert t.iloc[-1] == pytest.approx(3.71725, abs=1e-5)


def test_core_october_2026_not_computed_against_september_2025(core):
    t = transform_indicator(BY_ID["CPILFESL"], core)
    assert t.index[-1] == pd.Timestamp("2026-09-01")
    assert t.iloc[-1] == pytest.approx(2.56584, abs=1e-5)


def test_every_month_after_the_gap_uses_the_same_calendar_month(cpi, core):
    """الخلل شمل نوفمبر 2025 حتى سبتمبر 2026 أيضاً، لا آخر قيمة فقط."""
    for s in (cpi, core):
        t = transform(s, "yoy", "M")
        for d in pd.date_range("2025-11-01", "2026-09-01", freq="MS"):
            assert t[d] == pytest.approx(_yoy(s, d, d - pd.DateOffset(years=1))), d
        assert pd.isna(t["2026-10-01"])
        assert pd.Timestamp("2025-10-01") not in t.index


def test_gap_is_never_forward_filled():
    """pct_change في pandas 2.x يملأ الفجوة افتراضياً بالقيمة السابقة، فيعيد الخطأ نفسه بطريق آخر."""
    idx = pd.date_range("2020-01-01", "2023-12-01", freq="MS")
    s = pd.Series(np.arange(len(idx), dtype=float) + 100, index=idx).drop(pd.Timestamp("2022-06-01"))
    t = transform(s, "yoy", "M")
    assert pd.isna(t["2023-06-01"])
    assert t["2023-07-01"] == pytest.approx((s["2023-07-01"] / s["2022-07-01"] - 1) * 100)


def test_quarterly_yoy_gap():
    idx = pd.date_range("2018-01-01", "2023-10-01", freq="QS")
    s = pd.Series(1.005 ** np.arange(len(idx)) * 100, index=idx).drop(pd.Timestamp("2021-07-01"))
    t = transform(s, "yoy", "Q")
    assert pd.isna(t["2022-07-01"])
    assert t["2022-10-01"] == pytest.approx((1.005 ** 4 - 1) * 100)


@pytest.mark.parametrize("kind", ["level", "diff", "diff_avg3"])
def test_scope_other_transforms_unchanged_even_with_a_gap(kind):
    """التصحيح محصور في YoY: level وdiff وdiff_avg3 تبقى كما كانت حرفياً، حتى على سلسلة فيها فجوة."""
    idx = pd.date_range("2020-01-01", "2021-12-01", freq="MS")
    s = pd.Series(np.arange(len(idx), dtype=float) * 10, index=idx).drop(pd.Timestamp("2021-03-01"))
    old = {"level": s, "diff": s.diff(), "diff_avg3": s.diff().rolling(3).mean()}[kind]
    pd.testing.assert_series_equal(transform(s, kind, "M"), old)


@pytest.mark.parametrize("freq,code,n", [("M", "MS", 12), ("Q", "QS", 4)])
def test_complete_series_yoy_identical_to_previous_code(freq, code, n):
    idx = pd.date_range("2010-01-01", periods=80, freq=code)
    s = pd.Series(np.random.default_rng(0).normal(0, 1, len(idx)).cumsum() + 500, index=idx)
    pd.testing.assert_series_equal(transform(s, "yoy", freq), s.pct_change(n) * 100)


def test_daily_yoy_keeps_positional_behaviour():
    idx = pd.bdate_range("2024-01-01", periods=300)
    s = pd.Series(np.linspace(100, 130, len(idx)), index=idx)
    pd.testing.assert_series_equal(transform(s, "yoy", "D"), s.pct_change(252) * 100)


# ---------------------------------------------------------------- اللقطة
def test_snapshot_marks_october_unavailable_and_september_as_last(cpi):
    row = snapshot({"CPIAUCSL": cpi}).set_index("id").loc["CPIAUCSL"]
    assert row["as_of"] == pd.Timestamp("2026-09-01") and row["source_as_of"] == pd.Timestamp("2026-10-01")
    assert row["last"] == pytest.approx(3.71725, abs=1e-5)
    assert row["prev"] == pytest.approx(_yoy(cpi, "2026-08-01", "2025-08-01"))
    assert row["unavailable"] == {
        "date": "2026-10-01", "label": "أكتوبر 2026", "base": "2025-10-01", "base_label": "أكتوبر 2025",
        "reason": "التغير السنوي لقراءة أكتوبر 2026 غير متاح: شهر المقارنة (أكتوبر 2025) غير منشور في المصدر"}


def test_no_marker_without_gap_or_for_non_yoy():
    idx = pd.date_range("2015-01-01", "2026-10-01", freq="MS")
    full = pd.Series(1.002 ** np.arange(len(idx)) * 100, index=idx)
    gap = full.drop(pd.Timestamp("2025-10-01"))
    rows = snapshot({"PCEPILFE": full, "UNRATE": gap}).set_index("id")
    assert rows.loc["PCEPILFE", "unavailable"] is None
    assert rows.loc["UNRATE", "unavailable"] is None              # level: لا تغير سنوي ولا علامة
    assert rows.loc["UNRATE", "as_of"] == pd.Timestamp("2026-10-01")


# ---------------------------------------------------------------- data.json: الجلب منفصل عن التغير السنوي
@pytest.mark.parametrize("sid,last,prev", [("CPIAUCSL", 3.7173, 3.353), ("CPILFESL", 2.5658, 2.4462)])
def test_payload_september_last_with_date_and_age_october_unavailable(sid, last, prev):
    p = cpi_payload()
    i = by_id(p)[sid]
    assert (i["last"], i["prev"], i["as_of"], i["age_days"]) == (last, prev, "2026-09-01", 72)
    assert i["state"] == "ok" and i["issues"] == []                  # الجلب سليم: لا خطأ جلب
    assert i["source_as_of"] == "2026-10-01"                         # المصدر نشر أكتوبر فعلاً
    assert i["unavailable"]["label"] == "أكتوبر 2026" and i["unavailable"]["base_label"] == "أكتوبر 2025"
    dates = [d for d, _ in i["points"]]
    assert dates[-1] == "2026-09-01" and "2026-10-01" not in dates and "2025-10-01" not in dates
    assert not any(w in json.dumps(i) for w in WRONG)
    note = [w for w in p["status"]["warnings"] if f"({sid})" in w]
    assert note == [f"{i['name_ar']} ({sid}): التغير السنوي لقراءة أكتوبر 2026 غير متاح: شهر المقارنة "
                    "(أكتوبر 2025) غير منشور في المصدر. الجلب سليم، وآخر قيمة من المصدر بتاريخ 2026-10-01"]
    assert p["status"]["ok"] == 2


def test_fetch_failure_stays_a_fetch_failure():
    st = {"CPIAUCSL": {"state": "error", "issues": ["فشل الجلب: HTTP 503", "تُعرض آخر نسخة سليمة محفوظة"]}}
    i = by_id(cpi_payload(st))["CPIAUCSL"]
    assert i["state"] == "error" and i["issues"] == st["CPIAUCSL"]["issues"]   # لا يُخلط غياب YoY بأخطاء الجلب
    assert i["unavailable"]["label"] == "أكتوبر 2026"
    w = [x for x in cpi_payload(st)["status"]["warnings"] if "(CPIAUCSL)" in x]
    assert len(w) == 2 and "فشل الجلب" in w[0] and "الجلب سليم" not in w[1]


def test_staleness_follows_the_source_not_the_missing_yoy():
    """عمر القراءة المعروضة من سبتمبر؛ التأخر (stale) من آخر ما نشره المصدر (أكتوبر)."""
    i = by_id(cpi_payload(now=datetime(2026, 12, 15, 21, tzinfo=timezone.utc)))["CPIAUCSL"]
    assert i["age_days"] == 105 and i["state"] == "ok"               # المصدر عمره 75 يوماً < 80
    i = by_id(cpi_payload(now=datetime(2027, 1, 5, 21, tzinfo=timezone.utc)))["CPIAUCSL"]
    assert i["age_days"] == 126 and i["state"] == "stale" and i["issues"] == ["آخر قيمة عمرها 96 يوماً"]


def test_kpi_carries_marker_for_a_yoy_kpi():
    idx = pd.date_range("2015-01-01", "2026-10-01", freq="MS").drop(pd.Timestamp("2025-10-01"))
    s = pd.Series(1.002 ** np.arange(len(idx)) * 100, index=idx)
    k = {x["id"]: x for x in kpis(snapshot({"PCEPILFE": s}))}["PCEPILFE"]
    assert k["as_of"] == "2026-09-01" and k["unavailable"]["label"] == "أكتوبر 2026"


# ---------------------------------------------------------------- ما يصل إلى Claude
def test_claude_context_gets_september_and_the_unavailable_october():
    ctx = ai_report.context(cpi_payload())
    c = {i["id"]: i for i in ctx["indicators"]}["CPIAUCSL"]
    assert (c["last"], c["as_of"], c["age_days"], c["state"], c["issues"]) == (3.7173, "2026-09-01", 72, "ok", [])
    assert c["source_as_of"] == "2026-10-01" and "غير متاح" in c["unavailable"]["reason"]
    assert c["recent"][-1][0] == "2026-09-01"
    assert any("أكتوبر 2026 غير متاح" in w for w in ctx["data_warnings"])
    text = json.dumps(ctx, ensure_ascii=False)
    assert not any(w in text for w in WRONG)


def test_claude_request_text_is_built_from_the_corrected_payload():
    """النص الفعلي الذي يُرسل إلى النموذج (بلا اتصال: المستدعي المزيف يلتقطه ثم يوقف التنفيذ)."""
    seen = {}

    class Captured(Exception):
        pass

    def caller(user, system, model, effort):
        seen["user"] = user
        raise Captured

    with pytest.raises(Captured):
        ai_report.write_report(cpi_payload(), load_config("config.json"), now_utc=NOW, caller=caller)
    sent = json.loads(seen["user"].split("Platform data:\n", 1)[1])
    c = {i["id"]: i for i in sent["indicators"]}
    assert c["CPIAUCSL"]["last"] == 3.7173 and c["CPILFESL"]["last"] == 2.5658
    assert c["CPIAUCSL"]["unavailable"]["label"] == "أكتوبر 2026"
    assert not any(w in seen["user"] for w in WRONG)


# ---------------------------------------------------------------- الواجهة (قالب الصفحة؛ العرض الفعلي في tests_ui)
def test_page_template_escapes_every_new_field():
    html = open("index.html", encoding="utf-8").read()
    for frag in ('title="${esc(i.unavailable.reason)}">${esc(i.unavailable.label)}: غير متاح',
                 'title="${esc(k.unavailable.reason)}">· ${esc(k.unavailable.label)}: غير متاح',
                 'آخر قيمة من المصدر <bdi>${esc(i.source_as_of)}</bdi>؛ ${esc(i.unavailable.reason)}'):
        assert frag in html, frag


# ---------------------------------------------------------------- صيغة التاريخ داخل الشهر
def _shift_dates(s: pd.Series, how: str) -> pd.Series:
    """التواريخ نفسها شهرياً لكن بيوم مختلف: نهاية الشهر أو منتصفه. الكود السابق كان يعود هنا إلى عدّ الصفوف."""
    out = s.copy()
    out.index = s.index + (pd.offsets.MonthEnd(0) if how == "end" else pd.Timedelta(days=14))
    return out


@pytest.mark.parametrize("how,sep_date,age", [("end", "2026-09-30", 43), ("mid", "2026-09-15", 58)])
def test_october_2026_unavailable_when_dates_are_not_month_start(how, sep_date, age):
    cpi, core = _shift_dates(series(CPI, "CPIAUCSL"), how), _shift_dates(series(CORE, "CPILFESL"), how)
    t = transform_indicator(BY_ID["CPIAUCSL"], cpi)
    assert cpi.index[-1] not in t.index                                  # أكتوبر 2026 لا يُحسب
    assert t.index[-1] == pd.Timestamp(sep_date) and t.iloc[-1] == pytest.approx(3.71725, abs=1e-5)
    p = build_payload({"CPIAUCSL": cpi, "CPILFESL": core}, {}, load_config("config.json"), now=NOW)
    for sid, last in (("CPIAUCSL", 3.7173), ("CPILFESL", 2.5658)):
        i = by_id(p)[sid]
        assert (i["last"], i["as_of"], i["age_days"], i["state"], i["issues"]) == (last, sep_date, age, "ok", [])
        assert i["unavailable"]["label"] == "أكتوبر 2026" and i["unavailable"]["base_label"] == "أكتوبر 2025"
        assert i["unavailable"]["reason"].endswith("شهر المقارنة (أكتوبر 2025) غير منشور في المصدر")
        assert not any(w in json.dumps(i) for w in WRONG)


def test_quarter_end_dates_use_the_same_quarter_a_year_earlier():
    idx = pd.date_range("2018-03-31", "2023-12-31", freq="QE").drop(pd.Timestamp("2021-09-30"))
    s = pd.Series(1.005 ** np.arange(len(idx)) * 100, index=idx)
    t = transform(s, "yoy", "Q")
    assert pd.isna(t["2022-09-30"])
    assert t["2022-12-31"] == pytest.approx((s["2022-12-31"] / s["2021-12-31"] - 1) * 100)


def test_two_readings_in_one_month_take_the_last_as_base():
    s = pd.Series([100.0, 104.0, 110.0], index=pd.DatetimeIndex(["2024-01-10", "2024-01-31", "2025-01-31"]))
    t = transform(s, "yoy", "M")
    assert pd.isna(t["2024-01-10"]) and pd.isna(t["2024-01-31"])
    assert t["2025-01-31"] == pytest.approx((110 / 104 - 1) * 100)
