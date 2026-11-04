-- RestedXP guides added in the Glide app, already decrypted on the PC so the game never runs RestedXP's string
-- decryptor. The app writes the plain guide texts to Data/RXPImports.lua (companion/rxp_import.py); after login, each
-- pack this account hasn't loaded yet goes straight to RestedXP's ImportGuide (or RXPGuides.ImportGuide), which parses
-- and caches each guide in RestedXP's own SavedVariables. Done a little at a time out of combat, once per pack, and
-- noted in GlideRecorderDB.rxpImports[id] = { t, ok, err } (the app reads it to tell you RestedXP has them).
-- A pack goes only into the Battle.net account it was bought for: the decrypted texts carry no account check of
-- their own (RestedXP's string had one), and every account on the PC shares this folder. The app writes each pack's
-- key check `k` (rxp_import.py key_check: Adler-32 of the account's 16-number RestedXP key); this account's comes
-- from RestedXP's own key for it (ReadCacheData, made from the BattleTag). Another account's pack is left alone,
-- with no note. "/glide rxpimport" tries the ones that failed again.
-- Only the latest pack added in the app stays (rxp_import.py replace). Once RestedXP took a whole pack, its cache
-- (RestedXP's account-wide db.profile.guides) keeps one copy of each guide: ImportGuide caches a new copy every time
-- and an older one left beside it would load instead at random at the next login, so the copies that were there
-- before go wherever a new one was written. The guides the pack replaces (its `drop`: { group, name }) go too; they
-- leave RestedXP's guide list at the next /reload (RestedXP has no working way to unload a guide).
local _, ns = ...
local rec = ns.rec

local START_AFTER = 5       -- seconds after login: RestedXP has initialised its database by then
local POLL = 0.25           -- seconds between ticks while loading guides into RestedXP
local BUDGET_MS = 40        -- RestedXP parsing per tick: Hardcore aborts a script after 200 ms; RestedXP's own
                            -- importer loads one guide per frame (without debugprofilestop, so does this)

-- RestedXP's one-guide importer (GuideLoader.lua addon.ImportGuide / RXPGuides.ImportGuide), or nil when not loaded
local function importFn()
    local R = _G.RXP
    if type(R) ~= "table" then return nil end
    local fn = R.ImportGuide or (type(R.RXPGuides) == "table" and R.RXPGuides.ImportGuide)
        or (type(_G.RXPGuides) == "table" and _G.RXPGuides.ImportGuide)
    if type(fn) == "function" then return fn, R end
end

-- this account's key check, from RestedXP's key for it (the 16 numbers ReadCacheData answers, or RXPData.cache),
-- or nil when RestedXP can't say yet
local function accountCheck(R)
    local key
    if type(R) == "table" and type(R.ReadCacheData) == "function" then
        local ok, k = pcall(R.ReadCacheData)
        if ok and type(k) == "table" then key = k end
    end
    if not key and type(_G.RXPData) == "table" and type(_G.RXPData.cache) == "table" then key = _G.RXPData.cache end
    if not key then return nil end
    local a, b = 1, 0
    for i = 0, 15 do
        local v = tonumber(key[i])
        if not v or v < 0 or v > 255 then return nil end
        a = (a + v) % 65521
        b = (b + a) % 65521
    end
    return b * 65536 + a
end

local function notes()
    local db = rec.db
    if not db then return nil end
    if type(db.rxpImports) ~= "table" then db.rxpImports = {} end
    return db.rxpImports
end

local ticker, current, retry
local tried = {}            -- ids started this session: each goes to RestedXP once per run

-- the next pack for this account (its key check `mine`; nil: any account's, to say RestedXP isn't loaded) that it
-- hasn't loaded yet (with retry, also one that failed)
local function nextPending(mine)
    local done = notes()
    if not done then return nil end
    for _, e in ipairs(ns.rxpImports or {}) do
        if type(e) == "table" and type(e.id) == "string" and type(e.guides) == "table" and #e.guides > 0
           and type(e.k) == "number" and (mine == nil or e.k == mine) and not tried[e.id] then
            local n = done[e.id]
            if not n or (retry and not n.ok) then return e end
        end
    end
end

local function plural(n) return (n or 0) == 1 and "guide" or "guides" end

-- RestedXP's guide cache (db.profile.guides: "group|subgroup|name|n" -> { key = "group|subgroup|name", ... }), or nil
local function guideCache(R)
    local db = type(R) == "table" and R.db
    local profile = type(db) == "table" and db.profile
    local cache = type(profile) == "table" and profile.guides
    if type(cache) == "table" then return cache end
end

local function cachedKey(entry, pk)
    if type(entry) == "table" and type(entry.key) == "string" then return entry.key end
    return type(pk) == "string" and pk:match("^[^|]+|[^|]-|[^|]+") or nil       -- as RestedXP's LoadCachedGuides
end

-- the cache entries there before a pack starts (each slot's table: RestedXP numbers its slots from 1 again every
-- session, so a new copy can take an old copy's slot; CacheGuide always writes a new table)
local function cacheSnapshot(R)
    local seen = {}
    for pk, entry in pairs(guideCache(R) or {}) do seen[pk] = entry end
    return seen
end

-- after RestedXP took a whole pack: the older copies of every guide it cached anew, and the guides the pack
-- replaces (unless it cached them anew), leave RestedXP's cache. Returns how many replaced guides went.
local function tidyCache(R, cur)
    local cache = guideCache(R)
    if not cache then return 0 end
    local fresh, drop = {}, {}
    for pk, entry in pairs(cache) do
        if cur.before[pk] ~= entry then
            local key = cachedKey(entry, pk)
            if key then fresh[key] = true end
        end
    end
    for _, d in ipairs(cur.e.drop or {}) do
        if type(d) == "table" and type(d[1]) == "string" and type(d[2]) == "string" then
            drop[d[2]] = drop[d[2]] or {}
            drop[d[2]][d[1]] = true
        end
    end
    local gone, dropped, n = {}, {}, 0
    for pk, entry in pairs(cache) do
        local key = cur.before[pk] == entry and cachedKey(entry, pk)
        if key and fresh[key] then
            gone[#gone + 1] = pk
        elseif key then
            local group, subgroup, name = key:match("^(.*)|(.*)|(.*)$")
            local by = name and drop[name]
            if by and (by[group] or by[subgroup]) then         -- RestedXP may file a group as the subgroup
                gone[#gone + 1] = pk
                if not dropped[key] then dropped[key] = true; n = n + 1 end
            end
        end
    end
    for _, pk in ipairs(gone) do cache[pk] = nil end
    return n
end

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

-- a few decrypted guides handed straight to RestedXP's ImportGuide when RestedXP is free and out of combat
local function step()
    local fn, R = importFn()
    if not fn then
        if current or nextPending() then
            rec.Print("RestedXP isn't loaded: the guides you added in the Glide app wait for it.")
        end
        return stop()
    end
    local mine = accountCheck(R)
    if not mine then
        rec.Print("RestedXP can't say which Battle.net account this is yet: the guides you added in the Glide app wait.")
        return stop()
    end
    local busy = (type(R.workerFrame) == "table" and R.workerFrame:IsShown())
        or (InCombatLockdown and InCombatLockdown())
    if busy then return end
    if not current then
        local e = nextPending(mine)
        if not e then return stop() end
        tried[e.id] = true
        local total = e.n or #e.guides
        current = { e = e, i = 1, total = total, before = cacheSnapshot(R) }
        rec.Print(("handing the %d RestedXP %s you added in the Glide app to RestedXP..."):format(total, plural(total)))
    end
    local e = current.e
    local guides = e.guides
    local clock = _G.debugprofilestop
    local t0 = clock and clock()
    while current.i <= #guides do
        local g = guides[current.i]
        if type(g) == "string" and #g > 0 then
            local ok, res, err = pcall(fn, g)
            if not ok or type(res) ~= "table" or not res.name then
                local why = tostring((not ok and res) or err or (type(res) == "string" and res) or "Error parsing guide")
                notes()[e.id] = { t = rec.Now(), ok = false, err = why }
                rec.Print(("RestedXP couldn't add the %s from the Glide app: %s (/glide rxpimport tries again)")
                    :format(plural(current.total), why))
                current = nil
                if not nextPending(mine) then stop() end
                return
            end
        end
        current.i = current.i + 1          -- counted once RestedXP took it
        if not t0 or clock() - t0 >= BUDGET_MS then break end
    end
    if current.i <= #guides then return end
    local dropped = tidyCache(R, current)
    refreshMenu(R)
    notes()[e.id] = { t = rec.Now(), ok = true }
    rec.Print(("RestedXP now has the %d %s you added in the Glide app."):format(current.total, plural(current.total))
        .. (dropped > 0 and (" The %d older %s they replace leave RestedXP at your next /reload.")
            :format(dropped, plural(dropped)) or ""))
    current = nil
    if not nextPending(mine) then stop() end
end

-- start loading (true), or false when nothing is waiting or it already runs
local function start(again)
    if ticker then return false end
    retry = again or false
    local _, R = importFn()
    if not nextPending(R and accountCheck(R) or nil) then retry = false; return false end
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
    elseif not nextPending(nil) then
        rec.Print("RestedXP already has every guide you added in the Glide app.")
    else
        local _, R = importFn()
        if R and accountCheck(R) then
            rec.Print("the RestedXP guides from the Glide app are for another Battle.net account on this PC.")
        else
            rec.Print("RestedXP can't say which Battle.net account this is yet: try again in a moment.")
        end
    end
end)
