"""Read-only access to data/glide.db for the coach (the knowledge base, data/kb.db, is the knowledge package)."""
import os
import sqlite3
import sys
import threading

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if ROOT not in sys.path:
    sys.path.append(ROOT)       # the knowledge package: its store module (stdlib only) cleans quest text
from knowledge.store import wow_text  # noqa: E402


class GlideDB:
    def __init__(self, path):
        self.path = path
        self.ok = os.path.exists(path)
        self._local = threading.local()

    def reopen(self, path):
        """Switch to another glide.db (the background refresh's data/glide.next.db). Each thread reconnects on its
        next query; open connections to the old file are left to close with their threads."""
        self.path = path
        self.ok = os.path.exists(path)
        self._local = threading.local()

    def db(self):
        if not self.ok:
            return None
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect("file:%s?mode=ro" % self.path.replace("\\", "/"), uri=True, check_same_thread=False)
            c.row_factory = sqlite3.Row
            self._local.c = c
        return c

    def q(self, sql, *args):
        d = self.db()
        if not d:
            return []
        try:
            return d.execute(sql, args).fetchall()
        except sqlite3.Error:
            return []

    def map_name(self, ui_map):
        r = self.q("SELECT name FROM maps WHERE ui_map=?", ui_map)
        return r[0]["name"] if r else None

    def entity(self, kind, eid):
        r = self.q("SELECT name, min_level, max_level FROM entities WHERE kind=? AND id=?", kind, eid)
        return dict(r[0]) if r else None

    def quest(self, qid, near_map=None, detail=True):
        r = self.q("SELECT * FROM quests WHERE id=?", qid)
        if not r:
            return {"id": qid}
        q = dict(r[0])
        out = {k: q.get(k) for k in ("id", "name", "level", "min_level", "side", "xp", "zone_name",
                                     "objective_text")}
        out["objective_text"] = wow_text(out["objective_text"])
        if detail and q.get("details"):
            out["details"] = wow_text(q["details"])[:300]
        ents = self.q("SELECT role, kind, entity_id, name FROM quest_entities WHERE quest_id=?", qid)
        for role in ("start", "end"):
            names = [e["name"] or "%s %s" % (e["kind"], e["entity_id"]) for e in ents if e["role"] == role]
            if names:
                out["giver" if role == "start" else "turn_in"] = names[:3]
        pts = self.q("""SELECT role, ui_map, x, y FROM quest_points WHERE quest_id=? AND role IN ('start','end')
                        AND x IS NOT NULL""", qid)
        for p in pts:
            key = "giver_at" if p["role"] == "start" else "turn_in_at"
            out.setdefault(key, {"map": p["ui_map"], "x": p["x"], "y": p["y"]})
        objs = self.q("SELECT idx, type, target_id, amount, text FROM quest_objectives WHERE quest_id=? ORDER BY idx", qid)
        ol = []
        for o in objs:
            name = None
            kind = {"monster": "npc", "item": "item", "object": "object"}.get(o["type"])
            if o["target_id"] and kind in ("npc", "object"):
                e = self.entity(kind, o["target_id"])
                name = e and e.get("name")
                where = self.spawn_summary(kind, o["target_id"], near_map)
            else:
                where = None
            ol.append({k: v for k, v in (("type", o["type"]), ("need", o["amount"]), ("target", name),
                                          ("text", o["text"] or None), ("where", where)) if v})
        if ol:
            out["objectives"] = ol
        return {k: v for k, v in out.items() if v not in (None, "", [])}

    def spawn_summary(self, kind, eid, near_map=None):
        rows = self.q("SELECT ui_map, x, y FROM spawns WHERE kind=? AND entity_id=? AND x IS NOT NULL", kind, eid)
        if not rows:
            return None
        by = {}
        for r in rows:
            by.setdefault(r["ui_map"], []).append((r["x"], r["y"]))
        maps = sorted(by, key=lambda m: (m != near_map, -len(by[m])))
        m = maps[0]
        xs, ys = zip(*by[m])
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        return {"map": m, "x": round(cx, 1), "y": round(cy, 1), "spawn_points": len(by[m])}

    def rxp_upcoming(self, guide_name, quest_ids_done=(), limit=12):
        """Raw upcoming guide lines (accept/turnin/complete) for lookahead beyond the 3 live steps."""
        r = self.q("SELECT guide_key FROM rxp_guides WHERE name=? ORDER BY source LIKE 'owned%' DESC LIMIT 1", guide_name)
        if not r:
            return []
        rows = self.q("""SELECT step, verb, quest_id, text FROM rxp_steps WHERE guide_key=?
                         AND verb IN ('accept','turnin','complete') ORDER BY step, line""", r[0]["guide_key"])
        return [dict(x) for x in rows]
