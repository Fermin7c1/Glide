# longtail

## Summary

The long tail costs a lot of time for two reasons that aren't Python start-up. One is time spent waiting: fixed sleeps, leftover timers, server shutdown polling and SQLite fsync storms. The other is a few CPU hot spots inside tests. Per-process overhead is real but smaller.

Linux baseline (serial run, origin/main 070107e, 112 tests): the 109 tests other than navmesh, rxp_explorer and types took 69.5 s wall and 52.7 s CPU. That includes about 17.8 s of pure waiting. The 92 tests under 1 s took 28.0 s wall and 26.2 s CPU.

With all prototypes applied, the same tail took 42.0 s wall and 42.0 s CPU. Every test's exit code was the same before and after (pass, skip or fail). test_dashboard_page skips here because there is no Edge; with Chrome on PATH it went from 18.9 s to 7.2 s. A full -j4 run with everything applied passed apart from test_types, which fails on Linux as expected. The changed tests also passed 3 times at -j8, and twice more while 6 CPU-burning processes ran on the 4 CPUs.

The biggest finding is about Windows. The latest CI run (41384188701, 59 s) has 215.8 s of summed test wall time at -j4, so CI is limited by the 4 test slots, not by one long test: every second any test spends waiting comes off CI at roughly 1/4 rate. The tests that are much slower on Windows than here line up with SQLite fsync counts:
- test_rxp_facts: 30.1 s on CI vs 1.6 s here, 2076 fsyncs.
- kb/test_rxp_updates: 11.1 s, 386 fsyncs.
- test_rxp_routes: 10.5 s, 364 fsyncs.
- Six kb fixture tests: 3.0–3.8 s each on CI vs 0.3–0.9 s here, about 116 fsyncs each.

The cause is that knowledge/store.create_schema and build_db's SCHEMA are run with executescript on a plain connection. Each of the 85 (kb) or 25 (glide) DDL statements then commits on its own, with its own journal file and syncs. Running each schema in one transaction cut fsyncs across the suite from 3978 to 438.

Other wait-bound items:
- test_desktop_main: a never-cleared 6 s timer in its `within()` helper.
- dashboard_page_cdp.mjs: about 14.8 s of fixed sleeps.
- socketserver shutdown(): waits up to 0.5 s of polling per call, 19 calls in tests.
- The first-client-hook sleeps in test_dashboard_http.
- Fixed real-time sleeps in test_narrator, test_child_stdin, test_ptt and test_refresh_staging.

CPU items: test_link spends 5 s painting pixels one by one through lupa attribute reads, and test_run_all_interrupt burns about 3.6 s of navmesh CPU.

Per-process overhead: Python start-up is about 20 ms, and standard-library imports average 62 ms per test (6.67 s across 108 tests); repo modules add 1.61 s. No single import dominates: typing 4.1 ms, inspect 3.5 ms, ssl 3.2 ms per test. So no one shared import is worth caching.

I measured two ways to avoid a process per test on the 92 small tests:
- A warm in-process runner (runpy): CPU went from 26.2 s to 17.0 s (−35%). One test, test_coach_start, failed because an earlier test left threads running; 4 tests left 6 threads alive.
- A warm fork-per-test parent: CPU went to 18.5 s with no mismatches, but fork is POSIX only.
CI timings of trivial tests show 0.0–0.2 s, so per-process overhead on the Windows runner is small too. That is why a worker runner is ranked last. Caching the kb fixture per run is not worth it once the fsync fix is in: about 0.3 s CPU per test, absolute root paths baked into the kb meta, and tests that write into their copy.

Estimated CI effect of the prototypes: about 83 s less summed test time, taking the test step from 59 s to about 35 s. At that point test_navmesh (32 s on CI) becomes the critical path.

## Profile

Tools and data are in /tmp/claude-0/-home-user-Glide/4ddc3f14-3355-571d-9bad-778daa3d02a0/scratchpad/perf/lt_tools/:
- measure.py: per-test wall and CPU from os.wait4 rusage.
- wallprof.py: samples the main thread's stack every 2 ms.
- fsync_census.sh: strace counts of fsync, unlink, file creation and exec.
- imptime.py: `-X importtime` aggregated across tests.
- inproc.py and forkrun.py: the runner prototypes.
- Results: serial1.json (before), serial_after.json (after), census_orig.txt and census_new.txt.

1) Serial tail, all tests except navmesh, rxp_explorer and types: wall 69.5 → 42.0 s, CPU 52.7 → 42.0 s, no exit code changed. Biggest per-test drops (wall before → after):

| test | before | after |
|---|---|---|
| test_desktop_main | 6.78 | 0.73 |
| test_link (CPU) | 4.19 | 1.26 |
| test_run_all_interrupt (wall/CPU) | 3.18 / 3.79 | 0.69 / 0.20 |
| test_dashboard_http | 2.32 | 0.38 |
| test_narrator | 1.88 | 0.32 |
| test_models | 1.92 | 0.59 |
| test_rxp_facts | 1.57 | 0.31 |
| test_child_stdin | 1.57 | 0.36 |
| test_refresh_staging | 1.24 | 0.29 |
| test_ptt | 1.47 | 0.54 |
| test_conversation, test_dashboard_layout, test_endpoints, test_state_stream, test_ptt_online, test_tuning | 0.6–1.0 each | 0.15–0.5 each |

test_dashboard_page with Chrome: 18.9 → 7.2 s. Noise across 3–5 repeats is ±0.05–0.1 s for the small tests and ±0.5 s for the browser test.

2) Wall-time sampling:
- test_models: 1.4 s of 1.9 s is spent in `srv.shutdown()` waiting for serve_forever's 0.5 s poll (4 calls).
- test_rxp_facts: 1.3 s inside store.create_schema; strace counts 2076 fsyncs and 523 journal deletions.
- test_link: screen_frame takes 5.08 s of 6.0 s (each frame reads 1539 textures × 9 Lua fields from Python, then paints pixel by pixel).
- desktop_main_check.cjs: its own phases take 220 ms, then Node stays alive about 6 s for a pending `within()` timer.

3) Linking the CI log to the fsync census:

| test | CI s | here s | fsync before → after |
|---|---|---|---|
| test_rxp_facts | 30.1 | 1.57 | 2076 → 60 |
| kb/test_rxp_updates | 11.1 | 3.0 | 386 → 62 |
| test_rxp_routes | 10.5 | 5.7 | 364 → 28 |
| kb/test_store | 3.8 | 0.84 | 152 → 44 |
| kb/test_rxp_parser | 3.2 | 0.26 | 116 → 8 |
| kb mechanics, live_logs, community, analyzer | 3.0–3.4 each | | 116 → 8 each |

Suite totals: fsync 3978 → 438, journal unlinks 2052 → 1145, file creations 2505 → 1621. test_release_notes makes 66 successful process spawns (11 of them `git maintenance --auto`) and takes 3.5 s on CI; the change cuts that to 40 spawns.

4) Imports: the summed import time over 108 tests is 8.3 s (6.67 s standard library and third-party, 1.61 s repo modules). The costliest single module per test is typing at 4.1 ms; numpy is 61 ms but only 3 tests import it.

5) Runner prototypes on the 92 sub-1 s tests, original code, sum of separate processes as the baseline of 26.2 s CPU:

| prototype | CPU | outcome |
|---|---|---|
| in-process runpy, warm stdlib, repo modules dropped after each test | 17.0 s | 1 mismatch (test_coach_start: coach threads leaked from an earlier test); 4 tests left 6 threads |
| fork-per-test from a warm parent | 18.5 s, including the parent | 0 mismatches |

6) Mutation checks: each mutation below was caught (the test failed), the same way the original test fails on it where I compared:
- Strip.lua colour swap: test_link, new and original.
- engine.ts stop() not resolving: desktop_main, new and original.
- First-client hooks not cleared, run at once, fired twice, mute ignored: test_dashboard_http.
- narrator speakAndWait resolving early; cancel dropping the coach line (also compared with original).
- ptt min_seconds wrong in both directions.
- kb_refresh with a fixed stamp; promote picking the oldest.
- dashboard.js reconnect dedupe off; split taking the new step's model: test_dashboard_page.
- run_all's cancel_futures off: new test_run_all_interrupt catches it, the original passes it.
- Schema creation reverted to executescript, store.py and build_db.py separately: new guard in kb/test_store.
For test_link, all 69 painted frames are byte-identical to the per-pixel version. For test_narrator, all 13 event logs are identical; only the ms timings differ, as virtual time gives exactly 0 and 160.

## Proposal: sqlite-schema-one-transaction — Create the kb.db and glide.db schemas in one transaction (store.create_schema, build_db) — removes about 3500 fsyncs per run

**change**: knowledge/store.py create_schema(): run `conn.executescript("BEGIN;\n%s\nCOMMIT;" % script)`. On OperationalError, `conn.rollback()` if `conn.in_transaction`, then fall back statement by statement as today (same end state, verified against the original on a fresh DB, on re-application through the duplicate-column path, and on a partial schema, for both a plain and an open_rw connection). tools/build_db.py: new `create_schema(db)` (SCHEMA wrapped in BEGIN/COMMIT), used by build() and by tests/kb/fixture.py glide_db() in place of `executescript(build_db.SCHEMA)`. Regression guard added to tests/kb/test_store.py (test_schema_one_transaction): it traces the statements and expects exactly one BEGIN and one COMMIT for both schemas on a plain connection. The guard fails if either module reverts.

**measured**: fsyncs over the whole suite (strace): 3978 → 438. Per test: test_rxp_facts 2076 → 60, rxp_updates 386 → 62, rxp_routes 364 → 28, each kb fixture test 116 → 8. Linux wall: test_rxp_facts 1.57 → 0.40, rxp_updates 3.01 → 2.36, kb/test_analyzer 0.95 → 0.63, live_logs 0.75 → 0.48, mechanics 0.83 → 0.68, rxp_visibility 0.62 → 0.49. About 2.9 s wall in total, mostly I/O wait; about 1.3 s CPU. Windows is estimated, not measured: CI shows test_rxp_facts at 30.1 s for 2076 fsyncs, about 14 ms per fsync plus journal-file churn on the runner. That suggests test_rxp_facts 30 → about 1.5 s, the kb tests 3–3.8 → about 1.5 s each, and rxp_updates and rxp_routes about 4–5 s less each: about 50 s less summed test time, about 12 s off the CI test step.

**saving_s_linux**: 2.5

**windows_note**: This is the Windows win: FlushFileBuffers plus creating and deleting a -journal file for every DDL statement, which antivirus also scans, explains the 5–20x CI slowdowns of the kb and rxp tests. Player-facing: tools/build_db.py's glide.db build (first start, background refresh) does about 25 fewer commits, probably about a second less on a Windows disk. The kb build already used journal MEMORY and synchronous OFF, so it is unchanged. Both files are in build_db's CODE fingerprint, so players' glide.db rebuilds once after the release that carries this, as it does for any change to those files.

**coverage**: Production schemas are byte-identical (sqlite_master compared old vs new, fresh and re-applied). Tests read the same data. Only durability per statement changes, inside a build that already stages and renames its output. All tests keep the same exit code. A new guard keeps the property.

**risk**: Low. The one subtle path is an existing kb.db needing ALTERs: earlier statements are now rolled back, then re-applied statement by statement, giving the same final schema (verified). A caller using Python 3.12's autocommit=False mode would get 'cannot start a transaction within a transaction'. No caller does today (open_rw and the tests use the default legacy mode).

**effort**: small

**challenge (cov)**: holds=True, corrected saving 1.5 s

Reasons: I applied the diff in a scratch worktree of origin/main 070107e, alongside a base worktree. Both worktrees are now removed. All API keys were blank for every run.

SEMANTICS: no difference in normal use, checked against base.
- I compared sqlite_master, PRAGMA table_info and iterdump between base and proposal. They are identical in six cases:
  (a) a fresh kb schema;
  (b) create_schema run twice (the duplicate-column fallback);
  (c) an upgrade from the v1 block with 3 ALTERs applied and a table dropped;
  (d) the build_kb loader case: open_rw, then SAVEPOINT loader with a pending INSERT, then create_schema again, then RELEASE. The pending row is committed, in_transaction ends the same and RELEASE fails the same way in both;
  (e) the glide.db SCHEMA;
  (f) the tests/kb/fixture.py glide_db file, all 2,365 dump lines.
- A full fixture kb.db build (fixture.build: build_kb plus every loader, including rxp_parser/loader.py, which calls create_schema on the existing build connection) gives the same 3,295 dump lines and no loader errors. Meta differs only in paths, times and input mtimes.
- The only change in behaviour is on failure. A schema error now leaves no partial schema, where base left 41 tables. The same OperationalError is still raised.
- Production impact is small:
  - build_kb uses open_rw (journal_mode=MEMORY, synchronous=OFF), so kb.db builds gain nothing and lose nothing.
  - The real player-side gain is tools/build_db.py's glide.db build.
  - The rebuild-after-update cost is wider than the proposal says. knowledge/**/*.py is also a kb.db staleness input, so kb.db rebuilds once as well, not only glide.db. The content is identical.
- golden check --portable prints the same output on base and proposal.

TESTS: the 27 tests that touch the schema or the builders return the same codes on base and proposal, under py3.12 and py3.13 (some skip for missing numpy, lupa or a real KB). ruff is clean. The strace counts reproduce exactly: test_rxp_facts fsync 2,076 -> 60, unlink 523 -> 19.

MUTATION CHECKS
- Reverting store.create_schema or build_db.create_schema to a bare executescript makes the new test fail. Both checked.

GAPS IN THE NEW TEST (not a loss of existing coverage):
1. The ROLLBACK branch is load-bearing and nothing tests it. I deleted the two ROLLBACK lines: test_store, test_rxp_parser, test_rxp_facts, test_rxp_updates and test_analyzer all still pass. Without it, a caller that does not commit loses the upgrade. Example: an older kb.db with one ALTER applied, create_schema, then close. On reopen quest.removed and step_brief.race are missing, because the fallback ran inside the leftover BEGIN. With the ROLLBACK, or on base, they are present.
2. The assertion seen[-1].strip()=="COMMIT;" is brittle. Appending a trailing '-- comment' line to schema.sql is valid SQL and still one transaction, but the test then fails. The cause is that the trace text of COMMIT includes the comments before it.
3. `from tools import build_db` has no precedent in the repo. tools/ has no __init__.py, so it imports as a namespace package, and any installed regular `tools` package would take precedence over it. The test also loads build_db a second time beside fixture's `import build_db`.
4. The test checks build_db.create_schema but not that build() calls it. Reverting build() to db.executescript(SCHEMA) would pass.

SAVING ON LINUX, median of 3 runs at -j1, py3.13 (~10% noise on runs under 1 s):
- test_rxp_facts 1.15 -> 0.28 s wall (CPU 0.55 -> 0.23)
- kb/test_rxp_updates 2.73 -> 2.37
- kb/test_store 0.84 -> 0.73
- kb/test_analyzer 0.67 -> 0.58
- others 0 to 0.2
That is about 1.5 s of summed test wall and about 0.6 s CPU. None of these tests is on the critical path (test_navmesh), so run_all wall time at -j4 on Linux barely moves. The Windows win (fsync and the journal create/delete that Defender scans) is real but I did not measure it here.

Amendment: Keep the production change as proposed. Change the new test in tests/kb/test_store.py like this:
(1) Also cover the existing-database path. Run create_schema a second time on the same connection, then check `not conn.in_transaction` and that quest.removed and step_brief.race exist. That guards the ROLLBACK branch: deleting it currently passes every test and silently drops schema upgrades for a caller that does not commit.
(2) Check `seen[0].lstrip().startswith("BEGIN")` and `seen[-1].rstrip().endswith("COMMIT;")` rather than exact equality, so a trailing comment in schema.sql does not make it fail.
(3) Use `import build_db` (fixture.py already puts tools/ on sys.path, as test_rxp_updates relies on) instead of `from tools import build_db`.
(4) Optionally guard the call site too. For example, in test_rxp_updates, which already runs build_db.build in a subprocess, check that the built glide.db came from create_schema. Without that, a revert of build() to db.executescript(SCHEMA) goes unnoticed.
Also say in the proposal that kb.db rebuilds once for every player too, because knowledge/**/*.py is a kb.db staleness input. The content is identical.

**prototype diff**: `longtail__sqlite-schema-one-transaction.diff`

## Proposal: page-test-condition-waits — test_dashboard_page: wait for page conditions instead of about 14.8 s of fixed sleeps

**change**: tests/dashboard_page_cdp.mjs: add `until(expr, ms)`, which polls Runtime.evaluate every 20 ms and then waits two animation frames (capped at 250 ms), and `settled()`, which waits for no page fetch in flight. An injected, IIFE-scoped script counts the page's fetches in flight and its EventSource 'init' events; that listener is added in a microtask so it runs after the page's own. Each sleep is replaced by what the step needs:
- After navigate: readyState complete, one init, docked sections.
- Steps 76 and 77 shown: `onStep.n`.
- The question is in the feed, then the POST has settled.
- After a resize: innerWidth.
- The arrange live text is non-empty.
- Each drag: its /layout POST has settled.
- The reconnect: a second init (the browser's own retry, about 3 s, still sets the pace).
- The message after the reconnect is in the feed.
- The 'waiting' class is set.
The one negative check (a drag in the chat moves nothing) keeps its 400 ms dwell, since there is nothing to wait for. A wait that runs out returns false and the step records what the page shows, so the Python asserts and messages are unchanged. Also sets dashboard.SHUTDOWN_POLL_S in test_dashboard_page.py (see the shutdown proposal).

**measured**: Run with Google Chrome through a `chrome` wrapper adding --no-sandbox (probe_dashboard.edge() finds `chrome` on PATH): 18.9 s → 7.2 s median (3 runs, ±0.4 s); 10.7 s under 6 CPU burners at -j8. Every recorded step value (`seen`) is identical to the original except the clock text in 'asked'. Mutations caught: dedupe off ('replayed chat added again 17 → 33') and the split taking the new step's model. Not run with Edge on Windows. CI shows the test at 22.1 s, so it should drop to about 9–10 s (about 12 s less summed test time, about 3 s off the CI step).

**saving_s_linux**: 0

**windows_note**: On this Linux box the test skips (no Edge), so it saves 0 here as configured; 11.7 s wall with Chrome. On the Windows dev PC and CI it runs headless Edge, where the 12+ s of sleeps are paid every run. Condition waits also remove the flakiness risk of fixed sleeps on a loaded machine. frames() is capped at 250 ms in case a hidden window stops drawing.

**coverage**: Same steps, same values recorded, same Python assertions. The waits only end earlier when the awaited state is already there. Negative checks keep a dwell. The reconnect still has to really happen (counted on the page; the Python side still asserts at least 2 connects).

**risk**: Low to medium. It reads the page's top-level `onStep` (an inline classic script, so it is reachable from Runtime.evaluate); a rename would make that wait time out after 5 s and the split assert fail, not pass silently. Wrapping fetch and EventSource is invisible to the page.

**effort**: small

**prototype diff**: `longtail__page-test-condition-waits.diff`

## Proposal: node-leaked-timers — desktop_main_check.cjs: clear within()'s timeout (a pending 6 s timer kept Node alive); poll for the log batch instead of sleeping 700 ms

**change**: tests/desktop_main_check.cjs: `within(p, ms, what)` keeps its timer handle and returns `Promise.race([p, late]).finally(() => clearTimeout(timer))`. The race and its error are unchanged, but the timer no longer keeps the event loop alive for 6 s after "ok". tests/desktop_logfile_check.cjs: instead of `setTimeout(700)` then reading, poll every 10 ms for up to the same 700 ms bound until the batched line is on disk, then run the same assert.match.

**measured**: desktop_main_check.cjs on its own: 6.41 s → 0.29–0.46 s wall (CPU 0.27 s unchanged). test_desktop_main: 6.78 → 0.73 s serial; 0.94 s median over 3 runs, where 1.4 s of CPU is still tsc. test_desktop_logfile: 1.21 → 0.80 s. Mutation caught in both versions with the same message: stop() never seeing 'exit' gives 'stop() of a running coach took over 6000 ms'.

**saving_s_linux**: 6.4

**windows_note**: Platform-independent (Node timers). CI shows test_desktop_main at 8.2 s, so it should drop to about 2 s.

**coverage**: Same deadlines (1000 and 6000 ms; 700 ms for the log batch); only the idle time after success goes away.

**risk**: Very low.

**effort**: small

**prototype diff**: `longtail__node-leaked-timers.diff`

## Proposal: fast-server-shutdown — Make test HTTP servers' shutdown() return at once (serve_forever polls every 0.5 s)

**change**: companion/dashboard.py: new module constant `SHUTDOWN_POLL_S = 0.5`, passed to `serve_forever` in serve(). The default is unchanged and the engine never calls shutdown(). Tests that use dashboard.serve set `dashboard.SHUTDOWN_POLL_S = 0.02` after importing it, as they already set PING_S and FIRST_CLIENT_DELAY_S: test_dashboard_http, test_dashboard_layout, test_dashboard_page, test_state_stream, test_tuning, test_models. Tests that start their own ThreadingHTTPServer and later call shutdown() start it with `args=(0.02,)`: test_briefing, test_conversation, test_endpoints, test_llm_efficiency, test_models (3 servers), test_ptt_online, test_reminders, test_websearch. tests/golden/probe_dashboard.py also shuts a server down but was left alone, since golden probes are frozen.

**measured**: Simulated first by patching socketserver's poll to 0.02 s (3 runs each, median wall): test_models 1.98 → 0.66, dashboard_http 2.36 → 1.40, conversation 1.00 → 0.56, tuning 0.89 → 0.47, state_stream 0.72 → 0.25, dashboard_layout 0.65 → 0.17, endpoints 0.69 → 0.26, ptt_online 0.64 → 0.17. That is about 5.0 s of wall over 8 tests, CPU unchanged within noise. The real change gave the same numbers in the serial after-run. Tests that skip here without data/kb.db (reminders, briefing, websearch, llm_efficiency, coach_api) gain about 0.25–0.5 s each per shutdown on the dev PC.

**saving_s_linux**: 5

**windows_note**: Identical on Windows (select-based poll). 0.02 s becomes about 31 ms with the 15.6 ms timer tick; still a 15x shorter wait.

**coverage**: No assertion changes. Shutdown latency is not under test anywhere; servers still shut down the same way.

**risk**: Very low. Production behaviour is unchanged (default 0.5; ThreadingMixIn has no service_actions, so the poll interval only affects how soon shutdown() returns).

**effort**: small

**prototype diff**: `longtail__fast-server-shutdown.diff`

## Proposal: sleeps-to-events — Event-driven waits in test_dashboard_http, test_narrator (virtual clock), test_child_stdin, test_ptt, test_refresh_staging

**change**: - test_dashboard_http: replaces sleep 0.5/0.3/0.3 after the first-client-hook connects. `settle(before)` joins every thread started since a snapshot taken before connect(), repeating for any threads those start. The hooks' threading.Timer threads have then run, so 'once', 'waited for both windows' and 'muted: silent' are decided by the threads finishing, not by a time window.
- test_narrator: the Node run script installs a virtual clock (setTimeout, setInterval, clear*, Date.now). Timers fire in time order whenever the microtask queue has drained, checked from a real setImmediate, so every line still 'takes' DUR = 80 ms but no real time passes.
- test_child_stdin: the child signals an Event just before its blocking readline. The job waits for it plus a 0.25 s margin, instead of 1.0 s from thread start. The test waits on an Event set by the reader thread instead of polling every 0.5 s.
- test_ptt: waits for the loop's state changes (polled every 5 ms). A tap is released as soon as the loop sees the key; a hold lasts 0.25 s after the loop saw it, so measured duration ≥ 0.25 > min_seconds 0.2 is guaranteed. The coach answer polls drop from 0.1 s to 5 ms.
- test_refresh_staging: kb_refresh.time is replaced by a shim whose strftime is 2 s ahead (names carry the time to the second), plus a 0.05 s sleep so file mtimes stay ordered. Real file times are kept, so promote() takes the same 'install the legacy file' path as before.

**measured**: Median wall over 3–5 runs, before → after: test_dashboard_http 2.36 → 0.44 (with the shutdown change), test_narrator 1.88 → 0.31, test_child_stdin 1.57 → 0.38, test_ptt 1.47 → 0.53, test_refresh_staging 1.24 → 0.20. About 5.8 s of wall from these sleeps, CPU unchanged. Narrator event logs are identical to the real-time run; timings become exact (tagged_ms 160 vs 162). Mutations caught:
- dashboard_http: hooks not cleared, hook run at once, hook fired twice, mute ignored.
- narrator: early resolve; cancel dropping the coach line, with the same message as the original.
- ptt: min_seconds wrong both ways.
- refresh_staging: a fixed name stamp; promote picking the oldest.
All passed under 6 CPU burners at -j8.

**saving_s_linux**: 5.8

**windows_note**: These sleeps cost the same on Windows. The narrator virtual clock also removes a Windows flakiness risk: real 10–20 ms sleeps become 15.6–31 ms ticks there, close to the test's <20 ms bounds. test_child_stdin guards a Windows-only hang, so its 0.25 s margin matters only there. The margin is now measured from the moment the main thread is at its read (previously 1.0 s from thread start), which is more robust than before.

**coverage**: Every assertion is kept. Where a check is negative ('only once', 'muted', 'queued tests never start'), the wait joins the mechanism that could produce the event (the timer threads) or keeps a dwell. ptt's hold and tap durations now have guaranteed bounds instead of sleep-based ones.

**risk**: Low. The narrator clock assumes narrator.ts uses only setTimeout, setInterval and Date.now (true today; performance.now is not virtualised). refresh_staging relies on the second build's mtime being later by 50 ms, which holds on NTFS and ext4.

**effort**: small

**prototype diff**: `longtail__sleeps-to-events.diff`

## Proposal: test-link-row-paint — test_link: read the textures in one Lua call and paint rows, not pixels (byte-identical frames)

**change**: tests/test_link.py screen_frame(): a Lua helper returns the shown, non-skip textures as one string of '%.17g' fields (r, g, b, x, y, w, h). It iterates with pairs(), the same next() order as G.textures.values(), so later textures still paint over earlier ones. Python paints each texture row with one bytearray slice assignment. Any row not wholly inside the buffer falls back to the old per-pixel slice writes; one 1x1 texture at y = 357 in a 73-row screen used to append bytes past the end, and still does. This is test code only.

**measured**: CPU 4.0–4.6 s → 1.17–1.30 s (5 runs, median 1.26). All 69 painted frames were compared byte for byte with the per-pixel version (identical). Mutation caught by both versions with the same message: swapping a strip cell's colour channels in Strip.lua gives 'frame did not decode'.

**saving_s_linux**: 2.8

**windows_note**: Pure Python and lupa CPU, so the same gain is expected on Windows (CI 3.7 s → about 1 s).

**coverage**: The image handed to protocol.decode_image is byte-identical for every frame, so every assertion sees the same input.

**risk**: Very low. %.17g round-trips doubles exactly, and the per-pixel fallback keeps edge cases exact.

**effort**: small

**prototype diff**: `longtail__test-link-row-paint.diff`

## Proposal: run-all-interrupt-scratch-tree — test_run_all_interrupt: interrupt a copy of run_all.py on a scratch tree of waiting tests, not 3 s of real navmesh

**change**: tests/test_run_all_interrupt.py copies tests/run_all.py into a temp tests/ folder with 6 test_long_N.py files. Each writes a 'started' marker and then sleeps 120 s. The test runs the copy at -j 2, waits until two markers exist (polled every 20 ms) plus 0.2 s, and sends SIGINT to the process group. It asserts the same things as before (exit 130 within 15 s, 'interrupted:', no Traceback), plus a new check that after a 0.3 s dwell only the two running tests ever started.

**measured**: Wall 3.18 → 0.67 s; CPU 3.79 → 0.20 s (3 runs). Mutation: run_all with cancel_futures=False. The new test fails ('still running 30 s after Ctrl+C'); the original passes that mutation, because its queued tests finish quickly.

**saving_s_linux**: 3.6

**windows_note**: No gain on Windows or CI: the test is POSIX-only and skips there. Its value is Linux CPU (3.6 s) and a stronger check.

**coverage**: It exercises the current run_all.py (copied at test time). Same assertions, plus 'queued tests never start' from its own docstring, which the old version did not actually check.

**risk**: Low. The scratch tree doesn't depend on any real test's runtime.

**effort**: small

**prototype diff**: `longtail__run-all-interrupt-scratch-tree.diff`

## Proposal: release-notes-fewer-git-spawns — test_release_notes: no automatic git maintenance, rev-parse only when the sha is used

**change**: tests/test_release_notes.py git(): add `-c maintenance.auto=false -c gc.auto=0`. Each `git commit` otherwise starts `git maintenance run --auto`, 11 times. commit(subject, sha=False) calls rev-parse only for c2, c3 and c4.

**measured**: Successful process spawns 66 → 40. Linux wall unchanged within noise (0.44 vs 0.44 s; git spawns are about 5 ms here).

**saving_s_linux**: 0

**windows_note**: On Windows each git spawn costs tens of ms plus an antivirus scan; CI shows this test at 3.5 s vs 0.5 s here. 26 fewer spawns should be about 1–2 s.

**coverage**: Same repository history (same trees and subjects), same script run, same asserts.

**risk**: Very low.

**effort**: small

**prototype diff**: `longtail__release-notes-fewer-git-spawns.diff`

## Proposal: warm-worker-runner — (Evaluated, not recommended yet) Run small tests in warm worker processes instead of one interpreter per test

**change**: An optional run_all mode: W long-lived workers import the common standard-library and third-party modules once (ssl, http, email, typing, inspect, sqlite3, lupa, numpy), then run each test with runpy. Isolation per test:
- Restore sys.path, argv, os.environ and cwd.
- Drop every module imported during the test.
- Capture output at fd level; on Windows also SetStdHandle, because subprocess children take GetStdHandle, not fd 1.
- Recycle the worker when a test leaves threads alive or crashes it.
- Keep an opt-out list for tests that need a process of their own (run_all_interrupt, signal or stdlib patchers).
On POSIX a fork-per-test server gives perfect isolation, but there is no fork on Windows.

**measured**: Standalone prototypes, not wired into run_all (lt_tools/inproc.py and forkrun.py), on the 92 tests under 1 s with original code; baseline is the sum of separate processes, 26.2 s CPU / 28.0 s wall:
- In-process: 17.0 s CPU / 18.7 s wall (−35%). 1 false failure (test_coach_start sees coach threads leaked by an earlier test); 4 tests left 6 threads.
- Fork: 18.5 s CPU (including the parent's imports), 0 mismatches.
Per-test overhead is about 85–100 ms: 20 ms start-up plus 62 ms of standard-library imports on average. Upper bound about 9–10 s CPU per full run, about 2.5 s wall at -j4.

**saving_s_linux**: 9

**windows_note**: Fork is POSIX-only, so the fork variant gives the dev PC and CI nothing. The in-process variant works on Windows but needs SetStdHandle capture and thread-leak recycling. CI timings of trivial tests (0.0–0.2 s) suggest the runner's per-process cost is also around 0.1 s, so the gain there is likely similar (unmeasured).

**coverage**: With module dropping and recycling on leaked threads, each test still runs whole, but no longer in a fresh interpreter. Cross-test contamination through standard-library state is possible (one case observed). It cannot change a test's assertions, but it could in principle mask or cause a failure. That is why it ranks below the concrete fixes.

**risk**: Medium. It changes the execution model of every test, adds Windows-specific capture code, and needs ongoing care for the opt-out list.

**effort**: large

## Proposal: duration-ordered-scheduling — (Not prototyped) Order run_all by recorded durations (longest first) instead of the hand-kept _HEAVY list

**change**: run_all.py writes each test's wall time to a durations file in the temp directory (or a gitignored tests/.durations.json) and orders the next run longest-first, falling back to _HEAVY when there is no history (fresh CI checkout). Until then, refresh _HEAVY by hand: after these changes test_desktop_main (0.7 s) and test_models (0.6 s) no longer belong; test_rxp_explorer (45 s, today started at t = 22 s and finishing last) and test_rxp_routes (5 s) do.

**measured**: Not prototyped. The baseline already shows the cost of a stale list: test_rxp_explorer started at 22 s and ended last, at 67.9 s.

**saving_s_linux**: 0

**windows_note**: Platform-independent. CI has no history between runs unless cached, so _HEAVY should still be kept current there.

**coverage**: Only the order changes.

**risk**: Very low.

**effort**: small

## Proposal: fixture-kb-cache — (Evaluated, not recommended) Per-run cache of tests/kb/fixture.build keyed by content hash

**change**: Build the fixture project root and kb.db once per run into a cache directory keyed by a hash of the fixture files and the knowledge, tools and wowfiles code, using an atomic rename for concurrency. Tests would copy it.

**measured**: fixture.build costs 0.37 s CPU (0.45 s wall) here and is used by 6 tests, so at most about 2 s CPU. After the fsync fix, its Windows cost (116 fsyncs → 8) is mostly gone too.

**saving_s_linux**: 1.5

**windows_note**: Most of the Windows benefit is already captured by sqlite-schema-one-transaction.

**coverage**: Equivalence is hard to guarantee: kb meta records absolute root and rxp_source paths, tests write into their kb (live_logs opens it read-write), and staleness logic compares roots.

**risk**: Medium for little gain. Not worth it.

**effort**: medium

