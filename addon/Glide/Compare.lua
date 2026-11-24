-- Glide - an upgrade line on item tooltips, at level 60: what an item in your bags, a chat link, loot or a vendor's
-- would change in your DPS against what you wear in its slot, by the stat weights WoWSims measured on your own gear
-- and talents (the Glide app writes them into Data/SimWeights.lua: read at login and /reload). Rings and trinkets are
-- set against the weaker of your two, a two-hander against your main and off hand. No line without weights for this
-- character (below 60, a class WoWSims doesn't sim here, or not weighed yet), nor for an item you can't use.
-- On by default; the Glide window or "/glide compare on|off".
local _, ns = ...
local rec = ns.rec
local Safe, Print = ns.Safe, ns.Print

local DEFAULTS = { enabled = true }
local function settings() return ns.LinkSection("compare", DEFAULTS) end
function ns.CompareOn() return settings().enabled ~= false end
function ns.SetCompare(on) settings().enabled = not not on; ns.OnSettingsChanged() end

local GetItemInfoInstant = (C_Item and C_Item.GetItemInfoInstant) or _G.GetItemInfoInstant
local GetItemInfo = (C_Item and C_Item.GetItemInfo) or _G.GetItemInfo

-- an item's equip location -> the slots it would go in (as companion/gear.py LOC_SLOTS; a one-hander into the main
-- hand unless the class dual-wields)
local SLOTS = {
    INVTYPE_HEAD = { 1 }, INVTYPE_NECK = { 2 }, INVTYPE_SHOULDER = { 3 }, INVTYPE_CHEST = { 5 }, INVTYPE_ROBE = { 5 },
    INVTYPE_WAIST = { 6 }, INVTYPE_LEGS = { 7 }, INVTYPE_FEET = { 8 }, INVTYPE_WRIST = { 9 }, INVTYPE_HAND = { 10 },
    INVTYPE_FINGER = { 11, 12 }, INVTYPE_TRINKET = { 13, 14 }, INVTYPE_CLOAK = { 15 },
    INVTYPE_WEAPON = { 16 }, INVTYPE_WEAPONMAINHAND = { 16 }, INVTYPE_2HWEAPON = { 16 },
    INVTYPE_WEAPONOFFHAND = { 17 }, INVTYPE_SHIELD = { 17 }, INVTYPE_HOLDABLE = { 17 },
    INVTYPE_RANGED = { 18 }, INVTYPE_RANGEDRIGHT = { 18 }, INVTYPE_THROWN = { 18 }, INVTYPE_RELIC = { 18 },
}
local DUAL = { WARRIOR = true, ROGUE = true, HUNTER = true }

local cache, cached = {}, 0                 -- item link -> its stats (Gear.lua's reading), at most 400 kept
--- An item's stats, or nil while the client hasn't its data yet (the tooltip is filled again when it comes).
local function statsOf(link)
    local s = cache[link]
    if not s then
        if not GetItemInfo(link) then return nil end
        if cached >= 400 then cache, cached = {}, 0 end
        s = ns.ItemStats(link) or {}
        cache[link], cached = s, cached + 1
    end
    return s
end

--- This character's weights, at 60: {class, spec, dps, perRef, w = {CODE = EP}}, or nil.
local function mine()
    local all = ns.simWeights
    local name = Safe(UnitName("player"))
    local e = all and name and all[name]
    local _, class = UnitClass("player")
    if e and e.class == Safe(class) and (Safe(UnitLevel("player")) or 0) >= 60 then return e end
end

local function score(stats, w)
    local t = 0
    for code, v in pairs(stats) do t = t + v * (w[code] or 0) end
    return t
end

--- The line for an item: its text and colour, or nil.
local function lineFor(link)
    local e = mine()
    if not e or not link then return nil end
    local _, _, _, loc = GetItemInfoInstant(link)
    local slots = loc and SLOTS[loc]
    if not slots then return nil end
    local id = tonumber(link:match("item:(%d+)"))
    if id and C_PlayerInfo and C_PlayerInfo.CanUseItem and Safe(C_PlayerInfo.CanUseItem(id)) == false then return nil end
    if loc == "INVTYPE_WEAPON" then
        local _, class = UnitClass("player")
        if DUAL[Safe(class) or ""] then slots = { 16, 17 } end
    elseif slots[1] == 17 then                                -- (an off hand beside a two-hander: a different main hand)
        local mh = Safe(GetInventoryItemLink("player", 16))
        if mh and select(4, GetItemInfoInstant(mh)) == "INVTYPE_2HWEAPON" then return nil end
    end
    local stats = statsOf(link)
    if not stats then return nil end
    local new = score(stats, e.w)
    local old, worn
    for _, slot in ipairs(slots) do
        local wl = Safe(GetInventoryItemLink("player", slot))
        if wl == link then return nil end                     -- (the item you wear)
        local ws = wl and statsOf(wl)
        if wl and not ws then return nil end
        local s = ws and score(ws, e.w) or 0
        if loc == "INVTYPE_2HWEAPON" then
            local oh = Safe(GetInventoryItemLink("player", 17))
            local ohs = oh and statsOf(oh)
            if oh and not ohs then return nil end
            if ohs then s = s + score(ohs, e.w) end
        end
        if old == nil or s < old then old, worn = s, wl end
    end
    local dps = (new - old) * (e.perRef or 0)
    local pct = (e.dps and e.dps > 0) and dps / e.dps * 100 or 0
    local against = worn and worn:match("%[(.-)%]") or "an empty slot"
    local text = ("Glide (%s): %+.1f DPS (%+.1f%%) vs %s"):format(e.spec or "WoWSims", dps, pct, against)
    if dps > 0.05 then return text, 0.25, 0.85, 0.35 end
    if dps < -0.05 then return text, 0.95, 0.35, 0.3 end
    return ("Glide (%s): no change vs %s"):format(e.spec or "WoWSims", against), 0.6, 0.6, 0.6
end
ns.CompareLine = lineFor

local function linkOf(tooltip)
    if TooltipUtil and TooltipUtil.GetDisplayedItem then
        local _, link = TooltipUtil.GetDisplayedItem(tooltip)
        if link then return link end
    end
    if tooltip.GetItem then
        local _, link = tooltip:GetItem()
        return link
    end
end

local function onTooltip(tooltip)
    if not ns.CompareOn() or (tooltip ~= GameTooltip and tooltip ~= ItemRefTooltip) then return end
    local ok, text, r, g, b = pcall(function() return lineFor(Safe(linkOf(tooltip))) end)
    if ok and text then
        tooltip:AddLine(text, r, g, b)
    elseif not ok and rec and rec.debug then
        Print("|cffff5555compare:|r", text)
    end
end

if TooltipDataProcessor and TooltipDataProcessor.AddTooltipPostCall and Enum and Enum.TooltipDataType then
    TooltipDataProcessor.AddTooltipPostCall(Enum.TooltipDataType.Item, onTooltip)
end
ns.On("PLAYER_EQUIPMENT_CHANGED", function() cache, cached = {}, 0 end)

ns.Command("compare", "compare [on|off] - at level 60, what an item changes in your DPS on its tooltip (WoWSims weights)",
    function(rest)
        local a = (rest or ""):lower():match("^(%S*)")
        if a == "on" or a == "off" then ns.SetCompare(a == "on") end
        local e = mine()
        Print("upgrade line on item tooltips", ns.OnOff(ns.CompareOn()),
            e and ("(WoWSims %s, %s)"):format(e.wowsims or "", e.spec or "")
              or "(no WoWSims weights for this character yet: level 60, a mage, and the Glide app running; then /reload)")
    end)
