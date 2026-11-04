"""يولّد لوحة HTML ثابتة (ملف واحد، بلا CDN) باللغة العربية واتجاه RTL."""

from __future__ import annotations

import html
import math
from datetime import datetime, timezone

import pandas as pd

from macro_platform.analytics import signals, snapshot, transform_indicator
from macro_platform.catalog import CATEGORIES, INDICATORS, Indicator

SPARK_YEARS = 5
SPARK_POINTS = 120

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2330;--mute:#6b7280;--line:#e5e7eb;
--ok:#15803d;--warn:#b45309;--bad:#b91c1c;--neu:#475569;--accent:#1d4ed8}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#0f1419;--card:#171d26;
--ink:#e6e9ef;--mute:#8b95a5;--line:#2a3340;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--neu:#94a3b8;--accent:#60a5fa}}
:root[data-theme="dark"]{--bg:#0f1419;--card:#171d26;--ink:#e6e9ef;--mute:#8b95a5;--line:#2a3340;
--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--neu:#94a3b8;--accent:#60a5fa}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:system-ui,"Segoe UI",Tahoma,sans-serif;
line-height:1.6;padding:16px}
header{max-width:1200px;margin:0 auto 16px}
h1{font-size:1.5rem;margin:0 0 4px}
.sub{color:var(--mute);font-size:.9rem}
h2{font-size:1.05rem;margin:24px 0 8px;color:var(--accent)}
.wrap{max-width:1200px;margin:0 auto}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card .t{font-size:.85rem;color:var(--mute)}
.card .v{font-size:1.25rem;font-weight:600;margin:2px 0}
.card .d{font-size:.85rem}
.tone-ok{color:var(--ok)} .tone-warn{color:var(--warn)} .tone-bad{color:var(--bad)} .tone-neutral{color:var(--neu)}
.tbl{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
border-radius:10px;overflow:hidden;font-size:.9rem}
.tbl th,.tbl td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:start;white-space:nowrap}
.tbl th{color:var(--mute);font-weight:500;font-size:.8rem}
.tbl td.num{font-variant-numeric:tabular-nums}
.en{color:var(--mute);font-size:.78rem;direction:ltr;text-align:start}
.wrapx{overflow-x:auto;border-radius:10px}
svg.sp{display:block;width:130px;height:34px}
.foot{color:var(--mute);font-size:.8rem;margin:24px 0 8px}
"""


def fmt(v: float, unit: str = "") -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    s = f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.2f}"
    return s + (f" {unit}" if unit and unit != "%" else ("%" if unit == "%" else ""))


def _change_tone(change: float, good_up: bool | None) -> str:
    if good_up is None or change == 0 or math.isnan(change):
        return "neutral"
    improving = change > 0 if good_up else change < 0
    return "ok" if improving else "bad"


def sparkline(s: pd.Series) -> str:
    if s is None or s.empty:
        return ""
    end = s.index[-1]
    s = s[s.index >= end - pd.DateOffset(years=SPARK_YEARS)]
    if len(s) > SPARK_POINTS:
        s = s.iloc[:: max(1, len(s) // SPARK_POINTS)]
    vals = s.to_numpy(dtype=float)
    lo, hi = float(vals.min()), float(vals.max())
    span = (hi - lo) or 1.0
    w, h, pad = 130, 34, 2
    n = len(vals)
    pts = " ".join(
        f"{pad + i * (w - 2 * pad) / max(n - 1, 1):.1f},{h - pad - (v - lo) / span * (h - 2 * pad):.1f}"
        for i, v in enumerate(vals)
    )
    return (f'<svg class="sp" viewBox="0 0 {w} {h}" aria-hidden="true">'
            f'<polyline fill="none" stroke="var(--accent)" stroke-width="1.5" points="{pts}"/></svg>')


def _rows(series: dict[str, pd.Series], snap: pd.DataFrame, cat: str) -> str:
    out = []
    for _, r in snap[snap["category"] == cat].iterrows():
        ind: Indicator = next(i for i in INDICATORS if i.id == r["id"])
        change = r["change"]
        arrow = "▲" if change > 0 else ("▼" if change < 0 else "•")
        tone = _change_tone(change, r["good_up"])
        z = "—" if math.isnan(r["z5"]) else f"{r['z5']:+.1f}σ"
        pct = "—" if math.isnan(r["pct5"]) else f"{r['pct5']:.0f}"
        spark = sparkline(transform_indicator(ind, series[ind.id]))
        out.append(
            "<tr>"
            f'<td>{html.escape(r["name_ar"])}<div class="en">{html.escape(r["name_en"])}</div></td>'
            f'<td class="num"><bdi dir="ltr">{fmt(r["last"], r["unit"])}</bdi></td>'
            f'<td class="num tone-{tone}">{arrow} {fmt(abs(change), "")}</td>'
            f'<td class="num">{z}</td>'
            f'<td class="num">{pct}</td>'
            f'<td>{spark}</td>'
            f'<td class="num">{html.escape(r["as_of"].strftime("%Y-%m-%d"))}</td>'
            "</tr>"
        )
    return "".join(out)


def build_html(series: dict[str, pd.Series], generated: datetime | None = None,
               demo: bool = False) -> str:
    generated = generated or datetime.now(timezone.utc)
    snap = snapshot(series)
    cards = "".join(
        f'<div class="card"><div class="t">{html.escape(c["title"])}</div>'
        f'<div class="v tone-{c["tone"]}"><bdi dir="ltr">{html.escape(c["value"])}</bdi></div>'
        f'<div class="d">{html.escape(c["detail"])}</div></div>'
        for c in signals(series)
    )
    tables = []
    for cat, (ar, en) in CATEGORIES.items():
        if snap[snap["category"] == cat].empty:
            continue
        tables.append(
            f'<h2>{html.escape(ar)} <span class="en">{html.escape(en)}</span></h2>'
            '<div class="wrapx"><table class="tbl"><thead><tr>'
            '<th>المؤشر</th><th>آخر قيمة</th><th>التغير</th><th>Z (5y)</th><th>المئوي (5y)</th>'
            '<th>الاتجاه (5y)</th><th>آخر تحديث</th></tr></thead><tbody>'
            f'{_rows(series, snap, cat)}</tbody></table></div>'
        )
    demo_note = ("<br>بيانات تجريبية مولّدة للعرض فقط، وليست أرقاماً حقيقية." if demo else "")
    return f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>منصة الاقتصاد الكلي الأمريكي</title>
<style>{CSS}</style>
</head>
<body>
<header class="wrap">
<h1>منصة الاقتصاد الكلي الأمريكي <span class="en">US Macro Desk</span></h1>
<div class="sub">مصدر البيانات: FRED (Federal Reserve Bank of St. Louis) · آخر توليد: {generated:%Y-%m-%d %H:%M} UTC{demo_note}</div>
</header>
<main class="wrap">
<h2>الإشارات الرئيسية</h2>
<div class="cards">{cards}</div>
{''.join(tables)}
<div class="foot">Z (5y) = انحراف المؤشر عن متوسطه خلال 5 سنوات بالانحرافات المعيارية. المئوي = نسبة القيم
التاريخية الأدنى من القيمة الحالية. التصنيفات وصفية ولا تمثل توصية استثمارية.</div>
</main>
</body>
</html>
"""
