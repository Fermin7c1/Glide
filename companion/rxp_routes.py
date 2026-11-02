"""Walking routes between a guide's steps for the Guides page: the way RestedXP's route really goes over the terrain
(the walking maps the live coach uses), flights along their flown curves, hearths, deathskips and boats.

    python companion/rxp_import.py routes <id> <index>      (the request as JSON on stdin)

Run in its own short process, never in the engine: the walking-map loader keeps one continent in memory, so routes
on the other continent would push out the live coach's map. Below-normal priority on Windows (the game comes first).

Request {"steps": [st.idx, ...] the visible steps in the order the page shows them, "focus": st.idx (the step the
player is looking at: its legs are worked out first), "budget_ms": 2500, "faction": "Alliance"|"Horde", "level": n}.
faction and level default to the guide's own; budget_ms 0 answers only what the cache already holds.

Answer {"ok": true, "routes": {
    "legs": [leg, ...]          every leg worked out (from the cache or now), in step order
    "pending": [[from, a, to, b], ...]  legs not worked out yet within the budget: ask again (the cache keeps what
                                this call found), nearest the focus first
    "nav": {"<continent>": "graph"|"grid"|"none"}   what walking legs on each continent were routed over
    "unavailable": why          only when nothing can be placed (no data/kb.db map bounds)
    "ms": elapsed milliseconds}}
leg = {"from": st.idx, "a": waypoint index in that step's `waypoints` (-1: the step's own x/y),
       "to": st.idx, "b": waypoint index, "mode": "walk"|"fly"|"hearth"|"death"|"boat"|"straight",
       "yd": yards walked and flown (a jump's own yd is only how far it lands), "s": seconds when known,
       "approx": true when part of it is a straight guess,
       "segments": [segment, ...]}
A leg joins consecutive stops: the walk points (goto modes wp, arrive, path, noarrow, lowprio; never dynamic) inside
a step, and the last stop of a visible step to the first of the next visible step that has one. Its mode comes from
the earlier step and the position-less steps between them: .fly -> walk to the flight master, fly, walk from the
landing; .hs -> hearth to the last .home before it (else the inn of the hearth's town), then walk; .deathskip ->
the graveyard the analyzer picks (KBData.deathskip_graveyard), then walk; another continent -> boat.
segment = {"mode": "walk"|"straight"|"fly"|"hearth"|"death"|"boat",
           "maps": {"<ui_map>": [[x, y], ...]}   the segment in map percent on every map of the guide (and its
                    continent map) on that continent that some point falls in (-5..105); not clamped
           "yd", "s",
           walk:     "straight_yd", "swim_yd", "through": [area names], "dirs": [{"go", "yd", "to"}], "source"
           straight: "why" (not routed: too far, no walking map, unreachable within the search cap, ...)
           fly:      "from", "to" (flight master names), "hops"
           hearth / death / boat: "to" (where it lands, when known); a jump, drawn as an arc, never walked}

Cache: data/rxp_imports/routes.db (sqlite3), walking geometry only, keyed by continent and the two world endpoints
rounded to yards, A->B never reused for B->A (one-way drops); each row stamped with what it was routed over (the
walking map's version and file time, else the terrain grid's and kb.db's), and recomputed when that changes.
Times (level-dependent) and map percent are worked out on every answer. Walked legs are timed as the coach times
them (kbdata.run_time walked=True: the path's length at map-plane speed, no straight-line detour on top).
"""
import json
import math
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

CACHE = os.path.join("data", "rxp_imports", "routes.db")
ALGO = 1                    # bump when the routing changes: every cached leg is worked out again
WALK_MODES = ("wp", "arrive", "path", "noarrow", "lowprio")
SHORT_YD = 15.0             # closer stops: one straight line, nothing to route
FAR_YD = 4000.0             # further on one continent without a flight: almost always an unstated flight or boat
CONTINENT_MAPS = {1: 1414, 0: 1415}     # Kalimdor, Eastern Kingdoms: the continent view's maps
COMMIT_EVERY = 20


def max_expand(straight_yd):
    """The search cap for one leg: enough for long detours, about 1-5 s at worst for a leg that can't be reached."""
    return int(min(150000, 30000 + 60 * straight_yd))


def below_normal_priority():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x4000)     # BELOW_NORMAL_PRIORITY_CLASS
    except Exception as e:  # noqa: BLE001 - normal priority then
        from log import log_once
        log_once("rxp_routes.priority", e)


def _detail(root, iid, index):
    """The guide as the Guides page shows it (placed map percent, detect_swap applied)."""
    import rxp_import
    return rxp_import.guide_detail(root, iid, index)


def _layer():
    from knowledge import store
    from knowledge.analyzer import kbdata
    from knowledge.api import navigation
    from knowledge.mechanics import nav, taxi
    from knowledge.mechanics import params as mp
    return store, kbdata, navigation, nav, taxi, mp


def _dist(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


def _length(pts):
    return sum(_dist(pts[i - 1], pts[i]) for i in range(1, len(pts)))


# ---- the cache -------------------------------------------------------------------------------------------------
class Cache:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.c = sqlite3.connect(path, timeout=10)
        self.c.execute("""CREATE TABLE IF NOT EXISTS leg(cont INT, ax INT, ay INT, bx INT, by INT, stamp TEXT,
                          status TEXT, pts TEXT, walk_yd REAL, swim_yd REAL, areas TEXT, dirs TEXT, source TEXT,
                          why TEXT, PRIMARY KEY(cont, ax, ay, bx, by))""")
        self.c.commit()
        self.pending_writes = 0
        self.swept = set()
        self.hits = 0

    @staticmethod
    def key(cont, wa, wb):
        return (int(cont), int(round(wa[0])), int(round(wa[1])), int(round(wb[0])), int(round(wb[1])))

    def get(self, cont, wa, wb, stamp):
        r = self.c.execute("SELECT stamp, status, pts, walk_yd, swim_yd, areas, dirs, source, why FROM leg WHERE "
                           "cont=? AND ax=? AND ay=? AND bx=? AND by=?", self.key(cont, wa, wb)).fetchone()
        if not r or r[0] != stamp:
            return None
        self.hits += 1
        if r[1] != "ok":
            return {"why": r[8]}
        return {"pts": json.loads(r[2]), "walk_yd": r[3], "swim_yd": r[4], "areas": json.loads(r[5] or "[]"),
                "dirs": json.loads(r[6] or "[]"), "source": r[7]}

    def put(self, cont, wa, wb, stamp, res):
        if cont not in self.swept:              # rows routed over an older map are never read again
            self.c.execute("DELETE FROM leg WHERE cont=? AND stamp<>?", (int(cont), stamp))
            self.swept.add(cont)
        if "why" in res:
            row = (stamp, "fail", None, None, None, None, None, None, res["why"])
        else:
            row = (stamp, "ok", json.dumps(res["pts"]), res["walk_yd"], res["swim_yd"], json.dumps(res["areas"]),
                   json.dumps(res["dirs"]), res["source"], None)
        self.c.execute("INSERT OR REPLACE INTO leg VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", self.key(cont, wa, wb) + row)
        self.pending_writes += 1
        if self.pending_writes >= COMMIT_EVERY:         # a cut-off process keeps its progress
            self.flush()

    def flush(self):
        if self.pending_writes:
            self.c.commit()
            self.pending_writes = 0

    def close(self):
        self.flush()
        self.c.close()


# ---- routing ---------------------------------------------------------------------------------------------------
class Router:
    def __init__(self, root, kb):
        self.root = root
        self.kb = kb
        self.store, self.kbdata, self.navigation, self.nav, self.taxi, self.mp = _layer()
        self._stamps = {}

    def stamp(self, cont):
        """(stamp, kind) of what walking legs on this continent are routed over."""
        if cont in self._stamps:
            return self._stamps[cont]
        from wowfiles import navformat
        gd = os.path.join(self.root, "data", "gamedata")
        nav_p = os.path.join(gd, "nav", "%d.npz" % cont)
        ter_p = os.path.join(gd, "terrain", "%d.npz" % cont)
        try:
            out = ("a%d:nav:%d:%d" % (ALGO, navformat.NAV_VERSION, int(os.path.getmtime(nav_p))), "graph")
        except OSError:
            try:
                kb_mt = int(os.path.getmtime(self.kb.path)) if self.kb.ok else 0
                out = ("a%d:grid:%d:%d" % (ALGO, int(os.path.getmtime(ter_p)), kb_mt), "grid")
            except OSError:
                out = ("none", "none")
        self._stamps[cont] = out
        return out

    def area_at(self, cont, wx, wy):
        g = self.nav.load_graph(self.root, cont)
        grid = g if g is not None else self.nav.load(self.root, cont)
        if grid is None:
            return None
        rc = grid.cell_of(wx, wy)
        return int(grid.area[rc]) if rc else None

    def walk(self, cont, wa, wb):
        """Route one walking segment now -> cache row dict ({"pts", ...} or {"why"})."""
        straight = _dist(wa, wb)
        r = self.navigation.walk_world(self.kb, cont, wa, wb, max_expand=max_expand(straight), root=self.root)
        if isinstance(r, str):
            return {"why": r}
        pts, swim, areas, source = r
        walk = _length(pts)
        simple = self.nav.simplify(pts, 2.0) if len(pts) > 2 else list(pts)
        tol = max(12.0, walk / 40.0)
        legs = self.nav.simplify(pts, tol)
        while len(legs) - 1 > 7:
            tol *= 1.5
            legs = self.nav.simplify(pts, tol)
        dirs = []
        for i in range(1, len(legs)):
            (x1, y1), (x2, y2) = legs[i - 1], legs[i]
            dirs.append([self.nav.compass(-(y2 - y1), x2 - x1), int(round(_dist(legs[i - 1], legs[i]))),
                         self.area_at(cont, x2, y2)])
        ids = []
        for a in areas:
            if a and (not ids or ids[-1] != a):
                ids.append(int(a))
        return {"pts": [[round(x * 2) / 2.0, round(y * 2) / 2.0] for x, y in simple], "walk_yd": round(walk, 1),
                "swim_yd": round(swim, 1), "areas": ids[:40], "dirs": dirs, "source": source}


# ---- the guide's stops -----------------------------------------------------------------------------------------
def _stops(st):
    """[(waypoint index, ui_map, x, y)]: the step's walk points, else its own position."""
    out = []
    for i, w in enumerate(st.get("waypoints") or ()):
        if w.get("mode") in WALK_MODES and w.get("ui_map") and w.get("x") is not None and w.get("y") is not None:
            out.append((i, int(w["ui_map"]), float(w["x"]), float(w["y"])))
    if not out and st.get("ui_map") and st.get("x") is not None and st.get("y") is not None:
        out.append((-1, int(st["ui_map"]), float(st["x"]), float(st["y"])))
    return out


def _verbs(st):
    return {a.get("verb"): a for a in st.get("actions") or () if a.get("verb")}


SPECIAL = ("fly", "hs", "deathskip")
JUMPS = ("hearth", "death", "boat")      # not travelled along the line: no yards toward the leg's total


class Routes:
    def __init__(self, root, detail, req, kb=None):
        self.root = root
        self.t0 = time.monotonic()
        self.req = req or {}
        self.detail = detail
        store, kbdata, _nv, _n, taxi, mp = _layer()
        self.store, self.mp, self.taxi = store, mp, taxi
        self.kb = kb or store.KB(os.path.join(root, "data", "kb.db"))
        self.data = kbdata.get_data(self.kb) if self.kb.ok else None     # the kb's fitted parameters apply
        self.run_time = kbdata.run_time
        self.router = Router(root, self.kb)
        self.faction = self.req.get("faction") or detail.get("faction")
        lvl = self.req.get("level") or detail.get("level_min")
        self.level = int(lvl) if isinstance(lvl, (int, float)) and lvl > 0 else 1
        self.by_idx = {st["idx"]: st for st in detail.get("steps") or ()}
        self.order = [st["idx"] for st in detail.get("steps") or ()]
        self._area_names = {}
        self._net = None
        self._net_loaded = False
        maps = {int(z["ui_map"]) for z in detail.get("zones") or () if z.get("ui_map")}
        for st in detail.get("steps") or ():
            maps |= {int(w["ui_map"]) for w in st.get("waypoints") or () if w.get("ui_map")}
            if st.get("ui_map"):
                maps.add(int(st["ui_map"]))
        maps |= set(CONTINENT_MAPS.values())
        self.bounds = {}
        for m in sorted(maps):
            b = self.kb.bounds(m) if self.kb.ok else None
            if b and b.get("continent") is not None:
                self.bounds[m] = b

    # ---- coordinates ----
    def world(self, um, x, y):
        b = self.bounds.get(um) or (self.kb.bounds(um) if self.kb.ok else None)
        if not b or b.get("continent") is None:
            return None
        wx, wy = self.store.map_to_world(b, x, y)
        return int(b["continent"]), wx, wy

    def maps_of(self, cont, pts):
        """{ui_map: points in map %} on every map of this continent some point falls in."""
        out = {}
        for um, b in self.bounds.items():
            if int(b["continent"]) != cont:
                continue
            conv = [self.store.world_to_map(b, wx, wy) for wx, wy in pts]
            if any(x is None for x, _y in conv):
                continue
            if any(-5 <= x <= 105 and -5 <= y <= 105 for x, y in conv):
                out[str(um)] = [[round(x, 2), round(y, 2)] for x, y in conv]
        return out

    def area_names(self, ids):
        out = []
        for aid in ids:
            if aid not in self._area_names:
                r = self.kb.one("SELECT name FROM area WHERE area_id=?", aid) if aid and self.kb.ok else None
                self._area_names[aid] = (r or {}).get("name")
            nm = self._area_names[aid]
            if nm and (not out or out[-1] != nm):
                out.append(nm)
        return out

    def net(self):
        if not self._net_loaded:
            self._net = self.taxi.load(self.root)
            self._net_loaded = True
        return self._net

    def allowed(self):
        if self.data is None:
            return None
        ids = {r["id"] for r in self.data.flight_nodes(self.faction)}
        return ids or None

    def mounted(self):
        return self.level >= int(self.mp.get("mount.level"))

    # ---- legs ----
    def plan(self):
        """[(from, a, to, b, kind, info)] in step order: the legs between consecutive stops of the visible steps."""
        wanted = [i for i in (self.req.get("steps") or self.order) if i in self.by_idx]
        legs = []
        prev = None            # (idx, wp index, world point, step)
        between = []           # verbs of the steps since prev's (its own, and position-less steps after it)
        for idx in wanted:
            st = self.by_idx[idx]
            stops = [(i, self.world(um, x, y)) for i, um, x, y in _stops(st)]
            stops = [(i, w) for i, w in stops if w]
            if not stops:
                between.append(_verbs(st))
                continue
            if prev is not None:
                legs.append((prev[0], prev[1], idx, stops[0][0], prev[2], stops[0][1], self.kind(between), prev[3]))
            for k in range(1, len(stops)):
                legs.append((idx, stops[k - 1][0], idx, stops[k][0], stops[k - 1][1], stops[k][1], ("walk", None), st))
            prev = (idx, stops[-1][0], stops[-1][1], st)
            between = [_verbs(st)]
        return legs

    @staticmethod
    def kind(verb_sets):
        found = ("walk", None)
        for vs in verb_sets:
            for v in SPECIAL:
                if v in vs:
                    found = (v, vs[v])
        return found

    def home_before(self, idx):
        """World position of the last .home step before step idx in the guide (whatever is visible)."""
        best = None
        for i in self.order:
            if i >= idx:
                break
            st = self.by_idx[i]
            if "home" in _verbs(st):
                stops = _stops(st)
                w = self.world(*stops[-1][1:]) if stops else None
                if w:
                    best = w
        return best

    def pos_of(self, w):
        """(ui_map, x, y) of a world point, for KBData's map-percent helpers."""
        for um, b in self.bounds.items():
            if int(b["continent"]) == w[0]:
                x, y = self.store.world_to_map(b, w[1], w[2])
                if x is not None and 0 <= x <= 100 and 0 <= y <= 100:
                    return um, x, y
        return None

    def poi_world(self, r):
        if r is None:
            return None
        if r.get("wx") is not None and r.get("continent") is not None:
            return int(r["continent"]), float(r["wx"]), float(r["wy"])
        return self.world(r["ui_map"], r["x"], r["y"]) if r.get("ui_map") and r.get("x") is not None else None

    def pieces(self, leg):
        """The leg as segments to answer: ('walk', cont, wa, wb) still to route, or finished segment dicts."""
        frm, _a, to, _b, wa, wb, (kind, act), _st = leg
        out = []
        start = wa
        fly = self.flight(wa, wb, (act or {}).get("location")) if kind == "fly" else None
        if fly:
            seg, take, land = fly
            out.append(("walk", wa, take))
            out.append(seg)
            start = land
        elif kind == "hs":
            dest = self.home_before(frm)
            if dest is None and self.data is not None:
                p = self.data.place_named((act or {}).get("location"))
                dest = self.world(*p) if p else None
            dest = dest or wb
            out.append(self.jump("hearth", wa, dest, (act or {}).get("location")))
            start = dest
        elif kind == "deathskip" and self.data is not None:
            here, nxt = self.pos_of(wa), self.pos_of(wb)
            gy = self.data.deathskip_graveyard(here, nxt) if here else None
            dest = self.poi_world(gy) or wb
            out.append(self.jump("death", wa, dest, (gy or {}).get("name")))
            start = dest
        elif kind == "deathskip":
            out.append(self.jump("death", wa, wb, None))
            start = wb
        if start[0] != wb[0]:
            out.append(self.jump("boat", start, wb, None))
        else:
            out.append(("walk", start, wb))
        return [p for p in out if not (isinstance(p, tuple) and _dist(p[1][1:], p[2][1:]) < 0.5 and p[1][0] == p[2][0])]

    def jump(self, mode, wa, wb, to):
        seg = {"mode": mode, "yd": None, "s": None, "to": to}
        if wa[0] == wb[0]:
            seg["maps"] = self.maps_of(wa[0], [wa[1:], wb[1:]])
            seg["yd"] = int(round(_dist(wa[1:], wb[1:])))
        else:                       # each end on its own continent's maps
            ma, mb = self.maps_of(wa[0], [wa[1:]]), self.maps_of(wb[0], [wb[1:]])
            seg["maps"] = dict(ma, **mb)
        return seg

    def flight(self, wa, wb, location):
        """(fly segment, take-off world point, landing world point) or None."""
        net = self.net()
        allowed = self.allowed()
        if net is not None:
            org, _yd = net.nearest(wa[0], wa[1], wa[2], allowed)
            near = (wb[1], wb[2]) if wb[0] == wa[0] else None
            dst = net.named(location, wa[0], allowed, near) if location else None
            if dst is None and near is not None:
                dst, _ = net.nearest(wa[0], near[0], near[1], allowed)
            r = net.route(org, dst, allowed) if org is not None and dst is not None else None
            if r:
                s = None
                if self.data is not None:
                    s, _how = self.data.flight_time(self.faction, org, dst)
                    s = s if s else None
                s = s or self.taxi_s(r["yd"])
                a, b = net.nodes[org], net.nodes[dst]
                seg = {"mode": "fly", "maps": self.maps_of(wa[0], r["points"]), "yd": int(round(r["yd"])),
                       "s": int(round(s)), "from": a["name"], "to": b["name"], "hops": r["hops"]}
                return seg, (wa[0], a["x"], a["y"]), (wa[0], b["x"], b["y"])
        if self.data is None:
            return None
        here, near = self.pos_of(wa), self.pos_of(wb) if wb[0] == wa[0] else None
        org = self.data.nearest_flight_node(here, self.faction)[0] if here else None
        dst = self.data.flight_node_named(location, self.faction, near) if location else None
        if org is None or dst is None or org["id"] == dst["id"]:
            return None
        pa, pb = self.poi_world(org), self.poi_world(dst)
        if pa is None or pb is None or pa[0] != pb[0]:
            return None
        s, _how = self.data.flight_time(self.faction, org["id"], dst["id"])
        yd = _dist(pa[1:], pb[1:])
        seg = {"mode": "fly", "maps": self.maps_of(pa[0], [pa[1:], pb[1:]]), "yd": int(round(yd)),
               "s": int(round(s or self.taxi_s(yd))), "from": org.get("name"), "to": dst.get("name"), "hops": 1,
               "approx": True}
        return seg, pa, pb

    def taxi_s(self, yd):
        from knowledge.mechanics import travel
        return travel.taxi_time_s(yd)

    def walk_segment(self, cont, wa, wb, row):
        """A finished walk (or straight) segment dict from a cache row (None: route it)."""
        straight = _dist(wa, wb)
        if row is None or "why" in row:
            why = (row or {}).get("why") or "not worked out"
            return {"mode": "straight", "maps": self.maps_of(cont, [wa, wb]), "yd": int(round(straight)),
                    "s": int(round(self.run_time(straight, self.level))), "why": why}
        pts = [list(wa)] + row["pts"][1:-1] + [list(wb)]
        walk = row["walk_yd"]
        return {"mode": "walk", "maps": self.maps_of(cont, pts), "yd": int(round(walk)),
                "s": int(round(self.run_time(walk, self.level, walked=True))), "straight_yd": int(round(straight)),
                "swim_yd": int(round(row["swim_yd"] or 0)) or None, "through": self.area_names(row["areas"])[:10],
                "dirs": [{"go": go, "yd": yd, "to": (self.area_names([aid]) or [None])[0] if aid else None}
                         for go, yd, aid in row["dirs"]], "source": row["source"]}

    def run(self):
        if not self.bounds:
            return {"legs": [], "pending": [], "nav": {},
                    "unavailable": "no map bounds (the knowledge base, data/kb.db, isn't built yet)", "ms": self.ms()}
        budget = float(self.req.get("budget_ms", 2500) or 0)
        legs = self.plan()
        cache = Cache(os.path.join(self.root, CACHE))
        try:
            return self._run(legs, cache, budget)
        finally:
            cache.close()

    def ms(self):
        return int((time.monotonic() - self.t0) * 1000)

    def _run(self, legs, cache, budget):
        wanted = [i for i in (self.req.get("steps") or self.order) if i in self.by_idx]
        pos = {idx: k for k, idx in enumerate(wanted)}
        focus = pos.get(self.req.get("focus"), 0)
        built = []
        rows = {}               # (cont, key) -> cache row or None (to route)
        nav = {}
        todo = []               # (rank, cont, wa, wb)
        for n, leg in enumerate(legs):
            ps = self.pieces(leg)
            built.append(ps)
            d = pos.get(leg[0], 0) - focus
            for p in ps:
                if not isinstance(p, tuple):
                    continue
                _m, wa, wb = p
                cont = wa[0]
                stamp, kind = self.router.stamp(cont)
                nav[str(cont)] = kind
                k = (cont, Cache.key(cont, wa[1:], wb[1:]))
                if k in rows:
                    continue
                straight = _dist(wa[1:], wb[1:])
                if straight < SHORT_YD:
                    rows[k] = {"pts": [list(wa[1:]), list(wb[1:])], "walk_yd": straight, "swim_yd": 0, "areas": [],
                               "dirs": [], "source": "straight (a few yards)"}
                elif straight > FAR_YD:
                    rows[k] = {"why": "too far to route on foot"}
                elif kind == "none":
                    rows[k] = {"why": "no walking map or terrain for this continent yet"}
                else:
                    rows[k] = cache.get(cont, wa[1:], wb[1:], stamp)
                    if rows[k] is None:
                        todo.append(((abs(d), d < 0, n), cont, wa, wb, k, stamp))
        # nearest the focus first, one continent at a time (the walking-map loader keeps one in memory)
        first = {}
        for rank, cont, *_ in sorted(todo, key=lambda t: t[0]):
            first.setdefault(cont, rank)
        todo.sort(key=lambda t: (first[t[1]], t[0]))
        done_any = False
        for _rank, cont, wa, wb, k, stamp in todo:
            if rows.get(k) is not None:
                continue
            if self.ms() >= budget and (done_any or budget <= 0):
                break
            res = self.router.walk(cont, wa[1:], wb[1:])
            cache.put(cont, wa[1:], wb[1:], stamp, res)
            rows[k] = res
            done_any = True
        out, pending = [], []
        for leg, ps in zip(legs, built):
            frm, a, to, b, wa, wb, (kind, _act), _st = leg
            segs = []
            missing = False
            for p in ps:
                if isinstance(p, tuple):
                    _m, pa, pb = p
                    row = rows.get((pa[0], Cache.key(pa[0], pa[1:], pb[1:])))
                    if row is None:
                        missing = True
                        break
                    segs.append(self.walk_segment(pa[0], pa[1:], pb[1:], row))
                else:
                    segs.append(p)
            if missing:
                pending.append([frm, a, to, b])
                continue
            mode = {"fly": "fly", "hs": "hearth", "deathskip": "death"}.get(kind, "walk")
            if any(s["mode"] == "boat" for s in segs):
                mode = "boat" if mode == "walk" else mode
            elif mode == "walk" and segs and all(s["mode"] == "straight" for s in segs):
                mode = "straight"
            yd = [s["yd"] for s in segs if s.get("yd") is not None and s["mode"] not in JUMPS]
            secs = [s["s"] for s in segs if s.get("s") is not None]
            out.append({"from": frm, "a": a, "to": to, "b": b, "mode": mode, "yd": sum(yd) if yd else None,
                        "s": sum(secs) if secs else None,
                        "approx": any(s["mode"] == "straight" or s.get("approx") for s in segs),
                        "segments": segs})
        order = {idx: k for k, idx in enumerate(wanted)}
        pending.sort(key=lambda p: (abs(order.get(p[0], 0) - focus), order.get(p[0], 0) < focus))
        return {"legs": out, "pending": pending, "nav": nav, "ms": self.ms()}


def routes(root, iid, index, req):
    """The answer's "routes" object (see the module docstring)."""
    detail = _detail(root, iid, index)
    return Routes(root, detail, req).run()


def main(argv):
    """rxp_import.py routes <id> <index>: one JSON line on stdout."""
    real_out = sys.stdout
    sys.stdout = sys.stderr                 # the knowledge layer's notes never mix into the answer
    below_normal_priority()
    try:
        try:
            req = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            req = {}
        out = {"ok": True, "routes": routes(ROOT, argv[1], int(argv[2]), req if isinstance(req, dict) else {})}
    except Exception as e:  # noqa: BLE001 - the app shows the reason, whatever it is
        out = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    finally:
        sys.stdout = real_out
    sys.stdout.write(json.dumps(out) + "\n")
    return 0 if out.get("ok") else 1
