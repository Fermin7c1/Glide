#!/usr/bin/env python3
"""WoWSims' item database (companion/item_db.py) on a cut of its db.json and leftover_db.json
(tests/fixtures/wowsims_db_small.json): who can wear what (a mage's cloth, staff, one-hand sword in the main hand and
wand; not plate, a two-handed sword or an off-hand dagger; a paladin's plate, two-hander and libram), the required level
WoWSims falls back on and a character's level band, each side's items and vendors (Deliana for the Alliance, Mokvar for
the Horde), the random suffix formula (Willow Pants of the Eagle SA3/IN3, Willow Gloves of the Owl IN2/SI2, Native
Robe of Intellect IN4, a suffix id sent negative), enchants by effect id and slot, Blackrock Spire's two halves, the
honor vendors' zones, reputation vendors, stats in Glide's codes and whether an item can be had now.

Run: python3 tests/test_item_db.py   (prints OK)
"""
import json
import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import item_db  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


with open(os.path.join(ROOT, "tests", "fixtures", "wowsims_db_small.json"), encoding="utf-8") as fh:
    FX = json.load(fh)
DB_BYTES = json.dumps(FX["db"]).encode("utf-8")
LEFTOVER_BYTES = json.dumps(FX["leftover"]).encode("utf-8")
db = item_db.ItemDB.load(DB_BYTES, LEFTOVER_BYTES, "v1.0.4")

MAGE = {"class": "MAGE", "level": 60, "faction": "Alliance"}
PALADIN = {"class": "PALADIN", "level": 60, "faction": "Alliance"}
HORDE_MAGE = dict(MAGE, faction="Horde")
HORDE_PALADIN = dict(PALADIN, faction="Horde")

# ---- loading: both files, db.json alone, a broken leftover file, something that isn't a database ----------------------
check(db.tag == "v1.0.4" and len(db) == 32 and db.leftover, (db.tag, len(db), db.leftover))
check(db.name(6540) == "Willow Pants" and db.item("6540")["id"] == 6540, "leftover items, ids as text too")
alone = item_db.ItemDB.load(DB_BYTES, None, "v1.0.4")
check(len(alone) == 28 and alone.item(6540) is None and not alone.leftover, len(alone))
broken = item_db.ItemDB.load(DB_BYTES, b"{not json", "v1.0.4")
check(len(broken) == 28 and not broken.leftover, "a broken leftover file is left out")
for bad in (b"[]", b'{"zones": []}'):
    try:
        item_db.ItemDB.load(bad, None, "v1.0.4")
        check(False, "not a database: %r" % bad)
    except ValueError:
        pass
check(db.item(999999) is None and db.name(None) is None and db.item("x") is None, "unknown items")
check(db.npc(1853)["name"] == "Darkmaster Gandling" and db.zone(2057)["name"] == "Scholomance", "npcs and zones")
check(db.npc(1) is None and db.zone(2918) is None, "the honor vendors' zones aren't in db.json")
check(db.find("robe of the archmage") == [14152] and db.find(" Willow Pants ") == [6540] and db.find("nope") == [],
      "find by name")

# ---- eligibility (WoWSims' items.ts) ------------------------------------------------------------------------------
for item_id, mage, paladin, what in (
        (14152, True, False, "Robe of the Archmage: cloth, mages only"),
        (20041, False, True, "Highlander's Plate Girdle: plate"),
        (647, False, True, "Destiny: a two-handed sword"),
        (873, True, False, "Staff of Jordan: a staff, two-handed for a mage"),
        (754, True, True, "Shortsword of Vengeance: a one-hand sword (the main hand)"),
        (18392, False, False, "Distracting Dagger: an off-hand dagger, and neither dual wields"),
        (13371, True, True, "Father Flame: held in the off hand"),
        (5215, True, False, "Ember Wand: a wand, in the ranged slot"),
        (22400, False, True, "Libram of Truth: a libram"),
        (226935, True, False, "Sorcerer's Crown: mages only"),
        (12543, True, True, "Songstone of Ironforge: a ring"),
        (13968, True, True, "Eye of the Beast: a trinket"),
        (287031, False, True, "Spearfisher's Waders: leather")):
    check(db.eligible(item_id, MAGE) is mage, "mage: " + what)
    check(db.eligible(item_id, PALADIN) is paladin, "paladin: " + what)
check(not db.eligible(14152, {"class": "PRIEST", "level": 60}), "a class without a row: nothing")
check(not db.eligible(999999, MAGE) and not db.eligible(14152, None), "unknown item, no character")
check(not db.eligible(20041, HORDE_PALADIN), "an Alliance-only item for the Horde")
check(not db.eligible(20068, MAGE) and db.eligible(20068, HORDE_MAGE), "a Horde-only cloak")
check(db.eligible(20068, {"class": "MAGE", "level": 60}), "side unknown: either side's items")

# ---- required level: reqLvl, else 60 from item level 60 and item level - 5 below; the band is 10 below to 5 above ---
check(item_db.required_level(db.item(14152)) == 57, "reqLvl as given")
check(item_db.required_level(db.item(226935)) == 60, "no reqLvl, item level 60: 60")
check(item_db.required_level(db.item(13968)) == 60, "no reqLvl, item level 63: 60")
check(item_db.required_level(db.item(4746)) == 37, "no reqLvl, item level 42: 37")
check(db.eligible(14152, dict(MAGE, level=52)) and not db.eligible(226935, dict(MAGE, level=52)),
      "57 is within 5 levels of 52, 60 isn't")
check(db.eligible(226935, dict(MAGE, level=55)), "60 is within 5 levels of 55")
check(db.eligible(754, MAGE) and 754 not in db.band(MAGE), "wearable, but below the level-60 band")
check(4746 in db.band(dict(MAGE, level=32)) and 4746 in db.band(dict(MAGE, level=47))
      and 4746 not in db.band(dict(MAGE, level=48)) and 4746 not in db.band(dict(MAGE, level=31)), "the band's edges")
check(db.band(MAGE) == [12543, 13253, 13371, 13968, 14152, 14558, 16337, 16437, 16686, 18820, 21809, 22268,
                        22301, 226935], db.band(MAGE))
check(16533 in db.band(HORDE_MAGE) and 16437 not in db.band(HORDE_MAGE), "each side's PvP gear (Warlord's, Marshal's)")
check(db.band(PALADIN) == [647, 11735, 12543, 13253, 13371, 13968, 14558, 16337, 16686, 18820, 20041, 21809, 22268,
                           22400, 287031], db.band(PALADIN))
check(20041 not in db.band(HORDE_PALADIN) and 20068 in db.band(HORDE_MAGE), "each side's band")
check(db.band(dict(MAGE, level=12)) == [6540, 6541, 6543, 14109], "a low-level band: leftover_db.json's items")
check(db.band({"class": "MAGE", "faction": "Alliance"}) == [] and db.band(None) == [], "no level: no band")
check(db.band({"class": "WARRIOR", "level": 60}) == [], "no class row: no band")

# ---- sources: each side's vendors and quests ---------------------------------------------------------------------
def kinds(item_id, faction):
    return [s["kind"] for s in db.sources(item_id, faction)]


crown = db.sources(226935, "Alliance")
check(len(crown) == 1 and crown[0]["kind"] == "vendor" and crown[0]["npc_name"] == "Deliana"
      and crown[0]["npc"] == 230319 and crown[0]["zone"] == 1537 and crown[0]["zone_name"] == "Ironforge", crown)
check([(s["npc_name"], s["zone_name"]) for s in db.sources(226935, "Horde")] == [("Mokvar", "Orgrimmar")], "Horde")
check([s["npc_name"] for s in db.sources(226935, None)] == ["Mokvar", "Deliana"], "side unknown: both vendors")
check([s["quest"] for s in db.sources(13968, "Alliance")] == [5102], "Eye of the Beast: the Alliance's quest")
check([(s["quest"], s["quest_name"]) for s in db.sources(13968, "Horde")] == [(4974, "For The Horde!")], "Horde")
check(kinds(12543, "Horde") == [] and kinds(4746, "Horde") == ["quest"], "an Alliance quest; a quest for both")
for s in db.sources(16686, "Alliance") + db.sources(20041, "Alliance") + db.sources(14558, "Horde"):
    check(tuple(s) == item_db.SRC_KEYS and s["conf"] == 0.8 and s["src"] == "ws", s)
check(db.sources(999999, "Alliance") == [], "unknown item: no sources")

# ---- drops: Blackrock Spire's halves, trash, world drops, a boss named by its loot group, a chest ---------------------
gandling = db.sources(16686, "Alliance")[0]
check((gandling["kind"], gandling["npc"], gandling["npc_name"], gandling["zone_name"], gandling["instance"],
       gandling["difficulty"]) == ("drop", 1853, "Darkmaster Gandling", "Scholomance", "Scholomance", 1), gandling)
upper = db.sources(22268, "Alliance")[0]
check((upper["npc_name"], upper["zone"], upper["zone_name"], upper["instance"])
      == ("General Drakkisath", 1583, "Blackrock Spire", "Upper Blackrock Spire"), upper)
lower = db.sources(13253, "Alliance")[0]
check((lower["npc_name"], lower["instance"]) == ("Quartermaster Zigris", "Lower Blackrock Spire"), lower)
several = db.sources(13371, "Alliance")[0]
check((several["kind"], several["npc"], several["npc_name"], several["instance"])
      == ("drop", None, None, "Blackrock Spire"), "Multiple NPCs: no name, no half")
check(set(item_db.BRS_WING.values()) == {"Upper", "Lower"} and item_db.BRS_WING[10363] == "Upper", "BRS_WING")
trash = db.sources(1486, "Alliance")[0]
check((trash["kind"], trash["npc_name"], trash["instance"]) == ("trash", None, "Blackfathom Deeps"), trash)
check(kinds(21809, "Alliance") == ["trash"], "a loot group named Trash")
world = db.sources(14558, "Alliance")[0]
check((world["kind"], world["zone"], world["npc"]) == ("world", None, None), world)
hurley = db.sources(11735, "Alliance")[0]
check((hurley["npc"], hurley["npc_name"]) == (9537, "Hurley Blackbreath"), "the group names the boss: " + str(hurley))
heirlooms = db.sources(22301, "Alliance")[0]
check((heirlooms["kind"], heirlooms["npc"], heirlooms["npc_name"], heirlooms["instance"])
      == ("drop", None, "Sothos and Jarien's Heirlooms", "Stratholme"), heirlooms)
open_world = db.sources(287031, "Alliance")[0]
check((open_world["npc_name"], open_world["zone_name"], open_world["instance"], open_world["difficulty"])
      == ("Blackmaw Headhunter", "Azshara", None, None), "an open-world drop has no instance")
check([s["npc_name"] for s in db.sources(18820, "Alliance")] == ["Magmadar", "Garr", "Baron Geddon",
                                                                    "Golemagg the Incinerator"], "four bosses")
check(len(db.sources(6543, "Alliance")) == 3, "a leftover item's drops")

# ---- the honor vendors' zones (2917, 2918) read "PvP vendor"; reputation vendors are one "rep" source ---------------
clate = db.sources(16437, "Alliance")
check([(s["kind"], s["npc_name"], s["zone"], s["zone_name"]) for s in clate]
      == [("vendor", "Sergeant Major Clate", 2918, "PvP vendor"), ("pvp", None, None, None)], clate)
check([(s["npc_name"], s["zone"], s["zone_name"]) for s in db.sources(16533, "Horde") if s["kind"] == "vendor"]
      == [("Lady Palanseer", 2917, "PvP vendor")], "Horde's honor vendor")
# the other side's honor vendor is left out, and so is the PvP source WoWSims gives no side (regression: a Warlord's
# cowl, sold only by a Horde vendor, read "PvP" for the Alliance: ranked and named as an upgrade)
check(kinds(16533, "Alliance") == [] and not db.eligible(16533, MAGE) and kinds(16533, "Horde") == [
    "vendor", "pvp"], "the other side's PvP gear")
hawke = db.sources(20041, "Alliance")
check([(s["kind"], s["npc_name"], s["zone_name"], s["rep_faction"], s["rep_level"]) for s in hawke]
      == [("rep", "Samuel Hawke", "Arathi Highlands", "The League of Arathor", "Honored"),
          ("pvp", None, None, None, None)], hawke)
check(db.sources(20041, "Horde") == [], "an Alliance-only item has no sources for the Horde")
defiler = db.sources(20177, "Horde")
check([(s["kind"], s["npc"], s["rep_faction"], s["rep_level"]) for s in defiler]
      == [("pvp", None, None, None), ("rep", None, "The Defilers", "Honored")], "a reputation with no vendor")
check(db.sources(14152, "Alliance")[0]["profession"] == "Tailoring" and kinds(14152, "Horde") == ["crafted"],
      "crafted")
check(kinds(16337, "Alliance") == ["pvp"], "PvP")

# ---- random suffixes: round(stat x randPropPoints / 10000), the id as sent, then without its sign ------------------
check(db.suffix_stats(6540, 12718) == {"SA": 3, "IN": 3}, "Willow Pants of the Eagle")
check(db.suffix_stats(6541, 12720) == {"IN": 2, "SI": 2}, "Willow Gloves of the Owl")
check(db.suffix_stats(14109, 12729) == {"IN": 4}, "Native Robe of Intellect")
check(db.suffix_stats(6540, -12718) == {"SA": 3, "IN": 3} and db.suffix_stats(6540, "-12718") == {"SA": 3, "IN": 3},
      "a suffix id sent negative")
check(db.suffix_stats(6540, 1) == {} and db.suffix_stats(999999, 12718) == {} and db.suffix_stats(6540, None) == {},
      "unknown suffix or item")

# ---- enchants: the same effect id is another enchant on another slot -------------------------------------------------
check(db.enchant(66, 9)["name"] == "Enchant Bracer - Minor Stamina", "66 on the wrists")
check(db.enchant(66, 8)["name"] == "Enchant Boots - Minor Stamina", "66 on the feet")
check(db.enchant(66, 17)["name"] == "Enchant Shield - Minor Stamina", "66 on the off hand: the shield's")
check(db.enchant(66, 16) is None and db.enchant(66, 5) is None, "66 isn't a main-hand or chest enchant")
gi = db.enchant(1883, 9)
check(gi == {"id": 1883, "name": "Enchant Bracer - Greater Intellect", "stats": {"IN": 7}, "phase": 1, "proc": 0,
             "item_id": 16214, "spell_id": 13825}, gi)
check(db.enchant(2504, 16)["stats"] == {"SP": 30} and db.enchant(2504, "16")["id"] == 2504, "weapon spell power")
check(db.enchant(2544, 1)["stats"] == {"SP": 8} and db.enchant(2544, 7)["stats"] == {"SP": 8}, "head and legs")
check(db.enchant(15, 10)["stats"] == {"SA": 1, "AR": 8} and db.enchant(15, 5) and db.enchant(15, 1) is None,
      "an armor kit's extra slots")
check(db.enchant(99999, 1) is None and db.enchant(66, None) is None and db.enchant(66, 4) is None, "unknown")

# ---- stats in Glide's codes ------------------------------------------------------------------------------------------
check(db.stats(14152) == {"IN": 12, "SP": 40, "CR": 14, "AR": 96}, "damage and healing: SP, no HL " +
      str(db.stats(14152)))
check(db.stats(4746) == {"SP": 6, "SI": 18, "AR": 55, "HL": 16}, "healing apart: HL")
check(db.stats(13968) == {"CS": 1}, "a pseudo-stat: 1% spell crit")
check(db.stats(226935) == {"SA": 12, "IN": 25, "SP": 21, "PEN": 3, "CR": 14, "AR": 75}, db.stats(226935))
check(db.stats(873) == {"IN": 20, "SP": 60, "SI": 10, "HT": 15, "DP": 30.1, "WS": 3.7}, "a weapon's DPS and speed")
check("WS" not in db.stats(13371), "an off-hand frill has no speed")
check(db.stats(22268) == {} and db.stats(999999) == {}, "fire resistance isn't kept; unknown item")
check(item_db.codes({"4": 44, "5": 15}) == {"SP": 15, "HL": 44}, "codes: healing over damage")
check(item_db.codes([0, 0, 0, 7], [0] * 13 + [1]) == {"IN": 7, "HS": 1}, "codes: lists by index")
check(item_db.codes({"27": 50, "28": 10}) == {"AR": 60}, "armor and bonus armor")

# ---- can it be had now ---------------------------------------------------------------------------------------------
for item_id, faction, bonding, want in (
        (14152, "Alliance", None, "now"), (226935, "Horde", None, "now"),
        (18820, "Alliance", None, "later"), (16437, "Alliance", None, "later"),
        (647, "Alliance", None, "nosrc"), (647, "Alliance", item_db.BOND_EQUIP, "now"), (647, "Alliance", 1, "nosrc"),
        (20041, "Horde", item_db.BOND_EQUIP, "nosrc"), (12543, "Horde", None, "nosrc"),
        (999999, "Alliance", None, "nosrc"), (999999, "Alliance", item_db.BOND_EQUIP, "now")):
    check(db.avail(item_id, faction, bonding) == want, (item_id, faction, bonding, db.avail(item_id, faction, bonding)))
check(db.avail(14152, "Alliance") == "now", "bonding is optional")
# judged by the sources given (research's, merged from everywhere): an item WoWSims has no source for that the
# knowledge base knows can be had now (regression: it read "no known source")
check(db.avail(647, "Alliance", None, [item_db.src("drop", npc=1, conf=0.6, src="kb")]) == "now", "kb source")
check(db.avail(16686, "Alliance", None, []) == "nosrc", "none given: none")
check(db.avail(20068, "Alliance", None, [item_db.src("drop")]) == "nosrc", "the other side's item stays nosrc")
check(db.avail(18820, "Alliance", None, []) == "later", "a later phase stays later")

# ---- a quest's side, from any item's sources (research settles the knowledge base's "Both" quests with it) ----------
check(db.quest_side(4974) == "Horde" and db.quest_side("5102") == "Alliance" and db.quest_side(709) is None and
      db.quest_side(None) is None, "quest_side")

# ---- PvP gear WoWSims gives no side: the rank title in the name says it (regression: for an Alliance mage the
# Horde's Premier High Warlord's Spellblade, the Alliance's Mageblade twin in stats and the lower id, came first in the
# ranks and could be named the next upgrade); a source with a side wins over none; a drop is anyone's ----------------
pv = json.loads(DB_BYTES)
SPELL = {"type": 13, "weaponType": 2, "handType": 2, "reqLvl": 60, "phase": 1, "quality": 4,
         "scalingOptions": {"0": {"ilvl": 78, "stats": {"5": 94}}}}
pv["items"] = [dict(SPELL, id=272683, name="Premier High Warlord's Spellblade", sources=[{"pvp": {}}]),
               dict(SPELL, id=272856, name="Premier Grand Marshal's Mageblade", sources=[{"pvp": {}}]),
               dict(SPELL, id=900001, name="Knight-Champion's Dagger", sources=[{"pvp": {}}]),
               dict(SPELL, id=900002, name="Champion's Dagger", sources=[{"pvp": {}}]),
               dict(SPELL, id=900003, name="Insignia of the Horde", type=12, sources=[{"pvp": {}}]),
               dict(SPELL, id=900004, name="Battle Dagger", sources=[{"pvp": {}}]),
               dict(SPELL, id=900005, name="Frostwolf Legionnaire's Dagger", sources=[
                   {"soldBy": {"npcId": 1, "npcName": "Jekyll", "zoneId": 2597, "faction": 2}}, {"pvp": {}}]),
               dict(SPELL, id=900006, name="Field Dagger", sources=[
                   {"soldBy": {"npcId": 2, "npcName": "Quartermaster", "zoneId": 2597, "faction": 2}}, {"pvp": {}}]),
               dict(SPELL, id=900007, name="Lost Champion's Dagger", sources=[{"drop": {"zoneId": 1337}}, {"pvp": {}}]),
               dict(SPELL, id=900008, name="Neutral Dagger", sources=[
                   {"soldBy": {"npcId": 3, "npcName": "Goblin", "zoneId": 1337}},
                   {"soldBy": {"npcId": 4, "npcName": "Quartermaster", "zoneId": 2597, "faction": 1}}, {"pvp": {}}])]
pdb = item_db.ItemDB.load(json.dumps(pv).encode(), None, "v1.0.4")
for iid, horde in ((272683, True), (272856, False), (900001, False), (900002, True), (900003, True),
                   (900005, True), (900006, True)):
    mine, other = (HORDE_MAGE, MAGE) if horde else (MAGE, HORDE_MAGE)
    check(pdb.eligible(iid, mine) and not pdb.eligible(iid, other) and iid in pdb.band(mine)
          and iid not in pdb.band(other), ("one side's PvP gear", iid))
    check(pdb.avail(iid, mine["faction"]) == "now" and pdb.avail(iid, other["faction"]) == "nosrc"
          and pdb.sources(iid, other["faction"]) == [], ("the other side's PvP gear can't be had", iid))
check(pdb.eligible(900004, MAGE) and pdb.eligible(900004, HORDE_MAGE) and pdb.avail(900004, "Alliance") == "now",
      "PvP gear with no side to tell: either")
check([s["kind"] for s in pdb.sources(900007, "Alliance")] == ["drop"] and pdb.eligible(900007, MAGE)
      and [s["kind"] for s in pdb.sources(900007, "Horde")] == ["drop", "pvp"], "a drop is anyone's, the PvP Horde's")
check([s["kind"] for s in pdb.sources(900008, "Horde")] == ["vendor"] and pdb.eligible(900008, HORDE_MAGE)
      and [s["kind"] for s in pdb.sources(900008, "Alliance")] == ["vendor", "vendor", "pvp"],
      "a vendor of either side; the PvP source the Alliance's (its only sided vendor)")
check(item_db.pvp_side({"name": "Lieutenant Commander's Crown"}) == 1 and item_db.pvp_side(
    {"name": "Blood Guard's Silk Footwraps"}) == 2 and item_db.pvp_side({"name": "Sergeant's Cape"}) == 0, "titles")

# ---- WoWSims' names cleaned: a few keep a backslash before a quote (regression: 'Eric \\"The Swift\\"') ---------------
esc = json.loads(DB_BYTES)
esc["npcs"] = [{"id": 6907, "name": 'Eric \\"The Swift\\"', "zoneId": 1337}]
esc["zones"] = [{"id": 1337, "name": "Uldaman"}]
esc["items"] = [{"id": 9394, "name": 'Horned \\"Viking\\" Helmet', "sources": [
    {"drop": {"npcId": 6907, "zoneId": 1337, "difficulty": 1}},
    {"quest": {"id": 1, "name": 'The \\"Real\\" Deal'}},
    {"soldBy": {"npcId": 2, "npcName": 'Old \\"Salty\\" Pete', "zoneId": 1337}}]}]
edb = item_db.ItemDB.load(json.dumps(esc).encode(), None, "v1.0.4")
got = edb.sources(9394, "Alliance")
check([s["npc_name"] or s["quest_name"] for s in got] == ['Eric "The Swift"', 'The "Real" Deal', 'Old "Salty" Pete'] and
      edb.name(9394) == 'Horned "Viking" Helmet' and edb.find('horned "viking" helmet') == [9394], got)
print("OK")
