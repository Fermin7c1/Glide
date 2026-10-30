#!/usr/bin/env python3
"""Walkable-space graph of the open-world maps, from the client's own 3D data -> data/gamedata/nav/<map>.npz.

What the old terrain grid (wowfiles/terrain.py, 8.3 yd cells) couldn't know, this one reads:
  - the terrain's full triangle mesh: every quad is 4 triangles around its centre vertex (145 heights per chunk),
    sampled on a 1.04 yd lattice, with the exact slope of the triangle under each sample;
  - every collidable triangle of every building, city, cave, tunnel, bridge and wall (WMO groups, MPY2/MOPY
    collision flags), and of every doodad with a collision mesh (trees, rocks, fences, carts: M2 bounding
    triangles), including the doodads a WMO places itself;
  - water (MH2O): deep water is swum at its surface.

Movement rules, measured on recorded play (tests/test_navmesh.py documents the calibration):
  - a surface is walkable up to 50 degrees (sustained climbs on recorded runs top out at 47.5; none over 50);
  - a character hops up to JUMP (1.5 yd) over anything: curbs, steps, short steep bumps, the edge of a ledge;
  - drops of up to SAFE_DROP (8 yd) are taken, one way;
  - standing needs HEIGHT (2 yd) of free space above the feet.

Per tile (533 yd) the builder makes a layered fine grid (512 x 512 cells of 1.04 yd, up to 6 standable surfaces
per cell: ground, bridge, upper floor, tunnel...), links neighbouring surfaces by those rules, and condenses it
for the route planner (knowledge/mechanics/nav.py) into one node per connected walkable patch of each 8.33 yd cell
(64 x 64 cells per tile, up to 4 nodes per cell; when a cell has more, the ones joined to the most ground are
kept). The map file's layout and its two versions are in wowfiles/navformat.py.

It runs once on the player's PC, in the background (kb_refresh: idle priority, one thread, a pause after each tile
in proportion to its work) and resumes where it stopped: finished tiles are kept in data/gamedata/nav/<map>.part/.
The world's geometry hardly changes between game patches, so game updates never trigger a build. A map is built
by itself only when it's missing (a fresh install) or older than NAV_REQUIRED (wowfiles/navformat.py). Settings >
General > Walking map has a button that rebuilds it (--force).

    python tools/navmesh.py [--maps 0,1] [--force] [--throttle 0.3]
"""
import json
import os
import sys
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import numpy as np  # noqa: E402

from wowfiles import navformat  # noqa: E402
from wowfiles.adt import wdt_tiles  # noqa: E402
from wowfiles.navbuild import BLOCK, F, TileSource, build_tile, pack  # noqa: E402
from wowfiles.worldobjects import Models, read_placements  # noqa: E402


# ================================================================================================ one map
def build_map(casc, map_id, wdt_fid, out_dir, version, log=print, throttle=0.0, only_tiles=None, fresh=False):
    """Build data/gamedata/nav/<map>.npz, resuming from <map>.part/ (one .npz per finished tile) unless fresh."""
    t0 = time.time()
    try:
        tiles = wdt_tiles(casc.read_fdid(wdt_fid))
    except Exception as e:  # noqa: BLE001
        log("nav: map %s: no WDT (%s)" % (map_id, e))
        return None
    if not tiles:
        return None
    part = os.path.join(out_dir, "%d.part" % map_id)
    os.makedirs(part, exist_ok=True)
    stamp = os.path.join(part, "nav_version.txt")
    if fresh or not os.path.exists(stamp) or open(stamp).read().strip() != str(navformat.NAV_VERSION):
        for f in os.listdir(part):
            os.remove(os.path.join(part, f))
        with open(stamp, "w") as fh:
            fh.write(str(navformat.NAV_VERSION))
    models = Models(casc)
    placements = read_placements(casc, tiles)
    src = TileSource(casc, tiles, models, placements)
    todo = sorted(tiles) if only_tiles is None else [t for t in sorted(tiles) if t in only_tiles]
    done = bad = 0
    for n, (ty, tx) in enumerate(todo):
        p = os.path.join(part, "%d_%d.npz" % (ty, tx))
        if os.path.exists(p):
            done += 1
            continue
        t1 = time.time()
        try:
            r = build_tile(src, ty, tx)
        except Exception as e:  # noqa: BLE001 - one bad tile doesn't stop the map
            log("nav: map %s tile %d,%d failed: %s" % (map_id, ty, tx, e))
            r = None
        if r is None:
            bad += 1
            np.savez_compressed(p, empty=np.int8(1))
            continue
        np.savez_compressed(p, **{k: v for k, v in r.items() if k != "objects"})
        done += 1
        if n % 5 == 0 or n == len(todo) - 1:
            log("nav: map %s: %d/%d tiles (%.1fs for %d,%d with %d object triangles)" % (
                map_id, n + 1, len(todo), time.time() - t1, ty, tx, r["objects"]))
        if throttle:
            time.sleep(throttle * (time.time() - t1))        # leave the CPU to the game: pause in proportion
    # assemble the node table (module docstring)
    ty0 = min(t[0] for t in tiles); tx0 = min(t[1] for t in tiles)
    TH = max(t[0] for t in tiles) - ty0 + 1
    TW = max(t[1] for t in tiles) - tx0 + 1
    have = {}
    for (ty, tx) in sorted(tiles):
        p = os.path.join(part, "%d_%d.npz" % (ty, tx))
        if not os.path.exists(p):
            continue
        with np.load(p) as z:                       # closed at once: the tile files are deleted below
            if "empty" in z.files:
                continue
            have[(ty, tx)] = {k: z[k] for k in ("z", "link", "kind", "cxy", "area")}
    out = pack(have, ty0, tx0, TH, TW)
    built = [ty * 64 + tx for ty, tx in sorted(have)]
    path = os.path.join(out_dir, "%d.npz" % map_id)
    tmp = path + ".tmp.npz"
    np.savez_compressed(tmp, map=np.int32(map_id), ty0=np.int32(ty0), tx0=np.int32(tx0), cell=np.float32(F * BLOCK),
                        build=np.array(version), nav_version=np.int32(navformat.NAV_VERSION), tiles=np.array(built, np.int32),
                        **out)
    os.replace(tmp, path)
    for f in os.listdir(part):
        os.remove(os.path.join(part, f))
    os.rmdir(part)
    log("nav: map %s: %d tiles (%d unreadable), %d models unreadable, %.0fs" % (
        map_id, len(built), bad, models.bad, time.time() - t0))
    return {"tiles": len(built), "unreadable": bad}


def main(argv=None):
    import argparse
    from extract_maps import Extractor, wow_dir_default, OUT      # tools/, beside this script
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wow")
    ap.add_argument("--product")
    ap.add_argument("--maps", help="comma separated map ids (default: every open-world map)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--first", type=int, help="build this map first (the player's continent)")
    ap.add_argument("--throttle", type=float, default=0.3, help="pause this many seconds per second of work")
    a = ap.parse_args(argv)
    wow, product = wow_dir_default()
    ex = Extractor(a.wow or wow, a.product or product)
    out_dir = os.path.join(OUT, "nav")
    os.makedirs(out_dir, exist_ok=True)
    want = [int(x) for x in a.maps.split(",")] if a.maps else None
    info = {}
    rows = [m for m in ex.table("Map") if m.get("InstanceType") == 0 and m.get("MapType") == 1
            and m.get("WdtFileDataID") and (not want or m["ID"] in want)]
    # the order asked for (the player's continent first), else the game's
    rows.sort(key=lambda m: (m["ID"] != a.first, want.index(m["ID"]) if want else 0, m["ID"]))
    for m in rows:
        if not a.force and navformat.current(os.path.join(out_dir, "%d.npz" % m["ID"])):
            continue
        r = build_map(ex.c, m["ID"], m["WdtFileDataID"], out_dir, ex.c.version,
                      log=lambda *x: print("[nav]", *x, flush=True), throttle=a.throttle, fresh=a.force)
        if r:
            info[str(m["ID"])] = r
    print("[nav] done", json.dumps(info), flush=True)


if __name__ == "__main__":
    main()
