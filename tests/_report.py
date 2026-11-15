"""تقرير صالح بالصيغة 2، ونموذج مزيّف، وساعة مثبّتة، للاختبارات (بلا شبكة ولا API)."""
import copy
import json
from datetime import datetime, timezone

from macro_platform import ai_report


def scen(label, prob, low, high, series="DGS10", by="2026-11-20"):
    return {"label": label, "name": f"سيناريو {label}", "prob": prob, "horizon": "أسبوعان", "thesis": f"أطروحة {label}",
            "evidence": ["DGS10"], "confirm": f"يؤكده {label}", "invalidate": f"يبطله {label}",
            "moves_probability": "ما يحرّك الاحتمال", "check": {"series": series, "by": by, "low": low, "high": high}}


SCEN = [scen("A", 45, 4.95, 5.10), scen("B", 35, None, 4.95), scen("C", 20, 5.10, None)]


def gate(impact=0, **kw):
    return {"impact": impact, "drivers": ["العائد الحقيقي"], "positive": ["إيجابي"], "negative": ["سلبي"],
            "matters_now": "ما يهم الآن", "would_flip": "ما يقلب الحكم", "horizon": "أيام", **kw}


def body(scenarios=None, prob=45, gates=None, **eco):
    b = {"economy": {
        "executive": {"headline": "عنوان اليوم", "read": "القراءة الاقتصادية", "direction": "stable",
                      "biggest_change": "أكبر تغير"},
        "regime": {"growth": "نمو", "inflation": "تضخم", "labor": "عمل", "fed_rates": "فيدرالي", "financial": "مالية",
                   "label": "تباطؤ معتدل"},
        "dominant_driver": {"driver": "العائد الحقيقي", "kind": "data", "evidence": ["DFII10"], "event_ids": []},
        "key_conflicts": [],
        "what_changed": ["ما تغير"],
        "sections": {"growth": "فقرة النمو", "inflation": "فقرة التضخم", "labor": "فقرة العمل",
                     "fed_rates": "فقرة الفيدرالي", "financial_conditions": "فقرة الأوضاع المالية"},
        "news": [{"item": "خبر", "effect": "أثر", "changes_picture": False, "event_ids": ["e1"]}],
        "data_uncertainty": ["عدم يقين"],
        "scenarios": copy.deepcopy(SCEN if scenarios is None else scenarios),
        "conviction": "قناعة متوسطة", "bottom_line": "الخلاصة"},
        "market_translation": {"gates": gates if gates is not None else {"nq": gate(-1), "spx": gate(0), "dow": gate(0)},
                               "nq_vs_spx_10d": {"prob": prob}},
        "events": [{"id": "e1", "title": "FOMC", "date": "2026-11-16", "url": "https://www.federalreserve.gov/x"}]}
    b["economy"].update(eco)
    return b


def text(b=None):
    return "<report>" + json.dumps(body() if b is None else b, ensure_ascii=False) + "</report>"


def is_market(system):
    return system == ai_report.SYSTEM_MARKETS


def caller_for(b=None, seen=None, served=None, info=None, calls=None, market=None):
    """نموذج مزيّف بالخطوتين كما في الحقيقة: الاستدعاء الاقتصادي يعيد economy وevents فقط، واستدعاء Market Gates
    (بعده) يعيد market_translation فقط. seen يحفظ الاستدعاء الاقتصادي، وseen["market"] الثاني."""
    full = body() if b is None else b

    def caller(user, system, model, effort):
        mk = is_market(system)
        if seen is not None:
            (seen.setdefault("market", {}) if mk else seen).update(user=user, system=system, model=model, effort=effort)
        if calls is not None:
            calls.append("market" if mk else "economy")
        usage = {k: 10 for k in ai_report.USAGE_KEYS} | {"web_search_requests": 0 if mk else 3}
        if mk:
            out = market if market is not None else text({"market_translation": full.get("market_translation")}) \
                if isinstance(full, dict) and "market_translation" in full else "no market output"
        else:
            out = text({k: v for k, v in full.items() if k != "market_translation"}) if isinstance(full, dict) else text(full)
        return out, [] if mk else [{"url": "https://x.test", "title": "x"}], usage, served or model, \
            info or {"stop_reason": "end_turn", "duration_s": 1.0}
    return caller


def fake_fetch(series):
    """fetch_all مزيّف يحترم قائمة المؤشرات: كل جامع يأخذ سلاسل قسمه وحده."""
    def fetch(raw, start, fetcher=None, indicators=None, log=None):
        ids = None if indicators is None else {i.id for i in indicators}
        got = {k: v for k, v in series.items() if ids is None or k in ids}
        return got, {k: {"state": "ok", "issues": []} for k in got}
    return fetch


def utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def freeze(monkeypatch, iso, done=None):
    """يثبّت وقت fetch_macro.main ووقت اكتمال التقرير (done، افتراضياً بعد 15 دقيقة)."""
    import fetch_macro
    t = utc(iso)
    end = utc(done) if done else t.replace(minute=(t.minute + 15) % 60, hour=t.hour + (t.minute + 15) // 60)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return t

    monkeypatch.setattr(fetch_macro, "datetime", Frozen)
    monkeypatch.setattr(ai_report, "_now", lambda: end)
    return t
