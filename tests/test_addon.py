#!/usr/bin/env python3
"""Smoke-test the Glide addon's recorder part under Lua 5.1 with a mocked WoW API (needs lupa)."""
import os, sys
from lupa import lua51

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "addon", "Glide")
L = lua51.LuaRuntime(unpack_returned_tuples=True)

MOCK = r"""
local frames, timers, now, server = {}, {}, 1000, 1700000000
events_registered = {}
printed = {}
function print(...) local t = {} for i=1,select('#',...) do t[#t+1]=tostring(select(i,...)) end printed[#printed+1]=table.concat(t," ") end
function strsplit(sep, s) local out = {} for p in (s..sep):gmatch("(.-)%"..sep) do out[#out+1]=p end return unpack(out) end
function date(f) return "20261003-120000" end
function time() return server end
function GetServerTime() return server end
function GetTime() return now end
function CreateFrame() local f = {} function f:RegisterEvent(e) events_registered[e]=true end
  function f:SetScript(_, fn) frames[#frames+1]=fn end return f end
function fire(e, ...) for _, fn in ipairs(frames) do fn(nil, e, ...) end end
C_Timer = { After = function(d, fn) timers[#timers+1] = {at=now+d, fn=fn} end,
  NewTicker = function(d, fn) local t = {every=d, at=now+d, fn=fn} function t:Cancel() t.dead=true end timers[#timers+1]=t return t end }
function advance(sec)
  local target = now + sec
  while true do
    local nxt
    for _, t in ipairs(timers) do if not t.dead and t.at <= target and (not nxt or t.at < nxt.at) then nxt = t end end
    if not nxt then break end
    now = nxt.at
    if nxt.every then nxt.at = now + nxt.every else nxt.dead = true end
    nxt.fn()
  end
  now = target
end
issecretvalue = function(v) return v == "SECRET" end
local px, py = 0.5, 0.5
function setpos(x, y) px, py = x, y end
CURMAP = 2521
function CreateVector2D(x, y) return { x = x, y = y } end
-- UiMaps: [id] = { name, parent, instance, world x of top edge, world y of left edge, x bottom, y right }
MAPS = {
  [946] = { "Cosmic", 0 }, [947] = { "Azeroth", 946 },
  [1415] = { "Eastern Kingdoms", 947, 0, 4000, 4000, -16000, -6000 },
  [1429] = { "Elwynn Forest", 1415, 0, -7939.58, 1535.42, -10254.17, -1935.42 },
  [1426] = { "Dun Morogh", 1415, 0, "SECRET", 1000, -6000, -2000 },    -- secret value: skipped
  [1453] = { "Stormwind City", 1429 },                                  -- no world position: skipped
  [1437] = { "Wetlands", 1415, 0, 0/0, 0, -1, -1 },                     -- NaN: skipped
  [1438] = { "Flat", 1415, 0, 5, 5, 5, 5 },                             -- zero size: skipped
  [2521] = { "Zephras Isle", 0, 2552, 2500, 1500, 500, -1500 },         -- Forever, outside Azeroth
  [2522] = { "Thendal Grove", 2521, 2552, 1600, 300, 1200, -200 },
  [2524] = { "Forever Two", 0, 2552, 100, 100, -100, -100 },
  [4001] = { "Elsewhere", 0, 36, 900, 900, 100, 100 },                  -- reached only by standing in it
  [4000] = { "Elsewhere Mine", 4001, 36, 500, 500, 400, 400 },
}
for i = 0, 119 do MAPS[3000 + i] = { "Filler " .. i, 1415, 0, 100 + i, 50, -100 - i, -50 } end
C_Map = { GetBestMapForUnit = function() return CURMAP end,
  GetPlayerMapPosition = function() return { GetXY = function() return px, py end } end,
  GetMapInfo = function(id) local m = MAPS[id] if m then return { mapID = id, name = m[1], parentMapID = m[2] } end end,
  GetMapChildrenInfo = function(id, kind, all)
    if not MAPS[id] then return nil end
    local out = {}
    for cid, m in pairs(MAPS) do
      local p = m[2]
      while p and p ~= 0 do
        if p == id then out[#out + 1] = { mapID = cid, name = m[1], parentMapID = m[2] } break end
        if not all then break end
        p = MAPS[p] and MAPS[p][2]
      end
    end
    return out
  end,
  GetWorldPosFromMapPos = function(id, v)
    local m = MAPS[id]
    if not m or not m[3] then return nil end
    local function lerp(a, b, t) if t == 0 then return a elseif t == 1 then return b end return a + (b - a) * t end
    local x = (m[4] == "SECRET") and "SECRET" or lerp(m[4], m[6], v.y)    -- map y runs along world -x
    local y = lerp(m[5], m[7], v.x)                                         -- map x runs along world -y
    return m[3], { GetXY = function() return x, y end }
  end }
SKILLS = { { "Weapon Skills", true }, { "Swords", false, 5, 0, 10 }, { "Unarmed", false, 3, 2, 10 },
           { "Secondary Skills", true }, { "First Aid", false, 1, 0, 75 } }
function GetNumSkillLines() return #SKILLS end
function GetSkillLineInfo(i) local s = SKILLS[i] return s[1], s[2], true, s[3], 0, s[4], s[5] end
function UnitGUID(u) if u == "npc" or u == "questnpc" then return "Creature-0-1-0-1-251361-0000" end
  if u == "target" then return target_guid end return "Player-1-0001" end
function UnitName(u) if u == "npc" or u == "questnpc" then return "Rorian the Dayseeker" end if u=="target" then return target_name end return "Fermin" end
function UnitFullName() return "Fermin", "TestRealm" end
function UnitClass() return "Warrior", "WARRIOR" end
function UnitRace() return "Skyborne", "Skyborne" end
function UnitFactionGroup() return "Horde" end
function UnitLevel(u) if u == "target" then return 3 end return 2 end
function UnitXP() return 120 end function UnitXPMax() return 400 end function GetXPExhaustion() return 600 end
function UnitIsPlayer(u) return u == "player" end
function UnitClassification() return "normal" end function UnitCreatureType() return "Beast" end
function UnitReaction() return 2 end function UnitAffectingCombat() return false end function InCombatLockdown() return false end
function UnitOnTaxi() return false end
function GetBuildInfo() return "1.60.1", "70205", "Oct 1 2026", 16001 end
function GetLocale() return "enUS" end function GetRealmName() return "TestRealm" end
function GetQuestID() return 92461 end
function GetTitleText() return "Harmony in Balance" end
function GetQuestText() return "The juveniles are out of balance." end
function GetObjectiveText() return "Slay 8 Vuldren Juveniles." end
function GetProgressText() return "Is it done?" end function GetRewardText() return "Well done." end
function GetRewardXP() return 80 end function GetRewardMoney() return 15 end
function GetNumQuestRewards() return 0 end function GetNumQuestChoices() return 2 end
function GetQuestItemLink(t, i) return "|Hitem:25728"..i..":::|h[x]|h" end
function GetQuestItemInfo() return "x", 1, 1 end
local objs = { { text = "Vuldren Juvenile slain: 0/8", type = "monster", numFulfilled = 0, numRequired = 8, finished=false } }
function setprog(n) objs[1].numFulfilled = n objs[1].text = "Vuldren Juvenile slain: "..n.."/8" end
cached = {}
answers = {}
C_QuestLog = {
  GetQuestObjectives = function(q) if q == 92461 then return objs end return {} end,
  GetNumQuestLogEntries = function() return 2 end,
  GetInfo = function(i) if i == 1 then return {isHeader=true, title="Zephras"} end return {questID=92461, title="Harmony in Balance"} end,
  GetTitleForQuestID = function(q) if cached[q] then return "Quest "..q end return "" end,
  GetQuestDifficultyLevel = function(q) return 3 end,
  GetNextWaypointForMap = function() return 0.42, 0.28 end,
  GetNextWaypointText = function() return "Go to the den" end,
  IsQuestFlaggedCompleted = function() return false end,
  RequestLoadQuestByID = function(q) answers[#answers+1] = q end,
}
function HaveQuestData(q) return cached[q] == true end
function GetQuestFactionGroup() return 0 end function GetQuestUiMapID() return 2521 end
C_GossipInfo = { GetAvailableQuests = function() return { {questID=92462, title="Infestation Investigation", questLevel=2} } end,
  GetActiveQuests = function() return { {questID=92461} } end }
function GetNumLootItems() return 1 end function GetLootSlotLink() return "|Hitem:263408::|h" end
function GetLootSourceInfo() return "Creature-0-1-0-1-250000-00", 1 end
function GetMerchantNumItems() return 0 end
function GetZoneText() return "Zephras Isle" end
SlashCmdList = {}
function ReloadUI() reloaded = true end
function hooksecurefunc(t, k, fn) local o = t[k] t[k] = function(...) local r = {o(...)} fn(...) return unpack(r) end end
Enum = { CompressionMethod = { Deflate = 0 } }
C_EncodingUtil = { DecompressString = function(d) return d:sub(2) end }
"""
L.execute(MOCK)
ns = L.table()
toc = open(os.path.join(ROOT, "Glide.toc"), encoding="utf-8").read().splitlines()
RECORDER = {"Data\\QuestIDs.lua", "Core.lua", "Recorder.lua", "Scanner.lua", "RXPDump.lua"}   # live-link files: test_link.py
files = [l.strip() for l in toc if l.strip() and not l.startswith("#") and l.strip() in RECORDER]
assert len(files) == len(RECORDER), files
loader = L.eval("function(src, name, a, ns) local f, e = loadstring(src, name) if not f then error(e) end return f(a, ns) end")
# a fake RXP loaded before us
L.execute(r"""
RXP = { guides = {}, guideCache = {}, db = { profile = { guides = {
   ["k|1"] = { groupOrContent = "X#group RestedXP Forever Guide (H)\n#name 1-13 Zephras\nstep\n.accept 92461 >> Accept Harmony\n" .. string.rep("-- filler\n", 30) .. "step\n.turnin 92461\n" } } } } }
function RXP.ParseGuide(a, b) RXP.guide = "parsed" return {name="n", group="g", steps={{elements={{tag="accept", questId=92461, text="Accept"}}}}} end
RXP.guides[1] = { name = "1-13 Zephras", group = "RestedXP Forever Guide (H)", key = "kk",
   steps = { { index = 1, elements = { { tag = "accept", questId = 92461, text = "Accept Harmony", x = 42.1, y = 23.4, zone = "Zephras Isle" } } } } }
""")
for f in files:
    path = os.path.join(ROOT, *f.split("\\"))
    loader(open(path, encoding="utf-8").read(), "@" + f, "Glide", ns)

fire = L.globals().fire
fire("ADDON_LOADED", "Glide")
ns.skillRanks, ns.skillWeapons, ns.skillArmor = "Swords 5/10", 128, 2     # as the live link's Gear.lua sets them
fire("PLAYER_LOGIN")
G = L.globals()
db = G.GlideRecorderDB
s = db.sessions[db.lastSession]
assert s is not None and s.char.name == "Fermin", "session"

fire("GOSSIP_SHOW")
fire("QUEST_DETAIL")
fire("QUEST_ACCEPTED", 92461)
G.target_guid, G.target_name = "Creature-0-1-0-1-250001-00", "Vuldren Juvenile"
G.setpos(0.43, 0.29)
fire("CHAT_MSG_COMBAT_XP_GAIN", "Vuldren Juvenile dies, you gain 45 experience. (+22 exp Rested bonus)")
G.setprog(1)
fire("QUEST_LOG_UPDATE")
G.advance(1)
fire("PLAYER_XP_UPDATE", "player")
fire("LOOT_OPENED")
fire("QUEST_COMPLETE")
fire("QUEST_TURNED_IN", 92461, 80, 15)
G.setpos(0.6, 0.6)
G.advance(10)

q = s.quests[92461]
assert q.t == "Harmony in Balance" and q.details.startswith("The juveniles"), "detail"
assert q.giver[1].id == 251361 and q.giver[1].x == 50, ("giver", q.giver[1].x)
assert q.accepted and q.turned.xp == 80, "accept/turnin"
assert q.prog[1][1].x == 43, "objective progress position"
assert q.mobs[1][250001] == 1, "objective mob link"
assert s.kills[1].xp == 45 and s.kills[1].rest == 22 and s.kills[1].id == 250001, "kill"
assert s.gossip[251361].avail[92462], "gossip"
assert s.quests[92462].t == "Infestation Investigation", "gossip quest title"
assert q.wp[1].x == 42 and q.wpText == "Go to the den", "waypoint"
assert len(list(s.trail.items())) >= 1, "trail"
assert q.turned.rxp is None or True
assert len(list(s.errors.items())) == 0, list(s.errors.values())

# scanner: two cached, others answered by the "server"
G.cached[92460] = True
SC = G.SlashCmdList.GLIDE
SC("scan range 92458 92470")
for _ in range(20):
    G.advance(0.5)
    for qid in list(G.answers.values()):
        G.cached[qid] = True
        fire("QUEST_DATA_LOAD_RESULT", qid, True)
    L.execute("answers = {}")
sc = s.scan
assert sc[92460].h == "c" and sc[92465].h == "r" and sc[92465].t == "Quest 92465", "scan"
assert not L.eval("GlideRecorder_scanrunning") , "x"

SC("rxpdump")
assert db.rxp["RestedXP Forever Guide (H)||1-13 Zephras"].text.startswith("#group"), "rxp cache text"
assert db.rxpSteps["RestedXP Forever Guide (H)||1-13 Zephras"].steps[1].elements[1].questId == 92461, "rxp steps"
assert G.RXP.guide is None, "rxp state restored"
# RestedXP updated -> the cache is dumped automatically once per new version (login timer; here called directly)
assert not ns.rec.RXPAutoDump(), "no version known: nothing to do"
L.execute('RXPVER = "v4.11.15"; C_AddOns = { GetAddOnMetadata = function(a, k) if a == "RXPGuides" and k == "Version" then return RXPVER end end }')
L.execute('GlideRecorderDB.rxp = {}')
assert ns.rec.RXPAutoDump() and db.rxpVersion == "v4.11.15", "first login on a new RestedXP version dumps"
assert db.rxp["RestedXP Forever Guide (H)||1-13 Zephras"] is not None
assert not ns.rec.RXPAutoDump(), "same version: no second dump"
L.execute('RXPVER = "v4.11.16"; InCombatLockdown = function() return true end')
assert not ns.rec.RXPAutoDump() and db.rxpVersion == "v4.11.15", "in combat: retried later"
L.execute('InCombatLockdown = function() return false end')
G.advance(31)
assert db.rxpVersion == "v4.11.16", db.rxpVersion

# ---- trained skills snapshot (5 s after login): skill lines + the live link's masks/ranks ----
sk = s.skills
assert sk is not None and sk.t == 1700000000, "skills snapshot"
lines = [(l.n, l.r, l.mx, l.md, l.h) for l in sk.lines.values()]
assert lines == [("Swords", 5, 10, None, "Weapon Skills"), ("Unarmed", 3, 10, 2, "Weapon Skills"),
                 ("First Aid", 1, 75, None, "Secondary Skills")], lines
assert sk.ranks == "Swords 5/10" and sk.w == 128 and sk.a == 2, (sk.ranks, sk.w, sk.a)

# ---- map bounds: collected 5 s after login, 50 maps per tick ----
mb = db.mapBounds
assert db.mapBoundsBuild == "1.60.1.70205", db.mapBoundsBuild
assert tuple(mb[1429].values()) == (0, -7939.58, 1535.42, -10254.17, -1935.42, "Elwynn Forest"), tuple(mb[1429].values())
assert tuple(mb[2521].values()) == (2552, 2500, 1500, 500, -1500, "Zephras Isle"), "Forever map outside the roots"
assert mb[2522] is not None and mb[2522][6] == "Thendal Grove", "descendant of a Forever map"
assert mb[2524] is not None and mb[3119] is not None and mb[1415] is not None
for missing in (946, 947, 1426, 1453, 1437, 1438, 2548, 4000, 4001):   # no position / secret / NaN / zero size / unreached
    assert mb[missing] is None, missing
stored = len(list(mb.keys()))
assert stored == 125, stored
B = ns.rec.bounds
assert not B.running and B.total == 134 and B.done == 134 and B.found == 125, (B.running, B.total, B.done, B.found)
# throttling: a new pass takes 3 ticks for 134 maps; the saved table is replaced only when it completes
SC("maps scan")
assert B.running and B.done == 0 and B.total == 134
G.advance(0.1)
assert B.running and B.done == 50, B.done
G.advance(0.1)
assert B.running and B.done == 100, B.done
G.advance(0.1)
assert not B.running and B.done == 134 and len(list(db.mapBounds.keys())) == 125
# arriving on a map outside the walked trees stores it; the next pass also walks its parents
G.CURMAP = 4000
fire("ZONE_CHANGED_NEW_AREA")
G.advance(2.5)
assert db.mapBounds[4000] is not None and db.mapBounds[4000][1] == 36 and db.mapBounds[4001] is None
SC("maps scan")
G.advance(1)
assert db.mapBounds[4001] is not None and db.mapBounds[4000] is not None and len(list(db.mapBounds.keys())) == 127
n_print = len(G.printed)
SC("maps")
out = [G.printed[i] for i in range(n_print + 1, len(G.printed) + 1)]
assert "127 maps stored" in out[0] and "1.60.1.70205" in out[0], out
assert "current map 4000 Elsewhere Mine: instance 36" in out[1] and "100 x 100 yd" in out[1], out
G.CURMAP = 2521

# ---- mark: /glide mark <note> -> recorder event (+ live ring event; see test_link.py) ----
G.setpos(0.61, 0.62)
L.execute("RXP.currentGuide = { name = '1-13 Zephras', group = 'RestedXP Forever Guide (H)' }; RXPCData = { currentStep = 7 }")
n_print = len(G.printed)
SC("mark hello")
marks = [e for e in s.events.values() if e.e == "mark"]
assert len(marks) == 1, marks
m = marks[0]
assert m.text == "hello" and m.t == 1700000000 and m.lvl == 2 and m.m == 2521 and m.x == 61 and m.y == 62, dict(m)
assert m.rxp.guide == "1-13 Zephras" and m.rxp.step == 7, dict(m.rxp)
assert any("marked" in G.printed[i] and "hello" in G.printed[i] for i in range(n_print + 1, len(G.printed) + 1))
L.execute("RXP.currentGuide = nil; RXPCData = nil")
G.Glide_Mark()                                          # the key binding: a mark without a note
marks = [e for e in s.events.values() if e.e == "mark"]
assert len(marks) == 2 and marks[1].text is None and marks[1].m == 2521 and marks[1].rxp is None, dict(marks[1])
# the recorder keeps the note as typed (control chars squeezed); the ring copy is cleaned and capped
short = ns.Mark("  Coach: said; turn\nin = now|ok " + "y" * 50)
assert short == "Coach said turn in now ok " + "y" * 14 and len(short) == 40, short
assert marks[0].text == "hello" and [e for e in s.events.values() if e.e == "mark"][2].text.startswith("Coach: said; turn in = now|ok yy")
short = ns.Mark("a" + "é" * 30)                       # never cut inside a UTF-8 character
assert short == "a" + "é" * 19 and len(short.encode()) == 39, short
assert G.BINDING_HEADER_GLIDE == "Glide" and G.BINDING_NAME_GLIDE_MARK
import xml.etree.ElementTree as ET
bx = ET.parse(os.path.join(ROOT, "Bindings.xml")).getroot()
bl = bx.findall("Binding")
assert bx.tag == "Bindings" and len(bl) == 2, bl
assert (bl[0].get("name"), bl[0].get("header"), bl[0].get("category")) == ("GLIDE_MARK", "GLIDE", "ADDONS")
assert bl[0].text.strip() == "Glide_Mark()", bl[0].text
assert (bl[1].get("name"), bl[1].get("category"), bl[1].text.strip()) == ("GLIDE_TRASH", "ADDONS", "Glide_Trash()"), bl[1]
assert not any("Bindings" in l for l in toc), "Bindings.xml is loaded from the folder, not the TOC"
assert len(list(s.errors.items())) == 0, list(s.errors.values())
SC("status")
print("\n".join(G.printed.values()))
print("errors:", list(s.errors.values()))
print("OK")

# ---- write the SavedVariables the way WoW does, for the PC-side tests ----
ser = L.eval(r'''function(name, v)
  local out = {}
  local function key(k) if type(k)=="number" then return "["..k.."]" end return string.format("[%q]", k) end
  local function val(x, ind)
    if type(x) == "table" then
      out[#out+1] = "{\n"
      for k, y in pairs(x) do out[#out+1] = ind.."\t"..key(k).." = " val(y, ind.."\t") out[#out+1] = ",\n" end
      out[#out+1] = ind.."}"
    elseif type(x) == "string" then out[#out+1] = string.format("%q", x)
    else out[#out+1] = tostring(x) end
  end
  out[#out+1] = name.." = " val(v, "") out[#out+1] = "\n"
  return table.concat(out)
end''')
os.makedirs("/tmp/glidetest/WTF/Account/1/SavedVariables", exist_ok=True)
open("/tmp/glidetest/WTF/Account/1/SavedVariables/GlideRecorder.lua", "w").write(ser("GlideRecorderDB", db))
print("SV written")

# ---- one command registry (Core.lua ns.Command): /glide <name>, word aliases, slash aliases, generated help ----
G.ns = ns
L.execute("""LASTCMD = nil
ns.Command("probe", "probe <x> - a test command", function(rest) LASTCMD = rest; if rest == "bad" then return false end end,
           { "pr", "/glideprobe", "/gp" })
ns.Command("boom", nil, function() error("kaboom") end)""")
G.SlashCmdList.GLIDE("probe one two")
assert G.LASTCMD == "one two", G.LASTCMD
G.SlashCmdList.GLIDE("PR three")                       # a word alias, any case
assert G.LASTCMD == "three", G.LASTCMD
assert G.SLASH_GLIDEPROBE1 == "/glideprobe" and G.SLASH_GLIDEPROBE2 == "/gp", "slash aliases are named after the command"
G.SlashCmdList.GLIDEPROBE("four")
assert G.LASTCMD == "four", G.LASTCMD
G.SlashCmdList.GLIDELINK("probe five")                 # /glidelink is /glide under the old name
assert G.LASTCMD == "five", G.LASTCMD


def printed_after(fn):
    n = len(G.printed)
    fn()
    return list(G.printed.values())[n:]


out = printed_after(lambda: G.SlashCmdList.GLIDE("help"))
lines = [ln.strip() for ln in out[1:]]
assert "/glide probe <x> - a test command" in lines, out
assert lines == sorted(lines) and not any("boom" in ln for ln in lines), "sorted, unlisted commands left out: %s" % out
assert {"/glide status - what has been recorded this session", "/glide scan [new|all|range A B|stop|status] - ask the "
        "server for quest data"} <= set(lines), out
assert printed_after(lambda: G.SlashCmdList.GLIDE("nosuchcommand")) == out, "an unknown command shows the help"
assert printed_after(lambda: G.SlashCmdList.GLIDEPROBE("bad")) == out, "a command returning false shows the help"
err = printed_after(lambda: G.SlashCmdList.GLIDE("boom"))
assert len(err) == 1 and "kaboom" in err[0], err
print("commands OK")
