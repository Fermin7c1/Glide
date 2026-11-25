#!/usr/bin/env python3
"""GameState.parse_item on gear entries (pages W, I, R and D): the fields and the stats, and the worn item's enchant
and random suffix ids that addon 0.22 sends as the stat codes EN and SX (only when non-zero). They leave the stats for
int keys "enchant" and "suffix", which are there only when the entry carried them: an older addon's entry (or a bag
entry) has neither, which means unknown, not "no enchant". A worn list whose only change is an enchant is a new
list (gear_version moves), so whatever is keyed on the worn gear sees it."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "companion"))
from protocol import list_hash  # noqa: E402
from state import GameState  # noqa: E402

parse = GameState.parse_item

# ---- an entry as every addon sent it: no ids, no keys ----------------------------------------------------------------
old = parse("6:19136:INVTYPE_WAIST:4:1:61:4:56:1:Mana Igniting Cord:IN16/SA12/SP25")
assert old == {"key": "6", "id": 19136, "loc": "INVTYPE_WAIST", "cls": 4, "sub": 1, "ilvl": 61, "quality": 4,
               "req": 56, "usable": True, "name": "Mana Igniting Cord",
               "stats": {"IN": 16.0, "SA": 12.0, "SP": 25.0}}, old
assert "enchant" not in old and "suffix" not in old, old
weapon = parse("16:2361:INVTYPE_2HWEAPON:2:5:5:1:1:0:Battleworn Hammer:DP3.5/WS2.9")
assert weapon["stats"] == {"DP": 3.5, "WS": 2.9} and weapon["usable"] is False and "enchant" not in weapon, weapon
bare = parse("11:18103:INVTYPE_FINGER:4:0:30:2:25:1:Band")           # no stats field at all
assert bare["stats"] == {} and "enchant" not in bare and "suffix" not in bare, bare
assert parse("6:19136:INVTYPE_WAIST:4:1:61:4:56:1") is None            # too few fields
assert parse("") is None

# ---- a worn entry with both ids: they leave the stats, as ints -------------------------------------------------------
new = parse("6:19136:INVTYPE_WAIST:4:1:61:4:56:1:Mana Igniting Cord:EN2504/IN16/SA12/SP25/SX13002")
assert new["enchant"] == 2504 and new["suffix"] == 13002, new
assert isinstance(new["enchant"], int) and isinstance(new["suffix"], int), new
assert new["stats"] == {"IN": 16.0, "SA": 12.0, "SP": 25.0}, new
assert {k: v for k, v in new.items() if k not in ("enchant", "suffix")} == old, new
# a suffix as the link had it: a negative id stays negative (simbuild decides what to send)
neg = parse("15:15421:INVTYPE_CLOAK:4:1:35:2:30:1:Shimmering Cloak:AR30/SX-13002")
assert neg["suffix"] == -13002 and "enchant" not in neg and neg["stats"] == {"AR": 30.0}, neg
# only an enchant; only the ids and no other stat (a trimmed entry keeps the ids first)
en = parse("1:18727:INVTYPE_HEAD:4:1:55:3:50:1:Crimson Felt Hat:EN2545/IN9")
assert en["enchant"] == 2545 and "suffix" not in en and en["stats"] == {"IN": 9.0}, en
ids = parse("5:999998:INVTYPE_CHEST:4:1:63:4:60:1:Robe:EN8737/SX13784")
assert ids["enchant"] == 8737 and ids["suffix"] == 13784 and ids["stats"] == {}, ids
# a code without a number is skipped, as any stat would be
odd = parse("5:999998:INVTYPE_CHEST:4:1:63:4:60:1:Robe:EN/IN5")
assert "enchant" not in odd and odd["stats"] == {"IN": 5.0}, odd

# ---- through the strip's W list: the ids arrive on the worn items, and an enchant alone makes a new list --------------
st = GameState()


def send_worn(entries):
    joined = ",".join(entries)
    core = {"t": "1", "wn": "1", "wh": str(list_hash(joined)), "in": "0", "rn": "0"}
    st.update(core, {})
    st.update(core, {"pg": "W0", "g": joined})


worn = ["6:19136:INVTYPE_WAIST:4:1:61:4:56:1:Mana Igniting Cord:IN16/SA12/SP25",
        "16:2361:INVTYPE_2HWEAPON:2:5:5:1:1:1:Battleworn Hammer:DP3.5/WS2.9"]
send_worn(worn)
assert [it["id"] for it in st.gear["W"]] == [19136, 2361], st.gear["W"]
assert all("enchant" not in it for it in st.gear["W"]), st.gear["W"]
v0 = st.gear_version["W"]
send_worn(["6:19136:INVTYPE_WAIST:4:1:61:4:56:1:Mana Igniting Cord:EN2504/IN16/SA12/SP25", worn[1]])
assert st.gear_version["W"] == v0 + 1, (v0, st.gear_version)
cord = st.gear["W"][0]
assert cord["enchant"] == 2504 and "suffix" not in cord and cord["stats"] == {"IN": 16.0, "SA": 12.0, "SP": 25.0}, \
    cord
assert "enchant" not in st.gear["W"][1], st.gear["W"][1]
send_worn(["6:19136:INVTYPE_WAIST:4:1:61:4:56:1:Mana Igniting Cord:EN2504/IN16/SA12/SP25", worn[1]])
assert st.gear_version["W"] == v0 + 1, (v0, st.gear_version)                # the same list: no new version
print("OK")
