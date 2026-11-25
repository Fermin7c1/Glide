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
-- On an item Glide ranked for this character (at 60: the Glide app sims your own gear with WoWSims and writes the
-- ranks into Data/Live.lua, ns.live.chars[<Name-Realm or Name>].bis), the BiS line takes the source line's place, so a
-- tooltip never has more than two Glide lines: "Glide BiS #2 for Head, +6.1 DPS: sold by Deliana, Ironforge",
-- "Glide BiS #1 for Head: you wear it" (the addon sees what you wear), "about the same as yours" when the sims can't
-- tell the two apart, "with its set" for a set piece that is best only with another piece of its set, "+32.0 DPS with
-- Better Orb" for a one-hander ranked with an off hand in place of a two-hander. An item with random suffixes shows its
-- rank on the suffix it was ranked with only ("of Sorcery", not "of the Monkey"). On by default; the Glide window or
-- "/glide bis on|off".
-- The item Glide chases for this character (ns.live.chars[key].goal: the next BiS piece the app's plan goes after,
-- Pursuit.lua) says so on that same line, never a third: "Glide BiS #1 for Head, +6.1 DPS, your next BiS goal: sold by
-- Deliana, Ironforge", "Glide, your next BiS goal: drops from ..." on the source line, "Glide: your next BiS goal" alone
-- when neither has anything to say; nothing once you wear it.
-- And the item the player is looking at (ns.hover, for State.lua's core key hv): the same item on a tooltip for
-- 0.75 s, so the Glide app knows which item "this" is in a question and looks it up before it's asked. A "|" in a
-- source line's text is the Glide app's own escaping ("||"): the line never carries a colour or texture code.
-- "/glide sim <shift-clicked item>" asks the Glide app to sim that item on your gear with WoWSims (State.lua's
-- ns.SimRequest: hv's request form); the answer comes from the app, spoken and on its overlay. Once the app has simmed
-- an item for this character (ns.live.chars[key].sim), the upgrade line takes the simmed number in place of the
-- weights' estimate, its text unchanged, set against the worn item the sim replaced (a ring or trinket: the one the sim
-- picked), while that slot and the other of its pair hold what they held then (the sim was on that gear).
-- "/glide wowsims" shows the link that opens this character in WoWSims (ns.live.chars[key].link) in a box to copy.
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
local function bisSettings() return ns.LinkSection("bis", DEFAULTS) end
function ns.BisOn() return bisSettings().enabled ~= false end
function ns.SetBis(on) bisSettings().enabled = not not on; ns.OnSettingsChanged() end

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

local simmed            -- (id, link, slots) -> the DPS change the Glide app simmed for the item and the worn item's
                        -- link it was set against, or nil (below)

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
    local okSim, sim, simWorn = pcall(simmed, id, link, slots)  -- (a broken Live.lua entry leaves the estimate)
    if okSim and sim then dps, worn = sim, simWorn end          -- (named against what it was simmed against)
    local pct = (e.dps and e.dps > 0) and dps / e.dps * 100 or 0
    local against = worn and worn:match("%[(.-)%]") or "an empty slot"
    local text = ("Glide (%s): %+.1f DPS (%+.1f%%) vs %s"):format(e.spec or "WoWSims", dps, pct, against)
    if dps > 0.05 then return text, 0.25, 0.85, 0.35 end
    if dps < -0.05 then return text, 0.95, 0.35, 0.3 end
    return ("Glide (%s): no change vs %s"):format(e.spec or "WoWSims", against), 0.6, 0.6, 0.6
end
ns.CompareLine = lineFor

--- Where an item comes from, from ns.live.items[id] = { text, a = "n" (now) | "l" (later) | "x" (no known source),
--- w = 1 (found on the web) }: the text (" (web, unconfirmed)" after a web fact) and true when the item can't be had
--- yet or has no known source; or nil.
local function sourceOf(id)
    local items = type(ns.live) == "table" and ns.live.items
    local e = id and type(items) == "table" and items[id]
    local text = type(e) == "table" and e[1]
    if type(text) ~= "string" or text == "" then return nil end
    return text .. (e.w == 1 and " (web, unconfirmed)" or ""), e.a == "l" or e.a == "x"
end

local GOAL = "your next BiS goal"

--- The source line for an item id: its text and colour (grey for an item not out yet or with no known source), or nil.
--- goal: the item is the one Glide chases for this character, said before where it comes from ("Glide, your next BiS
--- goal: drops from ..."), or alone when no source is known.
local function sourceLine(id, goal)
    local text, grey = sourceOf(id)
    if not text then
        if goal then return "Glide: " .. GOAL, 0.4, 0.8, 1 end
        return nil
    end
    local head = goal and ("Glide, " .. GOAL .. ": ") or "Glide: "
    if grey then return head .. text, 0.6, 0.6, 0.6 end
    return head .. text, 0.4, 0.8, 1
end
ns.SourceLine = sourceLine

--- This character's entry in ns.live.chars, or nil: "Name-Realm" when the Glide app knows the realm (the realm as the
--- game gives it, with or without its spaces), else "Name".
local function myChar()
    local chars = type(ns.live) == "table" and ns.live.chars
    local name = Safe(UnitName("player"))
    if type(chars) ~= "table" or type(name) ~= "string" or name == "" then return nil end
    local tried = {}
    local function at(realm)
        if type(realm) ~= "string" or realm == "" or tried[realm] then return nil end
        tried[realm] = true
        local e = chars[name .. "-" .. realm]
        return type(e) == "table" and e or nil
    end
    local shown = GetRealmName and Safe(GetRealmName())
    local normalized = _G.GetNormalizedRealmName
    local e = at(UnitFullName and Safe(select(2, UnitFullName("player"))))
        or at(normalized and Safe(normalized())) or at(shown)
        or at(type(shown) == "string" and (shown:gsub("%s", "")) or nil)
    if e then return e end
    e = chars[name]
    return type(e) == "table" and e or nil
end
ns.LiveChar = myChar             -- (Pursuit.lua's next move, the recorder's auction prices for the plan's items)

--- An item link's item id, random suffix id (the link's field 7, counted after "item:" as Gear.lua does, so the
--- colon in Forever's "|cnIQ4:" colour prefix doesn't shift it; without its sign, 0 for none) and enchant id (field 2,
--- 0 for none), or nil.
local function linkIds(link)
    local body = type(link) == "string" and link:match("item:([%-%d:]*)")
    if not body then return nil end
    local f = {}
    for v in (body .. ":"):gmatch("([^:]*):") do f[#f + 1] = v end
    local id = tonumber(f[1] or "")
    if not id then return nil end
    return id, math.abs(tonumber(f[7] or "") or 0), math.abs(tonumber(f[2] or "") or 0)
end

-- the slots whose item changed since this UI load read Data/Live.lua (once in the world: what the login does isn't a
-- change), the quick check: a sim the Glide app wrote there was on the gear before (the items' ids are checked too)
local inWorld, swapped = false, {}
ns.On("PLAYER_ENTERING_WORLD", function() inWorld = true end)
local PAIR = { [11] = 12, [12] = 11, [13] = 14, [14] = 13, [16] = 17, [17] = 16 }   -- a slot -> the other of its pair

--- Whether a slot holds the item id it held when the Glide app simmed (0: nothing there), unchanged since Live.lua
--- was read.
local function holds(slot, want)
    if swapped[slot] or type(want) ~= "number" then return false end
    return ((linkIds(Safe(GetInventoryItemLink("player", slot)))) or 0) == want
end

--- The DPS change the Glide app simmed with WoWSims for an item (link: the tooltip's, for its random suffix) and the
--- link of the worn item it was set against (nil: an empty slot), from this character's ns.live.chars[key].sim[id] =
--- { d, the random suffix it was simmed with (without its sign, 0: none), the slot it went in, the item id worn there
--- (0: none), and for a ring, trinket or weapon the item id in the other slot of the pair (0: none) }; nil when there
--- is none, it was simmed with another suffix or in a slot the line doesn't compare against, or one of those two
--- slots holds another item now (worn since the sim, before this login too) or changed since Live.lua was read.
simmed = function(id, link, slots)
    local ch = id and myChar()
    local sims = ch and ch.sim
    local e = type(sims) == "table" and sims[id]
    if type(e) ~= "table" then return nil end
    local d, sfx, slot = tonumber(e[1]), math.abs(tonumber(e[2]) or 0), tonumber(e[3])
    if not d or d ~= d or d == math.huge or d == -math.huge or not slot then return nil end
    local _, lsfx = linkIds(link)
    if (lsfx or 0) ~= sfx then return nil end                      -- (simmed with another suffix, or none)
    local ours = false
    for _, s in ipairs(slots) do
        if s == slot then ours = true end
    end
    if not ours or not holds(slot, tonumber(e[4])) then return nil end
    if PAIR[slot] and not holds(PAIR[slot], tonumber(e[5])) then return nil end
    return d, Safe(GetInventoryItemLink("player", slot))
end

--- True when the player wears an item with this id (and, given one, this random suffix: without its sign).
local function wearing(id, sfx)
    for slot = 1, 18 do
        local lid, lsfx = linkIds(Safe(GetInventoryItemLink("player", slot)))
        if lid == id and (not sfx or lsfx == sfx) then return true end
    end
    return false
end

--- True for the item Glide chases for this character (ns.live.chars[key].goal, an item id) while it isn't worn yet.
--- An item ranked with a random suffix (ns.live.chars[key].bis[id][6]) is the goal with that suffix only, as the BiS
--- line has it (link: the tooltip's item link): another roll of it isn't the goal, and wearing another roll doesn't
--- reach it.
local function chased(id, link)
    local ch = id and myChar()
    if ch == nil or tonumber(ch.goal) ~= id then return false end
    local e = type(ch.bis) == "table" and ch.bis[id]
    local sfx = type(e) == "table" and tonumber(e[6]) or nil
    sfx = sfx and sfx ~= 0 and math.abs(sfx) or nil
    if sfx then
        local lid, lsfx = linkIds(link)
        if lid ~= id or lsfx ~= sfx then return false end           -- (another roll, or no link to tell)
    end
    return not wearing(id, sfx)
end

--- The BiS line for an item id (link: the tooltip's item link, for its random suffix), from this character's
--- ns.live.chars[key].bis[id] = { slot, rank, d, "~" (about the same as what you wear: within the sims' noise) or "s"
--- (best only with its set: d nil), the profession it needs to wear (when you don't have it), the random suffix it
--- was ranked with (without its sign), the weapon its d was simmed with (a one-hander and an off hand in place of a
--- two-hander: the pair's d) }: its text and colour (grey as the source line's), or nil (not ranked: the source line
--- shows instead; so for another random suffix of the item, or a tooltip without a link to tell). Where the item
--- comes from closes the line while the source line is switched on.
local function bisLine(id, link)
    local ch = id and myChar()
    local bis = ch and ch.bis
    local e = type(bis) == "table" and bis[id]
    if type(e) ~= "table" then return nil end
    local slot, rank, d = e[1], tonumber(e[2]), tonumber(e[3])
    if type(slot) ~= "string" or slot == "" or not rank or rank < 1 then return nil end
    local sfx = tonumber(e[6])
    sfx = sfx and sfx ~= 0 and math.abs(sfx) or nil
    if sfx then
        local lid, lsfx = linkIds(link)
        if lid ~= id or lsfx ~= sfx then return nil end            -- (ranked with another suffix)
    end
    local text = ("Glide BiS #%d for %s"):format(rank, slot)
    if wearing(id, sfx) then return text .. ": you wear it", 0.4, 0.8, 1 end
    if e[4] == "~" then
        text = text .. ", about the same as yours"
    elseif e[4] == "s" then
        text = text .. ", with its set"
    elseif d then
        text = text .. (", %+.1f DPS"):format(d)
    end
    if type(e[7]) == "string" and e[7] ~= "" then text = text .. " with " .. e[7] end
    if type(e[5]) == "string" and e[5] ~= "" then text = text .. (" (needs %s to wear)"):format(e[5]) end
    if chased(id, link) then text = text .. ", " .. GOAL end
    local src, grey = sourceOf(id)
    if src and ns.SourcesOn() then text = text .. ": " .. src end
    if grey then return text, 0.6, 0.6, 0.6 end
    return text, 0.4, 0.8, 1
end
ns.BisLine = bisLine

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

-- the item id a tooltip shows: its link's, else the one its data carries (a tooltip filled without a link)
local function shownId(tooltip, data)
    return linkedId(tooltip) or (type(data) == "table" and tonumber(Safe(data.id))) or nil
end

-- the lines and the hover, each on its own: one that fails leaves the others. Two Glide lines at most: the upgrade
-- line, then the BiS line on an item ranked for this character, else (or when the BiS line fails) the source line;
-- the item Glide chases says so on that second line (with the BiS line switched on).
local function onTooltip(tooltip, data)
    if tooltip ~= GameTooltip and tooltip ~= ItemRefTooltip then return end
    if ns.CompareOn() then
        add(tooltip, "compare", nil, pcall(function() return lineFor(Safe(linkOf(tooltip))) end))
    end
    local ranked = false
    if ns.BisOn() then
        local ok, text, r, g, b = pcall(function() return bisLine(shownId(tooltip, data), Safe(linkOf(tooltip))) end)
        add(tooltip, "bis", true, ok, text, r, g, b)
        ranked = ok and text ~= nil
    end
    if not ranked then
        local okG, goal = pcall(function()
            return ns.BisOn() and chased(shownId(tooltip, data), Safe(linkOf(tooltip)))
        end)
        goal = okG and goal or nil
        if ns.SourcesOn() then
            add(tooltip, "sources", true, pcall(function() return sourceLine(shownId(tooltip, data), goal) end))
        elseif goal then
            tooltip:AddLine("Glide: " .. GOAL, 0.4, 0.8, 1, true)
        end
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
ns.On("PLAYER_EQUIPMENT_CHANGED", function(slot)
    cache, cached = {}, 0
    slot = tonumber(Safe(slot))
    if inWorld and slot then swapped[slot] = true end
end)

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

ns.Command("bis", "bis [on|off] - at level 60, where an item ranks for you on its tooltip (WoWSims sims of your gear)",
    function(rest)
        local a = (rest or ""):lower():match("^(%S*)")
        if a == "on" or a == "off" then ns.SetBis(a == "on") end
        local loaded = type(ns.live) == "table"
        local ch = loaded and myChar()
        local n = 0
        for _ in pairs(ch and type(ch.bis) == "table" and ch.bis or {}) do n = n + 1 end
        Print("best-in-slot rank on item tooltips", ns.OnOff(ns.BisOn()),
            not loaded and "(Glide's item facts aren't loaded: restart WoW once)"
              or n > 0 and ("(%d items ranked for this character)"):format(n)
              or "(none ranked for this character yet: level 60, a mage, and a few minutes of the Glide app running; "
                .. "then /reload)")
    end)

-- ---- /glide sim: the Glide app sims an item on your gear -----------------------------------------------------------
-- "/glide sim" with an item shift-clicked into the chat line (or an item id): the request goes to the Glide app in
-- hv's request form (State.lua), with the item's random suffix and enchant, and the app answers by voice and on its
-- overlay. Gear only, as far as the client can tell at once (an item it hasn't seen yet goes, and the app tells).
ns.Command("sim", "sim <item> - shift-click an item: the Glide app sims it on your gear with WoWSims", function(rest)
    rest = rest or ""
    local link = rest:match("(|c[^|]*|H.-|h.-|h|r)") or rest:match("(|H.-|h.-|h)")
    local id, sfx, ench = linkIds(link or rest)
    if not id then id, sfx, ench = tonumber(rest:match("^%s*(%d+)%s*$")), 0, 0 end
    if not id then
        Print("shift-click an item after /glide sim to sim it on your gear")
        return
    end
    local name = link or ("item " .. id)
    local okLoc, loc = pcall(function() return select(4, GetItemInfoInstant(link or id)) end)
    if okLoc and loc ~= nil and not SLOTS[loc] then
        Print(name, "isn't gear, so there's nothing to sim")
        return
    end
    local form, why = ns.SimRequest(id, sfx, ench)
    if not form then
        Print(why == "busy" and "still sending your last sims: ask again in a few seconds"
            or "can't read that item: shift-click it from your bags or a chat link")
        return
    end
    local stripOff = ns.StripOn and not ns.StripOn()
    Print("simming", name, "on your gear: the Glide app answers in a moment"
        .. (stripOff and " (the data strip is off, so the app can't see it: /glide strip on)" or ""))
end)

-- ---- /glide wowsims: your character in WoWSims ---------------------------------------------------------------------
-- The link the Glide app wrote for this character (ns.live.chars[key].link: the setup the app simmed) in a popup's
-- edit box, selected for Ctrl+C, since the game can't open a browser. A popup, not the Glide window: an edit box there
-- would taint it. Only a link to WoWSims' own site is shown.
local SITE = "https://www.wowsims.com/forever/"
local POPUP = "GLIDE_WOWSIMS_LINK"
local shownUrl

local function editBoxOf(dialog)
    if type(dialog) ~= "table" then return nil end
    if dialog.GetEditBox then return dialog:GetEditBox() end
    return dialog.editBox or dialog.EditBox
end

-- the link into the popup's edit box (no letter limit: a link is longer than the default), all of it selected
local function fill(dialog)
    local box = editBoxOf(dialog)
    if not box or not shownUrl then return end
    if box.SetMaxLetters then box:SetMaxLetters(0) end
    if box.SetMaxBytes then box:SetMaxBytes(0) end
    box:SetText(shownUrl)
    box:HighlightText()
    if box.SetFocus then box:SetFocus() end
end

local function closeBox(box)
    local parent = box and box.GetParent and box:GetParent()
    if parent and parent.Hide then parent:Hide() end
end

--- Shows the link in the popup -> true, or false when the client has no popups (the link is printed instead).
local function showLink(url)
    local dialogs, show = _G.StaticPopupDialogs, _G.StaticPopup_Show
    if type(dialogs) ~= "table" or type(show) ~= "function" then return false end
    shownUrl = url
    if not dialogs[POPUP] then
        dialogs[POPUP] = {
            text = "Your character in WoWSims (wowsims.com/forever). Copy the link with Ctrl+C and paste it into your "
                .. "browser.",
            button1 = _G.CLOSE or "Close",
            hasEditBox = true, editBoxWidth = 350, maxLetters = 0,
            OnShow = function(self) fill(self) end,
            -- the text stays the link: typing over it puts it back, selected
            EditBoxOnTextChanged = function(box)
                if shownUrl and box:GetText() ~= shownUrl then box:SetText(shownUrl); box:HighlightText() end
            end,
            EditBoxOnEnterPressed = closeBox,
            EditBoxOnEscapePressed = closeBox,
            timeout = 0, whileDead = true, hideOnEscape = true, preferredIndex = 3,
        }
    end
    local dialog = show(POPUP, nil, nil, url)
    if dialog then fill(dialog) end
    return dialog ~= nil
end

ns.Command("wowsims", "wowsims - a link that opens your character in WoWSims, to copy into your browser", function()
    local loaded = type(ns.live) == "table"
    local ch = loaded and myChar()
    local url = ch and ch.link
    if type(url) ~= "string" or url:sub(1, #SITE) ~= SITE or url:find("[%s|]") then
        Print(not loaded and "Glide's item facts aren't loaded: restart WoW once"
            or "no WoWSims link for this character yet: level 60, a mage, and the Glide app running; then /reload")
        return
    end
    if not showLink(url) then Print(url) end
end)
