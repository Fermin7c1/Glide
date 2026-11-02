# runner

## Summary

Where the time goes in a full run (this box, 4 CPUs, 112 tests): one run's wall time can never be shorter than its longest test (test_navmesh, 54 s run alone) or than the total CPU divided by the cores (173.5 CPU-s / 4 = 43 s). So the floor is 54 s against today's 66-68 s on a quiet box, and the runner itself costs only about 12-14 s. Two separate problems cause that:

(a) The longest-first list in the runner is out of date. test_rxp_explorer (45 s) starts 22-43 s into the run and ends 3.6-13 s after navmesh. That tail showed up in all 6 baseline runs I timed.

(b) Just reordering is not enough. In measured runs, longest-first alone started navmesh, explorer, pyright (about 2 cores) and test_rxp_routes together at t=0. navmesh then lost CPU and the gain disappeared: fitted wall with no other load on the box was 67.8 s, against 65.7 s for the current runner. What works is longest-first plus running the tests the run length depends on at normal priority and the rest one step below:
- quiet-box runs: 61.3 / 61.5 / 61.3 s against 68.2 s for the current runner;
- fitted with no other load: 59.7 s against 65.7 s;
- extra wall per CPU-second used by other processes: 0.135 s against 0.198 s;
- tail after navmesh: 0 s in 6 of 7 runs.

The simulator I used (each running test gets a fair share of the CPUs) reproduced the CI log within 0.1 s (59.6 s simulated, 59.7 s actual). It also showed that timings recorded from a Linux box make CI slower (67.9 s): the timings file has to be per machine. Saving per full run: about 6.5 s here and 5.5 s on CI with longest-first alone; up to about 14 s on CI if its long tests are partly waiting, which only Windows CPU numbers can confirm.

For iteration, the bigger lever is a fast loop (tests/affected.py plus run_all.py --changed). It finds each test's dependencies statically (imports, plus the files and folders the test and the code it imports name) and runs only the tests a change can reach. I checked it against strace: it covers 100% of the 5,212 test-to-repo-file reads that strace recorded across all 112 tests. A naive version covered 85.7%, which is why the check was worth doing. Estimated wall per edit:

| Edit | Tests run | Wall |
|---|---|---|
| Full run | 112 | 55.8 s |
| Renderer (desktop/src/renderer) | 13 | 3.2 s |
| Main process (desktop/src/main) | 15 | 6.8 s |
| Addon Lua | 11 | 4.3 s |
| Engine .py, median | 39 | 12.2 s (pyright's time) |
| Coach screen (companion/static) | 28 | 12.1 s |
| knowledge/, tools/, wowfiles/ | ~70 | ~55 s |
| Last 40 commits, median | 37 | 12.6 s |

The knowledge/tools/wowfiles rows save nothing because navmesh reaches them statically through kb_refresh. In a real check, a mutated renderer function was caught by a 4.1 s run (14 tests).

pyright (test_types) costs about 14 s wall and 26 CPU-s whatever I try:
- About 12 of those CPU-s are V8 compiling pyright's own code on helper threads; without that compiler the main thread doubles to 29 s.
- --threads 2 and --threads 4 cost 41 and 57 CPU-s for the same wall.
- Splitting companion/ into separate pyright runs makes it slower, because app.py imports all 58 engine files and the inference over them is shared work.
- Memory and code-cache flags change nothing.
- Watch mode re-checks in 3-9 s but needs a background process.
- Checking only the changed files plus everything that imports them gives 9 s wall / 17 CPU-s.

Pinning `--pythonplatform Windows --pythonversion 3.12` makes the result identical on every OS (it now passes on Linux, where it used to fail with 14 Windows-only errors).

golden.py check --portable takes 2.1-2.4 s here, mostly the desktop probe's `npm run build`, and 8.9 s on CI. On the dev PC the routine checks run every golden probe twice (test_golden inside run_all, then golden.py check) and build the desktop app three times.

The -j default of min(8, cpu_count) is right. The run is navmesh-bound at -j8 on any core count. If navmesh and explorer get fast, pyright (about 12-14 s) becomes the floor at 8+ cores. Peak memory is about 1.5 GB at -j8 and 2.2 GB at -j16.

Measurement noise: another investigator used 5-221 CPU-s during my runs, so every A/B run logs the box's CPU used by other processes and I compare by regression against it and by quiet runs.

Files are in /tmp/claude-0/-home-user-Glide/4ddc3f14-3355-571d-9bad-778daa3d02a0/scratchpad/perf/rt/:
- final_files/ (run_all.py, affected.py, test_types.py, ci.yml, CONTRIBUTING.md, .gitignore)
- final.diff and diff_*.txt (the per-proposal diffs)
- psim.py, psim2.py (simulators)
- ab2_log.txt (A/B runs)
- trace_deps.json and validate_map.py (strace ground truth and the check against it)
- fastloop_final.txt (fast-loop numbers)

The scratch worktree has been removed and /home/user/Glide is untouched.

## Profile

BASELINE (serial1.json, run alone): 112 tests, 180.1 wall-s, 173.5 CPU-s. navmesh 54.0 wall / 53.6 CPU, explorer 44.7/44.2, types 11.95/22.97, desktop_main 6.8/1.4, rxp_routes 5.7/5.7.

FLOOR (longest test vs total CPU / cores): 4 cores max(54.0, 43.4) = 54.0 s; 8 cores 54.0 s. With the contended -j4 numbers: max(62.7, 45.8) = 62.7 s.

SIMULATOR (each running test gets a fair share of the CPUs). Validation: CI log 59.6 simulated vs 59.7 actual, explorer start 29.5 vs 29.6; Linux baseline 64.4 vs 67.9.

| Policy | -j4, 4 cores | -j8, 4 cores | -j8, 8 cores |
|---|---|---|---|
| Current | 64.4 | 64.5 | 54.5 |
| Longest-first | 55.9 | 66.1 (too many tests at once slow navmesh) | 54.4 |
| + CPU budget | 54.4 | 54.4 | 54.0 |
| + budget + priority | 54.0 | - | - |

- Timings recorded under contention (noisy): longest-first + budget 66.1 (worse than current); + priority 54.0.
- CI per-test walls: current 59.6; longest-first with CI's own timings 54.1; refreshed _HEAVY 55.7; longest-first with Linux timings 67.9; + budget 43.7 (45.8 if CI tests are 1.5x more CPU-bound); floor 40.6-45.6.
- If navmesh and explorer drop to 8 s each: -j8/8 cores current 15.2, longest-first 12.6, floor 12.0 (pyright).
- Peak memory: -j4 0.9-1.1 GB; -j8 1.5 GB; -j16 1.9-2.2 GB. Largest test: types 437 MB.

REAL A/B (-j4, wall vs CPU-s other processes used during the run):
- Current: 68.2 (16), 83.4 (93), 89.2 (109), 108.7 (221). Fit: 65.7 + 0.198 x others. Tail after navmesh 3.6-13.1 s; explorer starts 22-43 s.
- Longest-first only: 78.8 (60), 88.1 (111). Fit: 67.8 + 0.182 x others.
- Longest-first + priority: 61.3 (18), 78.0 (102).
- Longest-first + budget + priority: 61.3 (8), 61.5 (5), 62.8 (43), 64.0 (36), 71.6 (75). Fit: 59.7 + 0.135 x others. Tail 0 in 6 of 7 runs.
- CPU used by the whole suite: 184-209 CPU-s in every policy.

PYRIGHT (`pyright --outputjson companion`, median of 3):
- Plain: 14.0 wall / 26.4 CPU.
- --threads 2: 13.2 / 41.5. --threads 4: 15.4 / 57.5.
- V8 memory flags (semi-space 16-128, single-threaded GC, --v8-pool-size=1, compile cache): 13.5-16.3 wall / 23.6-29 CPU, no gain.
- Per thread: main 12.7 s plus 4 V8 helper threads (code compilation) at about 3.1 s each; without that compiler the main thread takes 29 s.
- Its own --stats: Check 8.5 s of 10.7 s; app.py's check 4.2 s; 305 files parsed.
- Subsets: app.py alone 6.7 / 12.8; app.py plus everything that imports it 9.0-9.2 / 17-17.6; 57 files without app.py 13-14 / 25; app.py and the rest in parallel 15 / 38; 3-way split 17-24 / 47-51.
- Watch mode: first report 14.8 s, re-check 3.3-8.7 s.
- `--pythonplatform Windows --pythonversion 3.12` on Linux gives exactly the KNOWN error counts.

GOLDEN (--portable): 2.06 / 2.37 wall, 4.0 / 4.6 CPU. Desktop probe (`npm run build`) 2.25 / 4.0; addon probe 0.33; dashboard probe skipped here (no Edge). CI step 8.9 s.

FAST LOOP:
- Strace check: 5,212 (test, repo file) reads, 100% covered; a naive version covered 85.7%. No test reads the prebuilt desktop/dist.
- Mutation check: comboOf mutated in rxp-model.ts; 14 tests selected; caught by test_rxp_conditions; 4.1 s wall.
- Building the dependency map: 3.2-3.5 s cold, 0.09-0.10 s warm (cache).

## Proposal: fast-loop-changed — Fast loop: run_all.py --changed runs only the tests a change can reach (static map tests/affected.py, checked against strace)

**change**: Add tests/affected.py, a new dev-only file (pure Python ast + regex, plus git). For each tests/**/test_*.py it computes a set of dependencies, over-approximated:
- Python imports, transitively: every import statement in the file, resolved against companion/, the repo root, tools/, tests/, tests/kb/ and tests/golden/. A name found in several folders counts for all of them. Also covered: importlib.import_module / __import__ with a constant name, or with a constant prefix ('knowledge.sources.' + name), which pulls in that whole package; and `python -c "import x"` code strings.
- Paths the code names: os.path.join / pathlib chains folded from __file__, ROOT, HERE and module constants (with per-function scoping and for-loops over constant tuples), string literals that are repo paths, and unique file names. A chain with an unknown tail depends on every file of the folder before it.
- Folder reads: listdir / glob / walk / copytree / iterdir, and literal glob patterns such as 'wowfiles/**/*.py'. These depend on the folder's files, not on what those files import.
- A folder passed on a command line (subprocess.run([...]): pyright companion, luacheck addon/Glide) is code: its files and everything they import.
- tsconfig include/files; relative TS/JS imports ('./x.js' resolves to x.ts); fixtures/ and snapshots/ folders.
- test_lint depends on the whole repo.

A change to tests/run_all.py, tests/affected.py, the requirements files, pyproject.toml, .luacheckrc, desktop/package*.json or a tsconfig selects every test. So does any code file no test reaches.

Per-file results are cached in tests/.affected-cache.json (gitignored), keyed by size and mtime plus a hash of the repo file list.

In run_all.py, `--changed[=REF]` (default origin/main) filters the tests to affected(changed_files(REF)). The changed files are the merge-base diff plus untracked files. The runner prints a one-line note that the full run is still the check before a commit. `py -3 tests/affected.py --why FILE` explains a selection.

CI and the default full run are unchanged.

**measured**: Estimated wall at -j4 on 4 cores (simulator, run-alone timings; full run 55.8 s):
- Renderer TS edit: median 13 tests, 3.2 s (max 3.9).
- Main-process TS: 15 tests, 6.8 s.
- Addon Lua: median 11 tests, 4.3 s.
- Engine .py: median 39 tests, 12.2 s (pyright is the floor; jsonfile.py / settings.py reach 83 tests, 55.6 s).
- companion/static: 28 tests, 12.1 s.
- knowledge/, tools/, wowfiles/: about 70 tests, 55 s (navmesh is reached through kb_refresh's imports, so no saving there yet).
- Last 40 commits on main: median 37 tests, 12.6 s; 2 of 40 selected everything.

Real check: rxp-model.ts comboOf mutated; 14 tests selected and run in 4.1 s wall; test_rxp_conditions FAILs (caught).

Strace check: every test run under strace -f. 5,212 repo files opened, 100% covered by the static map. Earlier versions of the rules covered 85.7% (missed engine data files such as settings_manifest.json, params.json, companion/static) and 99.7%; each miss led to a general rule, not a per-test exception.

Building the map: 3.2-3.5 s cold, 0.09-0.10 s warm.

**saving_s_linux**: 43

**windows_note**: Pure Python plus git, so it is portable. Paths are normalised to '/', and git prints '/'. The cold map build (290 Python files parsed) will be slower on Windows (file I/O, Defender), probably a few seconds. Warm runs take 0.1 s thanks to the cache. CI does not use it: CI keeps the full run, and so does every push to main (a release). The biggest win on Windows is the same as here: a renderer or addon edit runs about 13 tests instead of 112. On Windows each skipped test also saves process start-up and antivirus cost, so the gain is at least as large there. Dev-PC-only paths (Edge, data/kb.db) are covered by the imports, but the strace check could only see the Linux runs (tests that skip here read fewer files).

**coverage**: Opt-in. The full run (default, CI, release, before a commit) keeps every test and assertion unchanged. In fast-loop mode a test is left out only when none of its static dependencies changed. For all 112 tests, every repo file actually read at run time (strace -f, including node, tsc, pyright and Lua children) is inside the static set, so a change to an unselected file cannot change those tests' behaviour as observed here. Unreachable code changes, and changes to config, the runner or requirements, fall back to running everything. Statement of limits: static over-approximation can miss a dependency hidden in a computed path that no rule sees. The strace check is a regression tool for the rules, and the full run stays the gate.

**risk**: Its blind spots are rules of thumb. A future test that loads code through a fully computed path no rule sees could be skipped in fast mode until the full run. Mitigations: everything falls back when a code change is unreachable; the full run is still required before commit and on CI; the strace checker (validate_map.py + trace_deps.py) can be re-run after big test changes. Navmesh is over-selected for knowledge/tools edits, which is safe but slow.

**effort**: medium

**prototype diff**: `runner__fast-loop-changed.diff`

## Proposal: scheduler-lpt-critical-priority — run_all.py: longest-first from per-machine recorded timings, the tests the run's length hangs on at normal priority, wait-bound tests counted by real CPU use

**change**: In tests/run_all.py:

(1) Recorded timings. After each run, write tests/.timings.json (gitignored) with {test: {wall, cpu}} for the tests that passed or skipped. Merge with older entries and prune tests that no longer exist. A recorded time may rise at most 25% per run (min(new, old*1.25)) but falls at once. This was needed: one run on the noisy box recorded navmesh at 92 s wall / 62 CPU-s, and taking that at face value made over-admission worse. Write via tmp + os.replace, best effort.

(2) Order. Longest recorded wall first; tests with no record go first of all. With records for less than half the tests, fall back to _HEAVY, refreshed to: golden, navmesh, rxp_facts, rxp_explorer, dashboard_page, kb/rxp_updates, rxp_routes, types, desktop_main, models. Today's list misses explorer, facts and routes.

(3) Priority. The tests whose recorded wall is at least half of the run's floor (max(longest, sum cpu / jobs)) start at normal priority; every other test starts below normal (Windows: creationflags=BELOW_NORMAL_PRIORITY_CLASS, inherited by its children; POSIX: os.setpriority(pid, 10) right after Popen).

(4) CPU budget (separable stage 2). A test 'uses' cpu/wall cores (1 when unknown). The next test starts while the running tests' sum plus min(its own, 1) is at most -j. At most 2*j tests run at once.
- CPU is measured with os.wait4 rusage on POSIX.
- On Windows the test is put in a job object (ctypes QueryInformationJobObject) so CPU used by node, pyright and Edge adds up. Any failure gives cpu=None, which means 'CPU-bound'.

(5) Main loop. The main thread admits tests and waits with wait(timeout=0.5, FIRST_COMPLETED), so Ctrl+C reaches it on Windows too and nothing starts after it.

Also:
- -j 1 stays strictly one at a time, in collection order.
- The output format is identical.
- `--changed` filters the tests (see the fast-loop proposal).
- In ci.yml, actions/cache stores tests/.timings.json (key per run, restore-keys prefix) so runners keep their own timings. The pin is a placeholder: use a real release SHA.
- CONTRIBUTING documents -j as 'about N cores busy'.
- Default -j stays min(8, cpu_count).

**measured**: Real A/B at -j4 on this 4-CPU box, wall in s (CPU-s used by other processes during the run):
- Current runner: 68.2 (16), 83.4 (93), 89.2 (109), 108.7 (221). Fit 65.7 + 0.198 x others. A 3.6-13.1 s tail after navmesh in every run.
- Longest-first only: 78.8 (60), 88.1 (111). Fit 67.8 + 0.182 x others. No gain: navmesh is slowed because explorer, pyright and routes all start at t=0.
- Longest-first + priority: 61.3 (18), 78.0 (102).
- Longest-first + budget + priority: 61.3 (8), 61.5 (5), 62.8 (43), 64.0 (36), 71.6 (75). Fit 59.7 + 0.135 x others. Tail 0 in 6 of 7 runs. Total suite CPU unchanged (184-209 CPU-s).

On a quiet box: 68.2 to about 61.4 s (-6.8 s, -10%). The remaining gap to the 54 s floor is navmesh's own slowdown when 4 CPUs are busy (61 s vs 54 s alone).

Simulator (validated on the CI log within 0.1 s):
- 4 cores: current 64.4, longest-first 55.9, + budget + priority 54.0 (floor 54.0).
- CI: current 59.6, longest-first with CI's own timings 54.1, refreshed _HEAVY 55.7, Linux timings 67.9 (worse), + budget 43.7-45.8 (only if CI's long tests are partly waiting; unverified).
- If navmesh and explorer become fast (8 s each), -j8 on 8 cores: current 15.2, longest-first 12.6, floor 12.0.

Regression guard: test_run_all_interrupt passes; a mutated runner that swallows Ctrl+C makes it FAIL (returncode 1 instead of 130).

**saving_s_linux**: 6.5

**windows_note**: Priority uses BELOW_NORMAL_PRIORITY_CLASS. That is near-strict priority on Windows, so the critical tests keep their cores. If WoW or the player's app runs at normal priority, the below-normal tests yield to it: the game stays smooth, but the run gets longer while the PC is busy. Same effect here: 71.6 s with 75 CPU-s of other load.

The ctypes job-object CPU meter is UNTESTED on Windows (written to the documented API with argtypes and restypes set). Any failure gives None, and the runner then behaves as longest-first + priority, which measured about the same as the full design at low load. The decaying-min timings stop slow, contended runs from inflating the estimates.

Timings must be per machine. Windows CI has its own profile (rxp_facts 30 s on CI vs 1.6 s here), and Linux timings would make CI slower (67.9 s simulated). So the file is gitignored and CI caches its own (restore and save about 1-2 s each). Until the first cached run, the refreshed _HEAVY list applies (55.7 s simulated on CI vs 59.6).

The wait(timeout=0.5) loop also fixes a Windows-only gap: the old as_completed() wait could not be interrupted, and Ctrl+C took effect only when a test finished.

**coverage**: Every test still runs, in its own process, with the same command line, cwd, environment, assertions and output. Only start order, OS priority and how many tests run at once change. -j 1 is unchanged (serial, collection order). The Ctrl+C regression test still passes, and a mutation of the new Ctrl+C path is still caught. Skip and fail reporting is unchanged.

**risk**: Tests with timing-based waits (HTTP, push-to-talk, narrator) run below normal priority beside CPU-heavy critical tests. On a heavily loaded PC they could approach their timeouts; none did in 7 full runs here, including one with 221 CPU-s of other load on the current runner and 102 on the new one. The budget part depends on measured CPU per test: without priority, noisy estimates made it worse than today (66.1 s simulated), so ship budget only together with priority, or drop it (stage 1 = longest-first + priority, no ctypes). The CI action pin must be filled in. Two concurrent runs in one checkout race on .timings.json: the last writer wins, and the file is only a hint.

**effort**: medium

**prototype diff**: `runner__scheduler-lpt-critical-priority.diff`

## Proposal: golden-dedup — Stop running the golden probes twice on the dev PC; fold the portable tier into run_all's pool on CI

**change**: (a) Process change, CLAUDE.md / CONTRIBUTING. On the development PC, test_golden.py (run by run_all) already calls golden.main(['check'] + every snapshot), which is exactly what `golden.py check` runs. So the routine check list (run_all, then golden.py check, then npm run build) runs every golden probe twice and builds the desktop app three times: npm run build, plus probe_desktop's `npm run build` inside both golden runs. Document that a green run_all with test_golden 'ok' is the golden check. Keep golden.py for `record`, single probes, and `--portable` on machines without data.

(b) CI. Make test_golden.py fall back to the portable tier when data/kb.db or glide.db is missing, instead of exiting 77: run golden.main(['check'] + the portable probes), then exit 77 with 'machine tier skipped; portable tier passed'. Probes that report {'skipped': ...} (no Edge or node) must count as skipped, not CHANGED. Then drop the separate 'Golden masters (portable tier)' step from ci.yml. The probes (Edge rendering, npm build) then overlap with the other tests in the pool instead of running for 8.9 s after the suite.

**measured**: Not prototyped. Measured parts:
- golden.py check --portable here: 2.06 / 2.37 s wall, 4.0 / 4.6 CPU-s. probe_desktop (npm run build) is 2.25 s / 4.0 CPU-s of that; probe_addon 0.33 s; probe_dashboard skipped (no Edge here).
- CI golden step: 8.9 s, sequential after the 59.7 s test step.

Estimates:
- Dev PC: saves one full golden run per check cycle. Its length can't be measured here (no data); test_golden is first in _HEAVY there, so it is probably the longest test.
- CI: about 6-9 s off the job; the 8.9 s step goes into the pool, which has idle slots while navmesh, facts and explorer run.

**saving_s_linux**: 2.2

**windows_note**: The largest gain is on Windows. The dashboard probe drives headless Edge, and `npm run build` there pays for npm's start-up plus three tsc runs. Folding the portable tier into the CI pool takes a sequential 8.9 s step off every push and every release (the release job waits on this CI run). Edge needs no special handling in the pool: the probe already picks a free port.

**coverage**: Same probes and snapshots, same comparison (first_difference). Only the duplicate execution disappears. On CI the portable tier still runs on every push, inside run_all. A probe that cannot run (no Edge) must not silently pass on CI: CI has Edge. A machine without Edge would report 'skipped' instead of today's spurious CHANGED.

**risk**: It changes when a golden failure surfaces (inside run_all's summary instead of a separate step). The 'skipped probe' handling must not hide a probe that crashes. golden.py's command line stays as it is.

**effort**: small

## Proposal: types-platform-pin — test_types: pin pyright to the engine's platform (--pythonplatform Windows --pythonversion 3.12)

**change**: Pass '--pythonplatform', 'Windows', '--pythonversion', '3.12' to pyright in tests/test_types.py. That matches the bundled engine (desktop/scripts/fetch-python.ps1 ships 3.12.10 for Windows). The ratchet then means the same on every machine.

**measured**: On Linux without the pin, test_types FAILS: capture.py 7 errors (3 allowed), overlay.py 4 (0), ptt.py 3 (0); app.py drops to 12 (13 allowed). With the pin, the per-file counts equal KNOWN exactly and the test passes. Time unchanged: 14.4 s wall in the suite, about 26 CPU-s (CPU noise ±2 s).

**saving_s_linux**: 0

**windows_note**: Nothing changes on Windows or CI: pyright already used those values there, picked up from the host. It removes a permanently red test for Linux and cloud agents and any non-Windows contributor, which is iteration friction even though it saves no CPU.

**coverage**: Same checks, same KNOWN caps. The test now always checks the platform the engine actually runs on, so it cannot drift with the host's Python or OS.

**risk**: If the bundled Python moves to 3.13, the pin must move with it (one line, next to the fetch script's version).

**effort**: small

**prototype diff**: `runner__types-platform-pin.diff`

## Proposal: pyright-closure-subset — (Optional, fast loop only) pyright on the changed engine files plus everything that imports them

**change**: When run_all --changed is used, pass test_types the changed companion/*.py files plus every engine file that imports them, directly or indirectly (from affected.py's reverse graph). Check only those files and compare KNOWN only for the files checked.

Why that is enough: a file's diagnostics depend only on the file and what it imports, so files outside that set are unchanged from origin/main, which passed CI on Windows.

Any change to pyproject.toml, requirements or test_types.py runs the full check. Full runs and CI always check the whole of companion/.

**measured**: Measured by hand, n=3, with origin/main's results as the reference:

| Run | Wall | CPU |
|---|---|---|
| Full companion/ | 14.8 s | 27.3 s |
| Change to rxp_explorer.py (5 files) | 9.2 s | 17.6 s |
| Change to state.py (7 files) | 9.0 s | 17.0 s |
| app.py alone | 6.7 s | 12.8 s |

app.py imports all 58 engine files, so it is in every set and sets the floor. Rejected alternatives:
- --threads 2 / 4: 13.2 / 15.4 s wall, 41.5 / 57.5 CPU-s.
- Splitting companion/ in 2 or 3 parallel runs: 15-24 s wall, 38-51 CPU-s (the type inference over the shared imports is repeated in each).
- Node memory flags and the code cache: no change.
- About 12 CPU-s are V8 compiling pyright's own code on helper threads; without that compiler the main thread takes 29 s.
- Watch mode: 3.3-8.7 s re-check, but needs a background process.

**saving_s_linux**: 5.8

**windows_note**: Same ratio expected on Windows (it is node). It only matters in the fast loop for engine edits, where pyright is the floor: about 12 s of the 12.2 s median.

**coverage**: Full runs and CI keep the whole-folder check. In fast mode, the soundness argument is that pyright's diagnostics for a file depend on that file and what it imports. It does rely on origin/main being green (true for a pushed main).

**risk**: A second code path in a ratchet test. Low value until the fast loop is adopted. Ranked low on purpose.

**effort**: small

## Proposal: jobs-default — Keep -j default min(8, cpu_count); -j becomes a CPU budget, with at most 2*j tests at once

**change**: No code change beyond the scheduler proposal.

Why 8 is right:
- The floor is the longest test at any core count (54 s here, 32 s on CI). The simulator gives the same result at -j8 on 8 or 16 cores (54.0 s).
- More tests than cores without priority hurts: -j8 on 4 cores gives 64.5 s current and 66.1 s longest-first.
- Once navmesh and explorer are fixed, the floor becomes pyright (about 12-14 s) at 8+ cores: 12.6 s simulated at -j8 on 8 cores against a 12.0 s floor. 16 cores would not help.
- Memory is not a limit: peak about 1.5 GB at -j8 and 2.2 GB at -j16 (largest test: test_types 437 MB).
- cpu_count counts hyperthreads; capping at 8 leaves room for WoW and the player's app on the dev PC.

Tests that mostly wait (desktop_main 6.8 s wall / 1.4 CPU-s, dashboard_http 2.3 / 0.25, child_stdin 1.6 / 0.13, refresh_staging 1.2 / 0.07, models, narrator, ptt, conversation: about 15 s of idle slot time in total) are better handled by the CPU budget than by raising -j.

**measured**: Simulator only (fair-share model; validated on CI within 0.1 s). See the profile.

**saving_s_linux**: 0

**windows_note**: Windows CI runs 4 vCPU (-j4). The dev PC count is unknown; 8 is the cap either way. On Windows, extra tests at once also overlap the per-test process start-up and antivirus scans, which the budget lets happen without hurting the critical tests.

**coverage**: Unchanged.

**risk**: None beyond the scheduler proposal.

**effort**: small

