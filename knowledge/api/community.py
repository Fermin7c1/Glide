"""Community intelligence (knowledge/community, gathered by the daily community scan): the notes that concern the
player, as the knowledge block and the community tool show them."""
import time

from knowledge import store
from knowledge.api.values import as_int, clean


NOTE_SKIP = ("superseded", "contradicted")


def note_out(r, full=False):
    """A community_note row as the API shows it (full adds title, detail, zones, classes, sources)."""
    srcs = store.jload(r.get("sources"), []) or []
    out = {"fact": "n:%s" % r["id"], "kind": r.get("kind"), "claim": r.get("claim") or r.get("title"),
           "status": r.get("status"), "official": bool(r.get("official")) or None,
           "published": r.get("published") or r.get("seen"),
           "levels": [r["level_min"], r["level_max"]] if r.get("level_min") is not None else None,
           "source": (srcs[0].get("medium") if srcs and isinstance(srcs[0], dict) else None)}
    if full:
        out.update({"title": r.get("title"), "detail": r.get("detail"),
                    "zones": store.jload(r.get("zones"), []) or None, "classes": store.jload(r.get("classes"), []) or None,
                    "confidence": r.get("confidence"),
                    "sources": [{k: s.get(k) for k in ("medium", "url", "author", "date") if s.get(k)}
                                for s in srcs[:3] if isinstance(s, dict)] or None})
    return clean(out)


def community_notes(kb, ctx=None, query=None, level=None, zone=None, kind=None, limit=8, recent_days=None,
                    live=False, full=False):
    """Community notes relevant to the player: level within the note's range (+-3), the note's zones include the
    player's zone, or the note is for the player's class; superseded / contradicted notes left out unless asked for
    by query. live=True (the callout block) keeps only notes that touch the zone, level or class, newest first."""
    if kb is None:
        return []
    s = (ctx.summary if ctx is not None else {}) or {}
    lv = as_int(level) or as_int(s.get("level"))
    zn = (zone or s.get("zone") or "").strip().lower()
    cls = ((ctx.player or {}).get("class") if ctx is not None and isinstance(ctx.player, dict) else None) or \
        s.get("class") or ""
    rows = kb.q("SELECT * FROM community_note")
    if not rows:
        return []
    today = time.strftime("%Y-%m-%d")
    out = []
    q = (query or "").strip().lower()
    for r in rows:
        if r.get("status") in NOTE_SKIP and not q:
            continue
        if kind and r.get("kind") != kind:
            continue
        if recent_days:
            d = r.get("published") or r.get("seen") or ""
            try:
                age = (time.mktime(time.strptime(today, "%Y-%m-%d")) - time.mktime(time.strptime(d[:10], "%Y-%m-%d"))) / 86400
            except ValueError:
                age = 0
            if age > recent_days:
                continue
        zones = [z.lower() for z in store.jload(r.get("zones"), []) or []]
        classes = [c.upper() for c in store.jload(r.get("classes"), []) or []]
        lo, hi = r.get("level_min"), r.get("level_max")
        lvl_ok = lv is None or lo is None or (lo - 3 <= lv <= (hi if hi is not None else lo) + 3)
        zone_hit = bool(zn) and any(z and (z in zn or zn in z) for z in zones)
        class_hit = bool(cls) and cls.upper() in classes
        if classes and not class_hit and not q:
            continue                                   # another class's note (a search still finds it)
        text = " ".join(str(r.get(k) or "") for k in ("title", "claim", "detail", "tags")).lower()
        if q and not all(w in text for w in q.split()):
            continue
        if live and not (zone_hit or class_hit or (lo is not None and lvl_ok)):
            continue
        if not lvl_ok and not q:
            continue
        score = (3 if zone_hit else 0) + (2 if class_hit else 0) + (1 if r.get("official") else 0) + \
            (1 if r.get("status") in ("corroborated", "official") else 0)
        out.append((score, r.get("published") or r.get("seen") or "", r))
    out.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [note_out(r, full=full) for _, _, r in out[:max(1, int(limit))]]
