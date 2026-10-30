#!/usr/bin/env python3
"""Game tables after a patch the bundled .dbd files don't know yet (wowfiles/dbdefs.dbd_text): the definition is
fetched from WoWDBDefs once, kept in data/gamedata/dbd and used from there; without a network or when WoWDBDefs doesn't
know the layout either, the bundled one comes back and reading fails with the missing layout hash (the refresh reports
it). Uses SpellMisc as the sample table; no network (the fetch is replaced).

Needs data/gamedata/db2/SpellMisc.db2 (tools/extract_maps.py, stage "spells"); skipped without it.
Run: python3 tests/test_dbd_fallback.py   (prints OK)
"""
import os
import re
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
from wowfiles import dbdefs, spells  # noqa: E402
from wowfiles.db2 import DB2Error, has_layout, layout_hash, read_db2  # noqa: E402

DB2 = os.path.join(ROOT, "data", "gamedata", "db2", "SpellMisc.db2")
if not os.path.exists(DB2):
    print("SKIP: no data/gamedata/db2/SpellMisc.db2 (run tools/extract_maps.py --only spells)")
    raise SystemExit(77)
data = open(DB2, "rb").read()
real = open(os.path.join(ROOT, "tools", "dbd", "SpellMisc.dbd"), encoding="utf-8").read()
h = layout_hash(data)
assert has_layout(real, h), "the bundled .dbd knows today's client"
stale = re.sub(r"(?m)^LAYOUT .*$", "LAYOUT 00000000", real)          # as if a patch changed the layout
assert not has_layout(stale, h)

tmp = tempfile.mkdtemp()
os.makedirs(os.path.join(tmp, "tools", "dbd"))
with open(os.path.join(tmp, "tools", "dbd", "SpellMisc.dbd"), "w", encoding="utf-8") as fh:
    fh.write(stale)
calls = []


def wowdbdefs(text):
    def fetch(url):
        calls.append(url)
        if text is None:
            raise OSError("offline")
        return text
    return fetch


saved = dbdefs.BUNDLED, dbdefs.CACHE, dbdefs.FETCH
dbdefs.BUNDLED, dbdefs.CACHE = os.path.join(tmp, "tools", "dbd"), os.path.join(tmp, "gamedata", "dbd")
try:
    # offline: the stale bundled .dbd comes back, and reading says which layout is missing
    dbdefs.FETCH = wowdbdefs(None)
    t = dbdefs.dbd_text("SpellMisc", data)
    assert t == stale and calls[-1].endswith("/SpellMisc.dbd")
    try:
        read_db2(data, t)
        raise AssertionError("read a layout it doesn't know")
    except DB2Error as e:
        assert ("%08X" % h) in str(e), e
    # WoWDBDefs doesn't know it either: still the bundled one, nothing kept
    dbdefs.FETCH = wowdbdefs(stale)
    assert dbdefs.dbd_text("SpellMisc", data) == stale
    assert not os.path.exists(os.path.join(tmp, "gamedata", "dbd", "SpellMisc.dbd"))
    # WoWDBDefs knows it: fetched, kept, and the table reads with names
    dbdefs.FETCH = wowdbdefs(real)
    t = dbdefs.dbd_text("SpellMisc", data)
    assert t == real and os.path.exists(os.path.join(tmp, "gamedata", "dbd", "SpellMisc.dbd"))
    rows = read_db2(data, t)
    assert rows and "SpellIconFileDataID" in rows[0]
    # next time it comes from the kept copy, no fetch
    n = len(calls)
    dbdefs.FETCH = wowdbdefs(None)
    assert dbdefs.dbd_text("SpellMisc", data) == real and len(calls) == n
    # and the spell list reads named again (not by the known-buff fallback)
    icons, how, hit = spells.spell_icons(data)
    assert how == "dbd" and hit == len(spells.SPELL_ICON_ANCHORS), (how, hit)
finally:
    dbdefs.BUNDLED, dbdefs.CACHE, dbdefs.FETCH = saved
print("OK")
