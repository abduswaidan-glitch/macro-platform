"""تقرير Claude اليومي: يقرأ data.json ويبحث في أخبار اليوم، ثم يكتب ملخصاً وانحيازاً لـNQ وثلاثة سيناريوهات.

يُكتب مرة واحدة كل يوم عمل بعد الساعة المحددة بتوقيت نيويورك (config.json → ai.ny_hour/ny_minute)،
أو فوراً عند AI_FORCE=true. يحتاج السر ANTHROPIC_API_KEY في GitHub.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SYSTEM = """You write the daily US-macro report for an Arabic-language macro dashboard read by an intraday NQ (Nasdaq-100 futures) trader.

Data rules:
- The JSON in the user message is the platform's own data (source: FRED). It is the only source for numbers about indicators. Never invent a number. If you cite a market estimate you found on the web (consensus, FedWatch, nowcasts), label it as an estimate and link it.
- Every indicator carries "as_of" and "age_days". Monthly data lags; say how old a number is when it matters.
- Use web search for today's events: the US data calendar, Fed speakers and decisions, Treasury auctions, trade/tariff and political headlines, geopolitics. Cite a URL for each event.
- Keep five kinds of statement apart: platform data (the JSON); official releases you checked, with the official URL; reported news and provider-reported figures (the "financialjuice" block, wire stories), which you call reported; your hypotheses; and qualitative implications for NQ. Never state reported news as an official fact.
- For a headline that matters, reason about it: is it new, is a primary source confirming it, how large and from when, through which channel it would reach NQ (rates, inflation, growth, earnings, risk appetite), over what horizon, and what would contradict it. Say what is unknown.
- Scenario probabilities and confidence are your subjective weights, not calibrated estimates, and no single headline sets them. Give no other numeric probability unless it comes from the platform data or a cited market source, labelled as such.
- No buy/sell recommendations.

Write everything in Modern Standard Arabic (فصحى), keeping technical terms in English (NQ, CPI, PCE, VIX, bp, Fed, FOMC, yield, ...).

Output: after any research, end your answer with exactly one JSON object between <report> and </report>, no markdown fences, with these keys:
{
  "title": "one-line headline of the day's macro read",
  "summary": ["3-6 short bullet sentences"],
  "bias": {"direction": "bullish" | "bearish" | "neutral", "confidence": 0-100, "reason": "why, for NQ next session"},
  "scenarios": [{"label": "A", "name": "...", "prob": integer, "trigger": "what confirms it", "reason": "..."}, B, C],
  "sections": [{"heading": "...", "body": "a paragraph"}],
  "gates_read": "one paragraph on the NQ macro gates (10Y vs trigger, VIX, real yield, NQ/ES leadership)",
  "watch": ["what to watch next, with dates/times ET when known"],
  "risks": ["..."],
  "data_audit": ["stale, conflicting or estimate-based inputs and how you treated them"],
  "events": [{"title": "...", "date": "YYYY-MM-DD", "url": "https://..."}],
  "one_liner": "closing panic-proof one-liner"
}
Rules for the JSON: exactly three scenarios labelled A, B, C whose prob values sum to 100; sections should cover growth, labor, inflation, rates & the Fed, and risk/markets."""


class ReportError(RuntimeError):
    pass


def analyst_notes(path: str | Path = "prompts/analyst.md") -> str:
    p = Path(path)
    if not p.exists():
        return ""
    return re.sub(r"<!--.*?-->", "", p.read_text(encoding="utf-8"), flags=re.S).strip()


def should_run(now_utc: datetime, last_date: str | None, cfg: dict, force: bool = False) -> bool:
    if force:
        return True
    ny = now_utc.astimezone(NY)
    if ny.weekday() >= 5:
        return False
    due = ny.replace(hour=cfg.get("ny_hour", 8), minute=cfg.get("ny_minute", 30), second=0, microsecond=0)
    return ny >= due and last_date != ny.strftime("%Y-%m-%d")


def context(payload: dict, tail: int = 8, fj: dict | None = None) -> dict:
    """نسخة مضغوطة من data.json للنموذج: بلا السلاسل الكاملة، مع آخر نقاط لكل مؤشر لرؤية الاتجاه."""
    inds = []
    for i in payload["indicators"]:
        if i.get("state") == "error" and "last" not in i:
            inds.append({"id": i["id"], "name": i["name_en"], "state": "missing"})
            continue
        inds.append({k: i.get(k) for k in ("id", "name_en", "category", "freq", "transform", "unit", "last", "prev",
                                           "change", "z5", "pct5", "as_of", "age_days", "state", "issues",
                                           "source_as_of", "unavailable")}
                    | {"recent": i.get("points", [])[-tail:]})
    return {"generated": payload["generated"], "kpis": payload["kpis"], "gates": payload["gates"],
            "gate_levels": payload["config"]["gates"], "signals": payload["signals"],
            "quadrant": payload["quadrant"], "sahm_recent": payload["sahm"]["points"][-6:],
            "indicators": inds, "data_warnings": payload["status"]["warnings"],
            **({"financialjuice": fj} if fj else {})}   # أخبار مُبلَّغ عنها، منفصلة عن بيانات المنصة


def parse_report(text: str) -> dict:
    m = re.findall(r"<report>\s*(\{.*?\})\s*</report>", text, flags=re.S)
    if not m:
        raise ReportError("لم يُعثر على <report> في رد النموذج")
    rep = json.loads(m[-1])
    for key in ("title", "summary", "bias", "scenarios", "one_liner"):
        if key not in rep:
            raise ReportError(f"الحقل {key} مفقود")
    sc = rep["scenarios"]
    if len(sc) != 3:
        raise ReportError("عدد السيناريوهات ليس 3")
    total = sum(int(s.get("prob", 0)) for s in sc)
    if not 95 <= total <= 105:
        raise ReportError(f"مجموع الاحتمالات {total}")
    if total != 100:  # تصحيح تقريب بسيط على أكبر احتمال
        big = max(sc, key=lambda s: int(s["prob"]))
        big["prob"] = int(big["prob"]) + 100 - total
    for k in ("sections", "watch", "risks", "data_audit", "events"):
        rep.setdefault(k, [])
    rep.setdefault("gates_read", "")
    return rep


# أسعار Opus 5.5 لكل مليون رمز (تقدير الكلفة في سجل Actions فقط): إدخال، إخراج، كتابة cache لـ5 دقائق، قراءة cache
PRICES = {"claude-opus-5-5": (4.0, 20.0, 5.0, 0.20)}
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def estimate_cost(model: str, usage: dict) -> float | None:
    p = PRICES.get(model)
    if not p:
        return None
    return round(sum(usage.get(k, 0) * price for k, price in zip(USAGE_KEYS, p)) / 1e6, 4)


def call_claude(user_text: str, system: str, model: str, effort: str) -> tuple[str, list[dict], dict, str]:
    """Prompt caching: نقطة صريحة في آخر التعليمات الثابتة (system) + caching تلقائي يتقدّم مع المحادثة.
    خطوات البحث داخل التقرير الواحد تعيد قراءة السياق نفسه، فتُحتسب القراءة المكررة بـ5% من سعر الإدخال؛
    وأداة البحث تضيف نقاط cache بعد نتائجها تلقائياً حين يكون الطلب مفعّلاً للـcaching."""
    import anthropic  # يُثبَّت في GitHub Actions فقط

    client = anthropic.Anthropic()
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    messages = [{"role": "user", "content": user_text}]
    texts, sources, served = [], {}, model
    usage = {k: 0 for k in USAGE_KEYS}
    for _ in range(5):  # pause_turn: أدوات الخادم قد توقف الدور مؤقتاً فنكمل
        with client.beta.messages.stream(
            model=model,
            max_tokens=64000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            system=system_blocks,
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}],
            messages=messages,
            cache_control={"type": "ephemeral"},
        ) as stream:
            msg = stream.get_final_message()
        served = msg.model
        for k in USAGE_KEYS:
            usage[k] += getattr(msg.usage, k, 0) or 0
        if msg.stop_reason == "refusal":
            raise ReportError("رفض النموذج الطلب")
        for b in msg.content:
            if b.type == "text":
                texts.append(b.text)
            elif b.type == "web_search_tool_result" and isinstance(b.content, list):
                for r in b.content:
                    url = getattr(r, "url", None)
                    if url:
                        sources[url] = getattr(r, "title", "") or url
        if msg.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": msg.content})
    return "".join(texts), [{"url": u, "title": t} for u, t in sources.items()], usage, served


def fj_meta(fj: dict | None) -> dict | None:
    """أعداد وحالة مدخل FinancialJuice في سجل التقرير، بلا نص العناوين (ai_history.json في المستودع العام)."""
    if not fj:
        return None
    st: dict[str, int] = {}
    for e in fj.get("calendar", []):
        st[e.get("status")] = st.get(e.get("status"), 0) + 1
    return {"headlines": len(fj.get("headlines", [])), "calendar": st,
            **{k: fj.get(k) for k in ("headlines_current", "calendar_current",
                                      "headlines_last_success_utc", "calendar_last_success_utc")}}


def write_report(payload: dict, cfg: dict, now_utc: datetime | None = None, caller=call_claude,
                 fj: dict | None = None) -> dict:
    now_utc = now_utc or datetime.now(timezone.utc)
    ai = cfg.get("ai", {})
    notes = analyst_notes()
    system = SYSTEM + ("\n\nThe trader's standing instructions (Arabic):\n" + notes if notes else "")
    ny_now = now_utc.astimezone(NY)
    user = (f"Today is {ny_now:%A %Y-%m-%d}, {ny_now:%H:%M} New York time. Platform data:\n"
            + json.dumps(context(payload, fj=fj), ensure_ascii=False))
    text, sources, usage, served = caller(user, system, ai.get("model", "claude-opus-5-5"), ai.get("effort", "high"))
    rep = parse_report(text)
    rep["sources"] = sources
    rep["meta"] = {"written_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "ny_date": ny_now.strftime("%Y-%m-%d"),
                   "model": served, "usage": usage, "fj_input": fj_meta(fj)}
    return rep


def update_history(path: Path, report: dict, keep: int = 14) -> list[dict]:
    hist = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    hist = [h for h in hist if h.get("meta", {}).get("ny_date") != report["meta"]["ny_date"]]
    hist.insert(0, report)
    hist = hist[:keep]
    path.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")
    return hist
