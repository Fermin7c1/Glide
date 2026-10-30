-- Glide - auto repair: when a merchant who can repair opens, repair everything with your own gold. No prompt.
--
-- It repairs only when you can pay the whole bill; otherwise it says what's missing and leaves it to you (the
-- merchant's repair buttons still work for single items). "/glide repair" repairs now at an open merchant. On by
-- default; turn it off in the Glide window (/glide) or with "/glide repair off".
local _, ns = ...                                         -- the live-link namespace (the Glide window reads ns.RepairOn)
local rec = ns.rec                                        -- the recorder's debug switch (Core.lua)
local Safe, Print = ns.Safe, ns.Print

local DEFAULTS = { enabled = true }
local function settings() return ns.LinkSection("repair", DEFAULTS) end
function ns.RepairOn() return settings().enabled ~= false end
function ns.SetRepair(on) settings().enabled = not not on; ns.OnSettingsChanged() end

local money = ns.Money

--- Repair everything at the open merchant. why: "auto" (merchant opened) or "manual". Returns true when it repaired.
local function repair(why)
    if not (CanMerchantRepair and Safe(CanMerchantRepair())) then
        if why == "manual" then Print("open a merchant who repairs first") end
        return false
    end
    local cost, needed = GetRepairAllCost()
    cost, needed = Safe(cost), Safe(needed)
    if not cost or cost <= 0 or needed == false then
        if why == "manual" then Print("nothing to repair") end
        return false
    end
    local have = Safe(GetMoney()) or 0
    if have < cost then
        Print(("repairs cost %s, you have %s: repair the most important items by hand"):format(money(cost), money(have)))
        return false
    end
    RepairAllItems()
    Print(("repaired everything for %s"):format(money(cost)))
    return true
end

ns.On("MERCHANT_SHOW", function()
    if not ns.RepairOn() then return end
    local ok, err = pcall(repair, "auto")
    if not ok and rec.debug then Print("|cffff5555repair:|r", err) end
end)

ns.Command("repair", "repair [on|off] - repair everything at the open merchant; 'on'/'off' turns auto repair on or off", function(rest)
    local a = (rest or ""):lower():match("^(%S*)")
    if a == "on" or a == "off" then
        ns.SetRepair(a == "on")
        Print("auto repair", ns.RepairOn() and "on: Glide repairs everything when you open a merchant who repairs" or "off")
        if a == "on" and MerchantFrame and MerchantFrame:IsShown() then repair("auto") end
    else
        repair("manual")
    end
end)
