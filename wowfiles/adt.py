"""The client's map files: the chunk layout they share (WDT, ADT, WMO, M2), a map's tiles from its WDT, one root
ADT's terrain (heights, holes, areas) and its liquids (MH2O). Used by wowfiles/terrain.py and the walking map
(wowfiles/navbuild.py)."""
import struct

import numpy as np

from .navformat import TILE  # noqa: F401 - a map tile, yards (the walking map's format holds the one definition)

ORIGIN = 32 * TILE
CHUNK = TILE / 16.0
LIQUID_KIND = {1: 1, 2: 2, 3: 3, 4: 4, 5: 1, 6: 2, 7: 3, 8: 4}


def chunks(d, start=0, end=None, reverse=True):
    end = len(d) if end is None else end
    p = start
    while p + 8 <= end:
        tag = d[p:p + 4]
        if reverse:
            tag = tag[::-1]
        sz = struct.unpack_from("<I", d, p + 4)[0]
        yield tag, p + 8, sz
        p += 8 + sz


def wdt_tiles(d):
    """{(ty, tx): (root, obj0, obj1, tex0, lod, maptex, maptexN, minimap) FileDataIDs} from a WDT's MAID."""
    out = {}
    for tag, p, sz in chunks(d):
        if tag == b"MAID":
            for i in range(min(4096, sz // 32)):
                f = struct.unpack_from("<8I", d, p + 32 * i)
                if f[0]:
                    out[divmod(i, 64)] = f
    return out


def parse_terrain(d):
    """Root ADT -> dict(outer (129,129), inner (128,128) heights, hole (128,128), area (16,16), seen (16,16),
    wlevel (128,128) liquid surface or nan, liq (128,128) kind)."""
    outer = np.full((129, 129), np.nan, np.float32)
    inner = np.full((128, 128), np.nan, np.float32)
    hole = np.zeros((128, 128), bool)
    area = np.zeros((16, 16), np.uint16)
    seen = np.zeros((16, 16), bool)
    wlevel = np.full((128, 128), np.nan, np.float32)
    liq = np.zeros((128, 128), np.uint8)
    mh2o = None
    for tag, p, sz in chunks(d):
        if tag == b"MH2O":
            mh2o = p
        if tag != b"MCNK":
            continue
        flags, ix, iy = struct.unpack_from("<III", d, p)
        if ix > 15 or iy > 15:
            continue
        area[iy, ix] = struct.unpack_from("<I", d, p + 0x34)[0]
        z0 = struct.unpack_from("<f", d, p + 0x68 + 8)[0]
        if flags & 0x10000:
            bits = struct.unpack_from("<Q", d, p + 0x14)[0]
            hb = ((bits >> np.arange(64, dtype=np.uint64)) & np.uint64(1)).astype(bool).reshape(8, 8)
        else:
            lo = struct.unpack_from("<H", d, p + 0x3C)[0]
            g = ((lo >> np.arange(16)) & 1).astype(bool).reshape(4, 4)
            hb = np.repeat(np.repeat(g, 2, 0), 2, 1)
        hole[iy * 8:iy * 8 + 8, ix * 8:ix * 8 + 8] = hb
        for t2, p2, s2 in chunks(d, p + 128, p + sz):
            if t2 == b"MCVT" and s2 >= 580:
                v = np.frombuffer(d, np.float32, 145, p2) + z0
                v = v.reshape(-1)
                rows = [v[r * 17:r * 17 + 9] for r in range(9)]
                mids = [v[r * 17 + 9:r * 17 + 17] for r in range(8)]
                outer[iy * 8:iy * 8 + 9, ix * 8:ix * 8 + 9] = np.stack(rows)
                inner[iy * 8:iy * 8 + 8, ix * 8:ix * 8 + 8] = np.stack(mids)
                seen[iy, ix] = True
                break
    if mh2o is not None:
        liquids(d, mh2o, wlevel, liq)
    return {"outer": outer, "inner": inner, "hole": hole, "area": area, "seen": seen, "wlevel": wlevel, "liq": liq}


def liquids(d, base, wlevel, liq):
    for k in range(256):
        off_inst, layers, _attr = struct.unpack_from("<III", d, base + 12 * k)
        if not off_inst or not layers:
            continue
        cy, cx = divmod(k, 16)
        for n in range(layers):
            ip = base + off_inst + 24 * n
            ltype, lvf, mn, mx, xo, yo, w, hh, off_bm, off_v = struct.unpack_from("<HHffBBBBII", d, ip)
            if w == 0 or hh == 0 or xo + w > 8 or yo + hh > 8:
                continue
            exists = np.ones((hh, w), bool)
            if off_bm:
                nbytes = (w * hh + 7) // 8
                bits = np.unpackbits(np.frombuffer(d, np.uint8, nbytes, base + off_bm), bitorder="little")
                exists = bits[:w * hh].reshape(hh, w).astype(bool)
            level = np.full((hh, w), mx, np.float32)
            if off_v and (lvf in (0, 1, 3) or lvf >= 42):
                try:
                    vh = np.frombuffer(d, np.float32, (w + 1) * (hh + 1), base + off_v).reshape(hh + 1, w + 1)
                    vh = np.where(np.isfinite(vh) & (np.abs(vh) < 1e5), vh, mx)
                    level = np.maximum.reduce([vh[:-1, :-1], vh[:-1, 1:], vh[1:, :-1], vh[1:, 1:]])
                except ValueError:
                    pass
            r0, c0 = cy * 8 + yo, cx * 8 + xo
            sub = wlevel[r0:r0 + hh, c0:c0 + w]
            wlevel[r0:r0 + hh, c0:c0 + w] = np.fmax(sub, np.where(exists, level, np.nan))
            liq[r0:r0 + hh, c0:c0 + w] = np.where(exists, LIQUID_KIND.get(ltype, 1), liq[r0:r0 + hh, c0:c0 + w])
