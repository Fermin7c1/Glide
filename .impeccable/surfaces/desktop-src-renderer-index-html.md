---
version: 1
slug: "desktop-src-renderer-index-html"
primary_target: "desktop/src/renderer/index.html"
related_targets: ["companion/static/dashboard.html"]
---

# Glide desktop app: coach console, settings, addon, logs

Scope: the Electron shell (desktop/src/renderer: index.html, app.css, app.ts, overlay) and the coach dashboard it embeds (companion/static dashboard.*, glide.css). Mode: operate. Audience: WoW Forever levelers glancing at a second monitor from arm's length; strangers setting it up. Task: know the current step and where to go, XP pace, gear/buffs/reminders, in under a second; configure the app without help.

Pinned by the user (2026-10-24): lean into the WoW UI design language as inspiration only (no Blizzard assets, textures or fonts); the HUD composition of mock C with the quest-journal page of mock B in the centre; fonts "Clean": Tenor Sans titles, Geist interface and numbers, Source Serif 4 quest text. Nothing else is sacred. Mocks: .impeccable/mocks/reference/ (d-hud-journal is the merged pick; a, b, c are the three it came from; e-fonts.html is the font switcher).

Later user decisions (2026-10-24): exceptions to "no Blizzard assets" are granted one at a time; granted so far: the in-game buff icons, read at runtime from the player's own client (companion/icons.py), never shipped. Minimap route and waypoints must read at a glance (the user found the gold-on-map marks too subtle), so map marks use colours the map art doesn't, with dark outlines. The player can rearrange the HUD, but only within its parts (top bar, middle slots, bottom bar) in an explicit arrange mode.

## Direction contract

THESIS: The coach is a second HUD: the game's own interface grammar (unit frame, minimap, quest tracker, quest page, chat, XP bar) redrawn with restraint, so a WoW player knows where everything is before reading. It refuses the web dashboard of equal free-floating cards; the player rearranges pieces only within the HUD's own parts.

OWN-WORLD: Night ground (#0c0d12) with tooltip panels (near-black, 1px bevel edge, faint top light); quest gold (#f2c24f) for titles and the current thing; one parchment leaf per screen (#eadfc6, sepia ink, rubric red headings) for the current step; class colour for the character, item-quality colours for loot, purple segmented XP bar with rested blue. Tenor Sans titles, Geist UI with tabular figures, Source Serif 4 for quest prose.

STORY: The player glances over: the quest page says what and why, the arrow and yards say where, the tracker says what's next, the bar says how close the level is. A stranger finds Settings and Addon pages in the same calm tooltip language.

FIRST VIEWPORT: Icon rail left. Top: unit frame (level medallion, class-coloured name, health/power/durability), buffs with timers, pace (XP/h with history, to level, vs route). Right column: zone name over a round zoomable minimap, quest tracker below. Centre: parchment quest page (glyph, title 34-40px, why, arrow + yards 72px+, objectives, rewards, timing), tabbed book below. Bottom left: coach chat with ask box. Bottom edge: full-width segmented XP bar, bags and hearth at its right.

FORM: Pinned by the user (WoW HUD + quest journal); the roll was skipped because a user-pinned direction beats it (concept-seed --check-seed none: exit 0). seed: none

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
