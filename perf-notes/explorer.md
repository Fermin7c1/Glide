# explorer

## Summary

I made test_rxp_explorer.py about 4.3x cheaper: 40.45 s CPU down to 9.34 s (medians of 3 interleaved runs; before ranged 39.7-42.1 s, after 9.29-9.36 s). Every output stays identical and no check was dropped; one check got stronger.

Where the 40 s went, timed with process_time and no profiler (cProfile skews these small calls by 2-3x). Nothing re-parses a guide per character: parsing is per guide.
1. **Loading the seed: about 16 s (39%).** `rxp_import.free_guides` gunzips and parses the whole 5.9 MB seed.json.gz (0.13-0.17 s) to get 2.7 MB of guide text, and does it again on every `guide_detail` call. The test makes 92 of those calls.
2. **The page's masks: about 9.5 s.** `rxp_explorer.visibility` takes 0.106 s per guide. For each of the 56 characters it walked every step again, re-ran `guide_dungeons` over every action of the guide, and called repr on every step's flags.
3. **The coach's numbering (the test's reference answer): 6.2-6.4 s** over 13,440 calls. Each call re-scanned all ~960 actions of its guide in `guide_dungeons` (about 1.4 s in total). It also called `step_shown` on every step, although 12,402 of the 13,491 steps (92%) carry none of the 19 tags `step_shown` reads. It copied every step's flags with dict(fl) as well.
4. **Parsing: about 6 s over 312 `parse_guide` calls.** That is 80 for the test's corpus, about 130 inside `guide_detail`, about 100 for the test's own second `include_expanded` pass, plus 3 from rxp_facts. `copy.deepcopy` is about 40% of include expansion. `_build_cond` evaluates 162 class x race x faction combinations the first time each condition appears in a process.
5. **Smaller items:** `X.numbering` decodes hex for every step (1.2 s), and the tail of the test makes 12 more `guide_detail` calls.

**The fixes:**
- **Masks** are now computed once per distinct condition (as a 56-bit player mask) and once per distinct flags/dungeon-rules pair. `visibility` is 8.6x faster and its output is identical on all 80 guides x 5 option sets.
- **The seed** is parsed once per process and re-read if the file changes.
- **The coach's numbering** gets a shortcut in `step_shown` for steps with none of its tags, works out a guide's dungeon rules only when a dungeon step asks, and no longer copies flags.
- **Parsing:** step copies avoid `copy.deepcopy`; character and literal results are cached in conditions.py.
- **The test** now takes its reference answer from the knowledge build's own corpus expansion instead of a second page-style parse. It also reads `next_list` and Darkshore from results it already has.

**Effect for the player:** opening a guide on the Guides page (a fresh `rxp_import.py guide` process) costs 0.97 s -> 0.53 s CPU for 9-13 Zephras Isle (Dungeon), 0.53 -> 0.42 s for Darkshore, and 0.43 -> 0.35 s for 1-6 Durotar. The `routes` command calls `guide_detail` too, so it saves the same.

**test_rxp_facts (20 s on Windows CI vs 1.4 s here): the cause is SQLite fsync and journal-file churn, not CPU.** `store.create_schema` runs the 85-statement schema through `executescript` with no transaction. On a default connection (journal_mode=DELETE, synchronous=FULL) that makes each statement its own transaction: a -journal file is created, flushed and deleted every time. The test builds 6 fixture knowledge bases. Under strace that is 2,076 fsyncs and 519 journal create/delete cycles, against 0.6 s of CPU. On Windows each FlushFileBuffers takes milliseconds, and Defender scans every new journal file.

To stand in for Windows, I added 10 ms to every fsync (strace inject):

| test | before | after |
|---|---|---|
| test_rxp_facts | 27.3 s | 1.6 s |
| test_rxp_routes | 9.7-10.7 s | 5.7 s (1.4 s with its cached map) |
| kb/test_rxp_updates | 8.3 s | 4.1 s |
| kb/test_analyzer | 3.0 s | 1.0 s |

The same pattern is in `tools/build_db.py` (the glide.db build on the player's PC) and in `tests/kb/fixture.py`. Across the whole suite, fsyncs go from 3,994 to 454 and unlinks from 1,990 to 1,082 once the schema runs in one transaction.

**test_rxp_routes (5.2 s CPU):** 4.5 s of it is building the synthetic walking map with `wowfiles.navbuild` (`fine_edges` 1.9 s, `joins` 1.4 s, `labels` 1.1 s, `condense` 0.7 s). Another 364 fsyncs come from `create_schema`.

**test_rxp_conditions:** 2.36 -> 1.22 s CPU from the explorer work alone. Its 4 `guide_detail` calls were most of its cost; tsc takes only 0.32 s here.

**Checks:**
- **Byte-identical output.** I compared against origin/main dumps: `visibility` on 80 guides x 5 option sets; `rxp_numbering` on 80 x 56 x 8; `visible_steps` with dungeons on; `guide_detail` JSON for all 80 guides.
- **Tests.** Every related test passes: rxp_* tests, kb/*, test_store, test_build_install, test_kb_build_guard and test_strip_spec. `ruff check .` is clean.
- **Golden check.** `golden.py check --portable` shows the same `dashboard CHANGED` line on the unchanged baseline, so it comes from this container, not the change.
- **Mutation checks.** A mask that ignores conditional tags, one set of dungeon rules for everyone, no include expansion, and a lazy dungeon lookup that ignores the guide all make test_rxp_explorer fail. A missing tag in the `step_shown` shortcut fails test_rxp_visibility and kb/test_live_logs. Shallow step copies fail kb/test_rxp_parser.
- **New guards.** I added three cheap tests: the shortcut's tag list must contain every tag `step_shown` reads; included steps must share no objects with their source guide; schema creation must be one transaction.

**Upstream moved while I worked.** The diffs were made against 6176835; origin/main is now 070107e, and `git apply --check` passes on 070107e. The scratch worktrees are removed. The diffs are saved in /tmp/claude-0/-home-user-Glide/4ddc3f14-3355-571d-9bad-778daa3d02a0/scratchpad/perf/exw/ (p1_explorer.diff, p2_sqlite.diff, p3_routes.diff, all_prototype.diff).

**Two ideas I tried and did not keep:**
- Grouping characters by their answers to the guide's conditions (13.3 distinct groups per guide on average, out of 56) would cut the reference side another ~4x. It writes into the test the same assumption the new `visibility` code relies on, so it could hide a future rule that depends on something other than conditions. Not recommended.
- Splitting the seed so the player's process skips the 0.15 s JSON parse would change the seed format, which is a frozen interface.

## Profile

Baseline, origin/main 6176835. test_rxp_explorer: 40.45 s CPU median (39.73 / 40.45 / 42.07), wall 40.9 s. The cProfile run took 148.5 s under the profiler. Phase times without the profiler, from process_time wrappers:
- `guide_detail` x80: 26.1 s, of which `free_guides` 13.91 s (0.174 s per call; the seed load alone is 0.132-0.154 s: gunzip 0.032 s, json 0.105 s, and the regex split only 0.005 s), `visibility` 8.48 s, the page's `parse_guide` 2.10 s over 104 calls, `expand_includes` 0.55 s.
- The test's own `include_expanded` x80: 2.35 s.
- The reference `rxp_numbering` x13,440: 6.19 s; of that, `guide_dungeons` ~1.4 s.
- `X.numbering`: 1.23 s.
- In the profiler, 312 `parse_guide` calls, 18,592 `guide_dungeons` calls, 3.81 M `step_shown` calls, 122 M `dict.get` calls and 4.05 M `deepcopy` calls (10.5 s cumulative).
- Corpus: 13,491 steps after include expansion, 76,961 actions, 401 distinct conditions, 178 steps with conditional tags, 15 `.enabledungeon` actions. 12,402 steps (92%) carry no tag `step_shown` reads.

After the change: 9.34 s CPU median (9.29-9.36). In-process breakdown: `guide_detail` x87 4.02 s (`parse_guide` 1.95 s over 120 calls, `visibility` 0.72 s), reference `rxp_numbering` 2.02 s (`guide_dungeons` now runs 1,686 times instead of 13,440), corpus parse 1.19 s + expansion 0.31 s, `X.numbering` 0.27 s, `rxp_facts.parsed_guide` 0.17 s. `visibility` over 80 guides: 8.5 s -> 0.99 s; over 400 calls (5 option sets) 47.9 s -> 3.3 s. The reference loop alone: 6.40 s -> 2.55 s.

Player CLI `rxp_import.py guide rxp-free <i>`, fresh process, min of 5 runs, user+sys:
| guide | before | after |
|---|---|---|
| 79 (Zephras Dungeon, 847 steps) | 0.967 s | 0.534 s |
| 11 (Darkshore) | 0.527 s | 0.400 s |
| 32 (Durotar) | 0.428 s | 0.340 s |

`_build_cond` over 346 distinct conditions: 0.430 s -> 0.161 s.

SQLite, from strace -f -c:
- test_rxp_facts: 2,076 fsync, 6,571 pwrite and 523 unlink calls (519 journal creates). After the change: 60 fsync and 19 unlink calls; total syscalls 26,303 -> 6,741.
- Whole suite (109 tests): 3,994 -> 454 fsyncs and 1,990 -> 1,082 unlinks. Top before: rxp_facts 2,076, kb/test_rxp_updates 386, rxp_routes 364, kb/test_store 152, test_knowledge_seam 120, and 116 each in kb/test_analyzer, test_rxp_visibility, kb/test_live_logs, kb/test_community, kb/test_mechanics and kb/test_rxp_parser (the last five from fixture.glide_db's executescript).
- `create_schema` on an empty file database took 0.231 s on Linux for 85 autocommitted statements.

With strace adding 10 ms to each fsync:
| test | before | after |
|---|---|---|
| test_rxp_facts | 27.3 s | 1.6 s |
| test_rxp_routes | 9.7 s | 1.4 s (with its cached map) |
| kb/test_rxp_updates | 8.3 s | 4.1 s |
| kb/test_store | 3.2 s | 2.2 s |
| kb/test_live_logs | 2.3 s | 1.3 s |
| kb/test_analyzer | 3.0 s | 1.0 s |

Without the delay on Linux, test_rxp_facts wall 1.38 s -> 0.38 s and CPU 0.60 s -> 0.31 s.

test_rxp_routes in the profiler (5.73 s): `build()` 4.53 s (`fine_edges` 1.92, `joins` 1.42, `labels` 1.12, `condense` 0.68, `domain_reach` 0.64), route searches ~0.7 s, imports 0.25 s. CPU 5.15 s median -> 0.90 s with a warm map cache (4.87 s cold). The cached map's 17 arrays are equal to a fresh build.

test_rxp_conditions: CPU 2.36 s -> 1.22 s; tsc 0.32 s wall, node 0.16 s.

## Proposal: explorer-masks-and-coach-numbering — Faster masks, coach numbering and guide reading for the Guides page (test_rxp_explorer 40.5 s -> 9.3 s CPU)

**change**: Production changes:
- **companion/rxp_explorer.py `visibility()`.** Work out each distinct `<<` condition once, as a 56-bit player mask (`applies_to`). Work out the guide's dungeon rules per character from the header and the `.enabledungeon` steps only, and group characters by the result. Then walk the steps once: a step's mask is its condition mask, split by dungeon-rules group, with `step_shown` remembered per (flags key, dungeon rules) across all characters. Only steps with conditional tags still call `step_flags` per character.
- **`numbering()`.** Decode each distinct mask chunk once instead of once per step.
- **companion/rxp_import.py `free_guides()`.** The seed's free guides (gunzip + JSON + split) are parsed once per process, keyed by the seed's (mtime_ns, size); callers get copies. The installed-addon path is unchanged.
- **knowledge/rxp_parser/views.py.** `step_shown` returns True at once when the flags hold none of the 19 tags it reads (`_SHOWN_TAGS`). `visible_steps` and `rxp_numbering` share a generator `_walk`: `rxp_numbering` stops copying flags, and the guide's dungeon rules are worked out only when a step first asks about a dungeon (`_GuideDungeonsWhenAsked`).
- **knowledge/rxp_parser/conditions.py.** The race token lookup uses a dict; `_Player` objects are cached per argument tuple (`_player`, LRU 1024, typed); `_lit_true` is cached per (literal, player) with LRU 16384.
- **knowledge/rxp_parser/includes.py.** `expand_includes` copies steps with `_copy`, a dict/list/scalar copy that falls back to `copy.deepcopy` for any other type.

Test changes:
- **tests/test_rxp_explorer.py.** The reference numbering reads each guide as the knowledge build expands it (the corpus the test already builds) instead of a second `include_expanded` pass, and asserts the page's step texts equal the build's for every guide. Each character's condition answers are kept across guides, and the 3 settings dicts are built once. Darkshore and the Zephras `next_list` come from main-loop results already computed.
- **tests/test_rxp_visibility.py.** New check that `_SHOWN_TAGS` contains every tag `step_shown` reads, found by scanning its source.
- **tests/kb/test_rxp_parser.py.** New check that included steps share no containers with their source guide's steps.

**measured**: test_rxp_explorer CPU, 3 interleaved runs: 39.73 / 40.45 / 42.07 s -> 9.29 / 9.34 / 9.36 s (wall 40.9 -> 9.5 s). The steps, cumulatively:
- `visibility` rewrite plus seed memo: 43 -> 17.0 s.
- `step_shown` shortcut, lazy dungeon rules and step copy: about 16.5 s.
- Test reading the build's corpus and reusing results: 10.1 s.
- `_player` and `_lit_true` caches plus shared condition answers: 9.3 s.

Other tests, CPU before -> after: test_rxp_conditions 2.36 -> 1.22 s, test_rxp_live 1.04 -> 0.85 s, test_rxp_facts 0.60 -> 0.31 s (that last one together with the SQLite change). The coach's numbering without the `applies` cache (as live_logs StepMaps calls it): 30.7 -> 12.6 s per 35,840 calls. Player `guide` command CPU: 0.97 -> 0.53 s for the biggest guide, 0.53 -> 0.42 s for Darkshore, 0.43 -> 0.35 s for Durotar.

**saving_s_linux**: 32.5

**windows_note**: This is pure CPU in Python, so it scales about 1:1 on Windows: CI showed test_rxp_explorer at 29 s, so expect about 7 s. It no longer competes with test_navmesh for the end of the run. Nothing touches files, processes or fork. On the player's Windows PC, each Guides page guide open, and each `routes` call (which also runs `guide_detail`), uses about 0.1-0.45 s less CPU. The process-lifetime caches are small: the seed's guide texts (about 3 MB), up to 1,024 cached player objects and 16,384 cached literal answers.

**coverage**: Nothing is sampled or skipped: every (guide x 56 characters x 3 settings) comparison still runs, and the assertions are the same or stronger.
- **Reference input.** It is now the knowledge build's corpus-wide expansion instead of the page's own `include_expanded`. That is independent of the code under test, so the test now also checks that the page's expansion equals the build's for all 80 guides, where before it checked 3. I verified both give identical step lists for all 80 today.
- **Reused results.** Darkshore and the Zephras `next_list` are read from main-loop results computed in the same home state (no kb, no config). `next_list` does not depend on the kb. The 5 extra `guide_detail`-with-kb calls on Zephras guides had no assertion on their kb-dependent parts; the Durotar calls still exercise and assert the kb path.
- **Byte-identical output.** Compared against origin/main dumps for: `visibility` on 80 guides x 5 option sets; `rxp_numbering` on 80 x 56 x 8; `visible_steps` with dungeons on for every character; `guide_detail` JSON for all 80 guides.
- **Mutations.** Ignoring conditional tags, one set of dungeon rules for all characters, no include expansion, and a lazy lookup that ignores the guide all fail test_rxp_explorer. A tag missing from the shortcut fails test_rxp_visibility and kb/test_live_logs. Shallow list copies fail the new kb/test_rxp_parser check.
- **Pre-existing gap.** Dropping the `never` check in `visibility` passes, both before and after this change, because for these 56 characters a condition nobody meets already yields an empty mask. This is not a coverage loss.

**risk**: Low.
- `visibility` now relies on facts read from the code: `step_shown` depends only on (flags, settings, dungeon rules), and `guide_dungeons` reads only the header and the `.enabledungeon` steps. The parity test plus the new guards catch a drift.
- The lazy dungeon rules change only when an exception would surface on a malformed guide.
- `_copy` differs from `deepcopy` only if one step held the same container twice. The parser never does this; I checked the whole corpus and found 0 shared references.
- The caches in conditions.py rely on `_Player` never being changed after it is made (only `__init__` sets its attributes).
- knowledge/rxp_parser is part of the builders, so a refresh may rebuild once (the output is identical).
- The diff applies cleanly to origin/main 070107e.

**effort**: medium

**challenge (cov)**: holds=False, corrected saving 30.5 s

Reasons: The production changes hold up. One test change drops coverage that I could point to and confirm with a mutation, so the proposal does not hold as written. A small amendment fixes it.

What holds (checked in a scratch worktree at 070107e with the diff applied):
- **Same output, checked independently.** I dumped 148,566 hashed results from the old and new code and found 0 differences. They cover:
  - `free_guides`, and `expand_includes` stats and corpus;
  - `visibility` on 80 guides x 8 option sets (dungeons all or a list, season 1 or 2 with phase 3, xprate 1.5 or 0.5, phase 1);
  - `rxp_numbering` on 80 x 56 x 8 x 4 toggle combos, and `visible_steps` (flags included) with dungeons on for every character;
  - `guide_detail` on all 80 guides;
  - `cond_applies` over 413 raw conditions x odd race and class spellings x Alliance/Horde/Neutral/None x level, sex, season, locale and game, plus `parse_cond`.
- **No shared containers.** Within any expanded step there are 0, so `_copy` matches `deepcopy`. No duplicate guide keys exist, so `parsed[i]` really is guide i.
- **Shortcut tags are complete.** `_SHOWN_TAGS` equals the 19 tags `step_shown` reads.
- **`_build_cond` defaults match** `_Player`'s defaults, and `_Player` is only assigned in `__init__`.
- **No stale seed.** The seed is installed by `desktop/src/main/paths.ts` with `cpSync`, which sets a new mtime. No test rewrites a seed in-process, so the (mtime_ns, size) memo has no realistic staleness.
- **Nothing Windows-specific.**
- **The proposer's mutations behave as claimed.** One dungeon-rules group for all and coach numbering ignoring guide rules fail `test_rxp_explorer`. Dropping "dungeon" from the shortcut fails `test_rxp_visibility` and `test_live_logs`. Shallow copies fail the new `test_rxp_parser` check.
- **The new reference is stronger.** It checks the page's expansion against the build's for all 80 guides, where before only 3 were checked.

What breaks coverage:
- **The claim is incomplete.** The proposal says the 5 Zephras `guide_detail` calls made with kb.db present only covered parts that "Durotar still exercises". In fact they were the only calls in the whole suite that ran two branches:
  - `map_geometry`'s branch for a zone the kb doesn't know (rxp_explorer.py, `if not r and not b: continue`);
  - `place_world_points`' branch for a world point that can't be placed or is off the map (rxp_import.py, `return None` in `put`).
- **How I checked.** I measured line coverage with `sys.monitoring` for the old and new test, both on old code. I also covered every other test that reaches this code: test_rxp_import, test_rxp_routes, test_rxp_conditions, test_rxp_live, test_rxp_quests, test_rxp_facts, test_rxp_route, test_game_icons and test_target_icons, with node linked so none skipped.
- **Concrete mutation.** In `map_geometry`, change `bounds.get(um)` to `bounds[um]`, which assumes every zone has bounds in the kb. The Guides page would then fail to open Zephras guides (map 2521) for a player whose kb.db lacks that map.
  - Old `test_rxp_explorer` fails with `KeyError: 2521`.
  - Proposed test passes, and so do all 9 other tests listed above.

Smaller notes, not blocking:
- The `_SHOWN_TAGS` guard only sees tags written as `f.get("x")` or `f["x"]` in double quotes. A future rule in `step_shown` that hides steps without reading a flag would be skipped by the shortcut, and nothing would catch it.
- With lazy dungeon rules, an error from a malformed `.enabledungeon` only surfaces when a step asks about a dungeon.
- `free_guides` now runs the guide splitting inside the `(OSError, ValueError)` try; that code is regex-only, so this is harmless.

Measured CPU, 2 interleaved runs each (time.process_time / wait4 rusage):
- `test_rxp_explorer`: 40.58 / 40.87 s before, 9.29 / 9.46 s as proposed, 10.59 / 9.86 s with the amendment.
- Other tests: test_rxp_conditions 2.14 / 2.05 -> 1.11 / 1.09 s; test_rxp_live 1.02 -> 0.81 s; test_rxp_facts unchanged at 0.5 s (its claimed gain belongs to a separate SQLite change).
- `run_all` wall at -j4: test_navmesh (62.7 s) is the critical path, so the suite's wall time drops only about 5 s (67.9 -> about 63 s) until navmesh is also fixed. The roughly 31 s saved is CPU, which frees cores; on Windows CI it should save roughly 20 s of the test's 29 s.

Amendment: Keep the production changes and the other test changes. Change one thing in tests/test_rxp_explorer.py: build the Zephras `next_list` check from fresh `R.guide_detail(home, R.FREE_ID, i)` calls made after kb.db and kb.next exist, as origin/main does, not from the main loop's `details`. Add a comment that these calls also open guides whose maps the kb lacks.

That restores the only suite coverage of `map_geometry`'s branch for a zone missing from kb.db and of `place_world_points`' off-map branch. I checked that the amended test fails the `bounds[um]` mutation with `KeyError: 2521` and passes on the patched code. It costs about 0.4-1.3 s of CPU (amended 9.9-10.6 s vs proposed 9.3-9.5 s). Reusing `details` for Darkshore is fine, since no kb or config exists at that point.

Optionally, harden the `_SHOWN_TAGS` guard against single-quoted reads, and against a future `step_shown` rule that hides steps without reading a flag.

**prototype diff**: `explorer__explorer-masks-and-coach-numbering.diff`

## Proposal: sqlite-schema-one-transaction — Create kb.db and glide.db tables in one transaction (the Windows 20 s in test_rxp_facts; 3,994 -> 454 fsyncs suite-wide)

**change**: - **knowledge/store.py `create_schema`.** Run schema.sql as `conn.executescript("BEGIN;\n%s\nCOMMIT;" % script)`. If it fails, execute ROLLBACK when `conn.in_transaction` is true, then keep the existing statement-by-statement fallback for databases that already have the tables.
- **tools/build_db.py.** New `create_schema(db)`, which runs `executescript("BEGIN;\n%s\nCOMMIT;" % SCHEMA)`. `build()` uses it in place of `db.executescript(SCHEMA)`.
- **tests/kb/fixture.py `glide_db`.** Calls `build_db.create_schema(c)` in place of `c.executescript(build_db.SCHEMA)`.
- **tests/kb/test_store.py.** New `test_schema_one_transaction`: a trace callback must show BEGIN first and COMMIT last for both builders.

**measured**: strace -f -c counts:
- test_rxp_facts: fsync 2,076 -> 60; journal unlinks 523 -> 19; syscalls 26,303 -> 6,741.
- test_rxp_routes: 364 -> 28 fsyncs.
- kb/test_live_logs, kb/test_analyzer, kb/test_community, kb/test_mechanics, kb/test_rxp_parser and test_rxp_visibility: 116 -> 8 fsyncs each.
- kb/test_rxp_updates 386 -> 62, kb/test_store 152 -> 44, test_knowledge_seam 120 -> 12.
- Whole suite: 3,994 -> 454 fsyncs and 1,990 -> 1,082 unlinks.

Wall with 10 ms added to each fsync (strace inject, standing in for Windows), before -> after: test_rxp_facts 27.3 -> 1.6 s, test_rxp_routes 10.7 -> 5.7 s, kb/test_rxp_updates 8.3 -> 4.1 s, kb/test_store 3.2 -> 2.2 s, kb/test_live_logs 2.3 -> 1.3 s, kb/test_analyzer 3.0 -> 1.0 s.

Linux without the delay: test_rxp_facts wall 1.38 -> 0.38 s, CPU 0.60 -> 0.31 s. Empty-file `create_schema` took 0.23 s before.

**saving_s_linux**: 1.5

**windows_note**: This is the main Windows win. Each autocommitted statement on a default connection (journal_mode=DELETE, synchronous=FULL) creates the -journal file, FlushFileBuffers it and the database, then deletes it. Defender scans each new file. That is why test_rxp_facts takes 20 s on windows-latest but 1.4 s here. Suite-wide this removes about 3,500 fsyncs and 900 file deletions, roughly 35 s of aggregate wait at about 10 ms per flush, spread across the parallel workers. On the player's Windows PC, every glide.db build (tools/build_db.py) loses about 100 flushes and about 25 journal-file cycles. Nothing here is Linux-only.

**coverage**: The tables, columns and indexes created are identical: same script, same statements. The fallback path on an existing database ends in the same schema: the partial transaction is rolled back and every statement is re-run as before. No assertion changes. The new regression test fails when `create_schema` goes back to a bare `executescript`; I mutation-checked this.

**risk**: Low.
- SQLite DDL is transactional, and schema.sql has no PRAGMA, BEGIN or trigger statements.
- `executescript` already commits any pending transaction first, as it did before.
- An error part-way through now leaves no partial schema instead of a half-made one.
- tools/build_db.py and knowledge/store.py are in build_db's CODE fingerprint, so each player's glide.db rebuilds once after the update. The content is the same.

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

**prototype diff**: `explorer__sqlite-schema-one-transaction.diff`

## Proposal: rxp-routes-cached-walking-map — test_rxp_routes: build the synthetic walking map once per change of its builder (5.2 s -> 0.9 s CPU warm)

**change**: tests/test_rxp_routes.py gains `built_once(path, make)`. The key is the sha1 of numpy's version, wowfiles/{navbuild,navformat,adt,worldobjects}.py and the test file itself. If %TEMP%/glide-test-cache/rxp_routes-<key>.npz does not exist, it is built by the test's own `build()` + `save_graph()`, written to a temporary name with the PID in it, then os.replace'd into place. It is then copied into the test's temp home. The rest of the test is unchanged. The same helper could serve tests/test_navmesh.py's synthetic tiles; the other investigator owns that test.

**measured**: CPU over 4 interleaved runs: base 4.93 / 5.15 / 5.36 s; prototype 0.72 / 0.90 median, 4.87 s on the first (cold) run. Wall with 10 ms added to each fsync and this plus the SQLite change: 9.7 -> 1.4 s. The cached npz is 62 KB. All 17 arrays are equal to a fresh build.

**saving_s_linux**: 4.2

**windows_note**: Same CPU saving on the Windows dev PC, since the build is numpy CPU work. On GitHub's windows-latest runners the temp folder starts empty on every run, so CI gains nothing unless that folder is kept between runs with actions/cache, keyed on the hash of those files. Antivirus scans the 62 KB file once when it is first written. os.replace is atomic on NTFS, so two copies of the test running at once are safe.

**coverage**: Every route assertion runs on the same graph: the arrays are byte-equal to a fresh build. What is lost on a cache hit is the incidental run of navbuild's rasterise, surfaces, fine_edges, labels and condense inside this test. tests/test_navmesh.py exists to test exactly those. If anything in the key changes (navbuild, navformat, adt, worldobjects, this test or numpy), the map is rebuilt.

**risk**: A new module that navbuild imports would have to be added to the key, or a stale map could be used. The cache also leaves a persistent 62 KB file in %TEMP%. Optional; rank below the two above.

**effort**: small

**prototype diff**: `explorer__rxp-routes-cached-walking-map.diff`

## Proposal: remaining-fsync-audit — Audit the remaining fsync-heavy tests for Windows (follow-up)

**change**: After the SQLite change, the tests that still flush most are: kb/test_rxp_updates (62 fsync), test_refresh_staging (56), kb/test_store (44), test_community_feed (32), test_kb_build_guard (28), kb/test_map_bounds and test_rxp_explorer (16 each). Read each one's sqlite writes for autocommitted loops (DDL or one write per commit) and group them into one transaction. Where it is test-only fixture setup, PRAGMA synchronous=OFF and journal_mode=MEMORY could also be used. The release-notes and desktop tests delete many files (test_release_notes 243 unlinks, test_desktop_main 136, all without fsync); Defender scanning makes that slower on Windows too.

**measured**: Not prototyped. With 10 ms added to each fsync, the remaining 454 fsyncs suite-wide are worth at most about 4.5 s of aggregate wait on Windows; on Linux it is negligible.

**saving_s_linux**: 0.2

**windows_note**: Windows-only gain: FlushFileBuffers and journal-file create and delete under Defender. I estimate up to about 4 s of aggregate wait.

**coverage**: Batching writes into one transaction does not change what any test reads back. Setting synchronous=OFF on a test-only fixture database does not touch production code paths.

**risk**: Low. The one thing to watch: a test that deliberately exercises durability or journal behaviour (the refresh staging tests, for example) must keep its real settings.

**effort**: small

## Proposal: explorer-test-parse-memo-optional — Optional: memoise the pure parse_guide inside test_rxp_explorer (about -1.5 s)

**change**: In tests/test_rxp_explorer.py, wrap `rxp_explorer.parse_guide` with a cache keyed by (text, map_ids) that returns `includes._copy` of a pristine result. After the explorer proposal, the page still parses 120 times what the test's corpus already parsed 80 times.

**measured**: Not prototyped. Estimate: page parsing takes 1.95 s of the remaining 9.3 s, and copying costs about 0.3 s, so the saving is about 1.5 s CPU.

**saving_s_linux**: 1.5

**windows_note**: Pure CPU, so the same on Windows.

**coverage**: Every assertion is kept. It relies on parse_guide being pure, which its docstring states. The cost is that the test patches a production function, and every parse after the first is a copy rather than a re-run. I would only take it if the last 1.5 s matters. I rejected a stronger version, grouping characters by their condition answers (13.3 groups per guide instead of 56, about -1.5 s on the reference side), because it builds into the test the same assumption the new visibility code relies on.

**risk**: Patching a production function in a test hides any non-determinism in parse_guide. None is known.

**effort**: small

