-- RestedXP guides added in the Glide app, already decrypted on the PC so the game never runs RestedXP's string
-- decryptor. The app writes the plain guide texts to Data/RXPImports.lua (companion/rxp_import.py); after login, each
-- pack this account hasn't loaded yet goes straight to RestedXP's ImportGuide (or RXPGuides.ImportGuide), which parses
-- and caches each guide in RestedXP's own SavedVariables. Done in small batches out of combat, once per pack and
-- account, and noted in GlideRecorderDB.rxpImports[id] = { t, ok, err } (the app reads it to tell you RestedXP has
-- them). "/glide rxpimport" tries the ones that failed again.
local _, ns = ...
local rec = ns.rec

local START_AFTER = 5       -- seconds after login: RestedXP has initialised its database by then
local POLL = 0.25           -- seconds between batches while loading guides into RestedXP
local BATCH = 15            -- guides handed to RestedXP per tick: fast, well under Hardcore's 200 ms script cap

-- RestedXP's one-guide importer (GuideLoader.lua addon.ImportGuide / RXPGuides.ImportGuide), or nil when not loaded
local function importFn()
    local R = _G.RXP
    if type(R) ~= "table" then return nil end
    local fn = R.ImportGuide or (type(R.RXPGuides) == "table" and R.RXPGuides.ImportGuide)
        or (type(_G.RXPGuides) == "table" and _G.RXPGuides.ImportGuide)
    if type(fn) == "function" then return fn, R end
end

local function notes()
    local db = rec.db
    if not db then return nil end
    if type(db.rxpImports) ~= "table" then db.rxpImports = {} end
    return db.rxpImports
end

local ticker, current, retry
local tried = {}            -- ids started this session: each goes to RestedXP once per run

-- the next pack this account hasn't loaded yet (with retry, also one that failed)
local function nextPending()
    local done = notes()
    if not done then return nil end
    for _, e in ipairs(ns.rxpImports or {}) do
        if type(e) == "table" and type(e.id) == "string" and type(e.guides) == "table" and #e.guides > 0
           and not tried[e.id] then
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

local function refreshMenu(R)
    if type(R.ScheduleTask) == "function" and type(R.RXPFrame) == "table" and R.RXPFrame.GenerateMenuTable then
        pcall(R.ScheduleTask, R, R.RXPFrame.GenerateMenuTable)
    end
    if type(R.guideImporter) == "table" and type(R.guideImporter.UpdateImportUI) == "function" then
        pcall(R.guideImporter.UpdateImportUI, R.guideImporter)
    end
end

-- one batch of decrypted guides handed straight to RestedXP's ImportGuide when RestedXP is free and out of combat
local function step()
    local fn, R = importFn()
    if not fn then
        if current or nextPending() then
            rec.Print("RestedXP isn't loaded: the guides you added in the Glide app wait for it.")
        end
        return stop()
    end
    local busy = (type(R.workerFrame) == "table" and R.workerFrame:IsShown())
        or (InCombatLockdown and InCombatLockdown())
    if busy then return end
    if not current then
        local e = nextPending()
        if not e then return stop() end
        tried[e.id] = true
        local total = e.n or #e.guides
        current = { e = e, i = 1, total = total }
        rec.Print(("handing the %d RestedXP %s you added in the Glide app to RestedXP..."):format(total, plural(total)))
    end
    local e = current.e
    local guides = e.guides
    local last = math.min(current.i + BATCH - 1, #guides)
    while current.i <= last do
        local g = guides[current.i]
        current.i = current.i + 1
        if type(g) == "string" and #g > 0 then
            local ok, res, err = pcall(fn, g)
            if not ok or type(res) ~= "table" or not res.name then
                local why = tostring((not ok and res) or err or (type(res) == "string" and res) or "Error parsing guide")
                notes()[e.id] = { t = rec.Now(), ok = false, err = why }
                rec.Print(("RestedXP couldn't add the %s from the Glide app: %s (/glide rxpimport tries again)")
                    :format(plural(current.total), why))
                current = nil
                if not nextPending() then stop() end
                return
            end
        end
    end
    if current.i <= #guides then return end
    refreshMenu(R)
    notes()[e.id] = { t = rec.Now(), ok = true }
    rec.Print(("RestedXP now has the %d %s you added in the Glide app."):format(current.total, plural(current.total)))
    current = nil
    if not nextPending() then stop() end
end

-- start loading (true), or false when nothing is waiting or it already runs
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
