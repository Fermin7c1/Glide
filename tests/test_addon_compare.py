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
without Live.lua. On an item the Glide app ranked for this character (ns.live.chars[Name-Realm or Name].bis), the BiS
line takes the source line's place ("Glide BiS #2 for Finger, +6.1 DPS: <where it comes from>", "you wear it" when
worn, "about the same as yours" for "~", "with its set" for "s", the profession it needs), keyed by the realm with or
without its spaces, the
source line back for a broken entry, a failing BiS line, or with its own switch off (/glide bis off). Needs lupa."""
import os
import sys
import tempfile

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

# ---- the BiS line: where an item ranks for this character (ns.live.chars[key].bis[id] = { slot, rank, d, "~" }) ------
assert ns.BisOn() is True, "on by default"


def texts(got):
    return [ln.text for ln in got.values()]


up = "Glide (Arcane): +6.9 DPS (+1.2%) vs Band of Old"
gandling = "drops from Darkmaster Gandling, Scholomance"
chars = ns.live.chars
assert texts(lines(new)) == [up, "Glide: " + gandling], "nothing ranked: the source line"
chars.Nine = L.eval('{ bis = { [1003] = { "Finger", 2, 6.1 } } }')
got = list(lines(new).values())
assert [ln.text for ln in got] == [up, "Glide BiS #2 for Finger, +6.1 DPS: " + gandling], [ln.text for ln in got]
assert got[1].wrap is True and (got[1].r, got[1].g, got[1].b) == (0.4, 0.8, 1), (got[1].wrap, got[1].r)
bis_text = got[1].text
# on a chat link's tooltip, and on a tooltip without a link (the item id its data carries)
assert texts(lines(new, G.ItemRefTooltip)) == [up, bis_text]
assert texts(lines(None, G.GameTooltip, L.eval("{ id = 1003 }"))) == [bis_text], "data.id"
# about the same as what you wear (within the sims' noise); a worse one says so in numbers
chars.Nine.bis[1003] = L.eval('{ "Finger", 3, 0.2, "~" }')
assert texts(lines(new))[1] == "Glide BiS #3 for Finger, about the same as yours: " + gandling, texts(lines(new))
chars.Nine.bis[1003] = L.eval('{ "Finger", 5, -1.54 }')
assert texts(lines(new))[1] == "Glide BiS #5 for Finger, -1.5 DPS: " + gandling, texts(lines(new))
# a set piece best only with another piece of its set (alone it loses): no number, "with its set"
chars.Nine.bis[1003] = L.eval('{ "Finger", 1, nil, "s" }')
assert texts(lines(new))[1] == "Glide BiS #1 for Finger, with its set: " + gandling, texts(lines(new))
chars.Nine.bis[1003] = L.eval('{ "Finger", 1, nil, "s", "Tailoring" }')
assert texts(lines(new))[1] == "Glide BiS #1 for Finger, with its set (needs Tailoring to wear): " + gandling
chars.Nine.bis[1003] = L.eval('{ "Finger", 5, -1.54 }')
# the item you wear: the addon sees it (whatever the entry's numbers say), with no source and no upgrade line
chars.Nine.bis[1001] = L.eval('{ "Finger", 1, 0.3, "~" }')
got = list(lines(old).values())
assert [ln.text for ln in got] == ["Glide BiS #1 for Finger: you wear it"], [ln.text for ln in got]
assert (got[0].r, got[0].g, got[0].b) == (0.4, 0.8, 1)
G.WORN[11] = None
assert texts(lines(old)) == [up.replace("+6.9 DPS (+1.2%) vs Band of Old", "+4.5 DPS (+0.7%) vs an empty slot"),
                             "Glide BiS #1 for Finger, about the same as yours: sold by Deliana, Ironforge"], \
    texts(lines(old))
G.WORN[11] = old
# no known source: the rank alone; a web fact says so; an item not out yet or with no known source: grey
crown = item(5001, "Sorcerer's Crown", "INVTYPE_HEAD", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 25 }"))
chars.Nine.bis[5001] = L.eval('{ "Head", 2, 6.1 }')
got = texts(lines(crown))
assert len(got) == 2 and got[0].startswith("Glide (Arcane): +11.") and got[1] == "Glide BiS #2 for Head, +6.1 DPS", got
items[5001] = L.eval('{ "sold by Deliana, Ironforge", a = "n", w = 1 }')
assert texts(lines(crown))[1] == "Glide BiS #2 for Head, +6.1 DPS: sold by Deliana, Ironforge (web, unconfirmed)"
items[5001] = L.eval('{ "no known source; binds when equipped, so the auction house may have it", a = "x" }')
ln = list(lines(crown).values())[1]
assert ln.text == ("Glide BiS #2 for Head, +6.1 DPS: no known source; binds when equipped, so the auction house may "
                   "have it") and (ln.r, ln.g, ln.b) == (0.6, 0.6, 0.6), (ln.text, ln.r)
# a profession it needs to wear (one the character doesn't have)
robe = item(5002, "Robe of the Archmage", "INVTYPE_ROBE", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 40 }"))
chars.Nine.bis[5002] = L.eval('{ "Chest", 1, 9.0, nil, "Tailoring" }')
items[5002] = L.eval('{ "made with Tailoring", a = "n" }')
got = texts(lines(robe))
assert got[-1] == "Glide BiS #1 for Chest, +9.0 DPS (needs Tailoring to wear): made with Tailoring", got
# an item with random suffixes: the rank shows on the suffix it was ranked with only (sixth field, without its sign;
# Forever's links: "|cnIQ3:" before the item string, the suffix in field 7). Regression: a mage wearing Circlet of
# the Monkey read "you wear it" on a Circlet of Fiery Wrath, and any copy of a ranked item showed the ranked suffix's
# rank and gain
L.execute(r"""
function sfxlink(id, sfx, name) local l = "|cnIQ3:|Hitem:" .. id .. "::::::" .. sfx .. ":77:60|h[" .. name .. "]|h|r"
  ITEMS[l] = { loc = "INVTYPE_HEAD", stats = { ITEM_MOD_INTELLECT_SHORT = 12 } }; return l end
""")
monkey, wrath = G.sfxlink(7001, 1, "Circlet of the Monkey"), G.sfxlink(7001, -2, "Circlet of Fiery Wrath")
items[7001] = L.eval('{ "drops from Ras Frostwhisper, Scholomance", a = "n" }')
ras = "Glide: drops from Ras Frostwhisper, Scholomance"
worn_head = G.WORN[1]
G.WORN[1] = monkey
chars.Nine.bis[7001] = L.eval('{ "Head", 1, 8.1, nil, nil, 2 }')          # (ranked with Fiery Wrath)
assert texts(lines(wrath))[-1] == "Glide BiS #1 for Head, +8.1 DPS: " + ras[len("Glide: "):], texts(lines(wrath))
assert texts(lines(monkey)) == [ras], ("the worn Monkey isn't the ranked Fiery Wrath", texts(lines(monkey)))
assert texts(lines(None, G.GameTooltip, L.eval("{ id = 7001 }"))) == [ras], "no link to tell the suffix: the source"
chars.Nine.bis[7001] = L.eval('{ "Head", 3, 0.0, nil, nil, 1 }')          # (ranked as worn: the Monkey)
assert texts(lines(monkey)) == ["Glide BiS #3 for Head: you wear it"], texts(lines(monkey))
assert texts(lines(wrath))[-1] == ras, ("another suffix of what you wear isn't 'you wear it'", texts(lines(wrath)))
chars.Nine.bis[7001] = L.eval('{ "Head", 2, 4.0 }')                       # (no suffix in the row: by the id)
assert texts(lines(wrath))[-1] == "Glide BiS #2 for Head: you wear it", texts(lines(wrath))
G.WORN[1] = worn_head
chars.Nine.bis[7001] = None
# a one-hander ranked with an off hand in place of a two-hander: the gain is the pair's, the line names the partner
dagger = item(5003, "Better Dagger", "INVTYPE_WEAPON", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 5 }"))
chars.Nine.bis[5003] = L.eval('{ "Main Hand", 1, 32.0, nil, nil, nil, "Bright Orb" }')
items[5003] = L.eval('{ "sold by Deliana, Ironforge", a = "n" }')
assert texts(lines(dagger))[-1] == "Glide BiS #1 for Main Hand, +32.0 DPS with Bright Orb: sold by Deliana, Ironforge", \
    texts(lines(dagger))
chars.Nine.bis[5003] = None
# without weights the rank still shows (the app's own sims; the upgrade line has its own limits)
ns.simWeights = L.table()
assert texts(lines(new)) == ["Glide BiS #5 for Finger, -1.5 DPS: " + gandling], "no weights"
ns.simWeights = weights
chars.Nine.bis[1003] = L.eval('{ "Finger", 2, 6.1 }')

# the character's key: Name-Realm (the realm as the game gives it, with or without its spaces) before Name
chars["Nine-TestRealm"] = L.eval('{ bis = { [1003] = { "Finger", 1, 7.0 } } }')
assert texts(lines(new))[1] == "Glide BiS #1 for Finger, +7.0 DPS: " + gandling, "Name-Realm first"
chars["Nine-TestRealm"] = None
chars["Nine-Test Realm"] = L.eval('{ bis = { [1003] = { "Finger", 1, 7.0 } } }')
L.execute('REALM_GRN = GetRealmName; function GetRealmName() return "Test Realm" end')
assert texts(lines(new))[1] == "Glide BiS #1 for Finger, +7.0 DPS: " + gandling, "the realm with its spaces"
chars["Nine-Test Realm"] = None
chars["Nine-TestRealm"] = L.eval('{ bis = { [1003] = { "Finger", 1, 7.0 } } }')
L.execute('REALM_UFN = UnitFullName; function UnitFullName() return "Nine", nil end')
assert texts(lines(new))[1] == "Glide BiS #1 for Finger, +7.0 DPS: " + gandling, "no realm yet: its spaces taken out"
L.execute("GetRealmName, UnitFullName = REALM_GRN, REALM_UFN")
chars["Nine-TestRealm"] = None
saved = chars.Nine
chars.Nine = None
chars["Nine-OtherRealm"] = L.eval('{ bis = { [1003] = { "Finger", 1, 7.0 } } }')
assert texts(lines(new)) == [up, "Glide: " + gandling], "another realm's Nine"
chars["Nine-OtherRealm"] = None
chars.Nine = saved
G.NAME = "Dragon"
assert texts(lines(new)) == ["Glide: " + gandling], "another character"
G.NAME = "Nine"

# a broken entry: the source line instead; a BiS line that fails: the source line still shows (the error with debug)
for junk in ('"text"', "{}", '{ "Finger" }', '{ "", 1, 2 }', '{ "Finger", "two", 1 }', '{ "Finger", 0, 1 }',
             '{ 12, 1, 1 }'):
    chars.Nine.bis[1003] = L.eval(junk)
    assert texts(lines(new)) == [up, "Glide: " + gandling], junk
chars.Nine.bis[1003] = L.eval('{ "Finger", 2, 6.1 }')
for junk in ('{ bis = "x" }', '"x"', "{}"):
    chars.Nine = L.eval(junk)
    assert texts(lines(new)) == [up, "Glide: " + gandling], junk
ns.live.chars = L.eval("setmetatable({}, { __index = function() error('boom') end })")
ns.rec.debug = True
n_printed = len(list(G.printed.values()))
assert texts(lines(new)) == [up, "Glide: " + gandling], "the BiS line failed alone"
assert any("bis:" in p and "boom" in p for p in list(G.printed.values())[n_printed:]), "said with debug on"
ns.rec.debug = None
ns.live.chars = chars
chars.Nine = saved
assert texts(lines(new)) == [up, bis_text]

# the switches: sources off keeps the rank without where it comes from; bis off gives the source line back
G.SlashCmdList.GLIDE("sources off")
assert texts(lines(new)) == [up, "Glide BiS #2 for Finger, +6.1 DPS"], texts(lines(new))
G.SlashCmdList.GLIDE("bis off")
assert ns.BisOn() is False and G.GlideLinkDB.bis.enabled is False
assert texts(lines(new)) == [up], "both off: the upgrade line alone"
G.SlashCmdList.GLIDE("sources on")
assert texts(lines(new)) == [up, "Glide: " + gandling], "bis off: the source line"
G.SlashCmdList.GLIDE("compare off")
G.SlashCmdList.GLIDE("bis on")
assert ns.BisOn() is True and texts(lines(new)) == [bis_text], "each line has its own switch"
G.SlashCmdList.GLIDE("compare on")
G.SlashCmdList.GLIDE("bis")
said = list(G.printed.values())[-1]
assert "best-in-slot rank on item tooltips" in said and "4 items ranked for this character" in said, said
chars.Nine = None
G.SlashCmdList.GLIDE("bis")
said = list(G.printed.values())[-1]
assert "none ranked for this character yet" in said and "then /reload" in said, said
ns.live = None
G.SlashCmdList.GLIDE("bis")
said = list(G.printed.values())[-1]
assert "aren't loaded: restart WoW once" in said and "/reload" not in said, said
assert texts(lines(new)) == [up], "no Live.lua: the upgrade line alone"
ns.live = live
chars.Nine = saved

# ---- the file as the engine writes it (live_data.py, a "chars" provider as bis.BisRanker.live_section gives it) -----
sys.path.insert(0, os.path.join(ROOT, "companion"))
import live_data  # noqa: E402

home = tempfile.mkdtemp()
ld = live_data.LiveData(home, lambda: None, lambda: "0.22.0")
ld.register("items", lambda: {1003: {"t": gandling, "a": "n"}, 5002: {"t": "made with Tailoring", "a": "n"}})
ld.register("chars", lambda: {"Nine-TestRealm": {"bis": {1003: ["Finger", 2, 6.1], 1001: ["Finger", 1, 0.0],
                                                         5001: ["Head", 4, 0.2, "~"],
                                                         5002: ["Chest", 1, 9.0, None, "Tailoring"],
                                                         7001: ["Head", 1, 8.1, None, None, 2],
                                                         5003: ["Main Hand", 1, 32.0, None, None, None,
                                                                "Bright Orb"]}}})
L.eval("function(src, ns) local f, e = loadstring(src, '@Live.lua') if not f then error(e) end return f('Glide', ns) end")(
    ld.text(), ns)
assert ns.live is not live and ns.live.chars["Nine-TestRealm"] is not None
assert texts(lines(new)) == [up, "Glide BiS #2 for Finger, +6.1 DPS: " + gandling], texts(lines(new))
assert texts(lines(old)) == ["Glide BiS #1 for Finger: you wear it"]
assert texts(lines(crown))[1] == "Glide BiS #4 for Head, about the same as yours", texts(lines(crown))
assert texts(lines(robe))[1] == "Glide BiS #1 for Chest, +9.0 DPS (needs Tailoring to wear): made with Tailoring"
assert texts(lines(wrath))[-1] == "Glide BiS #1 for Head, +8.1 DPS", texts(lines(wrath))
assert not any(t.startswith("Glide BiS") for t in texts(lines(monkey))), ("another suffix", texts(lines(monkey)))
assert texts(lines(dagger))[-1] == "Glide BiS #1 for Main Hand, +32.0 DPS with Bright Orb", texts(lines(dagger))
ns.live = live
print("OK")
