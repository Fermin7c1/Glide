"""Walking routes over the real terrain (data/gamedata/terrain/<map>.npz from tools/extract_maps.py).

The grid has one cell per 8.33 yd with ground height, steepness, water depth, liquid kind, terrain holes and the
area ID (wowfiles/terrain.py). find_path() runs A* over it:
  - a cell is walkable unless it is mostly too steep (and not a hole: holes are cave mouths, gates and building
    entrances, whose real geometry is a separate model), or magma / slime;
  - a step up may climb at most 45 degrees between cell centres; a step down may drop at most 12 yd (or 45
    degrees), so cliffs are walked off but not up;
  - water deeper than 3 yd is swum (cost x1.5, swim speed is ~2/3 of run speed);
  - extra links join cells along paths people really walk (RestedXP waypoint chains, recorded movement): they
    carry the route through tunnels, bridges and cities that the terrain alone doesn't show.
The search is limited to a window around both ends, so a route is found in well under a second.
"""
import heapq
import math
import os

from wowfiles.navformat import DIRS as NAV_DIRS
from wowfiles.navformat import LAYERS, NONE, TILE
_CACHE = {}


class Terrain:
    def __init__(self, path):
        import numpy as np
        z = np.load(path)
        self.map = int(z["map"])
        self.ty0, self.tx0 = int(z["ty0"]), int(z["tx0"])
        self.cell = float(z["cell"])
        self.h = z["h"]
        self.steep = z["steep"]
        self.water = z["water"]
        self.liq = z["liq"]
        self.hole = z["hole"]
        self.area = z["area"]
        self.land = z["land"]
        self.H, self.W = self.h.shape

    def cell_of(self, wx, wy):
        """World (X north, Y west) -> (row, col) or None outside the grid."""
        r = int(math.floor((32.0 - wx / TILE - self.ty0) * 64))
        c = int(math.floor((32.0 - wy / TILE - self.tx0) * 64))
        if 0 <= r < self.H and 0 <= c < self.W:
            return r, c
        return None

    def world_of(self, r, c):
        return ((32.0 - (self.ty0 + (r + 0.5) / 64.0)) * TILE, (32.0 - (self.tx0 + (c + 0.5) / 64.0)) * TILE)


def load(root, map_id):
    """The Terrain of a map id (cached), or None when it hasn't been extracted."""
    key = (root, int(map_id))
    if key not in _CACHE:
        p = os.path.join(root, "data", "gamedata", "terrain", "%d.npz" % int(map_id))
        try:
            _CACHE[key] = Terrain(p) if os.path.exists(p) else None
        except Exception:  # noqa: BLE001 - numpy missing or a broken file: no terrain routes
            _CACHE[key] = None
    return _CACHE[key]


def find_path(t, a, b, links=None, margin=120, max_expand=400000):
    """A* from cell a to cell b (row, col). links: {(r, c): [((r2, c2), cost_yd), ...]} extra edges.
    -> ([cells], cost_yd, expanded) or (None, None, expanded)."""
    r0 = max(0, min(a[0], b[0]) - margin)
    r1 = min(t.H, max(a[0], b[0]) + margin + 1)
    c0 = max(0, min(a[1], b[1]) - margin)
    c1 = min(t.W, max(a[1], b[1]) + margin + 1)
    W = c1 - c0
    h = t.h[r0:r1, c0:c1].astype("int32").ravel().tolist()
    st = t.steep[r0:r1, c0:c1].ravel().tolist()
    wt = t.water[r0:r1, c0:c1].ravel().tolist()
    lq = t.liq[r0:r1, c0:c1].ravel().tolist()
    ho = t.hole[r0:r1, c0:c1].ravel().tolist()
    ld = t.land[r0:r1, c0:c1].ravel().tolist()
    n = len(h)
    cell = t.cell
    diag = cell * 1.41421356

    def ok(i):
        if not ld[i] or lq[i] in (3, 4) and wt[i] > 1:
            return False
        return st[i] < 200 or ho[i] or wt[i] > 6

    lk = {}
    for (ra, ca), outs in (links or {}).items():
        if r0 <= ra < r1 and c0 <= ca < c1:
            for (rb, cb), cost in outs:
                if r0 <= rb < r1 and c0 <= cb < c1:
                    lk.setdefault((ra - r0) * W + (ca - c0), []).append(((rb - r0) * W + (cb - c0), cost))
    s = (a[0] - r0) * W + (a[1] - c0)
    g = (b[0] - r0) * W + (b[1] - c0)
    gr, gc = divmod(g, W)

    def heur(i):
        r, c = divmod(i, W)
        return math.hypot(r - gr, c - gc) * cell
    best = {s: 0.0}
    prev = {}
    pq = [(heur(s), 0.0, s)]
    expanded = 0
    nb = ((-1, 0, cell), (1, 0, cell), (0, -1, cell), (0, 1, cell), (-1, -1, diag), (-1, 1, diag), (1, -1, diag),
          (1, 1, diag))
    while pq:
        _f, d, i = heapq.heappop(pq)
        if i == g:
            break
        if d > best.get(i, 1e18):
            continue
        expanded += 1
        if expanded > max_expand:
            return None, None, expanded
        r, c = divmod(i, W)
        hi = h[i]
        for dr, dc, step in nb:
            rr, cc = r + dr, c + dc
            if rr < 0 or cc < 0 or rr * W >= n or cc >= W:
                continue
            j = rr * W + cc
            if not ok(j):
                continue
            dh = (h[j] - hi) * 0.5
            swim = wt[j] > 6 or wt[i] > 6
            if dh > step and not swim and not ho[j] and not ho[i]:
                continue                                   # too steep to walk up
            if dh < -max(step, 12.0) and not swim:
                continue                                   # a drop too far to jump
            cost = step * (1.0 + 0.8 * st[j] / 255.0) * (1.5 if wt[j] > 6 else 1.0)
            nd = d + cost
            if nd < best.get(j, 1e18):
                best[j] = nd
                prev[j] = i
                heapq.heappush(pq, (nd + heur(j), nd, j))
        for j, cost in lk.get(i, ()):
            nd = d + cost
            if nd < best.get(j, 1e18):
                best[j] = nd
                prev[j] = i
                heapq.heappush(pq, (nd + heur(j), nd, j))
    if g not in best:
        return None, None, expanded
    path = [g]
    while path[-1] != s:
        path.append(prev[path[-1]])
    path.reverse()
    return [(r0 + i // W, c0 + i % W) for i in path], best[g], expanded


def nearest_walkable(t, rc, radius=6):
    """The cell itself when walkable, else the nearest walkable one within `radius` cells (or rc)."""
    r, c = rc
    best = None
    for dr in range(-radius, radius + 1):
        for dc in range(-radius, radius + 1):
            rr, cc = r + dr, c + dc
            if 0 <= rr < t.H and 0 <= cc < t.W and t.land[rr, cc] and (t.steep[rr, cc] < 200 or t.hole[rr, cc]
                                                                       or t.water[rr, cc] > 6):
                d = dr * dr + dc * dc
                if best is None or d < best[0]:
                    best = (d, (rr, cc))
    return best[1] if best else rc


def simplify(points, tol):
    """Ramer-Douglas-Peucker on [(x, y)] with tolerance `tol` (same units)."""
    if len(points) < 3:
        return list(points)
    (x1, y1), (x2, y2) = points[0], points[-1]
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy) or 1e-9
    far, idx = -1.0, 0
    for k in range(1, len(points) - 1):
        px, py = points[k]
        dd = abs(dy * (px - x1) - dx * (py - y1)) / L
        if dd > far:
            far, idx = dd, k
    if far <= tol:
        return [points[0], points[-1]]
    return simplify(points[:idx + 1], tol)[:-1] + simplify(points[idx:], tol)


def compass(dx_east, dy_north):
    deg = (math.degrees(math.atan2(dx_east, dy_north)) + 360) % 360
    return ("north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest")[
        int((deg + 22.5) // 45) % 8]


# ================================================================================================================
# The walkable-space graph (data/gamedata/nav/<map>.npz from tools/navmesh.py; its format: wowfiles/navformat.py):
# the terrain's full mesh plus every building, cave, tunnel, bridge and doodad, with up to 4 walkable surfaces per
# 8.33 yd cell (street, rooftop, tunnel under the mountain...). Nodes are (layer, row, col); edges come from the
# build (walkable slopes up to 50 degrees, hops of 1.5 yd, drops of up to 8 yd one way, swimming).
# ================================================================================================================
CLIMB_HALF = 4               # half yards: the most a step into or out of a steep patch may climb (a 1.5 yd hop + 0.5)
_GRAPHS = {}


class _Layer:
    """g.z[(l, r, c)] and the like over the sparse node table: the value of that node, or `none` without one."""

    def __init__(self, g, arr, none):
        self.g, self.arr, self.none = g, arr, none

    def __getitem__(self, n):
        i = self.g.index(n)
        return self.none if i < 0 else self.arr[i]


class _Area:
    """g.area[r, c]: the AreaTable ID of a cell, 0 off the map."""

    def __init__(self, g):
        self.g = g

    def __getitem__(self, rc):
        r, c = rc
        g = self.g
        if r < 0 or c < 0 or r >= g.H or c >= g.W:
            return 0
        s = int(g.tidx[r >> 6, c >> 6])
        return 0 if s < 0 else int(g.tarea[s, r & 63, c & 63])


class NavGraph:
    """The walking map of one continent: a table of nodes (wowfiles/navformat.py), addressed as
    (layer, row, col)."""

    def __init__(self, path):
        import numpy as np
        with np.load(path) as z:
            self.map = int(z["map"])
            self.ty0, self.tx0 = int(z["ty0"]), int(z["tx0"])
            self.cell = float(z["cell"])
            self.build = str(z["build"])
            self.tidx, self.tcnt, self.tcum, self.tbase = z["tidx"], z["tcnt"], z["tcum"], z["tbase"]
            self.tarea = z["tarea"]
            self.nz, self.nlink, self.nkind = z["nz"], z["nlink"], z["nkind"]
            self.ncxy, self.nreach = z["ncxy"], z["nreach"]
        self.L = LAYERS
        self.H, self.W = self.tidx.shape[0] * 64, self.tidx.shape[1] * 64
        self.fine = self.cell / 8.0
        self.z = _Layer(self, self.nz, NONE)
        self.link = _Layer(self, self.nlink, 0)
        self.kind = _Layer(self, self.nkind, 0)
        self.cxy = _Layer(self, self.ncxy, 0)
        self.reach = _Layer(self, self.nreach, 0)
        self.area = _Area(self)

    def index(self, n):
        """Node (l, r, c) -> its row in the node table, or -1."""
        l, r, c = n
        if r < 0 or c < 0 or r >= self.H or c >= self.W:
            return -1
        s = int(self.tidx[r >> 6, c >> 6])
        if s < 0:
            return -1
        lr, lc = r & 63, c & 63
        if l >= int(self.tcnt[s, lr, lc]):
            return -1
        return int(self.tbase[s]) + int(self.tcum[s, lr, lc]) + l

    def nbytes(self):
        return sum(a.nbytes for a in (self.tidx, self.tcnt, self.tcum, self.tbase, self.tarea, self.nz, self.nlink,
                                      self.nkind, self.ncxy, self.nreach))

    # ---- coordinates
    def cell_of(self, wx, wy):
        r = int(math.floor((32.0 - wx / TILE - self.ty0) * 64))
        c = int(math.floor((32.0 - wy / TILE - self.tx0) * 64))
        if 0 <= r < self.H and 0 <= c < self.W:
            return r, c
        return None

    def world_of(self, n):
        """Node (l, r, c) -> world (X, Y) of its walkable patch's centre."""
        l, r, c = n
        v = int(self.cxy[l, r, c])
        fr, fc = (v >> 4) + 0.5, (v & 15) + 0.5
        return ((32.0 - self.ty0) * TILE - (r * 8 + fr) * self.fine,
                (32.0 - self.tx0) * TILE - (c * 8 + fc) * self.fine)

    def height(self, n):
        return int(self.z[n]) * 0.5

    def has(self, n):
        l, r, c = n
        return 0 <= r < self.H and 0 <= c < self.W and int(self.z[l, r, c]) != NONE

    def node_at(self, wx, wy, prefer=None, radius=4):
        """The node a player at (X, Y) most likely stands on: in that cell, the layer of the biggest connected
        region (the street, not the rooftop or the terrain buried under the city), or the one nearest `prefer`
        (a height). Searches up to `radius` cells out when the cell has none."""
        rc = self.cell_of(wx, wy)
        if rc is None:
            return None
        r0, c0 = rc
        for rad in range(radius + 1):
            best = None
            for dr in range(-rad, rad + 1):
                for dc in range(-rad, rad + 1):
                    if max(abs(dr), abs(dc)) != rad:
                        continue
                    r, c = r0 + dr, c0 + dc
                    if not (0 <= r < self.H and 0 <= c < self.W):
                        continue
                    for l in range(self.L):
                        if int(self.z[l, r, c]) == NONE:
                            continue
                        score = int(self.reach[l, r, c])
                        if prefer is not None:
                            score = -abs(self.height((l, r, c)) - prefer)
                        # ties (floors of one connected region): walkable ground before water or a slope, then
                        # the lowest (the street under the rooftops, the city inside the mountain)
                        k = int(self.kind[l, r, c])
                        key = (score, -(dr * dr + dc * dc), k == 1, -int(self.z[l, r, c]))
                        if best is None or key > best[0]:
                            best = (key, (l, r, c))
            if best:
                return best[1]
        return None

    def candidates(self, wx, wy, radius=1):
        """Every node within `radius` cells of (X, Y), nearest first."""
        rc = self.cell_of(wx, wy)
        if rc is None:
            return []
        out = []
        for dr in range(-radius, radius + 1):
            for dc in range(-radius, radius + 1):
                r, c = rc[0] + dr, rc[1] + dc
                if 0 <= r < self.H and 0 <= c < self.W:
                    for l in range(self.L):
                        if int(self.z[l, r, c]) != NONE:
                            out.append((dr * dr + dc * dc, (l, r, c)))
        out.sort()
        return [n for _d, n in out]

    def track(self, prev, wx, wy, moved_yd):
        """The node at (X, Y) given the node the player was on a moment ago: the candidate reachable from `prev`
        by the shortest walk (the floor you're on doesn't change without a way between them). The addon doesn't
        report height, so this is how the planner knows you're in Ironforge and not on the mountain over it.
        -> node, or node_at() when nothing is reachable (a teleport, a gap in the data)."""
        if prev is None or not self.has(prev):
            return self.node_at(wx, wy)
        best = None
        limit = 3.0 * moved_yd + 3 * self.cell
        hp = self.height(prev)
        for n in self.candidates(wx, wy, 1):
            if n == prev:
                return n
            path, cost, _ = self.find(prev, n, margin=max(3, int(moved_yd / self.cell) + 3), max_expand=3000)
            if path is None or cost > limit:
                continue
            # the shortest walk, and staying on the same floor: you could jump off the bridge, but rarely do
            score = cost + 4.0 * abs(self.height(n) - hp)
            if best is None or score < best[0]:
                best = (score, n)
        return best[1] if best else self.node_at(wx, wy)

    def _step(self, n, d):
        """The node one step from n in direction d, or None."""
        l, r, c = n
        m = int(self.link[l, r, c])
        if not (m >> d) & 1:
            return None
        dr, dc = NAV_DIRS[d]
        j = ((m >> (8 + 2 * d)) & 3, r + dr, c + dc)
        if not self.has(j):
            return None
        if (int(self.kind[j]) == 3 or int(self.kind[n]) == 3) and int(self.z[j]) - int(self.z[n]) > CLIMB_HALF:
            return None
        return j

    # ---- search
    def find(self, a, b, links=None, margin=160, max_expand=600000, any_floor=False):
        """A* from node a to node b -> ([nodes], cost_yd, expanded) or (None, None, expanded). links: {node: [(node,
        cost_yd)]} extra edges (recorded movement). any_floor: b's cell is the goal, whichever floor of it is
        reached first (a waypoint or an NPC has no height: in a city it's the street you reach, not a rooftop over
        it)."""
        r0 = max(0, min(a[1], b[1]) - margin)
        r1 = min(self.H, max(a[1], b[1]) + margin + 1)
        c0 = max(0, min(a[2], b[2]) - margin)
        c1 = min(self.W, max(a[2], b[2]) + margin + 1)
        wb = self.world_of(b)
        kind, link, zarr = self.kind, self.link, self.z
        cache = {}

        def pos(n):
            p = cache.get(n)
            if p is None:
                p = cache[n] = self.world_of(n)
            return p

        def heur(n):
            p = pos(n)
            return math.hypot(p[0] - wb[0], p[1] - wb[1])
        best = {a: 0.0}
        prev = {}
        pq = [(heur(a), 0.0, a)]
        expanded = 0
        links = links or {}
        goal_cell = (b[1], b[2])
        while pq:
            _f, d, n = heapq.heappop(pq)
            if n == b or (any_floor and (n[1], n[2]) == goal_cell):
                b = n
                break
            if d > best.get(n, 1e18):
                continue
            expanded += 1
            if expanded > max_expand:
                return None, None, expanded
            l, r, c = n
            m = int(link[l, r, c])
            zn, kn = int(zarr[n]), int(kind[n])
            pa = pos(n)
            if m & 0xFF:
                for dd in range(8):
                    if not (m >> dd) & 1:
                        continue
                    dr, dc = NAV_DIRS[dd]
                    rr, cc = r + dr, c + dc
                    if rr < r0 or rr >= r1 or cc < c0 or cc >= c1:
                        continue
                    j = ((m >> (8 + 2 * dd)) & 3, rr, cc)
                    zj = int(zarr[j])
                    if zj == NONE:
                        continue
                    k = int(kind[j])
                    if (k == 3 or kn == 3) and zj - zn > CLIMB_HALF:
                        continue                   # never up a slope too steep to walk, beyond a hop
                    pb = pos(j)
                    nd = d + math.hypot(pb[0] - pa[0], pb[1] - pa[1]) * (1.5 if k == 2 else 1.15 if k == 3 else 1.0)
                    if nd < best.get(j, 1e18):
                        best[j] = nd
                        prev[j] = n
                        heapq.heappush(pq, (nd + heur(j), nd, j))
            if m >> 24:
                for lj in range(4):                # steps to another floor of the same cell
                    if not (m >> (24 + lj)) & 1:
                        continue
                    j = (lj, r, c)
                    zj = int(zarr[j])
                    if zj == NONE or ((int(kind[j]) == 3 or kn == 3) and zj - zn > CLIMB_HALF):
                        continue
                    pb = pos(j)
                    nd = d + max(1.0, math.hypot(pb[0] - pa[0], pb[1] - pa[1]))
                    if nd < best.get(j, 1e18):
                        best[j] = nd
                        prev[j] = n
                        heapq.heappush(pq, (nd + heur(j), nd, j))
            for j, cost in links.get(n, ()):
                nd = d + cost
                if nd < best.get(j, 1e18):
                    best[j] = nd
                    prev[j] = n
                    heapq.heappush(pq, (nd + heur(j), nd, j))
        if b not in best:
            return None, None, expanded
        path = [b]
        while path[-1] != a:
            path.append(prev[path[-1]])
        path.reverse()
        return path, best[b], expanded

    def straight(self, a, b, limits=None):
        """Can you walk the straight line from node a to node b? Follows the line cell by cell along the graph's
        own edges, so a shortcut can't cut through a wall, up a cliff or onto another floor. limits: {kind: most
        cells of it the line may cross} (2 swim, 3 steep; default none): straightening a route along a shore
        mustn't swim across the bay."""
        la, ra, ca = a
        lb, rb, cb = b
        dr, dc = rb - ra, cb - ca
        steps = max(abs(dr), abs(dc))
        if steps == 0:
            return a == b
        left = dict(limits or {})
        n, pr, pc = a, ra, ca
        for k in range(1, steps + 1):
            tr = ra + int(round(dr * k / float(steps)))
            tc = ca + int(round(dc * k / float(steps)))
            sr, sc = tr - pr, tc - pc
            if sr == 0 and sc == 0:
                continue
            n = self._step(n, NAV_DIRS.index((sr, sc)))
            if n is None:
                return False
            kn = int(self.kind[n])
            if kn != 1 and n != b:
                left[kn] = left.get(kn, 0) - 1
                if left[kn] < 0:
                    return False
            pr, pc = tr, tc
        return n == b

    def smooth(self, path, max_look=80):
        """String-pull a cell path: keep only the turns, each shortcut checked with straight() and allowed no more
        swimming or steep ground than the stretch of route it replaces."""
        if len(path) < 3:
            return list(path)
        kinds = [int(self.kind[n]) for n in path]
        out = [path[0]]
        i = 0
        while i < len(path) - 1:
            j = min(len(path) - 1, i + max_look)
            while j > i + 1:
                seg = kinds[i + 1:j]
                if self.straight(path[i], path[j], {2: seg.count(2), 3: seg.count(3)}):
                    break
                j -= 1
            out.append(path[j])
            i = j
        return out


def load_graph(root, map_id):
    """The NavGraph of a map (cached, reloaded when the file changes), or None when tools/navmesh.py hasn't built
    it yet."""
    key = (root, int(map_id))
    p = os.path.join(root, "data", "gamedata", "nav", "%d.npz" % int(map_id))
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return None
    hit = _GRAPHS.get(key)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        g = NavGraph(p)
    except Exception:  # noqa: BLE001 - a broken or older file: the terrain grid stays in use
        g = None
    _GRAPHS.clear()                  # one continent in memory at a time (~40-70 MB each)
    _GRAPHS[key] = (mt, g)
    return g
