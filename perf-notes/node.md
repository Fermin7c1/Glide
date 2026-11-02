# node

## Summary

TypeScript here is 7.0.2, the native Go compiler, so tsc is cheap: a single-file test compile takes 0.19-0.24 s wall / 0.27-0.35 s CPU. About 0.21 s CPU of that is fixed cost (lib.dom plus @types/node; an empty file costs the same). Another ~65 ms wall / ~75 ms CPU is the Node launcher: typescript/bin/tsc boots Node, then execve's the native exe on Linux, or spawns it as a child with execFileSync on Windows. The three main-process tests each compile all of tsconfig.main.json (0.36-0.52 s wall / ~1.0-1.15 s CPU). A full run does 17 tsc runs: 9 rxp tests, narrator, game_icons, 3x tsconfig.main (desktop_main/logfile/stores) and 3x settings-schema (desktop_schema.py via conversation/models/tuning; briefing skips first). Together that is ~7.9 s CPU and 4.9 s wall summed. The node side costs ~1.5 s CPU.

The biggest cost in this group is not tsc but idle Node processes. test_desktop_main's check finishes every assertion by 235 ms, then Node stays alive 6 s because the within() helper never clears its 6000 ms rejection timers. Result: 6.67 s per run, 7.5-8.2 s on windows-latest CI, in a _HEAVY slot. test_narrator sleeps 1.6 s of real time (80 ms per spoken line), and desktop_logfile_check sleeps a fixed 700 ms for a 250 ms batching timer.

Prototyped and measured, all four together, cold cache:
- The 17 tests run serially: 20.45 s wall / 12.8 s CPU before, 8.80 s / 6.65 s after.
- The same 17 tests in run_all at -j4: 7.16 s to 3.14 s wall, 11.95 s to 6.74 s CPU (3 alternating rounds, spread under 0.3 s).
- With a warm cache (sources unchanged since the last run), tsc never runs: 5.4 s CPU for the 17 tests.

Whole suite at -j4 (cache change and timer fixes, 2 alternating rounds): wall 65.7 s to 64.7 s, which is noise, because test_navmesh (~62 s) is the critical path. CPU went 176 s to 168 s. These gains become wall time only once navmesh/rxp_explorer stop being the critical path; then roughly (6 s CPU + ~8 s of idle slot time) / 4 ≈ 3.5 s at -j4. More on the Windows dev PC, where each removed tsc is 2 process creations plus ~60 new temp files scanned by Defender.

npm run build is 3 tsc passes: 1.65 s wall / 3.66 s CPU here, ~1.2-1.9 s on CI. `tsc -b --builders 1` over the three configs takes 0.59 s / 0.73 s CPU warm, 1.0-1.16 s after editing one renderer file, and 1.2-1.4 s cold. The golden desktop probe (which builds) goes 2.06 s to 0.97 s. Plain `--incremental` is unsafe: I verified it does not re-emit a deleted output.

Rejected, with numbers:
- One Node process for all JS checks: node starts in ~40 ms, so ~0.7 s CPU for 17 runs; it would couple the tests.
- Reusing desktop/dist: stale when the developer hasn't built, and racy because test_golden runs npm run build in parallel on the dev PC.
- `--singleThreaded` for test compiles: about 35% less CPU per compile, but compiles are rare once cached.

The rest of this group's cost is Python in test_rxp_conditions and test_rxp_live: rxp_explorer.visibility, the engine code behind opening a guide.

Scratch worktrees are removed; /home/user/Glide was not modified. Diffs are saved in /tmp/claude-0/-home-user-Glide/4ddc3f14-3355-571d-9bad-778daa3d02a0/scratchpad/perf/nodetools/diffs/ (p1_timers.diff, p2_shared_compile.diff, p3_narrator_clock.diff, p4_build.diff).

## Profile

Per test, before -> after (all prototypes), wall / CPU in s, CPU includes children (RUSAGE_CHILDREN deltas around each subprocess.run, single runs; Python CPU of test_rxp_conditions varies +-0.4 s run to run). The before column's tsc and node splits are wall/CPU.
test                    before        cold cache    warm cache   [before: tsc | node]
test_rxp_conditions     2.65/2.75     2.33/2.52     1.92/1.96    0.19/0.29 | 0.10/0.12 (rest is Python: rxp_explorer.visibility 2.4 s under cProfile, 92k step_shown calls)
test_rxp_geometry       0.26/0.39     0.09/0.15     0.07/0.10    0.18/0.27 | 0.06/0.06
test_rxp_icons          0.27/0.40     0.13/0.16     0.09/0.12    0.20/0.30 | 0.05/0.05
test_rxp_live           0.82/0.96     0.81/0.86     0.69/0.72    0.22/0.33 | 0.10/0.11
test_rxp_narration      0.37/0.50     0.10/0.14     0.10/0.14    0.24/0.35 | 0.10/0.08
test_rxp_plan           0.29/0.43     0.10/0.13     0.09/0.14    0.22/0.32 | 0.05/0.05
test_rxp_prep           0.32/0.45     0.14/0.17     0.20/0.26    0.20/0.29 | 0.05/0.05
test_rxp_quests         0.33/0.49     0.18/0.22     0.20/0.26    0.19/0.32 | 0.05/0.05
test_rxp_route          0.31/0.45     0.09/0.13     0.08/0.11    0.22/0.33 | 0.07/0.07
test_narrator           1.85/0.42     0.21/0.38     0.09/0.13    0.20/0.29 | 1.63/0.08 (sleep)
test_game_icons         0.59/0.79     0.68/1.50*    0.29/0.16    0.31/0.65 | 0.26/0.09   (*paid the shared main compile)
test_desktop_main       6.67/1.21     0.39/0.37     0.40/0.37    0.41/0.94 | 6.24/0.22 (all checks done at 235 ms, then 6 s idle on uncleared timers)
test_desktop_logfile    1.30/1.21     0.37/0.15     0.36/0.15    0.52/1.09 | 0.77/0.07 (fixed 700 ms sleep)
test_desktop_stores     0.58/1.27     0.12/0.13     0.10/0.13    0.50/1.15 | 0.07/0.07
test_conversation       0.91/0.55     0.78/0.34     0.71/0.24    0.21/0.32 | 0.07/0.07 (settings-schema via desktop_schema)
test_models             1.87/0.54     1.74/0.32     1.68/0.24    0.20/0.29 | 0.06/0.06
test_tuning             0.87/0.53     0.65/0.20     0.63/0.16    0.22/0.33 | 0.06/0.06
TOTAL                  20.25/13.32    8.92/7.87     7.69/5.41

tsc micro-benchmarks (median of 5-7, native TS 7.0.2):
- `node bin/tsc --version`: 0.068 s; native exe `--version`: 0.009 s.
- rxp-route.ts single-file compile: 0.188 s / 0.324 CPU via node, 0.123-0.136 s / 0.243-0.274 CPU via the native exe.
- Empty .ts with the same flags: 0.108 s / 0.206 CPU (fixed lib cost).
- Union of all 9 renderer test modules in one compile: 0.162 s / 0.384 CPU, vs 9 x ~0.27 CPU separately.
- tsconfig.main.json: 0.36 s / 1.04 CPU. tsconfig.renderer.json: 0.61 s / 1.63-1.75. tsconfig.dashboard.json: 0.35 s / 0.49-0.55.
- `--singleThreaded`: single file 0.18 vs 0.27 CPU at the same wall; renderer union 0.23 vs 0.38 CPU.

Emit equivalence:
- All 18 .js files from the per-test compiles are byte-identical to the union compile's output, including settings-schema, which used to compile without --strict.
- game-icons.js from its standalone `--module commonjs` compile is byte-identical to tsconfig.main's.

Windows CI (3 runs, windows-latest):
- test_desktop_main 7.5-8.2 s; narrator 1.9-2.4; desktop_logfile 1.0-1.6; rxp_conditions 1.6-3.0; single-file rxp tests 0.2-0.5 each.
- Desktop build step 1.2-1.9 s; golden portable step 8.8-9.4 s; Tests step 54-76 s.

npm run build here, median of 5 (other cases 3 single runs):
- Baseline: 1.65 s / 3.66 CPU, of which ~0.3 s is npm.
- `tsc -b --builders 1`: cold 1.19-1.37 / 2.8-3.3; warm 0.59 / 0.73; one renderer file edited 1.0-1.16 / 2.1-2.3.
- Golden desktop probe: 2.06 s / 4.0 CPU before, 0.97 s / 1.15 CPU after.

## Proposal: clear-check-timers — Stop desktop_main_check idling 6 s on uncleared within() timers; poll the logfile batching write instead of sleeping 700 ms

**change**: tests/desktop_main_check.cjs: within(p, ms, what) keeps its setTimeout handle and clears it in .finally() once p settles. Today every within() leaves a pending 1000/6000 ms timer that keeps Node alive ~6 s after the last check prints "ok".

tests/desktop_logfile_check.cjs: replace `await new Promise(r => setTimeout(r, 700))` with a 20 ms poll that ends as soon as the batched line is on disk, keeping the same 700 ms bound, then the unchanged assert.match.

Optional follow-up: drop test_desktop_main.py from run_all._HEAVY, since it now takes 0.4 s.

**measured**: test_desktop_main.py: 6.67 s wall / 1.21 s CPU before (node 6.24 s wall / 0.22 s CPU), 0.387 s wall / 0.31 s CPU after (median of 5, min 0.36, max 0.48). This figure includes the shared compile cache; the timer fix alone took the node check from 6.28 s to 0.30 s.

test_desktop_logfile.py: 1.30 s to 0.42 s wall (median of 5).

Both are pure waiting, so CPU is unchanged.

**saving_s_linux**: 6.7

**windows_note**: Node timers behave the same on Windows. CI shows test_desktop_main at 7.5-8.2 s on windows-latest, so expect about 1.5-2 s there. It is a _HEAVY test that starts at t=0 and holds a worker slot idle for 6 s. No new processes or files.

**coverage**: Same assertions. within() still rejects if the promise does not settle in time.

Mutation 1: Engine.stop() never resolving (exit/close listeners removed, kill timers pushed to 40 s) fails with "stop() of a running coach took over 6000 ms".

Mutation 2: logfile BATCH_MS 250 to 1000 fails the logfile check, exactly as the old fixed 700 ms sleep did. The bound is still 700 ms.

Reverted, both pass.

**risk**: Very low: test-only JavaScript. The poll checks the same condition the old code asserted after its sleep.

**effort**: small

**prototype diff**: `node__clear-check-timers.diff`

## Proposal: shared-ts-compile-cache — Compile the desktop TypeScript once per source content for all tests (tests/desktop_ts.py), calling the native tsc directly

**change**: New tests/desktop_ts.py exposes:
- node and available(), which replace each test's skip check.
- renderer(): one compile of the 9 renderer modules tests run (rxp-model, rxp-geometry, rxp-icons, rxp-narration, rxp-plan, rxp-prep, rxp-quests, rxp-route, settings-schema) with the tests' own flags (--target ES2023 --module ES2022 --moduleResolution bundler --strict --skipLibCheck --rootDir desktop/src), plus package.json {"type":"module"} so the .js files import as ESM.
- main(): `-p tsconfig.main.json --sourceMap false`, exactly what the 3 main-process tests ran.
- compiled(name, files, args): narrator.ts beside its stand-ins.
- url().

Output goes to <tempdir>/glide-tsc/<name>-<sha256 of: typescript and @types/node versions, the args, and the text of every .ts under the relevant desktop/src dirs (+ tsconfig.main.json and desktop/package.json for main)>. That means at most one compile per source content: once per run, zero when unchanged, and any edit recompiles.

How the cache stays safe:
- An OS file lock per entry (fcntl.flock / msvcrt.locking, released by the OS if the holder dies) means tests started together wait for the first compile.
- Compiles go to a scratch folder, get a .ok marker, and are renamed into place (with retries for Windows sharing violations).
- Entries unused for 2 days are pruned, marker first.
- The helper runs @typescript/typescript-<plat>-<arch>/lib/tsc(.exe) directly, as getExePath.js resolves it, falling back to node + bin/tsc.

Test changes:
- The 9 rxp tests and desktop_schema.py import from <renderer()>/renderer/<m>.js.
- Scripts that used to be written beside the compile output now go to the test's own temp dir as run.mjs, so the shared folder is never written to.
- desktop_logfile and desktop_stores pass <main()>/main.
- test_desktop_main copytrees main() into <tmp>/desktop/dist (4 ms), because paths.devRoot is derived from __dirname and the check writes beside it.
- test_game_icons uses <main()>/main/game-icons.js.
- test_narrator uses compiled("narrator", {narrator.ts + stubs}, flags).

Before landing: update the test docstrings that say "compiled into a temp folder", and add any future renderer module a test runs to RENDERER (a missing one fails loudly with Cannot find module).

**measured**: tsc runs per test run: 17 before (~7.9 s CPU, 4.9 s wall summed). After: 3 on a cold cache (renderer union ~0.16 s / 0.4 s CPU, main ~0.4 s / 1.0-1.4 s CPU, narrator ~0.15 s / 0.25 s CPU), 0 on a warm one.

The 17 tests via run_all -j4 (3 alternating rounds, with clear-check-timers applied as well):
- Cold cache: 6.97 s to 3.01 s wall, 11.99 s to 6.46 s CPU.
- Warm cache: 7.02 s to 3.01 s wall, 12.32 s to 5.56 s CPU.

Whole suite -j4 (2 rounds): CPU median 176.0 s to 168.1 s, wall 65.7 s to 64.7 s (noise; navmesh critical path).

Per test, the single-file rxp tests go from ~0.3 s to ~0.1 s wall.

Lock: 8 concurrent callers on an empty cache produced exactly 1 renderer and 1 main compile, all 8 done in 0.47 s. A compiling process killed with SIGKILL (by my own PID): the next caller got the lock at once and compiled in 0.19 s, and no half-written folder was used.

**saving_s_linux**: 6.5

**windows_note**: On Windows each removed tsc is node.exe start + execFileSync(tsc.exe), i.e. 2 process creations instead of an execve, plus ~60 files written to %TEMP% and deleted again, each scanned by Defender. Expect a larger saving than on Linux.

Paths:
- CI shows single-file rxp tests at 0.2-0.5 s, mostly tsc.
- Cache entries are written once per source content and reused, so fewer new-file scans and fewer deletes that can fail on open handles.
- File URLs are unchanged (file:///C:/...); pathlib.as_uri is used in desktop_schema.
- The native exe is @typescript/typescript-win32-x64/lib/tsc.exe; paths of 248+ chars fall back to the launcher, like getExePath.js.

Caveat: the msvcrt.locking branch and the rename retry are untested here (Linux container). They need one run on the dev PC and CI before landing. CI runners start empty, so CI gets 3 compiles per run instead of 17.

**coverage**: Emitted JavaScript is unchanged:
- All 18 files the per-test compiles produced are byte-identical to the union compile's.
- game-icons.js from its old standalone `--module commonjs --types node` compile is byte-identical to the tsconfig.main output.
- The main-process tests get exactly the compile they used to run.

Type checking is unchanged or wider: the union is checked with the same flags. A type error in any of the 9 modules now fails every renderer() user, not only the test of that module; the message is still tsc's file:line.

Two honest differences:
- settings-schema.ts is now checked with --strict (npm run build already requires that).
- game-icons.ts is checked under tsconfig.main (nodenext, the shipped build's settings) instead of standalone commonjs.

Mutations, all on the shared cache:
- A behaviour change in rxp-geometry.ts invalidates the cache (new hash). test_rxp_geometry and test_rxp_narration fail; narration imports geometry, as before.
- A type error in rxp-route.ts fails with "tsc failed (renderer): desktop/src/renderer/rxp-route.ts(16,7): error TS2322".
- Reverting reuses the old entry, and all pass.

Whole suite passes apart from test_types, which fails identically in baseline (Windows-only modules on Linux). ruff is clean.

**risk**: New shared state in %TEMP%. A stale result is possible only if something outside the hashed inputs changes emit, such as a hand-patched node_modules/typescript without a version bump.

The lock and publish code is new cross-process code; the Windows branch is untested here. The prune/rename edge case (a half-deleted entry that rmtree cannot clear) would raise instead of recovering.

Coupling: a type error in one of the 9 shared modules fails all the rxp tests at once.

**effort**: medium

**prototype diff**: `node__shared-ts-compile-cache.diff`

## Proposal: narrator-virtual-clock — Run test_narrator's speech scenario on a virtual clock instead of 1.6 s of real sleeps

**change**: tests/test_narrator.py: prefix the RUN script with a ~20-line fake clock. globalThis.setTimeout/clearTimeout and Date.now are replaced by a due-time-ordered timer queue (ties keep creation order, like Node). Each timer fires through the real setImmediate once nothing else is pending, so all promise chains settle between timers.

The clock starts at the real Date.now(). That matters because narrator.ts throttles its error note with `Date.now() - this.lastError > 60_000` from a 0 start, so a clock starting at 0 would suppress the first note.

The narrator module, the stand-ins and every assertion are unchanged. Applies on top of shared-ts-compile-cache (it touches the same file), or alone.

**measured**: test_narrator.py: 1.85 s wall / 0.42 s CPU before (node 1.63 s wall / 0.08 s CPU), 0.14 s wall / 0.14 s CPU after (median of 5, warm compile cache).

Event logs for all 10 scenarios are identical to 3 real-time runs. Measured times: tagged 160 ms virtual vs 160-162 ms real; muted, empty and cancel 0 ms vs 0-1 ms real.

**saving_s_linux**: 1.7

**windows_note**: Plain JavaScript, so it behaves the same on Windows. It also removes the test's dependence on Windows timer resolution (up to ~15.6 ms) and on runner load for its `cancel_ms < 20` / `muted_ms < 20` thresholds, which are potential CI flakes today.

**coverage**: Every assertion is kept. The timing assertions now measure exact virtual time (tagged_ms >= 155 is still enforced).

Mutation N1: cancel(tag) also drops the coach's re-handed line. Fails with ['speak:guide2','start:guide2','speak:coach2','cancel','resolved:guide3','resolved:guide2'].

Mutation N2: speakAndWait resolves at once instead of at the line's end. Fails with ['speak:coach1','start:coach1','resolved:guide1'].

What it no longer exercises: real Node timer jitter. The narrator's logic is about ordering, so a bug that appears only with real-time drift is unlikely. Stated plainly.

**risk**: Low. The fake clock covers only setTimeout/clearTimeout/Date.now. If narrator.ts starts using setInterval, performance.now or real I/O, the clock must cover those too; otherwise a stalled run fails loudly with no output.

**effort**: small

**prototype diff**: `node__narrator-virtual-clock.diff`

## Proposal: tsc-build-mode — npm run build: one `tsc -b --builders 1` over the three tsconfigs instead of three `tsc -p` passes

**change**: desktop/package.json scripts.build becomes `node scripts/gen-settings.js && tsc -b --builders 1 tsconfig.main.json tsconfig.renderer.json tsconfig.dashboard.json && node scripts/copy-static.js`.
- .gitignore gains `*.tsbuildinfo`. tsc -b writes tsconfig.*.tsbuildinfo next to the configs; electron-builder's files list (dist, assets, changelog.json, package.json) does not ship them.
- tests/test_engine_contract.py's string check becomes a regex that the build's `tsc -b` includes tsconfig.dashboard.json.
- gen-settings.js already writes only on change, so -b stays warm across builds.

Not frozen: contract 7 covers engine/tools/addon command lines, and the `npm run build` command itself is unchanged.

**measured**: npm run build: baseline 1.65 s / 3.66 s CPU (median of 5).

With `tsc -b --builders 1`:
- Warm, no change: 0.59 s / 0.73 s CPU (median of 5).
- One renderer file edited: 1.01-1.16 s / 2.1-2.3 s CPU.
- Cold (no outputs or buildinfo): 1.19-1.37 s / 2.8-3.3 s CPU.

The tsc part alone: three native `-p` passes 1.36 s / 3.39 s CPU; `-b` warm 0.27-0.30 s / 0.47 s CPU; cold 0.84 s in parallel, 0.96-1.19 s with --builders 1.

Golden desktop probe (runs npm run build): 2.06 s / 4.0 s CPU to 0.97 s / 1.15 s CPU, still "desktop same".

Plain `--incremental` was rejected: after deleting dist/renderer/rxp-geometry.js, a warm `tsc -p --incremental` did not re-emit it, whereas `-b` did.

**saving_s_linux**: 1.1

**windows_note**: On Windows npm runs each `tsc` as tsc.cmd, then node, then execFileSync of tsc.exe, so 3 passes are ~9 process creations; this makes ~3. That's more saving per build than on Linux.

CI: the Desktop build step builds cold; the golden step's second build becomes a warm ~0.3 s tsc. Releases run on fresh runners, so they always do a full build, and with --builders 1 in the old main, renderer, dashboard order dist was byte-identical to the old build.

**coverage**: Same three projects, same compiler options, same output.
- Full dist after `-b` is byte-identical to the 3-pass `-p` build (diff -r).
- The dashboard @ts-check always runs: tsgo treats dashboard.js as that project's output, so it is never "up to date".
- A JSDoc type error in dashboard.js fails the build on every repeated warm build (exit 1).
- A type error in rxp-route.ts fails the build twice in a row (exit 2), so no false "up to date" after a failure.
- A changed shared type fails the build, and deleted outputs are re-emitted.
- test_engine_contract still asserts the dashboard check is part of the build.

**risk**: Main and renderer both emit the type-only src/shared/*.ts into dist/shared/: main as CommonJS, renderer as ESM. When `-b` rebuilds only main (a main-only edit), dist/shared/*.js is left as the CommonJS stub instead of the renderer's ESM `export {}`. Contract R15 says nothing loads dist/shared, and full builds (releases) keep the old order, but local dist content becomes order-dependent.

-b is mtime-based, so copying a source in with an older mtime could skip a rebuild (rare).

It relies on tsgo's -b, which is newer than classic tsc's. Record a contracts note if dist content is considered frozen.

**effort**: small

**prototype diff**: `node__tsc-build-mode.diff`

## Proposal: rxp-visibility-python — (Pointer, not prototyped) Speed up rxp_explorer.visibility, the Python half of test_rxp_conditions and test_rxp_live and the engine's guide-open path

**change**: After the changes above, test_rxp_conditions is the heaviest test in this group: 1.6-2.3 s of Python CPU, almost all in rxp_import.guide_detail. Under cProfile (4.67 s total, about 2x real), visibility() takes 2.37 s for 4 guides, with 92,224 views.step_shown calls (1.21 s), 3.1M dict.get calls, and conditions parsing 0.45 s.

The same code serves the Guides page when a player opens a guide, so it is player-facing latency, very roughly 0.3 s per guide. It is also what test_rxp_explorer (45.9 s, the second-biggest test) stresses.

Suggested direction, for whoever owns test_rxp_explorer: evaluate each distinct step condition once per player/combo rather than once per step, or precompute per-condition bitmasks, keeping the output identical.

**measured**: Not prototyped. The estimate rests on the profile: halving visibility() would save ~1.0-1.5 s CPU across test_rxp_conditions and test_rxp_live, plus a share of test_rxp_explorer, and cut guide-open time in the app.

**saving_s_linux**: 1.5

**windows_note**: Pure Python CPU, so it scales the same on Windows. It overlaps with the test_rxp_explorer investigation.

**coverage**: Would need an output-identity check: the same vis masks and numbering for every free guide (test_rxp_explorer already compares masks with the coach's numbering).

**risk**: Production engine code (Guides page numbering and visibility). Must stay byte-identical in its output.

**effort**: medium

