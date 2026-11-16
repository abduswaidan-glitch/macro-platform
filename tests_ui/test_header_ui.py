"""شارة الرأس ووقته من آخر جلب حقيقي للبيانات (data.fetched)، لا من وقت بناء الصفحة الذي يتجدد كل 10 دقائق."""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cpi_ui_case_hdr", ROOT / "tests_ui" / "test_cpi_ui.py")
cpi_ui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cpi_ui)

NOW = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("fetched,cls,text", [
    ("2026-11-16T13:41:00Z", "live", "محدّثة"),                          # جلب اليوم
    ("2026-11-13T21:17:00Z", "live slow", "آخر جلب منذ أكثر من يوم"),    # الجمعة مساءً، والآن الاثنين: نهاية أسبوع عادية
    ("2026-11-10T21:17:00Z", "live late", "البيانات متأخرة"),            # ستة أيام بلا جلب
    (None, "live late", "وقت الجلب غير معروف"),
])
def test_badge_follows_the_last_fetch_even_when_the_page_was_just_built(ui, fetched, cls, text):
    payload = cpi_ui._payload()
    payload["generated"] = "2026-11-16T14:55:00Z"      # البناء قبل خمس دقائق في كل الحالات
    payload["fetched"] = fetched
    page = ui.open(payload, now=NOW)
    assert page.locator("#live").get_attribute("class") == cls
    assert page.locator("#liveTxt").inner_text() == text
    upd = page.locator("#updated").inner_text()
    assert upd.startswith("آخر جلب للبيانات: ") and ("غير معروف" in upd) == (fetched is None)


def test_methodology_names_both_sources(ui):
    page = ui.open(cpi_ui._payload())
    page.evaluate("location.hash = '#method'")
    box = page.locator("#p-method").inner_text()
    assert "كل السلاسل من FRED" not in box and "Tradingster" in box
