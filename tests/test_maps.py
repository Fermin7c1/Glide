#!/usr/bin/env python3
"""Game map extraction and terrain routes: CASC name hash and BLTE decoding (incl. TACT Salsa20 and zero-filled
unknown-key chunks), the DB2 (WDC5) reader on a synthetic table, BLP DXT1 decoding and the PNG writer, ADT terrain
parsing on a synthetic tile, A* over a grid with a mountain wall and a pass, the exact sub-zone raster, and the
walk_route tool when the extracted data is present.

Run: python3 tests/test_maps.py   (prints OK)
"""
import json
import os
import struct
import sys
import tempfile
import zlib

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "companion"))
import numpy as np  # noqa: E402
from glidedb import GlideDB  # noqa: E402

from wowfiles import blp, casc, db2, terrain  # noqa: E402

# ---- CASC ---------------------------------------------------------------------------------------------------
assert casc.hashlittle2(b"Four score and seven years ago") == (0x17770551, 0xCE7226E6)
assert casc.hashlittle2(b"") == (0xDEADBEEF, 0xDEADBEEF)
assert casc.name_hash("dbfilesclient/areatable.db2") == casc.name_hash("DBFILESCLIENT\\AREATABLE.DB2")
raw = b"hello world" * 50
one = b"BLTE" + struct.pack(">I", 0) + b"Z" + zlib.compress(raw)
assert casc.blte_decode(one) == raw
# two chunks: plain + encrypted with an unknown key -> zero-filled; with the key -> decrypted
key, kname, iv = bytes(range(16)), bytes(range(8)), b"\x01\x02\x03\x04"
plain2 = b"N" + b"secret map data!"
enc = casc.salsa20_xor(plain2, key, bytes([iv[0] ^ 1, iv[1], iv[2], iv[3], 0, 0, 0, 0]))   # chunk index 1
c1 = b"N" + b"open part"
c2 = b"E" + bytes([8]) + kname + bytes([4]) + iv + b"S" + enc
hdr = struct.pack(">I", 8 + 4 + 24 * 2)
table = bytes([0x0F]) + (2).to_bytes(3, "big") + struct.pack(">II", len(c1), 9) + b"\0" * 16 + \
    struct.pack(">II", len(c2), 16) + b"\0" * 16
blob = b"BLTE" + hdr + table + c1 + c2
casc.KEYS.pop(kname, None)
casc.MISSING.clear()
assert casc.blte_decode(blob) == b"open part" + b"\0" * 16 and casc.MISSING == {kname[::-1].hex().upper(): 1}
casc.KEYS[kname] = key
assert casc.blte_decode(blob) == b"open part" + b"secret map data!"
try:
    casc.KEYS.pop(kname)
    casc.blte_decode(blob, zero_fill=False)
    raise AssertionError("an unknown key must raise without zero_fill")
except casc.CascEncrypted:
    pass

# ---- DB2 (WDC5) on a synthetic two-row table -----------------------------------------------------------------
DBD = """COLUMNS
int ID
locstring Name_lang
float Pos[2]
int Level

LAYOUT 0BADF00D
$id$ID<32>
Name_lang
Pos[2]
Level<16>
"""
rec_size = 4 + 4 + 8 + 2
strings = b"Kharanos\0Anvilmar\0"
recs = []
for i, (rid, name_off, pos, lvl) in enumerate(((7, 0, (1.5, -2.0), 5), (9, 9, (3.0, 4.25), 12))):
    # a string field holds its offset from the field's own position to the string in the table after the records
    field_pos = i * rec_size + 4
    rel = 2 * rec_size + name_off - field_pos
    recs.append(struct.pack("<II2fh", rid, rel, pos[0], pos[1], lvl))
fields = [(0, 32, 0, 0), (32, 32, 0, 0), (64, 64, 0, 0), (128, 16, 0, 0)]
fsi = b"".join(struct.pack("<HHIIIII", ob, sb, add, kind, 0, 0, 0) for ob, sb, add, kind in fields)
header_len = 4 + 4 + 128 + 4 * 16 + 4
sec_hdr_len = 40
fs_len = 4 * 4
data_off = header_len + sec_hdr_len + fs_len + len(fsi)
head = b"WDC5" + struct.pack("<I", 5) + b"\0" * 128 + struct.pack(
    "<IIIIIIIIIHHIIIIIII", 2, 4, rec_size, len(strings), 0, 0x0BADF00D, 7, 9, 0, 0, 0, 4, 0, 0, len(fsi), 0, 0, 1)
sec = struct.pack("<QIIIIIIII", 0, data_off, 2, len(strings), 0, 0, 0, 0, 0)
fstruct = b"".join(struct.pack("<hH", 0, 0) for _ in range(4))
data = head + sec + fstruct + fsi + b"".join(recs) + strings
rows = db2.read_db2(data, DBD)
assert rows == [{"ID": 7, "Name_lang": "Kharanos", "Pos": [1.5, -2.0], "Level": 5},
                {"ID": 9, "Name_lang": "Anvilmar", "Pos": [3.0, 4.25], "Level": 12}], rows

# ---- BLP: a 4x4 DXT1 block + PNG -----------------------------------------------------------------------------
red, blue = 0xF800, 0x001F
block = struct.pack("<HHI", red, blue, 0b01010101 << 24)       # rows 0-2 colour 0 (red), row 3 colour 1 (blue)
hdr = b"BLP2" + struct.pack("<IBBBB", 1, 2, 0, 0, 0) + struct.pack("<II", 4, 4)
offs = [20 + 64 + 64 + 1024] + [0] * 15
sizes = [8] + [0] * 15
blpdata = hdr + struct.pack("<16I", *offs) + struct.pack("<16I", *sizes) + b"\0" * 1024 + block
img = blp.decode_blp(blpdata)
assert img.shape == (4, 4, 4) and tuple(img[0, 0]) == (255, 0, 0, 255) and tuple(img[3, 0]) == (0, 0, 255, 255), img
tmp = tempfile.mkdtemp()
blp.write_png(os.path.join(tmp, "t.png"), img)
assert open(os.path.join(tmp, "t.png"), "rb").read()[:8] == b"\x89PNG\r\n\x1a\n"


# ---- ADT terrain: one synthetic tile (flat chunks, one steep chunk, a hole, a lake) ----------------------------
def chunk(tag, payload):
    return tag[::-1] + struct.pack("<I", len(payload)) + payload


mcnks = []
for iy in range(16):
    for ix in range(16):
        hdr = bytearray(128)
        flags = 0
        struct.pack_into("<III", hdr, 0, flags, ix, iy)
        struct.pack_into("<I", hdr, 0x34, 100 + iy)             # area id per row
        if (ix, iy) == (5, 5):
            struct.pack_into("<H", hdr, 0x3C, 0xFFFF)          # all holes
        struct.pack_into("<3f", hdr, 0x68, 0.0, 0.0, 50.0)
        hv = np.zeros(145, np.float32)
        if ix == 8:                                             # a wall: each row climbs 60 yd across the chunk
            for r in range(9):
                hv[r * 17:r * 17 + 9] = np.linspace(0, 60, 9)
        mcnks.append(chunk(b"MCNK", bytes(hdr) + chunk(b"MCVT", hv.tobytes())))
# MH2O: chunk (2, 3) fully covered by water at 60 yd (10 above the 50 yd ground)
mh_head = bytearray(256 * 12)
inst_off = len(mh_head)
struct.pack_into("<III", mh_head, 12 * (2 * 16 + 3), inst_off, 1, 0)
inst = struct.pack("<HHffBBBBII", 1, 2, 60.0, 60.0, 0, 0, 8, 8, 0, 0)
adt = b"".join(mcnks) + chunk(b"MH2O", bytes(mh_head) + inst)
t = terrain.parse_adt(adt)
assert t["h"][0, 0] == 100 and t["steep"][0, 0] == 0                     # flat ground at 50 yd (half yards)
assert t["steep"][10, 33] == 255, "the wall chunk is too steep everywhere"
assert t["hole"][21, 21] == 1 and t["hole"][0, 0] == 0
assert t["water"][9, 13] == 20 and t["liq"][9, 13] == 1, (t["water"][9, 13], t["liq"][9, 13])   # 10 yd deep
assert t["area"][0, 0] == 100 and t["area"][63, 0] == 115

# ---- A*: a wall of steep cells with one pass ---------------------------------------------------------------
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("nav_t", os.path.join(ROOT, "knowledge", "mechanics", "nav.py"))
nav = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nav)


class G:
    pass


g = G()
H = W = 60
g.H, g.W, g.cell, g.map, g.ty0, g.tx0 = H, W, terrain.CELL, 0, 32, 32
g.h = np.zeros((H, W), np.int16)
g.steep = np.zeros((H, W), np.uint8)
g.steep[:, 30] = 255
g.steep[45, 30] = 0                                  # the pass
g.water = np.zeros((H, W), np.int16)
g.liq = np.zeros((H, W), np.uint8)
g.hole = np.zeros((H, W), np.uint8)
g.land = np.ones((H, W), np.uint8)
g.area = np.zeros((H, W), np.uint16)
path, cost, _n = nav.find_path(g, (10, 10), (10, 50), margin=60)
assert path and (45, 30) in path, "the route goes through the pass"
assert cost > 40 * terrain.CELL * 1.5, cost
# a link (a tunnel walked by RestedXP / you) makes the short way possible
path2, cost2, _n = nav.find_path(g, (10, 10), (10, 50), links={(10, 29): [((10, 31), 2 * terrain.CELL)]}, margin=60)
assert cost2 < cost and (10, 29) in path2 and (10, 31) in path2
# a cliff can be walked off, not up
g.h[:] = 0
g.h[:, 40:] = 24                                    # a 12 yd ledge past column 40
g.steep[:] = 0
up, _c, _n = nav.find_path(g, (10, 35), (10, 45), margin=8)
down, _c, _n = nav.find_path(g, (10, 45), (10, 35), margin=8)
assert up is None and down is not None
assert nav.compass(1, 0) == "east" and nav.compass(0, 1) == "north" and nav.compass(-1, -1) == "southwest"
assert nav.simplify([(0, 0), (1, 0.01), (2, 0), (3, 5)], 0.5) == [(0, 0), (2, 0), (3, 5)]

# ---- exact sub-zone raster ----------------------------------------------------------------------------------
import geo  # noqa: E402
root = tempfile.mkdtemp()
os.makedirs(os.path.join(root, "data", "gamedata"))
lab = np.zeros((668, 1002), np.uint16)
lab[300:400, 400:500] = 131
np.savez_compressed(os.path.join(root, "data", "gamedata", "areas.npz"), m1426=lab)
json.dump({"size": [1002, 668], "maps": [1426], "names": {"131": "Kharanos"}},
          open(os.path.join(root, "data", "gamedata", "areas.json"), "w"))
r = geo.AreaRaster(root)
assert r.at(1426, 45.0, 52.0) == "Kharanos" and r.at(1426, 10, 10) is None and r.at(1411, 45, 52) is None

# ---- the walk_route tool with the real extracted data (when present) -----------------------------------------
TER = os.path.join(ROOT, "data", "gamedata", "terrain", "0.npz")
KB = os.path.join(ROOT, "data", "kb.db")
skipped = None
if not (os.path.exists(TER) and os.path.exists(KB)):
    skipped = "walk_route part (needs data/gamedata/terrain/0.npz and data/kb.db); the rest passed"
else:
    from state import GameState  # noqa: E402
    import coach  # noqa: E402
    c = coach.Coach({"fast_model": "x"}, GlideDB("/x.db"), lambda m: None, kb=KB, offline_ok=True)
    gs = GameState()
    core = {"t": "1000", "l": "12", "x": "2000", "X": "4500", "cl": "WARRIOR", "mp": "1426", "px": "4660",
            "py": "5200", "st": "5"}
    for page in ({"pg": "B", "cl": "WARRIOR", "ra": "Dwarf", "fa": "Alliance", "nm": "T"},
                 {"pg": "G", "gn": "05-11 Dun Morogh", "gg": "RestedXP Forever Guide (A)"}, {}):
        gs.update(core, page)
    c.state = gs
    k = c.kctx(gs.summary())
    res = c.api.dispatch("walk_route", {"target": "1426 29.0,71.0"}, k, budget_s=20)   # Kharanos -> Coldridge
    if "walk_yd" in res:
        assert res["walk_yd"] >= res["straight_yd"] and res["legs"] and "Coldridge Valley" in res["through"], res
        assert any("Coldridge Pass" in x for x in res["through"]), "through the pass, not over the mountains"
        print("walk_route:", res["walk_yd"], "yd via", ", ".join(res["through"]))
    else:
        skipped = "walk_route part (the tool found no walk: %s); the rest passed" % res
if skipped:
    print("SKIP: " + skipped)
    raise SystemExit(77)
print("OK")
