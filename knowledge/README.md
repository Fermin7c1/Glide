# Glide knowledge layer

The coach's brain. The LLM narrates; this package knows and computes.

```
knowledge/
  README.md           this file: contracts every module follows
  schema.sql          DDL for data/kb.db (one SQLite file, rebuilt by build_kb.py)
  store.py            open kb.db, provenance helpers, small query helpers, BuildContext (the build's paths)
  build_kb.py         orchestrates: glide.db + RestedXP's files + observations + heuristics -> data/kb.db
  sources/            one loader per source (glide.db migration, RXP DB files, recorder observations,
                      map bounds, quest XP, DB2 CSV imports, travel edges, community notes, feedback flags)
  rxp_parser/         complete RestedXP guide parser -> rxp_guide / rxp_step / rxp_action
                      (guide.py, verbs.py, conditions.py, includes.py, loader.py, maps.py, text.py; views.py:
                      RxpSettings and the one rule for which steps a character sees and how RXP numbers them)
  mechanics/          pure functions + parameters: xp.py travel.py combat.py death.py economy.py rested.py
                      nav.py (walking routes) taxi.py, params.py (Forever-overridable parameters with status),
                      fit/ (fit from observations: python -m knowledge.mechanics.fit)
  analyzer/           route/ (per-step metrics, dependencies, justification -> step_brief),
                      alternatives.py (what else could be done here and why RXP didn't),
                      simulate.py (planner over state: skip / grind / dungeon / hold turn-ins); see analyzer/README.md
  heuristics/         rules.json (expert heuristics as conditional rules), engine.py (applicability)
  community/          daily community intel (sources.json, intel/, digests/); see community/README.md
  glossary.json       terms the coach uses consistently
  extras.py           addon extras (training, flight paths, consumables, corpse run) -> facts for the coach
  api/                the query API the coach calls: pack.py (KnowledgePack, a facade over resolve, lookup, panel,
                      extras, facts), navigation.py (walk_route, plan_to_step), tools/ (the coach's tool
                      definitions, exported as Claude tools)
  eval/               questions.jsonl + run_eval.py (source checkout only: the installer doesn't ship it)
```

## Conventions (binding for every module)

- Python 3.12. At runtime the standard library plus `numpy` (`requirements.txt`; the desktop app bundles
  it): only the map and walking-route code (`mechanics/nav.py`, the engine's `geo.py`) uses it, and it
  degrades without it (no terrain routes, area boxes instead of outlines). No other package at runtime.
  Build-time tools may use lupa if present but must degrade without it.
- The knowledge base is `data/kb.db`. `build_kb.py` creates it from scratch each run (write to a temp
  file, then replace: the engine may hold the old one open read-only). Never write to kb.db at
  runtime from the engine; runtime writes go to `data/live/`.
- Every fact row carries provenance: `src` (comma list of source tags, see below), `conf` (0..1),
  and where it matters `verified` (0 = prior from Classic / community, 1 = observed on Forever).
  Source tags: `wdb` (client cache, what the server sent), `obs` (our recorder), `rxp` (RestedXP
  guides or DB files), `fg` (ForeverGuide), `frq` (QuestieDB Forever fork), `frc` (forever-ref),
  `whd`/`whz` (Wowhead seeds), `db2` (client DB2 CSV export), `classic` (known Classic value),
  `note` (our own written note), `fit` (fitted from observations), `inf` (inferred).
- IDs are the game's: quest ID, creature (NPC) ID, gameobject ID, item ID, spell ID, UiMap ID,
  AreaTable ID. Class tokens are upper-case (`WARRIOR`), factions `Alliance`/`Horde`.
- Coordinates: map coordinates are `ui_map` + `x`,`y` in **percent** (0..100), as the addon and the
  rest of the engine use. World coordinates are `continent` (instance id: 0 Eastern Kingdoms,
  1 Kalimdor; Forever adds more) + `wx`,`wy` in yards. Conversion needs `map_bounds` rows
  (world position of the map's (0,0) and (100,100) corners); when none exists, keep both forms
  and leave the missing one NULL. Never guess.
- Times are seconds (REAL), XP are integers, money is copper.
- A loader module exposes `load(conn, ctx) -> dict` (counts/notes) and must be idempotent for a
  fresh database. `ctx` is `store.BuildContext` (paths: root, glide_db, reference, data, live). RestedXP
  files are read through `ctx.rxp()`: `reference/rxp/` when that folder exists (a reference checkout,
  not in the repository), else the installed addon in the WoW folder from `config.json`.
  Loaders never raise on a missing optional source; they return `{"skipped": reason}`.
- Mechanics functions are pure, take explicit arguments, and read parameters from
  `mechanics.params.get(key)`. Every parameter has a Classic default and a `status`:
  `classic`, `forever-verified`, `forever-fitted`, `unverified`. Unknown-on-Forever is a first
  class state, not a guess.
- Tests live in `tests/kb/test_<module>.py`, run with plain `python3`, no pytest dependency,
  print `OK` at the end. They build their inputs from the committed fixture corpus
  (`tests/kb/fixture.py`, `tests/kb/fixtures/`) into a temp folder, never from the player's data.
  Checks against the real `data/glide.db`, `data/kb.db` and installed RestedXP guides are the
  report-only `tests/kb/smoke_<module>.py` (`py -3 tests/run_all.py --smoke`).
- Output for the LLM is JSON-serialisable dicts with short keys and no prose paragraphs; the model
  gets facts and numbers, it writes the sentences.

## Ownership (who edits what; do not edit outside your lane)

| Lane | Files |
|---|---|
| parser | `knowledge/rxp_parser/*`, `tests/kb/test_rxp_parser.py` |
| mechanics | `knowledge/mechanics/*`, `tests/kb/test_mechanics.py`, `knowledge/VERIFY.md` |
| store | `knowledge/store.py`, `knowledge/build_kb.py`, `knowledge/sources/*`, `wowfiles/db2_import.py`, `tests/kb/test_store.py` |
| heuristics | `knowledge/heuristics/*`, `knowledge/glossary.json`, `knowledge/eval/questions.jsonl`, `tests/kb/test_heuristics.py` |
| analyzer | `knowledge/analyzer/*`, `tests/kb/test_analyzer.py` |
| coach | `knowledge/api/*`, `knowledge/eval/run_eval.py`, `companion/coach*.py`, `companion/app.py`, `companion/dashboard.py`, `tests/kb/test_api.py` |
| addon | `addon/Glide/*`, `companion/feedback.py`, `tests/test_addon.py`, `tests/test_link.py` |

`schema.sql` is shared. Adding tables or columns is allowed; renaming or removing is not, and any
addition is documented at the top of the file in the change log.

## Data inputs

In the repository:

- `data/seed/seed.json.gz`: community datasets (ForeverGuide, QuestieDB Forever, forever-ref,
  Wowhead seeds, free RXP guides). `tools/build_seed.py` documents its structure.
- `companion/data/stat_weights.json`: RXP stat weights (already extracted).
- `tests/kb/fixture.py`, `tests/kb/fixtures/`: the committed fixture corpus the tests build from.

On the player's PC only (built or recorded there; `.gitignore` keeps them out of the repository):

- `data/glide.db`: the merged database `tools/build_db.py` writes (quests, entities, spawns, zones, maps,
  map_areas, rxp_guides with the raw guide `text`, rxp_steps, sessions + obs_* tables from the recorder).
  Schema: see `tools/build_db.py`.
- `data/archive/sv/*.lua`: recorder SavedVariables (`GlideRecorderDB`), parse with `knowledge/sources/_lua.py`
  (`tools/svparse.py` is the same reader under its old name).
- `data/archive/wdb/`: client caches (questcache.wdb etc.), decode with `wowfiles/wdb.py` (`tools/wdb.py`).
- `data/live/*.jsonl`: the engine's session logs (core frames, pages, triggers, coach messages).
- `data/db2/*.csv`, `data/gamedata/`: what `tools/extract_maps.py` reads from the client's own files (`wowfiles/`).
- RestedXP's addon files (GuideLoader.lua = the authoritative guide parser; functions.lua = semantics of every
  tag; DB/forever/*.lua = spirit healers, flight paths with times, dangerous mobs with notes and patrol paths,
  spells to train by class/level, rares; Talents.lua; Tips.lua; ItemUpgrades.lua): read from the installed
  addon, or from a local `reference/rxp/` copy when one exists. Personal use only; nothing from it is
  redistributed.

## Known facts about Forever (as of 2026-10-04) that shape the design

- Client is Mainline 12.x based (interface 16001). Health/power/aura values are "secret" in combat.
- XP table 1..9 observed == Classic (400, 900, 1400, 2100, 2800, 3600, 4500, 5400, 6500).
- Kill XP at level 1 matches the Classic formula exactly (Young Wolf L1 -> 50, Kobold Vermin L2 -> 52).
  Observations at level 14 (Riverpaw Outrunner L10 -> 96, L9 -> 68, Hogger L11 elite -> 250) do NOT
  match the plain Classic formula (57/43/144): either rested/Boosted Rest applied or the mob levels
  were mis-recorded. `mechanics/fit/` must settle this from all 334 recorded kills.
- Forever has a camp system: sitting at a campfire grants "Camp Benefits" and "Boosted Rest";
  "Camping 101" quests exist. Effects on XP are unknown.
- Beta server returns no data for quests above ~level 30; launch is 2026-11-04. Partial data is
  the normal state; unknowns must be explicit.
