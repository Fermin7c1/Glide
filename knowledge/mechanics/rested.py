"""Rested XP (Classic rules; Forever camp system as explicit parameters).

Pool unit = the client's GetXPExhaustion(): the amount of XP that will be earned at the doubled kill
rate. A kill earns bonus = min(kill_xp, floor(pool * 0.5)) and the pool drops by twice the bonus
(verified on Forever: pool 236 -> 120 -> 4 over two 58+58 kills; pool 70 gave a 35 bonus).

Accrual (Classic): 5% of the current level's XP per 8 h resting in an inn/capital (in-session too),
1/4 of that elsewhere (logged out in the world), capped at 150% of a level. Forever: in-inn accrual
matches 5%/8 h (live logs: +2 every ~214 s at level 8, ~115 s at level 12); sitting at a campfire grants
an instant pool (rested.camp_bonus, observed +5% of a level) and the 1 h 'Camp Benefits' buff;
'Boosted Rest' is unverified.
"""
import math

from . import params as P
from . import xp as X


def cap_fraction():
    return float(P.get("rested.cap"))


def rate_per_hour(resting=True):
    r = float(P.get("rested.rate_per_8h")) / 8.0
    return r if resting else r * float(P.get("rested.away_mult"))


def accrual_fraction(hours, resting=True):
    """Fraction of a level gained in `hours` (capped at cap_fraction())."""
    return min(cap_fraction(), max(0.0, float(hours)) * rate_per_hour(resting))


def accrual_xp(level, hours, resting=True, pool=0):
    """Pool after resting `hours` at `level`, starting from `pool` (XP, capped)."""
    lx = X.level_xp(level)
    cap = int(cap_fraction() * lx)
    return int(min(cap, int(pool or 0) + accrual_fraction(hours, resting) * lx))


def hours_to_cap(level=None, pool=0, resting=True):
    """Hours of rest needed to fill the pool from `pool` (fraction of a level if level is None)."""
    if level is None:
        missing = cap_fraction() - float(pool or 0)
    else:
        missing = cap_fraction() - float(pool or 0) / max(1, X.level_xp(level))
    return max(0.0, missing) / rate_per_hour(resting)


def bonus_capacity(pool):
    """Bonus XP a pool can still pay out (half of the client value)."""
    return int(math.floor(max(0, int(pool or 0)) * float(P.get("rested.bonus_ratio"))))


def kill_bonus(kill_xp, pool):
    """(bonus, pool_after) for one kill worth kill_xp before the rested bonus."""
    ratio = float(P.get("rested.bonus_ratio"))
    bonus = min(int(kill_xp), bonus_capacity(pool))
    return bonus, max(0, int(pool or 0) - int(round(bonus / ratio)))


def camp_bonus_xp(level):
    """Instant rested pool from sitting at a Forever campfire (0 if unknown/none)."""
    return int(float(P.get("rested.camp_bonus")) * X.level_xp(level))


def summary(level, pool):
    """Facts for the coach about the current rested state."""
    lx = X.level_xp(level)
    return {"pool": int(pool or 0), "bonus_left": bonus_capacity(pool),
            "pool_pct_level": round(100.0 * int(pool or 0) / lx, 1) if lx else None,
            "cap": int(cap_fraction() * lx), "h_to_cap_inn": round(hours_to_cap(level, pool), 1),
            "camp_bonus": camp_bonus_xp(level),
            "status": {k: P.status(k) for k in ("rested.rate_per_8h", "rested.cap", "rested.camp_bonus")}}
