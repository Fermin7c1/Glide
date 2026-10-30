-- Glide - bag cleanup: when the bags are full, delete the cheapest item RestedXP has marked as junk. No prompt.
--
-- Only RestedXP's junk is ever deleted: its inventory manager's IsJunk (the items you marked as junk in RestedXP, and
-- grey items unless you marked them useful), or, with RestedXP's inventory manager off, the items in its discard pile
-- (RXPCData.discardPile[id] == true). Nothing RestedXP doesn't call junk is touched. The cheapest stack goes first.
--
-- The game only lets an addon delete an item in response to a click or key press, so a full bag arms the cleanup and
-- your next click in the world or key press carries it out (RestedXP's own auto-discard works the same way). When
-- RestedXP's own "auto discard" is on, Glide stands aside so two items aren't deleted for one full bag.
--
-- "/glide trash" deletes the pick now, "/glide trash list" shows it, and there's a key binding (Key Bindings >
-- AddOns > Glide). On by default; turn it off in the Glide window (/glide) or with "/glide trash off".
local _, ns = ...                                         -- the live-link namespace (the Glide window reads ns.TrashOn)
local rec = ns.rec                                        -- the recorder's debug switch (Core.lua)
local Safe, Print = ns.Safe, ns.Print

local DEFAULTS = { enabled = true }
local function settings() return ns.LinkSection("trash", DEFAULTS) end
function ns.TrashOn() return settings().enabled ~= false end

local armed = false
function ns.SetTrash(on)
    settings().enabled = not not on
    if not on then armed = false end
    ns.OnSettingsChanged()
end

local function money(c) return c > 0 and ns.Money(c) or "nothing" end

-- the pick as chat names it: its link, and the count of a stack
local function pickName(pick) return pick.link .. (pick.count > 1 and (" x" .. pick.count) or "") end

-- RestedXP's inventory manager, when it's there and switched on
local function rxpManager()
    local R = _G.RXP
    local im = type(R) == "table" and R.inventoryManager
    if type(im) ~= "table" or type(im.IsJunk) ~= "function" then return nil end
    if type(im.IsFeatureEnabled) == "function" then
        local ok, on = pcall(im.IsFeatureEnabled, im)
        if not ok or not on then return nil end
    end
    return im
end

--- Is this item RestedXP junk?
local function isJunk(id, bag, slot)
    local im = rxpManager()
    if im then
        local ok, junk = pcall(im.IsJunk, im, id, bag, slot)
        return ok and junk == true
    end
    local pile = type(RXPCData) == "table" and RXPCData.discardPile
    return type(pile) == "table" and pile[id] == true
end

--- RestedXP deletes junk itself when the bags fill ("auto discard"): then Glide doesn't.
local function rxpAutoDiscard()
    local p = rxpManager() and ns.RXPProfile()
    return p and p.autoDiscardItems and true or false
end

-- the general bags' free slots (profession bags, quivers and ammo pouches don't count)
local function freeSlots()
    local free = 0
    for bag = 0, 4 do
        local n, family = C_Container.GetContainerNumFreeSlots(bag)
        if Safe(family) == 0 then free = free + (Safe(n) or 0) end
    end
    return free
end

--- The cheapest stack of RestedXP junk: { bag, slot, id, link, count, value } or nil.
function ns.TrashPick()
    local best
    for bag, slot, info in ns.BagItems() do
        local id = Safe(info.itemID)
        if id and not Safe(info.isLocked) and isJunk(id, bag, slot) then
            local price = Safe(select(11, C_Item.GetItemInfo(id))) or 0
            local count = Safe(info.stackCount) or 1
            local value = price * count
            if not best or value < best.value then
                best = { bag = bag, slot = slot, id = id, link = Safe(info.hyperlink) or ("item " .. id),
                         count = count, value = value }
            end
        end
    end
    return best
end

--- Delete the pick. Must run from a click, a key press, a slash command or the key binding (a hardware event).
local function destroy(why)
    local pick = ns.TrashPick()
    if not pick then
        if why ~= "auto" then Print("nothing in your bags is marked as junk in RestedXP") end
        return false
    end
    if GetCursorInfo() or UnitIsDeadOrGhost("player") then return false end      -- busy: the next press tries again
    ClearCursor()
    C_Container.PickupContainerItem(pick.bag, pick.slot)
    if not CursorHasItem() then return false end
    DeleteCursorItem()
    Print(("%sdeleted %s (RestedXP junk, sold for %s)"):format(why == "auto" and "bags full: " or "", pickName(pick),
        money(pick.value)))
    return true
end

local function manual()
    if not ns.TrashOn() then Print("bag cleanup is off (turn it on in the Glide window (/glide) or with /glide trash on)") return end
    destroy("manual")
end
function Glide_Trash() manual() end                       -- the key binding (Bindings.xml)

-- ---------------------------------------------------------------------------------------------
-- automatic: a full bag arms it; the next click in the world or key press deletes one item
-- ---------------------------------------------------------------------------------------------
local noted
local function arm()
    if not ns.TrashOn() or freeSlots() > 0 then armed = false return end
    if rxpAutoDiscard() then
        armed = false
        if not noted then
            noted = true
            Print("bags full: RestedXP's own auto-discard is on, so Glide leaves the junk to it")
        end
        return
    end
    armed = true
end

local function onPress()
    if not armed then return end
    local ok, err = pcall(function()
        if not ns.TrashOn() or freeSlots() > 0 or rxpAutoDiscard() then armed = false return end
        if destroy("auto") or not ns.TrashPick() then armed = false end   -- busy cursor: the next press tries again
    end)
    if not ok then
        armed = false
        if rec.debug then Print("|cffff5555trash:|r", err) end
    end
end

local function rearm()
    local ok, err = pcall(arm)
    if not ok and rec.debug then Print("|cffff5555trash:|r", err) end
end
ns.On("BAG_UPDATE_DELAYED", rearm)
ns.On("UI_ERROR_MESSAGE", function(_, msg)
    if msg == _G.INVENTORY_FULL or msg == _G.ERR_INV_FULL then rearm() end
end)
ns.On("PLAYER_LOGIN", function()
    if WorldFrame and WorldFrame.HookScript then
        WorldFrame:HookScript("OnMouseDown", onPress)     -- clicks in the world (looting, moving, targeting)
    end
    rearm()
end)
-- key presses anywhere (passed on to the game untouched)
local keys = CreateFrame("Frame", "GlideTrashKeys", UIParent)
if keys.SetPropagateKeyboardInput then keys:SetPropagateKeyboardInput(true) end
if keys.EnableKeyboard then keys:EnableKeyboard(true) end
keys:SetScript("OnKeyDown", onPress)

ns.Command("trash", "trash [list|on|off] - delete the cheapest RestedXP junk now; 'list' shows it; "
    .. "'on'/'off' turns bag cleanup on or off", function(rest)
    local a = (rest or ""):lower():match("^(%S*)")
    if a == "list" then
        local pick = ns.TrashPick()
        if pick then
            Print(("cheapest RestedXP junk: %s, sells for %s (/glide trash deletes it)"):format(pickName(pick),
                money(pick.value)))
        else
            Print("nothing in your bags is marked as junk in RestedXP")
        end
    elseif a == "on" or a == "off" then
        ns.SetTrash(a == "on")
        Print("bag cleanup", ns.TrashOn() and "on: when the bags are full, Glide deletes the cheapest RestedXP junk" or "off")
        if a == "on" then arm() end
    else
        manual()
    end
end)
