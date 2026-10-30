"""Registry of every numeric parameter the mechanics formulas use.

Each parameter row: key, value, unit, classic_value, status, evidence (+ updated).

status (README contract):
  classic           Classic value used as the working assumption; nothing observed on Forever yet and
                    no specific reason to expect a change.
  unverified        Classic value or a heuristic prior that is in doubt on Forever (or a pure guess).
                    The coach must hedge. Every one has an experiment in knowledge/VERIFY.md.
  forever-verified  Read directly from the Forever client/server (exact value: UnitXPMax, GetUnitSpeed,
                    item cooldown, ...).
  forever-fitted    Inferred by fit.py from recorded observations (formula structure, rates, rounding).

Values live in code as Classic/prior defaults. fit.py writes data/params.json with the overrides whose
evidence is strong; this module loads that file at import (when present), so get() returns the best
known value and status() says how it is known. Values may be numbers, strings, lists or dicts; in the
`param` table (to_rows) value and classic_value are JSON text.

One registry for every lane: the analyzer registers its `analyzer.*` keys here too (register(..., lane="analyzer");
knowledge.analyzer.params is its view). keys() / all() / overrides() / reset() take a lane (default "mechanics").

params.json (and the analyzer's fitted values) are build inputs: the build writes the values it used into the KB's
`param` table. At runtime the KB in use answers: use_kb() (called by kbdata.get_data for every KB the engine reads)
makes that KB's snapshot the current one, replacing the previous KB's, so values never leak from one KB to the next.
A KB without a param table falls back to the overrides loaded here (plus its meta 'analyzer_params').

  get(key[, default])        current value (scoped overlay > the KB in use > override > registry default)
  status(key) / classic(key) / info(key) / lane(key)
  register(key, value, unit, status, evidence, classic=..., lane="mechanics")
  load_overrides(path_or_dict=None)   default path: data/params.json
  apply(overrides, lane)     fitted values ({key: value} / {key: {"value", "status", "evidence"}})
  snapshot_of(kb) / use_kb(kb or snapshot) / current_kb()
  reset(lane="mechanics") / reload()
  scoped({key: value})       context manager, thread-local temporary values (used by fit and tests)
  classic_scope()            context manager: every key returns its Classic value
  all() -> [row dict]        rows for the `param` table
  to_rows() -> [tuple]       (key, value, unit, classic_value, status, evidence, updated)
"""
import copy
import json
import os
import threading
from contextlib import contextmanager

STATUSES = ("classic", "forever-verified", "forever-fitted", "unverified")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
DEFAULT_OVERRIDES = os.path.join(ROOT, "data", "params.json")
REGISTERED = "2026-10-04"

_MISSING = object()
_SAME = object()
_CLASSIC = object()          # overlay marker: answer with classic values

_REG = {}                    # key -> row dict (defaults)
_LANE = {}                   # key -> the lane that registered it ("mechanics", "analyzer")
_OVR = {}                    # key -> {"value", "status", "evidence", "updated"}
_local = threading.local()
_loaded_from = []
_LOCK = threading.Lock()
_NOW = None                  # the values of the KB in use (a Snapshot; see use_kb), or None
MECHANICS = "mechanics"

CLASSES = ("WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK", "DRUID")


def register(key, value, unit, status, evidence, classic=_SAME, lane=MECHANICS):
    """Add a parameter to the registry: its default value, unit, status and evidence, under a lane."""
    if status not in STATUSES:
        raise ValueError("bad status for %s: %s" % (key, status))
    if key in _REG:
        raise ValueError("duplicate param %s" % key)
    _REG[key] = {"key": key, "value": value, "unit": unit,
                 "classic_value": copy.deepcopy(value) if classic is _SAME else classic,
                 "status": status, "evidence": evidence, "updated": REGISTERED}
    _LANE[key] = lane


_p = register


# ---------------------------------------------------------------------------------------------------
# XP
# ---------------------------------------------------------------------------------------------------
# Level XP table (XP to go from level L to L+1), vanilla 1.12 / Classic Era. It equals the Classic
# formula ((8*L + Diff(L)) * (45 + 5*L)) rounded half-up to 100, Diff = 0 (L<=28), 1, 3, 6, 5*(L-30)
# (L>=32); total 1->60 = 4,084,700. NOTE: the table in the task brief (8700, 9800, 11000 ... 108600,
# total 2,549,900) is a later-expansion reduced table; Forever shows 8800 at 11 and 10100 at 12
# (UnitXPMax in the live logs), i.e. this one.
CLASSIC_XP_TABLE = [400, 900, 1400, 2100, 2800, 3600, 4500, 5400, 6500, 7600,
                    8800, 10100, 11400, 12900, 14400, 16000, 17700, 19400, 21300, 23200,
                    25200, 27300, 29400, 31700, 34000, 36400, 38900, 41400, 44300, 47400,
                    50800, 54500, 58600, 62800, 67100, 71600, 76100, 80800, 85700, 90700,
                    95800, 101000, 106300, 111800, 117500, 123200, 129100, 135100, 141200, 147500,
                    153900, 160400, 167100, 173900, 180800, 187900, 195000, 202300, 209800]

_p("xp.max_level", 60, "level", "classic", "Forever level cap 60 (launch 2026-11-04); no XP at the cap")
for _lvl, _xp in enumerate(CLASSIC_XP_TABLE, 1):
    _p("xp.level.%d" % _lvl, _xp, "xp", "classic",
       "vanilla/Classic Era XP table (formula-consistent, total 4,084,700); "
       "README: 1..9 observed identical on Forever")


def _zd(level):
    # Classic zero-difference table (WoWWiki Formulas:Mob XP; cmangos/TrinityCore GetZeroDifference)
    for top, zd in ((7, 5), (9, 6), (11, 7), (15, 8), (19, 9), (29, 11), (39, 12), (44, 13), (49, 14),
                    (54, 15), (59, 16)):
        if level <= top:
            return zd
    return 17


def _gray(level):
    # Classic gray level (GetGrayLevel): mobs at or below give no XP
    if level <= 5:
        return 0
    if level <= 39:
        return level - level // 10 - 5
    if level <= 59:
        return level - level // 5 - 1
    return level - 9          # 60 -> 51


_p("xp.zd.table", [_zd(i) for i in range(61)], "levels (index = player level)", "classic",
   "Classic ZD: 1-7:5 8-9:6 10-11:7 12-15:8 16-19:9 20-29:11 30-39:12 40-44:13 45-49:14 50-54:15 "
   "55-59:16 60:17")
_p("xp.zd.verified_through", 0, "level", "unverified",
   "highest player level at which fit.py confirmed the ZD band on Forever kills (0 = none)")
_p("xp.gray.table", [_gray(i) for i in range(61)], "level (index = player level)", "classic",
   "Classic gray level: 1-5 none (0), 6-39 L-floor(L/10)-5, 40-59 L-floor(L/5)-1, 60 -> 51. Gray kills "
   "print no XP line, so the recorder cannot see them yet (VERIFY.md)")
_p("xp.kill.base_mul", 5, "xp/level", "classic", "same-level kill XP = 5*level + 45 (Classic, levels 1-60)")
_p("xp.kill.base_add", 45, "xp", "classic", "same-level kill XP = 5*level + 45 (Classic, Azeroth content)")
_p("xp.kill.high_step", 0.05, "fraction/level", "classic",
   "higher-level mob: base(player) * (1 + 0.05*diff) (WoWWiki Formulas:Mob XP)")
_p("xp.kill.high_cap", 4, "levels", "classic",
   "level difference counted for higher mobs is capped at +4 (cmangos/TrinityCore BaseGain)")
_p("xp.kill.low_base", "player", "player|mob", "classic",
   "lower-level mob: base * (1 - (player-mob)/ZD). Classic (WoWWiki/emulators) takes base from the "
   "PLAYER level; 'mob' takes base = 5*mob_level+45")
_p("xp.kill.rounding", "half_even", "half_even|half_up|floor", "classic",
   "kill XP rounding to an integer. Level 1 vs level 2 mob = 52 (52.5 rounds down) is the observed "
   "Classic value; emulator integer math would give 53. half_even = round half to even")
_p("xp.kill.elite_mult", 2.0, "x", "unverified",
   "Classic open-world elite kill XP x2, applied before rounding. Forever per-creature values differ "
   "(see data/mechanics_fit.json kill_xp.elite)")
_p("xp.kill.rested_mult", 2.0, "x", "classic",
   "rested: kill XP doubled (bonus = base) while the rested pool lasts (Classic)")
_p("xp.mod.well_fed", 0.0, "fraction", "unverified",
   "Forever 'Well Fed' (spell 1248422, from camp cooking) kill-XP bonus; no Classic counterpart (0)",
   classic=0.0)
_p("xp.group.rate", {"1": 1.0, "2": 1.0, "3": 1.166, "4": 1.3, "5": 1.4}, "x by party size", "unverified",
   "Classic group XP: XP is computed for the highest-level member, each member gets "
   "xp*rate(n)*own_level/sum_levels, truncated (cmangos xp_in_group_rate). Recorder does not log party")
_p("xp.quest.full_until", 5, "levels", "classic", "quest gives full XP while player_level <= quest_level+5")
_p("xp.quest.factors", [0.8, 0.6, 0.4, 0.2, 0.1], "fraction at +6,+7,+8,+9,>=+10", "classic",
   "Classic over-level quest XP reduction (diffFactor 2*(q-p)+20 clamped 1..10, /10)")
_p("xp.quest.rounding", "bands", "bands|none", "classic",
   "quest XP rounded: <=100 to 5, <=500 to 10, <=1000 to 25, else to 50 (Quest::XPValue)")

# ---------------------------------------------------------------------------------------------------
# Rested
# ---------------------------------------------------------------------------------------------------
_p("rested.rate_per_8h", 0.05, "fraction of level XP", "classic",
   "rested pool grows 5% of the current level's XP per 8 h resting (inn/city); pool unit = client "
   "GetXPExhaustion (XP that will be earned at double rate)")
_p("rested.away_mult", 0.25, "x", "unverified", "outside inns/cities rest accrues at 1/4 the rate (Classic)")
_p("rested.cap", 1.5, "fraction of level XP", "unverified", "rested pool cap 150% of a level (Classic)")
_p("rested.bonus_ratio", 0.5, "bonus xp per pool xp", "classic",
   "client pool is consumed by the total XP gained (base+bonus), so the bonus is at most pool*0.5; "
   "per kill bonus = min(kill_xp, floor(pool*0.5))")
_p("rested.camp_bonus", 0.0, "fraction of level XP", "unverified",
   "Forever camp: instant rested pool granted when sitting at a campfire ('Welcoming Campfire' -> "
   "'Camp Benefits'); no Classic counterpart (0)", classic=0.0)
_p("rested.camp_benefits_xp_mod", 0.0, "fraction", "unverified",
   "effect of the 1 h 'Camp Benefits' buff on kill XP (none seen so far); no Classic counterpart",
   classic=0.0)
_p("rested.boosted_rest_mult", 1.0, "x accrual", "unverified",
   "'Boosted Rest' buff seen in game; effect on accrual unknown; no Classic counterpart", classic=1.0)

# ---------------------------------------------------------------------------------------------------
# Travel
# ---------------------------------------------------------------------------------------------------
_p("travel.run_speed", 7.0, "yd/s", "classic", "run speed 7.0 yd/s (Classic)")
_p("travel.walk_speed", 2.5, "yd/s", "classic", "walk speed 2.5 yd/s (Classic)")
_p("travel.swim_speed", 4.722, "yd/s", "classic", "swim speed 4.722 yd/s (Classic)")
_p("travel.ghost_speed", 7.0, "yd/s", "unverified",
   "ghost (corpse run) speed; vanilla ghosts used the normal run speed (Night Elf wisp faster)")
_p("travel.stealth_mult", 0.5, "x run speed", "unverified", "rogue Stealth movement (rank 1, Classic)")
_p("travel.taxi_speed", 32.0, "yd/s", "unverified", "flight path speed (community estimate for Classic)")
_p("travel.taxi_overhead_s", 10.0, "s", "unverified", "talk to flight master + take-off/landing (heuristic)")
_p("travel.hearth_cd_s", 3600.0, "s", "unverified", "Hearthstone cooldown 60 min (Classic Era)")
_p("travel.hearth_cast_s", 10.0, "s", "classic", "Hearthstone cast 10 s (Classic)")
_p("travel.detour_factor", 1.0, "x straight line", "unverified",
   "path length / straight-line distance; not modelled (1.0); real routes are longer")
_p("travel.plane_speed_ratio", 1.0, "x run speed", "classic",
   "map-plane distance covered per second / GetUnitSpeed while running (slopes and turns make it < 1)")
_p("mount.level", 40, "level", "unverified", "apprentice riding at 40 (Classic); Forever unknown")
_p("mount.speed_bonus", 0.6, "fraction", "unverified", "apprentice mount +60% speed (Classic)")
_p("mount.cost", 1000000, "copper", "unverified", "riding skill + first mount ~100g at 40 (Classic)")
_p("mount.epic_level", 60, "level", "unverified", "epic riding at 60 (Classic)")
_p("mount.epic_speed_bonus", 1.0, "fraction", "unverified", "epic mount +100% speed (Classic)")
_p("mount.epic_cost", 10000000, "copper", "unverified", "epic riding + mount ~1000g at 60 (Classic)")

# ---------------------------------------------------------------------------------------------------
# Combat
# ---------------------------------------------------------------------------------------------------
_p("combat.aggro.base_yd", 20.0, "yd", "unverified", "aggro radius ~20 yd vs a same-level mob (Classic heuristic)")
_p("combat.aggro.per_level_yd", 1.0, "yd/level", "unverified", "+1 yd per level the mob is above, -1 per level below")
_p("combat.aggro.min_yd", 5.0, "yd", "unverified", "aggro radius floor (Classic heuristic)")
_p("combat.aggro.max_yd", 45.0, "yd", "unverified", "aggro radius ceiling (Classic heuristic)")
_p("combat.daze.slow", 0.5, "fraction", "unverified", "Dazed: movement slowed 50% (Classic, mobs hitting from behind)")
_p("combat.daze.duration_s", 4.0, "s", "unverified", "Dazed duration (Classic heuristic)")
_p("combat.daze.chance", 0.15, "probability/hit from behind", "unverified", "rough chance per hit from behind (guess)")
_p("combat.mob_hp", {"1": 42, "2": 55, "3": 71, "4": 86, "5": 102, "6": 120, "7": 137, "8": 156, "9": 176,
                     "10": 198, "12": 247, "14": 300, "16": 356, "18": 417, "20": 484, "25": 680, "30": 1000,
                     "35": 1350, "40": 1750, "45": 2200, "50": 2700, "55": 3250, "60": 3900},
   "hp by mob level (normal rank)", "unverified",
   "approximate Classic normal-creature HP curve (interpolated); Forever HP is secret in combat")
_p("combat.rank_hp_mult", {"0": 1.0, "1": 2.5, "2": 3.0, "3": 6.0, "4": 1.5}, "x hp by rank", "unverified",
   "rank 0 normal, 1 elite, 2 rare elite, 3 boss, 4 rare (rough Classic ratios)")
_p("combat.dps_ref", {"1": 3.5, "2": 4.3, "3": 5.0, "4": 5.8, "5": 6.6, "6": 7.4, "8": 9.0, "10": 11.0, "12": 13.0,
                      "15": 16.5, "20": 23.0, "25": 30.0, "30": 38.0, "35": 46.0, "40": 55.0, "45": 65.0,
                      "50": 76.0, "55": 88.0, "60": 100.0},
   "dps by player level", "unverified", "reference leveling DPS (melee, quest gear), interpolated; prior only")
_p("combat.class_dps", {"WARRIOR": 1.0, "PALADIN": 0.85, "HUNTER": 1.25, "ROGUE": 1.1, "PRIEST": 0.8,
                        "SHAMAN": 0.95, "MAGE": 1.3, "WARLOCK": 1.1, "DRUID": 0.9},
   "x dps_ref", "unverified", "class DPS multipliers for leveling (prior)")
_p("combat.level_diff_ttk", 0.06, "fraction/level above", "unverified",
   "time-to-kill grows ~6% per level the mob is above the player (misses, glancing; prior)")
for _c in CLASSES:
    _p("combat.ttk_scale.%s" % _c, 1.0, "x model ttk", "unverified",
       "calibration of time_to_kill_s for %s (fit.py: median observed/model)" % _c)
_p("combat.downtime_ref", {"1": 2.0, "10": 4.0, "20": 7.0, "30": 9.0, "40": 10.0, "60": 12.0},
   "s per fight by level", "unverified", "eat/drink/bandage time per fight for an average class (prior)")
_p("combat.class_downtime", {"WARRIOR": 1.3, "PALADIN": 0.6, "HUNTER": 0.4, "ROGUE": 1.0, "PRIEST": 0.8,
                             "SHAMAN": 0.7, "MAGE": 0.9, "WARLOCK": 0.4, "DRUID": 0.6},
   "x downtime_ref", "unverified", "class downtime multipliers (prior)")

# ---------------------------------------------------------------------------------------------------
# Death
# ---------------------------------------------------------------------------------------------------
_p("death.sickness.free_through", 10, "level", "unverified",
   "no Resurrection Sickness at level <= 10 (Classic: none through 10, 1 min per level above 10, max 10 min)")
_p("death.sickness.per_level_s", 60.0, "s/level above free_through", "unverified", "Classic: 1 min per level over 10")
_p("death.sickness.max_s", 600.0, "s", "unverified", "Classic: 10 min from level 20")
_p("death.durability.death", 0.10, "fraction of max", "classic", "every item loses 10% durability on death (Classic)")
_p("death.durability.spirit", 0.25, "fraction of max", "unverified",
   "spirit-healer resurrection costs a further 25% durability (Classic)")
_p("death.durability.min_level", 1, "level", "unverified",
   "lowest level that takes durability loss (Classic: every level)")
_p("death.release_s", 3.0, "s", "unverified", "time from death to release (player habit)")
_p("death.spirit_res_s", 6.0, "s", "unverified", "release -> spirit healer resurrection when the healer is at hand")
_p("death.corpse_res_s", 3.0, "s", "unverified", "reaching the corpse -> resurrect click")
_p("death.recover_s", 15.0, "s", "unverified", "eat/drink after resurrection (heuristic)")
_p("death.sickness_cost_frac", 0.75, "fraction of sickness", "unverified",
   "share of the sickness duration that is lost time (you can still travel/turn in; heuristic)")

# ---------------------------------------------------------------------------------------------------
# Economy
# ---------------------------------------------------------------------------------------------------
_p("economy.train.per_spell_low", {"1": 10, "2": 10, "3": 10, "4": 100, "5": 100, "6": 100, "7": 100,
                                   "8": 200, "9": 200, "10": 300},
   "copper per class spell rank by spell level", "classic", "Classic trainer prices at levels 1-10")
_p("economy.train.l10_cost", 300, "copper", "classic", "per-rank cost at level 10, anchor of the curve above 10")
_p("economy.train.exponent", 3.0, "power", "unverified",
   "per-rank cost above 10 ~ l10_cost*(level/10)^3 (~24s at 20, ~1.9g at 40, ~6.5g at 60; approximate)")
_RXP_SPELLS = {  # number of spell ranks per level in RestedXP's Forever default spell list (DB/forever/spells.lua)
    "DRUID": {1: 1, 4: 2, 6: 2, 8: 2, 10: 4, 12: 2, 14: 4, 16: 3, 18: 5, 20: 6, 22: 1, 24: 5, 26: 4, 28: 3, 30: 6, 32: 4,
              34: 3, 36: 5, 38: 3, 40: 7, 42: 4, 44: 5, 46: 5, 48: 5, 50: 3, 52: 2, 54: 4, 56: 4, 58: 4, 60: 8},
    "PALADIN": {1: 2, 4: 2, 6: 3, 8: 2, 10: 3, 12: 2, 14: 4, 16: 1, 18: 2, 20: 4, 22: 3, 24: 4, 26: 2, 28: 2, 30: 5,
                32: 2, 34: 3, 36: 2, 38: 3, 40: 5, 42: 4, 44: 3, 46: 3, 50: 7, 52: 5, 54: 4, 56: 1, 58: 2, 60: 9},
    "SHAMAN": {1: 1, 4: 1, 6: 1, 8: 5, 10: 3, 12: 3, 14: 2, 16: 3, 18: 5, 20: 5, 22: 4, 24: 6, 26: 5, 28: 5, 30: 6,
               32: 6, 34: 2, 36: 4, 38: 4, 40: 7, 42: 2, 44: 2, 46: 4, 48: 5, 50: 3, 52: 4, 54: 1, 56: 6, 58: 2, 60: 8},
    "HUNTER": {4: 1, 6: 2, 8: 3, 10: 6, 12: 4, 14: 2, 16: 2, 18: 4, 20: 10, 22: 4, 24: 2, 26: 3, 28: 6, 30: 2, 32: 1,
               34: 2, 36: 6, 38: 1, 40: 3, 42: 3, 44: 1, 48: 5, 50: 8, 52: 5, 54: 2, 56: 3, 58: 3, 60: 8},
    "WARRIOR": {1: 1, 4: 2, 6: 1, 8: 2, 10: 2, 12: 2, 14: 2, 16: 1, 20: 3, 22: 3, 24: 3, 26: 1, 28: 1, 30: 1, 32: 3,
                34: 2, 36: 1, 38: 1, 40: 2, 42: 1, 44: 2, 46: 2, 48: 4, 50: 1, 52: 1, 54: 4, 56: 1, 58: 1, 60: 5},
    "ROGUE": {1: 1, 6: 3, 8: 5, 10: 5, 12: 2, 14: 6, 16: 6, 20: 5, 22: 5, 24: 5, 26: 1, 28: 1, 30: 2, 32: 1, 34: 3,
              36: 5, 38: 2, 40: 3, 42: 2, 44: 2, 46: 2, 48: 1, 50: 2, 52: 4, 54: 2, 56: 1, 58: 1, 60: 5},
    "WARLOCK": {1: 1, 4: 1, 6: 2, 8: 2, 10: 4, 12: 2, 14: 2, 16: 2, 18: 3, 20: 6, 22: 2, 24: 2, 26: 4, 28: 4, 30: 7,
                32: 4, 34: 3, 36: 4, 38: 4, 40: 8, 42: 4, 44: 3, 46: 3, 48: 7, 50: 10, 52: 3, 54: 5, 56: 5, 58: 4,
                60: 15},
    "MAGE": {1: 1, 4: 1, 6: 2, 8: 2, 10: 4, 12: 5, 14: 3, 16: 1, 18: 2, 20: 12, 22: 2, 24: 5, 26: 2, 28: 5, 30: 7,
             32: 3, 34: 3, 36: 3, 38: 4, 40: 6, 42: 4, 44: 3, 46: 5, 48: 4, 50: 4, 52: 4, 54: 3, 56: 3, 58: 5, 60: 9},
    "PRIEST": {1: 1, 4: 2, 6: 2, 8: 1, 10: 5, 12: 3, 14: 4, 16: 2, 18: 5, 20: 9, 22: 5, 24: 3, 26: 4, 28: 5, 30: 8,
               32: 3, 34: 5, 36: 8, 38: 2, 40: 7, 42: 5, 44: 6, 46: 2, 48: 3, 50: 7, 52: 6, 54: 1, 56: 5, 58: 4,
               60: 12},
}
for _c, _tbl in _RXP_SPELLS.items():
    _p("economy.train.spells.%s" % _c, {str(k): v for k, v in sorted(_tbl.items())}, "ranks by level", "classic",
       "count of spell ranks per level in RestedXP's Forever default spell list (reference/rxp, personal use)")


# ---------------------------------------------------------------------------------------------------
# access
# ---------------------------------------------------------------------------------------------------
def _stack():
    s = getattr(_local, "stack", None)
    if s is None:
        s = _local.stack = []
    return s


class Snapshot:
    """The parameter values one KB was built with (its `param` table): entries for the keys whose value, status or
    evidence differs from the registry's, and every key the KB lists (a listed key without an entry has the registry
    default). Keys the KB doesn't list (added to the code since) fall through to the process's overrides."""

    def __init__(self, key, entries, listed):
        self.key, self.entries, self.listed = key, entries, frozenset(listed)


def snapshot_of(kb, key=None):
    """The Snapshot of a KB (store.KB-like: q(), meta()). A KB built before the analyzer's rows went into the param
    table adds its meta 'analyzer_params'; None when the KB has neither."""
    entries, listed = {}, set()
    try:
        rows = kb.q("SELECT key, value, status, evidence, updated FROM param")
    except Exception:  # noqa: BLE001 - an unreadable KB has no snapshot
        rows = []
    for r in rows or ():
        k = r.get("key")
        if k not in _REG:
            continue
        try:
            v = json.loads(r["value"]) if r.get("value") is not None else None
        except (TypeError, ValueError):
            continue
        listed.add(k)
        reg = _REG[k]
        if r.get("status") in STATUSES and (v, type(v), r.get("status"), r.get("evidence")) != (
                reg["value"], type(reg["value"]), reg["status"], reg["evidence"]):
            entries[k] = {"value": v, "status": r["status"], "evidence": r.get("evidence"),
                          "updated": r.get("updated")}
    if not any(_LANE[k] != MECHANICS for k in listed):
        try:
            fitted = json.loads(kb.meta("analyzer_params") or "null")
        except (TypeError, ValueError, AttributeError):
            fitted = None
        for k, ov in fitted.items() if isinstance(fitted, dict) else ():
            if k not in _REG or k in listed:
                continue
            ov = ov if isinstance(ov, dict) and "value" in ov else {"value": ov}
            st = ov.get("status") or "forever-fitted"
            if st in STATUSES:
                entries[k] = {"value": ov["value"], "status": st, "evidence": ov.get("evidence")}
                listed.add(k)
    if not listed:
        return None
    return Snapshot(key if key is not None else getattr(kb, "path", None) or id(kb), entries, listed)


def use_kb(kb_or_snapshot):
    """Make one KB's values the current ones: a Snapshot, a KB to read one from, or None (no KB: the process's
    overrides). The previous KB's values are dropped, never merged."""
    global _NOW
    snap = kb_or_snapshot
    if snap is _NOW:
        return                            # the usual case: kbdata.get_data on every read of the same KB
    if snap is not None and not isinstance(snap, Snapshot):
        snap = snapshot_of(snap)
    with _LOCK:
        _NOW = snap


def current_kb():
    """The key (path) of the KB whose values are current, or None."""
    now = _NOW
    return now.key if now is not None else None


def _override(key):
    """The override in force for a registered key: the KB in use's (None when it lists the key at its default),
    else the process's (params.json, fitted values applied at build time), else None."""
    now = _NOW
    if now is not None:
        e = now.entries.get(key)
        if e is not None:
            return e
        if key in now.listed:
            return None
    return _OVR.get(key)


def get(key, default=_MISSING):
    """Current value: innermost scoped overlay, then the KB in use, then the data/params.json override, then the
    registry default."""
    for layer in reversed(_stack()):
        if layer is _CLASSIC:
            if key in _REG:
                return _REG[key]["classic_value"]
        elif key in layer:
            return layer[key]
    if key in _REG:
        o = _override(key)
        return o["value"] if o is not None else _REG[key]["value"]
    if default is not _MISSING:
        return default
    raise KeyError(key)


def status(key):
    o = _override(key) if key in _REG else None
    if o is not None:
        return o["status"]
    return _REG[key]["status"]


def classic(key):
    return _REG[key]["classic_value"]


def lane(key):
    return _LANE[key]


def keys(lane=MECHANICS):
    return sorted(k for k, v in _LANE.items() if v == lane)


def info(key):
    row = dict(_REG[key])
    o = _override(key)
    if o is not None:
        row.update(value=o["value"], status=o["status"], evidence=o.get("evidence") or row["evidence"],
                   updated=o.get("updated") or row["updated"], prior_evidence=_REG[key]["evidence"])
    return row


def all(lane=MECHANICS):  # noqa: A001 (public API name from the contract)
    """Every parameter of a lane as a row dict for the `param` table (values as Python objects)."""
    return [info(k) for k in keys(lane)]


def _txt(v):
    return None if v is None else json.dumps(v, separators=(",", ":"), sort_keys=True)


def to_rows():
    """Tuples ready for INSERT INTO param(key,value,unit,classic_value,status,evidence,updated)."""
    return [(r["key"], _txt(r["value"]), r["unit"], _txt(r["classic_value"]), r["status"], r["evidence"],
             r["updated"]) for r in all()]


def apply(overrides, lane=MECHANICS):
    """Apply {key: value} / {key: {"value", "status", "evidence"}} to the keys of one lane (other keys are
    ignored). Returns the applied keys."""
    done = []
    with _LOCK:
        for k, v in (overrides or {}).items():
            if _LANE.get(k) != lane:
                continue
            if not isinstance(v, dict) or "value" not in v:
                v = {"value": v}
            st = v.get("status") or "forever-fitted"
            if st not in STATUSES:
                continue
            _OVR[k] = {"value": v["value"], "status": st, "evidence": v.get("evidence")}
            done.append(k)
    return done


def load_overrides(path_or_dict=None):
    """Apply fitted/verified overrides. Accepts a path (default data/params.json), a dict
    {key: {"value", "status", "evidence", "updated"}} / {key: value}, or {"params": {...}}.
    Returns {"applied": [...], "ignored": [...], "source": ...}. Never raises on a missing file."""
    src = path_or_dict
    if src is None:
        src = DEFAULT_OVERRIDES
    data = src
    if isinstance(src, str):
        if not os.path.exists(src):
            return {"applied": [], "ignored": [], "source": None}
        with open(src, encoding="utf-8") as fh:
            data = json.load(fh)
    if isinstance(data, dict) and isinstance(data.get("params"), dict):
        data = data["params"]
    applied, ignored = [], []
    for key, ov in (data or {}).items():
        if _LANE.get(key) != MECHANICS:
            ignored.append(key)
            continue
        if not isinstance(ov, dict) or "value" not in ov:
            ov = {"value": ov}
        st = ov.get("status") or "forever-fitted"
        if st not in STATUSES:
            ignored.append(key)
            continue
        _OVR[key] = {"value": ov["value"], "status": st, "evidence": ov.get("evidence"),
                     "updated": ov.get("updated")}
        applied.append(key)
    if isinstance(src, str):
        _loaded_from.append(src)
    return {"applied": applied, "ignored": ignored, "source": src if isinstance(src, str) else "dict"}


def overrides(lane=MECHANICS):
    return copy.deepcopy({k: v for k, v in _OVR.items() if _LANE[k] == lane})


def reset(lane=MECHANICS):
    """Drop a lane's overrides and the KB in use's values (registry defaults only, until the next use_kb)."""
    global _NOW
    with _LOCK:
        for k in [k for k in _OVR if _LANE[k] == lane]:
            del _OVR[k]
        _NOW = None
    if lane == MECHANICS:
        del _loaded_from[:]


def reload(path=None):
    reset()
    return load_overrides(path)


@contextmanager
def scoped(values):
    """Temporarily set values (thread-local). Unknown keys are allowed only if registered."""
    for k in values:
        if k not in _REG:
            raise KeyError(k)
    st = _stack()
    st.append(dict(values))
    try:
        yield
    finally:
        st.pop()


@contextmanager
def classic_scope(extra=None):
    """Every parameter returns its Classic value (optionally with `extra` values on top)."""
    st = _stack()
    st.append(_CLASSIC)
    if extra:
        st.append(dict(extra))
    try:
        yield
    finally:
        if extra:
            st.pop()
        st.pop()


try:                                  # best known values by default; never fail at import
    load_overrides()
except (OSError, ValueError):
    pass
