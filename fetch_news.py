"""يبني مجلد الموقع للنشر: ملفات الصفحة من المستودع + news.json المحدّث من FinancialJuice.

    python fetch_news.py --site _site                 # جلب وترجمة ثم بناء الموقع
    python fetch_news.py --site _site --no-fetch      # بناء الموقع فقط مع آخر news.json منشور

الحالة (العناوين وترجماتها) تُقرأ من news.json المنشور حالياً على الموقع، فلا شيء منها يدخل المستودع.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from macro_platform import news

ROOT = Path(__file__).resolve().parent
LIVE = "https://abduswaidan-glitch.github.io/macro-platform/news.json"
SITE_FILES = ["index.html", "data.json", ".nojekyll", "data/ai_history.json"]


def log(*a):
    print("[news]", *a, flush=True)


def load_live(url: str, tries: int = 3, wait: float = 5.0) -> dict | None:
    """news.json المنشور الآن، أو None إن تعذّرت قراءته بعد tries محاولات.

    الملف المنشور هو المخزن الوحيد لتراكم التقويم والعناوين، فالفشل لا يُعامل كملف فارغ: في 13 نوفمبر 15:11Z أعادت
    قراءة عابرة HTTPError، فبُني news.json من لا شيء ونُشر فوق الأصل (107 أحداث ← 0) وقرأت كل دورة بعدها الفارغ."""
    err = ""
    for k in range(1, tries + 1):
        try:
            req = urllib.request.Request(url + "?t=" + str(int(datetime.now().timestamp())),
                                         headers={"User-Agent": "macro-platform/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read().decode("utf-8"))
            if isinstance(d, dict):
                return d
            err = "not a JSON object"
        except Exception as e:
            err = f"{type(e).__name__} {getattr(e, 'code', '') or ''}".strip()
        log(f"live news.json read failed ({k}/{tries}): {err}")
        if k < tries:
            time.sleep(wait * k)
    return None


def build_site(site: Path) -> None:
    site.mkdir(parents=True, exist_ok=True)
    for f in SITE_FILES:
        src = ROOT / f
        if src.exists():
            (site / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, site / f)
        elif f == "data.json":
            log("WARNING: data.json missing; run `python fetch_macro.py --offline` before building the site")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="_site")
    ap.add_argument("--seed-url", default=LIVE, help="'' = لا news.json منشور بعد (أول نشر فقط)")
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args(argv)
    site = Path(args.site)
    build_site(site)

    now = datetime.now(timezone.utc)
    prev = load_live(args.seed_url) if args.seed_url else {}
    if prev is None:
        log("NOT publishing: the live news.json could not be read, and rebuilding it from nothing would erase the "
            "accumulated calendar. The live site stays as it is; the next run tries again.")
        return 1
    store = {str(i["id"]): i for i in prev.get("items", []) if isinstance(i, dict) and "id" in i}
    cal = {str(e["id"]): e for e in prev.get("calendar", []) if isinstance(e, dict) and "id" in e}
    errors: list[str] = []
    ok = {"items": False, "calendar": False}   # نجاح الجلب في هذه الدورة فقط؛ الفشل يُبقي آخر نجاح منشور
    translator = prev.get("translator")
    raw_key = os.environ.get("FJ_API_KEY", "")
    key = raw_key.strip().strip("\"'").strip()  # يتسامح مع اقتباس أو مسافة أو سطر جديد لُصق مع المفتاح
    if raw_key:
        # وصف شكل المفتاح فقط (لا المفتاح): يكشف أخطاء اللصق دون تسريب
        log(f"key shape: length {len(key)}, starts with fj_: {key.startswith('fj_')}, "
            f"trimmed {len(raw_key) - len(key)} chars, inner spaces: {any(c.isspace() for c in key)}")

    if not args.no_fetch:
        if not key:
            errors.append("لا يوجد السر FJ_API_KEY")
            log("no FJ_API_KEY")
        else:
            try:
                fresh = news.fetch(key, news.since_for(store, now))
                changed = news.merge(store, fresh)
                ok["items"] = True
                log(f"fetched {len(fresh)}, changed {changed}")
            except Exception as e:
                errors.append(str(e)[:160])
                log("fetch failed:", str(e)[:160])
            try:
                fresh_cal = news.fetch_calendar(key, now)
                log(f"calendar: fetched {len(fresh_cal)}, changed {news.merge_calendar(cal, fresh_cal, now)}")
                ok["calendar"] = True
            except Exception as e:
                errors.append("التقويم: " + str(e)[:140])
                log("calendar fetch failed:", str(e)[:160])
        store = news.prune(store, now)
        cal = news.prune_calendar(cal, now)
        akey = os.environ.get("ANTHROPIC_API_KEY", "")
        clean = akey.strip().strip("\"'").strip()
        if clean:
            os.environ["ANTHROPIC_API_KEY"] = clean
            # وصف شكل المفتاح فقط: sk-ant-api = مفتاح API صحيح الشكل؛ sk-ant-oat = رمز اشتراك لا يعمل هنا
            log(f"anthropic key shape: length {len(clean)}, prefix {clean[:10]!r}, trimmed {len(akey) - len(clean)} chars")
        if clean:
            done, terr = news.translate_missing(store)
            cdone, cerr = news.translate_missing(cal, field="title")
            errors += terr + cerr
            done += cdone
            translator = news.TRANSLATOR if (done or translator) else None
            log(f"translated {done} with {news.TRANSLATOR}" if done else "nothing to translate")
        else:
            log("no ANTHROPIC_API_KEY: headlines shown in English")
        out = news.payload(store, now, translator, errors, calendar=cal,
                           last_success=news.last_success(prev, now, ok))
    else:
        out = prev or news.payload({}, now, None, ["لم تُجلب الأخبار بعد"])

    (site / "news.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"site built: {len(out.get('items', []))} headlines, {len(out.get('calendar', []))} calendar events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
