"""تموضع الدولار (COT) من صفحات Tradingster العامة (قرار Abdu، 15 نوفمبر 2026).

بنية الصفحة في هذه الاختبارات منسوخة من صفحتي Tradingster الحقيقيتين في 15 نوفمبر 2026 (Legacy وTFF لرمز 098662):
بيانات الرسم (var dataNet) تحمل التاريخ الأسبوعي كاملاً، و«AS OF» في الرأس، وجدول المراكز. أرقام آخر أسبوع هنا هي
أرقام CFTC لمراكز 3 نوفمبر 2026: صافي Non-Commercial ‏+14,190 (33,054 − 18,864)، وLeveraged Funds ‏+3,311،
وAsset Managers ‏+16,820. قُرئت الصفحتان الحقيقيتان بالقارئ نفسه فأعطتا 519 و520 أسبوعاً منذ 22 نوفمبر 2016.
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from macro_platform import build, cot
from macro_platform.build import build_payload, fetch_all, load_config, save_raw
from macro_platform.catalog import BY_ID
from macro_platform.collectors import compact, section_indicators
from macro_platform.demo import demo_series

CFG = load_config("config.json")
WEEKS = ["2026-10-20", "2026-10-27", "2026-11-03"]


def page(kind, rows=None, asof="2026-11-03", nc_long=33054, nc_short=18864, market=cot.MARKET, code=cot.CODE):
    if rows is None:
        rows = ([{"Commercial": -17014, "NonCommercial": 15044, "NonRept": 1970},
                 {"Commercial": -16088, "NonCommercial": 14121, "NonRept": 1967},
                 {"Commercial": -16414, "NonCommercial": 14190, "NonRept": 2224}] if kind == "legacy" else
                [{"Dealer": -19979, "Asset_Mgr": 17020, "Lev_Money": 3183, "Other_Rept": -2291, "NonRept": 1970},
                 {"Dealer": -19547, "Asset_Mgr": 16713, "Lev_Money": 3158, "Other_Rept": -2291, "NonRept": 1967},
                 {"Dealer": -19814, "Asset_Mgr": 16820, "Lev_Money": 3311, "Other_Rept": -2541, "NonRept": 2224}])
    data = "".join(f"\t\t{{ date: new Date('{d}'), " + ", ".join(f"{k}: {v}" for k, v in r.items())
                   + ", open: 101.7300, high: 101.9400, low: 101.6200, close: 101.8700 },\n" for d, r in zip(WEEKS, rows))
    table = (f"""<tbody>
			<tr>
				<td colspan="5" class="text-left">(U.S. DOLLAR INDEX X $1000)</td>
				<td colspan="4" class="text-right">Open Interest: <span class="number">49,091</span></td>
			</tr>

			<tr>
				<td class="number">{nc_long:,}</td>
				<td class="number">{nc_short:,}</td>
				<td class="number">1,261</td>
			</tr>""" if kind == "legacy" else "")
    return f"""<!DOCTYPE html><html><head><title>COT Report: USD INDEX</title></head><body>
<strong>{market}</strong><br /><strong>Code-{code}</strong>
<div class="mb-2"><strong>AS OF: {asof} </strong></div>
{table}
<script>
	var dataNet = [
{data}			];
	var dataLong = [];
</script></body></html>"""


def getter(pages, calls=None):
    def get(url):
        kind = "legacy" if "legacy" in url else "tff"
        if calls is not None:
            calls.append(kind)
        return pages[kind]
    return get


@pytest.fixture(autouse=True)
def fresh_cache():
    cot.clear_cache()
    yield
    cot.clear_cache()


def test_the_three_series_are_read_from_the_two_pages_with_one_request_each():
    calls = []
    get = getter({"legacy": page("legacy"), "tff": page("tff")}, calls)
    got = {sid: cot.fetch_series(sid, "2000-01-01", getter=get) for sid in cot.SERIES}
    assert got["COT_DX_NC"].iloc[-1] == 14190 and got["COT_DX_LEV"].iloc[-1] == 3311 and got["COT_DX_AM"].iloc[-1] == 16820
    assert list(got["COT_DX_NC"].index.strftime("%Y-%m-%d")) == WEEKS and got["COT_DX_NC"].name == "COT_DX_NC"
    assert sorted(calls) == ["legacy", "tff"]                               # صفحة واحدة لكل تقرير في التشغيل
    assert cot.fetch_series("COT_DX_NC", "2026-10-27", getter=get).index[0] == pd.Timestamp("2026-10-27")


@pytest.mark.parametrize("bad, why", [
    (dict(asof="2026-10-27"), "لا يطابق AS OF"),                            # بيانات الرسم أحدث أو أقدم من رأس الصفحة
    (dict(nc_short=18000), "صافي الجدول"),                                 # الجدول والرسم مختلفان
    (dict(market="EURO FX - CHICAGO MERCANTILE EXCHANGE"), "ليست لعقد"),   # صفحة عقد آخر
    (dict(rows=[{"Commercial": 1}] * 3), "حقول ناقصة"),                    # تغيّر أسماء الحقول
])
def test_a_page_that_does_not_check_out_is_rejected(bad, why):
    with pytest.raises(cot.CotError, match=why):
        cot.parse_page(page("legacy", **bad), "legacy")


def test_a_redesigned_page_without_chart_data_is_rejected():
    with pytest.raises(cot.CotError, match="dataNet"):
        cot.parse_page(page("tff").replace("var dataNet", "var chartRows"), "tff")


def test_the_page_is_requested_only_when_a_newer_release_can_exist():
    tue = pd.Timestamp("2026-11-03")
    assert not cot.due(tue, datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc))     # الجمعة قبل 15:30 نيويورك
    assert cot.due(tue, datetime(2026, 11, 13, 19, 30, tzinfo=timezone.utc))
    assert cot.due(tue, datetime(2026, 11, 16, 13, 41, tzinfo=timezone.utc))         # تأخير العطلة: يُطلب حتى يظهر


def _weekly(last: pd.Timestamp, n=300):
    return pd.Series(range(n), index=pd.date_range(end=last, periods=n, freq="7D"), dtype=float)


def test_a_fresh_saved_copy_is_used_without_a_request_and_an_old_one_is_refreshed(tmp_path):
    today = pd.Timestamp(datetime.now(timezone.utc).date())
    save_raw(tmp_path, "COT_DX_NC", _weekly(today - pd.Timedelta(days=2)))
    save_raw(tmp_path, "COT_DX_LEV", _weekly(today - pd.Timedelta(days=20)))
    asked = []

    def fetcher(sid, start):
        asked.append(sid)
        return _weekly(today - pd.Timedelta(days=6), 301)
    series, st = fetch_all(tmp_path, "2000-01-01", fetcher=fetcher, log=lambda *a: None,
                           indicators=(BY_ID["COT_DX_NC"], BY_ID["COT_DX_LEV"]))
    assert asked == ["COT_DX_LEV"] and st["COT_DX_NC"] == {"state": "ok", "issues": []}
    assert series["COT_DX_NC"].index[-1] == today - pd.Timedelta(days=2) and st["COT_DX_LEV"]["state"] == "ok"


def test_cot_goes_to_tradingster_and_everything_else_to_fred(monkeypatch):
    seen = []
    monkeypatch.setattr(build.cot, "fetch_series", lambda sid, start: seen.append(("cot", sid)))
    monkeypatch.setattr(build, "fetch_series", lambda sid, start: seen.append(("fred", sid)))
    build.fetch_source("COT_DX_AM", "2000-01-01")
    build.fetch_source("DTWEXBGS", "2000-01-01")
    assert seen == [("cot", "COT_DX_AM"), ("fred", "DTWEXBGS")]


def test_tradingster_down_leaves_the_financial_section_working_on_the_last_good_copy(tmp_path):
    old = _weekly(pd.Timestamp("2026-10-20"))
    save_raw(tmp_path, "COT_DX_NC", old)
    fred = pd.Series(range(400), index=pd.bdate_range("2025-01-01", periods=400), dtype=float)

    def fetcher(sid, start):
        if sid.startswith("COT_"):
            raise cot.CotError("تعذّر جلب https://www.tradingster.com/...: HTTPError: 403")
        return fred * 1.0
    series, st = fetch_all(tmp_path, "2000-01-01", fetcher=fetcher, log=lambda *a: None,
                           indicators=section_indicators("financial"))
    assert {k for k, v in st.items() if v["state"] == "ok"} == {"VIXCLS", "DTWEXBGS", "DCOILWTICO", "BAMLH0A0HYM2",
                                                              "M2SL", "WALCL"}
    assert st["COT_DX_NC"]["state"] == "error" and "تُعرض آخر نسخة سليمة محفوظة" in st["COT_DX_NC"]["issues"]
    assert series["COT_DX_NC"].equals(old) and "COT_DX_LEV" not in series      # لا نسخة محفوظة له بعد


@pytest.mark.parametrize("age, state", [(13, "ok"), (17, "ok"), (18, "stale")])
def test_a_holiday_delay_is_not_stale_but_a_missing_release_is(age, state):
    now = datetime(2026, 11, 16, 13, 41, tzinfo=timezone.utc)
    s = demo_series(start="2018-01-01", end="2026-06-30")
    s["COT_DX_NC"] = _weekly(pd.Timestamp(now.date()) - pd.Timedelta(days=age))
    e = next(i for i in build_payload(s, {}, CFG, now=now)["indicators"] if i["id"] == "COT_DX_NC")
    assert e["state"] == state and e["source"] == "cot" and e["unit"] == "عقد"
    assert compact(e)["source"] == "cot"                                     # يصل المصدر إلى ملف القسم للنموذج


def test_the_model_is_told_what_cot_is_and_is_not():
    from macro_platform.ai_report import SYSTEM
    assert "CFTC Commitments of Traders figures read from Tradingster" in SYSTEM
    assert "positioning, not economic data" in SYSTEM and "never as a scenario check" in SYSTEM
    assert "Nasdaq" not in SYSTEM
