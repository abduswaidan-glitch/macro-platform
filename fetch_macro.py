"""نقطة التشغيل: جلب FRED → فحص → data.json → (تقرير Claude إن حان موعده).

    python fetch_macro.py            # تحديث حقيقي (يحتاج اتصالاً بـFRED)
    python fetch_macro.py --demo     # بيانات تجريبية مولّدة لمعاينة الصفحة محلياً
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from macro_platform import ai_report
from macro_platform.build import build_payload, fetch_all, load_config, write_json
from macro_platform.demo import demo_series

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def log(*a):
    print("[macro]", *a, flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="بيانات تجريبية مولّدة (لا تُرفع)")
    ap.add_argument("--out", default=str(ROOT / "data.json"))
    args = ap.parse_args(argv)

    cfg = load_config(ROOT / "config.json")
    now = datetime.now(timezone.utc)
    if args.demo:
        series = demo_series(start=cfg["start"], end=now.strftime("%Y-%m-%d"))
        status = {}
    else:
        series, status = fetch_all(DATA / "raw", cfg["start"], log=log)
    if not series:
        log("no data at all; data.json left unchanged")
        return 1
    payload = build_payload(series, status, cfg, now=now, demo=args.demo)
    log(f"{payload['status']['ok']}/{payload['status']['total']} indicators ok")
    for w in payload["status"]["warnings"]:
        log("warning:", w)

    # تقرير Claude
    state_p = DATA / "state.json"
    state = json.loads(state_p.read_text(encoding="utf-8")) if state_p.exists() else {}
    force = os.environ.get("AI_FORCE", "").lower() == "true"
    hist_p = DATA / "ai_history.json"
    if args.demo:
        log("AI report skipped in demo mode")
    elif not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        log("AI report skipped: no ANTHROPIC_API_KEY secret")
    elif ai_report.should_run(now, state.get("ai_last_date"), cfg.get("ai", {}), force):
        try:
            rep = ai_report.write_report(payload, cfg, now_utc=now)
            ai_report.update_history(hist_p, rep, keep=cfg.get("ai", {}).get("keep", 14))
            state["ai_last_date"] = rep["meta"]["ny_date"]
            state_p.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
            log(f"AI report written ({rep['meta']['model']}, {rep['meta']['usage']})")
        except Exception as exc:
            log("AI report FAILED:", repr(exc)[:300])
            payload["status"]["warnings"].append(f"فشل كتابة تقرير Claude: {str(exc)[:160]}")
    payload["ai_available"] = hist_p.exists()

    DATA.mkdir(exist_ok=True)
    write_json(args.out, payload)
    log("wrote", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
