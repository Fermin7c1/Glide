#!/usr/bin/env python3
"""Walking routes between a guide's steps (companion/rxp_routes.py, `rxp_import.py routes`) on a synthetic walking
map (built like tests/test_navmesh.py) with a temporary kb.db: a leg blocked by a wall goes around it, the second
call is served from the cache without searching, a tiny budget answers pending legs, flights, hearths, deathskips
and boats get their modes, a leg crossing two zones lands on both maps, and the walk caps reach navigation.py.
Needs numpy."""
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

try:
    import numpy as np
except ImportError:
    print("SKIP: numpy is not installed")
    sys.exit(77)

for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY"):
    os.environ[k] = ""
ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path[:0] = [ROOT, os.path.join(ROOT, "companion")]
from wowfiles import navbuild as NM  # noqa: E402
from wowfiles import navformat as NF  # noqa: E402
from knowledge import store  # noqa: E402
from knowledge.api import navigation as NV  # noqa: E402
from knowledge.mechanics import nav as NAV  # noqa: E402
from knowledge.mechanics import taxi as TX  # noqa: E402
import rxp_routes as RR  # noqa: E402

DN, F, A = NM.DN, NM.F, NM.APRON


def box(r0, r1, c0, c1, z0, z1):
    q = []

    def quad(a, b, c, d):
        q.append([a, b, c])
        q.append([a, c, d])
    quad((r0, c0, z0), (r0, c1, z0), (r0, c1, z1), (r0, c0, z1))
    quad((r1, c0, z0), (r1, c1, z0), (r1, c1, z1), (r1, c0, z1))
    quad((r0, c0, z0), (r1, c0, z0), (r1, c0, z1), (r0, c0, z1))
    quad((r0, c1, z0), (r1, c1, z0), (r1, c1, z1), (r0, c1, z1))
    quad((r0, c0, z1), (r0, c1, z1), (r1, c1, z1), (r1, c0, z1))
    return q


def build(tz, objects=()):
    tz = tz.astype(np.float32)
    gr, gc = np.gradient(tz, F)
    tnz = 1.0 / np.sqrt(1 + gr ** 2 + gc ** 2)
    th = np.zeros((DN, DN), bool)
    wl = np.full((DN, DN), np.nan, np.float32)
    tri = np.array([t for o in objects for t in o], np.float64).reshape(-1, 3, 3)
    return NM.build_domain(tz, tnz.astype(np.float32), th, wl, tri)


def save_graph(parts, path):
    z, link, kind, cxy = parts
    sp = NM.to_sparse(z, link, kind, cxy, np.zeros(z.shape[1:], np.uint16), 30, 30)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.savez(path, map=np.int32(0), ty0=np.int32(30), tx0=np.int32(30), cell=np.float32(F * NM.BLOCK),
             build=np.array("test"), nav_version=np.int32(NF.NAV_VERSION), tiles=np.array([0], np.int32), **sp)


def built_once(path, make):
    """The walking map `make` saves to `path`, built once per change of what builds it (the walking-map builder, this
    test, numpy) and kept in the temp folder between runs: the same file a build writes, without the 4 s build."""
    import hashlib
    h = hashlib.sha1(np.__version__.encode())
    for f in ("navbuild.py", "navformat.py", "adt.py", "worldobjects.py"):
        with open(os.path.join(ROOT, "wowfiles", f), "rb") as fh:
            h.update(fh.read())
    with open(os.path.abspath(__file__), "rb") as fh:
        h.update(fh.read())
    cache = os.path.join(tempfile.gettempdir(), "glide-test-cache", "rxp_routes-%s.npz" % h.hexdigest()[:20])
    if not os.path.exists(cache):
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        part = "%s.%d.tmp.npz" % (cache[:-4], os.getpid())
        make(part)
        try:
            os.replace(part, cache)
        except OSError:                    # Windows: another run built it first and is copying it right now
            os.remove(part)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    shutil.copyfile(cache, path)


home = tempfile.mkdtemp()
try:
    # ---- the world: one tile on continent 0 with a wall across most of it (open at the east end) ----------------
    row = A + 256
    built_once(os.path.join(home, "data", "gamedata", "nav", "0.npz"),
               lambda p: save_graph(build(np.full((DN, DN), 100.0), [box(row, row + 1, 0, A + 300, 100, 103)]), p))
    g = NAV.load_graph(home, 0)
    assert g is not None
    cell = g.cell
    T = NF.TILE

    def W(r, c):
        """World (X, Y) of coarse cell (r, c)'s centre."""
        return (2 * T - (r + 0.5) * cell, 2 * T - (c + 0.5) * cell)
    wy_mid = 2 * T - 32 * cell             # two zones side by side: west half 1429, east half 1433
    B = {1429: (0, 2 * T, 2 * T, T, wy_mid), 1433: (0, 2 * T, wy_mid, T, T),
         1415: (0, 2 * T + 200, 2 * T + 200, T - 200, T - 200),             # its continent map, a bit larger
         1438: (1, 5000.0, 5000.0, 4000.0, 4000.0)}                         # another continent (no walking map)
    kbp = os.path.join(home, "data", "kb.db")
    conn = sqlite3.connect(kbp)
    store.create_schema(conn)
    for um, (cont, wx0, wy0, wx1, wy1) in B.items():
        conn.execute("INSERT INTO map_bounds(ui_map, continent, wx0, wy0, wx1, wy1, src, conf) VALUES (?,?,?,?,?,?,"
                     "'db2',1)", (um, cont, wx0, wy0, wx1, wy1))
    gy = W(50, 50)
    conn.execute("INSERT INTO poi(kind, id, name, faction, ui_map, x, y, continent, wx, wy) VALUES "
                 "('graveyard', 7, 'Test Graveyard', NULL, ?, ?, ?, 0, ?, ?)",
                 (1433,) + store.world_to_map(dict(zip(("wx0", "wy0", "wx1", "wy1"), B[1433][1:])), *gy) + gy)
    conn.commit()
    conn.close()
    kb = store.KB(kbp)

    def P(um, r, c):
        x, y = kb.to_map(um, *W(r, c))
        return {"ui_map": um, "x": round(x, 3), "y": round(y, 3), "mode": "arrive"}

    def step(idx, wps=(), verbs=()):
        return {"idx": idx, "waypoints": list(wps), "actions": [dict(v) for v in verbs], "ui_map": None, "x": None}
    steps = [
        step(1, [P(1429, 20, 10)]),
        step(2, [P(1429, 44, 10)]),                                    # 1 -> 2: through the wall, so around it
        step(3, [P(1429, 10, 5), P(1433, 10, 58)]),                    # inside: from one zone into the other
        step(4, [P(1429, 12, 6)], [{"verb": "fly", "location": "South Post"}]),
        step(5, [P(1433, 56, 58)]),                                    # 4 -> 5: walk, fly, walk
        step(6, [P(1433, 56, 50)], [{"verb": "home", "location": "Test Inn"}]),
        step(7, [P(1429, 6, 6)]),
        step(8, [], [{"verb": "hs", "location": "Test Inn"}]),         # 7 -> 9: a hearth to step 6's inn
        step(9, [P(1433, 54, 50)], [{"verb": "deathskip"}]),           # 9 -> 10: die, the graveyard, walk
        step(10, [P(1433, 46, 46)]),
        step(11, [{"ui_map": 1438, "x": 50.0, "y": 50.0, "mode": "wp"}]),   # 10 -> 11: another continent
        step(12, [{"ui_map": 1429, "x": 30.0, "y": 30.0, "mode": "dynamic"}]),   # no stop: not part of the route
    ]
    detail = {"faction": "Alliance", "level_min": 10, "zones": [{"ui_map": 1429}, {"ui_map": 1433}], "steps": steps}

    class FakeNet:
        nodes = {1: {"id": 1, "name": "North Post", "cont": 0, "x": W(12, 8)[0], "y": W(12, 8)[1]},
                 2: {"id": 2, "name": "South Post", "cont": 0, "x": W(54, 56)[0], "y": W(54, 56)[1]}}

        def nearest(self, cont, wx, wy, allowed=None):
            n = min(self.nodes, key=lambda k: math.hypot(self.nodes[k]["x"] - wx, self.nodes[k]["y"] - wy))
            return n, math.hypot(self.nodes[n]["x"] - wx, self.nodes[n]["y"] - wy)

        def named(self, name, continent=None, allowed=None, near=None):
            return next((k for k, n in self.nodes.items() if n["name"].lower().startswith(name.lower())), None)

        def route(self, a, b, allowed=None):
            pa, pb = self.nodes[a], self.nodes[b]
            mid = ((pa["x"] + pb["x"]) / 2 + 40, (pa["y"] + pb["y"]) / 2)
            pts = [(pa["x"], pa["y"]), mid, (pb["x"], pb["y"])]
            return {"nodes": [a, b], "points": pts, "yd": RR._length(pts), "hops": 1}
    TX.load = lambda root: FakeNet()

    finds = []
    real_find = NAV.NavGraph.find

    def counting_find(self, a, b, links=None, margin=160, max_expand=600000, any_floor=False):
        finds.append(max_expand)
        return real_find(self, a, b, links, margin, max_expand, any_floor)
    NAV.NavGraph.find = counting_find
    RR._detail = lambda root, iid, index: detail
    ALL = [s["idx"] for s in steps]

    def ask(**req):
        return RR.routes(home, "free", 0, dict({"steps": ALL, "focus": 1, "budget_ms": 60000}, **req))

    # ---- a tiny budget: only what needs no search is answered, the rest is pending, nearest the focus first ----
    r = ask(budget_ms=0)
    assert not finds, "budget 0 searches nothing"
    assert r["pending"] and r["pending"][0][:3] == [1, 0, 2], r["pending"]
    assert r["nav"] == {"0": "graph"}, r["nav"]
    boat = [leg for leg in r["legs"] if leg["mode"] == "boat"]
    assert len(boat) == 1 and (boat[0]["from"], boat[0]["to"]) == (10, 11), boat
    assert set(boat[0]["segments"][-1]["maps"]) >= {"1438", "1433"}, boat[0]["segments"][-1]["maps"]

    # ---- a small budget works out at least one leg (the focus's) and leaves others pending ----
    r = ask(budget_ms=1, focus=2)
    assert 1 <= len(finds) and r["pending"], (finds, r["pending"])
    finds.clear()

    # ---- a full budget: every leg ----
    r = ask()
    assert not r["pending"], r["pending"]
    legs = {(leg["from"], leg["a"], leg["to"], leg["b"]): leg for leg in r["legs"]}
    assert all(m <= 150000 for m in finds), finds                    # each search capped
    walled = legs[(1, 0, 2, 0)]
    seg = walled["segments"][0]
    assert walled["mode"] == "walk" and seg["mode"] == "walk" and seg["source"].startswith("walkable-space"), walled
    assert seg["yd"] > seg["straight_yd"] * 1.2, ("around the wall", seg)
    from knowledge.analyzer.kbdata import run_time
    assert seg["s"] == int(round(run_time(seg["yd"], 10, walked=True))), "timed as the coach times walked paths"
    pts = seg["maps"]["1429"]
    assert abs(pts[0][0] - steps[0]["waypoints"][0]["x"]) < 0.01, "starts at the pin"
    assert len(pts) >= 3 and max(p[0] for p in pts) > 60, ("the bend goes east, round the wall's end", pts)
    assert "1415" in seg["maps"], "and on the continent map"

    cross = legs[(3, 0, 3, 1)]
    assert set(cross["segments"][0]["maps"]) >= {"1429", "1433"}, "a leg crossing two zones lands on both maps"

    fly = legs[(4, 0, 5, 0)]
    assert fly["mode"] == "fly", fly
    modes = [s["mode"] for s in fly["segments"]]
    assert modes == ["walk", "fly", "walk"], modes
    f = fly["segments"][1]
    assert (f["from"], f["to"], f["hops"]) == ("North Post", "South Post", 1) and f["s"] > 0, f

    hs = legs[(7, 0, 9, 0)]
    assert hs["mode"] == "hearth" and hs["segments"][0]["mode"] == "hearth", hs
    inn = kb.to_map(1433, *W(56, 50))
    end = hs["segments"][0]["maps"]["1433"][-1]
    assert abs(end[0] - inn[0]) < 0.05 and abs(end[1] - inn[1]) < 0.05, ("lands at the last .home", end, inn)
    assert hs["segments"][1]["mode"] == "walk", "then walks from the inn"

    death = legs[(9, 0, 10, 0)]
    assert death["mode"] == "death" and death["segments"][0]["to"] == "Test Graveyard", death
    assert legs[(10, 0, 11, 0)]["mode"] == "boat"
    assert not any(12 in (leg["from"], leg["to"]) for leg in r["legs"]), "a dynamic goto is not a stop"

    # ---- the second call is served from the cache: no search at all ----
    finds.clear()
    r2 = ask(budget_ms=0)
    assert not finds and not r2["pending"], (finds, r2["pending"])
    assert json.dumps(r2["legs"], sort_keys=True) == json.dumps(r["legs"], sort_keys=True)

    # ---- never reused reversed: B -> A is its own search ----
    rev = ask(steps=[2, 1], focus=2)
    assert finds and not rev["pending"], "2 -> 1 searched, not taken from 1 -> 2"
    finds.clear()

    # ---- a rebuilt walking map (new file time) is worked out again ----
    p = os.path.join(home, "data", "gamedata", "nav", "0.npz")
    os.utime(p, (os.path.getmtime(p) + 10, os.path.getmtime(p) + 10))
    ask(steps=[1, 2], budget_ms=60000)
    assert finds, "a changed walking map invalidates the cached legs"
    c = sqlite3.connect(os.path.join(home, RR.CACHE))
    stamps = {s for (s,) in c.execute("SELECT DISTINCT stamp FROM leg WHERE cont=0")}
    c.close()
    assert len(stamps) == 1, ("stale rows deleted", stamps)
    finds.clear()

    # ---- the caps reach the searches; None keeps the coach's defaults ----
    NV.walk_world(kb, 0, W(20, 10), W(44, 10), max_expand=12345, root=home)
    NV.walk_world(kb, 0, W(20, 10), W(44, 10), root=home)
    assert finds == [12345, 600000], finds
    finds.clear()
    assert RR.max_expand(0) == 30000 and RR.max_expand(5000) == 150000

    # ---- without a knowledge base: nothing to place ----
    bare = tempfile.mkdtemp()
    try:
        r = RR.routes(bare, "free", 0, {"steps": ALL})
        assert r["legs"] == [] and "kb.db" in r["unavailable"], r
    finally:
        shutil.rmtree(bare, ignore_errors=True)
finally:
    shutil.rmtree(home, ignore_errors=True)

# ---- the command line: rxp_import.py routes answers one JSON line, an unknown guide as an error ----
out = subprocess.run([sys.executable, os.path.join(ROOT, "companion", "rxp_import.py"), "routes", "nope", "0"],
                     input="{}", capture_output=True, text=True, timeout=60)
ans = json.loads(out.stdout.strip().splitlines()[-1])
assert ans["ok"] is False and ans["error"], ans
print("rxp routes ok")
