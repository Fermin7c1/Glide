"""A level-60 character as WoWSims Forever sims it (wowsims.py runs it): the player's own gear (with its enchants and
random suffixes) and talents, the rotation WoWSims gives their spec, and a raid's buffs and consumables as WoWSims' own
presets set them; and stat weights measured on it (stat_weights: a few sims with a little more of one stat each, on
the same random seed).

The request is WoWSims' RaidSimRequest in protojson (proto/api.proto at github.com/wowsims/forever):
    {"raid": {"parties": [{"players": [player], "buffs": partyBuffs}], "buffs": raidBuffs, "debuffs": debuffs},
     "encounter": encounter, "simOptions": {"iterations", "randomSeed"}}
WoWSims sims every character at level 60 and has no healer sims: only the classes in CLASSES are simmed (one spec per
talent tree, the tree with the most points). Pure: no files, no network (an enchant or suffix left out is logged).

A worn item is the ItemSpec {"id", "enchant", "randomSuffix"} (common.proto; v1.0.4's runner reads randomSuffix and
random_suffix alike). WoWSims v1.0.4 refuses the whole request for an item or suffix id it doesn't know, ignores an
enchant it doesn't know and puts any enchant it knows on any slot: so with WoWSims' item database (item_db.ItemDB) only
the enchants it has for that slot and the suffixes it has go in. Its suffix ids are never negative: one the item link
carries negative is sent without its sign.

Stat codes are the addon's and gear.py's (Gear.lua STAT_CODE / TOOLTIP): a weight per code is what one point of it
on an item is worth, against the reference stat (a caster's spell damage: EP). Forever's items give hit and crit as
ratings (HT, CR: 10 hit and 14 crit rating to 1%, and either counts for spells too), and older "chance ... with
spells by 1%" lines as percents (HS, CS): both are weighed.
"""
import threading

from log import log

STAT_COUNT, PSEUDO_COUNT = 39, 40           # proto/common.proto Stat and PseudoStat, as WoWSims v1.0 has them
VERSION = 3                                  # what a request holds and how weights are measured: weights from an
                                             # older one are measured again (3: enchants and random suffixes)

# WoW's inventory slot -> WoWSims' ItemSlot (Head 0 .. Ranged 16); shirt and tabard aren't simmed
INV_TO_SIM = {1: 0, 2: 1, 3: 2, 15: 3, 5: 4, 9: 5, 10: 6, 6: 7, 7: 8, 8: 9, 11: 10, 12: 11, 13: 12, 14: 13,
              16: 14, 17: 15, 18: 16}
SIM_SLOTS = 17
RING_SLOTS = (11, 12)                        # a ring's enchant counts only for an enchanter (WoWSims' sim_request.go)
ENCHANTING = "enchanting"
_unknown, _unknown_lock = set(), threading.Lock()     # (kind, id) WoWSims didn't know, logged once each
MAX_UNKNOWN = 200

# UnitRace's file name -> WoWSims' Race; Skyborne by faction, as WoWSims' own exporter reads it
RACES = {"Human": "RaceHuman", "Dwarf": "RaceDwarf", "NightElf": "RaceNightElf", "Gnome": "RaceGnome",
         "Orc": "RaceOrc", "Scourge": "RaceUndead", "Undead": "RaceUndead", "Tauren": "RaceTauren",
         "Troll": "RaceTroll", "BloodElf": "RaceBloodElf", "HighOrderSkyborne": "RaceHighOrderSkyborne",
         "WindshaperSkyborne": "RaceWindshaperSkyborne"}

# WoWSims' default raid boss (its spec defaults): level 63, 3 minutes
ENCOUNTER = {"duration": 180, "durationVariation": 5, "executeProportion20": 0.2, "executeProportion35": 0.35,
             "targets": [{"id": 31146, "name": "Raid Target", "level": 63, "mobType": "MobTypeDragonkin",
                          "stats": [0] * 14 + [320] + [0] * 9 + [54, 0, 0, 3731, 0, 1428900] + [0] * 9,
                          "minBaseDamage": 2601, "damageSpread": 0.5, "swingSpeed": 2, "parryHaste": True,
                          "canCrush": True}]}

# per class: WoWSims' class and spec, the talent trees in the game's tab order, each tree's rotation file in WoWSims'
# repository, the stats weighed (code, "s" stat or "p" pseudo-stat index, the amount added) with the reference first,
# and the buffs and consumables of WoWSims' preset for it (ui/specs/<class>/<spec>/presets.ts)
CLASSES = {
    "MAGE": {
        "class": "ClassMage", "spec": "mage",
        "options": {"options": {"classOptions": {"defaultMageArmor": "MageArmorMageArmor"}}},
        "trees": ["Arcane", "Fire", "Frost"],
        "apl": {"Arcane": "ui/specs/mage/dps/apls/arcane.apl.json",
                "Fire": "ui/specs/mage/dps/apls/deepFire.apl.json",
                "Frost": "ui/specs/mage/dps/apls/frost.apl.json"},
        "weigh": [("SP", "s", 5, 50), ("IN", "s", 3, 50), ("SI", "s", 13, 50), ("MR", "s", 31, 10),
                  ("AC", "s", 6, 50), ("FI", "s", 7, 50), ("FR", "s", 8, 50), ("CR", "s", 18, 28), ("HT", "s", 17, 20),
                  ("CS", "p", 15, 2), ("HS", "p", 13, 2)],
        "consumables": {"flaskId": 13513, "elixirIds": [13455, 250349, 20074], "foodId": 13932, "mhImbueId": 20750,
                        "mhWeaponImbueId": 277503, "resourcePotId": 13445},
        "buffs": {"greaterBlessingOfKings": True, "greaterBlessingOfWisdom": 1, "innervates": 1},
        "party": {"manaSpringTotem": 2, "manaTideTotems": 1, "darkInspiration": 5},
        "raid": {"prayerOfSpirit": True, "arcaneIntellect": True, "markOfTheWild": True, "powerWordFortitude": True,
                 "moonkinAura": True},
        "debuffs": {"curseOfElements": True, "judgementOfWisdom": True},
        "player": {"reactionTimeMs": 100, "channelClipDelayMs": 100, "distanceFromTarget": 20},
    },
}


class SimError(Exception):
    """A character WoWSims can't sim (class, level, missing gear or talents), or a sim that failed."""


def simmable(summary):
    """Whether WoWSims sims this character: level 60 and a class in CLASSES."""
    return (summary or {}).get("level") == 60 and str((summary or {}).get("class") or "").upper() in CLASSES


def race(race_file, faction):
    """WoWSims' race for UnitRace's file name ("Skyborne": High Order for the Alliance, Windshaper for the Horde)."""
    r = str(race_file or "").replace(" ", "")
    if r == "Skyborne":
        return "RaceWindshaperSkyborne" if faction == "Horde" else "RaceHighOrderSkyborne"
    if r not in RACES:
        raise SimError("WoWSims has no race %r" % race_file)
    return RACES[r]


def tree_points(talents, trees=3):
    """Points spent in each talent tree (the addon's talent list: pos "tab tier column")."""
    pts = [0] * trees
    for t in talents or ():
        tab = int(t["pos"][0]) - 1
        if 0 <= tab < trees:
            pts[tab] += int(t.get("rank") or 0)
    return pts


def spec_of(cls, talents):
    """The spec WoWSims sims for a class: the tree with the most points (the first on a tie)."""
    conf = CLASSES[str(cls).upper()]
    pts = tree_points(talents, len(conf["trees"]))
    return conf["trees"][pts.index(max(pts))]


def talents_string(talents, trees=3):
    """WoWSims' talent string: one run of digits per tree, each talent's rank in the tree's order (row by row, left to
    right), trailing zeroes and dashes trimmed (ui/sim/talents/talents_string.ts)."""
    per = [[] for _ in range(trees)]
    for t in talents or ():
        pos = t["pos"]
        tab = int(pos[0]) - 1
        if 0 <= tab < trees:
            per[tab].append((int(pos[1]), int(pos[2]), min(int(t.get("rank") or 0), 9)))
    runs = ["".join(str(r) for _, _, r in sorted(rows)).rstrip("0") for rows in per]
    return "-".join(runs).rstrip("-")


def enchanter(skills):
    """Whether a character has Enchanting: skills = its professions' names (any case; a dict: its keys), None when
    they aren't known (then no)."""
    if not skills:
        return False
    if isinstance(skills, str):
        skills = (skills,)
    return any(str(s).strip().lower() == ENCHANTING for s in skills)


def as_id(v):
    """An id as the strip sent it (int, float or text), or 0."""
    try:
        return int(float(v))
    except (TypeError, ValueError, OverflowError):
        return 0


def _left_out(what, value, item_id):
    """An enchant or suffix WoWSims doesn't know, logged once per id."""
    with _unknown_lock:
        if (what, value) in _unknown or len(_unknown) >= MAX_UNKNOWN:
            return
        _unknown.add((what, value))
    log("simbuild", "WoWSims has no %s %d (on item %d): simmed without it" % (what, value, item_id))


def item_spec(inv_slot, it, db=None, skills=None):
    """WoWSims' ItemSpec for an item worn in an inventory slot (it: parse_item's dict, its "enchant" and "suffix" when
    the addon sent them): {"id"}, with "enchant" and "randomSuffix" when known and, with db, when WoWSims knows them
    (an enchant for that slot; a suffix by its id, else without its sign). A ring's enchant only for an enchanter."""
    spec = {"id": int(it["id"])}
    enchant, suffix = as_id(it.get("enchant")), as_id(it.get("suffix"))
    if enchant and (inv_slot not in RING_SLOTS or enchanter(skills)):
        if db is None or db.enchant(enchant, inv_slot) is not None:
            spec["enchant"] = enchant
        else:
            _left_out("enchant", enchant, spec["id"])
    if suffix and db is None:
        spec["randomSuffix"] = abs(suffix)
    elif suffix:
        known = getattr(db, "suffixes", None) or {}
        found = suffix if suffix in known else abs(suffix) if abs(suffix) in known else 0
        if found:
            spec["randomSuffix"] = found
        else:
            _left_out("random suffix", suffix, spec["id"])
    return spec


def equipment(worn, db=None, skills=None):
    """WoWSims' equipment: one entry per sim slot, an empty one where nothing is worn ({"items": [...]}); each item
    with its enchant and random suffix as item_spec gives them (db: item_db.ItemDB, or None; skills: the character's
    professions, None when unknown: a ring's enchant is left out, as WoWSims does for a character without
    Enchanting)."""
    items = [{} for _ in range(SIM_SLOTS)]
    for it in worn or ():
        inv = as_id(str(it.get("key") or 0).split(".")[0])
        slot = INV_TO_SIM.get(inv)
        if slot is not None and it.get("id"):
            items[slot] = item_spec(inv, it, db, skills)
    return {"items": items}


def bonus_stats(bonus):
    """{(kind, index): amount} -> UnitStats protojson."""
    stats, pseudo = [0.0] * STAT_COUNT, [0.0] * PSEUDO_COUNT
    for (kind, idx), v in (bonus or {}).items():
        (pseudo if kind == "p" else stats)[idx] = float(v)
    return {"stats": stats, "pseudoStats": pseudo}


def request(char, rotation, iterations=10000, seed=1, bonus=None, db=None):
    """The RaidSimRequest for a character: char = {"class", "race", "faction", "talents" (the addon's list),
    "worn" (gear the addon reports: parse_item dicts), and "skills" (its professions' names) when known};
    rotation = the spec's APL (its .apl.json, parsed); db = WoWSims' item database (item_db.ItemDB), when loaded:
    what it doesn't know of the gear's enchants and suffixes is left out (equipment)."""
    cls = str(char.get("class") or "").upper()
    conf = CLASSES.get(cls)
    if conf is None:
        raise SimError("WoWSims has no sim for %s here" % (cls.capitalize() or "this class"))
    gear = equipment(char.get("worn"), db, char.get("skills"))
    if not any(gear["items"]):
        raise SimError("no gear to sim")
    talents = talents_string(char.get("talents"), len(conf["trees"]))
    if not talents:
        raise SimError("no talents to sim")
    player: dict = dict(conf["player"], name="Glide", race=race(char.get("race"), char.get("faction")),
                  **{"class": conf["class"], "equipment": gear, "consumables": conf["consumables"],
                     "buffs": conf["buffs"], conf["spec"]: conf["options"], "talentsString": talents,
                     "rotation": rotation})
    if bonus:
        player["bonusStats"] = bonus_stats(bonus)
    return {"raid": {"parties": [{"players": [player], "buffs": conf["party"]}], "buffs": conf["raid"],
                     "debuffs": conf["debuffs"]},
            "encounter": ENCOUNTER, "simOptions": {"iterations": int(iterations), "randomSeed": int(seed)}}


def stat_weights(cls, sim, request_for):
    """Each weighed stat's worth, measured: the DPS with a little more of it (request_for(bonus)) against without, on
    the same seed, per point; and against the reference stat (the first weighed) as EP. sim(request) -> DPS.
    -> {"dps", "per_ref" (DPS per point of the reference stat), "w": {code: EP}}. A stat worth less than nothing
    (the sim's noise) weighs 0."""
    weigh = CLASSES[str(cls).upper()]["weigh"]
    base = sim(request_for(None))
    per = {}
    for code, kind, idx, amount in weigh:
        per[code] = (sim(request_for({(kind, idx): amount})) - base) / amount
        if len(per) == 1 and per[code] <= 0:                 # (the reference: no use simming the rest)
            raise SimError("the reference stat added no DPS: can't weigh against it")
    ref = per[weigh[0][0]]
    return {"dps": round(base, 1), "per_ref": round(ref, 4),
            "w": {code: round(max(0.0, v / ref), 3) for code, v in per.items()}}
