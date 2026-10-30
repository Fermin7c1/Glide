#!/usr/bin/env python3
"""RestedXP extras (addon 0.13): talents, RestedXP's settings and skipped steps, vendor treasures.

Extras.lua under Lua 5.1 with a mocked WoW API -> strip pages -> GameState; then the companion side: the next talent
from a RestedXP build (talents.py), live settings deciding the route (knowledge.api.player_from), the vendor treasure
rule (rules.py + gear.py), RestedXP's levelling history read from SavedVariables (rxp_saved.py), and the generated
vendor table (tools/build_vendor_treasures.py <-> companion/vendor_treasures.py). Needs lupa.

Run: python3 tests/test_rxp_extras.py   (prints OK)
"""
import os
import sys
import tempfile

from lupa import lua51

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
sys.path.append(os.path.join(ROOT, "companion"))      # after ROOT: `knowledge` stays the package
sys.path.append(os.path.join(ROOT, "tools"))
import wowmock  # noqa: E402
import protocol  # noqa: E402
import rules as rules_mod  # noqa: E402
import talents  # noqa: E402
import rxp_saved  # noqa: E402
import vendor_treasures  # noqa: E402
import build_vendor_treasures  # noqa: E402
from gear import Gear  # noqa: E402
from state import GameState  # noqa: E402
from knowledge.api import player_from  # noqa: E402

L = lua51.LuaRuntime(unpack_returned_tuples=True)
L.execute(wowmock.MOCK)
L.execute(r"""
function UnitLevel() return 12 end
function UnitFactionGroup() return "Alliance" end
-- talents: Arms tab 1 (tier, column, rank, max) and Fury tab 2
TAL = { { {"Deflection", 1, 2, 0, 5}, {"Improved Rend", 1, 3, 0, 3} },
        { {"Cruelty", 1, 3, 2, 5}, {"Booming Voice", 1, 2, 0, 5} } }
function GetNumTalentTabs() return 2 end
function GetNumTalents(t) return #TAL[t] end
function GetTalentInfo(t, i) local x = TAL[t][i] return x[1], "icon", x[2], x[3], x[4], x[5] end
RXP.settings = { profile = { soloSelfFound = true, enableGroupQuests = false } }
RXPCData.stepSkip = { [12] = true, [3] = true, [40] = false }
-- a vendor treasure 2% of the map away, and one on another map
C_Map.GetBestMapForUnit = function() return 1429 end
C_Map.GetPlayerMapPosition = function() return { GetXY = function() return 0.4200, 0.6400 end } end
VITEMS = { [2488] = {"Gladius", "|Hitem:2488|h", 2, 15, 11, "INVTYPE_WEAPON", 2, 7, {ITEM_MOD_DAMAGE_PER_SECOND_SHORT=9.5}},
           [929] = {"Healing Potion", "|Hitem:929|h", 1, 22, 12, "", 0, 0, {}} }
local base = C_Item.GetItemStats
C_Item.GetItemInfo = function(x)
  local i = VITEMS[x]
  if not i then for _, v in pairs(VITEMS) do if v[2] == x then i = v end end end
  if not i then return GetItemInfo(x) end
  return i[1], i[2], i[3], i[4], i[5], "", "", 1, i[6], 0, 0, i[7], i[8] end
C_Item.GetItemInfoInstant = function(l) for id, v in pairs(VITEMS) do if v[2] == l then return id end end return GetItemInfoInstant(l) end
C_Item.GetItemStats = function(l) for _, v in pairs(VITEMS) do if v[2] == l then return v[9] end end return base(l) end
local tip = C_TooltipInfo.GetHyperlink
C_TooltipInfo.GetHyperlink = function(l) for _, v in pairs(VITEMS) do if v[2] == l then return { lines = {} } end end return tip(l) end
""")
ns = L.table()
ns.VENDOR_TREASURES = L.eval('{ { 1429, 41.0, 63.0, "Corina Steele", "G", "A", { 2488, 929 } },'
                             '  { 1411, 50.0, 50.0, "Far Away", "G", "N", { 2488 } } }')
for f in ("Core.lua", "Codec.lua", "State.lua", "Gear.lua", "Extras.lua", "Coach.lua", "Strip.lua"):
    src = open(os.path.join(ROOT, "addon", "Glide", f), encoding="utf-8").read()
    L.eval("function(src, name, a, ns) local f, e = loadstring(src, name) if not f then error(e) end return f(a, ns) end")(
        src, "@" + f, "Glide", ns)
G = L.globals()
G.fire("ADDON_LOADED", "Glide")
G.fire("PLAYER_LOGIN")
ns.ExtrasFlush()
ns.ExtrasPoll(3.5)                                      # the 3 s poll (RestedXP settings, skipped steps, vendors)


def frames(state, n=80):
    ns.RefreshPages()
    seen = set()
    for _ in range(n):
        core, page = protocol.parse_payload(ns.Core() + "|" + (ns.NextPage(1000) or ""))
        state.update(core, page)
        seen.add(page.get("pg"))
    return seen


state = GameState()
seen = frames(state)
assert {"N0", "V0", "D0", "X"} <= seen, seen
s = state.summary()
tal = {t["name"]: t for t in s["talents"]}
assert tal["Cruelty"] == {"pos": "213", "rank": 2, "max": 5, "name": "Cruelty"}, tal
assert s["rxp_settings"] == {"ssf": True, "group_quests": False}, s["rxp_settings"]
assert s["rxp_skipped"] == [3, 12], s["rxp_skipped"]
vt = s["vendor_treasure"]
assert vt and vt["vendor"] == 1 and [i["name"] for i in vt["items"]] == ["Gladius", "Healing Potion"], vt
assert vt["items"][1]["loc"] == "POTION" and vt["items"][0]["loc"] == "INVTYPE_WEAPON", vt

# live RestedXP settings win over the config and the defaults
p = player_from(s, {"ssf": False, "group_quests": True})
assert p["ssf"] is True and p["group_quests"] is False, p

# ---- talents: RestedXP's build -> the next pick, checked against the character's talents --------------------
GUIDE = '''addon.talents.RegisterGuide([[
#name Arms 10-30
#next Arms 30-55
#minLevel 10
#maxLevel 30
#order 4

level -- Cruelty (Rank 1)
    .talent 2,1,3,1
level -- Cruelty (Rank 2)
    .talent 2,1,3,2
level -- Cruelty (Rank 3)
    .talent 2,1,3,3
level -- Deflection or Improved Rend
    #optional
    .talent 1,1,2,1 -- Deflection
    .talent 1,1,3,1 -- Improved Rend
level -- Booming Voice (Rank 1)
    .talent 2,1,2,1
]])
addon.talents.RegisterGuide([[
#name Arms (Survival) 10-30
#hardcore
#order 1

level -- Booming Voice (Rank 1)
    .talent 2,1,2,1
]])'''
builds = talents.parse_file(GUIDE)
assert [b["name"] for b in builds] == ["Arms 10-30", "Arms (Survival) 10-30"] and builds[1]["hardcore"]
assert builds[0]["steps"][3]["optional"] and [x["label"] for x in builds[0]["steps"][3]["picks"]] == \
    ["Deflection", "Improved Rend"]
pick = talents.next_pick(builds, s["talents"], 12)
assert pick == {"build": "Arms 10-30", "talent": "Cruelty", "rank": 3, "level": 12, "due": True,
                "matches_forever": True}, pick
assert talents.describe(pick) == "Cruelty, rank 3"
after = [dict(t, rank=3) if t["name"] == "Cruelty" else t for t in s["talents"]]
pick = talents.next_pick(builds, after, 12)
assert pick["talent"] == "Deflection or Improved Rend" and pick["level"] == 13 and not pick["due"], pick
after = [dict(t, rank=1) if t["name"] == "Improved Rend" else t for t in after]
assert talents.next_pick(builds, after, 14)["talent"] == "Booming Voice", "one optional choice is enough"
# a spot that differs on Forever (another talent there): reported, not suggested as certain
odd = [dict(t, name="Unbridled Wrath") if t["name"] == "Cruelty" else t for t in s["talents"]]
assert talents.next_pick(builds, odd, 12)["matches_forever"] is False
assert talents.next_pick(builds, s["talents"], 9) is None and talents.next_pick(builds, None, 12) is None

# ---- the vendor treasure rule: gear scored like quest rewards, once per vendor ----------------------------------
src = '''local ZONE_ELWYNN = 1429;
local ZONE_DUROTAR = 1411;
    Frame:SetZoneNPCData(ZONE_ELWYNN, L"Corina Steele", 41.0, 63.0, CLASS_GEAR, ALLIANCE, {2488, 929})
    Frame:SetZoneNPCData(ZONE_DUROTAR, L"Far Away", 50, 50, CLASS_POTION, NEUTRAL,
                         {929})'''
vendors = build_vendor_treasures.parse(src)
assert vendors == [(1429, 41.0, 63.0, "Corina Steele", "G", "A", [2488, 929]),
                   (1411, 50.0, 50.0, "Far Away", "P", "N", [929])], vendors
tmp = os.path.join(tempfile.mkdtemp(), "VendorTreasures.lua")
open(tmp, "w", encoding="utf-8").write(build_vendor_treasures.lua(vendors))
vendor_treasures.PATH = tmp
vendor_treasures.glideconfig.wow_dir = lambda root=None: None     # not this PC's config.json: no WoW folder set
assert vendor_treasures.load(tmp)[0]["name"] == "Corina Steele" and vendor_treasures.vendor(2)["kind"] == "P"
assert vendor_treasures.vendor(3) is None
# the game numbers its vendors by the INSTALLED addon's table: a copy there that differs (an older addon, another
# order) wins over the project's; without one (no WoW folder, no addon installed) the project's copy is used
wow = tempfile.mkdtemp()
assert vendor_treasures.installed_path(wow).startswith(wow) and vendor_treasures.vendor(1, wow)["name"] == "Corina Steele"
os.makedirs(os.path.dirname(vendor_treasures.installed_path(wow)))
with open(vendor_treasures.installed_path(wow), "w", encoding="utf-8") as fh:
    fh.write(build_vendor_treasures.lua(vendors[::-1]))
assert vendor_treasures.vendor(1, wow)["name"] == "Far Away" and vendor_treasures.vendor(2, wow)["name"] == "Corina Steele"
assert vendor_treasures.vendor(1)["name"] == "Corina Steele", "no WoW folder set: the project's copy"
st = os.stat(vendor_treasures.installed_path(wow))
with open(vendor_treasures.installed_path(wow), "w", encoding="utf-8") as fh:      # the addon updated in place
    fh.write(build_vendor_treasures.lua(vendors[:1]))
os.utime(vendor_treasures.installed_path(wow), (st.st_atime, st.st_mtime + 5))
assert vendor_treasures.vendor(1, wow)["name"] == "Corina Steele" and vendor_treasures.vendor(2, wow) is None,     "read again when the file changes"
# the generated file in the addon is complete and readable
shipped = vendor_treasures.load(os.path.join(ROOT, "addon", "Glide", "Data", "VendorTreasures.lua"))
assert len(shipped) >= 80 and all(v["items"] for v in shipped), len(shipped)

r = rules_mod.Rules({})
r.started = True
r.gear = Gear({"kind": "Speedrun"})
state.map_sizes[1429] = (3470.0, 2313.0)
out = []
r._vendor(state, dict(s, consumables=[]), False, out)
assert len(out) == 1 and out[0].kind == "vendor_treasure", out
f = out[0].facts
assert f["vendor"] == "Corina Steele" and f["upgrades"] and f["upgrades"][0]["item"] == "Gladius", f
assert f["potions"] == ["Healing Potion"] and f["yards"] and f["direction"], f
out = []
r._vendor(state, dict(s, consumables=[]), False, out)
assert out == [], "once per vendor"
r2 = rules_mod.Rules({})
r2.gear = Gear({"kind": "Speedrun"})
r2._vendor(state, dict(s, consumables=[{"kind": "p", "count": 8, "req": 5}]), True, out)
assert out == [], "never in combat"
r2._vendor(state, dict(s, consumables=[{"kind": "p", "count": 8, "req": 5}]), False, out)
assert out and out[0].facts["potions"] == [], "enough potions: only the gear"

# ---- RestedXP's levelling history from SavedVariables ----------------------------------------------------------
wow = tempfile.mkdtemp()
char = os.path.join(wow, "WTF", "Account", "1#1", "Realm", "Testwar", "SavedVariables")
acct = os.path.join(wow, "WTF", "Account", "1#1", "SavedVariables")
os.makedirs(char)
os.makedirs(acct)
open(os.path.join(char, "RXPGuides.lua"), "w", encoding="utf-8").write('''
RXPCTrackingData = {
["profileKeys"] = { ["Testwar - PvE"] = "Testwar - PvE", },
["profiles"] = { ["Testwar - PvE"] = { ["levels"] = {
  { ["deaths"] = 0, ["groupExperience"] = 0, ["mobs"] = { ["Elwynn"] = { ["xp"] = 400, ["count"] = 9 } },
    ["quests"] = {}, ["timestamp"] = { ["started"] = 0, ["finished"] = 240 } },
  { ["deaths"] = 1, ["groupExperience"] = 50, ["mobs"] = { ["Elwynn"] = { ["xp"] = 300, ["count"] = 6 } },
    ["quests"] = { ["Elwynn"] = { ["33"] = 200, ["7"] = 85 } }, ["timestamp"] = { ["started"] = 240, ["finished"] = 900 } },
  { ["deaths"] = 0, ["groupExperience"] = 0, ["timestamp"] = { ["started"] = 900 } },
} } } }
''')
open(os.path.join(acct, "RXPGuides.lua"), "w", encoding="utf-8").write('''
RXPDB = { ["profiles"] = { ["global"] = { ["reports"] = { ["splits"] = {
  ["Alt|ROGUE|Realm"] = { ["history"] = { ["levels"] = { { ["text"] = "Level 2   03:00", ["duration"] = 180 },
                                                          { ["text"] = "Level 3   20:00", ["duration"] = 1200 } } } },
  ["Broken|MAGE|Realm"] = 7,
} } } } }
''')
h = rxp_saved.history(wow, "Testwar")
assert h["levels"] == [
    {"level": 1, "minutes": 4.0, "quest_xp": 0, "mob_xp": 400, "group_xp": 0, "deaths": 0, "best_minutes": 3.0,
     "best_character": "Alt"},
    {"level": 2, "minutes": 11.0, "quest_xp": 285, "mob_xp": 300, "group_xp": 50, "deaths": 1, "best_minutes": 20.0,
     "best_character": "Alt"}], h["levels"]
assert rxp_saved.history(wow, "Nobody") == {"levels": [], "bests": h["bests"]}
assert rxp_saved.history(None, "Testwar") is None

# the waypoint RestedXP's arrow points at (core aw, 0.13.1) wins over the step's first goto
core, _ = protocol.parse_payload(ns.Core())
assert "aw" not in core, core                                   # no arrow element yet
L.execute('RXP.arrowFrame = { element = { zone = 1429, x = 47.31, y = 61.5, step = { active = true } } }')
core, _ = protocol.parse_payload(ns.Core())
assert core["aw"] == "1429:47.3:61.5", core
st = GameState()
st.update(core, {"pg": "S0", "i": "4", "el": "go:1429:40.0:60.0,go:1429:47.3:61.5", "tx": ""})
assert st.goto_target() == {"tag": "goto", "map": 1429, "x": 47.3, "y": 61.5, "arrow": True}, st.goto_target()
L.execute('RXP.arrowFrame.element.step.active = false')
assert "aw" not in protocol.parse_payload(ns.Core())[0]
print("OK")
