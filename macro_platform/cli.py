from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from macro_platform.analytics import snapshot
from macro_platform.catalog import INDICATORS, validate_catalog
from macro_platform.dashboard import build_html
from macro_platform.demo import demo_series
from macro_platform.fred import FredError, SeriesStore

DEFAULT_CACHE = Path("data/macro")
DEFAULT_OUT = Path("reports/macro/index.html")


def _load(args) -> tuple[dict, bool]:
    if args.demo:
        return demo_series(), True
    store = SeriesStore(args.cache)
    errors: list[str] = []
    series = store.get_all(
        [i.id for i in INDICATORS], start=args.start, refresh=args.refresh,
        on_error=lambda sid, exc: errors.append(str(exc)),
    )
    for e in errors:
        print(f"تحذير: {e}", file=sys.stderr)
    if not series:
        raise FredError("لا توجد بيانات محلية ولا من FRED. شغّل `fetch` أولاً أو استخدم --demo")
    return series, False


def cmd_list(_args) -> int:
    for i in INDICATORS:
        print(f"{i.id:14s} {i.freq}  {i.transform:9s} {i.name_ar}  ({i.name_en})")
    return 0


def cmd_fetch(args) -> int:
    series, _ = _load(args)
    print(f"تم تحميل {len(series)} سلسلة إلى {args.cache}")
    return 0


def cmd_report(args) -> int:
    series, demo = _load(args)
    snap = snapshot(series)
    html_text = build_html(series, generated=datetime.now(timezone.utc), demo=demo)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")
    print(f"اللوحة: {out.resolve()} ({len(snap)} مؤشر)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="macro_platform", description="منصة الاقتصاد الكلي الأمريكي")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--cache", default=str(DEFAULT_CACHE), help="مجلد التخزين المحلي")
        sp.add_argument("--start", default="1990-01-01", help="تاريخ بداية الجلب")
        sp.add_argument("--refresh", action="store_true", help="أعد الجلب من FRED بدل الكاش")
        sp.add_argument("--demo", action="store_true", help="استخدم بيانات تجريبية مولّدة")

    sub.add_parser("list", help="اعرض كتالوج المؤشرات").set_defaults(func=cmd_list)
    common(sub.add_parser("fetch", help="اجلب السلاسل من FRED وخزّنها محلياً"))
    sp = sub.add_parser("report", help="ولّد لوحة HTML")
    common(sp)
    sp.add_argument("--out", default=str(DEFAULT_OUT))
    sp.set_defaults(func=cmd_report)
    return p


def main(argv: list[str] | None = None) -> int:
    validate_catalog()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FredError as exc:
        print(f"خطأ: {exc}", file=sys.stderr)
        return 2
