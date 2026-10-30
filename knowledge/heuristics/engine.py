"""Expert leveling heuristics: conditional rules (rules.json) evaluated against a flat game-state dict.

Public API
    load_rules(path=None) -> list            rules.json parsed and compiled (cached per path)
    derive(state) -> dict                    copy of `state` with every derived field filled in
    evaluate(state, rules=None, limit=8, tags=None, exclude_tags=None, min_conf=0.0)
        -> [{id, title, advice, rationale, priority, conf, verified, why, tags, cat}]
        ranked by priority (1 = act now .. 3 = background), then conf, then specificity
    validate(rules) -> list of problems      (used by tests/kb/test_heuristics.py)
    load(conn, ctx) -> dict                  build_kb loader: rows into `heuristic` and `glossary`
    STATE_FIELDS                             every field a predicate may reference (name -> type, meaning)
    benchmark_xph(level) -> int              "good RestedXP run" XP/hour by level band (see PACE_BENCHMARK)

Rule format (rules.json is a JSON list):
    {"id": "hearth.set-at-turnin-hub", "title": "...", "tags": ["route", "hearth"],
     "applies": <predicate>, "advice": "...", "rationale": "...", "source": ["url" | "classic-common-knowledge"
     | "rxp-guide-text[:guide]" | "rxp-addon:<file>" | "obs:<file>"], "conf": 0.9, "verified": 0, "priority": 2}
    priority: 1 = say it now (safety, money/time about to be lost), 2 = relevant to the current decision,
    3 = background or planning tip. verified: 1 only when observed on the Forever server.

Predicate language (a JSON object; all keys must hold, i.e. keys are AND-ed):
    {"level": {">=": 10, "<=": 19}}          comparison operators: == != < <= > >=
    {"class": {"in": ["WARRIOR", "PALADIN"]}} membership; for list-valued fields "in" = any overlap
    {"class": {"not_in": ["MAGE"]}}          negated membership
    {"step_text": {"contains": "escort"}}    substring (strings) / membership (lists)
    {"combat": true}  {"faction": "Alliance"} bare value = equality (for list fields: contains)
    {"has_mount": {"exists": true}}          the field is known
    {"in_group": {"==": false, "unknown": true}}  "unknown" is the result when the field is missing (default:
                                             not applicable); use it only where the advice holds either way
    {"buff_missing": "Battle Shout"}         buff list is known (not frozen by combat) and lacks it;
                                             a list means "any of these missing"
    {"buff_present": "Rend"}                 buff list is known and has it
    {"any": [p1, p2]}  {"all": [p1, p2]}  {"not": p}
Strings compare case-insensitively. Evaluation is three-valued: a predicate on a field that is missing,
None, or of the wrong type is "unknown", which propagates through all/any/not (Kleene logic); a rule fires
only when its predicate is definitely true. So unknown fields never raise and never fire a rule, unless the
rule opts in per field with "unknown": true.

Coach usage (the state is GameState.summary() plus whatever the coach knows; see STATE_FIELDS):
    state = dict(game_state.summary(), trigger=[t.kind for t in batch], deaths_recent=n, has_mount=...)
    hits = evaluate(state, limit=5, exclude_tags=[c for c, on in flags.items() if not on])
    for a question:  evaluate(dict(state, trigger="question"), tags=["hearth", "route"])  # topic filter
derive() fills the derived fields (xp_pct, rested_pct, money_gold, target_level_diff, gray_level, step_has,
guide_level_min/max, xp_per_hour_ratio, ...) and a few safe inferences (alive when health > 0, no mount below
level 30, in_group from group_size); fields the coach passes explicitly always win.
"""
import json
import os
import re

from knowledge import values

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(HERE, "rules.json")
GLOSSARY_PATH = os.path.normpath(os.path.join(HERE, "..", "glossary.json"))

REQUIRED_KEYS = ("id", "title", "tags", "applies", "advice", "rationale", "source", "conf", "verified", "priority")
CLASSES = ("WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK", "DRUID")
RACES = ("Human", "Dwarf", "NightElf", "Gnome", "Orc", "Troll", "Tauren", "Scourge", "Skyborne")

# Trigger kinds the engine emits (companion/rules.py) plus "question" (the player asked something).
TRIGGERS = ("session_start", "step_changed", "stuck_on_step", "moving_away_from_waypoint", "quest_accepted",
            "quest_turned_in", "quest_removed", "zone_changed", "flight_taken", "hearthed", "hearth_bound",
            "low_health_in_combat", "died", "released", "resurrected", "buff_expiring", "buffs_dropped",
            "gear_upgrade", "reward_choice", "pace_review", "level_up", "spell_learned", "bags_nearly_full",
            "low_durability", "addon_outdated", "question")

# Normalised RestedXP step element tags (what `step_has` contains).
STEP_TAGS = ("accept", "turnin", "complete", "collect", "goto", "hs", "home", "fly", "fp", "deathskip", "vendor",
             "train", "grind", "dungeon", "use", "abandon", "skill", "money", "zoneskip", "equip", "escort",
             "elite", "cast", "timer")

# ------------------------------------------------------------------------------------------------------
# State fields. "derived" fields are computed by derive() from raw ones when the coach does not pass them.
# Sources: core.* = addon strip core keys (companion/state.py), summary = GameState.summary(), kb = a KB
# lookup the coach does (quest/creature/step_brief tables), coach = bookkeeping in the engine.
# ------------------------------------------------------------------------------------------------------
STATE_FIELDS = {
    # character
    "level": {"type": "int", "meaning": "character level 1-60", "src": "core.l"},
    "class": {"type": "str", "meaning": "class token, upper case: " + "/".join(CLASSES), "src": "core.cl"},
    "race": {"type": "str", "meaning": "race token: " + "/".join(RACES) + " (Undead is 'Scourge')", "src": "page B ra"},
    "faction": {"type": "str", "meaning": "'Alliance' or 'Horde'", "src": "page B fa"},
    "level_even": {"type": "bool", "meaning": "level is even (Classic class trainers add spells at even levels)",
                   "src": "derived"},
    "hardcore": {"type": "bool", "meaning": "player plays a hardcore (no deaths) character", "src": "coach config"},
    # experience
    "xp": {"type": "int", "meaning": "XP into the current level", "src": "core.x"},
    "xp_max": {"type": "int", "meaning": "XP needed for this level", "src": "core.X"},
    "xp_pct": {"type": "float", "meaning": "percent of the current level done (0-100)", "src": "derived"},
    "xp_to_level": {"type": "int", "meaning": "XP still needed to ding", "src": "derived"},
    "rested": {"type": "int", "meaning": "rested pool in XP (kills earn double until it is used up)", "src": "core.r"},
    "rested_pct": {"type": "float", "meaning": "rested pool as percent of this level's bar (cap 150)",
                   "src": "derived"},
    "resting": {"type": "bool", "meaning": "standing in a rested area (inn or capital city)", "src": "core.rs"},
    "xp_per_hour": {"type": "int", "meaning": "XP/hour over the last ~15 minutes", "src": "summary"},
    "xp_per_hour_ratio": {"type": "float", "meaning": "xp_per_hour / benchmark_xph(level); 1.0 = on a good "
                          "RestedXP pace (~5.5 days /played to 60)", "src": "derived"},
    "ttl_minutes": {"type": "float", "meaning": "minutes to the next level at the current XP/hour",
                    "src": "derived from summary ttl_seconds"},
    "pending_turnin_xp": {"type": "int", "meaning": "XP of the completed quests in the log at the player's level",
                          "src": "coach (kb quest.xp + mechanics.xp.quest_xp)"},
    "turnins_will_level": {"type": "bool", "meaning": "pending_turnin_xp >= xp_to_level", "src": "derived"},
    "gray_level": {"type": "int", "meaning": "mobs at or below this level give no XP (Classic formula)",
                   "src": "derived"},
    "zd": {"type": "int", "meaning": "Classic zero-difference value for the player's level", "src": "derived"},
    # health and resources
    "hp_pct": {"type": "float", "meaning": "player health percent 0-100", "src": "core.h / health bar"},
    "power_pct": {"type": "float", "meaning": "mana/rage/energy percent 0-100", "src": "core.m / power bar"},
    "power_type": {"type": "str", "meaning": "'mana', 'rage' or 'energy'", "src": "core.pt"},
    "mana_pct": {"type": "float", "meaning": "power_pct when power_type is mana", "src": "derived"},
    "combat": {"type": "bool", "meaning": "player is in combat", "src": "core.c"},
    "dead": {"type": "bool", "meaning": "dead or ghost (inferred False when hp_pct > 0)",
             "src": "derived from core.d (1 dead, 2 ghost)"},
    "ghost": {"type": "bool", "meaning": "released spirit running back", "src": "derived from core.d"},
    "attackers": {"type": "int", "meaning": "number of mobs currently attacking the player", "src": "coach (optional)"},
    "deaths_recent": {"type": "int", "meaning": "deaths in the last 30 minutes", "src": "coach (died triggers)"},
    "deaths_session": {"type": "int", "meaning": "deaths this session", "src": "coach"},
    # target
    "target_name": {"type": "str", "meaning": "current target name", "src": "page T tn"},
    "target_npc": {"type": "int", "meaning": "target creature id", "src": "core.tg"},
    "target_level": {"type": "int", "meaning": "target level (-1 = skull/??)", "src": "core.tl"},
    "target_level_diff": {"type": "int", "meaning": "target_level - level (skull counts as +10)", "src": "derived"},
    "target_hp_pct": {"type": "float", "meaning": "target health percent", "src": "core.th / target bar"},
    "target_hostile": {"type": "bool", "meaning": "target reaction is hostile", "src": "derived from core.tr"},
    "target_dead": {"type": "bool", "meaning": "target is dead", "src": "core.td"},
    "target_gray": {"type": "bool", "meaning": "target gives no XP (level <= gray_level)", "src": "derived"},
    "target_elite": {"type": "bool", "meaning": "target is elite or rare elite", "src": "kb creature.rank"},
    "target_classification": {"type": "str", "meaning": "normal/elite/rare/rareelite/boss", "src": "kb creature.rank"},
    "target_type": {"type": "str", "meaning": "creature type: Humanoid/Beast/Undead/Demon/Elemental/Mechanical/"
                    "Dragonkin/Giant", "src": "kb creature.type"},
    "target_caster": {"type": "bool", "meaning": "target casts spells (stands at range)", "src": "kb creature.caster"},
    "target_flees": {"type": "bool", "meaning": "target runs away at low health", "src": "kb creature.flee"},
    "target_abilities": {"type": "list[str]", "meaning": "lower-case ability names/keywords of the target "
                         "(net, fear, heal, charge, poison, enrage, summon, stun, ...)",
                         "src": "kb creature.abilities / creature_note.notes"},
    # place and travel
    "zone": {"type": "str", "meaning": "zone name", "src": "page Z zn"},
    "subzone": {"type": "str", "meaning": "sub-zone name", "src": "page Z sz"},
    "map": {"type": "int", "meaning": "UiMap id", "src": "core.mp"},
    "in_instance": {"type": "bool", "meaning": "inside a dungeon instance", "src": "coach (map kind)"},
    "in_group": {"type": "bool", "meaning": "in a party (inferred from group_size)", "src": "coach (optional)"},
    "group_size": {"type": "int", "meaning": "party size", "src": "coach (optional)"},
    "mounted": {"type": "bool", "meaning": "currently mounted", "src": "core.mo"},
    "on_taxi": {"type": "bool", "meaning": "on a flight path", "src": "core.tx"},
    "has_mount": {"type": "bool", "meaning": "riding is learned / a mount is owned (inferred True when mounted, "
                  "False below level 30)", "src": "coach (skills, mounted)"},
    "hearth_cd_min": {"type": "float", "meaning": "hearthstone cooldown left, minutes (0 = ready)",
                      "src": "derived from core.hs seconds"},
    "hearth_ready": {"type": "bool", "meaning": "hearthstone is off cooldown", "src": "derived"},
    "bind": {"type": "str", "meaning": "hearthstone bind location name", "src": "page B bd"},
    "dist_to_goto_yd": {"type": "float", "meaning": "yards to the current step's waypoint", "src": "coach geo"},
    # inventory and money
    "money": {"type": "int", "meaning": "money in copper", "src": "core.g"},
    "money_gold": {"type": "float", "meaning": "money in gold", "src": "derived"},
    "free_bags": {"type": "int", "meaning": "free bag slots", "src": "core.b"},
    "durability_pct": {"type": "int", "meaning": "lowest equipped durability percent", "src": "core.du"},
    "skill_first_aid": {"type": "int", "meaning": "First Aid skill (0 = untrained)", "src": "page K ranks"},
    "skill_cooking": {"type": "int", "meaning": "Cooking skill (0 = untrained)", "src": "page K ranks"},
    "weapon_skill_gap": {"type": "int", "meaning": "5*level minus the equipped weapon's skill", "src": "page K ranks"},
    "spells_untrained": {"type": "int", "meaning": "class spells/ranks available at this level not yet trained",
                         "src": "coach (kb spell + spell_learned events)"},
    "buffs": {"type": "list[str]", "meaning": "names of the player's buffs (from summary buffs); used by "
              "buff_missing / buff_present", "src": "summary buffs"},
    "buffs_stale": {"type": "bool", "meaning": "the aura list is frozen (combat) or unreadable", "src": "summary"},
    # session and guide
    "trigger": {"type": "list[str]", "meaning": "trigger kinds being answered now: " + ", ".join(TRIGGERS),
                "src": "companion rules.Trigger.kind"},
    "time_of_session_min": {"type": "int", "meaning": "minutes since the companion session started",
                            "src": "summary session_minutes"},
    "guide": {"type": "str", "meaning": "RestedXP guide name, e.g. '6-11 Elwynn Forest'", "src": "page G gn"},
    "guide_group": {"type": "str", "meaning": "RestedXP guide group", "src": "page G gg"},
    "guide_level_min": {"type": "int", "meaning": "first level of the guide's range (from its name)",
                        "src": "derived"},
    "guide_level_max": {"type": "int", "meaning": "last level of the guide's range (from its name)",
                        "src": "derived"},
    "level_over_guide": {"type": "int", "meaning": "level - guide_level_max", "src": "derived"},
    "level_under_guide": {"type": "int", "meaning": "guide_level_min - level", "src": "derived"},
    "guide_dungeon": {"type": "bool", "meaning": "the guide is a RestedXP dungeon guide", "src": "derived"},
    "step": {"type": "int", "meaning": "current RestedXP step index", "src": "core.st"},
    "step_has": {"type": "list[str]", "meaning": "normalised tags of the current step: " + ", ".join(STEP_TAGS),
                 "src": "derived from summary steps[0]"},
    "step_text": {"type": "str", "meaning": "current step text, lower case", "src": "derived from summary steps[0]"},
    "step_flags": {"type": "list[str]", "meaning": "RestedXP flags of the step: optional, sticky, completewith, "
                   "hardcore, softcore", "src": "kb rxp_step.flags"},
    "step_quest_type": {"type": "str", "meaning": "type of the current step's quest: normal/elite/group/"
                        "dungeon/class/escort/timed", "src": "kb quest.type"},
    "step_quest_level_gap": {"type": "int", "meaning": "level - level of the current step's quest",
                             "src": "coach (kb quest.level)"},
    "step_xp_per_min": {"type": "float", "meaning": "analyzer estimate of the step's XP per minute",
                        "src": "kb step_brief.xp_per_min"},
    "step_xp_ratio": {"type": "float", "meaning": "step_xp_per_min / current XP per minute", "src": "derived"},
    "on_step_minutes": {"type": "float", "meaning": "minutes on the current step",
                        "src": "derived from summary on_step_seconds"},
    "quest_log_count": {"type": "int", "meaning": "quests in the log (Classic cap 20)", "src": "derived from quests"},
    "quests_complete": {"type": "int", "meaning": "completed quests waiting for turn-in", "src": "derived"},
    "turnin_level_gap": {"type": "int", "meaning": "largest (level - quest level) among completed quests in the "
                         "log", "src": "coach (kb quest.level)"},
}

# "Good RestedXP run" XP/hour by level band, about 70% of the pace of the record splits listed in
# warcrafttavern's speedrun thread (L10 1h12m-1h33m, L20 6h30m-8h25m, L30 16h46m, L40 1d21h, L50 2d14h53m,
# L60 4d1h17m /played). The full table below takes ~135 h /played (~5.6 days) for the 4,084,700 XP to 60.
PACE_BENCHMARK = ((1, 9, 14000), (10, 19, 17500), (20, 29, 21000), (30, 39, 24500), (40, 49, 31500),
                  (50, 60, 38500))

# Classic zero-difference table and gray level (warcraft.wiki.gg/wiki/Mob_experience).
_ZD = ((7, 5), (9, 6), (11, 7), (15, 8), (19, 9), (29, 11), (39, 12), (44, 13), (49, 14), (54, 15), (59, 16))

# Rule tag -> companion commentary category (/coach <type> off); first match in this order wins.
TAG_CATEGORY = (("low-health", "combat"), ("survival", "combat"), ("combat", "combat"), ("pull", "combat"),
                ("rotation", "combat"), ("mob", "combat"), ("death", "combat"), ("downtime", "combat"),
                ("buffs", "buffs"), ("gear", "gear"), ("weapon-skill", "gear"),
                ("bags", "upkeep"), ("repair", "upkeep"), ("vendor", "upkeep"), ("consumables", "upkeep"),
                ("economy", "upkeep"), ("pace", "pace"), ("training", "pace"), ("talents", "pace"),
                ("rested", "pace"), ("xp", "pace"), ("route", "route"), ("quest", "route"), ("hearth", "route"),
                ("travel", "route"), ("dungeon", "route"), ("professions", "upkeep"), ("class", "combat"),
                ("forever", "route"))

OPS = ("==", "!=", "<", "<=", ">", ">=", "in", "not_in", "contains", "exists", "unknown")
SPECIAL = ("any", "all", "not", "buff_missing", "buff_present")


def benchmark_xph(level):
    """Benchmark XP/hour for a strong RestedXP run at this level (None above 59: no XP to earn)."""
    try:
        lv = int(level)
    except (TypeError, ValueError):
        return None
    if lv >= 60:
        return None
    for lo, hi, xph in PACE_BENCHMARK:
        if lo <= lv <= hi:
            return xph
    return None


def zero_difference(level):
    for top, zd in _ZD:
        if level <= top:
            return zd
    return 17


def gray_level(level):
    """Mobs at or below this level give no XP (0 = every mob gives XP)."""
    if level <= 5:
        return 0
    if level <= 39:
        return level - level // 10 - 5
    if level <= 59:
        return level - level // 5 - 1
    return level - 9


# ------------------------------------------------------------------------------------------------------
# derive(): raw/summary state -> flat state with derived fields
# ------------------------------------------------------------------------------------------------------
_RACE_ALIASES = {"undead": "Scourge", "scourge": "Scourge", "night elf": "NightElf", "nightelf": "NightElf",
                 "human": "Human", "dwarf": "Dwarf", "gnome": "Gnome", "orc": "Orc", "troll": "Troll",
                 "tauren": "Tauren", "skyborne": "Skyborne"}
_TAG_ALIASES = {"go": "goto", "goto": "goto", "accept": "accept", "turnin": "turnin", "complete": "complete",
                "collect": "collect", "collectmultiple": "collect", "hs": "hs", "hsbatching": "hs", "home": "home",
                "bindlocation": "home", "fly": "fly", "fp": "fp", "deathskip": "deathskip", "vendor": "vendor",
                "train": "train", "trainer": "train", "xp": "grind", "use": "use", "abandon": "abandon",
                "skill": "skill", "money": "money", "zoneskip": "zoneskip", "subzoneskip": "zoneskip",
                "equip": "equip", "cast": "cast", "usespell": "cast", "timer": "timer", "dungeon": "dungeon"}
_GUIDE_RANGE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)")


def _num(v):
    return values.as_float(v, finite=True, bool_ok=False)


def _int(v):
    return values.as_int(v, loose=True, bool_ok=False)


def _bool(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "yes", "y"):
            return True
        if s in ("0", "false", "no", "n", ""):
            return False
    return None


def _set_default(out, key, value):
    if out.get(key) is None and value is not None:
        out[key] = value


def _step_tags(step):
    """Normalised tag list + lower-case text for one summary step ({'elements': [...], 'text': ...})."""
    tags = []
    text = (step.get("text") or "") if isinstance(step, dict) else ""
    for e in (step.get("elements") or []) if isinstance(step, dict) else []:
        t = _TAG_ALIASES.get(str(e.get("tag") or "").lower()) if isinstance(e, dict) else None
        if t and t not in tags:
            tags.append(t)
    low = text.lower()
    for word, tag in (("hearth", "hs"), ("die and respawn", "deathskip"), ("spirit healer", "deathskip"),
                      ("grind", "grind"), ("dungeon", "dungeon"), ("escort", "escort"), ("elite", "elite"),
                      ("train your class", "train"), ("fly to", "fly"), ("vendor", "vendor")):
        if word in low and tag not in tags:
            tags.append(tag)
    return tags, low


def derive(state):
    """Return a copy of `state` with derived fields. Accepts GameState.summary() output (nested target, steps,
    quests, buffs) or an already-flat dict; values the caller passes are never overwritten."""
    s = dict(state or {})
    lv = _int(s.get("level"))
    s["level"] = lv
    if isinstance(s.get("class"), str):
        s["class"] = s["class"].upper()
    if isinstance(s.get("race"), str):
        s["race"] = _RACE_ALIASES.get(s["race"].strip().lower(), s["race"])
    if isinstance(s.get("faction"), str):
        s["faction"] = s["faction"].strip().capitalize()
    if lv is not None:
        _set_default(s, "level_even", lv % 2 == 0)
        _set_default(s, "gray_level", gray_level(lv))
        _set_default(s, "zd", zero_difference(lv))
    # experience
    xp, xmax, rested = _num(s.get("xp")), _num(s.get("xp_max")), _num(s.get("rested"))
    if xp is not None and xmax:
        _set_default(s, "xp_pct", round(100.0 * xp / xmax, 1))
        _set_default(s, "xp_to_level", int(xmax - xp))
        if rested is not None:
            _set_default(s, "rested_pct", round(100.0 * rested / xmax, 1))
    xph = _num(s.get("xp_per_hour"))
    if xph and lv is not None and s.get("xp_per_hour_ratio") is None:
        bench = benchmark_xph(lv)
        mins = _num(s.get("time_of_session_min", s.get("session_minutes")))
        if bench and (mins is None or mins >= 10):
            s["xp_per_hour_ratio"] = round(xph / bench, 2)
    if s.get("ttl_minutes") is None and _num(s.get("ttl_seconds")) is not None:
        s["ttl_minutes"] = round(_num(s["ttl_seconds"]) / 60.0, 1)
    ptx, xtl = _num(s.get("pending_turnin_xp")), _num(s.get("xp_to_level"))
    if ptx is not None and xtl is not None:
        _set_default(s, "turnins_will_level", ptx >= xtl)
    # health and resources (the summary gives hp/power as percent; -1 = not shown)
    hp = _num(s.get("hp_pct", s.get("hp")))
    if s.get("hp_pct") is None and hp is not None and hp >= 0:
        s["hp_pct"] = hp
    pw = _num(s.get("power_pct", s.get("power")))
    if s.get("power_pct") is None and pw is not None and pw >= 0:
        s["power_pct"] = pw
    if isinstance(s.get("power_type"), str):
        s["power_type"] = s["power_type"].lower()
    if s.get("power_type") == "mana" and _num(s.get("power_pct")) is not None:
        _set_default(s, "mana_pct", _num(s["power_pct"]))
    for key in ("combat", "resting", "mounted", "on_taxi", "has_mount", "in_instance", "in_group", "hardcore",
                "buffs_stale", "target_elite", "target_caster", "target_flees", "target_dead", "hearth_ready"):
        if key in s:
            s[key] = _bool(s[key])
    d = s.get("dead")
    if d is not None and not isinstance(d, bool):
        di = _int(d)
        s["dead"] = None if di is None else di > 0
        _set_default(s, "ghost", None if di is None else di == 2)
    if s.get("dead") is None and _num(s.get("hp_pct")) is not None and _num(s["hp_pct"]) > 0:
        s["dead"] = False                            # a player with health is alive
    if s.get("dead") is False:
        _set_default(s, "ghost", False)
    # mounts: seen mounted -> has one; nobody can ride before level 30 (Classic 40; Forever unannounced)
    if s.get("has_mount") is None:
        if s.get("mounted") is True:
            s["has_mount"] = True
        elif lv is not None and lv < 30:
            s["has_mount"] = False
    gs = _int(s.get("group_size"))
    if s.get("in_group") is None and gs is not None:
        s["in_group"] = gs > 1
    # target (summary nests it)
    tgt = s.get("target")
    if isinstance(tgt, dict):
        _set_default(s, "target_name", tgt.get("name"))
        _set_default(s, "target_npc", _int(tgt.get("npc")))
        _set_default(s, "target_level", _int(tgt.get("level")))
        th = _num(tgt.get("hp"))
        if th is not None and th >= 0:
            _set_default(s, "target_hp_pct", th)
        if tgt.get("reaction") is not None and _int(tgt.get("reaction")) is not None:
            _set_default(s, "target_hostile", _int(tgt["reaction"]) <= 3)
        if tgt.get("dead") is not None:
            _set_default(s, "target_dead", _bool(tgt.get("dead")))
    tl = _int(s.get("target_level"))
    if tl is not None and lv is not None:
        if tl < 0:                                   # skull: 10+ levels above
            _set_default(s, "target_level_diff", 10)
        elif tl > 0:
            _set_default(s, "target_level_diff", tl - lv)
            _set_default(s, "target_gray", tl <= gray_level(lv))
    if isinstance(s.get("target_abilities"), (list, tuple)):
        s["target_abilities"] = [str(a).lower() for a in s["target_abilities"]]
    if isinstance(s.get("target_classification"), str):
        c = s["target_classification"].lower()
        s["target_classification"] = c
        _set_default(s, "target_elite", c in ("elite", "rareelite", "worldboss", "boss"))
    # money, bags, durability, hearth
    money = _num(s.get("money"))
    if money is not None:
        _set_default(s, "money_gold", round(money / 10000.0, 2))
    du = _num(s.get("durability_pct", s.get("durability")))
    if s.get("durability_pct") is None and du is not None and du >= 0:
        s["durability_pct"] = du
    hs = _num(s.get("hearth_cd"))
    if s.get("hearth_cd_min") is None and hs is not None and hs >= 0:
        s["hearth_cd_min"] = round(hs / 60.0, 1)
    if s.get("hearth_ready") is None and _num(s.get("hearth_cd_min")) is not None:
        s["hearth_ready"] = _num(s["hearth_cd_min"]) <= 0.05
    # session and guide
    if s.get("time_of_session_min") is None and _num(s.get("session_minutes")) is not None:
        s["time_of_session_min"] = _num(s["session_minutes"])
    if s.get("on_step_minutes") is None and _num(s.get("on_step_seconds")) is not None:
        s["on_step_minutes"] = round(_num(s["on_step_seconds"]) / 60.0, 1)
    g = s.get("guide")
    if isinstance(g, str):
        m = _GUIDE_RANGE.match(g)
        if m:
            _set_default(s, "guide_level_min", int(m.group(1)))
            _set_default(s, "guide_level_max", int(m.group(2)))
    if lv is not None:
        if _num(s.get("guide_level_max")) is not None:
            _set_default(s, "level_over_guide", lv - int(_num(s["guide_level_max"])))
        if _num(s.get("guide_level_min")) is not None:
            _set_default(s, "level_under_guide", int(_num(s["guide_level_min"])) - lv)
    gg = s.get("guide_group")
    if isinstance(gg, str) and s.get("guide_dungeon") is None:
        s["guide_dungeon"] = "dungeon" in gg.lower()
    steps = s.get("steps")
    if s.get("step_has") is None and isinstance(steps, (list, tuple)) and steps and isinstance(steps[0], dict):
        tags, low = _step_tags(steps[0])
        s["step_has"] = tags
        _set_default(s, "step_text", low)
    elif isinstance(s.get("step_has"), (list, tuple)):
        s["step_has"] = [str(t).lower() for t in s["step_has"]]
    if isinstance(s.get("step_text"), str):
        s["step_text"] = s["step_text"].lower()
    if isinstance(s.get("step_flags"), (list, tuple)):
        s["step_flags"] = [str(t).lower() for t in s["step_flags"]]
    sxm = _num(s.get("step_xp_per_min"))
    if sxm is not None and s.get("step_xp_ratio") is None and xph:
        s["step_xp_ratio"] = round(sxm / (xph / 60.0), 2)
    quests = s.get("quests")
    if isinstance(quests, dict):
        _set_default(s, "quest_log_count", len(quests))
        _set_default(s, "quests_complete",
                     sum(1 for q in quests.values() if isinstance(q, dict) and q.get("done")))
    # triggers: str | [str] | [[kind, facts]] | [{"kind": ...}]
    trig = s.get("trigger", s.get("triggers"))
    if trig is not None:
        kinds = []
        for t in ([trig] if isinstance(trig, str) else trig):
            k = t if isinstance(t, str) else (t.get("kind") if isinstance(t, dict) else
                                               (t[0] if isinstance(t, (list, tuple)) and t else None))
            if k:
                kinds.append(str(k).lower())
        s["trigger"] = kinds
    # buffs: [{"name": ...}] | ["name"]; unknown when hidden
    b = s.get("buffs")
    if isinstance(b, (list, tuple)) and not s.get("buffs_hidden"):
        names = [(x.get("name") if isinstance(x, dict) else x) for x in b]
        s["_buffs"] = {str(n).lower() for n in names if n}
        s["buffs"] = [n for n in names if n]
    return s


# ------------------------------------------------------------------------------------------------------
# predicate compiler (three-valued: True / False / None = unknown)
# ------------------------------------------------------------------------------------------------------
def _norm(v):
    if isinstance(v, str):
        return v.lower()
    if isinstance(v, (list, tuple, set)):
        return [_norm(x) for x in v]
    return v


def _eq(a, b):
    if isinstance(a, str) and isinstance(b, str):
        return a.lower() == b
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b if isinstance(a, bool) and isinstance(b, bool) else False
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return na == nb
    return a == b


def _apply(op, v, arg):
    """One comparison; v is the (non-None) state value, arg the normalised predicate argument."""
    is_list = isinstance(v, (list, tuple, set))
    if op == "==":
        return any(_eq(x, arg) for x in v) if is_list else _eq(v, arg)
    if op == "!=":
        return not any(_eq(x, arg) for x in v) if is_list else not _eq(v, arg)
    if op in ("<", "<=", ">", ">="):
        a, b = _num(v), _num(arg)
        if a is None or b is None or is_list:
            return None
        return a < b if op == "<" else a <= b if op == "<=" else a > b if op == ">" else a >= b
    if op in ("in", "not_in"):
        items = arg if isinstance(arg, list) else [arg]
        hit = any(_eq(x, y) for x in v for y in items) if is_list else any(_eq(v, y) for y in items)
        return hit if op == "in" else not hit
    if op == "contains":
        if is_list:
            return any(_eq(x, arg) for x in v)
        if isinstance(v, str) and isinstance(arg, str):
            return arg in v.lower()
        return None
    return None


def _fmt(name, v, spec):
    if isinstance(v, float):
        v = round(v, 1)
    if isinstance(spec, dict):
        cond = " ".join("%s %s" % (k, json.dumps(a)) for k, a in spec.items())
    else:
        cond = "== %s" % json.dumps(spec)
    return "%s=%s (%s)" % (name, json.dumps(v, default=str), cond)


def _field(name, spec):
    if isinstance(spec, dict) and spec and all(k in OPS for k in spec):
        tests = [(op, _norm(arg)) for op, arg in spec.items() if op not in ("exists", "unknown")]
        exists = spec.get("exists")
        if_unknown = bool(spec["unknown"]) if "unknown" in spec else None
    else:
        tests, exists, if_unknown = [("==", _norm(spec))], None, None

    def fn(s, why):
        v = s.get(name)
        if exists is not None:
            known = v is not None
            if known != bool(exists):
                return False
            if not tests:
                if why is not None:
                    why.append("%s %s" % (name, "known" if known else "unknown"))
                return True
        if v is None:
            if if_unknown is True and why is not None:
                why.append("%s unknown (assumed ok)" % name)
            return if_unknown
        for op, arg in tests:
            r = _apply(op, v, arg)
            if r is None:
                return None
            if not r:
                return False
        if why is not None:
            why.append(_fmt(name, v, spec))
        return True
    fn.leaves = 1
    return fn


def _all(fns):
    def fn(s, why):
        unknown = False
        mark = len(why) if why is not None else 0
        for f in fns:
            r = f(s, why)
            if r is False:
                if why is not None:
                    del why[mark:]
                return False
            if r is None:
                unknown = True
        if unknown:
            if why is not None:
                del why[mark:]
            return None
        return True
    fn.leaves = sum(getattr(f, "leaves", 1) for f in fns)
    return fn


def _any(fns):
    def fn(s, why):
        unknown = False
        for f in fns:
            scratch = [] if why is not None else None
            r = f(s, scratch)
            if r is True:
                if why is not None:
                    why.extend(scratch)
                return True
            if r is None:
                unknown = True
        return None if unknown else False
    fn.leaves = max([getattr(f, "leaves", 1) for f in fns] or [1])
    return fn


def _not(inner):
    def fn(s, why):
        r = inner(s, None)
        if r is None:
            return None
        if not r and why is not None:
            why.append("not(%s)" % getattr(inner, "desc", "..."))
        return not r
    fn.leaves = 1
    return fn


def _buff(val, missing):
    names = [_norm(val)] if isinstance(val, str) else [_norm(x) for x in val]

    def fn(s, why):
        known = s.get("_buffs")
        if known is None or s.get("buffs_stale"):
            return None
        hit = [n for n in names if (n not in known) == missing]
        if not hit:
            return False
        if why is not None:
            why.append("buff %s: %s" % ("missing" if missing else "present", ", ".join(hit)))
        return True
    fn.leaves = 1
    return fn


def compile_predicate(pred):
    """Predicate JSON -> fn(state, why_list_or_None) -> True/False/None. Raises ValueError on bad syntax."""
    if isinstance(pred, list):
        fn = _all([compile_predicate(p) for p in pred])
        fn.desc = json.dumps(pred)
        return fn
    if not isinstance(pred, dict):
        raise ValueError("predicate must be an object, got %r" % (pred,))
    fns = []
    for key, val in pred.items():
        if key == "all":
            fns.append(_all([compile_predicate(p) for p in val]))
        elif key == "any":
            fns.append(_any([compile_predicate(p) for p in val]))
        elif key == "not":
            fns.append(_not(compile_predicate(val)))
        elif key == "buff_missing":
            fns.append(_buff(val, True))
        elif key == "buff_present":
            fns.append(_buff(val, False))
        else:
            if isinstance(val, dict) and not (val and all(k in OPS for k in val)):
                raise ValueError("unknown operator in %s: %s" % (key, list(val)))
            fns.append(_field(key, val))
    if not fns:
        fn = lambda s, why: True          # noqa: E731  empty predicate = always applies
        fn.leaves = 0
    else:
        fn = fns[0] if len(fns) == 1 else _all(fns)
    fn.desc = json.dumps(pred, separators=(",", ":"))
    return fn


def predicate_fields(pred, out=None):
    """Every state field a predicate references (special keys map to their underlying field)."""
    out = set() if out is None else out
    if isinstance(pred, list):
        for p in pred:
            predicate_fields(p, out)
    elif isinstance(pred, dict):
        for key, val in pred.items():
            if key in ("all", "any"):
                predicate_fields(val, out)
            elif key == "not":
                predicate_fields(val, out)
            elif key in ("buff_missing", "buff_present"):
                out.add("buffs")
            else:
                out.add(key)
    return out


# ------------------------------------------------------------------------------------------------------
# rules
# ------------------------------------------------------------------------------------------------------
_CACHE = {}
_RAW = {}                     # id(raw dict) -> (raw dict, Rule) for rule lists passed in by callers


def _category(tags):
    tl = [str(t).lower() for t in tags or []]
    for tag, cat in TAG_CATEGORY:
        if tag in tl:
            return cat
    return "route"


class Rule(dict):
    """A rules.json entry: still a plain dict (JSON-serialisable), with the compiled predicate, the coach
    category and the lower-case tag set attached as attributes."""
    __slots__ = ("fn", "cat", "tagset")

    def __init__(self, data):
        super().__init__(data)
        self.fn = compile_predicate(self.get("applies") or {})
        self.cat = _category(self.get("tags"))
        self.tagset = {str(t).lower() for t in self.get("tags") or []}


def _prepare(rule):
    if isinstance(rule, Rule):
        return rule
    hit = _RAW.get(id(rule))
    if hit is not None and hit[0] is rule:
        return hit[1]
    if len(_RAW) > 20000:
        _RAW.clear()
    compiled = Rule(rule)
    _RAW[id(rule)] = (rule, compiled)        # keeps `rule` alive so its id can't be reused
    return compiled


def load_rules(path=None):
    """Parse and compile rules.json (cached by path and mtime). Returns a list of Rule (dict subclass)."""
    path = path or RULES_PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    hit = _CACHE.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    with open(path, encoding="utf-8") as fh:
        rules = [Rule(r) for r in json.load(fh)]
    _CACHE[path] = (mtime, rules)
    return rules


def evaluate(state, rules=None, limit=8, tags=None, exclude_tags=None, min_conf=0.0):
    """Rules whose predicate is true for `state`, best first: priority asc, conf desc, specificity desc.
    tags / exclude_tags: only rules having one of / none of these tags; exclude_tags also matches the coach
    category (combat/route/gear/pace/upkeep/buffs), so muted commentary types can be passed straight in.
    limit=0 or None returns every match."""
    s = derive(state)
    rules = load_rules() if rules is None else rules
    want = {t.lower() for t in tags} if tags else None
    drop = {t.lower() for t in exclude_tags} if exclude_tags else None
    hits = []
    for raw in rules:
        r = _prepare(raw)
        if want is not None and not (r.tagset & want):
            continue
        if drop is not None and (r.tagset & drop or r.cat in drop):
            continue
        if (r.get("conf") or 0) < min_conf:
            continue
        if r.fn(s, None) is True:
            hits.append(r)
    hits.sort(key=lambda r: (r.get("priority", 3), -(r.get("conf") or 0), -getattr(r.fn, "leaves", 0),
                             r.get("id", "")))
    out = []
    for r in hits[:limit if limit else None]:
        why = []
        r.fn(s, why)
        out.append({"id": r["id"], "title": r.get("title"), "advice": r.get("advice"),
                    "rationale": r.get("rationale"), "priority": r.get("priority", 3), "conf": r.get("conf"),
                    "verified": r.get("verified", 0), "why": why, "tags": list(r.get("tags") or []),
                    "cat": r.cat})
    return out


def validate(rules):
    """Problems with a rule list (empty list = valid)."""
    problems, seen = [], set()
    for i, r in enumerate(rules):
        rid = r.get("id") if isinstance(r, dict) else None
        where = rid or "#%d" % i
        if not isinstance(r, dict):
            problems.append("%s: not an object" % where)
            continue
        for k in REQUIRED_KEYS:
            if k not in r or r[k] in (None, "", []):
                if not (k == "applies" and r.get(k) == {}) and not (k == "verified" and r.get(k) == 0):
                    problems.append("%s: missing %s" % (where, k))
        if rid in seen:
            problems.append("%s: duplicate id" % where)
        seen.add(rid)
        if not isinstance(r.get("tags"), list):
            problems.append("%s: tags must be a list" % where)
        if not isinstance(r.get("source"), list) or not all(isinstance(x, str) and x for x in r.get("source") or []):
            problems.append("%s: source must be a non-empty list of strings" % where)
        c = r.get("conf")
        if not isinstance(c, (int, float)) or not 0 <= c <= 1:
            problems.append("%s: conf must be 0..1" % where)
        if r.get("verified") not in (0, 1):
            problems.append("%s: verified must be 0 or 1" % where)
        if r.get("priority") not in (1, 2, 3):
            problems.append("%s: priority must be 1, 2 or 3" % where)
        try:
            compile_predicate(r.get("applies") or {})
        except (ValueError, TypeError, AttributeError) as e:
            problems.append("%s: bad predicate: %s" % (where, e))
            continue
        for f in predicate_fields(r.get("applies") or {}):
            if f not in STATE_FIELDS:
                problems.append("%s: undocumented state field %s" % (where, f))
    return problems


# ------------------------------------------------------------------------------------------------------
# build_kb loader
# ------------------------------------------------------------------------------------------------------
def load_glossary(path=None):
    path = path or GLOSSARY_PATH
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        data = data.get("terms", [])
    return data


def load(conn, ctx=None):
    """Insert rules into `heuristic` and terms into `glossary` (INSERT OR REPLACE, so re-runs are safe).
    `priority` is written only when the table has that column (schema v1 does not)."""
    try:
        from knowledge.store import jdump, table_cols
    except ImportError:                                   # standalone use
        def jdump(o):
            return json.dumps(o, separators=(",", ":"), ensure_ascii=False, default=str)

        def table_cols(c, t):
            return [r[1] for r in c.execute("PRAGMA table_info(%s)" % t)]
    rules_path = RULES_PATH
    if ctx is not None and getattr(ctx, "root", None):
        cand = os.path.join(ctx.root, "knowledge", "heuristics", "rules.json")
        rules_path = cand if os.path.exists(cand) else RULES_PATH
    if not os.path.exists(rules_path):
        return {"skipped": "rules.json missing"}
    rules = load_rules(rules_path)
    cols = set(table_cols(conn, "heuristic"))
    if not cols:
        return {"skipped": "heuristic table missing"}
    keys = [k for k in ("id", "title", "applies", "advice", "rationale", "tags", "source", "conf", "verified",
                        "priority") if k in cols]
    rows = []
    for r in rules:
        row = {"id": r["id"], "title": r.get("title"), "applies": jdump(r.get("applies") or {}),
               "advice": r.get("advice"), "rationale": r.get("rationale"), "tags": jdump(r.get("tags") or []),
               "source": jdump(r.get("source") or []), "conf": r.get("conf"), "verified": r.get("verified", 0),
               "priority": r.get("priority")}
        rows.append([row[k] for k in keys])
    conn.executemany("INSERT OR REPLACE INTO heuristic(%s) VALUES(%s)" % (",".join(keys), ",".join("?" * len(keys))),
                     rows)
    terms = load_glossary(os.path.join(ctx.root, "knowledge", "glossary.json")
                          if ctx is not None and getattr(ctx, "root", None) else None)
    gl = 0
    if terms and set(table_cols(conn, "glossary")) >= {"term", "definition", "aliases"}:
        conn.executemany("INSERT OR REPLACE INTO glossary(term, definition, aliases) VALUES(?,?,?)",
                         [(t["term"], t.get("definition"), jdump(t.get("aliases") or [])) for t in terms])
        gl = len(terms)
    res = {"heuristic": len(rows), "glossary": gl}
    if "priority" not in cols:
        res["note"] = "heuristic.priority column absent; priorities live in rules.json only"
    return res


if __name__ == "__main__":                                 # python engine.py state.json
    import sys
    st = json.load(open(sys.argv[1], encoding="utf-8")) if len(sys.argv) > 1 else {}
    for hit in evaluate(st, limit=int(sys.argv[2]) if len(sys.argv) > 2 else 8):
        print("p%d %.2f %-40s %s | why: %s" % (hit["priority"], hit["conf"], hit["id"], hit["advice"],
                                              "; ".join(hit["why"])))
