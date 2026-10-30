---
name: Glide to 60
description: A live leveling coach drawn as a second HUD in the grammar of the game's own interface, read at arm's length from a second monitor.
colors:
  night: "#0c0d12"
  night-deep: "#07080b"
  tip: "#13151c"
  tip-2: "#1a1d26"
  edge: "#30343f"
  edge-soft: "#22252e"
  ink: "#f1ede3"
  ink-2: "#bab5a8"
  ink-3: "#8b867b"
  gold: "#f2c24f"
  gold-hi: "#ffd877"
  gold-dim: "#9c7d36"
  on-gold: "#1d1504"
  leaf: "#eadfc6"
  leaf-2: "#d8c7a2"
  leaf-edge: "#6d5534"
  sepia: "#2a1e12"
  sepia-2: "#57452e"
  sepia-3: "#6b573b"
  rubric: "#7a2412"
  ahead-leaf: "#2f6b2c"
  behind-leaf: "#9b2414"
  warn-leaf: "#7a5200"
  ahead: "#4cd471"
  behind: "#ff6257"
  warn: "#ffad3b"
  xp: "#9a5cf2"
  rested: "#3d7fd9"
  hp: "#2fbf4a"
  q-poor: "#9d9d9d"
  q-common: "#f1ede3"
  q-uncommon: "#1eff00"
  q-rare: "#2a8cff"
  q-epic: "#b04cf5"
  q-legendary: "#ff8000"
  map-route: "#fffaf0"
  map-target: "#ff5a36"
  map-flight: "#4fd8ff"
typography:
  display:
    fontFamily: "Tenor Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "clamp(28px, 2.5vw, 40px)"
    fontWeight: 400
    lineHeight: 1.12
    letterSpacing: "-0.005em"
  headline:
    fontFamily: "Tenor Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "28px"
    fontWeight: 400
    lineHeight: 1.15
  title:
    fontFamily: "Tenor Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.2
  distance:
    fontFamily: "Geist, Segoe UI, system-ui, sans-serif"
    fontSize: "clamp(54px, 5.4vw, 84px)"
    fontWeight: 750
    lineHeight: 0.9
    letterSpacing: "-0.035em"
    fontFeature: "tnum"
  figure:
    fontFamily: "Geist, Segoe UI, system-ui, sans-serif"
    fontSize: "27px"
    fontWeight: 650
    lineHeight: 1
    letterSpacing: "-0.01em"
    fontFeature: "tnum"
  body:
    fontFamily: "Geist, Segoe UI, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.45
    fontFeature: "tnum"
  prose:
    fontFamily: "Source Serif 4, Georgia, serif"
    fontSize: "17.5px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Geist, Segoe UI, system-ui, sans-serif"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.3
  mono:
    fontFamily: "Cascadia Mono, Consolas, monospace"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.6
rounded:
  s: "5px"
  m: "7px"
  l: "9px"
  leaf: "4px"
  pill: "999px"
spacing:
  row: "9px"
  panel: "14px 18px"
  hud-gap: "18px"
  hud-gutter: "24px"
  page: "32px"
components:
  button:
    backgroundColor: "{colors.tip-2}"
    textColor: "{colors.ink}"
    rounded: "{rounded.s}"
    padding: "0 15px"
    height: "36px"
  button-primary:
    backgroundColor: "{colors.gold}"
    textColor: "{colors.on-gold}"
    rounded: "{rounded.s}"
    padding: "0 15px"
    height: "36px"
  button-primary-hover:
    backgroundColor: "{colors.gold-hi}"
    textColor: "{colors.on-gold}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.s}"
    height: "36px"
  input:
    backgroundColor: "{colors.night-deep}"
    textColor: "{colors.ink}"
    rounded: "{rounded.s}"
    padding: "7px 10px"
    height: "36px"
  tooltip-panel:
    backgroundColor: "{colors.tip}"
    textColor: "{colors.ink}"
    rounded: "{rounded.l}"
    padding: "{spacing.panel}"
  quest-leaf:
    backgroundColor: "{colors.leaf}"
    textColor: "{colors.sepia}"
    rounded: "{rounded.leaf}"
    padding: "26px 40px 20px"
  rail-item-active:
    backgroundColor: "{colors.tip}"
    textColor: "{colors.gold}"
    rounded: "{rounded.m}"
  tab-active:
    textColor: "{colors.gold}"
    typography: "{typography.title}"
---

# Design System: Glide to 60

## Overview

**Creative North Star: "The Second HUD"**

Glide is drawn in the grammar of the game's own interface, so a World of Warcraft player knows where everything is before reading a word: a unit frame, buffs and pace across the top, the quest page in the centre, the coach's chat bottom-left, a round minimap with the quest tracker on the right, a segmented XP bar along the bottom edge. Every piece is redrawn from scratch and with restraint; WoW is the inspiration, not the source of assets, except where the player has granted one: so far the game's own buff icons, read at runtime from the player's own install and never shipped.

The world has two materials. Night ground carries tooltip panels: near-black, a 1px edge, a faint light along the top, a soft drop. On it sits exactly one parchment leaf per screen, for the one current thing (the quest step on the dashboard, the status, refresh or addon plate in the shell), set in sepia ink with rubric-red headings. Quest gold marks titles, the current thing and the single primary action. Everything else is quiet ink on night.

The density is a game HUD's: many small readouts, each placed by habit rather than by card grid, with the glance data (distance, XP/h, time to level) set large in tabular Geist so it reads from arm's length. The world refuses the web dashboard of equal free-floating cards: the player can rearrange the pieces (arrange mode), but only within the HUD's own parts, so the top bar, the middle and the bottom bar always stay where a player expects them.

**Key Characteristics:**
- Night ground with tooltip panels as the one container.
- One parchment leaf per screen for the current thing.
- Quest gold for titles, the current thing and one primary action per area.
- Game-native colour codes kept to their meanings: item quality on items, class colour on the character name, purple XP with rested blue.
- Tenor Sans titles, Geist for interface and every number, Source Serif 4 for quest prose.
- HUD regions in fixed places; more width docks the book into wing columns instead of stretching.

## Colors

A night palette with one warm signal (quest gold), one warm material (parchment), and the game's own colour codes held strictly to their meanings.

### Primary
- **Quest Gold** (gold): titles on night, the active rail item and tab, the live tracker step, links, focus rings, the one primary button per area. Its brighter step (gold-hi) is hover and focus; its dim step (gold-dim) rings the medallion, minimap and leaf, and your own buff icons. Text on gold uses on-gold.

### Secondary
- **Parchment Leaf** (leaf, leaf-2, leaf-edge): the quest page and its shell counterparts, a radial wash from leaf to leaf-2 with a leaf-edge line, a dark mat and a gold-dim outer ring.
- **Sepia Inks** (sepia, sepia-2, sepia-3): text on the leaf only, from body to secondary to meta.
- **Rubric** (rubric): leaf headings, the step's quest-giver mark and the waypoint arrow.

### Tertiary
- **XP Purple** (xp) and **Rested Blue** (rested): the XP bar fill and the rested-XP lead beneath it, and the XP/h sparkline.
- **Health Green** (hp): the health gauge. Power gauges follow the game's power colours (mana, rage, energy, focus, runic) inside the unit frame only.
- **Item Quality** (q-poor through q-legendary): item names and nothing else.
- **Class Colour**: the character's name in the unit frame, set per class at runtime (`--class`) from the game's class palette.

### Map Marks
- **Route** (map-route): the walking route on the minimap, a near-white line on a black casing with gold dashes flowing toward the target.
- **Target** (map-target): the current waypoint, a red-orange disc with a white rim; on arrival (under 5 yd) it becomes a ring around the player's arrow. Later waypoints are small white dots.
- **Flight** (map-flight): flight paths and the landing point.

### Neutral
- **Night** (night) and **Night Deep** (night-deep): page ground; night-deep for the rail, session bar, XP foot, inputs and logs.
- **Tooltip** (tip, tip-2): panel faces, top to bottom; tip-2 also button faces.
- **Edges** (edge, edge-soft): panel outlines and row dividers.
- **Inks** (ink, ink-2, ink-3): primary, secondary and meta text on night.
- **Signals on night** (ahead, behind, warn) and **on the leaf** (ahead-leaf, behind-leaf, warn-leaf): pace and state. The leaf inks exist because the night signals fail contrast on parchment.

### Named Rules
**The One Parchment Rule.** Each screen carries exactly one leaf, and it holds the current thing. A second leaf, or parchment as decoration, breaks the hierarchy.

**The Gold Is Earned Rule.** Gold marks titles, the current thing, and one primary action per area. Two gold buttons side by side means one of them is wrong.

**The Item Colour Rule.** Item-quality colours appear only on item names. Uncommon green is never a success colour; ahead is.

**The Right Ink Rule.** On the leaf use the leaf-safe state inks; the night signals are for night.

**The Map Marks Rule.** Marks on the minimap use colours the map art doesn't (near-white, red-orange, cyan), each with a dark outline, and are sized in screen pixels whatever the zoom, so they read at a glance over any terrain. Never tone them down to blend in; a route always ends on a target.

**The Not By Colour Alone Rule.** Every state also reads by form or text: dashed rings for in progress, filled marks for done, a knob position, "(unsaved)", "(unchecked)", a struck-through idle model.

## Typography

**Display Font:** Tenor Sans (with Segoe UI, system-ui)
**Body Font:** Geist (with Segoe UI, system-ui), tabular figures throughout
**Prose Font:** Source Serif 4 (with Georgia)
**Mono Font:** Cascadia Mono (with Consolas), logs and code only

**Character:** Tenor Sans gives titles an engraved, lapidary calm at weight 400 only; Geist carries the instrument readings with tight tabular numerals; Source Serif 4 is the voice of the quest text, as a quest log reads. All three are bundled woff2 files with their OFL licences in `companion/static/fonts`; nothing loads from the web.

### Hierarchy
- **Display** (Tenor Sans 400, clamp(28px, 2.5vw, 40px), 1.12): the quest title on the leaf; also the empty-state title (36px) and leaf plate titles (28-30px).
- **Headline** (Tenor Sans 400, 28px, 1.15): shell page titles, in gold.
- **Title** (Tenor Sans 400, 15-20px): zone names, leaf rubric heads (17px). Panels have no printed heads: their crests name them.
- **Distance** (Geist 750, clamp(54px, 5.4vw, 84px), 0.9, -0.035em): the yards to the waypoint, the largest thing on screen.
- **Figure** (Geist 650, 22-27px, 1): XP/h, to level, the step number, the session cost.
- **Body** (Geist 400, 15px, 1.45): interface text; rows at 14-14.5px; emphasis at 550-650.
- **Prose** (Source Serif 4 400, 16-18px, 1.5-1.55, max 64-70ch): the quest's why on the leaf, plate and empty-state paragraphs.
- **Label** (Geist 400, 12-13px, ink-3): captions under figures, meta lines, timestamps. Sentence case, no tracking.

### Named Rules
**The Every Number Is Geist Rule.** All numbers, on night or on the leaf, are Geist with tabular figures, so readouts do not jitter as they tick.

**The Titles Never Bold Rule.** Tenor Sans is used at 400 only; hierarchy comes from size and colour.

## Layout

The dashboard is a HUD grid, not a card grid: regions named top (unit frame, auras, pace), warn (an error banner), wing, centre (the leaf over chat and the tabbed book), side (zone, round minimap, tracker) and foot (the XP bar with bags and hearth). Gaps are 18px vertical, 22px horizontal, with a 24px gutter; the side column is clamp(290px, 23vw, 500px), the round minimap up to 460px. The shell is an 84px icon rail on night-deep, pages with a sticky blurred page head (22px 32px), 32px page padding, and a 46px session bar pinned at the bottom.

Width tiers:
- **Under 860px:** one column in priority order (top, warn, centre, side, foot); the wing is hidden.
- **Under 1200px:** the page scrolls; the centre stacks leaf, chat, book.
- **1200px and up (with 640px height):** the HUD fits the viewport.
- **From about 1840px:** book sections dock into always-open wing columns, one column per 400px beyond 1440px, in the book's order (by default quests, gear, buffs; then why, levels; then community, session), cut into columns of 3, 2 and 2.

**Arrange mode** (the Layout button in the XP line) lets the player rearrange the HUD within its parts: the top bar's pieces and the pace block's stats reorder; the middle's five panels (quest page, chat, book, minimap, route tracker) trade slots, the slots and not the panels holding the grid places; the book's order changes by dragging tabs or docked sections; the bottom bar's pieces reorder. A piece never leaves its part. Pieces get a dashed gold-line outline, a drop line or a gold outline shows where a drag lands, Alt with the arrow keys moves the focused piece, Esc or Done leaves. The arrangement is saved with the engine (`/layout`). Panels adapt to the slot they land in: the quest page steps its type down in a narrow slot (a container query), the minimap grows in a wide one.

**Waiting for the game:** with no character, the leaf becomes a three-step checklist (addon, log in, start a guide; the done step ticked, the current one ringed in rubric), the empty gauges, auras, pace figures and coordinates are hidden, the book shows only Community, and the blank minimap is a quiet compass face.

The top bar and the session bar use container queries: the XP/h history plot appears and widens with the top's width, and session bar items drop in a stated priority order (cost last) as it narrows.

Motion is functional and small: the leaf's contents rise in (8px, blur 2px, 0.55s, staggered 50ms) when the step changes; the waypoint arrow rotates the short way round (0.6s); gauges and the XP bar ease their widths (0.4-0.6s). All share one ease-out (cubic-bezier(0.16, 1, 0.3, 1)). On the minimap the route's gold dashes flow toward the target and a new target pulses three times, then holds. Pieces moved in arrange mode glide to their new place (0.28s). Reduced motion collapses every transition and animation.

## Elevation & Depth

Depth is tooltip-like: panels are lifted by a ring and a soft drop rather than by tonal steps, and the leaf sits highest. Rings (stacked spread shadows) also draw the HUD's bezels on the medallion, minimap and leaf.

### Shadow Vocabulary
- **Tooltip** (`box-shadow: 0 0 0 1px var(--edge), inset 0 1px 0 var(--edge-hi), 0 10px 28px rgba(0,0,0,0.35)`): every panel on night.
- **Float** (`box-shadow: 0 0 0 1px var(--edge), 0 18px 40px rgba(0,0,0,0.55), 0 3px 8px rgba(0,0,0,0.4)`): toasts and chart tooltips that hover over content.
- **Leaf Bezel** (`box-shadow: 0 0 0 1px var(--leaf-edge), 0 0 0 4px #15130f, 0 0 0 5px var(--gold-dim), 0 22px 50px rgba(0,0,0,0.55)`): the parchment leaf.
- **Round Bezel** (`box-shadow: 0 0 0 3px #000, 0 0 0 5px var(--gold-dim), 0 0 0 6px #000, 0 16px 40px rgba(0,0,0,0.6)`): the minimap; the level medallion uses the same black-gold-black stack.
- **Well** (`box-shadow: inset 0 1px 2px rgba(0,0,0,0.5)`): inputs, gauges and the XP bar are sunk, not raised.

### Named Rules
**The Sunk Readout Rule.** Bars and fields are recessed into near-black wells; only panels and the leaf rise.

## Shapes

Small, tight corners: 5px controls, 7px rows and highlights, 9px panels, 4px for the leaf (paper is nearly square). Round forms are reserved for the HUD's own round things: the level medallion, the minimap, the waypoint arrow dial, the live dot. Pills (999px) carry tags and reasons. Bars are 3px-radius slots. Rows divide with 1px edge-soft rules; on the leaf, dividers are sepia at 25%.

## Components

### Buttons
Quiet tooltip buttons; gold only where it is the action.
- **Shape:** 5px corners, 36px tall, 15px side padding, Geist 550 14px.
- **Default:** tip-2 face, edge outline, a faint top light; hover darkens the face and turns the edge gold-dim; press drops 1px.
- **Primary:** a gold gradient (gold-hi to gold to a deep gold), on-gold text; one per area.
- **Ghost:** transparent, ink-2, gaining a tip-2 face on hover. Danger shows its red only on hover.
- **Focus:** 2px gold-hi outline, 2px offset.

### Inputs / Fields
- **Style:** sunk night-deep well, edge outline, 5px corners, 36px tall, gold caret.
- **Focus:** gold-dim edge plus a 3px gold-wash halo.
- **Changed:** an unsaved field's label turns warn and says "(unsaved)" in text.

### Switch
A 40 by 22px track; state is the knob position and the fill (gold with on-gold knob), with a text state beside it.

### Navigation
- **Rail:** 84px, icon above a 12px label, ink-3; hover ink on tip; active gold on gold-wash. Gold count badges on night-deep.
- **Settings tabs:** active gold on gold-wash with a gold-line inset.

### Emblem (signature)
No headers, no tabs, no labels on panels, anywhere on the coach screen. A panel says what it is by its emblem: the section's glyph (Lucide, 76px, gold at 7.5% opacity) set into the top right of its ground, behind the content, where a panel's content rarely reaches. The name and the panel's reading are its accessible heading only (`h2.sr`). The panel moves by its grip, an invisible 14px strip along its top edge. The book is one page of its sections, one after another (a hairline and space between them), each with its own emblem; the wing docks them into columns as width grows. Glyphs: quests scroll, gear sword, buffs sparkles, why lightbulb, talents and levels a rising line, community people, session clock, coach voice lines, route a winding path. The minimap's zone name is engraved on the map under its rim. New community notes light the Community emblem gold (the level-up).

### Tooltip Panel
The world's one container: a tip-2 to tip gradient over 90px, 9px corners, the tooltip shadow. Status and Memory put all groups in one panel with group titles as dividers.

### Quest Leaf (signature)
The parchment page for the current step: a quest-giver mark in rubric (! to accept, ? to turn in, an icon to complete or travel), the Tenor title, the why in Source Serif, then a ruled band with the rotating arrow dial, the distance in yards and the time delta against the route, then rubric-headed objectives (en-dash bullets, counts right-aligned) and rewards, and a sepia foot line.

### Unit Frame
A gold-ringed level medallion overlapping a tooltip body: class-coloured name, health and power gauges (15px wells, value centred in white with a dark text shadow), durability as a small bar with its figure.

### Quest Tracker
Rows with the step mark, the step in a muted gold, and the time delta right-aligned. The live step gets a gold-wash gradient and a gold-line inset ring. Done steps fall to ink-3.

### Buff Auras
The game's own spell icons (the player's install, read at runtime), 36px squares with 3px corners on a black-and-edge ring, a gold-dim ring for the player's own casts, the time under each in Geist (red under a minute). With no icon (weapon enchants, no install found) the tile shows the buff's initial in Tenor Sans. The Buffs list repeats the icon at 24px beside each name.

### XP Bar
A 14px black slot with 20 tick segments, a purple gradient fill, a translucent rested-blue lead, and a 2px gold planned-position marker. The line below carries level, rested, bags and hearth.

## Do's and Don'ts

### Do:
- **Do** keep exactly one parchment leaf per screen, for the current thing.
- **Do** use gold for titles, the current thing and one primary action per area.
- **Do** set every number in Geist with tabular figures.
- **Do** carry state by form or text as well as colour: dashed rings for in-progress, filled marks for done, "(unsaved)", "(unchecked)", struck-through idle models.
- **Do** use the leaf-safe inks (ahead-leaf, behind-leaf, warn-leaf) for state on parchment.
- **Do** dock book sections into wing columns as width grows, rather than stretching panels.
- **Do** bundle every font and asset locally.

### Don't:
- **Don't** use item-quality colours for anything but item names.
- **Don't** use class colours for anything but the character.
- **Don't** use Blizzard art, icons, fonts or textures unless the player has granted that exception; granted so far: buff icons, read at runtime from the player's own install, never shipped with Glide.
- **Don't** add a second parchment, or parchment as decoration.
- **Don't** put two gold primary buttons in one area.
- **Don't** set Tenor Sans bold, or use Cascadia Mono outside logs and code.
- **Don't** let arrange mode move a piece out of its part, or lay the HUD out as equal free-floating cards.
