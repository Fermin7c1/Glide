-- Glide coach switches: what the companion's coach is allowed to talk about.
-- Stored in GlideLinkDB.coach and sent to the companion every frame as a bitmask (core key "cf").
-- The addon itself never acts on these; it only reports them.
local _, ns = ...

-- name and desc: the Glide window's checkboxes; label: /coach status
local FLAGS = {
    { key = "coach",  bit = 1,   name = "Coach commentary", desc = "The master switch: off mutes everything below",
      label = "all coach commentary (master switch)" },
    { key = "route",  bit = 2,   name = "Route",    desc = "Next step, turn-ins, off route, stuck",
      label = "route: next step, turn-ins, off-route, stuck" },
    { key = "combat", bit = 4,   name = "Combat",   desc = "Low health, deaths, abilities and pulls",
      label = "combat: low health, deaths, ability and pull advice" },
    { key = "buffs",  bit = 8,   name = "Buffs",    desc = "Reminders when a buff runs out",
      label = "buff reminders" },
    { key = "gear",   bit = 16,  name = "Gear",     desc = "Upgrades and quest reward picks",
      label = "gear upgrades and quest reward picks" },
    { key = "pace",   bit = 32,  name = "Pace",     desc = "Pace reviews, level-ups, new spells",
      label = "pace reviews, level-ups, new spells" },
    { key = "upkeep", bit = 64,  name = "Upkeep",   desc = "Full bags and repairs",
      label = "bags full, repairs" },
    { key = "voice",  bit = 128, name = "Voice",    desc = "Speak callouts aloud (the text still shows)",
      label = "spoken callouts (text still shows)" },
}
ns.COACH_FLAGS = FLAGS

local BY_KEY = {}
for _, f in ipairs(FLAGS) do BY_KEY[f.key] = f end
local ALIASES = { all = "coach", commentary = "coach", quests = "route", quest = "route", fight = "combat",
                  buff = "buffs", items = "gear", loot = "gear", speed = "pace", bags = "upkeep",
                  repair = "upkeep", speech = "voice", tts = "voice" }

local function db() return ns.LinkSection("coach") end

function ns.CoachOn(key) return db()[key] ~= false end

function ns.SetCoach(key, on)
    db()[key] = on and true or false
    ns.OnSettingsChanged()
end

function ns.ResetCoach()
    ns.LinkDB().coach = {}
    ns.OnSettingsChanged()
end

function ns.CoachMask()
    local m = 0
    for _, f in ipairs(FLAGS) do
        if ns.CoachOn(f.key) then m = m + f.bit end
    end
    return m
end

local function say(text) print("|cff66ccffGlide coach|r " .. text) end
local tag = ns.OnOff

local function status()
    local master = ns.CoachOn("coach")
    say(("%s%s"):format(tag(master), master and "" or " (everything below is muted)"))
    for i = 2, #FLAGS do
        local f = FLAGS[i]
        print(("  %-7s %s  %s"):format(f.key, tag(ns.CoachOn(f.key)), f.label))
    end
    print("  /coach on|off · /coach <type> [on|off] (no on/off = toggle) · /coach reset")
end

function ns.CoachCommand(msg)
    local a, b = (msg or ""):lower():match("^%s*(%S*)%s*(%S*)")
    if a == "" or a == "status" or a == "help" then return status() end
    if a == "reset" then
        ns.ResetCoach()
        say("all commentary types on")
        return
    end
    if a == "on" or a == "off" then a, b = "coach", a end
    local key = ALIASES[a] or a
    local f = BY_KEY[key]
    if not f then
        say(("unknown type '%s'"):format(a))
        return status()
    end
    local on
    if b == "on" then on = true elseif b == "off" then on = false else on = not ns.CoachOn(key) end
    ns.SetCoach(key, on)
    say(("%s %s"):format(key == "coach" and "commentary" or f.key, tag(on)))
end

ns.Command("coach", "coach [<type>] [on|off] - coach switches (also /coach)", function(msg) ns.CoachCommand(msg) end,
    { "/coach", "/glidecoach" })
