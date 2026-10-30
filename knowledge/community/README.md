# Community intelligence: the WoW Forever community scan

The coach should know what the WoW Forever community knows, as soon as it's known: patch and hotfix changes,
XP and rested findings, route tricks, class leveling power, dungeon changes, new addons and data projects. A
scheduled task runs this scan several times a day. This file is its method and note format; the scheduled run's
machine-specific steps (where the project lives, how the run reaches it, its offline copies) are in
`RUNBOOK.local.md` next to it, which isn't in the repository. Players' coaches get the notes from the community feed
(its format: companion/community_feed.py).

```
knowledge/community/
  README.md                 this runbook
  sources.json              registry of every source (data repos, forums, news, guides, wikis, video, social,
                            Discord servers, addons, bug tracker), with how it can be read from here
  intel/YYYY-MM-DD.jsonl    notes found that day, one JSON object per line (format below)
  digests/YYYY-MM-DD.md     the day's human-readable digest
data/seed/seed.json.gz      community data snapshot (tools/refresh_community.py -> tools/build_seed.py)
data/seed/seed_report.json  revisions of every data repo, what moved, per-source counts and deltas
```

`knowledge/build_kb.py` loads `sources.json` and every `intel/*.jsonl` into `community_note` /
`community_source` (knowledge/sources/community.py). A new intel file makes kb.db stale, so the next
`start-glide.cmd` rebuilds it. The coach sees relevant notes in every callout's knowledge block
(`community`: zone, level or class match), can search all of them with the `community` tool, and cites them as
`n:<id>`. The coach screen's Community panel lists the newest. Players get new notes through the community feed
(companion/community_feed.py): their coach writes them into its kb.db as they land.

## Note format (one line per note in intel/YYYY-MM-DD.jsonl)

```json
{"id": "20261008-dungeon-quest-bonus-xp-halved", "seen": "2026-10-08", "published": "2026-10-01",
 "kind": "patch", "title": "Dungeon quest bonus XP cut 50%",
 "claim": "One checkable sentence with the numbers.",
 "detail": "Context, examples, how it affects leveling, what is still unknown.",
 "levels": [20, 30], "zones": ["Wetlands"], "classes": ["MAGE"], "factions": [], "tags": ["xp", "dungeon"],
 "sources": [{"medium": "blizzard-forums", "url": "https://...", "author": "Kaivax", "date": "2026-10-01"}],
 "official": true, "confidence": 0.95, "status": "official", "supersedes": []}
```

- `id`: `YYYYMMDD-short-slug` from the day it was first seen; never reused for something else.
- `kind`: patch, tip, route, mechanic, class, dungeon, addon, data, news, warning.
- `levels`, `zones`, `classes` (upper-case tokens: WARRIOR, MAGE, ...): fill them whenever the note applies to
  part of the game only. The coach matches on them; a note without them reaches the coach only through searches.
- `status`: `official` (a Blizzard source: dev notes, blue post, staff reply, official news, a designer on the bug
  tracker), `corroborated` (two independent sources, or it matches Glide's own recorded data), `reported` (one
  community source), `contradicted` (later evidence says it's wrong), `superseded` (replaced by a newer note).
- To update a note, write it again with the same `id` in today's file (a later line replaces the earlier one):
  new status, corrected numbers, more sources. To replace one with a new fact, add `supersedes: [old id]`.
- `sources`: the primary source first, then corroboration. Every note needs at least one URL.

## A run

Budget: thorough, not endless. A run only needs what changed since the last one: roughly 15-30
fetches, the official sources and the bug tracker every run, the rest in rotation, so every source you can read in
`sources.json` gets looked at at least every day or two. The day's runs share today's `intel/YYYY-MM-DD.jsonl`
(append to it) and today's digest (a section per run). The data refresh (step 2) runs once a day, in the first run.

### 1. Load state
Read `knowledge/community/sources.json`, `data/seed/seed_report.json`, and an index of existing notes (id, title,
first source URL for every line in `intel/*.jsonl`).

### 2. Data refresh (once a day)
With `pip install lupa`, run `python3 tools/refresh_community.py --cache <scratch>/repos`. When it rebuilt the
snapshot, keep `data/seed/seed.json.gz`, `data/seed/seed_report.json` and `addon/Glide/Data/QuestIDs.lua` (the
last one also goes to the game's copy of the addon). Report the moved repos and the deltas in the digest.

### 3. Intelligence sweep (always)
Work outward from the most authoritative sources, and follow every promising item to its primary source and to
a second, independent source. Never stop at the first layer of results.

1. **Official.** The beta development notes thread (`<url>.json`: new posts = new builds), the Blizzard forum
   category `https://us.forums.blizzard.com/en/wow/c/wow-forever/346/l/latest.json` (Discourse JSON: look for
   staff posts and maintenance/build topics), EU forums, the Arctium blue-post tracker
   (`https://arctium.io/blue-posts`, every Blizzard post in every region), Blizzard news, and posts by Blizzard
   developers on X (search, e.g. Aggrend).
2. **Bug tracker:** the GitHub API,
   `https://api.github.com/repos/ClassicWoWCommunity/forever-bugs/issues?state=all&sort=updated&per_page=50` and
   `.../issues/comments?sort=updated&direction=desc&per_page=100&since=<yesterday ISO>`. Blizzard designers
   answer here (e.g. Aidan "Zirene" Moon confirmed the low-level mob XP reduction). Look for staff answers and for
   issues about XP, quests, respawns, camps, mobs and classes.
3. **News and guides.** WebSearch for WoW Forever news of the last 2 days, then read each relevant article
   (Wowhead Forever, Icy Veins, Warcraft Tavern, Massively OP, PC Gamer, PCGamesN, Kotaku, Mein-MMO, Kami Labs,
   wowguide.net patch notes, wowforeverguides.com, the Forever wiki). Articles cite Reddit threads, X posts,
   Discord findings, datamining and videos: follow those links.
4. **Community discussion.** Blizzard forum topics of the last day with the most replies and likes (the JSON has
   posts_count, like_count, views): read the leveling-relevant ones. Reddit (r/wowforever, r/classicwow) and
   YouTube can't be fetched from here: find posts and videos with WebSearch (titles, snippets), and read them
   through the articles, guides and forum posts that quote them. Discord can't be read (it needs a logged-in
   account, and automating one breaks Discord's terms): record Discord findings when a public source reports
   them, citing that source.
5. **Datamining.** Watch for client-data diffs (talent proc chances, spell values, new quests): forum threads,
   articles, GitHub data projects. Mark them `reported` until a patch note or Blizzard post confirms.
6. **New sources.** Every run looks for sources not yet in the registry: new GitHub repos and addons
   (CurseForge, GitHub search), new guide sites, creators, wikis, trackers, Discord servers, data sites. Add each
   with `status: candidate` (or `active` when the run already used it), `kind`, `medium`, `access`, `first_seen`
   and a note on what it offers. A data repo with a known format gets an `adapter` only when
   `tools/build_seed.py` can read it; otherwise list it in the digest under "Data sources needing an adapter".

### 4. Write notes
- Only what matters for leveling 1-60 fast: XP, rested/camps/food, quests and their XP, dungeons as leveling,
  respawns, routes, travel, class leveling power, talents for leveling, professions that help leveling, level
  caps and phases, launch timing, addons and data that help. Skip endgame-only, cosmetics and drama unless they
  change leveling.
- One checkable claim per note, with the numbers. No speculation presented as fact. Duplicates of an existing
  note become an update of that note (same id: new status, more sources), not a new note.
- Tag `model-impact` when a note changes something the knowledge base models (kill or quest XP formulas, rested,
  camp, respawn, flight times, level caps, mob levels); list those in the digest under "Model changes to review".

### 5. Save and report
- Write `knowledge/community/intel/<today>.jsonl`, `knowledge/community/digests/<today>.md` and the updated
  `sources.json` (`last_checked` dates, new sources).
- The final message: the most important findings (one line each, with the source), new sources, data refresh
  result, access problems. Plain and short. No notification.

