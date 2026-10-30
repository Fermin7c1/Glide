-- Glide knowledge base (data/kb.db). Built from scratch by knowledge/build_kb.py.
-- Change log (append; never rename/remove):
--   2026-10-04  v1 initial schema.
--   2026-10-04  store: + tables creature_note, rxp_meta, talent_build, object_loot, quest_xp_table, area_box, quest_poi;
--               + columns quest.(xp_conf, xp_difficulty, xp_multiplier, quest_info, removed, hidden, field_src),
--               quest_giver.pos_src, creature.friendly_to, obs_kill.id_inferred (ALTERs in the store block at
--               the end); + name/item indexes. World axes: wx = world X (north), wy = world Y (west); see store.py.
--   2026-10-04  analyzer: + table guide_brief; + columns step_brief.(rxp_step_no, kind, exp_level_after, facts, race)
--               (ALTERs in the analyzer block at the end). step_brief.exp_level = expected level BEFORE the step.
--   2026-10-04  store: + column heuristic.priority (1 act now .. 3 background; filled by heuristics/engine.py
--               load); + index i_step_time on obs_step_time(guide_key, idx) (rows from sources/live_logs.py).
--   2026-10-08  community: + tables community_note, community_source (daily community scan, sources/community.py).

CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);

-- ------------------------------------------------------------------------------------------------
-- World entities. Every row: src (comma list of source tags), conf 0..1, verified (1 = seen on Forever).
-- ------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quest(
  id INTEGER PRIMARY KEY, name TEXT, level INT, min_level INT, side TEXT, races INT, classes INT,
  zone_area INT, zone_name TEXT, ui_map INT,
  xp INT, xp_src TEXT,                 -- XP at or below quest level + 5 (full value); see quest_xp_obs
  money INT, next_quest INT,
  type TEXT,                           -- normal|elite|group|dungeon|class|escort|timed|pvp|raid
  suggested_group INT, is_forever INT, in_client INT,
  objective_text TEXT, details TEXT,
  src TEXT, conf REAL, verified INT DEFAULT 0);

CREATE TABLE IF NOT EXISTS quest_objective(
  quest_id INT, idx INT,
  type TEXT,                           -- kill|collect|interact|explore|talk|escort|event|other
  target_kind TEXT, target_id INT,     -- npc|item|object|area
  amount INT, text TEXT,
  drop_from_kind TEXT, drop_from_id INT, drop_rate REAL,   -- for collect objectives
  src TEXT, conf REAL,
  PRIMARY KEY(quest_id, idx));

CREATE TABLE IF NOT EXISTS quest_prereq(
  quest_id INT, pre_id INT,
  kind TEXT,                           -- pre|pregroup (one of)|breadcrumb|exclusive|class|level
  src TEXT, conf REAL,
  PRIMARY KEY(quest_id, pre_id, kind));

CREATE TABLE IF NOT EXISTS quest_chain(chain_id INT, quest_id INT, pos INT, PRIMARY KEY(chain_id, quest_id));

CREATE TABLE IF NOT EXISTS quest_giver(
  quest_id INT, role TEXT,             -- start|end
  kind TEXT, entity_id INT, name TEXT, -- npc|object|item
  ui_map INT, x REAL, y REAL,
  src TEXT, conf REAL);

CREATE TABLE IF NOT EXISTS quest_reward(quest_id INT, item_id INT, qty INT, choice INT, src TEXT);

CREATE TABLE IF NOT EXISTS quest_xp_obs(                 -- raw observations, never aggregated away
  quest_id INT, player_level INT, xp INT, money INT, session TEXT, t INT);

CREATE TABLE IF NOT EXISTS creature(
  id INTEGER PRIMARY KEY, name TEXT, subname TEXT, min_level INT, max_level INT,
  rank INT,                            -- 0 normal 1 elite 2 rare elite 3 boss 4 rare
  type TEXT, family TEXT, reaction TEXT,  -- hostile|neutral|friendly (to the player's side when known)
  flee INT, caster INT, abilities TEXT,   -- abilities: json list of strings
  movement TEXT, danger_notes TEXT, hp_est INT,
  src TEXT, conf REAL, verified INT DEFAULT 0);

CREATE TABLE IF NOT EXISTS creature_spawn(
  creature_id INT, ui_map INT, x REAL, y REAL, n INT, respawn_s REAL, movement TEXT, src TEXT, conf REAL);

CREATE TABLE IF NOT EXISTS creature_drop(creature_id INT, item_id INT, rate REAL, n_obs INT, src TEXT, conf REAL,
  PRIMARY KEY(creature_id, item_id));

CREATE TABLE IF NOT EXISTS gameobject(id INTEGER PRIMARY KEY, name TEXT, type TEXT, src TEXT);
CREATE TABLE IF NOT EXISTS gameobject_spawn(object_id INT, ui_map INT, x REAL, y REAL, src TEXT, conf REAL);

CREATE TABLE IF NOT EXISTS item(
  id INTEGER PRIMARY KEY, name TEXT, class INT, subclass INT, inv_type TEXT, quality INT, ilvl INT,
  req_level INT, stats TEXT, sell_price INT, buy_price INT, src TEXT);

CREATE TABLE IF NOT EXISTS vendor_item(npc_id INT, item_id INT, price INT, src TEXT, conf REAL, PRIMARY KEY(npc_id, item_id));

CREATE TABLE IF NOT EXISTS spell(
  id INTEGER PRIMARY KEY, name TEXT, rank INT, class TEXT, race TEXT, level INT, cost INT,
  kind TEXT,                           -- class|weapon|profession|riding|racial
  notes TEXT, src TEXT);

CREATE TABLE IF NOT EXISTS trainer(npc_id INT, name TEXT, class TEXT, kind TEXT, ui_map INT, x REAL, y REAL, src TEXT, conf REAL);

CREATE TABLE IF NOT EXISTS poi(                          -- points of interest
  kind TEXT, id INT,                    -- inn|flight|graveyard|vendor|trainer|mailbox|boat|zeppelin|hearth
  name TEXT, faction TEXT, ui_map INT, x REAL, y REAL, continent INT, wx REAL, wy REAL,
  src TEXT, conf REAL,
  PRIMARY KEY(kind, id));

CREATE TABLE IF NOT EXISTS flight_path(faction TEXT, from_id INT, to_id INT, time_s REAL, cost INT, src TEXT, conf REAL,
  PRIMARY KEY(faction, from_id, to_id));

CREATE TABLE IF NOT EXISTS area(area_id INTEGER PRIMARY KEY, name TEXT, ui_map INT, parent INT, level_min INT, level_max INT,
  x1 REAL, y1 REAL, x2 REAL, y2 REAL);

CREATE TABLE IF NOT EXISTS map(ui_map INTEGER PRIMARY KEY, name TEXT, parent INT, area_id INT, continent INT,
  width_yd REAL, height_yd REAL);

CREATE TABLE IF NOT EXISTS map_bounds(                   -- world position of map corners; exact when src=obs
  ui_map INTEGER PRIMARY KEY, continent INT, wx0 REAL, wy0 REAL, wx1 REAL, wy1 REAL, src TEXT, conf REAL);

CREATE TABLE IF NOT EXISTS travel_edge(                  -- observed run times between points
  ui_map INT, x1 REAL, y1 REAL, x2 REAL, y2 REAL, dist_yd REAL, time_s REAL, n_obs INT, src TEXT);

-- ------------------------------------------------------------------------------------------------
-- RestedXP guides, fully parsed
-- ------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rxp_guide(
  key TEXT PRIMARY KEY, name TEXT, grp TEXT, subgroup TEXT, next TEXT,
  level_min INT, level_max INT, faction TEXT, default_for TEXT, season TEXT, source TEXT, text TEXT);

CREATE TABLE IF NOT EXISTS rxp_step(
  guide_key TEXT, idx INT,
  text TEXT,                           -- the step's >> lines joined with newlines (colour codes stripped)
  flags TEXT,                          -- json: {optional, sticky, completewith, label, requires, loop, xprate, hardcore, softcore, season, ...}
  cond TEXT,                           -- json: {"class": [...], "not_class": [...], "race": [...], "faction": ..., "raw": "<< ..."}
  ui_map INT, x REAL, y REAL, continent INT, wx REAL, wy REAL, zone TEXT,
  comments TEXT,                       -- -- comments attached to this step (RXP author notes, XP accounting)
  PRIMARY KEY(guide_key, idx));

CREATE TABLE IF NOT EXISTS rxp_action(
  guide_key TEXT, idx INT, line INT,
  verb TEXT, args TEXT,                -- args: json (parsed per verb)
  quest_id INT, entity_kind TEXT, entity_id INT, qty INT,
  text TEXT, cond TEXT,                -- per-line >> text and << condition
  PRIMARY KEY(guide_key, idx, line));

-- ------------------------------------------------------------------------------------------------
-- Derived: route analysis
-- ------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS step_brief(
  guide_key TEXT, idx INT, class TEXT,  -- class '' = class-agnostic
  exp_level REAL, xp_now INT, xp_unlocked INT,
  time_s REAL, travel_s REAL, kill_s REAL, xp_per_min REAL, risk REAL,
  justification TEXT, alternatives TEXT, deps TEXT,      -- json
  computed TEXT,
  PRIMARY KEY(guide_key, idx, class));

-- ------------------------------------------------------------------------------------------------
-- Heuristics, glossary, parameters
-- ------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS heuristic(
  id TEXT PRIMARY KEY, title TEXT, applies TEXT, advice TEXT, rationale TEXT, tags TEXT,
  source TEXT, conf REAL, verified INT DEFAULT 0);

CREATE TABLE IF NOT EXISTS glossary(term TEXT PRIMARY KEY, definition TEXT, aliases TEXT);

CREATE TABLE IF NOT EXISTS param(
  key TEXT PRIMARY KEY, value TEXT, unit TEXT, classic_value TEXT,
  status TEXT,                         -- classic|forever-verified|forever-fitted|unverified
  evidence TEXT, updated TEXT);

-- ------------------------------------------------------------------------------------------------
-- Observations (ingested from recorder sessions and companion logs)
-- ------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS obs_session(id TEXT PRIMARY KEY, char TEXT, class TEXT, race TEXT, faction TEXT,
  level_start INT, level_end INT, started INT, ended INT, play_s INT, guide TEXT);
CREATE TABLE IF NOT EXISTS obs_step_time(session TEXT, guide_key TEXT, idx INT, t_start INT, t_end INT,
  level REAL, xp_gain INT, deaths INT);
CREATE TABLE IF NOT EXISTS obs_kill(session TEXT, t INT, creature_id INT, name TEXT, mob_level INT, player_level INT,
  xp INT, rested INT, ui_map INT, x REAL, y REAL, ttk_s REAL);
CREATE TABLE IF NOT EXISTS obs_death(session TEXT, t INT, ui_map INT, x REAL, y REAL, level INT, killer TEXT,
  release_t INT, alive_t INT, corpse_run_s REAL);
CREATE TABLE IF NOT EXISTS obs_respawn(creature_id INT, ui_map INT, x REAL, y REAL, respawn_s REAL, n INT);
CREATE TABLE IF NOT EXISTS obs_xp(session TEXT, t INT, level INT, xp INT, xp_max INT, rested INT);
CREATE TABLE IF NOT EXISTS obs_trail(session TEXT, t INT, ui_map INT, x REAL, y REAL, combat INT);
CREATE TABLE IF NOT EXISTS obs_gossip(session TEXT, t INT, npc_id INT, npc_name TEXT, quest_id INT, state TEXT);  -- avail|active
CREATE TABLE IF NOT EXISTS obs_loot(session TEXT, t INT, src_kind TEXT, src_id INT, item_id INT, qty INT, ui_map INT, x REAL, y REAL);
CREATE TABLE IF NOT EXISTS eval_flag(t INT, session TEXT, kind TEXT, text TEXT, state TEXT, note TEXT);

CREATE INDEX IF NOT EXISTS i_quest_zone ON quest(ui_map, level);
CREATE INDEX IF NOT EXISTS i_qobj ON quest_objective(target_kind, target_id);
CREATE INDEX IF NOT EXISTS i_qgiver ON quest_giver(quest_id);
CREATE INDEX IF NOT EXISTS i_cspawn ON creature_spawn(creature_id);
CREATE INDEX IF NOT EXISTS i_cspawn_map ON creature_spawn(ui_map);
CREATE INDEX IF NOT EXISTS i_action_quest ON rxp_action(quest_id);
CREATE INDEX IF NOT EXISTS i_action_guide ON rxp_action(guide_key, idx);
CREATE INDEX IF NOT EXISTS i_obs_kill ON obs_kill(creature_id);

-- ------------------------------------------------------------------------------------------------
-- Store lane additions (2026-10-04). New columns use ALTER so the v1 statements above stay intact;
-- store.create_schema tolerates re-applying them. World axes: wx = world X (north), wy = world Y (west).
-- ------------------------------------------------------------------------------------------------
ALTER TABLE quest ADD COLUMN xp_conf REAL;               -- confidence of quest.xp (1 = observed at full value)
ALTER TABLE quest ADD COLUMN xp_difficulty INT;          -- RewardXPDifficulty (client cache): column of quest_xp_table
ALTER TABLE quest ADD COLUMN xp_multiplier REAL;         -- RewardXPMultiplier (client cache), 1.0 = normal
ALTER TABLE quest ADD COLUMN quest_info INT;             -- QuestInfoID: 0 normal 1 group/elite 41 pvp 62 raid 81 dungeon 84 escort
ALTER TABLE quest ADD COLUMN removed INT;                -- 1 = removed in Forever (ForeverGuide)
ALTER TABLE quest ADD COLUMN hidden INT;                 -- 1 = hidden / not offered (ForeverGuide)
ALTER TABLE quest ADD COLUMN field_src TEXT;             -- json {field: source tag} (which source won each field)
ALTER TABLE quest_giver ADD COLUMN pos_src TEXT;         -- where x,y came from: a source tag, or 'spawn:<tag>'
ALTER TABLE creature ADD COLUMN friendly_to TEXT;        -- community friendlyToFaction A|H|AH (AH also covers neutral beasts)
ALTER TABLE obs_kill ADD COLUMN id_inferred INT DEFAULT 0;  -- 1 = creature_id resolved from the name (unique match)

CREATE TABLE IF NOT EXISTS creature_note(                -- RestedXP dangerous mobs, one row per DB entry
  name TEXT, zone TEXT, faction TEXT, creature_id INT, min_level INT, max_level INT, classification TEXT,
  movement TEXT, notes TEXT, location TEXT, src TEXT);

CREATE TABLE IF NOT EXISTS rxp_meta(key TEXT PRIMARY KEY, json TEXT);  -- RestedXP DB tables (skipPreReq, questCompleteItems, ...)

CREATE TABLE IF NOT EXISTS talent_build(                 -- leveling talent order (empty until a source provides builds)
  class TEXT, spec TEXT, level INT, talent TEXT, notes TEXT, src TEXT);

CREATE TABLE IF NOT EXISTS object_loot(object_id INT, item_id INT, rate REAL, n_obs INT, src TEXT, conf REAL,
  PRIMARY KEY(object_id, item_id));                      -- like creature_drop, for game objects

CREATE TABLE IF NOT EXISTS quest_xp_table(               -- full quest XP by quest level and difficulty index
  level INTEGER PRIMARY KEY, d0 INT, d1 INT, d2 INT, d3 INT, d4 INT, d5 INT, d6 INT, d7 INT, d8 INT, d9 INT,
  src TEXT);                                             -- db2 (QuestXP.csv) or fit (most common known reward)

CREATE TABLE IF NOT EXISTS area_box(area_id INT, ui_map INT, x1 REAL, y1 REAL, x2 REAL, y2 REAL);  -- sub-zone boxes (map %)

CREATE TABLE IF NOT EXISTS quest_poi(                    -- where a quest's objectives happen / server waypoints
  quest_id INT, role TEXT,                               -- objective | objective<N> (Nth objective) | waypoint
  ui_map INT, x REAL, y REAL, n INT, src TEXT, conf REAL);

CREATE INDEX IF NOT EXISTS i_quest_name ON quest(name);
CREATE INDEX IF NOT EXISTS i_creature_name ON creature(name);
CREATE INDEX IF NOT EXISTS i_cnote_name ON creature_note(name);
CREATE INDEX IF NOT EXISTS i_cdrop_item ON creature_drop(item_id);
CREATE INDEX IF NOT EXISTS i_oloot_item ON object_loot(item_id);
CREATE INDEX IF NOT EXISTS i_vendor_item ON vendor_item(item_id);
CREATE INDEX IF NOT EXISTS i_gospawn ON gameobject_spawn(object_id);
CREATE INDEX IF NOT EXISTS i_qprereq_pre ON quest_prereq(pre_id);
CREATE INDEX IF NOT EXISTS i_area_box ON area_box(ui_map);
CREATE INDEX IF NOT EXISTS i_quest_poi ON quest_poi(quest_id);

-- ------------------------------------------------------------------------------------------------
-- Store lane additions 2 (2026-10-04): heuristic priority, step-time lookup by guide step.
-- ------------------------------------------------------------------------------------------------
ALTER TABLE heuristic ADD COLUMN priority INT;           -- 1 = say it now .. 3 = background (rules.json "priority")

CREATE INDEX IF NOT EXISTS i_step_time ON obs_step_time(guide_key, idx);

-- ------------------------------------------------------------------------------------------------
-- Analyzer lane additions (2026-10-04): knowledge/analyzer/route.py load() fills step_brief (one row per guide
-- step x class, the brief contract of knowledge/analyzer/README.md) and guide_brief (one row per guide x class).
-- ------------------------------------------------------------------------------------------------
ALTER TABLE step_brief ADD COLUMN rxp_step_no INT;       -- RestedXP's live step number (core.st) for this class/race
ALTER TABLE step_brief ADD COLUMN kind TEXT;             -- turnin|accept|complete|collect|grind|travel|hs|fly|...
ALTER TABLE step_brief ADD COLUMN exp_level_after REAL;  -- expected level after the step
ALTER TABLE step_brief ADD COLUMN facts TEXT;            -- json list of fact ids (q:<id>, c:<id>, p:<param>, ...)
ALTER TABLE step_brief ADD COLUMN race TEXT;             -- race the class was analysed as (guide #defaultfor)

CREATE TABLE IF NOT EXISTS guide_brief(                  -- per guide x class summary of the analysed route
  guide_key TEXT, class TEXT, start_level REAL, end_level REAL, time_s REAL, xp_total INT, xp_quests INT,
  xp_kills INT, xp_grind INT,
  hubs TEXT,                                             -- json [{idx, ui_map, x, y, turnins, xp, zone}]
  notes TEXT,                                            -- json {skipped_steps, skipped_by_reason, race, start_from,...}
  PRIMARY KEY(guide_key, class));

-- community intelligence (knowledge/community/: sources.json + intel/*.jsonl, gathered daily by the scheduled
-- community scan; loaded by knowledge/sources/community.py). Unverified unless official = 1 or status says so.
CREATE TABLE IF NOT EXISTS community_note(
  id TEXT PRIMARY KEY,                  -- fact id n:<id>
  seen TEXT, published TEXT,            -- ISO dates: when the scan found it, when the source published it
  kind TEXT,                            -- patch|tip|route|mechanic|class|dungeon|addon|data|news|warning
  title TEXT, claim TEXT, detail TEXT,
  level_min INT, level_max INT,
  zones TEXT, classes TEXT, factions TEXT, tags TEXT,   -- JSON lists
  sources TEXT,                         -- JSON list of {url, medium, author, date}
  official INT, confidence REAL,
  status TEXT,                          -- reported|corroborated|official|contradicted|superseded
  supersedes TEXT);                     -- JSON list of note ids
CREATE TABLE IF NOT EXISTS community_source(
  id TEXT PRIMARY KEY, kind TEXT, medium TEXT, name TEXT, url TEXT, access TEXT, status TEXT,
  first_seen TEXT, last_checked TEXT, notes TEXT);
