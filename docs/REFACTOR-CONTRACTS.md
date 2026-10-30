# Refactor contracts

The refactor (branch `refactor/cohesion`) may restructure anything inside a part of Glide. It may not change what
crosses between parts, or what a player sees, without a deliberate decision recorded here. The golden masters in
`tests/golden/` (`python tests/golden/golden.py check`) catch behaviour changes; this file names the interfaces
they don't spell out.

## The parts

| Part | Code | Language |
|---|---|---|
| Addon | `addon/Glide/` | Lua 5.1 (WoW API) |
| Engine | `companion/` (minus `static/`) | Python 3.12 |
| Dashboard | `companion/static/` | HTML, CSS, browser JS |
| Knowledge and tools | `knowledge/`, `tools/`, `wowfiles/` | Python 3.12 |
| Desktop app | `desktop/src/` | TypeScript (Electron) |

## Interfaces between parts (frozen)

1. **The strip** (addon → engine). The pixel grid, its markers (magenta, then yellow for the binary strip, cyan
   for the old 6-bit one), the framing `[version][seq][len][payload][fletcher16]`, the payload `core|page` with
   `key=value;` pairs, every core key and page id (`pg=B`, `Z`, `G`, `S0..2`, `Q*`, `A*`, `W*`, `I*`, `R*`, ...),
   the ring events `id:kind:arg`, and the gauges under the grid. The full list is `docs/STRIP.md`, generated from
   `companion/strip_spec.py`: that is this interface's definition. `companion/protocol.py` and
   `addon/Glide/Codec.lua` must stay twins (framing, Fletcher-16, symbols; State.lua `ns.Hash` and
   `protocol.list_hash`; Coach.lua's flag bits and the registry's). Pinned by `tests/test_link.py`,
   `tests/test_strip_spec.py` and the addon and engine golden masters.
2. **Engine HTTP** (engine → dashboard and desktop app), on `127.0.0.1`:
   `/`, `/events` (SSE: `init`, `state`, `message`, `speak`), `/api/state`, `/ask`, `/settings`, `/models`,
   `/memory`, `/reminders`, `/layout`, `/health`, `/refresh`, `/calibrate`, `/mute`, `/xph`,
   `/fonts/<name>.woff2`, `/map/<uiMapID>.png`, `/icon/<spellID>.png`. Request and response shapes stay as they are.
3. **The dashboard state** (the `state` event and `/api/state`): every key the dashboard or the desktop app reads
   (`companion/static/dashboard.js`; in the app `desktop/src/renderer/session-bar.ts`, `overlay.ts`, `activity.ts`,
   `memory.ts`, `status.ts`, `settings/walk-map.ts` and `coach.ts`): `models`, `coach`, `refresh`, `navbuild`,
   `reminders`, `why`, `pace`, `steps`, `waypoint`, `walk`, `buffs`, `quests`, `addon`, `overlay_step`, ... The
   shapes are typed in `desktop/src/shared/engine.ts` (`EngineState`, `CoachMessage`, `SpeakEvent`), which both UIs
   are checked against. Keys can be added, not renamed or removed. Messages keep `kind`, `text`, `speak`, `urgent`,
   `url`, `model`, `facts`, `facts_unknown`, `web_sources`, `t`.
4. **Desktop IPC** (main ↔ renderer, `desktop/src/main/preload.ts`): the channel names (`engine:*`, `config:*`,
   `keys:*`, `speech:*`, `tts:*`, `update:*`, `addon:*`, `overlay:*`, `app:*`, `res:*`, `shell:open`,
   `dialog:folder`, `data:import`, `endpoint:test`) and their arguments.
5. **Files on the player's disk**, read across versions: `config.json` (engine settings; keys as in
   `companion/app.py` `DEFAULTS` and `desktop/src/renderer/settings-schema.ts`), `app-settings.json` (desktop),
   `secrets.json` (DPAPI-encrypted keys), `data/live/*` (logs, layout, reminders, memory, xph history),
   `data/gamedata/*` (manifest, maps, spell_icons.json, icons/), `GlideLinkDB` and `GlideRecorderDB`
   (addon SavedVariables). New fields are fine; existing ones keep their meaning, and old files must still load.
   **Who writes `config.json`** (R20): the engine owns it. In Python it is read and written only through
   `companion/glideconfig.py` (the engine, `knowledge/store.py`, `knowledge/eval/run_eval.py` and the tools
   `build_db.py`, `build_vendor_treasures.py`, `extract_maps.py`); a write re-reads the file, merges, and replaces it
   atomically, one writer at a time. While the engine runs, the desktop app saves through it:
   `POST /settings {"save": <patch>}` (the same patch `config:set` takes; `null` removes a key), and reads the file
   (`readConfig()` / `companionSection()`, read-only). The app writes `config.json` itself only when no engine is
   running: the WoW folder found at the first start (`findWowOnce`, before the engine starts), `config:set` while
   the coach is stopped or starting (Settings, the WoW folder on the Addon page, custom endpoints), and
   `data:import`, which copies another folder's `config.json` over this one. `companion.overlay.{enabled,x,y,width,alpha,
   font_size}` belong to the standalone Python overlay (`companion/overlay.py`, run without the app); the app's
   overlay uses `app-settings.json` `overlay.{opacity,fontSize,...}` and reads only `companion.overlay.drag_key`
   from `config.json` (both overlays use it).
6. **The databases**: `data/glide.db` and `data/kb.db` table and column names (51 kb tables) as read by the engine
   and written by `tools/` and `knowledge/build_kb.py`. A schema change means a rebuild for every player, so it is
   out of scope for the refactor. With a kb.db, glide.db is the knowledge build's input: at runtime the engine reads
   kb.db (through `knowledge.api`), and glide.db only when there is no knowledge pack and in the simulator (`--sim`).
7. **Command lines**: `companion/app.py` flags (`--app`, `--sim`, `--port`, `--no-voice`, ...), `tools/*.py`
   stages and flags, the addon's slash commands (`/glide ...`, `/coach ...`, `/glidelink ...`) and key bindings.
8. **What the player sees**: coach wording, dashboard and app texts, addon texts. Unchanged unless a step is
   explicitly about wording (the golden masters will flag every changed string).

## Rules for the refactor

- Each step keeps `python tests/run_all.py` (API keys blanked) and `python tests/golden/golden.py check` green, and
  builds the desktop app (`npm run build` in `desktop/`). One concern per commit.
- A golden difference is either a bug in the step (fix it) or an intended change (record it here first, then
  re-record that probe: `golden.py record <probe>`).
- No new runtime dependencies. No behaviour, performance or data-format "improvements" smuggled into a structural
  change: those are separate, explicit commits.
- Match the language's established idioms; keep comment density and voice as they are (comments say why).

## Recorded changes

- **R1, knowledge golden** (`knowledge` probe re-recorded): stored step briefs are built with RestedXP's SSF and
  group quests off (meta `step_brief_rxp`); a player with other RestedXP options now gets a brief computed for
  their own route instead of the stored one, and every route and knowledge-pack cache keys on those options. The
  probe's players use the default options (SSF and group quests on), so 15 of its 117 samples show the planned
  values (expected level, XP per minute, time) of their own route. No resolved step or error changed. The KB build
  now also writes meta `step_brief_rxp` (no table or column change); an older kb.db without it counts as built
  with the same options.
- **R3, addon 0.15.1: list pages leave room for their header.** Extras.lua chunked list pages for an
  `n=;h=;<key>=` header only; the training list (page P) also carries `pl=;k=;u=`, so from about 15 missing spells
  its pages were cut, never matched their hash, and the companion never took the training list. Chunks are now
  sized for the real header (the wire format is unchanged; a list may spread over one more page). Pages G, S0..2
  and K now fit their last field to the room left instead of being cut by RefreshPages (G and S: the same text the
  cut gave, give or take a byte; K: whole skill ranks only). `tests/test_page_sizes.py` checks every page at its
  longest. Addon golden re-recorded: only the version in the payload (`gv=0.15.1`) and the strip paint it changes.
- **R9, desktop probe**: `main/store.js` exports `readJsonFile` (a JSON file's object, and why it couldn't be read).
  The app no longer saves over a `config.json` it can't parse (it keeps a `config.json.<time>.bad` copy and shows
  why); the engine starts on its defaults instead of stopping with a traceback. Old configs load as before.
- **R7, desktop probe (Settings texts and defaults, contract 8)**: "Wait before briefing" shows 6 s, the engine's own
  default (it showed 2.5; the engine always waited 6). "Strip columns" / "Strip rows" ("Must match the addon") are
  gone from Settings > Game capture: the addon has no matching setting. The engine still reads `companion.grid`
  from an old config.json (contract 5). Save now sends `video_tips` to the running coach like the other toggles.
- **R4, contract 2 (engine HTTP): a Host check and a per-run token.** Every request must carry
  `Host: 127.0.0.1:<port>` or `localhost:<port>` (else 403), so a rebound DNS name can't reach the engine. Every
  POST, `GET /api/state` and `GET /memory` must also carry `X-Glide-Token: <token>` (else 403), so a web page open
  in a browser can't drive the coach (a cross-site form or no-cors POST can't send that header). The desktop app
  makes a new token per engine start and passes it in the env var `GLIDE_TOKEN`; run on its own, the engine makes
  one. The engine puts it in the page it serves (`<meta name="glide-token">`), so the dashboard sends it too.
  `serve(..., token=None)` (the tests) asks for none. Contract 4, additive: `engine:get` and `engine:state` carry
  `token`.
- **R6, contract 8 (what the player hears): "Glide voice ready."** With voice output "app" (the default) it is now
  said when the app's windows connect (about 1.5 s after the first event-stream client), honouring the `announce`
  setting; before, it was published before anyone listened and never heard.
- **R6, contract 8 (dashboard text): a question the engine didn't take** shows "Problem: The coach didn't get that
  question; ask again." in the chat instead of failing silently.
- **R14, documentation only (contract 3)**: the state's readers in the desktop app are `session-bar.ts`,
  `overlay.ts`, `activity.ts`, `memory.ts`, `status.ts`, `settings/walk-map.ts` and `coach.ts` (not `app.ts`), and
  the dashboard also reads the messages' `facts_unknown` and `web_sources`; contract 3 now says so. Nothing the
  engine sends changed. The stream is typed in `desktop/src/shared/engine.ts`; `dashboard.js` is checked against it
  with `// @ts-check` (`desktop/tsconfig.dashboard.json`, run by `npm run build`; JSDoc only, still no build step),
  and `tests/test_engine_contract.py` checks the types against `tests/golden/fixtures/dashboard_init.json`.
- **R15, no interface change (contract 4)**: the IPC channels, their arguments and events are typed once in
  `desktop/src/shared/ipc.ts` (type-only) and used by `main/app/ipc.ts`, `main/preload.ts` and the renderer; both
  tsconfigs now compile `src` into `dist` (output paths unchanged: `dist/main`, `dist/renderer`, plus a type-only
  `dist/shared` nothing loads). `tests/test_ipc_contract.py` checks the map, the preload lists and the handlers agree.
- **R12, contract 1 written down.** `docs/STRIP.md` (generated from `companion/strip_spec.py`) now lists every core
  key, page and page key, ring event kind and coach flag bit, with its type, meaning, the addon module that sets it
  and the version that brought it; it is the definition of contract 1. `tests/test_strip_spec.py` checks that the
  real addon sends only registered keys and that the engine and `knowledge/sources/live_logs.py` read only
  registered ones. "Twins" in contract 1 no longer covers the old 6-bit colour functions: `ns.SymbolColor`,
  `ns.LEVELS` and `ns.Fletcher16` are gone from Codec.lua (only the deleted `addon/_old` used them); the engine
  still reads the old cyan strip. The list hash lives once on the engine side (`protocol.list_hash`). No wire change.
- **R12 follow-up, addon 0.15.2: gear entries fit their page.** A worn, bag or reward entry with many stats could
  outgrow its page, get cut and keep the whole list from ever being taken. An entry now keeps the stats that fit,
  dropping the least useful first (school-only spell damage, defensive ratings, regen, spell hit/crit; weapon dps and
  speed, armor and the main stats stay); vendor treasure entries are fitted to page D the same way instead of being
  left out. Same format; ordinary items are unchanged. Addon golden re-recorded: only the version in the payload
  (`gv=0.15.2`) and the strip paint it changes.
- **R38, addon 0.15.2: luacheck fixes.** The recorder's flight-master scan now keeps the y coordinate of each node
  (it was always nil); the rest are unused locals. Addon golden re-recorded: only the version in the payload
  (`gv=0.15.2`), the window's version line and the strip paint change.
- **R35, desktop golden: app/ modules and the IPC channels (contract 4) pinned.** The probe now walks dist/main
  recursively (app/*.js was unpinned; only modules that still have a .ts source count, since tsc leaves stale
  output behind), and records every channel app/ipc.js registers and every event the main process sends. The probe
  fails unless they equal preload.ts's INVOKE and EVENTS. Re-recorded: new keys only (`main/app/*`, `ipc`).
- **R35, knowledge golden: other RestedXP settings sampled.** Every third moment is answered again with self-found
  off and with group quests off (one setting flipped from the default each); kb.db is opened read-only and a
  missing one is reported as skipped. Re-recorded: the old records are unchanged, the new ones are appended.
- **R35, engine golden: two sections.** `sim` is the old record list; the simulator now draws the binary strip the
  addon draws and sends a core that starts like the addon's (`gv`, `gk=0`, `cf=255`; the probe pins gv to 0.15.0),
  so the only change in it is `summary.addon_version` (was null). `addon` replays the addon golden's payloads
  through GameState and records the ring events and summary of each frame. Re-recorded.
- **R35, golden tiers.** Each probe declares `TIER`: addon, desktop and dashboard are "portable" (repository only),
  engine, knowledge and parser are "machine" (this PC's data). `golden.py check --portable` runs the first tier.
  The dashboard probe's headless Edge takes a free debugging port (it used fixed 9351) and waits until the page
  reads the same twice instead of a fixed 4 s.
- **R16, contract 5 (config.json): settings out of range are clamped.** Keys are unchanged. Every engine setting's
  default, range, kind and "applies live" flag now lives once in `companion/settings_manifest.json` (read by
  `settings.py`, app.py DEFAULTS, tuning.py and, via the generated `settings-manifest.ts`, the desktop Settings page).
  A hand-edited value outside its range now reads as the nearest end of it (`lookahead_steps: 500` -> 80, as the
  dashboard already showed), a value of the wrong kind as the default; `load_config` clamps what config.json sets.
  In-range configs behave as before (all goldens "same"). Small default alignments for configs built without
  app.py DEFAULTS (tests, evals): `min_interval` falls back to 20 (the coach's own fallback said 10), `timeout` to 25
  (llm_client said 40); DEFAULTS `gear.spec` is "" instead of None (same meaning: no spec). The dashboard's input
  price for ids without a catalog price now comes from models.py's one Anthropic table (claude-opus-4-0: $15/MTok;
  tuning.py's copy said $5); run_eval.py uses the same table.
- **R16 follow-up, desktop probe (main-process exports)**: `main/store.js` exports `keepBadCopy`,
  `unreadableMessage` and `appSettingsProblem`; `main/secrets.js` exports `secretsProblem`. An unreadable
  `app-settings.json` or `secrets.json` (contract 5) is no longer saved over: the app runs on its defaults (no saved
  keys) for that run, keeps a `<file>.<time>.bad` copy, logs why, and a settings or key change shows the reason
  instead of replacing the file (before, the next save wrote the defaults plus the change, losing every saved key).
- **R21, contract 2 (no shape change): `POST /mute` is for the engine's own voice only.** Mute lives in the app
  (`app-settings.json` `muted`). With voice output "app" (the default) the window's narrator drops the coach's lines
  while muted and the app no longer posts `/mute`; with output "windows" (the engine's speech process) it posts
  `/mute` as before, at every coach start too. The engine keeps mute per run in its voice (`Voice.set_muted` /
  `HubVoice.set_muted`, under the voice's lock; muting interrupts the line being spoken). A change of voice output
  takes a coach restart, as before.
- **R19, contract 8 (Status page and addon wording): one owner per check.** Run by the desktop app (`--app`), the
  engine's `/health` no longer reports the install (the addon installed or left over, "WoW folder not set",
  "RestedXP not found" / "RestedXP <version>"): the app's own checks (`addon_installed`, `addon_old`, `wow_dir`,
  `rxp_app`) cover them, so the page stops showing two items for one problem. The engine keeps only what it alone
  sees: the game still running another addon version, now titled "Restart WoW to load Glide <version>" (detail
  "The game is running Glide X, expected Y. Fully exit and restart WoW (a /reload doesn't load new addon files).",
  no button). The addon status (dashboard banner, the coach's `addon_outdated` callout) says under the app "install
  the update on the Glide app's Addon page, then fully restart WoW" / "install the addon on the Glide app's Addon
  page (it removes them), then fully restart WoW" instead of "copy addon\Glide from the project ..." / "remove the
  ... folder from Interface\AddOns ...", and "the app has" instead of "the project has". Contract 3, additive: the
  state's `addon` carries `kind` ("old", "installed", "running" or null). Run on its own the engine keeps every
  check and the developer wording.
- **R24, contract 2 (engine HTTP): one route table, and the token on two side-effecting GETs.** The engine's
  routes are one table (`dashboard.serve`'s page, `/events`, `/api/state`, `/layout` and static files, plus
  `App.http_routes()`); request and response shapes are unchanged. A path matches exactly with its query parsed
  (`/xph?x` now answers; `/modelsfoo` and `/index...` other than `/index.html` are 404); a known path asked with
  another method answers 405 with `Allow`; a handler's exception answers 500 `{"ok": false, "error"}` instead of
  dropping the connection; an empty POST body reads as `{}`. `GET /calibrate` (runs a capture) and `GET /models`
  (`?refresh=1` refreshes the catalog) now need `X-Glide-Token`, like `/api/state` and `GET /memory`: only the
  desktop app's renderer calls them, through `engineGet`, which sends it. Contract 2, additive (R20):
  `POST /settings` also takes `{"save": <config.json patch>}`.
- **R20, contract 5 (config.json): one reader and writer, and the engine owns the file.** Keys and format are
  unchanged. Every Python reader goes through `companion/glideconfig.py`, so the knowledge build and the tools
  (`build_db.py`, `build_vendor_treasures.py`, `extract_maps.py`, same command lines) now take a config.json that
  Notepad saved with a BOM (they failed on one), and an unreadable file reads as "no WoW folder" instead of an
  exception. While the engine runs, the desktop app's `config:set` saves through `POST /settings {"save": patch}`
  (see contract 5 above for when the app still writes it itself). Desktop probe re-recorded: `main/app/config.js`
  exports `saveConfig` (and `RunningEngine` is a type only). `settings_manifest.json` entries may carry
  `"standalone": true` (the standalone Python overlay's keys); the generated desktop manifest is unchanged.
- **R29, a new shipped code folder: `wowfiles/` (command lines unchanged, contract 7).** The readers of the game
  client's files (casc, db2, dbdefs, db2_import, blp, spells, wdb, adt, worldobjects, terrain, navbuild) moved from
  `tools/` into the `wowfiles` package, with `navformat.py` holding the walking map's format (tile size, layout,
  NAV_VERSION, NAV_REQUIRED, a numpy-free read_version). The engine and the knowledge loaders import it as a package
  (no sys.path edits into tools/); `kb_refresh.NavBuilder.needed` reads only navformat. `tools/*.py` keep every
  stage and flag (`casc.py`, `wdb.py`, `db2_import.py` and `svparse.py` are thin entry points now). The app ships
  the folder (`desktop/package.json` extraResources) and syncEngine copies and replaces it (`paths.ts` CODE_DIRS).
  `tools/svparse.py` is now `knowledge/sources/_lua.py`'s SavedVariables reader (`read_saved_variables`, the one Lua
  table reader); on the 141 real recorder/RXPGuides SavedVariables files it gives the identical result. glide.db's
  input fingerprint lists `knowledge/sources/_lua.py` and `wowfiles/wdb.py` instead of `tools/svparse.py` and
  `tools/wdb.py`, so the next refresh rebuilds glide.db once.
- **R18, parser golden (KB data): dungeon flags hold RestedXP's dungeon tag.** `.dungeon X` now stores X as
  RestedXP's GetDungeonName makes it (upper case, then its alias table: 'Mara' -> 'MARA', 'Stockades' -> 'STOCKS';
  `rxp_parser.dungeon_tag`), the key RestedXP's dungeon settings use; an unknown name sets no flag and warns, as
  RestedXP does. `#dungeon X` step tags stay as written (RestedXP keeps them raw too). `.enabledungeon [!]X` is a
  parsed verb now (args `dungeon` tag, `enabled`), no longer "unknown_verb". Parser golden re-recorded: the
  `parsed` hash of the 14 Dungeon-group guides with `.enabledungeon` and the 7 Alliance guides that spell dungeons
  in mixed case changed, and `_includes` with them; every step numbering (`_index_maps`) is unchanged. A kb.db
  built before this keeps the old spellings until the next KB build (only a selection of some dungeons, not all
  or none, notices).
- **R18, knowledge golden: one RestedXP step rule, RestedXP's own Dungeon-guide behaviour.** The live coach
  (`Route`) and the build's step timing (`live_logs.StepMaps`, obs_step_time) now share one rule and one numbering
  (`rxp_parser.step_shown` / `visible_steps` / `rxp_numbering`) over one settings type (`rxp_parser.RxpSettings`,
  read from RestedXP's SavedVariables names or Glide's `ssf` / `group_quests` / ... names; `settings_key` is
  unchanged). They disagreed on the Dungeon-group guides: the coach turned group quests and every dungeon on there
  when the player had not set them, the build none. Both now do what RestedXP's DungeonCheck does: the guide's own
  `.enabledungeon [!]X` lines first, then a `#disabledungeons` header (47 non-dungeon guides) turns dungeons off,
  then the player's selection; unset options are RestedXP's defaults (off) in every group. For the live coach
  (DEFAULT_RXP players, dungeons unset) this changes the numbering of 5 Dungeon-group guides whose dungeon steps
  name a dungeon the guide does not switch on (BFD in (A) 17-18 Hall of Thanes and 20-21 Darkshore/Ashenvale, DM in
  (H) 12-16 and 18-21 The Barrens and 16-17 Ragefire Chasm): RestedXP hides those steps unless that dungeon is
  selected, and now so does the coach. With the desktop "dungeon runs" switch on, guides with `#disabledungeons`
  drop the dungeon steps RestedXP never shows there (3 guides for a Warrior). Other rule details now follow RestedXP
  exactly where the two copies differed (never on today's data): `#beta`, `#som` / `#era` / `#era/som`, server tags,
  Lua truth for tag values. Stored briefs: meta `step_brief_rxp` gains `"rule": 2`; a kb.db built before it has its
  Dungeon-group guides' stored briefs treated as built with group quests and every dungeon on (as they were), so
  they are served only to such a player until the next KB build. Knowledge golden re-recorded: 7 of 196 records
  change, all in the two Alliance Dungeon guides with a BFD step (ETA, and the fact ids after the hidden step in
  Darkshore/Ashenvale).
- **R17, engine reads kb.db only through knowledge.api (contract 6 additive: no table or column change).** New
  KnowledgePack methods (`knowledge/api/lookup.py`, "the engine's reads"): `step_links`, `quest_steps`,
  `friendly_npcs`, `vendors_selling`, `continent_of`, `map_name`, `quest_name`, `map_areas`, `landmarks`,
  `rxp_upcoming`; briefing.py, reminders.py, coach.py and app.py no longer query kb.db. The pack is built with the
  coach's geo (`open_pack(api, kb, rxp, geo)`), and one `knowledge_layer.KnowledgeHandle` holds the current pack, api
  and reason for the coach, the app (`app.pack` / `app.kb_reason` are views of it; `app.kb` is gone) and the rules
  (`rules.kb_fn` is the handle). glide.db at runtime: geo.py's sub-zone boxes, landmarks and map names, the
  lookahead fallback's raw guide lines and the coach's quest-name fallback now come from kb.db (area_box/area,
  creature_spawn/creature/quest_giver, rxp_action, quest); glide.db is read for them only when there is no
  knowledge pack. Player-visible: landmark points are kb.db's creature_spawn (points rounded to 0.01 and merged per
  source: 88,665 kb rows vs glide.db's 115,757 spawn rows) instead of glide.db's spawns: on this PC the same NPCs
  (0 added, 0 lost over 43 maps), 7,429 vs 7,344 distinct points; sub-zone boxes identical. The lookahead fallback
  numbers lines by the guide's step index (it said "approximate" before too), and a quest with no objective spots in
  kb.db no longer falls back to glide.db's spawn centre for the "near the quest" briefing check. All goldens "same".
- **R17, kb.db schema stamp (contract 6 additive: a meta key, no table or column).** The build writes meta
  `schema_version` (`knowledge/store.py` `SCHEMA_VERSION`, now 1); a kb.db without it (built before) counts as 1.
  KnowledgePack refuses another version ("rebuild needed (schema N, engine expects M)", shown like the other
  knowledge-layer reasons: startup line, Status page, a hot swap keeps the old pack), and `build_kb.staleness` calls
  such a kb.db stale. The build adapter `kbdata.ConnKB.q` prints a failed query once per distinct error (as
  `store.KB.q` does) instead of swallowing it.
- **R30, one parameter registry read from the KB (no interface change; `p:` fact ids, the `param` and `heuristic`
  tables unchanged).** Analyzer parameters register in `knowledge/mechanics/params.py` (`register(..., lane=
  "analyzer")`; `knowledge/analyzer/params.py` is that lane's view, same functions). At runtime the KB in use
  answers: `kbdata.get_data(kb)` makes that KB's param table (plus meta `analyzer_params` for an older build) the
  current values, replacing the previous KB's (`use_kb`); before, the first KB's fitted analyzer values stayed in a
  process-global dict after a hot swap. The coach's heuristic rules and `h:` facts come from the KB's `heuristic`
  table (`KnowledgePack.rules()`); params.json and rules.json are build inputs (a KB without param or heuristic rows
  falls back to them). On this PC every parameter's value, status and evidence and all 321 rules are the same as
  before; all goldens "same".
- **R31, builders share one install and code fingerprint (knowledge/store.py).** `tools/build_db.py` now installs
  glide.db like build_kb installs kb.db (`store.install`: staged copy + `os.replace`; when the target stays open,
  `glide.new.db`, which `kb_refresh.promote` installs at the next engine start) instead of copying over the live
  file. Its input fingerprint now covers the content of every repo module it runs (`build_db.CODE`: store.py,
  _lua.py, wowfiles/wdb.py, glideconfig and its imports; checked by tests/test_build_install.py) instead of
  size+mtime of three files, and build_kb's staleness inputs gain `game file code` (`wowfiles/**/*.py`, content
  hashed). Effect for players: one rebuild of glide.db and kb.db at the next background refresh after the update
  (the fingerprint format changed). Command lines and exit codes unchanged.
- **R28, map_bounds fits only maps without an exact bound.** Recorder bounds are read before the fit; a map with a
  conf-1 bound (obs/db2) is no longer fitted (a fit, conf <= 0.95, never replaced it). meta `map_bounds:fit` lists
  those maps as `{"n", "fit": null, "exact": src}`; `GLIDE_MAP_FIT_ALL=1` restores the full validation fit (stats
  only, with `exact_err_pct`). The map_bounds and map tables of a full temp build are identical before/after;
  meta `map_bounds:continent` now validates the continents against the exact bounds. Knowledge golden "same".
- **R17, prompts: the quest rows come from kb.db (contract 6 wording; no table or column change).** The game context's
  quests (`prompt_context.game_context`: objectives with their spawn centres, givers, turn-ins and their places,
  level, zone, quest text) and its map names, and the dashboard's and the speech vocabulary's quest names and levels
  (app.py `_quest_basics`), come from the knowledge pack (`KnowledgePack.quest_detail(qid, near_map, detail)`, the
  shape `GlideDB.quest()` gives, and `quest_basics(qid)`); glide.db is read for them only without a pack. Contract
  6 now says glide.db is the knowledge build's input when a kb.db exists. `wow_text` moved to `knowledge/store.py`
  (glidedb.py imports it). The prompt layout, key order and caching split are unchanged; only the knowledge in the
  quest rows changes. On this PC, over the 7,406 kb quests: 1,976 rows identical; objective `type` uses the KB's
  words (kill, collect, interact, talk, explore, event, other instead of glide.db's monster, item, object, 10,
  19, ...; 6,251 objectives); `side` is capitalised (Alliance, Horde, Both; 4,575 quests); `giver_at` / `turn_in_at`
  added for 3,880 / 4,201 quests glide.db had no point for (the KB places the giver from its spawns), and an observed
  point (where the quest was seen taken or handed in) comes before a spawn-derived one (giver_at differs for 50,
  turn_in_at for 47, mostly by under 0.1%); giver / turn-in names are listed once (40 / 25 quests named the same
  poster or object twice); objective spawn centres come from kb.db's creature_spawn / gameobject_spawn rows (merged
  per source, plus RestedXP's pins): 59 centres moved (12 by more than 2% of the map, 2 onto another map), 73 spawn
  counts differ, 24 objectives gained a target and place (talk-to NPCs); 2 target names lost glide.db's cache
  garbage (quest 15 "Kobold Worker"); XP from the KB (329 added, many 0 for event quests; 15 changed to the KB's
  Forever values). A 10-quest Dun Morogh log's quest section went from 5,447 to 5,489 characters, an Elwynn one from
  5,040 to 5,528 (the added places). R34 fixture: every quest has the same keys, names, givers, places and objective
  targets; only the objective type and side words change, and one objective counts 13 spawn points instead of 12.
  All goldens "same" (no probe re-recorded).
- **R13, contract 8 (Status page, capture log and Game capture check): an addon in another strip version is named.**
  A frame whose length and Fletcher-16 hold but whose version byte isn't `protocol.PROTO` now decodes to
  `protocol.OtherVersion` (falsy, so readers that take good frames skip it; version 0 with no payload, what a black
  grid reads as, stays "unreadable"). The wire format is unchanged (contract 1). The capture loop counts those frames
  (`App.strip_other`) and logs "[capture] the game's Glide addon sends strip version N and this engine (Glide app)
  reads version 1: <fix>." instead of "no Glide strip found. Is the addon on ...". The Status page's game-link item
  then says, under the app, "The Glide addon doesn't match this app" / "The game's Glide addon sends strip version N
  and this Glide app reads version 1. Update the Glide app, or install its addon on the Addon page and fully restart
  WoW." (an older addon: "Install the update on the Glide app's Addon page, then fully restart WoW."); run on its own,
  "Glide addon speaks strip version N, the engine expects 1" with the developer fix (and the `addon_install` button
  for an older addon). Settings > Game capture's check answers `problem: "version"` with "The strip reads cleanly,
  but the addon speaks strip version N and this coach reads version 1: ...". Good frames after them clear it.
- **R13, contract 2/3/4 additive: the versions in play.** The desktop app passes its version to the engine (env
  `GLIDE_APP_VERSION`, beside `GLIDE_TOKEN`); `GLIDE_READY port=N app=V` (the app still matches `GLIDE_READY
  port=\d+`). The state and `GET /health` carry `versions` {`app` (null run on its own), `addon_expected`,
  `addon_installed`, `addon_running`, `strip` (PROTO), `strip_seen` (the version the addon last sent, null before any
  frame), `kb_schema`, `nav`}, typed `Versions` in `desktop/src/shared/engine.ts`; the dashboard fixture has it.
- **R13, addon golden: the addon's version is masked.** `probe_addon.py` makes the mocked addon report version
  `golden` (same length as a real one), so an addon version bump no longer changes the payloads (`gv=`), the strip
  paint or the window's version line. Re-recorded once: only `gv=0.15.2` -> `gv=golden`, the paint hashes and the
  window's "v0.15.2" -> "vgolden" change. The engine golden's `addon` section replays those payloads, so its
  `addon_version` reads "golden" (re-recorded for that only).
- **R26, contract 2 and 3: a slimmer state stream.** (1) `community` (about half the state) leaves the per-second
  `state` event: a new `event: community` (data: the notes, or null) is sent when they change, and a new client's
  `init` and `GET /api/state` still carry it inside the state (Hub keeps the last one). Its only reader, the
  dashboard, listens for that event (and still takes `community` from any state that has it). `EngineState.community`
  is now marked optional. (2) An additive light stream, `GET /events?lite=1`: the same events, but its `init` and
  `state` carry only `overlay_step`, `frames`, `level`, `zone`, `session_minutes`, `coach`, `models`, `refresh`,
  `navbuild`, `reminders` (dashboard.py `LITE_KEYS`, engine.ts `LiteState`) and it gets no `community` event; the
  app's overlay and its window (session bar, narrator) use it. (3) `tuning` stays in every state (no UI reads it) but
  is recomputed only when the settings, models or measurements it reads change, or once a minute
  (`tuning.SnapshotCache`). On the dashboard fixture: 3 x the full state per second before, the dashboard's
  state without community plus two light states after (numbers: `tests/test_state_stream.py`). No visible change.
- **R32, addon slash commands: one registry (contracts 7 and 8).** Every command is registered by its own module in
  Core.lua's `ns.Command(name, help, fn, aliases)`; `/coach` and `/glidecoach` are aliases of `/glide coach`, and
  `/glidelink <command>` is `/glide <command>`. Every command name and what it does are unchanged. Changed: `/glide
  help` (and an unknown command) lists every command sorted by name, one line each as `/glide <usage> - <what>`
  (before: the recorder's commands, then hand-written live-link lines); `/glidelink` alone or with an unknown
  command shows that same list (before: the live-link lines plus a strip status line, which `/glide dump` now
  prints); `/glidelink` also reaches the recorder's commands (`/glidelink status`); a live-link command that
  fails prints its error like the recorder's did. The buffs, gear, extras, coach and minimap diagnostics moved
  from Strip.lua to their own modules; their output is the same.
- **R32, addon 0.15.3: one error policy, reported on the strip (contracts 1, 3, 8; additive).** Every error the
  addon catches (a failing event handler of the recorder or the live link, a strip frame, a page or list build, a
  slash command) goes through Core.lua `ns.ReportError`: counted, the last kept, the session's first 50 saved in
  `GlideRecorderDB` (before: only the recorder's; the live link's were dropped unless `/glide debug`). An event the
  client doesn't have is no longer an error (listed by `/glide dump`; the recorder used to save it as one). New
  core key `er` (the count, sent only when there is one) and page `E` (`ex`, the last error's text); the state has
  `addon_errors` (`{count, last}` or null; desktop/src/shared/engine.ts, and the dashboard fixture got the key);
  health shows a warning "The addon reported N error(s)", detail "Last: <text>. In game, /glide debug prints each
  one as it happens; /glide dump shows the count and the last." `/glide dump` prints "errors: N, the last: ...".
- **R32, addon 0.15.3: no `fpn` ring event.** A new flight path on the taxi map no longer raises ring event `fpn`
  (nothing read it; a full taxi map was the one burst that could push real events out of the 3-slot ring). The
  registry keeps it, marked retired; the engine still ignores it from older copies. List `F` is unchanged.
- **R32, addon 0.15.3: State.lua builds its pages on its own 1 s clock** (Strip.lua only draws; before, the strip's
  frame rebuilt them at most once a second), and Extras.lua takes its events from the shared bus (`ns.On`) instead
  of its own event frame. tests/wowmock.py's `C_Timer.NewTicker` now honours its interval (it ran every ticker on
  every tick). Addon golden re-recorded: the version (`gv=0.15.3`, also in the window); the first three frames carry
  no page and `qn=0;qh=0;an=0;ak=0` (the first page build is 1 s after login), and the page order follows from that;
  from the QUEST_ACCEPTED frame on, the core carries `er=1` and page `E` goes out once: the mock lacks
  `C_QuestLog.GetTitleForQuestID`, so the recorder's QUEST_ACCEPTED handler fails there (it did before too: the
  session's error count is unchanged); the strip paint. Engine golden re-recorded: only the summary's new
  `addon_errors` key (null).
- **R32, sim: the quest log's hash.** companion/sim.py sent `qh` as the number of quests; it now sends the list
  hash of the Q0 chunk it sends, so the engine (which now checks it) takes the simulated quest log as before.
- **R32, engine: the quest log is checked against its hash.** `GameState` takes the quest log only when chunks
  `Q0..Qn-1` (core `qn`) are all there and their joined text matches core `qh`, like buffs and gear (before, a new
  chunk next to a stale one mixed two logs for a frame); a shorter log is taken as soon as the core says so.
- **R32, GlideRecorderDB readers check its layout.** tools/build_db.py and knowledge/sources/map_bounds.py read it
  through `knowledge/sources/_lua.py` `recorder_db()`, which warns (build log / KB notes) about a `schema` other
  than 1 or sessions recorded by an addon newer than the project's; the data is read as before. The layout is
  written down in docs/RECORDER.md.
- **R33, vendor treasures from the installed addon.** The addon names a nearby vendor by its index in its own
  Data/VendorTreasures.lua; companion/vendor_treasures.py now looks it up in the installed addon's copy
  (`<wow_dir>/Interface/AddOns/Glide/Data`), and in the project's only when there is none, re-reading a copy when
  its modification time changes. No wire change.
- **R28 follow-up, map_bounds reads only the recorder's mapBounds table.** `sv_bounds` still reads every recorder
  SavedVariables file (archive and live client) but no longer parses each whole ~5 MB file: a brace scan that skips
  strings and comments (`_lua.saved_variable_field`) finds `GlideRecorderDB["mapBounds"]` and parses only that
  table. Same rows (the player's 43 files: 2451 bound rows, identical hashes old vs new; `tests/test_sv_field.py`);
  that step went from ~44 s to ~1.4 s of each KB rebuild. No data change.
- **R37, contract 4 additive (desktop IPC): `app:logs`, and the logs on disk.** The Logs page's lines (the
  engine's stdout/stderr, tracebacks included, and the app's own lines) are now also appended to
  `userData/logs/engine-YYYYMMDD.log` (desktop/src/main/logfile.ts: a file per local day, the 7 newest kept,
  batched async writes, a failed write counted and dropped), with a `==== coach start | app | engine home | python`
  line per start and `==== coach ready | GLIDE_READY ...` once it listens. New channel `app:logs` (no arguments;
  answers shell.openPath's "" or the reason) opens that folder: the Logs page's new "Log files" button. Desktop
  golden re-recorded for the channel, the new `main/logfile.js` module and `paths.logsDir` only. Engine side (no
  interface change): status lines go through companion/log.py with their text unchanged, and the 21 silent
  `except Exception: pass` sites log their first error per site (`log_once`) and still carry on.
- **R25, one home for the shared helpers (contract 8: offline why wording).** The offline coach's "why this step"
  line now uses the knowledge layer's why phrases (knowledge/api/reasons.py: "on the way: little detour", "vendor /
  supplies" where it said "on the way, little detour" and "vendor and supplies"); its own copy, now the same words,
  is only the fallback when the knowledge layer did not load. No golden probe changed. Number parsing lives in
  knowledge/values.py (stdlib only) and every reader keeps its old results, except that an infinite or huge number
  (float('inf'), 1e400) now reads as "no number" in the int readers that used to raise OverflowError on it.
  Classes, races, factions and their bitmasks live in knowledge/vocab.py (bit 16 reads back as "Scourge", the game
  token, and both "Scourge" and "Undead" look it up, as before); feed message kinds in companion/msg_kinds.py; the
  core cf decoder in strip_spec.coach_flags. The knowledge loaders' and glide.db builder's code changed, so the
  next refresh rebuilds kb.db and glide.db once (code fingerprints).
- **R28 follow-up, faster Lua strings in every SavedVariables read.** `_lua._TOKEN`'s string tokens use the
  unrolled "plain run, then (escape, plain run)*" form `_HIDES` already used: the same strings (the player's 140
  recorder and RXPGuides SavedVariables files, 369 MB: tokens, `saved_variables` and `assignments` hash-identical
  old vs new; `tests/test_lua_strings.py`), tokenizing them in 8.7 s instead of 68 s and `saved_variables` in
  16 s instead of 77 s. No data change; the loaders' code changed, so the next refresh rebuilds once.
- **R25 follow-up, the knowledge side counts briefings and reminders as the coach's lines (contract 6: kb.db
  eval_flag).** knowledge/vocab.py `COACH_SAID` (callout, answer, review, briefing, reminder; kept equal to
  companion/msg_kinds.COACH_SAID by `tests/test_helpers.py`) replaces flags.py's `COACH_KINDS` (callout, answer,
  review) and run_eval.py's inline copy. A `/glide mark` made right after a quest briefing or a reminder now stores
  that line as its `eval_flag.text` instead of the older callout before it, and an eval replaying a session log
  keeps briefings and reminders among the coach's recent lines (`tests/test_coach_said.py`). The loaders' code
  changed, so the next refresh rebuilds kb.db once.
- **R37 follow-up, the broad excepts that fall back to a default log too.** The 23 engine sites that caught any
  error and silently used a fallback value (the state's community and tuning extras, the coach's history, talent
  pick, fact check and glossary, briefings, step videos, reminders, the knowledge extras, ...) now log their first
  error per site with `log_once` and fall back as before; four stay quiet because their error is the normal case
  (no knowledge or wowfiles package for `version_info`, the pre-8.1 Windows DPI call, a bad `POST /ask` body).
  `tests/test_log.py` fails on a new silent fallback. Only new console lines; no other change.
- **Contract 4 and files on disk additive (desktop IPC, `app-settings.json`): `sessionBarOrder`.** The session
  bar's pieces drag left and right (`desktop/src/renderer/session-bar-order.ts`), and their order is saved as
  `AppSettings.sessionBarOrder` (the pieces' `data-sb` keys) through the existing `app:settings:set`. A new
  optional key; nothing else changes. The player asked for it (2026-10-29), no review recommendation.
- **Contracts 3 and 4 (the coach screen's state, desktop IPC): the pre-0.8 addon cleanup is gone.** The Addon
  page's "Also remove the old GlideLink and GlideRecorder addons" switch, `addon:install`'s `removeOld` argument,
  `AddonStatus.leftovers`, the Status page's `addon_old` check, `versions.leftovers()` and the addon check's
  `kind: "old"` are removed: those addons predate 0.8.0 and no install has them any more (the player asked,
  2026-10-29). Old GlideRecorder.lua recordings are still read (archiver, build_db).
- **Player-visible text (the coach screen): panels show crests, not names** (`dashboard` golden re-recorded).
  The book's tabs, the docked sections, the coach chat and the route tracker no longer print their names
  ("Quest log", "Coach", "Route", ...): each carries a crest (its emblem in the HUD's round bezel) and the
  header row shows only its live reading; the names stay as accessible names (`.sr`, `aria-label`, the tab's
  tooltip). The player asked for it (2026-10-29), no review recommendation.
- **Contracts 2, 3 and files on disk additive: the community feed.** New notes reach players without an app
  update: the community feed in `feed/` of the public Fermin7c1/Glide-releases repo (a manifest with the feed
  file's sha256; the format is in `companion/community_feed.py`, which only reads it) is checked every
  `community.feed_minutes` (new settings `community.feed`, `community.feed_minutes`, `community.feed_url`),
  keeps it as `data/live/community_feed.json.gz` with its state in `data/live/community_feed.json`, and writes
  its notes into kb.db's community tables (again after a rebuild). The state gains `community_feed` (version,
  notes, checked, fresh, error); HTTP gains `POST /community/seen` and `GET /sound/<name>.ogg` (the game's
  level-up fanfare, read from the player's own client like the buff icons: a granted exception). The player
  asked for it (2026-10-29), no review recommendation.
- **Player-visible text (the coach screen and the session bar): no panel headers, the volume in the footer**
  (`dashboard` golden re-recorded). The panels' header rows are gone: each panel's emblem sits large and dim in
  the top right of its ground, it moves by an invisible grip along its top edge, and its name and reading stay as
  the accessible heading (`h2.sr`). The coach chat's volume bar is gone; the session bar's speaker raises a
  vertical volume bar (hover, wheel) and still mutes on a click. The coach screen no longer sends `volume` or
  `muted` messages to the app, only `ask`. The player asked for it (2026-10-29), no review recommendation.
- **Player-visible text and contract 2 additive (push-to-talk, the book, the minimap, the name).** The engine sends
  a `ptt` event (`listening`, `transcribing`, `idle`) to every client as it happens; the overlay, the session bar
  and the coach screen's ask box show it (`dashboard` golden re-recorded). The book's tabs move to a column of
  crests down its right edge; the minimap's zone name is engraved on the map (no header above it); every panel
  moves by its grip. The app shows its name as "Glide" (window, page and tray titles, shortcut and uninstall
  names); the product name, executable, install folder and settings folder stay "Glide to 60" so installs,
  settings and updates keep working. The update window now gets its arguments (a real UTF-8 BOM), opens over
  Glide's window, shows its icon and a filling bar, and Glide closes once it shows. The player asked
  (2026-10-29), no review recommendation.
- **Player-visible text (the coach screen): the book has no tabs** (`dashboard` golden re-recorded). The book is
  one page of its sections, one after another in the book's order, each named only by its emblem; a section
  moves by its grip (on the shelf or docked). The crest tabs, `#tabs`, the open-tab memory (`glide.tab`) and the
  tab keys are gone. The player asked for it (2026-10-29), no review recommendation.
- **Addon 0.16.0: the new-at-your-trainer window** (`addon` golden re-recorded for the TOC's new file). On a level
  up that brings spells or ranks (RestedXP's training list less the spellbook, Extras.lua), `Training.lua` shows
  them as the spellbook does, with what a new rank changes against the known one, the trainer costs the recorder
  has seen, the total and the player's gold; `/glide train` opens it, the Glide window switches the auto-open
  (`GlideLinkDB.train.auto`). The strip and everything it sends are unchanged. The player asked (2026-10-29).
- **Security review before the repository goes public (contracts 2, 4 and files on disk)** (`dashboard` and
  `desktop` goldens re-recorded). The engine's token is no longer in the served page: the app opens the coach
  screen at `/#t=<token>` (a URL fragment never reaches a server) and a standalone engine prints that link. The
  page gets a Content-Security-Policy (its own inline script by hash) and `nosniff`. The community feed refuses
  non-https addresses and redirects, inflates at most 64 MB and cleans note fields; community source links must be
  http(s). A custom endpoint only gets a built-in vendor's key at that vendor's own host. The app answers IPC only
  from its own pages, its windows show only its own pages, `shell:open` opens web pages and existing local folders
  only, saved keys must be named `*_KEY` / `*_TOKEN` (never PATH, PYTHON*, PIP_*, proxies), `endpoint:test`
  follows the vendor-key rule, `app:settings:set` and `data:import` refuse network shares, and the Whisper model
  name must be a known one. The coach can't change its memory in an answer that read web results. The TACT key list
  is fetched at runtime (wowdev/TACTKeys) instead of shipped; `tools/build_seed.py` runs third-party Lua in a
  sandbox (`tools/lua_sandbox.py`). Electron 44.10.0. The player asked (2026-10-30).
- **Electron's asynchronous safeStorage** (`desktop` probe re-recorded: `main/secrets.js` exports `loadSecrets`):
  Electron 45 deprecates the synchronous `safeStorage` calls and 46 removes them. The key store is decrypted once at
  start with the asynchronous ones (`loadSecrets`, before the engine starts) and kept in memory; `setKey` is async,
  still starts from the file on disk, runs one at a time, and a key Windows asks to re-encrypt is saved again.
  `secrets.json` keeps its format, and keys saved by earlier versions read as before. The player asked (2026-10-30).
