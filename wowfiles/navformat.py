"""The walking map's file format (data/gamedata/nav/<map>.npz), shared by its writer (tools/navmesh.py with
wowfiles/navbuild.py) and its reader (knowledge/mechanics/nav.py). No numpy here: the engine reads a map's version
(kb_refresh.NavBuilder) without loading the builder.

The output per map is a table of nodes: tiles in slots, each tile's cells row by row, each cell's nodes bottom up:
  tidx  (TH, TW)      int32   the slot of tile (ty0 + i, tx0 + j), -1 = none
  tcnt  (S, 64, 64)   uint8   nodes in each cell       tcum (S, 64, 64) uint16  nodes before it in its tile
  tbase (S+1,)        int64   nodes before each slot   tarea (S, 64, 64) uint16 AreaTable ID
  nz    (N,)    int16    height, half yards (NONE = no node)
  nlink (N,)    uint32   per direction d (0..7: N NE E SE S SW W NW, DIRS) bit d = an edge, and bits 8+2d..9+2d =
                         the target node's layer in that neighbour cell; bits 24..27 = an edge to layer 0..3 of the
                         same cell (a plateau's rim down onto the slope below it)
  nkind (N,)    uint8    1 walk, 2 swim, 3 steep (a slope too steep to walk up: hopped onto, slid down)
  ncxy  (N,)    uint8    the patch's centre inside its cell, fine units (low nibble = column, high = row)
  nreach (N,)   uint8    8 x log2 of the ground it's connected to both ways (the street, not the rooftop over it)
plus map, ty0, tx0, cell (8.333), build (the client version), nav_version (NAV_VERSION of the builder) and tiles
(the tiles built). Row r / column c is tile (ty0 + r // 64, tx0 + c // 64), like the terrain grid.

Two versions (see tools/navmesh.py for when a map is built):
  NAV_VERSION   the builder's. Raise it whenever the builder improves: players whose map is older see a "rebuild
                for better routes" note in Settings and on the Status page, and rebuild when they choose.
  NAV_REQUIRED  the oldest map the route planner accepts. Raising it rebuilds every install once, by itself; it's
                raised only when a rebuild has to be forced (decided at development time, never automatically).
"""
import ast
import os
import struct
import zipfile

TILE = 1600.0 / 3.0           # a map tile (ADT), yards
CELLS = 64                    # coarse cells per tile side (8.333 yd each)
BLOCK = 8                     # fine cells per coarse cell side (1.042 yd each)
LAYERS = 4                    # nodes (walkable patches) per coarse cell
NONE = -32768                 # nz of a missing node
DIRS = ((-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1))   # N NE E SE S SW W NW (row = south)

NAV_VERSION = 1               # the builder's version: raise it when it improves (players are told, not forced)
NAV_REQUIRED = 1              # the oldest map accepted: raising it forces every install to rebuild once


def path(root, map_id):
    return os.path.join(root, "data", "gamedata", "nav", "%d.npz" % int(map_id))


def read_version(p):
    """The NAV_VERSION a map file was built with, or None when it's missing or unreadable (0 before versions).
    Reads the one scalar straight from the .npz (a zip of .npy files) so it needs no numpy."""
    try:
        with zipfile.ZipFile(p) as z:
            if "nav_version.npy" not in z.namelist():
                return 0
            raw = z.read("nav_version.npy")
        if raw[:6] != b"\x93NUMPY":
            return None
        if raw[6] == 1:
            n, start = struct.unpack_from("<H", raw, 8)[0], 10
        else:
            n, start = struct.unpack_from("<I", raw, 8)[0], 12
        head = ast.literal_eval(raw[start:start + n].decode("latin-1"))
        fmt = {"<i4": "<i", "<i8": "<q", "<u4": "<I", "<i2": "<h", "|u1": "<B", "|i1": "<b"}.get(head.get("descr"))
        if fmt is None or head.get("shape") not in ((), (1,)):
            return None
        return int(struct.unpack_from(fmt, raw, start + n)[0])
    except Exception:  # noqa: BLE001 - a broken or half-written file: as if missing
        return None


def current(p):
    """Is this map's walking map there and new enough to use (NAV_REQUIRED)? Else it's built by itself."""
    v = read_version(p)
    return v is not None and v >= NAV_REQUIRED


def outdated(p):
    """Built by an older builder than this one (NAV_VERSION), though still usable: worth a rebuild, not forced."""
    v = read_version(p)
    return v is not None and NAV_REQUIRED <= v < NAV_VERSION
