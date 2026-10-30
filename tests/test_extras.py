#!/usr/bin/env python3
"""Addon 0.10 extras end to end: Extras.lua under Lua 5.1 with a mocked WoW API -> strip payload -> GameState ->
knowledge/extras.py (training, flight paths, consumables, corpse, target quests) -> rules triggers -> coach context.
Needs lupa; the knowledge part needs data/kb.db (SKIP without it)."""
import os
import sys
import time

from lupa import lua51

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
sys.path.append(os.path.join(ROOT, "companion"))      # after ROOT: `knowledge` stays the package
import wowmock  # noqa: E402
import protocol  # noqa: E402
import rules as rules_mod  # noqa: E402
from state import GameState  # noqa: E402

L = lua51.LuaRuntime(unpack_returned_tuples=True)
L.execute(wowmock.MOCK)
L.execute(r"""
function UnitLevel(u) return u == "target" and 11 or 12 end
function UnitRace() return "Orc", "Orc" end
KNOWN = { [6673] = true, [100] = true, [284] = true, [2687] = true, [6546] = true, [7384] = true, [674] = true }
-- the game's names for RestedXP's list ids; 1424 is RXP's Dual Wield id, the learned spell is 674 (same name)
NAMES = { [6673] = {"Battle Shout", "Rank 1"}, [100] = {"Charge", "Rank 1"}, [772] = {"Rend", "Rank 1"},
  [284] = {"Heroic Strike", "Rank 2"}, [1715] = {"Hamstring", "Rank 1"}, [2687] = {"Bloodrage", ""},
  [6546] = {"Rend", "Rank 2"}, [5242] = {"Battle Shout", "Rank 2"}, [7384] = {"Overpower", "Rank 1"},
  [2551] = {"Cooking", ""}, [1424] = {"Dual Wield", ""}, [674] = {"Dual Wield", ""} }
C_Spell = { GetSpellInfo = function(id) local n = NAMES[id] if n then return { name = n[1] } end end,
            GetSpellSubtext = function(id) return NAMES[id] and NAMES[id][2] end,
            RequestLoadSpellData = function(id) REQUESTED = id end }
BOOK = { {name = "Battle Shout", subName = "Rank 1"}, {name = "Charge", subName = "Rank 1"},
         {name = "Heroic Strike", subName = "Rank 2"}, {name = "Bloodrage"}, {name = "Rend", subName = "Rank 2"},
         {name = "Overpower", subName = "Rank 1"}, {name = "Dual Wield"} }
C_SpellBook = { GetNumSpellBookSkillLines = function() return 1 end,
  GetSpellBookSkillLineInfo = function() return { itemIndexOffset = 0, numSpellBookItems = #BOOK } end,
  GetSpellBookItemInfo = function(slot) return BOOK[slot] end }
function IsPlayerSpell(id) return KNOWN[id] or false end
RXP.defaultSpellList = {
  WARRIOR = { [1] = {6673}, [4] = {100, 772}, [8] = {284, 1715}, [10] = {2687, 6546, 1424, 129}, [12] = {5242, 7384},
              [14] = {1160, 1310185} },
  Orc = { [8] = {2551}, [40] = {6746, 149} } }
RXPCData.flightPaths = { [55] = "Brackenwall Village, Dustwallow Marsh" }
function GetTaxiMapID() return 1414 end
C_TaxiMap = { GetAllTaxiNodes = function() return {
  { nodeID = 23, state = 0, name = "Orgrimmar, Durotar" }, { nodeID = 25, state = 1, name = "Crossroads, The Barrens" },
  { nodeID = 80, state = 2, name = "Ratchet, The Barrens" } } end }
BAG = { {itemID = 4540, stackCount = 12}, {itemID = 159, stackCount = 5}, {itemID = 2512, stackCount = 600},
        {itemID = 6948, stackCount = 1}, {itemID = 1251, stackCount = 3}, {itemID = 4540, stackCount = 2},
        {itemID = 1205, stackCount = 4} }
C_Container.GetContainerNumSlots = function(b) return b == 0 and #BAG or 0 end
C_Container.GetContainerItemInfo = function(b, s) if b == 0 then return BAG[s] end end
local INFO = { [4540] = {"Tough Hunk of Bread", 0, 1, "Food"}, [159] = {"Refreshing Spring Water", 0, 1, "Drink"},
               [2512] = {"Rough Arrow", 6, 1, nil}, [6948] = {"Hearthstone", 15, 1, nil},
               [1251] = {"Linen Bandage", 0, 1, "First Aid"}, [1205] = {"Melon Juice", 0, 15, "Drink"} }
C_Item.GetItemInfoInstant = function(id) local i = INFO[id] return id, "", "", "", 0, i and i[2] or 15, 0 end
C_Item.GetItemSpell = function(id) local i = INFO[id] if i and i[4] then return i[4], 1 end end
C_Item.GetItemInfo = function(id) local i = INFO[id] if i then return i[1], "", 1, 5, i[3] end end
function UnitCharacterPoints() return 2 end
TAPPED = true
function UnitIsTapDenied() return TAPPED end
function UnitIsPlayer() return false end
function InCombatLockdown() return false end
C_TooltipInfo.GetUnit = function() return { lines = { {type = 2, leftText = "Bristleback Quilboar"},
  {type = 8, leftText = "Bristleback Quilboar slain: 3/10"} } } end
DEAD = false
function UnitIsDeadOrGhost() return DEAD end
function UnitIsGhost() return DEAD end
C_DeathInfo = { GetCorpseMapPosition = function() return { GetXY = function() return 0.6200, 0.3600 end } end }
C_UnitAuras.GetPlayerAuraBySpellID = function(id) if DEAD == false and SICK then return {expirationTime = NOW + 300} end end
C_Map.GetBestMapForUnit = function() return 1413 end
C_Map.GetPlayerMapPosition = function() return { GetXY = function() return 0.6350, 0.3750 end } end
""")
ns = L.table()
for f in ("Core.lua", "Codec.lua", "State.lua", "Gear.lua", "Extras.lua", "Coach.lua", "Strip.lua"):
    src = open(os.path.join(ROOT, "addon", "Glide", f), encoding="utf-8").read()
    L.eval("function(src, name, a, ns) local f, e = loadstring(src, name) if not f then error(e) end return f(a, ns) end")(
        src, "@" + f, "Glide", ns)
G = L.globals()
G.fire("ADDON_LOADED", "Glide")
G.fire("PLAYER_LOGIN")
G.fire("TAXIMAP_OPENED")
G.fire("PLAYER_TARGET_CHANGED")
ns.ExtrasFlush()
# rend r1 (772): the book has rank 2; Dual Wield listed as 1424 but known by name; 129 doesn't resolve to a spell
assert ns.trainState == "11 checked, 3 missing, 1 not checkable", ns.trainState
assert G.REQUESTED == 129


def frames(state, n=40):
    """Run the addon's own page rotation into the companion's state, like the strip does."""
    ns.RefreshPages()
    seen = set()
    for _ in range(n):
        payload = ns.Core() + "|" + (ns.NextPage(1000) or "")
        core, page = protocol.parse_payload(payload)
        state.update(core, page)
        seen.add(page.get("pg"))
    return seen


state = GameState()
seen = frames(state)
assert {"P0", "F0", "C0"} <= seen, seen
c, _ = protocol.parse_payload(ns.Core() + "|")
assert c["tp"] == "2" and c["tt"] == "1" and c["tq"] == "1" and "cx" not in c and "rz" not in c, c
G.fire("TAXIMAP_OPENED")
ns.ExtrasFlush()
assert G.GlideLinkDB.fp["Fermin-TestRealm"][23] == "Orgrimmar  Durotar", "taxi scan saved per character"

s = state.summary()
assert s["talent_points"] == 2, s["talent_points"]
assert s["training"] == {"missing": [1715, 2551, 5242], "for_level": 12, "checked": 11, "unchecked": 1}, s["training"]
assert s["flight_paths"] == [23, 25, 55], s["flight_paths"]                # node 80 is "unreachable" = not known
cons = {(x["kind"], x["name"]): x["count"] for x in s["consumables"]}
assert cons == {("f", "Tough Hunk of Bread"): 14, ("d", "Refreshing Spring Water"): 5, ("a", "Rough Arrow"): 600,
                ("h", "Linen Bandage"): 3, ("d", "Melon Juice"): 4}, cons
assert [x["req"] for x in s["consumables"] if x["name"] == "Melon Juice"] == [15]
t = s["target"]
assert t["tapped_by_others"] is True and t["quest_mob"] is True and t["classification"] == "normal", t
assert s["corpse"] is None and s["res_sickness_s"] is None

# a list page whose chunks disagree (hash) is not accepted; the old list stays
state2 = GameState()
state2.update({}, {"pg": "F0", "n": "2", "h": "1", "f": "1,2"})
assert state2.flight_paths() is None
state2.update({}, {"pg": "F1", "n": "2", "h": "1", "f": "3"})
assert state2.flight_paths() is None                                        # hash mismatch
txt = "1,2,3"
h = GameState._hash(txt)
state2.update({}, {"pg": "F0", "n": "2", "h": str(h), "f": "1,2"})
state2.update({}, {"pg": "F1", "n": "2", "h": str(h), "f": "3"})
assert state2.flight_paths() == [1, 2, 3]

# rules without the knowledge base: tapped target, talent points (after the grace period), consumables crossing
r = rules_mod.Rules({})
r.started = True
out = r.check(state, [])
kinds = [x.kind for x in out]
assert "target_tapped" in kinds and "talent_points_unspent" not in kinds, kinds
r.tp_since[12] -= rules_mod.TALENT_GRACE_S + 1
assert [x.kind for x in r.check(state, [])].count("talent_points_unspent") == 1
assert "talent_points_unspent" not in [x.kind for x in r.check(state, [])]          # once per level
L.execute("BAG[1].stackCount = 1; BAG[6].stackCount = 1")               # bread 14 -> 2
G.fire("BAG_UPDATE_DELAYED")
ns.ExtrasFlush()
frames(state, 20)
low = [x for x in r.check(state, []) if x.kind == "consumables_low"]
assert len(low) == 1 and low[0].facts == {"kind": "food", "left": 2}, low
assert not [x for x in r.check(state, []) if x.kind == "consumables_low"]            # said once per crossing
assert rules_mod.allowed("consumables_low", {"coach": True, "upkeep": False}) is False

# dead: corpse position and the release facts; resurrection sickness after
L.execute("DEAD = true")
frames(state, 3)
s = state.summary()
assert s["dead"] == 2 and s["corpse"] == {"x": 62.0, "y": 36.0}, (s["dead"], s["corpse"])
L.execute("DEAD = false; SICK = true")
frames(state, 3)
s = state.summary()
assert s["corpse"] is None and s["res_sickness_s"] == 300, (s["corpse"], s["res_sickness_s"])
L.execute("SICK = false")

# ---- knowledge layer -----------------------------------------------------------------------------------
KB = os.path.join(ROOT, "data", "kb.db")
if not os.path.exists(KB):
    print("SKIP: knowledge part (no data/kb.db); the rest passed")
    sys.exit(77)
from knowledge import api, extras  # noqa: E402

pack = api.KnowledgePack(KB)
assert pack.ok, pack.reason
state.map_sizes[1413] = (3000.0, 2000.0)
s = state.summary()
ex = pack.player_extras(s, state.map_size(1413))
tr = ex["training"]
names = [m["spell"] for m in tr["missing_now"]]
# rend r1 (772) is not worth buying: rank 2 is known
assert names == ["Hamstring", "Cooking", "Battle Shout (rank 2)"], names
assert "lower_ranks_skipped" not in tr, tr
assert tr["next"] == [{"level": 14, "spells": ["Demoralizing Shout (rank 1)", "Tactical Mastery"]}], tr["next"]
fp = ex["flight_paths"]
assert fp["known"] == ["Orgrimmar, Durotar", "Crossroads, The Barrens", "Brackenwall Village, Dustwallow Marsh"], fp
assert [n["name"] for n in fp["unknown_nearby"]] == ["Ratchet, The Barrens"] and fp["unknown_nearby"][0]["yards"] < 20, fp
assert "Camp Taurajo, The Barrens" in fp["unknown_on_this_map"] and "Ratchet, The Barrens" in fp["unknown_on_this_map"]
cn = ex["consumables"]
assert cn["counts"] == {"food": 2, "drink": 5, "ammo": 600, "bandage": 3}, cn["counts"]
assert "Melon Juice x4 (level 15)" in cn["drink"], cn["drink"]
assert pack.player_extras(s, state.map_size(1413)) is ex                       # cached on unchanged inputs
# corpse run facts
s_dead = dict(s, dead=2, corpse={"x": 62.0, "y": 36.0})
d = pack.player_extras(s_dead, (3000.0, 2000.0))["corpse"]
assert 40 < d["yards"] < 60 and d["faster"] in ("corpse", "spirit") and "res_sickness_s" in d, d
# target quest relevance from the KB: an npc that is an objective of an open quest
row = pack.kb.one("SELECT o.quest_id, o.target_id FROM quest_objective o WHERE o.target_kind = 'npc' "
                  "AND o.target_id IS NOT NULL LIMIT 1")
qs = dict(s, target=dict(s["target"], npc=str(row["target_id"])), quests={row["quest_id"]: {"done": False, "obj": [(0, 5)]}})
tq = pack.player_extras(qs, None)["target_quests"]
assert len(tq) == 1, tq
qs["quests"] = {row["quest_id"]: {"done": False, "obj": [(5, 5)]}}
assert pack.player_extras(qs, None)["target_quests"] == [], "a finished objective does not count"

# rules with the knowledge base: training once per level, the unknown flight master nearby once
r2 = rules_mod.Rules({})
r2.started = True
r2.kb_fn = lambda: pack
k2 = {x.kind: x for x in r2.check(state, [])}
assert k2["training_available"].facts == {"spells": names, "count": 3}, k2["training_available"].facts
assert k2["flight_path_nearby"].facts == {"flight_path": "Ratchet, The Barrens",
                                          "yards": fp["unknown_nearby"][0]["yards"]}, k2["flight_path_nearby"].facts
k3 = [x.kind for x in r2.check(state, [])]
assert "training_available" not in k3 and "flight_path_nearby" not in k3, k3

# coach context: names, not ids
from coach import Coach, CoachDeps  # noqa: E402


class _K:
    def quest(self, q, near_map=None, detail=False):
        return {"name": "quest %s" % q}

    def map_name(self, m):
        return "The Barrens"


class _Geo:
    def describe(self, *a, **k):
        return {"text": "here"}

    def area_at(self, *a):
        return None, None


co = Coach.__new__(Coach)
co.state, co.k, co.geo, co.pack = state, _K(), _Geo(), pack
co.deps = CoachDeps()
ctx = co._context(False, state.summary())
p = ctx["player"]
assert p["talent_points_unspent"] == 2
assert [m["spell"] for m in p["training"]["not_trained_yet"]] == names, p["training"]
assert p["flight_paths"]["unknown_nearby"][0]["name"] == "Ratchet, The Barrens"
assert p["consumables"]["counts"]["food"] == 2
assert p["target"]["tapped_by_others"] is True and "counts_for_quests" in p["target"], p["target"]
co.pack = None                                                                  # no KB: raw ids, still useful
p = co._context(False, state.summary())["player"]
assert p["training"] == {"not_trained_spell_ids": [1715, 2551, 5242]} and p["flight_path_node_ids"] == [23, 25, 55], p
assert {c["name"] for c in p["consumables"]} >= {"Tough Hunk of Bread", "Rough Arrow"}
# offline fallback text for the new triggers
line = co.offline([k2["flight_path_nearby"], k2["training_available"]], {"player": {}}, None)
assert line == "Grab the Ratchet, The Barrens flight path, %d yards away. Untrained: Hamstring, Cooking, Battle Shout " \
               "(rank 2)." % fp["unknown_nearby"][0]["yards"], line
print("OK")
