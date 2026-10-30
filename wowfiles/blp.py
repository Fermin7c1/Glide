#!/usr/bin/env python3
"""BLP2 texture decoding (palette, DXT1/3/5, raw BGRA) to RGBA numpy arrays, and a tiny PNG writer (zlib only).

    rgba = decode_blp(data)            # (h, w, 4) uint8
    write_png("map.png", rgba)
"""
import struct
import zlib

import numpy as np


class BLPError(Exception):
    pass


def decode_blp(data, mip=0):
    if data[:4] != b"BLP2":
        raise BLPError("not a BLP2 texture (%r)" % data[:4])
    _type, enc, alpha_depth, alpha_enc, _mips = struct.unpack_from("<IBBBB", data, 4)
    w, h = struct.unpack_from("<II", data, 12)
    offs = struct.unpack_from("<16I", data, 20)
    sizes = struct.unpack_from("<16I", data, 84)
    w, h = max(1, w >> mip), max(1, h >> mip)
    raw = data[offs[mip]:offs[mip] + sizes[mip]]
    if enc == 1:
        pal = np.frombuffer(data, np.uint8, 1024, 148).reshape(256, 4)[:, [2, 1, 0, 3]].copy()
        idx = np.frombuffer(raw, np.uint8, w * h).reshape(h, w)
        out = pal[idx]
        out[..., 3] = _palette_alpha(raw[w * h:], w, h, alpha_depth)
        return out
    if enc == 2:
        if alpha_enc == 0:
            return _dxt(raw, w, h, 1, alpha_depth)
        if alpha_enc == 1:
            return _dxt(raw, w, h, 3, alpha_depth)
        if alpha_enc == 7:
            return _dxt(raw, w, h, 5, alpha_depth)
        raise BLPError("DXT alpha encoding %d" % alpha_enc)
    if enc == 3:
        return np.frombuffer(raw, np.uint8, w * h * 4).reshape(h, w, 4)[..., [2, 1, 0, 3]].copy()
    raise BLPError("BLP encoding %d" % enc)


def _palette_alpha(a, w, h, depth):
    n = w * h
    if depth == 0:
        return np.full((h, w), 255, np.uint8)
    if depth == 8:
        return np.frombuffer(a, np.uint8, n).reshape(h, w)
    bits = np.unpackbits(np.frombuffer(a, np.uint8), bitorder="little")
    if depth == 1:
        return (bits[:n] * 255).astype(np.uint8).reshape(h, w)
    if depth == 4:
        v = np.frombuffer(a, np.uint8)
        lo, hi = v & 0x0F, v >> 4
        return (np.stack([lo, hi], 1).reshape(-1)[:n] * 17).astype(np.uint8).reshape(h, w)
    raise BLPError("palette alpha depth %d" % depth)


def _rgb565(c):
    r = ((c >> 11) & 31).astype(np.uint32)
    g = ((c >> 5) & 63).astype(np.uint32)
    b = (c & 31).astype(np.uint32)
    return np.stack([(r * 527 + 23) >> 6, (g * 259 + 33) >> 6, (b * 527 + 23) >> 6], -1)


def _dxt(raw, w, h, kind, alpha_depth):
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    n = bw * bh
    bsize = 8 if kind == 1 else 16
    blocks = np.frombuffer(raw, np.uint8, n * bsize).reshape(n, bsize)
    cblk = blocks[:, -8:]
    c0 = cblk[:, 0].astype(np.uint16) | (cblk[:, 1].astype(np.uint16) << 8)
    c1 = cblk[:, 2].astype(np.uint16) | (cblk[:, 3].astype(np.uint16) << 8)
    p0, p1 = _rgb565(c0), _rgb565(c1)
    four = (c0 > c1) | (kind != 1)
    p2 = np.where(four[:, None], (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four[:, None], (p0 + 2 * p1) // 3, 0)
    pal = np.stack([p0, p1, p2, p3], 1).astype(np.uint8)                # n,4,3
    bits = cblk[:, 4:8].astype(np.uint32)
    word = bits[:, 0] | (bits[:, 1] << 8) | (bits[:, 2] << 16) | (bits[:, 3] << 24)
    idx = (word[:, None] >> (2 * np.arange(16, dtype=np.uint32))) & 3     # n,16
    rgb = np.take_along_axis(pal, idx[..., None].repeat(3, -1).astype(np.int64), 1)   # n,16,3
    if kind == 1:
        a = np.where((~four[:, None]) & (idx == 3), 0, 255).astype(np.uint8)
        if alpha_depth == 0:
            a[:] = 255
    elif kind == 3:
        ab = blocks[:, :8]
        nib = np.stack([ab & 0x0F, ab >> 4], -1).reshape(n, 16)
        a = (nib * 17).astype(np.uint8)
    else:
        a0, a1 = blocks[:, 0].astype(np.int32), blocks[:, 1].astype(np.int32)
        ab = blocks[:, 2:8].astype(np.uint64)
        aw = np.zeros(n, np.uint64)
        for k in range(6):
            aw |= ab[:, k] << np.uint64(8 * k)
        ai = ((aw[:, None] >> (3 * np.arange(16, dtype=np.uint64))) & np.uint64(7)).astype(np.int32)
        eight = a0 > a1
        tab = np.zeros((n, 8), np.int32)
        tab[:, 0], tab[:, 1] = a0, a1
        for k in range(1, 7):
            tab[:, k + 1] = np.where(eight, ((7 - k) * a0 + k * a1) // 7, 0)
        for k in range(1, 5):
            tab[:, k + 1] = np.where(eight, tab[:, k + 1], ((5 - k) * a0 + k * a1) // 5)
        tab[:, 6] = np.where(eight, tab[:, 6], 0)
        tab[:, 7] = np.where(eight, tab[:, 7], 255)
        a = np.take_along_axis(tab, ai, 1).astype(np.uint8)
    px = np.concatenate([rgb, a[..., None]], -1).reshape(bh, bw, 4, 4, 4)
    img = px.transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return np.ascontiguousarray(img[:h, :w])


def write_png(path, img):
    """RGBA / RGB / grey uint8 array -> PNG file (no PIL needed)."""
    img = np.ascontiguousarray(img)
    if img.ndim == 2:
        ct, ch = 0, 1
    else:
        ch = img.shape[2]
        ct = {3: 2, 4: 6}[ch]
    h, w = img.shape[:2]
    rows = img.reshape(h, w * ch)
    raw = b"".join(b"\0" + rows[y].tobytes() for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ct, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)
