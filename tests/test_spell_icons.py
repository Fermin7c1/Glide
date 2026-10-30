#!/usr/bin/env python3
"""Buff icons: the spell -> icon map read from the client's SpellMisc table survives a game patch that changes the
table's layout. With the .dbd's layout hidden (as after a patch the .dbd doesn't know yet) and WoWDBDefs out of
reach, the table is read unnamed and the columns are found from long-standing buffs; the map matches the named read.

Needs data/gamedata/db2/SpellMisc.db2 (written by tools/extract_maps.py, stage "spells"); skipped without it.
Run: python3 tests/test_spell_icons.py   (prints OK)
"""
import os
import re
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
from wowfiles import dbdefs, spells  # noqa: E402
from wowfiles.db2 import read_db2  # noqa: E402

DB2 = os.path.join(ROOT, "data", "gamedata", "db2", "SpellMisc.db2")
if not os.path.exists(DB2):
    print("SKIP: no data/gamedata/db2/SpellMisc.db2 (run tools/extract_maps.py --only spells)")
    raise SystemExit(77)
data = open(DB2, "rb").read()

named, how, hit = spells.spell_icons(data)
assert how == "dbd" and hit == len(spells.SPELL_ICON_ANCHORS), (how, hit)

# a patch the .dbd doesn't know: the same definition with every LAYOUT hash replaced
dbd = open(os.path.join(ROOT, "tools", "dbd", "SpellMisc.dbd"), encoding="utf-8").read()
tmp = tempfile.mkdtemp()
os.makedirs(os.path.join(tmp, "dbd"))
with open(os.path.join(tmp, "dbd", "SpellMisc.dbd"), "w", encoding="utf-8") as fh:
    fh.write(re.sub(r"(?m)^LAYOUT .*$", "LAYOUT 00000000", dbd))


def offline(url):
    raise OSError("offline")


saved = dbdefs.BUNDLED, dbdefs.CACHE, dbdefs.FETCH
dbdefs.BUNDLED = dbdefs.CACHE = os.path.join(tmp, "dbd")
dbdefs.FETCH = offline                  # and WoWDBDefs can't be reached
try:
    patched, how2, hit2 = spells.spell_icons(data)
finally:
    dbdefs.BUNDLED, dbdefs.CACHE, dbdefs.FETCH = saved
assert how2 == "anchors" and hit2 >= spells.ANCHORS_NEEDED, (how2, hit2)
same = sum(patched.get(k) == v for k, v in named.items())
assert same >= 0.99 * len(named), "unnamed read agrees with the named one: %d of %d" % (same, len(named))

# the unnamed read itself: every row has an ID and the foreign key column
rows = read_db2(data)
assert rows and "ID" in rows[0] and "_relation" in rows[0], sorted(rows[0])
print("OK")
