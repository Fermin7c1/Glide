-- Glide core, loaded first: the addon's two namespaces, the helpers both halves share (secret-value safety, chat
-- output, saved settings and their change notice, event buses), the recorder's session and the slash commands.
--   ns      the addon table: the live link (strip, pages, coach switches, helpers, the Glide window)
--   ns.rec  the recorder: what it saves in GlideRecorderDB
local ADDON, ns = ...
ns.rec = ns.rec or {}
local rec = ns.rec           -- the recorder's namespace (kept apart from the live-link code)

local getMeta = (C_AddOns and C_AddOns.GetAddOnMetadata) or _G.GetAddOnMetadata
-- the TOC's ## Version: the strip sends it (gv), so the companion can tell when the game runs an old copy
ns.VERSION = (getMeta and getMeta(ADDON, "Version")) or "?"
rec.VERSION = ns.VERSION
rec.SCHEMA = 1
local KEEP_SESSIONS = 12          -- older sessions are pruned (the PC-side archiver keeps copies)

-- ---------------------------------------------------------------------------------------------
-- secret-value safety: Forever uses Midnight's restrictions; never compute on protected values
-- ---------------------------------------------------------------------------------------------
local issecret = _G.issecretvalue or function() return false end
function ns.Safe(v)
    if v == nil or issecret(v) then return nil end
    return v
end
function ns.IsSecret(v) return v ~= nil and issecret(v) end
rec.Safe, rec.IsSecret = ns.Safe, ns.IsSecret

-- ---------------------------------------------------------------------------------------------
-- chat output
-- ---------------------------------------------------------------------------------------------
function ns.Print(...) print("|cff66ccffGlide|r", ...) end
rec.Print = ns.Print

-- "on" in green, "off" in red
function ns.OnOff(on) return on and "|cff55dd55on|r" or "|cffff5555off|r" end

-- copper as the game writes money (with the coin icons)
function ns.Money(copper)
    if GetCoinTextureString then return GetCoinTextureString(copper) end
    return ("%dc"):format(copper)
end

-- ---------------------------------------------------------------------------------------------
-- GlideLinkDB, the live link's saved settings: the strip's own keys at the top (Strip.lua), and one section per
-- module (coach, minimap, panel, repair, trash, fp). A missing or damaged section starts from the module's defaults.
-- ---------------------------------------------------------------------------------------------
function ns.LinkDB()
    if type(GlideLinkDB) ~= "table" then GlideLinkDB = {} end
    return GlideLinkDB
end

function ns.LinkSection(key, defaults)
    local db = ns.LinkDB()
    if type(db[key]) ~= "table" then
        local t = {}
        for k, v in pairs(defaults or {}) do t[k] = v end
        db[key] = t
    end
    return db[key]
end

-- ---------------------------------------------------------------------------------------------
-- game state both halves read: the same API calls, guarded the same way
-- ---------------------------------------------------------------------------------------------
ns.HEARTHSTONE_SPELL, ns.HEARTHSTONE_ITEM = 8690, 6948

-- the player's map and position on it (0..1); x and y are nil when the game won't say (instances, secret values)
function ns.PlayerMapPosition()
    local map = ns.Safe(C_Map.GetBestMapForUnit("player"))
    if not map then return nil end
    local p = C_Map.GetPlayerMapPosition(map, "player")
    if not p then return map end
    local x, y = p:GetXY()
    return map, ns.Safe(x), ns.Safe(y)
end

-- for qid, info in ns.QuestLogQuests(): the quests in the log (headers skipped)
function ns.QuestLogQuests()
    local i, n = 0, C_QuestLog.GetNumQuestLogEntries()
    return function()
        while i < n do
            i = i + 1
            local info = C_QuestLog.GetInfo(i)
            local qid = info and not info.isHeader and ns.Safe(info.questID)
            if qid then return qid, info end
        end
    end
end

-- for bag, slot, info in ns.BagItems(): every filled slot of the backpack and the four bags
function ns.BagItems()
    local bag, slot, slots = 0, 0, C_Container.GetContainerNumSlots(0) or 0
    return function()
        while true do
            if slot < slots then
                slot = slot + 1
                local info = C_Container.GetContainerItemInfo(bag, slot)
                if info then return bag, slot, info end
            elseif bag < 4 then
                bag, slot = bag + 1, 0
                slots = C_Container.GetContainerNumSlots(bag) or 0
            else
                return nil
            end
        end
    end
end

-- does the character know this spell? Any of the client's spellbook APIs saying so is enough
local function says(api, id)
    if not api then return false end
    local ok, known = pcall(api, id)
    return ok and ns.Safe(known) and true or false
end
function ns.KnowsSpell(id)
    return says(C_SpellBook and C_SpellBook.IsSpellKnown, id) or says(_G.IsPlayerSpell, id)
        or says(_G.IsSpellKnown, id)
end

-- RestedXP (optional dependency): its running guide and the current step's index; nil without a guide
function ns.RXPStep()
    local R = _G.RXP
    if not R or not R.currentGuide then return nil end
    return tonumber(RXPCData and RXPCData.currentStep), R.currentGuide
end

-- RestedXP's settings (RXP.settings.profile), or nil
function ns.RXPProfile()
    local R = _G.RXP
    local settings = type(R) == "table" and R.settings
    return type(settings) == "table" and type(settings.profile) == "table" and settings.profile or nil
end

-- ---------------------------------------------------------------------------------------------
-- settings change notice
-- ---------------------------------------------------------------------------------------------
-- Views that show settings (the minimap button, the Glide window) register here; every setter calls
-- ns.OnSettingsChanged() so they all stay current, however the setting was changed (window, slash command).
local settingsListeners = {}
function ns.OnSettingsChange(fn) settingsListeners[#settingsListeners + 1] = fn end
function ns.OnSettingsChanged()
    for _, fn in ipairs(settingsListeners) do pcall(fn) end
end

-- ---------------------------------------------------------------------------------------------
-- event buses: one frame per bus, every handler run protected so one failing handler never stops the others
-- ---------------------------------------------------------------------------------------------
-- -> on(event, fn): fn(...) runs on each event with the event's arguments; failures go to onError(text)
local function eventBus(onError)
    local frame, handlers = CreateFrame("Frame"), {}
    frame:SetScript("OnEvent", function(_, event, ...)
        local list = handlers[event]
        if not list then return end
        for i = 1, #list do
            local ok, err = pcall(list[i], ...)
            if not ok then onError(event .. ": " .. tostring(err)) end
        end
    end)
    return function(event, fn)
        if not handlers[event] then
            handlers[event] = {}
            if not pcall(frame.RegisterEvent, frame, event) then
                onError("event not available: " .. event)
            end
        end
        table.insert(handlers[event], fn)
    end, frame
end

-- One error policy for the whole addon (recorder and live link): every error is counted (ns.errors) and the last one
-- kept (ns.lastError); the strip sends both (core er, page E) so the companion's health page shows them, the session
-- keeps the first 50 for the PC side, and /glide debug prints each as it happens. Handlers still fail quietly, so
-- the strip keeps streaming.
ns.errors = 0
function ns.ReportError(err)
    err = tostring(err)
    ns.errors, ns.lastError = ns.errors + 1, err
    if rec.session then
        local e = rec.session.errors
        if #e < 50 then e[#e + 1] = err end
    end
    if rec.debug then print("|cffff5555Glide error:|r", err) end
end
local report = ns.ReportError
rec.Report = report

-- ns.Try(what, fn, ...): fn(...) protected, a failure reported as "<what>: <error>" -> ok, fn's results or the error
local function checked(what, ok, ...)
    if not ok then report(what .. ": " .. tostring((...))) end
    return ok, ...
end
function ns.Try(what, fn, ...) return checked(what, pcall(fn, ...)) end

-- an event this client doesn't have is not an error of the addon's: noted for /glide dump (and debug), not counted
ns.missingEvents = {}
local function busError(err)
    local missing = err:match("^event not available: (.+)$")
    if not missing then return report(err) end
    ns.missingEvents[#ns.missingEvents + 1] = missing
    if rec.debug then print("|cffff5555Glide:|r", err) end
end
rec.On, rec.frame = eventBus(busError)
ns.On = eventBus(busError)

-- ---------------------------------------------------------------------------------------------
-- recorder helpers
-- ---------------------------------------------------------------------------------------------
local function round(v, n)
    local m = 10 ^ (n or 0)
    return math.floor(v * m + 0.5) / m
end
rec.Round = round

function rec.Now() return GetServerTime and GetServerTime() or time() end

-- client version + build, e.g. "1.60.1.70205" (session.build, GlideRecorderDB.mapBoundsBuild)
function rec.BuildString()
    local version, build = GetBuildInfo()
    return tostring(version) .. "." .. tostring(build)
end

-- player position on the best uiMap: {m=uiMapID, x=0..100, y=0..100}; nil in instances/secret
function rec.Pos()
    local map, x, y = ns.PlayerMapPosition()
    if not map then return nil end
    if not x or not y or (x == 0 and y == 0) then return { m = map } end
    return { m = map, x = round(x * 100, 2), y = round(y * 100, 2) }
end

-- "Creature-0-1234-0-56-4711-0000ABCD" -> "npc", 4711
function rec.ParseGUID(guid)
    guid = rec.Safe(guid)
    if type(guid) ~= "string" then return nil end
    local kind, _, _, _, _, id = strsplit("-", guid)
    id = tonumber(id)
    if not id then return nil end
    if kind == "Creature" or kind == "Vehicle" or kind == "Pet" then return "npc", id end
    if kind == "GameObject" then return "object", id end
    if kind == "Item" then return "item", id end
    return nil
end

function rec.UnitEntity(unit)
    local kind, id = rec.ParseGUID(UnitGUID(unit))
    if not kind then return nil end
    return { k = kind, id = id, n = rec.Safe(UnitName(unit)) }
end

function rec.ItemIDFromLink(link)
    link = rec.Safe(link)
    return link and tonumber(link:match("item:(%d+)")) or nil
end

-- append a point to list unless one within `minDist` (map %) on the same map already exists
function rec.AddPoint(list, pos, cap, minDist)
    if not pos or not pos.x then return false end
    minDist = minDist or 0.6
    for i = 1, #list do
        local p = list[i]
        if p.m == pos.m and math.abs(p.x - pos.x) < minDist and math.abs(p.y - pos.y) < minDist then
            p.c = (p.c or 1) + 1
            return false
        end
    end
    if #list >= (cap or 40) then return false end
    list[#list + 1] = { m = pos.m, x = pos.x, y = pos.y }
    return true
end

function rec.Player()
    local lvl = rec.Safe(UnitLevel("player"))
    return {
        lvl = lvl,
        xp = rec.Safe(UnitXP("player")),
        max = rec.Safe(UnitXPMax("player")),
        rest = rec.Safe(GetXPExhaustion()),
    }
end

-- ---------------------------------------------------------------------------------------------
-- saved variables / session
-- ---------------------------------------------------------------------------------------------
local function newSession()
    local name, realm = UnitFullName("player")
    local _, class = UnitClass("player")
    local _, race = UnitRace("player")
    local _, _, _, iface = GetBuildInfo()
    local s = {
        id = date("%Y%m%d-%H%M%S"),
        started = rec.Now(),
        addon = rec.VERSION,
        build = rec.BuildString(),
        iface = iface,
        locale = GetLocale(),
        char = { name = name, realm = realm or GetRealmName(), class = class, race = race,
                 faction = UnitFactionGroup("player"), level = UnitLevel("player") },
        quests = {},     -- [qid] = details/progress/complete text, rewards, xp, timings, positions
        npcs = {},       -- [npcID] = name, level, reaction, points
        objects = {},    -- [objectID] = name, points
        gossip = {},     -- [npcID] = { quest ids offered / active }
        taxi = {},       -- [nodeName] = node info
        trainers = {},   -- [npcID] = services
        vendors = {},    -- [npcID] = items
        loot = {},       -- list of loot events (source -> items)
        kills = {},      -- list of xp-from-kill events
        xp = {},         -- xp timeline
        trail = {},      -- position breadcrumbs
        events = {},     -- level ups, deaths, zone changes, hearth, marks
        scan = {},       -- quest-ID sweep results
        -- skills = {...} trained skills at login (Recorder.lua), when the client reports them
        errors = {},
    }
    return s
end

rec.On("ADDON_LOADED", function(name)
    if name ~= ADDON then return end
    GlideRecorderDB = type(GlideRecorderDB) == "table" and GlideRecorderDB or {}
    local db = GlideRecorderDB
    db.schema = rec.SCHEMA
    db.sessions = db.sessions or {}
    db.rxp = db.rxp or {}
    db.settings = db.settings or { trail = true }
    rec.db = db
end)

-- fn(session) runs once the login's session exists (the recorder modules start their timers there)
rec.OnSessionStart = {}
function rec.AtSessionStart(fn) table.insert(rec.OnSessionStart, fn) end

-- the oldest sessions beyond KEEP_SESSIONS go (session ids sort by time)
local function pruneSessions(db)
    local ids = {}
    for id in pairs(db.sessions) do ids[#ids + 1] = id end
    table.sort(ids)
    for i = 1, #ids - KEEP_SESSIONS do db.sessions[ids[i]] = nil end
end

rec.On("PLAYER_LOGIN", function()
    local db = rec.db
    rec.session = newSession()
    db.sessions[rec.session.id] = rec.session
    pruneSessions(db)
    db.lastSession = rec.session.id
    for _, fn in ipairs(rec.OnSessionStart) do
        local ok, err = pcall(fn, rec.session)
        if not ok then report(err) end
    end
    rec.Print(("recording (%s). /glide opens the Glide window."):format(rec.session.build))
end)

rec.On("PLAYER_LOGOUT", function()
    if rec.session then rec.session.ended = rec.Now() end
end)

-- ---------------------------------------------------------------------------------------------
-- slash commands: one registry, every module registers its own commands (and its diagnostics) here.
--   ns.Command(name, help, fn, aliases)
--     "/glide <name> <rest>" runs fn(rest); fn returning false means it didn't get what it needs: the help shows
--     help     its line in "/glide help", written as typed after "/glide " ("cell <1-8> - ..."); nil: not listed
--     aliases  other names: a word is one more /glide name ("link" for "strip"); a "/word" is a slash command of
--              its own running fn with what follows it ("/coach combat off" is "/glide coach combat off")
-- "/glide" alone opens the Glide window (Panel.lua); "/glide help", or a command it doesn't know, lists them all.
-- "/glidelink <command>" is "/glide <command>" under the live link's old name; "/glidelink on|off" switches the strip.
-- ---------------------------------------------------------------------------------------------
local commands, listed = {}, {}
function ns.Command(name, help, fn, aliases)
    local c = { name = name, help = help, fn = fn }
    commands[name] = c
    if help then listed[#listed + 1] = c end
    local key, n = "GLIDE" .. name:upper(), 0          -- SlashCmdList.GLIDECOACH: SLASH_GLIDECOACH1, 2, ...
    for _, a in ipairs(aliases or {}) do
        if a:sub(1, 1) == "/" then
            n = n + 1
            _G["SLASH_" .. key .. n] = a
            SlashCmdList[key] = function(msg) ns.RunCommand(name, msg) end
        else
            commands[a] = c
        end
    end
end

local function help()
    ns.Print("commands (/glide alone opens the Glide window):")
    table.sort(listed, function(a, b) return a.name < b.name end)
    for _, c in ipairs(listed) do print("  /glide " .. c.help) end
end

-- run a registered command with the text after it (the help when it fails to get what it needs)
function ns.RunCommand(name, rest)
    local c = commands[name]
    if not c then return help() end
    local ok, r = pcall(c.fn, rest or "")
    if not ok then
        ns.Print("|cffff5555error:|r", r)
        ns.ReportError("/glide " .. name .. ": " .. tostring(r))
    elseif r == false then
        help()
    end
end

local function dispatch(msg, alone)
    local cmd, rest = (msg or ""):match("^%s*(%S*)%s*(.-)%s*$")
    cmd = (cmd or ""):lower()
    if commands[cmd] then return ns.RunCommand(commands[cmd].name, rest) end
    if cmd == "" and alone then return alone() end
    help()
end

SLASH_GLIDE1 = "/glide"
SlashCmdList.GLIDE = function(msg)
    dispatch(msg, ns.TogglePanel and function() ns.TogglePanel() end)
end

SLASH_GLIDELINK1 = "/glidelink"
SlashCmdList.GLIDELINK = function(msg)
    local cmd = ((msg or ""):match("^%s*(%S*)") or ""):lower()
    if cmd == "on" or cmd == "off" then return ns.RunCommand("strip", cmd) end
    dispatch(msg)
end

ns.Command("help", nil, help)
ns.Command("status", "status - what has been recorded this session", function()
    local s = rec.session
    if not s then return end
    local function count(t) local n = 0 for _ in pairs(t) do n = n + 1 end return n end
    rec.Print(("session %s: %d quests, %d npcs, %d objects, %d kills, %d xp points, %d trail points, %d scanned, %d errors")
        :format(s.id, count(s.quests), count(s.npcs), count(s.objects), #s.kills, #s.xp, #s.trail,
            count(s.scan), #s.errors))
end)

ns.Command("debug", "debug - toggle printing of internal errors", function()
    rec.debug = not rec.debug
    rec.Print("debug", rec.debug and "on" or "off")
end)

ns.Command("save", "save - write data to disk now (reloads the UI)", function()
    ReloadUI()
end)

-- ---------------------------------------------------------------------------------------------
-- mark: "/glide mark [note]" or the Glide key binding flags this moment for review (the coach said
-- something wrong, the route looks off...). The live link carries it to the companion as ring event
-- "mark" (id:mark:<note>), which saves the game state and the coach's last messages with it; the
-- recorder keeps it as a session event { e = "mark", t, lvl, m, x, y, text, rxp }.
-- The key binding lives in Bindings.xml (Key Bindings > AddOns > Glide); it marks without a note.
-- ---------------------------------------------------------------------------------------------
BINDING_HEADER_GLIDE = "Glide"
BINDING_NAME_GLIDE_MARK = "Mark this moment (flag for review)"
BINDING_NAME_GLIDE_TRASH = "Delete the cheapest RestedXP junk in the bags"

local MARK_RING_MAX = 40       -- the live ring carries at most 40 bytes per event argument
local MARK_TEXT_MAX = 200      -- the recorder keeps a longer note

-- at most n bytes, never ending inside a UTF-8 character
local function cutUTF8(s, n)
    if #s <= n then return s end
    s = s:sub(1, n)
    local i = #s
    while i > 0 and s:byte(i) >= 0x80 and s:byte(i) < 0xC0 do i = i - 1 end     -- continuation bytes
    if i > 0 and s:byte(i) >= 0xC0 then
        local lead = s:byte(i)
        local len = (lead >= 0xF0 and 4) or (lead >= 0xE0 and 3) or 2
        if #s - i + 1 < len then s = s:sub(1, i - 1) end
    end
    return s
end

local function squeeze(s)
    s = s:gsub("%s+", " ")
    return (s:gsub("^ ", ""):gsub(" $", ""))
end

-- returns the note as sent to the companion (cleaned of ;=|: and cut to 40 bytes)
function ns.Mark(text)
    local note = cutUTF8(squeeze((tostring(text or ""):gsub("%c", " "))), MARK_TEXT_MAX)
    local short = cutUTF8(squeeze((note:gsub("[;=|:]", " "))), MARK_RING_MAX)
    if ns.Event then ns.Event("mark", short) end
    if rec.RecordEvent then
        local okR, rxp = pcall(function() return rec.RXPCurrent and rec.RXPCurrent() end)
        rec.RecordEvent("mark", { text = note ~= "" and note or nil, rxp = okR and rxp or nil })
    end
    local stripOff = ns.StripOn and not ns.StripOn()
    rec.Print(("|cffffd200marked|r%s%s"):format(note ~= "" and (": " .. note) or "",
        stripOff and "  (the data strip is off: the companion can't see this mark; the recorder keeps it)" or ""))
    return short
end

function Glide_Mark() ns.Mark("") end                  -- Bindings.xml: GLIDE_MARK

ns.Command("mark", "mark [note] - flag this moment for review (the companion saves what the coach just said)",
    function(rest) ns.Mark(rest) end)
