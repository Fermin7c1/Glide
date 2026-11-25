#!/usr/bin/env python3
"""Dungeon and raid entrances (knowledge/sources/entrances.py -> kb instance_entrance; knowledge.api
instance_entrance).

Synthetic part (always): a small world (one continent, two overlapping zones, a city inside one of them) and a
Map.csv in a temp project root. The corpse point goes on the right map (a zone with an outdoor area of the instance's
name, the city a zone's box hands it to, a sub-zone box, spawns near it, depth); an instance without an AreaTableID
gets the kb area of its name; unused, positionless and off-map rows get none; an override replaces its instance's
converted row and is written without Map data too; RestedXP's dungeon short names become aliases; the API answers
by area id, outdoor area id, name, alias and wing name, on a store.KB, a kb.db path (closed again) and the
KnowledgePack, and None on a kb.db built before the table. Without the client's Map table the overrides alone are
written.

Client part (data/kb.db and the client's Map table, data/db2/Map.csv or data/gamedata/db2/Map.db2, read-only; SKIP
without them): each hand-checked override lies within its tolerance of its instance's converted Corpse point, and the
loader puts the well-known dungeons on their zones.

    python3 tests/kb/test_entrances.py
"""
import csv
import json
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from knowledge import build_kb, store  # noqa: E402
from knowledge.api import KnowledgePack, instance_entrance  # noqa: E402
from knowledge.sources import entrances  # noqa: E402

WORLD, CONT, ZA, ZB, CITY = 99000, 99100, 99201, 99202, 99203
CITY_AREA = 99003


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def bounds(wx0, wy0, wx1, wy1):
    return {"wx0": wx0, "wy0": wy0, "wx1": wx1, "wy1": wy1}


# zone A: x% = (1000 - wy) / 10, y% = (1000 - wx) / 10 (1000 x 1000 yd); zone B overlaps A's west edge (B's x 80..100
# is A's x 0..20); the city sits inside A (A's 10..40 %) with A's sub-zone box for it
MAPS = {ZA: bounds(1000, 1000, 0, 0), ZB: bounds(1000, 1800, 0, 800), CITY: bounds(900, 900, 600, 600),
        CONT: bounds(2000, 2000, -1000, -1000)}
WPL = 1422                     # the Scholomance override's map: synthetic bounds, alone on continent 7
# Map.csv: ID, name, InstanceType, MaxPlayers, AreaTableID, CorpseMapID, Corpse x (world X), Corpse y (world Y)
MAP_ROWS = [
    (900, "Test Continent", 0, 0, 0, -1, 0, 0),                    # the world itself: not an instance
    (901, "Hollow Crypt", 1, 5, 99101, 0, 500, 500),               # only A holds it: A 50,50
    (902, "Border Keep", 1, 5, 0, 0, 500, 900),                    # A 10,50 and B 90,50: B has the outdoor area
    (903, "Deep Vault", 2, 40, 99103, 0, 500, 950),                # A 5,50 (in a box) vs B 85,50 (deeper)
    (904, "City Sewers", 1, 10, 99104, 0, 880, 880),               # A 12,12 (A's city box) vs the city 6.7,6.7
    (905, "Spawn Hollow", 1, 5, 99105, 0, 500, 960),               # A 4,50 (spawns) vs B 84,50 (deeper)
    (906, "Empty Tomb", 1, 5, 99106, -1, 0, 0),                    # no corpse position
    (907, "<unused> Old Crypt", 1, 5, 99107, 0, 500, 500),
    (908, "Far Place", 1, 5, 99108, 1, 500, 500),                  # a continent without maps
    (909, "Scholomance", 1, 5, 0, 7, 500, 500),        # AreaTableID 0 -> kb area 2057; the override wins (~300 yd off)
    (910, "Hollow Crypt Annex", 1, 5, 99101, 0, 400, 400),         # same area as 901: the lower Map ID wins
]


def make_kb(conn):
    store.create_schema(conn)
    conn.executemany("INSERT INTO map(ui_map, name, parent, area_id, continent) VALUES(?,?,?,?,0)", [
        (WORLD, "Test World", 0, None), (CONT, "Test Continent", WORLD, None), (ZA, "Alpha Vale", CONT, 99001),
        (ZB, "Beta Hills", CONT, 99002), (CITY, "Gamma City", CONT, CITY_AREA), (WPL, "Plaguelands", CONT, 28)])
    for um, b in list(MAPS.items()) + [(WPL, bounds(1000, 1000, 0, 0))]:
        conn.execute("INSERT INTO map_bounds(ui_map, continent, wx0, wy0, wx1, wy1, src, conf) VALUES(?,?,?,?,?,?,"
                     "'obs',1.0)", (um, 7 if um == WPL else 0, b["wx0"], b["wy0"], b["wx1"], b["wy1"]))
    conn.executemany("INSERT INTO area(area_id, name, ui_map) VALUES(?,?,?)", [
        (99101, "Hollow Crypt", 99301), (99102, "Border Keep", 99302), (99112, "Border Keep", ZB),
        (99103, "Deep Vault", 99303), (99104, "City Sewers", 99304), (99105, "Spawn Hollow", 99305),
        (99113, "Vault Ledge", ZA), (CITY_AREA, "Gamma City", CITY), (2057, "Scholomance", 306)])
    conn.executemany("INSERT INTO area_box(area_id, ui_map, x1, y1, x2, y2) VALUES(?,?,?,?,?,?)", [
        (99113, ZA, 4.5, 48.0, 5.5, 52.0), (CITY_AREA, ZA, 10.0, 10.0, 40.0, 40.0)])
    conn.execute("INSERT INTO creature(id, name) VALUES(99901, 'Hollow Bat')")
    conn.execute("INSERT INTO creature_spawn(creature_id, ui_map, x, y, src, conf) VALUES(99901, ?, 4.2, 50.5, 'fg', "
                 "0.6)", (ZA,))
    conn.execute("INSERT INTO rxp_meta(key, json) VALUES('dungeonList', ?)",
                 (json.dumps({"HC": 99101, "BK": 99112, "XX": 424242}),))
    conn.commit()


def write_map_csv(folder):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "Map.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ID", "MapName_lang", "InstanceType", "MaxPlayers", "AreaTableID", "CorpseMapID", "Corpse[0]",
                    "Corpse[1]"])
        w.writerows(MAP_ROWS)


def test_synthetic(tmp):
    root = os.path.join(tmp, "root")
    data = os.path.join(root, "data")
    write_map_csv(os.path.join(data, "db2"))
    kb_path = os.path.join(tmp, "kb.db")
    conn = store.open_rw(kb_path)
    make_kb(conn)
    ctx = store.BuildContext(root=root, data=data, log=lambda *a: None)
    res = entrances.load(conn, ctx)
    conn.commit()
    rows = {r["area_id"]: dict(r) for r in conn.execute("SELECT * FROM instance_entrance")}
    conn.close()

    check(str(res["map_table"]).endswith("Map.csv"), "read data/db2/Map.csv: %r" % res["map_table"])
    check(res["skipped"] == {"unused": 1, "same area as another instance": 1, "no corpse position": 1,
                             "no zone map holds it": 1}, "skipped %r" % res["skipped"])

    def at(aid, um, x, y):
        r = rows.get(aid)
        check(r is not None, "no row for %d" % aid)
        check((r["ui_map"], round(r["x"], 1), round(r["y"], 1)) == (um, x, y),
              "%s on %r, want %r" % (r["name"], (r["ui_map"], r["x"], r["y"]), (um, x, y)))
        return r

    r = at(99101, ZA, 50.0, 50.0)
    check(r["src"] == "db2" and r["kind"] == "party" and r["instance"] == 901 and r["conf"] == 0.7
          and (r["wx"], r["wy"], r["continent"]) == (500.0, 500.0, 0), "converted row %r" % r)
    check(json.loads(r["aliases"]) == ["hc"], "RestedXP's short name becomes an alias: %r" % r["aliases"])
    r = at(99102, ZB, 90.0, 50.0)                      # the zone with an outdoor 'Border Keep' wins, by name
    check(json.loads(r["aliases"]) == ["bk"], "a short name for the outdoor area id lands on the instance: %r" % r)
    check(at(99103, ZA, 5.0, 50.0)["kind"] == "raid", "raid kind")            # A's sub-zone box beats B's depth
    at(99104, CITY, 6.7, 6.7)                          # A's 'Gamma City' box hands the point to the city's map
    at(99105, ZA, 4.0, 50.0)                           # spawns near it on A beat B's depth
    check(99106 not in rows and 99107 not in rows and 99108 not in rows, "no row without a place: %r" % sorted(rows))

    r = rows[2057]                                     # Map 909 'Scholomance': area by name, then the override
    check(r["src"] == "hand" and (r["ui_map"], r["x"], r["y"]) == (WPL, 69.7, 73.4) and r["instance"] == 909
          and r["max_players"] == 5 and r["conf"] == entrances.HAND_CONF
          and (r["continent"], r["wx"], r["wy"]) == (7, 266.0, 303.0), "override replaces the conversion %r" % r)
    hand = {o[0] for o in entrances.OVERRIDES}
    check(hand <= set(rows) and all(rows[a]["src"] == "hand" for a in hand),
          "every override is written, Map row or not")
    check(rows[1583]["instance"] is None and rows[1583]["kind"] == "party" and rows[1583]["wx"] is None
          and "ubrs" in json.loads(rows[1583]["aliases"]), "an override without its Map row %r" % rows[1583])
    yd = res["override_check_yd"]
    check(300 <= yd["Scholomance"] <= 310 and yd["Molten Core"] is None, "the check measures what it can: %r" % yd)
    check(res["override_far"] == ["Scholomance %d yd (tolerance 100)" % yd["Scholomance"]] and ctx.notes,
          "a far corpse point is reported: %r" % res.get("override_far"))

    kb = store.open_ro(kb_path)
    e = instance_entrance(kb, 99101)
    check(e == {"map": ZA, "x": 50.0, "y": 50.0, "src": "db2", "name": "Hollow Crypt", "area_id": 99101,
                "kind": "party", "conf": 0.7}, "by area id %r" % e)
    for key in ("Hollow Crypt", "the hollow crypt", "HOLLOW  CRYPT", "hc", "99101", 99101.0):
        check((instance_entrance(kb, key) or {}).get("area_id") == 99101, "key %r" % (key,))
    check(instance_entrance(kb, 99112)["area_id"] == 99102, "an outdoor area id answers with its instance")
    check(instance_entrance(kb, "Border Keep")["map"] == ZB, "name")
    for key, aid in (("UBRS", 1583), ("Upper Blackrock Spire", 1583), ("Dire Maul North", 2557),
                     ("dire maul north gate", 2557), ("Onyxia", 2159), ("AQ40", 3428), ("Scholomance", 2057),
                     ("Stratholme (undead side)", 2017)):
        got = instance_entrance(kb, key)
        check(got and got["area_id"] == aid and got["src"] == "hand", "key %r -> %r" % (key, got))
    e = instance_entrance(kb, "MC")
    check(e["map"] == 1428 and 0 <= e["x"] <= 100 and e["x"] > 1 and e["kind"] == "raid", "map percent %r" % e)
    for key in ("Nowhere", 424242, None, "", True):
        check(instance_entrance(kb, key) is None, "unknown %r" % (key,))
    pack = KnowledgePack(kb_path)
    check(pack.ok, "pack: %s" % pack.reason)
    check(pack.instance_entrance("Deep Vault") == instance_entrance(kb_path, 99103), "pack method = function")
    check(instance_entrance(pack, "zg")["area_id"] == 1977, "the function takes a pack")
    kb.close()
    pack.kb.close()
    copy = os.path.join(tmp, "copy.db")               # a call by path closes what it opened (Windows pins open files)
    shutil.copyfile(kb_path, copy)
    check(instance_entrance(copy, "hc")["area_id"] == 99101, "by path")
    os.remove(copy)

    old = os.path.join(tmp, "old.db")                 # a kb.db built before the table existed
    c = sqlite3.connect(old)
    c.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    c.execute("CREATE TABLE area(area_id INTEGER PRIMARY KEY, name TEXT, ui_map INT)")
    c.commit()
    c.close()
    okb = store.open_ro(old)
    check(instance_entrance(okb, "Scholomance") is None and instance_entrance(okb, 2057) is None, "old kb")
    okb.close()
    check(instance_entrance(os.path.join(tmp, "missing.db"), 2057) is None, "no kb.db")

    names = [n for n, _ in build_kb.steps()]
    check(names.index("entrances") == names.index("map_bounds") + 1 and "entrances" in build_kb.STORE_LOADERS,
          "built right after map_bounds, as a store loader")
    print("synthetic: placement, overrides, aliases, API: ok")


def test_no_map_table(tmp):
    """Without the client's Map table (no Map.csv, no Map.db2) the overrides alone are written, and the build goes
    on."""
    root = os.path.join(tmp, "bare")
    data = os.path.join(root, "data")
    os.makedirs(data)
    conn = sqlite3.connect(":memory:")
    make_kb(conn)
    ctx = store.BuildContext(root=root, data=data, log=lambda *a: None)
    res = entrances.load(conn, ctx)
    rows = {r[0]: r[1:] for r in conn.execute("SELECT area_id, src, instance, x, y FROM instance_entrance")}
    conn.close()
    check(isinstance(res["map_table"], dict) and "Map" in res["map_table"]["skipped"], "skipped %r" % res)
    check(set(rows) == {o[0] for o in entrances.OVERRIDES} and all(r[0] == "hand" and r[1] is None
                                                                   for r in rows.values()), "overrides only %r" % rows)
    check(res["converted"] == 0 and all(v is None for v in res["override_check_yd"].values())
          and "override_far" not in res, "nothing to measure %r" % res)
    print("no Map table: the overrides alone: ok")


def test_client():
    """The overrides against the client's own corpse points (data/kb.db's map tables, the client's Map table)."""
    kb_path = os.path.join(ROOT, "data", "kb.db")
    ctx = store.BuildContext(root=ROOT, log=lambda *a: None)
    raw, source = entrances.map_rows(ctx) if os.path.exists(kb_path) else ([], "no data/kb.db")
    if not raw:
        return "client map check (%s)" % source
    src = sqlite3.connect("file:%s?mode=ro" % kb_path.replace("\\", "/"), uri=True)
    if not src.execute("SELECT COUNT(*) FROM map_bounds").fetchone()[0]:
        src.close()
        return "client map check (data/kb.db has no map_bounds)"
    conn = sqlite3.connect(":memory:")
    store.create_schema(conn)
    for t in ("map", "map_bounds", "area", "area_box", "rxp_meta", "creature_spawn"):
        cols = ",".join(store.table_cols(src, t))
        conn.executemany("INSERT INTO %s(%s) VALUES(%s)" % (t, cols, ",".join("?" * len(cols.split(",")))),
                         src.execute("SELECT %s FROM %s" % (cols, t)))
    src.close()
    zones = entrances.Zones(conn)
    by_area, _ = entrances.instances(raw, entrances.instance_areas(conn, zones.outdoor))
    checked = entrances.check_overrides(zones, by_area)
    print("override  converted corpse point  door  yd / tolerance  (%s)" % store.rel_path(source))
    for c in checked.values():
        print("  %-20s %-16s %5s / %d" % (c["name"], c["converted"], c["yd"], c["tol"]))
    unmeasured = [c["name"] for c in checked.values() if c["yd"] is None]
    far = ["%s %d yd" % (c["name"], c["yd"]) for c in checked.values() if c["ok"] is False]
    check(not unmeasured, "overrides without a corpse point on their map: %s" % unmeasured)
    check(not far, "overrides beyond their tolerance: %s" % far)

    res = entrances.load(conn, ctx)
    rows = {r[0]: r[1:] for r in conn.execute("SELECT area_id, ui_map, src FROM instance_entrance")}
    conn.close()
    # the zone each well-known dungeon is in (converted rows: the placement rules on the client's own data)
    zone_of = {718: 1413, 717: 1453, 796: 1420, 1581: 1436, 1337: 1418, 719: 1440, 2437: 1454, 1176: 1446,
               2100: 1443, 209: 1421, 722: 1413}
    wrong = {aid: rows.get(aid) for aid, um in zone_of.items() if aid in rows and rows[aid][0] != um}
    check(not wrong, "dungeons on the wrong map: %r" % wrong)
    check(sum(1 for aid in zone_of if aid in rows) >= 8, "converted rows %r (%r)" % (sorted(rows), res["skipped"]))
    print("client: %d overrides within tolerance, %d rows (%d converted)" % (len(checked), res["rows"],
                                                                             res["converted"]))
    return None


def main():
    tmp = tempfile.mkdtemp(prefix="glide_entrances_")
    try:
        test_synthetic(tmp)
        test_no_map_table(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    skipped = test_client()
    if skipped:
        print("SKIP: %s; the rest passed" % skipped)
        sys.exit(77)
    print("OK")


if __name__ == "__main__":
    main()
