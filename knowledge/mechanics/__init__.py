"""Game mechanics as pure functions with Forever-overridable parameters.

Public API (implemented in the submodules; the analyzer and the coach API import from here):

  xp.level_xp(level) -> int                      XP needed to go from `level` to level+1 (0 at the cap)
  xp.total_xp(level_from=1, level_to=None) -> int
  xp.xp_to_reach(level_from, xp_into, level_to) -> int
  xp.zero_difference(player_level) -> int        Classic ZD table
  xp.gray_level(player_level) -> int             mobs at or below give no XP
  xp.kill_xp(player_level, mob_level, elite=False, rested=False, group_size=1,
             *, rest_pool=None, xp_mod=0.0, group_levels=None) -> int
                                                 rest_pool = client rested pool before the kill
                                                 (bonus = min(xp, pool/2)); xp_mod = XP aura bonus
                                                 (Forever Well Fed 0.05); group_levels = party levels
  xp.kill_xp_detail(...) -> dict                 same, every step exposed (base, mod, group, bonus, pool_after)
  xp.kills_to_level(player_level, xp_into, mob_level, elite=False, rest_pool=0, xp_mod=0.0) -> int
  xp.quest_xp(full_xp, quest_level, player_level) -> int      over-level reduction + server rounding
  xp.quest_xp_factor(quest_level, player_level) -> float
  xp.round_quest_xp(xp) -> int                   <=100 to 5, <=500 to 10, <=1000 to 25, else 50
  rested.accrual_fraction(hours, resting=True) -> float       fraction of a level
  rested.cap_fraction() -> float
  rested.accrual_xp(level, hours, resting=True, pool=0) / hours_to_cap / bonus_capacity(pool)
  rested.kill_bonus(kill_xp, pool) -> (bonus, pool_after) / camp_bonus_xp(level) / summary(level, pool)
  travel.run_speed_yds(mounted=False, level=None) -> float
  travel.run_time_s(dist_yd, mounted=False, level=None) -> float
  travel.distance_yd(map_w_yd, map_h_yd, x1, y1, x2, y2) -> float   (x,y in percent)
  travel.hearth_cooldown_s() -> float
  travel.taxi_time_s(dist_yd) -> float
  travel.corpse_run_s(dist_yd) / swim_time_s(dist_yd) / mount_bonus(level)
  combat.aggro_radius_yd(player_level, mob_level) -> float
  combat.time_to_kill_s(player_class, player_level, mob_level, rank=0) -> float
  combat.downtime_s(player_class, player_level, fights) -> float
  combat.mob_hp / player_dps / daze() / kill_time_s
  death.res_sickness_s(level) -> float
  death.durability_loss(spirit_res=False, level=None) -> float
  death.death_cost_s(level, corpse_run_s, spirit_res=False) -> float
  death.best_res(level, dist_yd) -> dict
  economy.training_cost(player_class, level) -> int (copper)
  economy.spell_cost(level) / training_cost_range(player_class, a, b)
  economy.mount_cost(level, epic=False) -> int
  params.get(key) / params.status(key) / params.classic(key) / params.info(key) / params.all()
  params.to_rows() / params.load_overrides(path_or_dict) / params.scoped({...}) / params.classic_scope()
  fit.run(conn_glide_db, live_dir=None, seed_path=None, out_dir=None, write=True) -> report dict;
      writes data/mechanics_fit.json and data/params.json (strong overrides only)

Every parameter has a Classic default and a status (classic | unverified | forever-verified |
forever-fitted). params.py loads data/params.json at import, so values are the best known ones.
Forever findings so far (see data/mechanics_fit.json): lower-level kill XP uses base = 5*mob_level+45;
kill XP rounds half to even; rested bonus = min(xp, pool/2); Well Fed (1248422) +5% kill XP; XP table
1..12 equals vanilla (8800 at 11, 10100 at 12); camp gives +5% of a level rested; hearth 3600 s.
"""
from . import params, xp, rested, travel, combat, death, economy  # noqa: F401


def __getattr__(name):            # fit is only needed at build time: import it on first use
    if name == "fit":
        import importlib
        return importlib.import_module(__name__ + ".fit")
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
