-- Glide extras (0.10): training, talent points, flight paths, consumables, target extras, corpse.
-- Read-only like the rest of the addon: it only looks at game state and streams it on the strip.
--
-- Core keys (every frame, short):
--   tp  unspent talent points ("" = unknown)
--   tt  target tapped by someone else (1/0)        tq  target is a mob for an unfinished quest objective (1/0/"")
--   cx,cy  corpse position on the current map, x 10000 (only while dead or a ghost and the corpse is on this map)
--   rz  seconds of Resurrection Sickness left (only when present)
--   aw  map:x:y of the waypoint RestedXP's arrow points at (0.13.1)
--   wz  height (world z) in half yards (0.14)    fy  the current step's flight destination, RestedXP's .fly (0.14)
--   fd  where the flight you're on goes, the taxi map node you clicked (0.14)
-- List pages (each chunk carries n = chunk count and h = hash of all chunks, so the companion knows when it has
-- the complete list):
--   P0..  missing trainable spells: spell ids from RestedXP's training list (class + race) at or below the
--         player's level that the character doesn't know. pl = level the list was built for, k = ids checked.
--   F0..  known flight paths: taxi node ids (taxi map scans this addon saved + RestedXP's own list)
--   C0..  consumables in bags: kind:itemID:count:reqLevel:name
--   N0..  talents (0.13): <tab><tier><column>:rank:maxRank:name, every talent of the class
--   V0..  RestedXP steps the player skipped in the current guide (0.13): step indices
--   D0..  vendor treasure near the player (0.13): v = vendor index in Data/VendorTreasures.lua (0 = none), entries
--         in Gear.lua's item format (potions: id:POTION:::0:1:req:1:name:)
-- Page X (0.13): RestedXP settings that decide which steps it shows: ss solo self-found, gq group quests (1/0)
--         kinds f food, d drink, b food+drink, h bandage, p healing potion, m mana potion, x poison,
--               w weapon stone/oil, a ammo, s soul shard, r reagent
local _, ns = ...

local Safe = ns.Safe
local floor = math.floor
local RESURRECTION_SICKNESS = 15007
local CHUNK = 96                      -- list text per page (page max 118 minus "n=..;h=.....;p=" and the extra)

local clean = ns.Cleaner("[;=|:,\n\r]", " ")      -- a field of a list entry

-- lists[letter] = { chunks = {...}, key = "p", extra = "pl=12;" }
local lists = {}
local function setList(letter, key, entries, extra)
    extra = extra or ""
    -- the extra header fields ride on every page of the list: leave room for them, or the page is cut, its hash
    -- never matches and the companion never takes the list (training lists of 15+ spells did that)
    local chunks = ns.ChunkList(entries, CHUNK - #extra)
    lists[letter] = { chunks = chunks, key = key, h = ns.Hash(table.concat(chunks, ",")), extra = extra }
end
ns.ExtraLists = lists

table.insert(ns.pageHooks, function(builders)
    for letter, l in pairs(lists) do
        ns.ListPages(builders, letter, l.chunks, ("n=%d;h=%d;%s%s="):format(#l.chunks, l.h, l.extra, l.key))
    end
end)

local knows = ns.KnowsSpell

-- ---------------------------------------------------------------------------------------------
-- training: RestedXP's own per-level training list (global RXP, its addon table).
-- RestedXP matches that list to the trainer by spell NAME and rank (its ids are not always the id the game
-- teaches: Forever's list has 1424 for Dual Wield, the learned spell is 674). So a listed spell counts as known
-- when the id is known OR the spellbook has a spell of that name at that rank or higher. Ids that don't resolve
-- to a spell name (skill line ids, spell data not loaded yet) can't be checked and are left out (u = count).
-- ---------------------------------------------------------------------------------------------
ns.trainState = "unknown"

local function rankOf(sub)
    return tonumber((Safe(sub) or ""):match("(%d+)")) or 0
end

local function spellNameRank(id)
    local name, sub
    if C_Spell and C_Spell.GetSpellInfo then
        local ok, info = pcall(C_Spell.GetSpellInfo, id)
        name = ok and type(info) == "table" and Safe(info.name) or nil
    end
    if not name and GetSpellInfo then
        local ok, n = pcall(GetSpellInfo, id)
        name = ok and Safe(n) or nil
    end
    if C_Spell and C_Spell.GetSpellSubtext then
        local ok, t = pcall(C_Spell.GetSpellSubtext, id); sub = ok and t or nil
    elseif GetSpellSubtext then
        local ok, t = pcall(GetSpellSubtext, id); sub = ok and t or nil
    end
    return name, rankOf(sub)
end

-- name -> highest rank in the spellbook (rank 0 = no rank text), and name -> that rank's spell id (when the client
-- says it: the new-at-the-trainer window, Training.lua, shows it beside the new rank)
local function spellbookRanks()
    local book, ids = {}, {}
    local function add(name, sub, id)
        name = Safe(name)
        if name then
            local r = rankOf(sub)
            if not book[name] or r > book[name] then
                book[name] = r
                ids[name] = Safe(id)
            end
        end
    end
    if C_SpellBook and C_SpellBook.GetNumSpellBookSkillLines and C_SpellBook.GetSpellBookItemInfo then
        local bank = (Enum and Enum.SpellBookSpellBank and Enum.SpellBookSpellBank.Player) or 0
        local okN, n = pcall(C_SpellBook.GetNumSpellBookSkillLines)
        for i = 1, (okN and type(n) == "number") and n or 0 do
            local okL, line = pcall(C_SpellBook.GetSpellBookSkillLineInfo, i)
            if okL and type(line) == "table" then
                local off, num = Safe(line.itemIndexOffset) or 0, Safe(line.numSpellBookItems) or 0
                for slot = off + 1, off + num do
                    local ok, info = pcall(C_SpellBook.GetSpellBookItemInfo, slot, bank)
                    if ok and type(info) == "table" then add(info.name, info.subName, info.spellID or info.actionID) end
                end
            end
        end
    elseif GetNumSpellTabs and GetSpellTabInfo and GetSpellBookItemName then
        for t = 1, GetNumSpellTabs() do
            local _, _, off, num = GetSpellTabInfo(t)
            for slot = (off or 0) + 1, (off or 0) + (num or 0) do
                local ok, name, sub = pcall(GetSpellBookItemName, slot, BOOKTYPE_SPELL or "spell")
                local id
                if GetSpellBookItemInfo then
                    local okI, _, sid = pcall(GetSpellBookItemInfo, slot, BOOKTYPE_SPELL or "spell")
                    id = okI and sid or nil
                end
                if ok then add(name, sub, id) end
            end
        end
    end
    return book, ids
end

-- the spell ids RestedXP lists for this class and race at or below `level`, each once, and id -> the level it's
-- listed at (the level the trainer teaches it)
local function trainingIDs(list, level)
    local _, class = UnitClass("player")
    local _, race = UnitRace("player")
    local out, seen, levelOf = {}, {}, {}
    for _, key in ipairs({ Safe(class), Safe(race) }) do
        local byLevel = key and list[key]
        if type(byLevel) == "table" then
            for lv, ids in pairs(byLevel) do
                if type(lv) == "number" and lv <= level and type(ids) == "table" then
                    for _, id in ipairs(ids) do
                        if type(id) == "number" and not seen[id] then
                            seen[id] = true
                            out[#out + 1] = id
                            levelOf[id] = lv
                        end
                    end
                end
            end
        end
    end
    return out, levelOf
end

-- "known", "missing", or "unresolved" (no spell name yet: its data is requested once, a later pass checks it)
local requested = {}
local function trainingStatus(id, book)
    if knows(id) then return "known" end
    local name, rank = spellNameRank(id)
    if not name then
        if not requested[id] and C_Spell and C_Spell.RequestLoadSpellData then
            requested[id] = true
            pcall(C_Spell.RequestLoadSpellData, id)
        end
        return "unresolved"
    end
    return (book[name] and book[name] >= rank) and "known" or "missing"
end

local function buildTraining()
    local R = _G.RXP
    local list = R and R.defaultSpellList
    local level = Safe(UnitLevel("player"))
    if type(list) ~= "table" or not level then
        ns.trainState = "no RestedXP training list"
        lists.P = nil
        return
    end
    local ids, levelOf = trainingIDs(list, level)
    local book, bookIDs = spellbookRanks()
    local missing, checked, unresolved = {}, 0, 0
    for _, id in ipairs(ids) do
        local status = trainingStatus(id, book)
        if status == "unresolved" then
            unresolved = unresolved + 1
        else
            checked = checked + 1
            if status == "missing" then missing[#missing + 1] = id end
        end
    end
    table.sort(missing)
    local e = {}
    for i, id in ipairs(missing) do e[i] = tostring(id) end
    setList("P", "p", e, ("pl=%d;k=%d;u=%d;"):format(level, checked, unresolved))
    ns.trainState = ("%d checked, %d missing, %d not checkable"):format(checked, #missing, unresolved)
    -- for the new-at-the-trainer window (Training.lua): each missing spell, the level it's taught at, and the id of
    -- the rank of it the character knows now (nil for a new spell)
    local out = {}
    for _, id in ipairs(missing) do
        local name, rank = spellNameRank(id)
        out[#out + 1] = { id = id, level = levelOf[id], name = name, rank = rank, known = name and bookIDs[name] or nil }
    end
    ns.training = { level = level, spells = out }
    for _, fn in ipairs(ns.trainingHooks) do ns.Try("training window", function() fn(ns.training) end) end
end
ns.trainingHooks = {}                -- fn(ns.training), after each rebuild of the missing-spells list

-- ---------------------------------------------------------------------------------------------
-- talent points
-- ---------------------------------------------------------------------------------------------
local function unspentTalents()
    if UnitCharacterPoints then
        local ok, n = pcall(UnitCharacterPoints, "player")
        n = ok and Safe(n)
        if type(n) == "number" then return n end
    end
    local level = Safe(UnitLevel("player"))
    if not (level and GetNumTalentTabs and GetNumTalents and GetTalentInfo) then return nil end
    local okT, tabs = pcall(GetNumTalentTabs)
    if not okT or type(tabs) ~= "number" or tabs == 0 then return nil end
    local spent = 0
    for t = 1, tabs do
        local okN, n = pcall(GetNumTalents, t)
        for i = 1, (okN and type(n) == "number") and n or 0 do
            local ok, _, _, _, _, rank = pcall(GetTalentInfo, t, i)
            if ok and type(Safe(rank)) == "number" then spent = spent + rank end
        end
    end
    return math.max(0, level - 9 - spent)
end

-- ---------------------------------------------------------------------------------------------
-- flight paths
-- ---------------------------------------------------------------------------------------------
local function fpStore()
    local perChar = ns.LinkSection("fp")
    local key = (Safe(UnitName("player")) or "?") .. "-" .. (Safe(GetRealmName and GetRealmName()) or "?")
    perChar[key] = perChar[key] or {}
    return perChar[key]
end

local function buildFlights()
    local known = {}
    for id in pairs(fpStore()) do if type(id) == "number" then known[id] = true end end
    local rx = type(_G.RXPCData) == "table" and _G.RXPCData.flightPaths
    if type(rx) == "table" then
        for id, v in pairs(rx) do if type(id) == "number" and v then known[id] = true end end
    end
    local ids = {}
    for id in pairs(known) do ids[#ids + 1] = id end
    table.sort(ids)
    for i, id in ipairs(ids) do ids[i] = tostring(id) end
    setList("F", "f", ids)
end

local function scanTaxiMap()
    local store, new = fpStore(), 0
    local mapID = GetTaxiMapID and Safe(GetTaxiMapID())
    if C_TaxiMap and C_TaxiMap.GetAllTaxiNodes and mapID then
        local ok, nodes = pcall(C_TaxiMap.GetAllTaxiNodes, mapID)
        local S = Enum and Enum.FlightPathState or {}
        local cur, reach = S.Current or 0, S.Reachable or 1
        for _, n in ipairs(ok and nodes or {}) do
            local id, st = Safe(n.nodeID), Safe(n.state)
            if id and (st == cur or st == reach) then
                if not store[id] then new = new + 1 end      -- (no ring event: list F says it, and a full taxi
                                                              -- map's burst would push real events out of the ring)
                store[id] = clean(Safe(n.name), 40)
            end
        end
    end
    buildFlights()
    return new
end

-- ---------------------------------------------------------------------------------------------
-- consumables
-- ---------------------------------------------------------------------------------------------
local SPELL_KIND = { ["Food"] = "f", ["Drink"] = "d", ["Refreshment"] = "b", ["First Aid"] = "h",
                     ["Healing Potion"] = "p", ["Restore Mana"] = "m", ["Sharpen Blade"] = "w",
                     ["Enhance Blunt Weapon"] = "w", ["Mana Oil"] = "w", ["Wizard Oil"] = "w" }
local SOUL_SHARD = 6265
local function itemKind(id)
    if id == SOUL_SHARD then return "s" end
    local classID = select(6, C_Item.GetItemInfoInstant(id))
    if classID == 6 then return "a" end                       -- projectile
    local sname = C_Item.GetItemSpell and Safe(C_Item.GetItemSpell(id))
    if sname then
        if SPELL_KIND[sname] then return SPELL_KIND[sname] end
        if sname:find("Poison$") and not sname:find("^Cure") then return "x" end
        if sname:find("Healing Potion") then return "p" end
    end
    if classID == 5 then return "r" end                       -- reagent
    return nil
end

local function buildConsumables()
    local counts, order = {}, {}
    for _, _, info in ns.BagItems() do
        local id = Safe(info.itemID)
        if id then
            if not counts[id] then order[#order + 1] = id; counts[id] = 0 end
            counts[id] = counts[id] + (Safe(info.stackCount) or 1)
        end
    end
    local e = {}
    for _, id in ipairs(order) do
        local ok, kind = pcall(itemKind, id)
        if ok and kind then
            local name, _, _, _, req = C_Item.GetItemInfo(id)
            e[#e + 1] = ("%s:%d:%d:%s:%s"):format(kind, id, counts[id], Safe(req) or "", clean(Safe(name) or "", 24))
        end
    end
    table.sort(e)
    setList("C", "c", e)
end

-- ---------------------------------------------------------------------------------------------
-- talents: every talent with its position, rank and max rank (RestedXP's builds name talents by tab,tier,column)
-- ---------------------------------------------------------------------------------------------
local function buildTalents()
    if not (GetNumTalentTabs and GetNumTalents and GetTalentInfo) then return end
    local okT, tabs = pcall(GetNumTalentTabs)
    if not okT or type(tabs) ~= "number" then return end
    local e = {}
    for t = 1, tabs do
        local okN, n = pcall(GetNumTalents, t)
        for i = 1, (okN and type(n) == "number") and n or 0 do
            local ok, name, _, tier, column, rank, maxRank = pcall(GetTalentInfo, t, i)
            name, tier, column = ok and Safe(name), ok and Safe(tier), ok and Safe(column)
            if name and tier and column then
                e[#e + 1] = ("%d%d%d:%d:%d:%s"):format(t, tier, column, Safe(rank) or 0, Safe(maxRank) or 0,
                    clean(name, 22))
            end
        end
    end
    if #e > 0 then setList("N", "t", e) end
end

-- ---------------------------------------------------------------------------------------------
-- RestedXP: the settings that change which steps it shows, and the steps the player skipped in this guide
-- ---------------------------------------------------------------------------------------------
local rxpSettings = ""
local function buildRXP()
    local p = ns.RXPProfile()
    rxpSettings = p and ("ss=%d;gq=%d"):format(p.soloSelfFound and 1 or 0, p.enableGroupQuests and 1 or 0) or ""
    local skip = type(_G.RXPCData) == "table" and _G.RXPCData.stepSkip
    local ids = {}
    if type(skip) == "table" then
        for i, v in pairs(skip) do if type(i) == "number" and v then ids[#ids + 1] = i end end
    end
    table.sort(ids)
    for i = #ids, 81, -1 do ids[i] = nil end
    for i, v in ipairs(ids) do ids[i] = tostring(v) end
    setList("V", "v", ids)
end

-- ---------------------------------------------------------------------------------------------
-- vendor treasures (Data/VendorTreasures.lua): near one of those vendors on this map, list what it sells, gear in
-- Gear.lua's format (so the companion scores it like quest rewards) and potions as id:POTION:::0:1:req:1:name:
-- ---------------------------------------------------------------------------------------------
local vendorAt, NEAR = 0, 0.06          -- vendor index (0 = none); about 6% of the map's height
local function buildVendor()
    local list = ns.VENDOR_TREASURES
    local map, px, py = ns.PlayerMapPosition()
    local fac = (Safe(UnitFactionGroup("player")) or ""):sub(1, 1)
    local best, bd = 0, NEAR * NEAR
    for i, v in ipairs(px and py and list or {}) do
        if v[1] == map and (v[6] == "N" or v[6] == fac) then
            local dx, dy = (v[2] / 100 - px) * 1.5, v[3] / 100 - py
            if dx * dx + dy * dy < bd then best, bd = i, dx * dx + dy * dy end
        end
    end
    local e, missing = {}, false
    for _, id in ipairs(best > 0 and list[best][7] or {}) do
        local link = select(2, C_Item.GetItemInfo(id))
        local entry, miss = nil, not link
        if link and ns.GearSummarize then entry, miss = ns.GearSummarize("v", link, CHUNK - 6) end
        if not entry and link then
            local name, _, _, _, req, _, _, _, _, _, _, classID = C_Item.GetItemInfo(id)
            if Safe(classID) == 0 then                                   -- consumable: potions, elixirs
                entry = name and ("v:%d:POTION:::0:1:%d:1:%s:"):format(id, Safe(req) or 0, clean(Safe(name), 24))
            end
        end
        if miss and C_Item.RequestLoadItemDataByID then pcall(C_Item.RequestLoadItemDataByID, id) end
        missing = missing or miss
        if entry and #entry <= CHUNK - 6 then e[#e + 1] = entry end       -- room for the v= header
    end
    if missing then best = 0 end          -- item data still loading: send the vendor once it's complete
    if best ~= vendorAt or best > 0 then
        vendorAt = best
        setList("D", "d", e, ("v=%d;"):format(best))
    end
end

table.insert(ns.pageHooks, function(builders)
    builders["X"] = rxpSettings ~= "" and function() return rxpSettings end or nil
end)

-- ---------------------------------------------------------------------------------------------
-- target: is it a mob for an unfinished quest objective? (tooltip quest lines; cached per target)
-- ---------------------------------------------------------------------------------------------
local tq, tqGuid = "", nil
local function questTarget()
    if not UnitExists("target") or UnitIsPlayer("target") then return "" end
    if UnitIsQuestBoss then
        local ok, b = pcall(UnitIsQuestBoss, "target")
        if ok and Safe(b) then return 1 end
    end
    if not (C_TooltipInfo and C_TooltipInfo.GetUnit) then return "" end
    local ok, data = pcall(C_TooltipInfo.GetUnit, "target")
    if not ok or type(data) ~= "table" or type(data.lines) ~= "table" then return "" end
    local T = Enum and Enum.TooltipDataLineType or {}
    local objective = T.QuestObjective or 8
    for _, line in ipairs(data.lines) do
        if Safe(line.type) == objective then
            local txt = Safe(line.leftText) or ""
            local have, need = txt:match("(%d+)%s*/%s*(%d+)")
            if not have or tonumber(have) < tonumber(need) then return 1 end
        end
    end
    return 0
end

local function refreshTarget()
    local guid = UnitExists("target") and Safe(UnitGUID("target")) or nil
    if guid ~= tqGuid or not InCombatLockdown() then
        local ok, v = pcall(questTarget)
        tq, tqGuid = ok and v or "", guid
    end
end

-- ---------------------------------------------------------------------------------------------
-- core keys
-- ---------------------------------------------------------------------------------------------
-- the destination of RestedXP's .fly element on the current step, unless done
local function currentFly()
    local idx, guide = ns.RXPStep()
    local step = idx and guide.steps and guide.steps[idx]
    for _, el in ipairs(step and step.elements or {}) do
        if el.tag == "fly" and el.location and not el.completed then return clean(el.location, 24) end
    end
end

-- where the flight you took goes (the taxi map's button you clicked), while you're on it
local taxiDest
if hooksecurefunc and _G.TakeTaxiNode then
    hooksecurefunc("TakeTaxiNode", function(i)
        local ok, name = pcall(TaxiNodeName, i)
        taxiDest = ok and Safe(name) and clean(name, 32) or nil
    end)
end

local talentCache, talentAt = nil, 0
function ns.CoreExtras(put)
    local now = GetTime()
    if now - talentAt > 5 then talentAt = now; local ok, n = pcall(unspentTalents); talentCache = ok and n or nil end
    put("tp", talentCache or "")
    -- the waypoint RestedXP's arrow points at now: on a step with several gotos it moves on as each is reached,
    -- while the step page lists them all
    local af = _G.RXP and RXP.arrowFrame
    local el = af and af.element
    if el and el.x and el.y and not (el.step and el.step.active == false) then
        put("aw", ("%s:%.1f:%.1f"):format(Safe(el.zone) or "", el.x, el.y))
    end
    -- height (world z, in half yards): which floor you're on, a city street or the mountain over it, a bridge or
    -- the river under it, for the walking route
    local okp, _, _, wz = pcall(UnitPosition, "player")
    wz = okp and Safe(wz)
    if type(wz) == "number" then put("wz", floor(wz * 2 + 0.5)) end
    -- the current step's flight (RestedXP's .fly destination) while it's still to do
    local fly = currentFly()
    if fly then put("fy", fly) end
    if taxiDest and UnitOnTaxi("player") then put("fd", taxiDest) end
    if UnitExists("target") then
        local okT, tap = pcall(UnitIsTapDenied, "target")
        if not okT then tap = nil else tap = Safe(tap) end
        put("tt", tap == nil and "" or (tap and 1 or 0))
        put("tq", tq)
    end
    if UnitIsDeadOrGhost("player") and C_DeathInfo and C_DeathInfo.GetCorpseMapPosition then
        local map = Safe(C_Map.GetBestMapForUnit("player"))
        local ok, p = pcall(C_DeathInfo.GetCorpseMapPosition, map)
        if ok and p then
            local x, y = p:GetXY()
            x, y = Safe(x), Safe(y)
            if x and y and (x > 0 or y > 0) then put("cx", floor(x * 10000)); put("cy", floor(y * 10000)) end
        end
    end
    if C_UnitAuras and C_UnitAuras.GetPlayerAuraBySpellID then
        local ok, a = pcall(C_UnitAuras.GetPlayerAuraBySpellID, RESURRECTION_SICKNESS)
        local exp = ok and a and Safe(a.expirationTime)
        if exp and exp > 0 then put("rz", math.max(0, floor(exp - now))) end
    end
end

-- ---------------------------------------------------------------------------------------------
-- events
-- ---------------------------------------------------------------------------------------------
local pending = {}
local function mark(what) pending[what] = true end
-- what each game event (on the shared bus, Core.lua ns.On) makes stale; the work waits for the next poll
local EVENTS = {
    PLAYER_LOGIN = { "P", "F", "C", "N" }, PLAYER_LEVEL_UP = { "P", "N" }, LEARNED_SPELL_IN_SKILL_LINE = { "P" },
    CHARACTER_POINTS_CHANGED = { "N" },
    SPELLS_CHANGED = { "P" }, SPELL_DATA_LOAD_RESULT = { "P" }, BAG_UPDATE_DELAYED = { "C" }, GET_ITEM_INFO_RECEIVED = { "C" },
    TAXIMAP_OPENED = { "taxi" }, PLAYER_TARGET_CHANGED = { "target" }, QUEST_LOG_UPDATE = { "target" },
    UNIT_QUEST_LOG_CHANGED = { "target" },
}
for e, stale in pairs(EVENTS) do
    ns.On(e, function()
        for _, w in ipairs(stale) do mark(w) end
        if e == "PLAYER_TARGET_CHANGED" then tqGuid = false end      -- force a fresh read for the new target
    end)
end

-- batch the work: at most once per 0.5 s, and never on the frame that raised the event
local function flush()
    if pending.taxi then pending.taxi = nil; ns.Try("flight paths", scanTaxiMap) end
    if pending.P then pending.P = nil; ns.Try("training", buildTraining) end
    if pending.F then pending.F = nil; ns.Try("flight paths", buildFlights) end
    if pending.C then pending.C = nil; ns.Try("consumables", buildConsumables) end
    if pending.target then pending.target = nil; ns.Try("target", refreshTarget) end
    if pending.N then pending.N = nil; ns.Try("talents", buildTalents) end
end
ns.ExtrasFlush = flush
local acc, rxpAcc = 0, 3
-- ns.ExtrasPoll(dt): the batching clock, run every frame the game draws (tests call it with the seconds to pass)
function ns.ExtrasPoll(dt)
    acc, rxpAcc = acc + dt, rxpAcc + dt
    if acc < 0.5 then return end
    acc = 0
    if rxpAcc >= 3 then rxpAcc = 0; ns.Try("RestedXP", buildRXP); ns.Try("vendor", buildVendor) end   -- no events for these: poll
    flush()
end
CreateFrame("Frame"):SetScript("OnUpdate", function(_, dt) ns.ExtrasPoll(dt) end)

-- /glide extras: what this module sees right now
ns.Command("extras", "extras - training, talents, flight paths, consumables and the other lists the strip sends", function()
    ns.Print(("training: %s; talent points unspent: %s"):format(ns.trainState, tostring(talentCache)))
    print(("  RestedXP settings: %s"):format(rxpSettings ~= "" and rxpSettings or "(RestedXP not loaded)"))
    for _, letter in ipairs({ "P", "F", "C", "N", "V", "D" }) do
        local l = lists[letter]
        print(("  %s: %s"):format(letter, l and table.concat(l.chunks, ",") or "(not built)"))
    end
    print(("  target quest mob: %s"):format(tostring(tq)))
end)
