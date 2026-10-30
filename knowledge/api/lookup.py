"""KnowledgePack, lookups: places in words, names to ids, creature cards and quest rows."""
import math
import re

from knowledge import store
from knowledge.api._layer import A, MAX_LEVEL, MX, decay_level
from knowledge.api.guides import norm_guide_name
from knowledge.api.values import RANK, as_int, clean, compass, cut, parse_id, uniq


class LookupMixin:
    """Places, names, creature cards and quest rows, cached on the pack."""

    # --------------------------------------------------------------------------------------------- places
    def _dist_dir(self, a, b):
        """(yards, compass word) from a to b, or (None, None)."""
        d = self.data
        if a is None or b is None:
            return None, None
        if a[0] == b[0]:
            w, h = d.map_size(a[0])
            if not w:
                return None, None
            east, north = (b[1] - a[1]) / 100.0 * w, -(b[2] - a[2]) / 100.0 * h
            return math.hypot(east, north), compass(east, north)
        wa, wb = d.to_world(a), d.to_world(b)
        if not wa or not wb or wa[0] != wb[0]:
            return None, None
        north, east = wb[1] - wa[1], -(wb[2] - wa[2])
        return math.hypot(east, north), compass(east, north)

    def where(self, ctx, um, x, y):
        """A place in words: the geo description when there is one, else map, coordinates, distance and direction
        from the player."""
        if um is None or x is None or y is None:
            return None
        if self.geo is not None:
            try:
                txt = self.geo.describe(um, x, y, player=ctx.me, faction=ctx.summary.get("faction")).get("text")
                if txt:
                    return txt
            except Exception:  # noqa: BLE001
                pass
        txt = "%s %.1f,%.1f" % (self.data.map_name(um) or "map %s" % um, x, y)
        dd, dirn = self._dist_dir(ctx.here(), (um, x, y))
        if dd is not None:
            txt += " (%d yd %s)" % (int(round(dd / 10.0) * 10), dirn)
        return txt

    def spawn_summary(self, ctx, pts):
        """Nearest cluster of spawn points [(ui_map, x, y)] to the player (else the biggest map's centroid)."""
        if not pts:
            return None
        here = ctx.here()
        d = self.data
        if here:
            same = [p for p in pts if p[0] == here[0]]
            if same:
                best = min(same, key=lambda p: (p[1] - here[1]) ** 2 + (p[2] - here[2]) ** 2)
                near = [p for p in same if (d.dist_yd(best, p) or 1e9) <= 150]
                cx = sum(p[1] for p in near) / len(near)
                cy = sum(p[2] for p in near) / len(near)
                dd, dirn = self._dist_dir(here, best)
                return clean({"where": self.where(ctx, here[0], cx, cy),
                               "nearest_yd": int(dd) if dd is not None else None, "direction": dirn,
                               "spawns_nearby": len(near), "coords": "%.1f,%.1f" % (best[1], best[2])})
        by = {}
        for p in pts:
            by.setdefault(p[0], []).append(p)
        um = max(by, key=lambda m: len(by[m]))
        cx = sum(p[1] for p in by[um]) / len(by[um])
        cy = sum(p[2] for p in by[um]) / len(by[um])
        return clean({"where": self.where(ctx, um, cx, cy), "map": d.map_name(um), "spawns_on_map": len(by[um]),
                       "coords": "%.1f,%.1f" % (cx, cy)})

    # --------------------------------------------------------------------------------------------- names
    def name_index(self, kind):
        """Lower-case name -> ids for 'quest', 'creature' or 'item' (built once)."""
        idx = self._names.get(kind)
        if idx is None:
            d = self.data
            idx = {}
            if kind == "quest":
                for qid, q in d.quests.items():
                    if q.get("name"):
                        idx.setdefault(q["name"].lower(), []).append(qid)
            elif kind == "creature":
                for n, ids in d.creature_by_name.items():
                    idx[n] = list(ids)
            elif kind == "item":
                for iid, n in d.items.items():
                    if n:
                        idx.setdefault(n.lower(), []).append(iid)
            self._names[kind] = idx
        return idx

    def _fuzzy(self, kind, name, limit=5):
        """Ids whose name contains every token of `name` (plural 's' optional), shortest names first."""
        toks = [t for t in re.findall(r"[a-z0-9']+", name.lower()) if len(t) > 1] or [name.lower()]
        toks = [t[:-1] if len(t) > 3 and t.endswith("s") else t for t in toks]
        out = []
        for n, ids in self.name_index(kind).items():
            if all(t in n for t in toks):
                out.append((len(n), n, ids))
        out.sort()
        res = []
        for _, _, ids in out:
            res.extend(ids)
            if len(res) >= limit:
                break
        return res

    def quest_rank(self, ctx, qid):
        """Sort key among quests of one name: in the log, on the route, this side and race, still on Forever and with
        known XP first."""
        q = self.data.quest(qid) or {}
        log = ctx.summary.get("quests") or {}
        in_log = qid in log or str(qid) in log
        route = ctx.route
        in_route = route is not None and (qid in route.accept_idx or qid in route.turnin_idx)
        p = ctx.p()
        side_ok = q.get("side") in (None, "", "Both", p.get("faction"))
        try:
            race_ok = A._race_ok(q, p.get("race") or "Human", p.get("faction") or "Alliance")
        except Exception:  # noqa: BLE001
            race_ok = True
        return (not in_log, not in_route, not side_ok, not race_ok, bool(q.get("removed") or q.get("hidden")),
                q.get("xp") is None, qid)

    def find_quest(self, ctx, x):
        qid = parse_id(x, "q")
        if qid is not None:
            return qid if self.data.quest(qid) else None
        name = str(x or "").strip()
        if not name:
            return None
        ids = list(self.name_index("quest").get(name.lower()) or []) or self._fuzzy("quest", name, 8)
        return min(ids, key=lambda q: self.quest_rank(ctx, q)) if ids else None

    def find_creature(self, ctx, x):
        cid = parse_id(x, "c")
        d = self.data
        if cid is not None:
            return cid if d.creature(cid) else None
        name = str(x or "").strip()
        if not name:
            return None
        here = ctx.here()
        cid = d.creature_by_name_on(name, here[0] if here else None)
        if cid is not None:
            return cid
        ids = self._fuzzy("creature", name, 8)
        if not ids:
            return None
        return max(ids, key=lambda c: (bool(here and d.spawns_of(c, here[0])), d.spawn_count.get(c, 0), -c))

    def guide_loose(self, ctx, text):
        """A guide named by the model: key, G-page name, '5-11 Dun Morogh' or a fragment ('Westfall')."""
        t = str(text or "").strip()
        if not t:
            return None
        p = ctx.p()
        gk = self.guide_key(t, ctx.summary.get("guide_group"), p.get("faction"))
        if gk:
            return gk
        low = norm_guide_name(t)
        rows = [(k, r) for k, r in self.data.guide_rows.items() if low in norm_guide_name(r.get("name"))]
        if not rows:
            return None
        grp = (ctx.summary.get("guide_group") or "").lower()
        mine = self.class_guides(p.get("class"))
        lv = ctx.level or 0

        def rank(kr):
            k, r = kr
            lo = r.get("level_min")
            return (k not in mine, (r.get("grp") or "").lower() != grp, r.get("faction") not in (None, p.get("faction")),
                    "dungeon" in (r.get("grp") or "").lower(), abs((lo if lo is not None else 99) - lv), k)
        return min(rows, key=rank)[0]

    def class_guides(self, cls):
        """Guide keys the analyzer modelled for this class (guide_brief rows): the guides whose own `<<`
        condition admits the class (no Mage-AoE guide for a warrior)."""
        key = ("_class_guides", cls)
        hit = self._names.get(key)
        if hit is None:
            hit = {r["guide_key"] for r in self.kb.q("SELECT guide_key FROM guide_brief WHERE class=?",
                                                       str(cls or "").upper())}
            self._names[key] = hit
        return hit

    def _card(self, npc, name=None):
        """Creature card from creature + creature_note (cached). Keys starting with '_' are internal."""
        key = npc or (name or "").lower()
        hit = self._cards.get(key)
        if hit is not None:
            return hit
        cols = "id, name, min_level, max_level, rank, type, family, flee, caster, abilities, danger_notes, movement"
        row = self.kb.one("SELECT %s FROM creature WHERE id=?" % cols, npc) if npc else None
        if row is None and name:
            row = self.kb.one("SELECT %s FROM creature WHERE name=? ORDER BY verified DESC, id LIMIT 1" % cols, name)
        if row is None:
            return self.cache_put(self._cards, key, {"name": name, "known": False}, 512)
        notes = self.kb.q("""SELECT classification, movement, notes FROM creature_note
                             WHERE creature_id=? OR (creature_id IS NULL AND name=?) LIMIT 2""", row["id"], row["name"])
        ab = store.jload(row.get("abilities"), None)
        lo, hi = row.get("min_level") or row.get("max_level"), row.get("max_level") or row.get("min_level")
        danger = row.get("danger_notes") or "; ".join(n["notes"] for n in notes if n.get("notes")) or None
        card = {"name": row["name"], "fact": "c:%d" % row["id"], "_id": row["id"], "_rank": row.get("rank") or 0,
                "level_range": (lo if lo == hi else "%s-%s" % (lo, hi)) if lo else None,
                "rank": RANK.get(row.get("rank") or 0, row.get("rank")), "type": row.get("type"),
                "flees": bool(row["flee"]) if row.get("flee") is not None else None,
                "caster": bool(row["caster"]) if row.get("caster") is not None else None,
                "abilities": [str(a) for a in ab][:5] if isinstance(ab, list) and ab else None,
                "danger": cut(danger, 120),
                "rxp_class": next((n["classification"] for n in notes
                                   if n.get("classification") not in (None, "Normal")), None)}
        return self.cache_put(self._cards, key, card, 512)

    # --------------------------------------------------------------------------------------------- rules support
    def _qrow(self, qid):
        if qid in self._qrows:
            return self._qrows[qid]
        row = self.kb.one("SELECT id, name, level, xp FROM quest WHERE id=?", qid)
        return self.cache_put(self._qrows, qid, row, 4000)

    def decay_info(self, quest_id, level):
        """{"quest", "quest_id", "fact", "quest_level", "decay_at_level", "xp_now", "xp_after_level_up"} or None.
        decay_at_level is the brief's value (analyzer decay_level over the Forever quest-XP mechanics), else
        quest level + 6 (Classic). Cached per (quest, level)."""
        if not self.ok:
            return None
        qid, lv = as_int(quest_id), as_int(level)
        if qid is None or not lv or lv + 1 >= MAX_LEVEL:
            return None
        key = (qid, lv)
        if key in self._decay:
            return self._decay[key]
        out = None
        try:
            row = self._qrow(qid)
            if row and row.get("xp") and row.get("level") is not None:
                full, ql = row["xp"], row["level"]
                dl = decay_level(full, ql) or ql + 6
                out = {"quest": row.get("name") or "quest %d" % qid, "quest_id": qid, "fact": "q:%d" % qid,
                       "quest_level": ql, "decay_at_level": dl, "xp_now": MX.quest_xp(full, ql, lv),
                       "xp_after_level_up": MX.quest_xp(full, ql, lv + 1)}
        except Exception:  # noqa: BLE001
            out = None
        return self.cache_put(self._decay, key, out, 4000)

    # --------------------------------------------------------------------------------------------- the engine's reads
    # The companion reads kb.db only through these (quest briefings, step videos, reminders, the walking map), so a
    # schema change shows up here and nowhere in companion/.
    def step_links(self, guide_key, idx):
        """[(url, label)] of the links on one RestedXP step (rxp_action, verb 'link'), in guide order."""
        if not self.ok or guide_key is None or idx is None:
            return []
        out = []
        for r in self.kb.q("SELECT args, text FROM rxp_action WHERE guide_key=? AND idx=? AND verb='link' ORDER BY line",
                           guide_key, idx):
            args = store.jload(r.get("args"), {})
            args = args if isinstance(args, dict) else {}
            out.append(((args.get("url") or args.get("raw") or "").strip(), (r.get("text") or "").strip()))
        return out

    def quest_steps(self, guide_key, qid):
        """[{"idx", "text", "verbs"}] for every step of the guide that has an action on the quest, in step order
        (verbs: the step's action verbs for it, comma-joined)."""
        if not self.ok or guide_key is None or as_int(qid) is None:
            return []
        return self.kb.q("""SELECT s.idx AS idx, s.text AS text, group_concat(a.verb) AS verbs FROM rxp_action a
                            JOIN rxp_step s ON s.guide_key = a.guide_key AND s.idx = a.idx
                            WHERE a.guide_key = ? AND a.quest_id = ? GROUP BY s.idx ORDER BY s.idx""",
                         guide_key, int(qid))

    def friendly_npcs(self, ui_map, faction):
        """[{"id", "name", "subname", "x", "y"}]: spawn points on ui_map of NPCs with a title (vendor, trainer,
        innkeeper ...) friendly to the faction ('A' / 'H' or the faction name)."""
        if not self.ok:
            return []
        f = (faction or "")[:1].upper()
        return self.kb.q("""SELECT DISTINCT c.id, c.name, c.subname, s.x, s.y FROM creature c
                            JOIN creature_spawn s ON s.creature_id = c.id
                            WHERE s.ui_map = ? AND c.subname IS NOT NULL AND c.subname != ''
                            AND c.friendly_to LIKE ?""", ui_map, "%" + f + "%")

    def vendors_selling(self, item, ui_map, faction):
        """[{"id", "name", "subname", "x", "y"}]: spawn points on ui_map of vendors that sell the item (by name), not
        hostile to the faction."""
        if not self.ok or not item:
            return []
        f = (faction or "")[:1].upper()
        return self.kb.q("""SELECT DISTINCT c.id, c.name, c.subname, s.x, s.y FROM item i
                            JOIN vendor_item v ON v.item_id = i.id JOIN creature c ON c.id = v.npc_id
                            JOIN creature_spawn s ON s.creature_id = c.id
                            WHERE lower(i.name) = lower(?) AND s.ui_map = ? AND (c.friendly_to LIKE ?
                            OR c.friendly_to IS NULL)""", item, ui_map, "%" + f + "%")

    def continent_of(self, ui_map):
        """The continent (map id) a UiMap lies on, from its world bounds, or None."""
        if not self.ok or ui_map is None:
            return None
        return (self.kb.bounds(ui_map) or {}).get("continent")

    def map_name(self, ui_map):
        """A UiMap's name ('Dun Morogh'), or None."""
        return self.data.map_name(ui_map) if self.ok and ui_map is not None else None

    def quest_name(self, qid):
        """A quest's name, or None when the KB doesn't know it."""
        q = self.data.quest(as_int(qid)) if self.ok else None
        return (q or {}).get("name")

    def map_areas(self, ui_map):
        """[{"name", "x1", "y1", "x2", "y2"}]: the sub-zone boxes on a UiMap (map percent), for directions."""
        if not self.ok:
            return []
        return self.kb.q("""SELECT a.name AS name, b.x1 AS x1, b.y1 AS y1, b.x2 AS x2, b.y2 AS y2 FROM area_box b
                            JOIN area a ON a.area_id = b.area_id
                            WHERE b.ui_map = ? AND a.name IS NOT NULL ORDER BY b.rowid""", ui_map)

    def landmarks(self, ui_map):
        """[{"name", "subname", "friendly", "x", "y"}]: NPCs on a UiMap worth naming in directions: titled NPCs of a
        known side (innkeepers, trainers, vendors) and quest givers / enders. friendly: 'A', 'H', 'AH' or None."""
        if not self.ok:
            return []
        return self.kb.q("""SELECT DISTINCT c.name AS name, c.subname AS subname, c.friendly_to AS friendly, s.x AS x,
                                   s.y AS y FROM creature_spawn s JOIN creature c ON c.id = s.creature_id
                            WHERE s.ui_map = ? AND s.x IS NOT NULL AND c.name IS NOT NULL
                              AND ((c.subname IS NOT NULL AND c.friendly_to IS NOT NULL)
                                   OR EXISTS (SELECT 1 FROM quest_giver q WHERE q.kind = 'npc'
                                              AND q.entity_id = c.id))""", ui_map)

    def rxp_upcoming(self, guide_name, group=None, faction=None):
        """[{"step", "verb", "quest_id", "text"}]: a guide's raw accept / turn-in / complete lines in guide order
        (step = the guide's own step index, not RestedXP's live number). The lookahead's fallback when the route
        can't be resolved; [] when the guide is unknown."""
        gk = self.guide_key(guide_name, group, faction) if self.ok and guide_name else None
        if gk is None:
            return []
        return self.kb.q("""SELECT idx AS step, verb, quest_id, text FROM rxp_action WHERE guide_key = ?
                            AND verb IN ('accept', 'turnin', 'complete') ORDER BY idx, line""", gk)

    def quest_basics(self, qid):
        """{"name", "level"} of a quest for the dashboard's lists ({} when the KB doesn't know it)."""
        q = self.data.quest(as_int(qid)) if self.ok else None
        return {"name": q.get("name"), "level": q.get("level")} if q else {}

    def quest_detail(self, qid, near_map=None, detail=True):
        """A quest as the model's game context shows it, in the shape glide.db's reader (GlideDB.quest) gives: "id",
        "name", "level", "min_level", "side", "xp", "zone_name", "objective_text", "details" (with detail, cut to 300
        characters; WoW's $ codes resolved), "giver" / "turn_in" (up to 3 names), "giver_at" / "turn_in_at" ({"map",
        "x", "y"}) and "objectives" ([{"type", "need", "target", "text", "where"}]; target and where only for an NPC
        or object target, where = the centre of its kb spawn points on near_map, else on the map with most of them,
        {"map", "x", "y", "spawn_points"}). Empty values are left out; {"id": qid} when the KB doesn't know the quest."""
        q = self.data.quest(as_int(qid)) if self.ok else None
        if not q:
            return {"id": qid}
        d = self.data
        qid = q["id"]
        row = self.kb.one("SELECT zone_name, objective_text, details FROM quest WHERE id=?", qid) or {}
        out = {"id": qid, "name": q.get("name"), "level": q.get("level"), "min_level": q.get("min_level"),
               "side": q.get("side"), "xp": q.get("xp"), "zone_name": row.get("zone_name"),
               "objective_text": store.wow_text(row.get("objective_text"))}
        if detail and row.get("details"):
            out["details"] = store.wow_text(row["details"])[:300]
        givers = self.kb.q("""SELECT role, kind, entity_id, name, ui_map, x, y, pos_src FROM quest_giver
                              WHERE quest_id=? ORDER BY rowid""", qid)
        for role, key in (("start", "giver"), ("end", "turn_in")):
            rows = [r for r in givers if r["role"] == role]
            names = uniq(r.get("name") or self._entity_name(r["kind"], r["entity_id"])
                         or "%s %s" % (r["kind"], r["entity_id"]) for r in rows if r.get("entity_id") is not None)
            if names:
                out[key] = names[:3]
        for role, key in (("start", "giver_at"), ("end", "turn_in_at")):
            # where the quest was seen taken / handed in, before a giver's position guessed from its spawns
            placed = [r for r in givers if r["role"] == role and r.get("x") is not None]
            pos = min(placed, key=lambda r: (r.get("pos_src") or "").startswith("spawn:"), default=None)
            if pos:
                out[key] = {"map": pos["ui_map"], "x": pos["x"], "y": pos["y"]}
        ol = []
        for o in d.objectives.get(qid) or ():
            kind, tid = o.get("target_kind"), o.get("target_id")
            name = where = None
            if tid and kind in ("npc", "object"):
                name = self._entity_name(kind, tid)
                where = self._spawn_centre(d.spawns_of(tid) if kind == "npc" else d.object_spawns(tid), near_map)
            ol.append({k: v for k, v in (("type", o.get("type")), ("need", o.get("amount")), ("target", name),
                                          ("text", o.get("text") or None), ("where", where)) if v})
        if ol:
            out["objectives"] = ol
        return {k: v for k, v in out.items() if v not in (None, "", [])}

    def _entity_name(self, kind, eid):
        """An NPC's or object's name, or None."""
        if eid is None:
            return None
        if kind == "npc":
            return (self.data.creature(eid) or {}).get("name")
        if kind == "object":
            return (self.kb.one("SELECT name FROM gameobject WHERE id=?", eid) or {}).get("name")
        return None

    @staticmethod
    def _spawn_centre(pts, near_map=None):
        """{"map", "x", "y", "spawn_points"}: the centre of the spawn points [(ui_map, x, y)] on near_map, else on
        the map with most of them; None without points."""
        by = {}
        for m, x, y in pts or ():
            by.setdefault(m, []).append((x, y))
        if not by:
            return None
        m = min(by, key=lambda k: (k != near_map, -len(by[k])))
        xs, ys = zip(*by[m])
        return {"map": m, "x": round(sum(xs) / len(xs), 1), "y": round(sum(ys) / len(ys), 1),
                "spawn_points": len(by[m])}
