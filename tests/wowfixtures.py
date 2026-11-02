"""Tiny synthetic game files for the tests (no game client in a test run): a WDC3 table written to a .dbd layout,
a raw BGRA BLP2 texture, and a stand-in for an open client index (wowfiles/casc.Casc's read_fdid, fdid_of, has).
Everything here is made up by the tests; no game file is copied."""
import struct

from wowfiles.db2 import parse_dbd


def wdc3(dbd_text, layout, rows):
    """A WDC3 table (one section, plain fields, the ID list non-inline) holding rows ({field: value}; arrays as
    lists; strings are left empty) in the .dbd's layout `layout` (hex hash)."""
    cols, lays = parse_dbd(dbd_text)
    lay = next(lt for lt in lays if layout in lt["layouts"])
    inline = [f for f in lay["fields"] if "noninline" not in f["ann"]]
    sizes = [((f["bits"] or 32) // 8) * f["array"] for f in inline]
    rec_size = sum(sizes)
    recs = b""
    for r in rows:
        rec = b""
        for f, sz in zip(inline, sizes):
            esz = sz // f["array"]
            v = r.get(f["name"], [0] * f["array"] if f["array"] > 1 else 0)
            vals = v if isinstance(v, list) else [v]
            for x in vals:
                if cols.get(f["name"]) == "float":
                    rec += struct.pack("<f", float(x))
                elif cols.get(f["name"]) in ("string", "locstring"):
                    rec += b"\0\0\0\0"
                else:
                    rec += int(x).to_bytes(esz, "little", signed=x < 0)
        recs += rec
    ids = struct.pack("<%dI" % len(rows), *[r["ID"] for r in rows])
    n, nf = len(rows), len(inline)
    head_len = 4 + 68 + 40 + 4 * nf + 24 * nf
    header = b"WDC3" + struct.pack("<IIIIIIIIIHHIIIIIII", n, nf, rec_size, 0, 0, int(layout, 16),
                                   min(r["ID"] for r in rows), max(r["ID"] for r in rows), 0, 0, 0, nf, 0, 0,
                                   24 * nf, 0, 0, 1)
    section = struct.pack("<QIIIIIIII", 0, head_len, n, 0, 0, len(ids), 0, 0, 0)
    fstruct, fsi, off = b"", b"", 0
    for sz in sizes:
        fstruct += struct.pack("<hH", 0, off)
        fsi += struct.pack("<HHIIIII", off * 8, sz * 8, 0, 0, 0, 0, 0)
        off += sz
    return header + section + fstruct + fsi + recs + ids


def blp(w, h, rgba=(200, 120, 40, 255)):
    """A w x h raw-BGRA BLP2 texture of one colour, with a 1 px border of another (to see the icon trim)."""
    px = bytearray()
    for y in range(h):
        for x in range(w):
            r, g, b, a = (10, 10, 10, 255) if x in (0, w - 1) or y in (0, h - 1) else rgba
            px += bytes((b, g, r, a))
    head = b"BLP2" + struct.pack("<IBBBB", 1, 3, 8, 0, 0) + struct.pack("<II", w, h)
    offs = [0] * 16
    sizes = [0] * 16
    offs[0], sizes[0] = 148 + 1024, len(px)
    return head + struct.pack("<16I", *offs) + struct.pack("<16I", *sizes) + b"\0" * 1024 + bytes(px)


class FakeClient:
    """An open client index over a dict {FileDataID: bytes}, with optional file names {path: FileDataID}."""

    def __init__(self, files, names=None, version="1.60.1.69913"):
        self.files, self.names, self.version = dict(files), dict(names or {}), version
        self.reads = []

    def has(self, fid):
        return fid in self.files

    def fdid_of(self, path):
        return self.names.get(path.replace("\\", "/").lower())

    def read_fdid(self, fid):
        self.reads.append(fid)
        if fid not in self.files:
            raise KeyError("FileDataID %s not in the root" % fid)
        return self.files[fid]
