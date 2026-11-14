"""سجل التوقعات: ما يلتزم به التقرير قابل للقياس، ويحسمه الكود من سلاسل FRED نفسها بعد وقوعه.

لماذا: قبل هذا السجل كانت الثقة 55% في التقارير السبعة الأولى كلها (4 إلى 13 نوفمبر 2026)، وشروط السيناريوهات
نصاً مركّباً بلا موعد حسم، فلم يكن ممكناً قياس الدقة ولا المعايرة ولا المقارنة بمرجع بسيط.

Scenario-check: لكل سيناريو شرط على سلسلة اقتصادية/مالية يومية واحدة وتاريخ حسم واحد (يوم تداول خلال 21 يوماً)،
ونطاقات متتالية بلا فجوة ولا تداخل، والاحتمالات الثلاثة أعداد صحيحة مجموعها 100 بالضبط. الشرط **بصمة قريبة الأجل
مرتبطة بالسيناريو، وليس إثباتاً لتحقق السيناريو الاقتصادي كاملاً**؛ لذلك يُسمّى الحسم check_result لا outcome،
ولا يقيس Scenario-check Brier صحة السرد الاقتصادي. المرجع: من يعطي كل سيناريو الثلث (≈0.667).

Translation-check (منفصل كلياً، لا يُجمع مع Scenario-check في أي رقم): احتمال أن يتفوق Nasdaq-100 على S&P 500 خلال
10 أيام تداول، من آخر إغلاق قبل التقرير (أو إغلاق يومه لتقرير متأخر). يقيس الترجمة السوقية لا التحليل الاقتصادي.
المرجع: 0.25 (من يقول 50). يُحفظ مشتقاً فقط: الفرق النسبي وهل تفوق NQ، لا مستويات المؤشرين.

التوقيت: لكل مدخل timing (المحاولة، وموعدها، والبدء، والاكتمال، وهل اكتمل قبل الافتتاح). الإحصاءات تُحسب منفصلة
للتقارير قبل الافتتاح وللمتأخرة؛ الرقم الرئيسي للأولى. مدخل واحد لكل جلسة، ومن المحاولات المجدولة الناجحة فقط.

لا نص من التقرير يُحفظ هنا: أرقام وتواريخ فقط، في data/forecast_ledger.json (يكتبه update.yml ويحفظه في المستودع).
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from macro_platform.ai_report import is_trading_day

VERSION = 2
# سلاسل اقتصادية/مالية يومية تجلبها المنصة أصلاً. مؤشرات الأسهم ليست هنا: التقرير الاقتصادي لا يُقاس بها.
ALLOWED = ("DGS2", "DGS10", "DFII10", "T10YIE", "T10Y2Y", "BAMLH0A0HYM2", "DTWEXBGS", "DCOILWTICO", "VIXCLS")
MAX_DAYS = 21              # أبعد تاريخ حسم بعد يوم التقرير
MIN_N = 30                 # قاعدة إبهام لا اختبار إحصائي: دونها العينة صغيرة ولا تكفي للحكم
BASE_SCEN = round(2 / 3, 4)  # Brier لمن يعطي كل سيناريو الثلث
BASE_TRANS = 0.25            # Brier لمن يقول 50 في الترجمة
TRANS_DAYS = 10              # أيام تداول نافذة Translation-check
GROUPS = ("pre_open", "delayed")


def load(path: Path) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("entries"), list):
            return d
    except Exception:
        pass
    return {"version": VERSION, "entries": []}


def save(path: Path, led: dict) -> None:
    led["version"] = VERSION
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


def holiday_horizon(cfg: dict) -> int | None:
    """آخر سنة تغطيها قائمة العطل؛ بعدها لا نعرف أيام التداول فنرفض."""
    years = [int(str(d)[:4]) for d in cfg.get("market_holidays", []) if str(d)[:4].isdigit()]
    return max(years) if years else None


def probs_issue(scenarios: list) -> str | None:
    """الاحتمالات كما أرسلها النموذج: ثلاثة أعداد صحيحة مجموعها 100 بالضبط. لا تعديل صامت."""
    if len(scenarios or []) != 3:
        return f"expected 3 scenarios, got {len(scenarios or [])}"
    ps = [s.get("prob") if isinstance(s, dict) else None for s in scenarios]
    if not all(isinstance(p, int) and not isinstance(p, bool) and 0 <= p <= 100 for p in ps):
        return "probabilities must be whole numbers from 0 to 100"
    if sum(ps) != 100:
        return f"probabilities sum to {sum(ps)}, not 100"
    return None


def check_scenarios(scenarios: list, ny_date: str, cfg: dict) -> tuple[dict | None, str | None]:
    """شروط السيناريوهات الثلاثة: سلسلة واحدة مسموحة، وتاريخ حسم واحد هو يوم تداول خلال MAX_DAYS، ونطاقات
    تغطي كل القيم بلا تداخل، والاحتمالات مجموعها 100 بالضبط."""
    why = probs_issue(scenarios)
    if why:
        return None, why
    checks = [s.get("check") for s in scenarios]
    if not all(isinstance(c, dict) for c in checks):
        return None, "missing check"
    series = {c.get("series") for c in checks}
    if len(series) != 1 or next(iter(series)) not in ALLOWED:
        return None, "checks must use one allowed series"
    bys = {c.get("by") for c in checks}
    by, day = _day(next(iter(bys))), _day(ny_date)
    if len(bys) != 1 or by is None or day is None or not day < by <= day + timedelta(days=MAX_DAYS):
        return None, f"checks must share one date within {MAX_DAYS} days after the report"
    horizon = holiday_horizon(cfg)
    if horizon is None or by.year > horizon:
        return None, "market holidays are not known for that date"
    if not is_trading_day(datetime(by.year, by.month, by.day, 12), cfg):
        return None, "the check date is not a trading day"
    ranges = []
    for s, c in zip(scenarios, checks):
        lo, hi = c.get("low"), c.get("high")
        lo_n, hi_n = _num(lo), _num(hi)
        if (lo is not None and lo_n is None) or (hi is not None and hi_n is None):
            return None, "range bounds must be numbers or null"
        if lo_n is not None and hi_n is not None and lo_n >= hi_n:
            return None, "low must be below high"
        ranges.append({"label": s.get("label"), "low": lo_n, "high": hi_n, "prob": s["prob"]})
    ranges.sort(key=lambda r: -math.inf if r["low"] is None else r["low"])
    if ranges[0]["low"] is not None or ranges[-1]["high"] is not None:
        return None, "ranges must be open at both ends"
    for a, b in zip(ranges, ranges[1:]):
        if a["high"] is None or b["low"] is None or abs(a["high"] - b["low"]) > 1e-9:
            return None, "ranges must meet with no gap or overlap"
    return {"series": next(iter(series)), "by": by.isoformat(), "ranges": ranges, "check_result": None}, None


def _step(d: date, cfg: dict, n: int) -> date | None:
    """يوم التداول رقم n بعد d (n سالب = قبله)، أو None إن تجاوز تقويم العطل المعروف."""
    horizon, k = holiday_horizon(cfg), 1 if n > 0 else -1
    while n:
        d += timedelta(days=k)
        if horizon is None or d.year > horizon:
            return None
        if is_trading_day(datetime(d.year, d.month, d.day, 12), cfg):
            n -= k
    return d


def translation(rep: dict, cfg: dict, delayed: bool) -> dict:
    """Translation-check: احتمال تفوق NQ على S&P 500 خلال 10 أيام تداول. التقرير قبل الافتتاح يبدأ من آخر إغلاق
    قبله؛ والمتأخر من إغلاق يومه، فتبدأ النافذة بعد الجلسة التي رأى النموذج جزءاً منها."""
    mt = rep.get("market_translation") if isinstance(rep.get("market_translation"), dict) else {}
    tr = mt.get("nq_vs_spx_10d") if isinstance(mt.get("nq_vs_spx_10d"), dict) else {}
    p = tr.get("prob")
    if not (isinstance(p, int) and not isinstance(p, bool) and 0 <= p <= 100):
        return {"unscorable": "probability missing or not a whole number from 0 to 100"}
    day = _day((rep.get("meta") or {}).get("ny_date"))
    start = day if delayed else (_step(day, cfg, -1) if day else None)
    end = _step(start, cfg, TRANS_DAYS) if start else None
    if start is None or end is None:
        return {"unscorable": "market holidays are not known for that window"}
    return {"prob": p, "start": start.isoformat(), "end": end.isoformat(), "check_result": None}


def entry(rep: dict, cfg: dict) -> dict:
    m = rep.get("meta") or {}
    sc, why = check_scenarios(rep.get("scenarios") or [], m.get("ny_date"), cfg)
    delayed = bool(m.get("delayed"))
    return {"ny_date": m.get("ny_date"), "written_at": m.get("completed_at") or m.get("written_at"),
            "model": m.get("model"), "run_id": m.get("run_id"),
            "timing": {k: m.get(k) for k in ("attempt", "scheduled_for", "started_at", "completed_at", "pre_open")}
                      | {"delayed": delayed},
            "scenario_check": sc or {"unscorable": why},
            "translation_check": translation(rep, cfg, delayed)}


def add(led: dict, e: dict) -> None:
    """مدخل واحد لكل جلسة: تقرير ثانٍ لليوم نفسه يحلّ محل الأول."""
    led["entries"] = [x for x in led["entries"] if x.get("ny_date") != e.get("ny_date")] + [e]
    led["entries"].sort(key=lambda x: x.get("ny_date") or "")


# ---------------------------------------------------------------- settling, from the platform's own FRED series
def _clean(s) -> pd.Series:
    if s is None or len(s) == 0:
        return pd.Series(dtype=float)
    s = pd.to_numeric(pd.Series(s), errors="coerce").dropna()
    s.index = pd.to_datetime(s.index).normalize()
    return s.sort_index()


def _settle(sc: dict, s: pd.Series, ny_date: str) -> dict | None:
    by = pd.Timestamp(sc["by"])
    if s.empty or s.index.max() < by:
        return None                                        # لم تصل البيانات إلى تاريخ الحسم: لا حكم
    upto = s[(s.index <= by) & (s.index > pd.Timestamp(ny_date))]
    if upto.empty:
        return {"scorable": False, "why": "no observation between the report and the date"}
    v, on = float(upto.iloc[-1]), upto.index[-1].strftime("%Y-%m-%d")
    hit = next(r["label"] for r in sc["ranges"]
               if (r["low"] is None or v >= r["low"]) and (r["high"] is None or v < r["high"]))
    brier = sum((r["prob"] / 100 - (1.0 if r["label"] == hit else 0.0)) ** 2 for r in sc["ranges"])
    return {"scorable": True, "value": v, "date": on, "range_hit": hit, "brier": round(brier, 4)}


def _settle_translation(tc: dict, ndx: pd.Series, spx: pd.Series) -> dict | None:
    start, end = pd.Timestamp(tc["start"]), pd.Timestamp(tc["end"])
    if ndx.empty or spx.empty or ndx.index.max() < end or spx.index.max() < end:
        return None                                        # لم تصل الإغلاقات إلى نهاية النافذة: لا حكم
    a, b = ndx[ndx.index <= start], spx[spx.index <= start]
    c, d = ndx[(ndx.index > start) & (ndx.index <= end)], spx[(spx.index > start) & (spx.index <= end)]
    if a.empty or b.empty or c.empty or d.empty or a.index[-1] != b.index[-1] or c.index[-1] != d.index[-1]:
        return {"scorable": False, "why": "no matching closes for both indices at the window ends"}
    r_ndx, r_spx = c.iloc[-1] / a.iloc[-1], d.iloc[-1] / b.iloc[-1]
    hit = bool(r_ndx > r_spx)
    return {"scorable": True, "nq_minus_spx_pct": round(float(r_ndx - r_spx) * 100, 3), "nq_outperformed": hit,
            "from": a.index[-1].strftime("%Y-%m-%d"), "to": c.index[-1].strftime("%Y-%m-%d"),
            "brier": round((tc["prob"] / 100 - (1.0 if hit else 0.0)) ** 2, 4)}


def resolve(led: dict, series: dict) -> int:
    """يحسم ما وصلت بياناته. لا يحسم قبل أن تتجاوز السلسلةُ تاريخَ الحسم، فلا حكم على بيانات ناقصة."""
    done = 0
    ndx, spx = _clean(series.get("NASDAQ100")), _clean(series.get("SP500"))
    for e in led["entries"]:
        sc = e.get("scenario_check") or {}
        if sc.get("ranges") and sc.get("check_result") is None:
            out = _settle(sc, _clean(series.get(sc["series"])), e["ny_date"])
            if out is not None:
                sc["check_result"], done = out, done + 1
        tc = e.get("translation_check") or {}
        if "prob" in tc and tc.get("check_result") is None:
            out = _settle_translation(tc, ndx, spx)
            if out is not None:
                tc["check_result"], done = out, done + 1
    return done


def _group(e: dict) -> str:
    return "delayed" if (e.get("timing") or {}).get("delayed") else "pre_open"


def _stats(led: dict, key: str, base: float) -> dict:
    out = {}
    for g in GROUPS:
        res = [r for e in led["entries"] if _group(e) == g
               for r in [((e.get(key) or {}).get("check_result") or {})] if r.get("scorable")]
        n = len(res)
        out[g] = {"n": n, "brier": round(sum(r["brier"] for r in res) / n, 4) if n else None, "baseline": base,
                  "reports": sum(1 for e in led["entries"] if _group(e) == g),
                  "sample": "preliminary" if n >= MIN_N else "small"}
    return out


def summary(led: dict) -> dict:
    """قسمان لا يُجمعان أبداً، وكل منهما منفصل قبل الافتتاح وبعده. الرقم الرئيسي: pre_open."""
    return {"reports": len(led["entries"]), "min_n": MIN_N,
            "scenario_check": _stats(led, "scenario_check", BASE_SCEN),
            "translation_check": _stats(led, "translation_check", BASE_TRANS)}


def view(led: dict, keep: int = 14) -> dict:
    """ما تعرضه الصفحة: الملخص، وحالة آخر التقارير بتاريخها."""
    return {"summary": summary(led),
            "by_date": {e["ny_date"]: {k: e.get(k) for k in ("scenario_check", "translation_check", "timing")}
                        for e in led["entries"][-keep:] if e.get("ny_date")}}
