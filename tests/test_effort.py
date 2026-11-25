#!/usr/bin/env python3
"""The effort model (companion/effort.py): the geometric odds (13.84% is about 1 in 7, 2 to 10 runs, a median of 5,
16 at 90%; 12.93% "about 1 in 8, usually 3 to 10 runs"), every count checked against the distribution itself; the
Beta update of a classic or web chance by the player's own kills (and kill_obs merging one corpse's loot windows),
and the run time shrunk toward the prior; the 5-entries-an-hour floor, the 10-player forming time and the raid
calendar gate; win chances by class, armor, stats, group and bind-on-equip; an unknown chance never ranked above a
known one with the same or a larger gain; gold and reputation rates; and the wording of every source kind (each
number under 0.9 conf names its source; no em dashes).

Run: python3 tests/test_effort.py   (prints OK)
"""
import datetime
import os
import sys
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import effort  # noqa: E402

EM_DASH = "—"


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


def cdf(q, n):
    return 1 - (1 - q) ** n


def local_ts(y, m, d, h=12):
    return time.mktime(datetime.datetime(y, m, d, h).timetuple())


# ---- odds from the geometric distribution
CROWN, STYLUS, MANTLE = 0.1384, 0.1293, 0.1881
check(effort.quantiles(CROWN) == (2, 10, 16), effort.quantiles(CROWN))
check(effort.tries_for(CROWN, 0.5) == 5, "the crown's median")
check(round(1 / CROWN, 1) == 7.2, "7.2 runs expected (never said)")
check(effort.quantiles(STYLUS)[:2] == (3, 10), effort.quantiles(STYLUS))
check(effort.quantiles(None) == (None, None, None) and effort.quantiles(0) == (None, None, None), "no chance")
check(effort.quantiles(1.0) == (1, 1, 1), "a sure thing")
for q in (0.009, 0.02, 0.0453, 0.1, 0.1293, 0.1384, 0.185, 0.3, 0.5, 0.75, 0.9):
    for share in (0.25, 0.5, 0.75, 0.9):
        n = effort.tries_for(q, share)
        check(n is not None and n >= 1, (q, share))
        assert n is not None
        # n is the first try at which that share has it, read to the whole percent
        check(round(cdf(q, n) * 100) >= share * 100 and (n == 1 or round(cdf(q, n - 1) * 100) < share * 100),
              "tries_for(%s, %s) = %s: %.4f / %.4f" % (q, share, n, cdf(q, n - 1), cdf(q, n)))

# ---- the Beta update and its shrinkage
check(effort.drop_chance(1853, 16686, prior=CROWN) == (0.1384, 20, "classic"), "the classic prior alone")
check(effort.drop_chance(1853, 16686, prior=13.84) == (0.1384, 20, "classic"), "a percent is read as one")
check(effort.drop_chance(1853, 16686, prior={1853: {16686: CROWN}}) == (0.1384, 20, "classic"), "atlasloot's table")
check(effort.drop_chance("1853", "16686", prior={1853: {16686: CROWN}})[0] == 0.1384, "ids as text")
q, n, src = effort.drop_chance(1853, 16686, obs={"kills": 5, "got": 1}, prior=CROWN)
check((q, n, src) == (round((CROWN * 20 + 1) / 25, 4), 25, "classic+obs"), (q, n, src))
q, n, src = effort.drop_chance(1853, 16686, obs={"kills": 20, "got": 0}, prior=CROWN)
check((q, n, src) == (0.0692, 40, "obs"), (q, n, src))
check(effort.drop_chance(1, 2, prior=CROWN, web=0.5)[2] == "classic", "classic before the web")
check(effort.drop_chance(1, 2, web=0.2) == (0.2, 5, "web"), "a web rate worth 5 kills")
check(effort.drop_chance(1, 2, web={1: {2: 0.2}}) == (0.2, 5, "web"), "a web table")
check(effort.drop_chance(1, 2, obs={"kills": 5, "got": 0}, web=0.2) == (0.1, 10, "web+obs"), "5 kills halve the web")
check(effort.drop_chance(1, 2, obs={"kills": 20, "got": 2}, web=0.2) == (0.12, 25, "obs"), "20 kills: the player's")
check(effort.drop_chance(1, 2, obs={"kills": 3, "got": 1}) == (0.1875, 8, "guess+obs"), "kills and no rate")
check(effort.drop_chance(1, 2, obs={"kills": 19, "got": 1})[2] == "guess+obs" and
      effort.drop_chance(1, 2, obs={"kills": 20, "got": 1})[2] == "obs", "a few kills don't make it the player's own")
check(effort.drop_chance(1, 2) == (None, 0, "unknown"), "nothing known")
check(effort.drop_chance(1, 2, obs={"kills": 0}) == (None, 0, "unknown"), "no kills")
check(effort.drop_chance(1, 2, obs={"kills": 2, "got": 9}, prior=0.5)[0] == round((10 + 2) / 22, 4), "got <= kills")
check(effort.drop_chance(1, 2, obs={"kills": "x"}, prior=0.5)[0] == 0.5, "junk kills are none")
check(effort.drop_chance(1, 2, prior=0)[2] == "unknown" and effort.drop_chance(1, 2, prior=150)[2] == "unknown",
      "a chance outside (0, 1] (or 100%) is none")
# the more kills, the nearer the player's own rate
last = CROWN
for kills in (10, 40, 160, 640):
    q = effort.drop_chance(1, 2, obs={"kills": kills, "got": kills * 3 // 10}, prior=CROWN)[0]
    assert q is not None
    check(last < q < 0.3, "shrinkage at %d kills: %s" % (kills, q))
    last = q
check(abs(last - 0.3) < 0.01, last)

# raw loot windows: one corpse's are one kill (Urok: five windows in a few seconds)
M = effort.KILL_MERGE_S
kills = [{"npc": 10584, "t": 1794612323 + i, "loot": [12345]} for i in range(5)]
kills += [{"npc": 10584, "t": 1794620000, "loot": [16670, 12345]}, {"npc": 10584, "t": 1794620100, "loot": []},
          {"npc": 999, "t": 1794620000, "loot": [16670]}, {"npc": 10584, "t": 1794700000}, "junk"]
check(effort.kill_obs(kills, 10584, 16670, M) == {"kills": 3, "got": 1}, effort.kill_obs(kills, 10584, 16670, M))
check(effort.kill_obs(kills, "10584", "12345", M) == {"kills": 3, "got": 2}, "ids as text")
check(effort.kill_obs(None, 1, 2) == {"kills": 0, "got": 0} and effort.kill_obs(kills, "x", 2)["kills"] == 0, "none")
# recorder_data.kills' rows are one per corpse already: two of a farmed creature a minute apart are two kills, and a
# row given twice (the same second) once
farm = [{"npc": 7438, "t": 1794612000, "loot": [4306]}, {"npc": 7438, "t": 1794612060, "loot": []},
        {"npc": 7438, "t": 1794612060, "loot": []}]
check(effort.kill_obs(farm, 7438, 4306) == {"kills": 2, "got": 1}, effort.kill_obs(farm, 7438, 4306))
check(effort.kill_obs(farm, 7438, 4306, M) == {"kills": 1, "got": 1}, "merged as raw windows")

# the run time: the median shrunk toward 45 minutes with 3 hours of weight (Nine's two Scholomance runs)
check(effort.run_minutes("Scholomance", None, 2700, 3) == (45.0, "prior"), "no runs: the prior")
m, src = effort.run_minutes("Scholomance", [65.5 * 60, 69.2 * 60], 2700, 3)
h = (65.5 + 69.2) / 60
check((m, src) == (round((h * 67.35 + 3 * 45) / (h + 3), 1), "prior+obs") and 54 < m < 55, (m, src))
m, src = effort.run_minutes("Scholomance", [{"s": 3600}] * 12, 2700, 3)
check(src == "obs" and abs(m - (12 * 60 + 3 * 45) / 15) < 0.1, (m, src))
check(effort.run_minutes("x", [30, 6 * 3600], 2700, 3) == (45.0, "prior"), "a zone-in and an AFK aren't runs")
check(effort.run_minutes("x", [(1000, 1000 + 3600)], 2700, 3)[1] == "prior+obs", "(start, end) pairs")
check(effort.run_minutes("x", [{"t0": 0, "t1": 3600}, {"min": 60}, {"start": 0, "end": 3600}, True, "x"], 2700, 3)
      == effort.run_minutes("x", [3600, 3600, 3600], 2700, 3), "the other shapes")
check(effort.run_minutes("x")[0] == round(effort.priors()["run_s"] / 60, 1), "the analyzer's prior by default")

# ---- time per try: Forever's 5 entries an hour, forming, travel
check(effort.try_hours("inst", run_min=6, form_min=0) == (0.2, "prior"), "a 6-minute run is still 12 minutes")
check(effort.try_hours("inst", run_min=6, form_min=0, entries=10) == (0.1, "prior"), "the cap is a parameter")
check(effort.try_hours("inst", run_min=40, form_min=20) == (1.0, "prior"), "run plus forming")
check(effort.try_hours("inst", run_min=40, form_min=20, travel_min=15) == (1.25, "prior"), "plus travel")
check(effort.try_hours("inst", run_min=40, form_min=20, group="solo") == (round(40 / 60, 3), "prior"), "solo: no group")
check(effort.try_hours("inst", instance="Upper Blackrock Spire", run_min=60, form_min=20) == (1.5, "prior"),
      "10 players take half as long again to form")
check(effort.group_size("Upper Blackrock Spire") == 10 and effort.group_size("Lower Blackrock Spire") == 5, "UBRS")
check(effort.try_hours("inst", instance="Scholomance", history=[65.5 * 60, 69.2 * 60], prior_s=2700, prior_h=3)
      == (round((54.6 + 20) / 60, 3), "prior+obs"), effort.try_hours("inst", instance="Scholomance",
                                                                    history=[3930, 4152], prior_s=2700, prior_h=3))

# the raid calendar: nothing before December 9, then once a lockout
check(effort.RAID_OPENS == datetime.date(2026, 12, 9), "raids open Dec 9")
check(effort.try_hours("raid", now=local_ts(2026, 12, 1)) == (None, "calendar"), "before Dec 9")
check(effort.try_hours("raid", now=local_ts(2026, 12, 8, 23)) == (None, "calendar"), "the day before")
check(effort.try_hours("raid", now=local_ts(2026, 12, 9, 1)) == (3.5, "prior"), "open: a guessed raid night")
check(effort.try_hours("raid", now=local_ts(2026, 12, 10), locked_until=local_ts(2026, 12, 16)) == (None, "lockout"),
      "this week's roll is used")
check(effort.try_hours("raid", now=local_ts(2026, 12, 17), locked_until=local_ts(2026, 12, 16))[0] == 3.5, "reset")
check(not effort.raid_open(local_ts(2026, 11, 25)) and effort.raid_open(local_ts(2027, 1, 5)), "raid_open")

# quests, vendors, reputation, crafts, the auction house, PvP
check(effort.try_hours("quest", steps=[600, 1200]) == (0.5, "steps"), "the analyzer's step times")
check(effort.try_hours("quest", steps=[600, None]) == (0.417, "prior"), "a step it can't time is guessed")
check(effort.try_hours("quest", steps=[1, True]) == (round((1 + 900) / 3600, 3), "prior"), "a bool isn't a time")
check(effort.try_hours("quest", done=True) == (0.0, "obs") and effort.try_hours("quest")[1] == "prior", "done; none")
check(effort.try_hours("vendor", price=None, travel_min=6) == (0.1, "noprice"), "price unknown: travel only")
check(effort.try_hours("vendor", price=500000, currency=True)[1] == "noprice", "a currency cost isn't known")
check(effort.try_hours("vendor", price=120000, gold=500000) == (0.0, "obs"), "the gold is there")
check(effort.try_hours("vendor", price=500000, gold=100000, gph=200000) == (2.0, "obs"), "40g missing at 20g an hour")
check(effort.try_hours("vendor", price=500000, gold=100000) == (4.0, "prior"), "no gold rate: 10g an hour, a guess")
check(effort.try_hours("vendor", price=500000, gold=100000, gph=200000, price_src="web") == (2.0, "web"), "web price")
check(effort.try_hours("rep", target="Honored", value=3000, rate=600) == (10.0, "obs"), "6,000 rep at 600 an hour")
check(effort.try_hours("rep", target=6, value=1000, standing=5, rate=1000) == (5.0, "obs"),
      "a value within Friendly is read as 4,000")
check(effort.try_hours("rep", target="Honored", standing="Friendly", rate=1000) == (6.0, "obs"), "standing only")
check(effort.try_hours("rep", target="Revered", value=25000) == (0.0, "obs"), "reached")
check(effort.try_hours("rep", target="Honored", value=3000) == (None, "unknown"), "no rate yet")
check(effort.try_hours("rep", target="Honored") == (None, "nostanding"), "no standing read yet")
check(effort.rep_value("exalted") == 42000 and effort.rep_value("7") == 21000 and effort.rep_value("x") is None, "rv")
check(effort.try_hours("crafted", profession="Tailoring", skills=None) == (None, "crafter"), "skills unknown")
check(effort.try_hours("crafted", profession="Tailoring", skills={"Mining"}) == (None, "crafter"), "not a tailor")
check(effort.try_hours("crafted", profession="Tailoring", skills={"Tailoring"}) == (None, "nomats"), "no reagents")
check(effort.try_hours("crafted", profession="Tailoring", skills={"Tailoring"}, mats=300000, gold=100000,
                       gph=100000, pattern_h=1.5, pattern_src="prior") == (3.5, "prior"), "pattern and reagents")
check(effort.try_hours("crafted", profession="Tailoring", skills=set(), price=300000, gold=100000, gph=100000)
      == (2.0, "ah"), "bought instead: the auction house")
check(effort.try_hours("ah", price=None) == (None, "noprice"), "never seen on the AH")
check(effort.try_hours("ah", price=450000, gold=50000, gph=200000) == (2.0, "ah"), "45g, 5g on hand")
check(effort.try_hours("ah", price=450000, gold=50000) == (4.0, "prior"), "the guessed gold rate is weaker")
check(effort.try_hours("pvp") == (None, "pvp") and effort.try_hours("nonsense") == (None, "unknown"), "pvp; unknown")
# the planner's names for the same things: money (copper on hand), need (rep missing), have (the profession), steps left
check(effort.try_hours("vendor", price=500000, money=100000, gph=200000) == (2.0, "obs"), "money is gold")
check(effort.try_hours("ah", price=450000, money=50000, gph=200000, travel_min=6) == (2.1, "ah"), "money on the AH")
check(effort.try_hours("rep", need=6000, rate=600) == (10.0, "obs") and effort.try_hours("rep", need=0, rate=None)
      == (0.0, "obs") and effort.try_hours("rep", need=6000) == (None, "unknown"), "need given")
check(effort.try_hours("crafted", have=True) == (None, "nomats") and effort.try_hours("crafted", have=False) ==
      (None, "crafter") and effort.try_hours("crafted", have=True, mats=0)[0] == 0.0, "have given")
check(effort.try_hours("quest", steps=3, travel_min=6) == (0.85, "prior"), "three steps left, each guessed")
check(effort.try_hours("quest", steps=0, travel_min=6) == (0.1, "obs"), "no steps left")

# ---- win chances
MAGE_ONLY = {"type": 1, "armorType": 1, "classAllowlist": [8]}
CROWN_REC = {"type": 1, "armorType": 1, "scalingOptions": {"0": {"stats": {"2": 10, "3": 30, "4": 11, "5": 11}}}}
check(effort.likely_needers(MAGE_ONLY) == {8}, "the class list")
check(effort.likely_needers(CROWN_REC) == {5, 8, 9}, "cloth caster: priests, mages, warlocks")
check(effort.likely_needers({"type": 5, "armorType": 2, "stats": {"AG": 20}}) == {4, 11}, "leather agility")
check(effort.likely_needers({"type": 5, "armorType": 4, "stats": {"IN": 20}}) == {2}, "plate caster: paladins")
check(effort.likely_needers({"type": 11, "stats": {"SP": 20}}) == effort.CASTERS, "a caster ring")
check(effort.likely_needers({"type": 4, "armorType": 1, "stats": {"AG": 9}}) == effort.FIGHTERS, "a cloak anyone wears")
check(effort.likely_needers(None) is None, "nothing known")
mage = (1 - (8 / 9) ** 5) / (5 / 9)
check(effort.win_chance(MAGE_ONLY) == round(mage, 4) and 0.79 < mage < 0.81, effort.win_chance(MAGE_ONLY))
check(effort.win_chance(CROWN_REC) == round((1 - (2 / 3) ** 5) / (5 / 3), 4), effort.win_chance(CROWN_REC))
check(effort.win_chance(CROWN_REC, size=10) < effort.win_chance(CROWN_REC), "more rivals in 10")
check(effort.win_chance(CROWN_REC, group="solo") == 1.0, "solo")
check(effort.win_chance(CROWN_REC, needers=2) == 0.3333 and effort.win_chance(None, needers=0) == 1.0, "needers given")
check(effort.win_chance(CROWN_REC, needers={5, 9}) == 0.3333, "the needers themselves: their count")
check(effort.likely_needers({"type": 13, "scalingOptions": {"0": {"stats": {"18": 1}}}}) == effort.FIGHTERS,
      "crit (WoWSims' index 18) is a fighter's")
check(effort.win_chance(dict(CROWN_REC, bond=2)) == 0.2, "a BoE in a pick-up group: everyone rolls")
check(effort.win_chance(dict(CROWN_REC, boe=True), group="guild") == effort.win_chance(CROWN_REC), "guild: no greed")
check(effort.win_chance(12345) == round((1 - (2 / 3) ** 5) / (5 / 3), 4), "an item nothing tells about")

# ---- ranking: an unknown chance never ahead of a known one with the same or a larger gain
hours = 1.5
known = {"name": "known", "d": 10.0, "src": "classic", "value": effort.value(0.05, 10.0, hours)}
guess = {"name": "guess", "d": 10.0, "src": "guess", "value": effort.value(effort.GUESS_Q, 10.0, hours)}
check(guess["value"] > known["value"], "the guess alone would win")
check([e["name"] for e in effort.rank([guess, known])] == ["known", "guess"], "same d: the known one first")
smaller = dict(guess, name="smaller", d=8.0, value=effort.value(effort.GUESS_Q, 8.0, hours))
check([e["name"] for e in effort.rank([smaller, known])] == ["known", "smaller"], "a larger known d first")
bigger = dict(guess, name="bigger", d=30.0, value=effort.value(effort.GUESS_Q, 30.0, hours))
check([e["name"] for e in effort.rank([known, bigger])] == ["bigger", "known"], "a far larger gain may lead")
unknown = dict(guess, name="unknown", src="unknown")
third = {"name": "third", "d": 2.0, "src": "obs", "value": 0.01}
check([e["name"] for e in effort.rank([unknown, guess, third, known])] == ["known", "unknown", "guess", "third"],
      [e["name"] for e in effort.rank([unknown, guess, third, known])])
check(effort.known("classic+obs") and effort.known("web") and effort.known("obs"), "known sources")
check(not effort.known("guess+obs") and not effort.known("unknown") and not effort.known(None), "guesses")
check(effort.value(0.1384, 10, 2.0, win=0.5) == 0.1384 * 0.5 * 10 / 2.0, "q * win * d / hours")
check(effort.value(None, 10, 1) == 0 and effort.value(0.1, 10, None) == 0 and effort.value(0.1, 10, 0) == 0, "gated")

# ---- gold and reputation rates
t0 = 1_800_000_000
samples = [(t0 + 10 * i, 1_000_000 + 100 * i) for i in range(181)]            # 30 minutes, 1s every 10 seconds
check(effort.gold_per_hour(samples) == (36000, "obs"), effort.gold_per_hour(samples))
gap = samples + [(t0 + 1800 + 4000, 5_000_000)] + [(t0 + 5800 + 10 * i, 5_000_000 + 100 * i) for i in range(1, 91)]
check(effort.gold_per_hour(gap) == (36000, "obs"), "an idle gap counts neither its time nor its change")
spend = samples + [(t0 + 1810, 1_018_000 - 10_000_000)] + [(t0 + 1810 + 10 * i, -8_982_000 + 100 * i)
                                                            for i in range(1, 91)]
check(effort.gold_per_hour(spend) == (36000, "obs"), "a 1000g mount isn't the rate")
check(effort.gold_per_hour([{"t": t0 + 10 * i, "g": 500 * i} for i in range(181)]) == (180000, "obs"), "dicts")
check(effort.gold_per_hour(samples[:60]) == (None, "unknown"), "under 15 minutes")
check(effort.gold_per_hour([(t0 + 10 * i, 1_000_000 - 100 * i) for i in range(181)]) == (0, "obs"), "losing gold")
check(effort.gold_per_hour(None) == (None, "unknown") and effort.gold_per_hour([("x", 1), (1,)]) == (None, "unknown"),
      "junk")
check(effort.rep_per_hour([{"gain": 300, "s": 1800}, {"gain": 150, "s": 900}]) == (600, "obs"), "stretches")
check(effort.rep_per_hour([{"gain": 300, "h": 0.5}]) == (600, "obs"), "a stretch in hours")
readings = [(t0, 3000), (t0 + 1200, 3200), (t0 + 2400, 3400), (t0 + 90000, 9000), (t0 + 90600, 9100)]
check(effort.rep_per_hour(readings) == (600, "obs"), effort.rep_per_hour(readings))
check(effort.rep_per_hour([(t0, 3000), (t0 + 600, 3100)]) == (None, "unknown"), "under 15 minutes")
check(effort.rep_per_hour([(t0, 3000), (t0 + 1800, 3000)]) == (None, "unknown") and effort.rep_per_hour([]) ==
      (None, "unknown"), "no rise, nothing")

# ---- the wording, one per source kind
said = []


def say(text):
    said.append(text)
    return text


check(say(effort.say_odds(STYLUS, "classic")) == "about 1 in 8, usually 3 to 10 runs; that's classic's chance",
      said[-1])
check(say(effort.say_odds(CROWN, "classic")) == "about 1 in 7, usually 2 to 10 runs; that's classic's chance", said[-1])
check(say(effort.say_odds(MANTLE, "classic")).startswith("about 1 in 5, usually"), said[-1])
check(say(effort.say_odds(CROWN, "classic", asked=True)) ==
      "about 1 in 7, usually 2 to 10 runs (9 in 10 get it by run 16); that's classic's chance", said[-1])
check(say(effort.say_odds(0.1507, "classic+obs")).endswith("; that's classic's chance with your kills counted"),
      said[-1])
check(say(effort.say_odds(0.2, "web")) == "about 1 in 5, usually 2 to 7 runs; web, unconfirmed", said[-1])
check(say(effort.say_odds(0.1, "web+obs")).endswith("; web, unconfirmed, with your kills counted"), said[-1])
check(say(effort.say_odds(0.0692, "obs")).endswith(", by your own kills") and ";" not in said[-1], said[-1])
check(say(effort.say_odds(effort.GUESS_Q, "guess")) ==
      "about 1 in 10, usually 3 to 13 runs; that's my guess, nobody lists its chance", said[-1])
check(say(effort.say_odds(0.15, "guess+obs")).endswith("; that's my guess with your kills counted"), said[-1])
check(say(effort.say_odds(None, "unknown")) == "I don't know its drop chance yet", said[-1])
check(say(effort.say_odds(0.97, "obs")) == "every run, by your own kills", said[-1])
check(say(effort.say_odds(0.8, "classic", unit="kills")) == "about 8 in 10, usually the first kill; that's classic's "
      "chance", said[-1])
check(say(effort.say_odds(0.02, "classic", unit="weeks")).startswith("about 1 in 50, usually 14 to 68 weeks"),
      said[-1])
check(say(effort.say_tries(2, STYLUS)) == "That's run 2; it usually takes 3 to 10.", said[-1])
check(say(effort.say_tries(6, STYLUS)) == "That's run 6; it usually takes 3 to 10. Six runs without it is bad luck, "
      "not a bad plan.", said[-1])
check(say(effort.say_tries(14, STYLUS)).endswith(" 14 runs without it is bad luck, not a bad plan."), said[-1])
check(effort.say_tries(5, STYLUS) == "That's run 5; it usually takes 3 to 10.", "the median: not bad luck yet")
check(say(effort.say_tries(2, None)) == "That's run 2.", said[-1])
check(say(effort.say_tries(1, 0.8, unit="kills")) == "That's kill 1; it usually takes one.", said[-1])

check(say(effort.say_effort("raid", "calendar")) == "Raid gear: earliest December 9, then one roll a week.", said[-1])
check(say(effort.say_effort("raid", "lockout")).startswith("Your raid lockout is used"), said[-1])
check(say(effort.say_effort("raid", "prior", 3.5)) ==
      "About 3.5 hours a raid, one roll a week; that's my guess until you've raided.", said[-1])
check(say(effort.say_effort("raid", "prior+obs", 3.0)).endswith("; that's your raids and my guess."), said[-1])
check(say(effort.say_effort("inst", "obs", 1.25)) == "About 1.5 hours a run, by your own runs.", said[-1])
check(say(effort.say_effort("inst", "prior", 1.0, form_min=20)) ==
      "About an hour a run, finding a group included; that's my guess until you've run it.", said[-1])
check(say(effort.say_effort("inst", "prior+obs", 1.25)) == "About 1.5 hours a run; that's your runs and my guess.",
      said[-1])
check(say(effort.say_effort("quest", "steps", 0.5)) == "About 30 minutes of questing, by my route times.", said[-1])
check(say(effort.say_effort("quest", "prior", 0.417)) == "About 25 minutes of questing; that's my guess.", said[-1])
check(say(effort.say_effort("quest", "obs", 0.0)) == "The quests for it are done.", said[-1])
check(say(effort.say_effort("vendor", "noprice", 0.1)) == "I don't know its price yet.", said[-1])
check(say(effort.say_effort("vendor", "obs", 0.0, price=120000, gold=500000)) == "It costs 12g; you have the gold.",
      said[-1])
check(say(effort.say_effort("vendor", "obs", 2.1, price=500000, gold=100000, travel_min=6)) ==
      "About 2 hours of farming for the gold, at your gold rate.", said[-1])
check(say(effort.say_effort("vendor", "prior", 4.0, price=500000, gold=100000)) ==
      "About 4 hours of farming for the gold, at a guessed 10g an hour.", said[-1])
check(say(effort.say_effort("vendor", "web", 2.0, price=500000, gold=100000)) ==
      "About 2 hours of farming for the gold, at your gold rate; its price is from the web, unconfirmed.", said[-1])
check(say(effort.say_effort("vendor", "web", 0.0, price=120000, gold=500000)) ==
      "It costs 12g (web, unconfirmed); you have the gold.", said[-1])
# regression: 5g on hand, no gold rate learned yet (the planner's gph_src "unknown"), a 40g price the web gave: the
# hours are the 10g-an-hour guess and the web's price, and both are said (not "at your gold rate", not the web dropped)
h, src = effort.try_hours("vendor", price=400000, price_src="web", gold=50000, gph=None, gph_src="unknown",
                          travel_min=10)
check(src == "prior" and abs(h - (3.5 + 10 / 60.0)) < 0.01, (h, src))
check(say(effort.say_effort("vendor", src, h, price=400000, price_src="web", gold=50000, travel_min=10,
                            gph_src="unknown")) ==
      "About 3.5 hours of farming for the gold, at a guessed 10g an hour; its price is from the web, unconfirmed.",
      said[-1])
check(say(effort.say_effort("vendor", "obs", 2.0, price=500000, gold=100000, gph_src="prior")).endswith(
    "at a guessed 10g an hour."), said[-1])
check(say(effort.say_effort("ah", "prior", 2.0, price=450000, gold=50000, gph_src="unknown")).endswith(
    "at a guessed 10g an hour."), said[-1])
check(say(effort.say_effort("vendor", "obs", 2.0, price=500000, gold=100000, gph_src="obs")).endswith(
    "at your gold rate."), said[-1])
# regression: a run time of the player's own with a guessed time to find a group: the guess is said as one
check(say(effort.say_effort("inst", "obs", 1.25, form_min=20)) ==
      "About 1.5 hours a run by your own runs, plus my guess for finding a group.", said[-1])
check(say(effort.say_effort("inst", "prior+obs", 1.25, form_min=20)) ==
      "About 1.5 hours a run, finding a group included; that's your runs and my guess.", said[-1])
# a quest ready to turn in: the way to whom it goes, said so
check(effort.try_hours("quest", done=True, travel_min=6) == (0.1, "obs"), effort.try_hours("quest", done=True,
                                                                                          travel_min=6))
check(say(effort.say_effort("quest", "obs", 0.1, done=True)) == "The quest is ready to turn in.", said[-1])
check(say(effort.say_effort("rep", "nostanding", faction="Timbermaw Hold")) ==
      "I don't know your Timbermaw Hold standing yet; it shows after a /reload.", said[-1])
check(say(effort.say_effort("rep", "unknown", faction="Theramore")) ==
      "I don't know Theramore rep rates yet; I'll learn them from your turn-ins.", said[-1])
check(say(effort.say_effort("rep", "obs", 0.0, faction="The Argent Dawn")) ==
      "You have the Argent Dawn standing for it, as of your last reload.", said[-1])
check(say(effort.say_effort("rep", "nostanding", faction="The Argent Dawn")) ==
      "I don't know your Argent Dawn standing yet; it shows after a /reload.", said[-1])
check(say(effort.say_effort("rep", "obs", 10.0, faction="Timbermaw Hold")) ==
      "About 10 hours of Timbermaw Hold rep, as of your last reload.", said[-1])
check(say(effort.say_effort("crafted", "crafter", profession="Tailoring")) ==
      "Ask a tailor or check the auction house.", said[-1])
check(say(effort.say_effort("crafted", "crafter", profession="Alchemy")) ==
      "Ask an alchemist or check the auction house.", said[-1])
check(say(effort.say_effort("crafted", "crafter", profession="Mining")) ==
      "Ask a Mining crafter or check the auction house.", said[-1])
check(say(effort.say_effort("crafted", "nomats", profession="Tailoring")) ==
      "You can craft it; I don't know its reagents yet.", said[-1])
check(say(effort.say_effort("crafted", "obs", 2.0)) ==
      "You can craft it; about 2 hours of farming for the reagents, at your gold rate.", said[-1])
check(say(effort.say_effort("ah", "noprice")) == "The auction house may have it.", said[-1])
now = 1_800_000_000
check(say(effort.say_effort("ah", "ah", 0.0, price=450000, gold=900000, seen_t=now - 3 * 86400 - 60, now=now)) ==
      "The auction house had it for 45g 3 days ago; you have the gold.", said[-1])
check(say(effort.say_effort("ah", "ah", 2.0, price=450000, gold=50000, seen_t=now - 7200, now=now)) ==
      "The auction house had it for 45g 2 hours ago; about 2 hours of farming for the gold, at your gold rate.",
      said[-1])
check(say(effort.say_effort("pvp", "pvp")) == "I can't estimate PvP rewards yet.", said[-1])
for text in said:
    check(EM_DASH not in text and "–" not in text, "a dash in %r" % text)
# every number under 0.9 conf names its source
for src, words in (("classic", "classic's"), ("web", "web, unconfirmed"), ("guess", "my guess")):
    check(words in effort.say_odds(0.2, src), (src, effort.say_odds(0.2, src)))
# ... after the clause's last "; ", where the offline coach reads it back (offline_coach.odds_source)
for src, words in (("classic", "that's classic's chance"), ("classic+obs", "classic's chance with your kills"),
                   ("web", "web, unconfirmed"), ("web+obs", "web, unconfirmed, with"), ("guess", "my guess"),
                   ("guess+obs", "my guess with your kills")):
    tail = effort.say_odds(0.2, src, asked=True).rpartition(";")[2]
    check(words in tail, (src, tail))
check(effort.conf("classic") == 0.5 and effort.conf("classic+obs") == 0.5 and effort.conf("obs") == 0.95 and
      effort.conf("calendar") == 0.0, "conf")
check(effort.weakest("obs", "web", None) == "web" and effort.weakest() == "unknown", "weakest")

# ---- the words for numbers
check([effort.say_hours(h) for h in (0.01, 0.1, 0.5, 1.0, 1.4, 2.26, 12.4)] ==
      ["a minute", "about 6 minutes", "about 30 minutes", "about an hour", "about 1.5 hours", "about 2.5 hours",
       "about 12 hours"], [effort.say_hours(h) for h in (0.01, 0.1, 0.5, 1.0, 1.4, 2.26, 12.4)])
check(effort.say_hours(None) is None, "no hours")
check([effort.money(c) for c in (450000, 32000, 8000, 15, 30000)] == ["45g", "3g 20s", "80s", "15c", "3g"],
      [effort.money(c) for c in (450000, 32000, 8000, 15, 30000)])
check([effort.ago(now - s, now) for s in (60, 5000, 5 * 3600, 30 * 3600, 3 * 86400)] ==
      ["just now", "an hour ago", "5 hours ago", "yesterday", "3 days ago"], "ago")

print("OK")
