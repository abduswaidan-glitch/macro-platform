"""الزخم والمراجعات التي يحسبها الكود تظهر بجانب المؤشر، والنص يمر عبر esc()."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cpi_ui_case", ROOT / "tests_ui" / "test_cpi_ui.py")
cpi_ui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cpi_ui)


def test_momentum_and_revision_lines_are_shown_next_to_the_indicator(ui):
    payload = cpi_ui._payload()
    for i in payload["indicators"]:
        if i["id"] == "PAYEMS":
            i["revisions"] = [{"detected_utc": "2026-11-12T00:00:00Z", "date": "2026-09-01", "old": 159200.0, "new": 159150.0}]
        if i["id"] == "CES0500000003":
            i["revisions"] = [{"detected_utc": "2026-11-12T00:00:00Z", "bulk": True, "count": 14,
                               "range": ["2025-01-01", "2026-02-01"], "largest": {}}]
    page = ui.open(payload)
    page.click('button.tab[data-k="cat-inflation"]')
    core = cpi_ui._row(page, "#p-cat-inflation", "CPILFESL").inner_text()
    assert "الزخم (" in core and "شهري" in core and "3 أشهر سنوياً" in core
    page.click('button.tab[data-k="cat-labor"]')
    assert "رُوجعت قراءة 2026-09-01: 159200 ← 159150" in cpi_ui._row(page, "#p-cat-labor", "PAYEMS").inner_text()
    assert "مراجعة جماعية: 14 قراءات" in cpi_ui._row(page, "#p-cat-labor", "CES0500000003").inner_text()


def test_revision_text_is_escaped(ui):
    payload = cpi_ui._payload()
    evil = '<img src=x onerror="window.__xss=1">'
    for i in payload["indicators"]:
        if i["id"] == "PAYEMS":
            i["revisions"] = [{"detected_utc": "x", "date": evil, "old": 1, "new": 2}]
    page = ui.open(payload)
    page.click('button.tab[data-k="cat-labor"]')
    assert page.locator("#p-cat-labor img").count() == 0 and page.evaluate("window.__xss") is None
