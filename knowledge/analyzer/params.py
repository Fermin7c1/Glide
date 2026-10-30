"""Analyzer parameters: the time/XP cost model constants the route analyzer adds on top of mechanics.

They live in the one parameter registry (mechanics.params), registered under the lane "analyzer"; this module is
that lane's view, with the same row shape and statuses (classic | unverified | forever-verified | forever-fitted).
Keys are `analyzer.*`.

  get(key[, default])   current value of any key (analyzer or mechanics)
  status(key) / info(key) / all() / keys()      (keys() / all(): the analyzer's)
  apply(overrides)      {key: value} or {key: {"value", "status", "evidence"}} (fitted values, at build time)
  reset()               drop the analyzer's overrides
  load_from_kb(kb)      make the KB's values current (mechanics.params.use_kb)

Fitted values: route.load() fits analyzer.kill.search_s from obs_kill gaps and analyzer.calib.<CLASS> from
obs_step_time (when rows exist), applies them for the build and stores them in the param table (and meta
'analyzer_params'); at runtime they come from the KB in use (kbdata.get_data -> mechanics.params.use_kb), so a
newer KB's values replace the older one's.
"""
from knowledge.mechanics import params as MP

STATUSES = MP.STATUSES
REGISTERED = MP.REGISTERED
CLASSES = MP.CLASSES
LANE = "analyzer"
_MISSING = object()


def _p(key, value, unit, status, evidence):
    MP.register(key, value, unit, status, evidence, lane=LANE)


# ---- interaction costs ------------------------------------------------------------------------------
_p("analyzer.time.accept_s", 6.0, "s/quest", "unverified", "talk to the giver, read, accept (one quest)")
_p("analyzer.time.turnin_s", 6.0, "s/quest", "unverified", "talk to the ender, pick a reward, turn in (one quest)")
_p("analyzer.time.train_s", 20.0, "s", "unverified", "class/profession trainer visit (.train/.trainer)")
_p("analyzer.time.vendor_s", 20.0, "s", "unverified", "vendor visit: sell trash, buy food/ammo (.vendor/.buy)")
_p("analyzer.time.buy_s", 8.0, "s/item", "unverified", "buying a listed item (.collect without quest) at a vendor step")
_p("analyzer.time.home_s", 8.0, "s", "unverified", "bind the hearthstone at an innkeeper (.home)")
_p("analyzer.time.fp_s", 6.0, "s", "unverified", "discover a flight path (.fp)")
_p("analyzer.time.gossip_s", 3.0, "s", "unverified", "gossip option / skip gossip")
_p("analyzer.time.use_s", 3.0, "s", "unverified", "use an item / cast a quest spell")
_p("analyzer.time.interact_s", 5.0, "s/object", "unverified", "click one quest object (loot, open, use)")
_p("analyzer.time.object_search_s", 12.0, "s/object", "unverified",
   "moving between quest objects of one objective (spawn spacing; prior)")
_p("analyzer.time.explore_s", 5.0, "s", "unverified", "explore/area objective beyond the travel to the spot")
_p("analyzer.time.event_s", 120.0, "s", "unverified", "escort/event objective (prior)")
_p("analyzer.time.other_obj_s", 20.0, "s", "unverified", "talk/interact/other objective without kills")
_p("analyzer.time.unknown_obj_s", 180.0, "s", "unverified",
   "objective whose kill/loot source is unknown (no objective row, no drop source, no step mob)")
_p("analyzer.time.wait_max_s", 300.0, "s", "unverified", "cap for 'wait N minutes' read from step text")
_p("analyzer.hs.load_s", 8.0, "s", "unverified", "loading screen / queue after the hearthstone cast")
_p("analyzer.fly.overhead_s", 15.0, "s", "unverified", "talk to the flight master, take-off and landing")
_p("analyzer.death.time_to_die_s", 20.0, "s", "unverified", "deathskip: pull mobs and die (before the release)")

# ---- kills ------------------------------------------------------------------------------------------
_p("analyzer.kill.search_s", 8.0, "s/kill", "unverified",
   "finding/pulling/looting the next mob, on top of ttk + downtime (fitted from obs_kill gaps at build)")
_p("analyzer.kill.incidental_frac", 0.2, "kills/objective kill", "unverified",
   "extra kills (adds, mobs in the way) per objective kill; same mob type")
_p("analyzer.drop.default_rate", 0.4, "probability", "unverified",
   "drop chance used for a collect objective whose drop rate is unknown")
_p("analyzer.drop.min_rate", 0.05, "probability", "unverified", "floor for drop rates (protects against bad data)")
_p("analyzer.grind.radius_yd", 450.0, "yd", "unverified", "search radius for the mobs of a grind (.xp) step without .mob")

# ---- travel -----------------------------------------------------------------------------------------
_p("analyzer.travel.detour", 1.25, "x straight line", "unverified",
   "path length / straight line when no observed path exists; applied only while mechanics "
   "travel.detour_factor is unfitted (1.0)")
_p("analyzer.travel.edge_max_ratio", 2.5, "x straight line", "unverified",
   "an observed travel_edge path longer than this ratio is ignored (bad graph link)")
_p("analyzer.travel.cross_continent_s", 420.0, "s", "unverified", "boat/zeppelin/portal leg between continents (wait + ride)")
_p("analyzer.travel.tram_s", 200.0, "s", "unverified",
   "Deeprun Tram Ironforge <-> Stormwind: walk to the platform, wait, ride, walk out (prior)")
_p("analyzer.travel.unknown_s", 60.0, "s", "unverified", "travel leg whose distance cannot be computed (no map size/bounds)")
_p("analyzer.travel.instance_s", 30.0, "s", "unverified", "moving inside an instance map without size")

# ---- dungeons -----------------------------------------------------------------------------------------
_p("analyzer.dungeon.run_s", 2700.0, "s", "unverified", "one dungeon run with a group, entrance to last boss (prior)")
_p("analyzer.dungeon.kill_xp_per_level", 1200.0, "xp/level of player", "unverified",
   "kill XP of one dungeon run ~ this x player level (prior; quest XP counted separately)")

# ---- spatial reasoning ---------------------------------------------------------------------------------
_p("analyzer.colocated.radius_yd", 80.0, "yd", "classic", "co-located actions radius (brief contract)")
_p("analyzer.risk.radius_yd", 150.0, "yd", "classic", "dangerous-mob radius around a step spot (brief contract)")
_p("analyzer.alt.radius_yd", 600.0, "yd", "classic", "alternatives search radius")
_p("analyzer.alt.max_level_over", 3, "levels", "classic", "quests up to player level + this are alternatives")
_p("analyzer.alt.far_objective_yd", 900.0, "yd", "unverified", "objective farther than this from the giver = far_objectives")
_p("analyzer.alt.long_chain", 4, "quests", "unverified", "chains with more follow-ups than this = long_chain")
_p("analyzer.alt.low_drop", 0.2, "probability", "unverified", "drop rate below this = low_drop_rate")
_p("analyzer.on_the_way.ratio", 0.15, "fraction", "unverified",
   "a step is on the way when visiting it adds < this fraction (or < 40 yd) to a prev->next leg of at least "
   "100 yd, and it is not at the previous or next spot (>= 20 yd from both)")

# ---- calibration --------------------------------------------------------------------------------------
_p("analyzer.calib.prior_h", 3.0, "hours", "unverified",
   "weight of the 'model is right' prior when fitting analyzer.calib.<CLASS> from recorded step times: "
   "factor = (h*ratio + prior_h) / (h + prior_h), h = recorded hours")
for _c in CLASSES:
    _p("analyzer.calib.%s" % _c, 1.0, "x model step time", "unverified",
       "obs_step_time actual/model median for %s (1.0 until recorded step times exist)" % _c)


# ---------------------------------------------------------------------------------------------------
def get(key, default=_MISSING):
    return MP.get(key) if default is _MISSING else MP.get(key, default)


def status(key):
    return MP.status(key)


def info(key):
    return MP.info(key)


def keys():
    return MP.keys(LANE)


def all():  # noqa: A001
    return MP.all(LANE)


def apply(overrides):
    """Apply {key: value} / {key: {"value","status","evidence"}}; unknown keys are ignored. Returns applied keys."""
    return MP.apply(overrides, LANE)


def reset():
    MP.reset(LANE)


def overrides():
    return MP.overrides(LANE)


def load_from_kb(kb):
    """Make the KB's parameter values (its param table; meta 'analyzer_params' for an older build) the current
    ones, replacing the previous KB's."""
    MP.use_kb(kb)


def fact(key):
    """Fact id the coach may cite for a parameter."""
    return "p:" + key
