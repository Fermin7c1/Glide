#!/usr/bin/env python3
"""Game icons for the Guides page's targets, read from the player's client (wowfiles/target_icons.py, the refresh's
"icons" stage, the knowledge build's Creature import, the guide facts' "icons" and the `rxp_import.py icons` helper),
over small synthetic tables and textures (tests/wowfixtures.py; no game client needed):

- Creature, Item and CreatureFamily read with the vendored .dbd and checked: a table whose known creatures or icons
  don't check out is left out (the page keeps its glyphs), never guessed; a layout the .dbd doesn't know is read
  unnamed, the creature columns found by the known creatures.
- the role and type icons found by name, else by their FileDataID; UI textures kept whole, icons trimmed.
- kb creature.type / family filled from Creature.csv (rank only where kb has none).
- the facts' icon lookup: items, spells, a beast's family icon, another creature's type icon, the role marks.
- the decode cache: an icon is decoded once; a file that isn't a small texture is refused.

Run: python3 tests/test_target_icons.py   (prints OK)
"""
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(1, os.path.dirname(os.path.abspath(__file__)))
import importlib.util  # noqa: E402

if importlib.util.find_spec("numpy") is None:       # the texture decoder needs it
    print("SKIP: needs numpy")
    raise SystemExit(77)
from wowfiles import db2_import, dbdefs  # noqa: E402
from wowfiles import target_icons as T  # noqa: E402
from wowfixtures import FakeClient, blp, wdc3  # noqa: E402

DBD = {n: open(os.path.join(ROOT, "tools", "dbd", n + ".dbd"), encoding="utf-8").read()
       for n in ("Creature", "CreatureFamily", "Item")}
LAYOUT = {"Creature": "6E14C900", "CreatureFamily": "7FE3CBA7", "Item": "9A2A4834"}      # WoW Forever 1.60.1


def creature_rows(anchors=T.CREATURE_ANCHORS):
    rows = [{"ID": cid, "CreatureType": t, "CreatureFamily": f, "Classification": 0, "DisplayID": [cid, 0, 0, 0]}
            for cid, (t, f) in anchors.items()]
    rows.append({"ID": 448, "CreatureType": 7, "CreatureFamily": 0, "Classification": 1, "DisplayID": [1, 0, 0, 0]})   # Hogger, elite
    rows.append({"ID": 3100, "CreatureType": 1, "CreatureFamily": 6, "Classification": 0, "DisplayID": [2, 0, 0, 0]})  # a crocolisk
    return rows


ICON_BOAR, ICON_WOLF, ICON_CROC, ICON_HEARTH, ICON_LINEN = 132184, 132203, 132199, 134414, 132889
FAMILY_ROWS = [{"ID": 1, "IconFileID": ICON_WOLF, "CategoryEnumID": 1}, {"ID": 5, "IconFileID": ICON_BOAR},
               {"ID": 6, "IconFileID": ICON_CROC}, {"ID": 3, "IconFileID": 132196}]
ITEM_ROWS = [{"ID": 6948, "ClassID": 15, "IconFileDataID": ICON_HEARTH},
             {"ID": 2589, "ClassID": 7, "IconFileDataID": ICON_LINEN},
             {"ID": 769, "ClassID": 7, "IconFileDataID": 134027},
             {"ID": 4865, "ClassID": 12, "IconFileDataID": 0}]                 # no icon: left out


def client(creatures=None, items=ITEM_ROWS, families=FAMILY_ROWS, layouts=LAYOUT, names=None):
    files = {841631: wdc3(DBD["Creature"], layouts["Creature"], creatures or creature_rows()),
             1351351: wdc3(DBD["CreatureFamily"], layouts["CreatureFamily"], families),
             841626: wdc3(DBD["Item"], layouts["Item"], items)}
    for fid in {r["IconFileID"] for r in families} | {r["IconFileDataID"] for r in items if r["IconFileDataID"]}:
        files[fid] = blp(64, 64)
    for cands in list(T.TYPE_ICONS.values()) + list(T.NAMED.values()):
        files[cands[0][1]] = blp(64, 64) if cands[0][0].lower().startswith(T.ICON_DIR) else blp(32, 32)
    return FakeClient(files, names)


# ---- the tables, read with the .dbd and checked --------------------------------------------------------------------
c = client()
book, cre = T.build(c, log=lambda *a: None)
assert not book["problems"], book["problems"]
assert book["how"]["creatures"]["how"] == "dbd" and book["how"]["creatures"]["anchors"] == "12/12", book["how"]
assert cre[3098][:2] == (1, 5) and cre[448] == (7, 0, 1) and cre[3100][:2] == (1, 6), cre
assert book["items"] == {"6948": ICON_HEARTH, "2589": ICON_LINEN, "769": 134027}, book["items"]
assert book["families"] == {"1": ICON_WOLF, "5": ICON_BOAR, "6": ICON_CROC, "3": 132196}, book["families"]
assert book["types"]["Humanoid"] == 134166 and book["types"]["Beast"] == 132164, book["types"]
assert book["named"]["vendor"] == 132060 and book["named"]["flight"] == 136456, book["named"]
assert 132060 in book["raw"] and 134166 not in book["raw"], "UI textures shown whole, icons trimmed"
assert set(T.fixed_icons(book)) >= {ICON_BOAR, 132060, 134166} and ICON_LINEN not in T.fixed_icons(book)

# a name in the client's root wins over the listfile's id
c2 = client(names={"interface/gossipframe/vendorgossipicon.blp": 999001})
c2.files[999001] = blp(16, 16)
assert T.build(c2, log=lambda *a: None)[0]["named"]["vendor"] == 999001
# a role whose icon the client doesn't have: left out (the page keeps its glyph)
c3 = client()
del c3.files[T.NAMED["mailbox"][0][1]]
assert "mailbox" not in T.build(c3, log=lambda *a: None)[0]["named"]

# ---- reads that don't check out are left out, not guessed -----------------------------------------------------------
wrong = dict(T.CREATURE_ANCHORS)
for cid in (3098, 113, 299, 69):                 # columns swapped in a patch: boars read as humanoids
    wrong[cid] = (7, 0)
b, cr = T.build(client(creatures=creature_rows(wrong)), log=lambda *a: None)
assert cr == {} and any(p.startswith("Creature:") for p in b["problems"]), b["problems"]
assert b["items"], "the other tables still read"
few = {k: v for k, v in list(T.CREATURE_ANCHORS.items())[:3]}
b, cr = T.build(client(creatures=creature_rows(few)), log=lambda *a: None)
assert cr == {} and "3 in the table" in " ".join(b["problems"]), b["problems"]
bad_items = [dict(r) for r in ITEM_ROWS]
bad_items[0]["IconFileDataID"] = ICON_LINEN      # the Hearthstone with another item's icon: the column is wrong
b, _ = T.build(client(items=bad_items), log=lambda *a: None)
assert b["items"] == {} and any("Item: the icon of 6948" in p for p in b["problems"]), b["problems"]
c4 = client()
for r in ITEM_ROWS:
    c4.files.pop(r["IconFileDataID"], None)      # icon ids that aren't files: not an icon column
b, _ = T.build(c4, log=lambda *a: None)
assert b["items"] == {} and any(p.startswith("Item: no column") for p in b["problems"]), b["problems"]
c5 = client()
del c5.files[841631]                             # no Creature table in this client
b, cr = T.build(c5, log=lambda *a: None)
assert cr == {} and b["families"] and any(p.startswith("Creature:") for p in b["problems"])

# ---- a layout the .dbd doesn't know (a patch): read unnamed, columns found by the known creatures ---------------------
tmp = tempfile.mkdtemp()
try:
    os.makedirs(os.path.join(tmp, "dbd"))
    for n, text in DBD.items():
        with open(os.path.join(tmp, "dbd", n + ".dbd"), "w", encoding="utf-8") as fh:
            fh.write(re.sub(r"(?m)^LAYOUT .*$", "LAYOUT 00000000", text))

    def offline(url):
        raise OSError("offline")
    saved = dbdefs.BUNDLED, dbdefs.CACHE, dbdefs.FETCH
    dbdefs.BUNDLED = dbdefs.CACHE = os.path.join(tmp, "dbd")
    dbdefs.FETCH = offline
    try:
        b, cr = T.build(client(), log=lambda *a: None)
    finally:
        dbdefs.BUNDLED, dbdefs.CACHE, dbdefs.FETCH = saved
    assert b["how"]["creatures"]["how"] == "anchors", b["how"]
    assert {k: v[:2] for k, v in cr.items()} == {k: v[:2] for k, v in cre.items()}, "the unnamed read agrees"
    assert b["items"] == book["items"] and b["how"]["items"]["how"] == "values", b["how"]
    assert b["families"] == book["families"]

    # ---- icons decoded once into the cache: icons trimmed, UI textures whole, non-textures refused ------------------
    from wowfiles.blp import decode_blp
    out = os.path.join(tmp, "icons")
    T.write_icon_png(c, ICON_BOAR, os.path.join(out, "%d.png" % ICON_BOAR))
    T.write_icon_png(c, 132060, os.path.join(out, "132060.png"), trim=False)
    png = open(os.path.join(out, "%d.png" % ICON_BOAR), "rb").read()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and int.from_bytes(png[16:20], "big") == 56, "64 px icon trimmed to 56"
    assert int.from_bytes(open(os.path.join(out, "132060.png"), "rb").read()[16:20], "big") == 32
    assert decode_blp(blp(4, 4))[0, 0].tolist() == [10, 10, 10, 255]
    for fid, why in ((841631, "not a texture"), (5, "not in the root")):
        try:
            T.write_icon_png(c, fid, os.path.join(out, "x.png"))
            raise AssertionError("decoded %s" % fid)
        except Exception as e:  # noqa: BLE001
            assert why in str(e), e
    c.files[7] = blp(512, 512)
    try:
        T.write_icon_png(c, 7, os.path.join(out, "7.png"))
        raise AssertionError("decoded a 512 px texture as an icon")
    except T.IconsError as e:
        assert "not an icon" in str(e)
    assert not [f for f in os.listdir(out) if f.endswith(".tmp")], os.listdir(out)

    # ---- what the stage writes, read back by the guide facts (only for the client build in the manifest) -------------
    gd = os.path.join(tmp, "gamedata")
    os.makedirs(gd)
    with open(os.path.join(gd, "target_icons.json"), "w", encoding="utf-8") as fh:
        json.dump(book, fh)
    with open(os.path.join(gd, "spell_icons.json"), "w", encoding="utf-8") as fh:
        json.dump({"version": c.version, "icons": {"2018": 136241}}, fh)
    with open(os.path.join(gd, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"version": "1.60.0.1"}, fh)
    assert not T.IconBook.load(gd), "icons from another client build aren't used"
    with open(os.path.join(gd, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"version": c.version}, fh)
    ib = T.IconBook.load(gd)
    assert ib and ib.item(2589) == ICON_LINEN and ib.spell(2018) == 136241 and ib.named("vendor") == 132060
    assert ib.creature("Beast", "Boar") == ICON_BOAR and ib.creature("Beast", None) == 132164
    assert ib.creature("Humanoid", None) == 134166 and ib.creature(None, None) is None
    assert ib.creature("Beast", "Crocolisk") == ICON_CROC and ib.creature("Beast", "77") == 132164
    assert T.IconBook.load(os.path.join(tmp, "nowhere")).item(2589) is None

    # ---- Creature.csv -> kb creature.type / family / rank -----------------------------------------------------------
    csvp = os.path.join(tmp, "Creature.csv")
    T.write_creature_csv(csvp, cre)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE creature(id INTEGER PRIMARY KEY, name TEXT, rank INT, type TEXT, family TEXT)")
    conn.executemany("INSERT INTO creature(id, name, rank) VALUES(?,?,?)",
                     [(3098, "Mottled Boar", None), (448, "Hogger", None), (6, "Kobold Vermin", 0), (12345, "X", 2)])
    assert db2_import.available(tmp).get("Creature") == csvp
    n = db2_import.creature_types(conn, db2_import.read_csv(csvp))
    got = {r[0]: r[1:] for r in conn.execute("SELECT id, type, family, rank FROM creature")}
    conn.close()
    assert n == 3 and got == {3098: ("Beast", "Boar", 0), 448: ("Humanoid", None, 1), 6: ("Humanoid", None, 0),
                              12345: (None, None, 2)}, got

    # ---- the facts' icon lookup ---------------------------------------------------------------------------------------
    from knowledge.api import explorer

    class KB:
        ok = True

        def __init__(self):
            self.c = sqlite3.connect(":memory:")
            self.c.row_factory = sqlite3.Row
            self.c.execute("CREATE TABLE creature(id INTEGER PRIMARY KEY, type TEXT, family TEXT)")
            self.c.executemany("INSERT INTO creature VALUES(?,?,?)",
                               [(3098, "Beast", "Boar"), (3100, "Beast", "Crocolisk"), (3158, "Humanoid", None),
                                (5, None, None)])

        def q(self, sql, *args):
            return [dict(r) for r in self.c.execute(sql, args)]

    guide = {"steps": [
        {"idx": 1, "actions": [{"verb": "collect", "entity_kind": "item", "entity_id": 2589, "args": {"item_id": 2589}},
                               {"verb": "train", "entity_kind": "spell", "entity_id": 2018, "args": {"spell_id": 2018}},
                               {"verb": "use", "entity_kind": None, "entity_id": None, "args": {"items": [{"id": 769}]}},
                               {"verb": "target", "entity_kind": "npc", "entity_id": 3158, "args": {}}]}]}
    facts = {"targets": {"1": [{"kind": "npc", "id": 3098, "item": None}, {"kind": "object", "id": 9, "item": 4865},
                               {"kind": "npc", "id": 5, "item": None}]},
             "prep": [{"item_id": 6948, "vendors": [{"id": 3100}]}]}
    kb = KB()
    icons = explorer.guide_icons(kb, guide, facts, ib)
    kb.c.close()
    assert icons["item:2589"] == ICON_LINEN and icons["item:769"] == 134027 and icons["item:6948"] == ICON_HEARTH
    assert icons["spell:2018"] == 136241 and icons["npc:3098"] == ICON_BOAR and icons["npc:3100"] == ICON_CROC
    assert icons["npc:3158"] == 134166 and "npc:5" not in icons and "item:4865" not in icons, icons
    assert icons["role:vendor"] == 132060 and icons["role:quest_turnin"] == 132048, icons
    assert explorer.guide_icons(None, guide, facts, ib).get("npc:3098") is None, "no kb: no creature icons"
    assert explorer.guide_icons(None, guide, facts, T.IconBook()) == {}
    assert explorer.guide_facts(None, guide, icons=ib)["icons"]["item:2589"] == ICON_LINEN, "works with no kb"

    # ---- the refresh's "icons" stage: the table copies, Creature.csv, target_icons.json, the fixed icons decoded ------
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import extract_maps as X
    saved = X.OUT, X.ROOT
    X.OUT, X.ROOT = os.path.join(tmp, "stage", "data", "gamedata"), os.path.join(tmp, "stage")
    try:
        ex = X.Extractor.__new__(X.Extractor)
        ex.c, ex.force, ex.tables = client(), False, {}
        os.makedirs(X.OUT)
        ex.mpath, ex.manifest = os.path.join(X.OUT, "manifest.json"), {"version": ex.c.version, "stages": {}}
        ex.stage_icons()
        done = json.load(open(ex.mpath, encoding="utf-8"))["stages"]["icons"]
        assert done["items"] == 3 and done["creatures"] == len(cre) and done["failed"] == 0, done
        assert done["decoded"] == len(T.fixed_icons(book)), done
        assert json.load(open(os.path.join(X.OUT, "target_icons.json"), encoding="utf-8"))["items"] == book["items"]
        assert sorted(os.listdir(os.path.join(X.OUT, "db2"))) == ["Creature.db2", "CreatureFamily.db2", "Item.db2"]
        rows = db2_import.read_csv(os.path.join(X.ROOT, "data", "db2", "Creature.csv"))
        assert len(rows) == len(cre) and {"ID": "3098", "CreatureType": "1", "CreatureFamily": "5",
                                          "Classification": "0"} in rows
        assert os.path.exists(os.path.join(X.OUT, "icons", "%d.png" % ICON_BOAR))
        assert not os.path.exists(os.path.join(X.OUT, "icons", "%d.png" % ICON_LINEN)), "item icons: on first use"
        n0 = len(ex.c.reads)
        ex.stage_icons()                         # again (a --force run): nothing decoded twice
        assert json.load(open(ex.mpath, encoding="utf-8"))["stages"]["icons"]["decoded"] == 0
        assert len(ex.c.reads) - n0 == 3, "only the three tables read again"
    finally:
        X.OUT, X.ROOT = saved

    # ---- `rxp_import.py icons`: the desktop's glide://icon misses, decoded in one batch ---------------------------------
    sys.path.append(os.path.join(ROOT, "companion"))
    import io
    import icons as I
    import rxp_import
    saved = I.OUT, I.ICON_DIR, I._casc, I._casc_t
    I.OUT, I.ICON_DIR = gd, os.path.join(gd, "icons")
    fc = client()
    I._casc, I._casc_t = fc, 9e18                # an open client index that never idles out
    try:
        got = I.ensure([ICON_LINEN, 132060, "132060", "x", -3, 841631])
        assert got[ICON_LINEN] and got[132060] and got[841631] is None and set(got) == {ICON_LINEN, 132060, 841631}
        assert int.from_bytes(open(got[132060], "rb").read()[16:20], "big") == 32, "a raw texture (target_icons.json)"
        assert int.from_bytes(open(got[ICON_LINEN], "rb").read()[16:20], "big") == 56
        n0 = len(fc.reads)
        I.ensure([ICON_LINEN, 132060])
        assert len(fc.reads) == n0, "cached icons aren't read again"
        assert len(I.ensure(range(1, 1000))) == I.MAX_ENSURE
        stdin, stdout = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = io.StringIO(json.dumps({"fdids": [ICON_BOAR, 5]})), io.StringIO()
        try:
            rxp_import.main(["icons"])
            ans = json.loads(sys.stdout.getvalue().strip().splitlines()[-1])
        finally:
            sys.stdin, sys.stdout = stdin, stdout
        assert ans == {"ok": True, "ready": [ICON_BOAR], "failed": [5]}, ans
    finally:
        I.OUT, I.ICON_DIR, I._casc, I._casc_t = saved
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
