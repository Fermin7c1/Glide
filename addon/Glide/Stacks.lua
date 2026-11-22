-- Glide - stack counts on the Cooldown Manager: Blizzard's Tracked Bars and Tracked Buffs show a buff's stacks, but
-- never those of a stacking debuff on you (Arcane Blast): an item writes its stack text before it finds that debuff
-- (it looks your auras up as helpful ones first, and finds the debuff after), then only its timer moves.
--
-- Glide writes the count after the item has found the aura, on a text of its own laid over the item's (the same font,
-- the same place), only when the item's own text had no aura to show: Blizzard's frames are never written to. In
-- combat the count is a secret value: handed to the game to draw as it is, never read (a count of 0, an aura that
-- doesn't stack, shows nothing). On by default; the Glide window or "/glide stacks on|off".
local _, ns = ...
local rec = ns.rec
local Print, IsSecret = ns.Print, ns.IsSecret

local DEFAULTS = { enabled = true }
local function settings() return ns.LinkSection("stacks", DEFAULTS) end
function ns.StacksOn() return settings().enabled ~= false end

local VIEWERS = { "BuffBarCooldownViewer", "BuffIconCooldownViewer" }   -- Tracked Bars, Tracked Buffs
local texts = setmetatable({}, { __mode = "k" })      -- item -> our count text
-- item -> its own stack text had an aura to show (a buff): true, false, or nil until its next refresh says (nothing
-- of ours is drawn then: a buff it already counts would show twice)
local theirs = setmetatable({}, { __mode = "k" })
local hooked = setmetatable({}, { __mode = "k" })     -- items and viewers already hooked
local lastError                                        -- the last failure, for /glide stacks (never silent)

--- The item's own stack text: a bar's on its icon, an icon item's on its Applications frame.
local function ownText(item)
    local icon = item.Icon
    if type(icon) == "table" and icon.Applications then return icon.Applications end
    local apps = item.Applications
    if type(apps) == "table" and apps.Applications then return apps.Applications end
end

--- Our text, laid over the item's own (its font, its bottom-right corner; sized by its own text: the item's is empty
--- whenever ours shows, and an icon item's sizes to its text): made the first time it's needed.
local function textFor(item)
    if texts[item] then return texts[item] end
    local own = ownText(item)
    local parent = own and own:GetParent()
    if not parent then return nil end
    local fs = parent:CreateFontString(nil, "OVERLAY")
    fs:SetFontObject(own:GetFontObject())
    fs:SetJustifyH(own:GetJustifyH())
    fs:SetPoint("BOTTOMRIGHT", own, "BOTTOMRIGHT", 0, 0)
    texts[item] = fs
    return fs
end

--- What to show: the aura's stacks when the item's own text had no aura (a debuff on you), else nothing. A secret
--- count (in combat) goes to the game as it is, never tested or compared (0, an aura that doesn't stack, shows
--- nothing; a single stack shows 1, as there is no telling 1 from more without reading it).
local function countFor(item)
    if theirs[item] ~= false then return "" end
    local aura = item.GetAuraDataCached and item:GetAuraDataCached()
    if type(aura) ~= "table" then return "" end
    local n = aura.applications
    if n == nil then return "" end
    if IsSecret(n) then
        local trunc = C_StringUtil and C_StringUtil.TruncateWhenZero
        if trunc then return trunc(n) end      -- (its result is secret too: handed on as it is)
        return n
    end
    return (type(n) == "number" and n > 0) and n or ""
end

local function update(item)
    local fs = texts[item]
    if not ns.StacksOn() then
        if fs then fs:SetText("") end
        return
    end
    fs = fs or textFor(item)
    if fs then fs:SetText(countFor(item)) end
end

local function safely(fn, item)
    local ok, err = pcall(fn, item)
    if not ok then
        if texts[item] then pcall(texts[item].SetText, texts[item], "") end
        lastError = tostring(err)
        if rec.debug then Print("|cffff5555stacks:|r", err) end
    end
end

local function hookItem(item)
    if hooked[item] or type(item) ~= "table" or not item.RefreshData then return end
    hooked[item] = true
    -- its own text first (did it have an aura?), then, once the item has found its aura, ours
    if item.RefreshApplications then
        hooksecurefunc(item, "RefreshApplications", function(self)
            theirs[self] = type(self.GetAuraDataCached and self:GetAuraDataCached()) == "table"
        end)
    end
    hooksecurefunc(item, "RefreshData", function(self) safely(update, self) end)
    if item.ResetCooldownData then           -- back to the pool: nothing of the last aura stays
        hooksecurefunc(item, "ResetCooldownData", function(self)
            theirs[self] = nil
            if texts[self] then texts[self]:SetText("") end
        end)
    end
end

local function eachItem(viewer, fn)
    local pool = viewer.itemFramePool
    if pool and pool.EnumerateActive then
        for item in pool:EnumerateActive() do fn(item) end
    end
end

local function hookViewers()
    if not hooksecurefunc then return end
    for _, name in ipairs(VIEWERS) do
        local viewer = _G[name]
        if type(viewer) == "table" and not hooked[viewer] and viewer.OnAcquireItemFrame then
            hooked[viewer] = true
            hooksecurefunc(viewer, "OnAcquireItemFrame", function(_, item) hookItem(item) end)
            eachItem(viewer, hookItem)
        end
    end
end

--- Switched on or off: every item shown now is updated at once.
function ns.SetStacks(on)
    settings().enabled = not not on
    for _, name in ipairs(VIEWERS) do
        local viewer = _G[name]
        if type(viewer) == "table" and hooked[viewer] then
            eachItem(viewer, function(item) safely(update, item) end)
        end
    end
    ns.OnSettingsChanged()
end

-- the Cooldown Manager is Blizzard's own addon: hooked once it is loaded (it may load before or after Glide)
hookViewers()
ns.On("ADDON_LOADED", function(name) if name == "Blizzard_CooldownViewer" then hookViewers() end end)
ns.On("PLAYER_LOGIN", hookViewers)

ns.Command("stacks", "stacks [on|off] - stack counts of debuffs on you (Arcane Blast) on the Cooldown Manager's bars and icons",
    function(rest)
        local a = (rest or ""):lower():match("^(%S*)")
        if a == "on" or a == "off" then ns.SetStacks(a == "on") end
        Print("stack counts on the Cooldown Manager", ns.OnOff(ns.StacksOn()))
        if lastError then Print("|cffff5555last problem:|r", lastError) end
    end)
