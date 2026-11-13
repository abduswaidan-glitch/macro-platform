"""يقرر في كل دورة أخبار هل تطلب تشغيل update.yml لتقرير ما قبل الافتتاح. لا يطلب شيئاً بنفسه.

    python report_trigger.py --runs runs.json --state state.json
يطبع سطراً واحداً يبدأ بـ dispatch أو skip، ويخرج دائماً بـ0. news.yml يطلب التشغيل عند dispatch فقط.
runs.json: `gh run list --workflow update.yml --json status,event,createdAt`؛ state.json: data/state.json من main.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from macro_platform import ai_report

ROOT = Path(__file__).resolve().parent


def _read(path: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def main(argv=None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    args = ap.parse_args(argv)
    runs, state, cfg = _read(args.runs), _read(args.state), _read(args.config)
    if not isinstance(runs, list) or not isinstance(state, dict) or not isinstance(cfg, dict):
        print("skip: could not read the run list, state.json or config.json (no request without all three)")
        return 0
    ok, why = ai_report.dispatch_decision(now or datetime.now(timezone.utc), state.get("ai_last_date"), runs,
                                          cfg.get("ai", {}))
    print(("dispatch: " if ok else "skip: ") + why)
    return 0


if __name__ == "__main__":
    sys.exit(main())
