-- The Glide window (/glide, or a left-click on the minimap button): every switch the addon has, in one place, built
-- from the game's own widgets so it looks and behaves like the default UI. Escape closes it, it drags anywhere and
-- keeps its spot (GlideLinkDB.panel), and it stays current however a setting changes (ns.OnSettingsChange).
--
-- Only the game's basic templates are used (BackdropTemplate, UICheckButtonTemplate, UIPanelButtonTemplate,
-- UIPanelCloseButton): no Settings panel, no Menu system, no UIPanel manager. Opening those from addon code taints
-- the default UI on Forever, whose secret values then error in the character sheet and other Blizzard panels.
-- Every label is a font string of our own: a template's built-in label (.Text / .text, renamed across client
-- versions) is never relied on, so no option can show up without its text.
local _, ns = ...

local WIDTH, PAD, ROW, INDENT = 470, 24, 28, 22
local GOLD = { 1, 0.82, 0 }
local panel, rows, refreshers = nil, {}, {}

local function pdb() return ns.LinkSection("panel") end

local function sound(kit)
    if SOUNDKIT and SOUNDKIT[kit] and PlaySound then pcall(PlaySound, SOUNDKIT[kit]) end
end

local function text(parent, font, str, layer)
    local fs = parent:CreateFontString(nil, layer or "ARTWORK", font)
    fs:SetJustifyH("LEFT")
    fs:SetText(str or "")
    return fs
end

-- why: a function returning the reason the option is greyed out (nil while it's usable)
local function tooltip(owner, title, body, why)
    owner:SetScript("OnEnter", function(self)
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        GameTooltip:AddLine(title)
        if body then GameTooltip:AddLine(body, 1, 1, 1, true) end
        local reason = why and why()
        if reason then GameTooltip:AddLine(reason, 1, 0.3, 0.3, true) end
        GameTooltip:Show()
    end)
    owner:SetScript("OnLeave", function() GameTooltip:Hide() end)
end

-- ---------------------------------------------------------------------------------------------
-- widgets
-- ---------------------------------------------------------------------------------------------

-- A section title in gold with a hairline running to the window's edge.
local function section(y, title)
    local t = text(panel, "GameFontNormal", title)
    t:SetPoint("TOPLEFT", PAD, y)
    local line = panel:CreateTexture(nil, "ARTWORK")
    line:SetColorTexture(GOLD[1], GOLD[2], GOLD[3], 0.25)
    line:SetHeight(1)
    line:SetPoint("LEFT", t, "RIGHT", 10, 0)
    line:SetPoint("RIGHT", panel, "TOPRIGHT", -PAD, y - 6)
    return y - 24
end

-- A checkbox with our own label and a grey description beside it; the label and description are part of the
-- click area, hovering shows the description as a tooltip too. get() -> bool, set(bool). enabled() -> bool, or nil.
local function checkbox(y, x, opt)
    local cb = CreateFrame("CheckButton", nil, panel, "UICheckButtonTemplate")
    cb:SetSize(26, 26)
    cb:SetPoint("TOPLEFT", x, y + 3)
    local builtIn = cb.Text or cb.text
    if builtIn then builtIn:SetText(""); builtIn:Hide() end
    cb.label = text(cb, "GameFontHighlight", opt.name)
    cb.label:SetPoint("LEFT", cb, "RIGHT", 2, 1)
    if opt.desc then
        cb.desc = text(cb, "GameFontDisableSmall", opt.desc)
        cb.desc:SetPoint("LEFT", cb.label, "RIGHT", 8, 0)
        cb.desc:SetPoint("RIGHT", panel, "TOPRIGHT", -PAD, y - 9)
        cb.desc:SetWordWrap(false)
    end
    cb.opt = opt
    cb:SetScript("OnClick", function(self)
        local on = self:GetChecked() and true or false
        sound(on and "IG_MAINMENU_OPTION_CHECKBOX_ON" or "IG_MAINMENU_OPTION_CHECKBOX_OFF")
        opt.set(on)
    end)
    tooltip(cb, opt.name, opt.tip or opt.desc, opt.why)
    rows[#rows + 1] = cb
    refreshers[#refreshers + 1] = function()
        cb:SetChecked(opt.get() and true or false)
        local enabled = not opt.enabled or opt.enabled()
        cb:SetEnabled(enabled)
        cb.label:SetFontObject(enabled and "GameFontHighlight" or "GameFontDisable")
        -- the whole line answers the mouse, not just the box, up to the window's edge
        local w = cb.label:GetStringWidth() + (cb.desc and (cb.desc:GetStringWidth() + 8) or 0) + 6
        cb:SetHitRectInsets(0, -math.min(w, WIDTH - PAD - x - 26), 0, 0)
    end
    return y - ROW
end

-- "Label   [-]  value  [+]" for a number with a step of 1.
local function stepper(y, opt)
    local label = text(panel, "GameFontHighlight", opt.name)
    label:SetPoint("TOPLEFT", PAD + 28, y - 4)
    local minus = CreateFrame("Button", nil, panel, "UIPanelButtonTemplate")
    minus:SetSize(26, 22)
    minus:SetText("-")
    minus:SetPoint("TOPLEFT", PAD + 190, y)
    local value = text(panel, "GameFontHighlight", "")
    value:SetJustifyH("CENTER")
    value:SetWidth(52)
    value:SetPoint("LEFT", minus, "RIGHT", 4, 0)
    local plus = CreateFrame("Button", nil, panel, "UIPanelButtonTemplate")
    plus:SetSize(26, 22)
    plus:SetText("+")
    plus:SetPoint("LEFT", value, "RIGHT", 4, 0)
    local function step(d)
        sound("IG_MAINMENU_OPTION_CHECKBOX_ON")
        opt.set(opt.get() + d)
    end
    minus:SetScript("OnClick", function() step(-1) end)
    plus:SetScript("OnClick", function() step(1) end)
    tooltip(minus, opt.name, opt.tip, opt.why)
    tooltip(plus, opt.name, opt.tip, opt.why)
    local hint = text(panel, "GameFontDisableSmall", opt.desc)
    hint:SetPoint("TOPLEFT", PAD + 28, y - 26)
    hint:SetWidth(WIDTH - 2 * PAD - 28)
    hint:SetWordWrap(false)
    rows[#rows + 1] = { label = label, value = value, minus = minus, plus = plus, opt = opt }
    refreshers[#refreshers + 1] = function()
        local v = opt.get()
        local enabled = not opt.enabled or opt.enabled()
        value:SetText(opt.format and opt.format(v) or tostring(v))
        value:SetFontObject(enabled and "GameFontHighlight" or "GameFontDisable")
        label:SetFontObject(enabled and "GameFontHighlight" or "GameFontDisable")
        minus:SetEnabled(enabled and v > opt.min)
        plus:SetEnabled(enabled and v < opt.max)
    end
    return y - 52
end

-- ---------------------------------------------------------------------------------------------
-- the window
-- ---------------------------------------------------------------------------------------------
local function refresh()
    if not panel or not panel:IsShown() then return end
    local streaming = ns.StripOn and ns.StripOn()
    panel.status:SetText(streaming and "|cff55dd55Streaming to the Glide app|r"
        or "|cffff5555Data strip off: the Glide app can't see the game|r")
    for _, fn in ipairs(refreshers) do pcall(fn) end
end

local function savePosition()
    local point, _, rel, x, y = panel:GetPoint(1)
    local db = pdb()
    db.point, db.rel, db.x, db.y = point, rel, x, y
end

local function build()
    panel = CreateFrame("Frame", "GlideOptionsFrame", UIParent, BackdropTemplateMixin and "BackdropTemplate" or nil)
    panel:Hide()
    panel:SetWidth(WIDTH)
    panel:SetFrameStrata("DIALOG")
    panel:SetToplevel(true)
    panel:SetClampedToScreen(true)
    panel:EnableMouse(true)
    panel:SetMovable(true)
    panel:RegisterForDrag("LeftButton")
    panel:SetScript("OnDragStart", panel.StartMoving)
    panel:SetScript("OnDragStop", function(self) self:StopMovingOrSizing(); savePosition() end)
    panel:SetBackdrop({
        bgFile = "Interface\\DialogFrame\\UI-DialogBox-Background",
        edgeFile = "Interface\\DialogFrame\\UI-DialogBox-Border",
        tile = true, tileSize = 32, edgeSize = 32,
        insets = { left = 11, right = 12, top = 12, bottom = 11 },
    })
    -- the gold title ribbon of the game's dialogs
    local ribbon = panel:CreateTexture(nil, "ARTWORK")
    ribbon:SetTexture("Interface\\DialogFrame\\UI-DialogBox-Header")
    ribbon:SetSize(256, 64)
    ribbon:SetPoint("TOP", 0, 12)
    local title = text(panel, "GameFontNormal", "Glide", "OVERLAY")
    title:SetPoint("TOP", ribbon, "TOP", 0, -14)
    local close = CreateFrame("Button", nil, panel, "UIPanelCloseButton")
    close:SetPoint("TOPRIGHT", -6, -6)
    close:SetScript("OnClick", function() ns.TogglePanel(false) end)
    tinsert(UISpecialFrames, "GlideOptionsFrame")               -- Escape closes it, like the game's own windows

    panel.status = text(panel, "GameFontHighlightSmall", "")
    panel.status:SetPoint("TOPLEFT", PAD, -30)
    local version = text(panel, "GameFontDisableSmall", "v" .. tostring(ns.VERSION or "?"))
    version:SetPoint("TOPRIGHT", -PAD - 18, -30)

    local y = -58
    -- coach: the master switch, then each kind of commentary (greyed out while the master is off)
    y = section(y, "Coach")
    local flags = ns.COACH_FLAGS
    y = checkbox(y, PAD, { name = flags[1].name, desc = flags[1].desc,
        tip = "What the Glide app's coach may talk about. The switches only tell the app; the addon itself never speaks.",
        get = function() return ns.CoachOn("coach") end, set = function(v) ns.SetCoach("coach", v) end })
    local masterOn = function() return ns.CoachOn("coach") end
    local masterOff = function() return not masterOn() and "Turn on Coach commentary to change this." or nil end
    for i = 2, #flags do
        local f = flags[i]
        y = checkbox(y, PAD + INDENT, { name = f.name, desc = f.desc, enabled = masterOn, why = masterOff,
            get = function() return ns.CoachOn(f.key) end, set = function(v) ns.SetCoach(f.key, v) end })
    end
    local reset = CreateFrame("Button", nil, panel, "UIPanelButtonTemplate")
    reset:SetSize(150, 22)
    reset:SetText("Turn all on")
    reset:SetPoint("TOPLEFT", PAD + INDENT + 4, y - 2)
    reset:SetScript("OnClick", function() sound("IG_MAINMENU_OPTION_CHECKBOX_ON"); ns.ResetCoach() end)
    tooltip(reset, "Turn all on", "Every kind of commentary back on, the master switch included.")
    y = y - 40

    -- what the addon does by itself
    y = section(y, "Helpers")
    if ns.RepairOn then
        y = checkbox(y, PAD, { name = "Auto repair", desc = "Repair everything at a merchant who repairs",
            tip = "Uses your own gold, only when you can pay the whole bill. /glide repair repairs now.",
            get = ns.RepairOn, set = ns.SetRepair })
    end
    if ns.TrashOn then
        y = checkbox(y, PAD, { name = "Bag cleanup", desc = "When bags are full, delete the cheapest RestedXP junk",
            tip = "Only items RestedXP marks as junk, cheapest first, on your next click or key press. /glide trash list shows the pick.",
            get = ns.TrashOn, set = ns.SetTrash })
    end
    if ns.TrainAutoOn then
        y = checkbox(y, PAD, { name = "New spells on level up", desc = "Show what your trainer now teaches, and what it changes",
            tip = "Opens on a level up that brings new spells or ranks: each as the spellbook shows it, what the new "
                .. "rank changes, and its cost. /glide train opens it any time.",
            get = ns.TrainAutoOn, set = ns.SetTrainAuto })
    end
    y = y - 10

    -- the link to the desktop app
    y = section(y, "Link to the Glide app")
    y = checkbox(y, PAD, { name = "Data strip", desc = "The pixel strip in the top-left corner the app reads",
        tip = "Off, the Glide app sees nothing: no coaching, no live data. Your recording still runs.",
        get = function() return ns.StripOn() end, set = function(v) ns.SetStrip(v) end })
    y = stepper(y - 4, { name = "Strip cell size", min = ns.CELL_MIN, max = ns.CELL_MAX,
        enabled = function() return ns.StripOn() end,
        why = function() return not ns.StripOn() and "Turn on the data strip to change this." or nil end,
        desc = "Bigger cells read better on high-resolution or HDR screens.",
        tip = "The size of each strip cell in screen pixels. The default is 3.",
        format = function(v) return v .. " px" end,
        get = ns.StripCell, set = ns.SetCell })
    y = checkbox(y, PAD, { name = "Minimap button", desc = "Left-click opens this window, right-click the coach",
        get = function() return ns.MinimapShown() end, set = function(v) ns.SetMinimapShown(v) end })

    local foot = text(panel, "GameFontDisableSmall", "/glide help lists the chat commands. Drag the window to move it.")
    foot:SetPoint("TOPLEFT", PAD, y - 10)
    panel:SetHeight(-(y - 10) + 32)

    local db = pdb()
    panel:ClearAllPoints()
    if db.point then
        panel:SetPoint(db.point, UIParent, db.rel or db.point, db.x or 0, db.y or 0)
    else
        panel:SetPoint("CENTER", UIParent, "CENTER", 0, 60)
    end
    panel:SetScript("OnShow", function() sound("IG_CHARACTER_INFO_OPEN"); refresh() end)
    panel:SetScript("OnHide", function() sound("IG_CHARACTER_INFO_CLOSE") end)
end

--- Show or hide the Glide window; with no argument, toggle it.
function ns.TogglePanel(show)
    if not panel then build() end
    if show == nil then show = not panel:IsShown() end
    panel:SetShown(show)
end

ns.OnSettingsChange(refresh)
