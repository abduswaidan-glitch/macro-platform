"""يبني data.json الذي تقرؤه الصفحة: السلاسل المحوّلة، اللقطة، الإشارات، بوابات NQ، وحالة كل مصدر.

قاعدة السلامة (كما في منصة الذهب): كل سلسلة تُفحص قبل النشر. إن فشل الجلب أو الفحص تبقى آخر نسخة سليمة
محفوظة في data/raw/<ID>.csv ويظهر تحذير، فلا تُنشر بيانات معطوبة أبداً.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from macro_platform.analytics import (growth_inflation_quadrant, sahm_rule, signals, snapshot,
                                      transform_indicator, SAHM_TRIGGER)
from macro_platform.catalog import CATEGORIES, INDICATORS, Indicator, PERIODS_PER_YEAR
from macro_platform.fred import FredError, fetch_series

MIN_ROWS = {"D": 200, "W": 50, "M": 24, "Q": 8}
MAX_AGE_DAYS = {"D": 7, "W": 14, "M": 80, "Q": 220}  # تأخر النشر الطبيعي لكل تكرار
# سلاسل يومية ينشرها FRED متأخرة أسبوعاً عادةً (نفط WTI من EIA، الدولار الواسع من الفيدرالي)
MAX_AGE_OVERRIDE = {"DCOILWTICO": 14, "DTWEXBGS": 14}
KPI_IDS = ("DGS10", "DFII10", "DTWEXBGS", "DCOILWTICO", "T10Y2Y", "FEDFUNDS", "PCEPILFE", "UNRATE", "VIXCLS", "NASDAQ100")
DEFAULT_CONFIG = {
    "start": "2000-01-01",
    "gates": {"us10y_trigger": 4.5, "vix_calm": 20, "vix_stress": 25, "real10_restrictive": 2.0, "hy_stress": 5.0},
}


def load_config(path: str | Path = "config.json") -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    p = Path(path)
    if p.exists():
        user = json.loads(p.read_text(encoding="utf-8"))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


# ---------------------------------------------------------------- raw store (last good data)
def raw_path(raw_dir: Path, sid: str) -> Path:
    return raw_dir / f"{sid}.csv"


def load_raw(raw_dir: Path, sid: str) -> pd.Series | None:
    p = raw_path(raw_dir, sid)
    if not p.exists():
        return None
    df = pd.read_csv(p, index_col=0, parse_dates=True)
    return df.iloc[:, 0].astype("float64").rename(sid)


def save_raw(raw_dir: Path, sid: str, s: pd.Series) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    out = s.rename(sid).to_frame()
    out.index.name = "date"
    out.to_csv(raw_path(raw_dir, sid), float_format="%.6g")


# ---------------------------------------------------------------- validation
def validate(ind: Indicator, new: pd.Series, old: pd.Series | None) -> tuple[bool, list[str]]:
    """يرفض البيانات المعطوبة (قصيرة، تراجع في التاريخ، فقدان سجل)، ويحذّر من القفزات غير المعتادة."""
    issues: list[str] = []
    if len(new) < MIN_ROWS[ind.freq]:
        return False, [f"عدد صفوف قليل ({len(new)})"]
    if old is not None and len(old):
        if new.index[-1] < old.index[-1]:
            return False, ["آخر تاريخ أقدم من النسخة المحفوظة"]
        overlap = old[old.index >= new.index[0]]
        if len(overlap) and len(new) < 0.9 * len(overlap):
            return False, ["السجل الجديد أقصر بكثير من المحفوظ"]
    d = new.diff().dropna()
    if len(d) > 30:
        med = d.abs().iloc[:-1].median()
        if med > 0 and abs(d.iloc[-1]) > 12 * med:
            issues.append(f"قفزة غير معتادة في آخر قيمة ({d.iloc[-1]:+.4g})")
    return True, issues


def staleness_days(ind: Indicator, s: pd.Series, now: pd.Timestamp) -> int:
    return int((now.normalize() - s.index[-1].normalize()).days)


def fetch_all(raw_dir: Path, start: str, fetcher: Callable[[str, str], pd.Series] = fetch_series,
              indicators=INDICATORS, log=print) -> tuple[dict[str, pd.Series], dict[str, dict]]:
    series: dict[str, pd.Series] = {}
    status: dict[str, dict] = {}
    for ind in indicators:
        old = load_raw(raw_dir, ind.id)
        st = {"state": "ok", "issues": []}
        try:
            new = fetcher(ind.id, start)
            ok, issues = validate(ind, new, old)
            st["issues"] += issues
            if ok:
                save_raw(raw_dir, ind.id, new)
                series[ind.id] = new
            else:
                st["state"] = "error"
                log(f"{ind.id}: rejected: {'; '.join(issues)}")
        except (FredError, Exception) as exc:  # أي فشل: نبقي آخر نسخة سليمة
            st["state"] = "error"
            st["issues"].append(f"فشل الجلب: {str(exc)[:160]}")
            log(f"{ind.id}: fetch failed: {str(exc)[:160]}")
        if ind.id not in series and old is not None:
            series[ind.id] = old
            st["issues"].append("تُعرض آخر نسخة سليمة محفوظة")
        status[ind.id] = st
    return series, status


# ---------------------------------------------------------------- payload pieces
def _r(x, nd=4):
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) or math.isinf(x) else round(x, nd)


def chart_points(ind: Indicator, s: pd.Series) -> list[list]:
    """اليومي: أسبوعي للتاريخ الأقدم من سنة + يومي لآخر سنة، لتبقى الصفحة خفيفة."""
    t = transform_indicator(ind, s)
    if t.empty:
        return []
    if ind.freq == "D":
        cut = t.index[-1] - pd.DateOffset(years=1)
        old = t[t.index < cut].resample("W-FRI").last().dropna()
        t = pd.concat([old, t[t.index >= cut]])
    return [[d.strftime("%Y-%m-%d"), _r(v)] for d, v in t.items()]


def _last(series, sid):
    s = series.get(sid)
    if s is None or s.empty:
        return None, None
    return float(s.iloc[-1]), s.index[-1]


def _chg(series, sid, periods):
    s = series.get(sid)
    if s is None or len(s) <= periods:
        return None
    return float(s.iloc[-1] - s.iloc[-1 - periods])


def nq_gates(series: dict[str, pd.Series], cfg: dict) -> list[dict]:
    """بوابات NQ الكلية: مستويات الحسم التي يقرأ عليها المتداول NY AM."""
    g = cfg["gates"]
    out = []
    y10, d10 = _last(series, "DGS10")
    if y10 is not None:
        trig = g["us10y_trigger"]
        dist = (y10 - trig) * 100
        wk = _chg(series, "DGS10", 5)
        out.append({
            "key": "us10y", "title": "بوابة العائد 10Y", "value": f"{y10:.2f}%",
            "detail": (f"فوق الحد {trig:.2f}% بـ{dist:+.0f} bp: ضغط على تقييمات NQ" if dist > 0
                       else f"تحت الحد {trig:.2f}% بـ{abs(dist):.0f} bp: البوابة مفتوحة"),
            "extra": None if wk is None else f"أسبوع: {wk * 100:+.0f} bp",
            "tone": "bad" if dist > 0 else ("warn" if dist > -10 else "ok"),
            "as_of": d10.strftime("%Y-%m-%d"),
        })
    vix, dv = _last(series, "VIXCLS")
    if vix is not None:
        calm, stress = g["vix_calm"], g["vix_stress"]
        out.append({
            "key": "vix", "title": "بوابة VIX", "value": f"{vix:.1f}",
            "detail": ("توسّع تذبذب: Trend days وحركات عنيفة" if vix >= stress else
                       "منطقة توتر: حذر في الحجم" if vix >= calm else "تذبذب منخفض: Mean reversion أرجح"),
            "extra": f"الحدود {calm:g} / {stress:g}",
            "tone": "bad" if vix >= stress else ("warn" if vix >= calm else "ok"),
            "as_of": dv.strftime("%Y-%m-%d"),
        })
    real, dr = _last(series, "DFII10")
    if real is not None:
        lim = g["real10_restrictive"]
        out.append({
            "key": "real10", "title": "العائد الحقيقي 10Y", "value": f"{real:.2f}%",
            "detail": "مقيِّد لأسهم النمو" if real > lim else "غير مقيِّد",
            "extra": f"الحد {lim:.2f}%", "tone": "warn" if real > lim else "ok",
            "as_of": dr.strftime("%Y-%m-%d"),
        })
    y2, d2 = _last(series, "DGS2")
    ff, _ = _last(series, "FEDFUNDS")
    if y2 is not None and ff is not None:
        gap = (y2 - ff) * 100
        out.append({
            "key": "pricing", "title": "تسعير الفيدرالي (2Y − FF)", "value": f"{gap:+.0f} bp",
            "detail": ("السوق يسعّر خفضاً للفائدة" if gap < -25 else
                       "السوق يسعّر رفعاً للفائدة" if gap > 25 else "السوق يسعّر ثباتاً تقريبياً"),
            "extra": f"2Y {y2:.2f}% · FF {ff:.2f}%", "tone": "neutral", "as_of": d2.strftime("%Y-%m-%d"),
        })
    ndx, sp = series.get("NASDAQ100"), series.get("SP500")
    if ndx is not None and sp is not None:
        ratio = (ndx / sp).dropna()
        if len(ratio) > 21:
            r20 = (ratio.iloc[-1] / ratio.iloc[-21] - 1) * 100
            out.append({
                "key": "leadership", "title": "قيادة NQ مقابل ES (20 يوماً)", "value": f"{r20:+.2f}%",
                "detail": "Nasdaq يقود" if r20 > 0.5 else ("S&P يقود: دوران بعيداً عن النمو" if r20 < -0.5 else "تكافؤ"),
                "extra": "نسبة NDX/SPX", "tone": "ok" if r20 > 0.5 else ("warn" if r20 < -0.5 else "neutral"),
                "as_of": ratio.index[-1].strftime("%Y-%m-%d"),
            })
    hy, dh = _last(series, "BAMLH0A0HYM2")
    if hy is not None:
        lim = g["hy_stress"]
        out.append({
            "key": "credit", "title": "الائتمان (High Yield OAS)", "value": f"{hy:.2f}%",
            "detail": "ضغط ائتماني يسبق الأسهم عادة" if hy > lim else "لا ضغط ائتماني",
            "extra": f"الحد {lim:.1f}%", "tone": "bad" if hy > lim else "ok", "as_of": dh.strftime("%Y-%m-%d"),
        })
    return out


def kpis(snap: pd.DataFrame) -> list[dict]:
    rows = snap.set_index("id")
    out = []
    for sid in KPI_IDS:
        if sid not in rows.index:
            continue
        r = rows.loc[sid]
        out.append({"id": sid, "label": r["name_ar"], "value": _r(r["last"]), "unit": r["unit"],
                    "change": _r(r["change"]), "good_up": r["good_up"], "as_of": r["as_of"].strftime("%Y-%m-%d")})
    return out


def build_payload(series: dict[str, pd.Series], status: dict[str, dict], cfg: dict,
                  now: datetime | None = None, demo: bool = False) -> dict:
    now = now or datetime.now(timezone.utc)
    now_ts = pd.Timestamp(now).tz_localize(None) if pd.Timestamp(now).tzinfo else pd.Timestamp(now)
    snap = snapshot(series)
    snap_rows = {r["id"]: r for r in snap.to_dict("records")}
    indicators = []
    warnings = []
    for ind in INDICATORS:
        s = series.get(ind.id)
        st = dict(status.get(ind.id, {"state": "ok", "issues": []}))
        if s is None or s.empty or ind.id not in snap_rows:
            indicators.append({"id": ind.id, "name_ar": ind.name_ar, "name_en": ind.name_en,
                               "category": ind.category, "state": "error",
                               "issues": st.get("issues") or ["لا بيانات"]})
            warnings.append(f"{ind.name_ar} ({ind.id}): لا بيانات")
            continue
        age = staleness_days(ind, s, now_ts)
        if age > MAX_AGE_OVERRIDE.get(ind.id, MAX_AGE_DAYS[ind.freq]) and st["state"] == "ok":
            st["state"] = "stale"
            st["issues"] = st["issues"] + [f"آخر قيمة عمرها {age} يوماً"]
        if st["issues"]:
            warnings.append(f"{ind.name_ar} ({ind.id}): {'؛ '.join(st['issues'])}")
        r = snap_rows[ind.id]
        indicators.append({
            "id": ind.id, "name_ar": ind.name_ar, "name_en": ind.name_en, "category": ind.category,
            "freq": ind.freq, "transform": ind.transform, "unit": ind.unit, "good_up": ind.good_up,
            "last": _r(r["last"]), "prev": _r(r["prev"]), "change": _r(r["change"]),
            "z5": _r(r["z5"], 2), "pct5": _r(r["pct5"], 1), "as_of": r["as_of"].strftime("%Y-%m-%d"),
            "age_days": age, "state": st["state"], "issues": st["issues"],
            "points": chart_points(ind, s),
        })
    q = growth_inflation_quadrant(series)
    if q:
        q = {**q, "as_of": q["as_of"].strftime("%Y-%m-%d"),
             **{k: _r(q[k], 3) for k in ("indpro_yoy", "payems_avg3", "corepce_yoy")}}
    sahm = sahm_rule(series["UNRATE"]).dropna() if "UNRATE" in series else pd.Series(dtype=float)
    return {
        "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "demo": demo,
        "source": "FRED, Federal Reserve Bank of St. Louis",
        "config": {"gates": cfg["gates"]},
        "categories": [{"key": k, "ar": ar, "en": en} for k, (ar, en) in CATEGORIES.items()],
        "kpis": kpis(snap),
        "gates": nq_gates(series, cfg),
        "signals": signals(series),
        "quadrant": q,
        "sahm": {"trigger": SAHM_TRIGGER,
                 "points": [[d.strftime("%Y-%m-%d"), _r(v, 3)] for d, v in sahm.items()]},
        "indicators": indicators,
        "status": {"ok": sum(1 for i in indicators if i["state"] == "ok"), "total": len(indicators),
                   "warnings": warnings},
    }


def write_json(path: str | Path, payload: dict) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
                          encoding="utf-8")
