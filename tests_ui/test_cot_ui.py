"""تموضع الدولار (COT) في الصفحة: صفوفه في تبويب «الأوضاع المالية» بعدد العقود، ورسمه يسمّي مصدره
«CFTC عبر Tradingster» لا FRED، والتذييل يذكر المصدر، وقسم التعليم يشرحه."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("nav_ui_cot", ROOT / "tests_ui" / "test_navigation_ui.py")
nav = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nav)


def test_cot_rows_name_their_source_and_the_rest_stay_fred(ui):
    page = nav.open_page(ui, nav.healthy())
    page.click('button.tab[data-k="cat-financial"]')
    panel = "#p-cat-financial"
    for sid in ("COT_DX_NC", "COT_DX_LEV", "COT_DX_AM"):
        assert page.locator(f"{panel} tbody tr[data-id='{sid}']").count() == 1
    page.click(f"{panel} tbody tr[data-id='COT_DX_NC']")
    assert page.locator(f"{panel} .chart-card p").inner_text().endswith("· CFTC عبر Tradingster: COT_DX_NC")
    page.click(f"{panel} tbody tr[data-id='DTWEXBGS']")
    assert page.locator(f"{panel} .chart-card p").inner_text().endswith("· FRED: DTWEXBGS")
    assert "CFTC Commitments of Traders عبر Tradingster" in page.locator("footer").inner_text()


def test_each_cot_row_links_to_its_learn_entry(ui):
    learn = json.loads((ROOT / "content" / "learn.json").read_text(encoding="utf-8"))
    page = ui.open(nav.healthy(), {"news.json": nav.news(), "data/ai_history.json": [], "content/learn.json": learn},
                   now=nav.WINTER)
    page.click('button.tab[data-k="cat-financial"]')
    page.locator('#p-cat-financial a.learn-link[href="#learn-COT_DX_LEV"]').click()
    page.wait_for_selector("#learn-COT_DX_LEV[open]")
    assert "المال السريع" in page.locator("#learn-COT_DX_LEV").inner_text()
