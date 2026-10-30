"""The way somewhere: walking routes over the real terrain (knowledge/mechanics/nav.py; data/gamedata/terrain
from extract_maps, data/gamedata/nav from tools/navmesh.py) and the way to the current step (walk, fly, walk)."""
import math
import threading

from knowledge import store
from knowledge.api._layer import MT, NAV, TX, get_data, run_time
from knowledge.api.values import as_int, as_num, clean


_NAV_LINKS = {}
_NAV_LOCK = threading.Lock()


def _world(ctx, um, x, y):
    cont, wx, wy = ctx.kb.to_world(um, x, y)
    return (cont, wx, wy) if wx is not None else (None, None, None)


def _nav_links(ctx, t):
    """Extra walkable links on map t: consecutive RestedXP waypoints and recorded movement segments up to
    150 yd apart (tunnels, bridges, cities and stairs the terrain grid doesn't show). Cached per KB and map."""
    key = (id(ctx.kb), t.map)
    with _NAV_LOCK:
        if key in _NAV_LINKS:
            return _NAV_LINKS[key]
    bounds = {}

    def cell(um, x, y):
        if um not in bounds:
            bounds[um] = ctx.kb.bounds(um)
        b = bounds[um]
        if not b or b.get("continent") != t.map or x is None or y is None:
            return None, None
        wx, wy = store.map_to_world(b, x, y)
        return t.cell_of(wx, wy), (wx, wy)
    links = {}

    def add(a, b, wa, wb, factor):
        if a is None or b is None or a == b:
            return
        dist = math.hypot(wa[0] - wb[0], wa[1] - wb[1])
        if dist > 150:
            return
        links.setdefault(a, []).append((b, dist * factor))
        links.setdefault(b, []).append((a, dist * factor))
    prev = None
    for r in ctx.kb.q("SELECT guide_key, idx, line, args FROM rxp_action WHERE verb='goto' ORDER BY guide_key, idx, line"):
        a = store.jload(r.get("args"), {}) or {}
        um, x, y = as_int(a.get("ui_map")), as_num(a.get("x")), as_num(a.get("y"))
        cur = (r["guide_key"],) + cell(um, x, y) if um else None
        if cur and prev and cur[0] == prev[0] and cur[1] is not None and prev[1] is not None:
            add(prev[1], cur[1], prev[2], cur[2], 1.15)
        prev = cur
    for r in ctx.kb.q("SELECT ui_map, x1, y1, x2, y2 FROM travel_edge"):
        ca, wa = cell(r["ui_map"], r["x1"], r["y1"])
        cb, wb = cell(r["ui_map"], r["x2"], r["y2"])
        if ca is not None and cb is not None:
            add(ca, cb, wa, wb, 1.05)
    with _NAV_LOCK:
        _NAV_LINKS[key] = links
    return links


def _walk_grid(ctx, t, wa, wb):
    """The old 8.3 yd terrain grid (until tools/navmesh.py has built the walkable-space graph) -> (world points,
    swim yd, area ids along the way, source) or None."""
    a, b = NAV.nearest_walkable(t, t.cell_of(*wa)), NAV.nearest_walkable(t, t.cell_of(*wb))
    straight = math.hypot(wa[0] - wb[0], wa[1] - wb[1])
    margin = int(min(300, max(60, straight / t.cell * 0.6)))
    path, _cost, _ex = NAV.find_path(t, a, b, _nav_links(ctx, t), margin=margin)
    if path is None:
        return None
    pts = [t.world_of(r, c) for r, c in path]
    swim = sum(math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]) for i in range(1, len(pts))
               if t.water[path[i]] > 6)
    return pts, swim, [int(t.area[rc]) for rc in path], "terrain grid"


_TRACK = {}          # kb id -> (node, (X, Y)): the node the player was last seen on (which floor: NavGraph.track)


def _walk_nav(ctx, g, wa, wb, track=False):
    """Over the walkable-space graph -> (world points, swim yd, area ids, source) or None."""
    a = None
    if track:
        h = ctx.summary.get("height")
        last = _TRACK.get(id(ctx.kb))
        if h is not None:
            a = g.node_at(wa[0], wa[1], prefer=h)
        elif last and g.has(last[0]):
            a = g.track(last[0], wa[0], wa[1], math.hypot(wa[0] - last[1][0], wa[1] - last[1][1]))
    a = a or g.node_at(*wa)
    b = g.node_at(*wb)
    if a is None or b is None:
        return None
    if track:
        _TRACK[id(ctx.kb)] = (a, wa)
    straight = math.hypot(wa[0] - wb[0], wa[1] - wb[1])
    margin = int(min(320, max(40, straight / g.cell * 0.7)))
    path, _cost, _ex = g.find(a, b, margin=margin, any_floor=True)
    if path is None:
        return None
    nodes = g.smooth(path)
    pts = [wa] + [g.world_of(n) for n in nodes[1:-1]] + [wb]
    swim = 0.0
    for i in range(1, len(path)):
        if int(g.kind[path[i]]) == 2:
            p, q = g.world_of(path[i]), g.world_of(path[i - 1])
            swim += math.hypot(p[0] - q[0], p[1] - q[1])
    src = "walkable-space graph (terrain mesh, buildings, caves, bridges, doodads)"
    return pts, swim, [int(g.area[n[1], n[2]]) for n in path], src


def _walk(ctx, cont, wa, wb, track=False):
    """Walking route between two world points of one continent -> (points, swim_yd, area ids, source) or a reason."""
    g = NAV.load_graph(store.ROOT, cont)
    if g is not None:
        r = _walk_nav(ctx, g, wa, wb, track)
        if r is not None:
            return r
    t = NAV.load(store.ROOT, cont)
    if t is None:
        return "no terrain for this continent yet"
    if t.cell_of(*wa) is None or t.cell_of(*wb) is None:
        return "a point is outside the extracted terrain"
    return _walk_grid(ctx, t, wa, wb) or "no walkable route found"


def _area_names(ctx, ids):
    names, out = {}, []
    for aid in ids:
        if aid not in names:
            row = ctx.kb.one("SELECT name FROM area WHERE area_id=?", aid) if aid else None
            names[aid] = (row or {}).get("name")
        nm = names[aid]
        if nm and (not out or out[-1] != nm):
            out.append(nm)
    return out


def _legs(ctx, um, pts, areas_at, max_legs):
    walk = sum(math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]) for i in range(1, len(pts)))
    tol = max(12.0, walk / 40.0)
    simple = NAV.simplify(pts, tol)
    while len(simple) - 1 > max_legs:
        tol *= 1.5
        simple = NAV.simplify(pts, tol)
    legs = []
    for i in range(1, len(simple)):
        (x1, y1), (x2, y2) = simple[i - 1], simple[i]
        mx, my = ctx.kb.to_map(um, x2, y2)
        aid = areas_at(x2, y2)
        legs.append(clean({"go": NAV.compass(-(y2 - y1), x2 - x1), "yd": int(round(math.hypot(x2 - x1, y2 - y1))),
                            "to": (_area_names(ctx, [aid]) or [None])[0] if aid else None,
                            "coords": "%.1f,%.1f" % (mx, my) if mx is not None and 0 <= mx <= 100 and 0 <= my <= 100
                            else None}))
    return legs


def _to_map(ctx, um, pts):
    out = []
    for wx, wy in pts:
        mx, my = ctx.kb.to_map(um, wx, wy)
        if mx is not None:
            out.append([round(mx, 2), round(my, 2)])
    return out


def walk_route(ctx, here, dest, max_legs=7, track=False):
    """Walking route between two (ui_map, x, y) points of one continent -> dict, or {"unavailable": why}.
    track: `here` is the player (keeps following which floor they're on)."""
    ca, wxa, wya = _world(ctx, *here)
    cb, wxb, wyb = _world(ctx, *dest)
    if wxa is None or wxb is None:
        return {"unavailable": "no map bounds for one end"}
    if ca != cb:
        return {"unavailable": "the target is on another continent"}
    straight = math.hypot(wxa - wxb, wya - wyb)
    r = _walk(ctx, ca, (wxa, wya), (wxb, wyb), track)
    if isinstance(r, str):
        return {"unavailable": r, "straight_yd": int(round(straight))}
    pts, swim, areas, source = r
    walk = sum(math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]) for i in range(1, len(pts)))
    um = here[0]
    g = NAV.load_graph(store.ROOT, ca)
    t = NAV.load(store.ROOT, ca) if g is None else None

    def area_at(x, y):
        grid = g or t
        rc = grid.cell_of(x, y) if grid is not None else None
        return int(grid.area[rc]) if rc else None
    return clean({"points": _to_map(ctx, um, pts), "walk_yd": int(round(walk)),
                   "straight_yd": int(round(straight)),
                   "detour_pct": int(round((walk / straight - 1) * 100)) if straight > 1 else 0,
                   "run_s": int(round(run_time(walk, ctx.level or 1))), "swim_yd": int(round(swim)) or None,
                   "through": _area_names(ctx, areas)[:10], "legs": _legs(ctx, um, pts, area_at, max_legs),
                   "source": source})


# ---- the way to the current step: walk, fly, walk ----------------------------------------------------------------
_TAKEOFF = {}        # kb id -> world (X, Y) where the current flight started


def _faction_nodes(ctx):
    d = get_data(ctx.kb)
    fac = ctx.p().get("faction")
    return {r["id"] for r in d.flight_nodes(fac)} if d is not None else None


def _arrow(ctx):
    """(ui_map, x, y) of the place the step sends you to: RestedXP's arrow (addon 0.13.1), else the step's first
    goto, else the knowledge base's position for the step."""
    s = ctx.summary
    a = s.get("arrow")
    if a and a.get("x") is not None:
        return (a.get("map") or s.get("map"), a["x"], a["y"])
    st = (s.get("steps") or [None])[0] or {}
    for e in st.get("elements") or ():
        if e.get("tag") == "goto" and e.get("x") is not None:
            return (e.get("map") or s.get("map"), e["x"], e["y"])
    route = ctx.route
    if route is not None and ctx.idx is not None:
        stp = route.by_idx.get(ctx.idx) or {}
        return stp.get("pos") or route.next_pos(ctx.idx)
    return None


def _fly_target(ctx):
    """The current step's flight destination from the guide itself, when the addon doesn't send it (before 0.14):
    the step is tagged fly and the guide's .fly line names the place."""
    st = (ctx.summary.get("steps") or [None])[0] or {}
    if not any(e.get("tag") == "fly" for e in st.get("elements") or ()) or ctx.guide_key is None or ctx.idx is None:
        return None
    row = ctx.kb.one("SELECT args FROM rxp_action WHERE guide_key=? AND idx=? AND verb='fly' ORDER BY line LIMIT 1",
                     ctx.guide_key, ctx.idx)
    a = store.jload((row or {}).get("args"), {}) or {}
    return a.get("location") or a.get("raw")


def _dist_to_line(x, y, pts):
    best = 1e18
    for i in range(1, len(pts)):
        (ax, ay), (bx, by) = pts[i - 1], pts[i]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        u = 0.0 if L2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / L2))
        best = min(best, math.hypot(x - ax - u * dx, y - ay - u * dy))
    return best


def flight_from_track(net, a, seen, allowed):
    """Where a flight from node a goes, from where you've flown so far (an addon before 0.14 doesn't say): the
    destination whose route as flown passes by every position you've flown over; once the routes out of a flight
    master part, only one fits."""
    if len(seen) < 3:
        return None
    fits = []
    for b in net.nodes:
        if b == a or (allowed is not None and b not in allowed) or net.nodes[b]["cont"] != net.nodes[a]["cont"]:
            continue
        r = net.route(a, b, allowed)
        if r is None:
            continue
        pts = r["points"]
        # the flight you're on passes by every point you've flown over: its worst miss (to the line, not to its
        # points, which are ~150 yd apart)
        err = max(_dist_to_line(x, y, pts) for x, y in seen)
        fits.append((err, r["yd"], b, r["nodes"]))
    fits.sort()
    if not fits or fits[0][0] > 80:
        return None
    # flights that share their first stretch fit equally well until they part: when every close fit flies on
    # through the nearest of them, that's the next landing (it moves on if you keep flying); otherwise say nothing
    # until one stands out
    close = [f for f in fits if f[0] < max(80.0, 2 * fits[0][0])]
    first = min(close, key=lambda f: f[1])
    if all(f[3][:len(first[3])] == first[3] for f in close):
        return first[2]
    return None


def _fly_segment(ctx, net, um, a, b):
    r = net.route(a, b, allowed=_faction_nodes(ctx))
    if r is None:
        return None
    d = get_data(ctx.kb)
    t = None
    if d is not None and r["hops"] == 1:
        t, _how = d.flight_time(ctx.p().get("faction"), a, b)
    pts = r["points"]
    return {"mode": "fly", "from": net.nodes[a]["name"], "to": net.nodes[b]["name"], "hops": r["hops"],
            "points": _to_map(ctx, um, pts), "yd": int(round(r["yd"])),
            "s": int(round(t if t else MT.taxi_time_s(r["yd"]))), "world": pts}


def plan_to_step(ctx):
    """The way to the current step for the map and the coach -> {"step", "map", "segments": [{"mode": "walk"|"fly",
    "points" (map %), "yd", "s", ...}], "walk_yd", "run_s", "legs", "through", "faster"} or None.
    On a flight: the rest of it. A step that says fly: walk to the flight master, then the flight as flown. Else:
    walk to RestedXP's arrow; "faster" names a known flight that beats running by a wide margin."""
    s = ctx.summary
    here = ctx.here()
    if here is None:
        return None
    um = here[0]
    cont, wx, wy = _world(ctx, *here)
    if wx is None:
        return None
    net = TX.load(store.ROOT)
    kid = id(ctx.kb)
    out = {"step": ctx.rxp_step_no, "map": um, "segments": []}
    known = set(s.get("flight_paths") or ()) or None
    # ---- on a flight: what's left of it
    if s.get("on_taxi"):
        fl = _TAKEOFF.setdefault(kid, {"start": (wx, wy), "seen": []})
        fl["seen"] = (fl["seen"] + [(wx, wy)])[-12:]
        if net is not None:
            allowed = _faction_nodes(ctx)
            a, _d = net.nearest(cont, fl["start"][0], fl["start"][1], allowed)
            b = net.named(s.get("taxi_to") or s.get("fly_to") or _fly_target(ctx), cont, near=(wx, wy))
            if b is None and a is not None:
                b = flight_from_track(net, a, fl["seen"], allowed)
            seg = _fly_segment(ctx, net, um, a, b) if a and b and a != b else None
            if seg:
                ahead = net.ahead(seg.pop("world"), wx, wy)
                seg["points"] = _to_map(ctx, um, ahead)
                seg["yd"] = int(round(sum(math.hypot(ahead[i][0] - ahead[i - 1][0], ahead[i][1] - ahead[i - 1][1])
                                          for i in range(1, len(ahead)))))
                seg["s"] = int(round(seg["yd"] / TX.FLY_SPEED))
                out["segments"].append(seg)
        return out
    _TAKEOFF.pop(kid, None)
    # ---- a flight step: walk to the flight master, then the flight
    fly_to = s.get("fly_to") or _fly_target(ctx)
    if fly_to and net is not None:
        allowed = _faction_nodes(ctx)
        dest_node = net.named(fly_to, cont, allowed=allowed, near=(wx, wy))
        arrow = _arrow(ctx)
        ac, ax, ay = _world(ctx, *arrow) if arrow else (None, None, None)
        origin = None
        if ax is not None and ac == cont:
            origin, dist = net.nearest(cont, ax, ay, allowed)
            if dist is not None and dist > 80:
                origin = None
        if origin is None:
            origin, _dist = net.nearest(cont, wx, wy, known or allowed)
        if origin and dest_node and origin != dest_node:
            o = net.nodes[origin]
            ox, oy = ctx.kb.to_map(um, o["x"], o["y"])
            w = walk_route(ctx, here, (um, ox, oy), track=True) if ox is not None else {}
            if w.get("walk_yd") is not None:
                out["segments"].append({"mode": "walk", "points": w["points"], "yd": w["walk_yd"], "s": w["run_s"],
                                        "to": o["name"]})
                out.update({"walk_yd": w["walk_yd"], "run_s": w.get("run_s"), "through": w.get("through"),
                            "legs": w.get("legs")})
            seg = _fly_segment(ctx, net, um, origin, dest_node)
            if seg:
                seg.pop("world", None)
                out["segments"].append(seg)
            return out
    # ---- walk to the arrow
    dest = _arrow(ctx)
    if not dest:
        return out if out["segments"] else None
    w = walk_route(ctx, here, dest, track=True)
    if w.get("walk_yd") is None:
        out["unavailable"] = w.get("unavailable")
        return out
    out["segments"].append({"mode": "walk", "points": w["points"], "yd": w["walk_yd"], "s": w["run_s"]})
    out.update({"walk_yd": w["walk_yd"], "straight_yd": w["straight_yd"], "run_s": w.get("run_s"),
                "swim_yd": w.get("swim_yd"), "through": w.get("through"), "legs": w.get("legs"),
                "source": w.get("source")})
    # a flight you know that beats the run by a wide margin
    if net is not None and known and w["walk_yd"] > 1200:
        dc, dx, dy = _world(ctx, *dest)
        a, da = net.nearest(cont, wx, wy, known)
        b, db = net.nearest(cont, dx, dy, known) if dc == cont else (None, None)
        if a and b and a != b:
            seg = _fly_segment(ctx, net, um, a, b)
            if seg:
                lv = ctx.level or 1
                total = run_time(da * 1.25, lv) + seg["s"] + run_time(db * 1.25, lv)
                if total < 0.7 * w["run_s"] and w["run_s"] - total > 60:
                    out["faster"] = {"fly_from": seg["from"], "fly_to": seg["to"], "total_s": int(round(total)),
                                     "run_s": w["run_s"], "to_flight_master_yd": int(round(da)),
                                     "from_landing_yd": int(round(db))}
    return out


def walk_to_step(ctx, plan=None):
    """Compact form of plan_to_step for the question context: {"step", "walk_yd", "run_s", "legs": "NE 430 > N
    245 > ...", "through", "fly", "faster_by_flight"} or None."""
    try:
        p = plan if plan is not None else plan_to_step(ctx)
        if not p or not p.get("segments"):
            return None
        short = {"north": "N", "northeast": "NE", "east": "E", "southeast": "SE", "south": "S", "southwest": "SW",
                 "west": "W", "northwest": "NW"}
        fly = [sg for sg in p["segments"] if sg["mode"] == "fly"]
        return clean({"step": p.get("step"), "walk_yd": p.get("walk_yd"), "straight_yd": p.get("straight_yd"),
                       "run_s": p.get("run_s"), "swim_yd": p.get("swim_yd"), "through": p.get("through"),
                       "legs": " > ".join("%s %d%s" % (short.get(l["go"], l["go"]), l["yd"],
                                                       " to " + l["to"] if l.get("to") else "")
                                          for l in p.get("legs") or ()) or None,
                       "fly": {"from": fly[0]["from"], "to": fly[0]["to"], "s": fly[0]["s"], "hops": fly[0]["hops"]}
                       if fly else None, "faster_by_flight": p.get("faster")})
    except Exception:  # noqa: BLE001 - an extra; never break a question on it
        return None
