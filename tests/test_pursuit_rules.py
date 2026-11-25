#!/usr/bin/env python3
"""The coach and the next move toward the next best-in-slot piece (Phase 4): companion/rules.py Rules._pursuit over a
planner's plan (pursuit.Planner, faked here: plan, told / mark_told kept in a dict that outlives an engine restart,
pursue, skip, runs), the coach's lines without a model (offline_coach), the reminders it places (reminders.py, source
"pursuit"), the prompt's gear.next_move (prompt_context) and the bis_plan / pursuit_set tools and spoken requests
(coach_tools).

- next_move: said once per plan change, never again after a restart, held in combat and while dead, below 60 nothing,
  held untold while gear talk is muted; Phase 2's next_upgrade isn't said while the plan has a move.
- zoning into the move's dungeon: what drops there, once.
- pursuit_roll: per kill of a boss that drops a plan item, after the loot, with the run count and the range most runs
  take; one kill per boss corpse; "bad luck, not a bad plan" past the range.
- pursuit_got: the item lands, said with the next move once the plan moved on; gear_upgrade doesn't say it.
- the move's vendor and auction house steps are reminders, replaced when the plan changes; the player's own stay.
- bis_plan, pursuit_set (a goal note in memory), "what's my next move?", "chase the robe", "skip that", "how's the
  stylus hunt?".

Run: python3 tests/test_pursuit_rules.py   (prints OK)
"""
import os
import sys
import types

for _k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
           "TAVILY_API_KEY", "BRAVE_API_KEY"):
    os.environ[_k] = ""
ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import coach_tools  # noqa: E402
import gear as gear_mod  # noqa: E402
import memory as memory_mod  # noqa: E402
import offline_coach  # noqa: E402
import prompt_context  # noqa: E402
import pursuit as pursuit_mod  # noqa: E402
import reminder_ticker  # noqa: E402
import reminders as reminders_mod  # noqa: E402
import rules as rules_mod  # noqa: E402
import strip_spec  # noqa: E402
from rules import Rules, Trigger  # noqa: E402
from state import GameState  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


STYLUS, CROWN, SORC, ROBE, MANTLE, AHRING = 13938, 16686, 226935, 14152, 14112, 18500
GANDLING, DELIANA, KIRTONOS = 1853, 226001, 10506
NAMES = {STYLUS: "Bonecreeper Stylus", CROWN: "Magister's Crown", SORC: "Sorcerer's Crown",
         ROBE: "Robe of the Archmage", MANTLE: "Felcloth Shoulders", AHRING: "Band of the Ogre King"}
SRCS = {STYLUS: [{"kind": "drop", "npc": GANDLING, "npc_name": "Darkmaster Gandling", "zone": 2057,
                  "zone_name": "Scholomance", "instance": "Scholomance"}],
        CROWN: [{"kind": "drop", "npc": GANDLING, "npc_name": "Darkmaster Gandling", "zone": 2057,
                 "zone_name": "Scholomance", "instance": "Scholomance"}],
        MANTLE: [{"kind": "drop", "npc": KIRTONOS, "npc_name": "Kirtonos the Herald", "zone": 2057,
                  "zone_name": "Scholomance", "instance": "Scholomance"}],
        SORC: [{"kind": "vendor", "npc": DELIANA, "npc_name": "Deliana", "zone": 1537, "zone_name": "Ironforge"}],
        ROBE: [{"kind": "crafted", "profession": "Tailoring"}],
        AHRING: []}
NOTED = []


class FakeResearch:
    def corrections(self):
        return []

    def brief(self, iid):
        return {"id": iid, "name": NAMES.get(iid), "comes_from": None}

    def facts(self, iid):
        return {"s": SRCS.get(iid, [])} if iid in SRCS else None

    def note_told(self, iid):
        NOTED.append(iid)
        return True

    def _find(self, q):
        hit = [i for i, n in NAMES.items() if n.lower() == str(q).lower()]
        return hit[0] if hit else None


ODDS = "about 1 in 8, usually 3 to 10 runs; that's classic's chance"
SCHOLO = {"act": "inst:2057", "items": [STYLUS], "value": 0.7, "hours": 0.9, "odds": ODDS,
          "why": "the stylus is your biggest gain for an hour's play", "where": {"map": 1422, "x": 0.69, "y": 0.73}}
VENDOR = {"act": "vendor:%d" % DELIANA, "items": [SORC], "value": 2.0, "hours": 0.3,
          "odds": "I don't know its price yet", "why": "a sure thing, close by", "where": None}
CRAFT = {"act": "craft:%d" % ROBE, "items": [ROBE], "value": 0.4, "hours": 2.0, "odds": None,
         "why": "ask a Tailoring crafter", "where": None}
AH = {"act": "ah:%d" % AHRING, "items": [AHRING], "value": 0.5, "hours": 0.5, "odds": None, "why": "", "where": None}
RUN2 = {"act": "inst:2057", "items": [CROWN, MANTLE], "value": 0.6, "hours": 1.0,
        "odds": "about 1 in 5, usually 2 to 6 runs; that's classic's chance", "why": "two pieces in one run"}


class FakePlanner:
    """plan / told / mark_told / pursue / skip / runs; `disk` (told) outlives a Rules (an engine restart)."""
    def __init__(self, disk):
        self.disk, self.cur, self.calls, self.counted = disk, None, [], None

    def plan(self, char, fresh=False):
        self.calls.append(("plan", char))
        if fresh:
            self.calls.append(("fresh", char))
        return self.cur

    def told(self, key):
        return key in self.disk

    def mark_told(self, key):
        self.disk[key] = 1

    def pursue(self, char, iid):
        self.calls.append(("pursue", char, iid))
        if iid == ROBE:
            self.cur = {"goal": ROBE, "moves": [CRAFT, SCHOLO], "alts": [VENDOR]}

    def skip(self, char, iid):
        self.calls.append(("skip", char, iid))
        if iid == STYLUS:
            self.cur = {"goal": None, "moves": [VENDOR], "alts": [CRAFT]}
        elif self.cur and self.cur.get("goal") == iid:
            self.cur = dict(self.cur, goal=None, pursued=False)     # (the player's goal skipped: dropped)

    def owned(self, char):
        return list(self.owned_ids)

    owned_ids: list = []


def plan(*moves, alts=(), goal=None):
    return {"goal": goal, "moves": list(moves), "alts": list(alts)}


WORN = ["1:18000:INVTYPE_HEAD:4:1:50:3:45:1:Old Hat:IN10",
        "18:18004:INVTYPE_RANGEDRIGHT:4:1:50:3:45:1:Old Wand:IN3"]

clock = [1000000.0]
real_time = rules_mod.time
rules_mod.time = types.SimpleNamespace(time=lambda: clock[0])
saved = (coach_tools.research, coach_tools.bis, coach_tools.pursuit, coach_tools.pursuit_char,
         coach_tools.goal_memory)
try:
    disk = {}
    gear = gear_mod.Gear({"kind": "Speedrun"})
    planner = FakePlanner(disk)
    rem = reminders_mod.Reminders(None)
    own = rem.add("repair", None, now=0, durability=30)            # the player's own: never touched

    def engine():
        """A fresh rules engine (an engine start) on the same gear, planner file and reminders."""
        st, r = GameState(), Rules({"review_minutes": 120})
        r.gear, r.research, r.pursuit, r.reminders = gear, FakeResearch(), planner, rem
        return st, r

    st, r = engine()

    def frame(combat=0, dead=0, level=60, cf=None, step=16, bags=(), zone="Ironforge", target=None, kinds=None,
              name="Nine"):
        """One frame `step` seconds on -> the pursuit triggers (and gear_upgrade) it raised."""
        clock[0] += step
        core = {"t": str(int(clock[0]) % 1000000), "l": str(level), "x": "1", "X": "100", "c": str(combat),
                "d": str(dead), "gv": "0.24.0", "cl": "MAGE"}
        if cf is not None:
            core["cf"] = str(cf)
        if target is not None:
            core.update(tg=str(target[0]), td="1" if target[1] else "0")
        st.update(core, {"pg": "B", "nm": name, "cl": "MAGE", "ra": "Gnome", "fa": "Alliance"}, now=clock[0])
        st.pages["Z"] = {"zn": zone}
        st.gear["W"] = [GameState.parse_item(e) for e in WORN]
        new = [GameState.parse_item(b) for b in bags]
        if new != st.gear["I"]:
            st.gear["I"] = new
            st.gear_version["I"] += 1                     # (as a new page I does)
        want = kinds or ("next_move", "pursuit_roll", "pursuit_got", "gear_upgrade", "next_upgrade")
        return [t for t in r.check(st, []) if t.kind in want]

    def settle():
        for _ in range(16):
            check(frame(step=0.1) == [], "nothing before a plan")

    settle()

    # ---- next_move: once per plan change; held in combat; never again after a restart ----------------------------
    planner.cur = plan(SCHOLO, alts=[VENDOR, CRAFT])
    check(frame(combat=1) == [] and frame(dead=1) == [], "held in combat and while dead")
    got = frame()
    check([t.kind for t in got] == ["next_move"] and got[0].priority == 2, got)
    check(got[0].facts == {"text": "Scholomance. The Bonecreeper Stylus drops from Darkmaster Gandling",
                           "odds": ODDS, "why": SCHOLO["why"]}, got[0].facts)
    check(offline_coach.callout(got, {"player": {}}) ==
          "Next: Scholomance. The Bonecreeper Stylus drops from Darkmaster Gandling, about 1 in 8, usually 3 to 10 "
          "runs; that's classic's chance.", offline_coach.callout(got, {"player": {}}))
    check(planner.calls[-1] == ("plan", "Nine"), planner.calls[-1])
    check(NOTED == [STYLUS], ("a web source said is kept as told", NOTED))
    for _ in range(5):
        check(frame() == [], "said once")
    st, r = engine()
    settle()
    check(frame() == [] and frame() == [], "not again after a restart")

    # a plan change is said once: a vendor with no price yet
    planner.cur = plan(VENDOR, alts=[SCHOLO])
    got = frame()
    check([t.facts["text"] for t in got] == ["Deliana in Ironforge sells the Sorcerer's Crown"], got)
    check(offline_coach.next_move_line(got[0].facts) ==
          "Next: Deliana in Ironforge sells the Sorcerer's Crown; I don't know its price yet.", "the vendor line")
    check(frame() == [], "once")

    # below 60, nothing; muted gear talk: held untold, said once it is back on
    planner.cur = plan(CRAFT)
    check(frame(level=59) == [], "below 60")
    gear_off = strip_spec.ALL_FLAGS_ON & ~next(f.bit for f in strip_spec.COACH_FLAGS if f.name == "gear")
    check(frame(cf=gear_off) == [] and not any("craft" in k for k in disk), "muted: untold")
    got = frame()
    check([t.facts["text"] for t in got] == ["the Robe of the Archmage, made with Tailoring"], got)

    # every kind of move in words
    for m, want in ((AH, ("the auction house for the Band of the Ogre King", None)),
                    # (a run that rolls more: its lead item, whose odds they are, then the others)
                    (RUN2, ("Scholomance. The Magister's Crown drops from Darkmaster Gandling", "the Felcloth Shoulders")),
                    (dict(RUN2, items=[MANTLE, CROWN, STYLUS, SORC]),
                     ("Scholomance. The Felcloth Shoulders drop from Kirtonos the Herald",
                      "the Magister's Crown, the Bonecreeper Stylus and more")),
                    ({"act": "quest:5384", "items": [ROBE]}, ("a quest rewards the Robe of the Archmage", None)),
                    ({"act": "quest:5384", "items": [ROBE], "npc_name": "Marshal Maxwell", "place": "Burning Steppes"},
                     ("a quest rewards the Robe of the Archmage; it starts with Marshal Maxwell in Burning Steppes",
                      None)),
                    ({"act": "vendor:%d" % DELIANA, "items": [SORC, ROBE, AHRING]},
                     ("Deliana in Ironforge sells the Sorcerer's Crown and the Robe of the Archmage and more", None)),
                    ({"act": "inst:2057", "items": [4242]}, (None, None)),
                    (dict(SCHOLO, text="Scholomance: Bonecreeper Stylus"),        # (the planner's short line: Live.lua's)
                     ("Scholomance. The Bonecreeper Stylus drops from Darkmaster Gandling", None))):
        said = offline_coach.move_parts(m, NAMES.get, lambda i: SRCS.get(i, []))
        check(said == want and offline_coach.move_text(m, NAMES.get, lambda i: SRCS.get(i, [])) == want[0],
              (m["act"], said))
    check(offline_coach.move_say(RUN2, NAMES.get, lambda i: SRCS.get(i, [])) ==
          "Scholomance. The Magister's Crown drops from Darkmaster Gandling, about 1 in 5, usually 2 to 6 runs; that's "
          "classic's chance. The run also rolls the Felcloth Shoulders", "the run as said")
    check(offline_coach.next_move_line({"text": "Scholomance. The Magister's Crown drops from Darkmaster Gandling",
                                        "odds": RUN2["odds"], "also": "the Felcloth Shoulders"}) ==
          "Next: Scholomance. The Magister's Crown drops from Darkmaster Gandling, about 1 in 5, usually 2 to 6 runs; "
          "that's classic's chance. The run also rolls the Felcloth Shoulders.", "the offline next_move with also")
    # what the planner says of a move fills in what the research doesn't know
    ubrs = {"act": "inst:1583:Upper", "kind": "inst", "items": [16900], "names": {"16900": "Stormrage Cover"},
            "place": "Upper Blackrock Spire", "zone": "Blackrock Spire", "text": "Upper Blackrock Spire: Stormrage Cover",
            "drops": [{"item": 16900, "name": "Stormrage Cover", "npc": 10363, "npc_name": "General Drakkisath"}]}
    check(offline_coach.move_text(ubrs, lambda i: ubrs["names"].get(str(i)), lambda i: []) ==
          "Upper Blackrock Spire. The Stormrage Cover drops from General Drakkisath", "the planner's fields")
    check(offline_coach.move_place(ubrs, lambda i: []) == ["Upper Blackrock Spire", "Blackrock Spire"], "its places")
    check(rules_mod.same_place("Blackrock Spire", "Upper Blackrock Spire") and not rules_mod.same_place("Spire", ""),
          "the strip's zone name for a wing")
    mob = {"act": "mob:7846", "items": [ROBE], "npc_name": "Teremus the Devourer", "place": "Blasted Lands"}
    check(offline_coach.move_text(mob, NAMES.get, lambda i: []) ==
          "Teremus the Devourer in Blasted Lands drops the Robe of the Archmage", "a creature outside an instance")
    check(r._bosses_of(mob, "mob", 7846, ROBE) == [7846] and r._bosses_of(ubrs, "inst", "1583:Upper", 16900) == [10363],
          "whose kill is a run")

    # ---- categories and topics ----------------------------------------------------------------------------------
    for kind in ("next_move", "pursuit_roll", "pursuit_got"):
        check(rules_mod.CATEGORY[kind] == "gear" and prompt_context.TRIGGER_TOPIC[kind] == "gear", kind)
        check(not rules_mod.allowed(kind, {"coach": True, "gear": False}), ("the gear switch", kind))

    # ---- Phase 2's next_upgrade waits while the plan has a move ----------------------------------------------------
    class Ranker:
        def told(self, key):
            return False

        def mark_told(self, key):
            pass
    r.bis = Ranker()
    gear.set_bis("Nine", {"slots": {}, "next": {"id": SORC, "slot": "Head", "d": 8.1, "s": 0.3, "avail": "now"}})
    check(frame(kinds=("next_upgrade",)) == [], "the next move speaks for it")
    r.bis = None
    gear.set_bis("Nine", None)

    # ---- zoning into the move's dungeon: what drops there, once --------------------------------------------------
    planner.cur = plan(SCHOLO, alts=[VENDOR])
    frame()                                                  # (the plan seen; its next_move was said before)
    frame(zone="Western Plaguelands")
    got = frame(zone="Scholomance")
    check([(t.kind, t.facts.get("here")) for t in got] == [("next_move", True)], got)
    check(offline_coach.callout(got, {"player": {}}) == "The Bonecreeper Stylus drops from Darkmaster Gandling here.",
          offline_coach.callout(got, {"player": {}}))
    frame(zone="Western Plaguelands")
    check(frame(zone="Scholomance") == [], "once in a while per place")
    # an alternative's dungeon too (the player went for it anyway)
    strat = {"act": "inst:2017", "items": [MANTLE], "place": "Stratholme", "zone": "Stratholme",
             "drops": [{"item": MANTLE, "npc": 10440, "npc_name": "Baron Rivendare"}]}
    planner.cur = plan(SCHOLO, alts=[VENDOR, strat])
    frame(zone="Eastern Plaguelands")
    got = frame(zone="Stratholme")
    check([offline_coach.next_move_line(t.facts) for t in got] ==
          ["The Felcloth Shoulders drop from Baron Rivendare here."], got)
    frame(zone="Eastern Plaguelands", step=0.5)
    check(frame(zone="Stratholme", step=0.5) == [], "once")
    planner.cur = plan(SCHOLO, alts=[VENDOR])
    frame(zone="Western Plaguelands")

    # ---- pursuit_roll: per kill, after the loot, with the runs and the range --------------------------------------
    check(frame(zone="Scholomance", target=(GANDLING, False)) == [], "alive: no kill")
    check(frame(zone="Scholomance", target=(GANDLING, True)) == [], "the loot comes first")
    check(frame(zone="Scholomance", target=(GANDLING, True), step=60) == [], "still looting")
    check(rules_mod.ROLL_WAIT_S >= pursuit_mod.LOOT_S, "the roll said before the loot may land (pursuit.LOOT_S)")
    check(frame(zone="Scholomance", target=(GANDLING, True), combat=1, step=rules_mod.ROLL_WAIT_S - 76) == [],
          "in combat: held")
    got = frame(zone="Scholomance", target=(GANDLING, True))
    check([t.kind for t in got] == ["pursuit_roll"] and got[0].priority == 3, got)
    check(got[0].facts == {"item": "Bonecreeper Stylus", "tries": 1, "range": "3 to 10",
                           "src": "classic's chance"}, got[0].facts)
    check(offline_coach.callout(got, {"player": {}}) ==
          "No Bonecreeper Stylus. That's run 1; it usually takes 3 to 10 (classic's chance).", "the roll line")
    for _ in range(5):
        check(frame(zone="Scholomance", target=(GANDLING, True), step=30) == [], "one kill per corpse")
    clock[0] += rules_mod.KILL_GAP_S
    frame(zone="Scholomance", target=(GANDLING, True))
    got = frame(zone="Scholomance", step=rules_mod.ROLL_WAIT_S)
    check([t.facts.get("tries") for t in got] == [2], got)
    # the planner's own count wins; past the range: bad luck, not a bad plan
    planner.runs = lambda char, iid: 11 if (char, iid) == ("Nine", STYLUS) else 0
    clock[0] += rules_mod.KILL_GAP_S
    frame(zone="Scholomance", target=(GANDLING, True))
    got = frame(zone="Scholomance", step=rules_mod.ROLL_WAIT_S)
    check(got and got[0].facts["tries"] == 11 and got[0].facts.get("unlucky") is True, got)
    check(offline_coach.pursuit_roll_line(got[0].facts) ==
          "No Bonecreeper Stylus. That's run 11; it usually takes 3 to 10 (classic's chance). 11 runs without it is "
          "bad luck, not a bad plan.",
          offline_coach.pursuit_roll_line(got[0].facts))
    # the planner's count stuck (its poll missed a kill): its count when first followed plus this session's kills
    planner.runs = lambda char, iid: 0
    clock[0] += rules_mod.KILL_GAP_S
    frame(zone="Scholomance", target=(GANDLING, True))
    got = frame(zone="Scholomance", step=rules_mod.ROLL_WAIT_S)
    check(got and got[0].facts["tries"] == 4 == r.p_runs[STYLUS], (got, r.p_runs))
    del planner.runs
    # muted: counted, untold
    clock[0] += rules_mod.KILL_GAP_S
    frame(zone="Scholomance", target=(GANDLING, True))
    check(frame(zone="Scholomance", step=rules_mod.ROLL_WAIT_S, cf=gear_off) == [], "muted roll")
    check(r.p_runs[STYLUS] == 5, r.p_runs)
    check(rules_mod.odds_range({"odds": "earliest December 9, then one roll a week"}) is None, "no range in words")
    check(rules_mod.odds_range({"range": [2, 6], "odds": ODDS}) == (2, 6), "the planner's range first")
    # bad luck past the median try for the chance (12.93%: a median of 5; the spec's "Six runs without it")
    stylus_p = dict(SCHOLO, p=0.1293)
    check(rules_mod.odds_range(stylus_p) == (3, 10), rules_mod.odds_range(stylus_p))
    check(not rules_mod.unlucky(5, stylus_p, (3, 10)) and rules_mod.unlucky(6, stylus_p, (3, 10)), "the median")
    check(rules_mod.unlucky(11, SCHOLO, (3, 10)) and not rules_mod.unlucky(10, SCHOLO, (3, 10)), "no chance: range")
    check(not rules_mod.unlucky(0, stylus_p, (3, 10)) and not rules_mod.unlucky(None, stylus_p), "no runs")
    # a raid's try is a week, a creature's a kill
    check(offline_coach.pursuit_roll_line({"item": "Robe of the Archmage", "tries": 2, "range": "2 to 9",
                                           "unit": "weeks", "src": "classic's chance"}) ==
          "No Robe of the Archmage. That's week 2; it usually takes 2 to 9 (classic's chance).", "a raid")
    check(offline_coach.pursuit_roll_line({"item": "Robe of the Archmage", "tries": 1, "range": None, "unit": "kills",
                                           "unlucky": True}) ==
          "No Robe of the Archmage. That's kill 1. 1 kill without it is bad luck, not a bad plan.", "a creature")

    # the planner's ledger: its tries and range for the item (the tries it keeps in data/pursuit.json)
    class Ledger:
        def ledger(self, char, iid):
            return {"item": iid, "tries": 6, "range": [3, 11]} if iid == STYLUS else None
    check(rules_mod.pursuit_runs(Ledger(), "Nine", STYLUS, 2) == 6 and
          rules_mod.pursuit_runs(Ledger(), "Nine", CROWN, 2) == 2, "the ledger's count, else the caller's")
    check(rules_mod.pursuit_range(Ledger(), "Nine", STYLUS, SCHOLO) == (3, 11) and
          rules_mod.pursuit_range(Ledger(), "Nine", CROWN, SCHOLO) == (3, 10), "the ledger's range, else the move's")

    # told with the character when the planner takes it (a key said before its first poll is the right one's)
    class Told:
        def __init__(self):
            self.got = []

        def told(self, key, char_key_=None):
            self.got.append((key, char_key_))
            return False

        def mark_told(self, key, char_key_=None):
            self.got.append(("mark", key, char_key_))
    tp = Told()
    rules_mod.planner_told(tp, "k", "Nine")
    rules_mod.planner_told(tp, "k", "Nine", mark=True)
    check(tp.got == [("k", "Nine"), ("mark", "k", "Nine")], tp.got)

    # ---- pursuit_got: the item lands; gear_upgrade leaves it; the next move follows once the plan moves on ---------
    stylus_bag = "0.1:%d:INVTYPE_RANGEDRIGHT:4:1:65:4:57:1:Bonecreeper Stylus:IN20/SP20" % STYLUS
    other_bag = "0.2:18555:INVTYPE_HEAD:4:1:60:4:55:1:Spare Hat:IN40"
    clock[0] += rules_mod.KILL_GAP_S
    frame(zone="Scholomance", target=(GANDLING, True))
    got = frame(zone="Scholomance", bags=[stylus_bag, other_bag], step=1)
    names = [u["item"] for t in got if t.kind == "gear_upgrade" for u in t.facts["upgrades"]]
    check("Spare Hat" in names and "Bonecreeper Stylus" not in names, ("gear_upgrade leaves the stylus", got))
    check(not [t for t in got if t.kind == "pursuit_got"], "said once the plan moved past it")
    planner.cur = plan(RUN2, alts=[VENDOR])
    got = frame(zone="Scholomance", bags=[stylus_bag, other_bag])
    check([t.kind for t in got] == ["pursuit_got"] and got[0].priority == 2, got)
    check(("fresh", "Nine") in planner.calls, "the plan worked out again at once when an item lands")
    check(got[0].facts == {"item": "Bonecreeper Stylus", "equip": True,
                           "next": "Scholomance. The Magister's Crown drops from Darkmaster Gandling, about 1 in 5, "
                                   "usually 2 to 6 runs; that's classic's chance. The run also rolls the Felcloth "
                                   "Shoulders"}, got[0].facts)
    check(offline_coach.callout(got, {"player": {}}) ==
          "The Bonecreeper Stylus is yours; equip it. Next: Scholomance. The Magister's Crown drops from Darkmaster "
          "Gandling, about 1 in 5, usually 2 to 6 runs; that's classic's chance. The run also rolls the Felcloth "
          "Shoulders.", offline_coach.callout(got, {"player": {}}))
    for _ in range(3):
        check(frame(zone="Scholomance", bags=[stylus_bag, other_bag], step=rules_mod.ROLL_WAIT_S) == [],
              "no roll for a kill that dropped it, no next_move for the move it named, no gear_upgrade for it")
    # a late gear_upgrade for it is left out too (the item's name, from a frame raised before)
    out = [Trigger("gear_upgrade", {"upgrades": [{"item": "Bonecreeper Stylus"}, {"item": "Spare Hat"}]})]
    r._drop_gear_line("Bonecreeper Stylus", out)
    check([u["item"] for u in out[0].facts["upgrades"]] == ["Spare Hat"], out)
    out = [Trigger("gear_upgrade", {"upgrades": [{"item": "Bonecreeper Stylus"}]})]
    r._drop_gear_line("Bonecreeper Stylus", out)
    check(out == [], "the whole line goes")
    # regression: the stylus moved to another bag slot (a new key, the same item): gear_upgrade still leaves it
    moved_bag = stylus_bag.replace("0.1:", "1.4:", 1)
    got = frame(zone="Scholomance", bags=[moved_bag, other_bag])
    check(not [u for t in got if t.kind == "gear_upgrade" for u in t.facts["upgrades"]
               if u["item"] == "Bonecreeper Stylus"], ("moved, said as an upgrade again", got))
    # ... nor after an engine restart (this session's record gone): the planner's own record of what landed
    saved_sr = (st, r)
    st, r = engine()
    FakePlanner.owned_ids = [STYLUS]
    got = frame(zone="Scholomance", bags=[moved_bag, other_bag], kinds=("gear_upgrade",))
    names = [u["item"] for t in got for u in t.facts["upgrades"]]
    check("Bonecreeper Stylus" not in names and "Spare Hat" in names, ("after a restart", got))
    FakePlanner.owned_ids = []
    st, r = saved_sr
    frame(zone="Scholomance", bags=[stylus_bag, other_bag])
    # the plan not moving on: said after GOT_WAIT_S without a next move
    planner.cur = plan(dict(RUN2, items=[CROWN]))
    frame(zone="Scholomance", bags=[stylus_bag])
    got = frame(zone="Scholomance", bags=[stylus_bag, "0.3:%d:INVTYPE_HEAD:4:1:62:4:57:1:Magister's Crown:IN30" % CROWN])
    check(not [t for t in got if t.kind == "pursuit_got"], "waits for the plan")
    got = frame(zone="Scholomance", bags=[stylus_bag, "0.3:%d:INVTYPE_HEAD:4:1:62:4:57:1:Magister's Crown:IN30" % CROWN],
                step=rules_mod.GOT_WAIT_S)
    check([(t.kind, t.facts.get("next")) for t in got if t.kind == "pursuit_got"] == [("pursuit_got", None)], got)
    check(offline_coach.pursuit_got_line({"item": "Magister's Crown"}) == "The Magister's Crown is yours.", "no next")

    # ---- reminders: the move's vendor and auction house steps, replaced when the plan changes ---------------------
    planner.cur = plan(VENDOR)
    frame()
    mine = [x for x in rem.list() if x.get("source") == "pursuit"]
    check([(x["what"], x["when"]) for x in mine] ==
          [("buy the Sorcerer's Crown from Deliana", {"kind": "npc", "npc": DELIANA, "name": "Deliana"}),
           ("buy the Sorcerer's Crown from Deliana", {"kind": "place", "place": "Ironforge"})], mine)
    check(all(x["every_min"] == 0 for x in mine), "no nudges")
    check(all(x.get("char") == "Nine" for x in mine), ("the character's own", mine))
    check(reminders_mod.describe_when(mine[0]) == "near Deliana", reminders_mod.describe_when(mine[0]))
    ids = [x["id"] for x in mine]
    frame()
    check([x["id"] for x in rem.list() if x.get("source") == "pursuit"] == ids, "the same plan: kept as they are")
    planner.cur = plan(AH)
    frame()
    mine = [x for x in rem.list() if x.get("source") == "pursuit"]
    check([(x["what"], x["when"]) for x in mine] ==
          [("check the auction house for the Band of the Ogre King",
            {"kind": "place", "place": ["Stormwind City", "Ironforge", "Darnassus"]})], mine)
    check(reminders_mod.describe_when(mine[0]) == "in Stormwind City, Ironforge or Darnassus", mine)
    check(own["id"] in [x["id"] for x in rem.list()], "the player's own stays")
    planner.cur = plan(SCHOLO)
    frame()
    check([x for x in rem.list() if x.get("source") == "pursuit"] == [], "a dungeon move: none")
    check([x["id"] for x in rem.list()] == [own["id"]], "the player's own stays")

    # a place reminder with a list: said in the one the player is in; an npc one near that NPC
    class Env:
        near_yd, level, durability, busy = 40, 60, 100, False

        def __init__(self, place=None, near=None):
            self.place, self.near = place, near

        def nearest(self, role=None, item=None, npc=None, name=None):
            return self.near if npc == DELIANA else None

        def in_place(self, p):
            return p == self.place
    rs = reminders_mod.Reminders(None)
    rs.replace("pursuit", rules_mod.step_reminders(AH, NAMES.get, lambda i: SRCS.get(i, []), "Alliance"), now=0)
    ev = rs.check(Env(place="Ironforge"), now=10)
    check([reminders_mod.speech(*e) for e in ev] ==
          ["Reminder, you're in Ironforge: Check the auction house for the Band of the Ogre King."], ev)
    rs.replace("pursuit", rules_mod.step_reminders(VENDOR, NAMES.get, lambda i: SRCS.get(i, []), "Alliance"), now=20)
    ev = rs.check(Env(near={"name": "Deliana", "subname": "Mail Armor Merchant", "yards": 24, "direction": "north"}),
                  now=30)
    check([reminders_mod.speech(*e) for e in ev] ==
          ["Reminder: Buy the Sorcerer's Crown from Deliana. Deliana (Mail Armor Merchant) is 25 yards north."], ev)
    check(rules_mod.step_reminders(SCHOLO, NAMES.get, lambda i: SRCS.get(i, [])) == [], "none for a dungeon")
    added, removed = rs.replace("pursuit", [], now=40)
    check(added == [] and len(removed) == 2 and rs.list() == [], "replaced by none")
    # regression: the steps are the 60 main's alone: an alt (here level 32) in Ironforge hears none, and they stay
    # armed for the main
    rs.replace("pursuit", rules_mod.step_reminders(AH, NAMES.get, lambda i: SRCS.get(i, []), "Alliance"), now=50,
               char="Nine-Skyborne")
    alt, main = Env(place="Ironforge"), Env(place="Ironforge")
    alt.s, main.s = {"name": "Paly", "realm": "Skyborne", "level": 32}, {"name": "Nine", "realm": "Skyborne"}
    check(rs.check(alt, now=60) == [] and rs.list()[0]["armed"], "the main's step said to an alt")
    check(len(rs.check(main, now=70)) == 1, "the main's step, still armed for them")
    # regression: held while gear talk is muted (/coach gear off holds the move: its steps too), untold and armed
    rs.replace("pursuit", [], now=80)
    rs.replace("pursuit", rules_mod.step_reminders(AH, NAMES.get, lambda i: SRCS.get(i, []), "Alliance"), now=90,
               char="Nine-Skyborne")
    check(rs.check(main, now=100, held=lambda x: reminder_ticker.muted(x, {"gear": False})) == []
          and rs.list()[0]["armed"], "a step said while gear talk is muted")
    check(len(rs.check(main, now=110, held=lambda x: reminder_ticker.muted(x, {"gear": True}))) == 1,
          "said once gear talk is on again")
    check(not reminder_ticker.muted({"what": "repair"}, {"gear": False})
          and reminder_ticker.muted({"source": "pursuit"}, {"coach": False}), "only the pursuit's, by its talk")
    rs.replace("pursuit", [], now=120)

    # the real index: one NPC by id, whatever its role
    class Pack:
        def friendly_npcs(self, ui_map, faction):
            return [{"id": DELIANA, "name": "Deliana", "subname": "Mail Armor Merchant", "x": 50.0, "y": 50.0},
                    {"id": 5, "name": "Someone", "subname": "Banker", "x": 51.0, "y": 50.0}]

        def vendors_selling(self, item, ui_map, faction):
            return []
    env = reminders_mod.Env({"faction": "Alliance"}, {"map": 1455, "x": 50.5, "y": 50.0},
                            reminders_mod.NpcIndex(Pack()))
    hit = env.nearest(npc=DELIANA, name="Deliana")
    check(hit and hit["name"] == "Deliana", hit)
    check(env.nearest(name="deliana")["name"] == "Deliana" and env.nearest(npc=999) is None, "by name; none")
    # the planner's own vendor and town fill in what the research doesn't know
    ven = {"act": "vendor:11536", "items": [AHRING], "npc_name": "Quartermaster Miranda Breechlock",
           "place": "Eastern Plaguelands"}
    check([(x["what"], x["when"]) for x in rules_mod.step_reminders(ven, NAMES.get, lambda i: [])] ==
          [("buy the Band of the Ogre King from Quartermaster Miranda Breechlock",
            {"kind": "npc", "npc": 11536, "name": "Quartermaster Miranda Breechlock"}),
           ("buy the Band of the Ogre King from Quartermaster Miranda Breechlock",
            {"kind": "place", "place": "Eastern Plaguelands"})], "the planner's vendor")

    # ---- what the real planner's moves carry (pursuit.Planner: key, names with stand-ins, p, text) ----------------
    real = {"act": "inst:2057", "key": "inst:2057|13938,16686", "kind": "inst", "items": [CROWN, STYLUS],
            "names": {str(CROWN): "Magister's Crown", str(STYLUS): "item 13938"}, "p": 0.1384,
            "odds": "about 1 in 7, usually 2 to 10 runs; that's classic's chance", "text": "Scholomance: Magister's "
            "Crown, item 13938", "place": "Scholomance", "zone": "Scholomance",
            "drops": [{"item": CROWN, "name": "Magister's Crown", "npc": GANDLING, "npc_name": "Darkmaster Gandling"},
                      {"item": STYLUS, "name": "item 13938", "npc": GANDLING, "npc_name": "Darkmaster Gandling"}]}
    check(rules_mod.move_key("Nine", real) == "next_move|Nine|inst:2057|13938,16686" ==
          rules_mod.move_key("Nine", dict(real, key=None, items=[STYLUS, CROWN])), "the move's own key; sorted")
    check(offline_coach.given_names(real) == {CROWN: "Magister's Crown"}, "a stand-in isn't a name")
    check(offline_coach.move_parts(real, lambda i: offline_coach.given_names(real).get(i) or NAMES.get(i),
                                   lambda i: []) ==
          ("Scholomance. The Magister's Crown drops from Darkmaster Gandling", "the Bonecreeper Stylus"),
          "the research's name in place of the stand-in")
    check(offline_coach.move_parts(real, offline_coach.given_names(real).get, lambda i: []) ==
          ("Scholomance. The Magister's Crown drops from Darkmaster Gandling", None), "no name: left out, never an id")
    check(offline_coach.overlay_line(60, real, NAMES.get) ==
          "L60  Next: Scholomance. The Magister's Crown drops from Darkmaster Gandling", "the overlay without a stand-in")
    check(offline_coach.overlay_line(60, dict(SCHOLO, text="Scholomance: Bonecreeper Stylus")) ==
          "L60  Next: Scholomance: Bonecreeper Stylus", "the overlay: the planner's short line")
    check(offline_coach.overlay_line(60, None) is None and offline_coach.overlay_line(60, {"act": "inst:1"}) is None,
          "no move, no line")
    for odds, src in (("about 1 in 8, usually 3 to 10 runs; that's classic's chance", "classic's chance"),
                      ("about 1 in 8, usually 3 to 10 runs; that's classic's chance with your kills counted",
                       "classic's chance with your kills counted"),
                      ("about 1 in 5, usually 2 to 7 runs (web, unconfirmed)", "web, unconfirmed"),
                      ("about 1 in 5, usually 2 to 7 runs (web, unconfirmed, with your kills counted)",
                       "web, unconfirmed"),
                      ("about 1 in 3, usually 1 to 4 runs, by your own kills", None),
                      ("my guess is about 1 in 10, usually 3 to 13 runs; I don't know the real chance yet", "a guess"),
                      ("I don't know its price yet", None), (None, None)):
        check(offline_coach.odds_source(odds) == src, (odds, offline_coach.odds_source(odds)))

    # only the first move and its two alternatives are followed (what the player was told of)
    BARON = 10440
    far = {"act": "inst:2017", "items": [ROBE], "drops": [{"item": ROBE, "npc": BARON, "npc_name": "Baron Rivendare"}]}
    planner.cur = plan(SCHOLO, VENDOR, CRAFT, far, alts=[VENDOR, CRAFT])
    frame()
    check(BARON not in r.p_bosses and GANDLING in r.p_bosses and ROBE in r.p_missing, (r.p_bosses, r.p_missing))
    check([m["act"] for m in r.p_moves] == ["inst:2057", VENDOR["act"], "craft:%d" % ROBE], r.p_moves)

    # the planner switched off (pursuit.enabled): nothing said, its reminders gone, next_upgrade speaks again
    planner.cur = plan(VENDOR)
    frame()
    check([x for x in rem.list() if x.get("source") == "pursuit"], "the vendor's reminders")
    planner.enabled = lambda: False
    check(frame() == [] and r.p_move is None and r.p_looked, "off")
    check([x for x in rem.list() if x.get("source") == "pursuit"] == [] and own["id"] in [x["id"] for x in rem.list()],
          "its reminders gone, the player's own stay")
    del planner.enabled
    frame()
    check(r.p_move is VENDOR or r.p_move == VENDOR, "on again")
    # next_upgrade waits for the plan's first look (the two would say the same thing): a planner set after the start
    st, r = engine()
    r.pursuit = None
    settle()
    check(rules_mod.planner_on(planner) and not r.p_looked, "a fresh engine hasn't looked")
    r.pursuit, r.bis = planner, Ranker()
    gear.set_bis("Nine", {"slots": {}, "next": {"id": SORC, "slot": "Head", "d": 8.1, "s": 0.3, "avail": "now"}})
    planner.cur = plan()                                     # (no move: nothing better to chase now)
    check(frame(kinds=("next_upgrade", "next_move")) == [] and r.p_looked, "waits for the look")
    check([t.kind for t in frame(kinds=("next_upgrade", "next_move"))] == ["next_upgrade"], "then it speaks")
    r.bis = None
    gear.set_bis("Nine", None)
    planner.cur = plan(SCHOLO, alts=[VENDOR, CRAFT])

    # ---- the prompt: gear.next_move, and its alternatives trimmed first --------------------------------------------
    coach_tools.research, coach_tools.pursuit, coach_tools.pursuit_char = FakeResearch(), planner, lambda: "Nine"
    coach_tools.bis = None
    planner.cur = plan(SCHOLO, alts=[VENDOR, CRAFT])
    brief = coach_tools.plan_brief()
    check(brief == {"move": "Scholomance. The Bonecreeper Stylus drops from Darkmaster Gandling", "odds": ODDS,
                    "why": SCHOLO["why"], "alts": ["Deliana in Ironforge sells the Sorcerer's Crown",
                                                   "the Robe of the Archmage, made with Tailoring"]}, brief)

    class Geo:
        def describe(self, *a, **k):
            return {}

        def area_at(self, *a):
            return None, None

    class Know:
        def quest(self, *a, **k):
            return {}
    s = st.summary()
    ctx = prompt_context.game_context(s, None, Know(), Geo(), gear_fn=lambda: ([], [], st.gear["W"], []),
                                      pursuit_fn=coach_tools.plan_brief)
    check(ctx["gear"]["next_move"] == brief, ctx["gear"].get("next_move"))
    ctx = prompt_context.game_context(s, None, Know(), Geo(), gear_fn=lambda: ([], [], st.gear["W"], []))
    check("next_move" not in ctx["gear"], "without the getter: as before")
    ref = {"gear": {"next_move": dict(brief)}, "community": []}
    for step in prompt_context.reference_trims(ref):
        while step(ref):
            pass
    check(ref["gear"]["next_move"]["alts"] == [] and ref["gear"]["next_move"]["move"], "alternatives trimmed")

    # ---- bis_plan and pursuit_set ----------------------------------------------------------------------------------
    check([d["name"] for d in coach_tools.item_tools()] == ["item_info", "bis_plan", "pursuit_set"],
          [d["name"] for d in coach_tools.item_tools()])
    check("bis_plan" in coach_tools.LOCAL_TOOLS and "pursuit_set" in coach_tools.ITEM_TOOL_NAMES, "local tools")
    disk.clear()
    planner.runs = lambda char, iid: 2
    got = coach_tools.run_item_tool("bis_plan", {})
    check(got == {"move": "Scholomance. The Bonecreeper Stylus drops from Darkmaster Gandling", "odds": ODDS,
                  "why": SCHOLO["why"], "runs": 2,
                  "alts": [{"move": "Deliana in Ironforge sells the Sorcerer's Crown",
                            "odds": "I don't know its price yet", "why": "a sure thing, close by"},
                           {"move": "the Robe of the Archmage, made with Tailoring", "odds": None,
                            "why": "ask a Tailoring crafter"}], "goal": None}, got)
    check(disk == {rules_mod.move_key("Nine", SCHOLO): 1}, ("told: the rules don't say it again", disk))
    mem = memory_mod.Memory(None)
    mem.add("Wants to hit 60 by Friday.", "goal")
    mem.add("Best-in-slot goal: Felcloth Shoulders.", "goal")
    coach_tools.goal_memory = mem
    got = coach_tools.run_item_tool("pursuit_set", {"item": "robe"})
    check(("pursue", "Nine", ROBE) in planner.calls and got["done"] == "chasing the Robe of the Archmage", got)
    check(got["move"] == "the Robe of the Archmage, made with Tailoring" and got["goal"] == "Robe of the Archmage", got)
    check(sorted(n["text"] for n in mem.list() if n["kind"] == "goal") ==
          ["Best-in-slot goal: Robe of the Archmage.", "Wants to hit 60 by Friday."], mem.list())
    got = coach_tools.run_item_tool("pursuit_set", {"skip": True})
    check(("skip", "Nine", ROBE) in planner.calls and got["done"] == "skipping the Robe of the Archmage", got)
    # regression: the skipped goal's note goes at once (not at the next bis_plan); the planner's on_goal_cleared (its
    # goal landed) takes it too
    check([n["text"] for n in mem.list() if n["kind"] == "goal"] == ["Wants to hit 60 by Friday."],
          ("the skipped goal's note stays", mem.list()))
    coach_tools._goal_note("Robe of the Archmage")
    coach_tools.goal_cleared()
    check([n["text"] for n in mem.list() if n["kind"] == "goal"] == ["Wants to hit 60 by Friday."],
          ("the reached goal's note stays", mem.list()))
    # regression: an item nobody can name is "that item", never "the that item"; the plan's own name first
    nameless = dict(SCHOLO, act="inst:9", items=[77777])
    planner.cur = plan(nameless)
    got = coach_tools.run_item_tool("pursuit_set", {"skip": True})
    check(got["done"] == "skipping that item", got)
    planner.cur = plan(dict(nameless, names={"77777": "Shard of Nowhere"}))
    check(coach_tools.run_item_tool("pursuit_set", {"skip": True})["done"] == "skipping the Shard of Nowhere",
          "the plan's name")
    planner.cur = plan(CRAFT)
    check(coach_tools.run_item_tool("pursuit_set", {"item": "Shoes of Nowhere"}).get("say") ==
          "I don't know which item you mean.", "an unknown item")
    planner.cur = plan(SCHOLO)
    got = coach_tools.run_item_tool("bis_plan", {})
    check(got["goal"] is None and [n["text"] for n in mem.list() if n["kind"] == "goal"] ==
          ["Wants to hit 60 by Friday."], ("the goal's note goes with it", mem.list()))
    got = coach_tools.run_item_tool("pursuit_set", {})
    check(planner.calls[-1] == ("plan", "Nine") and ("pursue", "Nine", None) in planner.calls and
          got["done"] == "Glide picks the next move again", got)
    planner.cur = dict(plan(SCHOLO, goal=STYLUS), pursued=False)
    check(coach_tools.bis_plan()["goal"] is None, "the plan's own pick isn't the player's goal")
    planner.cur = dict(plan(SCHOLO, goal=STYLUS), pursued=True)
    check(coach_tools.bis_plan()["goal"] == "Bonecreeper Stylus", "the player's goal")
    planner.cur = None
    check(coach_tools.bis_plan().get("say") == coach_tools.NO_PLAN, "no plan yet")

    # ---- the spoken requests (the AI off) --------------------------------------------------------------------------
    planner.cur = plan(SCHOLO, alts=[VENDOR, CRAFT])
    for q, want in (("What's my next move?", ("plan", None)), ("what should I farm next", ("plan", None)),
                    ("how's the stylus hunt going?", ("hunt", "stylus")), ("Chase the robe", ("chase", "the robe")),
                    ("skip that", ("skip", "that")), ("stop chasing the stylus", ("skip", "the stylus")),
                    ("let Glide pick again", ("pick", None)), ("what should I do next", None),
                    ("where does the robe drop", None), ("", None)):
        check(coach_tools.pursuit_question(q) == want, (q, coach_tools.pursuit_question(q)))
    check(coach_tools.item_command("What's my next move?", True) is None, "the AI answers")
    check(coach_tools.item_command("What's my next move?", False) ==
          "Next: Scholomance. The Bonecreeper Stylus drops from Darkmaster Gandling, about 1 in 8, usually 3 to 10 "
          "runs; that's classic's chance. Or: Deliana in Ironforge sells the Sorcerer's Crown; or the Robe of the "
          "Archmage, made with Tailoring.", coach_tools.item_command("What's my next move?", False))
    check(coach_tools.item_command("how's the stylus hunt?", False) ==
          "Run 2 without the Bonecreeper Stylus; it usually takes 3 to 10 (classic's chance).", "the hunt")
    planner.runs = lambda char, iid: 12
    check(coach_tools.item_command("how's the stylus hunt?", False) ==
          "12 runs without the Bonecreeper Stylus: bad luck, not a bad plan; it usually takes 3 to 10 (classic's "
          "chance).", "unlucky")
    check(offline_coach.odds_source(ODDS) == "classic's chance" and offline_coach.odds_source(
        "I don't know its price yet") is None and offline_coach.odds_source(
        "about 1 in 6, usually 2 to 8 runs; web, unconfirmed") == "web, unconfirmed", "the source an odds line names")
    del planner.runs
    check(coach_tools.item_command("how's the stylus hunt?", False) ==
          "I haven't counted runs for the Bonecreeper Stylus yet.", "no count")
    check(coach_tools.item_command("chase the robe", False) ==
          "Chasing the Robe of the Archmage. Next: the Robe of the Archmage, made with Tailoring.", "chase")
    planner.cur = plan(SCHOLO, alts=[VENDOR])
    check(coach_tools.item_command("skip that", False) ==
          "Skipping the Bonecreeper Stylus. Next: Deliana in Ironforge sells the Sorcerer's Crown; I don't know its "
          "price yet.", coach_tools.item_command("skip that", False))
    check(coach_tools.item_command("let's go for a run", False) is None, "no item: another question")
    planner.cur = plan(SCHOLO)
    check(coach_tools.item_command("chase this", False, hovered=ROBE).startswith("Chasing the Robe of the Archmage"),
          "this: the hovered item")
    # regression: pursuit.enabled off: no tools, nothing in the prompt, no answers, nothing changed
    planner.cur = plan(SCHOLO, alts=[VENDOR])
    planner.enabled = lambda: False
    calls = len(planner.calls)
    check([d["name"] for d in coach_tools.item_tools()] == ["item_info"], "the tools offered while off")
    check(coach_tools.plan_brief() is None and coach_tools.bis_plan().get("say") == coach_tools.NO_PLAN
          and coach_tools.pursuit_set("robe").get("say") == coach_tools.NO_PLAN, "planned while off")
    check(coach_tools.item_command("What's my next move?", False) is None
          and coach_tools.item_command("chase the robe", False) is None, "answered while off")
    check(len(planner.calls) == calls, ("the planner asked while off", planner.calls[calls:]))
    del planner.enabled
    coach_tools.pursuit = None
    check(coach_tools.item_command("What's my next move?", False) is None and
          [d["name"] for d in coach_tools.item_tools()] == ["item_info"], "no planner: nothing")

    # ---- regression: the item lands 101 s after its boss died (the adds cleared first, a group's roll): never "No
    # Bonecreeper Stylus" first and "The Bonecreeper Stylus is yours" after ------------------------------------------
    st, r = engine()
    planner.cur = None
    settle()
    planner.cur = plan(SCHOLO, alts=[VENDOR, CRAFT])
    disk.clear()
    frame(zone="Scholomance")                               # (the move, said)
    frame(zone="Scholomance", target=(GANDLING, True))
    kinds = []
    for step, bags in ((85, ()), (16, [stylus_bag]), (20, [stylus_bag]), (rules_mod.GOT_WAIT_S, [stylus_bag])):
        kinds += [t.kind for t in frame(zone="Scholomance", bags=bags, step=step)]
    check(kinds == ["pursuit_got"], kinds)

    # ---- regression: an alt below 60: the planner isn't asked, and the main's steps aren't touched (theirs alone) ---
    planner.cur = plan(VENDOR)
    frame()
    placed = [x["id"] for x in rem.list() if x.get("source") == "pursuit"]
    check(placed, "the main's vendor steps")
    calls = len(planner.calls)
    for _ in range(3):
        check(frame(level=32, name="Paly") == [], "said to the alt")
    check([x["id"] for x in rem.list() if x.get("source") == "pursuit"] == placed, "the main's steps changed")
    check(r.p_char == "Paly" and not [c for c in planner.calls[calls:] if c[0] == "plan"], planner.calls[calls:])
    # ... and the reminder check says them to the main only (Env over the game state's summary)
    s_alt = st.summary()
    env_alt = reminders_mod.Env(s_alt, None, None)
    env_alt.in_place = lambda p: True
    check([e for e in rem.check(env_alt, now=clock[0]) if e[0].get("source") == "pursuit"] == [],
          "the main's step said to the alt")
    frame(name="Nine")
    env_main = reminders_mod.Env(st.summary(), None, None)
    env_main.in_place = lambda p: True
    check([e[0]["what"] for e in rem.check(env_main, now=clock[0]) if e[0].get("source") == "pursuit"] ==
          ["buy the Sorcerer's Crown from Deliana"], "the main's step, for the main")
finally:
    rules_mod.time = real_time
    (coach_tools.research, coach_tools.bis, coach_tools.pursuit, coach_tools.pursuit_char,
     coach_tools.goal_memory) = saved

print("OK")
