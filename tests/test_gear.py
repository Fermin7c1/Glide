#!/usr/bin/env python3
"""Gear: page parsing -> evaluation with RestedXP weights -> upgrade / reward triggers -> offline callouts."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "companion"))
from state import GameState
from rules import Rules
from gear import Gear

def h(s):
    x = 0
    for b in s.encode():
        x = (x * 31 + b) % 65521
    return x

g = Gear({"kind": "Speedrun"})
assert g.weights("WARRIOR", 12)["title"].startswith("Warrior Speedrun 10"), g.weights("WARRIOR", 12)["title"]

worn = [
  "16:2361:INVTYPE_2HWEAPON:2:5:5:1:1:1:Battleworn Hammer:DP3.5/WS2.9",
  "5:6125:INVTYPE_CHEST:4:2:5:1:1:1:Brawler's Harness:AR30",
  "7:139:INVTYPE_LEGS:4:3:5:1:1:1:Brawler's Pants:AR20",
]
bags = [
  "0.3:1:INVTYPE_2HWEAPON:2:1:14:2:9:1:Heavy Hand Axe:DP8.1/ST3/WS3.2",          # big upgrade
  "0.4:2:INVTYPE_CHEST:4:1:11:1:7:1:Linen Robe:AR15/IN2",                          # cloth, worse
  "0.5:3:INVTYPE_LEGS:4:4:20:2:20:0:Plate Leggings:AR300/ST5",                     # plate at 12: not usable
  "1.1:4:INVTYPE_FINGER:4:0:15:2:14:0:Ring of Might:ST4/SA2",                      # req 14: later
]
st = GameState()
rules = Rules({})
rules.gear = g
def send(core, pages):
    st.update({k: str(v) for k, v in core.items()}, {})
    for p in pages:
        st.update({k: str(v) for k, v in core.items()}, p)
    return rules.check(st, [])

core = {"t": 1, "l": 12, "x": 1, "X": 100, "c": 0, "st": 1, "wn": 1, "wh": h(",".join(worn)), "in": 1,
        "ih": h(",".join(bags)), "rn": 0, "rh": 0, "rq": 0}
tr = send(core, [{"pg": "B", "cl": "WARRIOR", "ra": "Human", "fa": "Alliance"},
                 {"pg": "W0", "g": ",".join(worn)}, {"pg": "I0", "g": ",".join(bags)}])
gu = [t for t in tr if t.kind == "gear_upgrade"]
assert gu, tr
items = {u["item"]: u for u in gu[0].facts["upgrades"]}
assert "Heavy Hand Axe" in items and items["Heavy Hand Axe"]["slot"] == "main hand", items
assert "Linen Robe" not in items and "Plate Leggings" not in items, items
assert items["Ring of Might"]["equip_at_level"] == 14, items
print("upgrades:", [(u["item"], u["gain"], u["equip_at_level"]) for u in gu[0].facts["upgrades"]])
# not repeated
assert not [t for t in send(core, []) if t.kind == "gear_upgrade"]
# level 14: the ring becomes equippable -> told again once
core["l"] = 14
tr = [t for t in send(core, []) if t.kind == "gear_upgrade"]
assert tr and tr[0].facts["upgrades"][0]["item"] == "Ring of Might" and not tr[0].facts["upgrades"][0]["equip_at_level"], tr
# quest reward window: ranked choices, urgent
choices = ["1:10:INVTYPE_CHEST:4:3:16:2:12:1:Chainmail Vest:AR95/SA3", "2:11:INVTYPE_WAIST:4:1:16:2:12:1:Cloth Sash:AR8/IN3"]
core.update({"rn": 1, "rh": h(",".join(choices)), "rq": 783})
tr = [t for t in send(core, [{"pg": "R0", "g": ",".join(choices)}]) if t.kind == "reward_choice"]
assert tr and tr[0].priority == 1 and tr[0].facts["ranked"][0]["item"] == "Chainmail Vest", tr
core.update({"rn": 0})
send(core, [])
assert st.gear["R"] == [] and rules.rewards == [], (st.gear["R"], rules.rewards)
# offline coach lines
from coach import Coach
from glidedb import GlideDB
os.environ.pop("ANTHROPIC_API_KEY", None)
c = Coach({"enabled": False}, GlideDB("/nonexistent.db"), lambda m: None)
c.state = st
from rules import Trigger
ctx = {"player": {}, "rxp_steps": [], "quests": {}}
print(c.offline([Trigger("reward_choice", {"ranked": [{"item": "Chainmail Vest", "gain": 9.5, "slot_name": "chest"}]}, 1),
                 Trigger("gear_upgrade", {"upgrades": [{"item": "Heavy Hand Axe", "slot": "main hand", "replaces": "Battleworn Hammer", "gain": 60, "equip_at_level": None}]})], ctx, None))
print("OK")

# every bag item gets a verdict; an armor-only item into an empty slot counts as an upgrade
parse_item = GameState.parse_item
w2 = [parse_item(x) for x in worn[:2]]
b2 = [parse_item("0.6:10:INVTYPE_LEGS:4:3:5:0:1:1:Flimsy Chainmail Pants:AR24"),
      parse_item(bags[1]), parse_item(bags[2])]
allv = {x["item"]: x["verdict"] for x in g.compare_all(w2, b2, "WARRIOR", 6)}
assert allv["Flimsy Chainmail Pants"].startswith("upgrade") and "empty" in allv["Flimsy Chainmail Pants"], allv
assert allv["Linen Robe"] == "not better" and allv["Plate Leggings"].startswith("can't use"), allv
assert g.compare_all(w2, b2, "SHAMAN", 6)[0]["verdict"].startswith("can't use"), "mail before proficiency"
print("compare_all OK", allv)

# trained skills from the addon beat the class table: an untrained 2H sword is never an upgrade
sword2h = parse_item("0.7:20:INVTYPE_2HWEAPON:2:8:20:2:5:1:Big Sword:DP12/ST6/WS3.5")
worn2 = [parse_item(worn[0])]
sk = {"weapons": {0, 4, 7}, "armor": {1, 2, 3}, "ranks": []}
v = g.compare_all(worn2, [sword2h], "WARRIOR", 10, sk)[0]
assert v["verdict"] == "can't use (not trained: Two-Handed Swords)", v
assert not g.evaluate(worn2, [sword2h], "WARRIOR", 10, sk)
sk["weapons"].add(8)
assert g.compare_all(worn2, [sword2h], "WARRIOR", 10, sk)[0]["verdict"].startswith("upgrade")
assert g.compare_all(worn2, [sword2h], "WARRIOR", 10, None)[0]["verdict"].startswith("upgrade")   # unknown -> class table
r = g.rank_rewards(worn2, [sword2h], "WARRIOR", 10, {"weapons": {7}, "armor": None})
assert r[0]["usable"] is False and r[0]["untrained"] == "Two-Handed Swords", r
print("skills OK")
