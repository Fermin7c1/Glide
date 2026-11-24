#!/usr/bin/env python3
"""How the addon reads an item's stats (Gear.lua ns.ItemStats, the worn and bag gear entries the strip sends, and
the upgrade line on item tooltips) under Lua 5.1 with the mocked WoW API: what GetItemStats reports, then the
"Equip:" lines only the tooltip has. A set's bonus lines ("(3) Set: Restores 4 mana per 5 sec." until enough pieces
are worn, "Set: ..." once they are) say the same words but are the whole set's: they add nothing to the piece
(regression: every piece of a set carried the set's MR, and the upgrade line counted it on each). Needs lupa."""
import os
import sys

from lupa import lua51

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wowmock  # noqa: E402

L = lua51.LuaRuntime(unpack_returned_tuples=True)
L.execute(wowmock.MOCK)
L.execute(r"""
-- gear of our own: link -> name, equip location, what GetItemStats reports, the tooltip's left lines
GEAR = {}
function gear(id, name, loc, stats, tip)
  local l = "|cff0070dd|Hitem:" .. id .. "::::::::40:::::|h[" .. name .. "]|h|r"
  GEAR[l] = { name = name, loc = loc, stats = stats, tip = tip }
  return l
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
print("OK")
