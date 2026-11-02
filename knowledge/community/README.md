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
6. **New sources, across all media.** Discovery is half the job: the registry must keep growing beyond the
   sources it already has, and beyond code and addons. Every run spends about a third of its budget on it,
   rotating through the media below so each one is searched at least every two days:
   - **Video:** YouTube channels and new videos (WoW Forever + leveling, route, class guide, world first, race to
     60), Twitch (the `wowforever` tag, racers and their VODs), TikTok/Shorts only when they carry a concrete tip.
   - **Audio:** podcasts (Blizzard's WoW: Forever Podcast, community shows on RSS, Spotify, Apple).
   - **Social:** Reddit subs and recurring threads, X and Bluesky accounts of developers, dataminers, racers and
     guide writers.
   - **Chat:** Discord servers, found through public listings (discord.com/servers, disboard, disdex) and the
     sites that link them. Record the invite and topic; never join or read them.
   - **Web:** guide sites, databases, calculators, route planners, trackers and leaderboards (race trackers show
     real XP/hour), auction-house price and economy trackers, gold-making guides, guild-recruitment and LFG sites,
     wikis, newsletters, blogs.
   - **Code and addons:** GitHub search (repos created or pushed in the last 2 days), CurseForge, Wago,
     WoWInterface.
   - **Other languages:** search in the language: Chinese (NGA, Bilibili, Zhihu, "魔兽世界 永恒 练级"), Korean
     (Inven, Ruliweb, "와우 포에버 레벨업"), Russian ("WoW Forever прокачка"), German, French, Spanish, Portuguese,
     Polish.
   Prefer sources with first-hand data (measured XP and timings, datamining, leaderboards, routes) over reworded
   SEO guides; mark boosting and gold sellers' blogs as commercial in `notes`. Add each with `status: candidate`
   (or `active` when the run already used it), `kind`, `medium`, `access`, `first_seen` and a note on what it
   offers. Promote a candidate to `active` once a run has read something useful from it; set `dead` after two
   failed checks. A data repo with a known format gets an `adapter` only when `tools/build_seed.py` can read it;
   otherwise list it in the digest under "Data sources needing an adapter". The digest lists the run's new
   sources by medium and which media it searched.

### 4. Write notes
- Scope: everything a WoW Forever player needs to play well, with leveling 1-60 first.
  - **Leveling:** XP, rested/camps/food, quests and their XP, dungeons as leveling, respawns, routes, travel,
    level caps and phases, launch timing.
  - **Classes and combat:** class power, talents, spells and ranks, racials, rotations, gear upgrades.
  - **Gold and economy:** gold-making methods with numbers (farms, vendor items, profession profits, AH flips),
    prices and how they move, gold sinks and costs (mounts, training, repairs), the AH and trade rules.
  - **Professions:** what pays and what helps, recipes and where they come from, leveling a profession.
  - **Social:** guilds (features, recruiting, perks), grouping and LFG tools, communities, realm rulesets and
    population, faction balance, events, world PvP that affects play.
  - **Endgame prep:** raids, attunements and reputations, as they open.
  - **Addons, data and tools** for any of the above.
  Skip cosmetics, store items and drama unless they change how people play. Tag every note with its area
  (`leveling`, `class`, `gold`, `economy`, `profession`, `guild`, `social`, `pvp`, `endgame`) as well as the
  specifics, and keep `kind` to the fixed list (a gold method is a `tip`, a price trend is `data`).
- One checkable claim per note, with the numbers. No speculation presented as fact. Duplicates of an existing
  note become an update of that note (same id: new status, more sources), not a new note.
- Tag `model-impact` when a note changes something the knowledge base models (kill or quest XP formulas, rested,
  camp, respawn, flight times, level caps, mob levels); list those in the digest under "Model changes to review".

### 5. Save and report
- Write `knowledge/community/intel/<today>.jsonl`, `knowledge/community/digests/<today>.md` and the updated
  `sources.json` (`last_checked` dates, new sources).
- The final message: the most important findings (one line each, with the source), new sources, data refresh
  result, access problems. Plain and short. No notification.

