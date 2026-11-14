"""PR 5 — التعليم: محتوى ثابت مراجَع (content/learn.json)، بلا استدعاء نموذج يومي.

كل مؤشر في الكتالوج له مدخل، وكل مدخل in_platform يطابق مؤشراً موجوداً، وكل concept_only يحمل الوسم «مفهوم تعليمي،
غير مستخدم حاليًا في بيانات المنصة»، والحقول الإلزامية غير فارغة، وفصل المنهجية موجود، والملف يُنشر مع الموقع.
"""
import json
from pathlib import Path

import fetch_news
from macro_platform.catalog import BY_ID, INDICATORS, INTERNAL_IDS

ROOT = Path(__file__).resolve().parent.parent
L = json.loads((ROOT / "content" / "learn.json").read_text(encoding="utf-8"))
KEYS = ("title", "what", "measures", "why", "rise", "fall", "compare", "timing", "relation", "mistakes", "example")
LABEL = "مفهوم تعليمي، غير مستخدم حاليًا في بيانات المنصة"
SECTIONS = {"growth", "inflation", "labor", "fed_rates", "financial", "gates"}


def test_every_indicator_has_an_entry_and_every_platform_entry_is_real():
    inside = {e["id"] for e in L["entries"] if e["status"] == "in_platform"}
    assert inside == {i.id for i in INDICATORS}
    assert len(inside) == len([e for e in L["entries"] if e["status"] == "in_platform"])     # بلا تكرار


def test_concept_only_entries_are_labelled_and_not_in_the_data():
    concept = [e for e in L["entries"] if e["status"] == "concept_only"]
    assert concept and L["label_concept_only"] == LABEL
    for e in concept:
        assert e["label"] == LABEL and e["id"] not in BY_ID and "غير موجود في بيانات المنصة" in e["measures"]
    assert {e["status"] for e in L["entries"]} == {"in_platform", "concept_only"}


def test_mandatory_fields_are_filled_and_sections_exist():
    for e in L["entries"]:
        for k in KEYS:
            assert isinstance(e.get(k), str) and e[k].strip(), (e["id"], k)
        assert e["section"] in SECTIONS, e["id"]
        assert e["example"].startswith("مثال توضيحي"), e["id"]          # لا رقم يُقدَّم كأنه بيانات حقيقية
    for e in L["entries"]:
        if e["status"] == "in_platform" and e["id"] not in INTERNAL_IDS:
            assert e["section"] == BY_ID[e["id"]].category, e["id"]      # القسم نفسه في الصفحة
        elif e["id"] in INTERNAL_IDS:
            assert e["section"] == "gates" and "لا تُنشر مستوياته" in e["measures"]


def test_chapters_methodology_measurement_and_gates():
    ch = {c["id"]: " ".join(c["body"]) for c in L["chapters"]}
    assert set(ch) == {"platform", "measurement", "gates"}
    assert "FRED" in ch["platform"] and "09:00" in ch["platform"] and "12:00" in ch["platform"]
    m = ch["measurement"]
    # الشرح يسمّي العبارة الصحيحة، وينفي الممنوعة صراحة، ويقول إن الشرط ليس إثباتاً للسيناريو
    assert "تحقق شرط قياس السيناريو" in m and "لا «تحقق السيناريو»" in m and "وليس إثباتاً لتحقق السيناريو" in m
    assert "0.667" in m and "0.25" in m and "30" in m and "لا دلالة إحصائية" in m
    assert "مماثل إلى حد كبير" in ch["gates"] and "−2" in ch["gates"] and "+2" in ch["gates"]


def test_the_content_is_published_with_the_site(tmp_path):
    assert "content/learn.json" in fetch_news.SITE_FILES
    fetch_news.build_site(tmp_path)
    assert json.loads((tmp_path / "content" / "learn.json").read_text(encoding="utf-8")) == L
