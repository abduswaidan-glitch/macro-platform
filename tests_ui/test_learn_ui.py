"""التعليم في الصفحة: الفصول والمداخل من content/learn.json، ووسم المفاهيم غير المستخدمة، ورابط من كل مؤشر إلى مدخله،
وكل نص عبر esc()."""
import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("nav_ui_case", ROOT / "tests_ui" / "test_navigation_ui.py")
nav = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nav)
LEARN = json.loads((ROOT / "content" / "learn.json").read_text(encoding="utf-8"))


def open_learn(ui, learn=LEARN, hash_=""):
    files = {"news.json": nav.news(), "data/ai_history.json": []}
    if learn is not None:
        files["content/learn.json"] = learn
    page = ui.open(nav.healthy(), files, now=nav.WINTER)
    if hash_:
        page.evaluate(f"location.hash = '{hash_}'")
    else:
        page.click('button.tab[data-k="learn"]')
    page.wait_for_selector("#p-learn .sec-head")
    return page


def test_chapters_and_entries_render_with_the_concept_label(ui):
    page = open_learn(ui)
    p = page.locator("#p-learn")
    assert p.locator(".chapter").count() == 3 and "كيف تعمل المنصة" in p.inner_text()
    assert p.locator("details.learn").count() == len(LEARN["entries"])
    concept = p.locator("details.learn.concept")
    assert concept.count() == 3 and all(
        "مفهوم تعليمي، غير مستخدم حاليًا في بيانات المنصة" in t for t in concept.locator("summary").all_inner_texts())
    assert p.locator("details.learn:not(.concept) .concept-label").count() == 0


def test_every_indicator_row_links_to_its_entry_and_opens_it(ui):
    page = open_learn(ui)
    page.click('button.tab[data-k="cat-labor"]')
    page.locator('#p-cat-labor a.learn-link[href="#learn-PAYEMS"]').click()
    page.wait_for_selector("#p-learn:not([hidden])")
    entry = page.locator("#learn-PAYEMS")
    assert entry.get_attribute("open") is not None and entry.is_visible()
    assert "الحكم من شهر واحد وتجاهل المراجعات" in entry.inner_text()
    assert page.locator("#kpis a.learn-link").count() == page.locator("#kpis .kpi").count()


def test_a_direct_link_opens_the_entry_on_load(ui):
    page = open_learn(ui, hash_="learn-DFII10")
    page.wait_for_selector("#learn-DFII10[open]")
    assert page.locator("#p-learn").is_visible()


def test_missing_content_says_so_and_text_is_escaped(ui):
    assert "تعذّر تحميل المحتوى التعليمي." in open_learn(ui, learn=None).locator("#p-learn").inner_text()
    bad = "<img src=x onerror=alert(1)>"
    evil = copy.deepcopy(LEARN)
    evil["entries"][0]["what"] = bad
    evil["chapters"][0]["title"] = bad
    p = open_learn(ui, learn=evil).locator("#p-learn")
    assert p.locator("img").count() == 0 and bad in p.inner_text()
