#!/usr/bin/env python3
"""The item the player is looking at (addon 0.21.0), so the Glide app knows which item "this" is in a question and
looks it up before it's asked.
Addon side, under Lua 5.1 with the mocked WoW API (Compare.lua and State.lua, the TOC's order): once the same item
has shown on GameTooltip or ItemRefTooltip for 0.75 s, ns.hover is its id (a glance doesn't count: another item, or
the tooltip hidden, before then), until that tooltip hides or shows something else: another item, or, after its
0.75 s too, a unit or a spell or quest link refilled into it without hiding (seen by the tooltip-data post-call for
every kind, and by the frame's own check that the tooltip still shows that item); a tooltip refreshing the same item
doesn't start over; a tooltip that moved on to a unit before the 0.75 s isn't an item; an item shown without a link
counts while the tooltip is up. Each tooltip keeps its own item: a chat link's item settles though the mouse crossed
a bag item meanwhile, and is hovered again once a bag item looked at over it is gone. State.lua's optional core key
hv carries it while it shows and 15 s after, and is left out of a frame whose core would pass the budget, sent in the
next. Neither tooltip switch turns it off, and a failing hover never costs the tooltip its lines. The source line of a
web fact ends " (web, unconfirmed)".
Engine side: GameState.hovered() is the last hv seen and when ({"id", "t"}), None once frames came without it for 3 s
(the addon's 15 s hold is the one: "this" means the item for 15 s after the tooltip closed, not 30), or, with no
frames at all (WoW minimised, the Glide window over the strip while the question is typed), 18 s after the last one
with it; through the real payload too. Needs lupa."""
import os
import sys

from lupa import lua51

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import protocol  # noqa: E402
import wowmock  # noqa: E402
from state import GameState  # noqa: E402

L = lua51.LuaRuntime(unpack_returned_tuples=True)
L.execute(wowmock.MOCK)
L.execute(r"""
-- timers run when their time comes (the shared mock runs them at once): tick() moves the clock a quarter second
AFTER = {}
C_Timer.After = function(d, fn) AFTER[#AFTER + 1] = { due = NOW + d, fn = fn } end
local tick0 = tick
function tick()
  tick0()
  local due = {}
  for i = #AFTER, 1, -1 do if NOW + 1e-9 >= AFTER[i].due then table.insert(due, 1, table.remove(AFTER, i)) end end
  for _, a in ipairs(due) do a.fn() end
end
-- the two item tooltips: shown or hidden (OnHide runs the hooks), the item they show (GetItem: none on a unit's)
local function tip(t)
  t.lines, t.hooks, t.shown = {}, {}, false
  function t:GetItem() if self.link then return "x", self.link end end
  function t:AddLine(text, r, g, b, wrap) self.lines[#self.lines + 1] = { text = text, r = r, g = g, b = b, wrap = wrap } end
  function t:IsShown() return self.shown end
  function t:Show() self.shown = true end
  function t:Hide() local was = self.shown; self.shown = false
    if was then for _, fn in ipairs(self.hooks.OnHide or {}) do fn(self) end end end
  function t:HookScript(k, fn) self.hooks[k] = self.hooks[k] or {}; table.insert(self.hooks[k], fn) end
  return t
end
tip(GameTooltip)
ItemRefTooltip = tip({})
POSTCALLS = {}
Enum = Enum or {}
Enum.TooltipDataType = { Item = 0, Spell = 1, Unit = 2 }
TooltipDataProcessor = { AllTypes = "ALL", AddTooltipPostCall = function(kind, fn) POSTCALLS[kind] = fn end }
-- tooltip t shows item id (its link; none: the tooltip data's id alone), as the game fills it: the post-calls
function showItem(t, id, nolink)
  t.link = not nolink and ("|cff1eff00|Hitem:" .. id .. "::::::::60:::::|h[Item " .. id .. "]|h|r") or nil
  t.lines = {}
  t:Show()
  POSTCALLS[0](t, { id = id, type = 0 })
  POSTCALLS.ALL(t, { id = id, type = 0 })
  return t.lines
end
-- tooltip t moves on to a unit without hiding, and no post-call tells (only the frame's own check sees it)
function showUnit(t) t.link, t.lines = nil, {}; t:Show() end
-- tooltip t is refilled with a spell (a spell link clicked in chat: SetItemRef refills ItemRefTooltip, no hide)
function showSpell(t) t.link, t.lines = nil, {}; t:Show(); POSTCALLS.ALL(t, { id = 133, type = 1 }) end
""")
ns = L.table()
toc = [ln.strip() for ln in open(os.path.join(ROOT, "addon", "Glide", "Glide.toc"), encoding="utf-8")
       if ln.strip().endswith(".lua") and (ln.strip() in ("Data\\SimWeights.lua", "Data\\Live.lua") or
                                            not ln.startswith(("Data", "Recorder", "Scanner", "RXPDump")))]
assert toc.index("State.lua") < toc.index("Compare.lua"), toc
for f in toc:
    src = open(os.path.join(ROOT, "addon", "Glide", *f.split("\\")), encoding="utf-8").read()
    L.eval("function(src, name, a, ns) local f, e = loadstring(src, name) if not f then error(e) end return f(a, ns) end")(
        src, "@" + f, "Glide", ns)
G = L.globals()
G.fire("ADDON_LOADED", "Glide")
G.fire("PLAYER_LOGIN")
GT, IRT = G.GameTooltip, G.ItemRefTooltip
assert len(list(GT.hooks.OnHide.values())) == 1 and len(list(IRT.hooks.OnHide.values())) == 1, "OnHide hooked once"


def advance(seconds):
    for _ in range(round(seconds / 0.25)):
        G.tick()


def core():
    """The core the addon sends now, as the engine reads it."""
    return protocol.parse_payload(ns.Core() + "|")[0]


def hide(t):
    t.Hide(t)


assert ns.hover is None and "hv" not in core(), "nothing looked at yet: no hv, so the strip is what it was"

# ---- the debounce: the same item for 0.75 s ------------------------------------------------------------------------
G.showItem(GT, 18842)
advance(0.5)
assert ns.hover is None and "hv" not in core(), "0.5 s: not yet"
G.showItem(GT, 19136)                         # the mouse moves on to the next bag slot: that item starts over
advance(0.5)
assert ns.hover is None, "the first item's 0.75 s came, but it isn't shown any more"
advance(0.25)
assert ns.hover == 19136 and core()["hv"] == "19136", (ns.hover, core().get("hv"))
# the tooltip refreshing the same item (the game fills it again) changes nothing
G.showItem(GT, 19136)
advance(1)
assert ns.hover == 19136 and ns.hoverLast == 19136
# hidden: no longer hovered, but hv holds it 15 s after, so a question asked just after still knows the item
hide(GT)
hid = G.NOW
assert ns.hover is None and ns.hoverAt == hid and core()["hv"] == "19136"
advance(15)
assert core()["hv"] == "19136", "15 s after: still"
advance(0.25)
assert "hv" not in core(), "then gone"

# ---- a glance doesn't count --------------------------------------------------------------------------------------
G.showItem(GT, 2361)
advance(0.5)
hide(GT)                                       # hidden before its 0.75 s
advance(2)
assert ns.hover is None and "hv" not in core(), "a glance"
# a tooltip that moved on to a unit (no hide, no item) before the 0.75 s: the item isn't shown any more
G.showItem(GT, 2361)
advance(0.25)
G.showUnit(GT)
advance(1)
assert ns.hover is None, "a unit's tooltip now"
hide(GT)

# ---- another item on the same tooltip ends the hover at once; the new one after its own 0.75 s ---------------------
G.showItem(GT, 18842)
advance(0.75)
assert ns.hover == 18842
G.showItem(GT, 19136)
assert ns.hover is None and ns.hoverLast == 18842 and core()["hv"] == "18842", "the last one held meanwhile"
advance(0.75)
assert ns.hover == 19136 and core()["hv"] == "19136"
hide(GT)

# ---- a chat link (ItemRefTooltip) stays up: hovered while it does, whatever passes over GameTooltip ---------------
G.showItem(IRT, 16686)
advance(0.75)
assert ns.hover == 16686
G.showItem(GT, 2361)                           # a glance at a bag item meanwhile
advance(0.25)
hide(GT)
advance(1)
assert ns.hover == 16686, "the chat link's item still shows"
G.showItem(IRT, 16686)                         # (its tooltip filled again)
advance(1)
assert ns.hover == 16686
hide(IRT)
assert ns.hover is None and core()["hv"] == "16686"
advance(16)
assert "hv" not in core()

# ---- each tooltip keeps its own item -------------------------------------------------------------------------------
# a chat link clicked, the mouse crossing a bag item before its 0.75 s: the chat link's item still settles
G.showItem(IRT, 16686)
advance(0.25)
G.showItem(GT, 2361)
advance(0.25)
hide(GT)
advance(0.5)
assert ns.hover == 16686 and core()["hv"] == "16686", ns.hover
# a bag item looked at over it for 0.75 s: that one; the bags closed: the chat link's item again, still showing
G.showItem(GT, 2361)
advance(0.75)
assert ns.hover == 2361 and core()["hv"] == "2361"
hide(GT)
assert ns.hover == 16686 and core()["hv"] == "16686", "the chat link's item, again"
advance(20)
assert core()["hv"] == "16686", "sent while it shows"
hide(IRT)
advance(16)
assert "hv" not in core()

# ---- a tooltip that moved on after its 0.75 s, without hiding ------------------------------------------------------
# a spell link clicked in chat refills ItemRefTooltip (no hide, no item post-call): the item's hover ends there
G.showItem(IRT, 18842)
advance(0.75)
assert ns.hover == 18842
G.showSpell(IRT)
assert ns.hover is None and core()["hv"] == "18842", "held its 15 s, then gone"
advance(15.25)
assert "hv" not in core(), "not for as long as the spell shows"
hide(IRT)
# GameTooltip on a unit after the 0.75 s, no post-call: the frame's own check sees the link is gone
G.showItem(GT, 19136)
advance(0.75)
assert ns.hover == 19136
G.showUnit(GT)
assert core()["hv"] == "19136" and ns.hover is None, (ns.hover, core().get("hv"))
advance(15.25)
assert "hv" not in core()
hide(GT)

# ---- an item shown without a link (the tooltip data's id): counts while the tooltip is up ------------------------
G.showItem(GT, 777, True)
advance(0.75)
assert ns.hover == 777 and core()["hv"] == "777"
G.showSpell(GT)                                # (a spell after it: the post-call for every kind ends it)
assert ns.hover is None
hide(GT)
advance(16)

# ---- neither tooltip switch turns it off; a failing hover leaves the lines ---------------------------------------
G.SlashCmdList.GLIDE("compare off")
G.SlashCmdList.GLIDE("sources off")
G.showItem(GT, 18842)
advance(0.75)
assert ns.hover == 18842, "the switches are for the tooltip lines"
hide(GT)
G.SlashCmdList.GLIDE("sources on")
items = ns.live["items"]                       # (lupa: .items is the Python method)
items[19136] = L.eval('{ "drops from Golemagg, Molten Core", a = "n", w = 1 }')
L.execute("AFTER_OK = C_Timer.After; C_Timer.After = function() error('no timers') end")
got = [ln.text for ln in G.showItem(GT, 19136).values()]
assert got == ["Glide: drops from Golemagg, Molten Core (web, unconfirmed)"], got
L.execute("C_Timer.After = AFTER_OK")
hide(GT)
G.SlashCmdList.GLIDE("compare on")

# ---- the optional key: left out of a frame whose core would pass the budget, sent in the next --------------------
G.showItem(GT, 18842)
advance(0.75)
assert ns.hover == 18842 and ns.live.gen == 0, "hv alone (no lg)"
long_core = "x" * 440                          # 440 + ";hv=18842" > 442
assert ns.CoreOptional(long_core) == long_core, "left out"
assert ns.CoreOptional(long_core) == long_core + ";hv=18842", "in the next frame"
assert ns.CoreOptional(long_core) == long_core, "and out of the one after"
assert ns.CoreOptional("x" * 400) == "x" * 400 + ";hv=18842", "a short core: every frame"
assert ns.CoreOptional("x" * 400) == "x" * 400 + ";hv=18842"

# ---- the engine: GameState.hovered() ----------------------------------------------------------------------------
st = GameState()
assert st.hovered() is None and st.hovered(now=0) is None
payload = ns.Core()                            # the addon's core with hv, read as the engine reads a frame
st.update(protocol.parse_payload(payload + "|")[0], {})
h = st.hovered()
assert h and h["id"] == 18842 and isinstance(h["id"], int) and isinstance(h["t"], float), h
hide(GT)

st = GameState()
st.update({"l": "60", "hv": "18842"}, {}, now=1000.0)
assert st.hovered(now=1000.0) == {"id": 18842, "t": 1000.0}
# frames without it (the budget left it out, sent in the next; or the addon stopped sending it): the last one stays
# 3 s after the first such frame, no more (the addon holds it the 15 s after the tooltip closed: held here too it was
# "this" for 30)
assert GameState.HOVER_HOLD == 3
st.update({"l": "60"}, {}, now=1001.0)
assert st.hovered(now=1001.0) == {"id": 18842, "t": 1000.0}
st.update({"l": "60"}, {}, now=1003.0)
assert st.hovered(now=1004.0) == {"id": 18842, "t": 1000.0}, "3 s"
assert st.hovered(now=1004.5) is None, "then nothing"
# seen again: the hold starts over; another item replaces it; junk changes nothing
st.update({"hv": "18842"}, {}, now=1020.0)
assert st.hovered(now=1022.0) == {"id": 18842, "t": 1020.0}
st.update({"hv": "19136"}, {}, now=1031.0)
assert st.hovered(now=1031.0) == {"id": 19136, "t": 1031.0}
for junk in ("", "0", "-5", "abc", "12x"):
    st.update({"hv": junk}, {}, now=1032.0)
    assert st.hovered(now=1032.0) == {"id": 19136, "t": 1031.0}, junk

# no frames at all: WoW minimised, or the Glide window over the strip while the player types "is this any good?" (the
# addon is still sending hv for its 15 s hold): kept until that hold would have ended, then the 3 s
st = GameState()
st.update({"l": "60", "hv": "18842"}, {}, now=2000.0)
assert st.hovered(now=2004.0) == {"id": 18842, "t": 2000.0}, "typing the question"
assert st.hovered(now=2018.0) == {"id": 18842, "t": 2000.0}
assert st.hovered(now=2018.5) is None, "past the addon's own hold"
# frames again, carrying it: held again; frames without it once WoW is back: 3 s after the first
st.update({"hv": "18842"}, {}, now=2030.0)
st.update({"l": "60"}, {}, now=2031.0)
assert st.hovered(now=2033.5) == {"id": 18842, "t": 2030.0} and st.hovered(now=2034.5) is None
print("OK")
