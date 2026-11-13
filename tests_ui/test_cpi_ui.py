"""ما يظهر فعلاً في الصفحة لتصحيح CPI: index.html الحقيقي مع data.json مبني من قيم CPI المجمّدة، في Chromium.

خارج tests/ لأن deploy.yml لا يثبّت متصفحاً؛ يشغّله pr-tests.yml على كل طلب دمج. محلياً:
    pip install playwright==1.63.0 && python -m playwright install chromium
    python -m pytest -q tests_ui
"""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from macro_platform.build import build_payload, load_config

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cpi_case", ROOT / "tests" / "test_cpi_yoy_gap.py")
case = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(case)

NOW = datetime(2026, 11, 12, 21, 0, tzinfo=timezone.utc)
REASON = "التغير السنوي لقراءة أكتوبر 2026 غير متاح: شهر المقارنة (أكتوبر 2025) غير منشور في المصدر"


def _payload():
    idx = pd.date_range("2015-01-01", "2026-10-01", freq="MS").drop(pd.Timestamp("2025-10-01"))
    pce = pd.Series(1.002 ** np.arange(len(idx)) * 100, index=idx, name="PCEPILFE")   # KPI شهري YoY بالفجوة نفسها
    full = pd.date_range("2015-01-01", "2026-10-01", freq="MS")
    series = {"CPIAUCSL": case.series(case.CPI, "CPIAUCSL"), "CPILFESL": case.series(case.CORE, "CPILFESL"),
              "PCEPILFE": pce,
              # مدخلا المربع الآخران: growth_inflation_quadrant يتعطل إن وُجد PCE وحده (خلل قائم خارج هذا التصحيح)
              "INDPRO": pd.Series(np.linspace(100, 104, len(full)), index=full, name="INDPRO"),
              "PAYEMS": pd.Series(np.linspace(150000, 160000, len(full)), index=full, name="PAYEMS"),
              # KPI بـgood_up=None كما في الإنتاج: بدونه يصبح عمود good_up منطقياً فيخرج numpy.bool لا يقبله JSON
              "DGS10": pd.Series(4.5, index=pd.bdate_range("2025-01-01", "2026-11-11"), name="DGS10")}
    return build_payload(series, {}, load_config(ROOT / "config.json"), now=NOW)


def _row(page, panel, sid):
    return page.locator(f"{panel} tbody tr", has_text=sid).first


def test_inflation_table_shows_september_and_october_unavailable(ui):
    page = ui.open(_payload())
    page.click('button.tab[data-k="cat-inflation"]')
    for sid, value in (("CPIAUCSL", "3.72%"), ("CPILFESL", "2.57%")):
        row = _row(page, "#p-cat-inflation", sid)
        text = row.inner_text()
        assert value in text and "2026-09-01" in text and "أكتوبر 2026: غير متاح" in text, text
        assert "4.08" not in text and "2.87" not in text and "2026-10-01" not in text
        assert row.locator(".yoy-na").get_attribute("title") == REASON


def test_status_table_separates_fetch_state_from_missing_yoy(ui):
    page = ui.open(_payload())
    page.click('button.tab[data-k="method"]')
    cells = _row(page, "#p-method", "CPIAUCSL").locator("td").all_inner_texts()
    # المؤشر | FRED | التكرار | آخر قيمة بتاريخ | العمر | الحالة
    assert cells[3] == "2026-09-01" and cells[4] == "72"
    assert cells[5].startswith("سليم")
    assert "آخر قيمة من المصدر 2026-10-01" in cells[5] and REASON in cells[5]
    # السبب الطويل يلتف داخل الخلية ولا يدفع الجدول إلى تمرير أفقي (عرض 1280 الافتراضي في Playwright)
    assert page.evaluate("(() => { const w = document.querySelector('#p-method .tbl-wrap');"
                         " return w.scrollWidth <= w.clientWidth; })()")


def test_overview_warning_and_kpi_card(ui):
    page = ui.open(_payload())
    page.click('button.tab[data-k="overview"]')
    warn = page.locator("#p-overview .warn-box").inner_text()
    assert REASON in warn and "الجلب سليم، وآخر قيمة من المصدر بتاريخ 2026-10-01" in warn
    kpi = page.locator("#kpis .kpi", has_text="PCE").inner_text()
    assert "2026-09-01" in kpi and "أكتوبر 2026: غير متاح" in kpi


def test_marker_text_is_escaped(ui):
    payload = _payload()
    evil = '<img src=x onerror="window.__xss=1">'
    for i in payload["indicators"]:
        if i["id"] == "CPIAUCSL":
            i["unavailable"] = {**i["unavailable"], "label": evil, "reason": evil}
    page = ui.open(payload)
    page.click('button.tab[data-k="cat-inflation"]')
    assert evil in _row(page, "#p-cat-inflation", "CPIAUCSL").inner_text()
    page.click('button.tab[data-k="method"]')
    assert page.evaluate("window.__xss") is None


def test_browser_monitor_catches_and_blocks_an_external_request(ui):
    """ضابط للمراقبة نفسها: تنقّل إلى مضيف خارجي يُسجَّل ويُلغى، فلا يصل المتصفح إليه."""
    page = ui.open(_payload())
    with pytest.raises(Exception):
        page.goto("https://example.com/", wait_until="commit")
    assert any(u.startswith("https://example.com/") for u in ui.external)
    ui.external[:] = [u for u in ui.external if not u.startswith("https://example.com/")]


def test_page_only_tries_the_declared_font_hosts(ui):
    ui.open(_payload())
    from urllib.parse import urlsplit
    assert {urlsplit(u).hostname for u in ui.external} <= {"fonts.googleapis.com", "fonts.gstatic.com"}
