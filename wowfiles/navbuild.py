"""The walkable-space builder of tools/navmesh.py: one tile's terrain and objects rasterised onto a layered fine
grid, its standable surfaces linked by the movement rules, condensed into coarse nodes; and finished tiles packed
into the node table a map file holds (the format is in wowfiles/navformat.py)."""
import math

import numpy as np

from .adt import CHUNK, TILE, parse_terrain
from .navformat import BLOCK, CELLS, DIRS, LAYERS, NONE
from .worldobjects import world_triangles

UNIT = CHUNK / 8.0            # terrain quad, 4.167 yd
SUB = 4                       # fine cells per quad
FN = 128 * SUB                # fine cells per tile side (512)
F = UNIT / SUB                # fine cell, 1.042 yd
CN = FN // BLOCK              # coarse cells per tile side (64)
assert CN == CELLS
APRON = BLOCK                 # fine cells borrowed from each neighbouring tile
DN = FN + 2 * APRON           # fine cells per side of the domain one tile is built on
L = LAYERS                    # nodes (walkable patches) per coarse cell
LF = 6                        # surfaces per fine cell (a tunnel under rock shells under a mountain: keep them all)

MAX_SLOPE = 50.0
COS_WALK = math.cos(math.radians(MAX_SLOPE))
TAN_WALK = math.tan(math.radians(MAX_SLOPE))
JUMP = 1.5
SAFE_DROP = 8.0
HEIGHT = 2.0
CLEAR = 0.5                   # solids lower than this above the feet are stepped over
MERGE = 1.0                   # surfaces closer than this are one
SWIM_DEPTH = 1.6
MIN_PATCH = 3                 # fine cells: smaller patches are noise (a doodad's top, a ledge)



def terrain_fine(t):
    """Terrain sampled at the fine cell centres of one tile -> z (FN,FN), nz (FN,FN) |normal z| of the triangle
    under each centre (the quad's 4-triangle fan around its centre vertex)."""
    outer, inner = t["outer"], t["inner"]
    k = (np.arange(FN) + 0.5) / SUB
    q = np.minimum(k.astype(int), 127)
    f = k - q
    qr, qc = np.meshgrid(q, q, indexing="ij")
    v, u = np.meshgrid(f, f, indexing="ij")
    A, B = outer[qr, qc], outer[qr, qc + 1]
    C, D = outer[qr + 1, qc], outer[qr + 1, qc + 1]
    M = inner[qr, qc]
    north = (v <= u) & (v <= 1 - u)
    east = (u >= v) & (u >= 1 - v) & ~north
    south = (v >= u) & (v >= 1 - u) & ~north & ~east
    sel = [north, east, south]
    p1u = np.select(sel, [0.0, 1.0, 1.0], 0.0)
    p1v = np.select(sel, [0.0, 0.0, 1.0], 1.0)
    p2u = np.select(sel, [1.0, 1.0, 0.0], 0.0)
    p2v = np.select(sel, [0.0, 1.0, 1.0], 0.0)
    h1 = np.select(sel, [A, B, D], C)
    h2 = np.select(sel, [B, D, C], A)
    ax, ay, az = (p1u - 0.5) * UNIT, (p1v - 0.5) * UNIT, h1 - M
    bx, by, bz = (p2u - 0.5) * UNIT, (p2v - 0.5) * UNIT, h2 - M
    nx, ny, nz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
    ln = np.sqrt(nx * nx + ny * ny + nz * nz)
    ln = np.where(ln > 0, ln, 1)
    du, dv = (u - 0.5) * UNIT, (v - 0.5) * UNIT
    with np.errstate(divide="ignore", invalid="ignore"):
        z = M - (nx * du + ny * dv) / np.where(nz != 0, nz, 1e-9)
    return z.astype(np.float32), (np.abs(nz) / ln).astype(np.float32)




def _split_big(tri, max_edge):
    """Split triangles (k,3,3) until no edge is longer than max_edge (yards): tight z ranges for walls."""
    out = []
    for _ in range(12):
        e = np.stack([np.linalg.norm(tri[:, 1] - tri[:, 0], axis=1), np.linalg.norm(tri[:, 2] - tri[:, 1], axis=1),
                      np.linalg.norm(tri[:, 0] - tri[:, 2], axis=1)], 1)
        big = e.max(1) > max_edge
        out.append(tri[~big])
        tri = tri[big]
        if not len(tri):
            break
        k = e[big].argmax(1)                       # split the longest edge at its midpoint
        a = tri[np.arange(len(tri)), k]
        b = tri[np.arange(len(tri)), (k + 1) % 3]
        c = tri[np.arange(len(tri)), (k + 2) % 3]
        m = (a + b) / 2
        tri = np.concatenate([np.stack([a, m, c], 1), np.stack([m, b, c], 1)])
    out.append(tri)
    return np.concatenate(out)


def _pairs(r0, r1, c0, c1, n):
    """Cells (i, row, col) of every triangle's bounding box (rows r0..r1, cols c0..c1 inclusive, clipped to 0..n-1)."""
    r0 = np.clip(r0, 0, n - 1); r1 = np.clip(r1, 0, n - 1)
    c0 = np.clip(c0, 0, n - 1); c1 = np.clip(c1, 0, n - 1)
    h, w = r1 - r0 + 1, c1 - c0 + 1
    cnt = np.maximum(h, 0) * np.maximum(w, 0)
    idx = np.repeat(np.arange(len(cnt)), cnt)
    if not len(idx):
        return idx, idx, idx
    start = np.repeat(np.cumsum(cnt) - cnt, cnt)
    k = np.arange(len(idx)) - start
    ww = w[idx]
    return idx, r0[idx] + k // ww, c0[idx] + k % ww


def _overlap(T, r, c, rad=0.7072):
    """Does triangle T (n,3,2: row, col) come within `rad` cells of the centre of cell (r, c)? With half the cell's
    diagonal that's every cell the triangle touches (and a few more at its corners). Slivers with no area (a vertical
    wall seen from above) are tested as their longest edge."""
    pr, pc = r + 0.5, c + 0.5
    A, B, C = T[:, 0], T[:, 1], T[:, 2]
    area = (B[:, 0] - A[:, 0]) * (C[:, 1] - A[:, 1]) - (C[:, 0] - A[:, 0]) * (B[:, 1] - A[:, 1])
    sign = np.where(area >= 0, 1.0, -1.0)
    ok = np.ones(len(T), bool)
    for P, Q in ((A, B), (B, C), (C, A)):
        er, ec = Q[:, 0] - P[:, 0], Q[:, 1] - P[:, 1]
        ln = np.hypot(er, ec)
        ln = np.where(ln > 1e-9, ln, 1e-9)
        # signed distance of the centre outside this edge (positive = outside)
        d = -sign * (er * (pc - P[:, 1]) - ec * (pr - P[:, 0])) / ln
        ok &= d <= rad
    flat = np.abs(area) < 1e-6
    if flat.any():
        i = np.nonzero(flat)[0]
        best = np.zeros(len(i), bool)
        for P, Q in ((A, B), (B, C), (C, A)):
            P_, Q_ = P[i], Q[i]
            dr, dc = Q_[:, 0] - P_[:, 0], Q_[:, 1] - P_[:, 1]
            L2 = np.maximum(dr * dr + dc * dc, 1e-12)
            s = np.clip(((pr[i] - P_[:, 0]) * dr + (pc[i] - P_[:, 1]) * dc) / L2, 0, 1)
            best |= np.hypot(pr[i] - P_[:, 0] - s * dr, pc[i] - P_[:, 1] - s * dc) <= rad
        ok[i] = best
    return ok


def rasterise(tri_rc):
    """Triangles in fine-cell space (k,3,3: row, col, z) of one domain ->
    floors: (cells, z) arrays for walkable triangles over cell centres,
    solids: (cells, zlo, zhi) for every triangle overlapping a cell (conservative), walls included."""
    if not len(tri_rc):
        e = np.zeros(0, np.int64)
        return (e, np.zeros(0, np.float32)), (e, np.zeros(0, np.float32), np.zeros(0, np.float32))
    P = tri_rc.astype(np.float64)
    u = P[:, 1] - P[:, 0]
    v = P[:, 2] - P[:, 0]
    # normals in yards (rows/cols are F yards)
    ux, uy, uz = u[:, 0] * F, u[:, 1] * F, u[:, 2]
    vx, vy, vz = v[:, 0] * F, v[:, 1] * F, v[:, 2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    ln = np.sqrt(nx * nx + ny * ny + nz * nz)
    ok = ln > 1e-9
    P, nz, ln = P[ok], nz[ok], ln[ok]
    walk = np.abs(nz) / ln >= COS_WALK
    # ---- floors: every cell a walkable triangle touches (not only cells whose centre it covers: the rim of a
    # platform or a ramp's edge would be left with no surface at all), at the plane's height over the cell centre
    # clamped to the triangle's own height range
    W = P[walk]
    rr, cc = W[:, :, 0], W[:, :, 1]
    i, r, c = _pairs(np.floor(rr.min(1)).astype(int), np.floor(rr.max(1)).astype(int),
                     np.floor(cc.min(1)).astype(int), np.floor(cc.max(1)).astype(int), DN)
    fz = np.zeros(0)
    fcell = np.zeros(0, np.int64)
    if len(i):
        pr, pc = r + 0.5, c + 0.5
        a, b, cpt = W[i, 0], W[i, 1], W[i, 2]
        d = (b[:, 0] - a[:, 0]) * (cpt[:, 1] - a[:, 1]) - (cpt[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
        d = np.where(np.abs(d) < 1e-12, 1e-12, d)
        l1 = ((b[:, 0] - pr) * (cpt[:, 1] - pc) - (cpt[:, 0] - pr) * (b[:, 1] - pc)) / d
        l2 = ((cpt[:, 0] - pr) * (a[:, 1] - pc) - (a[:, 0] - pr) * (cpt[:, 1] - pc)) / d
        l3 = 1 - l1 - l2
        zc = l1 * a[:, 2] + l2 * b[:, 2] + l3 * cpt[:, 2]
        tz = W[:, :, 2]
        zc = np.clip(zc, tz.min(1)[i], tz.max(1)[i])
        inside = _overlap(W[i][:, :, :2], r, c)
        fz = zc[inside]
        fcell = (r * DN + c)[inside]
    # ---- solids: every triangle, split small, over every cell its footprint touches
    S = _split_big(P, 3.0)
    rr, cc, zz = S[:, :, 0], S[:, :, 1], S[:, :, 2]
    i, r, c = _pairs(np.floor(rr.min(1)).astype(int), np.floor(rr.max(1)).astype(int),
                     np.floor(cc.min(1)).astype(int), np.floor(cc.max(1)).astype(int), DN)
    scell = np.zeros(0, np.int64)
    zlo = zhi = np.zeros(0)
    if len(i):
        keep = _overlap(S[i][:, :, :2], r, c)
        scell = (r * DN + c)[keep]
        zlo = zz.min(1)[i][keep]
        zhi = zz.max(1)[i][keep]
    return (fcell, fz.astype(np.float32)), (scell, zlo.astype(np.float32), zhi.astype(np.float32))


# ================================================================================================ one tile
def surfaces(tz, tnz, thole, wl, floors, solids):
    """Standable surfaces of every fine cell of the domain -> zs (LF, DN, DN) float32 (nan = none),
    kind (LF, DN, DN) uint8 (1 walk, 2 swim), and the terrain's z / walkable for steep marching."""
    n = DN * DN
    tz_f, tnz_f, th_f, wl_f = tz.ravel(), tnz.ravel(), thole.ravel(), wl.ravel()
    have_t = np.isfinite(tz_f) & ~th_f
    fcell, fz = floors
    scell, zlo, zhi = solids
    # candidates: terrain (walkable or not, it's solid either way), object floors, water surfaces
    deep = have_t & np.isfinite(wl_f) & (wl_f - tz_f > SWIM_DEPTH)
    cand_cell = np.concatenate([np.nonzero(have_t)[0], fcell, np.nonzero(deep)[0]])
    cand_z = np.concatenate([tz_f[have_t], fz, wl_f[deep]])
    cand_kind = np.concatenate([np.where(tnz_f[have_t] >= COS_WALK, 1, 3), np.ones(len(fz), np.uint8),
                                np.full(int(deep.sum()), 2)]).astype(np.uint8)
    # a terrain candidate under deep water isn't standable (it's the lake bed)
    tb = np.zeros(n, bool)
    tb[np.nonzero(deep)[0]] = True
    cand_kind[:int(have_t.sum())][tb[have_t]] = 0
    # headroom: the nearest solid above each candidate, from object triangles, other floors and the terrain
    #   solid intervals per cell: object triangles (zlo..zhi) + terrain as a thin sheet (not under holes)
    s_cell = np.concatenate([scell, np.nonzero(have_t)[0]])
    s_lo = np.concatenate([zlo, tz_f[have_t]])
    s_hi = np.concatenate([zhi, tz_f[have_t]])
    order = np.argsort(s_cell, kind="stable")
    s_cell, s_lo, s_hi = s_cell[order], s_lo[order], s_hi[order]
    start = np.searchsorted(s_cell, cand_cell, "left")
    end = np.searchsorted(s_cell, cand_cell, "right")
    cnt = end - start
    ci = np.repeat(np.arange(len(cand_cell)), cnt)
    si = np.repeat(start, cnt) + (np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt))

    def check(cz):
        """-> (blocked by something taller than a hop, top of the low obstacles in the way or -inf)"""
        z = cz[ci]
        hit = (s_hi[si] > z + CLEAR) & (s_lo[si] < z + HEIGHT)
        tall = hit & (s_hi[si] > z + JUMP)
        blocked = np.zeros(len(cz), bool)
        np.logical_or.at(blocked, ci[tall], True)
        top = np.full(len(cz), -np.inf)
        np.maximum.at(top, ci[hit & ~tall], s_hi[si][hit & ~tall])
        return blocked, top
    blocked, top = check(cand_z)
    # a low obstacle (a stone, a root, a curb, the rim of a platform): you hop onto it, so the surface is its top,
    # if there's room to stand up there
    low = ~blocked & np.isfinite(top)
    if low.any():
        raised = np.where(low, top, cand_z)
        b2, t2 = check(raised)
        cand_z = np.where(low, raised, cand_z)
        blocked = np.where(low, b2 | np.isfinite(t2), blocked)
        cand_kind = np.where(low & (cand_kind == 3), 1, cand_kind).astype(np.uint8)
    stand = (cand_kind > 0) & ~blocked
    cell = cand_cell[stand]
    sz = cand_z[stand]
    sk = cand_kind[stand]
    # per cell: sort by z, merge close ones (keep the higher), keep the LF highest
    order = np.lexsort((sz, cell))
    cell, sz, sk = cell[order], sz[order], sk[order]
    zs = np.full((LF, n), np.nan, np.float32)
    kind = np.zeros((LF, n), np.uint8)
    if len(cell):
        newc = np.ones(len(cell), bool)
        newc[1:] = cell[1:] != cell[:-1]
        # merge: a surface within MERGE above the previous one in the same cell replaces it
        nxt_same = np.zeros(len(cell), bool)
        nxt_same[:-1] = (cell[:-1] == cell[1:]) & (sz[1:] - sz[:-1] < MERGE)
        keep = ~nxt_same
        cell, sz, sk = cell[keep], sz[keep], sk[keep]
        newc = np.ones(len(cell), bool)
        newc[1:] = cell[1:] != cell[:-1]
        grp = np.cumsum(newc) - 1
        first = np.nonzero(newc)[0]
        rank = np.arange(len(cell)) - first[grp]
        # keep the top LF (highest) per cell: rank from the top
        cnt = np.bincount(grp)
        rtop = cnt[grp] - 1 - rank
        sel = rtop < LF
        lay = (LF - 1 - rtop[sel])                       # layers sorted bottom->top within the kept ones
        # renumber so the lowest kept is layer 0
        kept_cnt = np.minimum(cnt, LF)[grp[sel]]
        lay = lay - (LF - kept_cnt)
        zs[lay, cell[sel]] = sz[sel]
        kind[lay, cell[sel]] = sk[sel]
    return zs.reshape(LF, DN, DN), kind.reshape(LF, DN, DN)


def fine_edges(zs, kind, tz, twalk):
    """Directed edges between the fine surfaces -> tgt (8, LF, DN, DN) int8: the target layer in the neighbour cell
    for direction d, -1 = none; jumps over short steep terrain and drops off ledges included."""
    tgt = np.full((8, LF, DN, DN), -1, np.int8)
    has = np.isfinite(zs)
    for d, (dr, dc) in enumerate(DIRS):
        dist = F * (1.4142 if dr and dc else 1.0)
        up = dist * TAN_WALK + 0.25
        nb = np.full((LF, DN, DN), np.nan, np.float32)
        nk = np.zeros((LF, DN, DN), np.uint8)
        rs = slice(max(0, -dr), DN - max(0, dr))
        cs = slice(max(0, -dc), DN - max(0, dc))
        rt = slice(max(0, dr), DN - max(0, -dr))
        ct = slice(max(0, dc), DN - max(0, -dc))
        nb[:, rs, cs] = zs[:, rt, ct]
        nk[:, rs, cs] = kind[:, rt, ct]
        best = np.full((LF, DN, DN), -1, np.int8)
        bestz = np.full((LF, DN, DN), -np.inf, np.float32)
        for la in range(LF):
            za = zs[la]
            ka = kind[la]
            for lb in range(LF):
                zb = nb[lb]
                dz = zb - za
                kb = nk[lb]
                # walk: up a walkable slope or a hop of JUMP; onto steep ground only by a hop; from steep ground
                # (sliding) or water only level or down, or a hop out of the water; down at most SAFE_DROP, and
                # off steep ground no steeper than 70 degrees a step (beyond that it's a fall)
                lim_up = np.where(ka == 3, 0.25, np.where((ka == 2) | (kb == 3) | (kb == 2), JUMP,
                                                          max(up, JUMP)))
                lim_dn = np.where(ka == 3, -min(SAFE_DROP, dist * 2.75), -SAFE_DROP)
                ok = has[la] & np.isfinite(zb) & (dz <= lim_up) & (dz >= lim_dn)
                # out of water you can only climb a little; into water from any safe height
                take = ok & (zb > bestz[la])
                best[la] = np.where(take, lb, best[la])
                bestz[la] = np.where(take, zb, bestz[la])
            tgt[d, la] = best[la]
        if dr and dc:
            # no corner cutting: both side cells must have a surface the move could pass at
            for la in range(LF):
                za = zs[la]
                side_ok = np.zeros((DN, DN), bool)
                for sr, sc in ((dr, 0), (0, dc)):
                    s = np.full((LF, DN, DN), np.nan, np.float32)
                    r1 = slice(max(0, -sr), DN - max(0, sr)); c1 = slice(max(0, -sc), DN - max(0, sc))
                    r2 = slice(max(0, sr), DN - max(0, -sr)); c2 = slice(max(0, sc), DN - max(0, -sc))
                    s[:, r1, c1] = zs[:, r2, c2]
                    near = np.any(np.isfinite(s) & (np.abs(s - za[None]) <= JUMP + 0.5), axis=0)
                    side_ok = side_ok | near if sr == dr and sc == 0 else side_ok & near
                tgt[d, la] = np.where(side_ok, tgt[d, la], -1)
    return tgt


def joins(zs, tgt, kind):
    """The pairs of fine surfaces that belong together -> (s, t) flat indices into (LF, DN, DN): an edge that goes
    both ways (so a bridge and the river under it, linked only by the drop, stay apart), or between two steep
    surfaces either way (a slope is only ever gone down, so it would otherwise fall apart into single cells; the
    route planner never lets a step out of or into a steep patch climb more than a hop)."""
    n = LF * DN * DN
    kf = kind.ravel()
    rr, cc = np.meshgrid(np.arange(DN), np.arange(DN), indexing="ij")
    src_all, dst_all = [], []
    for d, (dr, dc) in enumerate(DIRS):
        r2, c2 = rr + dr, cc + dc
        inside = (r2 >= 0) & (r2 < DN) & (c2 >= 0) & (c2 < DN)
        for la in range(LF):
            lb = tgt[d, la]
            ok = inside & (lb >= 0)
            if not ok.any():
                continue
            src_all.append(la * DN * DN + rr[ok] * DN + cc[ok])
            dst_all.append(lb[ok].astype(np.int64) * DN * DN + r2[ok] * DN + c2[ok])
    if not src_all:
        e = np.zeros(0, np.int64)
        return e, e
    s = np.concatenate(src_all)
    t = np.concatenate(dst_all)
    key = np.sort(s * n + t)
    back = t * n + s
    pos = np.minimum(np.searchsorted(key, back), len(key) - 1)
    mutual = key[pos] == back
    keep = (mutual & (s < t)) | (~mutual & (kf[s] == 3) & (kf[t] == 3))    # a two-way pair once
    return s[keep], t[keep]


def _union(n, s, t):
    """Connected components of n items joined by pairs (s, t) -> the smallest index of each item's component.
    Each round hooks every pair's larger root under the smaller one, then flattens the trees: about log(n) rounds."""
    parent = np.arange(n, dtype=np.int64)
    for _ in range(64):
        ps, pt = parent[s], parent[t]
        hi, lo = np.maximum(ps, pt), np.minimum(ps, pt)
        diff = hi != lo
        if not diff.any():
            break
        np.minimum.at(parent, hi[diff], lo[diff])
        for _k in range(64):
            pp = parent[parent]
            if np.array_equal(pp, parent):
                break
            parent = pp
    return parent


def labels(zs, tgt, kind, pairs=None):
    """Connected patches inside each BLOCK x BLOCK cell (joins() inside one block) -> lab (LF, DN, DN) int64,
    -1 = none."""
    s, t = pairs if pairs is not None else joins(zs, tgt, kind)
    rs, cs = (s % (DN * DN)) // DN, s % DN
    rt, ct = (t % (DN * DN)) // DN, t % DN
    same = (rs // BLOCK == rt // BLOCK) & (cs // BLOCK == ct // BLOCK)
    lab = _union(LF * DN * DN, s[same], t[same])
    return np.where(np.isfinite(zs).ravel(), lab, -1).reshape(LF, DN, DN)


def domain_reach(zs, tgt, kind, pairs=None):
    """Size of the connected walkable area (joins() across the whole domain) of every fine surface ->
    (LF, DN, DN) int64, 0 = none."""
    n = LF * DN * DN
    has = np.isfinite(zs).ravel()
    s, t = pairs if pairs is not None else joins(zs, tgt, kind)
    lab = _union(n, s, t)
    size = np.bincount(lab[has], minlength=n)
    out = np.zeros(n, np.int64)
    out[has] = size[lab[has]]
    return out.reshape(LF, DN, DN)


def condense(zs, kind, tgt, lab, comp=None):
    """Fine patches -> coarse nodes for the core CN x CN cells of the domain. Returns z, link, kind, cxy arrays of
    shape (L, CN, CN). comp: the size of the connected walkable area each fine surface is part of (domain_reach):
    when a cell has more patches than L, the ones joined to the most ground are kept (a tunnel's floor, not the
    pockets inside the rock around it)."""
    CD = DN // BLOCK                                   # coarse cells in the domain (with the apron ring)
    has = lab >= 0
    la_i, r_i, c_i = np.nonzero(has)
    lv = lab[la_i, r_i, c_i]
    br, bc = r_i // BLOCK, c_i // BLOCK
    # patch statistics
    uniq, inv, size = np.unique(lv, return_inverse=True, return_counts=True)
    zsum = np.bincount(inv, zs[la_i, r_i, c_i].astype(np.float64))
    rsum = np.bincount(inv, (r_i % BLOCK).astype(np.float64))
    csum = np.bincount(inv, (c_i % BLOCK).astype(np.float64))
    swim = np.bincount(inv, (kind[la_i, r_i, c_i] == 2).astype(np.float64))
    pblock = np.zeros(len(uniq), np.int64)
    pblock[inv] = br * CD + bc
    pz = zsum / size
    reachsz = np.zeros(len(uniq), np.int64)
    if comp is not None:
        np.maximum.at(reachsz, inv, comp[la_i, r_i, c_i].astype(np.int64))
    # per block: keep the L patches of at least MIN_PATCH cells joined to the most ground (then the biggest),
    # layers ordered by height
    order = np.lexsort((-size, -reachsz, pblock))
    pb_sorted = pblock[order]
    first = np.ones(len(order), bool)
    first[1:] = pb_sorted[1:] != pb_sorted[:-1]
    grp = np.cumsum(first) - 1
    rank = np.arange(len(order)) - np.nonzero(first)[0][grp]
    keep = (rank < L) & (size[order] >= MIN_PATCH)
    kept = order[keep]
    # layer index by height within the block
    kb = pblock[kept]
    o2 = np.lexsort((pz[kept], kb))
    kept = kept[o2]
    kb = pblock[kept]
    f2 = np.ones(len(kept), bool)
    f2[1:] = kb[1:] != kb[:-1]
    g2 = np.cumsum(f2) - 1
    layer_of = np.full(len(uniq), -1, np.int8)
    layer_of[kept] = (np.arange(len(kept)) - np.nonzero(f2)[0][g2]).astype(np.int8)
    # coarse arrays over the domain
    cz = np.full((L, CD, CD), NONE, np.int16)
    ck = np.zeros((L, CD, CD), np.uint8)
    cxy = np.zeros((L, CD, CD), np.uint8)
    kl = layer_of[kept].astype(int)
    kr, kc = kb // CD, kb % CD
    cz[kl, kr, kc] = np.clip(np.round(pz[kept] * 2), -32000, 32000).astype(np.int16)
    steep = np.bincount(inv, (kind[la_i, r_i, c_i] == 3).astype(np.float64))
    ck[kl, kr, kc] = np.where(swim[kept] / size[kept] > 0.5, 2, np.where(steep[kept] / size[kept] > 0.5, 3, 1))
    cxy[kl, kr, kc] = (np.clip(np.round(csum[kept] / size[kept]), 0, 7).astype(np.uint8) |
                       (np.clip(np.round(rsum[kept] / size[kept]), 0, 7).astype(np.uint8) << 4))
    # fine node -> coarse layer
    node_layer = np.full((LF, DN, DN), -1, np.int8)
    node_layer[la_i, r_i, c_i] = layer_of[inv]
    # coarse edges from fine edges that cross into a neighbouring block (counted per target layer)
    link = np.zeros((L, CD, CD), np.uint32)
    within = np.zeros((L, CD, CD), np.uint32)
    votes = np.zeros((L, CD, CD, 8, L), np.int32)
    for d, (dr, dc) in enumerate(DIRS):
        rs = slice(max(0, -dr), DN - max(0, dr)); cs = slice(max(0, -dc), DN - max(0, dc))
        rt = slice(max(0, dr), DN - max(0, -dr)); ct = slice(max(0, dc), DN - max(0, -dc))
        rr = np.arange(DN)[rs][:, None]; cc = np.arange(DN)[cs][None, :]
        for la in range(LF):
            lb = tgt[d, la, rs, cs]
            src = node_layer[la, rs, cs]
            okb = (lb >= 0) & (src >= 0)
            lbc = np.where(lb >= 0, lb, 0)
            dst = node_layer[lbc, np.arange(DN)[rt][:, None], np.arange(DN)[ct][None, :]]
            ok = okb & (dst >= 0)
            sbr, sbc = rr // BLOCK, cc // BLOCK
            tbr, tbc = (rr + dr) // BLOCK, (cc + dc) // BLOCK
            # a step between two patches of the same cell (the rim of a plateau down onto the slope below it)
            same = ok & (sbr == tbr) & (sbc == tbc) & (src != dst)
            if same.any():
                ii, jj = np.nonzero(same)
                np.bitwise_or.at(within, (src[ii, jj].astype(int), rr[ii, 0] // BLOCK, cc[0, jj] // BLOCK),
                                 (np.uint32(1) << (24 + dst[ii, jj].astype(np.uint32))))
            cross = ok & ((sbr != tbr) | (sbc != tbc))
            if not cross.any():
                continue
            ii, jj = np.nonzero(cross)
            R, C = rr[ii, 0], cc[0, jj]
            s_r, s_c = R // BLOCK, C // BLOCK
            t_r, t_c = (R + dr) // BLOCK, (C + dc) // BLOCK
            ddr, ddc = t_r - s_r, t_c - s_c
            cd = np.array([DIRS.index((int(a), int(b))) for a, b in zip(ddr, ddc)], np.int64) if len(ddr) else ddr
            np.add.at(votes, (src[ii, jj].astype(int), s_r, s_c, cd, dst[ii, jj].astype(int)), 1)
    best = votes.argmax(4)
    anyv = votes.max(4) > 0
    for d in range(8):
        link |= (anyv[:, :, :, d].astype(np.uint32) << d) | (best[:, :, :, d].astype(np.uint32) << (8 + 2 * d))
    link |= within
    core = slice(1, CD - 1)
    return cz[:, core, core], link[:, core, core], ck[:, core, core], cxy[:, core, core]


class TileSource:
    """Terrain of a map's tiles (cached) and its object placements."""

    def __init__(self, casc, tiles, models, placements):
        self.c, self.tiles, self.models, self.placements = casc, tiles, models, placements
        self.cache = {}

    def terrain(self, ty, tx):
        key = (ty, tx)
        if key not in self.cache:
            if len(self.cache) > 24:
                self.cache.pop(next(iter(self.cache)))
            f = self.tiles.get(key)
            t = None
            if f:
                try:
                    t = parse_terrain(self.c.read_fdid(f[0]))
                    t["z"], t["nz"] = terrain_fine(t)
                except Exception:  # noqa: BLE001 - encrypted or broken tile
                    t = None
            self.cache[key] = t
        return self.cache[key]


def build_tile(src, ty, tx):
    """One tile -> dict(z, link, kind, cxy) coarse arrays (L, CN, CN) and area (CN, CN), or None."""
    me = src.terrain(ty, tx)
    if me is None:
        return None
    tz = np.full((DN, DN), np.nan, np.float32)
    tnz = np.zeros((DN, DN), np.float32)
    th = np.zeros((DN, DN), bool)
    wl = np.full((DN, DN), np.nan, np.float32)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            t = me if (dy, dx) == (0, 0) else src.terrain(ty + dy, tx + dx)
            if t is None:
                continue
            r0 = APRON + dy * FN
            c0 = APRON + dx * FN
            ra, rb = max(0, r0), min(DN, r0 + FN)
            ca, cb = max(0, c0), min(DN, c0 + FN)
            if ra >= rb or ca >= cb:
                continue
            sr = slice(ra - r0, rb - r0)
            sc = slice(ca - c0, cb - c0)
            tz[ra:rb, ca:cb] = t["z"][sr, sc]
            tnz[ra:rb, ca:cb] = t["nz"][sr, sc]
            th[ra:rb, ca:cb] = np.repeat(np.repeat(t["hole"], SUB, 0), SUB, 1)[sr, sc]
            w = np.where(t["liq"] >= 3, np.nan, t["wlevel"])            # magma and slime aren't swum
            wl[ra:rb, ca:cb] = np.repeat(np.repeat(w, SUB, 0), SUB, 1)[sr, sc]
            if (dy, dx) != (0, 0):
                continue
    # magma / slime: no surface at all
    bad = np.zeros((DN, DN), bool)
    lq = np.repeat(np.repeat(me["liq"], SUB, 0), SUB, 1)
    bad[APRON:APRON + FN, APRON:APRON + FN] = (lq >= 3) & np.isfinite(np.repeat(np.repeat(me["wlevel"], SUB, 0),
                                                                                 SUB, 1))
    tz = np.where(bad, np.nan, tz)
    # objects over the domain
    top_x = (32 - ty) * TILE + APRON * F
    left_y = (32 - tx) * TILE + APRON * F
    box = (top_x - DN * F, top_x, left_y - DN * F, left_y)
    tri = world_triangles(src.models, src.placements, box)
    rc = np.empty_like(tri)
    rc[:, :, 0] = (top_x - tri[:, :, 0]) / F
    rc[:, :, 1] = (left_y - tri[:, :, 1]) / F
    rc[:, :, 2] = tri[:, :, 2]
    floors, solids = rasterise(rc)
    zs, kind = surfaces(tz, tnz, th, wl, floors, solids)
    twalk = (tnz >= COS_WALK) & ~th
    tgt = fine_edges(zs, kind, tz, twalk)
    pairs = joins(zs, tgt, kind)
    lab = labels(zs, tgt, kind, pairs)
    comp = domain_reach(zs, tgt, kind, pairs)
    z, link, k, cxy = condense(zs, kind, tgt, lab, comp)
    area = np.repeat(np.repeat(me["area"], 4, 0), 4, 1)
    return {"z": z, "link": link, "kind": k, "cxy": cxy, "area": area, "objects": len(tri)}


def pack(tiles, ty0, tx0, TH, TW):
    """Coarse arrays per tile {(ty, tx): {"z", "link", "kind", "cxy" (L, CN, CN), "area" (CN, CN)}} -> the node
    table a map file holds (module docstring): tiles in slots, each tile's cells row by row, each cell's nodes bottom
    up."""
    keys = sorted(tiles)
    S = len(keys)
    tidx = np.full((TH, TW), -1, np.int32)
    tcnt = np.zeros((S, CN, CN), np.uint8)
    tcum = np.zeros((S, CN, CN), np.uint16)
    tarea = np.zeros((S, CN, CN), np.uint16)
    tbase = np.zeros(S + 1, np.int64)
    for s, (ty, tx) in enumerate(keys):
        tidx[ty - ty0, tx - tx0] = s
        k = (tiles[(ty, tx)]["z"] != NONE).sum(0)
        tcnt[s] = k
        flat = k.ravel().astype(np.int64)
        tcum[s] = (np.cumsum(flat) - flat).reshape(CN, CN)
        tbase[s + 1] = tbase[s] + flat.sum()
        tarea[s] = tiles[(ty, tx)]["area"]
    N = int(tbase[-1])
    g = {"tidx": tidx, "tcnt": tcnt, "tcum": tcum, "tbase": tbase, "tarea": tarea, "nz": np.zeros(N, np.int16),
         "nlink": np.zeros(N, np.uint32), "nkind": np.zeros(N, np.uint8), "ncxy": np.zeros(N, np.uint8)}
    for s, key in enumerate(keys):
        d = tiles[key]
        for l in range(L):
            rr, cc = np.nonzero(d["z"][l] != NONE)
            i = tbase[s] + tcum[s, rr, cc] + l
            g["nz"][i], g["nlink"][i] = d["z"][l][rr, cc], d["link"][l][rr, cc]
            g["nkind"][i], g["ncxy"][i] = d["kind"][l][rr, cc], d["cxy"][l][rr, cc]
    g["nreach"] = reach_packed(g, ty0, tx0)
    return g


def to_sparse(z, link, kind, cxy, area, ty0=30, tx0=30):
    """Dense (L, H, W) node arrays of whole tiles -> pack()'s table (tests)."""
    tiles = {}
    for r in range(0, z.shape[1], CN):
        for c in range(0, z.shape[2], CN):
            sl = (slice(None), slice(r, r + CN), slice(c, c + CN))
            tiles[(ty0 + r // CN, tx0 + c // CN)] = {"z": z[sl], "link": link[sl], "kind": kind[sl], "cxy": cxy[sl],
                                                     "area": area[r:r + CN, c:c + CN]}
    return pack(tiles, ty0, tx0, z.shape[1] // CN, z.shape[2] // CN)


def reach_packed(g, ty0, tx0):
    """How much ground each node is connected to both ways -> (N,) uint8, 8 x log2(nodes) (the route planner tells
    the street from the rooftop over it, or the terrain buried under a city, by it)."""
    tidx, tcnt, tcum, tbase, lk = g["tidx"], g["tcnt"], g["tcum"], g["tbase"], g["nlink"].astype(np.int64)
    TH, TW = tidx.shape
    N = len(lk)
    if not N:
        return np.zeros(0, np.uint8)
    S = len(tcnt)
    slot_of = np.repeat(np.arange(S), np.diff(tbase))
    cells = np.concatenate([np.repeat(np.arange(CN * CN), tcnt[s].ravel()) for s in range(S)])
    lr, lc = np.divmod(cells, CN)
    sl_ty, sl_tx = np.nonzero(tidx >= 0)
    order = np.argsort(tidx[sl_ty, sl_tx])
    t_r, t_c = sl_ty[order], sl_tx[order]                 # tile row/col of each slot
    r = t_r[slot_of] * CN + lr
    c = t_c[slot_of] * CN + lc
    l = np.arange(N) - (tbase[slot_of] + tcum[slot_of, lr, lc])
    H, W = TH * CN, TW * CN
    src, dst = [], []
    for d, (dr, dc) in enumerate(DIRS[:4]):              # each pair once; the back edge must exist too
        back = (d + 4) % 8
        bit = (lk >> d) & 1
        tl = (lk >> (8 + 2 * d)) & 3
        r2, c2 = r + dr, c + dc
        ok = (bit == 1) & (r2 >= 0) & (r2 < H) & (c2 >= 0) & (c2 < W)
        r2c, c2c = np.clip(r2, 0, H - 1), np.clip(c2, 0, W - 1)
        s2 = tidx[r2c // CN, c2c // CN]
        ok &= s2 >= 0
        s2c = np.maximum(s2, 0)
        ok &= tl < tcnt[s2c, r2c % CN, c2c % CN]
        j = np.where(ok, tbase[s2c] + tcum[s2c, r2c % CN, c2c % CN] + tl, 0)
        bj = lk[j]
        ok &= (((bj >> back) & 1) == 1) & (((bj >> (8 + 2 * back)) & 3) == l)
        src.append(np.nonzero(ok)[0])
        dst.append(j[ok])
    lab = _union(N, np.concatenate(src), np.concatenate(dst))
    size = np.bincount(lab, minlength=N)[lab]
    return np.minimum(255, np.ceil(8 * np.log2(size + 1))).astype(np.uint8)
