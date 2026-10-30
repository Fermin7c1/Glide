# Mechanics verification backlog

Every parameter in `knowledge/mechanics/params.py` has a status: `classic`, `unverified`,
`forever-verified` or `forever-fitted`. The fit (`knowledge/mechanics/fit/`) upgrades a parameter by writing it to
`data/params.json`, and only when the evidence is strong. This file lists what is still open: for
each parameter, the in-game experiment, what the recorder and the engine capture, and how the fit
reads it. Run `python3 -m knowledge.mechanics.fit` after a session; the report is in
`data/mechanics_fit.json`.

Sources the fit reads:
- **glide.db** (recorder SavedVariables): `obs_kills`, `obs_xp`, `obs_quests`, `obs_trail`, `obs_events`.
- **Live logs** (`data/live/*.jsonl`, 1 Hz core frames): level, XP and max XP (`l x X`), rested pool
  `r`, `IsResting` (`rs`), speed `sp` (GetUnitSpeed ×10, floored), mounted/taxi/dead (`mo tx d`),
  target id/level/dead (`tg tl td`), kill counter and last kill XP (`kc kx`), hearth cooldown `hs`,
  weakest-item durability `du`, money `g`, and the event ring `e1..e3` (`learn`, `tin`, `die`, `hs`).
- **Live log pages**: auras (`A*`), map size in yards (`Z`), target classification (`T`).

## Already settled (2026-10-04, 16 recorder sessions + 14 live logs, levels 1-12)

| Parameter | Result |
|---|---|
| `xp.kill.low_base` | Lower-level mobs use base = 5×mob level + 45. The Classic formula uses the player's level. The fitted model is exact on 315/318 recorded kills and 217/219 live-only kills; Classic is exact on 143/318. |
| `xp.kill.rounding` | Round half to even, confirmed by 9 distinct x.5 cases. |
| `xp.kill.base_*`, `xp.kill.high_step` | Confirmed through level 12 and up to +2 levels above the player. |
| `xp.zd.verified_through` | 12 |
| `rested.bonus_ratio`, `xp.kill.rested_mult` | The kill bonus is min(xp, pool/2), and the pool drops by twice the bonus. |
| `rested.camp_benefits_xp_mod` | Camp Benefits has no kill-XP effect (21/21 kills). |
| `xp.mod.well_fed` | +5% with spell 1248422 (61/62 kills). |
| `xp.level.1..12` | 1-12 equal the vanilla table: 8800 at 11, 10100 at 12. The brief's 8700/9800 table is wrong. |
| `xp.quest.rounding` | 121/121 turn-ins match ForeverGuide raw XP after rounding to 5/10/25/50. |
| `rested.rate_per_8h` | 5% of a level per 8 h in an inn. |
| `rested.camp_bonus` | +5% of a level per campfire sit. |
| `travel.run_speed` | 7.0 yd/s. Map-plane ratio 0.98. |
| Ghost and taxi speed | Ghost 8.75 yd/s, taxi 30.3 yd/s. |
| `travel.hearth_cd_s` | 3600 s |
| `death.durability.min_level` | 10. No durability loss below 10, including after spirit-healer resurrections. |
| `economy.train.per_spell_low` | Matched. |
| `combat.ttk_scale` | Calibrated for WARRIOR (levels 1-9) and ROGUE (levels 1-12). |

## XP

1. **`xp.kill.elite_mult`** (2.0, unverified).
   - **Do:** Solo, not rested and without Well Fed, kill at least 5 different open-world elites that are not quest bosses. Include some at your level and some 1-2 levels above.
   - **Captured:** `kx`, `tl` and `tg` (live), the `T` page `tc=elite`, and `obs_kills` with `mob_level`.
   - **Fit reads:** `kill_xp.elite` lists XP ÷ normal prediction per creature. So far Shinyfinder Narf (L3 elite) gave 3.0× and Vagash (L11 elite) gave 1.25×; Hogger is 2× if the level-14 kill was rested.
   - **Decision:** If the ratios are all 2.0, the override is written. Otherwise the coach needs a per-creature table, and these ratios suggest per-creature XP multipliers.
2. **`xp.gray.table`** (classic).
   - **Do:** At level 10 or higher, kill 3 mobs at exactly the gray level and 3 at gray+1. For example, at level 13 gray is 6, so kill level 6 and level 7 mobs.
   - **Captured:** Gray kills print no XP line, so neither `obs_kills` nor `kc` sees them. The live frames still show the target dying (`td` 0→1) while you are in combat (`c=1`) with no `kc` step.
   - **Fit reads:** Add a gray-kill detector: target died in combat with no `kc` step within ±3 s. So far the 7 such events were all mobs killed by others while out of combat.
3. **`xp.kill.high_cap`** (+4, classic).
   - **Do:** Solo, kill a mob at least 5 levels above you. Pick a weak caster or use crowd control.
   - **Captured:** `kx` and `tl`.
   - **Fit reads:** In `kill_xp.forever_by_level_diff`, keys ≥ +5 should match the +4 value. Only +1 and +2 have been seen so far (67 kills).
4. **`xp.quest.factors` / `xp.quest.full_until`** (classic).
   - **Do:** Keep 3-5 easy low-level quests and turn them in at quest level +6, +7, +8, +9 and +10. Breadcrumbs and letters work well, for example Simple Letter or A Threat Within.
   - **Captured:** `obs_quests` (turned_lvl, xp) and the live `tin` event (quest, xp) with the player level.
   - **Fit reads:** `quest_xp.over_level` compares against `quest_xp(fg_raw, fg_level, player_level)`. There are 0 cases so far; the largest gap seen is +5.
5. **`xp.group.rate`** (Classic split, unverified).
   - **Do:** Group with one player of the same level and kill 10 normal mobs. Repeat with a partner 2 levels higher, then in a 3-player group.
   - **Captured:** `kx` per kill. **Recorder gap:** the party size and levels are not recorded.
   - **Fit reads:** Compare against `xp.kill_xp(group_levels=[...])` once the party is logged. The data has 67 partial-XP kills (median 0.23 of the normal value) in `kill_xp.partial`. These are probably group splits or NPC damage, and they cannot be modelled yet.
6. **Camp Benefits, other effects** (no parameter yet).
   - **Settled:** No kill-XP effect (21/21 kills exact).
   - **Do:** Read the 1 h buff's tooltip in game. Then stand in an inn with it and without it, and compare the `r` tick interval, to see whether it speeds up rest accrual.
   - **Fit reads:** `rested.accrual` intervals, tagged by aura.

## Rested

7. **`rested.cap`** (150%, unverified).
   - **Do:** Leave a character logged out in an inn for 10 or more days, then log in.
   - **Captured:** The first `obs_xp` row and the live `r` at login, together with `X`.
   - **Fit reads:** `r = 1.5×X` at login means the cap holds. Add a session-start check.
8. **`rested.away_mult`** (0.25, unverified).
   - **Do:** Log out in the open world, outside any inn or city, for a known time of at least 8 h, then log back in.
   - **Captured:** `sessions.ended` and `started`, plus the pool at logout and at login.
   - **Fit reads:** gain / (hours/8 × 5% × X) should equal 0.25. To implement: pair consecutive sessions of the same character.
9. **`rested.camp_bonus`** (0.05, fitted on 2 sits).
   - **Do:** Sit at 3 different campfires, and twice at the same one within an hour. This tests whether the bonus repeats and whether it has a cooldown or a cap.
   - **Captured:** Live `r` jumps and the `A` pages Welcoming Campfire, Camp Benefits and Campfire Nearby.
   - **Fit reads:** `rested.jumps`.
10. **`rested.boosted_rest_mult`** (unverified).
    - **Do:** Find the Boosted Rest buff and read its tooltip. Stand in an inn for 10 minutes with it and 10 minutes without it.
    - **Captured:** Live `r` ticks (+2 each) with `rs=1`, and the `A` pages.
    - **Fit reads:** `rested.accrual` intervals, split by aura. To implement: add an aura tag to each interval.

## Travel

11. **`mount.level`, `mount.cost`, `mount.speed_bonus`** (40, 100g, +60%, unverified).
    - **Do:** At 40, or whenever the riding trainer offers it, buy riding and a mount, then ride for 1 minute.
    - **Captured:** Money `g` and the `learn` events (a trainer visit), plus `sp` with `mo=1`.
    - **Fit reads:** `training.visits` and the `speed.get_unit_speed.mounted` plateau. The snap candidate is 11.2.
12. **`mount.epic_*`** (60, 1000g, +100%).
    - **Do:** The same at 60.
    - **Fit reads:** The mounted plateau at 14.0.
13. **`travel.taxi_speed`** (30.3, verified on one flight) and **`travel.taxi_overhead_s`** (unverified).
    - **Do:** Take 3 different flights.
    - **Captured:** Frames with `tx=1`, which give the flight duration and speed.
    - **Fit reads:** To implement: compare flight duration with RestedXP's `flightData` time for the same path, which gives the take-off/landing overhead.
14. **`travel.detour_factor`** (1.0, unverified).
    - **Do:** Nothing special; normal questing with the trail on.
    - **Captured:** The `obs_trail` path between accept and turn-in positions.
    - **Fit reads:** To implement: the median of path length ÷ straight-line distance per accept→turn-in leg.
15. **`travel.stealth_mult`** (0.5 at rank 1, unverified for later ranks).
    - **Do:** Move in stealth at 20 and at 40.
    - **Captured:** `sp` while the Stealth aura is up.
    - **Fit reads:** The speed plateaus.
16. **`travel.swim_speed`, `travel.walk_speed`** (classic; 47 and 25 plateaus already seen).
    - **Do:** Swim for 30 s and walk for 30 s.
    - **Captured:** `sp`. **Recorder gap:** there is no IsSwimming flag, so plateaus are labelled by value.
17. **`travel.hearth_cast_s`** (10 s, classic).
    - **Do:** Hearth.
    - **Captured:** The `hs` event (cast) followed by the map change.

## Combat

18. **`combat.aggro.*`** (20 yd ±1 per level, 5..45, unverified).
    - **Do:** Walk, don't run, straight at an isolated, non-pathing mob that is −3 to +3 levels from you. Stop the moment it aggroes, and do this for about 10 mobs.
    - **Captured:** Live `px`/`py` at `c` 0→1, and the target id. The mob's position comes from spawn points (glide.db `spawns`).
    - **Fit reads:** To implement: the distance from the aggro point to the nearest spawn of `tg`, by level difference.
19. **`combat.daze.*`** (50%, 4 s, chance; unverified).
    - **Do:** Let 2-3 mobs hit you from behind while you run away.
    - **Captured:** `sp` drops to 3.5 while `c=1`, not stealthed and not hamstrung. The plateau length gives the duration. The chance is not measurable because hits are not logged.
20. **`combat.mob_hp`, `combat.rank_hp_mult`, `combat.dps_ref`, `combat.class_dps`, `combat.level_diff_ttk`** (priors).
    - **Do:** Keep playing past 12, try other classes, and fight some elites.
    - **Captured:** Fights: `c` windows and kills, with `th` (target health %).
    - **Fit reads:** The fit recalibrates `combat.ttk_scale.<CLASS>` from at least 40 fights. Observed TTK grows about 6%/level faster than the model over 1-12, so revisit the HP/DPS curves at 20 and 30.
21. **`combat.downtime_ref`, `combat.class_downtime`** (priors).
    - **Do:** Nothing special.
    - **Captured:** Food/Drink/First Aid channels in the `A` pages.
    - **Fit reads:** `downtime.by_class`: so far 1.5-1.7 s of channel per kill for warrior and rogue, which is a lower bound because natural regeneration is invisible (health is secret).

## Death

22. **`death.sickness.*`** (none through 10, 1 min per level above, max 10 min; unverified).
    - **Do:** Resurrect at the spirit healer at 11, 15 and 20+.
    - **Captured:** The `A` page "Resurrection Sickness" with its duration. None have been seen yet; all 11 spirit resurrections were at levels 4-9.
    - **Fit reads:** `death.res_sickness_aura_seen`. To implement: read the aura duration.
23. **`death.durability.spirit`** (25%, unverified).
    - **Do:** At 10+, resurrect at the spirit healer once.
    - **Captured:** `du` before and after death and after resurrection.
    - **Fit reads:** `death.deaths[].du_loss_total` minus `du_loss_death`.
24. **`death.durability.death`** (10%, classic). The 3 deaths at level 10 each cost 2 points on a 25-durability item, which is floor(10%).
    - **Do:** Die once while your weakest item has 50 or more max durability.
    - **Captured:** `du`.
25. **`death.release_s`, `spirit_res_s`, `corpse_res_s`, `recover_s`, `sickness_cost_frac`** (behavioural).
    - **Fit reads:** `death.deaths` timelines. Release takes 1-28 s, spirit resurrection 3-21 s, and corpse runs 27-216 s for 167-1253 yd.

## Economy

26. **`economy.train.exponent`, `economy.train.l10_cost`** (prices above 10, unverified).
    - **Do:** Visit the class trainer at every even level.
    - **Captured:** Money `g` with the `learn` events. **Recorder gap:** TRAINER_SHOW reads 0 services on Forever, and rank upgrades do not always fire `LEARNED_SPELL_IN_SKILL_LINE`, so the fit infers the number of ranks from the payment.
    - **Fit reads:** `training.visits[].ranks_implied`, per level.

## Recorder gaps (for the addon lane)

- **Rested bonus text:** The kill message's rested bonus never parses (`rest` is NULL on 334/334 kills). Fix: record `GetXPExhaustion()` before and after each kill (bonus = Δpool/2), or loosen the `%+(%d+) exp Rested` pattern.
- **Party:** Party size and levels are not stored with kills, so the group split cannot be verified.
- **Gray kills:** Gray and zero-XP kills leave no record. Fix: add a "target died in combat without an XP line" record with the mob id and level.
- **Trainer list:** The trainer service list is empty on Forever (`GetNumTrainerServices` returns 0). Prices only come from money deltas.
- **Movement state:** There is no swimming or walking flag next to `sp`.
- **Lost observations:** The level-14 paladin kills quoted in the README (96 / 68 / 250) were never recorded: that session has 0 kills. They are kept as manual anchors in `fit.README_ANCHORS`.
