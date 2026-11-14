"""سجل التوقعات: كل تقرير يترك توقعين قابلين للقياس، ويحكم عليهما الكود من بيانات FRED نفسها بعد وقوعهما.

لماذا: قبل هذا السجل كانت الثقة 55% في التقارير السبعة الأولى كلها (4 إلى 13 نوفمبر 2026)، وشروط السيناريوهات
نصاً مركّباً بلا موعد حسم، فلم يكن ممكناً قياس الدقة ولا المعايرة ولا المقارنة بمرجع بسيط.

1) الانحياز: confidence احتمالٌ (50 إلى 100) أن يغلق NASDAQ100 في جلسة التقرير على جهة الاتجاه مقارنة بالإغلاق
   السابق. p_up = confidence (bullish) أو 100 − confidence (bearish) أو 50 (neutral). Brier = (p_up − up)²،
   والمرجع 0.25 (خمسون في المئة دائماً).
2) السيناريوهات A وB وC: شرط لكل سيناريو على سلسلة يومية واحدة وتاريخ واحد، ونطاقات متتالية بلا فجوة ولا تداخل،
   فالاحتمالات الثلاثة توزيع كامل. يُحسم حين تصل بيانات السلسلة إلى التاريخ. Brier متعدد الفئات، والمرجع ثلث لكل
   سيناريو (≈0.667).

لا نص من التقرير يُحفظ هنا: أرقام وتواريخ فقط، في data/forecast_ledger.json (يكتبه update.yml ويحفظه في المستودع).
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from macro_platform.ai_report import NY, is_trading_day

ALLOWED = ("NASDAQ100", "SP500", "DGS10", "DGS2", "DFII10", "T10YIE", "VIXCLS", "DTWEXBGS", "DCOILWTICO",
           "BAMLH0A0HYM2")   # سلاسل يومية تجلبها المنصة أصلاً؛ لا مصدر جديد
BIAS_SERIES = "NASDAQ100"
MAX_DAYS = 21              # أبعد تاريخ حسم للسيناريوهات بعد يوم التقرير
MIN_N = 30                 # قاعدة إبهام: دون 30 حالة محسومة لا يُقرأ أي متوسط
BASE_BIAS = 0.25           # Brier لمن يقول 50% دائماً
BASE_SCEN = round(2 / 3, 4)  # Brier لمن يعطي كل سيناريو الثلث


def load(path: Path) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("entries"), list):
            return d
    except Exception:
        pass
    return {"version": 1, "entries": []}


def save(path: Path, led: dict) -> None:
    Path(path).write_text(json.dumps(led, ensure_ascii=False, indent=1), encoding="utf-8")


def _num(x) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        return None
    return float(x)


def _day(s) -> date | None:
    try:
        return date.fromisoformat(str(s))
    except Exception:
        return None


# ---------------------------------------------------------------- what a report commits to
def p_up(bias: dict) -> tuple[float | None, str | None]:
    """احتمال الصعود من الانحياز، أو سبب عدم صلاحيته."""
    d = (bias or {}).get("direction")
    c = _num((bias or {}).get("confidence"))
    if d == "neutral":
        return 0.5, None
    if d not in ("bullish", "bearish"):
        return None, "unknown direction"
    if c is None or not 50 <= c <= 100:
        return None, "confidence outside 50-100"
    return round(c / 100 if d == "bullish" else 1 - c / 100, 4), None


def session_date(written_at: str, cfg: dict) -> str | None:
    """جلسة التقرير: يوم كتابته إن كُتب في يوم تداول قبل إغلاق 16:00 بتوقيت نيويورك، وإلا يوم التداول التالي."""
    try:
        ny = datetime.fromisoformat(str(written_at).replace("Z", "+00:00")).astimezone(NY)
    except Exception:
        return None
    day = ny if ny.hour < 16 else ny + timedelta(days=1)
    day = day.replace(hour=12, minute=0, second=0, microsecond=0)
    for _ in range(10):
        if is_trading_day(day, cfg):
            return day.strftime("%Y-%m-%d")
        day += timedelta(days=1)
    return None


def check_scenarios(scenarios: list, ny_date: str) -> tuple[dict | None, str | None]:
    """شروط السيناريوهات الثلاثة: سلسلة واحدة مسموحة، وتاريخ واحد خلال MAX_DAYS، ونطاقات تغطي كل القيم بلا تداخل."""
    checks = [s.get("check") if isinstance(s, dict) else None for s in scenarios or []]
    if len(checks) != 3 or not all(isinstance(c, dict) for c in checks):
        return None, "missing check"
    series = {c.get("series") for c in checks}
    if len(series) != 1 or next(iter(series)) not in ALLOWED:
        return None, "checks must use one allowed series"
    bys = {c.get("by") for c in checks}
    by, day = next(iter(bys)), _day(ny_date)
    if len(bys) != 1 or _day(by) is None or day is None or not day < _day(by) <= day + timedelta(days=MAX_DAYS):
        return None, f"checks must share one date within {MAX_DAYS} days after the report"
    ranges = []
    for s, c in zip(scenarios, checks):
        lo, hi = c.get("low"), c.get("high")
        lo_n, hi_n = _num(lo), _num(hi)
        if (lo is not None and lo_n is None) or (hi is not None and hi_n is None):
            return None, "range bounds must be numbers or null"
        if lo_n is not None and hi_n is not None and lo_n >= hi_n:
            return None, "low must be below high"
        ranges.append({"label": s.get("label"), "low": lo_n, "high": hi_n, "prob": int(s.get("prob", 0))})
    ranges.sort(key=lambda r: -math.inf if r["low"] is None else r["low"])
    if ranges[0]["low"] is not None or ranges[-1]["high"] is not None:
        return None, "ranges must be open at both ends"
    for a, b in zip(ranges, ranges[1:]):
        if a["high"] is None or b["low"] is None or abs(a["high"] - b["low"]) > 1e-9:
            return None, "ranges must meet with no gap or overlap"
    return {"series": next(iter(series)), "by": by, "ranges": ranges, "outcome": None}, None


def entry(rep: dict, cfg: dict) -> dict:
    m = rep.get("meta") or {}
    pu, why = p_up(rep.get("bias") or {})
    sc, sc_why = check_scenarios(rep.get("scenarios") or [], m.get("ny_date"))
    return {"ny_date": m.get("ny_date"), "written_at": m.get("completed_at") or m.get("written_at"),
            "model": m.get("model"), "run_id": m.get("run_id"),
            "bias": {"direction": (rep.get("bias") or {}).get("direction"),
                     "confidence": _num((rep.get("bias") or {}).get("confidence")), "p_up": pu,
                     "session": session_date(m.get("completed_at") or m.get("written_at"), cfg),
                     "invalid": why, "outcome": None},
            "scenarios": sc or {"invalid": sc_why}}


def add(led: dict, e: dict) -> None:
    """تقرير ثانٍ لليوم نفسه (تشغيل يدوي) يحلّ محل الأول، كما في ai_history.json."""
    led["entries"] = [x for x in led["entries"] if x.get("ny_date") != e.get("ny_date")] + [e]
    led["entries"].sort(key=lambda x: x.get("ny_date") or "")


# ---------------------------------------------------------------- outcomes, from the platform's own FRED series
def _clean(s) -> pd.Series:
    if s is None or len(s) == 0:
        return pd.Series(dtype=float)
    s = pd.to_numeric(pd.Series(s), errors="coerce").dropna()
    s.index = pd.to_datetime(s.index).normalize()
    return s.sort_index()


def _resolve_bias(b: dict, s: pd.Series) -> dict | None:
    sess = pd.Timestamp(b["session"])
    if s.empty or s.index.max() < sess:
        return None                                        # لم تصل البيانات إلى الجلسة بعد
    prev = s[s.index < sess]
    if sess not in s.index or prev.empty:
        return {"scorable": False, "why": "no close for the session"}
    close, before = float(s[sess]), float(prev.iloc[-1])
    up = close > before
    return {"scorable": True, "close": close, "prev_close": before, "prev_date": prev.index[-1].strftime("%Y-%m-%d"),
            "change_pct": round((close / before - 1) * 100, 3), "up": up,
            "hit": None if b["direction"] == "neutral" else (up == (b["direction"] == "bullish")),
            "brier": round((b["p_up"] - (1.0 if up else 0.0)) ** 2, 4)}


def _resolve_scenarios(sc: dict, s: pd.Series, ny_date: str) -> dict | None:
    by = pd.Timestamp(sc["by"])
    if s.empty or s.index.max() < by:
        return None
    upto = s[(s.index <= by) & (s.index > pd.Timestamp(ny_date))]
    if upto.empty:
        return {"scorable": False, "why": "no observation between the report and the date"}
    v, on = float(upto.iloc[-1]), upto.index[-1].strftime("%Y-%m-%d")
    hit = next(r["label"] for r in sc["ranges"]
               if (r["low"] is None or v >= r["low"]) and (r["high"] is None or v < r["high"]))
    brier = sum((r["prob"] / 100 - (1.0 if r["label"] == hit else 0.0)) ** 2 for r in sc["ranges"])
    return {"scorable": True, "value": v, "date": on, "label": hit, "brier": round(brier, 4)}


def resolve(led: dict, series: dict) -> int:
    """يحسم ما وصلت بياناته. لا يحسم شيئاً قبل أن تتجاوز السلسلةُ التاريخَ، فلا حكم على بيانات ناقصة."""
    done = 0
    for e in led["entries"]:
        b = e.get("bias") or {}
        if b.get("outcome") is None and b.get("p_up") is not None and b.get("session"):
            out = _resolve_bias(b, _clean(series.get(BIAS_SERIES)))
            if out is not None:
                b["outcome"], done = out, done + 1
        sc = e.get("scenarios") or {}
        if sc.get("ranges") and sc.get("outcome") is None:
            out = _resolve_scenarios(sc, _clean(series.get(sc["series"])), e["ny_date"])
            if out is not None:
                sc["outcome"], done = out, done + 1
    return done


def summary(led: dict) -> dict:
    bias = [e["bias"]["outcome"] for e in led["entries"] if (e.get("bias") or {}).get("outcome", {}) and
            e["bias"]["outcome"].get("scorable")]
    scen = [e["scenarios"]["outcome"] for e in led["entries"] if (e.get("scenarios") or {}).get("outcome") and
            e["scenarios"]["outcome"].get("scorable")]
    directional = [o for o in bias if o.get("hit") is not None]
    mean = lambda xs: round(sum(xs) / len(xs), 4) if xs else None   # noqa: E731
    return {"reports": len(led["entries"]),
            "bias": {"n": len(bias), "directional": len(directional), "hits": sum(o["hit"] for o in directional),
                     "brier": mean([o["brier"] for o in bias]), "baseline": BASE_BIAS},
            "scenarios": {"n": len(scen), "brier": mean([o["brier"] for o in scen]), "baseline": BASE_SCEN},
            "min_n": MIN_N, "meaningful": min(len(bias), len(scen)) >= MIN_N}


def view(led: dict, keep: int = 14) -> dict:
    """ما تعرضه الصفحة: الملخص، وحالة آخر التقارير بتاريخها."""
    return {"summary": summary(led),
            "by_date": {e["ny_date"]: {"bias": e.get("bias"), "scenarios": e.get("scenarios")}
                        for e in led["entries"][-keep:] if e.get("ny_date")}}
