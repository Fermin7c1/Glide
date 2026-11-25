-- Glide - an upgrade line on item tooltips, at level 60: what an item in your bags, a chat link, loot or a vendor's
-- would change in your DPS against what you wear in its slot, by the stat weights WoWSims measured on your own gear
-- and talents (the Glide app writes them into Data/SimWeights.lua: read at login and /reload). Rings and trinkets are
-- set against the weaker of your two, a two-hander against your main and off hand. No line without weights for this
-- character (below 60, a class WoWSims doesn't sim here, or not weighed yet), nor for an item you can't use.
-- On by default; the Glide window or "/glide compare on|off".
-- Under it, at any level and on any item (the one you wear too), the source line: where the item comes from, "Glide:
-- drops from Darkmaster Gandling, Scholomance", from the facts the Glide app writes into Data/Live.lua (ns.live.items,
-- read at login and /reload). Grey for an item you can't get yet or with no known source, " (web, unconfirmed)" after
-- a fact found only on the web. On by default; the Glide window or "/glide sources on|off".
-- And the item the player is looking at (ns.hover, for State.lua's core key hv): the same item on a tooltip for
-- 0.75 s, so the Glide app knows which item "this" is in a question and looks it up before it's asked. A "|" in a
-- source line's text is the Glide app's own escaping ("||"): the line never carries a colour or texture code.
local _, ns = ...
local rec = ns.rec
local Safe, Print = ns.Safe, ns.Print

local DEFAULTS = { enabled = true }
local function settings() return ns.LinkSection("compare", DEFAULTS) end
function ns.CompareOn() return settings().enabled ~= false end
function ns.SetCompare(on) settings().enabled = not not on; ns.OnSettingsChanged() end
local function sourceSettings() return ns.LinkSection("sources", DEFAULTS) end
function ns.SourcesOn() return sourceSettings().enabled ~= false end
function ns.SetSources(on) sourceSettings().enabled = not not on; ns.OnSettingsChanged() end

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

--- The source line for an item id, from ns.live.items[id] = { text, a = "n" (now) | "l" (later) | "x" (no known
--- source), w = 1 (found on the web) }: its text and colour, or nil.
local function sourceLine(id)
    local items = type(ns.live) == "table" and ns.live.items
    local e = id and type(items) == "table" and items[id]
    local text = type(e) == "table" and e[1]
    if type(text) ~= "string" or text == "" then return nil end
    text = "Glide: " .. text .. (e.w == 1 and " (web, unconfirmed)" or "")
    if e.a == "l" or e.a == "x" then return text, 0.6, 0.6, 0.6 end
    return text, 0.4, 0.8, 1
end
ns.SourceLine = sourceLine

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

-- a pcall's line onto the tooltip (wrap: wrapped to the tooltip's width); its error only with the recorder's debug on
local function add(tooltip, what, wrap, ok, text, r, g, b)
    if ok and text then
        tooltip:AddLine(text, r, g, b, wrap)
    elseif not ok and rec and rec.debug then
        Print("|cffff5555" .. what .. ":|r", text)
    end
end

--- The item id in the tooltip's link, or nil.
local function linkedId(tooltip)
    local link = Safe(linkOf(tooltip))
    return type(link) == "string" and tonumber(link:match("item:(%d+)")) or nil
end

-- ---- the item the player is looking at ------------------------------------------------------------------------------
-- Once the same item has shown on GameTooltip or ItemRefTooltip for 0.75 s, it is that tooltip's item until the
-- tooltip hides or shows something else (another item, a unit, a spell, a quest): a glance while the mouse runs over
-- bags or the auction house doesn't count. Each tooltip keeps its own; ns.hover is the one that settled last on a
-- tooltip still showing it (a chat link's item again once a bag item over it is gone). ns.hoverLast and ns.hoverAt keep
-- the last one and when it last showed (State.lua holds hv 15 s after). ns.HoverCheck (State.lua, every frame) drops a
-- tooltip's item once it no longer shows it, should the game have refilled the tooltip without telling.
local HOVER_AFTER = 0.75
local pend = {}                 -- tooltip -> the item it shows before its 0.75 s: { id, linked (read off its link), seq }
local settled = {}              -- tooltip -> the item it has shown 0.75 s and still shows: { id, linked, seq }
local seq = 0
local hoverTip                  -- the tooltip ns.hover is on

--- ns.hover: the item settled last on a tooltip still showing it; none: hovered no more (ns.hoverAt: since when).
local function pick()
    local best
    for tip, s in pairs(settled) do
        if (tip.IsShown and not tip:IsShown()) or (s.linked and linkedId(tip) ~= s.id) then
            settled[tip] = nil                                       -- (that tooltip moved on)
        elseif not best or s.seq > settled[best].seq then
            best = tip
        end
    end
    local s = best and settled[best]
    if s then
        if ns.hover ~= s.id or hoverTip ~= best then
            ns.hover, ns.hoverLast, ns.hoverAt, hoverTip = s.id, s.id, GetTime(), best
        end
    elseif ns.hover then
        ns.hover, ns.hoverAt, hoverTip = nil, GetTime(), nil
    end
end
function ns.HoverCheck() pick() end

local function settle(tooltip, n)
    local p = pend[tooltip]
    if not p or p.seq ~= n then return end
    pend[tooltip] = nil
    if tooltip.IsShown and not tooltip:IsShown() then return end
    -- still that item (one shown without a link: the tooltip still up is all that can be told)
    if p.linked and linkedId(tooltip) ~= p.id then return end
    settled[tooltip] = p
    pick()
end

local function onItemShown(tooltip, id, linked)
    if not id then return end
    local p, s = pend[tooltip], settled[tooltip]
    if (p and p.id == id) or (s and s.id == id) then return end              -- (the same item again: a refresh)
    if s then settled[tooltip] = nil; pick() end                              -- (that tooltip moved on)
    seq = seq + 1
    local n = seq
    pend[tooltip] = { id = id, linked = linked, seq = n }
    C_Timer.After(HOVER_AFTER, function() pcall(settle, tooltip, n) end)
end

-- the tooltip hid, or shows something that isn't an item now (a unit, a spell, a quest link refilled into it)
local function onTooltipGone(tooltip)
    pend[tooltip] = nil
    if settled[tooltip] then settled[tooltip] = nil; pick() end
end

local function hookHide(tip)
    if type(tip) == "table" and tip.HookScript then
        tip:HookScript("OnHide", function(self) pcall(onTooltipGone, self) end)
    end
end
ns.On("PLAYER_LOGIN", function() hookHide(GameTooltip); hookHide(ItemRefTooltip) end)

-- the two lines and the hover, each on its own: one that fails leaves the others
local function onTooltip(tooltip, data)
    if tooltip ~= GameTooltip and tooltip ~= ItemRefTooltip then return end
    if ns.CompareOn() then
        add(tooltip, "compare", nil, pcall(function() return lineFor(Safe(linkOf(tooltip))) end))
    end
    if ns.SourcesOn() then
        add(tooltip, "sources", true, pcall(function()
            return sourceLine(linkedId(tooltip) or (type(data) == "table" and tonumber(Safe(data.id))))
        end))
    end
    local ok, err = pcall(function()
        local id = linkedId(tooltip)
        onItemShown(tooltip, id or (type(data) == "table" and tonumber(Safe(data.id))), id ~= nil)
    end)
    if not ok and rec and rec.debug then Print("|cffff5555hover:|r", err) end
end

-- any other kind of data filled into one of the two tooltips (a unit, a spell or quest link clicked in chat: the game
-- refills ItemRefTooltip without hiding it) ends its item there
local function onOtherTooltip(tooltip, data)
    if tooltip ~= GameTooltip and tooltip ~= ItemRefTooltip then return end
    if type(data) == "table" and data.type ~= nil and data.type ~= Enum.TooltipDataType.Item then
        onTooltipGone(tooltip)
    end
end

if TooltipDataProcessor and TooltipDataProcessor.AddTooltipPostCall and Enum and Enum.TooltipDataType then
    TooltipDataProcessor.AddTooltipPostCall(Enum.TooltipDataType.Item, onTooltip)
    if TooltipDataProcessor.AllTypes ~= nil then
        pcall(TooltipDataProcessor.AddTooltipPostCall, TooltipDataProcessor.AllTypes, function(tooltip, data)
            pcall(onOtherTooltip, tooltip, data)
        end)
    end
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

ns.Command("sources", "sources [on|off] - where an item comes from on its tooltip (the boss, quest, vendor or craft)",
    function(rest)
        local a = (rest or ""):lower():match("^(%S*)")
        if a == "on" or a == "off" then ns.SetSources(a == "on") end
        -- no ns.live at all: the game never loaded Data/Live.lua (an addon updated while WoW ran: a /reload keeps the
        -- file list WoW read when it started), and only a restart loads it
        local loaded = type(ns.live) == "table"
        local items, n = loaded and ns.live.items, 0
        for _ in pairs(type(items) == "table" and items or {}) do n = n + 1 end
        Print("where items come from, on item tooltips", ns.OnOff(ns.SourcesOn()),
            not loaded and "(Glide's item facts aren't loaded: restart WoW once)"
              or n > 0 and ("(%d items known)"):format(n)
              or "(none known yet: the Glide app finds them while it runs; then /reload)")
    end)
