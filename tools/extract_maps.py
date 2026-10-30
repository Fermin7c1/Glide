#!/usr/bin/env python3
"""Extract the game's map data from the local client (read-only) into data/gamedata/ and data/db2/.

    python tools/extract_maps.py [--wow "D:\\Blizzard\\World of Warcraft"] [--product wow_classic_beta]
                                 [--only db2,images,areas,terrain] [--force]

Stages (each skipped when its output is already from this client build, unless --force):
  db2      the map tables (AreaTable, Map, UiMap, UiMapAssignment, TaxiNodes/Path/PathNode, AreaPOI, world map
           art and overlays) -> data/gamedata/db2/*.db2 and data/db2/*.csv (imported by build_kb's db2 loader:
           real map bounds, area names and level ranges, flight masters and flight costs/times)
  images   every zone and continent map, the base art with all explored-area overlays drawn on it
           -> data/gamedata/maps/<uiMapID>.png (1002 x 668, the in-game world map's own pixel space)
  areas    the exact sub-zone shapes, from the explored-area overlay textures' alpha
           -> data/gamedata/areas.npz (one uint16 AreaTable-ID raster per uiMap, 1002 x 668) + areas.json
  terrain  height, water and walkability for the open-world continents from the terrain tiles (ADT)
           -> data/gamedata/terrain/<map>.npz (see wowfiles/terrain.py)
  spells   which icon each spell shows (SpellMisc) -> data/gamedata/spell_icons.json, for the dashboard's buff
           icons (companion/icons.py, which decodes each icon on first use into data/gamedata/icons/). Read with
           the .dbd that knows the client's layout (see dbd_text: bundled, else fetched from WoWDBDefs); failing
           that, the table is read unnamed and the columns are found from long-standing buffs whose icons never change

Writes data/gamedata/manifest.json (client version, stages done, counts). Some Forever content is encrypted
with keys the client gets from the server; those rows/tiles are reported and left out.
"""
import argparse
import csv
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from wowfiles import casc  # noqa: E402
from wowfiles.db2 import read_db2  # noqa: E402
from wowfiles.dbdefs import dbd_text  # noqa: E402
from wowfiles.spells import SPELL_ICON_ANCHORS, SPELLMISC_FDID, spell_icons  # noqa: E402

OUT = os.path.join(ROOT, "data", "gamedata")
DB2_FDID = {"AreaTable": 1353545, "Map": 1349477, "UiMap": 1957206, "UiMapAssignment": 1957219,
            "UiMapArt": 1957202, "UiMapArtTile": 1957210, "UiMapXMapArt": 1957217, "UiMapArtStyleLayer": 1957208,
            "WorldMapOverlay": 1134579, "WorldMapOverlayTile": 1957212, "TaxiNodes": 1068100,
            "TaxiPath": 1067802, "TaxiPathNode": 1000437, "AreaPOI": 1000630}
CSV_TABLES = ("AreaTable", "UiMap", "UiMapAssignment", "TaxiNodes", "TaxiPath", "TaxiPathNode", "AreaPOI")


def log(*a):
    print("[maps]", *a, flush=True)


def client_locale(wow_root, flavor_dir=None, product=None):
    """The client's text locale (deDE, frFR...): Config.wtf "SET textLocale" in the flavour folder, else the locale
    tag of the product's row in .build.info, else enUS. CASC keeps per-language copies of the map tables."""
    import re
    if flavor_dir:
        try:
            m = re.search(r'SET textLocale "(\w{4})"', open(os.path.join(flavor_dir, "WTF", "Config.wtf"),
                                                            encoding="utf-8", errors="replace").read())
            if m and m.group(1) in casc.LOCALE_MASK:
                return m.group(1)
        except OSError:
            pass
    try:
        lines = open(os.path.join(wow_root, ".build.info"), encoding="utf-8").read().splitlines()
        head = [h.split("!")[0] for h in lines[0].split("|")]
        for ln in lines[1:]:
            row = dict(zip(head, ln.split("|")))
            if product and row.get("Product") != product:
                continue
            for code in re.findall(r"\b([a-z]{2}[A-Z]{2})\?", row.get("Tags", "")):
                if code in casc.LOCALE_MASK:
                    return code
    except (OSError, IndexError):
        pass
    return "enUS"


def wow_dir_default():
    """(World of Warcraft folder, product) from config.json's wow_dir (which names the game flavour folder,
    e.g. ...\\World of Warcraft\\_classic_beta_, whose .flavor.info says the product)."""
    if os.path.join(ROOT, "companion") not in sys.path:
        sys.path.append(os.path.join(ROOT, "companion"))
    import glideconfig
    return casc.install_of(glideconfig.wow_dir(ROOT))


class Extractor:
    def __init__(self, wow, product, force=False, locale="enUS"):
        casc.load_default_keys()
        t = time.time()
        self.c = casc.Casc(wow, product, locale_mask=casc.LOCALE_MASK.get(locale, 0x2))
        log("client %s (%s): %d files (%.1fs)" % (self.c.version, locale, len(self.c.fdid), time.time() - t))
        self.force = force
        os.makedirs(OUT, exist_ok=True)
        self.mpath = os.path.join(OUT, "manifest.json")
        try:
            self.manifest = json.load(open(self.mpath, encoding="utf-8"))
        except (OSError, ValueError):
            self.manifest = {}
        if self.manifest.get("version") != self.c.version:
            self.manifest = {"version": self.c.version, "stages": {}}
        self.tables = {}

    def done(self, stage):
        return not self.force and stage in self.manifest.get("stages", {})

    def mark(self, stage, info):
        self.manifest.setdefault("stages", {})[stage] = dict(info, t=time.strftime("%Y-%m-%d %H:%M:%S"))
        with open(self.mpath, "w", encoding="utf-8") as fh:
            json.dump(self.manifest, fh, indent=1)

    def table(self, name):
        if name not in self.tables:
            p = os.path.join(OUT, "db2", name + ".db2")
            data = open(p, "rb").read() if os.path.exists(p) and self.done("db2") else self.c.read_fdid(DB2_FDID[name])
            self.tables[name] = read_db2(data, dbd_text(name, data))
        return self.tables[name]

    # ---- spells -----------------------------------------------------------------------------------
    def stage_spells(self):
        """The spell -> icon map for the buff icons; a new client build also clears the decoded icons."""
        import shutil
        data = self.c.read_fdid(SPELLMISC_FDID)
        os.makedirs(os.path.join(OUT, "db2"), exist_ok=True)
        with open(os.path.join(OUT, "db2", "SpellMisc.db2"), "wb") as fh:     # kept, like the map tables
            fh.write(data)
        icons, how, hit = spell_icons(data)
        tmp = os.path.join(OUT, "spell_icons.json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": self.c.version, "how": how, "icons": icons}, fh, separators=(",", ":"))
        os.replace(tmp, os.path.join(OUT, "spell_icons.json"))
        shutil.rmtree(os.path.join(OUT, "icons"), ignore_errors=True)
        log("spells: %d spell icons (%s, %d of %d known buffs check out)" % (len(icons), how, hit, len(SPELL_ICON_ANCHORS)))
        self.mark("spells", {"spells": len(icons), "how": how, "anchors": hit})

    # ---- db2 --------------------------------------------------------------------------------------
    def stage_db2(self):
        os.makedirs(os.path.join(OUT, "db2"), exist_ok=True)
        csv_dir = os.path.join(ROOT, "data", "db2")
        os.makedirs(csv_dir, exist_ok=True)
        counts, missing = {}, {}
        for name, fid in DB2_FDID.items():
            casc.MISSING.clear()
            data = self.c.read_fdid(fid)
            with open(os.path.join(OUT, "db2", name + ".db2"), "wb") as fh:
                fh.write(data)
            rows = read_db2(data, dbd_text(name, data))
            self.tables[name] = rows
            counts[name] = len(rows)
            if casc.MISSING:
                missing[name] = sorted(casc.MISSING)
            if name in CSV_TABLES:
                _write_csv(os.path.join(csv_dir, name + ".csv"), rows)
        log("db2: %s" % ", ".join("%s %d" % kv for kv in counts.items()))
        if missing:
            log("db2: parts encrypted with keys this client doesn't have (left out): %s" % ", ".join(missing))
        self.mark("db2", {"rows": counts, "encrypted": missing})

    # ---- images + areas ---------------------------------------------------------------------------
    def _art(self):
        """uiMap -> (art id, layer width, height, tile w, tile h)."""
        style = {r["ID"]: r for r in self.table("UiMapArtStyleLayer") if r.get("LayerIndex", 0) == 0}
        art = {r["ID"]: r for r in self.table("UiMapArt")}
        out = {}
        for x in self.table("UiMapXMapArt"):
            a = art.get(x["UiMapArtID"])
            if a is None or x.get("PhaseID"):
                continue
            st = style.get(a["UiMapArtStyleID"]) or next(iter(style.values()))
            out[x["UiMapID"]] = (x["UiMapArtID"], st["LayerWidth"], st["LayerHeight"], st["TileWidth"],
                                 st["TileHeight"])
        return out

    def _tiles(self, rows, key, keyval, tw, th, w, h):
        import numpy as np
        from wowfiles.blp import decode_blp
        img = np.zeros((h + th, w + tw, 4), np.uint8)
        bad = 0
        for r in rows:
            if r[key] != keyval or r.get("LayerIndex", 0) != 0:
                continue
            try:
                t = decode_blp(self.c.read_fdid(r["FileDataID"]))
            except Exception:  # noqa: BLE001 - a missing / encrypted tile leaves a hole
                bad += 1
                continue
            y, x = r["RowIndex"] * th, r["ColIndex"] * tw
            img[y:y + t.shape[0], x:x + t.shape[1]] = t[:img.shape[0] - y, :img.shape[1] - x]
        return img[:h, :w], bad

    def stage_images_areas(self, images=True, areas=True):
        import numpy as np
        from wowfiles.blp import write_png
        arts = self._art()
        tiles = self.table("UiMapArtTile")
        ov = self.table("WorldMapOverlay")
        ovt = self.table("WorldMapOverlayTile")
        uimaps = {r["ID"]: r for r in self.table("UiMap")}
        names = {r["ID"]: r.get("AreaName_lang") for r in self.table("AreaTable")}
        os.makedirs(os.path.join(OUT, "maps"), exist_ok=True)
        rasters, legend, n_img, holes = {}, {}, 0, 0
        for ui, (art, w, h, tw, th) in sorted(arts.items()):
            um = uimaps.get(ui)
            if um is None or um.get("Type") not in (2, 3, 4, 6):      # continent, zone, dungeon, micro
                continue
            base, bad = self._tiles(tiles, "UiMapArtID", art, tw, th, w, h)
            holes += bad
            label = np.zeros((h, w), np.uint16)
            mine = sorted([o for o in ov if o["UiMapArtID"] == art],
                          key=lambda o: -(o["TextureWidth"] * o["TextureHeight"]))     # big first, sub-areas on top
            for o in mine:
                ow, oh = o["TextureWidth"], o["TextureHeight"]
                tex, bad = self._tiles(ovt, "WorldMapOverlayID", o["ID"], 256, 256, ow, oh)
                holes += bad
                x0, y0 = o["OffsetX"], o["OffsetY"]
                x1, y1 = min(w, x0 + ow), min(h, y0 + oh)
                if x1 <= x0 or y1 <= y0:
                    continue
                t = tex[:y1 - y0, :x1 - x0]
                a = t[..., 3:4].astype(np.uint16)
                if images:
                    region = base[y0:y1, x0:x1].astype(np.uint16)
                    base[y0:y1, x0:x1, :3] = ((t[..., :3] * a + region[..., :3] * (255 - a)) // 255).astype(np.uint8)
                    base[y0:y1, x0:x1, 3] = np.maximum(base[y0:y1, x0:x1, 3], t[..., 3])
                aid = next((x for x in (o.get("AreaID") or []) if x), 0)
                if aid and areas:
                    label[y0:y1, x0:x1][t[..., 3] > 96] = aid
                    legend[aid] = names.get(aid) or legend.get(aid)
            if images:
                write_png(os.path.join(OUT, "maps", "%d.png" % ui), base)
                n_img += 1
            if areas and label.any():
                rasters["m%d" % ui] = label
        if areas:
            np.savez_compressed(os.path.join(OUT, "areas.npz"), **rasters)
            with open(os.path.join(OUT, "areas.json"), "w", encoding="utf-8") as fh:
                json.dump({"size": [1002, 668], "maps": sorted(int(k[1:]) for k in rasters),
                           "names": {str(k): v for k, v in legend.items()}}, fh, indent=1)
        log("images: %d maps -> data/gamedata/maps | areas: %d maps, %d sub-zones | %d tiles missing" % (
            n_img, len(rasters), len(legend), holes))
        if images:
            self.mark("images", {"maps": n_img, "missing_tiles": holes})
        if areas:
            self.mark("areas", {"maps": len(rasters), "areas": len(legend)})

    def stage_terrain(self):
        from wowfiles import terrain
        info = terrain.extract_all(self, os.path.join(OUT, "terrain"), log=log)
        self.mark("terrain", info)


def _write_csv(path, rows):
    if not rows:
        return
    cols = []
    for r in rows:
        for k, v in r.items():
            names = ["%s_%d" % (k, i) for i in range(len(v))] if isinstance(v, list) else [k]
            for n in names:
                if n not in cols:
                    cols.append(n)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(cols)
        for r in rows:
            flat = {}
            for k, v in r.items():
                if isinstance(v, list):
                    for i, x in enumerate(v):
                        flat["%s_%d" % (k, i)] = x
                else:
                    flat[k] = v
            wr.writerow([flat.get(c, "") for c in cols])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wow", default=None)
    ap.add_argument("--product", default=None, help="default: from the flavour folder, else wow_classic_beta")
    ap.add_argument("--only", default="db2,images,areas,terrain,spells")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    wow, product = wow_dir_default()
    wow = a.wow or wow
    if not wow or not os.path.exists(os.path.join(wow, ".build.info")):
        log("World of Warcraft folder not found (%s): pass --wow or set wow_dir in config.json" % wow)
        return 2
    import importlib.util
    if importlib.util.find_spec("numpy") is None:
        log("numpy is needed for the map images and terrain: py -3 -m pip install numpy")
        return 3
    only = set(a.only.split(","))
    product = a.product or product or "wow_classic_beta"
    import glideconfig                  # on sys.path: wow_dir_default() put it there
    flavor = glideconfig.wow_dir(ROOT)
    ex = Extractor(wow, product, a.force, locale=client_locale(wow, flavor, product))
    t0 = time.time()
    if "db2" in only and not ex.done("db2"):
        ex.stage_db2()
    want_img = "images" in only and not ex.done("images")
    want_area = "areas" in only and not ex.done("areas")
    if want_img or want_area:
        ex.stage_images_areas(want_img, want_area)
    if "terrain" in only and not ex.done("terrain"):
        ex.stage_terrain()
    if "spells" in only and not ex.done("spells"):
        try:
            ex.stage_spells()
        except Exception as e:  # noqa: BLE001 - the buff icons fall back to letters; the Status page says so
            log("spells: the spell table couldn't be read (%s); buff icons show letters" % e)
    log("done in %.0fs (client %s)" % (time.time() - t0, ex.c.version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
