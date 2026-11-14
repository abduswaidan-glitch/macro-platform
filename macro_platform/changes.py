"""ما الذي تغيّر منذ التقرير السابق: الكود يحدد ماذا تغيّر، والنموذج يفسّر ماذا يعني.

عند كتابة كل تقرير تُحفظ لقطة أرقام فقط (meta.inputs_snapshot في ai_history.json، بلا ملف حالة جديد). في التقرير
التالي يقارن الكود اللقطتين: قراءة جديدة، أو قيمة تغيّرت للتاريخ نفسه، أو حالة تغيّرت، ومراجعات اكتُشفت منذ
التقرير السابق. لا عتبات اعتباطية: كل تغيّر يُذكر، والنموذج يقرر أيّه مهم اقتصادياً.
"""

from __future__ import annotations


def snapshot(payload: dict) -> dict:
    # مؤشرات internal_only (ترخيص S&P DJI وNasdaq) لا تدخل اللقطة: ai_history.json عام
    return {i["id"]: {k: i.get(k) for k in ("last", "as_of", "source_as_of", "state", "pct5")}
            for i in payload.get("indicators", []) if isinstance(i, dict) and i.get("id") and not i.get("internal_only")}


def diff(previous: dict | None, now_snap: dict, revisions: list[dict]) -> dict:
    """previous: مدخل التقرير السابق من ai_history.json (أو None)."""
    prev_meta = (previous or {}).get("meta") or {}
    prev_snap = prev_meta.get("inputs_snapshot")
    if not previous:
        return {"available": False, "reason": "no previous report"}
    if not isinstance(prev_snap, dict) or not prev_snap:
        return {"available": False, "reason": "previous report has no snapshot", "previous_ny_date": prev_meta.get("ny_date")}
    since = prev_meta.get("completed_at") or prev_meta.get("written_at") or ""
    items, unchanged = [], 0
    for sid, now in now_snap.items():
        old = prev_snap.get(sid)
        if not isinstance(old, dict):
            items.append({"id": sid, "kind": "new_indicator", "now": now.get("last"), "now_as_of": now.get("as_of")})
            continue
        changed = False
        if now.get("as_of") != old.get("as_of") or now.get("last") != old.get("last"):
            items.append({"id": sid, "kind": "new_observation" if now.get("as_of") != old.get("as_of") else "value_change",
                          "prev": old.get("last"), "now": now.get("last"), "prev_as_of": old.get("as_of"),
                          "now_as_of": now.get("as_of"), "pct5_prev": old.get("pct5"), "pct5_now": now.get("pct5")})
            changed = True
        if now.get("state") != old.get("state"):
            items.append({"id": sid, "kind": "state_change", "from": old.get("state"), "to": now.get("state")})
            changed = True
        unchanged += not changed
    items += [{"kind": "revision", **r} for r in revisions or [] if str(r.get("detected_utc", "")) > str(since)]
    return {"available": True, "previous_ny_date": prev_meta.get("ny_date"), "since_utc": since,
            "items": items, "unchanged_count": unchanged}
