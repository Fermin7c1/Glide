#!/usr/bin/env python3
"""Walking map (tools/navmesh.py -> knowledge/mechanics/nav.py NavGraph) on synthetic worlds with known answers:
walls block and low ledges are hopped, a slope over 50 degrees can't be climbed but can be gone down, a bridge over
a river is two floors (crossing on it and swimming under it), a wall with a doorway lets you through only the
doorway, the floor a player is on is followed along their movement, routes are string-pulled, and the flight
network routes and names flights. Needs numpy.

The movement rules come from recorded play: across 6,927 sustained 8-16 yd uphill stretches of real runs the
steepest was 47.5 degrees and none passed 50, while short steeper bits were crossed by hopping (scratch analysis
for the 0.3.28 walking map; see tools/navmesh.py).
"""
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


def build(tz, objects=(), water=None, tnz=None):
    """Synthetic domain -> (z, link, kind, cxy) coarse (L, CN, CN) like build_tile."""
    tz = tz.astype(np.float32)
    if tnz is None:
        gr, gc = np.gradient(tz, F)
        tnz = 1.0 / np.sqrt(1 + gr ** 2 + gc ** 2)
    th = np.zeros((DN, DN), bool)
    wl = np.full((DN, DN), np.nan, np.float32) if water is None else water.astype(np.float32)
    tri = np.array([t for o in objects for t in o], np.float64).reshape(-1, 3, 3)
    floors, solids = NM.rasterise(tri)
    zs, kind = NM.surfaces(tz, tnz.astype(np.float32), th, wl, floors, solids)
    tgt = NM.fine_edges(zs, kind, tz, (tnz >= NM.COS_WALK) & ~th)
    lab = NM.labels(zs, tgt, kind)
    return NM.condense(zs, kind, tgt, lab, NM.domain_reach(zs, tgt, kind))


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
