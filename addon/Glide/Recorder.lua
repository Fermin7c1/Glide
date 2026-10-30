-- GlideRecorder passive recorder: everything the server shows us while we play.
local _, ns = ...
local rec = ns.rec           -- the recorder's namespace (Core.lua), kept apart from the live-link code
local Safe, Pos, Now = rec.Safe, rec.Pos, rec.Now

local function S() return rec.session end

local function Q(qid)
    local s = S()
    if not s or not qid or qid == 0 then return nil end
    local q = s.quests[qid]
    if not q then
        q = {}
        s.quests[qid] = q
    end
    return q
end

local function stamp(extra)
    local p = Pos() or {}
    local pl = rec.Player()
    local e = { t = Now(), m = p.m, x = p.x, y = p.y, lvl = pl.lvl }
    if extra then for k, v in pairs(extra) do e[k] = v end end
    return e
end

-- entity talking to us in a quest/gossip dialog
local function dialogNPC()
    return rec.UnitEntity("questnpc") or rec.UnitEntity("npc")
end

local function addEntityRef(list, ent)
    if not ent then return end
    local p = Pos() or {}
    for _, e in ipairs(list) do
        if e.k == ent.k and e.id == ent.id then return end
    end
    if #list < 8 then
        list[#list + 1] = { k = ent.k, id = ent.id, n = ent.n, m = p.m, x = p.x, y = p.y }
    end
end

-- ent as one of a quest's givers or enders (role "giver" / "ender")
local function addQuestNPC(q, role, ent)
    q[role] = q[role] or {}
    addEntityRef(q[role], ent)
end

-- the session's record of an NPC or object, created when first met
local function entityRecord(s, ent)
    local book = ent.k == "object" and s.objects or s.npcs
    local e = book[ent.id]
    if not e then
        e = { at = {}, near = {} }
        book[ent.id] = e
    end
    e.n = e.n or ent.n
    return e
end

-- accurate NPC/object position: we are standing next to it (dialog, vendor, trainer, loot)
local function seenAt(ent, how)
    local s = S()
    if not s or not ent then return end
    local e = entityRecord(s, ent)
    if how then e[how] = true end
    rec.AddPoint(e.at, Pos(), 20, 0.5)
end
rec.SeenAt = seenAt

-- the NPC of the open quest dialog is this quest's giver or ender (role), and stands right here
local function dialogNPCIs(q, role)
    local ent = dialogNPC()
    addQuestNPC(q, role, ent)
    seenAt(ent, role)
end

local function rewards()
    local r, c = {}, {}
    for i = 1, (GetNumQuestRewards and GetNumQuestRewards() or 0) do
        local link = GetQuestItemLink("reward", i)
        local _, _, count = GetQuestItemInfo("reward", i)
        r[#r + 1] = { id = rec.ItemIDFromLink(link), c = count }
    end
    for i = 1, (GetNumQuestChoices and GetNumQuestChoices() or 0) do
        local link = GetQuestItemLink("choice", i)
        c[#c + 1] = { id = rec.ItemIDFromLink(link) }
    end
    return (#r > 0) and r or nil, (#c > 0) and c or nil
end

-- ---------------------------------------------------------------------------------------------
-- quest dialogs
-- ---------------------------------------------------------------------------------------------
rec.On("QUEST_DETAIL", function()
    local qid = GetQuestID()
    local q = Q(qid)
    if not q then return end
    q.t = Safe(GetTitleText()) or q.t
    q.details = Safe(GetQuestText())
    q.objText = Safe(GetObjectiveText())
    q.xpOffer = Safe(GetRewardXP and GetRewardXP())
    q.moneyOffer = Safe(GetRewardMoney and GetRewardMoney())
    q.rewards, q.choices = rewards()
    if C_QuestLog.GetQuestDifficultyLevel then q.lvl = Safe(C_QuestLog.GetQuestDifficultyLevel(qid)) end
    q.offeredAt = q.offeredAt or stamp()
    dialogNPCIs(q, "giver")
end)

rec.On("QUEST_PROGRESS", function()
    local q = Q(GetQuestID())
    if not q then return end
    q.progText = Safe(GetProgressText())
    dialogNPCIs(q, "ender")
end)

rec.On("QUEST_COMPLETE", function()
    local qid = GetQuestID()
    local q = Q(qid)
    if not q then return end
    q.t = q.t or Safe(GetTitleText())
    q.doneText = Safe(GetRewardText())
    q.xpOffer = Safe(GetRewardXP and GetRewardXP()) or q.xpOffer
    q.moneyOffer = Safe(GetRewardMoney and GetRewardMoney()) or q.moneyOffer
    local r, c = rewards()
    q.rewards, q.choices = r or q.rewards, c or q.choices
    dialogNPCIs(q, "ender")
end)

local function objectives(qid)
    local list = C_QuestLog.GetQuestObjectives(qid)
    if not list then return nil end
    local out = {}
    for i, o in ipairs(list) do
        out[i] = { text = Safe(o.text), type = Safe(o.type), need = Safe(o.numRequired) }
    end
    return out
end

rec.On("QUEST_ACCEPTED", function(a1, a2)
    local qid = tonumber(a2 or a1)
    local q = Q(qid)
    if not q then return end
    q.t = q.t or Safe(C_QuestLog.GetTitleForQuestID(qid))
    q.accepted = stamp()
    q.objectives = objectives(qid) or q.objectives
    if C_QuestLog.GetQuestDifficultyLevel then q.lvl = q.lvl or Safe(C_QuestLog.GetQuestDifficultyLevel(qid)) end
    rec.snapshotObjectives(qid)
    rec.recordWaypoint(qid)
end)

rec.On("QUEST_TURNED_IN", function(qid, xp, money)
    qid = tonumber(qid)
    local q = Q(qid)
    if not q then return end
    q.turned = stamp({ xp = Safe(xp), money = Safe(money) })
end)

rec.On("QUEST_REMOVED", function(qid)
    qid = tonumber(qid)
    local q = Q(qid)
    if q and not q.turned then q.removed = Now() end
end)

-- ---------------------------------------------------------------------------------------------
-- objective progress: where each objective actually advanced, and which mob we had just killed
-- ---------------------------------------------------------------------------------------------
local progress = {}  -- [qid] = { [i] = numFulfilled }
local lastKill       -- { id, n, t }

function rec.snapshotObjectives(qid)
    local list = C_QuestLog.GetQuestObjectives(qid)
    if not list then return end
    local p = progress[qid] or {}
    for i, o in ipairs(list) do p[i] = Safe(o.numFulfilled) or p[i] or 0 end
    progress[qid] = p
end

-- objective j of quest qid advanced: where we stood, and the mob we had just killed (within 3 s)
local function objectiveAdvanced(qid, info, j)
    local q = Q(qid)
    q.t = q.t or Safe(info.title)
    q.prog = q.prog or {}
    q.prog[j] = q.prog[j] or {}
    rec.AddPoint(q.prog[j], Pos(), 60, 0.8)
    if lastKill and Now() - lastKill.t <= 3 then
        q.mobs = q.mobs or {}
        q.mobs[j] = q.mobs[j] or {}
        q.mobs[j][lastKill.id] = (q.mobs[j][lastKill.id] or 0) + 1
    end
end

local function scanLog()
    if not S() then return end
    for qid, info in ns.QuestLogQuests() do
        local list = C_QuestLog.GetQuestObjectives(qid)
        local old = progress[qid]
        if list and old then
            for j, o in ipairs(list) do
                local now = Safe(o.numFulfilled)
                if now and old[j] and now > old[j] then objectiveAdvanced(qid, info, j) end
                old[j] = now or old[j]
            end
        elseif list then
            rec.snapshotObjectives(qid)
        end
    end
end

local pending = false
rec.On("QUEST_LOG_UPDATE", function()
    if pending then return end
    pending = true
    C_Timer.After(0.4, function()
        pending = false
        local ok, err = pcall(scanLog)
        if not ok and rec.debug then rec.Print(err) end
    end)
end)

-- server-side quest waypoints (the arrow Blizzard shows); a free hint for where to go
function rec.recordWaypoint(qid)
    local q = Q(qid)
    if not q or not C_QuestLog.GetNextWaypointForMap then return end
    local map = Safe(C_Map.GetBestMapForUnit("player"))
    if not map then return end
    local x, y = C_QuestLog.GetNextWaypointForMap(qid, map)
    x, y = Safe(x), Safe(y)
    if x and y then
        q.wp = q.wp or {}
        local txt = C_QuestLog.GetNextWaypointText and Safe(C_QuestLog.GetNextWaypointText(qid))
        rec.AddPoint(q.wp, { m = map, x = rec.Round(x * 100, 2), y = rec.Round(y * 100, 2) }, 10, 1)
        if txt then q.wpText = txt end
    end
end

-- ---------------------------------------------------------------------------------------------
-- gossip / greeting: which quests an NPC offers, without accepting them
-- ---------------------------------------------------------------------------------------------
local function gossipRecord(ent, avail, active)
    local s = S()
    if not s or not ent then return end
    local g = s.gossip[ent.id] or { k = ent.k, n = ent.n, avail = {}, active = {} }
    s.gossip[ent.id] = g
    for _, id in ipairs(avail) do g.avail[id] = true end
    for _, id in ipairs(active) do g.active[id] = true end
    seenAt(ent, "gossip")
end

rec.On("GOSSIP_SHOW", function()
    local ent = rec.UnitEntity("npc")
    local avail, active = {}, {}
    for _, info in ipairs(C_GossipInfo.GetAvailableQuests() or {}) do
        local id = Safe(info.questID)
        if id then
            avail[#avail + 1] = id
            local q = Q(id)
            q.t = q.t or Safe(info.title)
            q.lvl = q.lvl or Safe(info.questLevel)
            addQuestNPC(q, "giver", ent)
        end
    end
    for _, info in ipairs(C_GossipInfo.GetActiveQuests() or {}) do
        local id = Safe(info.questID)
        if id then active[#active + 1] = id end
    end
    gossipRecord(ent, avail, active)
end)

rec.On("QUEST_GREETING", function()
    local ent = rec.UnitEntity("npc") or rec.UnitEntity("questnpc")
    local avail = {}
    for i = 1, (GetNumAvailableQuests and GetNumAvailableQuests() or 0) do
        local ok, _, _, _, _, id = pcall(GetAvailableQuestInfo, i)
        id = ok and Safe(id)
        if id then
            avail[#avail + 1] = id
            local q = Q(id)
            q.t = q.t or Safe(GetAvailableTitle(i))
            addQuestNPC(q, "giver", ent)
        end
    end
    gossipRecord(ent, avail, {})
end)

-- ---------------------------------------------------------------------------------------------
-- NPCs we see (approximate position = ours) and NPCs we talk to (accurate)
-- ---------------------------------------------------------------------------------------------
local function seeUnit(unit)
    local s = S()
    if not s or UnitIsPlayer(unit) then return end
    local ent = rec.UnitEntity(unit)
    if not ent or ent.k ~= "npc" then return end
    local e = entityRecord(s, ent)
    e.lvl = e.lvl or Safe(UnitLevel(unit))
    e.cls = e.cls or Safe(UnitClassification(unit))
    e.ct = e.ct or Safe(UnitCreatureType(unit))
    e.r = e.r or Safe(UnitReaction(unit, "player"))
    rec.AddPoint(e.near, Pos(), 25, 1.5)
end

rec.On("UPDATE_MOUSEOVER_UNIT", function() seeUnit("mouseover") end)
rec.On("PLAYER_TARGET_CHANGED", function() seeUnit("target") end)
rec.On("NAME_PLATE_UNIT_ADDED", function(unit) seeUnit(unit) end)

-- ---------------------------------------------------------------------------------------------
-- XP: timeline, kills (mob, xp, rested bonus, where)
-- ---------------------------------------------------------------------------------------------
local lastXP = {}
rec.On("PLAYER_XP_UPDATE", function()
    local s = S()
    if not s then return end
    local p = rec.Player()
    if p.xp == lastXP.xp and p.lvl == lastXP.lvl then return end
    lastXP = p
    s.xp[#s.xp + 1] = { t = Now(), lvl = p.lvl, xp = p.xp, max = p.max, rest = p.rest }
end)

local XP_KILL = "^(.+) dies, you gain (%d+) experience"
local XP_REST = "%+(%d+) exp Rested"
rec.On("CHAT_MSG_COMBAT_XP_GAIN", function(text)
    local s = S()
    text = Safe(text)
    if not s or not text then return end
    local mob, xp = text:match(XP_KILL)
    if not mob then return end
    local k = stamp({ n = mob, xp = tonumber(xp), rest = tonumber(text:match(XP_REST)) })
    -- the target is usually the corpse we just killed
    local ent = rec.UnitEntity("target")
    if ent and ent.k == "npc" and ent.n == mob then
        k.id = ent.id
        k.ml = Safe(UnitLevel("target"))
        lastKill = { id = ent.id, n = mob, t = Now() }
    else
        lastKill = nil
    end
    if #s.kills < 20000 then s.kills[#s.kills + 1] = k end
end)

-- ---------------------------------------------------------------------------------------------
-- loot (quest item sources), vendors, trainers, flight masters
-- ---------------------------------------------------------------------------------------------
rec.On("LOOT_OPENED", function()
    local s = S()
    if not s or #s.loot >= 5000 then return end
    local ev = stamp({ items = {} })
    for i = 1, GetNumLootItems() do
        local link = GetLootSlotLink(i)
        local id = rec.ItemIDFromLink(link)
        local srcs = { GetLootSourceInfo(i) }
        local kind, src = rec.ParseGUID(srcs[1])
        if id then ev.items[#ev.items + 1] = { id = id, c = Safe(srcs[2]), sk = kind, src = src } end
        if kind == "object" then seenAt({ k = "object", id = src }, "loot") end
    end
    if #ev.items > 0 then s.loot[#s.loot + 1] = ev end
end)

rec.On("MERCHANT_SHOW", function()
    local s = S()
    local ent = rec.UnitEntity("npc")
    if not s or not ent then return end
    seenAt(ent, "vendor")
    local items = {}
    for i = 1, GetMerchantNumItems() do
        local id = rec.ItemIDFromLink(GetMerchantItemLink(i))
        local price
        if C_MerchantFrame and C_MerchantFrame.GetItemInfo then
            local info = C_MerchantFrame.GetItemInfo(i)
            price = info and Safe(info.price)
        elseif GetMerchantItemInfo then
            price = select(3, GetMerchantItemInfo(i))
        end
        if id then items[#items + 1] = { id = id, p = price } end
    end
    s.vendors[ent.id] = { n = ent.n, items = items, pos = Pos() }
end)

rec.On("TRAINER_SHOW", function()
    local s = S()
    local ent = rec.UnitEntity("npc")
    if not s or not ent or not GetNumTrainerServices then return end
    seenAt(ent, "trainer")
    local list = {}
    for i = 1, GetNumTrainerServices() do
        local name, sub, cat = GetTrainerServiceInfo(i)
        list[#list + 1] = {
            n = Safe(name), r = Safe(sub), cat = Safe(cat),
            cost = GetTrainerServiceCost and Safe((GetTrainerServiceCost(i))),
            lvl = GetTrainerServiceLevelReq and Safe(GetTrainerServiceLevelReq(i)),
        }
    end
    s.trainers[ent.id] = { n = ent.n, services = list, pos = Pos(), lvl = UnitLevel("player") }
end)

rec.On("TAXIMAP_OPENED", function()
    local s = S()
    if not s or not C_TaxiMap then return end
    local ent = rec.UnitEntity("npc")
    seenAt(ent, "flight")
    local mapID = GetTaxiMapID and GetTaxiMapID()
    if not mapID then return end
    for _, node in ipairs(C_TaxiMap.GetAllTaxiNodes(mapID) or {}) do
        local x, y
        if node.position then x, y = node.position:GetXY() end
        s.taxi[node.name] = { id = node.nodeID, state = node.state, map = mapID,
                              x = x and rec.Round(x * 100, 2), y = y and rec.Round(y * 100, 2),
                              cost = node.slotIndex and TaxiNodeCost and TaxiNodeCost(node.slotIndex) }
    end
    if ent then s.taxi._master = s.taxi._master or {} ; s.taxi._master[ent.id] = Pos() end
end)

-- ---------------------------------------------------------------------------------------------
-- milestones and the route trail
-- ---------------------------------------------------------------------------------------------
local function event(kind, extra)
    local s = S()
    if not s then return end
    local e = stamp(extra)
    e.e = kind
    s.events[#s.events + 1] = e
    return e
end
rec.RecordEvent = event       -- also used by ns.Mark (Core.lua)

rec.On("PLAYER_LEVEL_UP", function(level) event("level", { to = Safe(level) }) end)
rec.On("PLAYER_DEAD", function() event("death") end)
rec.On("PLAYER_ALIVE", function() event("alive") end)
rec.On("PLAYER_UNGHOST", function() event("unghost") end)
rec.On("ZONE_CHANGED_NEW_AREA", function() event("zone", { z = Safe(GetZoneText()) }) end)
rec.On("PLAYER_REGEN_DISABLED", function() event("combat") end)
rec.On("PLAYER_REGEN_ENABLED", function() event("ooc") end)
rec.On("PLAYER_CONTROL_LOST", function() if UnitOnTaxi("player") then event("taxi") end end)
rec.On("PLAYER_CONTROL_GAINED", function() event("control") end)
rec.On("HEARTHSTONE_BOUND", function() event("bind", { z = Safe(GetBindLocation()) }) end)
rec.On("UNIT_SPELLCAST_SUCCEEDED", function(unit, _, spellID)
    if unit ~= "player" then return end
    spellID = Safe(spellID)
    if spellID == ns.HEARTHSTONE_SPELL then event("hearth") end
end)

local lastTrail
rec.AtSessionStart(function()
    C_Timer.NewTicker(3, function()
        local s = S()
        if not s or not rec.db.settings.trail or #s.trail >= 30000 then return end
        local p = Pos()
        if not p or not p.x then return end
        if lastTrail and lastTrail.m == p.m and math.abs(lastTrail.x - p.x) < 0.25
            and math.abs(lastTrail.y - p.y) < 0.25 then return end
        lastTrail = p
        s.trail[#s.trail + 1] = { Now(), p.m, p.x, p.y, UnitAffectingCombat("player") and 1 or 0 }
    end)
    -- refresh server waypoints for everything in the log every 15s
    C_Timer.NewTicker(15, function()
        if not S() then return end
        for qid in ns.QuestLogQuests() do pcall(rec.recordWaypoint, qid) end
    end)
end)

-- ---------------------------------------------------------------------------------------------
-- trained skills, once per session a few seconds after login:
-- session.skills = { t, lines = { { n = name, r = rank, mx = max rank, md = modifier, h = header } },
--                    w = weapon mask, a = armor mask, ranks = "Swords 5/10,..." }
-- lines: Classic-style skill lines when the client has them; w/a/ranks: the live link (Gear.lua).
-- Left out entirely when neither answers.
-- ---------------------------------------------------------------------------------------------
local function snapshotSkills()
    local s = S()
    if not s then return end
    local out = {}
    if GetNumSkillLines and GetSkillLineInfo then
        local okN, n = pcall(GetNumSkillLines)
        n = okN and Safe(n)
        local lines, header = {}, nil
        for i = 1, (type(n) == "number" and n or 0) do
            local ok, name, isHeader, _, rank, _, mod, maxRank = pcall(GetSkillLineInfo, i)
            name = ok and Safe(name)
            if name then
                if Safe(isHeader) then
                    header = name
                else
                    mod = Safe(mod)
                    lines[#lines + 1] = { n = name, r = Safe(rank), mx = Safe(maxRank),
                                          md = (mod and mod ~= 0) and mod or nil, h = header }
                end
            end
        end
        if #lines > 0 then out.lines = lines end
    end
    if ns.BuildSkills then pcall(ns.BuildSkills) end
    out.w, out.a = ns.skillWeapons, ns.skillArmor
    if type(ns.skillRanks) == "string" and ns.skillRanks ~= "" then out.ranks = ns.skillRanks end
    if next(out) then
        out.t = Now()
        s.skills = out
    end
end

-- ---------------------------------------------------------------------------------------------
-- map bounds: world position of each UiMap's (0,0) and (1,1) corners, so the PC side can convert
-- map % <-> world yards:  GlideRecorderDB.mapBounds[uiMapID] = { instanceID, x0, y0, x1, y1, name }
-- (world x runs north, world y west: x0,y0 = top-left corner of the map, x1,y1 = bottom-right).
-- Collected once per login a few seconds in (the map system needs a moment), 50 maps per tick, from
-- every map under Cosmic/Azeroth, the known Forever maps and the player's map (and its parents).
-- Maps stored on earlier logins with the same client build are kept; a new build starts over.
-- The new table replaces the saved one only once the pass completes.
-- ---------------------------------------------------------------------------------------------
local BOUNDS_ROOTS = { 946, 947 }                          -- Cosmic, Azeroth (all descendants)
local BOUNDS_EXTRA = { 2521, 2524, 2548, 2652, 2665 }      -- known Forever maps (and their descendants)
local BOUNDS_DELAY, BOUNDS_TICK, BOUNDS_PER_TICK = 5, 0.1, 50
local bounds = { running = false, done = 0, total = 0, found = 0 }
rec.bounds = bounds

local function finite(v)
    v = Safe(v)
    if type(v) ~= "number" or v ~= v or v == math.huge or v == -math.huge then return nil end
    return v
end

local function corner(mapID, vec)
    local ok, inst, pos = pcall(C_Map.GetWorldPosFromMapPos, mapID, vec)
    if not ok or not pos then return nil end
    local okXY, x, y = pcall(function() return pos:GetXY() end)
    if not okXY then return nil end
    return finite(inst), finite(x), finite(y)
end

local function mapName(mapID)
    if not C_Map.GetMapInfo then return nil end
    local ok, info = pcall(C_Map.GetMapInfo, mapID)
    local name = ok and type(info) == "table" and Safe(info.name)
    return type(name) == "string" and name or nil
end

-- { instanceID, x0, y0, x1, y1, name } or nil when the game gives no usable world position
local V00, V11
local function boundsEntry(mapID, name)
    V00 = V00 or CreateVector2D(0, 0)
    V11 = V11 or CreateVector2D(1, 1)
    local i0, x0, y0 = corner(mapID, V00)
    if not (i0 and x0 and y0) then return nil end
    local i1, x1, y1 = corner(mapID, V11)
    if not (i1 and x1 and y1) or i1 ~= i0 or x0 == x1 or y0 == y1 then return nil end
    return { i0, x0, y0, x1, y1, name or mapName(mapID) }
end

local function collectMaps()
    local ids, names, seen = {}, {}, {}
    local function add(id, name)
        id = Safe(id)
        if type(id) ~= "number" or id <= 0 or seen[id] then return end
        seen[id] = true
        ids[#ids + 1] = id
        name = Safe(name)
        names[id] = type(name) == "string" and name or nil
    end
    local function tree(id)
        add(id)
        if not C_Map.GetMapChildrenInfo then return end
        local ok, list = pcall(C_Map.GetMapChildrenInfo, id, nil, true)
        if not ok or type(list) ~= "table" then return end
        for _, info in ipairs(list) do
            if type(info) == "table" then add(info.mapID, info.name) end
        end
    end
    for _, id in ipairs(BOUNDS_ROOTS) do tree(id) end
    for _, id in ipairs(BOUNDS_EXTRA) do tree(id) end
    -- the player's map and its parents, with their subtrees if Forever hangs them off another root
    local cur = Safe(C_Map.GetBestMapForUnit("player"))
    for _ = 1, 12 do
        if type(cur) ~= "number" or cur <= 0 then break end
        if not seen[cur] then tree(cur) end
        local ok, info = pcall(C_Map.GetMapInfo, cur)
        cur = ok and type(info) == "table" and Safe(info.parentMapID) or nil
    end
    return ids, names
end

-- start a pass; false when one is running or the client lacks the API
function rec.ScanMapBounds()
    local db = rec.db
    if bounds.running or not db or not C_Map or not C_Map.GetWorldPosFromMapPos or not CreateVector2D then
        return false
    end
    local build = rec.BuildString()
    local ids, names = collectMaps()
    if #ids == 0 then return false end
    local out = {}
    if db.mapBoundsBuild == build and type(db.mapBounds) == "table" then
        for id, e in pairs(db.mapBounds) do out[id] = e end
    end
    bounds.running, bounds.out, bounds.total, bounds.done, bounds.found = true, out, #ids, 0, 0
    local i, ticker = 0, nil
    ticker = C_Timer.NewTicker(BOUNDS_TICK, function()
        local ok, err = pcall(function()
            for _ = 1, BOUNDS_PER_TICK do
                i = i + 1
                local id = ids[i]
                if not id then break end
                local e = boundsEntry(id, names[id])
                if e then
                    out[id] = e
                    bounds.found = bounds.found + 1
                end
            end
        end)
        bounds.done = math.min(i, #ids)
        if ok and i < #ids then return end
        ticker:Cancel()
        bounds.running, bounds.out = false, nil
        if ok then
            db.mapBounds, db.mapBoundsBuild = out, build
        else
            rec.Report("map bounds: " .. tostring(err))
        end
    end)
    bounds.ticker = ticker
    return true
end

rec.AtSessionStart(function()
    C_Timer.After(BOUNDS_DELAY, function()
        local ok, err = pcall(snapshotSkills)
        if not ok then rec.Report("skills: " .. tostring(err)) end
        ok, err = pcall(rec.ScanMapBounds)
        if not ok then rec.Report("map bounds: " .. tostring(err)) end
    end)
end)

-- a map outside the walked trees (an instance, say): add it when we arrive
rec.On("ZONE_CHANGED_NEW_AREA", function()
    C_Timer.After(2, function()
        local ok, err = pcall(function()
            local db = rec.db
            if not db or not C_Map.GetWorldPosFromMapPos or not CreateVector2D then return end
            local into = bounds.running and bounds.out
                or (db.mapBoundsBuild == rec.BuildString() and db.mapBounds)
            local map = Safe(C_Map.GetBestMapForUnit("player"))
            if type(into) ~= "table" or not map or into[map] then return end
            into[map] = boundsEntry(map)
        end)
        if not ok then rec.Report("map bounds: " .. tostring(err)) end
    end)
end)

ns.Command("maps", "maps [scan] - stored map bounds (map % <-> world yards); scan = collect again now", function(arg)
    local db = rec.db
    if (arg or ""):lower() == "scan" then
        if bounds.running then
            rec.Print(("map bounds: already collecting (%d/%d)"):format(bounds.done, bounds.total))
        elseif rec.ScanMapBounds() then
            rec.Print(("map bounds: collecting %d maps..."):format(bounds.total))
        else
            rec.Print("map bounds: this client has no map position API")
        end
        return
    end
    local n = 0
    for _ in pairs(db.mapBounds or {}) do n = n + 1 end
    rec.Print(("map bounds: %d maps stored (client %s)%s"):format(n, tostring(db.mapBoundsBuild or "-"),
        bounds.running and (", collecting %d/%d"):format(bounds.done, bounds.total) or ""))
    local map = Safe(C_Map.GetBestMapForUnit("player"))
    if not map then
        rec.Print("current map: none (the game gives no map here)")
        return
    end
    local e = db.mapBounds and db.mapBounds[map]
    local stored = e ~= nil
    if not e and C_Map.GetWorldPosFromMapPos and CreateVector2D then e = boundsEntry(map) end
    if not e then
        rec.Print(("current map %d %s: the game gives no world position for it"):format(map, mapName(map) or ""))
        return
    end
    rec.Print(("current map %d %s: instance %s, (0,0) at %.1f %.1f, (1,1) at %.1f %.1f, %.0f x %.0f yd%s")
        :format(map, e[6] or mapName(map) or "", tostring(e[1]), e[2], e[3], e[4], e[5],
            math.abs(e[3] - e[5]), math.abs(e[2] - e[4]), stored and "" or " (not stored yet)"))
end)
