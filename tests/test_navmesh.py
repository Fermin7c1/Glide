#!/usr/bin/env python3
"""Walking map (tools/navmesh.py -> knowledge/mechanics/nav.py NavGraph) on synthetic worlds with known answers:
walls block and low ledges are hopped, a slope over 50 degrees can't be climbed but can be gone down, a bridge over
a river is two floors (crossing on it and swimming under it), a wall with a doorway lets you through only the
doorway, the floor a player is on is followed along their movement, routes are string-pulled, and the flight
network routes and names flights. Cell by cell: no corner is cut, steep ground is left only level or down, a cell
with more patches than nodes keeps the ones joined to the most ground, and the planner's climb and swim rules and
costs hold on hand-made maps. Needs numpy.

The movement rules come from recorded play: across 6,927 sustained 8-16 yd uphill stretches of real runs the
steepest was 47.5 degrees and none passed 50, while short steeper bits were crossed by hopping (scratch analysis
for the 0.3.28 walking map; see tools/navmesh.py).
"""
import hashlib
import math
import os
import sys
import tempfile

import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
from wowfiles import navbuild as NM  # noqa: E402
from wowfiles import navformat as NF  # noqa: E402
from knowledge.mechanics import nav as NAV  # noqa: E402
from knowledge.mechanics import taxi as TX  # noqa: E402

DN, F = NM.DN, NM.F


def box(r0, r1, c0, c1, z0, z1):
    """Triangles (fine-cell space: row, col, z) of an axis-aligned box's sides and top."""
    P = lambda r, c, z: (r, c, z)  # noqa: E731
    q = []

    def quad(a, b, c, d):
        q.append([a, b, c])
        q.append([a, c, d])
    quad(P(r0, c0, z0), P(r0, c1, z0), P(r0, c1, z1), P(r0, c0, z1))
    quad(P(r1, c0, z0), P(r1, c1, z0), P(r1, c1, z1), P(r1, c0, z1))
    quad(P(r0, c0, z0), P(r1, c0, z0), P(r1, c0, z1), P(r0, c0, z1))
    quad(P(r0, c1, z0), P(r1, c1, z0), P(r1, c1, z1), P(r0, c1, z1))
    quad(P(r0, c0, z1), P(r0, c1, z1), P(r1, c1, z1), P(r1, c0, z1))
    return q


_BUILT = {}
_RULES = repr(sorted((k, v) for k, v in vars(NM).items() if k.isupper() and isinstance(v, (int, float))))


def build(tz, objects=(), water=None, tnz=None):
    """Synthetic domain -> (z, link, kind, cxy) coarse (L, CN, CN) by build_tile's own pipeline (build_domain). A
    world built before (same terrain, objects, water and builder constants) is served again, read-only."""
    tz = tz.astype(np.float32)
    if tnz is None:
        gr, gc = np.gradient(tz, F)
        tnz = 1.0 / np.sqrt(1 + gr ** 2 + gc ** 2)
    tnz = tnz.astype(np.float32)
    th = np.zeros((DN, DN), bool)
    wl = np.full((DN, DN), np.nan, np.float32) if water is None else water.astype(np.float32)
    tri = np.array([t for o in objects for t in o], np.float64).reshape(-1, 3, 3)
    key = hashlib.sha1(b"".join(a.tobytes() for a in (tz, tnz, wl, tri)) + _RULES.encode()).hexdigest()
    if key not in _BUILT:
        parts = NM.build_domain(tz, tnz, th, wl, tri)
        for a in parts:
            a.setflags(write=False)
        _BUILT[key] = parts
    return _BUILT[key]


def graph(parts):
    z, link, kind, cxy = parts
    path = os.path.join(tempfile.mkdtemp(), "nav.npz")
    sp = NM.to_sparse(z, link, kind, cxy, np.zeros(z.shape[1:], np.uint16), 30, 30)
    np.savez(path, map=np.int32(0), ty0=np.int32(30), tx0=np.int32(30), cell=np.float32(F * NM.BLOCK),
             build=np.array("test"), nav_version=np.int32(NF.NAV_VERSION), tiles=np.array([0], np.int32), **sp)
    return NAV.NavGraph(path)


def node(g, r, c, prefer=None):
    """The node at coarse cell (r, c) of the tile: world position of the cell centre."""
    wx = (32 - g.ty0) * NM.TILE - (r + 0.5) * g.cell
    wy = (32 - g.tx0) * NM.TILE - (c + 0.5) * g.cell
    return g.node_at(wx, wy, prefer=prefer)


def reachable(g, a, b):
    p, cost, _ = g.find(a, b, margin=64)
    return p is not None


A = NM.APRON
flat = np.full((DN, DN), 100.0)

# ---- a wall across the middle (3 yd high) with no gap: the two sides don't connect; with a doorway they do
row = A + 256
wall = box(row, row + 1, 0, DN, 100, 103)
g = graph(build(flat, [wall]))
north, south = node(g, 20, 32), node(g, 44, 32)
assert north and south and not reachable(g, north, south), "a 3 yd wall must block"
door = box(row, row + 1, 0, A + 200, 100, 103) + box(row, row + 1, A + 204, DN, 100, 103)   # a 4 yd doorway
g = graph(build(flat, [door]))
p, cost, _ = g.find(node(g, 20, 32), node(g, 44, 32), margin=64)
assert p is not None, "a 4 yd doorway lets you through"
wx_door = [g.world_of(n) for n in p]
# the route goes through the doorway's columns
cols = [int((32 - g.tx0) * NM.TILE - y) / F for _x, y in wx_door]
assert any(A + 192 <= c_ <= A + 212 for c_ in cols), "the route passes the doorway"

# ---- a low ledge (1.2 yd) is hopped onto; a 3 yd one isn't (but you can drop off it)
ledge = box(A + 200, A + 312, A + 200, A + 312, 100, 101.2)
g = graph(build(flat, [ledge]))
top, ground = node(g, 32, 32, prefer=101.2), node(g, 10, 10)
assert abs(g.height(top) - 101.2) < 0.6 and reachable(g, ground, top) and reachable(g, top, ground)
high = box(A + 200, A + 312, A + 200, A + 312, 100, 103.0)
g = graph(build(flat, [high]))
top, ground = node(g, 32, 32, prefer=103), node(g, 10, 10)
assert abs(g.height(top) - 103) < 0.6, g.height(top)
assert not reachable(g, ground, top), "a 3 yd ledge can't be climbed"
assert reachable(g, top, ground), "but you can drop off it"

# ---- slopes: a 40 degree hill is walked up, a 60 degree one isn't (but can be gone down)
for deg, up in ((40, True), (60, False)):
    tz = flat.copy()
    rr = np.arange(DN)[:, None] * np.ones((1, DN))
    rise = np.clip((rr - (A + 200)) * F * math.tan(math.radians(deg)), 0, 40)
    tz = 100 + rise
    g = graph(build(tz))
    low, hi = node(g, 10, 32), node(g, 60, 32)
    assert reachable(g, low, hi) == up, (deg, "up")
    assert reachable(g, hi, low), (deg, "down")

# ---- a short steep bump (1.2 yd at 70 degrees) is hopped
tz = flat.copy()
tz[A + 256:, :] = 101.2
tz[A + 256, :] = 100.6
g = graph(build(tz))
assert reachable(g, node(g, 10, 32), node(g, 50, 32)), "a short steep bump is hopped"

# ---- a bridge over a river: two floors, crossing on top, swimming underneath
tz = flat.copy()
tz[:, A + 220:A + 292] = 88.0                                  # river bed 12 yd down, 75 yd wide
water = np.full((DN, DN), np.nan)
water[:, A + 220:A + 292] = 95.0                               # 4 yd of room under the deck
banks = []
bridge = box(A + 240, A + 250, A + 210, A + 302, 99.0, 100.0)  # 10 yd wide deck at bank height
g = graph(build(tz, [bridge], water=water))
west, east = node(g, 31, 20), node(g, 31, 44)
p, cost, _ = g.find(west, east, margin=64)
assert p is not None
deck = [n for n in p if 27 <= n[2] <= 35]
assert deck and all(abs(g.height(n) - 100) < 1.0 for n in deck), [g.height(n) for n in deck]
mid = (30, 32)
layers = [l for l in range(g.L) if int(g.z[l, mid[0], mid[1]]) != NAV.NONE]
assert len(layers) >= 2, "bridge deck and water surface"
under = node(g, 10, 32, prefer=95.0)
assert int(g.kind[under]) == 2, "the river is swum"

# ---- following the floor along movement: from the water under the bridge you stay in the water
w0 = g.world_of(node(g, 29, 32, prefer=95.0))
n0 = g.node_at(*w0, prefer=95.0)
w1 = g.world_of(node(g, 31, 32, prefer=95.0))
n1 = g.track(n0, w1[0], w1[1], 17.0)
assert abs(g.height(n1) - 95.0) < 1.0, ("track keeps you under the bridge", g.height(n1))
m0 = node(g, 30, 30, prefer=100.0)                               # on the deck, walking along it over the water
assert abs(g.height(m0) - 100.0) < 1.0, g.height(m0)
e1 = g.world_of(node(g, 30, 33, prefer=100.0))
m1 = g.track(m0, e1[0], e1[1], 25.0)
assert abs(g.height(m1) - 100.0) < 1.0, ("and on the deck you stay on it", g.height(m1))


def recost(g, p):
    """A route's cost from its nodes' world_of() positions, by the planner's rules: a step into water costs 1.5x,
    onto steep ground 1.15x, to another floor of the same cell at least 1 yd."""
    t = 0.0
    for a, b in zip(p, p[1:]):
        (ax, ay), (bx, by) = g.world_of(a), g.world_of(b)
        d = math.hypot(bx - ax, by - ay)
        k = int(g.kind[b])
        t += max(1.0, d) if a[1:] == b[1:] else d * (1.5 if k == 2 else 1.15 if k == 3 else 1.0)
    return t


# ---- the planner's costs come from the same positions world_of() gives: over the deck, swum under it, down into it
for a, b in ((west, east), (node(g, 10, 32, prefer=95.0), node(g, 54, 32, prefer=95.0)), (m0, under)):
    p, cost, _ = g.find(a, b, margin=64)
    assert p is not None and abs(recost(g, p) - cost) < 1e-6, (a, b, cost, p and recost(g, p))

# ---- string-pulling: an open field gives a straight line, around the wall's end it bends once
g = graph(build(flat))
p, _c, _ = g.find(node(g, 5, 5), node(g, 58, 50), margin=64)
assert len(g.smooth(p)) == 2, "open ground: one straight leg"
half = box(row, row + 1, 0, A + 300, 100, 103)
g = graph(build(flat, [half]))
p, _c, _ = g.find(node(g, 20, 10), node(g, 44, 10), margin=64)
s = g.smooth(p)
assert 3 <= len(s) <= 5, len(s)
for i in range(1, len(s)):
    assert g.straight(s[i - 1], s[i])

# ---- straightening never swims further than the route did: around a lake, not across it
tz = flat.copy()
water = np.full((DN, DN), np.nan)
lake = np.zeros((DN, DN), bool)
lake[A + 120:A + 400, A + 150:A + 360] = True                  # 290 x 220 yd lake, the shore walked around it
tz[lake] = 90.0
water[lake] = 99.5
g = graph(build(tz, water=water))
p, _c, _ = g.find(node(g, 32, 12), node(g, 32, 52), margin=64)
s = g.smooth(p)
swum = sum(1 for n in p if int(g.kind[n]) == 2)
for i in range(1, len(s)):
    seg = [int(g.kind[n]) for n in p[p.index(s[i - 1]) + 1:p.index(s[i])]]
    assert g.straight(s[i - 1], s[i], {2: seg.count(2), 3: seg.count(3)})
pts = [g.world_of(n) for n in s]
assert not any(int(g.kind[g.node_at(*q)]) == 2 for q in pts[1:-1]) or swum, "the shortcut swims"

# ---- reach: the biggest connected floor wins when there's no height (the street, not the rooftop)
g = graph(build(flat, [high]))
r0 = g.node_at(*g.world_of(node(g, 32, 32, prefer=100)))
assert abs(g.height(r0) - 100) < 1.0 or int(g.reach[r0]) >= int(g.reach[node(g, 32, 32, prefer=103)])

# ---- walk times: a walked route already follows the terrain, so it is timed by its length at map-plane run speed,
# without the straight-line detour run_time adds for straight distances (500 yd walked at level 10: 73 s, not 91)
from knowledge.analyzer.kbdata import run_time  # noqa: E402
from knowledge.api import navigation as NV  # noqa: E402
from knowledge.mechanics import params as MP  # noqa: E402

SPEEDS = {"travel.run_speed": 7.0, "travel.plane_speed_ratio": 0.98, "travel.detour_factor": 1.0,
          "analyzer.travel.detour": 1.25, "mount.level": 40, "mount.speed_bonus": 0.6, "travel.taxi_speed": 30.0,
          "travel.taxi_overhead_s": 10.0}
FOOT = 7.0 * 0.98                                              # yd/s of map distance on foot


class FakeKB:
    """A map whose coordinates are the world's own (ui_map 1, continent 0)."""
    def to_world(self, um, x, y):
        return 0, x, y

    def to_map(self, um, wx, wy):
        return wx, wy

    def one(self, *a):
        return None


class FakeCtx:
    def __init__(self, level=10, here=None, summary=None):
        self.kb, self.level, self.summary, self.rxp_step_no, self._here = FakeKB(), level, summary or {}, 1, here

    def here(self):
        return self._here

    def p(self):
        return {"faction": "Alliance"}


saved = NAV.load_graph, NAV.load, NV._walk, TX.load, NV.get_data
try:
    with MP.scoped(SPEEDS):
        assert int(round(run_time(500, 10))) == 91, "a straight line keeps its detour (the analyzer's simulator)"
        # over the walking map, open ground: the route is the straight line itself, 500 yd
        g = graph(build(flat))
        NAV.load_graph, NAV.load = (lambda root, m: g), (lambda root, m: None)
        here, dest = (1, 600.0, 1000.0), (1, 1000.0, 700.0)    # 400 yd north, 300 yd east
        w = NV.walk_route(FakeCtx(), here, dest)
        assert w["walk_yd"] == 500 and w["straight_yd"] == 500, w
        assert w["run_s"] == int(round(500 / FOOT)) == 73, ("walked 500 yd at level 10", w["run_s"])
        w = NV.walk_route(FakeCtx(level=40), here, dest)
        assert w["run_s"] == int(round(500 / (FOOT * 1.6))) == 46, ("mounted at 40, as run_time", w["run_s"])
        # around the end of a wall: the time is the bent route's length at run speed, nothing on top
        g = graph(build(flat, [half]))
        here, dest = (1,) + g.world_of(node(g, 20, 10)), (1,) + g.world_of(node(g, 44, 10))
        pts = NV._walk(FakeCtx(), 0, here[1:], dest[1:])[0]
        walk = sum(math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]) for i in range(1, len(pts)))
        w = NV.walk_route(FakeCtx(), here, dest)
        assert w["detour_pct"] > 20 and w["run_s"] == int(round(walk / FOOT)), (w, walk)

        # "faster by flight": the straight walks to and from the flight masters get run_time's detour once, and are
        # weighed against the walked route's own time
        class FakeNet:
            nodes = {1: {"name": "North Post", "x": 0.0, "y": 100.0, "cont": 0},
                     2: {"name": "South Post", "x": 0.0, "y": 2300.0, "cont": 0}}

            def nearest(self, cont, x, y, allowed=None):
                n = min(self.nodes, key=lambda k: math.hypot(self.nodes[k]["x"] - x, self.nodes[k]["y"] - y))
                return n, math.hypot(self.nodes[n]["x"] - x, self.nodes[n]["y"] - y)

            def route(self, a, b, allowed=None):
                pa, pb = self.nodes[a], self.nodes[b]
                return {"points": [(pa["x"], pa["y"]), (pb["x"], pb["y"])], "yd": 2400.0, "hops": 1, "nodes": [a, b]}
        NAV.load_graph = lambda root, m: None
        TX.load, NV.get_data = (lambda root: FakeNet()), (lambda kb: None)
        NV._walk = lambda ctx, cont, wa, wb, track=False: ([wa, (900.0, 1200.0), wb], 0.0, [], "test")   # 3000 yd
        ctx = FakeCtx(here=(1, 0.0, 0.0), summary={"flight_paths": [1, 2], "arrow": {"map": 1, "x": 0.0, "y": 2400.0}})
        p = NV.plan_to_step(ctx)
        run_s, fly_total = int(round(3000 / FOOT)), int(round(2 * 100 * 1.25 / FOOT + 2400 / 30.0 + 10))
        assert (run_s, fly_total) == (437, 126)
        assert p["run_s"] == p["segments"][0]["s"] == run_s, p
        assert p["faster"] == {"fly_from": "North Post", "fly_to": "South Post", "total_s": fly_total,
                               "run_s": run_s, "to_flight_master_yd": 100, "from_landing_yd": 100}, p["faster"]
        q = NV.walk_to_step(ctx, p)
        assert q["run_s"] == run_s and q["faster_by_flight"] == p["faster"], q
finally:
    NAV.load_graph, NAV.load, NV._walk, TX.load, NV.get_data = saved

# ---- fine edges, cell by cell (fine_edges on hand-made surfaces): no cutting corners, hops and steep ground
zs = np.full((NM.LF, DN, DN), np.nan, np.float32)
kd = np.zeros((NM.LF, DN, DN), np.uint8)


def put(l, r0, c0, n, z, k=1, gap=()):
    """An n x n square of surfaces on layer l at height z, without the cells in gap (offsets from r0, c0)."""
    for i in range(n):
        for j in range(n):
            if (i, j) not in gap:
                zs[l, r0 + i, c0 + j], kd[l, r0 + i, c0 + j] = z, k


put(0, 20, 20, 3, 0.0)                                         # open ground
put(0, 20, 40, 2, 0.0, gap=((1, 0), (0, 1)))                   # two cells between two posts
put(0, 20, 60, 3, 0.0, gap=((1, 0),))                          # a post on one side of the diagonal
put(0, 20, 80, 3, 0.0)
put(1, 20, 80, 3, 20.0)                                        # a deck 20 yd over the ground
put(0, 20, 100, 3, 0.0)
put(1, 20, 100, 3, 20.0, gap=((1, 0),))                        # the deck with a hole beside the diagonal
for i, (k0, dz, k1) in enumerate(((1, 1.2, 1), (1, 1.6, 1), (3, 0.2, 1), (3, 1.0, 1), (3, -2.5, 1), (3, -3.5, 1),
                                  (1, -3.5, 1), (1, -8.5, 1))):
    put(0, 40, 20 + 10 * i, 1, 0.0, k0)                        # a step east from ground (1) or steep ground (3)
    put(0, 40, 21 + 10 * i, 1, dz, k1)
tgt = NM.fine_edges(zs, kd, np.zeros((DN, DN), np.float32), np.ones((DN, DN), bool))
SE, E = NF.DIRS.index((1, 1)), NF.DIRS.index((0, 1))
assert tgt[SE, 0, 20, 20] == 0, "open ground: the diagonal is walked"
assert tgt[SE, 0, 20, 40] == -1, "no squeezing between two posts"
assert tgt[SE, 0, 20, 60] == -1, "no cutting a post's corner"
assert (tgt[SE, 0, 21, 61], tgt[SE, 0, 20, 61]) == (0, 0), "but around it"
assert (tgt[SE, 0, 20, 80], tgt[SE, 1, 20, 80]) == (0, 1), "the diagonal on the ground and on the deck over it"
assert (tgt[SE, 0, 20, 100], tgt[SE, 1, 20, 100]) == (0, -1), "no cutting the deck's corner over thin air"
steps = [int(tgt[E, 0, 40, 20 + 10 * i]) for i in range(8)]
assert steps == [0, -1, 0, -1, 0, -1, 0, -1], ("hop 1.2 yd not 1.6; off steep ground only level (no hop out), "
                                               "down 2.5 yd not 3.5; off ground down 3.5 yd, not 8.5", steps)
# components join at their smallest member
assert list(NM._union(6, np.array([4, 1]), np.array([2, 5]))) == [0, 1, 2, 3, 2, 1]

# ---- reach decides which patches a crowded cell keeps: under four rooftops stacked over one cell (the cell half
# missing on the ground), the street joined to the whole map is kept, not the biggest patches
tz, tnz = np.full((DN, DN), 100.0, np.float32), np.ones((DN, DN), np.float32)
th = np.zeros((DN, DN), bool)
B0 = A + 32 * NM.BLOCK                                         # coarse cell (32, 32)
th[B0:B0 + 4, B0:B0 + 8] = True
roofs = []
b0, b1 = B0 + 0.01, B0 + 7.99                                 # just inside the cell's 8 x 8 fine cells
for zr in (110.0, 115.0, 120.0, 125.0):
    roofs += [[(b0, b0, zr), (b0, b1, zr), (b1, b1, zr)], [(b0, b0, zr), (b1, b1, zr), (b1, b0, zr)]]
roofs, wl = np.array(roofs, np.float64), np.full((DN, DN), np.nan, np.float32)
floors, solids = NM.rasterise(roofs)
zs, kd = NM.surfaces(tz, tnz, th, wl, floors, solids)
tgt = NM.fine_edges(zs, kd, tz, ~th)
pairs = NM.joins(zs, tgt, kd)
lab = NM.labels(zs, tgt, kd, pairs)
reach = NM.domain_reach(zs, tgt, kd, pairs, lab)
assert np.array_equal(reach, NM.domain_reach(zs, tgt, kd, pairs)), "joining patches = joining every surface"
cell = reach[:, B0 + 6, B0 + 3]
assert list(cell[:5]) == [DN * DN - 32] + [64] * 4, cell
kept = NM.build_domain(tz, tnz, th, wl, roofs)[0][:, 32, 32]                     # build_tile's own pipeline
assert kept[0] == 200 and NAV.NONE not in kept, ("the street and three roofs", kept)
assert 200 not in NM.condense(zs, kd, tgt, lab)[0][:, 32, 32], "without reach the biggest patches would win"

# ---- the planner's own rules on a hand-made map: a step into or out of a steep patch climbs at most a hop,
# swimming costs 1.5x (so a short walk around beats a swim), and every node has its own index
L_, CN_ = NF.LAYERS, NF.CELLS


def hand(nodes, edges, cxy=lambda n: 0x44):
    """nodes {(l, r, c): (height yd, kind)}, edges [(node, node)] both ways -> NavGraph. cxy(node): where in its
    cell the node's patch is."""
    z = np.full((L_, CN_, CN_), NAV.NONE, np.int16)
    k, lk = np.zeros((L_, CN_, CN_), np.uint8), np.zeros((L_, CN_, CN_), np.uint32)
    xy = np.zeros((L_, CN_, CN_), np.uint8)
    for n, (h, kn) in nodes.items():
        z[n], k[n], xy[n] = round(h * 2), kn, cxy(n)
    for a, b in edges:
        for u, v in ((a, b), (b, a)):
            if u[1:] == v[1:]:
                lk[u] |= 1 << (24 + v[0])
            else:
                d = NF.DIRS.index((v[1] - u[1], v[2] - u[2]))
                lk[u] |= (1 << d) | (v[0] << (8 + 2 * d))
    return graph((z, lk, k, xy))


def corridor(kinds, heights):
    ns = {(0, 10, 10 + i): (h, kn) for i, (kn, h) in enumerate(zip(kinds, heights))}
    ks = sorted(ns)
    return hand(ns, list(zip(ks, ks[1:]))), ks[0], ks[-1]


g, lo, hi = corridor((1, 3, 3, 1), (100, 101, 104, 104))
assert g.find(lo, hi)[0] is None and g.find(hi, lo)[0] is not None, "no climbing 3 yd up a steep patch"
g, lo, hi = corridor((1, 1, 1, 1), (100, 101, 104, 104))
assert g.find(lo, hi)[0] is not None, "(the climb rule is the steep ground's)"
wet = {(0, 20, c): (100, 2) for c in (11, 12, 13)}
dry = {(0, 21, c): (100, 1) for c in (11, 12, 13)}
ends = {(0, 20, 10): (100, 1), (0, 20, 14): (100, 1)}
ks = [(0, 20, c) for c in range(10, 15)]
g = hand({**wet, **dry, **ends}, list(zip(ks, ks[1:])) + [((0, 20, 10), (0, 21, 11)), ((0, 21, 11), (0, 21, 12)),
                                                            ((0, 21, 12), (0, 21, 13)), ((0, 21, 13), (0, 20, 14))])
p, cost, _ = g.find((0, 20, 10), (0, 20, 14))
assert [n[1] for n in p] == [20, 21, 21, 21, 20] and abs(recost(g, p) - cost) < 1e-6, ("walk round, not swim", p)
ns = {(0, 30, 10): (100, 1), (1, 30, 10): (110, 1), (0, 31, 11): (100, 1), (0, 31, 12): (100, 1)}
g = hand(ns, [((0, 30, 10), (1, 30, 10)), ((0, 30, 10), (0, 31, 11)), ((0, 31, 11), (0, 31, 12))], 
         cxy=lambda n: (n[0] * 5 + n[1]) % 8 << 4 | n[2] * 3 % 8)   # off the cells' centres, rows != cols
p, cost, _ = g.find((1, 30, 10), (0, 31, 12))
assert p == [(1, 30, 10), (0, 30, 10), (0, 31, 11), (0, 31, 12)] and abs(recost(g, p) - cost) < 1e-6, (p, cost)
order = sorted(ns, key=lambda n: (n[1], n[2], n[0]))
assert [g.index(n) for n in order] == list(range(len(order))), "nodes indexed cell by cell, bottom up"
assert [g.index(n) for n in ((2, 30, 10), (1, 31, 11), (0, 30, 11), (0, g.H, 0), (0, 0, g.W), (0, -1, 0))] == [-1] * 6

# ---- flights: names, multi-hop routes along the flown curves (from the client's taxi tables when extracted)
net = TX.load(ROOT)
if net is not None and net.nodes:
    sw, lk = net.named("Stormwind", 0), net.named("LAKESHIRE", 0)
    assert "Stormwind" in net.nodes[sw]["name"] and "Lakeshire" in net.nodes[lk]["name"]
    r = net.route(sw, lk)
    assert r and r["yd"] > 1000 and len(r["points"]) > 5
    ahead = net.ahead(r["points"], *r["points"][len(r["points"]) // 2])
    assert 1 < len(ahead) < len(r["points"])
    # where a flight goes, from the positions flown over so far (addons before 0.14 don't say)
    from knowledge import api
    allowed = set(net.nodes)
    pts = r["points"]
    seen = [pts[i] for i in range(0, int(len(pts) * 0.7), max(1, len(pts) // 12))]
    assert api._flight_from_track(net, sw, seen, allowed) == lk
    assert api._flight_from_track(net, sw, seen[:1], allowed) is None, "too early to tell"
# ---- rebuild policy: a missing map is built by itself; one from an older builder (NAV_VERSION) is only flagged
# for the app to suggest a rebuild; only maps older than NAV_REQUIRED (raised at development time) are rebuilt
sys.path.insert(0, os.path.join(ROOT, "companion"))
import kb_refresh  # noqa: E402
root = tempfile.mkdtemp()
os.makedirs(os.path.join(root, "data", "gamedata", "terrain"))
os.makedirs(os.path.join(root, "data", "gamedata", "nav"))
for m in (0, 1, 37):
    open(os.path.join(root, "data", "gamedata", "terrain", "%d.npz" % m), "wb").close()
saved = (NF.NAV_VERSION, NF.NAV_REQUIRED)
try:
    NF.NAV_VERSION, NF.NAV_REQUIRED = 3, 2
    np.savez(os.path.join(root, "data", "gamedata", "nav", "0.npz"), nav_version=np.int32(3))   # current
    np.savez(os.path.join(root, "data", "gamedata", "nav", "1.npz"), nav_version=np.int32(2))   # older builder
    b = kb_refresh.NavBuilder(root)
    assert b.needed() == [37], b.needed()                      # only the missing one is built by itself
    assert b.status["outdated"] == [1] and b.status["built"] == [0, 1], b.status
    NF.NAV_REQUIRED = 3                                        # a rebuild forced at development time
    assert b.needed() == [1, 37] and b.status["outdated"] == []
    # the version is read without numpy or the builder (the engine only compares it): compressed, missing, broken
    ndir = os.path.join(root, "data", "gamedata", "nav")
    np.savez_compressed(os.path.join(ndir, "2.npz"), nz=np.zeros(3, np.int16), nav_version=np.int32(7))
    np.savez_compressed(os.path.join(ndir, "3.npz"), nz=np.zeros(3, np.int16))
    open(os.path.join(ndir, "4.npz"), "wb").write(b"PK half written")
    assert [NF.read_version(os.path.join(ndir, "%d.npz" % m)) for m in (2, 3, 4, 5)] == [7, 0, None, None]
    import subprocess
    probe = ("import sys; sys.path[:0] = [%r, %r]; import kb_refresh; kb_refresh.NavBuilder(%r).needed(); "
             "print(sorted(m for m in sys.modules if m.split('.')[-1] in ('numpy', 'navbuild', 'adt', 'worldobjects')))"
             % (os.path.join(ROOT, "companion"), ROOT, root))
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True).stdout.strip()
    assert out == "[]", "NavBuilder.needed loaded " + out
finally:
    NF.NAV_VERSION, NF.NAV_REQUIRED = saved
print("navmesh ok")
