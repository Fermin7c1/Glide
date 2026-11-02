# ci

## Summary

Releases spend most of their time waiting, not computing. I measured 24 runs since Oct 31 that actually built a release, from the GitHub Actions job and step timestamps. A release takes a median 295 s end to end (p90 380 s), made of three parts in a row:
- the test job: 150 s median
- a 3 to 9 s gap
- the release job: 156 s median

The release job only starts after the tests finish, yet it never needs their result until its very last step (`gh release edit --draft=false`, 2 s). Everything before that only touches a draft, which installed apps never see: electron-updater asks `/releases/latest` and the releases feed, and a draft has no tag (`untagged-...`).

**1. Overlap the build with the tests (biggest win).** Rename the release job to `build` and drop its `needs: test`. Add a small `publish` job on ubuntu-latest with `needs: [test, build]`, guarded so it publishes only the draft this run built. Modelled on the 24 real runs, the median drops from 295 s to 167.5 s (p90 380 s to 206 s): about 2.1 min saved per release.
- Release safety holds. The workflow-level concurrency group 'release' still serializes whole runs, so two runs cannot claim the same version. A failed, cancelled or skipped test job skips publish.
- A draft left by failed tests is taken over by the next run, as the workflow already does for a failed build. I checked electron-builder 26.18's `gitHubPublisher.js`: it reuses an existing draft, and on "422 already_exists" it deletes the old asset and uploads again.
- The new guard also closes a hole today's design has. If an old run's failed test job is re-run after a newer run published, today's flow would compute a new version for the old commit and could publish older code as the latest release.

**2. Cache the bundled Python** (key: runner image + hash of `fetch-python.ps1`). That step is 34.5 s median: about 14 s just starting Windows PowerShell 5.1 and its setup, about 19 s downloading and unzipping the embeddable zip, 6 s for pip and numpy. I rebuilt the same tree from the pinned files here: 1,456 files, 72 MB, 25 MB compressed. The CI logs show a 34 MB npm cache restored in 1.7 to 2.0 s, so a restore should take about 3 to 5 s.
- Alone it saves about 29 s per release.
- On top of item 1 it saves only about 6.5 s today (median 161 s), because the test job then becomes the critical path. The build job drops to about 126 s, level with the tests.

**3 to 5. Shorten the test job.**
- I replayed the 10 recent CI test logs through a model of run_all's 4-worker scheduler. It reproduces the real Tests step (72.3 s simulated against 71.5 s actual median).
- On CI, test_rxp_explorer (37.9 s) is not in `_HEAVY`, starts about 33 s in and ends last. Ordering `_HEAVY` longest-first by CI times gives 65.4 s (−7 s).
- Running the portable golden check (9 to 17 s, run after the tests today) alongside the tests saves about 9.6 s, even assuming a 25% slowdown while it runs.
- Running pip (19 s) and npm ci (10 s) at the same time is estimated at about −8 s.

Together these take the test job from about 150 s to about 124 s on every push (CI feedback −26 s). With items 1 and 2 the release median becomes about 140 s (p90 180 s): **about 2.6 min (53%) saved per release**. At about 12 releases a day that is roughly 30 min a day.

**Cheaper runner time (no latency change).** 22 of the 25 CI runs on main repeat the release's own test job for the same commit, and 16 of 43 commits were also tested on a branch first. Runner minutes on this public repo are free and these runs are parallel, so this costs no latency. It is a cleanup, not a speedup.

**Looked at and rejected:**
- **Caching site-packages:** about 6,900 small files (5,435 are pyright's stubs). Windows tar extraction of that many files likely costs about as much as pip's 14.5 s install.
- **Caching electron-builder's downloads:** the electron zip and NSIS take about 1 to 5 s; restoring about 120 MB from cache would cost about the same.
- **Bigger runners:** not available to a personal account's public repo.
- **Lower NSIS compression:** a player-facing trade-off, not worth it while the build is off the critical path (numbers below).
- **Sharding the tests over 2 runners:** −17 s for CI only; it stops helping releases once the build job (~126 s) is the critical path.

**Things to tell the player:**
- release.yml's header says the release environment has a required reviewer. Every release job since Oct 31 started 3 to 9 s after its tests, so no approval is being waited on. If a reviewer is re-enabled, design 1 asks for approval twice (build and publish). The alternative is a build without the token (`--publish never`, then upload-artifact) and a publish job that drafts, uploads and publishes; that puts about 15 s back on the critical path.
- "Re-run all jobs" on an old release run (today, and still with design 1) computes a new version and could publish an older commit as latest. A one-line check in the version step would stop it: `git merge-base --is-ancestor $SINCE HEAD`.
- Noticed in passing: `package.json` allows scripts for `koffi@3.3.3`, but 3.3.4 is installed, so npm warns that koffi's install script is "not yet covered by allowScripts" on every `npm ci`.

**What I could not run.** GitHub Actions can't run from here, pwsh isn't installed, and actionlint was blocked (external binary). So the PowerShell parts (Start-Job / Receive-Job error handling) were not executed. What was checked:
- every prototype parses as YAML;
- each of the five split diffs applies cleanly to origin/main;
- run_all.py passes ruff;
- the publish guard's bash ran against a fake `gh` in all 5 cases: correct draft publishes; a draft taken over by a newer run is refused; already published exits 0; a missing blockmap is refused; notes without a source are refused.

**How to roll out.** The ci.yml and run_all changes can be checked with a branch push (CI runs on every branch and releases nothing). release.yml is not in the release path filter, so pushing only that file to main releases nothing. The next real push, or a workflow_dispatch, exercises it.

Files are in /tmp/claude-0/-home-user-Glide/4ddc3f14-3355-571d-9bad-778daa3d02a0/scratchpad/perf/ci-data:
- the combined diff: /tmp/claude-0/-home-user-Glide/4ddc3f14-3355-571d-9bad-778daa3d02a0/scratchpad/perf/ci-data/prototype.diff
- the per-proposal diffs (each applies to origin/main on its own): the split subfolder, /tmp/claude-0/-home-user-Glide/4ddc3f14-3355-571d-9bad-778daa3d02a0/scratchpad/perf/ci-data/split
- the job logs, the scheduler model (/tmp/claude-0/-home-user-Glide/4ddc3f14-3355-571d-9bad-778daa3d02a0/scratchpad/perf/ci-data/sim.py) and the compression benchmark (/tmp/claude-0/-home-user-Glide/4ddc3f14-3355-571d-9bad-778daa3d02a0/scratchpad/perf/ci-data/lzbench.py)

The scratch worktree has been removed.

## Profile

**Release runs.** 35 runs since Oct 30. Of those, 24 succeeded, actually built, and had no approval wait:
- end to end: median 295 s, p90 380 s
- queue before the test job: 3 s
- test job: 150 s median (110 to 213)
- gap before the release job: 3 to 9 s
- release job: 156 s median (107 to 204)

**Release job steps (medians, 26 runs).**

| Step | Time |
|---|---|
| Set up job | 1 s |
| checkout (full history) | 8 s |
| Version step (5 gh calls + node) | 8 s (5 to 25) |
| setup-node | 8.5 s (its "Environment details" alone takes 4 to 16 s) |
| npm ci | 10 s |
| Bundled Python | 34.5 s (21 to 50) |
| Draft the release | 1.5 s |
| Build and upload | 72.5 s (50 to 113) |
| Publish | 2 s |
| post steps | 2 s |

Breakdown of one run (job 125081913520) from the log timestamps:
- **Bundled Python, 42 s total:**
  - Windows PowerShell 5.1 start, PSModulePath fix and Add-Type: 14.3 s
  - embeddable zip download, SHA check and unzip, plus the pip wheel: 18.8 s
  - pip itself: 1.2 s
  - numpy: 3.4 s
  - smoke test and cleanup: 1.3 s
- **Build and upload, 68 s total:**
  - tsc build: 1.6 s
  - electron-builder start and rebuild: 1.7 s
  - electron zip extraction: 5 s
  - asar, node modules and signing skips: 4 s
  - 7z LZMA2 of the app payload (`-mx=9 -md=1m -ms=off -mf=BCJ`): about 39.5 s
  - makensis: 4.5 s
  - blockmap: 5.3 s
  - upload of the 138,435,597-byte installer and its blockmap: 5.5 s

**Test job steps (medians).**

| Step | Time |
|---|---|
| checkout | 8 s |
| setup-python (52 MB pip cache restored in about 1.8 s) | 4 s |
| Python packages | 19 s (about 14.5 s of it is "Installing collected packages") |
| setup-node | 8 s |
| npm ci (294 packages) | 10 s |
| luacheck download, ruff, luacheck | about 3 s |
| Desktop build | 2 s |
| Tests | 72 s (40 to 129) |
| Golden masters (portable tier) | 10 s (9 to 17) |

**CI per-test times.** Medians over 10 Windows runs, sum 259 s:

| Test | Median | Range |
|---|---|---|
| test_navmesh | 47.8 s | 28.9 to 65.3 |
| test_rxp_explorer | 37.9 s | 20.9 to 40.2; not in `_HEAVY`, starts at about 33 s and ends last |
| test_rxp_facts | 22.4 s | 14.5 to 61.9 |
| test_dashboard_page | 22.2 s | |
| test_rxp_routes | 12.1 s | |
| kb/test_rxp_updates | 11.4 s | |
| test_types | 9.9 s | |
| test_desktop_main | 8.4 s | |
| test_link | 6.1 s | |

The lower bound for the Tests step is sum/4 = 62.5 s, and the longest single test is 48.5 s.

**Scheduler replay.** The 4-worker model gives 72.3 s against 71.5 s actual (median).

| Scheduling option | Simulated Tests step |
|---|---|
| Current order | 72.3 s |
| Proposed `_HEAVY` order | 65.4 s |
| Proposed order + golden alongside (pessimistic +25% while it runs) | 68.2 s, against 77.8 s with golden after the tests |
| Longest-first by median (LPT) | 63.8 s |
| 2 shards of 4 workers | 48.5 s |

**End-to-end model per real run** (24 runs, publish job counted as 9 s for queue, setup and gh):

| Scenario | Median | p90 |
|---|---|---|
| Today | 295 s | 380 s |
| Overlap | 167.5 s | 206 s |
| + Python cache | 161 s | 202 s |
| + test-job changes | 140 s | 179.6 s |
| Python cache alone, no overlap | 266 s | |

Derived job lengths: build job 154.5 s, or 126 s with the Python cache; test job with the CI changes 124 s.

**Cache sizing** (rebuilt here from the pinned, SHA-checked files; the embed zip hash matched `fetch-python.ps1`):
- bundled Python: 1,456 files, 72 MB, 25.1 MB gzip
- test site-packages: 6,898 files, 119 MB, 30.6 MB gzip; pyright alone is 5,435 files and 38 MB

**Compression proxy.** Per-file LZMA2 with a 1 MiB dictionary and the x86 BCJ filter (same parameters as electron-builder's NSIS payload), over 222 MB (Linux node binary + bundled Python + ruff.exe). Here "time" means CPU time.

| xz preset | Matches 7-Zip | Time | Output / input | Speed |
|---|---|---|---|---|
| 6 | -mx=9 with -md=1m | 109.1 s | 0.258 | 2.0 MB/s |
| 5 | | 99.0 s (−9%) | 0.259 | |
| 3 | | 54.0 s (−50%) | 0.278 (+8% size) | |
| 1 | | 31.6 s (−71%) | 0.284 (+10% size) | |

**Local run_all check.** Old and new `_HEAVY` orders, alternated, at load average 6.3 to 9.7 from another investigator: wall 97 to 106 s, so noise-dominated. CPU was the same for both (191 to 198 s user+sys). test_types fails on Linux, which is expected.

## Proposal: overlap-build-gated-publish — Build and upload the release to a draft while the tests run; publish in a separate job once tests and build both pass

**change**: .github/workflows/release.yml:
- Rename the `release` job to `build`, remove `needs: test`, and add job outputs `version` and `exists` taken from steps.ver.
- Remove the final `Publish the release` step from it.
- Add a `publish` job with `needs: [test, build]`, `if: needs.build.outputs.exists == 'false'` (GitHub adds `success()` to that automatically), `runs-on: ubuntu-latest`, `environment: release`.
- Its single bash step:
  1. `gh release view v$VER --json isDraft,body,assets`
  2. Refuse unless the notes' `source: <sha>` equals `$GITHUB_SHA`.
  3. Exit 0 if the release is already published.
  4. Refuse unless `latest.yml`, `Glide-to-60-Setup-$VER.exe` and its `.blockmap` are attached.
  5. `gh release edit v$VER --draft=false --latest`
- Keep the workflow-level `concurrency: group: release` unchanged: it is what keeps one run's version step, build and publish serialized against the next run.
- Recommended follow-up, for a hazard that exists today too: in the version step, fail when `$SINCE` (the last published source) is not an ancestor of HEAD (`git merge-base --is-ancestor`). That stops 'Re-run all jobs' on an old run from publishing older code as the newest release.

**measured**: Not runnable here (GitHub Actions). Modelled per run from the step timings of 24 real release runs that built:
- before: median 295 s, p90 380 s
- after: median 167.5 s, p90 206 s, computed as queue + max(test job, release job minus publish) + 9 s publish job
- saves about 128 s median per release

The guard script was run against a fake gh in 5 cases; each behaved as intended:
- correct draft: published
- draft taken over by a newer run: refused, exit 1
- already published: exit 0
- missing blockmap: refused
- notes without a source line: refused

The YAML parses and the diff applies cleanly to origin/main.

**saving_s_linux**: 0

**windows_note**: The build stays on windows-latest exactly as before. Publish moves to ubuntu-latest, which has gh, jq and bash preinstalled and starts faster than a Windows job. Nothing on the Linux box changes; the saving is about 2.1 min of wall time per release on GitHub's runners. Two jobs now reference the 'release' environment. No approval is being waited on today (3 to 9 s gaps since Oct 31), but if a required reviewer is re-enabled it would prompt twice. In that case use the variant where the build runs with `--publish never` and upload-artifact, and publish does draft, upload and publish (about +15 s on the critical path).

**coverage**: No check is skipped. The same reusable ci.yml test job runs on every release, and nothing becomes visible to players until it has passed:
- Installed apps only see published releases (electron-updater uses /releases/latest and the feed; a draft has no tag).
- Publish needs both jobs, so a failed, cancelled or skipped test or build skips it.
- The new guard adds safety. It publishes only the draft whose notes name this commit and only if every expected asset is attached. That blocks a re-run of an old run's failed jobs from marking an old or taken-over version as latest, which today's design could do.

**risk**: A test failure now leaves a fully uploaded draft. That is about 15% of runs: 5 of 35 failed since Oct 30. The next releasing run takes the draft over (same version, notes edited; electron-builder 26.18 reuses a draft release and replaces assets with the same name: on 422 already_exists it deletes and re-uploads, as verified in electron-publish/out/gitHubPublisher.js). If package.json is bumped by hand in between, the stale draft stays as an invisible orphan, as a failed build can leave today. RELEASES_TOKEN is used before the tests pass. That is the same trust as today: the same main-only environment and the same npm ci and build code holding the token.

**effort**: small

**prototype diff**: `ci__overlap-build-gated-publish.diff`

## Proposal: bundled-python-cache — Cache desktop/build/python (npm run python) keyed by runner image and fetch-python.ps1 hash

**change**: In release.yml's build job:
- A 'Runner image' step writes `${env:ImageOS}-${env:ImageVersion}` to GITHUB_OUTPUT.
- `actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9` (v6.1.0, a lightweight tag, so this SHA is the commit) caches `desktop/build/python` under the key `bundled-python-<image>-<hashFiles(desktop/scripts/fetch-python.ps1)>`.
- `npm run python` runs only on a cache miss.
- On a hit, a step runs the script's own smoke test (`build\python\python.exe -c "import sys, sqlite3, ctypes, winsound, json, zlib, numpy"`).

fetch-python.ps1 itself is unchanged, so the player's local `npm run python` behaves as before.

**measured**: Not runnable here. I rebuilt the cached tree from the pinned files; the embed zip's SHA-256 matched the pin. It is 1,456 files, 72 MB, 25.1 MB gzipped. The CI logs show the 34 MB npm cache restored in 1.7 to 2.0 s, so the estimated restore plus smoke test is about 4 to 6 s, against 34.5 s median (21 to 50) for `npm run python`:
- about 14 s Windows PowerShell 5.1 start, PSModulePath fix and Add-Type
- about 19 s download, SHA check and unzip
- about 6 s pip and numpy

Modelled per run: alone it saves about 29 s per release (295 to 266 s). On top of overlap-build-gated-publish it saves about 6.5 s today (167.5 to 161 s), because the test job becomes the critical path; the build job drops from 154.5 to about 126 s. It grows to about 29 s as the test job gets faster.

**saving_s_linux**: 0

**windows_note**: This is a Windows-runner-only saving. On a hit it skips the slow Windows PowerShell 5.1 cold start and the python.org download. The cache is scoped to main, saved only by a succeeded job, and evicted after 7 days unused (about 12 releases a day keep it warm). The runner image changes about weekly, so expect one miss (one normal 35 s run) per image update.

**coverage**: The installer's contents are what the script produced for that exact script version and runner image. On a cache hit:
- the pinned SHA-256 checks of the downloads ran when the entry was made;
- the Authenticode and 14.x checks of the copied VC++ DLLs ran at that same time, against the same image version, which is part of the key;
- the smoke test runs again on every hit.

The only behaviour that changes is that an image update between a cache save and a release could ship DLLs from that image until the next miss, and the image version in the key bounds this.

**risk**: Cache poisoning would need code running on main with the Actions runtime token. That is the same trust boundary as the npm ci already running in this job. A corrupt entry would fail the smoke test (red run, nothing published) or the electron-builder step. Changing the pip or numpy pins or the Python version changes the script, so the key misses.

**effort**: small

**prototype diff**: `ci__bundled-python-cache.diff`

## Proposal: golden-alongside-tests — Run the portable golden check as a background job during run_all instead of after it

**change**: In ci.yml, merge the 'Tests' and 'Golden masters (portable tier)' steps into one pwsh step:
1. `$golden = Start-Job -WorkingDirectory $PWD { python tests/golden/golden.py check --portable 2>&1 | ForEach-Object { "$_" }; if ($LASTEXITCODE) { throw ... } }`
2. `python tests/run_all.py; $tests = $LASTEXITCODE`
3. `Receive-Job $golden -Wait -AutoRemoveJob`, which prints golden's output and rethrows its failure under the runner's `$ErrorActionPreference = 'stop'`
4. `exit $tests`

**measured**: I replayed 10 Windows CI runs through the scheduler model, which reproduces the real Tests step (72.3 s simulated against 71.5 s actual). Golden after the tests: 77.8 s. Golden alongside, assuming everything runs 25% slower while golden runs (pessimistic): 68.2 s. That saves about 9.6 s on every CI run and every release, while the test job is on the critical path. The pwsh step itself was not executed (no pwsh or Actions here).

**saving_s_linux**: 0

**windows_note**: CI only (windows-latest, PowerShell 7's Start-Job -WorkingDirectory). The tail of run_all leaves 2 to 3 of the 4 vCPUs idle for about 20 s, which absorbs golden's 3 parallel probes (Edge, node, Lua). The dev PC is unchanged, since that is the separate `golden.py check` command.

**coverage**: The same command and the same snapshots run, and either failing fails the step, as before. Overlapping them is safe:
- The probes read only the repository and desktop/dist.
- No test writes desktop/dist; the tests compile TypeScript into temp dirs.
- gen-settings.js rewrites settings-manifest.ts only when its content differs, and the earlier 'Desktop build' step has already brought it up to date.

**risk**: More CPU contention during the overlap could slow test_navmesh, the critical test, by a few seconds; the 25% slowdown model already accounts for that. If the error propagation of Start-Job and Receive-Job misbehaves, a golden failure could go unreported. Verify once on a branch push by breaking a snapshot (CI runs on every branch and does not release).

**effort**: small

**prototype diff**: `ci__golden-alongside-tests.diff`

## Proposal: heavy-order-ci-times — Order run_all's _HEAVY list longest-first by Windows CI times (adds test_rxp_explorer, test_rxp_facts, test_rxp_routes, test_link)

**change**: tests/run_all.py: set `_HEAVY = ("test_golden.py", "test_navmesh.py", "test_rxp_explorer.py", "test_rxp_facts.py", "test_dashboard_page.py", "test_rxp_routes.py", "kb/test_rxp_updates.py", "test_types.py", "test_desktop_main.py", "test_link.py", "test_models.py")`.

test_golden.py stays first. On CI it skips in 0.0 s, so the slot frees at once; on the player's PC with data it runs every probe. On CI, test_rxp_explorer (37.9 s median) currently starts about 33 s in and ends last. test_rxp_facts costs 22 s on CI against 1.6 s on Linux, because of Windows file costs.

**measured**: Scheduler replay of 10 Windows CI runs (the model reproduces the real Tests step: 72.3 s simulated against 71.5 s actual median). The Tests step goes from 72.3 to 65.4 s median, or −7 s on every CI run; the lower bound is sum/4 = 62.5 s. A local A/B on this box was noise-dominated by another investigator's load (load average 6.3 to 9.7; wall 97 to 106 s for both orders), with equal CPU of 191 to 198 s user+sys, as expected for a pure reorder. The local wall gain is estimated at about 4 s from the quiet baseline (explorer ended last at 67.9 s while navmesh ends at about 62.7 s).

**saving_s_linux**: 4

**windows_note**: This is mainly a Windows saving. Windows CI times drive the order, since several rxp tests are 10x slower there, and the player's Windows PC likely gains the same way. It only changes start order; it starts no extra processes.

**coverage**: No test or assertion changes; this is pure scheduling order.

**risk**: Very low. Several CPU-heavy tests now start together at t=0, which is the point; the CPU total is unchanged. This may overlap with another investigator's scheduling proposal; merge the two lists.

**effort**: small

**prototype diff**: `ci__heavy-order-ci-times.diff`

## Proposal: parallel-pip-npm — Install the Python packages in a background job while npm ci runs (test job)

**change**: ci.yml:
- Move setup-node next to setup-python.
- Replace the 'Python packages' and 'Desktop packages' steps with one pwsh step:
  1. `$pip = Start-Job -WorkingDirectory $PWD { python -m pip install ... -r requirements-dev.txt 2>&1 | % { "$_" }; if ($LASTEXITCODE) { throw ... } }`
  2. `Push-Location desktop; npm ci; $npm = $LASTEXITCODE; Pop-Location`
  3. `Receive-Job $pip -Wait -AutoRemoveJob` (prints pip's output, rethrows its failure)
  4. `if ($npm) { exit $npm }`
- The pip and npm caches from setup-python and setup-node are unchanged.

**measured**: Not prototyped on Windows (no pwsh or Actions here). From the logs: pip install takes 19 s median (14.5 s of it 'Installing collected packages': about 6,900 files, 5,435 of them pyright's stubs, numpy 921) and npm ci 10 s, run one after the other today. Overlapped, the estimate is about −8 s per CI run, assuming about 80% of the shorter one overlaps given 4 vCPUs and disk contention.

**saving_s_linux**: 0

**windows_note**: CI only; pip's work is Windows small-file I/O plus .pyc compilation, mostly on 1 core. I considered and rejected caching site-packages instead: about 6,900 small files would make the Windows tar restore about as slow as pip itself.

**coverage**: The same packages are installed from the same pins, and a failure of either install still fails the step and the job. No test changes.

**risk**: Low. The risk is pwsh job semantics (error propagation); verify on a branch push by breaking a pin once. Interleaved output: pip's lines print after npm's.

**effort**: small

**prototype diff**: `ci__parallel-pip-npm.diff`

## Proposal: shard-tests-2-runners — Split the CI test step across 2 windows-latest jobs (matrix) once the build job is no longer the release bottleneck

**change**: Add `--shard i/n` to tests/run_all.py. The split must be deterministic and balanced, for example longest-first by a static weight table (the `_HEAVY` CI medians), with the rest dealt round-robin. Make ci.yml's test job a 2-entry matrix. Lint, Desktop build and golden run only in shard 1, the shorter one. release.yml's `needs: test` then waits for both shards automatically.

**measured**: Not prototyped. The scheduler replay with 2 shards of 4 workers, balanced longest-first, gives a Tests step of 48.5 s median against 65.4 s with the new `_HEAVY` order. It is limited by test_navmesh alone (47.8 s), and each shard pays its own setup in parallel. CI feedback improves about 17 s per push. Release latency gains about 0 s while the build job (~126 s with the Python cache) is the critical path; it only pays once test_navmesh or the build gets faster.

**saving_s_linux**: 0

**windows_note**: Uses 2 Windows runners per test job. That is free for public repos, but a push to main already runs 2 test jobs (CI plus the release's own), so this makes 4. Larger (8+ core) runners are not available to a personal account's public repo, so more runners is the only way to more cores.

**coverage**: The same tests run, each exactly once across the shards; a coverage assertion that the shards' union equals the collected list would guard against mistakes.

**risk**: Medium. Shard balance drifts as test times change, and setup is duplicated. Wait until the per-test speedups (test_navmesh, test_rxp_explorer) land: they may make this unnecessary.

**effort**: medium

## Proposal: nsis-compression-level — (Not recommended now) Lower the NSIS payload's 7z level via ELECTRON_BUILDER_COMPRESSION_LEVEL

**change**: Set `ELECTRON_BUILDER_COMPRESSION_LEVEL: 3` in the env of the 'Build and upload' step. electron-builder's compute7zCompressArgs checks this variable before everything else, so it overrides the differential-aware 'normal' setting.

**measured**: Proxy measured here, since no 7-Zip is available: per-file LZMA2 with a 1 MiB dictionary and the x86 BCJ filter (electron-builder's NSIS parameters), over 222 MB (node binary standing in for electron.exe, the bundled Python, ruff.exe).
- preset 6 (equivalent to -mx=9 with -md=1m): 109.1 s CPU, output 0.258 of input
- preset 5: 99.0 s (−9%), +0.6% size
- preset 3: 54.0 s (−50%), +8% size
- preset 1: 31.6 s (−71%), +10% size

On CI the 7z step is about 39.5 s of the 72.5 s build, so level 3 would save about 20 s, but only when the build job is the critical path.

**saving_s_linux**: 0

**windows_note**: CI build only. The cost lands on players: the installer grows from 138.4 MB to about 149 MB for every fresh install. The first update after the change re-downloads in full, because every compressed byte changes.

**coverage**: No test impact; this is player-facing installer size.

**risk**: A player-visible trade-off (download size). Not worth it while the test job, not the build, is the critical path after the first two proposals.

**effort**: small

