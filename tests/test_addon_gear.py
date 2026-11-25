#!/usr/bin/env python3
"""How the addon reads an item's stats (Gear.lua ns.ItemStats, the worn and bag gear entries the strip sends, and
the upgrade line on item tooltips) under Lua 5.1 with the mocked WoW API: what GetItemStats reports, then the
"Equip:" lines only the tooltip has. A set's bonus lines ("(3) Set: Restores 4 mana per 5 sec." until enough pieces
are worn, "Set: ..." once they are) say the same words but are the whole set's: they add nothing to the piece
(regression: every piece of a set carried the set's MR, and the upgrade line counted it on each).
Worn entries also carry the link's enchant and random suffix ids (EN, SX; only when non-zero), read from the fields
after "item:" so Forever's "|cnIQ4:" colour prefix doesn't shift them; bag entries never carry them; a trimmed entry
keeps them; enchanting a worn item (UNIT_INVENTORY_CHANGED for the player) rebuilds the list; the engine moves them
into the item's "enchant" and "suffix". Needs lupa."""
import os
import sys

from lupa import lua51

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wowmock  # noqa: E402
from state import GameState  # noqa: E402

L = lua51.LuaRuntime(unpack_returned_tuples=True)
L.execute(wowmock.MOCK)
L.execute(r"""
-- gear of our own: link -> name, equip location, what GetItemStats reports, the tooltip's left lines
GEAR = {}
function gearLink(l, name, loc, stats, tip)
  GEAR[l] = { name = name, loc = loc, stats = stats, tip = tip }
  return l
end
function gear(id, name, loc, stats, tip)
  return gearLink("|cff0070dd|Hitem:" .. id .. "::::::::40:::::|h[" .. name .. "]|h|r", name, loc, stats, tip)
end
local info, instant, stats, hyper = GetItemInfo, GetItemInfoInstant, C_Item.GetItemStats, C_TooltipInfo.GetHyperlink
function GetItemInfo(l) local g = GEAR[l]
  if g then return g.name, l, 3, 40, 35, "", "", 1, g.loc, 0, 0, 4, 1 end return info(l) end
function GetItemInfoInstant(l) if GEAR[l] then return tonumber(l:match("item:(%d+)")) end return instant(l) end
C_Item.GetItemStats = function(l) local g = GEAR[l] if g then return g.stats end return stats(l) end
C_TooltipInfo.GetHyperlink = function(l) local g = GEAR[l]
  if not g then return hyper(l) end
  local lines = { { leftText = g.name } }
  for _, t in ipairs(g.tip) do lines[#lines + 1] = { leftText = t } end
  return { lines = lines } end
WORN = {}
function GetInventoryItemLink(_, slot) return WORN[slot] end
""")
ns = L.table()
for f in ("Core.lua", "Codec.lua", "State.lua", "Gear.lua"):
    src = open(os.path.join(ROOT, "addon", "Glide", f), encoding="utf-8").read()
    L.eval("function(src, name, a, ns) local f, e = loadstring(src, name) if not f then error(e) end return f(a, ns) end")(
        src, "@" + f, "Glide", ns)
G = L.globals()


def stats(link):
    return dict(ns.ItemStats(link).items())


# a piece of a set: its own stats and "Equip:" line count; the set's bonuses, reached or not, don't
robe = G.gear(16805, "Robe of the Set", "INVTYPE_ROBE", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 10, ITEM_MOD_STAMINA_SHORT = 8 }"),
              L.eval('{ "Chest", "Equip: Increases damage and healing done by magical spells and effects by up to 12.", '
                     '"Set of the Test (2/8)", "(2) Set: +200 Armor.", "(3) Set: Restores 4 mana per 5 sec.", '
                     '"(5) Set: Increases your chance to hit with spells by 1%.", '
                     '"Set: Improves your chance to get a critical strike with spells by 2%.", '
                     '"Set: Increases damage done by Fire spells and effects by up to 23." }'))
got = stats(robe)
assert got == {"IN": 10, "SA": 8, "SP": 12}, got          # no MR4, HS1, CS2 or FI23 from the set; no AR200
# an item's own "Equip:" mana regeneration still counts
ring = G.gear(18103, "Band of Rumination", "INVTYPE_FINGER", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 6 }"),
              L.eval('{ "Finger", "Equip: Restores 4 mana per 5 sec." }'))
assert stats(ring) == {"IN": 6, "MR": 4}, stats(ring)

# the worn gear entries the strip sends (page W): the robe without the set's stats
G.WORN[5], G.WORN[11] = robe, ring
G.fire("PLAYER_LOGIN")
worn = ",".join(ns.gear.W.values())
assert "5:16805:INVTYPE_ROBE:" in worn and worn.split("5:16805:", 1)[1].split(",")[0].endswith(":IN10/SA8/SP12"), worn
assert "11:18103:INVTYPE_FINGER:" in worn and "IN6/MR4" in worn, worn
assert "EN" not in worn and "SX" not in worn, worn                  # neither link carries an enchant or a suffix


def entries(kind):
    return [e for c in getattr(ns.gear, kind).values() for e in c.split(",") if e]


def entry(kind, key):
    got = [e for e in entries(kind) if e.split(":", 1)[0] == key]
    assert len(got) == 1, (kind, key, entries(kind))
    return got[0]


def stat_codes(e):
    return {p.rstrip("0123456789.-"): p for p in e.split(":", 10)[10].split("/")}


# ---- enchant and random suffix ids (EN, SX) on worn entries ---------------------------------------------------------
# a Forever link: the "|cnIQ4:" colour prefix has a colon of its own, so counting fields over the whole link would
# read the wrong ones; after "item:", field 2 is the enchant and field 7 the suffix (here a negative one, as sent)
cord_link = "|cnIQ4:|Hitem:19136:2504:::::-13002:1234:60:::::|h[Mana Igniting Cord]|h|r"
cord = G.gearLink(cord_link, "Mana Igniting Cord", "INVTYPE_WAIST",
                  L.eval("{ ITEM_MOD_INTELLECT_SHORT = 16, ITEM_MOD_STAMINA_SHORT = 12 }"), L.eval("{ \"Waist\" }"))
# zero ids are no ids: neither code is sent
plain = G.gearLink("|cnIQ3:|Hitem:18727:0:0:0:0:0:0:0:60|h[Crimson Felt Hat]|h|r", "Crimson Felt Hat",
                   "INVTYPE_HEAD", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 9 }"), L.eval("{ \"Head\" }"))
# only a suffix: a positive id, no enchant
cloak = G.gearLink("|cnIQ2:|Hitem:15421::::::13784:77:60|h[Shimmering Cloak]|h|r", "Shimmering Cloak",
                   "INVTYPE_CLOAK", L.eval("{ ITEM_MOD_SPIRIT_SHORT = 5 }"), L.eval("{ \"Back\" }"))
G.WORN[6], G.WORN[1], G.WORN[15] = cord, plain, cloak
# the same cord in the bags: bag entries never carry the ids
L.execute(r"""
C_Container.GetContainerNumSlots = function(b) return b == 0 and 1 or 0 end
C_Container.GetContainerItemInfo = function(b, s) if b == 0 and s == 1 then return { hyperlink = BAGLINK } end end
""")
G.BAGLINK = cord_link
G.fire("BAG_UPDATE_DELAYED")
w6 = entry("W", "6")
assert w6.startswith("6:19136:INVTYPE_WAIST:") and w6.endswith(":EN2504/IN16/SA12/SX-13002"), w6
w1, w15 = entry("W", "1"), entry("W", "15")
assert w1.endswith(":IN9") and "EN" not in w1 and "SX" not in w1, w1
assert w15.endswith(":SI5/SX13784") and "EN" not in w15, w15
i0 = entry("I", "0.1")
assert i0.startswith("0.1:19136:INVTYPE_WAIST:") and i0.endswith(":IN16/SA12"), i0
assert all("EN" not in e and "SX" not in e for e in entries("I")), entries("I")    # (and the cached stats kept none)

# through the engine: the ids leave the stats for "enchant" and "suffix"; an entry without them has neither key
items = {it["key"]: it for it in (GameState.parse_item(e) for e in entries("W"))}
assert items["6"]["enchant"] == 2504 and items["6"]["suffix"] == -13002, items["6"]
assert items["6"]["stats"] == {"IN": 16.0, "SA": 12.0}, items["6"]
assert items["15"]["suffix"] == 13784 and "enchant" not in items["15"], items["15"]
assert "enchant" not in items["1"] and "suffix" not in items["1"], items["1"]
assert "enchant" not in items["5"] and items["5"]["stats"] == {"IN": 10.0, "SA": 8.0, "SP": 12.0}, items["5"]

# an entry too long for its page keeps the ids: the least useful stats go first, never EN or SX
L.execute(r"""
BIG = {}
for _, c in ipairs({"STRENGTH", "AGILITY", "STAMINA", "INTELLECT", "SPIRIT", "ATTACK_POWER", "RANGED_ATTACK_POWER",
                    "HIT_RATING", "CRIT_RATING", "HIT_SPELL_RATING", "CRIT_SPELL_RATING", "HEALTH_REGEN",
                    "POWER_REGEN0", "DEFENSE_SKILL_RATING", "DODGE_RATING", "PARRY_RATING"}) do
  BIG["ITEM_MOD_" .. c .. "_SHORT"] = 999
end
""")
big = G.gearLink("|cnIQ4:|Hitem:999998:8737:::::13784:1:60|h[" + "x" * 24 + "]|h|r", "x" * 24, "INVTYPE_CHEST",
                 G.BIG, L.eval("{ \"Chest\" }"))
G.WORN[5] = big
G.fire("PLAYER_EQUIPMENT_CHANGED")
w5 = entry("W", "5")
codes = stat_codes(w5)
assert len(w5.encode()) <= 112 and len(codes) < 16, (len(w5), w5)            # it was trimmed, and it fits
assert codes["EN"] == "EN8737" and codes["SX"] == "SX13784", w5
assert GameState.parse_item(w5)["enchant"] == 8737 and GameState.parse_item(w5)["suffix"] == 13784, w5

# enchanting a worn item changes its link: UNIT_INVENTORY_CHANGED for the player rebuilds the worn list (another
# unit's doesn't)
G.WORN[6] = G.gearLink(cord_link.replace(":2504:", ":2650:"), "Mana Igniting Cord", "INVTYPE_WAIST",
                       L.eval("{ ITEM_MOD_INTELLECT_SHORT = 16, ITEM_MOD_STAMINA_SHORT = 12 }"), L.eval("{ \"Waist\" }"))
G.fire("UNIT_INVENTORY_CHANGED", "target")
assert stat_codes(entry("W", "6"))["EN"] == "EN2504", entry("W", "6")
G.fire("UNIT_INVENTORY_CHANGED", "player")
assert stat_codes(entry("W", "6"))["EN"] == "EN2650", entry("W", "6")
print("OK")
