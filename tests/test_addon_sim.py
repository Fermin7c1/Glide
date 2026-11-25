#!/usr/bin/env python3
"""Sims the player asks for in the game (addon 0.23.0), under Lua 5.1 with the mocked WoW API (the TOC's files):
- "/glide sim <shift-clicked item>" sends the request in core key hv's request form, "hv=<id>.<sfx>.<ench>!<n>": the
  item, its link's random suffix without its sign and its enchant (0 for none; also on Forever's "|cnIQ4:" links, and
  for a bare item id), n a nonce 1 to 9, the next one for each request (the same item asked again is a new request),
  kept across reloads in GlideLinkDB.sim. The request takes the hover's place for 3 s from the first core that carries
  it, then the hover is back; one asked meanwhile waits its turn, a fourth is refused while three wait, one the strip
  never sent is dropped 30 s after it was asked, and it is left out of a frame whose core would pass the budget and
  sent in the next. No item, an item that isn't gear, or one with numbers too long for the form: nothing sent.
- "/glide wowsims" opens a StaticPopup (not the Glide window) whose edit box holds this character's link from
  Data/Live.lua (ns.live.chars[Name-Realm or Name].link), selected, with no letter limit, and put back when typed
  over; the game's older edit box field and Midnight's GetEditBox both; only a link to WoWSims' own site; without a
  link, without Live.lua, or without popups, it says so (the link printed in the last case).
- The upgrade line takes the simmed number when Live.lua has one for the item (ns.live.chars[key].sim[id] = { d,
  suffix, slot, the item id worn there, the item id in the other slot of its pair }), its text unchanged ("Glide
  (Arcane): +8.2 DPS (+1.4%) vs ..."), red for a loss, "no change" within 0.05 (the engine writes 0 within the sims'
  noise), named against the worn item the sim replaced (a ring or trinket: the one the sim picked, even when the
  weights would pick the other); the weights' estimate again for another suffix, a slot the line doesn't compare
  against, an entry of another form, once the slot or the other of its pair holds another item than when simmed (a
  ring swapped while the game was closed: at login already) or changed after login (a two-hander: the off hand too).
Engine side, through the real payload: GameState.sim_request() is each request once, by nonce (frames repeating it
don't bring it back; the same request after 10 s without it is new), oldest first, dropped after 30 s untaken; junk is
none; the item asked about is GameState.hovered()'s. Needs lupa."""
import os
import sys

from lupa import lua51

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import protocol  # noqa: E402
import strip_spec  # noqa: E402
import wowmock  # noqa: E402
from state import GameState  # noqa: E402

for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
          "TAVILY_API_KEY", "BRAVE_API_KEY"):
    os.environ[k] = ""

MOCK = r"""
LEVEL, CLASS, NAME = 60, "MAGE", "Nine"
function UnitName() return NAME end
function UnitClass() return "Mage", CLASS end
function UnitLevel() return LEVEL end
-- items: link -> equip location and stats (GetItemStats' keys); a link's fields as the game writes them
function link(id, name, ench, sfx, prefix)
  return (prefix or "|cffa335ee") .. "|Hitem:" .. id .. ":" .. (ench or "") .. ":::::" .. (sfx or "") .. "::60:::::|h["
    .. name .. "]|h|r"
end
ITEMS = {}
function item(id, name, loc, stats, ench, sfx, prefix)
  local l = link(id, name, ench, sfx, prefix); ITEMS[l] = { loc = loc, stats = stats }; return l
end
WORN = {}
function GetInventoryItemLink(_, slot) return WORN[slot] end
C_Item = { GetItemInfoInstant = function(l)
             if type(l) ~= "string" then return l end                  -- (an id: the client may not know it yet)
             local i = ITEMS[l]; return tonumber(l:match("item:(%d+)")), "", "", i and i.loc end,
           GetItemStats = function(l) return (ITEMS[l] or {}).stats end,
           GetItemInfo = function(l) local i = ITEMS[l]; if i then return l:match("%[(.-)%]") end end }
C_PlayerInfo = { CanUseItem = function() return true end }
printed = {}
function print(...) local t = {} for i = 1, select("#", ...) do t[#t + 1] = tostring(select(i, ...)) end
  printed[#printed + 1] = table.concat(t, " ") end
-- the game's popups: StaticPopup_Show empties the edit box, then shows the dialog (its OnShow); typing runs
-- EditBoxOnTextChanged, Enter EditBoxOnEnterPressed. POPUP_STYLE "field": the older dialog.editBox, "method":
-- Midnight's dialog:GetEditBox()
StaticPopupDialogs = {}
POPUPS = {}
POPUP_STYLE = "field"
function StaticPopup_Show(which, a, b, data)
  local info = StaticPopupDialogs[which]
  local box = { text = "", maxLetters = 255 }
  local dialog = { which = which, data = data, shown = false }
  function box:SetText(t) self.text = t; if info.EditBoxOnTextChanged then info.EditBoxOnTextChanged(self, dialog.data) end end
  function box:GetText() return self.text end
  function box:HighlightText() self.highlighted = self.text end
  function box:SetFocus() self.focused = true end
  function box:SetMaxLetters(n) self.maxLetters = n end
  function box:SetMaxBytes(n) self.maxBytes = n end
  function box:GetParent() return dialog end
  function box:Type(t) self:SetText(t) end
  function box:Enter() info.EditBoxOnEnterPressed(self, dialog.data) end
  if POPUP_STYLE == "method" then function dialog:GetEditBox() return box end else dialog.editBox = box end
  function dialog:Hide() self.shown = false end
  dialog.box = box
  if info.hasEditBox then box.maxLetters = info.maxLetters or 255; box.text = "" end
  dialog.shown = true
  if info.OnShow then info.OnShow(dialog, data) end
  POPUPS[#POPUPS + 1] = dialog
  return dialog
end
"""

SITE = "https://www.wowsims.com/forever/"


def boot(nonce=None):
    """A fresh UI load: the TOC's files (the recorder's data left out) on the mocked API -> (L, ns, G); nonce: the
    GlideLinkDB.sim.n the last session saved."""
    L = lua51.LuaRuntime(unpack_returned_tuples=True)
    L.execute(wowmock.MOCK)
    L.execute(MOCK)
    if nonce is not None:
        L.execute("GlideLinkDB = { sim = { n = %d } }" % nonce)
    ns = L.table()
    toc = [ln.strip() for ln in open(os.path.join(ROOT, "addon", "Glide", "Glide.toc"), encoding="utf-8")
           if ln.strip().endswith(".lua") and (ln.strip() in ("Data\\SimWeights.lua", "Data\\Live.lua") or
                                                not ln.startswith(("Data", "Recorder", "Scanner", "RXPDump")))]
    assert toc.index("State.lua") < toc.index("Compare.lua"), toc
    assert "Sims.lua" not in toc, "no new file: a TOC line needs a full game restart"
    for f in toc:
        src = open(os.path.join(ROOT, "addon", "Glide", *f.split("\\")), encoding="utf-8").read()
        L.eval("function(src, name, a, ns) local f, e = loadstring(src, name) if not f then error(e) end "
               "return f(a, ns) end")(src, "@" + f, "Glide", ns)
    G = L.globals()
    G.ns = ns
    G.fire("ADDON_LOADED", "Glide")
    G.fire("PLAYER_LOGIN")
    return L, ns, G


L, ns, G = boot()


def core():
    """The core the addon sends now, as the engine reads it."""
    return protocol.parse_payload(ns.Core() + "|")[0]


def advance(seconds):
    for _ in range(round(seconds / 0.25)):
        G.tick()


def said():
    """What the addon printed since the last call."""
    out = [s for s in G.printed.values()]
    L.execute("printed = {}")
    return out


def glide(cmd):
    G.SlashCmdList.GLIDE(cmd)
    return said()


item = G.item
toc_version = [ln for ln in open(os.path.join(ROOT, "addon", "Glide", "Glide.toc"), encoding="utf-8")
               if ln.startswith("## Version:")]
assert toc_version == ["## Version: 0.23.0\n"], toc_version

# ---- /glide sim: the request form and the nonce --------------------------------------------------------------------
ring = item(19136, "Ring of Sorcery", "INVTYPE_FINGER", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 15 }"), 2564, -13002)
assert "hv" not in core(), "nothing asked, nothing looked at"
out = glide("sim " + ring)
assert any("simming" in s and "[Ring of Sorcery]" in s and "on your gear" in s for s in out), out
assert not any("strip is off" in s for s in out), out
kv = core()
assert kv["hv"] == "19136.13002.2564!1", kv.get("hv")
assert strip_spec.hv_request(kv["hv"]) == {"id": 19136, "sfx": 13002, "ench": 2564, "n": 1}
assert G.GlideLinkDB.sim.n == 1, "the nonce is kept for the next UI load"
# it takes the hover's place for 3 s from its first core, then the hover is back (an item that left its tooltip a
# moment ago: State.lua holds it 15 s)
L.execute("ns.hoverLast, ns.hoverAt = 18842, GetTime()")
advance(2.75)
assert core()["hv"] == "19136.13002.2564!1", "still going out"
advance(0.25)
assert core()["hv"] == "18842", "3 s: the hover again"
L.execute("ns.hoverLast, ns.hoverAt = nil, nil")
assert "hv" not in core()

# the same item again: a new request (the next nonce); Forever's "|cnIQ4:" colour prefix doesn't shift the fields
forever = item(19136, "Ring of Sorcery", "INVTYPE_FINGER", L.eval("{}"), 2564, -13002, "|cnIQ4:")
glide("sim " + forever)
assert core()["hv"] == "19136.13002.2564!2", core().get("hv")
advance(3)
assert "hv" not in core()
# an item without a suffix or an enchant, and a bare item id: zeros
plain = item(16686, "Magister's Crown", "INVTYPE_HEAD", L.eval("{}"))
glide("sim " + plain)
assert core()["hv"] == "16686.0.0!3", core().get("hv")
advance(3)
glide("sim 22589")
assert core()["hv"] == "22589.0.0!4", core().get("hv")
advance(3)
# the nonce goes 1 to 9 and round again
for n in (5, 6, 7, 8, 9, 1, 2):
    glide("sim " + plain)
    assert core()["hv"] == "16686.0.0!%d" % n, (n, core().get("hv"))
    advance(3)
assert "hv" not in core()

# one asked while another goes out waits its turn; a fourth while three wait is refused
glide("sim " + ring)
assert core()["hv"] == "19136.13002.2564!3"
advance(1)
glide("sim " + plain)
glide("sim 22589")
out = glide("sim 16686")
assert any("still sending your last sims" in s for s in out) and G.GlideLinkDB.sim.n == 5, (out, G.GlideLinkDB.sim.n)
assert core()["hv"] == "19136.13002.2564!3"
advance(2)
assert core()["hv"] == "16686.0.0!4", "the second, once the first had its 3 s"
advance(3)
assert core()["hv"] == "22589.0.0!5", "then the third"
advance(3)
assert "hv" not in core()

# nothing sent: no item, an item that isn't gear, numbers too long for the form
for cmd in ("sim", "sim   ", "sim hello", "sim [Ring of Sorcery]"):
    out = glide(cmd)
    assert any("shift-click an item after /glide sim" in s for s in out), (cmd, out)
hearth = item(6948, "Hearthstone", "", L.eval("{}"))
out = glide("sim " + hearth)
assert any("[Hearthstone]" in s and "isn't gear" in s for s in out), out
out = glide("sim 1234567")
assert any("can't read that item" in s for s in out), out
assert ns.SimRequest(19136, L.eval("0/0"), 0) == (None, "item") and ns.SimRequest(0, 0, 0) == (None, "item")
assert "hv" not in core() and G.GlideLinkDB.sim.n == 5, "none of them went"

# the strip off (no frames, so no core): said so; the request waits for it 30 s after it was asked, then it's dropped
glide("strip off")
out = glide("sim " + plain)
assert any("strip is off" in s and "/glide strip on" in s for s in out), out
advance(30.25)
glide("strip on")
assert "hv" not in core(), "dropped: the answer would come out of nowhere"
glide("strip off")
glide("sim " + plain)
advance(29)
glide("strip on")                                       # (on again in time: it goes)
assert core()["hv"] == "16686.0.0!7", core().get("hv")
advance(3)

# the budget: left out of a frame whose core would pass it, sent in the next
form = ns.SimRequest(19136, 13002, 2564)
assert form == "19136.13002.2564!8", form
long_core = "x" * 425                                   # 425 + ";hv=19136.13002.2564!8" > 442
assert ns.CoreOptional(long_core) == long_core, "left out"
assert ns.CoreOptional(long_core) == long_core + ";hv=" + form, "in the next frame"
assert ns.CoreOptional(long_core) == long_core, "and out of the one after"
assert ns.CoreOptional("x" * 400) == "x" * 400 + ";hv=" + form, "a short core: every frame"
advance(3)

# a new UI load (/reload) goes on from the nonce the last one kept, so a request then is never taken for the last one
L, ns, G = boot(nonce=8)
item = G.item
plain = item(16686, "Magister's Crown", "INVTYPE_HEAD", L.eval("{}"))
glide("sim " + plain)
assert core()["hv"] == "16686.0.0!9", core().get("hv")
advance(3)
glide("sim " + plain)
assert core()["hv"] == "16686.0.0!1", core().get("hv")
advance(3)

# ---- the engine: GameState.sim_request(), through the real payload --------------------------------------------------
st = GameState()
glide("sim " + plain)
payload = ns.Core()
st.update(protocol.parse_payload(payload + "|")[0], {}, now=1000.0)
assert st.sim_request(now=1000.0) == {"id": 16686, "sfx": 0, "ench": 0, "n": 2}
assert st.sim_request(now=1000.0) is None, "taken once"
assert st.hovered(now=1000.0) == {"id": 16686, "t": 1000.0}, "the item asked about is the one looked at"
# the frames repeating it (3 s of them, one now and then left out by the budget) don't bring it back
for t in (1000.2, 1000.4, 1001.0, 1002.9):
    st.update({"hv": "16686.0.0!2"}, {}, now=t)
    st.update({"l": "60"}, {}, now=t + 0.1)
assert st.sim_request(now=1003.0) is None
# the same item asked again: a new nonce, a new request; the same form after 10 s without it is new too (its nonce
# came round again, or a crash cost the addon the one it kept)
st.update({"hv": "16686.0.0!3"}, {}, now=1010.0)
assert st.sim_request(now=1010.0) == {"id": 16686, "sfx": 0, "ench": 0, "n": 3}
st.update({"hv": "16686.0.0!3"}, {}, now=1019.9)
assert st.sim_request(now=1020.0) is None, "within 10 s: the same"
st.update({"hv": "16686.0.0!3"}, {}, now=1030.0)
assert st.sim_request(now=1030.0) == {"id": 16686, "sfx": 0, "ench": 0, "n": 3}, "after 10 s without it: new"
# two before a call: oldest first; one nobody took for 30 s: dropped
st.update({"hv": "19136.13002.2564!4"}, {}, now=1040.0)
st.update({"hv": "22589.0.0!5"}, {}, now=1043.0)
assert st.sim_request(now=1044.0) == {"id": 19136, "sfx": 13002, "ench": 2564, "n": 4}
assert st.sim_request(now=1044.0) == {"id": 22589, "sfx": 0, "ench": 0, "n": 5}
assert st.sim_request(now=1044.0) is None
st.update({"hv": "16686.0.0!6"}, {}, now=1050.0)
assert st.sim_request(now=1080.5) is None, "30 s untaken"
# junk is no request (and no hover); the plain item id is the hover, no request
st = GameState()
for junk in ("", "0", "19136.0.0", "19136.0.0!0", "19136.0.0!10", "1234567.0.0!1", "19136.123456.0!1", "a.b.c!1",
             "19136.0.0!1x", "0.0.0!1", "-5.0.0!1", "19136,0,0!1", "１９１３６.0.0!1"):
    st.update({"hv": junk}, {}, now=2000.0)
    assert st.sim_request(now=2000.0) is None and strip_spec.hv_request(junk) is None, junk
    assert st.hovered(now=2000.0) is None, junk
st.update({"hv": "19136"}, {}, now=2001.0)
assert st.sim_request(now=2001.0) is None and st.hovered(now=2001.0) == {"id": 19136, "t": 2001.0}
assert strip_spec.hv_request(19136) is None and strip_spec.hv_request(None) is None

# ---- /glide wowsims: the link in a popup to copy -----------------------------------------------------------------
url = SITE + "mage/dps/?i=gtm#eJzjYGBgYGRgYmBi4GBkYgQAAeUAIg=="
out = glide("wowsims")
assert any("no WoWSims link for this character yet" in s for s in out) and len(G.POPUPS) == 0, out
chars = ns.live.chars
chars["Nine-TestRealm"] = L.eval('{ link = "%s" }' % url)
out = glide("wowsims")
assert out == [] and len(G.POPUPS) == 1, out
d = G.POPUPS[1]
info = G.StaticPopupDialogs[d.which]
assert d.which == "GLIDE_WOWSIMS_LINK" and d.shown and info.hasEditBox and info.timeout == 0, d.which
assert "WoWSims (wowsims.com/forever)" in info.text and "Ctrl+C" in info.text, info.text
assert "—" not in info.text and "dashboard" not in info.text.lower()
box = d.box
assert box.text == url and box.highlighted == url and box.focused, box.text
assert box.maxLetters == 0, "no letter limit: the link is longer than the default 255"
box.Type(box, "oops")
assert box.text == url and box.highlighted == url, "typed over: the link again, selected"
box.Enter(box)
assert not d.shown, "Enter closes it"
assert not G.GlideOptionsFrame or not G.GlideOptionsFrame.shown, "not the Glide window"
# Midnight's GetEditBox; the name-only key (the engine doesn't know the realm)
L.execute('POPUP_STYLE = "method"')
del chars["Nine-TestRealm"]
chars["Nine"] = L.eval('{ link = "%s" }' % (url + "x"))
glide("wowsims")
d = G.POPUPS[2]
assert d.box.text == url + "x" and d.box.highlighted == url + "x", d.box.text
# only WoWSims' own site; nothing else that could be a link
for bad in ("https://evil.example/forever/", "http://www.wowsims.com/forever/mage/dps/", SITE + "mage/dps/ x",
            SITE + "mage|r", "", 42):
    chars["Nine"] = L.eval("{ link = %s }" % (repr(bad) if isinstance(bad, str) else bad))
    out = glide("wowsims")
    assert any("no WoWSims link" in s for s in out) and len(G.POPUPS) == 2, (bad, out)
# no Live.lua (the game hasn't loaded it); no popups at all: the link in chat
L.execute("LIVE = ns.live; ns.live = nil")
out = glide("wowsims")
assert any("restart WoW once" in s for s in out) and len(G.POPUPS) == 2, out
L.execute("ns.live = LIVE")
chars["Nine"] = L.eval('{ link = "%s" }' % url)
L.execute("SP = StaticPopup_Show; StaticPopup_Show = nil")
out = glide("wowsims")
assert any(url in s for s in out) and len(G.POPUPS) == 2, out
L.execute("StaticPopup_Show = SP")

# ---- the upgrade line takes the simmed number ------------------------------------------------------------------
# the weights the app wrote for Nine (as test_addon_compare): a ring against the weaker worn one
ns.simWeights = L.eval('{ ["Nine"] = { class = "MAGE", spec = "Arcane", dps = 600, perRef = 0.52, wowsims = "v1.0.4", '
                       'w = { SP = 1, IN = 0.865, CS = 9.038 } } }')
old = item(1001, "Band of Old", "INVTYPE_FINGER", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 10 }"))
better = item(1002, "Loop of Better", "INVTYPE_FINGER", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 20 }"))
G.WORN[11], G.WORN[12] = old, better
new = item(1003, "Ring of New", "INVTYPE_FINGER", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 15, "
                                                         "ITEM_MOD_CRIT_SPELL_RATING_SHORT = 1 }"))


def line(link):
    got = ns.CompareLine(link)
    if not got:
        return None
    text, r, g, b = got
    return text, r, g, b


est = "Glide (Arcane): +6.9 DPS (+1.2%) vs Band of Old"
assert line(new)[0] == est, line(new)
sims = L.eval("{}")
chars["Nine"].sim = sims
sims[1003] = L.eval("{ 8.24, 0, 11, 1001, 1002 }")              # (in ring 1 over Band of Old, ring 2: Loop of Better)
text, r, g, b = line(new)
assert text == "Glide (Arcane): +8.2 DPS (+1.4%) vs Band of Old" and g > r, text      # 8.24 / 600 = 1.37%
sims[1003][1] = -2
text, r, g, b = line(new)
assert text == "Glide (Arcane): -2.0 DPS (-0.3%) vs Band of Old" and r > g, text
sims[1003][1] = 0.04
assert line(new)[0] == "Glide (Arcane): no change vs Band of Old", line(new)
sims[1003][1] = 0                                               # (the engine's for a gain within the sims' noise)
assert line(new)[0] == "Glide (Arcane): no change vs Band of Old", line(new)
for junk in ("0/0", '"8"', "8.24", "{ 8.24 }", "{ 8.24, 0, nil, 1001, 1002 }", "{ 8.24, 0, 1, 1001, 1002 }",
             "{ 8.24, 0, 11, 1001 }", "{ 8.24, 0, 11, nil, 1002 }"):
    sims[1003] = L.eval(junk)
    assert line(new)[0] == est, ("not a sim the line can use: the estimate", junk)
# the sim replaced the other ring (by the weights Band of Old is the weaker, by the sims Loop of Better): said against
# Loop of Better, as the coach says it
sims[1003] = L.eval("{ 5.0, 0, 12, 1002, 1001 }")
assert line(new)[0] == "Glide (Arcane): +5.0 DPS (+0.8%) vs Loop of Better", line(new)
# simmed against a ring that isn't worn now (swapped while the game was closed, or before this login), or with
# another ring in the other slot: the estimate, at login already
sims[1003] = L.eval("{ 8.24, 0, 11, 1999, 1002 }")
assert line(new)[0] == est, "the ring it replaced is gone"
sims[1003] = L.eval("{ 8.24, 0, 11, 1001, 1998 }")
assert line(new)[0] == est, "the other ring is another"
sims[1003] = L.eval("{ 8.24, 0, 11, 0, 1002 }")
assert line(new)[0] == est, "simmed into an empty slot that now holds a ring"
# random suffixes: the suffix it was simmed with only (0: none)
sorcery = item(1004, "Ring of Sorcery", "INVTYPE_FINGER", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 15 }"), None, -13002)
monkey = item(1004, "Ring of the Monkey", "INVTYPE_FINGER", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 15 }"), None, -1200)
sorcery_est = line(sorcery)[0]
assert sorcery_est == "Glide (Arcane): +2.2 DPS (+0.4%) vs Band of Old", sorcery_est
sims[1004] = L.eval("{ 5, 0, 11, 1001, 1002 }")
assert line(sorcery)[0] == sorcery_est, "simmed without a suffix: not for an item with one"
sims[1004] = L.eval("{ 5.5, 13002, 11, 1001, 1002 }")
assert line(sorcery)[0] == "Glide (Arcane): +5.5 DPS (+0.9%) vs Band of Old", line(sorcery)
assert line(monkey)[0] == "Glide (Arcane): +2.2 DPS (+0.4%) vs Band of Old", "another suffix: the estimate"
# a slot it compares against changed after login: the sim was on the gear before, the estimate again (what the login
# itself does isn't a change)
sims[1003] = L.eval("{ 8.24, 0, 11, 1001, 1002 }")
G.fire("PLAYER_EQUIPMENT_CHANGED", 11, True)
assert line(new)[0] == "Glide (Arcane): +8.2 DPS (+1.4%) vs Band of Old", "before PLAYER_ENTERING_WORLD"
G.fire("PLAYER_ENTERING_WORLD", True, False)
helm = item(1005, "Hood", "INVTYPE_HEAD", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 10 }"))
sims[1005] = L.eval("{ 3.6, 0, 1, 0 }")
G.fire("PLAYER_EQUIPMENT_CHANGED", 12, True)
assert line(new)[0] == est, "the other ring slot changed: the estimate"
assert line(helm)[0] == "Glide (Arcane): +3.6 DPS (+0.6%) vs an empty slot", "another slot: still the sim"
# a two-hander is set against main and off hand: either one changed drops its sim
G.WORN[16] = item(2002, "Old Sword", "INVTYPE_WEAPONMAINHAND", L.eval("{}"))
G.WORN[17] = item(2003, "Orb", "INVTYPE_HOLDABLE", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 10 }"))
staff = item(2004, "Great Staff", "INVTYPE_2HWEAPON", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 30 }"))
sims[2004] = L.eval("{ 12, 0, 16, 2002, 2003 }")
assert line(staff)[0] == "Glide (Arcane): +12.0 DPS (+2.0%) vs Old Sword", line(staff)
sims[2004] = L.eval("{ 12, 0, 16, 2002, 0 }")
assert line(staff)[0] == "Glide (Arcane): +9.0 DPS (+1.5%) vs Old Sword", "simmed with no off hand: one is worn now"
sims[2004] = L.eval("{ 12, 0, 16, 2002, 2003 }")
G.fire("PLAYER_EQUIPMENT_CHANGED", 17, True)
assert line(staff)[0] == "Glide (Arcane): +9.0 DPS (+1.5%) vs Old Sword", line(staff)
# a sim for another character or none at all: the estimate
del chars["Nine"]
chars["Other"] = L.eval("{ sim = { [1005] = { 9, 0, 1, 0 } } }")
assert line(helm)[0] == "Glide (Arcane): +4.5 DPS (+0.7%) vs an empty slot", line(helm)
# a Live.lua that fails to read leaves the estimate, never the line
chars["Nine"] = L.eval("{ sim = { [1005] = { 9, 0, 1, 0 } } }")
L.execute("CHARS = ns.live.chars; ns.live.chars = setmetatable({}, { __index = function() error('boom') end })")
assert line(helm)[0] == "Glide (Arcane): +4.5 DPS (+0.7%) vs an empty slot", line(helm)
L.execute("ns.live.chars = CHARS")
assert line(helm)[0] == "Glide (Arcane): +9.0 DPS (+1.5%) vs an empty slot", line(helm)

# a new login whose Live.lua was written before the player swapped a ring (the Glide app wasn't running, or WoWSims was
# updating): no PLAYER_EQUIPMENT_CHANGED this session, yet the sim against the ring that's gone isn't said
L, ns, G = boot()
item = G.item
ns.simWeights = L.eval('{ ["Nine"] = { class = "MAGE", spec = "Arcane", dps = 600, perRef = 0.52, wowsims = "v1.0.4", '
                       'w = { SP = 1, IN = 0.865, CS = 9.038 } } }')
G.WORN[11] = item(1006, "Band of Newer", "INVTYPE_FINGER", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 10 }"))
G.WORN[12] = item(1002, "Loop of Better", "INVTYPE_FINGER", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 20 }"))
new = item(1003, "Ring of New", "INVTYPE_FINGER", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 15, "
                                                         "ITEM_MOD_CRIT_SPELL_RATING_SHORT = 1 }"))
ns.live.chars["Nine"] = L.eval("{ sim = { [1003] = { 8.24, 0, 11, 1001, 1002 } } }")
G.fire("PLAYER_ENTERING_WORLD", True, False)
assert ns.CompareLine(new)[0] == "Glide (Arcane): +6.9 DPS (+1.2%) vs Band of Newer", ns.CompareLine(new)
ns.live.chars["Nine"].sim[1003][4] = 1006                         # (simmed against the ring worn now: said)
assert ns.CompareLine(new)[0] == "Glide (Arcane): +8.2 DPS (+1.4%) vs Band of Newer", ns.CompareLine(new)
print("OK")
