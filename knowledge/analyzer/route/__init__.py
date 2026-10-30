"""Route analysis: why each RestedXP step exists, what it costs, what it yields, what depends on it.

  Route(kb, guide_key, player)            the applicable steps for one player, with RXP step numbers
  analyze(kb, route, start_level=None, start_xp=0, ...) -> [brief]   one brief per route step (contract below)
  load(conn, ctx)                         build_kb loader: briefs for every guide x class -> step_brief, guide_brief
  why(kb, guide_key, idx, player, live_state=None) -> brief (+ "live" when a live state is given)
  expected_level_at / pending_turnin_xp / turnin_level_gap / step_quest_type / step_xp_per_min /
  brief_for_rxp_step / next_guides      helpers for the coach lane

player = {"class": "WARRIOR", "race": "Human", "faction": "Alliance", "hardcore": False}
         optional RestedXP options (rxp_parser.RxpSettings, unset = RestedXP's own default in every group):
         "dungeons" (names or RXP dungeon tags selected, True / "all" for every one; none), "group_quests"
         (False), "season" (0), "phase" (6), "xprate" (1.0), "ssf" (False). A guide's own .enabledungeon /
         #disabledungeons lines apply on top, as in RestedXP.

Brief contract (README.md): guide_key, idx, rxp_step_no, class, kind, exp_level_before, exp_level_after, xp_now,
xp_unlocked, time_s, travel_s, kill_s, xp_per_min, risk, justification{quests, kills, travel, co_located,
rxp_notes, why, risks}, alternatives, deps{needs, enables, last_chance}, facts.

Modules: steps (Route, the player, the next guides), briefs (the analysis and the guide summary), build (the
build_kb loader), runtime (the coach's cached queries).
"""
from knowledge.analyzer.route.briefs import analyze, build_briefs, guide_summary, public, start_state
from knowledge.analyzer.route.build import (GUIDE_BRIEF_DDL, MODEL_VERSION, STEP_BRIEF_COLS, chained_start,
                                            ensure_schema, fit_params, guide_classes, load)
from knowledge.analyzer.route.runtime import (BRIEF_RXP, BRIEF_RXP_META, brief_for_rxp_step, brief_settings,
                                              briefs_for, clear_caches, expected_level_at, get_route, guide_start,
                                              live_compare, pending_turnin_xp, state_fields, step_quest_type,
                                              step_xp_per_min, stored_brief, stored_matches, turnin_level_gap, why)
from knowledge.analyzer.route.steps import (ALT_KINDS, DEFAULT_RACE, GOTO_VERBS, OBJ_VERBS, OTHER_WORK, Route,
                                            next_guides, norm_player, settings_key, step_shown)
from knowledge.vocab import FACTION_CLASSES, FACTION_RACES, RACE_CLASSES

__all__ = ["analyze", "build_briefs", "guide_summary", "public", "start_state", "GUIDE_BRIEF_DDL", "MODEL_VERSION",
           "STEP_BRIEF_COLS", "chained_start", "ensure_schema", "fit_params", "guide_classes", "load", "BRIEF_RXP",
           "BRIEF_RXP_META", "brief_for_rxp_step", "brief_settings", "briefs_for", "clear_caches", "expected_level_at",
           "get_route", "guide_start", "live_compare", "pending_turnin_xp", "state_fields", "step_quest_type",
           "step_xp_per_min", "stored_brief", "stored_matches", "turnin_level_gap", "why", "ALT_KINDS", "DEFAULT_RACE",
           "FACTION_CLASSES", "FACTION_RACES", "GOTO_VERBS", "OBJ_VERBS", "OTHER_WORK", "RACE_CLASSES", "Route",
           "next_guides", "norm_player", "settings_key", "step_shown"]
