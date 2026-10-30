# The recorder's SavedVariables (`GlideRecorderDB`)

The Glide addon's recorder half (Core.lua, Recorder.lua, Scanner.lua, RXPDump.lua) keeps what it records in
`GlideRecorderDB`, written by WoW to `WTF/Account/<account>/SavedVariables/Glide.lua` at logout or `/reload`. The PC
side reads it back (contract 5, files on the player's disk): `tools/build_db.py` (sessions and RestedXP guide texts
into `data/glide.db`) and `knowledge/sources/map_bounds.py` (exact map bounds into `data/kb.db`), both through
`knowledge/sources/_lua.py` `recorder_db()`, which warns when the file's `schema` or a session's addon version is
newer than the code reading it knows. `companion/archiver.py` keeps copies, so the 12 sessions the addon keeps are
not the only ones. The live link's other SavedVariable, `GlideLinkDB`, holds settings only (Core.lua `ns.LinkDB`).

This is a hand-written description of the addon's code; the strip (addon to engine, live) is `docs/STRIP.md`.

## Top level

| Key | What |
|---|---|
| `schema` | Layout version of this table, `1` (Core.lua `rec.SCHEMA`). Raised only when a key changes meaning or goes away; new keys keep it. |
| `sessions` | `[sessionId] = session` (below); the 12 newest are kept (Core.lua `KEEP_SESSIONS`) |
| `lastSession` | Id of the newest session |
| `settings` | Recorder switches, `{ trail = true }` |
| `rxp` | RestedXP guide texts: `[key] = { name, group, text, len, how, t }` (`/glide rxpdump`, or automatically after a RestedXP update) |
| `rxpSteps` | RestedXP guides serialised as steps, by guide key |
| `rxpVersion` | The RestedXP version whose guides were last copied |
| `mapBounds` | `[uiMapID] = { instanceID, x0, y0, x1, y1, name }`: world yards of the map's (0,0) and (1,1) corners |
| `mapBoundsBuild` | Client version + build the bounds were read on (e.g. `1.60.1.70205`) |

## A session

One per login (Core.lua `newSession`), id `YYYYMMDD-HHMMSS`.

| Key | What |
|---|---|
| `id`, `started`, `ended` | Id, server time at login and at logout |
| `addon` | The addon version that recorded it (the TOC's `## Version`) |
| `build`, `iface`, `locale` | Client version + build, interface number, locale |
| `char` | `{ name, realm, class, race, faction, level }` at login |
| `quests` | `[questID]`: details, progress and completion text, rewards, XP, timings, positions (Recorder.lua) |
| `npcs`, `objects` | `[id]`: name, level, reaction, points where seen |
| `gossip` | `[npcID]`: the quest ids offered and active |
| `taxi`, `trainers`, `vendors` | Flight masters' nodes, trainers' services, vendors' items |
| `loot`, `kills`, `xp` | Lists: loot events (source and items), XP-from-kill events, the XP timeline |
| `trail` | Position breadcrumbs `{ time, map, x, y, inCombat }` |
| `events` | `{ e = kind, t, lvl, m, x, y, ... }`: level ups, deaths, zones, hearth, taxis, marks (`/glide mark`) |
| `scan` | Quest-ID sweep results (`/glide scan`) |
| `skills` | Trained skills at login, when the client reports them |
| `errors` | The first 50 errors of the session, as text: the whole addon's (recorder and live link), Core.lua `ns.ReportError` |
