# knowledge/analyzer — why each RestedXP step exists, what else could be done, what if

The reasoning core of the Glide knowledge layer. It walks a RestedXP (RXP) guide with an explicit cost/XP
model built on `knowledge.mechanics` and answers, per step: what it costs, what it yields, why it is there,
what depends on it, what the alternatives were, and what changes if the player deviates.

```
params.py        analyzer.* parameters (value, unit, status, evidence); mechanics keys pass through
kbdata.py        cached read-only KB access (store.KB or a sqlite3 connection), spatial grids, geometry,
                 observed travel graph (travel_edge Dijkstra), flight nodes, graveyards
model.py         the walker: State + Walker (gates, travel, objectives, kills, grind, hearth, flights, deathskips)
route/           Route (applicable steps + RXP numbering), analyze() -> briefs, load() for build_kb, why() and
                 the coach helpers (build.py, steps.py, briefs.py, runtime.py; `knowledge.analyzer.route` exports
                 them all)
alternatives.py  quests / grind spots / dungeons near a point and why the route does not take them
simulate.py      run / compare / eta over a state with variants (skip, grind, hold, leave zone, dungeon)
review.py        recorded play (obs_step_time) vs the model; per-class time calibration
```

Standard library only. Everything returns JSON-serialisable dicts of facts and numbers; the LLM writes the
sentences.

## Coach API (import `knowledge.analyzer.route as R`)

| call | returns |
|---|---|
| `R.why(kb, guide_key, idx, player, live_state=None)` | the brief (stored one when class and race match, else computed); with `live_state={"level","xp"[,"quests_complete"]}` adds `"live"`: level delta vs plan, per-quest XP now vs planned, plain notes ("you are 2 levels above the plan: X now gives Y XP instead of Z") |
| `R.brief_for_rxp_step(kb, guide_key, rxp_step_no, player)` | same, from RXP's live step number (`core.st`) |
| `R.state_fields(kb, guide_key, idx, player, level=, quests_complete=, xp_per_min=)` | heuristics STATE_FIELDS: `step_quest_type`, `step_quest_level_gap`, `step_xp_per_min`, `step_xp_ratio`, `step_flags`, `pending_turnin_xp`, `turnin_level_gap` |
| `R.expected_level_at(kb, guide_key, idx, cls)` | planned fractional level when the step starts |
| `R.step_xp_per_min(kb, guide_key, idx, cls)` | modelled XP/min of the step |
| `R.pending_turnin_xp(kb, guide_key, idx, quest_ids_complete, level)` | int: XP of those turn-ins at `level` |
| `R.turnin_level_gap(kb, quest_ids_complete, level)` | max(level - quest level) |
| `R.step_quest_type(kb, guide_key, idx, player_or_class)` | normal/elite/group/dungeon/class/... |
| `R.briefs_for(kb, guide_key, player)` / `R.Route(...)` + `R.analyze(...)` | all briefs of a guide |
| `alternatives.alternatives_near(kb, ui_map, x, y, level, player, exclude_quests=(), radius_yd=600, done=None, routed_later=None)` | quests, grind spots, dungeons with `why_not` |
| `simulate.compare(kb, route_or_(key, player), state, start_idx, variant)` | baseline vs variant + delta |
| `simulate.eta(kb, route, state, start_idx, target_level)` | time to a level (across next guides) |
| `review.review_session(kb, session_id)` | recorded session vs model: time sinks, deaths, XP/hour |

`player = {"class": "WARRIOR", "race": "Human", "faction": "Alliance", "hardcore": False}`; optional RXP
settings that change which steps exist and how RXP numbers them: `ssf` (solo self-found), `group_quests`,
`dungeons` (set of RXP codes, or "all"), `season` (0), `phase` (6), `xprate` (1.0). Defaults are RXP's, except
that Dungeon Guide groups default to all dungeons and group quests on. `kb` is a `store.KB`, a path or a
sqlite3 connection.

## The brief (contract; one per route step and class)

```
{"guide_key", "idx", "rxp_step_no", "class",
 "kind": turnin|accept|complete|collect|grind|travel|hs|fly|deathskip|train|vendor|dungeon|other,
 "exp_level_before", "exp_level_after",      fractional levels (7.59 = level 7, 59% of the bar)
 "xp_now",          XP earned in this step (turn-ins + objective kills + grind)
 "xp_unlocked",     XP this step makes possible later: turn-in XP of quests accepted here + of follow-ups
                    unlocked by the turn-ins here that the route takes later
 "time_s", "travel_s", "kill_s",   seconds (time = travel + kills + interactions, x analyzer.calib.<CLASS>)
 "xp_per_min", "risk" (0..1 heuristic),
 "justification": {
   "quests": {"<qid>": {"name","level","type","xp_full","xp_at_level","role","chain_next","unlocks",
                        "unlocks_xp","turnin_step","turnin_guide","accept_step","level_gap","decay_at_level"}},
   "kills": [{"creature_id","name","level","rank","count","xp_each","ttk_s"} (+ "extra" incidental kills,
             "xp", "purpose": objective|grind|extra_grind)],
   "travel": {"from_idx","dist_yd","time_s","detour_yd"} (+ "mode": flight|hearth|death|boat|tram,
             "observed": true when the time comes from recorded travel_edge paths, "flight": {from,to,time_s}),
   "co_located": [{"idx","kind","quest_id","name","dist_yd"}]     other route steps with quest work <= 80 yd
   "rxp_notes": [...]       the step's -- comments and its >> lines with numbers or warnings (<= 4)
   "why": [reason codes], "risks": [{"creature_id","name","level","note"} (+ "dist_yd", "elite")]},
 "alternatives": [{"kind","id","name","level","xp","xp_per_min_est","dist_yd","why_not"}]  (turnin/accept/grind
                  steps; <= 3: best quests first, then the best grind spot / dungeon),
 "deps": {"needs": [{"quest","from_idx"}], "enables": [{"quest","idx"}], "last_chance": bool},
 "facts": ["q:<quest>", "c:<creature>", "i:<item>", "p:<param key>", "poi:flight:<id>", "poi:graveyard:<id>"]}
```

`role` is `accept`, `turnin`, `objective`, `gate` (a runtime check such as isOnQuest) or a `+` join.
`xp_at_level` is the XP the quest gives at the step's expected level (for a turn-in: what it gave).
`decay_at_level` = first player level where the quest gives less than full XP (quest level + 6 in Classic).
`turnin_guide` = the next guide that turns it in when this guide does not. `needs.from_idx` = the accept step
(or the prerequisite's turn-in step); `enables.idx` = the later turn-in of a quest accepted here, or the accept
of a follow-up unlocked here. `last_chance` = last step with quest work on this map before the route leaves it.

### Reason codes (`why`)

| code | meaning |
|---|---|
| `hub_turnin` | 2+ turn-ins here, or a turn-in with 2+ other quest givers/enders within 80 yd |
| `stacks_objectives` | objectives of 2+ quests done in the same place |
| `enables_chain` | a quest turned in or accepted here unlocks a follow-up the route takes later |
| `on_the_way` | between two other spots: visiting it adds < 15% (or < 40 yd) to a previous -> next leg of >= 100 yd |
| `level_gate:<L>` | an `.xp`/`.maxlevel` gate at L, or a quest whose minimum level L was just reached |
| `xp_gate:<L>` | the step grinds until an `.xp` target in level L |
| `grind_filler` | the `.xp` target needed actual grinding in the model |
| `hearth_cycle` | hearthstone use or bind |
| `flight_unlock` | discovers a flight path |
| `class_quest` | class quest or class-restricted step |
| `training_stop` | trainer visit |
| `vendor_supplies` | vendor / purchase |
| `deathskip_travel` | intentional death to respawn at a graveyard near the next step |
| `dungeon_run` | instance objectives (covered by the dungeon run lump) |
| `optional` | RXP `#optional` |
| `last_chance_before_zone_change` | see `deps.last_chance` |
| `skipped:<reason>` | the step did not apply at runtime in the model: `already_done`, `not_on_quest`, `on_quest`, `quest_not_complete`, `quest_complete`, `prereq_not_turned_in`, `already_turned_in`, `xp_gate`, `above_maxlevel`, `hearth_cooldown`, `zoneskip`, `subzoneskip`, `requires:<label>`, `variant_skip` |

### Alternatives `why_not` codes

`far_objectives` (farthest objective > 900 yd from the giver), `low_drop_rate` (< 20%), `elite`, `group`,
`long_chain` (> 4 follow-ups), `out_of_level` (quest > level+2, or so low it gives reduced XP),
`class_restricted`, `low_xp_per_min` (< 80% of the reference pace: the step's own XP/min when given, else the
heuristics benchmark for the level), `routed_later:<guide_key>/<idx>`, `unknown_objective_location`.
Grind spots: rank-0 hostile creatures within the radius whose level is in [gray+1, level+2], at least 4 spawns;
XP/min = kill XP / (ttk + downtime + density-based search time). Dungeons: Classic level ranges for the RXP
`dungeonList` codes (ratings from `rxp_meta.dungeonStats`) plus the Dungeon Guide group's dungeon guides; `group`
always applies.

## The model

**Route.** A step belongs to the route when its `step << cond` applies to the class/race/faction
(`rxp_parser.cond_applies`, never-steps dropped) and RXP's stepLogic shows it (season 0, phase 6, xprate 1,
not a hardcore server, `#hardcore`/`#softcore`, `#ssf`/`#ah`, `#group`/`#solo`, `#dungeon`/`#dungeonskip`,
`#som`, `#beta`, `#questguide`). RXP Forever leaves hidden steps out of the guide, so `rxp_step_no` counts
only route steps (the store lane verified this against the addon's step pages). Actions are filtered by their
own `<<` condition. Position = first applicable `.goto`; the walked path = the step's arrive/path/waypoint
gotos (only the first for `#loop` steps and multi-point farming steps).

**Runtime gates** are evaluated against the modelled state, so RXP's branches resolve as in game:
isOnQuest / isNotOnQuest / isQuestComplete / isQuestNotComplete / isQuestTurnedIn / isQuestAvailable, `.xp`
with a skip (met == not reverse), `.maxlevel`, the hearthstone `.cooldown`, `.zoneskip`, `.subzoneskip`
(area boxes), `#requires <label>`. A step whose quest work is all done is skipped (`already_done`).
Money, skill, item counts, auras and gossip are assumed to pass.

**XP.** Turn-ins: `mechanics.xp.quest_xp(full, quest level, level at that moment)`. Kills: the mean of
`mechanics.xp.kill_xp` over the creature's level range (elite x2 per mechanics), applied kill by kill across
level-ups (the rested pool, when the state has one, doubles kills until used). Objective kills = amount; collect
objectives = amount / drop rate; incidental extra kills = 20% of objective kills (same mob). `.xp` targets
(no skip flag) grind exactly the missing XP on the step's `.mob`s (else the last mob type nearby, else the best
grind spot within 450 yd). Exploration/discovery XP and Well Fed are not modelled.

**Objective attribution.** Each (quest, objective) the route lists is paid once, at its last applicable listing
before the quest's turn-in (RXP's loop/farm step; the earlier `#completewith`/`#sticky` listings are
background). When gates hide a designated step the walk is repeated with the next candidate (<= 3 passes).
Listings that all come before the accept are pre-collection and pay at the last of them. Objectives the
route never lists (deliveries, provided items) cost nothing. Sources: quest_objective (kill target / item),
drop_from, creature_drop (preferring the step's mobs, then spawns on the map), object_loot (clicks), vendor
(the step says "Buy"), friendly NPC "drops" = an interaction. Instance-only elite targets in dungeon guides
are covered by one dungeon run lump (`analyzer.dungeon.*`).

**Time.** travel + kills + interactions, times `analyzer.calib.<CLASS>`.
Travel per leg: an observed path over the `travel_edge` 1%-cell graph when both endpoints are within one cell
of it (Dijkstra; ignored when longer than 2.5x the straight line), else `mechanics.travel.run_time_s` (mounted
from `mount.level`) x `analyzer.travel.detour` (applied while mechanics `travel.detour_factor` is unfitted);
other maps of the continent via world coordinates (map_bounds); Ironforge <-> Stormwind by tram; other
continents a fixed leg. Flights: RXP flight times between the nearest node and the named node (nearest to the
next step), else taxi speed. Hearth: cast + load to the bound inn (`.home`), or the inn of the place named in
"Hearth to X"; skipped while on cooldown. Deathskip: time to die + `mechanics.death.death_cost_s` (spirit
res), respawning at the graveyard near the next step. Kills: `combat.time_to_kill_s` + `combat.downtime_s` +
`analyzer.kill.search_s` per kill. Interactions: the `analyzer.time.*` parameters; "wait N minutes" in the
step text is honoured.

**Start state and chaining.** `analyze()` starts at the guide's `level_min` with 0 XP unless given a level or
state. `load()` processes guides in level order and starts each from its predecessor's modelled end (same
group preferred), never below the guide's nominal start (`guide_brief.notes.start_from` says which).
`briefs_for()` / `why()` reuse that start.

**Calibration.** `review.calibration()` compares recorded step visits with the model over step windows (time
logged on a step covers the background work and auto-skipped steps since the previous visit), takes
sum(actual)/sum(model) per class and shrinks it toward 1 with a prior worth `analyzer.calib.prior_h` hours:
`factor = (h*ratio + prior_h) / (h + prior_h)`, clipped to 0.6..1.6. `load()` fits it before computing the
briefs; `analyzer.kill.search_s` (median kill gap - ttk - downtime over recorded kills) and
`analyzer.drop.default_rate` (median best known quest-item drop rate) are fitted the same way. Fitted values
go to meta `analyzer_params` (applied at runtime by `params.load_from_kb`) and every analyzer parameter is
written to the `param` table.

## Simulation variants (`simulate.run/compare`)

`{"skip_quest": id}` (plus every follow-up that becomes unreachable, in this and the next guide: next_quest and
`pre` prerequisites; a `pregroup` only when all its alternatives are lost), `{"skip_step": idx | [idx]}`,
`{"grind_here": {"idx", "minutes" | "to_level"}}`, `{"hold_turnin": {"quest", "until_level"}}` (the trip back
to the ender is added when it is released; released at the end if the level is never reached),
`{"leave_zone_at": idx}` (both runs continue through the next guide), `{"do_dungeon": guide_key}` (the dungeon
guide runs before start_idx with dungeons and group quests on, plus the run lump). `compare` returns the end
state of both and `delta = {time_s, end_level, xp, time_s_equal_xp (variant time + grinding the XP shortfall at
the best spot near the end), lost_quests, gained, changed (same quest, different XP), notes}`.

## Storage (data/kb.db)

`step_brief` (guide_key, idx, class): exp_level (= before), exp_level_after, xp_now, xp_unlocked, time_s,
travel_s, kill_s, xp_per_min, risk, kind, rxp_step_no, race, justification/alternatives/deps/facts (json, the
contract above), computed (json `{t0: seconds from the guide start, xp: [quests, kills, grind], skip}`).
`guide_brief` (guide_key, class): start_level, end_level, time_s, xp_total/xp_quests/xp_kills/xp_grind, hubs
(json: up to 6 turn-in hubs), notes (json: skipped steps by reason, race, start_from, carried-in turn-ins,
discovery steps, grinds over one level). Meta `analyzer` = the build report (counts, unknowns, fitted values,
calibration). Briefs are computed for every guide x every class of its faction for which the guide's own
`<<` condition holds (Hunter-only / Mage-AoE guides get only that class), with the guide's `#defaultfor` race
when it fits the class (Skyborne for Zephras Isle), else the faction default.
`R.stored_brief()` reads a row back as a brief.

## Parameters (`params.all()`; status as in mechanics)

Interaction costs `analyzer.time.*` (accept/turn-in 6 s, train/vendor 20 s, buy 8 s, home 8 s, fp 6 s,
gossip 3 s, use 3 s, object click 5 s + 12 s search, explore 5 s, event/escort 120 s, other objective 20 s,
unknown objective 180 s, text waits capped at 300 s), `analyzer.hs.load_s` 8, `analyzer.fly.overhead_s` 15,
`analyzer.death.time_to_die_s` 20, `analyzer.kill.search_s` (fitted), `analyzer.kill.incidental_frac` 0.2,
`analyzer.drop.default_rate` (fitted), `analyzer.drop.min_rate` 0.05, `analyzer.grind.radius_yd` 450,
`analyzer.travel.*` (detour 1.25, edge_max_ratio 2.5, cross_continent 420 s, tram 200 s, unknown leg 60 s,
instance leg 30 s), `analyzer.dungeon.run_s` 2700 / `kill_xp_per_level` 1200, spatial radii (co-located 80,
risk 150, alternatives 600 yd), alternative thresholds, `analyzer.calib.<CLASS>` and `analyzer.calib.prior_h`.
All are `unverified` priors except the fitted ones (`forever-fitted`) and the contract radii (`classic`).

## Limitations

- Static modelling of a dynamic guide: gates that depend on money, skills, items, auras or reputation pass;
  `.xp` targets are met by grinding exactly the missing XP; one player path, no deaths (except deathskips).
- Quest-item drop rates are mostly unknown (default rate, counted in meta `analyzer.unknowns`); objective
  locations for alternatives come from spawns or quest_poi; a few objective sources and mob names are unknown.
- Above level ~40 the modelled quest XP per guide is well below what RXP's level ranges imply, so guides start
  at their nominal level (the clamp in chaining) and RXP's `.xp` targets show long grinds
  (`guide_brief.notes.grind_over_one_level`). Use `why()` / `simulate` with the live level for real advice.
- Travel is straight-line x detour outside recorded areas (no terrain, no swimming, no mounts below
  `mount.level`); boats/zeppelins are one fixed leg; Deeprun Tram is the only modelled transit.
- Discovery XP, Well Fed, rested XP in stored briefs (start pool 0) and group XP are not modelled.
- Dungeon runs are a lump (time and kill XP per level); dungeon quest objectives inside instances cost nothing
  on top. Class-specific DPS/downtime come from mechanics priors (only WARRIOR/ROGUE ttk scales are fitted).
- Risk is a heuristic score from RXP dangerous-mob notes, hostile elites within 150 yd and higher-level kills.
- Storage and time: 92,549 briefs (160 guides with steps, 1,034 guide x class routes) take ~135 MB of kb.db
  (the JSON is not deduplicated across classes, ~56% of rows repeat another class's justification); load()
  takes ~38 s inside build_kb. why() for a class/race not stored recomputes the guide (~0.25 s, then cached).
