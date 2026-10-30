# Heuristics sources

Every rule in `rules.json` lists its sources in `source`. All text in the rules, glossary and eval golds is
written in our own words; nothing is copied. This file lists what each source was used for and how much we
trust it.

## Source tags used in `source`

| Tag | Meaning |
|---|---|
| `https://...` | a public web page (listed below) |
| `classic-common-knowledge` | long-standing Classic (1.12 / Classic Era) mechanics and class play that are not tied to one page |
| `rxp-guide-text[:group/guide]` | RestedXP guide steps and author notes in `data/glide.db` `rxp_guides.text` (personal use, not redistributed) |
| `rxp-addon:<file>` | RestedXP addon source in `reference/rxp/` (ItemUpgrades.lua, functions.lua, DB/forever/*.lua, CHANGELOG.md) |
| `obs:<file>` | our own recorder/companion observations on the Forever beta |

## Confidence policy

- Classic mechanics with a wiki formula: conf 0.85-0.9.
- Class play and route judgement from guides or common knowledge: conf 0.55-0.85.
- Forever-specific systems: `verified: 0` and conf <= 0.5, except where Blizzard's own development notes (as
  republished by MMO-Champion) or our recorder confirm them (conf 0.6-0.9; `verified: 1` only for our recorder).
- `verified: 1` is used for exactly two rules: the 60-minute hearthstone cooldown and the levels 1-9 XP table.

## Classic mechanics

| URL | Used for |
|---|---|
| https://warcraft.wiki.gg/wiki/Mob_experience | kill XP formula (5*level+45), +5%/level above (cap +4), zero-difference table, gray level formula, elite x2, rested x2, group modifiers |
| https://warcraft.wiki.gg/wiki/Experience_points | quest XP decay (full to +5, then 80/60/40/20%, 10% from +10), 4,084,700 XP to 60 |
| https://warcraft.wiki.gg/wiki/Rest | rested accrual (inn/city 4x faster), cap 150% of a level, quest/exploration XP doesn't consume rest, consumption counts total XP |
| https://warcrafttavern.com/wow-classic/guides/rested-experience | instant logout in inns vs 20 s outside, 200% kill XP, cap 1.5 levels |
| https://wow.allakhazam.com/wiki/Resurrection_Sickness_(WoW) | sickness table: none 1-10, 1-9 min at 11-19, 10 min from 20, -75% stats |
| https://warcraft.wiki.gg/wiki/Die | 10% durability per death, ghost speed 125%, spirit healer penalties |
| https://warcraft.wiki.gg/wiki/Spirit_Healer | 25% durability for spirit resurrection (since patch 1.1.1) |
| https://www.wowhead.com/classic/item=6948/hearthstone | 10 s cast, 60 min cooldown (Classic) |
| https://warcraft.wiki.gg/wiki/Aggro_radius | ~20 yd at equal level, ~1 yd per level, min 5, max 45 |
| https://warcraft.wiki.gg/wiki/Daze | daze from behind: 50% slow 4 s, 20% base chance, up to 40%, lower under 30, dismount |
| https://warcraft.wiki.gg/wiki/Weapon_skill | max skill 5x level, low skill misses/glancing, near-100% skill gains when low |
| https://www.warcrafttavern.com/wow-classic/guides/weapon-skill-trainers-and-locations/ | weapon masters by city, 10 silver per skill, polearms 1g at 20 |
| https://warcrafttavern.com/wow-classic/guides/mounts | riding at 40: 20g + 80g mount, 10% discount at Honored; paladin/warlock class mounts |
| https://warcraft.wiki.gg/wiki/Quest_log | 20-quest cap in original WoW |
| https://warcraft.wiki.gg/wiki/First_Aid | skill tiers, 60 s Recently Bandaged, damage interrupts bandaging |
| https://warcraft.wiki.gg/wiki/Travel_guide_to_zeppelins_and_boats | routes, 60 s dock time, 3-5 minute cycles |
| https://www.wowhead.com/classic/guide/classic-wow-leveling-basics-tips-and-tricks | new spells every 2 levels, 1-hour hearth, ~90g mount, Classic leveling dungeons (Uldaman 41+, ZF 42+, Maraudon 46+, BRD 52+) |
| https://www.warcrafttavern.com/wow-classic/guides/how-long-to-60/ | cumulative XP at 10/20/30/40/50/60, Joana 4d20h record, typical 8-10 days |
| https://www.warcrafttavern.com/community/wow-classic-general/vanilla-classic-beta-speedrunning/ | record splits (L10 1h12m-1h33m, L20 6h30m-8h25m, L30 16h46m, L40 1d21h, L50 2d14h53m, L60 4d1h17m) used for the pace benchmark |

## Class leveling guides (Icy Veins, Classic)

| URL | Used for |
|---|---|
| https://icy-veins.com/wow-classic/classic-warrior-leveling-guide | slow two-handers, Arms at 30 (Sweeping Strikes), level 10/30 class quests, First Aid |
| https://icy-veins.com/wow-classic/fury-warrior-leveling-guide-1-60 | ability levels (Battle Shout 1, Charge/Rend 4, Hamstring 8, Bloodrage 10, Overpower 12, Execute 24), rotation priority, skip Thunder Clap |
| https://icy-veins.com/wow-classic/classic-mage-leveling-guide | Frost leveling, AoE from 22, wand, drinking |
| https://icy-veins.com/wow-classic/classic-hunter-leveling-guide | pet at 10, Beast Mastery, dead zone, Arcane Shot sparingly, Feign Death |
| https://icy-veins.com/wow-classic/classic-rogue-leveling-guide | Dual Wield at 10, swords solo / daggers group, First Aid, gathering for gold |
| https://icy-veins.com/wow-classic/classic-priest-leveling-guide | wand as main damage, Shadow at 40 |
| https://icy-veins.com/wow-classic/classic-warlock-leveling-guide | Imp 1-10, Voidwalker 10, DoTs + wand, Life Tap, free mount at 40 |
| https://icy-veins.com/wow-classic/classic-paladin-leveling-guide | auto-attack + Judgement, Seal of Command, two-handers, mount quest at 40 |
| https://icy-veins.com/wow-classic/classic-shaman-leveling-guide | Rockbiter, totem quests 4/10/20/30, Ghost Wolf 20, two-handers at 20, Stormstrike 40 |
| https://icy-veins.com/wow-classic/classic-druid-leveling-guide | Bear Form at 10, Cat Form at 20, five-second rule, Cat single / Bear multiple |

## WoW Forever

| URL | Used for | Status |
|---|---|---|
| https://www.mmo-champion.com/content/14573-World-of-Warcraft-Forever-Beta-Development-Notes-October-1 | Blizzard's Oct 1 beta notes: dungeon quests give 50% less extra XP; Boosted Rest debuff icon/tooltip; Excavation Site: Wetlands 26-31; faster respawns in Hillsbrad, Ashenvale, Wetlands, Duskwood, Thousand Needles; level cap 30 on beta | official (beta) |
| https://www.pcgamer.com/games/world-of-warcraft/the-wow-forever-betas-nerf-to-dungeon-xp-isnt-nearly-as-bad-as-it-seems/ | Deviate Hides 1,600 (Classic) -> ~4,650 -> ~3,100 XP | press, beta |
| https://kotaku.com/5-big-things-world-of-warcraft-forever-beta-2000739404 | level 30 beta, dungeon quest XP change | press, beta |
| https://massivelyop.com/2026/09/12/blizzcon-2026-world-of-warcraft-forever-announced-as-a-permanent-home-for-classic-players/ | launch 2026-11-04, level cap 60, Skyborne, new zones, 1,000+ quests, 9 dungeons | press |
| https://classicwow.gg/forever/changes | talents, Legacy system, camping object limits, new zones, Skyborne | community |
| https://classicwowforever.com/guides/wow-forever-zephras-isle-skyborne/ | Zephras Isle 1-12, Skyborne classes and racials, Valanaar zeppelin to Mulgore | community, beta |
| https://wowforeverguides.com/races/skyborne | Skyborne classes, racials, access | community |
| https://wowforeverguides.com/leveling/tips | campfire rest, crafted-food XP, Well Rested legacy perk, dungeon XP | community |
| https://wowforeverguides.com/systems/dungeon-xp | dungeon brackets, kill XP down / quest XP up (developer interview) | community |
| https://wowforeverguides.com/camping | camp object limits, Camping 101 at skill 20, Camp Tent rest up to 5% of a level | community |
| https://www.warcrafttavern.com/forever/guides/camping/ | campfire tiers, 10 min duration, 5 min cooldown, sit 1 minute for Camp Benefits | community |
| https://wowforever.be/guides/camping/ | Camp Tent: rested XP up to 5% of a level, then Boosted Rest for an hour | community |
| https://www.wowhead.com/forever/quest=95998/the-great-outdoors | The Great Outdoors objectives (/sit, gain Boosted Rest), relog workaround | community |
| https://lfcarry.com/guides/wow-forever-leveling | ~150 h to 60 for an average player, dungeon brackets, curve unchanged | community |
| https://lfcarry.com/guides/wow-forever-mounts | mount granted with riding training; price unannounced; no flying | community |
| https://timesaver.gg/blog/wow-forever-mount-cost-gold-to-save | TBC-style mount model, Classic 100g as a planning anchor | community |
| https://allthings.how/wow-forever-beta-level-30-cap-and-the-maintenance-window | beta cap 20 -> 30 on Oct 1 | press |

### RestedXP material (reference/rxp and glide.db guides)

- Forever guide text: Boosted Rest / Camp Benefits steps (/sit 60 s, relog if missing, craft while sitting), buff food
  "+5% kill XP for 15 minutes", Windstone crystals, tornadoes (+40% speed 5 min), Cooking 10 (Auberdine) and 50
  (Darkshire) requirements, First Aid while waiting for boats/trams, hearth-cooldown-aware grinding (Duskwood
  "<25 minutes", "<2 minutes"), deathskips, logout skips, the 25+ kills skip rule for dry collection quests,
  Deadmines Miners' Union Cards having no dungeon bonus XP, Bag of Marbles warning, Milly's Harvest skipped for
  non-priest/mage classes, Grik'nir the Cold inside the Frostmane cave.
- `ItemUpgrades.lua`: Forever proficiency rules (plate/mail at 35, polearms and off-hands at 18).
- `functions.lua`: `.deathskip` is softcore-only; a spell is cast on every Forever resurrection.
- `DB/forever/dangerousMobs.lua`: dangerous-mob notes (charge/stun, heals, patrols).
- `CHANGELOG.md` (v4.11.13): preserving cooking ingredients, Skyborne zeppelin route and ley-line reminder removal.

### Our observations (Forever beta, 2026-10-04)

- `data/live/20261004-154307.jsonl`: hearthstone cooldown read 3598 s immediately after a hearth -> 60 minutes.
- `data/live/*.jsonl`: durability stayed 100% through deaths at levels 2-8 and dropped only from level 10.
- `knowledge/README.md`: XP table for levels 1-9 and level-1 kill XP match Classic.
- `data/live/*.jsonl` player questions: all 110 logged utterances are eval items in `knowledge/eval/questions.jsonl`
  (ids `log-01`..`log-110`, source `data/live/<file>#L<line>`), and they shaped rules such as checking the drop
  source, Battle Shout reminders only with 10+ rage, believing the player about buffs, and odd-level training.

## Forever facts: confirmed vs not

Confirmed (official notes or our recorder): hearthstone 60 min; XP table 1-9 = Classic; dungeon quests give
50% less extra XP since Oct 1 (beta); faster respawns in five zones (beta); Boosted Rest exists as a debuff.

Not confirmed (community or RestedXP only; rules carry conf <= 0.5): campfire/tent rested amount (up to 5%
of a level) and Boosted Rest's duration; buff food +5% kill XP; plate/mail at 35; polearms/dual wield at 18;
mount level and price; Camping 101 at skill 20; Skyborne racial numbers and cooldowns; Zephras Isle range
(1-12 vs RestedXP's 1-14); dungeon brackets other than Excavation Site; dungeon kill-XP reduction size;
durability-free deaths below 10; resurrection sickness on Forever; quest log cap; rested accrual rate and the
Legacy Well Rested amount.
