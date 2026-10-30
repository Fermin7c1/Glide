"""Combat heuristics: aggro radius, daze, time-to-kill and downtime models (all parametric priors;
fit.py calibrates combat.ttk_scale.<CLASS> from recorded fights).

time_to_kill_s = mob_hp(mob_level, rank) / (dps_ref(player_level) * class_dps[class])
                 * (1 + level_diff_ttk * max(0, mob_level - player_level)) * ttk_scale[class]
downtime_s     = fights * downtime_ref(player_level) * class_downtime[class]
"""
from . import params as P


def _interp(table, x):
    """Linear interpolation in a {level: value} dict (string or int keys), clamped at the ends."""
    pts = sorted((float(k), float(v)) for k, v in table.items())
    x = float(x)
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def aggro_radius_yd(player_level, mob_level):
    """Classic heuristic: ~20 yd at equal level, +1 yd per mob level above, -1 per level below, 5..45."""
    r = float(P.get("combat.aggro.base_yd")) + float(P.get("combat.aggro.per_level_yd")) * (
        int(mob_level) - int(player_level))
    return max(float(P.get("combat.aggro.min_yd")), min(float(P.get("combat.aggro.max_yd")), r))


def daze():
    """Daze parameters (all unverified): slow fraction, duration, rough chance per hit from behind."""
    return {"slow": float(P.get("combat.daze.slow")), "duration_s": float(P.get("combat.daze.duration_s")),
            "chance": float(P.get("combat.daze.chance"))}


def mob_hp(mob_level, rank=0):
    mult = P.get("combat.rank_hp_mult").get(str(int(rank or 0)), 1.0)
    return _interp(P.get("combat.mob_hp"), mob_level) * float(mult)


def player_dps(player_class, player_level):
    cls = (player_class or "").upper()
    return _interp(P.get("combat.dps_ref"), player_level) * float(P.get("combat.class_dps").get(cls, 1.0))


def ttk_scale(player_class):
    cls = (player_class or "").upper()
    return float(P.get("combat.ttk_scale.%s" % cls, 1.0))


def time_to_kill_s(player_class, player_level, mob_level, rank=0, calibrated=True):
    """Seconds of combat per kill (pull to death) for a solo player against one mob."""
    d = max(0, int(mob_level) - int(player_level))
    t = mob_hp(mob_level, rank) / player_dps(player_class, player_level)
    t *= 1.0 + float(P.get("combat.level_diff_ttk")) * d
    if calibrated:
        t *= ttk_scale(player_class)
    return t


def downtime_s(player_class, player_level, fights=1):
    """Eat/drink/bandage seconds for `fights` fights."""
    cls = (player_class or "").upper()
    per = _interp(P.get("combat.downtime_ref"), player_level) * float(P.get("combat.class_downtime").get(cls, 1.0))
    return per * max(0, float(fights))


def kill_time_s(player_class, player_level, mob_level, rank=0):
    """Combat + downtime for one kill (no travel)."""
    return time_to_kill_s(player_class, player_level, mob_level, rank) + downtime_s(player_class, player_level, 1)
