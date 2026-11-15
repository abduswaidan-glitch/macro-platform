"""جامعو الأقسام والعزل: لا كود واحد يتولى كل شيء، ولا خلل في جزء واحد يُسقط المنصة (قرار Abdu، 15 نوفمبر 2026).

الحالة المقيسة قبل هذا التغيير: إن توفر PCE وتعذّر جلب الإنتاج الصناعي والوظائف، انهار بناء المنصة كله
(KeyError: 'indpro_yoy' في حساب «المربع»)، فلا data.json ولا تقرير.
"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from macro_platform import build, collectors
from macro_platform.build import build_payload, indicator_entries, load_config
from macro_platform.catalog import BY_ID
from macro_platform.demo import demo_series

CFG = load_config("config.json")
NOW = datetime(2026, 11, 16, 13, 41, tzinfo=timezone.utc)        # 08:41 نيويورك


def test_one_missing_input_no_longer_takes_the_whole_platform_down():
    idx = pd.date_range("2015-01-01", "2026-10-01", freq="MS")
    p = build_payload({"PCEPILFE": pd.Series(1.002 ** np.arange(len(idx)) * 100, index=idx)}, {}, CFG, now=NOW)
    assert p["quadrant"] is None and next(i for i in p["indicators"] if i["id"] == "PCEPILFE")["state"] == "ok"


def test_a_failing_computed_part_is_a_warning_and_the_rest_is_built(monkeypatch):
    monkeypatch.setattr(build, "signals", lambda s: 1 / 0)
    monkeypatch.setattr(build, "nq_gates", lambda s, c: 1 / 0)
    p = build_payload(demo_series(start="2018-01-01", end="2026-06-30"), {}, CFG, now=NOW)
    assert p["signals"] == [] and p["gates"] == [] and p["quadrant"] is not None
    assert "تعذّر حساب الإشارات: ZeroDivisionError" in p["status"]["warnings"]
    assert "تعذّر حساب بوابات NQ: ZeroDivisionError" in p["status"]["warnings"]


def test_one_failing_indicator_is_marked_and_the_others_are_built(monkeypatch):
    real = build.momentum
    monkeypatch.setattr(build, "momentum", lambda ind, s: (_ for _ in ()).throw(ValueError("x")) if ind.id == "CPILFESL"
                        else real(ind, s))
    entries, warn = indicator_entries(demo_series(start="2018-01-01", end="2026-06-30"), {}, NOW)
    bad = next(e for e in entries if e["id"] == "CPILFESL")
    assert bad["state"] == "error" and bad["issues"] == ["تعذّر حساب المؤشر: ValueError"]
    assert next(e for e in entries if e["id"] == "CPIAUCSL")["state"] != "error" and "last" in next(e for e in entries if e["id"] == "CPIAUCSL")


def test_each_collector_builds_its_own_section_only():
    s = demo_series(start="2018-01-01", end="2026-06-30")
    for key in collectors.ECONOMY:
        inds = collectors.section_indicators(key)
        sub = {i.id: s[i.id] for i in inds}
        entries, _ = indicator_entries(sub, {}, NOW, indicators=inds)
        b = collectors.brief(key, entries, [], None, None, NOW)
        assert {i["id"] for i in b["indicators"]} == {i.id for i in inds} and b["available"] == b["total"] == len(inds)
        assert b["collected_at"] == "2026-11-16T13:41:00Z" and b["ok"]


def _man(at, ok=True):
    briefs = {k: {"ok": ok if k == "labor" else True, "collected_at": at} for k in collectors.ALL}
    return {"collected_at": at, "trigger": "pre_report_collection", "sections": {}}, briefs


@pytest.mark.parametrize("at, ok, now, want, why", [
    ("2026-11-16T13:41:00Z", True, NOW + timedelta(minutes=20), True, "collected"),              # 08:41 ثم 09:01
    ("2026-11-16T13:20:00Z", True, NOW + timedelta(minutes=20), False, "after 08:30"),           # 08:20: قبل إصدارات 08:30
    ("2026-11-13T13:41:00Z", True, NOW + timedelta(minutes=20), False, "not from today"),        # الجمعة
    ("2026-11-16T13:41:00Z", False, NOW + timedelta(minutes=20), False, "sections not ready: labor"),
    ("2026-11-16T14:30:00Z", True, NOW + timedelta(minutes=20), False, "after now"),
])
def test_ready_means_collected_today_after_0830_before_now_with_every_section(at, ok, now, want, why):
    man, briefs = _man(at, ok)
    got, reason = collectors.fresh(man, briefs, now)
    assert got is want and why in reason
    assert collectors.fresh(None, {}, now) == (False, "no pre-report collection found")


def test_the_market_step_does_not_search_the_web(monkeypatch):
    import sys
    import types
    calls = []

    class Msg:
        model, stop_reason, content = "claude-opus-5-5", "end_turn", []
        usage = types.SimpleNamespace(input_tokens=1, output_tokens=1, cache_creation_input_tokens=0,
                                      cache_read_input_tokens=0, server_tool_use=None)

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return Msg()

    class Client:
        def __init__(self):
            self.messages = types.SimpleNamespace(stream=lambda **kw: calls.append(kw) or Stream())

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Client))
    from macro_platform import ai_report
    ai_report._market_caller(ai_report.call_claude)("u", ai_report.SYSTEM_MARKETS, "claude-opus-5-5", "xhigh")
    ai_report.call_claude("u", ai_report.SYSTEM, "claude-opus-5-5", "xhigh")
    assert "tools" not in calls[0] and calls[1]["tools"][0]["type"] == "web_search_20260209"
    assert BY_ID  # الكتالوج محمّل


def test_headline_text_never_reaches_the_public_repository(tmp_path):
    # نص العناوين لا يُحفظ في المستودع العام: حتى لو مرّ ملف الأخبار إلى الحفظ، لا يُكتب
    briefs = {"growth": {"ok": True, "section": "growth"},
              "news": collectors.news_brief({"headlines": [{"title": "Secret headline"}]}, NOW)}
    collectors.save(tmp_path, briefs, collectors.manifest(briefs, NOW, "data_run", None))
    assert {f.name for f in tmp_path.iterdir()} == {"growth.json", "index.json"}
    assert "Secret headline" not in "".join(f.read_text(encoding="utf-8") for f in tmp_path.iterdir())
