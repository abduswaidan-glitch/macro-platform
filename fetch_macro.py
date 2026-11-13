"""نقطة التشغيل: جلب FRED → فحص → data.json → (تقرير Claude إن حان موعده).

    python fetch_macro.py            # تحديث حقيقي (يحتاج اتصالاً بـFRED)
    python fetch_macro.py --offline  # إعادة بناء data.json من data/raw بلا جلب ولا تقرير (عند كل نشر)
    python fetch_macro.py --demo     # بيانات تجريبية مولّدة لمعاينة الصفحة محلياً

data.json لا يُحفظ في المستودع: يُبنى عند كل نشر بأحدث كود من السلاسل المحفوظة في data/raw وحالة آخر جلب
(data/fetch_status.json)، فأي تعديل في الكود يظهر في أول نشر بعده.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from macro_platform import ai_report
from macro_platform.build import build_payload, fetch_all, load_config, load_raw, write_json
from macro_platform.catalog import INDICATORS
from macro_platform.demo import demo_series

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def log(*a):
    print("[macro]", *a, flush=True)


def load_status(path: Path) -> tuple[dict, list[str]]:
    if not path.exists():
        return {}, []
    d = json.loads(path.read_text(encoding="utf-8"))
    return d.get("series", {}), d.get("extra_warnings", [])


def save_status(path: Path, status: dict, extra: list[str], when: datetime) -> None:
    path.write_text(json.dumps({"fetched_utc": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "series": status,
                                "extra_warnings": extra}, ensure_ascii=False, indent=1), encoding="utf-8")


def load_saved_series(raw_dir: Path) -> dict:
    out = {}
    for ind in INDICATORS:
        s = load_raw(raw_dir, ind.id)
        if s is not None and len(s):
            out[ind.id] = s
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="بيانات تجريبية مولّدة (لا تُرفع)")
    ap.add_argument("--offline", action="store_true", help="إعادة بناء من data/raw بلا جلب ولا تقرير")
    ap.add_argument("--out", default=str(ROOT / "data.json"))
    args = ap.parse_args(argv)

    cfg = load_config(ROOT / "config.json")
    now = datetime.now(timezone.utc)
    status_p = DATA / "fetch_status.json"
    extra: list[str] = []
    if args.demo:
        series = demo_series(start=cfg["start"], end=now.strftime("%Y-%m-%d"))
        status = {}
    elif args.offline:
        series = load_saved_series(DATA / "raw")
        status, extra = load_status(status_p)
    else:
        series, status = fetch_all(DATA / "raw", cfg["start"], log=log)
    if not series:
        log("no data at all; data.json left unchanged")
        return 1
    payload = build_payload(series, status, cfg, now=now, demo=args.demo)
    payload["status"]["warnings"] += extra
    log(f"{payload['status']['ok']}/{payload['status']['total']} indicators ok")
    for w in payload["status"]["warnings"]:
        log("warning:", w)

    # تقرير Claude
    state_p = DATA / "state.json"
    state = json.loads(state_p.read_text(encoding="utf-8")) if state_p.exists() else {}
    force = os.environ.get("AI_FORCE", "").lower() == "true"
    hist_p = DATA / "ai_history.json"
    if args.demo or args.offline:
        log("AI report skipped (demo/offline build)")
    elif not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        log("AI report skipped: no ANTHROPIC_API_KEY secret")
    elif ai_report.should_run(now, state.get("ai_last_date"), cfg.get("ai", {}), force):
        try:
            from fetch_news import load_live
            from macro_platform import news
            # يُرسل دائماً مع حالة التغطية، ولو كان المصدر متوقفاً: الغياب الصامت يوحي بأن لا أخبار
            fj = news.report_feed(load_live("https://abduswaidan-glitch.github.io/macro-platform/news.json"), now)
            rep = ai_report.write_report(payload, cfg, now_utc=now, fj=fj)
            ai_report.update_history(hist_p, rep, keep=cfg.get("ai", {}).get("keep", 14))
            state["ai_last_date"] = rep["meta"]["ny_date"]
            state_p.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
            u = rep["meta"]["usage"]
            cost = ai_report.estimate_cost(rep["meta"]["model"], u)
            log(f"AI report written ({rep['meta']['model']}, {u}"
                + (f", est. ${cost}" if cost is not None else "") + ")")   # الكلفة في السجل فقط
        except Exception as exc:
            log("AI report FAILED:", repr(exc)[:300])
            msg = f"فشل كتابة تقرير Claude: {str(exc)[:160]}"
            payload["status"]["warnings"].append(msg)
            extra.append(msg)
    payload["ai_available"] = hist_p.exists()
    if not (args.demo or args.offline):
        DATA.mkdir(exist_ok=True)
        save_status(status_p, status, extra, now)

    DATA.mkdir(exist_ok=True)
    write_json(args.out, payload)
    log("wrote", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
