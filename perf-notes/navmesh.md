# navmesh

## Summary

I cut test_navmesh.py from 55-61 s CPU to 8.9-10.6 s (about 6x) and test_rxp_routes.py from 4.8-5.8 s to 1.6-1.8 s. No assertion changed, and the builder output is bit-identical to origin/main.

Where the time went, from origin/main measured here:
- The test builds a full tile domain 14 times. That is 528x528 fine cells, 6 surface layers, 8 directions. Each build costs about 4 s. Builds are 63.6 of 65.4 s profiled. The A* searches, the flight checks and the rebuild-policy check together cost under 2 s.
- Per build, fine_edges takes 1.9 s. It loops over all 6x6 layer pairs on full 528x528 arrays for each direction, but the test worlds use only 1-2 layers. About 90% of that work is on empty layers.
- joins takes 0.75 s and runs twice per build. The test's build() helper called labels() and domain_reach() without passing the pairs, unlike build_tile, so joins ran twice. Inside joins, sorting and searching 2.2M int64 keys is about 0.6 s.
- condense takes 0.74 s. Of that, about 0.5 s is a pure-Python list comprehension that calls DIRS.index() once per fine edge crossing a block border: 5.5M calls in total. A full-array 3-D gather is also run for all 48 direction-and-layer combinations.
- _union takes about 0.07 s per call. surfaces and rasterise are under 0.1 s on these worlds.
- 3 of the 14 builds repeat an earlier world exactly: flat, high and half-wall are each built twice.
- So the time is not slow searches, and nothing searches without a cap. It is vectorised numpy doing 6x6x8 times the needed work, plus one Python loop, plus duplicated work in the test.

What I changed in the prototype (all four files pass ruff; pyright shows only the one error already on origin/main):
1. navbuild.py: fine_edges, joins, condense, _union, labels and domain_reach rewritten to touch only surfaces that exist. Each build of a test world dropped from 2.8-3.2 s to 0.55-0.65 s.
2. A new production function, build_domain(). build_tile and both tests now call the same pipeline.
3. test_navmesh's build() caches finished worlds by a hash of their inputs.
4. NavGraph.index() and find() are faster, with identical results.

How I verified it:
- Builder: a hash of every stage's output (surfaces, edges, pairs, labels, reach, final arrays) matches origin/main exactly on 14 worlds. These are the test's 11 worlds plus 3 random stress worlds with stacked floors, ramps, bridges, water, holes and cliffs, up to 6 surfaces per cell.
- A*: path, cost and expanded count match exactly on 304 random searches across 9 graphs, including extra links, any_floor, max_expand caps, missing goal nodes, and 4x4-tile maps. smooth(), node_at() and track() match too.
- Mutations: the test still fails when the slope limit, the one-way-drop rule in joins, or the coarse direction in condense is broken.
- Coverage gaps, not caused by these changes: these mutations still pass the test. In the builder: removing the corner-cutting check, letting a steep surface hop out by 2 yd, and never joining patches in domain_reach. In A*: dropping the steep-climb check, swimming costing the same as walking, and an off-by-one in index(). I re-ran the A* climb and swim-cost mutations on origin/main and they pass there too.

Effect on the run:
- test_navmesh stops being the critical path. Its time is now about 7 s of builds, plus searches and imports.
- The next tail is test_rxp_explorer at 45.9 s. It is not in _HEAVY, so it starts late. I'd suggest adding it there whatever else happens.
- On the Windows CI runner, test_navmesh took 39-65 s and test_rxp_routes 11-13 s. I'd expect roughly 7-12 s and 3-5 s, because the work is numpy compute and these tests do almost no process or file work. That is an estimate, not measured on Windows.

For the player:
- The walking-map build in the background is identical in output and uses less CPU per tile. On an object-heavy synthetic tile it went from 5.0 s to 3.0 s; real tiles are not in the repo.
- Routes are identical but find() runs about 2.85x faster. This is the walk-to-step route in the live coach, and the per-leg searches in the Guides routes engine (companion/rxp_routes.py). Those have a 2,500 ms budget per request, and an unreachable leg costs about 1-5 s, so more legs fit in each budget.
- Nothing frozen changes: same map file format, same NAV_VERSION, same cached legs.

## Profile

Measurements: CPU is user+sys via os.wait4. Old and new runs were interleaved. Load average was 1-2.5, so another job was probably running on this machine.

| Test | origin/main CPU s | prototype CPU s |
|---|---|---|
| test_navmesh.py | 55.1 / 60.2 / 61.4 | 9.1 / 10.5 / 8.9, and 10.6 / 10.5 / 10.6 in a busier later run |
| test_rxp_routes.py | 5.6 / 5.8 / 4.8 | 1.8 / 1.7 / 1.6 |

Each change on its own, test_navmesh, 2 runs each:

| Change applied | CPU s |
|---|---|
| Builder and A* only, test file unchanged | 15.2 / 14.5 |
| Test helper and cache only, builder unchanged | 37.2 / 39.5 |
| Everything except the A* change | 10.3-11.2 |
| Everything | 10.5-10.6 |

So the A* change makes no measurable difference to this test.

cProfile of test_navmesh on origin/main, 65.4 s profiled:

| Function | Calls | Cumulative s | Note |
|---|---|---|---|
| build() | 14 | 63.6 | |
| fine_edges | 14 | 26.4 | 25.0 s of it in its own body |
| joins | 28 | 21.0 | searchsorted 8.8 s, sort/copy 2.6 s |
| labels | 14 | 14.6 | includes joins |
| domain_reach | 14 | 10.7 | includes joins |
| condense | 14 | 10.3 | 7.4 s in its own body, plus 5.5M tuple.index calls (1.26 s) |
| _union | 42 | 3.0 | |
| NavGraph.find | 49 | 1.2 | 312k _Layer.__getitem__ → index() calls |
| surfaces | 14 | 1.1 | |
| rasterise | 14 | 0.4 | |

CPU per section of the test, origin/main → prototype (s):

| Section | origin/main | prototype |
|---|---|---|
| wall + door | 8.5 | 2.4 |
| ledges | 7.9 | 2.1 |
| slopes | 8.0 | 1.7 |
| bump | 4.1 | 1.0 |
| bridge | 4.0 | 0.8 |
| string-pulling | 7.5 | 1.5 |
| lake | 2.9 | 0.7 |
| reach (rebuilt `high`) | 3.9 | 0.02 |
| walk times (rebuilt flat and half) | 8.6 | 0.1 |
| track, flights, rebuild policy | under 0.1 | under 0.1 |
| Total | 55.8 | 10.5 |

Per-stage CPU for one build, best of N runs, origin/main → prototype (s):

| Stage | flat world | stress1 (6 layers, about 4k triangles) |
|---|---|---|
| fine_edges | 1.84 → 0.15 | 1.64 → 0.58 |
| joins | 0.57 → 0.09 | 0.72 → 0.12 |
| labels | 0.13 → 0.11 | 0.25 → 0.18 |
| domain_reach | 0.15 → 0.055 | 0.29 → 0.06 |
| condense | 0.44 → 0.12 | 0.61 → 0.38 |
| surfaces | 0.08 (unchanged) | 0.69-0.75 (unchanged) |
| rasterise | 0.00 | 0.82-0.98 (unchanged) |
| Total | 3.22 → 0.60 | 5.0 → 3.0 |

All 14 worlds together: 61.0 → 23.0 s. rasterise plus surfaces account for 10.7 s of that and are untouched; they are the next production target on object-heavy tiles.

A* benchmark: 304 find() calls take 11.9 s on origin/main and 4.2 s with the prototype (2.85x), with identical outputs.

Other figures:
- System time fell from about 8 s to 1.5 s per run because far fewer large temporary arrays are allocated.
- The rebuild-policy section spawns one subprocess, about 0.1 s. It is needed: it checks that NavBuilder.needed loads no numpy, which only a fresh interpreter can show.

The harness scripts (domains.py, equiv.py, astar_equiv.py, stages.py, sections.py, cpu.py) are in /tmp/claude-0/-home-user-Glide/4ddc3f14-3355-571d-9bad-778daa3d02a0/scratchpad/perf/nmwork. That folder also has the reference hashes (ref.json) and the diffs: p1_navbuild.diff, p2_tests.diff, p3_astar.diff and all.diff.

## Proposal: navbuild-sparse-vectorise — Builder: touch only the surfaces that exist (fine_edges, joins, condense, union-find)

**change**: In wowfiles/navbuild.py, with identical output:

(1) fine_edges: work out nl, the highest layer in use. For each direction and each source layer `la`, take only the flat indices of cells that have a surface on `la` and whose neighbour is inside the domain. For each target layer `lb`, compare against only the neighbours that have a surface on `lb`. When every neighbour has that layer (the ground), use the whole array without a subset. Keep the same lim_up/lim_dn formulas and the strict `zb > bestz` choice. Run the diagonal corner-cutting check only on cells that got an edge. This replaces 8x6x6 passes over full (528x528) arrays.

(2) joins: test whether an edge goes both ways with a direct lookup, `tgt[(d+4)%8, lb, target_cell] == la`. This replaces sorting 2.2M int64 keys and searchsorted. The pairs come out in the same order.

(3) condense: replace the Python `DIRS.index(...)` list comprehension, 5.5M calls, with a 3x3 lookup table (_DIR_OF). Gather target layers with a slice plus take_along_axis, or nlt[0] when every edge goes to layer 0. Count coarse-edge votes only for fine cells whose step leaves their 8x8 block, using one np.bincount instead of np.add.at. Edges inside a block still go to the `within` bits.

(4) _union: renumber only the items that appear in some pair. Numbering in order keeps the smallest index as the component label.

(5) labels: decide same-block with a precomputed cell→block table (_BLOCK_OF) instead of 8 integer div/mod passes.

(6) domain_reach(..., patches=lab): union the patches from labels() using only the pairs that join two different patches. The components, and so the labels, are the same.

(7) New build_domain(tz, tnz, th, wl, tri_rc) holds the post-read pipeline. build_tile calls it.

Player-facing: the walking map files (data/gamedata/nav/*.npz) are bit-identical, so NAV_VERSION stays and nobody rebuilds. The background build uses less CPU per tile: 5.0 → 3.0 s on a synthetic tile with 6 layers and about 4k triangles, and about 3.2 → 0.6 s on plain terrain.

**measured**: Test file unchanged, builder and A* changes only: test_navmesh 55-61 → 14.5-15.2 s CPU. test_rxp_routes 4.8-5.8 → 1.6-1.8 s CPU (its single build is about 0.6 s now).

One build of a test world, best of 5: 2.8-3.2 → 0.55-0.65 s. fine_edges 1.84 → 0.15, joins 0.57 → 0.09, condense 0.44 → 0.12, domain_reach 0.15 → 0.055.

Every stage's output hash matches origin/main on 14 worlds: the test's 11 plus 3 random multi-floor stress worlds.

**saving_s_linux**: 45

**windows_note**: Pure numpy, with no processes or files involved, so the speedup should carry over to the Windows dev PC and windows-latest CI. There, test_navmesh was 39-65 s and test_rxp_routes 11-13 s. Far fewer large temporary arrays are allocated: system time here fell from about 8 s to 1.5 s. Windows memory commit costs more than Linux, so this probably helps there at least as much. The player's background map build runs the same code on Windows.

**coverage**: The tests are untouched by this proposal. The builder's output is proved bit-identical by stage hashes on 14 worlds. Those include multi-layer cases with stacked floors up to 6 per cell, ramps, bridges, deep water, holes and cliff bands, which the test itself never reaches. So every check the test makes sees exactly the same data.

The test still catches these mutations of the new code: slope limit raised ("a 3 yd wall must block" fails), joins treating one-way drops as two-way ("a 3 yd ledge can't be climbed" fails), and the wrong coarse direction in condense (fails).

Already uncaught on origin/main, unchanged here: removing the corner-cutting check, letting a steep surface hop out by 2 yd, and breaking domain_reach. Each could get a cheap targeted assertion as a separate task.

**risk**: Low. The output is identical by construction and by checking. The rewrite assumes nothing about the layer order inside a cell: it keeps the strict 'highest zb wins' rule and counts layers up to the highest one in use. Two things to note: domain_reach gets an optional `patches` argument, and build_domain is a new public function. No frozen interface in docs/REFACTOR-CONTRACTS.md is affected (same file format, same CLI).

**effort**: medium

**challenge (cov)**: holds=True, corrected saving 46 s

Reasons: I tried to break the proposal on coverage and semantics. The output-identity claim held, but I found one real coverage gap, which is fixable.

OUTPUT IDENTITY HOLDS. I applied the prototype_diff to a scratch worktree off origin/main. Then I hashed every stage against origin/main: zs, kind, tgt, pairs, labels, domain_reach (both branches), condense output, build_domain and to_sparse nreach. I did this on 28 random worlds (seeds 0-27): cliff bands, rough steep noise, 30-75 degree slopes, holes, terrain missing at an edge, lakes, up to 40 boxes, stacked slabs that fill all 6 layers, and ramps. I added 6 edge cases: an empty domain (nl=0), all holes, a single cell, strips along the border, 9 stacked slabs with no terrain (more than LF), and a field of posts that gives more than L patches per block (where comp decides). There were 0 differences.

I re-derived each step by hand. They are equivalent:
- The joins back-lookup works because DIRS[(d+4)%8] = -DIRS[d].
- The corner check works because an in-domain diagonal neighbour implies in-domain side cells.
- The renumbering in _union keeps order, so the smallest index stays the root. This also covers reach_packed, which shares _union; its nreach hash is identical.
- In domain_reach with patches, the smallest surface of a component is always its patch's representative.

numpy is pinned to 2.5.4, where the default int is 64-bit on Windows too, so there are no dtype traps. The change is pure numpy, with no processes, files or timing, so it adds no flakiness. Each test still runs on its own. No frozen interface is touched.

The player-facing effect is that tools/navmesh.py builds tiles faster. Its throttle sleeps in proportion to build time, so the game still gets the same share of the CPU. I could only check that the maps are bit-identical on synthetic worlds: there is no game data in this container.

COVERAGE GAP (the reason for the amendment): both tests' build() still run their own copy of the pipeline. They call labels(zs,tgt,kind) and domain_reach(zs,tgt,kind) without pairs or patches. Nothing in tests/ calls build_domain or domain_reach(..., patches=lab).

On origin/main, build_tile ran the same functions the tests run. After the change, the player's map build runs a domain_reach branch that no test executes, and build_domain's docstring says the tests use it when they don't.

Mutation runs on the proposal code, with test_navmesh and test_rxp_routes unchanged:
- The patches branch with the cross-patch union removed: both tests pass.
- The patches branch made to raise IndexError: both tests pass. On main, a crash in the domain_reach path the build uses would fail the test. Now the crash ships, and tools/navmesh.py catches it per tile and writes the tile as empty, so whole tiles silently vanish from the walking map.

The amendment closes this. With it, both of those mutations are caught (AssertionError and IndexError).

PRE-EXISTING GAPS, NOT A LOSS: the proposal rewrites code these tests never guarded.
- Removing the corner check: uncaught.
- Restricting the corner check to layer 0: uncaught.
- domain_reach returning one-cell areas: uncaught, on main too.
- _union returning a wrong but consistent representative (items[::-1][p]): uncaught.

Once the old code is gone, the off-repo hash comparison is all that vouches for the rewritten corner check and the _union root rule. The tests do still catch: dropping the top layer (fine_edges nl off by one) in both tests, and condense taking the target from layer 0 only in test_navmesh. The proposal's own three mutations are also caught.

SAVING: these are CPU numbers with the test files unchanged, measured with os.wait4 rusage in alternating runs.
- test_navmesh: 61.4 and 62.1 s on origin/main, 19.2 and 16.3 s with the proposal. The amended test runs in 14.9 s.
- test_rxp_routes: 5.5 and 5.9 s on origin/main, 1.9 and 2.0 s with the proposal.
- Total: about 46 s CPU per run. That matches the claimed 45 s, even though the proposal's 14.5-15.2 s figure mixed in A* changes this diff doesn't contain.

Caveat on wall time: test_rxp_explorer (45.9 s, not in _HEAVY, starts around 22 s) then becomes the critical path. A schedule simulation from base_j4.json gives run_all -j4 68.0 → 66.3 s from this change alone. Putting test_rxp_explorer first in _HEAVY gives 62.7 → 45.9 s. So the wall-clock win needs that one-line runner change, or the explorer work.

On Windows (CI test_navmesh 39-65 s, test_rxp_routes 11-13 s), the gain should be at least as large, since system time here fell from 6.4-8.1 s to 2.7-4.6 s.

Amendment: 1) Required, same change. In tests/test_navmesh.py, replace build()'s inline pipeline with `return NM.build_domain(tz, tnz.astype(np.float32), th, wl, tri)`; do the same in tests/test_rxp_routes.py build(). The tests then run the player's real path, including domain_reach(..., patches), and build_domain's docstring becomes true. Then, after the bridge-world checks, add one guard that the patches shortcut equals the reference on a multi-floor world: recompute zs/kind/tgt/pairs/lab for the bridge and `assert np.array_equal(NM.domain_reach(zs, tgt, kind, pairs, lab), NM.domain_reach(zs, tgt, kind, pairs))`. Prototyped: ruff clean, test passes in 14.9 s CPU, and both patches-branch mutations are caught (diff saved at /tmp/claude-0/-home-user-Glide/4ddc3f14-3355-571d-9bad-778daa3d02a0/scratchpad/perf/vcov/amended_test.diff).
2) Strongly recommended in the same change rather than "a separate task", because the rewrite touches exactly this unguarded logic: a direct fine_edges corner-cutting assertion on a hand-made zs. Five checks: open ground lets the diagonal through; no squeezing between two posts; no cutting a post's corner; on a deck 20 yd above ground the diagonal is walked; and it is blocked where a side cell's deck is missing. Prototyped (/tmp/claude-0/-home-user-Glide/4ddc3f14-3355-571d-9bad-778daa3d02a0/scratchpad/perf/vcov/scripts/corner.py); it costs 0.2-0.5 s CPU and catches both corner mutations. Also add a one-line _union contract assert, `list(NM._union(6, np.array([4, 1]), np.array([2, 5]))) == [0, 1, 2, 3, 2, 1]`, which guards the smallest-index root that the new renumbering must keep.
3) To turn the CPU saving into run_all wall time, put test_rxp_explorer.py first in _HEAVY in tests/run_all.py. Otherwise the -j4 wall barely moves (68.0 → 66.3 s simulated).

**challenge (gain)**: holds=True, corrected saving 45 s

Reasons: The CPU gain reproduces and the output is identical. The suite wall-clock does not drop by 45 s on its own.

Setup: the prototype diff applied to an origin/main worktree. Its last hunk header is miscounted (14,7 for 13,6), so it needs `git apply --recount`. A/B runs were interleaved, CPU time came from os.wait4 rusage, and load average was 4-6 from another investigator.

1. **test_navmesh CPU:** origin/main 62.3, 62.9, 61.7 s (user about 54.6, sys 7.0-8.3). Patched: 17.9, 16.4, 16.1 s (sys 2.8-3.8). The saving is 45.5 s, range 43.8-46.8. Peak memory (maxrss) went from 263 to 228 MB. The test passes.
   - The proposal's "14.5-15.2 s" includes A* changes that are not in this diff. The diff alone gives 16-18 s.
   - cProfile of the patched test: about 14 s is still the builder (_union 4.0, joins 3.3 twice per build because the test's build() recomputes the pairs, fine_edges 2.6, condense 2.4, surfaces 1.3) and 1.6 s is A* `find`.
2. **test_rxp_routes CPU:** 5.06, 5.27, 5.07 → 2.67, 2.64, 2.14 s, a saving of about 2.6 s (claimed 4.8-5.8 → 1.6-1.8, again with the A* changes).
3. **Same output:** I checked independently, with my own harness and my own stress worlds. Every stage's output is bit-identical: tgt, the pairs (including their order), labels, domain_reach with and without `patches`, the condense outputs and dtypes. That holds on the test's 11 worlds and on 4 random worlds with 6 stacked layers, 1-2k triangles, holes, water, ramps, cliff bands and a deck. build_tile's own pipeline and to_sparse (reach_packed's _union) are also identical.
   - The equality follows from the code too. Renumbering in _union keeps the order, so the minimum label is unchanged. Contracting patches leaves the components the same. The (d+4)%8 lookup is the opposite direction under DIRS. bincount and add.at give the same argmax and max.
4. **Mutations:** the test still catches three mutations of the new code. joins always mutual fails "3 yd ledge"; wrong condense direction fails "3 yd wall"; a raised slope limit fails "3 yd ledge". ruff passes.
5. **Player-facing builder, build_tile pipeline, CPU best of 3:**
   - Plain or few-object tiles: 2.7-3.2 → 0.56-0.77 s (4-5x).
   - The 6-layer stress tiles: 6.9-9.4 → 4.5-6.7 s (1.4-1.6x). Real city tiles gain less than the plain-terrain figure.
6. **What run_all gains:**
   - test_rxp_explorer (45.9 s CPU-bound, not in _HEAVY, started at t=22 s) becomes the critical path. I list-scheduled run_all's own order using the measured per-test walls; the simulation reproduces the baseline (68.0 s against 67.9 measured, rxp_explorer 22-68).
   - **Linux -j4:** 68 → about 66 s with this change alone. With test_rxp_explorer also put first in _HEAVY: about 46-49 s.
   - **Windows CI, median per-test walls, -j4:** 70 → 65 s alone, 56-59 s with the _HEAVY change.
   - **8-core dev PC, using CI walls:** rxp_explorer (12-50 s) is already the tail, so this change alone saves about 0 s; with it in _HEAVY, 49.5 → 38 s.
   - The 45 s is real for CPU, and for a developer running test_navmesh by itself (62 → 17 s wall). It is not real for the full-suite wall unless test_rxp_explorer is scheduled first or made faster.
7. **Windows:** the change is pure numpy with no processes or files, so the ratio should carry over. The original code allocates many 2-7 MB full-grid temporaries. Windows has no transparent hugepages, and committing and zeroing pages costs more there, so its sys share is likely larger and the relative gain at least as good. I could not measure this here.
   - Estimate for CI: test_navmesh median 47.75 s → about 13 s, and test_rxp_routes 12.2 → about 6 s. That is about 41 s less runner time, but only about 5 s off the CI test step on its own.

Amendment: 1. Ship the change together with putting test_rxp_explorer.py first in tests/run_all.py _HEAVY (one line), or with a speedup of test_rxp_explorer. Without that, run_all's wall time at -j4 drops only about 2 s on Linux and about 5 s on CI, because test_rxp_explorer becomes the critical path.
2. Restate the measured figure for this diff alone: test_navmesh 62 → 16-18 s CPU, test_rxp_routes 5.1 → 2.5 s. The 14.5-15.2 / 1.6-1.8 s figures need the A* changes, which are not in the diff.
3. Fix the last hunk header of the diff; it needs `git apply --recount` as it stands.
4. Optional extra saving (not counted): the test's build() could pass the pairs once instead of having labels() and domain_reach() each recompute joins(), which would save about 1.5 s more. Doing it via build_domain would also move the test onto the `patches` path, so keep an assertion that still exercises domain_reach without `patches`.

**prototype diff**: `navmesh__navbuild-sparse-vectorise.diff`

## Proposal: navmesh-test-build-once — Tests: build through build_domain (pairs computed once) and serve repeated worlds from a cache

**change**: In tests/test_navmesh.py, build() calls NM.build_domain(tz, tnz, th, wl, tri), the same pipeline build_tile uses. The old helper differed from production: it did not pass `pairs`, so joins ran twice per build.

build() also caches the result in a module-level dict keyed by a sha1 of the input bytes (tz, tnz, wl, tri). The three worlds that were built twice (flat, the 3 yd `high` ledge, the half wall in the string-pulling, reach and walk-times sections) are built once. The cached arrays are only read: graph() packs them into a new npz each time and NavGraph loads fresh arrays.

In tests/test_rxp_routes.py, its copy of the helper becomes one line calling NM.build_domain. This depends on build_domain from navbuild-sparse-vectorise; on its own it needs only that 10-line function.

Optional hygiene, not prototyped: the test leaves 15 mkdtemp folders in %TEMP% per run. graph() could write into one TemporaryDirectory removed in a finally. That is safe on Windows because NavGraph closes the npz with `with`.

**measured**: On top of navbuild-sparse-vectorise: 14.5-15.2 → 8.9-10.6 s CPU (3 builds fewer, joins once per build). Builder unchanged, this change alone: 55-61 → 37-40 s CPU. The sha1 key costs about 4 ms per call.

**saving_s_linux**: 5

**windows_note**: Same benefit on Windows. Pure compute is saved, and 3 fewer builds also means 3 fewer cycles of large temporary allocations. No change to processes or files.

**coverage**: Every assertion is unchanged and runs on the same arrays. The builder is deterministic and a cached world is the same input, so each section's assertions see exactly what they saw before. The tests now exercise the production pipeline (build_domain, which build_tile calls) instead of a hand copy that differed from it, so coverage of what ships slightly improves.

**risk**: Very low. If a later test mutates a returned array in place, the cache would share the mutation. Today nothing does: to_sparse/pack copy the values. The docstring says the arrays are only read.

**effort**: small

**challenge (cov)**: holds=True, corrected saving 5 s

Reasons: I tried to break it and couldn't find any coverage it loses. The checks were run in a scratch worktree at origin/main 070107e, which I have since removed.

1. The assertions see identical arrays. I added a harness version of the test. On every one of the 14 build() calls, including the 3 cache hits, it rebuilt the world fresh with the old hand-copied pipeline (joins run inside labels and inside domain_reach) and compared the result with what build() returned. All 4 arrays had the same dtype, shape and bytes on all 14 calls. That holds on main with the standalone build_domain and again on the navbuild-sparse-vectorise builder. 14 calls over 11 distinct worlds means 3 builds saved, as the proposal says (high, flat, half).

2. Nothing mutates the cached arrays. to_sparse only slices, pack copies through fancy indexing, and NavGraph loads fresh arrays from the npz.

3. Hidden state is not an issue. The builder imports only math, numpy, adt, navformat and worldobjects. It reads no MP params, environment variables or randomness, so building inside MP.scoped(SPEEDS) in the walk-times section gives the same result as the cached build made outside it. The only module state the test patches is NF.NAV_VERSION and NAV_REQUIRED, after the last build. The NAV_VERSION in the npz is still read at graph() time, which is not cached.

4. The cache key is sound. tz, tnz and wl have fixed sizes and tri is the variable tail, so the concatenated bytes are unambiguous. tnz is float32 in both helpers, so the twalk comparison is unchanged. A different NaN payload could only cause a cache miss, never a wrong hit.

5. Mutation check: setting JUMP = 3.5 still fails test_navmesh ("a 3 yd wall must block") and test_rxp_routes. Because the cache is a pure memo over a deterministic builder, any builder mutation reaches the second-use sections exactly as a rebuild would.

6. Windows, running on its own, flakiness: no new processes, files or I/O; hashlib is stdlib; the cache is per process, so nothing is shared across run_all workers. The test passes on its own, ruff is clean, and test_rxp_routes passes with the one-line build_domain helper.

7. Player behaviour: none changes. build_domain makes the same calls as build_tile, in the same order. NAV_VERSION is bumped by hand and no source hash is involved, so no rebuild is triggered on players' PCs. But wowfiles/ ships (R29), so adding build_domain to navbuild.py is a shipped change and a push to main releases it.

Caveats:
(a) The coverage claim is overstated. Removing domain_reach entirely (comp=None) still passes both tests, and the arrays are byte-identical on all 14 builds. No test world puts more than L patches in a coarse cell, so domain_reach is not checked at all, before or after this change. The pairs-once wiring only matters for labels(), which is checked. On top of sparse, its new patches path in domain_reach is not checked by any test. That gap existed before this change and is not caused by it.
(b) The pairs=None defaults of labels and domain_reach become dead code: no test or production caller uses them. They only call joins(), so no meaningful coverage is lost.
(c) The memo key leaves out NM's module constants. A future section that monkeypatches NM.JUMP or SAFE_DROP and rebuilds an already-cached world would silently get the old arrays. No section does that today.

Measured CPU time with os.wait4 rusage, nothing else heavy running:
- Builder unchanged: baseline 50.7, 58.5, 59.4, 60.2 s; with the proposal 37.1, 40.1, 41.4 s. About 18 s saved.
- On the sparse builder: 15.3, 15.5, 15.6 s → 9.0, 10.1, 11.2 s. About 5 s saved, matching the proposal.
- test_rxp_routes: 5.1-5.4 s → 4.3-5.7 s, within noise.

Amendment: Four amendments:

1. The standalone build_domain on main must call domain_reach(zs, tgt, kind, pairs) without the 5th `lab` argument. The sparse version's `domain_reach(..., pairs, lab)` raises a TypeError on main, which has no `patches` parameter. I prototyped the standalone version and saved it as a diff at /tmp/claude-0/-home-user-Glide/4ddc3f14-3355-571d-9bad-778daa3d02a0/scratchpad/perf/vbo_proposal_standalone.diff.

2. Drop the claim that coverage of what ships improves for domain_reach. Neither test checks it (mutating comp to None passes, with byte-identical arrays). Better still, add one synthetic world with more than L patches in a coarse cell, where the reach-ranked choice changes which patch is kept. That matters if navbuild-sparse-vectorise lands, since it rewrites domain_reach.

3. Say in build()'s docstring that the cache assumes NM is not monkeypatched between builds, or key it on the relevant NM constants. Alternatively, reuse the three repeated worlds through named variables instead of a hash memo.

4. It ships: wowfiles/navbuild.py is in the installer, so landing it on main is a release.

**challenge (gain)**: holds=True, corrected saving 5 s

Reasons: The claimed CPU saving reproduces. The one thing it does not deliver is much suite wall-clock time.

**How I measured.** Child CPU (user+sys) from os.wait4, interleaved runs, on a worktree at origin/main 070107e. The navbuild_sparse.diff from the scratch perf folder was applied for the "on top of" variants. For the standalone case I added a 10-line build_domain to wowfiles/navbuild.py.

**test_navmesh.py, CPU in seconds:**
- Base: 58.9 / 61.8 / 60.6.
- Base plus this change: 40.5 / 43.5 / 35.3. That saves 18-25 s, median 18.4. The claim was 55-61 → 37-40.
- Splitting that saving: passing pairs (joins once) alone gives 48.8 / 49.0 / 44.6. The cache alone gives 45.9 / 49.4 / 43.1.
- On top of sparse, 7 paired rounds: 14.3-17.2 → 9.7-11.1. In the 4 quiet rounds (load about 1), 14.9-15.2 → 9.7-10.5. The saving is 4.5-6.1 s, median 4.9. The claim was 14.5-15.2 → 8.9-10.6, a saving of 5. **It holds.**
- Max RSS also drops: 260 → 251 MB on base, 239 → 199 MB on sparse.

**test_rxp_routes.py** (one build, joins once): 5.3-5.8 → 4.6-4.8 s CPU on base. On sparse, 2.06-2.24 → 1.94-2.22, which is within noise.

**Coverage is kept (verified).**
- The test builds 14 times and 11 worlds are unique. The 3 repeats are exactly flat, flat+high and flat+half.
- I re-ran the original test with every build also calling build_domain. All 14 outputs (z, link, kind, cxy) were bit-identical in dtype, shape and values, both on base and on sparse.
- The 3 repeated builds are bit-identical to their first build, on base and on sparse. tnz is already float32, so the float32 walk mask equals the old one.
- navbuild has no module-level mutable state.
- Freezing the cached arrays (setflags write=False) still passes, so nothing writes to them.
- Mutation checks:
  - JUMP=3.5 fails identically in old and new ("a 3 yd wall must block").
  - Dropping joins' steep-to-steep rule fails identically in sparse and sparse plus this change (AssertionError (60, 'down')).
- The only thing the cache could hide is a builder that gives different output for the same input. Nothing guards against that today, so nothing is lost.

**Wall-clock reality.** I replayed run_all's list scheduling with the measured per-test times. It reproduces the baseline 67.9 s exactly and the CI totals within 0.5 s.
- At -j4 (this container and windows-latest CI), test_rxp_explorer is the tail. It is not in _HEAVY, starts at about 22 s and ends last. On CI it finishes last in 9 of 10 runs.
  - Standalone, this change saves 0.0 s of run_all wall time here, and 0-2.4 s on CI (about 1 s median over 9 CI logs).
  - On top of sparse it saves about 1.2 s locally and about 0-1 s on CI.
- At -j6 or -j8 (a dev PC with 6 or more cores), navmesh is the critical path. Standalone saves 4.8-9.2 s of wall time there, but on top of sparse it saves 0-0.7 s.
- The release pipeline gets no measurable change.

**Windows.** The saving is pure numpy compute and large temporary allocations. sys time drops from 6.5-9.9 s to 2.7-4.8 s on base. It adds no new processes or files, so the proportion (about 31% standalone, about 33% on sparse) should carry to Windows. CI navmesh takes 29-65 s against 60 s here, so I expect about 15 s (standalone) or about 4 s (on sparse) of CI CPU saved per run, but about 1 s or less of CI wall time.

Ruff passes on the changed files. The sha1 key costs about 4 ms per call.

Amendment: 1) Standalone, build_domain must go into wowfiles/navbuild.py and build_tile must call it, as navbuild_sparse.diff does. Otherwise the claim that the tests exercise the production pipeline is false and the code is just a second copy. This touches the player's walking-map builder (NavBuilder on their PC), but the output is bit-identical (checked on all 14 test worlds), so no NAV_VERSION bump is needed.
2) Make the "only read" docstring enforced: call setflags(write=False) on the cached arrays (tested, it still passes). That removes the shared-mutation risk.
3) Label the saving as test-process CPU: about 5 s on top of sparse, about 18 s standalone. The run_all wall saving at -j4 is about 1 s (0 s standalone) until the test_rxp_explorer tail is fixed (put it in _HEAVY or speed it up). Only then, or at -j6 and up, does navmesh CPU turn into wall time.
4) The optional single TemporaryDirectory in graph() is worth doing for Windows %TEMP% hygiene, but it is not part of the gain.

**prototype diff**: `navmesh__navmesh-test-build-once.diff`

## Proposal: navgraph-astar-memo — Production: faster NavGraph.index() and an A* that reads each node once per search

**change**: In knowledge/mechanics/nav.py, NavGraph.__init__ keeps the small tables as Python lists: tidx (at most 64x64) and tbase (at most S+1). It keeps flat views of tcnt and tcum. No new memory beyond about 8k list entries.

index() uses list indexing and ndarray.item() on a flat index, (s<<12)|(r&63)<<6|(c&63), instead of four numpy scalar reads with int().

find() builds a per-search memo, info(node) → (z, kind, link, world X, world Y). It replaces three separate _Layer lookups per node plus pos(). The neighbour loop reads from the memo, using exactly the same arithmetic, so float costs and tie-breaking are unchanged.

Player-facing: routes are identical; they are just computed faster. That covers the live coach's walk-to-step (plan_to_step/walk_route), the Guides routes engine (companion/rxp_routes.py via walk_world, max_expand up to 150k and a 2,500 ms budget per request), and track(). Unreachable legs that hit the cap finish about 2.85x sooner, so more legs fit in each budget. The expanded counts are unchanged, so the max_expand caps behave the same and routes.db does not need its ALGO bumped.

**measured**: 304 random find() calls on 9 synthetic graphs, including 4x4-tile tilings of stress worlds: 11.9 s → 4.2 s CPU (x2.85). Path, cost and expanded count are identical in every call, as are smooth(), node_at() and track(). On test_navmesh itself it is within noise (about 0.5 s of A* work per run).

**saving_s_linux**: 0.3

**windows_note**: Pure Python and numpy scalar access, about the same speedup on Windows. It helps the player's live coach and Guides route filling on Windows directly.

**coverage**: Checked by an old-vs-new harness for identical results (astar_equiv.py in the scratchpad). The test still passes. Note that test_navmesh does not catch these A* mutations, either on origin/main or with this change: dropping the steep-climb check in find(), swimming costing the same as walking, and an off-by-one in index(). A separate small task could add targeted assertions: a swim-versus-walk cost choice, and a steep patch whose climb only the A* rule blocks.

**risk**: Low. Only reads are memoised, within one search, so a stale value is impossible. index() returns the same Python ints. This adds about 15 lines to a hot production path. Exact equality was checked against origin/main, not just the tests passing.

**effort**: small

**challenge (cov)**: holds=True, corrected saving 0.4 s

Reasons: The proposal holds. Routes are identical, tests are not touched, and I found no flakiness or Windows breakage. It does open one gap the tests cannot see, so it needs the amendment below. I applied p3_astar.diff (same as prototype_diff, against blob 6188fdd) in a scratch worktree, now removed.

Semantics: routes, costs and expanded counts are identical.
- index(): exhaustive check of old vs new over every (l, r, c) with layers -1..4 and one cell past each edge. Graphs: 1-tile test worlds, a 4x4 stress tiling, and a 4x4 tiling with three tiles set to tidx -1 (missing tiles). No difference.
- find(): 420 random calls compared tuple for tuple (path, float cost, expanded). Inputs included nodes that don't exist, out-of-range and layer-5 nodes, a == b, links pointing at non-nodes, caps 10..150k, any_floor, and missing tiles. All equal. CPU old 43.0 s, new 15.3 s (x2.81).
- Big capped searches gain less: 150k-cap unreachable search 6.77 s → 3.17 s, 529k expansions 24.7 s → 11.85 s (about x2.1).
- Numpy-int nodes behave the same wherever origin/main accepted them. np.int64 and np.int32 give equal results. The small-int cases that raise OverflowError in new code already raised on origin/main. Production builds nodes only from Python ints (navigation.py, node_at, track).
- smooth(), straight(), node_at(), candidates() and track() only reach the tables through index(), so they are unchanged. No code patches NavGraph attributes. The test_rxp_routes monkeypatch of NavGraph.find still works.
- ruff passes. The tests needed no change and pass on their own: test_navmesh rc=0, test_rxp_routes rc=0.

Coverage: no existing assertion stops catching anything. But the change duplicates world_of()'s cxy decoding inside find() (info()), and no test pins the two together.
- I swapped the nibbles in info() only. test_navmesh rc=0 and test_rxp_routes rc=0: the A* then costs paths with positions that differ from the world_of() points it returns, and nothing notices. On origin/main that divergence can't happen, because find() calls world_of().
- I confirmed the proposer's note: dropping the steep-climb check, swim cost x1.0, and index `>=` → `>` all pass both tests with the change.
- I prototyped a cost-pinning check (vastar/recost_check.py). On the bridge world it recomputes each path's cost from world_of() and the kind multipliers. It passes on both old and new, and fails on the info() nibble mutant and the swim mutant. It does not catch steep, so that still needs its own assertion.

Player-facing:
- Memory claim is wrong. "No new memory beyond 8k list entries" doesn't hold: the per-search memo holds 5-tuples where the old cache held 2-tuples. tracemalloc peak 55.3 → 60.7 MB at a 150k cap and 195 → 214 MB at 529k expansions; maxrss growth 57 → 61 and 202 → 218 MB. That is about +10% transient memory per search. Acceptable, but it should be stated.
- Guides routes fill more legs per budget. Pending legs never reach the cache, so the cache holds the same rows and ALGO doesn't need a bump.

Windows: pure Python and numpy .item()/.tolist(), so the speedup should be similar there.
- I checked one timing risk. test_rxp_routes asserts that a 1 ms budget leaves legs pending, and rxp_routes times its budget with time.monotonic, which ticks every 15.6 ms on Python 3.12 Windows.
- In budget-1 leg order, the last budget check lands about 51-59 ms after start with the new code (about 127-146 ms on origin/main).
- So a flake would need a machine more than 3.3x faster per thread than this 2.8 GHz Xeon (8x before). Negligible risk, but the margin is smaller.

Saving: A* time inside test_navmesh 0.50 → 0.15 s CPU (whole run 59.8 → 58.4). test_rxp_routes find() time 75-84 → 23-25 ms. Total about 0.4 s, on run_all's critical path (test_navmesh). The real value is in production.

Amendment: Land it together with a regression assertion in tests/test_navmesh.py that ties find()'s inlined positions and costs to world_of(). For each returned path on the bridge world (over the deck, swum along the river, and down into it), recompute the cost from consecutive world_of() distances: x1.5 for swim (kind 2), x1.15 for steep (kind 3), and max(1, d) for a step between floors of the same cell. Assert it equals the cost find() returns within 1e-6. This costs about 2 extra finds on a graph the test already builds. The prototype (vastar/recost_check.py) catches both the duplicated-decoding divergence and the swim-cost mutation. Better still, also add the steep-climb assertion the proposer mentions, since this change rewrites those lines. Correct the memory claim to about +10% transient peak per search (for example +19 MB at 529k expansions). Optional: memoise only nodes that exist and pass the climb check, to trim that.

**challenge (gain)**: holds=True, corrected saving 0.6 s

Reasons: The speedup reproduces and the results are identical. It saves almost no wall time in the test suite today; its real value is in the product.

I re-measured from scratch with CPU time. The diff applied is the proposer's p3_astar.diff, the same bytes as the -cov verifier's tree. The harness and data are in /tmp/claude-0/-home-user-Glide/4ddc3f14-3355-571d-9bad-778daa3d02a0/scratchpad/perf/vgain/ (bench.py, timefind.py, ab.py, mem.py, proposal_applied.diff).

1) Synthetic A* benchmark (my own harness, interleaved old/new order, gc off, 2 seeds, 276 find() calls each). Graphs: half, bridge, high, slope60, lake, stress1-3, plus 4x4 tilings of stress1-3, plus capped searches to unreachable nodes at the routes engine's caps.
- Seed 11: 24.49 s to 8.93 s CPU (x2.74).
- Seed 12: 25.25 s to 9.14 s (x2.76).
- By category: single-tile random searches x3.07-3.37; big-graph random x2.71-2.99; 30k-capped unreachable x3.04-3.27; 150k-capped unreachable x2.38-2.45.
- (path, cost, expanded) and smooth() were exactly equal in all 552 calls. The proposer's x2.85 holds, but "unreachable legs at the cap finish 2.85x sooner" is really 2.4x at the 150k cap and about 3x at the 30k cap.
- Memory on a capped search that expands 83k nodes: tracemalloc peak 25.6 to 28.6 MB (+12%), which is acceptable.

2) Inside the tests (find() wrapped with process_time, run in-process).
- test_navmesh: find CPU 0.534 / 0.614 to 0.158 / 0.198 s over 2 runs per arm, 49 calls, expanded 15423 in both. That is about 0.40 s saved per run. The whole test is 56-60 s CPU and varies by ±2 s between runs, so a whole-process A/B can't see it, as the proposer said.
- test_rxp_routes (the proposal missed this one; it also calls NavGraph.find through walk_world): find CPU 0.269 / 0.347 / 0.266 to 0.084 / 0.131 / 0.087 s, expanded 8812 in both. About 0.19 s saved. A whole-process rusage A/B with 5 interleaved reps gives orig median 5.32 s vs new 5.10 s (a 0.22 s difference), with ±0.5 s noise.
- No other test runs NavGraph.find. test_maps uses the separate find_path, and kb/test_api skips without data.
- Total: about 0.6 s of CPU per suite run, roughly 0.3% of the 183 s summed test CPU.

3) Wall time today: about 0 s. Locally test_navmesh ends at about 63 s and the suite at 67.9 s; test_rxp_explorer ends last. I parsed 10 Windows CI test logs: test_navmesh finished last in only 1 of 10 runs. In the other 9 it finished 8.5-56 s before the last test (rxp_explorer, rxp_facts or kb/test_store). So the 0.4 s only comes off wall time once the explorer and rxp_facts problems are fixed and navmesh becomes the critical path.

4) Windows/CI: the removed work runs inside the interpreter (numpy scalar creation, int() calls, _Layer method calls, repeated index() calls). There is no file or process cost, so Windows' expensive process spawn neither helps nor hurts, and the same ratio and roughly the same absolute CPU saving (about 0.4 + 0.2 s) should carry over. That is negligible next to the 29-65 s run-to-run variation of test_navmesh on CI. Python 3.12 + numpy 2.5.4 was not available here (I measured on 3.13 + numpy 2.5.4); the mechanism doesn't depend on the version.

5) Safety and coverage. With the change, test_navmesh and test_rxp_routes pass and ruff is clean. A mutation that swaps row and column in the new flat index key is caught by both tests ("a 3 yd wall must block" and "around the wall"). The flat key assumes the (S, 64, 64) tables documented in wowfiles/navformat.py. Nothing outside NavGraph writes tidx/tbase/tcnt/tcum, so the list copies cannot go stale. Expanded counts are unchanged, so the max_expand caps and routes.db's ALGO are unaffected.

What the player gets: identical routes, about 2.4-3x faster, in the routes engine (more legs fit in each budget), walk_route/plan_to_step and track(). That is the real reason to take it; the test suite gain is marginal.

Note the proposal's 0.3 s saving_s_linux is roughly the wall time navmesh would save if it were the critical path. As CPU it is an underestimate: about 0.6 s, because test_rxp_routes benefits too.

Amendment: Nothing needs to change for the change to be safe. Correct the claims: the test-suite saving is about 0.6 s of CPU (about 0.4 s in test_navmesh plus about 0.2 s in test_rxp_routes) and about 0 s of wall time today, because test_navmesh is not the critical path locally or in 9 of 10 CI runs. Capped unreachable legs get faster by x2.4 at 150k and about x3 at 30k, not a flat x2.85. Memory peak is about 12% higher on long capped searches. Justify it as a product speedup for routes and walk_route, not as a test-suite speedup.

**prototype diff**: `navmesh__navgraph-astar-memo.diff`

## Proposal: navmesh-split-file — Only if still on the critical path later: split test_navmesh into two files of about 5 s each

**change**: Not recommended right now. Move box/build/graph/node/reachable into a helper module run_all does not collect (it globs test_*.py), for example tests/navworlds.py. test_rxp_routes.py could share its box() and build() too.

Split the sections:
- File A: wall/door, ledges plus reach (reach reuses `high`), bump, about 5.4 s.
- File B: slopes, bridge plus track, string-pulling and walk-times (flat and half), lake, flights, rebuild policy, about 4.8 s.

Keep both in _HEAVY.

**measured**: Not prototyped. Estimated from the per-section CPU after the other proposals: file A about 5.4 s plus 0.3 s of imports, file B about 4.8 s plus 0.3 s. Wall time could drop from about 10 s to about 5.7 s. Total CPU goes up by about 0.3 s here and about 0.5-1 s on Windows (one more Python process, plus numpy and knowledge imports scanned by antivirus).

**saving_s_linux**: 0

**windows_note**: It costs one more process spawn and numpy import per run, which is pricier on Windows. It is worth it only if test_navmesh is still among the last tests to finish once test_rxp_explorer, test_types and the others have been fixed. Before the speedup, splitting the 60 s file into about 4 files would have cut the critical path to about 15 s with no code changes. After the speedup it buys about 4-5 s of wall time at best.

**coverage**: It would be a pure move: the same assertions and the same worlds. The only coupling, the reach section reusing `high` and walk-times reusing flat/half, stays inside one file in the split above.

**risk**: Low. The risk is the duplicated world definitions drifting between files, which the shared helper module avoids.

**effort**: small

**challenge (cov)**: holds=True, corrected saving 0 s

Reasons: I built the split and tested it, so this isn't a paper review. I set up a scratch worktree at origin/main and applied the other navmesh speedups (nmwork/all.diff: navbuild sparse, the NavGraph A* rewrite, and the test's `_BUILT` world cache). The proposal's estimates assume all of these. Then I made three files: tests/navworlds.py holds box, build (with `_BUILT`), graph, node, reachable, DN, F, A, flat and row. File A (tests/test_navmesh.py) has wall/door, ledges, bump and reach. File B (tests/test_navmesh_routes.py) has slopes, bridge, track, string-pulling, lake, walk times, flights and rebuild policy. Both are in _HEAVY. Copies are in /tmp/claude-0/-home-user-Glide/4ddc3f14-3355-571d-9bad-778daa3d02a0/scratchpad/perf/vsplit/.

Coverage holds:
- All 42 asserts and every non-import statement are still present, run in the same order inside each file.
- ruff passes, including F821, so no file uses a name that is only defined in the other one.
- Mutation checks, with `__pycache__` cleared and PYTHONDONTWRITEBYTECODE set. My first try was spoiled by a stale .pyc, because a mutation of the same byte size reused the old bytecode. Every mutation the single file caught was also caught by A or B:
  - MAX_SLOPE 50 to 65: A fails on "3 yd ledge", B fails on (60, 'up').
  - JUMP 1.5 to 3.5: A fails on "3 yd wall", B fails on the smooth length.
  - track() returning node_at: B only.
  - smooth max_look=3: B only.
- Two mutations survived both the single file and the split, so the split loses nothing there: dropping track's same-floor penalty, and flipping the reach sign in node_at.
- The split actually gives better failure reports. The single file stops at the first assert, so with the slope mutation it never shows the (60, 'up') failure; the split shows it.
- The `_BUILT` cache still gets hits in the right places. Reach costs 0.02 s because it reuses `high` from A. Walk times reuses flat and half from string-pulling in B.

Semantics: no production code changes, nothing player-facing changes, no new timing, ports or randomness, so no added flakiness.

Running standalone and on Windows:
- `python tests/test_navmesh_routes.py` passes when run from another directory, because sys.path[0] is tests/. The repo already imports sibling helpers this way (wowmock.py, desktop_schema.py).
- run_all does not collect navworlds.py, and `run_all.py navmesh` picks up both files.
- Windows pays one more process spawn plus a numpy and knowledge import, roughly 0.5-1 s of CPU. That cost runs in parallel, so it is not on the wall clock.

Measured on Linux, 4 runs each, while another investigator was using the same CPUs:

| File | Wall (s) | CPU (s) |
|---|---|---|
| Single file | 9.6 / 9.8 / 9.9 / 10.9 | 9.7 / 9.9 / 10.1 / 10.6 |
| File A | 5.1-5.3 | 5.2-5.5 |
| File B | 4.8-5.8 | 5.1-5.8 |

Per-section CPU puts A at about 4.6 s and B at about 4.9 s, so the 5.4 / 4.8 estimate is close. The extra CPU is about 0.4-0.6 s, slightly more than the proposal's 0.3 s.

The saving right now is 0, as the proposal itself says. After the speedups, test_navmesh takes about 10 s, starts at t=0, and the rest of the run lasts 25 s or more, so the test is not on the critical path.

What the proposal gets wrong is small and about wording and docs, not coverage:
1. It says the only shared state stays inside one file. That is wrong: `half`, used by string-pulling and walk times in B, is built from `row`, which is defined in the wall section in A.
2. Three docs and comments name tests/test_navmesh.py and would go out of date:
   - tools/navmesh.py:12 says that file documents the calibration, but the calibrated rules end up split: hops in A, the 50-degree slope rule in B.
   - knowledge/api/__init__.py:73 says `_flight_from_track` is "the name tests/test_navmesh.py knows it by", but the flights section moves to B.
   - The test_rxp_routes.py docstring says its worlds are "built like tests/test_navmesh.py".

Amendment: 1. Move `row = A + 256` into the shared helper along with flat, A, box, build (with its `_BUILT` cache), graph, node and reachable.
2. Name the second file test_navmesh_*.py so that `run_all.py navmesh` still selects both files.
3. Keep the calibration notes in a docstring that covers both halves, and update tools/navmesh.py:12 and knowledge/api/__init__.py:73 to point at the right file or at tests/test_navmesh*.py.
4. If test_rxp_routes.py shares the helper, import it after that file's numpy SKIP guard. Numpy is pinned in requirements.txt, so this mostly keeps the documented exit-77 behaviour.
5. As the proposal itself says, only do the split if test_navmesh (now about 10 s) is still among the last tests to finish once test_rxp_explorer and test_types are fixed.

**challenge (gain)**: holds=True, corrected saving 0 s

Reasons: The claimed gain checks out. Suite saving is 0 on Linux. The file's own wall time roughly halves. CPU goes up a little. The proposal claims no more than that.

What I measured: scratch worktree at origin/main, removed afterwards. CPU from os.wait4 rusage plus per-section process_time. Another investigator's test_rxp_explorer was running at the same time (load 1-4).

1. Before the split. On main as is, test_navmesh uses 64.7 s CPU: 14 builds at about 4.6 s each. With the navmesh investigation's other changes applied (nmwork/all.diff: navbuild speedup, the _BUILT cache, A* changes), it uses 9.40 s CPU (median, range 8.28-9.71, n=5). That is 0.42 s of imports plus these sections:
   - wall/door 1.6-2.5 s
   - ledges 1.7 s
   - slopes 1.4 s
   - bump 0.7 s
   - bridge 0.8-0.9 s
   - string-pulling 1.35-1.7 s
   - lake 0.7-0.8 s
   - reach 0.02 s and walk times 0.1-0.15 s (both served from the cache)

2. The split, prototyped as proposed (tests/navworlds.py, test_navmesh.py for A, test_navmesh_more.py for B, both in _HEAVY):
   - A: 4.74 s CPU (4.53-4.95), wall 4.5 s.
   - B: 4.91 s CPU (4.88-5.73), wall 4.8 s.
   - Total CPU is 9.65 s against 9.40 s, so +0.25 s, inside the noise (about 0.7 s).
   - `run_all.py -j4 navmesh` went from 11.0 s and 9.6 s to 6.4 s and 5.9 s. This is the only real gain: about 4 s per run for someone working on the walking map.

3. Full run_all -j4, with the other navmesh changes applied:
   - Split: 64.0, 65.9, 75.1 s. Unsplit: 81.0, 72.1, 64.8 s.
   - The other investigator's load swings each run by about 8 s, and test_rxp_explorer finished last in all 6 runs (it starts at 20-25 s).
   - The instrumented pair used 123.0 s CPU (split) against 120.5 s (unsplit).
   - So no real gain shows up; the difference is noise.

4. Scheduling simulation of run_all's queue (heavy tests first), driven by the baseline per-test times. It reproduces the 67.9 s baseline exactly.
   - With the navmesh changes applied, splitting changes the suite's wall time by -0.2 to +0.3 s at -j2, -j4 and -j8 in every scenario. That includes test_rxp_explorer cut to 1/8 and test_rxp_facts cut to 3 s or less.
   - test_navmesh only becomes the last test to finish if every other test is also cut to a third and the run uses -j8. Even then splitting saves 0.2 s, because the next-longest test ends at about 9 s.
   - With 4 workers, the suite can't take less than its total CPU divided by 4, which is at least about 17 s. A 9.4 s test that starts at t=0 can't be the last to finish.

5. Windows CI (median times, 4 workers):
   - After the navmesh changes, test_navmesh would take about 7 s on the runner (47.75 s scaled by 9.3/62.7).
   - Each split file costs about 0.7 s more for the extra process and the numpy import. This is assumed, not measured; the 0.5-1 s range is plausible.
   - That makes the suite 0.3-0.8 s slower in the simulation. The split is a small loss on CI, as the proposal says.

6. Coverage. It is a pure move:
   - The same 42 assert lines appear in both versions.
   - Builds with the cache: 5 in A plus 6 in B makes 11, the same as unsplit. No world is built in both files.
   - Mutation JUMP 1.5 -> 3.5: the unsplit file and both split files fail. A fails with "a 3 yd wall must block"; B fails on its bend-count check (len(s) == 2).
   - Mutation MAX_SLOPE 50 -> 65: all three fail. A fails with "a 3 yd ledge can't be climbed"; B fails with "(60, 'up')".
   - The split reports a failure in each half, where the single file stops at the first one.

Where the proposal is slightly off:
- (a) Its coupling list is incomplete. `row` is defined in the wall/door section, which goes to file A, but the `half` world in B's string-pulling and walk-times sections uses it too. `A` and `flat` are shared as well. The helper module has to export all three.
- (b) "Keep both in _HEAVY" needs care about order. Inserting test_navmesh_more before test_types delayed test_types' start from 2.4 s to 7.0 s in the real -j4 run. It should be listed after the long tests.
- (c) The side claim needs correcting: "before the speedup, splitting into about 4 files would have cut the critical path to about 15 s". At suite level, on main as is, a 4-way split makes -j4 slower in the simulation (67.9 -> 78.2 s), because four heavy files take all the workers and push test_rxp_explorer later. It helps (67.9 -> 52.2 s) only when test_rxp_explorer also starts first.

Amendment: Leave it as "not now". Split only if a run_all report shows test_navmesh among the last tests to finish. With -j4 and the suite at 67 s or more of total CPU, a test of about 9.4 s that starts at t=0 can't be last.

If it is done:
- navworlds.py must export `row`, `A` and `flat` along with box, build, graph, node and reachable, because B's `half` world uses `row`.
- List test_navmesh_more in _HEAVY after the true long tests (test_types, test_rxp_explorer), not before them.
- Present it as a developer convenience: about 4 s less per `run_all.py navmesh` run. It doesn't shorten the suite or CI. Expect about +0.25 s CPU on Linux and an estimated +0.5-1 s per run on Windows.

Correct the side claim about a 4-way split on today's main: on its own it makes -j4 about 10 s slower in the simulation (67.9 -> 78.2 s). It helps only when test_rxp_explorer is also started first.

