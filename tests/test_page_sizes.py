#!/usr/bin/env python3
"""Every page the addon builds fits a page whole (124 bytes with its "pg=<id>;"), with every field at its
longest: State.lua cuts a longer page, and a cut list page never matches its hash, so the companion would never
take that list. The training list (page P, whose header carries pl=;k=;u=) did that from about 15 missing spells.
Runs the real addon under Lua 5.1 with tests/wowmock, then sends the pages through GameState."""
import os
import re
import sys

from lupa import lua51

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import protocol  # noqa: E402
import wowmock  # noqa: E402
from state import GameState  # noqa: E402

PAGE_MAX = 124                       # State.lua: PAGE_MAX + 6, where RefreshPages cuts

L = lua51.LuaRuntime(unpack_returned_tuples=True)
L.execute(wowmock.MOCK)
# the longest gear entry: a long name, the longest equip slot, and every stat the addon knows, at three digits
L.execute(r"""
MONSTER = "|Hitem:999999|h"
local CODES = {"STRENGTH", "AGILITY", "STAMINA", "INTELLECT", "SPIRIT", "ATTACK_POWER", "RANGED_ATTACK_POWER",
  "HIT_RATING", "CRIT_RATING", "HIT_SPELL_RATING", "CRIT_SPELL_RATING", "HEALTH_REGEN", "POWER_REGEN0",
  "DEFENSE_SKILL_RATING", "DODGE_RATING", "PARRY_RATING"}
local STATS = { ITEM_MOD_DAMAGE_PER_SECOND_SHORT = 999.9, RESISTANCE0_NAME = 9999, ITEM_MOD_SPELL_HEALING_DONE = 999 }
for _, c in ipairs(CODES) do STATS["ITEM_MOD_" .. c .. "_SHORT"] = 999 end
local LINES = {}
for _, school in ipairs({"Fire", "Frost", "Arcane", "Nature", "Shadow"}) do
  LINES[#LINES + 1] = {leftText = "Equip: Increases damage done by " .. school .. " spells and effects by up to 999."}
end
LINES[#LINES + 1] = {leftText = "Equip: Increases damage and healing done by magical spells and effects by up to 999."}
LINES[#LINES + 1] = {leftText = "Main Hand", rightText = "Speed 3.90"}
local getInfo, getInstant, getStats, getTip = GetItemInfo, GetItemInfoInstant, C_Item.GetItemStats, C_TooltipInfo.GetHyperlink
function GetItemInfo(l) if l == MONSTER then
  return string.rep("\195\169", 20), l, 4, 63, 60, "", "", 1, "INVTYPE_WEAPONMAINHAND", 0, 0, 2, 15 end
  return getInfo(l) end
function GetItemInfoInstant(l) if l == MONSTER then return 999999 end return getInstant(l) end
C_Item.GetItemStats = function(l) if l == MONSTER then return STATS end return getStats(l) end
C_TooltipInfo.GetHyperlink = function(l) if l == MONSTER then return {lines = LINES} end return getTip(l) end
function GetInventoryItemLink(u, slot) return MONSTER end
function GetNumQuestChoices() return 6 end
function GetQuestItemLink() return MONSTER end
function GetQuestID() return 999999 end
""")
ns = L.table()
for f in ("Core.lua", "Codec.lua", "State.lua", "Gear.lua", "Extras.lua", "Coach.lua", "Strip.lua"):
    src = open(os.path.join(ROOT, "addon", "Glide", f), encoding="utf-8").read()
    L.eval("function(src, name, a, ns) local f, e = loadstring(src, name) if not f then error(e) end return f(a, ns) end")(
        src, "@" + f, "Glide", ns)
G = L.globals()
G.fire("ADDON_LOADED", "Glide")

# the longest everything: names past their cut, five-digit map sizes, 25 quests with 5 objectives, 40 buffs,
# skill lines, 60 missing six-digit training spells (300 checked, 99 not checkable), 120 flight paths, 80
# skipped steps, 75 talents, 30 consumables
L.execute(r"""
LONG = string.rep("w", 60)
function UnitLevel() return 60 end
function UnitName(u) if u == "target" then return LONG end return string.rep("\195\169", 12) end
function GetBindLocation() return LONG end
function GetZoneText() return LONG end function GetSubZoneText() return LONG end
function UnitClassification() return "rareelite" .. LONG end
C_Map.GetBestMapForUnit = function() return 9999 end
C_Map.GetWorldPosFromMapPos = function(m, v)
  return 0, { GetXY = function() return 99999.9 * (1 - v.y), 99999.9 * (1 - v.x) end } end
RXP.currentGuide.name, RXP.currentGuide.group, RXP.currentGuide.next = LONG, LONG, LONG
RXPCData.currentStep = 9998
local function bigStep()
  local els = {}
  for i = 1, 6 do els[#els + 1] = {tag = "goto", x = 99.94, y = 99.94, zone = 9999} end
  for i = 1, 6 do els[#els + 1] = {tag = "turnin", questId = 900000 + i, completed = true, text = LONG} end
  for i = 1, 10 do els[#els + 1] = {tag = "collect"} end
  return {elements = els}
end
for i = 9998, 10000 do RXP.currentGuide.steps[i] = bigStep() end
C_QuestLog.GetNumQuestLogEntries = function() return 25 end
C_QuestLog.GetInfo = function(i) return {questID = 900000 + i} end
C_QuestLog.GetQuestObjectives = function()
  local o = {} for i = 1, 5 do o[i] = {numFulfilled = 999, numRequired = 999} end return o end
C_QuestLog.IsComplete = function() return true end
C_UnitAuras.GetAuraDataByIndex = function(u, i) if i <= 40 then
  return {name = LONG, spellId = 1000000 + i, expirationTime = 9000, duration = 3600, isFromPlayerOrPlayerPet = true}
  end end
local LINES = {"One-Handed Axes", "Two-Handed Axes", "Bows", "Guns", "One-Handed Maces", "Two-Handed Maces",
  "Polearms", "One-Handed Swords", "Two-Handed Swords", "Staves", "Fist Weapons", "Daggers", "Thrown", "Crossbows",
  "Wands", "Defense", "Unarmed"}
function GetNumSkillLines() return #LINES end
function GetSkillLineInfo(i) return LINES[i], false, nil, 300, nil, nil, 300 end
local train = {}
for i = 1, 300 do train[i] = 100000 + i; if i > 60 then KNOWN[100000 + i] = true end end
for i = 1, 99 do train[#train + 1] = 200000 + i end                     -- no spell name: not checkable
RXP.defaultSpellList = { WARRIOR = { [1] = train } }
C_Spell = { GetSpellInfo = function(id) if id < 200000 then return {name = "Spell " .. id} end end,
            GetSpellSubtext = function() return "Rank 1" end, RequestLoadSpellData = function() end }
RXPCData.flightPaths, RXPCData.stepSkip = {}, {}
for i = 1, 120 do RXPCData.flightPaths[1000 + i] = "Somewhere" end
for i = 1, 80 do RXPCData.stepSkip[9000 + i] = true end
function GetNumTalentTabs() return 3 end function GetNumTalents() return 25 end
function GetTalentInfo(t, i) return LONG, nil, 1 + i % 7, 1 + i % 4, 5, 5 end
""")
G.fire("PLAYER_LOGIN")
G.SlashCmdList.GLIDE("mark " + "z" * 60)
L.execute(r"""
C_Container.GetContainerNumSlots = function(b) return (b == 0 and 30) or (b == 4 and 36) or 0 end
C_Container.GetContainerItemInfo = function(b, s) if b == 0 then return {itemID = 200000 + s, stackCount = 200} end
  if b == 4 then return {hyperlink = MONSTER} end end
C_Item.GetItemInfoInstant = function(id) return id, "", "", "", 0, 0, 0 end
C_Item.GetItemSpell = function() return "Food", 1 end
C_Item.GetItemInfo = function(id) return LONG, "", 1, 60, 60 end
""")
G.fire("BAG_UPDATE_DELAYED")
G.fire("QUEST_COMPLETE")
ns.ExtrasFlush()
ns.ExtrasPoll(5)                                               # Extras' poll (RestedXP settings, skipped steps)
G.ns = ns
L.execute('ns.ReportError("Extras.lua:1: " .. string.rep("e;=|", 80))')      # page E at its longest
L.execute("table.insert(ns.pageHooks, function(b) BUILDERS = b end)")
ns.RefreshPages()
ns.RefreshPages()                                              # buffs: two agreeing reads

built = L.execute("""
local out = {}
for id, fn in pairs(BUILDERS) do out[id] = "pg=" .. id .. ";" .. fn() end
return out
""")
pages = dict(built.items())
sizes = {k: len(v.encode()) for k, v in pages.items()}
for want in ("E", "B", "K", "Z", "T", "G", "S0", "S1", "S2", "Q0", "A0", "W0", "I0", "R0", "P0", "F0", "C0", "N0", "V0"):
    assert want in pages, (want, sorted(pages))
too_long = {k: n for k, n in sizes.items() if n > PAGE_MAX}
assert not too_long, too_long
assert sizes["P0"] > 100 and sizes["G"] > 110 and sizes["S0"] > 110, (sizes["P0"], sizes["G"], sizes["S0"])

# ---- through the strip's page rotation into the companion: every list complete, every field whole ----------
state = GameState()
for _ in range(4 * len(pages)):
    core, page = protocol.parse_payload(ns.Core() + "|" + (ns.NextPage(1000) or ""))
    state.update(core, page)
tr = state.training()
assert tr and len(tr["missing"]) == 60 and tr["checked"] == 300 and tr["unchecked"] == 99, tr
assert tr["missing"] == [100001 + i for i in range(60)] and tr["for_level"] == 60, tr
assert state.flight_paths() == [1001 + i for i in range(120)], state.flight_paths()
assert state.rxp_skipped() == [9001 + i for i in range(80)], state.rxp_skipped()
assert len(state.talents()) == 75 and len(state.consumables()) == 30, (len(state.talents()), len(state.consumables()))
assert len(state.quests) == 25 and len(state.buffs) == 41, (len(state.quests), len(state.buffs))   # + weapon enchant
g = state.pages["G"]
assert g["gn"] == "w" * 40 and g["gg"] == "w" * 40 and g["gx"] == "w" * 27, g
s0 = state.pages["S0"]
assert len(s0["el"]) == 70 and s0["tx"] and set(s0["tx"]) == {"w"}, s0
# gear: every worn, bag and reward entry fits its page whole, keeping the stats that count (weapon dps and speed,
# armor, the main stats) and dropping the least useful (school-only spell damage first)
gear = {k: state.gear[k] for k in ("W", "I", "R")}
assert [len(gear[k]) for k in ("W", "I", "R")] == [17, 36, 6], {k: len(v) for k, v in gear.items()}
for item in gear["W"] + gear["I"] + gear["R"]:
    st = item["stats"]
    assert item["id"] == 999999 and item["name"] == "\u00e9" * 12 and item["loc"] == "INVTYPE_WEAPONMAINHAND", item
    assert st["DP"] == 999.9 and st["WS"] == 3.9 and st["AR"] == 9999 and st["ST"] == 999 and "SH" not in st, st
assert gear["I"][-1]["key"] == "4.36", gear["I"][-1]
ranks = state.skills()["ranks"]
assert ranks and all(re.fullmatch(r"[A-Za-z -]+ 300/300", r) for r in ranks), ranks
# vendor treasure (page D): the real Data/VendorTreasures.lua, standing at each vendor in turn; gear entries are the
# longest gear above, potions the longest names. Every vendor's whole list arrives, no page over 124 bytes.
src = open(os.path.join(ROOT, "addon", "Glide", "Data", "VendorTreasures.lua"), encoding="utf-8").read()
L.eval("function(src, ns) local f = assert(loadstring(src, '@VendorTreasures.lua')) return f('Glide', ns) end")(src, ns)
L.execute(r"""
C_Item.GetItemInfo = function(id)
  if id % 2 == 0 then return GetItemInfo(MONSTER) end
  return LONG, "|Hitem:6948|h", 1, 60, 60, "", "", 1, "", 0, 0, 0, 0                  -- a potion: class 0
end
VENDOR = 1
C_Map.GetBestMapForUnit = function() return ns.VENDOR_TREASURES[VENDOR][1] end
C_Map.GetPlayerMapPosition = function() local v = ns.VENDOR_TREASURES[VENDOR]
  return { GetXY = function() return v[2] / 100, v[3] / 100 end } end
function UnitFactionGroup() return ns.VENDOR_TREASURES[VENDOR][6] == "A" and "Alliance" or "Horde" end
""")
vendors = int(L.eval("#ns.VENDOR_TREASURES"))
assert vendors > 20, vendors
biggest = 0
for i in range(1, vendors + 1):
    G.VENDOR = i
    ns.ExtrasPoll(5)
    ns.RefreshPages()
    d = {pid: "pg=%s;%s" % (pid, fn()) for pid, fn in L.execute("local o = {} for id, fn in pairs(BUILDERS) do "
                                                               "if id:sub(1, 1) == 'D' then o[id] = fn end end "
                                                               "return o").items()}
    biggest = max([biggest] + [len(p.encode()) for p in d.values()])
    assert all(len(p.encode()) <= PAGE_MAX for p in d.values()), (i, d)
    st = GameState()
    for p in d.values():
        st.update({}, protocol.parse_kv(p))
    vt = st.vendor_treasure()
    want = len(L.eval("ns.VENDOR_TREASURES[%d][7]" % i))
    # (two vendors at one spot: the nearer is the one listed)
    assert vt and len(vt["items"]) == len(L.eval("ns.VENDOR_TREASURES[%d][7]" % vt["vendor"])), (i, vt)
    assert all(it["stats"].get("DP") == 999.9 for it in vt["items"] if it["loc"] != "POTION"), vt
print("vendors checked:", vendors, "largest D page:", biggest)
print("largest pages:", sorted(sizes.items(), key=lambda kv: -kv[1])[:6])
print("OK")
