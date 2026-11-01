-- RestedXP guide strings added in the Glide app, so a guide bought while you can't stop to import it reaches
-- RestedXP anyway. The app writes them to Data/RXPImports.lua (companion/rxp_import.py); after login, each one this
-- account hasn't handed over yet goes to RestedXP's own importer, as if pasted into its import window: RestedXP checks
-- the string against the account and keeps the guides itself. Once per string and account, out of combat, noted in
-- GlideRecorderDB.rxpImports[id] = { t, ok, err } (the app reads it to tell you RestedXP has them).
-- "/glide rxpimport" tries the ones that failed again.
local _, ns = ...
local rec = ns.rec

local START_AFTER = 20      -- seconds after login: RestedXP has loaded its own guides by then
local POLL = 2              -- seconds between looks at RestedXP's importer while it works

-- RestedXP's importer (its UI/V2/GuideImporter.lua and GuideLoader.lua), or nil when RestedXP isn't loaded
local function importer()
    local R = _G.RXP
    local gi = type(R) == "table" and R.guideImporter
    if type(gi) == "table" and type(gi.ImportString) == "function" then return gi, R end
end

local function notes()
    local db = rec.db
    if not db then return nil end
    if type(db.rxpImports) ~= "table" then db.rxpImports = {} end
    return db.rxpImports
end

local ticker, current, retry
local tried = {}            -- ids started this session: each goes to RestedXP once per run

-- the next string this account hasn't handed over (with retry, also one that failed)
local function nextPending()
    local done = notes()
    if not done then return nil end
    for _, e in ipairs(ns.rxpImports or {}) do
        if type(e) == "table" and type(e.id) == "string" and type(e.s) == "string" and not tried[e.id] then
            local n = done[e.id]
            if not n or (retry and not n.ok) then return e end
        end
    end
end

local function plural(n) return (n or 0) == 1 and "guide" or "guides" end

local function stop()
    if ticker then ticker:Cancel() end
    ticker, current, retry = nil, nil, false
end

-- one look: note how the running import ended, or start the next one when RestedXP is free and we're out of combat
local function step()
    local gi, R = importer()
    if not gi then
        if nextPending() then rec.Print("RestedXP isn't loaded: the guides you added in the Glide app wait for it.") end
        return stop()
    end
    local busy = gi.importCoroutine ~= nil or (type(R.workerFrame) == "table" and R.workerFrame:IsShown())
    if current then
        if busy then return end
        local gui = type(gi.gui) == "table" and gi.gui or {}
        local ok = not gui.progressError
        local why = type(gui.importStatusHistory) == "table" and gui.importStatusHistory[1]
        notes()[current.id] = { t = rec.Now(), ok = ok, err = (not ok) and tostring(why or "?") or nil }
        if ok then
            rec.Print(("RestedXP now has the %d %s you added in the Glide app."):format(current.n or 0, plural(current.n)))
        else
            rec.Print(("RestedXP couldn't add the %s from the Glide app: %s (/glide rxpimport tries again)")
                :format(plural(current.n), tostring(why or "?")))
        end
        current = nil
        if not nextPending() then stop() end
        return
    end
    if busy or (InCombatLockdown and InCombatLockdown()) then return end
    local e = nextPending()
    if not e then return stop() end
    tried[e.id] = true
    local called, started, err = pcall(gi.ImportString, gi, e.s)
    if called and started then
        current = e
        rec.Print(("handing the %d RestedXP %s you added in the Glide app to RestedXP..."):format(e.n or 0, plural(e.n)))
    else
        local why = tostring((called and err) or started or "?")
        notes()[e.id] = { t = rec.Now(), ok = false, err = why }
        rec.Print(("RestedXP couldn't add the %s from the Glide app: %s (/glide rxpimport tries again)")
            :format(plural(e.n), why))
    end
end

-- start looking (true), or false when nothing is waiting or it already runs
local function start(again)
    if ticker then return false end
    retry = again or false
    if not nextPending() then retry = false; return false end
    ticker = C_Timer.NewTicker(POLL, step)
    return true
end
ns.RXPImportStart = start

rec.On("PLAYER_LOGIN", function()
    if C_Timer then C_Timer.After(START_AFTER, function() start(false) end) end
end)

ns.Command("rxpimport", "rxpimport - hand the RestedXP guides you added in the Glide app to RestedXP (again)", function()
    tried = {}
    if start(true) then return end
    if ticker then
        rec.Print("already handing your guides to RestedXP.")
    elseif #(ns.rxpImports or {}) == 0 then
        rec.Print("no RestedXP guides from the Glide app here. Add them on the app's Addon page; /reload after.")
    else
        rec.Print("RestedXP already has every guide you added in the Glide app.")
    end
end)
