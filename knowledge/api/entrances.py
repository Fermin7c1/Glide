"""Dungeon and raid entrances: where the door of an instance is (kb table instance_entrance, built by
knowledge/sources/entrances.py), for the pursuit's "run Scholomance" move and its map pin.

    instance_entrance(kb, key) -> {"map", "x", "y", "src", "name", "area_id", "kind", "conf"} or None
    KnowledgePack.instance_entrance(key)   the same on the pack (knowledge/api/lookup.py)
"""
from knowledge import store
from knowledge.api.values import as_int
from knowledge.sources.entrances import norm

__all__ = ["instance_entrance"]


def instance_entrance(kb, key):
    """Where the entrance of a dungeon or raid is, or None.

    kb: a store.KB, a KnowledgePack or a kb.db path. key: the instance's area id (kb.area's instance row, which is
    WoWSims' drop zoneId: 2057 Scholomance, 1583 Blackrock Spire; an outdoor area of the same name works too, like
    RestedXP's dungeonList ids: 978 -> Zul'Farrak) or a name ("Scholomance", "UBRS", "Dire Maul North", "Onyxia").

    Returns {"map": UiMap id of the outdoor zone or city, "x", "y": map PERCENT (0..100) like every kb position (a
    0..1 pin coordinate is x / 100), "src": "hand" (a checked override) | "db2" (the client's corpse point, converted),
    "name", "area_id", "kind": "party" | "raid", "conf": 0.9 hand, lower for db2 ('roughly here')}. None when the kb
    has no such table (built before it), or doesn't know the instance."""
    if isinstance(kb, str):
        own = store.open_ro(kb)
        try:
            return instance_entrance(own, key) if own.ok else None
        finally:
            own.close()                                  # Windows: an open connection pins the file
    kb = _kb(kb)
    if kb is None:
        return None
    rows = _rows(kb)
    if not rows:
        return None
    aid = as_int(key) if not isinstance(key, str) or key.strip().isdigit() else None
    if aid is not None:
        for r in rows:
            if r["area_id"] == aid:
                return _out(r)
        area = kb.one("SELECT name FROM area WHERE area_id=?", aid)
        key = area.get("name") if area else None
    n = norm(key)
    if not n:
        return None
    for r in rows:
        if norm(r["name"]) == n:
            return _out(r)
    for r in rows:
        if n in r["_aliases"]:
            return _out(r)
    best = None             # "Dire Maul North gate", "Stratholme (undead side)": the longest name it starts with
    for r in rows:
        for a in [norm(r["name"])] + r["_aliases"]:
            if a and n.startswith(a + " ") and (best is None or len(a) > best[0]):
                best = (len(a), r)
    return _out(best[1]) if best else None


def _kb(kb):
    if kb is not None and not isinstance(kb, store.KB):
        kb = getattr(kb, "kb", None)                     # a KnowledgePack
    return kb if kb is not None and getattr(kb, "ok", False) else None


def _rows(kb):
    if not kb.one("SELECT 1 AS ok FROM sqlite_master WHERE type='table' AND name='instance_entrance'"):
        return []                                        # a kb.db built before the table existed
    rows = kb.q("SELECT * FROM instance_entrance ORDER BY area_id")
    for r in rows:
        r["_aliases"] = [a for a in (store.jload(r.get("aliases"), []) or []) if isinstance(a, str)]
    return rows


def _out(r):
    return {"map": r["ui_map"], "x": r["x"], "y": r["y"], "src": r["src"], "name": r["name"],
            "area_id": r["area_id"], "kind": r["kind"], "conf": r["conf"]}
