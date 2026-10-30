"""Advice tools: the expert rules that apply now and what the community reports."""
from knowledge.api._layer import E
from knowledge.api.community import community_notes
from knowledge.api.values import clean, cut


def t_community(ctx, query=None, kind=None, level=None, zone=None):
    err = ctx.need(guide=False, step=False)
    if err:
        return err
    notes = community_notes(ctx.kb, ctx, query=query, level=level, zone=zone, kind=kind, limit=8, full=True)
    total = (ctx.kb.one("SELECT COUNT(*) AS n, MAX(seen) AS last FROM community_note") or {})
    return {"notes": notes, "facts": [n["fact"] for n in notes],
            "note": "community reports, not verified unless official or corroborated; %s notes in total, newest scan %s"
                    % (total.get("n") or 0, total.get("last") or "never")}


def t_heuristics(ctx, topic=None, tags=None):
    err = ctx.need(guide=False, step=False)
    if err:
        return err
    st = ctx.pack.heuristic_state(ctx, ["question"])
    muted = ctx.muted or None
    want = [str(t).strip().lower() for t in (tags if isinstance(tags, (list, tuple)) else [tags] if tags else [])
            if str(t).strip()]
    t = str(topic).strip().lower() if topic else None
    if t and not want:
        want = [t]
    rules = ctx.pack.rules()                  # the rules this KB was built with
    hits = E.evaluate(st, rules=rules, limit=8, tags=want or None, exclude_tags=muted)
    if want and not hits:                     # not a tag: match the words of the rules instead
        words = " ".join(want)
        hits = [h for h in E.evaluate(st, rules=rules, limit=0, exclude_tags=muted)
                if words in ("%s %s %s" % (h.get("title"), h.get("advice"), " ".join(h.get("tags") or []))).lower()][:8]
    rows = [clean({"id": "h:%s" % h["id"], "title": h.get("title"), "advice": h.get("advice"),
                    "rationale": cut(h.get("rationale"), 240), "priority": h.get("priority"), "conf": h.get("conf"),
                    "verified_on_forever": bool(h.get("verified")), "why": (h.get("why") or [])[:4]}) for h in hits]
    return {"topic": t, "tags": want or None, "muted": muted, "rules": rows, "facts": [r["id"] for r in rows]}
