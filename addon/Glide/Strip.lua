-- GlideLink strip: a grid of solid-colour cells at the top-left corner of the game window,
-- sized in physical pixels (independent of UI scale). The companion app reads it from the screen.
local ADDON, ns = ...

local DEFAULTS = { cols = 96, rows = 8, cell = 3, fps = 5, enabled = true }
local cfg
local strip, cells = nil, {}
local seq = 0
local MARK1 = { 1, 0, 1 }   -- magenta
local MARK2 = { 1, 1, 0 }   -- yellow: the binary strip (cyan marked the old 6-bit one)

-- Each 6-bit symbol is drawn as two on/off cells, so the strip shows 2 * cfg.rows rows for the capacity of cfg.rows.
local function shownRows() return cfg.rows * 2 end

local function capacityBytes()
    return math.floor((cfg.cols * cfg.rows - 2) / 4) * 3   -- 4 cells carry 3 bytes
end

-- the grid: cols x shownRows() cells, black but for the two marker cells (the frame paints the rest)
local function layoutCells()
    for _, t in ipairs(cells) do t:Hide() end
    for i = 1, cfg.cols * shownRows() do
        local t = cells[i]
        if not t then
            t = strip:CreateTexture(nil, "OVERLAY")
            if t.SetSnapToPixelGrid then t:SetSnapToPixelGrid(false); t:SetTexelSnappingBias(0) end
            cells[i] = t
        end
        local c = (i - 1) % cfg.cols
        local r = math.floor((i - 1) / cfg.cols)
        t:ClearAllPoints()
        t:SetPoint("TOPLEFT", strip, "TOPLEFT", c * cfg.cell, -r * cfg.cell)
        t:SetSize(cfg.cell, cfg.cell)
        t:SetColorTexture(0, 0, 0, 1)
        t:Show()
    end
    cells[1]:SetColorTexture(MARK1[1], MARK1[2], MARK1[3], 1)
    cells[2]:SetColorTexture(MARK2[1], MARK2[2], MARK2[3], 1)
end

-- gauges under the grid: health, power, target health. Forever hides these numbers from addons
-- ("secret values"), but a StatusBar may still display them; the companion measures the fill.
local GAUGE_COLORS = { { 0, 1, 0 }, { 0, 0, 1 }, { 1, 0, 0 } }
local function layoutGauges()
    for i = 1, 3 do
        local g = ns.gauges[i]
        if not g then
            g = CreateFrame("StatusBar", nil, strip)
            g:SetStatusBarTexture("Interface\\Buttons\\WHITE8X8")
            local bg = g:CreateTexture(nil, "BACKGROUND")
            bg:SetAllPoints(); bg:SetColorTexture(0, 0, 0, 1)
            local tex = g:GetStatusBarTexture()
            if tex and tex.SetSnapToPixelGrid then tex:SetSnapToPixelGrid(false); tex:SetTexelSnappingBias(0) end
            ns.gauges[i] = g
        end
        local c = GAUGE_COLORS[i]
        g:SetStatusBarColor(c[1], c[2], c[3], 1)
        g:ClearAllPoints()
        g:SetPoint("TOPLEFT", strip, "TOPLEFT", 0, -(shownRows() * cfg.cell + (i - 1) * 3))
        g:SetSize(200, 3)
        g:SetMinMaxValues(0, 1); g:SetValue(0)
    end
end

local function build()
    local _, physH = GetPhysicalScreenSize()
    if not strip then
        strip = CreateFrame("Frame", "GlideLinkStrip", WorldFrame)
        strip:SetFrameStrata("TOOLTIP")
        strip:SetFrameLevel(10000)
        strip:SetIgnoreParentScale(true)
    end
    strip:SetScale(768 / physH)                 -- 1 UI unit == 1 physical pixel
    strip:ClearAllPoints()
    strip:SetPoint("TOPLEFT", WorldFrame, "TOPLEFT", 0, 0)
    strip:SetSize(cfg.cols * cfg.cell, shownRows() * cfg.cell)
    layoutCells()
    ns.gauges = ns.gauges or {}
    local okG, errG = pcall(layoutGauges)
    if not okG then ns.gaugeError = errG end
    strip:SetSize(cfg.cols * cfg.cell, shownRows() * cfg.cell + 9)
    strip:SetShown(cfg.enabled)
end

local function setGauge(g, unit, kind)
    if not g then return end
    local ok = pcall(function()
        if not UnitExists(unit) then error("no unit") end
        if kind == "h" then
            g:SetMinMaxValues(0, UnitHealthMax(unit)); g:SetValue(UnitHealth(unit))
        else
            g:SetMinMaxValues(0, UnitPowerMax(unit)); g:SetValue(UnitPower(unit))
        end
    end)
    if not ok then g:SetMinMaxValues(0, 1); g:SetValue(0) end
    return ok
end

local function updateGauges()
    local g = ns.gauges
    if not g then return end
    -- gk tells the companion which bars hold a real value (bit 1 health, 2 power, 4 target health)
    ns.gaugeMask = (setGauge(g[1], "player", "h") and 1 or 0) + (setGauge(g[2], "player", "p") and 2 or 0)
        + (setGauge(g[3], "target", "h") and 4 or 0)
end

-- one strip frame: the gauges, the core and the next page (State.lua builds the pages on its own clock)
local function frame()
    if not cfg.enabled then return end
    pcall(updateGauges)
    local core = ns.Core()
    -- room for the page: capacity - 6 framing bytes - core - "|" (a page that doesn't fit waits)
    local payload = core .. "|" .. (ns.NextPage(capacityBytes() - 7 - #core) or "")
    seq = (seq + 1) % 256
    local syms = ns.Encode(seq, payload, capacityBytes())
    local n = cfg.cols * shownRows()
    for i = 3, n - 1, 2 do
        local s = syms[(i - 1) / 2] or 0
        local r, g, b = ns.BitsColor(math.floor(s / 8))
        cells[i]:SetColorTexture(r, g, b, 1)
        r, g, b = ns.BitsColor(s % 8)
        cells[i + 1]:SetColorTexture(r, g, b, 1)
    end
    ns.lastPayload = payload
end

local function start()
    if ns.ticker then ns.ticker:Cancel() end
    ns.ticker = C_Timer.NewTicker(1 / cfg.fps, function()
        local ok, err = pcall(frame)
        if not ok then ns.ReportError("strip: " .. tostring(err)) end
    end)
end

-- the screen or UI scale changed (or another addon loaded): lay the strip out again
local function relayout() if cfg and strip then build() end end

ns.On("ADDON_LOADED", function(name)
    if name ~= ADDON then return relayout() end
    cfg = ns.LinkDB()
    for k, v in pairs(DEFAULTS) do if cfg[k] == nil then cfg[k] = v end end
end)
ns.On("PLAYER_LOGIN", function()
    build()
    start()
    ns.Print(("v%s: strip %s (%dx%d cells @%dpx, %d fps). /glide or the minimap button for settings.")
        :format(ns.VERSION or "?", cfg.enabled and "streaming" or "off", cfg.cols, shownRows(), cfg.cell, cfg.fps))
end)
ns.On("DISPLAY_SIZE_CHANGED", relayout)
ns.On("UI_SCALE_CHANGED", relayout)

function ns.SetStrip(on)
    if not cfg then return end
    cfg.enabled = on and true or false
    build()
    ns.OnSettingsChanged()
end
function ns.StripOn() return cfg and cfg.enabled end

-- the strip's cell size in physical pixels (1-8); the companion measures it, so any size reads
ns.CELL_MIN, ns.CELL_MAX = 1, 8
function ns.StripCell() return cfg and cfg.cell or DEFAULTS.cell end
function ns.SetCell(n)
    if not cfg then return end
    cfg.cell = math.max(ns.CELL_MIN, math.min(ns.CELL_MAX, math.floor(tonumber(n) or DEFAULTS.cell)))
    build()
    ns.OnSettingsChanged()
end

-- ---------------------------------------------------------------------------------------------
-- commands (Core.lua's registry; "/glidelink on|off" switches the strip too)
-- ---------------------------------------------------------------------------------------------
local function switchStrip(on)
    ns.SetStrip(on)
    ns.Print("strip " .. (cfg.enabled and "on" or "off (the companion sees nothing)"))
end

ns.Command("strip", "strip [on|off] - the data strip the companion reads (also /glidelink on|off)", function(a)
    switchStrip(a == "on" or (a ~= "off" and not cfg.enabled))           -- no on/off: toggle
end, { "link" })
ns.Command("cell", "cell <1-8> - strip cell size in screen pixels", function(a)
    if not tonumber(a) then return false end
    ns.SetCell(a)
end)
ns.Command("fps", "fps <1-15> - strip frame rate", function(a)
    if not tonumber(a) then return false end
    cfg.fps = math.max(1, math.min(15, tonumber(a))); start()
end)
ns.Command("dump", "dump - what the strip is sending", function()
    ns.Print(ns.lastPayload or "(nothing yet)")
    print(("  v%s strip: %s, %dx%d cells @%dpx, %d fps, capacity %d bytes"):format(ns.VERSION or "?",
        cfg.enabled and "on" or "off", cfg.cols, shownRows(), cfg.cell, cfg.fps, capacityBytes()))
    if ns.lastError then print(("errors: %d, the last: %s"):format(ns.errors, ns.lastError)) end
    if #ns.missingEvents > 0 then print("events this client doesn't have:", table.concat(ns.missingEvents, ", ")) end
    if ns.gaugeError then print("gauge error:", ns.gaugeError) end
end)
