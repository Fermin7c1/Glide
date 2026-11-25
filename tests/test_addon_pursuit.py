#!/usr/bin/env python3
"""The next move toward the next BiS piece in the game (addon 0.24.0), under Lua 5.1 with the mocked WoW API (the
TOC's files, Data/Live.lua as the engine's live_data.py writes it):
- Pursuit.lua: "/glide next" says the move and two alternatives (ns.live.chars[Name-Realm].next); a new move is said
  once per move, a few seconds into the world (after a fight, when it came in one), never again on a /reload; the
  game's own pin (C_Map.SetUserWaypoint, super-tracked) goes down by itself only with pins "free" (the game's
  setting and the app's ns.live.pursuit.pin: the more careful wins), nothing else super-tracked or pinned, RestedXP's
  arrow empty, out of combat; Glide's own earlier pin is replaced; "/glide next pin" places it on request (not in
  combat, not with pins off); a pin the game refuses (the pcall) turns pins off until the next /reload; a map the game
  can't pin says so and leaves pins on.
- Compare.lua: the chased item (ns.live.chars[key].goal) says "your next BiS goal" on the existing second Glide line
  (the BiS line, else the source line, else alone), never a third line, nothing once worn or with the BiS line off.
- Recorder.lua: session.factions[id] = {standing, value, t} for WoWSims' 15 factions (at login and after
  UPDATE_FACTION; C_Reputation or the classic GetFactionInfoByID), GlideRecorderDB.auction[item] = {copper, t} for
  the plan's items the player sees while browsing the auction house (the client's browse, item and commodity results,
  or the classic list), the lowest buyout for one, never a search of Glide's own, the newest 300 kept; on a client
  that lists no skill lines (Forever's), session.skills.lines from the spells it knows (each profession by its rank
  spells, the weapon and armor skills), which the engine's recorder_data.py reads as the character's professions.
Needs lupa."""
import os
import sys
import tempfile

try:
    from lupa import lua51
except ImportError:
    print("SKIP: needs lupa")
    sys.exit(77)

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import wowmock  # noqa: E402
import live_data  # noqa: E402

for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
          "TAVILY_API_KEY", "BRAVE_API_KEY"):
    os.environ[k] = ""

GLIDE = os.path.join(ROOT, "addon", "Glide")
toc_text = open(os.path.join(GLIDE, "Glide.toc"), encoding="utf-8").read().splitlines()
VERSION = [ln for ln in toc_text if ln.startswith("## Version:")]
assert len(VERSION) == 1 and tuple(int(x) for x in VERSION[0].split(":")[1].strip().split(".")) >= (0, 24, 0), VERSION
TOC = [ln.strip() for ln in toc_text if ln.strip().endswith(".lua")]
assert TOC.index("Compare.lua") < TOC.index("Pursuit.lua") < TOC.index("Panel.lua"), TOC
assert TOC[-1] == "Panel.lua", TOC

MOCK = r"""
LEVEL, CLASS, NAME = 60, "MAGE", "Nine"
function UnitName() return NAME end
function UnitClass() return "Mage", CLASS end
function UnitLevel() return LEVEL end
printed = {}
function print(...) local t = {} for i = 1, select("#", ...) do t[#t + 1] = tostring(select(i, ...)) end
  printed[#printed + 1] = table.concat(t, " ") end
-- items: link -> equip location and stats; what is worn
local function link(id, name) return "|cffa335ee|Hitem:" .. id .. "::::::::60:::::|h[" .. name .. "]|h|r" end
ITEMS = {}
function item(id, name, loc, stats) local l = link(id, name); ITEMS[l] = { loc = loc, stats = stats }; return l end
WORN = {}
function GetInventoryItemLink(_, slot) return WORN[slot] end
C_Container.GetContainerNumSlots = function() return 0 end        -- (empty bags: no item the client is still loading)
C_Item = { GetItemInfoInstant = function(l) local i = ITEMS[l]; return tonumber(l:match("item:(%d+)")), "", "", i and i.loc end,
           GetItemStats = function(l) return (ITEMS[l] or {}).stats end,
           GetItemInfo = function(l) local i = ITEMS[l]; if i then return l:match("%[(.-)%]") end end }
C_PlayerInfo = { CanUseItem = function() return true end }
POSTCALLS = {}
Enum = Enum or {}
Enum.TooltipDataType = { Item = 0 }
TooltipDataProcessor = { AddTooltipPostCall = function(kind, fn) POSTCALLS[kind] = fn end }
GameTooltip = { lines = {} }
function GameTooltip:GetItem() return "x", self.link end
function GameTooltip:AddLine(t, r, g, b, wrap) self.lines[#self.lines + 1] = { text = t, r = r, g = g, b = b, wrap = wrap } end
ItemRefTooltip = { lines = {}, GetItem = GameTooltip.GetItem, AddLine = GameTooltip.AddLine }
function lines(l) GameTooltip.link, GameTooltip.lines = l, {}; POSTCALLS[0](GameTooltip, {}); return GameTooltip.lines end
-- the game's map pin: one user waypoint, what is super-tracked ("pin", "quest", nil), each SetUserWaypoint counted
PIN, TRACKED, PIN_CALLS, PIN_FAIL, NO_PIN_MAP = nil, nil, 0, false, {}
local VEC = { __index = { GetXY = function(self) return self.x, self.y end } }
function point(m, x, y) return { uiMapID = m, position = setmetatable({ x = x, y = y }, VEC) } end
UiMapPoint = { CreateFromCoordinates = function(m, x, y) return point(m, x, y) end }
C_Map.SetUserWaypoint = function(p) PIN_CALLS = PIN_CALLS + 1; if PIN_FAIL then error("blocked by the client") end; PIN = p end
C_Map.ClearUserWaypoint = function() PIN = nil; if TRACKED == "pin" then TRACKED = nil end end
C_Map.HasUserWaypoint = function() return PIN ~= nil end
C_Map.GetUserWaypoint = function() return PIN end
C_Map.CanSetUserWaypointOnMap = function(m) return not NO_PIN_MAP[m] end
TRACK_FAIL = false
C_SuperTrack = { SetSuperTrackedUserWaypoint = function(on) if TRACK_FAIL then error("tracking blocked") end
                   if on then TRACKED = "pin" elseif TRACKED == "pin" then TRACKED = nil end end,
                 IsSuperTrackingAnything = function() return TRACKED ~= nil end,
                 IsSuperTrackingUserWaypoint = function() return TRACKED == "pin" end }
-- reputation (the client's C_Reputation): id -> { name, reaction, currentStanding }; the ids asked
REP, REP_ASKED = {}, {}
C_Reputation = { GetFactionDataByID = function(id) REP_ASKED[id] = true; return REP[id] end }
-- the auction house as the client has it now; Glide must never search (SEARCHED)
BROWSE, ITEM_RESULTS, COMMODITY, SEARCHED = {}, {}, {}, nil
C_AuctionHouse = { GetBrowseResults = function() return BROWSE end,
  GetNumItemSearchResults = function(key) return #(ITEM_RESULTS[key.itemID] or {}) end,
  GetItemSearchResultInfo = function(key, i) return (ITEM_RESULTS[key.itemID] or {})[i] end,
  GetCommoditySearchResultInfo = function(id, i) return (COMMODITY[id] or {})[i] end,
  SendBrowseQuery = function() SEARCHED = true end, SendSearchQuery = function() SEARCHED = true end,
  SearchForItemKeys = function() SEARCHED = true end, RequestMoreBrowseResults = function() SEARCHED = true end }
-- GlideLinkDB and the game's pin as a Lua chunk: what a /reload keeps
local function dump(v)
  if type(v) == "table" then
    local out = {}
    for k, x in pairs(v) do out[#out + 1] = "[" .. dump(k) .. "] = " .. dump(x) end
    return "{ " .. table.concat(out, ", ") .. " }"
  elseif type(v) == "string" then return string.format("%q", v) end
  return tostring(v)
end
function carry()
  local pin = PIN and string.format("point(%d, %.17g, %.17g)", PIN.uiMapID, PIN.position.x, PIN.position.y) or "nil"
  return "GlideLinkDB = " .. dump(GlideLinkDB or {}) .. "\nPIN = " .. pin .. "\nTRACKED = " .. dump(TRACKED)
end
"""

SCHOLO = "Scholomance: Bonecreeper Stylus"
ALTS = ["Deliana, Ironforge: Sorcerer's Crown", "Stratholme: Magister's Crown"]
GANDLING = "drops from Darkmaster Gandling, Scholomance"
STYLUS, CROWN, OTHER, ARCANITE = 13938, 5001, 9999, 12360


def live_text(nxt, goal=STYLUS, pursuit=None, bis=None, extra=None):
    """Data/Live.lua as the engine writes it (live_data.py), with this "next" for Nine on TestRealm."""
    ld = live_data.LiveData(tempfile.mkdtemp(), lambda: None, lambda: "0.24.0")
    ld.register("items", lambda: {STYLUS: {"t": GANDLING, "a": "n"}})
    me = {"next": nxt, "goal": goal, "bis": bis if bis is not None else {STYLUS: ["Ranged", 1, 7.2],
                                                                          CROWN: ["Head", 2, 6.1]}}
    me.update(extra or {})
    ld.register("chars", lambda: {"Nine-TestRealm": me})
    if pursuit:
        ld.register("pursuit", lambda: pursuit)
    return ld.text()


def move(t=SCHOLO, m=1422, x=0.69, y=0.73, alt=ALTS):
    return {"t": t, "m": m, "x": x, "y": y, "alt": alt}


def boot(text, pre="", enter=True):
    """A UI load: the TOC's files on the mocked API, then this Live.lua -> (L, ns, G). pre: Lua run first (what the
    last UI load left: GlideLinkDB, the game's pin; the client's own API)."""
    L = lua51.LuaRuntime(unpack_returned_tuples=True)
    L.execute(wowmock.MOCK)
    L.execute(MOCK)
    if pre:
        L.execute(pre)
    ns = L.table()
    load = L.eval("function(src, name, a, ns) local f, e = loadstring(src, name) if not f then error(e) end "
                  "return f(a, ns) end")
    for f in TOC:
        load(open(os.path.join(GLIDE, *f.split("\\")), encoding="utf-8").read(), "@" + f, "Glide", ns)
    if text is not None:
        load(text, "@Data\\Live.lua", "Glide", ns)
    G = L.globals()
    G.fire("ADDON_LOADED", "Glide")
    G.fire("PLAYER_LOGIN")
    L.execute("printed = {}")
    if enter:
        G.fire("PLAYER_ENTERING_WORLD", True, False)
    return L, ns, G


def said(L):
    """What the addon printed since the last call, with Glide's coloured name as plain text."""
    out = [s.replace("|cff66ccffGlide|r", "Glide") for s in L.globals().printed.values()]
    L.execute("printed = {}")
    return out


def pin(G):
    p = G.PIN
    return None if p is None else (p.uiMapID, round(p.position.x, 4), round(p.position.y, 4))


# ---- a new move: said once, the pin by itself when nothing else guides ---------------------------------------------
L, ns, G = boot(live_text(move()))
assert said(L) == ["Glide next: " + SCHOLO + " (pinned on your map)"], "said at the moment it came"
assert pin(G) == (1422, 0.69, 0.73) and G.TRACKED == "pin" and G.PIN_CALLS == 1, (pin(G), G.TRACKED)
assert ns.errors == 0, ns.lastError
db = G.GlideLinkDB.pursuit
assert db.pin == "free" and db.told["Nine-TestRealm"] and db.placed.m == 1422, "kept for the next UI load"

# /glide next: the move and the two next best
G.SlashCmdList.GLIDE("next")
assert said(L) == ["Glide next: " + SCHOLO + " (/glide next pin puts it on your map)",
                   "  or: " + ALTS[0], "  or: " + ALTS[1]], "the move and two alternatives"
G.SlashCmdList.GLIDE("next junk")
assert any("/glide next [pin]" in s for s in said(L)), "an unknown word: the help"

# ---- the chased item on its tooltip: on the second Glide line, never a third ------------------------------------
ns.simWeights = L.eval('{ ["Nine"] = { class = "MAGE", spec = "Arcane", dps = 600, perRef = 0.52, w = { SP = 1, IN = 0.865 } } }')
stylus = G.item(STYLUS, "Bonecreeper Stylus", "INVTYPE_RANGEDRIGHT", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 8 }"))
crown = G.item(CROWN, "Sorcerer's Crown", "INVTYPE_HEAD", L.eval("{ ITEM_MOD_INTELLECT_SHORT = 25 }"))


def texts(link):
    return [ln.text for ln in G.lines(link).values()]


up = "Glide (Arcane): +3.6 DPS (+0.6%) vs an empty slot"
assert texts(stylus) == [up, "Glide BiS #1 for Ranged, +7.2 DPS, your next BiS goal: " + GANDLING], texts(stylus)
assert texts(crown)[1] == "Glide BiS #2 for Head, +6.1 DPS", "another item: no goal"
chars = ns.live.chars
me = chars["Nine-TestRealm"]
me.bis[STYLUS] = None                                                           # (not ranked: the source line)
assert texts(stylus) == [up, "Glide, your next BiS goal: " + GANDLING], texts(stylus)
ns.live["items"][STYLUS] = None                                                 # (no known source either)
assert texts(stylus) == [up, "Glide: your next BiS goal"], texts(stylus)
G.SlashCmdList.GLIDE("sources off")
assert texts(stylus) == [up, "Glide: your next BiS goal"], "the source line off: the goal alone"
ns.live["items"][STYLUS] = L.eval('{ "%s", a = "n" }' % GANDLING)
assert texts(stylus) == [up, "Glide: your next BiS goal"], "nor where it comes from"
G.SlashCmdList.GLIDE("sources on")
G.SlashCmdList.GLIDE("bis off")
assert texts(stylus) == [up, "Glide: " + GANDLING], "the BiS line off: no goal"
G.SlashCmdList.GLIDE("bis on")
me.bis[STYLUS] = L.eval('{ "Ranged", 1, 7.2 }')
G.WORN[18] = stylus                                                             # (worn: got it)
assert texts(stylus) == ["Glide BiS #1 for Ranged: you wear it"], texts(stylus)
me.bis[STYLUS] = None
assert texts(stylus) == ["Glide: " + GANDLING], "worn and not ranked: no goal"
G.WORN[18] = None
me.bis[STYLUS] = L.eval('{ "Ranged", 1, 7.2 }')
me.goal = "junk"
assert texts(stylus)[1] == "Glide BiS #1 for Ranged, +7.2 DPS: " + GANDLING, "a broken goal: none"
me.goal = STYLUS
ns.live.chars = L.eval("setmetatable({}, { __index = function() error('boom') end })")
assert texts(stylus) == [up, "Glide: " + GANDLING], "a failing goal check leaves the source line"
ns.live.chars = chars
# regression: a goal ranked with a random suffix is the goal with that suffix only: another roll of it (browsing the
# auction house) isn't, and wearing another roll doesn't reach it
BRACERS = 15000
roll = L.eval('function(sfx, name) local l = "|cff1eff00|Hitem:15000::::::" .. sfx .. "::60:::::|h[" .. name .. '
              '"]|h|r"; ITEMS[l] = { loc = "INVTYPE_WRIST", stats = {} }; return l end')
sorcery, monkey = roll("-1825", "Bracers of Sorcery"), roll("-600", "Bracers of the Monkey")
me.goal = BRACERS
me.bis[BRACERS] = L.eval('{ "Wrist", 1, 5.0, nil, nil, 1825 }')
assert not any("goal" in t for t in texts(monkey)), texts(monkey)
assert "Glide BiS #1 for Wrist, +5.0 DPS, your next BiS goal" in texts(sorcery), texts(sorcery)
G.WORN[9] = monkey
assert "Glide BiS #1 for Wrist, +5.0 DPS, your next BiS goal" in texts(sorcery), "another roll worn: the goal gone"
assert not any("goal" in t for t in texts(monkey)), texts(monkey)
G.WORN[9] = sorcery
assert "Glide BiS #1 for Wrist: you wear it" in texts(sorcery) and not any("goal" in t for t in texts(sorcery)), \
    texts(sorcery)
G.WORN[9] = None
me.bis[BRACERS] = None
me.goal = STYLUS
assert ns.errors == 0, ns.lastError
said(L)

# ---- the recorder: reputation and auction prices for the plan --------------------------------------------------------
L.execute("""REP[529] = { name = "Argent Dawn", reaction = 5, currentStanding = 3500 }
REP[2799] = { name = "Theramore Expeditionary Force", reaction = 6, currentStanding = 9100.4 }""")
G.fire("UPDATE_FACTION")
rdb = G.GlideRecorderDB
s = rdb.sessions[rdb.lastSession]
assert sorted(G.REP_ASKED.keys()) == [21, 59, 369, 470, 509, 510, 529, 576, 577, 609, 910, 2798, 2799, 2819, 2827]
f = s.factions
assert sorted(f.keys()) == [529, 2799], list(f.keys())
assert (f[529].standing, f[529].value, f[529].t) == (5, 3500, G.GetServerTime()), dict(f[529])
assert (f[2799].standing, f[2799].value) == (6, 9100), dict(f[2799])
assert f[529].v0 is None and f[529].t0 is None and f[529].t1 is None, "no rise yet: no grind"
L.execute("REP[529].currentStanding = 3650; REP[529].reaction = 5; NOW = NOW + 60")
G.fire("UPDATE_FACTION")
assert f[529].value == 3650 and f[529].t == G.GetServerTime(), "read again after a change"
# regression: the session's grind, its first rise to its last, so a rate is learned however long it runs between
# reloads (one reading per faction reaches the Glide app at each /reload)
first = G.GetServerTime()
assert (f[529].v0, f[529].t0, f[529].t1) == (3650, first, first), dict(f[529])
L.execute("NOW = NOW + 600")
G.fire("UPDATE_FACTION")                                                # (nothing changed: the grind stays)
assert (f[529].v0, f[529].t0, f[529].t1) == (3650, first, first) and f[529].t == G.GetServerTime(), dict(f[529])
L.execute("REP[529].currentStanding = 3800; NOW = NOW + 600")
G.fire("UPDATE_FACTION")
assert (f[529].value, f[529].v0, f[529].t0, f[529].t1) == (3800, 3650, first, G.GetServerTime()), dict(f[529])
assert f[2799].t0 is None, "a faction that didn't rise has no grind"
L.execute("REP[910] = { name = 'Brood of Nozdormu', reaction = 9, currentStanding = 1 }")   # (not a standing)
G.fire("UPDATE_FACTION")
assert f[910] is None, "a standing out of range is left out"

# auction: the browse results of the player's own search (plan items only, the lowest for one)
L.execute("""BROWSE = { { itemKey = { itemID = 13938 }, minPrice = 250000 }, { itemKey = { itemID = 13938 }, minPrice = 240000 },
  { itemKey = { itemID = 5001 }, minPrice = 0 }, { itemKey = { itemID = 9999 }, minPrice = 100 }, { itemKey = {} }, 7 }""")
L.execute('GlideRecorderDB.auction = { [4242] = { copper = 1, t = 1 }, ["Elsewhere|Alliance"] = { [4242] = { copper = 2, '
          't = 1 } } }')
G.fire("AUCTION_HOUSE_BROWSE_RESULTS_UPDATED")
# regression: the prices of this character's auction house only (its realm and side), never one account-wide book
assert sorted(rdb.auction.keys()) == ["Elsewhere|Alliance", "TestRealm|Horde"], list(rdb.auction.keys())
assert rdb.auction["Elsewhere|Alliance"][4242].copper == 2, "another house's book changed"
book = rdb.auction["TestRealm|Horde"]
assert sorted(book.keys()) == [STYLUS], list(book.keys())
assert (book[STYLUS].copper, book[STYLUS].t) == (240000, G.GetServerTime()), dict(book[STYLUS])
# an item's own auctions (bid-only ones have no buyout): the lowest buyout
L.execute("""ITEM_RESULTS[5001] = { { buyoutAmount = 90000 }, { bidAmount = 1000 }, { buyoutAmount = 85000 } }""")
G.fire("ITEM_SEARCH_RESULTS_UPDATED", L.eval("{ itemID = 5001 }"))
assert book[CROWN].copper == 85000, dict(book[CROWN])
# a commodity on the plan's "ah" list (a reagent), per one
L.execute("COMMODITY[12360] = { { unitPrice = 1200000 } }")
G.fire("COMMODITY_SEARCH_RESULTS_UPDATED", ARCANITE)
assert book[ARCANITE] is None, "not on the plan's list"
me.ah = L.eval("{ %d }" % ARCANITE)
G.fire("COMMODITY_SEARCH_RESULTS_UPDATED", ARCANITE)
assert book[ARCANITE].copper == 1200000, "on the plan's list"
# another character's plan, or none: nothing kept
G.NAME = "Dragon"
L.execute("BROWSE = { { itemKey = { itemID = 13938 }, minPrice = 1 } }")
G.fire("AUCTION_HOUSE_BROWSE_RESULTS_ADDED")
assert book[STYLUS].copper == 240000, "Dragon has no plan"
G.NAME = "Nine"
G.fire("AUCTION_HOUSE_BROWSE_RESULTS_ADDED")
assert book[STYLUS].copper == 1, "a newer price replaces the old one"
# the newest 300 kept
L.execute('for i = 1, 310 do GlideRecorderDB.auction["TestRealm|Horde"][100000 + i] = { copper = 5, t = i } end')
G.fire("AUCTION_HOUSE_BROWSE_RESULTS_UPDATED")
n = len(list(book.keys()))
assert n == 300 and book[STYLUS] is not None and book[100001] is None and book[100310] is not None, n
assert G.SEARCHED is None, "Glide never searches the auction house by itself"
assert ns.errors == 0, ns.lastError

# ---- a /reload: the same move isn't said again, nor pinned again --------------------------------------------------
L, ns, G = boot(live_text(move()), G.carry())
assert said(L) == [] and G.PIN_CALLS == 0 and pin(G) == (1422, 0.69, 0.73), "the same move: quiet"

# a new move: said, and Glide's own earlier pin (still super-tracked) replaced
L, ns, G = boot(live_text(move(ALTS[0], 1455, 0.53, 0.66)), G.carry())
assert said(L) == ["Glide next: " + ALTS[0] + " (pinned on your map)"], "Glide's own pin is replaced"
assert pin(G) == (1455, 0.53, 0.66) and G.TRACKED == "pin"
keep = G.carry()

# ---- no pin by itself: in combat, something else super-tracked or pinned, RestedXP's arrow, pins not "free" ---------
L, ns, G = boot(live_text(move("Stratholme: Magister's Crown", 1423, 0.43, 0.19)), keep + "\nCOMBAT = true")
assert said(L) == [] and G.PIN_CALLS == 0, "in combat: neither said nor pinned"
G.fire("PLAYER_REGEN_ENABLED")                                 # (a fight's end while another goes on)
assert said(L) == [] and G.PIN_CALLS == 0, "still in combat"
G.COMBAT = False
G.fire("PLAYER_REGEN_ENABLED")
assert said(L) == ["Glide next: Stratholme: Magister's Crown (pinned on your map)"] and G.PIN_CALLS == 1
G.fire("PLAYER_REGEN_ENABLED")
assert said(L) == [] and G.PIN_CALLS == 1, "once"

L, ns, G = boot(live_text(move("Stratholme: Magister's Crown", 1423, 0.43, 0.19)), keep + '\nTRACKED = "quest"')
assert said(L) == ["Glide next: Stratholme: Magister's Crown"] and G.PIN_CALLS == 0, "a quest super-tracked"
G.COMBAT = True
G.SlashCmdList.GLIDE("next pin")
assert said(L) == ["Glide in combat: /glide next pin again once the fight is over"] and G.PIN_CALLS == 0
G.COMBAT = False
G.SlashCmdList.GLIDE("next pin")
assert said(L) == ["Glide pinned on your map: Stratholme: Magister's Crown"] and pin(G) == (1423, 0.43, 0.19), \
    "on request, it replaces what was tracked"
assert G.TRACKED == "pin"

L, ns, G = boot(live_text(move("Stratholme: Magister's Crown", 1423, 0.43, 0.19)),
                keep + "\nPIN = point(1429, 0.5, 0.5)\nTRACKED = nil")
assert said(L) == ["Glide next: Stratholme: Magister's Crown"] and G.PIN_CALLS == 0, "another addon's pin on the map"
assert pin(G) == (1429, 0.5, 0.5)

arrow = "\nRXP.arrowFrame = { element = { x = 42.1, y = 23.5, zone = 1422 } }"
L, ns, G = boot(live_text(move("Stratholme: Magister's Crown", 1423, 0.43, 0.19)), keep + arrow)
assert said(L) == ["Glide next: Stratholme: Magister's Crown"] and G.PIN_CALLS == 0, "RestedXP's arrow points"
done = "\nRXP.arrowFrame = { element = { x = 42.1, y = 23.5, step = { active = false } } }"
L, ns, G = boot(live_text(move("Stratholme: Magister's Crown", 1423, 0.43, 0.19)), keep + done)
assert G.PIN_CALLS == 1, "an arrow on a step that is over points nowhere"

L, ns, G = boot(live_text(move("Stratholme: Magister's Crown", 1423, 0.43, 0.19)),
                keep + '\nGlideLinkDB.pursuit.pin = "command"')
assert said(L) == ["Glide next: Stratholme: Magister's Crown"] and G.PIN_CALLS == 0, "pins on command only"
G.SlashCmdList.GLIDE("next pin")
assert said(L) == ["Glide pinned on your map: Stratholme: Magister's Crown"] and G.PIN_CALLS == 1

# the app's setting: the more careful of the two wins
L, ns, G = boot(live_text(move("Stratholme: Magister's Crown", 1423, 0.43, 0.19), pursuit={"pin": "command"}), keep)
assert said(L) == ["Glide next: Stratholme: Magister's Crown"] and G.PIN_CALLS == 0, "the app says on command"
assert tuple(ns.PinMode()) == ("command", True)
L, ns, G = boot(live_text(move("Stratholme: Magister's Crown", 1423, 0.43, 0.19), pursuit={"pin": "off"}), keep)
assert G.PIN_CALLS == 0, "the app says off"
G.SlashCmdList.GLIDE("next pin")
assert said(L)[-1] == "Glide map pins are off in the Glide app's settings" and G.PIN_CALLS == 0
L, ns, G = boot(live_text(move("Stratholme: Magister's Crown", 1423, 0.43, 0.19), pursuit={"pin": "junk"}), keep)
assert G.PIN_CALLS == 1, "a setting the addon doesn't know: the game's own"
assert said(L) == ["Glide next: Stratholme: Magister's Crown (pinned on your map)"]

# ---- /glide next pin off|command|free ------------------------------------------------------------------------------
G.SlashCmdList.GLIDE("next pin off")
assert said(L) == ["Glide map pins off"] and G.GlideLinkDB.pursuit.pin == "off"
assert G.PIN is None, "Glide's own pin taken off the map"
G.SlashCmdList.GLIDE("next pin")
assert said(L) == ["Glide map pins are off: /glide next pin command (or free) turns them on"] and G.PIN is None
G.SlashCmdList.GLIDE("next")
assert said(L)[0] == "Glide next: Stratholme: Magister's Crown", "no pin hint with pins off"
G.SlashCmdList.GLIDE("next pin manual")
assert said(L) == ["Glide map pins only on /glide next pin"] and G.GlideLinkDB.pursuit.pin == "command"
G.SlashCmdList.GLIDE("next pin auto")
assert said(L)[0].startswith("Glide map pins free: by themselves") and G.GlideLinkDB.pursuit.pin == "free"
G.SlashCmdList.GLIDE("next pin sideways")
assert any("/glide next [pin]" in s for s in said(L)) and G.GlideLinkDB.pursuit.pin == "free", "unknown: the help"
keep = G.carry()

# ---- a pin the game refuses: pins off until the next /reload -------------------------------------------------------
L, ns, G = boot(live_text(move(SCHOLO)), keep + "\nPIN = nil\nTRACKED = nil\nPIN_FAIL = true")
assert said(L) == ["Glide next: " + SCHOLO] and G.PIN_CALLS == 1 and G.PIN is None, "tried once, refused"
assert ns.errors == 1 and "map pin" in ns.lastError and "blocked" in ns.lastError, ns.lastError
G.PIN_FAIL = False
G.SlashCmdList.GLIDE("next pin")
assert said(L) == ["Glide map pins are off until you /reload: the game refused the last one"] and G.PIN_CALLS == 1
G.SlashCmdList.GLIDE("next")
assert said(L)[0] == "Glide next: " + SCHOLO, "no pin hint while pins are off"
L, ns, G = boot(live_text(move(SCHOLO)), G.carry())
G.SlashCmdList.GLIDE("next pin")
assert said(L) == ["Glide pinned on your map: " + SCHOLO] and pin(G) == (1422, 0.69, 0.73), "on again after a /reload"
# refused on request: said, and off for the session
L, ns, G = boot(live_text(move(SCHOLO)), G.carry() + "\nPIN_FAIL = true")
G.SlashCmdList.GLIDE("next pin")
assert said(L) == ["Glide the game refused the pin, so Glide won't place one again until you /reload"]
G.SlashCmdList.GLIDE("next pin")
assert G.PIN_CALLS == 1 and "until you /reload" in said(L)[0]
# a pin placed that the game won't super-track: pins off for the session, the pin still Glide's (the next move's
# replaces it, after a /reload)
L, ns, G = boot(live_text(move("Stratholme: Magister's Crown", 1423, 0.43, 0.19)), G.carry() + "\nTRACK_FAIL = true")
assert said(L) == ["Glide next: Stratholme: Magister's Crown"] and pin(G) == (1423, 0.43, 0.19), pin(G)
assert ns.errors == 1 and "tracking blocked" in ns.lastError and G.GlideLinkDB.pursuit.placed.m == 1423
G.SlashCmdList.GLIDE("next pin")
assert said(L) == ["Glide map pins are off until you /reload: the game refused the last one"] and G.PIN_CALLS == 1
L, ns, G = boot(live_text(move(ALTS[0], 1455, 0.53, 0.66)), G.carry() + "\nTRACKED = nil")
assert said(L) == ["Glide next: " + ALTS[0] + " (pinned on your map)"] and pin(G) == (1455, 0.53, 0.66), pin(G)
assert G.TRACKED == "pin" and ns.errors == 0, ns.lastError

# a map the game takes no pin on: said, pins stay on
L, ns, G = boot(live_text(move("Dire Maul: Tome", 1444, 0.6, 0.3)), G.carry() + "\nNO_PIN_MAP[1444] = true")
assert said(L) == ["Glide next: Dire Maul: Tome"] and G.PIN_CALLS == 0
G.SlashCmdList.GLIDE("next pin")
assert said(L) == ["Glide the game can't put a pin on that map"]
L.execute("NO_PIN_MAP[1444] = nil")
G.SlashCmdList.GLIDE("next pin")
assert said(L) == ["Glide pinned on your map: Dire Maul: Tome"], "pins still on"

# a client without pins
L, ns, G = boot(live_text(move("Dire Maul: Tome", 1444, 0.6, 0.3)), G.carry() + "\nUiMapPoint = nil")
G.SlashCmdList.GLIDE("next pin")
assert said(L) == ["Glide this client has no map pins"] and ns.errors == 0

# ---- moves without a place, no move, no Live.lua; what chat can't show -----------------------------------------
L, ns, G = boot(live_text({"t": "Theramore rep: turn in writs", "alt": []}), G.carry())
assert said(L) == ["Glide next: Theramore rep: turn in writs"] and G.PIN_CALLS == 0, "no place: said, not pinned"
G.SlashCmdList.GLIDE("next")
assert said(L) == ["Glide next: Theramore rep: turn in writs"], "no alternatives, no pin hint"
G.SlashCmdList.GLIDE("next pin")
assert said(L) == ["Glide that move has no place on the map to pin"]
for bad in ({"t": "x", "m": 1422, "x": 1.5, "y": 0.2}, {"t": "x", "m": -3, "x": 0.5, "y": 0.2},
            {"t": "x", "m": 1422.5, "x": 0.5, "y": 0.2}):
    L, ns, G = boot(live_text(bad), G.carry())
    assert G.PIN_CALLS == 0, bad
L, ns, G = boot(live_text({"t": "Scholo |cffff0000red|r\nnow", "alt": ["A || B", 12, ""]}), G.carry())
assert said(L) == ["Glide next: Scholo ||cffff0000red||r now"], "a | as itself, no line break"
G.SlashCmdList.GLIDE("next")
assert said(L)[1:] == ["  or: A || B"], "broken alternatives left out"

L, ns, G = boot(live_text({"alt": ALTS}), G.carry())
assert said(L) == [], "a move without text: nothing"
G.SlashCmdList.GLIDE("next")
assert said(L) == ["Glide no next move for this character yet: level 60 and a few minutes of the Glide app running; "
                   "then /reload"]
L, ns, G = boot(None, G.carry())
ns.live = None
G.SlashCmdList.GLIDE("next")
assert said(L) == ["Glide Glide's item facts aren't loaded: restart WoW once"]
G.SlashCmdList.GLIDE("next pin")
assert said(L) == ["Glide Glide's item facts aren't loaded: restart WoW once"]
L, ns, G = boot(live_text(move()), G.carry() + '\nNAME = "Dragon"')
assert said(L) == [] and G.PIN_CALLS == 0, "another character's move"
assert ns.errors == 0, ns.lastError

# ---- the classic API: GetFactionInfoByID, the classic auction list -------------------------------------------------
CLASSIC = r"""
C_Reputation, C_AuctionHouse = nil, nil
FACTIONS = { [576] = { "Timbermaw Hold", "", 3, -6000, -3000, -4200 } }
function GetFactionInfoByID(id) local f = FACTIONS[id]; if f then return unpack(f) end end
AUCTIONS = { { "Bonecreeper Stylus", 0, 1, 4, true, 60, "", 1000, 100, 300000, 0, false, nil, "x", nil, 0, 13938 },
             { "Bonecreeper Stylus", 0, 2, 4, true, 60, "", 1000, 100, 500000, 0, false, nil, "x", nil, 0, 13938 },
             { "Sorcerer's Crown", 0, 1, 4, true, 60, "", 1000, 100, 0, 0, false, nil, "x", nil, 0, 5001 },
             { "Linen Cloth", 0, 20, 1, true, 1, "", 10, 1, 400, 0, false, nil, "x", nil, 0, 2589 } }
function GetNumAuctionItems() return #AUCTIONS, #AUCTIONS end
function GetAuctionItemInfo(_, i) return unpack(AUCTIONS[i], 1, 17) end
function QueryAuctionItems() SEARCHED = true end
"""
L, ns, G = boot(live_text(move()), CLASSIC)
s = G.GlideRecorderDB.sessions[G.GlideRecorderDB.lastSession]
assert (s.factions[576].standing, s.factions[576].value) == (3, -4200), "the classic call, at login"
G.fire("AUCTION_ITEM_LIST_UPDATE")
book = G.GlideRecorderDB.auction["TestRealm|Horde"]
assert sorted(book.keys()) == [STYLUS] and book[STYLUS].copper == 250000, "per one: the stack of two at 500000"
assert G.SEARCHED is None and ns.errors == 0, ns.lastError

# ---- professions on a client with no skill lines: from the rank spells it knows (recorder_data reads them) ----------
L, ns, G = boot(live_text(move()), "KNOWN[3909] = true; KNOWN[7411] = true; KNOWN[13920] = true; KNOWN[7620] = true")
s = G.GlideRecorderDB.sessions[G.GlideRecorderDB.lastSession]
lines = [dict(ln) for ln in s.skills.lines.values()]
prof = {ln["n"]: (ln["mx"], ln["h"]) for ln in lines if ln["h"] in ("Professions", "Secondary Skills")}
assert prof == {"Tailoring": (150, "Professions"), "Enchanting": (300, "Professions"),
                "Fishing": (75, "Secondary Skills")}, prof
assert {"Swords", "Cloth"} <= {ln["n"] for ln in lines} and s.skills["from"] == "spells", lines
assert ns.errors == 0, ns.lastError
import recorder_data  # noqa: E402
got = set()
for ln in lines:
    if ln["n"] in recorder_data.PROFESSIONS or ln.get("h") in recorder_data.PROFESSION_HEADERS:
        got.add(ln["n"])
assert got == {"Tailoring", "Enchanting", "Fishing"}, got
# none known: the weapon and armor skills still say the character has no profession (not unknown)
L, ns, G = boot(live_text(move()))
s = G.GlideRecorderDB.sessions[G.GlideRecorderDB.lastSession]
lines = [dict(ln) for ln in s.skills.lines.values()]
assert lines and not [ln for ln in lines if ln["h"] in ("Professions", "Secondary Skills")], lines
# a spell lookup that tells nothing (no weapon or armor skill known): no lines, the professions stay unknown
L, ns, G = boot(live_text(move()), "KNOWN = {}")
s = G.GlideRecorderDB.sessions[G.GlideRecorderDB.lastSession]
assert s.skills is None or s.skills.lines is None, dict(s.skills)

# a client with neither: nothing, no error
L, ns, G = boot(live_text(move()), "C_Reputation, C_AuctionHouse = nil, nil")
s = G.GlideRecorderDB.sessions[G.GlideRecorderDB.lastSession]
G.fire("UPDATE_FACTION")
assert s.factions is None and G.GlideRecorderDB.auction is None and ns.errors == 0, ns.lastError
print("OK")
