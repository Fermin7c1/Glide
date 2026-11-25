"""Loader: dungeon and raid entrances -> instance_entrance, one row per instance with its door on the outdoor zone
(or city) map in map percent: where the pursuit's map pin and "go to <instance>" point.

Sources:

  Map.db2, the client's instance list: per instance its InstanceType (1 party, 2 raid), MaxPlayers, AreaTableID and
      CorpseMapID + Corpse[x, y], the world position (Corpse_0 = world X, north; Corpse_1 = world Y, west: store.py's
      axes) where the corpse of a player who died inside is put, outside the instance's door. Read from
      data/db2/Map.csv (a wow.tools export, like the other DB2 tables) or else data/gamedata/db2/Map.db2 (kept by
      tools/extract_maps.py; read with tools/dbd/Map.dbd or the definition extract_maps fetched into
      data/gamedata/dbd, never fetched here). Converted through map_bounds (loaded before) onto the zone or city map
      whose rectangle holds the point (zone rectangles overlap at their edges; Zones.place says how one is chosen:
      an outdoor area of the instance's name, a city's own box, sub-zone boxes, spawns near the point, depth).
      src 'db2', conf DB2_CONF x the bounds' conf: the corpse point is not always the door (Sunken Temple's is
      ~300 yd from it, Blackrock Depths' lies off the mountain).
      AreaTableID is 0 for some instances (Scholomance, Stratholme, the Deadmines...): their area id is then the
      kb.area row of the same name that isn't an outdoor zone's (2057 Scholomance, 2017 Stratholme).
  OVERRIDES: hand-checked doors of the level-55+ dungeons and the raids, and of Sunken Temple, whose corpse point is
      off (src 'hand', conf HAND_CONF). They win over the conversion, and are written without Map.db2 too.
      check_overrides() measures each against its converted Corpse point (in the loader result; a note when one is
      beyond its tolerance), and tests/kb/test_entrances.py does the same on data/kb.db wherever the client's Map
      table is there.

AreaPOI is not used: few dungeons have a marker there and some markers sit on the wrong map (the ones that exist were
one of the checks of the overrides). Instances without a corpse position (the Forever-only ones: The Hall of Thanes,
Manor Mistmantle...) get no row unless an override names them.

aliases (json, normalised by norm()): the client's and the kb's names, RestedXP's dungeon short names (rxp_meta
dungeonList: "BRD", "LBRS", "SCHOLO"...) and the hand ones in ALIASES ("UBRS", "MC", "Dire Maul North"...);
knowledge.api.instance_entrance answers to all of them.

Staleness: build_kb rebuilds when data/db2/*.csv change, and tools/extract_maps.py rewrites those together with
Map.db2, so Map.db2 is not an input of its own.
"""
import collections
import math
import os
import re

from knowledge import store
from knowledge.values import as_float, as_int

KIND = {1: "party", 2: "raid"}         # Map.db2 InstanceType (0 world, 3 battleground, 4 arena)
HAND_CONF = 0.9
DB2_CONF = 0.7
NEAR_YD = 200.0                        # "spawns near the point" when choosing between overlapping zone maps

# Hand-checked doors: (area_id, name, kind, ui_map, x %, y %, tolerance yd, evidence). The tolerance is how far the
# client's converted Corpse point may lie from the door (tests/kb/test_entrances.py, check_overrides). All Blackrock
# Mountain instances go on Burning Steppes (1428), whose 'Blackrock Mountain' sub-zone box holds the mountain.
OVERRIDES = (
    (2057, "Scholomance", "party", 1422, 69.7, 73.4, 100,
     "AreaPOI 710 'Scholomance' (the client's own marker) 69.7,73.4; Corpse 69.1,73.0; Caer Darrow, where Magistrate "
     "Marduke stands at 70.6,74.1"),
    (2017, "Stratholme", "party", 1423, 26.1, 10.4, 100,
     "Corpse 26.1,10.4, in the Stratholme sub-zone box (24.5-37.9, 5.2-25.5) where outdoor spawns stand at the gate"),
    (1583, "Blackrock Spire", "party", 1428, 33.0, 25.2, 100,
     "Corpse 33.0,25.2 inside the Blackrock Mountain box; Lower and Upper Blackrock Spire share the door"),
    (1584, "Blackrock Depths", "party", 1428, 29.0, 28.9, 500,
     "Franclorn Forgewright's spot (Questie) 29.0,28.9 = RestedXP's 'near the Molten Core entrance' goto, inside the "
     "mountain; the client's Corpse point lies ~460 yd north, outside the mountain box (Searing Gorge 27.2,72.5)"),
    (2717, "Molten Core", "raid", 1428, 26.3, 24.6, 100,
     "Corpse 26.3,24.6, where Lothos Riftwaker (who ports attuned players in) stands: Questie Searing Gorge 32.1,94.7"),
    (2677, "Blackwing Lair", "raid", 1428, 32.5, 32.4, 100,
     "Corpse 32.5,32.4 inside the Blackrock Mountain box (reached through Upper Blackrock Spire)"),
    (2159, "Onyxia's Lair", "raid", 1445, 52.6, 76.8, 100,
     "between AreaPOI 1094 52.3,76.1 and the Corpse 52.9,77.7, in the Wyrmbog box"),
    (1977, "Zul'Gurub", "raid", 1434, 53.9, 17.6, 100, "Corpse 54.0,17.6"),
    (2557, "Dire Maul", "party", 1444, 62.0, 33.3, 100, "Corpse 62.0,33.3, the courtyard between the three wings"),
    (3429, "Ruins of Ahn'Qiraj", "raid", 1451, 29.0, 92.8, 100, "Corpse 29.0,92.8, at the Scarab Wall"),
    (3428, "Ahn'Qiraj Temple", "raid", 1451, 29.0, 92.7, 100, "Corpse 29.0,92.7, at the Scarab Wall"),
    (1477, "Sunken Temple", "party", 1435, 70.0, 53.0, 350,
     "AreaPOI 1013 'Temple of Atal'Hakkar' 70.0,52.9 = RestedXP's 'Zone into Sunken Temple' goto 70.0,53.2; the "
     "client's Corpse point lies ~310 yd northeast (77.3,35.9)"),
)

# Names players use for them (normalised on use). A wing answers with its instance's door.
ALIASES = {
    2057: ("scholo",),
    2017: ("strat", "strat live", "strat ud", "strat undead", "stratholme live", "stratholme undead"),
    1583: ("brs", "lbrs", "ubrs", "lower blackrock spire", "upper blackrock spire", "lower spire", "upper spire"),
    1584: ("brd",),
    2717: ("mc",),
    2677: ("bwl",),
    2159: ("ony", "onyxia", "onyxias lair"),
    1977: ("zg",),
    2557: ("dme", "dmn", "dmw", "dm east", "dm north", "dm west", "dire maul east", "dire maul north",
           "dire maul west"),
    3429: ("aq20", "ruins of aq", "ruins of ahnqiraj"),
    3428: ("aq40", "aq", "ahnqiraj", "temple of ahnqiraj", "temple of aq"),
}


def norm(name):
    """'The Temple of Atal'Hakkar' -> 'temple of atalhakkar': lower case, apostrophes dropped, other punctuation a
    space, a leading 'the ' dropped. The form aliases are stored and looked up in."""
    s = re.sub("['`%s]" % chr(0x2019), "", str(name or "").lower())      # ' ` and the typographic apostrophe
    s = " ".join(re.sub(r"[^0-9a-z]+", " ", s).split())
    return s[4:] if s.startswith("the ") else s


def load(conn, ctx):
    zones = Zones(conn)
    raw, source = map_rows(ctx)
    res = {"map_table": store.rel_path(source) if raw else {"skipped": source}}
    area_name = {r[0]: r[1] for r in conn.execute("SELECT area_id, name FROM area WHERE name IS NOT NULL")}
    by_area, skipped = instances(raw, instance_areas(conn, zones.outdoor))
    rows = {}
    for aid, m in by_area.items():
        if m["cont"] is None or m["cont"] < 0 or m["wx"] is None or m["wy"] is None or (not m["wx"] and not m["wy"]):
            skipped["no corpse position"] += 1
            continue
        hit = zones.place(m["cont"], m["wx"], m["wy"], {norm(m["name"]), norm(area_name.get(aid))})
        if hit is None:
            skipped["no zone map holds it"] += 1
            continue
        um, x, y = hit
        b = zones.bounds[um]
        rows[aid] = {"area_id": aid, "ui_map": um, "x": round(x, 2), "y": round(y, 2), "src": "db2",
                     "instance": m["id"], "name": m["name"], "kind": KIND[m["type"]], "max_players": m["max"],
                     "continent": m["cont"], "wx": round(m["wx"], 1), "wy": round(m["wy"], 1),
                     "conf": round(DB2_CONF * (b.get("conf") or 0.5), 2)}
    res["converted"] = len(rows)
    for aid, name, kind, um, x, y, _tol, _why in OVERRIDES:
        m = by_area.get(aid) or {}
        b = zones.bounds.get(um)
        wx, wy = store.map_to_world(b, x, y) if b else (None, None)
        rows[aid] = {"area_id": aid, "ui_map": um, "x": x, "y": y, "src": "hand", "instance": m.get("id"),
                     "name": name, "kind": KIND.get(m.get("type") or 0, kind), "max_players": m.get("max"),
                     "continent": b.get("continent") if b else None,
                     "wx": round(wx, 1) if wx is not None else None, "wy": round(wy, 1) if wy is not None else None,
                     "conf": HAND_CONF}
    res["hand"] = len(OVERRIDES)
    aliases = claim_aliases(conn, rows, by_area, area_name)
    for aid, r in rows.items():
        r["aliases"] = store.jdump(sorted(a for a, t in aliases.items() if t == aid and a != norm(r["name"])))
    store.insert_rows(conn, "instance_entrance", list(rows.values()))
    res["rows"] = len(rows)
    res["skipped"] = dict(skipped)
    check = check_overrides(zones, by_area)
    res["override_check_yd"] = {c["name"]: c["yd"] for c in check.values()}
    far = ["%s %s yd (tolerance %d)" % (c["name"], c["yd"], c["tol"]) for c in check.values() if c["ok"] is False]
    if far:
        res["override_far"] = far
        ctx.notes.append("entrances: the client's corpse point is far from the hand-checked door: %s" % "; ".join(far))
    return res


# ------------------------------------------------------------------------------------------------
# the client's Map table
# ------------------------------------------------------------------------------------------------
def map_rows(ctx):
    """The client's instances: ([{"id", "name", "type", "max", "area", "cont", "wx", "wy"}], source path), or
    ([], why) when there is no readable Map table."""
    csv_path = _file_in(ctx.db2_dir, "Map.csv")
    if csv_path:
        from wowfiles import db2_import
        raw, source = db2_import.read_csv(csv_path), csv_path
    else:
        path = os.path.join(ctx.data, "gamedata", "db2", "Map.db2")
        if not os.path.exists(path):
            return [], "no Map table (data/db2/Map.csv, or data/gamedata/db2/Map.db2 from tools/extract_maps.py)"
        try:
            raw = read_map_db2(path, os.path.join(ctx.data, "gamedata", "dbd", "Map.dbd"))
        except Exception as e:  # noqa: BLE001 - a damaged or foreign file must not stop the build
            return [], "cannot read %s: %s: %s" % (store.rel_path(path), type(e).__name__, e)
        if raw is None:
            return [], "%s has a layout no local definition knows (tools/extract_maps.py fetches it)" % (
                store.rel_path(path))
        source = path
    out = []
    for r in raw:
        corpse = r.get("Corpse")
        wx, wy = (corpse[0], corpse[1]) if isinstance(corpse, (list, tuple)) and len(corpse) >= 2 else (
            r.get("Corpse_0"), r.get("Corpse_1"))
        name = r.get("MapName_lang") or r.get("MapName") or r.get("Directory")
        out.append({"id": as_int(r.get("ID"), loose=True), "name": str(name).strip() if name else None,
                    "type": as_int(r.get("InstanceType"), loose=True), "max": as_int(r.get("MaxPlayers"), loose=True),
                    "area": as_int(r.get("AreaTableID"), loose=True) or None,
                    "cont": as_int(r.get("CorpseMapID"), loose=True),
                    "wx": as_float(wx, finite=True), "wy": as_float(wy, finite=True)})
    return out, source


def read_map_db2(path, cached_dbd=None):
    """Map.db2's rows with the bundled tools/dbd/Map.dbd or `cached_dbd` (whichever knows the file's layout); None
    when neither does. Never fetches a definition."""
    from wowfiles.db2 import has_layout, layout_hash, read_db2
    from wowfiles.dbdefs import BUNDLED
    with open(path, "rb") as fh:
        data = fh.read()
    h = layout_hash(data)
    for dbd in (os.path.join(BUNDLED, "Map.dbd"), cached_dbd):
        if not dbd:
            continue
        try:
            with open(dbd, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        if has_layout(text, h):
            return read_db2(data, text)
    return None


def _file_in(folder, name):
    if not folder or not os.path.isdir(folder):
        return None
    for f in os.listdir(folder):
        if f.lower() == name.lower():
            return os.path.join(folder, f)
    return None


# ------------------------------------------------------------------------------------------------
# placing a world point on a zone map
# ------------------------------------------------------------------------------------------------
class Zones:
    """The zone and city maps with world bounds (two levels below the world in kb.map: world > continent > zone),
    their sub-zone boxes and the outdoor areas named on them."""

    def __init__(self, conn):
        self.conn = conn
        self.bounds = {r[0]: {"continent": r[1], "wx0": r[2], "wy0": r[3], "wx1": r[4], "wy1": r[5], "conf": r[6]}
                       for r in conn.execute("SELECT ui_map, continent, wx0, wy0, wx1, wy1, conf FROM map_bounds")}
        parent = {r[0]: r[1] for r in conn.execute("SELECT ui_map, parent FROM map")}
        depth = {}

        def level(um, seen=()):
            if um not in depth:
                p = parent.get(um)
                if not p:
                    depth[um] = 0
                elif p not in parent or p in seen:
                    depth[um] = 2                   # a parent the kb doesn't list: take it for a continent
                else:
                    depth[um] = level(p, seen + (um,)) + 1
            return depth[um]

        self.zones = {um for um in self.bounds if um in parent and level(um) >= 2}
        self.outdoor = set(self.bounds)
        self.map_of_area = {aid: um for um, aid in conn.execute("SELECT ui_map, area_id FROM map")
                            if um in self.zones and aid}
        self.boxes = collections.defaultdict(list)
        for um, aid, x1, y1, x2, y2 in conn.execute("SELECT ui_map, area_id, x1, y1, x2, y2 FROM area_box"):
            if um in self.zones and None not in (x1, y1, x2, y2):
                self.boxes[um].append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2), aid))
        self.named = collections.defaultdict(set)
        for name, um in conn.execute("SELECT name, ui_map FROM area WHERE name IS NOT NULL AND ui_map IS NOT NULL"):
            if um in self.zones:
                self.named[norm(name)].add(um)

    def spawns_near(self, um, x, y, yd=NEAR_YD):
        """Creature spawn points the kb has on map um within `yd` (a square) of (x, y): the client records a spawn on
        the map the player stands on, so spawns near the point say it is on that map's land."""
        w, h = store.map_size_yd(self.bounds[um])
        if not w or not h:
            return 0
        rx, ry = yd / w * 100.0, yd / h * 100.0
        return self.conn.execute("""SELECT COUNT(*) FROM creature_spawn WHERE ui_map=? AND x BETWEEN ? AND ?
                                    AND y BETWEEN ? AND ?""", (um, x - rx, x + rx, y - ry, y + ry)).fetchone()[0]

    def place(self, continent, wx, wy, names=()):
        """(ui_map, x %, y %) of the world point on the best zone map of that continent holding it, or None. Ranked:
        a map with an outdoor area of one of `names`; a map another one's sub-zone box hands the point to (Elwynn's
        'Stormwind City' box: the Stockade goes on the city's own map); a map with a sub-zone box around the point; a
        map with spawns near it (Scarlet Monastery: Tirisfal, not Western Plaguelands' overlapping corner); the map the
        point sits deepest inside."""
        cands = {}
        for um in self.zones:
            b = self.bounds[um]
            if b["continent"] != continent:
                continue
            x, y = store.world_to_map(b, wx, wy)
            if x is not None and y is not None and 0 <= x <= 100 and 0 <= y <= 100:
                cands[um] = (x, y, [bx[4] for bx in self.boxes.get(um, ())
                                    if bx[0] <= x <= bx[2] and bx[1] <= y <= bx[3]])
        handed = {self.map_of_area.get(aid) for _, _, hits in cands.values() for aid in hits}
        best = None
        for um, (x, y, hits) in cands.items():
            named = any(um in self.named.get(n, ()) for n in names if n)
            score = (named, um in handed, bool(hits), self.spawns_near(um, x, y) > 0, min(x, 100 - x, y, 100 - y), -um)
            if best is None or score > best[0]:
                best = (score, um, x, y)
        return best[1:] if best else None


def instances(raw, area_of):
    """({area id: Map row} of the client's dungeons and raids, the lowest Map ID winning an area; Counter of the rows
    left out and why). area_of: instance_areas(), for rows without an AreaTableID."""
    out, skipped = {}, collections.Counter()
    for m in sorted(raw, key=lambda r: r["id"] if r["id"] is not None else -1):
        if m["type"] not in KIND:
            continue                                           # the world, battlegrounds
        if not m["name"] or "unused" in m["name"].lower():
            skipped["unused"] += 1
            continue
        aid = m["area"] or area_of.get(norm(m["name"]))
        if not aid:
            skipped["no area id"] += 1
        elif aid in out:
            skipped["same area as another instance"] += 1
        else:
            out[aid] = m
    return out, skipped


def instance_areas(conn, outdoor):
    """{normalised name: area id} for naming an instance whose Map row has no AreaTableID: the kb.area row of that
    name that is not on an outdoor map (the instance's own), else one with a map, else the lowest id."""
    by = collections.defaultdict(list)
    for aid, name, um in conn.execute("SELECT area_id, name, ui_map FROM area WHERE name IS NOT NULL"):
        by[norm(name)].append((um in outdoor, um is None, aid))
    return {n: min(v)[2] for n, v in by.items() if n}


def claim_aliases(conn, rows, by_area, area_name):
    """{normalised alias: area id}, each alias to one instance, first claim wins: the hand ALIASES, the instances'
    own names (client and kb), then RestedXP's dungeon short names (their ids may be the outdoor area of the same
    name, e.g. ZF 978: resolved through that name)."""
    out = {}

    def claim(alias, aid):
        a = norm(alias)
        if a and aid in rows and a not in out:
            out[a] = aid

    for aid, names in ALIASES.items():
        for n in names:
            claim(n, aid)
    for aid, r in rows.items():
        claim(r["name"], aid)
        claim(area_name.get(aid), aid)
        claim((by_area.get(aid) or {}).get("name"), aid)
    rec = conn.execute("SELECT json FROM rxp_meta WHERE key='dungeonList'").fetchone()
    dl = store.jload(rec[0], {}) if rec else {}
    for short, aid in sorted((dl or {}).items()) if isinstance(dl, dict) else ():
        aid = as_int(aid)
        target = aid if aid in rows else out.get(norm(area_name.get(aid)))
        if target is not None:
            claim(short, target)
    return out


# ------------------------------------------------------------------------------------------------
# the overrides against the client's corpse points
# ------------------------------------------------------------------------------------------------
def distance_yd(b, a, c):
    """Yards between two (x %, y %) points on a map with bounds b."""
    w, h = store.map_size_yd(b)
    return math.hypot((c[0] - a[0]) / 100.0 * w, (c[1] - a[1]) / 100.0 * h)


def check_overrides(zones, by_area):
    """{area id: {"name", "yd", "tol", "ok", "converted"}}: each override's door against the client's Corpse point of
    its instance converted onto the override's own map. yd and ok are None when that can't be measured (no Map row,
    no corpse position, no bounds for the map, another continent)."""
    out = {}
    for aid, name, _kind, um, x, y, tol, _why in OVERRIDES:
        m, b = by_area.get(aid) or {}, zones.bounds.get(um)
        rec = {"name": name, "yd": None, "tol": tol, "ok": None, "converted": None}
        if b and m.get("wx") is not None and m.get("wy") is not None and (m["wx"] or m["wy"]) \
                and m.get("cont") == b["continent"]:
            cx, cy = store.world_to_map(b, m["wx"], m["wy"])
            if cx is not None and cy is not None:
                rec["converted"] = (round(cx, 2), round(cy, 2))
                rec["yd"] = int(round(distance_yd(b, (x, y), (cx, cy))))
                rec["ok"] = rec["yd"] <= tol
        out[aid] = rec
    return out
