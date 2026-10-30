#!/usr/bin/env python3
"""Settings > "Dungeon runs in the plan" saves companion.rxp.dungeons as true/false. The route analyzer took any
non-string as a list of dungeon codes and iterated it: "guide resolution failed: TypeError: 'bool' object is not
iterable" in the Why this step panel. On means every dungeon, off means none; lists and "all" still work. Unset is
RestedXP's default, none, in every guide group (R18: a Dungeon guide turns its own dungeons on with .enabledungeon,
the step rule's business, not the player's)."""
import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
from knowledge.analyzer import route as R  # noqa: E402

DUNGEON_GUIDE = {"grp": "RestedXP Forever Dungeon Guide (A)"}
PLAIN_GUIDE = {"grp": "RestedXP Alliance 1-20"}
base = {"class": "WARRIOR", "race": "Dwarf", "faction": "Alliance"}

assert R.norm_player(dict(base, dungeons=True), DUNGEON_GUIDE)["dungeons"] == "all"
assert R.norm_player(dict(base, dungeons=True), PLAIN_GUIDE)["dungeons"] == "all"
assert R.norm_player(dict(base, dungeons=False), DUNGEON_GUIDE)["dungeons"] == set()
assert R.norm_player(dict(base), DUNGEON_GUIDE)["dungeons"] == set()              # unset: RestedXP's default
assert R.norm_player(dict(base), PLAIN_GUIDE)["dungeons"] == set()
assert R.norm_player(dict(base, dungeons=["rfc", "DM"]), PLAIN_GUIDE)["dungeons"] == {"RFC", "DM"}
assert R.norm_player(dict(base, dungeons=["Stockades", "Mara"]), PLAIN_GUIDE)["dungeons"] == {"STOCKS", "MARA"}
assert R.norm_player(dict(base, dungeons="all"), PLAIN_GUIDE)["dungeons"] == "all"

# a real route with the switch on, when a knowledge base is around
kb_path = os.path.join(ROOT, "data", "kb.db")
if os.path.exists(kb_path):
    from knowledge import store  # noqa: E402
    kb = store.open_ro(kb_path)
    if kb is not None:
        import sqlite3
        c = sqlite3.connect(kb_path)
        row = c.execute("select key from rxp_guide where grp like '%Dungeon Guide%' limit 1").fetchone()
        c.close()
        if row:
            for flag in (True, False):
                r = R.get_route(kb, row[0], dict(base, dungeons=flag))
                assert r is not None and r.steps, (flag, row[0])
print("rxp dungeons switch: ok")
