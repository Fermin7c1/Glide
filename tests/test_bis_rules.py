#!/usr/bin/env python3
"""The coach and Glide's own best-in-slot ranks (Phase 2): companion/rules.py Rules._bis over the ranks handed to the
gear (gear.Gear.set_bis) and what was told kept by the ranker (bis.BisRanker.told / mark_told, faked here), the coach's
lines without a model (offline_coach), the prompt's gear.next_upgrades (prompt_context) and the bis_slot tool and
spoken question (coach_tools).

- next_upgrade: said once per change of the next upgrade, never again after a restart (the ranker keeps what was
  told), held in combat and while dead, not while the item is worn or in the bags, never for an item the ranks don't
  name as next or one within the sims' noise, held untold while gear talk is muted (/coach gear off).
- enchant_missing: never on an addon older than 0.22.0 (it sends no enchants, so a missing one isn't "none"), not on
  an enchanted item, the largest first, one at a time, once per worn item whichever enchant a later rank names.
- vendor_treasure is a gear callout now: the gear switch mutes it.
- bis_slot answers for a slot word ("my bracers"), and the spoken question gets one line with one number.

Run: python3 tests/test_bis_rules.py   (prints OK)
"""
import os
import sys
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import coach_tools  # noqa: E402
import gear as gear_mod  # noqa: E402
import offline_coach  # noqa: E402
import prompt_context  # noqa: E402
import rules as rules_mod  # noqa: E402
import strip_spec  # noqa: E402
from rules import Rules  # noqa: E402
from state import GameState  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


class FakeRanker:
    """told / mark_told over a dict standing for data/bis.json (it outlives a Rules: an engine restart)."""
    def __init__(self, disk):
        self.disk = disk

    def told(self, key):
        return key in self.disk

    def mark_told(self, key):
        self.disk[key] = 1


NAMES = {226935: "Sorcerer's Crown", 16686: "Magister's Crown", 22403: "Diana's Pearl Necklace",
         18727: "Crimson Felt Hat", 13968: "Eye of the Beast", 999: "Robe of the Void", 2204: "Better Dagger",
         2208: "Bright Orb", 18010: "Old Choker", 22268: "Draconic Infused Emblem"}
SOURCES = {226935: "sold by Deliana, Ironforge", 16686: "drops from Darkmaster Gandling, Scholomance",
           18727: "drops from Kirtonos the Herald, Scholomance", 2204: "sold by Deliana, Ironforge"}
NOTED = []                                  # the items whose source the coach said (Research.note_told)


class FakeResearch:
    def corrections(self):
        return []

    def brief(self, iid):
        return {"id": iid, "name": NAMES.get(iid), "comes_from": SOURCES.get(iid)}

    def note_told(self, iid):
        NOTED.append(iid)
        return True


def charbis(next_id=226935, d=8.1, s=0.3, avail="now", enchants=None, slot="Head"):
    cb = {"tag": "v1.0.4", "gear_key": "k1", "dps": 629.1,
          "slots": {"Head": [{"id": 226935, "sfx": 0, "rank": 1, "d": 8.1, "s": 0.3, "how": "sim", "avail": "now",
                              "req": None},
                             {"id": 16686, "sfx": 0, "rank": 2, "d": 6.0, "s": 0.3, "how": "sim", "avail": "now",
                              "req": None},
                             {"id": 18727, "sfx": 0, "rank": 3, "d": 0.2, "s": 0.3, "how": "sim", "avail": "now",
                              "req": None},
                             {"id": 18000, "sfx": 0, "rank": 4, "d": 0.0, "s": 0.0, "how": "worn", "avail": "now",
                              "req": None}],
                    "Neck": [{"id": 22403, "sfx": 0, "rank": 1, "d": 3.2, "s": 0.4, "how": "sim", "avail": "now",
                              "req": None}],
                    "Chest": [{"id": 999, "sfx": 0, "rank": 1, "d": 9.5, "s": 0.4, "how": "sim", "avail": "now",
                               "req": "Tailoring"}],
                    "Trinket": [{"id": 13968, "sfx": 0, "rank": 1, "d": 4.4, "s": 1.0, "how": "sim", "avail": "later",
                                 "req": None}]},
          "set": {"Head": 226935}, "next": {"id": next_id, "slot": slot, "d": d, "s": s, "avail": avail},
          "told": {}}
    if enchants is not None:
        cb["enchants"] = enchants
    return cb


WORN = ["1:18000:INVTYPE_HEAD:4:1:50:3:45:1:Old Hat:IN10",
        "9:18001:INVTYPE_WRIST:4:1:50:3:45:1:Old Bracers:IN5",
        "10:18002:INVTYPE_HAND:4:1:50:3:45:1:Old Gloves:IN5",
        "11:18003:INVTYPE_FINGER:4:0:50:3:45:1:Old Ring:SP5"]


def worn_items(enchanted=()):
    out = []
    for e in WORN:
        it = GameState.parse_item(e)
        if it["id"] in enchanted:
            it["enchant"] = 1883
        out.append(it)
    return out


clock = [1000000.0]
real_time = rules_mod.time
rules_mod.time = types.SimpleNamespace(time=lambda: clock[0])
saved_tools = (coach_tools.bis, coach_tools.research)
try:
    disk = {}
    gear = gear_mod.Gear({"kind": "Speedrun"})

    def engine():
        """A fresh rules engine (an engine start) on the same gear and ranker file."""
        st, r = GameState(), Rules({"review_minutes": 120})      # (no pace review in between: a quiet frame each)
        r.gear, r.bis, r.research = gear, FakeRanker(disk), FakeResearch()
        return st, r

    st, r = engine()

    def frame(combat=0, dead=0, level=60, gv="0.22.0", cf=None, step=6, enchanted=(), bags=()):
        """One frame `step` seconds on -> the next_upgrade / enchant_missing triggers it raised."""
        clock[0] += step
        core = {"t": str(int(clock[0]) % 1000000), "l": str(level), "x": "1", "X": "100", "c": str(combat),
                "d": str(dead), "gv": gv, "cl": "MAGE"}
        if cf is not None:
            core["cf"] = str(cf)
        st.update(core, {"pg": "B", "nm": "Nine", "cl": "MAGE", "ra": "Gnome", "fa": "Alliance"}, now=clock[0])
        st.gear["W"] = worn_items(enchanted)
        st.gear["I"] = [GameState.parse_item(b) for b in bags]
        return [t for t in r.check(st, []) if t.kind in ("next_upgrade", "enchant_missing")]

    def settle():
        """Frames until the session has started (the first 15) without anything to say."""
        for _ in range(16):
            check(frame(step=0.1) == [], "nothing before the ranks")

    # ---- the gear's ranks: a version that changes with them, not with what was told -----------------------------
    v0 = gear.bis_version
    gear.set_bis("Nine-Skyborne", charbis())
    check(gear.bis_version == v0 + 1 and gear.bis_key == "Nine-Skyborne", gear.bis_version)
    cb = charbis()
    cb["told"] = {"next_upgrade|226935|0": 1}
    gear.set_bis("Nine-Skyborne", cb)
    check(gear.bis_version == v0 + 1 and gear.bis is cb, "what was told is no change")
    check(gear.bis_for("Nine") is cb and gear.bis_for("Ninety") is None and gear.bis_for(None) is None, "name-realm")
    gear.set_bis("Nine", None)
    check(gear.bis is None and gear.bis_version == v0 + 2, "cleared")
    check(gear.bis_on() is True, "on by default")

    # ---- next_upgrade: once per change, held in combat --------------------------------------------------------------
    settle()
    gear.set_bis("Nine-Skyborne", charbis())
    check(frame(combat=1) == [] and frame(dead=1) == [], "held in combat and while dead")
    got = frame()
    check(len(got) == 1 and got[0].kind == "next_upgrade" and got[0].priority == 2, got)
    check(got[0].facts == {"item": "Sorcerer's Crown", "slot": "head", "dps": 8.1,
                           "source": "sold by Deliana, Ironforge"}, got[0].facts)
    check(disk == {"next_upgrade|226935|0": 1}, ("told is kept by the ranker", disk))
    for _ in range(5):
        check(frame() == [], "said once")
    check(offline_coach.callout(got, {"player": {}}) ==
          "Next upgrade: Sorcerer's Crown (head, about +8 DPS): sold by Deliana, Ironforge.", "the offline line")

    # a restart: the ranker's file remembers, nothing is said again
    st, r = engine()
    settle()
    check(frame() == [] and frame() == [], "not again after a restart")

    # a change of the next upgrade is said, once
    gear.set_bis("Nine-Skyborne", charbis(next_id=16686, d=6.0))
    got = frame()
    check([(t.kind, t.facts["item"]) for t in got] == [("next_upgrade", "Magister's Crown")], got)
    check(frame() == [], "once")

    # an item simmed with a random suffix is named with it (the ranker's name), told per item and suffix
    cbx = charbis(next_id=16686, d=6.0)
    cbx["next"].update(sfx=13001, name="Magister's Crown of Sorcery")
    gear.set_bis("Nine-Skyborne", cbx)
    got = frame()
    check([t.facts["item"] for t in got] == ["Magister's Crown of Sorcery"] and "next_upgrade|16686|13001" in disk, got)
    check(gear_mod.next_upgrades(cbx, set(), FakeResearch().brief)[0]["item"] == "Magister's Crown of Sorcery",
          "the prompt names it the same way")
    cbx["slots"]["Head"][1].update(sfx=13001, name="Magister's Crown of Sorcery")
    check([r_["item"] for r_ in gear_mod.bis_rows(cbx, "head", set(), FakeResearch().brief)][:2] ==
          ["Sorcerer's Crown", "Magister's Crown of Sorcery"], "and so does bis_slot")

    # not while it is worn or already in the bags, not within the sims' noise, not for a later or source-less item,
    # not without a name, not below 60
    for cbx, kw, why in ((charbis(next_id=18727, d=0.5, s=0.3), {}, "within 2 sigma"),
                         (charbis(next_id=13968, d=4.4, s=1.0, avail="later"), {}, "a later phase"),
                         (charbis(next_id=22403, d=3.2, s=0.4, avail="nosrc"), {}, "no source"),
                         (charbis(next_id=4242, d=5.0), {}, "no name for it"),
                         (charbis(next_id=18000, d=5.0), {}, "worn already"),
                         (charbis(next_id=22403, d=3.2, s=0.4),
                          {"bags": ["0.1:22403:INVTYPE_NECK:4:0:60:4:55:1:Diana's Pearl Necklace:IN10"]}, "in the bags"),
                         (charbis(next_id=22403, d=3.2, s=0.4), {"level": 59}, "below 60")):
        gear.set_bis("Nine-Skyborne", cbx)
        check(frame(**kw) == [], why)
    check(not any(k.startswith("next_upgrade|22403") for k in disk), "none of those told")

    # gear talk muted in game: held, untold; said once it is back on
    gear.set_bis("Nine-Skyborne", charbis(next_id=22403, d=3.2, s=0.4, slot="Neck"))
    gear_off = strip_spec.ALL_FLAGS_ON & ~next(f.bit for f in strip_spec.COACH_FLAGS if f.name == "gear")
    check(frame(cf=gear_off) == [] and "next_upgrade|22403|0" not in disk, "muted: untold")
    got = frame()
    check([t.facts["item"] for t in got] == ["Diana's Pearl Necklace"] and got[0].facts["slot"] == "neck", got)

    # the gear.bis switch off: nothing
    gear.set_bis("Nine-Skyborne", charbis(next_id=999, d=9.5, s=0.4))
    gear.cfg["bis"] = False
    if "gear.bis" in gear_mod.settings.ENTRIES:
        check(frame() == [], "gear.bis off")
    gear.cfg.pop("bis")

    # ---- enchant_missing: only from addon 0.22.0, never on an enchanted item, the largest first -------------------
    disk.clear()
    disk["next_upgrade|999|0"] = 1
    ench = {"Wrist": {"item": 18001, "id": 1883, "name": "Greater Intellect", "d": 3.1, "s": 0.4},
            "Hands": {"item": 18002, "id": 2934, "name": "Minor Haste", "d": 1.4, "s": 0.4},
            "Feet": {"item": 18009, "id": 911, "name": "Minor Speed", "d": 9.0, "s": 0.4},        # (not worn now)
            "Finger": {"item": 18003, "id": 2928, "name": "Spellpower", "d": 0.5, "s": 0.4}}     # (within the noise)
    gear.set_bis("Nine-Skyborne", charbis(next_id=999, d=9.5, s=0.4, enchants=ench))
    for gv in ("0.21.0", "", "0.9.9"):
        check(frame(gv=gv) == [], ("never on an old addon", gv))
    check(frame(enchanted=(18001, 18002)) == [], "every candidate enchanted already")
    got = frame()
    check(len(got) == 1 and got[0].kind == "enchant_missing" and got[0].priority == 3, got)
    check(got[0].facts == {"slot": "bracers", "item": "Old Bracers", "enchant": "Greater Intellect", "dps": 3.1},
          got[0].facts)
    check(offline_coach.callout(got, {"player": {}}) ==
          "Your bracers have no enchant: Greater Intellect is worth about 3 DPS.", "the offline line")
    check(offline_coach.enchant_missing_line({"slot": "head", "enchant": "Arcanum of Focus", "dps": 1.2}) ==
          "Your head has no enchant: Arcanum of Focus is worth about 1.2 DPS.", "has / have")
    check(frame() == [], "one slot at a time")
    clock[0] += rules_mod.ENCHANT_GAP_S
    got = frame()
    check([t.facts["enchant"] for t in got] == ["Minor Haste"] and got[0].facts["slot"] == "gloves", got)
    clock[0] += rules_mod.ENCHANT_GAP_S
    check(frame() == [], "said once each; the ring's is within the noise, the boots aren't worn")
    check("enchant_missing|18001" in disk and "enchant_missing|18002" in disk, ("told per worn item", disk))
    # a later rank (after an unrelated gear change) names the other of two close enchants for the same bracers: not
    # said again, nor after a restart (regression: the told key held the enchant, so the coach repeated "your bracers
    # have no enchant" with the other one's name)
    ench2 = dict(ench, Wrist={"item": 18001, "id": 2650, "name": "Spell Power", "d": 3.2, "s": 0.4})
    gear.set_bis("Nine-Skyborne", charbis(next_id=999, d=9.5, s=0.4, enchants=ench2))
    clock[0] += rules_mod.ENCHANT_GAP_S
    check(frame() == [], "the same bracers again, for the other enchant")
    st, r = engine()
    settle()
    clock[0] += rules_mod.ENCHANT_GAP_S
    check(frame() == [], "the same bracers again after a restart")
    # a list of enchants, and one with no worn item id: found by the slot it is worn in
    disk.clear()
    disk["next_upgrade|999|0"] = 1
    gear.set_bis("Nine-Skyborne", charbis(next_id=999, d=9.5, s=0.4, enchants=[
        {"slot": "Head", "id": 2544, "name": "Arcanum of Focus", "d": 4.0, "s": 0.5}]))
    clock[0] += rules_mod.ENCHANT_GAP_S
    got = frame()
    check([(t.facts["item"], t.facts["slot"]) for t in got] == [("Old Hat", "head")], got)

    # ---- categories and topics: gear callouts; vendor_treasure now muted by the gear switch -----------------------
    for kind in ("next_upgrade", "enchant_missing", "vendor_treasure"):
        check(rules_mod.CATEGORY[kind] == "gear" and prompt_context.TRIGGER_TOPIC[kind] == "gear", kind)
        check(rules_mod.allowed(kind, {"coach": True}) and not rules_mod.allowed(kind, {"coach": True, "gear": False}),
              ("the gear switch", kind))

    # ---- the prompt: gear.next_upgrades, 3 slots, its own trim step ---------------------------------------------
    cb = charbis()
    worn = worn_items()
    nu = prompt_context.bis_next(cb, worn, FakeResearch().brief)
    check([(u["slot"], u["item"], u["dps"]) for u in nu] ==
          [("head", "Sorcerer's Crown", 8.1), ("neck", "Diana's Pearl Necklace", 3.2)], nu)
    check(nu[0] == {"slot": "head", "item": "Sorcerer's Crown", "dps": 8.1, "plus_minus": 0.6,
                    "source": "sold by Deliana, Ironforge"}, nu[0])
    check(prompt_context.bis_next(None, worn, FakeResearch().brief) == [], "no ranks")
    st2 = GameState()
    st2.update({"l": "60", "cl": "MAGE"}, {"pg": "B", "nm": "Nine", "cl": "MAGE"})
    st2.gear["W"] = worn
    gear.set_bis("Nine-Skyborne", cb)
    got5 = gear.bis_now(st2)
    check(got5[0] is cb and 18000 in got5[1], got5)

    class Geo:
        def describe(self, *a, **k):
            return {}

        def area_at(self, *a):
            return None, None

    class Know:
        def quest(self, *a, **k):
            return {}

    s = st2.summary()
    ctx = prompt_context.game_context(s, None, Know(), Geo(),
                                      gear_fn=lambda: ([], [], worn, [], got5), item_brief=FakeResearch().brief)
    check(ctx["gear"]["next_upgrades"] == nu, ctx["gear"].get("next_upgrades"))
    ctx4 = prompt_context.game_context(s, None, Know(), Geo(), gear_fn=lambda: ([], [], worn, []))
    check("next_upgrades" not in ctx4["gear"], "four getters: as before")
    ref = {"gear": {"next_upgrades": list(nu) + [dict(nu[0], slot="neck")]}, "community": []}
    steps = prompt_context.reference_trims(ref)
    for step in steps:
        while step(ref):
            pass
    check(len(ref["gear"]["next_upgrades"]) == 1, "trimmed down to one")

    # ---- bis_slot: the tool and the spoken question -------------------------------------------------------------
    coach_tools.research = FakeResearch()
    coach_tools.bis = None
    check([d["name"] for d in coach_tools.item_tools()] == ["item_info"], "no ranker: no bis_slot")
    check(coach_tools.run_item_tool("bis_slot", {"slot": "head"}).get("error"), "nothing ranked")
    coach_tools.bis = lambda: gear.bis_now(st2)
    check([d["name"] for d in coach_tools.item_tools()] == ["item_info", "bis_slot"], "offered with the ranker")
    check("bis_slot" in coach_tools.LOCAL_TOOLS and "bis_slot" in coach_tools.ITEM_TOOL_NAMES, "a local tool")
    rows = coach_tools.bis_slot("my helmet")
    check([(x["item"], x["rank"]) for x in rows] == [("Sorcerer's Crown", 1), ("Magister's Crown", 2),
                                                    ("Crimson Felt Hat", 3), (None, 4)], rows)
    check(rows[0] == {"item": "Sorcerer's Crown", "rank": 1, "d": 8.1, "source": "sold by Deliana, Ironforge",
                      "avail": "now"}, rows[0])
    check(rows[2].get("about_same") is True and rows[3].get("worn") is True, rows)
    check(coach_tools.bis_slot("chest")[0]["needs"] == "Tailoring", "a profession it needs")
    got = coach_tools.run_item_tool("bis_slot", {"slot": "Neck"})
    check(got["slot"] == "neck" and got["ranks"][0]["item"] == "Diana's Pearl Necklace", got)
    check(coach_tools.run_item_tool("bis_slot", {"slot": "pocket"}).get("error") and
          coach_tools.run_item_tool("bis_slot", {}).get("error"), "bad slots")
    check(coach_tools.run_item_tool("bis_slot", {"slot": "wrists"}) == {"slot": "bracers", "ranks": []}, "unranked")

    for q, want in (("What's best in slot for my neck?", "neck"), ("whats my best trinket", "trinket"),
                    ("bis bracers", "wrists"), ("neck bis", "neck"), ("what are my best boots", "feet"),
                    ("best in slot for my main hand", "main hand")):
        check(coach_tools.bis_question(q) == want, (q, coach_tools.bis_question(q)))
    for q in ("what is the best way to level", "What's the best place to buy a staff?", "where does it drop",
              "best", ""):
        check(coach_tools.bis_question(q) is None, (q, coach_tools.bis_question(q)))
    check(coach_tools.item_command("What's best in slot for my head?", True) is None, "the AI answers")
    check(coach_tools.item_command("What's best in slot for my head?", False) ==
          "Best for your head: Sorcerer's Crown, about +8 DPS: sold by Deliana, Ironforge.", "offline answer")
    check(coach_tools.item_command("best neck?", False) ==
          "Best for your neck: Diana's Pearl Necklace, about +3 DPS.", "no source known")
    check(coach_tools.item_command("what's my best trinket", False) ==
          "I have nothing ranked for your trinket yet.", "a later phase isn't named")
    cb_worn = charbis()
    cb_worn["slots"]["Head"][0]["id"] = 18000
    NAMES[18000] = "Old Hat"
    coach_tools.bis = lambda: (cb_worn, [18000])
    check(coach_tools.item_command("bis head", False) == "Best for your head: Old Hat, and you wear it.", "worn")
    # a set piece best only with another piece of its set (alone it loses): "with its set", never "about the same"
    cb_set = charbis()
    cb_set["slots"]["Head"][0].update(d=-10.5, s=0.7, together=1)
    coach_tools.bis = lambda: (cb_set, [])
    check(coach_tools.bis_slot("head")[0].get("with_set") is True and "about_same" not in coach_tools.bis_slot(
        "head")[0], coach_tools.bis_slot("head")[0])
    check(coach_tools.item_command("bis head", False) ==
          "Best for your head: Sorcerer's Crown, with its set: sold by Deliana, Ironforge.", "with its set")
    coach_tools.bis = lambda: (None, [])
    check(coach_tools.item_command("bis head", False) ==
          "I haven't ranked your gear yet; that takes level 60 and WoWSims.", "not ranked yet")

    # ---- unranked rows (no source yet, a later phase: rank 0) come after the ranked ones (regression: they sorted
    # first, so six of them pushed every ranked row out and the answer was "nothing ranked") -------------------------
    cb_neck = charbis()
    cb_neck["slots"]["Neck"] = ([{"id": 18010, "sfx": 0, "rank": 1, "d": 0.0, "s": 0.0, "how": "worn",
                                  "avail": "now", "req": None}]
                                + [{"id": 30000 + i, "sfx": 0, "rank": 0, "d": 9.0 - i, "s": 0.3, "how": "sim",
                                    "avail": "nosrc" if i % 2 else "later", "req": None} for i in range(6)]
                                + [{"id": 22403, "sfx": 0, "rank": 2, "d": 3.2, "s": 0.4, "how": "sim",
                                    "avail": "now", "req": None}])
    coach_tools.bis = lambda: (cb_neck, [18010])
    rows = coach_tools.bis_slot("neck")
    check([x["rank"] for x in rows] == [1, 2, 0, 0, 0, 0] and rows[1]["item"] == "Diana's Pearl Necklace", rows)
    check(coach_tools.item_command("best neck?", False) == "Best for your neck: Old Choker, and you wear it.",
          "the ranked rows answer")

    # ---- an item already in the bags: never something to go and get (regression: its drop source was the advice) ---
    cb_bag = charbis()
    cb_bag["slots"]["Neck"][0].update(bag=1)
    check(not any(u["slot"] == "neck" for u in gear_mod.next_upgrades(cb_bag, set(), FakeResearch().brief)),
          "a bag item in gear.next_upgrades")
    coach_tools.bis = lambda: (cb_bag, [])
    check(coach_tools.bis_slot("neck")[0].get("in_bags") is True, coach_tools.bis_slot("neck"))
    check(coach_tools.item_command("best neck?", False) ==
          "Best for your neck: Diana's Pearl Necklace, about +3 DPS, and it's in your bags.", "in the bags")

    # ---- a one-hander and an off hand in place of a two-hander: the pair named, its gain once; a bonus the sims
    # leave out (cond) said with the number ---------------------------------------------------------------------------
    cb_pair = charbis(next_id=2204, d=32.0, s=0.5, slot="Main Hand")
    cb_pair["next"].update({"with": 2208, "with_name": "Bright Orb"})
    cb_pair["slots"]["Main Hand"] = [{"id": 2204, "sfx": 0, "rank": 1, "d": 32.0, "s": 0.5, "how": "sim",
                                      "avail": "now", "req": None, "with": 2208, "with_name": "Bright Orb"}]
    cb_pair["slots"]["Off Hand"] = [{"id": 2208, "sfx": 0, "rank": 1, "d": 32.0, "s": 0.5, "how": "sim",
                                     "avail": "now", "req": None, "with": 2204, "with_name": "Better Dagger"}]
    cb_pair["slots"]["Trinket"] = [{"id": 22268, "sfx": 0, "rank": 1, "d": 5.0, "s": 0.4, "how": "sim",
                                    "avail": "now", "req": None, "cond": 1}]
    nu = gear_mod.next_upgrades(cb_pair, set(), FakeResearch().brief, n=5)
    check([(u["slot"], u["item"], u.get("with")) for u in nu if u["slot"] in ("main hand", "off hand")] ==
          [("main hand", "Better Dagger", "Bright Orb")], ("the pair once", nu))
    check(any(u["item"] == "Draconic Infused Emblem" and u.get("cond") is True for u in nu), nu)
    coach_tools.bis = lambda: (cb_pair, [])
    check(coach_tools.bis_slot("trinket")[0].get("cond") is True and coach_tools.bis_slot("off hand")[0]["with"]
          == "Better Dagger", "cond and with in bis_slot")
    del NOTED[:]
    check(coach_tools.item_command("bis main hand", False) ==
          "Best for your main hand: Better Dagger with Bright Orb, about +32 DPS: sold by Deliana, Ironforge.",
          coach_tools.item_command("bis main hand", False))
    check(NOTED and NOTED[-1] == 2204, ("the source said is told to the research", NOTED))
    check(coach_tools.item_command("best trinket", False) == "Best for your trinket: Draconic Infused Emblem, "
          "about +5 DPS, not counting its bonus against some creatures.", "cond offline")
    disk.clear()
    gear.set_bis("Nine", None)
    st, r = engine()
    settle()
    gear.set_bis("Nine-Skyborne", cb_pair)
    del NOTED[:]
    got = frame()
    check(len(got) == 1 and got[0].facts == {"item": "Better Dagger", "slot": "main hand", "dps": 32.0,
                                             "source": "sold by Deliana, Ironforge", "with": "Bright Orb"},
          got and got[0].facts)
    check(NOTED == [2204], ("next_upgrade's source is told to the research (a web one is corrected later)", NOTED))
    check(offline_coach.callout(got, {"player": {}}) ==
          "Next upgrade: Better Dagger with Bright Orb (main hand, about +32 DPS): sold by Deliana, Ironforge.",
          offline_coach.callout(got, {"player": {}}))
    check(offline_coach.next_upgrade_line({"item": "Draconic Infused Emblem", "slot": "trinket", "dps": 5.0,
                                           "cond": True}) ==
          "Next upgrade: Draconic Infused Emblem (trinket, about +5 DPS, not counting its bonus against some "
          "creatures).", "cond in the line")

    # ---- a key told before a restart is remembered: the ranker isn't asked again every poll (regression: the capture
    # thread waited on the ranker's lock every 5 s for every key told in an earlier session) ---------------------------
    class CountingRanker(FakeRanker):
        def __init__(self, disk_):
            super().__init__(disk_)
            self.asked = []

        def told(self, key):
            self.asked.append(key)
            return super().told(key)
    st, r = engine()
    r.bis = counting = CountingRanker(disk)
    settle()
    for _ in range(4):
        check(frame() == [], "told before the restart")
    check(counting.asked.count("next_upgrade|2204|0") == 1, ("asked once", counting.asked))

    # ---- another random suffix of a worn item is the next upgrade (the addon from 0.22 sends worn suffixes: none
    # sent is none); with an older addon, the worn item may be it --------------------------------------------------
    disk.clear()
    cb_sfx = charbis(next_id=18000, d=5.0)                  # (18000: the worn Old Hat, sent without SX)
    cb_sfx["next"].update(sfx=13001, name="Old Hat of Sorcery")
    gear.set_bis("Nine", None)
    st, r = engine()
    settle()
    gear.set_bis("Nine-Skyborne", cb_sfx)
    check(frame(gv="0.21.0") == [], "an older addon: the worn suffix is unknown")
    got = frame()
    check([t.facts["item"] for t in got] == ["Old Hat of Sorcery"], ("another suffix of the worn hat", got))
    disk.clear()
    gear.set_bis("Nine", None)
    st, r = engine()
    settle()
    gear.set_bis("Nine-Skyborne", cb_sfx)
    WORN[0] = "1:18000:INVTYPE_HEAD:4:1:50:3:45:1:Old Hat:IN10/SX-13001"
    try:
        check(frame() == [], "the hat worn with that suffix (sign aside)")
    finally:
        WORN[0] = "1:18000:INVTYPE_HEAD:4:1:50:3:45:1:Old Hat:IN10"
finally:
    rules_mod.time = real_time
    coach_tools.bis, coach_tools.research = saved_tools
print("OK")
