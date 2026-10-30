-- GlideRecorder: copy the RestedXP guides you own into GlideRecorderDB.rxp so the PC side can read
-- the full route (all races/classes, not only the steps that apply to this character).
--   1. imported/premium guides are cached by RXP as deflate-compressed text in RXP.db.profile.guides
--   2. any guide text RXP parses after we load is captured by a post-hook on RXP.ParseGuide
--   3. as a fallback, the parsed steps of every registered guide are serialised
-- Personal use only: RestedXP content is licensed to you; do not redistribute the dump.
local _, ns = ...
local rec = ns.rec           -- the recorder's namespace (Core.lua), kept apart from the live-link code

local function rxp() return _G.RXP end

local function decompress(data)
    if type(data) ~= "string" then return nil end
    if C_EncodingUtil and C_EncodingUtil.DecompressString then
        local method = Enum and Enum.CompressionMethod and Enum.CompressionMethod.Deflate or 0
        local ok, out = pcall(C_EncodingUtil.DecompressString, data, method)
        if ok and type(out) == "string" and #out > 0 then return out end
    end
    local LD = LibStub and LibStub("LibDeflate", true)
    if LD then
        local out = LD:DecompressDeflate(data)
        if out then return out end
    end
    return nil
end

local function meta(text)
    return text:match("#name%s+([^\r\n]+)"), text:match("#group%s+([^\r\n]+)")
end

local function store(text, how)
    if type(text) ~= "string" or #text < 200 or not text:find("step") then return false end
    local db = rec.db
    if not db then return false end
    local name, group = meta(text)
    local key = (group or "?") .. "||" .. (name or tostring(#text))
    local old = db.rxp[key]
    if old and old.len == #text then return false end
    db.rxp[key] = { name = name, group = group, text = text, len = #text, how = how, t = rec.Now() }
    return true
end

-- 2. capture raw guide text whenever RXP parses one (premium guides parse lazily when selected)
local hooked = false
local function hook()
    local R = rxp()
    if hooked or not R or type(R.ParseGuide) ~= "function" then return end
    hooksecurefunc(R, "ParseGuide", function(groupOrContent, text)
        if type(text) == "string" and #text > 200 then
            store(text, "parse")
        elseif type(groupOrContent) == "string" and #groupOrContent > 200 then
            store(groupOrContent, "parse")
        end
    end)
    hooked = true
end
hook()
rec.On("ADDON_LOADED", function() hook() end)

local function copyScalars(src, depth)
    local out = {}
    for k, v in pairs(src) do
        local tk, tv = type(k), type(v)
        if (tk == "string" or tk == "number") and k ~= "step" and k ~= "parent" then
            if tv == "string" or tv == "number" or tv == "boolean" then
                out[k] = v
            elseif tv == "table" and depth > 0 then
                out[k] = copyScalars(v, depth - 1)
            end
        end
    end
    return out
end

local function serialiseGuide(g)
    local steps = {}
    for i, step in ipairs(g.steps or {}) do
        local st = copyScalars(step, 0)
        st.elements = {}
        for j, el in ipairs(step.elements or {}) do st.elements[j] = copyScalars(el, 1) end
        steps[i] = st
    end
    return { name = g.name, group = g.group, next = g.next, key = g.key, steps = steps }
end

-- the whole dump; returns (stored, cached, serialised, registered) or nil when RestedXP isn't loaded
local function dumpAll()
    local R = rxp()
    if not R then return nil end
    hook()
    local cached, parsed, steps = 0, 0, 0
    -- 1. compressed guide cache (imported / premium guides)
    local profile = R.db and R.db.profile and R.db.profile.guides
    if profile then
        for _, data in pairs(profile) do
            local blob = type(data) == "table" and data.groupOrContent or data
            local text = decompress(blob)
            if text and store(text, "cache") then cached = cached + 1 end
        end
    end
    -- 3. parsed steps of every registered guide (character-specific, but always available)
    rec.db.rxpSteps = rec.db.rxpSteps or {}
    -- ParseGuide writes some RXP state as a side effect; put it back afterwards
    local saved = {}
    local stateKeys = { "guide", "currentGuideGroup", "currentGuideName", "lastObjective", "lastElement", "step" }
    for _, k in ipairs(stateKeys) do saved[k] = R[k] end
    for _, g in ipairs(R.guides or {}) do
        local guide = g
        if (not guide.steps or #guide.steps == 0) and guide.key and R.guideCache and R.guideCache[guide.key] then
            local ok, res = pcall(R.guideCache[guide.key], guide)
            if ok and type(res) == "table" then guide = res end
        end
        if guide.steps and #guide.steps > 0 and guide.name then
            local k = (guide.group or "?") .. "||" .. guide.name
            rec.db.rxpSteps[k] = serialiseGuide(guide)
            steps = steps + 1
        end
        parsed = parsed + 1
    end
    for _, k in ipairs(stateKeys) do R[k] = saved[k] end
    local n = 0
    for _ in pairs(rec.db.rxp) do n = n + 1 end
    return n, cached, steps, parsed
end
rec.RXPDumpAll = dumpAll

ns.Command("rxpdump", "rxpdump - copy your RestedXP guides into the recorder (then /glide save)", function()
    local n, cached, steps, parsed = dumpAll()
    if not n then rec.Print("RestedXP (RXPGuides) is not loaded."); return end
    rec.Print(("RestedXP: %d guide texts stored (%d new from cache), %d of %d guides serialised as steps.")
        :format(n, cached, steps, parsed))
    rec.Print("Type /glide save (reload) or log out to write it to disk.")
end)

-- 4. automatic dump when RestedXP was updated: the first login after a new RestedXP version copies its guide
-- cache once (out of combat, 15 s after login), so the PC side gets the new guide texts at the next logout.
local function rxpVersion()
    local get = (C_AddOns and C_AddOns.GetAddOnMetadata) or _G.GetAddOnMetadata
    local ok, v = pcall(get, "RXPGuides", "Version")
    return ok and type(v) == "string" and v or nil
end
rec.RXPVersion = rxpVersion

function rec.RXPAutoDump()
    local ver = rxpVersion()
    if not ver or not rec.db or not rxp() then return false end
    if rec.db.rxpVersion == ver then return false end
    if InCombatLockdown and InCombatLockdown() then
        if C_Timer then C_Timer.After(30, rec.RXPAutoDump) end
        return false
    end
    local ok, n, cached = pcall(dumpAll)
    if not ok or not n then return false end
    local was = rec.db.rxpVersion
    rec.db.rxpVersion = ver
    rec.Print(("RestedXP %s%s: %d guide texts captured (%d from its cache); saved at logout or /reload.")
        :format(was and (was .. " -> ") or "", ver, n, cached or 0))
    return true
end
rec.On("PLAYER_LOGIN", function()
    if C_Timer then C_Timer.After(15, rec.RXPAutoDump) end
end)

-- current RXP position, used by the live exporter later and recorded with quest events now
function rec.RXPCurrent()
    local R = rxp()
    if not R or not R.currentGuide then return nil end
    local step = R.GetGuideProgress and select(1, R.GetGuideProgress())
    return { guide = R.currentGuide.name, group = R.currentGuide.group,
             step = RXPCData and RXPCData.currentStep or step }
end

rec.On("QUEST_TURNED_IN", function(qid)
    local q = rec.session and rec.session.quests[tonumber(qid)]
    if q and q.turned then q.turned.rxp = rec.RXPCurrent() end
end)
