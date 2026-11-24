#!/usr/bin/env python3
"""Learned drop rates count a corpse once, however often it was looted (regression: reopening one corpse opens a
loot window each time, and derive_drops counted every window as a kill: Urok Doomhowl's one kill left five windows
and every item in them read as a 1.0 drop over 5 kills). knowledge/sources/observations.py merges an NPC's windows
within 300 s with no kill of it between them, and still tells apart the corpses of a grind: a kill between two
windows, or several kills before a group of windows, keeps them separate. Where no kill was recorded (no XP: level 60,
grey mobs) only a window seconds after the one before with nothing new in it is the same corpse (regression: a level
60 farm read as one corpse). Objects (veins, herbs, chests) are not merged: each window is one node.

Run: python3 tests/test_observations_drops.py   (prints OK)
"""
import os
import sqlite3
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
from knowledge import store  # noqa: E402
from knowledge.sources import observations  # noqa: E402

UROK, NUGGET, ARMOR = 10584, 18784, 13178                  # Urok Doomhowl, two items from his one kill
S = "20261113-164918"


def kb(kills=(), loot=()):
    """A fresh kb in memory: kills (session, t, creature), loot windows (session, t, kind, source, [items]); then
    derive_drops. -> (conn, its result)"""
    conn = sqlite3.connect(":memory:")
    store.create_schema(conn)
    store.insert_rows(conn, "obs_kill", list(kills), ["session", "t", "creature_id"])
    store.insert_rows(conn, "obs_loot", [(s, t, k, src, i, 1) for s, t, k, src, items in loot for i in items],
                      ["session", "t", "src_kind", "src_id", "item_id", "qty"])
    return conn, observations.derive_drops(conn)


def drop(conn, src, item, table="creature_drop", col="creature_id"):
    """-> (rate, n_obs) of one source and item"""
    return conn.execute("SELECT rate, n_obs FROM %s WHERE %s=? AND item_id=?" % (table, col), (src, item)).fetchone()


# the regression, as the recorder logged it: one kill, five windows over seven seconds, the items thinning out
conn, res = kb(kills=[(S, 1794612296, UROK)],
               loot=[(S, 1794612323, "npc", UROK, [NUGGET, ARMOR]), (S, 1794612324, "npc", UROK, [NUGGET, ARMOR]),
                     (S, 1794612326, "npc", UROK, [NUGGET, ARMOR]), (S, 1794612328, "npc", UROK, [NUGGET, ARMOR]),
                     (S, 1794612330, "npc", UROK, [ARMOR])])
assert drop(conn, UROK, NUGGET) == (1.0, 1), drop(conn, UROK, NUGGET)     # was (0.8, 5): five "kills"
assert drop(conn, UROK, ARMOR) == (1.0, 1), drop(conn, UROK, ARMOR)
assert res["creature_drop"] == 2, res

# level 60: no XP, so no kill is recorded; the windows of one corpse still merge, and 300 s later it's another corpse
conn, _ = kb(loot=[(S, 1000, "npc", UROK, [NUGGET, ARMOR]), (S, 1004, "npc", UROK, [ARMOR]),
                   (S, 1300, "npc", UROK, [ARMOR]), (S, 1301, "npc", UROK, [ARMOR])])
assert drop(conn, UROK, NUGGET) == (0.5, 2), drop(conn, UROK, NUGGET)
assert drop(conn, UROK, ARMOR) == (1.0, 2), drop(conn, UROK, ARMOR)
conn, _ = kb(loot=[(S, 1000, "npc", UROK, [ARMOR]), (S, 1301, "npc", UROK, [ARMOR])])    # 301 s apart: two
assert drop(conn, UROK, ARMOR) == (1.0, 2), drop(conn, UROK, ARMOR)
conn, _ = kb(loot=[(S, 1000, "npc", UROK, [ARMOR]), ("other", 1001, "npc", UROK, [ARMOR])])  # two sessions: two
assert drop(conn, UROK, ARMOR) == (1.0, 2), drop(conn, UROK, ARMOR)
# ...but with no kill recorded, windows within 300 s aren't one corpse (regression: six Scholomance Students looted 40 s
# apart at level 60 read as one corpse, the stylus a 1.0 drop): only a window seconds after the one before, with
# nothing new in it, is that corpse looted again
STUDENT, CLOTH, STYLUS = 10475, 14047, 13938
conn, _ = kb(loot=[(S, 1000 + 40 * n, "npc", STUDENT, [CLOTH, STYLUS] if n == 2 else [CLOTH]) for n in range(6)])
assert drop(conn, STUDENT, STYLUS) == (round(1 / 6, 3), 6), drop(conn, STUDENT, STYLUS)
assert drop(conn, STUDENT, CLOTH) == (1.0, 6), drop(conn, STUDENT, CLOTH)
# an AoE pull looted seconds apart: corpses whose loot differs are different corpses
conn, _ = kb(loot=[(S, 1000, "npc", STUDENT, [CLOTH]), (S, 1003, "npc", STUDENT, [STYLUS]),
                   (S, 1005, "npc", STUDENT, [CLOTH, STYLUS]), (S, 1007, "npc", STUDENT, [CLOTH])])
assert drop(conn, STUDENT, STYLUS) == (round(2 / 3, 3), 3), drop(conn, STUDENT, STYLUS)   # the last: a re-loot
# a level 59 kill recorded, then level 60's unrecorded ones later in the session: those are still told apart
conn, _ = kb(kills=[(S, 990, STUDENT)],
             loot=[(S, 995, "npc", STUDENT, [CLOTH]), (S, 1400, "npc", STUDENT, [CLOTH]),
                   (S, 1440, "npc", STUDENT, [STYLUS]), (S, 1480, "npc", STUDENT, [CLOTH])])
assert drop(conn, STUDENT, CLOTH) == (0.75, 4) and drop(conn, STUDENT, STYLUS) == (0.25, 4), drop(conn, STUDENT, CLOTH)

# a grind: kill, loot, kill, loot... a kill between two windows is another corpse (quest items keep their rates)
MOB, PAW = 1234, 5678
conn, _ = kb(kills=[(S, t, MOB) for t in (100, 130, 160, 190)],
             loot=[(S, 102, "npc", MOB, [PAW]), (S, 103, "npc", MOB, [PAW]),          # the first corpse looted twice
                   (S, 132, "npc", MOB, [9999]), (S, 162, "npc", MOB, [PAW])])          # the fourth kill: no loot
assert drop(conn, MOB, PAW) == (0.5, 4), drop(conn, MOB, PAW)                          # 2 of 4 kills
assert drop(conn, MOB, 9999) == (0.25, 4), drop(conn, MOB, 9999)

# a pull of three, looted after the fight: three corpses, though no kill came between the windows
conn, _ = kb(kills=[(S, t, MOB) for t in (100, 104, 109)],
             loot=[(S, 115, "npc", MOB, [PAW]), (S, 118, "npc", MOB, [PAW]), (S, 121, "npc", MOB, [9999])])
assert drop(conn, MOB, PAW) == (round(2 / 3, 3), 3), drop(conn, MOB, PAW)
# ...and a corpse of the pull reopened counts no more corpses than kills, nor an item more than once per corpse
conn, _ = kb(kills=[(S, t, MOB) for t in (100, 104)],
             loot=[(S, 115, "npc", MOB, [PAW]), (S, 116, "npc", MOB, [PAW]), (S, 117, "npc", MOB, [PAW]),
                   (S, 121, "npc", MOB, [9999])])
assert drop(conn, MOB, PAW) == (1.0, 2) and drop(conn, MOB, 9999) == (0.5, 2), (drop(conn, MOB, PAW), drop(conn, MOB, 9999))

# objects aren't merged: three copper veins in a minute are three nodes
VEIN, ORE, STONE = 1731, 2770, 2835
conn, res = kb(loot=[(S, 200, "object", VEIN, [ORE]), (S, 230, "object", VEIN, [ORE, STONE]),
                     (S, 260, "object", VEIN, [ORE])])
assert drop(conn, VEIN, ORE, "object_loot", "object_id") == (1.0, 3), drop(conn, VEIN, ORE, "object_loot", "object_id")
assert drop(conn, VEIN, STONE, "object_loot", "object_id") == (round(1 / 3, 3), 3)
assert res["object_loot"] == 2 and res["creature_drop"] == 0, res

# the merge itself, on its own: no kill recorded, a re-loot holds nothing new, a window with something new is a corpse
ev = observations.loot_events({10: {1, 2}, 12: {2}, 400: {2}}, kills=[])
assert ev == [(1, {1: 1, 2: 1}), (1, {2: 1})], ev
ev = observations.loot_events({10: {1}, 12: {1, 2}, 20: {2}, 40: {2}}, kills=[])
assert ev == [(1, {1: 1}), (1, {1: 1, 2: 1}), (1, {2: 1})], ev                 # 20: a re-loot of 12; 40: 20 s on
ev = observations.loot_events({1000: {1}, 1010: {2}, 1020: {1}, 1030: {3}, 1040: {4}}, kills=[])
assert sum(n for n, _ in ev) == 5 and sum(items.get(1, 0) for _, items in ev) == 2, ev
assert observations.loot_events({10: {1}, 12: {1, 2}}, kills=[5]) == [(1, {1: 1, 2: 1})], "a kill: merged as before"
assert observations.loot_events({10: {1}, 12: {1}}, kills=None) == [(1, {1: 1}), (1, {1: 1})]
assert observations.loot_events({None: {1}, 10: {1}}, kills=[5]) == [(1, {1: 1}), (1, {1: 1})]
assert observations.LOOT_MERGE_S == 300 and observations.REOPEN_S == 15

print("OK")
