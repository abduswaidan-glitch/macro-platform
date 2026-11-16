"""نقطة التشغيل: جلب FRED → فحص → data.json → (تقرير Claude إن طُلبت محاولة).

    python fetch_macro.py            # تحديث حقيقي (يحتاج اتصالاً بـFRED)؛ تقرير فقط مع REPORT_ATTEMPT=1|2|manual
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

from macro_platform import ai_report, collectors, ledger
from macro_platform.analytics import signals
from macro_platform.build import (build_payload, fetch_all, indicator_entries, load_config, load_raw, market_relative,
                                  merge_status, nq_gates, public_payload, write_json)
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


def load_fetched(path: Path) -> datetime | None:
    """وقت آخر جلب حقيقي من المصادر (fetched_utc)، أو None إن لم يُعرف. البناء offline لا يجلب، فلا يغيّره."""
    try:
        t = datetime.fromisoformat(str(json.loads(path.read_text(encoding="utf-8"))["fetched_utc"]).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def save_status(path: Path, status: dict, extra: list[str], when: datetime | None) -> None:
    path.write_text(json.dumps({"fetched_utc": when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else None, "series": status,
                                "extra_warnings": extra}, ensure_ascii=False, indent=1), encoding="utf-8")


def apply_market_derived(payload: dict, path: Path, write: bool) -> None:
    """مؤشرا الأسهم داخليان (ترخيص طرف ثالث) فلا يُحفظان في data/raw. ما يُنشر منهما مشتق فقط: الأداء النسبي
    وبوابة قيادة NQ مقابل ES. تشغيل البيانات يحفظ المشتق هنا، والبناء offline (كل 10 دقائق) يقرؤه."""
    lead = next((g for g in payload["gates"] if g.get("key") == "leadership"), None)
    if write and payload.get("market_relative") and lead:
        path.write_text(json.dumps({"generated": payload["generated"], "market_relative": payload["market_relative"],
                                    "leadership_gate": lead}, ensure_ascii=False, indent=1), encoding="utf-8")
        return
    if payload.get("market_relative") or not path.exists():
        return
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    payload["market_relative"] = d.get("market_relative")
    if lead is None and isinstance(d.get("leadership_gate"), dict):
        payload["gates"].append(d["leadership_gate"])


NEWS_URL = "https://abduswaidan-glitch.github.io/macro-platform/news.json"


def collect_sections(cfg: dict, now: datetime, prev_status: dict, calendar, previous, run_log=None):
    """جامعو الأقسام، واحداً واحداً وكلٌّ في عزله: يجلب سلاسل قسمه من FRED بنفسه ويبني ملفه. ثم جامع Market Gates
    (أرقام مشتقة فقط) من سلاسل المؤشرين والسلاسل التي تقوم عليها حدود NQ."""
    run_log = run_log or log
    series, status, briefs = {}, {}, {}
    for key in collectors.ECONOMY + (collectors.MARKETS,):
        try:
            inds = collectors.section_indicators(key)
            s_k, st_k = fetch_all(DATA / "raw", cfg["start"], indicators=inds, log=run_log)
            st_k = merge_status(prev_status, st_k, now)        # مراجعات آخر 45 يوماً، ووقت آخر قراءة جديدة
            series.update(s_k)
            status.update(st_k)
            if key != collectors.MARKETS:
                entries, _ = indicator_entries(s_k, st_k, now, indicators=inds)
                briefs[key] = collectors.brief(key, entries, signals(s_k), calendar, previous, now)
                run_log(f"collector {key}: {briefs[key]['available']}/{briefs[key]['total']} indicators")
        except Exception as exc:                                # عزل: قسم واحد لا يوقف البقية
            briefs[key] = collectors.failed(key, now, exc)
            run_log(f"collector {key} FAILED: {briefs[key]['error']}")
    try:
        briefs[collectors.MARKETS] = collectors.markets_brief(nq_gates(series, cfg), market_relative(series), cfg, now)
    except Exception as exc:
        briefs[collectors.MARKETS] = collectors.failed(collectors.MARKETS, now, exc)
        run_log(f"collector markets FAILED: {briefs[collectors.MARKETS]['error']}")
    return series, status, briefs


def previous_report(hist_p: Path, now: datetime) -> dict | None:
    hist = json.loads(hist_p.read_text(encoding="utf-8")) if hist_p.exists() else []
    today = now.astimezone(ai_report.NY).strftime("%Y-%m-%d")
    older = [h for h in hist if (h.get("meta") or {}).get("ny_date", "") < today]
    return max(older, key=lambda h: h["meta"]["ny_date"]) if older else None


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
    sec_dir = DATA / "sections"
    state_p = DATA / "state.json"
    hist_p = DATA / "ai_history.json"
    state = json.loads(state_p.read_text(encoding="utf-8")) if state_p.exists() else {}
    attempt = os.environ.get("REPORT_ATTEMPT", "").strip().lower()
    ai_cfg = cfg.get("ai", {})
    allowed, why = ai_report.attempt_allowed(attempt, now, state, ai_cfg)
    extra: list[str] = []
    feed = previous = briefs = None
    source = None
    fetched = now                       # وقت آخر جلب حقيقي: الآن في تشغيل يجلب، والمحفوظ في بناء لا يجلب
    man, _ = collectors.load(sec_dir)
    if args.demo:
        series = demo_series(start=cfg["start"], end=now.strftime("%Y-%m-%d"))
        status = {}
    elif args.offline:
        series = load_saved_series(DATA / "raw")
        status, extra = load_status(status_p)
        fetched = load_fetched(status_p)
    else:
        from fetch_news import load_live
        from macro_platform import news
        # يُقرأ دائماً مع حالة التغطية، ولو كان المصدر متوقفاً: الغياب الصامت يوحي بأن لا أخبار
        feed = news.report_feed(load_live(NEWS_URL), now)
        previous = previous_report(hist_p, now)
        reporting = allowed and attempt in ("1", "2", "manual")
        saved_man, saved = collectors.load(sec_dir)
        ready, ready_why = collectors.fresh(saved_man, saved, now)
        # المحاولة 1 (09:00) تأخذ ملفات جمع 08:40 كما هي. المحاولة 2 (12:00) واليدوي يجمعان من جديد قبل الاستدعاء:
        # ما صدر بعد 09:00 (CPI يصل FRED نحو 09:12، والوظائف نحو 10:42) لا يُترك خارج التقرير
        if reporting and ready and attempt == "1":
            # جمع جامعو الأقسام معلوماتهم قبل موعد التقرير: تُسلَّم ملفاتهم كما هي، بلا جلب جديد
            log(f"section inputs ready before the report: {ready_why}")
            series = load_saved_series(DATA / "raw")
            status, extra = load_status(status_p)
            fetched = load_fetched(status_p)        # ملفات جمع 08:40 كما هي: لا جلب في هذا التشغيل
            briefs, source, man = saved, "pre_report_collection", saved_man
        else:
            if reporting:
                log(f"collecting section inputs in the report run, before the model call: {ready_why}")
            prev_status, _ = load_status(status_p)
            series, status, briefs = collect_sections(cfg, now, prev_status, feed.get("calendar"), previous)
            for sid, st in status.items():
                for r in st.get("revisions", []):
                    if r.get("detected_utc", "") >= now.strftime("%Y-%m-%dT%H:%M:%SZ")[:16]:
                        log(f"revision detected: {sid} {r}")
            source = "report_run" if reporting else "pre_report_collection" if attempt == "collect" else "data_run"
            man = collectors.manifest(briefs, now, source, os.environ.get("GITHUB_RUN_ID"))
            collectors.save(sec_dir, briefs, man)
            fresh_now, _ = collectors.fresh(man, briefs, now)
            state["collection"] = {"ny_date": man["ny_date"], "collected_at": man["collected_at"],
                                   "collected_ny": now.astimezone(ai_report.NY).strftime("%H:%M"), "fresh": fresh_now,
                                   "sections_ok": sum(1 for v in man["sections"].values() if v["ok"]),
                                   "total": len(man["sections"]), "trigger": source}
    if not series:
        log("no data at all; data.json left unchanged")
        return 1
    payload = build_payload(series, status, cfg, now=now, demo=args.demo)
    # generated وقت بناء الصفحة (يتجدد كل 10 دقائق)؛ fetched وقت آخر جلب حقيقي، وعليه تُبنى شارة «محدّثة»
    payload["fetched"] = fetched.strftime("%Y-%m-%dT%H:%M:%SZ") if fetched else None
    if not args.demo:
        DATA.mkdir(exist_ok=True)
        apply_market_derived(payload, DATA / "market_derived.json", write=not args.offline)
    payload["status"]["warnings"] += extra
    log(f"{payload['status']['ok']}/{payload['status']['total']} indicators ok")
    for w in payload["status"]["warnings"]:
        log("warning:", w)

    # تقرير Claude: فقط في تشغيل يحمل REPORT_ATTEMPT (1 أو 2 من الجدولة، أو manual بإذن Abdu).
    # تشغيلات cron وتشغيل الجمع 08:40 تحدّث البيانات وملفات الأقسام فقط، فلا مسار ثانٍ لكتابة تقرير مدفوع.
    ledger_p = DATA / "forecast_ledger.json"
    led = ledger.load(ledger_p)
    # الحكم على الشروط من سلاسل FRED نفسها قبل التقرير، فيرى التقرير نتيجة شروط سابقه
    settled = ledger.resolve(led, series)
    payload["ledger"] = ledger.view(led)
    if args.demo or args.offline:
        log("AI report skipped (demo/offline build)")
    elif not allowed:
        log(f"AI report skipped: {why}")
    elif not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        log("AI report skipped: no ANTHROPIC_API_KEY secret")
    else:
        started, rep, err = datetime.now(timezone.utc), None, None
        try:
            rep = ai_report.write_report(payload, cfg, now_utc=now, feed=feed, previous=previous, attempt=attempt,
                                         briefs=briefs, source=source)
            ai_report.update_history(hist_p, rep, keep=ai_cfg.get("keep", 14))
            if attempt in ai_report.SCHEDULED:          # Forecast واحد لكل جلسة، من المحاولة المجدولة الناجحة فقط
                ledger.add(led, ledger.entry(rep, ai_cfg))
                state["ai_last_date"] = rep["meta"]["ny_date"]
            m = rep["meta"]
            log(f"AI report written (attempt {attempt}, inputs {m['inputs']['source']}, {m['model']}, effort {m['effort']}, "
                f"stop {m['stop_reason']}, {m['duration_s']}s, {m['usage']}, est. ${m['cost_estimate']}; market gates "
                f"{'ok' if m['market']['ok'] else 'FAILED: ' + str(m['market'].get('reason'))}, total ${m['cost_total']}; "
                f"warnings {len(rep['warnings'])})")
            for w in rep["warnings"]:
                log("report warning:", w)
        except Exception as exc:                        # كل فشل هنا تقني ويمنع النشر: قابل لإعادة المحاولة
            err = exc
            log(f"AI report FAILED (attempt {attempt}):", repr(exc)[:300])
        rec = ai_report.attempt_record(attempt, cfg, now, started, datetime.now(timezone.utc), rep, err)
        log("attempt record:", json.dumps(rec, ensure_ascii=False))
        state["ai_attempts"] = ((state.get("ai_attempts") or []) + [rec])[-90:]
    if not (args.demo or args.offline):                 # سجل المحاولات وآخر جمع للأقسام
        state_p.parent.mkdir(exist_ok=True)
        state_p.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    payload["ai_available"] = hist_p.exists()
    payload["ai_status"] = ai_report.status_view(state, now, ai_cfg)    # محاولات اليوم، في كل بناء (offline أيضاً)
    payload["collection"] = collectors.page_view(man)                  # آخر جمع للأقسام: الوقت وحالة كل جامع
    # الحكم على التوقعات من سلاسل FRED نفسها؛ يُحفظ فقط في تحديث البيانات (update.yml يحفظ data/ في المستودع)
    payload["ledger"] = ledger.view(led)
    if settled:
        log(f"forecast ledger: {settled} outcome(s) settled")
    if not (args.demo or args.offline):
        DATA.mkdir(exist_ok=True)
        save_status(status_p, status, extra, fetched)
        if led["entries"]:
            ledger.save(ledger_p, led)

    DATA.mkdir(exist_ok=True)
    write_json(args.out, public_payload(payload))      # بلا مستويات المؤشرين الداخليين ولا نقاطهما
    log("wrote", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
