-- GlideLink gear: equipped items, equippable items in bags, and quest reward choices, with their
-- stats, so the companion can spot upgrades. Built on bag/equipment/quest events, not every frame.
-- Entry: key:itemID:equipLoc:classID:subclassID:ilvl:quality:reqLevel:usable:name:stats
--   key   = slot number (worn), bag.slot (bags) or choice index (rewards)
--   stats = CODE value pairs joined by "/" (codes match companion/gear.py), e.g. ST5/SA3/AR120/DP7.4/WS2.6
local _, ns = ...

local Safe = ns.Safe
local floor = math.floor

local STAT_CODE = {
    ITEM_MOD_STRENGTH_SHORT = "ST", ITEM_MOD_AGILITY_SHORT = "AG", ITEM_MOD_STAMINA_SHORT = "SA",
    ITEM_MOD_INTELLECT_SHORT = "IN", ITEM_MOD_SPIRIT_SHORT = "SI", RESISTANCE0_NAME = "AR",
    ITEM_MOD_DAMAGE_PER_SECOND_SHORT = "DP", ITEM_MOD_ATTACK_POWER_SHORT = "AP",
    ITEM_MOD_RANGED_ATTACK_POWER_SHORT = "RA", ITEM_MOD_HIT_RATING_SHORT = "HT", ITEM_MOD_CRIT_RATING_SHORT = "CR",
    ITEM_MOD_HIT_SPELL_RATING_SHORT = "HS", ITEM_MOD_CRIT_SPELL_RATING_SHORT = "CS",
    ITEM_MOD_SPELL_HEALING_DONE = "HL", ITEM_MOD_HEALTH_REGEN_SHORT = "HR", ITEM_MOD_POWER_REGEN0_SHORT = "MR",
    ITEM_MOD_DEFENSE_SKILL_RATING_SHORT = "DF", ITEM_MOD_DODGE_RATING_SHORT = "DG", ITEM_MOD_PARRY_RATING_SHORT = "PA",
}
-- "Equip:" lines that GetItemStats doesn't report on Classic-style items (enUS)
local TOOLTIP = {
    { "critical strike with spells by (%d+)%%", "CS" }, { "hit with spells by (%d+)%%", "HS" },
    { "critical strike by (%d+)%%", "CR" }, { "chance to hit by (%d+)%%", "HT" },
    { "healing done by spells and effects by up to (%d+)", "HL" },
    { "damage and healing done by magical spells and effects by up to (%d+)", "SP" },
    { "damage done by Fire spells and effects by up to (%d+)", "FI" },
    { "damage done by Frost spells and effects by up to (%d+)", "FR" },
    { "damage done by Arcane spells and effects by up to (%d+)", "AC" },
    { "damage done by Nature spells and effects by up to (%d+)", "NA" },
    { "damage done by Shadow spells and effects by up to (%d+)", "SH" },
    { "%+(%d+) ranged Attack Power", "RA" }, { "%+(%d+) Attack Power", "AP" },
    { "Restores (%d+) mana per 5 sec", "MR" }, { "Restores (%d+) health every 5 sec", "HR" },
    { "Increased Defense %+(%d+)", "DF" }, { "chance to dodge an attack by (%d+)%%", "DG" },
    { "chance to parry an attack by (%d+)%%", "PA" },
}

local clean = ns.Cleaner("[;=|:,/\n\r]", " ")    -- a field of an entry (and "/", which joins the stats)
local CHUNK = 112                                 -- entry text per page (page max 118 minus "g=" and room)

-- Retail-era clients moved these into C_Item (the globals are gone on Forever)
local GetItemInfo = (C_Item and C_Item.GetItemInfo) or _G.GetItemInfo
local GetItemInfoInstant = (C_Item and C_Item.GetItemInfoInstant) or _G.GetItemInfoInstant
local GetItemStats = (C_Item and C_Item.GetItemStats) or _G.GetItemStats
ns.gearErrors = {}

-- equip slots that hold no gear worth comparing
local NOT_GEAR = { [""] = true, INVTYPE_BAG = true, INVTYPE_AMMO = true, INVTYPE_QUIVER = true,
                   INVTYPE_NON_EQUIP_IGNORE = true }

-- the item's stats by code: what GetItemStats reports, then what only the tooltip says
local function itemStats(link)
    local stats = {}
    local raw = (GetItemStats and GetItemStats(link)) or {}
    for k, v in pairs(raw) do
        local code = STAT_CODE[k]
        v = Safe(v)
        if code and v and v ~= 0 then stats[code] = v end
    end
    if C_TooltipInfo and C_TooltipInfo.GetHyperlink then
        local ok, data = pcall(C_TooltipInfo.GetHyperlink, link)
        for _, line in ipairs(ok and data and data.lines or {}) do
            local left, right = Safe(line.leftText), Safe(line.rightText)
            if left then
                for _, p in ipairs(TOOLTIP) do
                    local n = tonumber(left:match(p[1]))
                    if n and not stats[p[2]] then stats[p[2]] = n; break end
                end
                local ar = tonumber(left:match("^(%d+) Armor$"))
                if ar and not stats.AR then stats.AR = ar end
                local dps = tonumber(left:match("%(([%d%.]+) damage per second%)"))
                if dps and not stats.DP then stats.DP = dps end
            end
            local speed = right and tonumber(right:match("Speed ([%d%.]+)"))
            if speed then stats.WS = speed end
        end
    end
    return stats
end

-- the stats an entry keeps when they don't all fit its page, most worth keeping first (a weapon's speed and damage,
-- armor, the main stats); codes not listed go before any of these
local KEEP = { "DP", "WS", "AR", "ST", "AG", "SA", "IN", "SI", "AP", "RA", "SP", "HL", "CR", "HT", "CS", "HS", "MR",
               "HR", "DF", "DG", "PA", "FI", "FR", "AC", "NA", "SH" }
local KEEP_RANK = {}
for i, code in ipairs(KEEP) do KEEP_RANK[code] = i end

-- stats as the entry carries them: CODE value pairs, sorted, joined by "/", in at most `room` bytes. An entry longer
-- than its page would be cut, never match its list's hash, and the companion would never take the list, so the
-- least useful stats are dropped until the rest fit.
local function statsText(stats, room)
    local parts = {}
    for code, v in pairs(stats) do
        parts[#parts + 1] = code .. (v == floor(v) and tostring(v) or ("%.1f"):format(v))
    end
    local text = table.concat(parts, "/")
    if #text > room then
        table.sort(parts, function(a, b)
            local ra, rb = KEEP_RANK[a:sub(1, 2)] or 99, KEEP_RANK[b:sub(1, 2)] or 99
            if ra ~= rb then return ra < rb end
            return a < b
        end)
        while #parts > 0 and #table.concat(parts, "/") > room do parts[#parts] = nil end
    end
    table.sort(parts)
    return table.concat(parts, "/")
end

-- 1, or 0 when the game says this character can't use the item
local function usableFlag(link)
    if C_PlayerInfo and C_PlayerInfo.CanUseItem then
        local ok, can = pcall(C_PlayerInfo.CanUseItem, (GetItemInfoInstant(link)))
        if ok and can == false then return 0 end
    end
    return 1
end

-- the entry without its key and stats, and the stats, cached per link (false: not gear); nil, true while the item
-- isn't loaded yet
local cache = {}
local function itemEntry(link)
    if cache[link] then return cache[link] end    -- not gear (false) is asked again: one GetItemInfo
    local name, _, quality, ilvl, req, _, _, _, loc, _, _, classID, subID = GetItemInfo(link)
    if not name then return nil, true end              -- not cached yet; GET_ITEM_INFO_RECEIVED retries
    if not loc or NOT_GEAR[loc] then
        cache[link] = false
        return false
    end
    local stats = itemStats(link)
    local usable = usableFlag(link)
    local id = GetItemInfoInstant(link)
    cache[link] = { ("%s:%s:%s:%s:%s:%s:%s:%s:%s"):format(id or 0, loc, classID or "", subID or "", ilvl or 0,
        quality or 0, req or 0, usable, clean(name, 24)), stats }
    return cache[link]
end

-- key:entry in at most `max` bytes (default: one page's chunk), or nil (and true while the item isn't loaded)
local function summarize(key, link, max)
    link = Safe(link)
    if not link then return nil end
    local e, missing = itemEntry(link)
    if not e then return nil, missing end
    local head = key .. ":" .. e[1] .. ":"
    return head .. statsText(e[2], (max or CHUNK) - #head)
end

ns.GearSummarize = summarize          -- Extras.lua: vendor treasure items, in the same format

local function hash(chunks) return ns.Hash(table.concat(chunks, ",")) end
local function chunkList(entries) return ns.ChunkList(entries, CHUNK) end

ns.gear = { W = { "" }, I = { "" }, R = {}, rq = 0 }
local pending = false

local function build()
    pending = false
    local worn, bags, missing = {}, {}, false
    for slot = 1, 18 do
        if slot ~= 4 then
            local e, miss = summarize(slot, GetInventoryItemLink("player", slot))
            if e then worn[#worn + 1] = e end
            missing = missing or miss
        end
    end
    for bag, slot, info in ns.BagItems() do
        if info.hyperlink then
            local e, miss = summarize(bag .. "." .. slot, info.hyperlink)
            if e then bags[#bags + 1] = e end
            missing = missing or miss
        end
    end
    ns.gear.W, ns.gear.I = chunkList(worn), chunkList(bags)
    ns.gear.Wh, ns.gear.Ih = hash(ns.gear.W), hash(ns.gear.I)
    if missing then C_Timer.After(1, ns.GearDirty) end
end

function ns.GearDirty()
    if pending then return end
    pending = true
    C_Timer.After(0.3, function()
        local ok, err = ns.Try("gear", build)
        if not ok then pending = false; ns.gearErrors[#ns.gearErrors + 1] = tostring(err) end
    end)
end

local function rewards(show)
    local list = {}
    if show then
        for i = 1, (GetNumQuestChoices and GetNumQuestChoices() or 0) do
            local e = summarize(i, GetQuestItemLink("choice", i))
            if e then list[#list + 1] = e end
        end
    end
    ns.gear.R = #list > 0 and chunkList(list) or {}
    ns.gear.Rh = hash(ns.gear.R)
    ns.gear.rq = show and #list > 0 and (GetQuestID and GetQuestID() or 0) or 0
end

for _, e in ipairs({ "PLAYER_LOGIN", "BAG_UPDATE_DELAYED", "PLAYER_EQUIPMENT_CHANGED", "GET_ITEM_INFO_RECEIVED",
    "PLAYER_LEVEL_UP" }) do
    ns.On(e, function() ns.GearDirty() end)
end
ns.On("QUEST_COMPLETE", function() rewards(true) end)       -- the reward choices on screen
ns.On("QUEST_FINISHED", function() rewards(false) end)

-- ---------------------------------------------------------------------------------------------
-- trained weapon / armor skills, so the coach never suggests gear you can't use yet.
-- Core keys: sw = bitmask of weapon item subclasses trained (bit n = subclass n), sa = armor subclasses.
-- Empty = unknown (no API answered). Page K carries skill ranks when the client still has skill lines.
-- ---------------------------------------------------------------------------------------------
local WEAPON_SPELLS = { [0] = 196, [1] = 197, [2] = 264, [3] = 266, [4] = 198, [5] = 199, [6] = 200, [7] = 201,
                        [8] = 202, [10] = 227, [13] = 15590, [15] = 1180, [16] = 2567, [18] = 5011, [19] = 5009 }
local ARMOR_SPELLS = { [1] = 9078, [2] = 9077, [3] = 8737, [4] = 750, [6] = 9116 }
local WEAPON_LINES = { ["Axes"] = 0, ["One-Handed Axes"] = 0, ["Two-Handed Axes"] = 1, ["Bows"] = 2, ["Guns"] = 3,
                       ["Maces"] = 4, ["One-Handed Maces"] = 4, ["Two-Handed Maces"] = 5, ["Polearms"] = 6,
                       ["Swords"] = 7, ["One-Handed Swords"] = 7, ["Two-Handed Swords"] = 8, ["Staves"] = 10,
                       ["Fist Weapons"] = 13, ["Daggers"] = 15, ["Thrown"] = 16, ["Crossbows"] = 18, ["Wands"] = 19 }
local ARMOR_LINES = { ["Cloth"] = 1, ["Leather"] = 2, ["Mail"] = 3, ["Plate Mail"] = 4, ["Plate"] = 4, ["Shield"] = 6 }

local knows = ns.KnowsSpell
local RANKS_MAX = 115            -- page K's text after "sk=" (page max 118)

function ns.BuildSkills()
    local w, a, ranks = 0, 0, {}
    local function setbit(mask, n) return (floor(mask / 2 ^ n) % 2 == 1) and mask or mask + 2 ^ n end
    for sub, id in pairs(WEAPON_SPELLS) do if knows(id) then w = setbit(w, sub) end end
    for sub, id in pairs(ARMOR_SPELLS) do if knows(id) then a = setbit(a, sub) end end
    -- Classic-style skill lines (name, rank, max), if this client still has them
    if GetNumSkillLines and GetSkillLineInfo then
        for i = 1, GetNumSkillLines() do
            local ok, name, header, _, rank, _, _, maxRank = pcall(GetSkillLineInfo, i)
            name = ok and Safe(name)
            if name and not header then
                local ws, as = WEAPON_LINES[name], ARMOR_LINES[name]
                if ws then w = setbit(w, ws) end
                if as then a = setbit(a, as) end
                local r = ("%s %s/%s"):format(clean(name, 20), Safe(rank) or "?", Safe(maxRank) or "?")
                if (ws or name == "Defense" or name == "Unarmed") and #table.concat(ranks, ",") + #r < RANKS_MAX then
                    ranks[#ranks + 1] = r           -- whole ranks only: page K holds what fits
                end
            end
        end
    end
    -- nobody has zero weapon skills or no cloth: an all-zero answer means the APIs told us nothing
    ns.skillWeapons = w > 0 and floor(w) or nil
    ns.skillArmor = a > 0 and floor(a) or nil
    ns.skillRanks = table.concat(ranks, ",")
end

-- /glide gear: the gear entries this module sends, and the trained weapon and armor skills
ns.Command("gear", "gear - the gear entries the strip sends, and the trained weapon and armor skills", function()
    ns.Print(("worn chunks=%d bag chunks=%d"):format(#(ns.gear and ns.gear.W or {}), #(ns.gear and ns.gear.I or {})))
    for _, kind in ipairs({ "W", "I" }) do
        for _, c in ipairs(ns.gear and ns.gear[kind] or {}) do
            for e in c:gmatch("[^,]+") do print(kind == "W" and "worn:" or "bag:", e) end
        end
    end
    for _, e in ipairs(ns.gearErrors or {}) do print("gear error:", e) end
    if ns.BuildSkills then pcall(ns.BuildSkills) end
    print(("trained skills: weapons mask=%s armor mask=%s %s"):format(tostring(ns.skillWeapons),
        tostring(ns.skillArmor), ns.skillRanks ~= "" and ("(" .. tostring(ns.skillRanks) .. ")") or ""))
end)
