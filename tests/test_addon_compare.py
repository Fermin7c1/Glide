#!/usr/bin/env python3
"""The upgrade line on item tooltips (addon Compare.lua) under Lua 5.1 with the mocked WoW API: with the WoWSims
weights the Glide app wrote for this character (Data/SimWeights.lua's ns.simWeights), an item's tooltip says what it
would change in DPS against what is worn in its slot (Gear.lua reads the stats): a ring against the weaker of the two,
a two-hander against main and off hand, a worse item in red, an equal one "no change"; nothing for the item worn, one
the character can't use, below 60, another class or character, or with the switch off (/glide compare off).
Under it, the source line: where the item comes from, from the facts the Glide app wrote into Data/Live.lua
(ns.live.items), wrapped, grey for an item not out yet or with no known source, " (web, unconfirmed)" after a web
fact; it shows without weights, below 60, on the item worn, on a chat link (also one whose data hasn't come yet) and
when the upgrade line fails, and not with its own switch off (/glide sources off), for an item with no fact, nor
without Live.lua. Needs lupa."""
import os
import sys

from lupa import lua51

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wowmock  # noqa: E402

L = lua51.LuaRuntime(unpack_returned_tuples=True)
L.execute(wowmock.MOCK)
L.execute(r"""
LEVEL, CLASS, NAME = 60, "MAGE", "Nine"
function UnitName() return NAME end
function UnitClass() return "Mage", CLASS end
function UnitLevel() return LEVEL end
-- items: link -> equip location and stats (GetItemStats' keys)
local function link(id, name) return "|cffa335ee|Hitem:" .. id .. "::::::::60:::::|h[" .. name .. "]|h|r" end
ITEMS = {}
function item(id, name, loc, stats) local l = link(id, name); ITEMS[l] = { loc = loc, stats = stats }; return l end
WORN = {}
function GetInventoryItemLink(_, slot) return WORN[slot] end
UNUSABLE = {}
C_Item = { GetItemInfoInstant = function(l) local i = ITEMS[l]; return tonumber(l:match("item:(%d+)")), "", "", i and i.loc end,
           GetItemStats = function(l) return (ITEMS[l] or {}).stats end,
           GetItemInfo = function(l) local i = ITEMS[l]; if i and not i.loading then return l:match("%[(.-)%]") end end }
C_PlayerInfo = { CanUseItem = function(id) return not UNUSABLE[id] end }
-- the tooltip: TooltipDataProcessor's item post-call, GameTooltip's lines
POSTCALLS = {}
Enum = Enum or {}
Enum.TooltipDataType = { Item = 0 }
TooltipDataProcessor = { AddTooltipPostCall = function(kind, fn) POSTCALLS[kind] = fn end }
GameTooltip = { lines = {} }
function GameTooltip:GetItem() return "x", self.link end
function GameTooltip:AddLine(t, r, g, b, wrap) self.lines[#self.lines + 1] = { text = t, r = r, g = g, b = b, wrap = wrap } end
ItemRefTooltip = { lines = {}, GetItem = GameTooltip.GetItem, AddLine = GameTooltip.AddLine }
-- the lines Glide adds to a tooltip showing link l (data: the tooltip data the post-call gets)
function lines(l, tip, data)
  tip = tip or GameTooltip
  tip.link, tip.lines = l, {}
  POSTCALLS[0](tip, data or {})
  return tip.lines
end
function show(l, tip) return lines(l, tip)[1] end
printed = {}
function print(...) local t = {} for i = 1, select("#", ...) do t[#t + 1] = tostring(select(i, ...)) end
  printed[#printed + 1] = table.concat(t, " ") end
""")
ns = L.table()
toc = [ln.strip() for ln in open(os.path.join(ROOT, "addon", "Glide", "Glide.toc"), encoding="utf-8")
       if ln.strip().endswith(".lua") and (ln.strip() in ("Data\\SimWeights.lua", "Data\\Live.lua") or
                                            not ln.startswith(("Data", "Recorder", "Scanner", "RXPDump")))]
assert toc.index("Data\\SimWeights.lua") + 1 == toc.index("Data\\Live.lua"), toc          # Live.lua right after it
assert toc.index("Data\\Live.lua") < toc.index("Gear.lua") < toc.index("Compare.lua") < toc.index("Panel.lua"), toc
for f in toc:
    src = open(os.path.join(ROOT, "addon", "Glide", *f.split("\\")), encoding="utf-8").read()
    L.eval("function(src, name, a, ns) local f, e = loadstring(src, name) if not f then error(e) end return f(a, ns) end")(
        src, "@" + f, "Glide", ns)
G = L.globals()
G.fire("ADDON_LOADED", "Glide")
G.fire("PLAYER_LOGIN")
assert len(list(ns.simWeights.keys())) == 0, "the addon ships no weights"
assert ns.live.v == 1 and ns.live.gen == 0 and len(list(ns.live["items"].keys())) == 0, "nor any item facts"
show, lines = G.show, G.lines

# the weights the app wrote for Nine: spell power 1, intellect 0.865, 1% spell crit 9.038; 0.52 DPS per spell power
ns.simWeights = L.eval('{ ["Nine"] = { class = "MAGE", spec = "Arcane", dps = 600, perRef = 0.52, wowsims = "v1.0.4", '
                       'w = { SP = 1, IN = 0.865, CS = 9.038 } } }')
item = G.item
old = item(1001, "Band of Old", "INVTYPE_FINGER", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 10 }"))
better = item(1002, "Loop of Better", "INVTYPE_FINGER", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 20 }"))
G.WORN[11], G.WORN[12] = old, better
new = item(1003, "Ring of New", "INVTYPE_FINGER", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 15, ITEM_MOD_CRIT_SPELL_RATING_SHORT = 1 }"))

# a ring: against the weaker of the two (Band of Old: 8.65 EP; the new one 12.975 + 9.038): +13.36 EP = +6.9 DPS
ln = show(new)
assert ln and ln.text == "Glide (Arcane): +6.9 DPS (+1.2%) vs Band of Old" and ln.g > ln.r, ln and ln.text
# the same on a chat link's tooltip
assert show(new, G.ItemRefTooltip).text == ln.text

# a two-hander against main and off hand together: 30 int against the off hand's 10: +17.3 EP = +9.0 DPS
mh = item(2002, "Old Sword", "INVTYPE_WEAPONMAINHAND", L.eval("{}"))
G.WORN[16] = mh
G.WORN[17] = item(2003, "Orb", "INVTYPE_HOLDABLE", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 10 }"))
staff = item(2004, "Great Staff", "INVTYPE_2HWEAPON", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 30 }"))
ln = show(staff)
assert ln.text == "Glide (Arcane): +9.0 DPS (+1.5%) vs Old Sword", ln.text        # (30 - 10 int) * 0.865 * 0.52
# holding the staff, an off hand would take a different main hand: no line
orb2 = item(2005, "Bright Orb", "INVTYPE_HOLDABLE", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 12 }"))
G.WORN[16], G.WORN[17] = staff, None
assert show(orb2) is None, "an off hand beside a two-hander"
G.WORN[16], G.WORN[17] = mh, G.item(2003, "Orb", "INVTYPE_HOLDABLE", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 10 }"))
assert show(orb2).text == "Glide (Arcane): +0.9 DPS (+0.1%) vs Orb", show(orb2).text

# a worse item in red; an equal one "no change"; an empty slot
worse = item(1004, "Twig Ring", "INVTYPE_FINGER", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 2 }"))
ln = show(worse)
assert ln.text == "Glide (Arcane): -3.6 DPS (-0.6%) vs Band of Old" and ln.r > ln.g, ln.text
same = item(1005, "Band of Same", "INVTYPE_FINGER", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 10 }"))
assert show(same).text == "Glide (Arcane): no change vs Band of Old", show(same).text
cloak = item(3001, "Cape", "INVTYPE_CLOAK", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 10 }"))
assert show(cloak).text == "Glide (Arcane): +4.5 DPS (+0.7%) vs an empty slot", show(cloak).text

# a chat link whose data the client hasn't yet: no line, and nothing kept; the line once the data came
linked = item(3002, "Far Ring", "INVTYPE_FINGER", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 30 }"))
G.ITEMS[linked].loading = True
assert show(linked, G.ItemRefTooltip) is None, "still loading"
G.ITEMS[linked].loading = None
assert show(linked, G.ItemRefTooltip).text == "Glide (Arcane): +9.0 DPS (+1.5%) vs Band of Old", "after it came"

# nothing: the item worn, one the character can't use, a bag, below 60, another class or character, switched off
assert show(old) is None, "the ring you wear"
plate = item(4001, "Plate Helm", "INVTYPE_HEAD", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 50 }"))
G.UNUSABLE[4001] = True
assert show(plate) is None, "can't use it"
assert show(item(4002, "Bag", "INVTYPE_BAG", L.eval("{}"))) is None, "not gear"
for var, val in (("LEVEL", 59), ("CLASS", "PRIEST"), ("NAME", "Dragon")):
    old_val = G[var]
    G[var] = val
    assert show(new) is None, var
    G[var] = old_val
G.SlashCmdList.GLIDE("compare off")
assert show(new) is None and ns.CompareOn() is False, "switched off"
G.SlashCmdList.GLIDE("compare on")
assert show(new).text.startswith("Glide (Arcane): +6.9 DPS"), "back on"
assert any("WoWSims v1.0.4, Arcane" in p for p in G.printed.values()), list(G.printed.values())

# ---- the source line: where an item comes from (Data/Live.lua, ns.live.items) --------------------------------------
assert [ln.text for ln in lines(new).values()] == ["Glide (Arcane): +6.9 DPS (+1.2%) vs Band of Old"], "no facts yet"
items = ns.live["items"]                       # (lupa: .items is the Python method)
items[1003] = L.eval('{ "drops from Darkmaster Gandling, Scholomance", a = "n" }')
got = list(lines(new).values())
assert [ln.text for ln in got] == ["Glide (Arcane): +6.9 DPS (+1.2%) vs Band of Old",
                                   "Glide: drops from Darkmaster Gandling, Scholomance"], [ln.text for ln in got]
src = got[1]
assert src.wrap is True and (src.r, src.g, src.b) == (0.4, 0.8, 1), (src.wrap, src.r, src.g, src.b)
assert not got[0].wrap, "the upgrade line is as it was"
# on a chat link's tooltip too
assert [ln.text for ln in lines(new, G.ItemRefTooltip).values()][1] == src.text
# none of the upgrade line's limits: the item worn, no weights, below 60, a chat link whose data hasn't come yet
items[1001] = L.eval('{ "sold by Deliana, Ironforge", a = "n" }')
assert [ln.text for ln in lines(old).values()] == ["Glide: sold by Deliana, Ironforge"], "the ring you wear"
G.LEVEL = 30
assert [ln.text for ln in lines(new).values()] == [src.text], "below 60"
G.LEVEL = 60
weights = ns.simWeights
ns.simWeights = L.table()
assert [ln.text for ln in lines(new).values()] == [src.text], "no weights"
ns.simWeights = weights
far = item(3003, "Distant Ring", "INVTYPE_FINGER", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 30 }"))
G.ITEMS[far].loading = True
items[3003] = L.eval('{ "reward of General Drakkisath\'s Demise", a = "n" }')
got = [ln.text for ln in lines(far, G.ItemRefTooltip).values()]
assert got == ["Glide: reward of General Drakkisath's Demise"], got
# a tooltip without a link: the item id the tooltip data carries
assert [ln.text for ln in lines(None, G.GameTooltip, L.eval("{ id = 1003 }")).values()] == [src.text], "data.id"
# an item that isn't out yet, or with no known source: grey; a fact found only on the web says so
items[1003] = L.eval('{ "drops from Golemagg the Incinerator, Molten Core", a = "l", w = 1 }')
ln = list(lines(new).values())[1]
assert ln.text == "Glide: drops from Golemagg the Incinerator, Molten Core (web, unconfirmed)", ln.text
assert (ln.r, ln.g, ln.b) == (0.6, 0.6, 0.6), (ln.r, ln.g, ln.b)
items[1003] = L.eval('{ "no known source; binds when equipped, so the auction house may have it", a = "x" }')
ln = list(lines(new).values())[1]
assert ln.text.endswith("the auction house may have it") and ln.r == 0.6, ln.text
items[1003] = L.eval('{ "drops from Darkmaster Gandling, Scholomance", a = "n" }')
# when the upgrade line fails, the source line still shows
ns.simWeights.Nine.perRef = "junk"
assert [ln.text for ln in lines(new).values()] == [src.text], "the upgrade line failed alone"
ns.simWeights.Nine.perRef = 0.52
# nothing: an item with no fact, a broken entry, its own switch off, no Live.lua
assert [ln.text for ln in lines(better).values()] == [], "no fact for it (and it's worn)"
for junk in ('"text"', "{}", '{ "" }', "{ 12 }"):
    items[1005] = L.eval(junk)
    assert [ln.text for ln in lines(same).values()] == ["Glide (Arcane): no change vs Band of Old"], junk
G.SlashCmdList.GLIDE("sources off")
assert [ln.text for ln in lines(new).values()] == ["Glide (Arcane): +6.9 DPS (+1.2%) vs Band of Old"], "switched off"
assert ns.SourcesOn() is False and G.GlideLinkDB.sources.enabled is False
G.SlashCmdList.GLIDE("compare off")
G.SlashCmdList.GLIDE("sources on")
assert [ln.text for ln in lines(new).values()] == [src.text], "each line has its own switch"
G.SlashCmdList.GLIDE("compare on")
assert any("where items come from, on item tooltips" in p and "items known" in p for p in G.printed.values())
live = ns.live
ns.live = None
assert len(list(lines(new).values())) == 1, "no Live.lua: the upgrade line alone"
# never loaded (an addon updated while WoW ran: /reload keeps the old file list): a restart, not a /reload, loads it
G.SlashCmdList.GLIDE("sources")
said = list(G.printed.values())[-1]
assert "aren't loaded: restart WoW once" in said and "/reload" not in said, said
# loaded, nothing in it yet: the app finds them, then a /reload
ns.live = L.eval("{ v = 1, gen = 0, t = 0, items = {}, chars = {} }")
G.SlashCmdList.GLIDE("sources")
said = list(G.printed.values())[-1]
assert "none known yet" in said and "then /reload" in said, said
ns.live = live
print("OK")
