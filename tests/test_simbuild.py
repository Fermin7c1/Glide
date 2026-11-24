#!/usr/bin/env python3
"""A level-60 character as WoWSims sims it (companion/simbuild.py): the talent string WoWSims reads (each tree's
talents row by row, trailing zeroes trimmed), the spec (the tree with the most points), the 17 sim slots from WoW's
inventory slots, races (Skyborne by faction), the request's shape, and stat weights measured against a fake sim whose
DPS is known per stat.

Run: python3 tests/test_simbuild.py   (prints OK)
"""
import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import sim_weights  # noqa: E402
import simbuild  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


# ---- talents: the addon's list (tab tier column) -> WoWSims' string ---------------------------------------------------
arcane = sim_weights.talent_list("050205223101311531-5500003-003")
check(simbuild.talents_string(arcane) == "050205223101311531-5500003-003", simbuild.talents_string(arcane))
shuffled = list(reversed(arcane))                                # the order the addon sends them in doesn't matter
check(simbuild.talents_string(shuffled) == "050205223101311531-5500003-003", "sorted by row, then column")
check(simbuild.talents_string(sim_weights.talent_list("-5550000123011303-2035010311013002")) ==
      "-5550000123011303-2035010311013002", "an empty first tree keeps its dash")
check(simbuild.talents_string(sim_weights.talent_list("0502052-0-0")) == "0502052", "trailing trees trimmed")
check(simbuild.spec_of("MAGE", arcane) == "Arcane" and
      simbuild.spec_of("mage", sim_weights.talent_list("203205-5-053500011121331251")) == "Frost" and
      simbuild.spec_of("MAGE", sim_weights.talent_list("0502052001001-55500001230133031-003")) == "Fire" and
      simbuild.spec_of("MAGE", sim_weights.talent_list("-5550000123011303-2035010311013002")) == "Fire",
      "the spec: the tree with the most points (WoWSims' Frostfire build has more in Fire)")
check(simbuild.tree_points(arcane) == [35, 13, 3], simbuild.tree_points(arcane))      # (51: level 60's points)

# ---- gear, race, who's simmed -----------------------------------------------------------------------------------------
worn = [{"key": str(s), "id": i} for s, i in sim_weights.SMOKE_GEAR] + [{"key": "4", "id": 6125}, {"key": "19", "id": 5976}]
eq = simbuild.equipment(worn)["items"]
check(len(eq) == 17 and eq[0] == {"id": 226935} and eq[3] == {"id": 272411} and eq[5] == {"id": 226929} and
      eq[14] == {"id": 22383} and eq[16] == {"id": 13938} and {"id": 6125} not in eq, "the sim's slot order, no shirt")
check(simbuild.equipment([{"key": "1", "id": 1}])["items"][1] == {}, "an empty slot stays empty")
check(simbuild.race("Skyborne", "Alliance") == "RaceHighOrderSkyborne" and
      simbuild.race("Skyborne", "Horde") == "RaceWindshaperSkyborne" and simbuild.race("Scourge", "Horde") == "RaceUndead"
      and simbuild.race("NightElf", "Alliance") == "RaceNightElf", "races")
try:
    simbuild.race("Murloc", "Alliance")
    check(False, "an unknown race is refused")
except simbuild.SimError:
    pass
check(simbuild.simmable({"level": 60, "class": "MAGE"}) and not simbuild.simmable({"level": 59, "class": "MAGE"}) and
      not simbuild.simmable({"level": 60, "class": "PRIEST"}) and not simbuild.simmable(None), "level 60 mages")

# ---- the request ----------------------------------------------------------------------------------------------------
char = {"name": "Nine", "class": "MAGE", "race": "Skyborne", "faction": "Alliance", "talents": arcane, "worn": worn}
rot = {"type": "TypeAPL", "priorityList": []}
req = simbuild.request(char, rot, 500, 3)
p = req["raid"]["parties"][0]["players"][0]
check(p["class"] == "ClassMage" and p["race"] == "RaceHighOrderSkyborne" and p["talentsString"] == "050205223101311531-5500003-003"
      and p["rotation"] is rot and p["equipment"]["items"][0] == {"id": 226935} and "mage" in p and "bonusStats" not in p
      and req["simOptions"] == {"iterations": 500, "randomSeed": 3} and req["encounter"]["targets"][0]["level"] == 63
      and len(req["encounter"]["targets"][0]["stats"]) == simbuild.STAT_COUNT, p)
b = simbuild.request(char, rot, bonus={("s", 5): 50, ("p", 15): 2})["raid"]["parties"][0]["players"][0]["bonusStats"]
check(b["stats"][5] == 50 and b["pseudoStats"][15] == 2 and len(b["stats"]) == 39 and len(b["pseudoStats"]) == 40, b)
for bad, why in ((dict(char, worn=[]), "no gear"), (dict(char, talents=[]), "no talents"),
                 (dict(char, **{"class": "PRIEST"}), "no sim")):
    try:
        simbuild.request(bad, rot)
        check(False, why)
    except simbuild.SimError:
        pass

# ---- stat weights: a sim whose DPS is 500 + 0.5 per spell damage + 0.45 per intellect + 5 per 1% crit ---------------
PER = {("s", 5): 0.5, ("s", 3): 0.45, ("p", 15): 5.0, ("s", 6): 0.45, ("p", 13): -0.01}
seen = []


def fake_sim(r):
    seen.append(r)
    bs = r["raid"]["parties"][0]["players"][0].get("bonusStats")
    total = 500.0
    for (kind, idx), per in PER.items():
        if bs:
            total += per * (bs["pseudoStats"] if kind == "p" else bs["stats"])[idx]
    return total


res = simbuild.stat_weights("MAGE", fake_sim, lambda bonus: simbuild.request(char, rot, 100, 1, bonus))
w = res["w"]
check(res["dps"] == 500.0 and res["per_ref"] == 0.5 and w["SP"] == 1.0 and w["IN"] == 0.9 and w["CS"] == 10.0 and
      w["AC"] == 0.9 and w["FR"] == 0.0 and w["HS"] == 0.0, res)
check(len(seen) == 1 + len(simbuild.CLASSES["MAGE"]["weigh"]) and all(r["simOptions"]["randomSeed"] == 1 for r in seen),
      "a baseline and one sim per stat, on one seed")
try:
    simbuild.stat_weights("MAGE", lambda r: 500.0, lambda bonus: simbuild.request(char, rot, 100, 1, bonus))
    check(False, "no reference: refused")
except simbuild.SimError:
    pass
print("OK")
