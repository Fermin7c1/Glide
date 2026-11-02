# Guides page plan (the RestedXP Guide & Route Explorer)

The player's goal for the Guides page (desktop rail, `desktop/src/renderer/rxp-browser.ts`): their RestedXP route over
the zone map with waypoints, directions and objectives visible; hover or click any element for details; a drawn route
from one step to the next that follows the real terrain (the walking maps the live coach uses); and five additions:
live character sync with "Jump to live step", quest-chain highlighting, a "before you start" prep list, time / XP /
hazard callouts on the timeline, and map zoom/pan with keyboard stepping. Research notes with file:line references,
measured costs and data shapes: [guides-explorer-research.md](guides-explorer-research.md).

Glide is not a dashboard (CLAUDE.md). Every addition here earns its place by changing the next minutes of play: where
to walk, what to buy before leaving town, which follow-up a skip loses, where the elites are. When an idea drifts to
"show more", leave it out.

## Already shipped (do not redo)

- 0.4.9: world-coordinate positions placed with kb.db `map_bounds` (`rxp_import.place_world_points`), free 1-20 guides
  listed (`rxp_import.free_catalog`), Skyborne under both factions, paid packs bound to the buyer account (addon 0.17.3).
- 0.4.10: the Guides page in the rail, fitted to the window; only the step list scrolls.
- 0.4.11: zoom/pan (CSS transform on `#rxpb-map-layer`, bodies counter-scaled with `--inv`), Follow step, Done steps
  hidden, Left/Right/Space/+/-/0 keys. Feature 5 is done apart from the polish in phase 1.
- 0.4.12: pin hover cards and keyboard access to pins; one-sided race/class picks; Next guide stays in its group.

## Phases

One session per phase, in order: every phase touches `rxp-browser.ts`, and each ships to `main` on its own. A phase
may ship in several releases (one per finished piece); each release is green and useful on its own.

### Phase 0: one truth for steps, and a tight page

The ground the rest stands on. Research: explorer-ui steps 1-3, 9; kb-data "One parse path"; map-imagery 1-4.

1. **The engine decides which steps show and their numbers**, with `knowledge/rxp_parser` (the coach's own rules), not
   the TypeScript copy in `rxp-conditions.ts`, which disagrees on 41 of 80 free guides for a Human Mage (`step << skip`,
   season/dungeon/xprate/phase tags, sod/era words). `guide_detail` sends compact per-step visibility masks over every
   valid character (race pick x class, `knowledge/vocab.py`) and the settings combinations (ssf, group quests,
   hardcore); the renderer only reads masks. Keep `racePick` and the picker; rewrite `tests/test_rxp_conditions.py`
   around the masks and add a parity test against `rxp_parser.rxp_numbering`.
2. **`#include` expansion** in `guide_detail` over the same corpus the knowledge build uses: the free dungeon guides
   currently show as one empty step (kb has 71-847), and `14-16 Darkshore` drifts from step 166. Regression test with
   the seed.
3. **Identity by guide step number (`st.idx`)**, not position in the filtered list: active step, pinned/selected
   element (`selected: {kind: 'step'|'wp'|'leg'|'loop'|'obj', src, sub}`), checked objectives, sub-waypoints. A filter
   change keeps the step or moves to the next visible one.
4. **Map geometry from the engine**: each zone's size in yards and rectangle on its continent (kb.db `map` +
   `map_bounds`, newest kb including `data/kb.next-*.db`), used for real yards and bearings (the `~N yd` readout
   assumes 3500x2400 yd today). A small DOM-free `rxp-geometry.ts` tested under Node.
5. **Map images by URL, not base64**: a `glide://map/<id>.png` protocol in the main process (every guide open now
   base64-ships all its zone PNGs over IPC and caches them for the session). `object-fit: fill` like the coach's map.
6. **A tight page**, as the player asked: one slim toolbar row instead of the wrapping header (guide picker, one
   "Character" chip opening a popover for race/class/SSF/group/hardcore/show-all, the map toggles); one scroller in
   the guide window (the current step expands inside the list instead of its own 42% box); timeline ticks as glyph
   buttons above the slider (shape as well as colour), the legend row gone; the step list a listbox with a roving
   tabindex; no page scroll at 900 px wide either. Check at 2560x1440 first, then 1920x1080, 1280x800, 900x600.

### Phase 1: the map comes alive (the player's original goal)

Research: walking-routes (all), map-imagery 5-9, explorer-ui 4-7, kb-data "Per-step targets".

1. **Legs through RestedXP's own path points** (6,504 of 13,868 gotos in the free guides are path points): a step's
   pin on its first stop; path points as small dots; sub-waypoint letters only on stops.
2. **Real walking paths, offline and cached**: `knowledge/api/navigation.py` gets an optional `max_expand`, a public
   `walk_world(kb, cont, wa, wb, max_expand)`; a new `companion/rxp_routes.py` behind `rxp_import.py routes <id>
   <index>` (request JSON on stdin: visible step order, focus step, budget_ms) computes legs nearest the focus first
   within a time budget, caps each search, answers `pending` legs for the app to ask again, and caches leg geometry in
   `data/rxp_imports/routes.db` (stdlib sqlite3; key = continent + rounded endpoints; stamp = walking map / terrain /
   kb mtimes; never reuse A->B reversed). Below-normal process priority on Windows. Large guides have 400-640 legs and
   an unreachable leg can cost ~20 s uncapped, hence the budget and cache. Time walked legs with `MT.run_time_s`, not
   `walk_route`'s `run_s` (it double-counts the 1.25 detour; a separate task was suggested for the coach).
3. **Flights, hearths, deathskips, boats**: fly legs from the client's taxi tables (`knowledge/mechanics/taxi.py`),
   hearth to the last `.home`, deathskip to the graveyard (kb `poi`); drawn as the coach screen draws them (walk:
   case + walk + flowing dashes; fly: dotted cyan + landing circle; hearth/death: a faint arc with the glyph).
   Straight and not-yet-computed legs stay a faint straight line marked approximate.
4. **Cross-zone legs and a continent view**: legs clipped at the map edge with a "from <zone>" marker that switches
   tabs; a "Continent" tab (maps 1414/1415) when the guide spans zones, used automatically for an active flight.
5. **Objectives on the map**: for the active step's quests, kill/loot/use targets from kb (`quest_objective` ->
   `creature_spawn` / `gameobject_spawn` / `creature_drop` / `quest_poi`), a shape per kind, a convex outline for
   spawn clusters, under the pins and counter-scaled.
6. **Hover and click everything**: legs (yards, time, compass directions, areas passed through, how it was worked
   out), objectives (target, level, rank, what it drops), loops; the inspector shows the selected element of any kind.
   Persistent SVG layers (`#rxpb-l-route`, `-loops`, `-objectives`, `-pins`, `-active`, `-hl`) rebuilt only when the
   guide or map changes; selecting a step only moves classes and redraws the active layer.
7. **Zoom polish**: `will-change` only while moving (re-raster at rest), Follow centres on the active target, Ctrl+wheel
   left to Electron's app zoom, Escape closes the card/inspector.

### Phase 2: sync with the live character (feature 1)

Research: live-sync (all).

Add `name, class, race, faction, guide, guide_group, step, rxp_settings` to the light stream (`LITE_KEYS` and the
`LiteState` Pick; contract 3 additive; ~200 B/s) and keep them in `live.game`. `guide_summary` also emits RestedXP's
page forms (`rxp_name`: zero-padded, cut to 40 bytes; `rxp_group`). The live step maps onto guide steps through
phase 0's engine numbering (`rxp_numbering` for the character's settings). On the page's first show with the coach
running, the race/class/SSF/group toggles take the character's (hardcore from config: the strip has none), and a
"Your step N" button in the timeline bar (key L) jumps to the live step so the player can scrub ahead to the next
hearth, deathskip or flight before spending a cooldown. The live step is marked in the list and timeline. Without the
coach the button is hidden and the pickers stay manual.

### Phase 3: quest chains (feature 2)

Research: kb-data "Quests block", explorer-ui 8. A `facts <id> <index> [CLASS] [RACE]` command
(`knowledge/api/explorer.py guide_facts`, read-only, works with no kb and fills what it can) returns per quest its
accept/objective/turn-in steps and, with kb, follow-ups (`quest.next_quest`, `quest_prereq`) and what skipping loses
(`simulate.unreachable()`, the coach's own rule). Quest badges become buttons: clicking one highlights its steps on the
map (connector in step order, "continues in <zone>"), timeline and list, fades the rest, and says what a skip breaks.

### Phase 4: before you start (feature 3)

Research: kb-data "Prep list". From the parsed steps (works without kb): `.buy/.buyAll`, `.collect` on an AH or vendor
step, `.bankwithdraw/.bankdeposit`, with names and vendors from kb `item` / `vendor_item`. A compact strip at the top
of the guide (not a panel): each item with its count and where it's needed; click jumps to that step. Conditional
items follow the character picks.

### Phase 5: time, XP and hazards on the timeline (feature 4)

Research: kb-data "Plan per step", "Hazards per step". `step_brief` (time, travel, kill time, XP now and unlocked,
level, risk) keyed by guide key, idx and class, mapped through phase 0's idx match (exact, or by text when the kb
predates the guide); hazards from the brief's risks, elite/rare kill targets and `creature_note`. Scrubbing shows the
time and XP between steps and marks dangerous stretches on the timeline; a hazard shows at its leg on the map. Moments
in play, not metrics: "elite patrol on this road", "45 min to 23", not a table.

## Rules every phase session follows

These come on top of CLAUDE.md (loaded automatically) and CONTRIBUTING.md.

- **Ship each finished piece**: commit on the session's branch with a player-readable subject (subjects become What's
  new), rebase on `origin/main`, and fast-forward `main` (`git push origin HEAD:main`) only when every check is green
  and no "Release desktop app" run is in progress (check the latest `release.yml` run with the GitHub MCP tools; two
  releases at once can claim the same version). After the release succeeds, read the version from
  `git ls-remote --tags https://github.com/Fermin7c1/glide-releases` and send the player a push notification
  (PushNotification) naming the version and what to try. A failed release: read the failed job's log, fix, ship again.
- **Checks before every push to main**: `ruff check .`, `luacheck addon/Glide`, `cd desktop && npm run build`,
  `python3 tests/run_all.py` (API keys blanked), `python3 tests/golden/golden.py check --portable`. In the Linux cloud
  container two differences are expected and not regressions: `test_types.py` fails on Windows-only modules
  (capture/overlay/ptt) and the `dashboard` golden reports "key '1150x800' missing". Check types the way the Windows
  build machine does instead: `pyright --pythonplatform Windows --outputjson companion` and compare each file's error
  count with `KNOWN` in `tests/test_types.py` (a new file is allowed 0). The build machine caught a real type error the
  Linux run missed.
- **Frozen interfaces**: record every change to one in `docs/REFACTOR-CONTRACTS.md` under "Recorded changes" (IPC
  channels and types, `rxp_import.py` subcommands, engine state keys, files on disk, player-visible text), and
  re-record only the golden probe it changes (`python3 tests/golden/golden.py record desktop`). An addon change bumps
  the version once in `addon/Glide/Glide.toc` and `tests/test_versions.py`.
- **UI checks**: build, serve `desktop/dist/renderer` with `python3 -m http.server <port>` (never 8797), stub
  `window.glide` with Playwright's `addInitScript` (invoke answering `addon:rxpImports` with test data), open the Guides
  page, measure and screenshot at 2560x1440 first, then smaller. Build realistic test data with the real parser:
  `rxp_import.free_guides(".", None)` plus a temporary `data/kb.db` holding a `map_bounds` row fitted around the
  guide's world points (see how `tests/test_rxp_import.py` does it). Stop servers by the PID you started.
- **Data that only exists on the player's PC** (kb.db, glide.db, data/gamedata walking maps and map PNGs, data/db2):
  tests build small fixtures or skip with exit 77; costs measured here are synthetic, so keep budgets configurable.
- **RestedXP's own addon** (github.com/RestedXP/RXPGuides) may be cloned into the scratchpad and read; executing its
  code is not allowed in these sessions.
- **When the phase is done**: send the coordinating session a short report (what shipped, versions, anything left or
  found) with the remote-session send_message tool, then stop. Don't start the next phase.
