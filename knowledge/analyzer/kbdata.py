"""Cached, read-only access to kb.db for the analyzer (works on a store.KB or on a raw sqlite3 connection).

get_data(kb) returns one KBData per KB (keyed by path + mtime, or by connection), so a companion process
pays the table loads once. Every table is loaded lazily on first use; nothing here writes.

Geometry: positions are (ui_map, x, y) with x, y in map percent. dist_yd(a, b) uses the map size when
both points are on one map, world coordinates (map_bounds) when they are on different maps of one continent,
and returns None when neither works. travel_time() prefers observed travel_edge paths (Dijkstra over the
1%-cell graph, endpoints within one cell of a graph node), else distance / run speed x detour.
"""
import heapq
import json
import math
import os
import re
import sqlite3
import threading

from knowledge import store
from knowledge.mechanics import travel as MT
from knowledge.mechanics import params as MP
from . import params as AP

_CACHE = {}
_CACHE_LOCK = threading.Lock()

RANK_ELITE = (1, 2, 3)        # elite, rare elite, boss


class ConnKB:
    """store.KB-like adapter over an open sqlite3 connection (load() runs on the build connection)."""

    def __init__(self, conn, path=None):
        self.c = conn
        self.path = path or ":conn:%d" % id(conn)
        self.ok = True

    def q(self, sql, *args):
        try:
            cur = self.c.execute(sql, args)
        except sqlite3.Error as e:
            store.sql_error_once(sql, e)      # still [] for the build, but a schema slip shows in its log
            return []
        cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, tuple(r))) for r in cur.fetchall()]

    def one(self, sql, *args):
        r = self.q(sql, *args)
        return r[0] if r else None

    def meta(self, key, default=None):
        r = self.one("SELECT value FROM meta WHERE key=?", key)
        return r["value"] if r else default

    def bounds(self, ui_map):
        return self.one("SELECT * FROM map_bounds WHERE ui_map=?", ui_map)


def as_kb(obj):
    if isinstance(obj, sqlite3.Connection):
        return ConnKB(obj)
    if isinstance(obj, str):
        return store.KB(obj)
    return obj


def get_data(kb):
    kb = as_kb(kb)
    if isinstance(kb, KBData):
        return kb
    key = getattr(kb, "path", None)
    if isinstance(kb, ConnKB):
        key = ("conn", id(kb.c))
    else:
        try:
            key = (key, os.path.getmtime(key))
        except (OSError, TypeError):
            key = (key, None)
    with _CACHE_LOCK:
        d = _CACHE.get(key)
        if d is None:
            if len(_CACHE) > 8:
                _CACHE.clear()
            d = _CACHE[key] = KBData(kb)
    if not isinstance(kb, ConnKB):
        MP.use_kb(d.params)           # this KB's parameter values from now on (the build applies its own)
    return d


def drop_cache():
    with _CACHE_LOCK:
        _CACHE.clear()


def _j(text, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


class KBData:
    """Lazily loaded tables + indexes. Attribute access loads on first use."""

    def __init__(self, kb):
        self.kb = kb
        self._lock = threading.RLock()
        self._loaded = set()
        self._guides = {}
        self._dij = {}
        self._spawn_map = {}
        self.counters = {}            # what could not be estimated (filled by the model)
        # the parameter values this KB was built with (mechanics.params.Snapshot); the build's own connection has
        # none: it applies the values it fits itself
        self.params = None if isinstance(kb, ConnKB) else MP.snapshot_of(kb)

    # ------------------------------------------------------------------------------------------
    def count(self, what, key=None):
        """Record something the model could not estimate; with a key, unique keys are counted."""
        if key is None:
            self.counters[what] = self.counters.get(what, 0) + 1
        else:
            self.__dict__.setdefault("_ukeys", {}).setdefault(what, set()).add(key)

    def unknowns(self):
        out = dict(self.counters)
        for k, v in self.__dict__.get("_ukeys", {}).items():
            out[k] = len(v)
        return dict(sorted(out.items()))

    def _need(self, name):
        if name in self._loaded:
            return
        with self._lock:
            if name in self._loaded:
                return
            getattr(self, "_load_" + name)()
            self._loaded.add(name)

    def __getattr__(self, name):
        loader = "_load_" + name
        if name.startswith("_") or not hasattr(type(self), loader):
            raise AttributeError(name)
        self._need(name)
        return self.__dict__[name]

    # ------------------------------------------------------------------------------------------ quests
    def _load_quests(self):
        rows = self.kb.q("""SELECT id, name, level, min_level, side, races, classes, xp, xp_src, xp_conf, type,
                            next_quest, suggested_group, removed, hidden, quest_info, ui_map, is_forever
                            FROM quest""")
        self.__dict__["quests"] = {r["id"]: r for r in rows}

    def quest(self, qid):
        return self.quests.get(qid) if qid is not None else None

    def _load_objectives(self):
        out = {}
        for r in self.kb.q("SELECT * FROM quest_objective ORDER BY quest_id, idx"):
            out.setdefault(r["quest_id"], []).append(r)
        self.__dict__["objectives"] = out

    def _load_prereqs(self):
        pre, post = {}, {}
        for r in self.kb.q("SELECT quest_id, pre_id, kind FROM quest_prereq"):
            pre.setdefault(r["quest_id"], []).append((r["pre_id"], r["kind"]))
            post.setdefault(r["pre_id"], []).append((r["quest_id"], r["kind"]))
        self.__dict__["prereqs"] = pre
        self.__dict__["unlocks_of"] = post

    def _load_unlocks_of(self):
        self._need("prereqs")

    def unlocks(self, qid):
        """Quests that become available after qid (next_quest + quests having qid as pre/pregroup)."""
        out = []
        q = self.quest(qid)
        if q and q.get("next_quest"):
            out.append(q["next_quest"])
        for nq, kind in self.unlocks_of.get(qid, ()):
            if kind in ("pre", "pregroup") and nq not in out:
                out.append(nq)
        return out

    def chain_length(self, qid, limit=12):
        n, seen, cur = 0, {qid}, qid
        while n < limit:
            nxt = [u for u in self.unlocks(cur) if u not in seen]
            if not nxt:
                break
            cur = nxt[0]
            seen.add(cur)
            n += 1
        return n

    def _load_givers(self):
        out, by_map = {}, {}
        for r in self.kb.q("SELECT quest_id, role, kind, entity_id, name, ui_map, x, y FROM quest_giver"):
            out.setdefault(r["quest_id"], {}).setdefault(r["role"], []).append(r)
            if r["role"] == "start" and r["ui_map"] is not None and r["x"] is not None:
                by_map.setdefault(r["ui_map"], []).append(r)
        self.__dict__["givers"] = out
        self.__dict__["start_by_map"] = by_map

    def _load_start_by_map(self):
        self._need("givers")

    def giver(self, qid, role):
        lst = self.givers.get(qid, {}).get(role) or []
        for r in lst:
            if r["ui_map"] is not None and r["x"] is not None:
                return r
        return lst[0] if lst else None

    def _load_quest_poi(self):
        out = {}
        for r in self.kb.q("SELECT quest_id, role, ui_map, x, y, n FROM quest_poi WHERE x IS NOT NULL"):
            out.setdefault(r["quest_id"], []).append(r)
        self.__dict__["quest_poi"] = out

    # ------------------------------------------------------------------------------------------ creatures
    def _load_creatures(self):
        rows = self.kb.q("""SELECT id, name, min_level, max_level, rank, type, reaction, friendly_to, danger_notes,
                            movement FROM creature""")
        by_name = {}
        for r in rows:
            if r["name"]:
                by_name.setdefault(r["name"].lower(), []).append(r["id"])
        self.__dict__["creatures"] = {r["id"]: r for r in rows}
        self.__dict__["creature_by_name"] = by_name

    def _load_creature_by_name(self):
        self._need("creatures")

    def creature(self, cid):
        return self.creatures.get(cid) if cid is not None else None

    def _load_spawn_count(self):
        self.__dict__["spawn_count"] = {r["creature_id"]: r["n"] for r in self.kb.q(
            "SELECT creature_id, COUNT(*) n FROM creature_spawn GROUP BY creature_id")}

    def creature_by_name_on(self, name, ui_map=None):
        """Resolve a mob name ('Ice Claw Bears' -> 'Ice Claw Bear') to a creature id, preferring one that
        spawns on ui_map, then the one with most spawns."""
        if not name:
            return None
        n = name.strip().lower()
        ids = self.creature_by_name.get(n)
        if not ids and n.endswith("s"):
            ids = self.creature_by_name.get(n[:-1]) or (self.creature_by_name.get(n[:-2]) if n.endswith("es") else None)
        if not ids:
            return None
        if len(ids) == 1:
            return ids[0]
        best, best_key = None, None
        for cid in ids:
            on_map = 1 if ui_map is not None and self.spawns_of(cid, ui_map) else 0
            k = (on_map, self.spawn_count.get(cid, 0), -cid)
            if best_key is None or k > best_key:
                best, best_key = cid, k
        return best

    def spawns_of(self, cid, ui_map=None):
        """[(ui_map, x, y)] spawn points of a creature (optionally on one map)."""
        sp = self._spawns_by_creature(cid)
        if ui_map is None:
            return sp
        return [s for s in sp if s[0] == ui_map]

    def _spawns_by_creature(self, cid):
        d = self.__dict__.setdefault("_spc", {})
        if cid not in d:
            d[cid] = [(r["ui_map"], r["x"], r["y"]) for r in self.kb.q(
                "SELECT ui_map, x, y FROM creature_spawn WHERE creature_id=? AND x IS NOT NULL", cid)]
        return d[cid]

    def spawns_on_map(self, ui_map):
        """[(creature_id, x, y)] for one map (cached)."""
        if ui_map not in self._spawn_map:
            self._spawn_map[ui_map] = [(r["creature_id"], r["x"], r["y"]) for r in self.kb.q(
                "SELECT creature_id, x, y FROM creature_spawn WHERE ui_map=? AND x IS NOT NULL", ui_map)]
        return self._spawn_map[ui_map]

    GRID = 5.0                                   # percent per grid cell

    def spawn_grid(self, ui_map):
        """{(cx, cy): [(creature_id, x, y)]} with GRID-percent cells (cached)."""
        g = self.__dict__.setdefault("_sgrid", {})
        if ui_map not in g:
            cells = {}
            for cid, x, y in self.spawns_on_map(ui_map):
                cells.setdefault((int(x // self.GRID), int(y // self.GRID)), []).append((cid, x, y))
            g[ui_map] = cells
        return g[ui_map]

    def spawns_near(self, pos, radius_yd):
        """[(creature_id, x, y, dist_yd)] within radius of pos on its map (grid query)."""
        um = pos[0]
        w, h = self.map_size(um)
        if not w:
            return []
        grid = self.spawn_grid(um)
        rx, ry = radius_yd / w * 100.0, radius_yd / h * 100.0
        x0, x1 = int((pos[1] - rx) // self.GRID), int((pos[1] + rx) // self.GRID)
        y0, y1 = int((pos[2] - ry) // self.GRID), int((pos[2] + ry) // self.GRID)
        out = []
        px, py = pos[1], pos[2]
        for cx in range(x0, x1 + 1):
            for cy in range(y0, y1 + 1):
                for cid, x, y in grid.get((cx, cy), ()):
                    d = math.hypot((x - px) * w / 100.0, (y - py) * h / 100.0)
                    if d <= radius_yd:
                        out.append((cid, x, y, d))
        return out

    def givers_near(self, pos, radius_yd):
        """[(giver_row, dist_yd)] quest start givers within radius of pos (grid query)."""
        um = pos[0]
        w, h = self.map_size(um)
        g = self.__dict__.setdefault("_ggrid", {})
        if um not in g:
            cells = {}
            for r in self.start_by_map.get(um, ()):
                cells.setdefault((int(r["x"] // self.GRID), int(r["y"] // self.GRID)), []).append(r)
            g[um] = cells
        if not w:
            return []
        cells = g[um]
        rx, ry = radius_yd / w * 100.0, radius_yd / h * 100.0
        x0, x1 = int((pos[1] - rx) // self.GRID), int((pos[1] + rx) // self.GRID)
        y0, y1 = int((pos[2] - ry) // self.GRID), int((pos[2] + ry) // self.GRID)
        out = []
        for cx in range(x0, x1 + 1):
            for cy in range(y0, y1 + 1):
                for r in cells.get((cx, cy), ()):
                    d = math.hypot((r["x"] - pos[1]) * w / 100.0, (r["y"] - pos[2]) * h / 100.0)
                    if d <= radius_yd:
                        out.append((r, d))
        return out

    def _load_drops(self):
        out = {}
        for r in self.kb.q("SELECT creature_id, item_id, rate, n_obs FROM creature_drop"):
            out.setdefault(r["item_id"], []).append(r)
        self.__dict__["drops"] = out

    def _load_object_loot(self):
        out = {}
        for r in self.kb.q("SELECT object_id, item_id, rate FROM object_loot"):
            out.setdefault(r["item_id"], []).append(r)
        self.__dict__["object_loot"] = out

    def object_spawns(self, oid, ui_map=None):
        d = self.__dict__.setdefault("_osp", {})
        if oid not in d:
            d[oid] = [(r["ui_map"], r["x"], r["y"]) for r in self.kb.q(
                "SELECT ui_map, x, y FROM gameobject_spawn WHERE object_id=? AND x IS NOT NULL", oid)]
        return [s for s in d[oid] if ui_map is None or s[0] == ui_map]

    def _load_vendor_items(self):
        out = {}
        for r in self.kb.q("SELECT npc_id, item_id FROM vendor_item"):
            out.setdefault(r["item_id"], set()).add(r["npc_id"])
        self.__dict__["vendor_items"] = out

    def _load_items(self):
        self.__dict__["items"] = {r["id"]: r["name"] for r in self.kb.q("SELECT id, name FROM item")}

    def _load_notes(self):
        """Dangerous mobs: RXP creature_note rows -> [{creature_id, name, level, note, points: [(map, x, y)]}]."""
        out = []
        maps = {}
        for r in self.kb.q("SELECT ui_map, name FROM map"):
            if r["name"]:
                maps.setdefault(r["name"].lower(), r["ui_map"])
        for r in self.kb.q("SELECT * FROM creature_note"):
            pts = []
            for part in (r["location"] or "").split(";"):
                m = re.match(r"\s*\.(pin|line|goto)\s+([^,]+),(.*)", part)
                if not m:
                    continue
                zone = m.group(2).strip()
                um = maps.get(zone.lower())
                if um is None and zone.isdigit():
                    um = int(zone)
                nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", m.group(3))]
                for i in range(0, len(nums) - 1, 2):
                    if um is not None:
                        pts.append((um, nums[i], nums[i + 1]))
            lvl = r["max_level"] or r["min_level"]
            out.append({"creature_id": r["creature_id"], "name": r["name"], "level": lvl,
                        "note": " / ".join(x for x in (r["classification"] if r["classification"] not in (None, "Normal") else None,
                                                       r["movement"], r["notes"]) if x),
                        "points": pts})
        self.__dict__["notes"] = out
        by_map = {}
        for i, n in enumerate(out):
            for p in n["points"]:
                by_map.setdefault(p[0], []).append((i, p[1], p[2]))
            if n["creature_id"] is not None and not n["points"]:
                for um, x, y in self.spawns_of(n["creature_id"]):
                    by_map.setdefault(um, []).append((i, x, y))
        self.__dict__["notes_by_map"] = by_map

    def _load_notes_by_map(self):
        self._need("notes")

    def danger_on_map(self, ui_map, faction=None):
        """[(kind, ref, x, y)] dangerous points on a map: creature notes and hostile elite/rare spawns (cached).
        Creatures friendly to the player's faction (community friendlyToFaction) are left out."""
        d = self.__dict__.setdefault("_danger", {})
        key = (ui_map, faction)
        if key in d:
            return d[key]
        letter = {"Alliance": "A", "Horde": "H"}.get(faction, "?")
        out = [("note", i, x, y) for i, x, y in self.notes_by_map.get(ui_map, ())]
        noted = {self.notes[i]["creature_id"] for i, _, _ in self.notes_by_map.get(ui_map, ())}
        for cid, x, y in self.spawns_on_map(ui_map):
            c = self.creatures.get(cid)
            if not c or (c["rank"] or 0) not in RANK_ELITE or cid in noted:
                continue
            ft = c.get("friendly_to") or ""
            if ft == "AH" or letter in ft:
                continue
            out.append(("elite", cid, x, y))
        d[key] = out
        return out

    # ------------------------------------------------------------------------------------------ places
    def _load_maps(self):
        maps = {}
        for r in self.kb.q("SELECT ui_map, name, parent, continent, width_yd, height_yd FROM map"):
            maps[r["ui_map"]] = r
        self.__dict__["maps"] = maps

    def _load_bounds(self):
        self.__dict__["bounds"] = {r["ui_map"]: r for r in self.kb.q("SELECT * FROM map_bounds")}

    def map_size(self, ui_map):
        m = self.maps.get(ui_map)
        if m and m.get("width_yd") and m.get("height_yd"):
            return m["width_yd"], m["height_yd"]
        b = self.bounds.get(ui_map)
        if b:
            w, h = store.map_size_yd(b)
            if w and h:
                return w, h
        return None, None

    def map_name(self, ui_map):
        m = self.maps.get(ui_map)
        return m["name"] if m else None

    def continent(self, ui_map):
        b = self.bounds.get(ui_map)
        if b and b.get("continent") is not None:
            return b["continent"]
        m = self.maps.get(ui_map)
        return m.get("continent") if m else None

    def to_world(self, p):
        if p is None:
            return None
        b = self.bounds.get(p[0])
        if not b:
            return None
        wx, wy = store.map_to_world(b, p[1], p[2])
        return b["continent"], wx, wy

    def to_map(self, ui_map, wx, wy):
        b = self.bounds.get(ui_map)
        if not b:
            return None, None
        return store.world_to_map(b, wx, wy)

    def dist_yd(self, a, b):
        """Yards between two (ui_map, x, y) points; None when unknown, math.inf across continents."""
        if a is None or b is None:
            return None
        if a[0] == b[0]:
            w, h = self.map_size(a[0])
            if w:
                return MT.distance_yd(w, h, a[1], a[2], b[1], b[2])
        wa, wb = self.to_world(a), self.to_world(b)
        if wa and wb:
            if wa[0] != wb[0]:
                return math.inf
            return math.hypot(wa[1] - wb[1], wa[2] - wb[2])
        ca, cb = self.continent(a[0]), self.continent(b[0])
        if ca is not None and cb is not None and ca != cb:
            return math.inf
        return None

    def _world_to_any_map(self, um, cont, wx, wy, name=None):
        """(ui_map, x, y) for a world point, as build_kb's finalize places world-only POIs: a city map the POI
        name mentions ("Ironforge, Dun Morogh" -> Ironforge) when it contains the point, else the named map,
        else the smallest map of the continent containing it. None when outside all."""
        def on(b, a1, a2):
            x, y = store.world_to_map(b, a1, a2)
            return (x, y) if x is not None and -0.5 <= x <= 100.5 and -0.5 <= y <= 100.5 else None
        inside = []
        for m, bb in self.bounds.items():
            if (cont is not None and bb.get("continent") != cont) or m in (1414, 1415, 947):
                continue
            xy = on(bb, wx, wy)
            if xy:
                w, h = store.map_size_yd(bb)
                inside.append((w * h, m, xy))
        inside.sort()
        parts = [p.strip().lower() for p in (name or "").split(",") if p.strip()]
        def mentioned(m):
            mn = (self.map_name(m) or "").lower()
            return bool(mn) and any(mn.startswith(p) or p.startswith(mn) for p in parts)
        for cities in (True, False):                 # a mentioned city first, then a mentioned zone
            for _, m, xy in inside:
                if (m in (1453, 1454, 1455, 1456, 1457, 1458)) == cities and mentioned(m):
                    return m, xy[0], xy[1]
        for _, m, xy in inside:
            if m == um:
                return m, xy[0], xy[1]
        if inside:
            return inside[0][1], inside[0][2][0], inside[0][2][1]
        b = self.bounds.get(um)
        if b:
            xy = on(b, wy, wx)                     # a source with the axes swapped
            if xy:
                return um, xy[0], xy[1]
        return None

    def _load_pois(self):
        flights, gys, inns = {}, [], []
        for r in self.kb.q("SELECT * FROM poi"):
            if r.get("x") is None and r.get("wx") is not None and r.get("wy") is not None:
                # world-only rows (flight nodes, graveyards) before build_kb's finalize step converts them
                cont = r.get("continent")
                if cont is None and r.get("ui_map") in self.bounds:
                    cont = self.bounds[r["ui_map"]].get("continent")
                pos = self._world_to_any_map(r.get("ui_map"), cont, r["wx"], r["wy"], r.get("name"))
                if pos:
                    r = dict(r, ui_map=pos[0], x=round(pos[1], 2), y=round(pos[2], 2))
            if r["kind"] == "flight":
                flights[r["id"]] = r
            elif r["kind"] == "graveyard":
                gys.append(r)
            elif r["kind"] in ("inn",):
                inns.append(r)
        self.__dict__["pois"] = {"flight": flights, "graveyard": gys, "inn": inns}

    def _load_flights(self):
        out = {}
        for r in self.kb.q("SELECT faction, from_id, to_id, time_s FROM flight_path"):
            out[(r["faction"], r["from_id"], r["to_id"])] = r["time_s"]
        self.__dict__["flights"] = out

    def flight_nodes(self, faction):
        out = []
        for r in self.pois["flight"].values():
            if r["x"] is None:
                continue
            if r["faction"] in (None, "Both", faction):
                out.append(r)
        return out

    def nearest_flight_node(self, p, faction, max_yd=None):
        best, bd = None, None
        for r in self.flight_nodes(faction):
            d = self.dist_yd(p, (r["ui_map"], r["x"], r["y"]))
            if d is None or d == math.inf:
                continue
            if bd is None or d < bd:
                best, bd = r, d
        if best is not None and max_yd is not None and bd > max_yd:
            return None, bd
        return best, bd

    def flight_node_named(self, name, faction, near=None):
        """Node whose name starts with `name` ('Ironforge' -> 'Ironforge, Dun Morogh'), nearest to `near`."""
        if not name:
            return None
        n = name.strip().lower()
        cands = [r for r in self.flight_nodes(faction) if r["name"] and
                 (r["name"].lower().startswith(n) or n in r["name"].lower().split(",")[-1].strip()
                  or r["name"].lower().split(",")[0].strip() in n)]
        if not cands:
            return None
        if near is not None and len(cands) > 1:
            cands.sort(key=lambda r: (self.dist_yd(near, (r["ui_map"], r["x"], r["y"])) or math.inf))
        return cands[0]

    def flight_time(self, faction, a, b):
        """Seconds a->b for flight node ids (known RXP time, else taxi speed over the straight line)."""
        if a == b:
            return 0.0, "same_node"
        t = self.flights.get((faction, a, b))
        if t is None:
            for f in ("Both", None):
                t = self.flights.get((f, a, b))
                if t is not None:
                    break
        if t is not None:
            return float(t), "flight_path"
        ra, rb = self.pois["flight"].get(a), self.pois["flight"].get(b)
        if ra and rb:
            d = self.dist_yd((ra["ui_map"], ra["x"], ra["y"]), (rb["ui_map"], rb["x"], rb["y"]))
            if d is not None and d != math.inf:
                return MT.taxi_time_s(d * 1.15), "taxi_speed"
        return None, "unknown"

    def nearest_graveyard(self, p):
        best, bd = None, None
        for r in self.pois["graveyard"]:
            if r["x"] is None:
                continue
            d = self.dist_yd(p, (r["ui_map"], r["x"], r["y"]))
            if d is None or d == math.inf:
                continue
            if bd is None or d < bd:
                best, bd = r, d
        return best, bd

    def _load_area_boxes(self):
        out = {}
        for r in self.kb.q("SELECT area_id, ui_map, x1, y1, x2, y2 FROM area_box"):
            out.setdefault(r["area_id"], []).append(r)
        for r in self.kb.q("SELECT area_id, ui_map, x1, y1, x2, y2 FROM area WHERE x1 IS NOT NULL"):
            if r["area_id"] not in out:
                out.setdefault(r["area_id"], []).append(r)
        self.__dict__["area_boxes"] = out

    def _load_areas_by_name(self):
        out = {}
        for r in self.kb.q("SELECT area_id, name, ui_map FROM area"):
            if r["name"]:
                out.setdefault(r["name"].lower(), []).append(r)
        self.__dict__["areas_by_name"] = out

    def place_named(self, name):
        """Sub-zone / town name -> (ui_map, x, y): the inn nearest to its box centre (or the centre itself)."""
        if not name:
            return None
        key = name.strip().lower()
        cache = self.__dict__.setdefault("_places", {})
        if key in cache:
            return cache[key]
        out = None
        for a in self.areas_by_name.get(key, ()):
            for b in self.area_boxes.get(a["area_id"], ()):
                c = (b["ui_map"], (b["x1"] + b["x2"]) / 2.0, (b["y1"] + b["y2"]) / 2.0)
                best, bd = None, None
                for inn in self.pois["inn"]:
                    if inn["ui_map"] != c[0] or inn["x"] is None:
                        continue
                    d = self.dist_yd(c, (inn["ui_map"], inn["x"], inn["y"]))
                    if d is not None and (bd is None or d < bd):
                        best, bd = inn, d
                out = (best["ui_map"], best["x"], best["y"]) if best is not None and bd < 400 else c
                break
            if out:
                break
        if out is None:
            for f in self.pois["flight"].values():
                if f["name"] and f["name"].lower().split(",")[0].strip() == key and f["x"] is not None:
                    out = (f["ui_map"], f["x"], f["y"])
                    break
        cache[key] = out
        return out

    def in_area(self, area_id, p):
        """True/False when the area has boxes on p's map, None when unknown."""
        if p is None:
            return None
        boxes = [b for b in self.area_boxes.get(area_id, ()) if b["ui_map"] == p[0]]
        if not boxes:
            return None                 # no box on this map (or none at all): unknown, per the contract above
        return any(b["x1"] <= p[1] <= b["x2"] and b["y1"] <= p[2] <= b["y2"] for b in boxes)

    def _load_meta_rxp(self):
        out = {}
        for r in self.kb.q("SELECT key, json FROM rxp_meta"):
            out[r["key"]] = _j(r["json"])
        self.__dict__["meta_rxp"] = out

    # ------------------------------------------------------------------------------------------ travel
    def _load_edges(self):
        g = {}
        for r in self.kb.q("SELECT ui_map, x1, y1, x2, y2, dist_yd, time_s FROM travel_edge WHERE time_s > 0"):
            a, b = (int(round(r["x1"])), int(round(r["y1"]))), (int(round(r["x2"])), int(round(r["y2"])))
            dist = r["dist_yd"]
            if dist is None:
                w, h = self.map_size(r["ui_map"])
                dist = MT.distance_yd(w, h, a[0], a[1], b[0], b[1]) if w else None
            g.setdefault(r["ui_map"], {}).setdefault(a, []).append((b, float(r["time_s"]), dist or 0.0))
        self.__dict__["edges"] = g

    def _graph_node(self, ui_map, x, y):
        g = self.edges.get(ui_map)
        if not g:
            return None
        cx, cy = int(round(x)), int(round(y))
        best, bd = None, None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                n = (cx + dx, cy + dy)
                if n in g:
                    d = (n[0] - x) ** 2 + (n[1] - y) ** 2
                    if bd is None or d < bd:
                        best, bd = n, d
        return best

    def _dijkstra(self, ui_map, src):
        key = (ui_map, src)
        if key in self._dij:
            return self._dij[key]
        g = self.edges.get(ui_map, {})
        best = {src: (0.0, 0.0)}
        heap = [(0.0, 0.0, src)]
        while heap:
            t, d, n = heapq.heappop(heap)
            if best.get(n, (math.inf,))[0] < t:
                continue
            for m, et, ed in g.get(n, ()):
                nt = t + et
                if nt < best.get(m, (math.inf,))[0]:
                    best[m] = (nt, d + ed)
                    heapq.heappush(heap, (nt, d + ed, m))
        if len(self._dij) > 20000:
            self._dij.clear()
        self._dij[key] = best
        return best

    def observed_path(self, a, b):
        """(time_s, dist_yd) over observed travel_edge cells when both endpoints are within one cell of the
        graph and a path exists; else None."""
        if a is None or b is None or a[0] != b[0] or a[0] not in self.edges:
            return None
        na, nb = self._graph_node(*a), self._graph_node(*b)
        if na is None or nb is None:
            return None
        if na == nb:
            return None
        r = self._dijkstra(a[0], na).get(nb)
        if not r:
            return None
        straight = self.dist_yd(a, b) or 0.0
        if r[1] > AP.get("analyzer.travel.edge_max_ratio") * straight + 50.0:
            return None
        # endpoints to their cell centres at run speed
        extra = (self.dist_yd(a, (a[0], na[0], na[1])) or 0.0) + (self.dist_yd(b, (b[0], nb[0], nb[1])) or 0.0)
        return r[0] + MT.run_time_s(extra), r[1] + extra

    # ------------------------------------------------------------------------------------------ guides
    def _load_guide_rows(self):
        out = {}
        for r in self.kb.q("SELECT key, name, grp, subgroup, next, level_min, level_max, faction, default_for, "
                           "season FROM rxp_guide"):
            out[r["key"]] = r
        self.__dict__["guide_rows"] = out
        by_name = {}
        for k, r in out.items():
            by_name.setdefault(r["name"], []).append(k)
        self.__dict__["guide_by_name"] = by_name

    def _load_guide_by_name(self):
        self._need("guide_rows")

    def guide_header(self, key):
        """Guide-level `<<` condition and #group lines parsed from the raw text header, and the header text
        itself ("text": everything before the first step) (cached)."""
        d = self.__dict__.setdefault("_hdr", {})
        if key in d:
            return d[key]
        from knowledge import rxp_parser as RP
        r = self.kb.one("SELECT text FROM rxp_guide WHERE key=?", key)
        text = (r or {}).get("text") or ""
        hdr = re.split(r"(?m)^\s*step\b", text, maxsplit=1)[0]
        conds, groups = [], []
        for line in hdr.splitlines():
            code = line.split("--", 1)[0].strip()
            if code.startswith("<<"):
                conds.append(code[2:].strip())
            elif code.startswith("#group"):
                m = re.match(r"#group\s+(.*?)(?:\s*<<\s*(.*))?$", code)
                if m:
                    groups.append((m.group(1).strip(), m.group(2)))
        gcond = RP.and_cond(conds[0], conds[-1]) if conds else None
        d[key] = {"cond": gcond, "groups": groups, "text": hdr}
        return d[key]

    def guide(self, key):
        """Raw guide: header row + steps (json decoded) + actions per step. Cached per key."""
        if key in self._guides:
            return self._guides[key]
        row = self.guide_rows.get(key)
        if row is None:
            return None
        steps = []
        acts = {}
        for a in self.kb.q("""SELECT idx, line, verb, args, quest_id, entity_kind, entity_id, qty, text, cond
                              FROM rxp_action WHERE guide_key=? ORDER BY idx, line""", key):
            a["args"] = _j(a["args"], {}) or {}
            a["cond"] = _j(a["cond"])
            acts.setdefault(a["idx"], []).append(a)
        for s in self.kb.q("""SELECT idx, text, flags, cond, ui_map, x, y, continent, wx, wy, zone, comments
                              FROM rxp_step WHERE guide_key=? ORDER BY idx""", key):
            s["flags"] = _j(s["flags"], {}) or {}
            s["cond"] = _j(s["cond"])
            s["actions"] = acts.get(s["idx"], [])
            steps.append(s)
        g = dict(row)
        g["steps"] = steps
        with self._lock:
            if len(self._guides) > 64:
                self._guides.clear()
            self._guides[key] = g
        return g


def run_time(dist, level=None):
    """Seconds on foot (mounted when the level allows a mount) for a straight-line map distance."""
    mounted = level is not None and int(level) >= int(MP.get("mount.level"))
    t = MT.run_time_s(dist, mounted=mounted, level=level)
    if MP.status("travel.detour_factor") in ("classic", "unverified") and float(MP.get("travel.detour_factor")) == 1.0:
        t *= float(AP.get("analyzer.travel.detour"))
    return t
