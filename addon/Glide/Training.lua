-- New at your trainer (0.16): when a level brings spells or ranks your class trainer teaches, a window shows them the
-- way the spellbook does (the icon, the name and rank, the spell's own text), each with what the new rank changes
-- against the rank you know now (the numbers that changed, and by how much), its cost once a trainer has shown it to
-- you (the recorder keeps every trainer's list), the total and your gold. It decides nothing: you see whether the
-- trip to town is worth it. Hovering a spell shows the game's own tooltip for it.
--
-- The list is Extras.lua's (RestedXP's training list for the class and race, less what the spellbook has). The
-- window opens by itself on a level up that brought new spells (never in combat: it waits), unless it's switched off
-- (GlideLinkDB.train.auto, the Glide window's "New spells on level up"); /glide train opens it any time.
local _, ns = ...

local Safe = ns.Safe
local WIDTH, PAD, ROW_H, VISIBLE = 470, 22, 92, 5
local frame, rows = nil, {}
local wantOpen = false              -- a level up brought new spells; open once out of combat

local function tdb() return ns.LinkSection("train", { auto = true }) end
function ns.TrainAutoOn() return tdb().auto ~= false end
function ns.SetTrainAuto(v) tdb().auto = v and true or false; ns.OnSettingsChanged() end

local function call(fn, ...)
    if not fn then return nil end
    local ok, a, b, c = pcall(fn, ...)
    if ok then return a, b, c end
    return nil
end

local function description(id)
    local d = call(C_Spell and C_Spell.GetSpellDescription, id) or call(GetSpellDescription, id)
    return Safe(d) or ""
end

local function icon(id)
    return call(C_Spell and C_Spell.GetSpellTexture, id) or call(GetSpellTexture, id) or 134400   -- the question mark
end

-- "a b c" -> the numbers in a spell's text, in order
local function numbers(text)
    local out = {}
    for n in text:gsub(",", ""):gmatch("%d+%.?%d*") do out[#out + 1] = tonumber(n) end
    return out
end

local function fmt(n)
    if n == math.floor(n) then return tostring(n) end
    return ("%.1f"):format(n)
end

--- What a new rank changes against the known one: "11 -> 21 (+91%)", the changed numbers in order (at most four),
--- the percentage of the first; nil when the texts don't line up (a new spell, or a different shape of text).
function ns.RankChange(old, new)
    local a, b = numbers(old or ""), numbers(new or "")
    if #a == 0 or #a ~= #b then return nil end
    local parts, first = {}, nil
    for i = 1, #a do
        if a[i] ~= b[i] then
            if #parts < 4 then parts[#parts + 1] = fmt(a[i]) .. " -> " .. fmt(b[i]) end
            if not first and a[i] > 0 then first = (b[i] - a[i]) / a[i] end
        end
    end
    if #parts == 0 then return nil end
    local pct = first and (" (%s%d%%)"):format(first >= 0 and "+" or "", math.floor(first * 100 + 0.5)) or ""
    return table.concat(parts, ", ") .. pct
end

-- name..rank -> copper, from every trainer the recorder has seen (the newest session wins)
local function knownCosts()
    local costs, db = {}, GlideRecorderDB
    if type(db) ~= "table" or type(db.sessions) ~= "table" then return costs end
    local ids = {}
    for id in pairs(db.sessions) do ids[#ids + 1] = id end
    table.sort(ids)
    for _, sid in ipairs(ids) do
        local trainers = type(db.sessions[sid]) == "table" and db.sessions[sid].trainers
        for _, t in pairs(type(trainers) == "table" and trainers or {}) do
            for _, s in ipairs(type(t) == "table" and type(t.services) == "table" and t.services or {}) do
                if s.n and type(s.cost) == "number" then costs[s.n .. "|" .. (s.r or "")] = s.cost end
            end
        end
    end
    return costs
end

local money = ns.Money

local function rankText(rank) return (rank and rank > 0) and ("Rank " .. rank) or "" end

-- ---------------------------------------------------------------------------------------------
-- the window
-- ---------------------------------------------------------------------------------------------
local function text(parent, font, str, layer)
    local fs = parent:CreateFontString(nil, layer or "ARTWORK", font)
    fs:SetJustifyH("LEFT")
    fs:SetText(str or "")
    return fs
end

local function savePosition()
    local point, _, rel, x, y = frame:GetPoint(1)
    local db = tdb()
    db.point, db.rel, db.x, db.y = point, rel, x, y
end

local function row(i)
    if rows[i] then return rows[i] end
    local r = CreateFrame("Frame", nil, frame.list)
    r:SetSize(WIDTH - 2 * PAD - 8, ROW_H)
    r:SetPoint("TOPLEFT", 0, -(i - 1) * ROW_H)
    r:EnableMouse(true)
    r.icon = r:CreateTexture(nil, "ARTWORK")
    r.icon:SetSize(40, 40)
    r.icon:SetPoint("TOPLEFT", 0, -6)
    r.border = r:CreateTexture(nil, "OVERLAY")                -- the spellbook's square slot around the icon
    r.border:SetTexture("Interface\\Buttons\\UI-Quickslot2")
    r.border:SetSize(66, 66)
    r.border:SetPoint("CENTER", r.icon, "CENTER", 0, 0)
    r.name = text(r, "GameFontNormal", "")
    r.name:SetPoint("TOPLEFT", r.icon, "TOPRIGHT", 12, 0)
    r.rank = text(r, "GameFontDisableSmall", "")
    r.rank:SetPoint("LEFT", r.name, "RIGHT", 8, 0)
    r.cost = text(r, "GameFontHighlightSmall", "")
    r.cost:SetJustifyH("RIGHT")
    r.cost:SetPoint("TOPRIGHT", 0, -2)
    r.change = text(r, "GameFontHighlightSmall", "")
    r.change:SetPoint("TOPLEFT", r.name, "BOTTOMLEFT", 0, -4)
    r.desc = text(r, "GameFontHighlightSmall", "")
    r.desc:SetPoint("TOPLEFT", r.change, "BOTTOMLEFT", 0, -3)
    r.desc:SetWidth(WIDTH - 2 * PAD - 70)
    r.desc:SetWordWrap(true)
    if r.desc.SetMaxLines then r.desc:SetMaxLines(3) end
    r.desc:SetTextColor(0.82, 0.8, 0.72)
    r:SetScript("OnEnter", function(self)
        if not self.id then return end
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        if GameTooltip.SetSpellByID then pcall(GameTooltip.SetSpellByID, GameTooltip, self.id) end
        GameTooltip:Show()
    end)
    r:SetScript("OnLeave", function() GameTooltip:Hide() end)
    rows[i] = r
    return r
end

local function refresh()
    if not frame then return end
    local t = ns.training or { spells = {} }
    local spells = t.spells or {}
    local costs, total, priced = knownCosts(), 0, 0
    for i, s in ipairs(spells) do
        local r = row(i)
        r.id = s.id
        r.icon:SetTexture(icon(s.id))
        r.name:SetText(s.name or ("Spell " .. s.id))
        r.rank:SetText(rankText(s.rank) .. (s.level and ("  (level " .. s.level .. ")") or ""))
        local new = description(s.id)
        r.desc:SetText(new ~= "" and new or "Hover for the spell's tooltip.")
        local change = s.known and ns.RankChange(description(s.known), new)
        r.change:SetText(change and ("|cff40ff40" .. change .. "|r")
            or (s.known and "|cffbbbbbbA higher rank of a spell you know|r" or "|cffffd100New spell|r"))
        local cost = s.name and costs[s.name .. "|" .. rankText(s.rank)]
        if cost then total, priced = total + cost, priced + 1 end
        r.cost:SetText(cost and money(cost) or "|cff888888cost: ask a trainer|r")
        r:Show()
    end
    for i = #spells + 1, #rows do rows[i]:Hide() end
    frame.list:SetHeight(math.max(1, #spells) * ROW_H)
    frame.scroll:SetVerticalScroll(0)
    local n = #spells
    frame.sub:SetText(n == 0 and "Nothing new at your trainer right now."
        or ("Level %s: %d spell%s and rank%s to learn"):format(tostring(t.level or "?"), n, n == 1 and "" or "s", n == 1 and "" or "s"))
    local gold = Safe(GetMoney and GetMoney()) or 0
    frame.total:SetText(n == 0 and "" or (priced == n and ("All of it: %s.  You have %s."):format(money(total), money(gold))
        or priced > 0 and ("Known costs: %s for %d of %d.  You have %s."):format(money(total), priced, n, money(gold))
        or ("You have %s. Costs show once a trainer has shown you them."):format(money(gold))))
end
ns.RefreshTraining = refresh

local function build()
    frame = CreateFrame("Frame", "GlideTrainingFrame", UIParent, BackdropTemplateMixin and "BackdropTemplate" or nil)
    frame:Hide()
    frame:SetSize(WIDTH, 96 + VISIBLE * ROW_H)
    frame:SetFrameStrata("DIALOG")
    frame:SetToplevel(true)
    frame:SetClampedToScreen(true)
    frame:EnableMouse(true)
    frame:SetMovable(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", frame.StartMoving)
    frame:SetScript("OnDragStop", function(self) self:StopMovingOrSizing(); savePosition() end)
    frame:SetBackdrop({
        bgFile = "Interface\\DialogFrame\\UI-DialogBox-Background",
        edgeFile = "Interface\\DialogFrame\\UI-DialogBox-Border",
        tile = true, tileSize = 32, edgeSize = 32,
        insets = { left = 11, right = 12, top = 12, bottom = 11 },
    })
    local ribbon = frame:CreateTexture(nil, "ARTWORK")
    ribbon:SetTexture("Interface\\DialogFrame\\UI-DialogBox-Header")
    ribbon:SetSize(300, 64)
    ribbon:SetPoint("TOP", 0, 12)
    local title = text(frame, "GameFontNormal", "New at your trainer", "OVERLAY")
    title:SetPoint("TOP", ribbon, "TOP", 0, -14)
    local close = CreateFrame("Button", nil, frame, "UIPanelCloseButton")
    close:SetPoint("TOPRIGHT", -6, -6)
    close:SetScript("OnClick", function() ns.ToggleTraining(false) end)
    tinsert(UISpecialFrames, "GlideTrainingFrame")              -- Escape closes it, like the game's own windows

    frame.sub = text(frame, "GameFontHighlight", "")
    frame.sub:SetPoint("TOPLEFT", PAD, -32)
    frame.scroll = CreateFrame("ScrollFrame", nil, frame)
    frame.scroll:SetPoint("TOPLEFT", PAD, -58)
    frame.scroll:SetSize(WIDTH - 2 * PAD, VISIBLE * ROW_H)
    frame.list = CreateFrame("Frame", nil, frame.scroll)
    frame.list:SetSize(WIDTH - 2 * PAD, ROW_H)
    frame.scroll:SetScrollChild(frame.list)
    frame.scroll:EnableMouseWheel(true)
    frame.scroll:SetScript("OnMouseWheel", function(self, delta)
        local max = math.max(0, frame.list:GetHeight() - self:GetHeight())
        self:SetVerticalScroll(math.min(max, math.max(0, self:GetVerticalScroll() - delta * ROW_H)))
    end)
    frame.total = text(frame, "GameFontHighlightSmall", "")
    frame.total:SetPoint("BOTTOMLEFT", PAD, 34)
    local foot = text(frame, "GameFontDisableSmall", "Hover a spell for its tooltip. /glide train opens this window.")
    foot:SetPoint("BOTTOMLEFT", PAD, 18)

    local db = tdb()
    frame:ClearAllPoints()
    if db.point then
        frame:SetPoint(db.point, UIParent, db.rel or db.point, db.x or 0, db.y or 0)
    else
        frame:SetPoint("CENTER", UIParent, "CENTER", 0, 40)
    end
    frame:SetScript("OnShow", function()
        if SOUNDKIT and SOUNDKIT.IG_SPELLBOOK_OPEN and PlaySound then pcall(PlaySound, SOUNDKIT.IG_SPELLBOOK_OPEN) end
        refresh()
    end)
end

--- Show or hide the new-at-your-trainer window; with no argument, toggle it.
function ns.ToggleTraining(show)
    if not frame then build() end
    if show == nil then show = not frame:IsShown() end
    frame:SetShown(show)
    if show then refresh() end
end

-- a level up: open once the list is rebuilt for the new level, if it has spells taught at it (and not in combat)
local leveledTo
ns.On("PLAYER_LEVEL_UP", function(level) leveledTo = Safe(tonumber(level)) end)
table.insert(ns.trainingHooks, function(t)
    if frame and frame:IsShown() then refresh() end
    if not leveledTo or t.level ~= leveledTo then return end
    leveledTo = nil
    local fresh = false
    for _, s in ipairs(t.spells or {}) do
        if s.level == t.level then fresh = true end
    end
    if fresh and ns.TrainAutoOn() then
        if InCombatLockdown and InCombatLockdown() then wantOpen = true else ns.ToggleTraining(true) end
    end
end)
ns.On("PLAYER_REGEN_ENABLED", function()
    if wantOpen then wantOpen = false; ns.ToggleTraining(true) end
end)
-- spell texts load on demand: show them as they arrive
ns.On("SPELL_DATA_LOAD_RESULT", function() if frame and frame:IsShown() then refresh() end end)

ns.Command("train", "train - the spells and ranks your trainer teaches you now, with what they change and cost",
    function() ns.ToggleTraining(true) end)
