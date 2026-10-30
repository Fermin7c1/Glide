"""Death costs: resurrection sickness, durability, time lost.

Classic: no Resurrection Sickness through level 10, then 1 min per level above 10, 10 min from 20
(spirit-healer resurrection only); every death costs 10% durability, the spirit healer a further 25%.
Forever (live logs): no durability loss at all below level 10 (deaths and spirit-healer resurrections
at 4-9), 10% at level 10; ghosts run at 8.75 yd/s. Sickness above 10 is unverified.
"""
from . import params as P
from . import travel


def res_sickness_s(level):
    """Resurrection Sickness duration after a spirit-healer resurrection."""
    lv = int(level)
    free = int(P.get("death.sickness.free_through"))
    if lv <= free:
        return 0.0
    return min(float(P.get("death.sickness.max_s")), (lv - free) * float(P.get("death.sickness.per_level_s")))


def durability_loss(spirit_res=False, level=None):
    """Fraction of max durability lost on every item (death plus spirit healer)."""
    if level is not None and int(level) < int(P.get("death.durability.min_level")):
        return 0.0
    loss = float(P.get("death.durability.death"))
    if spirit_res:
        loss += float(P.get("death.durability.spirit"))
    return loss


def death_cost_s(level, corpse_run_s, spirit_res=False):
    """Seconds lost to one death: release + (corpse run + res) or (spirit res + lost sickness time),
    plus recovery (eat/drink). corpse_run_s may be None: then a spirit res is assumed."""
    t = float(P.get("death.release_s")) + float(P.get("death.recover_s"))
    if spirit_res or corpse_run_s is None:
        t += float(P.get("death.spirit_res_s")) + res_sickness_s(level) * float(P.get("death.sickness_cost_frac"))
    else:
        t += float(corpse_run_s) + float(P.get("death.corpse_res_s"))
    return t


def corpse_run_from_distance_s(dist_yd):
    """Ghost run time for a graveyard -> corpse distance in yards."""
    return travel.corpse_run_s(dist_yd)


def best_res(level, dist_yd):
    """Compare corpse run vs spirit healer for a graveyard -> corpse distance. Returns facts."""
    run = travel.corpse_run_s(dist_yd)
    c_corpse = death_cost_s(level, run, spirit_res=False)
    c_spirit = death_cost_s(level, None, spirit_res=True)
    return {"corpse_s": round(c_corpse, 1), "spirit_s": round(c_spirit, 1),
            "sickness_s": res_sickness_s(level), "durability_spirit": durability_loss(True, level),
            "durability_corpse": durability_loss(False, level),
            "faster": "spirit" if c_spirit < c_corpse else "corpse"}
