-- Glide - the next move toward this character's next best-in-slot piece, at level 60. The Glide app works out the move
-- that gets the most DPS for the time it takes (a dungeon boss, a quest, a vendor, a reputation, a craft, the auction
-- house) and writes it into Data/Live.lua, read at login and /reload:
--   ns.live.chars[<Name-Realm or Name>].next = { t = "Scholomance: Bonecreeper Stylus", m = uiMapID, x = 0..1,
--       y = 0..1, alt = { "<the next best move>", "<and the one after>" } }      (m, x, y: where to go, when it has one)
--   ns.live.pursuit.pin = "free" | "command" | "off"                              (the app's setting, when it sends one)
-- "/glide next" says the move and the two next best in chat; "/glide next pin" puts the game's own pin on the map there
-- (C_Map.SetUserWaypoint, super-tracked so the game's arrow points at it). A new move is said once in chat, a few
-- seconds into the world after the login or /reload that read it (once out of combat), and the pin goes down by itself
-- at that moment only when all of these hold: pins are "free" ("/glide next pin free|command|off"; the app's setting
-- too, the more careful of the two wins), nothing is super-tracked and no other pin is on the map (a quest, a corpse,
-- another addon's pin), RestedXP's arrow points nowhere, and you're out of combat. Placing a pin runs protected: one the
-- game refuses turns pins off until the next /reload.
-- GlideLinkDB.pursuit = { pin = "free" | "command" | "off", told = { [Name-Realm] = the move last said },
--                         placed = { m, x, y } (Glide's own pin, so a later move may replace it) }
local _, ns = ...
local Safe, Print = ns.Safe, ns.Print

local STATE_AFTER = 5                        -- seconds into the world before a new move is said (RestedXP's arrow set)
local MODES = { off = 1, command = 2, free = 3 }  -- the more careful, the lower
local WORDS = { free = "free", auto = "free", on = "free", command = "command", manual = "command", off = "off" }
local SAY_MODE = { free = "free: by themselves when nothing else is guiding you, and on /glide next pin",
                   command = "only on /glide next pin", off = "off" }

local function settings() return ns.LinkSection("pursuit", { pin = "free" }) end

--- The pin setting in effect -> mode, and true when it's the Glide app's (more careful than the game's own).
function ns.PinMode()
    local own = settings().pin
    own = MODES[own] and own or "free"
    local live = type(ns.live) == "table" and ns.live.pursuit
    local app = type(live) == "table" and live.pin
    if type(app) == "string" and MODES[app] and MODES[app] < MODES[own] then return app, true end
    return own, false
end
function ns.SetPinMode(mode) settings().pin = mode; ns.OnSettingsChanged() end

-- a text from Live.lua as chat shows it: no control characters, a "|" as itself ("||" is the app's own escaping)
local function clean(s)
    if type(s) ~= "string" then return nil end
    s = s:gsub("%c", " "):gsub("||", "\1"):gsub("|", "||"):gsub("\1", "||")
    s = s:gsub("^%s+", ""):gsub("%s+$", "")
    return s ~= "" and s or nil
end

--- This character's next move: { t, m, x, y, alt = { up to two texts } } (m, x, y nil without a place), or nil.
local function move()
    local ch = ns.LiveChar and ns.LiveChar()
    local n = type(ch) == "table" and ch.next
    if type(n) ~= "table" then return nil end
    local mv = { t = clean(n.t), alt = {} }
    if not mv.t then return nil end
    local m, x, y = tonumber(n.m), tonumber(n.x), tonumber(n.y)
    if m and m > 0 and m == math.floor(m) and x and y and x >= 0 and x <= 1 and y >= 0 and y <= 1 then
        mv.m, mv.x, mv.y = m, x, y
    end
    if type(n.alt) == "table" then
        for i = 1, 2 do mv.alt[#mv.alt + 1] = clean(n.alt[i]) end
    end
    return mv
end
ns.NextMove = move

local function charKey()
    local name = Safe(UnitName("player"))
    if type(name) ~= "string" or name == "" then return nil end
    local realm = GetRealmName and Safe(GetRealmName())
    return type(realm) == "string" and realm ~= "" and (name .. "-" .. realm) or name
end

local function inCombat()
    if InCombatLockdown and Safe(InCombatLockdown()) then return true end
    return Safe(UnitAffectingCombat("player")) and true or false
end

-- RestedXP's arrow points somewhere (the waypoint the strip sends as aw)
local function rxpArrow()
    local R = _G.RXP
    local af = type(R) == "table" and R.arrowFrame
    local el = type(af) == "table" and af.element
    if type(el) ~= "table" or not el.x or not el.y then return false end
    return not (type(el.step) == "table" and el.step.active == false)
end

-- fn(...) protected -> its first result made safe, or nil
local function ask(fn, ...)
    if type(fn) ~= "function" then return nil end
    local ok, v = pcall(fn, ...)
    if ok then return Safe(v) end
end

--- Whether the pin on the map is the one Glide placed (saved, so a later move replaces it after a /reload too).
local function pinIsOurs()
    local at = settings().placed
    if type(at) ~= "table" or not C_Map.GetUserWaypoint then return false end
    local ok, p = pcall(C_Map.GetUserWaypoint)
    if not ok or type(p) ~= "table" or Safe(p.uiMapID) ~= at.m or type(p.position) ~= "table" then return false end
    local okXY, x, y = pcall(function() return p.position:GetXY() end)
    x, y = okXY and Safe(x), okXY and Safe(y)
    return type(x) == "number" and type(y) == "number" and type(at.x) == "number" and type(at.y) == "number"
        and math.abs(x - at.x) < 0.001 and math.abs(y - at.y) < 0.001
end

--- Whether something else is guiding the player: a super-tracked quest, corpse or pin that isn't Glide's, or another
--- pin on the map.
local function othersGuiding()
    local mine = pinIsOurs()
    local st = _G.C_SuperTrack
    if type(st) == "table" then
        local any = ask(st.IsSuperTrackingAnything)
        if any == nil then
            local q = ask(st.GetSuperTrackedQuestID)
            any = (type(q) == "number" and q > 0) or ask(st.IsSuperTrackingUserWaypoint) or ask(st.IsSuperTrackingCorpse)
        end
        if any and not (mine and ask(st.IsSuperTrackingUserWaypoint)) then return true end
    end
    return (ask(C_Map.HasUserWaypoint) and not mine) and true or false
end

local pinsOff = false               -- the game refused a pin this session: none until the next /reload

--- Puts the game's pin on the move's place and super-tracks it -> true, or false and why ("map": the game takes no pin
--- on that map, "api": the client has no pins, "failed": refused, so pins are off for the session).
local function placePin(mv)
    if pinsOff then return false, "failed" end
    local P, st = _G.UiMapPoint, _G.C_SuperTrack
    if not (C_Map.SetUserWaypoint and type(P) == "table" and P.CreateFromCoordinates) then return false, "api" end
    if C_Map.CanSetUserWaypointOnMap and ask(C_Map.CanSetUserWaypointOnMap, mv.m) == false then return false, "map" end
    local ok, err = pcall(function()
        C_Map.SetUserWaypoint(P.CreateFromCoordinates(mv.m, mv.x, mv.y))
        settings().placed = { m = mv.m, x = mv.x, y = mv.y }      -- (Glide's, even when the game won't track it)
        if type(st) == "table" and st.SetSuperTrackedUserWaypoint then st.SetSuperTrackedUserWaypoint(true) end
    end)
    if not ok then
        pinsOff = true
        ns.ReportError("map pin: " .. tostring(err))
        return false, "failed"
    end
    return true
end

-- ---- a new move, said once ------------------------------------------------------------------------------------------
local entered, waiting = false, false

local function said(mv) return ("%s|%s|%s|%s"):format(mv.t, tostring(mv.m), tostring(mv.x), tostring(mv.y)) end

local function state()
    waiting = false
    local mv, key = move(), charKey()
    if not mv or not key then return end
    local db = settings()
    if type(db.told) ~= "table" then db.told = {} end
    local s = said(mv)
    if db.told[key] == s then return end
    if inCombat() then waiting = true; return end          -- (said once the fight is over)
    db.told[key] = s
    local pinned = mv.m and ns.PinMode() == "free" and not pinsOff and not rxpArrow() and not othersGuiding()
        and placePin(mv)
    Print("next: " .. mv.t .. (pinned and " (pinned on your map)" or ""))
end

local function stateSafely()
    local ok, err = pcall(state)
    if not ok then ns.ReportError("next move: " .. tostring(err)) end
end

ns.On("PLAYER_ENTERING_WORLD", function()
    if entered then return end                              -- (the login or /reload's; not each loading screen)
    entered = true
    C_Timer.After(STATE_AFTER, stateSafely)
end)
ns.On("PLAYER_REGEN_ENABLED", function() if waiting then stateSafely() end end)

-- ---- /glide next ----------------------------------------------------------------------------------------------------
local function none()
    if type(ns.live) ~= "table" then return "Glide's item facts aren't loaded: restart WoW once" end
    return "no next move for this character yet: level 60 and a few minutes of the Glide app running; then /reload"
end

local function pinCommand(arg)
    if arg ~= "" then
        local mode = WORDS[arg]
        if not mode then return false end
        ns.SetPinMode(mode)
        if mode == "off" and pinIsOurs() and C_Map.ClearUserWaypoint then pcall(C_Map.ClearUserWaypoint) end
        local now, app = ns.PinMode()
        Print("map pins", SAY_MODE[mode] .. (app and ("; the Glide app's setting keeps them %s"):format(SAY_MODE[now])
            or ""))
        return
    end
    local mv = move()
    if not mv then return Print(none()) end
    if not mv.m then return Print("that move has no place on the map to pin") end
    local mode, app = ns.PinMode()
    if mode == "off" then
        return Print(app and "map pins are off in the Glide app's settings"
            or "map pins are off: /glide next pin command (or free) turns them on")
    end
    if pinsOff then return Print("map pins are off until you /reload: the game refused the last one") end
    if inCombat() then return Print("in combat: /glide next pin again once the fight is over") end
    local ok, why = placePin(mv)
    if ok then return Print("pinned on your map: " .. mv.t) end
    Print(why == "map" and "the game can't put a pin on that map"
        or why == "api" and "this client has no map pins"
        or "the game refused the pin, so Glide won't place one again until you /reload")
end

ns.Command("next", "next [pin] - your next move toward your next best-in-slot piece (pin: on your map; "
    .. "pin free|command|off: when Glide pins by itself)", function(rest)
    local a, b = (rest or ""):lower():match("^(%S*)%s*(%S*)")
    if a == "pin" then return pinCommand(b) end
    if a ~= "" then return false end
    local mv = move()
    if not mv then return Print(none()) end
    Print("next: " .. mv.t .. ((mv.m and ns.PinMode() ~= "off" and not pinsOff)
        and " (/glide next pin puts it on your map)" or ""))
    for _, alt in ipairs(mv.alt) do print("  or: " .. alt) end
end)
