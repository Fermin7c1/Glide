#!/usr/bin/env python3
"""Stack counts on the Cooldown Manager (addon Stacks.lua) under Lua 5.1 with the mocked WoW API, against stand-ins
for Blizzard's Tracked Bars and Tracked Buffs items that refresh in Blizzard's order (Blizzard_CooldownViewer,
CooldownViewerBuffBarItemMixin:RefreshData): the player's auras looked up as helpful ones, the item's own stack text
written, then a debuff on the player found (RefreshActive). So a stacking debuff (Arcane Blast) had no count:
- Glide shows its count on a text of its own laid over the item's (its font, its bottom-right corner, sized by its
  own text), after the item found it, and again on each stack change; never for a buff the item's own text shows,
  nor for an aura that doesn't stack, nor on an item hooked while shown until its next refresh says which it is;
- in combat the count is a secret value: handed to the game as it is, never compared (0 shows nothing);
- the Cooldown Manager may load before or after Glide; items acquired later, and ones already shown, are hooked;
- an item back in the pool, or the switch off (the Glide window, /glide stacks off), shows nothing;
- a failure is never silent: /glide stacks says the last one.
Needs lupa."""
import os
import sys

from lupa import lua51

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wowmock  # noqa: E402

L = lua51.LuaRuntime(unpack_returned_tuples=True)
L.execute(wowmock.MOCK)
L.execute(r"""
-- a secret value: the game hands it over but any comparison or arithmetic on it fails, as in combat
local SecretMT = { __lt = function() error("compared a secret") end, __le = function() error("compared a secret") end,
                   __add = function() error("arithmetic on a secret") end, __tostring = function() return "<secret>" end }
function Secret(v) return setmetatable({ v = v }, SecretMT) end
issecretvalue = function(v) return getmetatable(v) == SecretMT end
C_StringUtil = { TruncateWhenZero = function(n)
  if issecretvalue(n) then return n.v == 0 and "" or Secret(tostring(n.v)) end
  return n == 0 and "" or tostring(n) end }

function hooksecurefunc(t, name, fn)
  local orig = assert(t[name], "no " .. name)
  t[name] = function(...) local r = { orig(...) }; fn(...); return unpack(r) end
end

-- font strings and frames as the Cooldown Manager items have them
local function newText(parent, font)
  local fs = { parent = parent, font = font, justify = "RIGHT" }
  function fs:SetText(t) self.text = t end function fs:GetText() return self.text end
  function fs:GetParent() return self.parent end
  function fs:GetFontObject() return self.font end function fs:SetFontObject(f) self.font = f end
  function fs:GetJustifyH() return self.justify end function fs:SetJustifyH(j) self.justify = j end
  function fs:SetPoint(point, rel, relPoint, x, y) self.point, self.over, self.relPoint, self.x, self.y = point, rel, relPoint, x, y end
  return fs
end
local function newHost()
  local h = { made = {} }
  function h:CreateFontString(_, layer) local fs = newText(self); fs.layer = layer; self.made[#self.made + 1] = fs; return fs end
  return h
end

-- an item: kind "bar" (the count on its Icon frame) or "icon" (on its Applications frame); `helpful` is the aura the
-- player's helpful lookup finds, `debuff` the one RefreshActive finds after
function newItem(kind)
  local item, host = {}, newHost()
  local own = newText(host, kind == "bar" and "NumberFontNormalSmall" or "NumberFontNormal")
  host.Applications = own
  if kind == "bar" then item.Icon = host else item.Icon = { texture = true }; item.Applications = host end
  item.own, item.host = own, host
  function item:GetAuraDataCached() return self.cached end
  function item:RefreshAuraInstance() self.cached = self.helpful end
  function item:RefreshApplications()
    local a = self.cached
    own:SetText((a and a.applications and a.applications > 1) and a.applications or "")
  end
  function item:RefreshActive() if not self.cached then self.cached = self.debuff end end
  function item:RefreshData() self:RefreshAuraInstance(); self:RefreshApplications(); self:RefreshActive() end
  function item:ResetCooldownData() self.cached, self.helpful, self.debuff = nil, nil, nil end
  return item
end

function newViewer()
  local v = { active = {} }
  v.itemFramePool = { EnumerateActive = function() local i = 0; return function() i = i + 1; return v.active[i] end end }
  function v:OnAcquireItemFrame(item) end
  function v:Acquire(item) self.active[#self.active + 1] = item; self:OnAcquireItemFrame(item); item:RefreshData() end
  return v
end
function ours(item) return item.host.made[1] end
printed = {}
function print(...) local t = {} for i = 1, select("#", ...) do t[#t + 1] = tostring(select(i, ...)) end
  printed[#printed + 1] = table.concat(t, " ") end
""")
ns = L.table()
for f in ("Core.lua", "Stacks.lua"):
    src = open(os.path.join(ROOT, "addon", "Glide", f), encoding="utf-8").read()
    L.eval("function(src, name, a, ns) local f, e = loadstring(src, name) if not f then error(e) end return f(a, ns) end")(
        src, "@" + f, "Glide", ns)
G = L.globals()
G.fire("ADDON_LOADED", "Glide")
G.fire("PLAYER_LOGIN")
ours = G.ours
same = L.eval("function(a, b) return rawequal(a, b) end")    # (two handles on one Lua table)


def text(item):
    fs = ours(item)
    return None if fs is None else fs.text


# ---- the Cooldown Manager loads after Glide: hooked then, the items already shown too -------------------------------
bars, icons = G.newViewer(), G.newViewer()
early = G.newItem("bar")
bars.active[1] = early                        # (shown before Glide hooked it)
G.BuffBarCooldownViewer, G.BuffIconCooldownViewer = bars, icons
G.fire("ADDON_LOADED", "Blizzard_CooldownViewer")

# Arcane Blast: a debuff on you, found only after the item wrote its own text
blast = G.newItem("bar")
blast.debuff = L.eval("{ applications = 3 }")
bars.Acquire(bars, blast)
assert blast.own.text == "" and text(blast) == 3, (blast.own.text, text(blast))
fs = ours(blast)
assert fs.font == "NumberFontNormalSmall" and same(fs.over, blast.own) and fs.layer == "OVERLAY" and fs.justify == "RIGHT" \
    and fs.point == "BOTTOMRIGHT" and fs.relPoint == "BOTTOMRIGHT" and fs.x == 0 and fs.y == 0, \
    "the same font and corner as the item's own text (sized by its own: the item's is empty then)"
blast.debuff = L.eval("{ applications = 4 }")             # a stack more: the item refreshes (UNIT_AURA)
blast.RefreshData(blast)
assert text(blast) == 4, text(blast)
blast.debuff = None                                       # it ran out
blast.RefreshData(blast)
assert text(blast) == "", text(blast)

# the item shown before Glide hooked it: hooked, but nothing drawn until its next refresh (it may hold a buff its own
# text already counts), then counted
early.helpful = L.eval("{ applications = 3 }")
early.cached = early.helpful
early.own.SetText(early.own, 3)
assert text(early) is None, "nothing of ours before its next refresh"
ns.SetStacks(True)
assert text(early) in (None, ""), "not even on a switch: a buff it counts would show twice"
early.helpful = None
early.debuff = L.eval("{ applications = 2 }")
early.RefreshData(early)
assert text(early) == 2, text(early)

# a buff the item's own text shows (helpful, 5 stacks): nothing of ours on top; a debuff that doesn't stack: nothing
buff = G.newItem("bar")
buff.helpful = L.eval("{ applications = 5 }")
bars.Acquire(bars, buff)
assert buff.own.text == 5 and text(buff) == "", (buff.own.text, text(buff))
dot = G.newItem("bar")
dot.debuff = L.eval("{ applications = 0 }")
bars.Acquire(bars, dot)
assert text(dot) == "", text(dot)

# in combat: the count is a secret, handed to the game as it is (never compared); a secret 0 shows nothing
blast.debuff = L.eval("{ applications = Secret(3) }")
blast.RefreshData(blast)
got = text(blast)
assert G.issecretvalue(got) and got.v == "3", got
blast.debuff = L.eval("{ applications = Secret(0) }")
blast.RefreshData(blast)
assert text(blast) == "", text(blast)

# Tracked Buffs (the icon view): the same, on its Applications frame's text
icon = G.newItem("icon")
icon.debuff = L.eval("{ applications = 2 }")
icons.Acquire(icons, icon)
assert text(icon) == 2 and ours(icon).font == "NumberFontNormal" and same(ours(icon).over, icon.own) and \
    ours(icon).point == "BOTTOMRIGHT", text(icon)

# back in the pool: nothing of the last aura stays
icon.ResetCooldownData(icon)
assert text(icon) == "", text(icon)

# switched off (the Glide window, /glide stacks off): nothing shown, even on refresh; on: back at once
blast.debuff = L.eval("{ applications = 2 }")
blast.RefreshData(blast)
assert text(blast) == 2
G.SlashCmdList.GLIDE("stacks off")
assert ns.StacksOn() is False and text(blast) == "", text(blast)
blast.RefreshData(blast)
assert text(blast) == "", "off stays off"
ns.SetStacks(True)
assert text(blast) == 2 and G.GlideLinkDB.stacks.enabled is True, text(blast)

# a viewer hooked once: loading again (PLAYER_LOGIN after ADDON_LOADED) doesn't stack hooks
G.fire("PLAYER_LOGIN")
again = G.newItem("bar")
again.debuff = L.eval("{ applications = 3 }")
bars.Acquire(bars, again)
assert len(list(again.host.made.values())) == 1 and text(again) == 3, "one text of ours per item"

# a failure: the text left empty, and /glide stacks says what it was
broken = G.newItem("bar")
bars.Acquire(bars, broken)
L.execute("function _bad(self) error('cache gone') end")
broken.GetAuraDataCached = G._bad
broken.RefreshApplications = L.eval("function(self) end")       # (its own text: nothing to show)
broken.RefreshData(broken)
assert text(broken) in (None, ""), text(broken)
G.SlashCmdList.GLIDE("stacks")
said = "\n".join(G.printed.values())
assert "last problem" in said and "cache gone" in said, said
print("OK")
