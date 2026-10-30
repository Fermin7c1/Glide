"""Experience: level table, kill XP, quest XP. Classic formulas as defaults, Forever overrides via params.

Kill XP (WoWWiki "Formulas:Mob XP"; cmangos/TrinityCore BaseGain):
  base(L)         = 5*L + 45                                  (xp.kill.base_mul / base_add)
  mob >= player   : base(player) * (1 + 0.05 * min(mob-player, 4))
  gray < mob < pl : B * (1 - (player-mob)/ZD(player)), B = base(player) in Classic,
                    B = base(mob) when xp.kill.low_base == "mob" (what Forever does, see fit.py)
  mob <= gray     : 0
  elite           : x2 (xp.kill.elite_mult) before rounding
  rounding        : xp.kill.rounding (round half to even fits every Forever observation)
  modifiers       : e.g. Forever Well Fed +5% (xp.mod.well_fed), applied to the rounded value, rounded again
  group           : Classic split, xp computed for the highest-level member, each gets
                    xp * rate(n) * own_level / sum_levels (truncated)
  rested          : bonus = kill XP (doubling); with an explicit pool, bonus = min(xp, floor(pool*0.5))
                    and the client pool drops by 2*bonus (rested.bonus_ratio)
All arithmetic is exact (fractions): 52.5 must round like 52.5, not 52.500000000000002.
"""
from fractions import Fraction
import math

from . import params as P


def _F(v):
    return v if isinstance(v, Fraction) else Fraction(str(v))


def round_xp(x, mode=None):
    """Round a Fraction/number to an int with the kill-XP rounding rule."""
    x = _F(x)
    mode = mode or P.get("xp.kill.rounding")
    if mode == "floor":
        return int(math.floor(x))
    if mode == "half_up":
        return int(math.floor(x + Fraction(1, 2)))
    if mode == "ceil":
        return int(math.ceil(x))
    # half_even
    q = math.floor(x)
    r = x - q
    if r > Fraction(1, 2):
        return int(q + 1)
    if r < Fraction(1, 2):
        return int(q)
    return int(q + (q % 2))


# ---------------------------------------------------------------------------------------------------
# level table
# ---------------------------------------------------------------------------------------------------
def max_level():
    return int(P.get("xp.max_level"))


def level_xp(level):
    """XP needed to go from `level` to level+1 (0 at or above the cap)."""
    level = int(level)
    if level < 1:
        raise ValueError("level must be >= 1")
    if level >= max_level():
        return 0
    return int(P.get("xp.level.%d" % level))


def total_xp(level_from=1, level_to=None):
    """XP from the start of level_from to the start of level_to."""
    level_to = max_level() if level_to is None else level_to
    return sum(level_xp(lv) for lv in range(int(level_from), int(level_to)))


def xp_to_reach(level_from, xp_into, level_to):
    """XP still needed to reach level_to from (level_from, xp_into); 0 if already there."""
    if level_to <= level_from:
        return 0
    return max(0, total_xp(level_from, level_to) - int(xp_into or 0))


# ---------------------------------------------------------------------------------------------------
# kill XP
# ---------------------------------------------------------------------------------------------------
def _table(key, level):
    t = P.get(key)
    return int(t[max(0, min(int(level), len(t) - 1))])


def zero_difference(player_level):
    """Classic ZD: 1-7 5, 8-9 6, 10-11 7, 12-15 8, 16-19 9, 20-29 11, 30-39 12, 40-44 13, 45-49 14,
    50-54 15, 55-59 16, 60 17."""
    return _table("xp.zd.table", player_level)


def gray_level(player_level):
    """Mobs at or below this level give no XP (0 = nothing is gray)."""
    return _table("xp.gray.table", player_level)


def base_xp(level):
    return int(P.get("xp.kill.base_mul")) * int(level) + int(P.get("xp.kill.base_add"))


def _raw_kill(player_level, mob_level, elite):
    """Unrounded kill XP (Fraction) before modifiers, or 0 for gray."""
    pl, ml = int(player_level), int(mob_level)
    if ml <= gray_level(pl):
        return Fraction(0)
    if ml >= pl:
        d = min(ml - pl, int(P.get("xp.kill.high_cap")))
        x = base_xp(pl) * (1 + _F(P.get("xp.kill.high_step")) * d)
    else:
        zd = zero_difference(pl)
        b = base_xp(ml) if P.get("xp.kill.low_base") == "mob" else base_xp(pl)
        x = Fraction(b) * (zd - (pl - ml)) / zd
        if x <= 0:
            return Fraction(0)
    if elite:
        x *= _F(P.get("xp.kill.elite_mult"))
    return x


def group_rate(n):
    rates = P.get("xp.group.rate")
    n = max(1, int(n))
    return float(rates.get(str(min(n, 5)), 1.0))


def kill_xp_detail(player_level, mob_level, elite=False, rested=False, group_size=1, *, rest_pool=None,
                   xp_mod=0.0, group_levels=None):
    """Kill XP with every step exposed: {xp, base, mod, group, bonus, pool_after, gray, rule}.

    rested=True without rest_pool doubles the kill XP (Classic rule). rest_pool = client
    GetXPExhaustion() before the kill: bonus = min(xp, floor(pool*rested.bonus_ratio)), pool_after =
    pool - bonus/ratio. xp_mod = summed XP aura bonus (Forever Well Fed: params xp.mod.well_fed).
    group_levels: levels of every party member including the player (Classic split); group_size
    alone assumes equal levels."""
    pl, ml = int(player_level), int(mob_level)
    out = {"gray": gray_level(pl), "zd": zero_difference(pl), "rule": P.get("xp.kill.low_base")}
    if pl >= max_level():
        out.update(xp=0, base=0, mod=0, group=0, bonus=0, pool_after=rest_pool, note="level cap")
        return out
    levels = [int(v) for v in group_levels] if group_levels else None
    top = max(levels) if levels else pl
    x = _raw_kill(top, ml, elite)
    base = round_xp(x)
    out["base"] = base
    xp = base
    if xp and xp_mod:
        xp = round_xp(Fraction(xp) * (1 + _F(xp_mod)))
    out["mod"] = xp
    if xp and (levels and len(levels) > 1 or group_size and int(group_size) > 1):
        n = len(levels) if levels else int(group_size)
        share = Fraction(pl, sum(levels)) if levels else Fraction(1, n)
        xp = int(math.floor(xp * _F(group_rate(n)) * share))
    out["group"] = xp
    bonus, pool_after = 0, rest_pool
    if xp and rest_pool is not None:
        ratio = _F(P.get("rested.bonus_ratio"))
        bonus = min(xp, int(math.floor(_F(max(0, rest_pool)) * ratio)))
        pool_after = max(0, int(rest_pool) - int(round(bonus / ratio)))
    elif xp and rested:
        bonus = round_xp(xp * (_F(P.get("xp.kill.rested_mult")) - 1))
    out.update(bonus=bonus, pool_after=pool_after, xp=xp + bonus)
    return out


def kill_xp(player_level, mob_level, elite=False, rested=False, group_size=1, *, rest_pool=None, xp_mod=0.0,
            group_levels=None):
    """XP for one kill (int). See kill_xp_detail for the keyword arguments."""
    return kill_xp_detail(player_level, mob_level, elite, rested, group_size, rest_pool=rest_pool,
                          xp_mod=xp_mod, group_levels=group_levels)["xp"]


def kills_to_level(player_level, xp_into, mob_level, elite=False, rest_pool=0, xp_mod=0.0):
    """Number of identical kills to finish the current level (rested pool consumed as it goes)."""
    need = max(0, level_xp(player_level) - int(xp_into or 0))
    n, pool = 0, int(rest_pool or 0)
    while need > 0:
        d = kill_xp_detail(player_level, mob_level, elite, rest_pool=pool, xp_mod=xp_mod)
        if d["xp"] <= 0:
            return None
        need -= d["xp"]
        pool = d["pool_after"] or 0
        n += 1
        if n > 100000:
            return None
    return n


# ---------------------------------------------------------------------------------------------------
# quest XP
# ---------------------------------------------------------------------------------------------------
def quest_xp_factor(quest_level, player_level):
    """1.0 while player <= quest+5; then 0.8, 0.6, 0.4, 0.2 at +6..+9 and 0.1 from +10 (Classic)."""
    d = int(player_level) - int(quest_level)
    full = int(P.get("xp.quest.full_until"))
    if d <= full:
        return 1.0
    f = P.get("xp.quest.factors")
    return float(f[min(d - full - 1, len(f) - 1)])


def round_quest_xp(xp):
    """Server rounding of quest XP: <=100 to 5, <=500 to 10, <=1000 to 25, else to 50."""
    xp = int(xp)
    if P.get("xp.quest.rounding") != "bands":
        return xp
    if xp <= 100:
        return 5 * ((xp + 2) // 5)
    if xp <= 500:
        return 10 * ((xp + 5) // 10)
    if xp <= 1000:
        return 25 * ((xp + 12) // 25)
    return 50 * ((xp + 25) // 50)


def quest_xp(full_xp, quest_level, player_level):
    """Quest XP at player_level for a quest worth full_xp at full value (raw or already rounded);
    None if full_xp is unknown, 0 at the level cap."""
    if full_xp is None:
        return None
    if int(player_level) >= max_level():
        return 0
    if quest_level is None:
        return round_quest_xp(full_xp)
    f = quest_xp_factor(quest_level, player_level)
    return round_quest_xp(int(math.floor(_F(full_xp) * _F(f))))
