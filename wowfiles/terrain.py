#!/usr/bin/env python3
"""Terrain of the open-world maps from the client's ADT tiles -> navigation grids (data/gamedata/terrain/<map>.npz).

Every map tile (533.33 yd) is 16 x 16 chunks of 8 x 8 quads (4.17 yd); the grid here has one cell per 2 x 2
quads (8.33 yd), 64 x 64 cells per tile. Per cell:
  h      ground height, half yards (int16)
  steep  share of the cell's quads too steep to walk up (uint8, 0..255; a quad is steep above 50 degrees)
  water  liquid surface above the ground, half yards (int16; 0 = dry)
  liq    liquid kind (uint8: 0 none, 1 water, 2 ocean, 3 magma, 4 slime)
  hole   1 where the terrain has a hole (cave mouths, building and city entrances: WMO geometry, not terrain)
  area   AreaTable ID of the chunk (uint16)
plus meta: map id, the tile origin (ty0, tx0), the cell size. Row r / column c of the grid is tile
(ty0 + r // 64, tx0 + c // 64); world X (north) = (32 - row / 64) * TILE, world Y (west) = (32 - col / 64) * TILE.

What the grid can't know: buildings, bridges, tunnels and cities are separate models (WMO) on top of the terrain,
so the route planner (knowledge/mechanics/nav.py) also links cells along paths people actually walk (RestedXP's
waypoint chains, your own recorded movement).
"""
import os
import struct
import time

import numpy as np

from . import adt

UNIT = adt.CHUNK / 8.0           # quad size, 4.1667 yd
CELL = UNIT * 2              # grid cell, 8.333 yd
STEEP = np.tan(np.radians(50.0))


def parse_adt(d):
    """-> dict of 64 x 64 arrays for one tile (see the module docstring), or None."""
    h = np.zeros((129, 129), np.float32)               # outer vertex grid (shared edges)
    hole = np.zeros((128, 128), bool)                  # per quad
    area = np.zeros((16, 16), np.uint16)
    wlevel = np.full((128, 128), np.nan, np.float32)   # liquid surface per quad
    liq = np.zeros((128, 128), np.uint8)
    mh2o = None
    seen = 0
    for tag, p, sz in adt.chunks(d):
        if tag == b"MH2O":
            mh2o = (p, sz)
        elif tag == b"MCNK":
            flags, ix, iy = struct.unpack_from("<III", d, p)
            if ix > 15 or iy > 15:
                continue
            area[iy, ix] = struct.unpack_from("<I", d, p + 0x34)[0]
            z0 = struct.unpack_from("<f", d, p + 0x68 + 8)[0]
            if flags & 0x10000:
                bits = struct.unpack_from("<Q", d, p + 0x14)[0]
                hb = np.array([(bits >> (r * 8 + c)) & 1 for r in range(8) for c in range(8)], bool).reshape(8, 8)
            else:
                lo = struct.unpack_from("<H", d, p + 0x3C)[0]
                g = np.array([(lo >> (r * 4 + c)) & 1 for r in range(4) for c in range(4)], bool).reshape(4, 4)
                hb = np.repeat(np.repeat(g, 2, 0), 2, 1)
            hole[iy * 8:iy * 8 + 8, ix * 8:ix * 8 + 8] = hb
            for t2, p2, s2 in adt.chunks(d, p + 128, p + sz):
                if t2 == b"MCVT" and s2 >= 580:
                    v = np.frombuffer(d, np.float32, 145, p2)
                    outer = np.stack([v[r * 17:r * 17 + 9] for r in range(9)]) + z0
                    h[iy * 8:iy * 8 + 9, ix * 8:ix * 8 + 9] = outer
                    seen += 1
                    break
    if not seen:
        return None
    if mh2o is not None:
        adt.liquids(d, mh2o[0], wlevel, liq)
    # quad slopes: the steepest of the quad's 4 edges and 2 diagonals
    a, b, c, e = h[:-1, :-1], h[:-1, 1:], h[1:, :-1], h[1:, 1:]
    slope = np.maximum.reduce([abs(a - b), abs(c - e), abs(a - c), abs(b - e),
                               abs(a - e) / 1.4142, abs(b - c) / 1.4142]) / UNIT
    steep_q = slope > STEEP
    mean_h = (a + b + c + e) / 4.0

    def pool(x, fn):
        return fn(fn(x.reshape(64, 2, 64, 2), axis=3), axis=1)
    cell_h = pool(mean_h, np.mean)
    steep = (pool(steep_q.astype(np.float32), np.mean) * 255).astype(np.uint8)
    hole_c = pool(hole.astype(np.float32), np.mean) >= 0.5
    wl = np.where(np.isnan(wlevel), -1e9, wlevel)
    wl_c = pool(wl, np.max)
    depth = np.where(wl_c > -1e8, np.maximum(0, wl_c - cell_h), 0)
    liq_c = pool(liq, np.max)
    return {"h": np.clip(np.round(cell_h * 2), -32000, 32000).astype(np.int16), "steep": steep,
            "water": np.clip(np.round(depth * 2), 0, 32000).astype(np.int16),
            "liq": np.where(wl_c > -1e8, liq_c, 0).astype(np.uint8), "hole": hole_c.astype(np.uint8),
            "area": np.repeat(np.repeat(area, 4, 0), 4, 1)}


def wdt_tiles(d):
    """{(ty, tx): root ADT FileDataID} from a WDT's MAID chunk."""
    return {tile: fids[0] for tile, fids in adt.wdt_tiles(d).items()}


def extract_map(c, map_id, wdt_fid, out_path, log=print):
    t0 = time.time()
    try:
        tiles = wdt_tiles(c.read_fdid(wdt_fid))
    except Exception as e:  # noqa: BLE001
        log("terrain: map %s: no WDT (%s)" % (map_id, e))
        return None
    if not tiles:
        return None
    ty0 = min(t[0] for t in tiles)
    tx0 = min(t[1] for t in tiles)
    H = (max(t[0] for t in tiles) - ty0 + 1) * 64
    W = (max(t[1] for t in tiles) - tx0 + 1) * 64
    g = {"h": np.zeros((H, W), np.int16), "steep": np.full((H, W), 255, np.uint8), "water": np.zeros((H, W), np.int16),
         "liq": np.zeros((H, W), np.uint8), "hole": np.zeros((H, W), np.uint8), "area": np.zeros((H, W), np.uint16),
         "land": np.zeros((H, W), np.uint8)}
    ok = bad = 0
    for (ty, tx), fid in sorted(tiles.items()):
        try:
            t = parse_adt(c.read_fdid(fid))
        except Exception:  # noqa: BLE001 - encrypted / missing tile
            t = None
        if t is None:
            bad += 1
            continue
        r, q = (ty - ty0) * 64, (tx - tx0) * 64
        for k, v in t.items():
            g[k][r:r + 64, q:q + 64] = v
        g["land"][r:r + 64, q:q + 64] = 1
        ok += 1
    np.savez_compressed(out_path, map=np.int32(map_id), ty0=np.int32(ty0), tx0=np.int32(tx0), cell=np.float32(CELL),
                        **g)
    log("terrain: map %s: %d tiles (%d unreadable), grid %d x %d, %.0fs" % (map_id, ok, bad, H, W, time.time() - t0))
    return {"tiles": ok, "unreadable": bad, "grid": [H, W]}


def extract_all(ex, out_dir, log=print, maps=None):
    """Every open-world map (Map.db2 InstanceType 0, MapType 1) -> out_dir/<map id>.npz."""
    os.makedirs(out_dir, exist_ok=True)
    info = {}
    for m in ex.table("Map"):
        if m.get("InstanceType") != 0 or m.get("MapType") != 1 or not m.get("WdtFileDataID"):
            continue
        if maps and m["ID"] not in maps:
            continue
        r = extract_map(ex.c, m["ID"], m["WdtFileDataID"], os.path.join(out_dir, "%d.npz" % m["ID"]), log)
        if r:
            info[str(m["ID"])] = dict(r, name=m.get("MapName_lang"))
    return {"maps": info}
