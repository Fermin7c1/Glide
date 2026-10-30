"""Money: class training costs and mounts (copper).

Per spell rank: Classic trainer prices through level 10 (10c at 1-3, 1s at 4-7, 2s at 8-9, 3s at 10;
Forever matched these at 2/4/6/8/10), then ~3s*(level/10)^3 (approximate, unverified). Number of ranks
per level: RestedXP's Forever default spell list. Mount: ~100g at 40, ~1000g epic at 60 (Classic,
unverified on Forever).
"""
from . import params as P


def spell_cost(level):
    """Copper for one spell rank whose required level is `level`."""
    lv = int(level)
    low = P.get("economy.train.per_spell_low")
    if str(lv) in low:
        return int(low[str(lv)])
    if lv < 1:
        return 0
    return int(round(float(P.get("economy.train.l10_cost")) * (lv / 10.0) ** float(P.get("economy.train.exponent"))))


def spells_at(player_class, level):
    tbl = P.get("economy.train.spells.%s" % (player_class or "").upper(), None)
    if tbl is None:
        return 2 if int(level) % 2 == 0 else 0
    return int(tbl.get(str(int(level)), 0))


def training_cost(player_class, level):
    """Copper to train everything new at `level` (0 at levels without new ranks)."""
    return spells_at(player_class, level) * spell_cost(level)


def training_cost_range(player_class, level_from, level_to):
    """Copper for all training from level_from to level_to inclusive."""
    return sum(training_cost(player_class, lv) for lv in range(int(level_from), int(level_to) + 1))


def mount_cost(level, epic=False):
    """Copper for riding + mount available at `level` (0 if none is available yet)."""
    lv = int(level)
    if epic:
        return int(P.get("mount.epic_cost")) if lv >= int(P.get("mount.epic_level")) else 0
    return int(P.get("mount.cost")) if lv >= int(P.get("mount.level")) else 0
