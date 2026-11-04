from __future__ import annotations

import argparse

from macro_platform.catalog import INDICATORS, validate_catalog


def main(argv: list[str] | None = None) -> int:
    validate_catalog()
    p = argparse.ArgumentParser(prog="macro_platform", description="منصة الاقتصاد الكلي الأمريكي")
    p.add_argument("cmd", choices=["list"], help="list: اعرض كتالوج المؤشرات (للتحديث استخدم fetch_macro.py)")
    p.parse_args(argv)
    for i in INDICATORS:
        print(f"{i.id:14s} {i.freq}  {i.transform:9s} {i.name_ar}  ({i.name_en})")
    return 0
