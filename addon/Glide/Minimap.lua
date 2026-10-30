-- Glide minimap button: left-click opens the Glide window (Panel.lua), right-click turns coach commentary on or
-- off, drag moves it around the minimap. Its look says the state at a glance: grey when the coach is off, red
-- when the data strip is off (the companion sees nothing). Settings live in GlideLinkDB.minimap (angle, hide).
local _, ns = ...

local ICON = "Interface\\Icons\\Ability_Rogue_Sprint"
local button

local DEFAULTS = { angle = 200 }
local function mdb() return ns.LinkSection("minimap", DEFAULTS) end

function ns.MinimapShown() return not mdb().hide end

local function place()
    if not button then return end
    local a = math.rad(mdb().angle or 200)
    local r = (Minimap:GetWidth() / 2) + 5
    button:ClearAllPoints()
    button:SetPoint("CENTER", Minimap, "CENTER", math.cos(a) * r, math.sin(a) * r)
end

local function refresh()
    if not button then return end
    button.icon:SetDesaturated(not ns.CoachOn("coach"))
    if ns.StripOn and not ns.StripOn() then
        button.icon:SetVertexColor(1, 0.35, 0.35)        -- strip off: the companion is blind
    else
        button.icon:SetVertexColor(1, 1, 1)
    end
    button:SetShown(not mdb().hide)
end
ns.OnSettingsChange(refresh)

local ON, OFF = ns.OnOff(true), ns.OnOff(false)

local function tooltip(self)
    GameTooltip:SetOwner(self, "ANCHOR_LEFT")
    GameTooltip:AddLine("Glide")
    GameTooltip:AddDoubleLine("Coach commentary", ns.CoachOn("coach") and ON or OFF, 1, 1, 1)
    GameTooltip:AddDoubleLine("Data strip", ns.StripOn() and "|cff55dd55streaming|r" or OFF, 1, 1, 1)
    local off = {}
    for i = 2, #ns.COACH_FLAGS do
        local f = ns.COACH_FLAGS[i]
        if not ns.CoachOn(f.key) then off[#off + 1] = f.name end
    end
    if #off > 0 and ns.CoachOn("coach") then GameTooltip:AddLine("Muted: " .. table.concat(off, ", "), 0.8, 0.8, 0.8, true) end
    GameTooltip:AddLine(" ")
    GameTooltip:AddLine("|cffffd200Left-click|r  settings", 0.8, 0.8, 0.8)
    GameTooltip:AddLine("|cffffd200Right-click|r  coach commentary on or off", 0.8, 0.8, 0.8)
    GameTooltip:AddLine("|cffffd200Drag|r  move this button", 0.8, 0.8, 0.8)
    GameTooltip:Show()
end

local function create()
    if button or not Minimap then return end
    button = CreateFrame("Button", "GlideMinimapButton", Minimap)
    button:SetSize(31, 31)
    button:SetFrameStrata("MEDIUM")
    button:SetFrameLevel(8)
    button:RegisterForClicks("LeftButtonUp", "RightButtonUp")
    button:RegisterForDrag("LeftButton")
    button:SetHighlightTexture("Interface\\Minimap\\UI-Minimap-ZoomButton-Highlight")
    local bg = button:CreateTexture(nil, "BACKGROUND")
    bg:SetSize(20, 20); bg:SetPoint("TOPLEFT", 7, -5)
    bg:SetTexture("Interface\\Minimap\\UI-Minimap-Background")
    local icon = button:CreateTexture(nil, "ARTWORK")
    icon:SetSize(17, 17); icon:SetPoint("TOPLEFT", 7, -6)
    icon:SetTexture(ICON)
    icon:SetTexCoord(0.08, 0.92, 0.08, 0.92)
    button.icon = icon
    local border = button:CreateTexture(nil, "OVERLAY")
    border:SetSize(53, 53); border:SetPoint("TOPLEFT")
    border:SetTexture("Interface\\Minimap\\MiniMap-TrackingBorder")

    button:SetScript("OnClick", function(self, which)
        if which == "RightButton" then
            local on = not ns.CoachOn("coach")
            ns.SetCoach("coach", on)
            ns.Print("coach commentary " .. (on and ON or OFF))
        else
            ns.TogglePanel()
        end
        if GameTooltip:IsOwned(self) then tooltip(self) end
    end)
    button:SetScript("OnDragStart", function(self)
        GameTooltip:Hide()
        self:SetScript("OnUpdate", function()
            local mx, my = Minimap:GetCenter()
            local px, py = GetCursorPosition()
            local s = Minimap:GetEffectiveScale()
            mdb().angle = math.deg(math.atan2(py / s - my, px / s - mx)) % 360
            place()
        end)
    end)
    button:SetScript("OnDragStop", function(self) self:SetScript("OnUpdate", nil) end)
    button:SetScript("OnEnter", tooltip)
    button:SetScript("OnLeave", function() GameTooltip:Hide() end)
    place()
    refresh()
end

function ns.SetMinimapShown(shown)
    mdb().hide = not shown
    if shown and not button then create() end
    ns.OnSettingsChanged()
end

function ns.MinimapCommand(arg)
    arg = (arg or ""):lower()
    local shown
    if arg == "on" or arg == "show" then shown = true
    elseif arg == "off" or arg == "hide" then shown = false
    else shown = not ns.MinimapShown() end
    ns.SetMinimapShown(shown)
    ns.Print("minimap button " .. (shown and "shown" or "hidden (/glide minimap on, or the Glide window)"))
end

ns.On("PLAYER_LOGIN", create)

ns.Command("minimap", "minimap [on|off] - show or hide the minimap button", function(a) ns.MinimapCommand(a) end)
