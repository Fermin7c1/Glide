"""Movement and travel times. Coordinates are map percent (0..100); map sizes in yards.

Classic: run 7.0 yd/s, walk 2.5, swim 4.722, apprentice mount +60% at 40, epic +100% at 60,
hearthstone 60 min. Forever (live logs, GetUnitSpeed): run 7.0, swim 4.7x, ghost 8.75, taxi 30.3
instantaneous; hearthstone cooldown 3600 s. Mount level/cost are unverified.
"""
import math

from . import params as P


def distance_yd(map_w_yd, map_h_yd, x1, y1, x2, y2):
    """Straight-line yards between two points of the same map (x, y in percent)."""
    return math.hypot((float(x2) - float(x1)) / 100.0 * float(map_w_yd),
                      (float(y2) - float(y1)) / 100.0 * float(map_h_yd))


def mount_bonus(level):
    """Speed bonus of the best mount available at `level` (0 below the riding level)."""
    if level is None:
        return 0.0
    if int(level) >= int(P.get("mount.epic_level")):
        return float(P.get("mount.epic_speed_bonus"))
    if int(level) >= int(P.get("mount.level")):
        return float(P.get("mount.speed_bonus"))
    return 0.0


def run_speed_yds(mounted=False, level=None):
    """Ground speed: run speed, times the mount bonus when mounted and the level allows a mount."""
    v = float(P.get("travel.run_speed"))
    if mounted:
        v *= 1.0 + mount_bonus(level if level is not None else P.get("mount.level"))
    return v


def map_speed_yds(mounted=False, level=None):
    """Speed over map distance (yards on the map plane per second): run speed x plane_speed_ratio."""
    return run_speed_yds(mounted, level) * float(P.get("travel.plane_speed_ratio"))


def run_time_s(dist_yd, mounted=False, level=None):
    """Seconds to cover a map distance dist_yd on foot (or mounted): detour factor and the map-plane
    speed ratio (terrain) included."""
    return float(dist_yd) * float(P.get("travel.detour_factor")) / map_speed_yds(mounted, level)


def swim_time_s(dist_yd):
    return float(dist_yd) / float(P.get("travel.swim_speed"))


def corpse_run_s(dist_yd):
    """Ghost run from the graveyard to the corpse."""
    return float(dist_yd) * float(P.get("travel.detour_factor")) / float(P.get("travel.ghost_speed"))


def hearth_cooldown_s():
    return float(P.get("travel.hearth_cd_s"))


def hearth_time_s():
    """Cast time of the hearthstone (plus nothing else: loading screens are not modelled)."""
    return float(P.get("travel.hearth_cast_s"))


def taxi_time_s(dist_yd):
    """Flight time estimate from path length (prefer known flight times, e.g. RXP flightData)."""
    return float(dist_yd) / float(P.get("travel.taxi_speed")) + float(P.get("travel.taxi_overhead_s"))
