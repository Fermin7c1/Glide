"""RestedXP (RXP) guide parser -> rxp_guide / rxp_step / rxp_action (see schema.sql).

Grammar follows reference/rxp/GuideLoader.lua (addon.ParseGuide, parseLine, applies); per-verb argument
semantics follow reference/rxp/functions.lua (addon.functions.<verb>, addon.separators). Standard library only.

Line pipeline (same order as RXP):
  1. `--` starts a comment anywhere in a line (RXP strips `%-%-[^\\r\\n]*` from the whole text first).
  2. The line is trimmed; a line whose first four characters are `step` opens a step (`step << cond`).
  3. Before the first step: header lines. `<< cond` alone = guide condition; `#tag value [<< cond]` = header tag.
  4. Inside a step: `<<` (first occurrence, to end of line) = line condition; then `#tag value` = step tag;
     then `>>` (first occurrence) = display text; then `.verb args`; else `+text` (objective), `*text` (note).

Public API
  plain(text)                      colour/icon markup -> plain words
  parse_cond(raw) / and_cond(a, b) `<<` condition -> dict; cond_applies(cond, cls=, race=, faction=, ...)
  parse_guide(text, source=None)   pure: guide text -> dict (see docstring)
  parse_next(raw, own_group=None)  `#next` raw lines -> [{"name","group","cond"}]
  expand_includes(guides)          resolve step-level `#include Group\\Name[@Start-End]` across a corpus
  dungeon_tag(name)                RestedXP's dungeon tag for a guide's spelling ('Stockades' -> 'STOCKS')
  step_flags(step, **player)       effective flags for a player (conditional step tags resolved)
  rxp_index_map(guide, **player)   RXP's step number over the steps whose `step << cond` applies -> our idx
  RxpSettings                      RestedXP's options, one vocabulary (from_profile: RestedXP's names,
                                   from_options: Glide's); defaults are RestedXP's own
  step_shown(flags, settings, gd)  RestedXP's stepLogic: does it show a step (gd: guide_dungeons(guide))
  visible_steps / rxp_numbering    the steps a player gets and RestedXP's live step number -> idx (the one
                                   numbering the live coach and the build's step timing share)
  load(conn, ctx)                  loader: glide.db rxp_guides -> kb.db rxp_guide / rxp_step / rxp_action

Modules: text (Lua character classes, tonumber, markup), maps (zone names <-> uiMapIDs), conditions (`<<`),
verbs (per-verb arguments), guide (the line pipeline), views (player-specific views), includes (`#include`),
loader (glide.db -> kb.db).
"""
from knowledge.rxp_parser.conditions import and_cond, cond_applies, parse_cond
from knowledge.rxp_parser.guide import affix_name, parse_guide, parse_next
from knowledge.rxp_parser.includes import expand_includes
from knowledge.rxp_parser.loader import load
from knowledge.rxp_parser.maps import DUNGEON_ALIASES, DUNGEON_TAGS, ZONE_ALIASES, ZONE_UIMAP, dungeon_tag
from knowledge.rxp_parser.text import lua_num, plain
from knowledge.rxp_parser.verbs import KNOWN_VERBS, PARSED_VERBS
from knowledge.rxp_parser.views import (OPTION_KEYS, PROFILE_KEYS, GuideDungeons, RxpSettings, guide_dungeons,
                                        rxp_index_map, rxp_numbering, step_flags, step_shown, visible_steps)

__all__ = ["and_cond", "cond_applies", "parse_cond", "affix_name", "parse_guide", "parse_next", "expand_includes",
           "load", "ZONE_ALIASES", "ZONE_UIMAP", "lua_num", "plain", "KNOWN_VERBS", "PARSED_VERBS", "rxp_index_map",
           "step_flags", "DUNGEON_ALIASES", "DUNGEON_TAGS", "dungeon_tag", "OPTION_KEYS", "PROFILE_KEYS", "GuideDungeons",
           "RxpSettings", "guide_dungeons", "rxp_numbering", "step_shown", "visible_steps"]
