"""تحويلات المؤشرات، ومؤشرات مشتقة (Sahm, Curve, Real rates, Growth/Inflation quadrant)."""

from __future__ import annotations

import math

import pandas as pd

from macro_platform.catalog import BY_ID, PERIODS_PER_YEAR, Indicator, INDICATORS

SAHM_TRIGGER = 0.5
HOT_CORE_PCE = 2.5  # أعلى من هدف الاحتياطي الفيدرالي (2%) بهامش
ZSCORE_YEARS = 5


def transform(s: pd.Series, kind: str, freq: str) -> pd.Series:
    if kind == "level":
        return s
    if kind == "yoy":
        return s.pct_change(PERIODS_PER_YEAR[freq]) * 100
    if kind == "diff":
        return s.diff()
    if kind == "diff_avg3":
        return s.diff().rolling(3).mean()
    raise ValueError(f"unknown transform {kind}")


def transform_indicator(ind: Indicator, s: pd.Series) -> pd.Series:
    return transform(s, ind.transform, ind.freq).dropna()


def _nan(x) -> float:
    return float("nan") if x is None else float(x)


def snapshot(series: dict[str, pd.Series], indicators=INDICATORS) -> pd.DataFrame:
    """جدول لقطة: آخر قيمة، التغير، Z-score ونسبة المئوي على نافذة 5 سنوات."""
    rows = []
    for ind in indicators:
        raw = series.get(ind.id)
        if raw is None or raw.empty:
            continue
        t = transform_indicator(ind, raw)
        if t.empty:
            continue
        last_date = t.index[-1]
        last = float(t.iloc[-1])
        prev = float(t.iloc[-2]) if len(t) > 1 else float("nan")
        window = t[t.index >= last_date - pd.DateOffset(years=ZSCORE_YEARS)]
        sd = window.std()
        z = (last - window.mean()) / sd if len(window) >= 12 and sd and sd > 0 else float("nan")
        pct = float((window <= last).mean() * 100) if len(window) else float("nan")
        rows.append({
            "id": ind.id, "category": ind.category, "name_ar": ind.name_ar, "name_en": ind.name_en,
            "unit": ind.unit, "freq": ind.freq, "good_up": ind.good_up,
            "as_of": last_date, "last": last, "prev": prev, "change": last - prev,
            "z5": z, "pct5": pct,
        })
    return pd.DataFrame(rows)


def sahm_rule(unrate: pd.Series) -> pd.Series:
    """Sahm Rule: متوسط البطالة 3 أشهر ناقص أدنى متوسط 3 أشهر خلال الـ12 شهراً السابقة."""
    u3 = unrate.rolling(3).mean()
    return u3 - u3.shift(1).rolling(12).min()


def monthly_panel(series: dict[str, pd.Series]) -> pd.DataFrame:
    """يرسم المؤشرات الثلاثة اللازمة للـquadrant على شبكة شهرية (آخر قيمة معروفة لكل شهر)."""
    parts = {}
    if "INDPRO" in series:
        parts["indpro_yoy"] = transform(series["INDPRO"], "yoy", "M")
    if "PAYEMS" in series:
        parts["payems_avg3"] = transform(series["PAYEMS"], "diff_avg3", "M")
    if "PCEPILFE" in series:
        parts["corepce_yoy"] = transform(series["PCEPILFE"], "yoy", "M")
    if not parts:
        return pd.DataFrame()
    panel = pd.DataFrame(parts)
    return panel.resample("ME").last().ffill()


def growth_inflation_quadrant(series: dict[str, pd.Series]) -> dict | None:
    """تصنيف مبسّط: النمو صاعد إذا كان الإنتاج الصناعي (تغير سنوي) والتوظيف (متوسط 3 أشهر) موجبين معاً.
    التضخم ساخن إذا تجاوز Core PCE (تغير سنوي) الحد HOT_CORE_PCE. هذا تصنيف وصفي وليس توقعاً."""
    panel = monthly_panel(series).dropna()
    if panel.empty:
        return None
    row = panel.iloc[-1]
    growth_up = bool(row["indpro_yoy"] > 0 and row["payems_avg3"] > 0)
    hot = bool(row["corepce_yoy"] > HOT_CORE_PCE)
    names = {
        (True, True): ("تسخين اقتصادي (Reflation)", "warn"),
        (True, False): ("نمو بلا تضخم (Goldilocks)", "ok"),
        (False, True): ("ركود تضخمي (Stagflation)", "bad"),
        (False, False): ("تباطؤ انكماشي (Disinflation)", "warn"),
    }
    label, tone = names[(growth_up, hot)]
    return {
        "as_of": panel.index[-1], "growth_up": growth_up, "inflation_hot": hot,
        "label_ar": label, "tone": tone,
        "indpro_yoy": float(row["indpro_yoy"]), "payems_avg3": float(row["payems_avg3"]),
        "corepce_yoy": float(row["corepce_yoy"]),
    }


def _last(s: pd.Series | None) -> float | None:
    if s is None:
        return None
    s = s.dropna()
    return None if s.empty else float(s.iloc[-1])


def signals(series: dict[str, pd.Series]) -> list[dict]:
    """بطاقات الإشارات للوحة. كل بطاقة: عنوان، قيمة، شرح، ولون (ok/warn/bad/neutral)."""
    cards: list[dict] = []

    if "UNRATE" in series:
        sahm = sahm_rule(series["UNRATE"]).dropna()
        if not sahm.empty:
            v = float(sahm.iloc[-1])
            trig = v >= SAHM_TRIGGER
            cards.append({
                "title": "Sahm Rule", "value": f"{v:+.2f} pp",
                "detail": ("مفعّلة: البطالة ترتفع بسرعة تاريخية (علامة ركود)" if trig
                           else f"غير مفعّلة. العتبة {SAHM_TRIGGER:.2f} pp"),
                "tone": "bad" if trig else "ok",
            })

    curve = _last(series.get("T10Y2Y"))
    if curve is not None:
        cards.append({
            "title": "منحنى العائد 10Y-2Y", "value": f"{curve:+.2f}%",
            "detail": "مقلوب (Inverted): تاريخياً يسبق الركود" if curve < 0 else "طبيعي (Normal)",
            "tone": "warn" if curve < 0 else "ok",
        })

    real = _last(series.get("DFII10"))
    if real is not None:
        cards.append({
            "title": "العائد الحقيقي 10Y", "value": f"{real:.2f}%",
            "detail": "تشديد نقدي حقيقي (أعلى من 2%)" if real > 2 else "شروط نقدية حقيقية مخففة",
            "tone": "warn" if real > 2 else "ok",
        })

    be = _last(series.get("T10YIE"))
    if be is not None:
        cards.append({
            "title": "توقعات التضخم 10Y (Breakeven)", "value": f"{be:.2f}%",
            "detail": "فوق 2.5%: توقعات تضخم مرتفعة" if be > 2.5 else "قريبة من هدف الفيدرالي",
            "tone": "warn" if be > 2.5 else "ok",
        })

    hy = _last(series.get("BAMLH0A0HYM2"))
    if hy is not None:
        cards.append({
            "title": "فارق High Yield", "value": f"{hy:.2f}%",
            "detail": "ضغط ائتماني (فوق 5%)" if hy > 5 else ("توتر معتدل" if hy > 4 else "هدوء ائتماني"),
            "tone": "bad" if hy > 5 else ("warn" if hy > 4 else "ok"),
        })

    vix = _last(series.get("VIXCLS"))
    if vix is not None:
        cards.append({
            "title": "مؤشر VIX", "value": f"{vix:.1f}",
            "detail": "خوف مرتفع (فوق 25)" if vix > 25 else ("توتر (20-25)" if vix > 20 else "هدوء نسبي"),
            "tone": "bad" if vix > 25 else ("warn" if vix > 20 else "ok"),
        })

    q = growth_inflation_quadrant(series)
    if q is not None:
        cards.append({
            "title": "المربع: النمو × التضخم", "value": q["label_ar"],
            "detail": (f"الصناعة {q['indpro_yoy']:+.1f}% سنوياً، توظيف {q['payems_avg3']:+.0f}k/شهر، "
                       f"Core PCE {q['corepce_yoy']:.2f}%"),
            "tone": q["tone"],
        })
    return cards


def finite(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


__all__ = ["BY_ID", "transform", "transform_indicator", "snapshot", "sahm_rule", "monthly_panel",
           "growth_inflation_quadrant", "signals", "finite"]
