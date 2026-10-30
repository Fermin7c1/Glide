"""The world's objects as solid geometry: WMO (buildings, caves, bridges) and M2 (doodads) collision meshes, where
each map places them (MDDF / MODF), and their triangles in world coordinates."""
import math
import struct

import numpy as np

from .adt import ORIGIN, chunks

# MOPY / MPY2 triangle flags
F_DETAIL, F_COLLISION, F_RENDER = 0x04, 0x08, 0x20


def wmo_root(d):
    out = {"groups": [], "ngroups": 0, "sets": [], "doodads": [], "modi": None, "bbox": None}
    for tag, p, sz in chunks(d):
        if tag == b"MOHD":
            out["ngroups"] = struct.unpack_from("<I", d, p + 4)[0]
            out["bbox"] = struct.unpack_from("<6f", d, p + 36)
        elif tag == b"GFID":
            out["groups"] = [int(x) for x in np.frombuffer(d, np.uint32, sz // 4, p)]
        elif tag == b"MODS":
            for i in range(sz // 32):
                _name, start, count = struct.unpack_from("<20sII", d, p + 32 * i)
                out["sets"].append((start, count))
        elif tag == b"MODI":
            out["modi"] = [int(x) for x in np.frombuffer(d, np.uint32, sz // 4, p)]
        elif tag == b"MODD":
            for i in range(sz // 40):
                ofs, x, y, z, qx, qy, qz, qw, sc = struct.unpack_from("<I3f4ff", d, p + 40 * i)
                out["doodads"].append((ofs & 0xFFFFFF, (x, y, z), (qx, qy, qz, qw), sc))
    return out


def wmo_group(d):
    """One WMO group file -> (verts, tris) of its collidable triangles, or None."""
    for tag, p, sz in chunks(d):
        if tag != b"MOGP":
            continue
        flags = movi = movt = None
        for t2, p2, s2 in chunks(d, p + 0x44, p + sz):
            if t2 == b"MOPY":
                flags = np.frombuffer(d, np.uint8, s2, p2).reshape(-1, 2)[:, 0].astype(np.uint16)
            elif t2 == b"MPY2":
                flags = np.frombuffer(d, np.uint16, s2 // 2, p2).reshape(-1, 2)[:, 0]
            elif t2 == b"MOVI":
                movi = np.frombuffer(d, np.uint16, s2 // 2, p2).reshape(-1, 3).astype(np.int32)
            elif t2 == b"MOVX":
                movi = np.frombuffer(d, np.uint32, s2 // 4, p2).reshape(-1, 3).astype(np.int32)
            elif t2 == b"MOVT":
                movt = np.frombuffer(d, np.float32, s2 // 4, p2).reshape(-1, 3)
        if flags is None or movi is None or movt is None:
            return None
        render = ((flags & F_RENDER) != 0) & ((flags & F_DETAIL) == 0)
        keep = ((flags & F_COLLISION) != 0) | render
        n = min(len(keep), len(movi))
        tris = movi[:n][keep[:n]]
        if not len(tris):
            return None
        return movt.astype(np.float32), tris
    return None


def m2_collision(d):
    """An M2 -> (verts, tris) of its collision mesh, or None (most small doodads have none)."""
    base = None
    if d[:4] == b"MD21":
        for tag, p, _sz in chunks(d, reverse=False):
            if tag == b"MD21":
                base = p
                break
    elif d[:4] == b"MD20":
        base = 0
    if base is None or d[base:base + 4] != b"MD20":
        return None
    ni, oi = struct.unpack_from("<II", d, base + 216)
    nv, ov = struct.unpack_from("<II", d, base + 224)
    if not ni or not nv or ni % 3:
        return None
    tris = np.frombuffer(d, np.uint16, ni, base + oi).astype(np.int32).reshape(-1, 3)
    verts = np.frombuffer(d, np.float32, nv * 3, base + ov).reshape(-1, 3)
    if tris.max() >= nv:
        return None
    return verts.astype(np.float32), tris




def _rz(deg):
    a = math.radians(deg)
    return np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])


def _ry(deg):
    a = math.radians(deg)
    return np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])


def _rx(deg):
    a = math.radians(deg)
    return np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]])


def place_matrix(rot):
    """MDDF/MODF rotation (degrees, placement axes) -> model-to-world rotation (world: X north, Y west, Z up).
    The placement frame is world X and Y negated; checked against MODF bounding boxes (exact to 0.01 yd)."""
    rx, ry, rz = rot
    flip = np.diag([-1.0, -1.0, 1.0])
    return flip @ _rz(ry) @ _ry(rx) @ _rx(rz)


def place_pos(p):
    return np.array([ORIGIN - p[2], ORIGIN - p[0], p[1]])


def quat_matrix(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


class Models:
    """Model geometry by FileDataID (model space), loaded once per build. Unreadable (encrypted) files -> None."""

    def __init__(self, casc):
        self.c = casc
        self.m2 = {}
        self.wmo = {}
        self.bad = 0

    def read(self, fdid):
        try:
            return self.c.read_fdid(int(fdid))
        except Exception:  # noqa: BLE001 - encrypted, missing
            self.bad += 1
            return None

    def m2_mesh(self, fdid):
        if fdid not in self.m2:
            d = self.read(fdid)
            m = None
            if d:
                try:
                    m = m2_collision(d)
                except Exception:  # noqa: BLE001
                    m = None
            if m is not None:
                v, t = m
                m = (v, t, float(np.sqrt((v ** 2).sum(1)).max()) if len(v) else 0.0)
            self.m2[fdid] = m
        return self.m2[fdid]

    def wmo_mesh(self, fdid, doodad_set):
        """-> (verts, tris, [(m2 fdid, pos, quat, scale)]) in WMO model space, or None."""
        key = (fdid, doodad_set)
        if key in self.wmo:
            return self.wmo[key]
        d = self.read(fdid)
        out = None
        if d:
            try:
                root = wmo_root(d)
                V, T, off = [], [], 0
                for g in root["groups"][:root["ngroups"] or len(root["groups"])]:
                    gd = self.read(g)
                    r = wmo_group(gd) if gd else None
                    if r is None:
                        continue
                    V.append(r[0])
                    T.append(r[1] + off)
                    off += len(r[0])
                dd = []
                sets = root["sets"]
                use = [0] + ([doodad_set] if doodad_set and doodad_set < len(sets) else [])
                modi = root["modi"] or []
                for s in use:
                    if s >= len(sets):
                        continue
                    start, count = sets[s]
                    for ofs, pos, q, sc in root["doodads"][start:start + count]:
                        if ofs < len(modi) and modi[ofs]:
                            dd.append((modi[ofs], pos, q, sc))
                if V:
                    out = (np.concatenate(V), np.concatenate(T), dd)
                elif dd:
                    out = (np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int32), dd)
            except Exception:  # noqa: BLE001
                out = None
        self.wmo[key] = out
        return out


def read_placements(c, tiles):
    """Every object placed on a map (deduplicated by uniqueId): [dict(kind 'wmo'|'m2', fdid, pos, rot, scale,
    set, box (xmin, xmax, ymin, ymax) world or None)]."""
    seen, out = set(), []
    for (_ty, _tx), f in sorted(tiles.items()):
        try:
            d = c.read_fdid(f[1])
        except Exception:  # noqa: BLE001
            continue
        for tag, p, sz in chunks(d):
            if tag == b"MDDF":
                for i in range(sz // 36):
                    nid, uid, px, py, pz, rx, ry, rz, sc, fl = struct.unpack_from("<II3f3fHH", d, p + 36 * i)
                    if ("m", uid) in seen or not (fl & 0x40):
                        continue
                    seen.add(("m", uid))
                    out.append({"kind": "m2", "fdid": nid, "pos": (px, py, pz), "rot": (rx, ry, rz),
                                "scale": sc / 1024.0, "box": None})
            elif tag == b"MODF":
                for i in range(sz // 64):
                    v = struct.unpack_from("<II3f3f6fHHHH", d, p + 64 * i)
                    nid, uid = v[0], v[1]
                    if ("w", uid) in seen:
                        continue
                    seen.add(("w", uid))
                    e = v[8:14]
                    xs, ys = sorted((ORIGIN - e[2], ORIGIN - e[5])), sorted((ORIGIN - e[0], ORIGIN - e[3]))
                    out.append({"kind": "wmo", "fdid": nid, "pos": v[2:5], "rot": v[5:8],
                                "scale": (v[17] / 1024.0) if (v[14] & 0x4) and v[17] else 1.0, "set": v[15],
                                "box": (xs[0], xs[1], ys[0], ys[1])})
    return out


def world_triangles(models, placements, box):
    """All collidable triangles (k, 3, 3) world coordinates of the placements touching box (xmin, xmax, ymin, ymax)."""
    xmin, xmax, ymin, ymax = box
    out = []

    def add(V, T, R, pos):
        if not len(T):
            return
        W = V @ R.T + pos
        tri = W[T]
        tx, ty = tri[:, :, 0], tri[:, :, 1]
        keep = (tx.max(1) >= xmin) & (tx.min(1) <= xmax) & (ty.max(1) >= ymin) & (ty.min(1) <= ymax)
        if keep.any():
            out.append(tri[keep].astype(np.float32))
    for p in placements:
        pos = place_pos(p["pos"])
        if p["kind"] == "m2":
            m = models.m2_mesh(p["fdid"])
            if m is None:
                continue
            r = m[2] * p["scale"]
            if pos[0] + r < xmin or pos[0] - r > xmax or pos[1] + r < ymin or pos[1] - r > ymax:
                continue
            add(m[0], m[1], place_matrix(p["rot"]) * p["scale"], pos)
        else:
            b = p["box"]
            if b and (b[1] < xmin or b[0] > xmax or b[3] < ymin or b[2] > ymax):
                continue
            m = models.wmo_mesh(p["fdid"], p.get("set", 0))
            if m is None:
                continue
            R = place_matrix(p["rot"]) * p["scale"]
            add(m[0], m[1], R, pos)
            for fd, dpos, q, sc in m[2]:
                mm = models.m2_mesh(fd)
                if mm is None:
                    continue
                wp = R @ np.array(dpos) + pos
                r = mm[2] * sc * p["scale"]
                if wp[0] + r < xmin or wp[0] - r > xmax or wp[1] + r < ymin or wp[1] - r > ymax:
                    continue
                add(mm[0], mm[1], R @ quat_matrix(q) * sc, wp)
    return np.concatenate(out) if out else np.zeros((0, 3, 3), np.float32)
