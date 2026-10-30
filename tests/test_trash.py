#!/usr/bin/env python3
"""Addon bag cleanup (Trash.lua) under Lua 5.1 with a mocked WoW API and a stand-in for RestedXP's inventory
manager: when the bags are full, the next click in the world or key press deletes the cheapest item RestedXP calls
junk (marked junk, or grey unless marked useful) and nothing else; no prompt. RestedXP's own auto-discard makes it
stand aside; with RestedXP's manager off, only its discard pile counts; the minimap toggle turns it off. Needs lupa."""
import os
import sys

from lupa import lua51

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wowmock  # noqa: E402

L = lua51.LuaRuntime(unpack_returned_tuples=True)
L.execute(wowmock.MOCK)
L.execute(r"""
local noop = function() end
local baseCreate = CreateFrame
function CreateFrame(kind, name, parent)
  local f = baseCreate(kind, name)
  f.SetPropagateKeyboardInput = noop
  f.EnableKeyboard = noop
  return f
end
WorldFrame = { HookScript = function(self, k, fn) self.hooks = self.hooks or {}; self.hooks[k] = fn end }
INVENTORY_FULL = "Inventory is full."
-- [id] = {name, quality, vendor price}
ITEMS = {
  [3300] = {"Rabbit's Foot", 0, 2},      [4865] = {"Ruined Pelt", 0, 5},     [7073] = {"Broken Fang", 0, 6},
  [2589] = {"Linen Cloth", 1, 13},       [4540] = {"Tough Hunk of Bread", 1, 1},
  [2210] = {"Battered Buckler", 0, 1},   [6948] = {"Hearthstone", 1, 0},     [2575] = {"Red Linen Shirt", 2, 30},
}
BAG = { {itemID = 3300, stackCount = 3}, {itemID = 4865, stackCount = 1}, {itemID = 7073, stackCount = 1},
        {itemID = 2589, stackCount = 2}, {itemID = 4540, stackCount = 5}, {itemID = 2210, stackCount = 1},
        {itemID = 6948, stackCount = 1}, {itemID = 2575, stackCount = 1} }
FREE = 2
C_Container.GetContainerNumSlots = function(b) return b == 0 and 16 or 0 end
C_Container.GetContainerItemInfo = function(b, s)
  local x = b == 0 and BAG[s] if not x then return nil end
  return { itemID = x.itemID, stackCount = x.stackCount, quality = ITEMS[x.itemID][2], hyperlink = "[" .. ITEMS[x.itemID][1] .. "]" }
end
C_Container.GetContainerNumFreeSlots = function(b) if b == 0 then return FREE, 0 end return 0, 0 end
C_Item.GetItemInfo = function(id) local i = ITEMS[id] if i then return i[1], "", i[2], 1, 1, "", "", 20, "", 0, i[3], 15 end end
CURSOR = nil
DELETED = {}
function GetCursorInfo() return CURSOR and "item" or nil end
function ClearCursor() CURSOR = nil end
C_Container.PickupContainerItem = function(b, s) CURSOR = {b, s} end
function CursorHasItem() return CURSOR ~= nil end
function DeleteCursorItem() local s = CURSOR[2] DELETED[#DELETED+1] = BAG[s].itemID BAG[s] = nil CURSOR = nil FREE = FREE + 1 end
function UnitIsDeadOrGhost() return false end
PRINTED = {}
function print(...) local t = {} for i = 1, select("#", ...) do t[#t+1] = tostring(select(i, ...)) end PRINTED[#PRINTED+1] = table.concat(t, " ") end
-- RestedXP: its discard pile and the inventory manager's IsJunk, as in InventoryManager.lua
RXPCData = RXPCData or {}
RXPCData.discardPile = { [4540] = true, [2210] = false }        -- bread marked junk, the buckler marked useful
IM_ON = true
RXP = RXP or {}
RXP.settings = { profile = { enableInventoryManager = true, autoDiscardItems = false } }
RXP.inventoryManager = {
  IsFeatureEnabled = function(self) return IM_ON end,
  IsJunk = function(self, id)
    if id == 6948 then return false end
    local d = RXPCData.discardPile[id]
    if d == nil then return ITEMS[id][2] == 0 end
    return d
  end }
""")
ns = L.table()
for f in ("Core.lua", "Coach.lua", "Trash.lua", "Minimap.lua"):
    src = open(os.path.join(ROOT, "addon", "Glide", f), encoding="utf-8").read()
    L.eval("function(src, name, a, ns) local f, e = loadstring(src, name) if not f then error(e) end return f(a, ns) end")(
        src, "@" + f, "Glide", ns)
G = L.globals()
G.fire("ADDON_LOADED", "Glide")
G.fire("PLAYER_LOGIN")
click = G.WorldFrame.hooks["OnMouseDown"]
key = G.GlideTrashKeys.scripts["OnKeyDown"]
deleted = lambda: list(G.DELETED.values())
last_print = lambda: G.PRINTED[len(G.PRINTED)] if len(G.PRINTED) else ""

# RestedXP junk here: Rabbit's Foot x3 (6c), Ruined Pelt (5c), Broken Fang (6c), the bread x5 (5c, marked);
# never: the buckler (grey but marked useful), cloth, the Hearthstone, the green shirt
pick = ns.TrashPick()
assert pick.id in (4865, 4540) and pick.value == 5, (pick.id, pick.value)

# room in the bags: clicks delete nothing
G.fire("BAG_UPDATE_DELAYED")
click()
key()
assert deleted() == [], deleted()

# full: nothing yet, until the next click in the world; then exactly one item, the cheapest junk
G.FREE = 0
G.fire("BAG_UPDATE_DELAYED")
assert deleted() == []
click()
assert len(deleted()) == 1 and deleted()[0] in (4865, 4540), deleted()
assert "bags full: deleted" in last_print(), last_print()
click()
assert len(deleted()) == 1, "room again: no second delete"

# full again (the loot error): the next key press takes the next cheapest
G.FREE = 0
G.fire("UI_ERROR_MESSAGE", 3, "Inventory is full.")
key()
assert len(deleted()) == 2 and set(deleted()) == {4865, 4540}, deleted()

# then Rabbit's Foot x3 and Broken Fang (6c each), then nothing: the rest isn't RestedXP junk
for _ in range(4):
    G.FREE = 0
    G.fire("BAG_UPDATE_DELAYED")
    click()
assert set(deleted()) == {4865, 4540, 3300, 7073}, deleted()
left = {b.itemID for b in G.BAG.values() if b}
assert left == {2589, 2210, 6948, 2575}, left
G.SlashCmdList.GLIDE("trash")
assert "nothing in your bags is marked as junk" in last_print(), last_print()

# RestedXP's own auto-discard on: Glide stands aside (and says so once)
G.BAG[9] = L.table_from({"itemID": 3300, "stackCount": 1})
G.RXP.settings.profile.autoDiscardItems = True
G.FREE = 0
G.fire("BAG_UPDATE_DELAYED")
click()
assert 3300 not in deleted()[4:], deleted()
assert "auto-discard is on" in last_print(), last_print()
G.RXP.settings.profile.autoDiscardItems = False

# RestedXP's inventory manager off: only its discard pile counts (a grey that isn't in the pile stays)
G.IM_ON = False
assert ns.TrashPick() is None, "a grey item without a RestedXP mark"
G.RXPCData.discardPile[3300] = True
assert ns.TrashPick().id == 3300
G.IM_ON = True

# the minimap toggle turns it off
ns.SetTrash(False)
G.FREE = 0
G.fire("BAG_UPDATE_DELAYED")
click()
key()
assert deleted().count(3300) == 1, deleted()
G.SlashCmdList.GLIDE("trash")
assert "bag cleanup is off" in last_print()
ns.SetTrash(True)
print("trash: ok")
