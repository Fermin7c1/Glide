-- GlideRecorder quest-ID sweep.
-- Asks the server for every known quest ID (C_QuestLog.RequestLoadQuestByID). The server's answer
-- lands in the client cache (Cache/WDB/<locale>/questcache.wdb: title, level, objectives with
-- target IDs, description text), which tools/wdb.py decodes on the PC. What the API exposes is also
-- copied into the session for a second channel. Cached IDs (HaveQuestData) are not requested again,
-- so the sweep resumes where it left off after a logout.
local _, ns = ...
local rec = ns.rec           -- the recorder's namespace (Core.lua), kept apart from the live-link code
local Safe = rec.Safe
local QL = C_QuestLog

local MAX_RATE, MIN_RATE = 12, 1
local TICK = 0.25
local TIMEOUT = 15

local sc = {
    running = false, rate = 6, budget = 0, queue = {}, qi = 1,
    inflight = {}, retry = {}, pausedUntil = 0,
    sent = 0, answered = 0, cached = 0, failed = 0, timedOut = 0,
    win = { sent = 0, ans = 0 },
}
rec.scanner = sc

local function record(qid, ok, how)
    local s = rec.session
    if not s then return end
    local r = s.scan[qid] or {}
    local t = Safe(QL.GetTitleForQuestID(qid))
    r.t = (t and t ~= "") and t or r.t
    if QL.GetQuestDifficultyLevel then r.l = Safe(QL.GetQuestDifficultyLevel(qid)) or r.l end
    if GetQuestFactionGroup then r.f = Safe(GetQuestFactionGroup(qid)) or r.f end
    if GetQuestUiMapID then
        local m = Safe(GetQuestUiMapID(qid))
        if m and m > 0 then r.m = m end
    end
    local objs = QL.GetQuestObjectives(qid)
    if objs and #objs > 0 then
        r.o = {}
        for i, o in ipairs(objs) do r.o[i] = { Safe(o.text), Safe(o.type), Safe(o.numRequired) } end
    end
    if QL.IsQuestFlaggedCompleted and QL.IsQuestFlaggedCompleted(qid) then r.done = 1 end
    r.ok = ok and 1 or 0
    r.h = how
    s.scan[qid] = r
end

local function buildQueue(mode, a, b)
    local q, seen = {}, {}
    local function add(id)
        if id and not seen[id] then seen[id] = true; q[#q + 1] = id end
    end
    if mode == "range" then
        for id = a, b do add(id) end
        return q
    end
    for _, id in ipairs(ns.ForeverNewIDs or {}) do add(id) end   -- Data/QuestIDs.lua: on ns, not rec
    if mode ~= "new" then
        for _, r in ipairs(ns.QuestIDRanges or {}) do
            for id = r[1], r[2] do add(id) end
        end
    end
    return q
end

local function status(prefix)
    local left = #sc.queue - sc.qi + 1
    local n = 0
    for _ in pairs(sc.inflight) do n = n + 1 end
    rec.Print(("%sscan: %d answered, %d already cached, %d no data, %d timed out, %d waiting, %d left, %.0f/s")
        :format(prefix or "", sc.answered, sc.cached, sc.failed, sc.timedOut, n, left, sc.rate))
end

local function finish()
    sc.running = false
    if sc.ticker then sc.ticker:Cancel(); sc.ticker = nil end
    if sc.reporter then sc.reporter:Cancel(); sc.reporter = nil end
    status("finished ")
    rec.Print("Log out (or /glide save) so the client writes its cache; then run the PC import.")
end

local function tick()
    if not sc.running then return end
    local now = GetTime()
    -- expire requests the server never answered
    for qid, t in pairs(sc.inflight) do
        if now - t > TIMEOUT then
            sc.inflight[qid] = nil
            sc.timedOut = sc.timedOut + 1
            if not sc.retry[qid] then
                sc.retry[qid] = true
                sc.queue[#sc.queue + 1] = qid    -- one retry at the end
            end
        end
    end
    if now < sc.pausedUntil or InCombatLockdown() or UnitAffectingCombat("player") then return end

    sc.budget = math.min(sc.budget + sc.rate * TICK, sc.rate)
    while sc.budget >= 1 and sc.qi <= #sc.queue do
        local qid = sc.queue[sc.qi]
        sc.qi = sc.qi + 1
        if HaveQuestData(qid) and not sc.retry[qid] then
            sc.cached = sc.cached + 1
            if not (rec.session.scan[qid]) then record(qid, true, "c") end
        elseif not sc.inflight[qid] then
            sc.inflight[qid] = now
            sc.sent = sc.sent + 1
            sc.win.sent = sc.win.sent + 1
            sc.budget = sc.budget - 1
            QL.RequestLoadQuestByID(qid)
        end
    end
    if sc.qi > #sc.queue and next(sc.inflight) == nil then finish() end
end

-- every 5s: back off when the server stops answering, speed up while it keeps up
local function adapt()
    local w = sc.win
    if w.sent >= 10 and w.ans < w.sent * 0.2 then
        sc.rate = math.max(MIN_RATE, sc.rate / 2)
        sc.pausedUntil = GetTime() + 20
        if rec.debug then rec.Print("server slow; backing off to", sc.rate) end
    elseif w.sent >= 5 and w.ans >= w.sent * 0.9 then
        sc.rate = math.min(MAX_RATE, sc.rate + 1)
    end
    sc.win = { sent = 0, ans = 0 }
end

rec.On("QUEST_DATA_LOAD_RESULT", function(qid, ok)
    qid = tonumber(qid)
    if not qid or not sc.inflight[qid] then return end
    sc.inflight[qid] = nil
    sc.win.ans = sc.win.ans + 1
    if ok then
        sc.answered = sc.answered + 1
    else
        sc.failed = sc.failed + 1
    end
    record(qid, ok, "r")
end)

local function start(mode, a, b)
    if sc.running then rec.Print("scan already running; /glide scan stop first"); return end
    if not rec.session then return end
    sc.queue = buildQueue(mode, a, b)
    sc.qi, sc.sent, sc.answered, sc.cached, sc.failed, sc.timedOut = 1, 0, 0, 0, 0, 0
    sc.inflight, sc.retry, sc.budget, sc.rate = {}, {}, 0, 6
    sc.running = true
    sc.ticker = C_Timer.NewTicker(TICK, function()
        local ok, err = pcall(tick)
        if not ok then rec.Print("scan error:", err) end
    end)
    sc.reporter = C_Timer.NewTicker(5, function()
        adapt()
        sc.reports = (sc.reports or 0) + 1
        if sc.reports % 6 == 0 then status() end
    end)
    rec.Print(("scanning %d quest IDs (%s). Pauses in combat. /glide scan stop to stop."):format(#sc.queue, mode))
end

ns.Command("scan", "scan [new|all|range A B|stop|status] - ask the server for quest data", function(arg)
    local w = {}
    for x in (arg or ""):gmatch("%S+") do w[#w + 1] = x end
    local mode = (w[1] or "all"):lower()
    if mode == "stop" then
        if sc.running then finish() end
    elseif mode == "status" then
        status()
    elseif mode == "range" then
        local a, b = tonumber(w[2]), tonumber(w[3])
        if not a or not b or b < a then rec.Print("usage: /glide scan range 90000 99999"); return end
        start("range", a, b)
    elseif mode == "new" or mode == "all" then
        start(mode)
    else
        rec.Print("usage: /glide scan [new|all|range A B|stop|status]")
    end
end)
