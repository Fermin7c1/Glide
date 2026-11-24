#!/usr/bin/env python3
"""Where an item comes from, in the player's words (companion/item_text.py): one case per kind of source (a boss, a
chest, trash, a world drop, a quest with the chain the knowledge base knows, a vendor, an honor vendor, a vendor that
needs a reputation, a craft, PvP), the one line a tooltip shows when an item has several (the easiest to act on, a sure
source before a web one, the bosses that share a drop), the line for an item nobody knows a source for (the auction
house when it binds when equipped, else nothing), and the spec's own lines from WoWSims' records
(tests/fixtures/wowsims_db_small.json). No text has an em dash or the "Glide: " the addon puts in front.

Run: python3 tests/test_item_text.py   (prints OK)
"""
import json
import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import item_db  # noqa: E402
import item_text  # noqa: E402
from item_db import src  # noqa: E402

said = []


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


def text(s, kb=None):
    out = item_text.source_text(s, kb)
    said.append(out)
    return out


def line(srcs, kb=None, bond=None):
    out = item_text.line(srcs, kb, bond)
    said.append(out or "")
    return out


class FakeStore:
    """The knowledge base's quest_chain (store.KB.q)."""
    def __init__(self, chains, fail=False):
        self.chains, self.fail, self.queries = chains, fail, 0

    def q(self, sql, *args):
        self.queries += 1
        if self.fail:
            raise RuntimeError("no such table: quest_chain")
        assert "quest_chain" in sql, sql
        return [{"quest_id": q, "n": n} for q, n in self.chains.items()]


class FakeData:
    """KBData: quest givers, map names, creatures."""
    GIVERS = {5102: {"end": {"kind": "npc", "entity_id": 9560, "name": "Marshal Maxwell", "ui_map": 1428}},
              4363: {"end": {"kind": "npc", "entity_id": 2784, "name": "King Magni Bronzebeard", "ui_map": 1455}},
              77: {"end": {"kind": "npc", "entity_id": 99, "name": None, "ui_map": None}},
              78: {"end": {"kind": "item", "entity_id": 12780, "name": None, "ui_map": None}}}

    def giver(self, qid, role):
        return self.GIVERS.get(qid, {}).get(role)

    def map_name(self, ui_map):
        return {1428: "Burning Steppes", 1455: "Ironforge"}.get(ui_map)

    def creature(self, cid):
        return {99: {"name": "Old Man Heming"}, 2172: {"name": "Strider Clutchmother"}}.get(cid)


class FakePack:
    """api.KnowledgePack, as item_text reads it."""
    def __init__(self, ok=True, fail=False):
        self.ok, self.kb, self.data = ok, FakeStore({5089: 1, 5102: 2, 4363: 7, 77: 1}, fail), FakeData()


pack = FakePack()

# ---- one case per kind ---------------------------------------------------------------------------------------------
gandling = src("drop", npc=1853, npc_name="Darkmaster Gandling", zone=2057, zone_name="Scholomance",
               instance="Scholomance", difficulty=1)
check(text(gandling) == "drops from Darkmaster Gandling, Scholomance", said[-1])
drakk = src("drop", npc=10363, npc_name="General Drakkisath", zone=1583, zone_name="Blackrock Spire",
            instance="Upper Blackrock Spire", difficulty=1)
check(text(drakk) == "drops from General Drakkisath, Upper Blackrock Spire", said[-1])
outdoors = src("drop", npc=2172, zone=148, zone_name="Darkshore", conf=0.95, src="obs")
check(text(outdoors, pack) == "drops from Strider Clutchmother, Darkshore", "an NPC's name from the knowledge base")
check(text(outdoors) == "drops in Darkshore", "no name anywhere: the place")
check(text(src("drop")) == "drops from enemies", "nothing known but a drop")
check(text(src("drop", npc_name="Sothos and Jarien's Heirlooms", zone=2017, zone_name="Stratholme",
                instance="Stratholme", difficulty=1)) == "from Sothos and Jarien's Heirlooms, Stratholme", "a chest")
check(text(src("trash", zone=2017, zone_name="Stratholme", instance="Stratholme", difficulty=1))
      == "drops from trash in Stratholme", said[-1])
check(text(src("trash")) == "drops from trash", said[-1])
check(text(src("world")) == "world drop", said[-1])

demise = src("quest", quest=5102, quest_name="General Drakkisath's Demise")
check(text(demise, pack) == "reward of General Drakkisath's Demise (2-quest chain, ends at Marshal Maxwell, "
                            "Burning Steppes)", said[-1])
check(text(demise) == "reward of General Drakkisath's Demise", "no knowledge base: the quest alone")
check(text(src("quest", quest=4363, quest_name="The Princess's Surprise"), pack)
      == "reward of The Princess's Surprise (7-quest chain, ends at King Magni Bronzebeard, Ironforge)", said[-1])
check(text(src("quest", quest=77, quest_name="A Lone Errand"), pack) == "reward of A Lone Errand (ends at Old Man "
                                                                         "Heming)", "one quest; no map: no place")
check(text(src("quest", quest=78, quest_name="A Note"), pack) == "reward of A Note", "ends at an item: not said")
check(text(src("quest", quest=5, quest_name="Unknown Task"), pack) == "reward of Unknown Task", "kb doesn't know it")
check(text(src("quest", quest=5102), pack).startswith("a quest reward (2-quest chain"), said[-1])
check(text(demise, FakePack(ok=False)) == "reward of General Drakkisath's Demise", "a pack that isn't ready")
check(text(demise, FakePack(fail=True)) == "reward of General Drakkisath's Demise", "a kb without quest_chain")
check(pack.kb.queries == 1, "the chains are read once per pack: %d" % pack.kb.queries)

check(text(src("vendor", npc=230319, npc_name="Deliana", zone=1537, zone_name="Ironforge"))
      == "sold by Deliana, Ironforge", said[-1])
check(text(src("vendor", npc=12785, npc_name="Sergeant Major Clate", zone=2918, zone_name="PvP vendor"))
      == "sold by Sergeant Major Clate, a PvP vendor", said[-1])
check(text(src("vendor", zone=2917)) == "sold by a PvP vendor", said[-1])
check(text(src("vendor", zone=1537, zone_name="Ironforge")) == "sold by a vendor in Ironforge", said[-1])
check(text(src("rep", npc=15127, npc_name="Samuel Hawke", zone=45, zone_name="Arathi Highlands",
               rep_faction="The League of Arathor", rep_level="Honored"))
      == "sold by Samuel Hawke; needs Honored with the League of Arathor", said[-1])
check(text(src("rep", npc_name="Meilosh", rep_faction="Timbermaw Hold", rep_level="Revered"))
      == "sold by Meilosh; needs Revered with Timbermaw Hold", said[-1])
check(text(src("rep", npc_name="Meilosh", rep_faction="Timbermaw Hold")) ==
      "sold by Meilosh; needs reputation with Timbermaw Hold", said[-1])
# (was "a reward at Neutral with Brood of Nozdormu": a standing read as a place)
check(text(src("rep", rep_faction="The Defilers", rep_level="Honored")) ==
      "a reputation reward; needs Honored with the Defilers", said[-1])
check(text(src("rep", rep_faction="Brood of Nozdormu", rep_level="Neutral")) ==
      "a reputation reward; needs Neutral with Brood of Nozdormu", said[-1])
check(text(src("rep", rep_faction="The Defilers")) == "a reputation reward from the Defilers", said[-1])
check(text(src("rep")) == "a reputation reward", said[-1])
check(text(src("drop", npc=2172, npc_name="Strider Clutchmother")) == "drops from Strider Clutchmother", said[-1])
check(text(src("drop", zone=3, zone_name="Zephras Isle")) == "drops in Zephras Isle", said[-1])
check(text(src("vendor", npc_name="Deliana")) == "sold by Deliana" and text(src("vendor")) == "sold by a vendor",
      said[-2:])
check(text(src("rep", npc_name="Samuel Hawke")) == "sold by Samuel Hawke", said[-1])
check(text(src("crafted", profession="Tailoring")) == "made with Tailoring", said[-1])
check(text(src("crafted")) == "crafted", said[-1])
check(text(src("pvp")) == "PvP reward", said[-1])
check(text(src("mystery")) == "", "a kind it doesn't know: nothing")

# ---- the one line: no source, the best of several, bosses that share a drop -------------------------------------------
check(line([], bond=item_db.BOND_EQUIP) == "no known source; binds when equipped, so the auction house may have it",
      said[-1])
check(line([]) is None and line([], bond=1) is None and line(None) is None, "no source, not BoE: silent")
check(line([src("mystery")], bond=item_db.BOND_EQUIP) == item_text.NO_SOURCE_BOE, "only kinds it can't say")
deliana = src("vendor", npc=230319, npc_name="Deliana", zone=1537, zone_name="Ironforge")
check(line([gandling, deliana]) == "sold by Deliana, Ironforge", "a vendor before a boss")
check(line([src("pvp"), demise], pack).startswith("reward of General Drakkisath's Demise (2-quest"), "quest over PvP")
web = src("vendor", npc_name="Someone", zone_name="Booty Bay", conf=0.3, src="web")
check(line([web, gandling]) == "drops from Darkmaster Gandling, Scholomance", "WoWSims before the web")
check(line([web]) == "sold by Someone, Booty Bay", "a web fact alone (the addon marks it)")
check(item_text.best([web, gandling]) is gandling and item_text.best([]) is None, "best")
seen = src("drop", npc=1853, npc_name="Darkmaster Gandling", zone=2057, instance="Scholomance", conf=0.95, src="obs")
check(line([gandling, seen]) == "drops from Darkmaster Gandling, Scholomance", "the same boss twice: once")
mc = [src("drop", npc=n, npc_name=name, zone=2717, zone_name="Molten Core", instance="Molten Core", difficulty=2)
      for n, name in ((11982, "Magmadar"), (12057, "Garr"), (12056, "Baron Geddon"), (11988, "Golemagg"))]
check(line(mc) == "drops from Magmadar and 3 others, Molten Core", said[-1])
check(line(mc[:2]) == "drops from Magmadar or Garr, Molten Core", said[-1])
check(line([gandling, drakk]) == "drops from Darkmaster Gandling, Scholomance", "another place: not merged")
check(line([dict(mc[0], conf=0.3, src="web"), gandling]) == "drops from Darkmaster Gandling, Scholomance",
      "a web drop isn't merged into a sure one")

# ---- the spec's lines, from WoWSims' records ----------------------------------------------------------------------------
with open(os.path.join(ROOT, "tests", "fixtures", "wowsims_db_small.json"), encoding="utf-8") as fh:
    fx = json.load(fh)
db = item_db.ItemDB.load(json.dumps(fx["db"]).encode(), json.dumps(fx["leftover"]).encode(), "v1.0.4")
for item_id, faction, bond, want in (
        (16686, "Alliance", None, "drops from Darkmaster Gandling, Scholomance"),
        (22268, "Horde", None, "drops from General Drakkisath, Upper Blackrock Spire"),
        (13968, "Alliance", None, "reward of General Drakkisath's Demise (2-quest chain, ends at Marshal Maxwell, "
                                  "Burning Steppes)"),
        (226935, "Alliance", None, "sold by Deliana, Ironforge"),
        (226935, "Horde", None, "sold by Mokvar, Orgrimmar"),
        (20041, "Alliance", None, "sold by Samuel Hawke; needs Honored with the League of Arathor"),
        (14152, "Alliance", None, "made with Tailoring"),
        (16337, "Alliance", None, "PvP reward"),
        (16437, "Alliance", None, "sold by Sergeant Major Clate, a PvP vendor"),
        (14558, "Alliance", None, "world drop"),
        (1486, "Alliance", None, "drops from trash in Blackfathom Deeps"),
        (18820, "Alliance", None, "drops from Magmadar and 3 others, Molten Core"),
        (647, "Alliance", item_db.BOND_EQUIP, "no known source; binds when equipped, so the auction house may have it"),
        (647, "Alliance", None, None),
        (12543, "Horde", None, None)):
    got = line(db.sources(item_id, faction), pack, bond)
    check(got == want, (item_id, faction, got))

for s in said:
    check("—" not in s and "–" not in s and not s.startswith("Glide"), "plain text: %r" % s)
print("OK")
