"""KnowledgePack, player extras: facts from the addon's extra pages and the quests a creature counts for."""
import json

from knowledge.api.values import as_int


# a quest objective (quest_objective o) the creature counts for: kill it, or it drops the item (parameters: the
# creature id three times)
COUNTS_FOR_CREATURE = ("((o.target_kind = 'npc' AND o.target_id = ?) OR (o.target_kind = 'item' AND "
                       "(o.drop_from_id = ? OR o.target_id IN "
                       "(SELECT item_id FROM creature_drop WHERE creature_id = ?))))")


class ExtrasMixin:
    """Training, flight paths, consumables, corpse run, and the quests a target or spot counts for."""

    # --------------------------------------------------------------------------------------------- player extras
    def player_extras(self, s, map_size=None):
        """Facts from the addon 0.10 pages for the coach context and rules (knowledge/extras.py):
        {"training", "flight_paths", "consumables", "corpse", "target_quests"}; missing parts are left out.
        Cached on the inputs; cheap enough for the capture thread."""
        if not self.ok or not s:
            return {}
        from knowledge import extras as X
        lvl = as_int(s.get("level"))
        tr = s.get("training")
        fp = s.get("flight_paths")
        pos = (s.get("map"), s.get("x"), s.get("y"))
        tgt = s.get("target") or {}
        open_q = tuple(sorted(q for q, v in (s.get("quests") or {}).items() if isinstance(v, dict) and not v.get("done")))
        key = (s.get("class"), s.get("race"), lvl, json.dumps(tr, sort_keys=True), tuple(fp) if fp is not None else None,
               s.get("faction"), pos[0], round(pos[1] or 0), round(pos[2] or 0), tuple(map_size or ()),
               json.dumps(s.get("consumables"), sort_keys=True), json.dumps(s.get("corpse")), tgt.get("npc"), open_q,
               json.dumps({q: (s.get("quests") or {})[q].get("obj") for q in open_q}, default=str))
        cache = getattr(self, "_extras", None)
        if cache is not None and cache[0] == key:
            return cache[1]
        out = {}
        try:
            t = X.training(self.kb, s.get("class"), s.get("race"), lvl, tr)
            if t is not None:
                out["training"] = t
            f = X.flights(self.kb, s.get("faction"), fp, pos[0], pos[1], pos[2], map_size)
            if f is not None:
                out["flight_paths"] = f
            c = X.consumables(s.get("consumables"), lvl)
            if c is not None:
                out["consumables"] = c
            d = X.corpse(lvl, pos[1], pos[2], s.get("corpse"), map_size)
            if d is not None:
                out["corpse"] = d
            tq = self._target_quests(as_int(tgt.get("npc")), open_q, s.get("quests") or {})
            if tq is not None:
                out["target_quests"] = tq
        except Exception as e:  # noqa: BLE001 - extras never break the coach
            out["error"] = "%s: %s" % (type(e).__name__, e)
        self._extras = (key, out)
        return out

    def _target_quests(self, npc, open_q, log):
        """Names of unfinished quests in the log with an unfinished objective the target counts for (kill it, or it
        drops a quest item): [] = none, None = no target / no data."""
        if not npc or not open_q:
            return None
        marks = ",".join("?" * len(open_q))
        rows = self.kb.q("SELECT o.quest_id, o.idx, q.name FROM quest_objective o LEFT JOIN quest q ON q.id = o.quest_id "
                         "WHERE o.quest_id IN (%s) AND %s" % (marks, COUNTS_FOR_CREATURE), *open_q, npc, npc, npc)
        out = []
        for r in rows:
            obj = (log.get(r["quest_id"]) or {}).get("obj") or []
            i = (r["idx"] or 1) - 1
            if 0 <= i < len(obj) and obj[i][0] >= obj[i][1] > 0:
                continue                               # that objective is already done
            name = r["name"] or "quest %d" % r["quest_id"]
            if name not in out:
                out.append(name)
        return out

    def target_quest_ids(self, npc, qids):
        """Ids among qids with an objective the creature counts for (kill it, or it drops a quest item)."""
        if not self.ok or not npc or not qids:
            return []
        qids = [int(q) for q in qids]
        marks = ",".join("?" * len(qids))
        rows = self.kb.q("SELECT DISTINCT o.quest_id FROM quest_objective o WHERE o.quest_id IN (%s) AND %s"
                         % (marks, COUNTS_FOR_CREATURE), *qids, npc, npc, npc)
        return [r["quest_id"] for r in rows]

    def quest_spots(self, qid, ui_map, limit=300):
        """[(x, y)] on ui_map where the quest's objectives happen: observed objective points, spawns of the
        creatures to kill or that drop its items, and the objects to use or loot."""
        if not self.ok or not qid or not ui_map:
            return []
        q, m = int(qid), int(ui_map)
        rows = self.kb.q(
            "SELECT x, y FROM quest_poi WHERE quest_id = ? AND ui_map = ? AND role LIKE 'objective%' "
            "UNION SELECT s.x, s.y FROM quest_objective o JOIN creature_spawn s ON s.creature_id = CASE "
            "  WHEN o.target_kind = 'npc' THEN o.target_id WHEN o.drop_from_kind = 'npc' THEN o.drop_from_id END "
            "  WHERE o.quest_id = ? AND s.ui_map = ? "
            "UNION SELECT s.x, s.y FROM quest_objective o JOIN creature_drop d ON d.item_id = o.target_id "
            "  JOIN creature_spawn s ON s.creature_id = d.creature_id "
            "  WHERE o.quest_id = ? AND o.target_kind = 'item' AND o.drop_from_id IS NULL AND s.ui_map = ? "
            "UNION SELECT g.x, g.y FROM quest_objective o JOIN gameobject_spawn g ON g.object_id = o.target_id "
            "  WHERE o.quest_id = ? AND o.target_kind = 'object' AND g.ui_map = ? LIMIT ?",
            q, m, q, m, q, m, q, m, int(limit))
        return [(r["x"], r["y"]) for r in rows if r["x"] is not None and r["y"] is not None]
