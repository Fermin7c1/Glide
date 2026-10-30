#!/usr/bin/env python3
"""Addon auto repair (Repair.lua) under Lua 5.1 with a mocked WoW API: opening a merchant who repairs repairs
everything when you can pay the whole bill, says what's missing when you can't, does nothing at a merchant who
doesn't repair or with nothing damaged, and stays off when turned off (minimap toggle, /glide repair off). Needs lupa."""
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
CAN, COST, MONEY, REPAIRS = true, 1234, 50000, 0
function CanMerchantRepair() return CAN end
function GetRepairAllCost() return COST, COST > 0 end
function GetMoney() return MONEY end
function RepairAllItems() REPAIRS = REPAIRS + 1; COST = 0 end
function GetCoinTextureString(c) return c .. "c" end
PRINTED = {}
function print(...) local t = {} for i = 1, select("#", ...) do t[#t+1] = tostring(select(i, ...)) end PRINTED[#PRINTED+1] = table.concat(t, " ") end
""")
ns = L.table()
for f in ("Core.lua", "Coach.lua", "Repair.lua", "Minimap.lua"):
    src = open(os.path.join(ROOT, "addon", "Glide", f), encoding="utf-8").read()
    L.eval("function(src, name, a, ns) local f, e = loadstring(src, name) if not f then error(e) end return f(a, ns) end")(
        src, "@" + f, "Glide", ns)
G = L.globals()
G.fire("ADDON_LOADED", "Glide")
G.fire("PLAYER_LOGIN")
last = lambda: G.PRINTED[len(G.PRINTED)] if len(G.PRINTED) else ""

# on by default: a repairing merchant opens -> everything repaired, once, and the cost said
assert ns.RepairOn()
G.fire("MERCHANT_SHOW")
assert G.REPAIRS == 1 and "repaired everything for 1234c" in last(), (G.REPAIRS, last())
G.fire("MERCHANT_SHOW")                                   # nothing damaged now: nothing done, nothing said
assert G.REPAIRS == 1, G.REPAIRS

# a merchant who doesn't repair
G.COST, G.CAN = 800, False
G.fire("MERCHANT_SHOW")
assert G.REPAIRS == 1, G.REPAIRS

# can't pay the whole bill: nothing spent, and it says so
G.CAN, G.MONEY = True, 500
G.fire("MERCHANT_SHOW")
assert G.REPAIRS == 1 and "repairs cost 800c, you have 500c" in last(), last()

# turned off (/glide repair off): merchants are left alone; /glide repair still repairs by hand
G.MONEY = 50000
G.SlashCmdList.GLIDE("repair off")
assert not ns.RepairOn() and "auto repair off" in last(), last()
G.fire("MERCHANT_SHOW")
assert G.REPAIRS == 1, G.REPAIRS
G.SlashCmdList.GLIDE("repair")
assert G.REPAIRS == 2, G.REPAIRS
G.SlashCmdList.GLIDE("repair")
assert "nothing to repair" in last(), last()

# the minimap menu's toggle
ns.SetRepair(True)
assert ns.RepairOn() and G.GlideLinkDB.repair.enabled
G.COST = 300
G.fire("MERCHANT_SHOW")
assert G.REPAIRS == 3, G.REPAIRS
print("repair ok")
