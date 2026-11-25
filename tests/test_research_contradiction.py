#!/usr/bin/env python3
"""The game checks what the web said (companion/research.py; the rules' fact_corrected in companion/rules.py, its
topic in prompt_context.py and its line in offline_coach.py), against fake item data, a small knowledge base, a fake
clock and a fake LLM client (never a real model):

the creature a web fact names dying CONTRA_KILLS times without the item (kills of one creature within KILL_DEDUP_S of
the first count once, as the player's own loot observation merges them; a kill whose item did come isn't one) halves
the fact's conf and looks the item up again, CONTRA_KILLS more halving it again; the knowledge base's observed looted
kills count the same way; a drop the player sees confirms the fact (it leads at 0.95, its doubts go). When the coach
had said the fact (item_info) and the new lookup puts the item elsewhere, the rules say fact_corrected once, out of
combat ({"item", "old", "new"}, priority 3, an upkeep callout about gear: "I had <item> wrong: <new source>."), also
after a restart; the same answer again, or a fact the coach never said, says nothing. Only the game shows it wrong: a
new lookup putting it elsewhere with no kill against it says nothing, nor does one naming the same boss by a shorter
name (his id, found by the name it is short for: his kills' doubts kept). A web fact naming a creature by a shorter
name the knowledge base doesn't know still has its kills counted. A correction not said yet survives the coach
answering about the item again, and what the coach said since is kept.

Run: python3 tests/test_research_contradiction.py   (prints OK)
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
          "TAVILY_API_KEY", "BRAVE_API_KEY"):
    os.environ[k] = ""
import offline_coach  # noqa: E402
import prompt_context  # noqa: E402
import research  # noqa: E402
import research_budget  # noqa: E402
import rules as rules_mod  # noqa: E402
from rules import Rules, Trigger  # noqa: E402
from state import GameState  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


WOWHEAD = "https://www.wowhead.com/classic/item=%d"


def answer(npc, zone="Molten Core", iid=18842):
    return (json.dumps({"facts": [{"kind": "drop", "npc": npc, "zone": zone, "url": WOWHEAD % iid}]}),
            {"cost_usd": 0.02, "calls": 1, "searches": 1, "local_searches": 0, "urls": [WOWHEAD % iid],
             "why": None, "stop": "end_turn"})


def nothing():
    return json.dumps({"facts": []}), {"cost_usd": 0.02, "calls": 1, "searches": 1, "urls": [], "why": None,
                                       "stop": "end_turn"}


class FakeLLM:
    """web_research answers with the next of `answers` (item id -> list of replies)."""
    cfg = {"fast_model": "claude-haiku-5-5"}
    web_off = None

    def __init__(self):
        self.answers, self.asked = {}, []

    def available(self, model):
        return True

    def busy(self):
        return False

    def web_search_tools(self, model, n):
        return [{"name": "web_search"}], False

    def web_research(self, prompt, system="", max_searches=3, **kw):
        for iid, replies in self.answers.items():
            if "item id %d)" % iid in prompt:
                self.asked.append(iid)
                return replies.pop(0)
        return "nothing", {"calls": 1, "cost_usd": 0.0, "urls": []}


class FakeDB:
    """WoWSims' item data: it knows none of these items (they are Forever's own)."""
    tag, leftover, npcs = "v1.0.4", True, {}

    def item(self, iid):
        return None

    def name(self, iid):
        return None

    def band(self, ch):
        return []


class FakeKB:
    def __init__(self):
        self.c = sqlite3.connect(":memory:", check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.executescript("""
            CREATE TABLE creature(id INTEGER PRIMARY KEY, name TEXT, friendly_to TEXT);
            CREATE TABLE creature_spawn(creature_id INT, ui_map INT, x REAL, y REAL);
            CREATE TABLE creature_drop(creature_id INT, item_id INT, rate REAL, n_obs INT, src TEXT, conf REAL);
            CREATE TABLE vendor_item(npc_id INT, item_id INT, price INT, src TEXT, conf REAL);
            CREATE TABLE quest(id INTEGER PRIMARY KEY, name TEXT, side TEXT, races INT);
            CREATE TABLE quest_reward(quest_id INT, item_id INT, qty INT, choice INT, src TEXT);
            CREATE TABLE item(id INTEGER PRIMARY KEY, name TEXT, class INT, subclass INT, req_level INT);
            INSERT INTO creature VALUES (11988, 'Golemagg the Incinerator', NULL), (11502, 'Ragnaros', NULL),
                (12118, 'Lucifron', NULL), (12056, 'Baron Geddon', NULL);
            INSERT INTO item VALUES (18842, 'Staff of Dominance', 2, 10, 60), (19136, 'Mana Igniting Cord', 4, 1, 60),
                (18829, 'Deep Earth Spaulders', 4, 3, 60), (18820, 'Talisman of Ephemeral Power', 4, 0, 60);
            -- the player's own loot observation: Lucifron looted 9 times (his other drops), never the spaulders;
            -- Baron Geddon looted 9 times, the talisman among his drops
            INSERT INTO creature_drop VALUES (12118, 16800, 0.2, 9, 'obs', 0.6), (12118, 16801, 0.1, 9, 'fg,obs', 0.6),
                (12056, 16797, 0.2, 9, 'obs', 0.6), (12056, 18820, 0.11, 9, 'obs', 0.6);
        """)

    def q(self, sql, *args):
        return [dict(r) for r in self.c.execute(sql, args).fetchall()]

    def one(self, sql, *args):
        r = self.q(sql, *args)
        return r[0] if r else None


class FakePack:
    ok = True

    def __init__(self):
        self.kb = FakeKB()

    def status(self):
        return {"ok": True, "built": "b1"}

    def map_name(self, ui_map):
        return None


class FakeState:
    def __init__(self):
        self.s = {"name": "Nine", "class": "MAGE", "level": 60, "faction": "Alliance", "zone": "Molten Core",
                  "target": None, "money": 1000}
        self.gear = {"W": [], "I": [{"key": "1", "id": 6948, "name": "Hearthstone"}], "R": []}

    def summary(self):
        return dict(self.s)


tmp = tempfile.mkdtemp(prefix="glide-contradiction-")
real_rules_time = rules_mod.time
try:
    clock = [time.mktime((2026, 11, 24, 20, 0, 0, 0, 0, -1))]
    home = os.path.join(tmp, "engine")
    db, pack, st = FakeDB(), FakePack(), FakeState()
    budget = research_budget.Budget(home, lambda: {"web": True, "web_per_day": 50, "usd_per_day": 5.0},
                                    now=lambda: clock[0])
    llm = FakeLLM()

    def worker():
        w = research.Research(home, lambda: db, lambda: pack, lambda: st, lambda: True, now=lambda: clock[0])
        w.set_web(research.WebResolver(lambda: llm, budget, now=lambda: clock[0]))
        w.spawn = lambda fn: fn()                    # (a lookup runs in the poll that starts it)
        return w
    r = worker()

    def tick(dt=15):
        clock[0] += dt
        r.tick()

    def kill(npc, name, loot=None):
        """The target dies (one poll), is looted (loot: an item id into the bags) or not, and LOOT_S passes."""
        st.s["target"] = {"npc": str(npc), "name": name, "dead": True}
        tick()
        if loot:
            st.gear["I"] = st.gear["I"] + [{"key": str(len(st.gear["I"]) + 1), "id": loot, "name": "Loot"}]
        tick()
        st.s["target"] = None
        tick(research.LOOT_S)

    # ---- a web fact the coach said: the staff drops from Golemagg -------------------------------------------------
    llm.answers[18842] = [answer("Golemagg the Incinerator"), answer("Ragnaros")]
    info = r.item_info("Staff of Dominance")
    check(info.get("looking_up"), info)
    tick()
    f = r.facts(18842)
    check(f and f["s"][0]["npc"] == 11988 and f["s"][0]["conf"] == 0.3 and llm.asked == [18842], f)
    info = r.item_info("Staff of Dominance")
    check(info["sources"] == ["drops from Golemagg the Incinerator, Molten Core (web, unconfirmed)"], info)
    check(r.told["i:18842"]["text"] == "drops from Golemagg the Incinerator, Molten Core", r.told)

    # ---- kills without it: one within KILL_DEDUP_S of the first counts once; 7 don't doubt it -----------------------
    kill(11988, "Golemagg the Incinerator")
    check(r.miss["i:18842"]["11988"] == [1, 0], r.miss)
    kill(11988, "Golemagg the Incinerator")              # (the same pull: within KILL_DEDUP_S of the first)
    check(r.miss["i:18842"]["11988"] == [1, 0], ("the same kill", r.miss))
    for _ in range(6):
        tick(research.KILL_DEDUP_S)
        kill(11988, "Golemagg the Incinerator")
    check(r.miss["i:18842"]["11988"] == [7, 0] and r.facts(18842)["s"][0]["conf"] == 0.3, r.miss)
    check(r.corrections() == [], "nothing to correct yet")
    kill(12118, "Lucifron")                               # (another creature: not this fact's)
    check(r.miss["i:18842"]["11988"] == [7, 0], r.miss)

    # ---- the 8th: halved and looked up again; elsewhere now -> corrected once ---------------------------------------
    llm.answers[18842] = [answer("Ragnaros")]
    tick(research.KILL_DEDUP_S)
    kill(11988, "Golemagg the Incinerator")
    check(llm.asked == [18842, 18842], ("looked up again", llm.asked))
    f = r.facts(18842)
    check([(s["npc_name"], s["src"]) for s in f["s"]] == [("Ragnaros", "web")] and f["s"][0]["npc"] == 11502, f)
    check(r.items_section()[18842]["t"] == "drops from Ragnaros, Molten Core" and r.items_section()[18842]["w"] == 1,
          r.items_section()[18842])
    fix = {"item": "Staff of Dominance", "old": "drops from Golemagg the Incinerator, Molten Core",
           "new": "drops from Ragnaros, Molten Core (web, unconfirmed)"}

    # (saved: a restart before the coach said it still says it)
    r = worker()
    check(r.told["i:18842"].get("fix") == fix, r.told)

    # ---- the rules: fact_corrected once, out of combat; its topic and the coach's line without a model -----------
    clock_r = [1000000.0]
    rules_mod.time = types.SimpleNamespace(time=lambda: clock_r[0])
    gs, rl = GameState(), Rules({})
    rl.research = r

    def frame(combat=0, dead=0):
        clock_r[0] += 6
        gs.update({"t": str(int(clock_r[0]) % 1000000), "l": "60", "x": "1", "X": "100", "c": str(combat),
                   "d": str(dead)}, {}, now=clock_r[0])
        return [t for t in rl.check(gs, []) if t.kind == "fact_corrected"]
    check(frame(combat=1) == [] and frame(dead=1) == [], "not in combat or dead")
    got = frame()
    check(len(got) == 1 and got[0].facts == fix and got[0].priority == 3, got)
    check(frame() == [] and "fix" not in str(r.told) and r.corrections() == [], "said once")
    check(rules_mod.CATEGORY["fact_corrected"] == "upkeep" and not rules_mod.allowed("fact_corrected", {"upkeep": False})
          and prompt_context.TRIGGER_TOPIC["fact_corrected"] == "gear", "an upkeep callout about gear")
    line = offline_coach.callout([Trigger("fact_corrected", fix, 3)], {"player": {}})
    check(line == "I had Staff of Dominance wrong: drops from Ragnaros, Molten Core (web, unconfirmed).", line)
    check(offline_coach.fact_corrected_line({"item": "Mana Igniting Cord", "new": research.NO_SOURCE_YET}) ==
          "I had Mana Igniting Cord wrong: no known source yet.", "nothing known now")

    # a research worker that fails doesn't break the rules
    class Broken:
        def corrections(self):
            raise OSError("facts.json")
    rl.research = Broken()
    check(frame() == [], "a broken worker")
    rules_mod.time = real_rules_time

    # ---- doubted again: the same answer says nothing (the conf stays halved); 16 kills halve it twice -------------
    llm.answers[18842] = [answer("Ragnaros"), answer("Ragnaros")]
    r.item_info("Staff of Dominance")                     # (the coach says the Ragnaros fact now)
    for _ in range(8):
        tick(research.KILL_DEDUP_S)
        kill(11502, "Ragnaros")
    check(r.miss["i:18842"]["11502"] == [8, 1] and r.facts(18842)["s"][0]["conf"] == 0.15, r.facts(18842))
    check(r.corrections() == [] and r.told["i:18842"]["npc"] == 11502, "the same place: not corrected")
    for _ in range(8):
        tick(research.KILL_DEDUP_S)
        kill(11502, "Ragnaros")
    check(r.miss["i:18842"]["11502"] == [16, 2] and r.facts(18842)["s"][0]["conf"] == 0.075, r.facts(18842))
    check(llm.asked.count(18842) == 4, llm.asked)

    # ---- a drop the player sees confirms it: the observed source leads, the doubts go ------------------------------
    tick(research.KILL_DEDUP_S)
    kill(11502, "Ragnaros", loot=18842)
    f = r.facts(18842)
    check(f["s"][0]["src"] == "obs" and f["s"][0]["conf"] == 0.95 and f["src"] == "obs", f)
    check("11502" not in r.miss.get("i:18842", {}) and r.items_section()[18842]["w"] == 0, r.miss)

    # ---- the knowledge base's observed kills: Lucifron looted 9 times, never the spaulders ------------------------
    llm.answers[18829] = [answer("Lucifron", iid=18829), answer("Garr", iid=18829)]
    r.ask("i:18829", "question")
    tick()
    check(r.miss["i:18829"]["12118"] == [9, 1], ("9 looted kills in the knowledge base", r.miss.get("i:18829")))
    tick()
    check(llm.asked.count(18829) == 2 and r.facts(18829)["s"][0]["npc_name"] == "Garr", r.facts(18829))
    check(r.corrections() == [], "the coach never said it: nothing to correct")

    # the web finding nothing the next time, with no kill against it: not a sign the coach was wrong
    r.item_info("Deep Earth Spaulders")                   # (the coach says the Garr fact)
    check(r.told["i:18829"]["who"] == "garr", r.told.get("i:18829"))
    llm.answers[18829] = [nothing()]
    r.web_found["i:18829"]["t"] -= research.TTL_S         # (a month on: looked up again)
    r.ask("i:18829", "question")
    tick()
    check(llm.asked.count(18829) == 3 and "i:18829" in r.web_neg and r.corrections() == [], "not corrected")

    # ---- a creature known only by its name; nothing found after the doubt: "no known source yet" ----------------
    llm.answers[19136] = [answer("Shazzrah", iid=19136), nothing()]
    r.ask("i:19136", "question")
    tick()
    check(r.facts(19136)["s"][0]["npc"] is None and r.facts(19136)["s"][0]["npc_name"] == "Shazzrah", r.facts(19136))
    r.item_info("Mana Igniting Cord")
    for _ in range(8):
        tick(research.KILL_DEDUP_S)
        kill(12264, "Shazzrah")
    check(r.miss.get("i:19136") == {"n:shazzrah": [8, 1]} or "i:19136" not in r.web_found, r.miss.get("i:19136"))
    # (a web fact's creature by its name only still "drops from": item_text.source_text)
    got = r.corrections()
    check(got == [{"item": "Mana Igniting Cord", "old": "drops from Shazzrah, Molten Core",
                   "new": research.NO_SOURCE_YET}], ("nothing known now", got))
    check(r.facts(19136) is None and 19136 not in r.items_section(), "silent on the tooltip")

    # an item the player's loot observation knows isn't looked up on the web, even when asked (Baron Geddon's talisman)
    llm.answers[18820] = [answer("Baron Geddon", iid=18820)]
    r.ask("i:18820", "question")
    tick()
    check(llm.asked.count(18820) == 0 and "i:18820" not in r.web_q and r.facts(18820)["src"] == "obs",
          ("known offline", r.facts(18820)))
    # a web fact the loot observation confirms isn't doubted (Geddon looted 9 times, the talisman among his drops)
    r.web_found["i:18820"] = {"s": [research.web_src(json.loads(answer("Baron Geddon", iid=18820)[0])["facts"][0],
                                                     "i:18820", [WOWHEAD % 18820])], "t": clock[0], "ttl": research.TTL_S}
    r.web_found["i:18820"]["s"][0]["npc"] = 12056
    r.found.pop("i:18820")
    r.enqueue("i:18820", "test", research.PRIO_NOW)
    tick()
    check(not r.miss.get("i:18820") and r.facts(18820)["s"][0]["src"] == "obs" and "i:18820" not in r.web_q,
          (r.miss.get("i:18820"), r.facts(18820)))

    # ==== only the game shows a web fact wrong; a page shortening a creature's name names the same creature ==========
    home2 = os.path.join(tmp, "engine2")
    pack2 = FakePack()
    pack2.kb.c.execute("""INSERT INTO creature VALUES (9019, 'Emperor Dagran Thaurissan', NULL),
                          (15775, 'Christmas Emperor Dagran Thaurissan', NULL)""")
    budget2 = research_budget.Budget(home2, lambda: {"web": True, "web_per_day": 50, "usd_per_day": 5.0},
                                     now=lambda: clock[0])
    r = research.Research(home2, lambda: db, lambda: pack2, lambda: st, lambda: True, now=lambda: clock[0])
    r.set_web(research.WebResolver(lambda: llm, budget2, now=lambda: clock[0]))
    r.spawn = lambda fn: fn()

    # a month on, no kill against it: another page saying elsewhere is no correction
    llm.answers[18842] = [answer("Golemagg the Incinerator")]
    r.ask("i:18842", "question")
    tick()
    r.item_info("Staff of Dominance")
    check(r.told["i:18842"]["npc"] == 11988, r.told)
    llm.answers[18842] = [answer("Ragnaros")]
    r.web_found["i:18842"]["t"] -= research.TTL_S
    r.ask("i:18842", "question")
    tick()
    check(r.facts(18842)["s"][0]["npc_name"] == "Ragnaros" and r.corrections() == [], ("one page against another",
                                                                                         r.told))

    # 8 kills of Golemagg, and the new lookup names him "Golemagg": his id, the same boss, no correction, doubt kept
    llm.answers[18829] = [answer("Golemagg the Incinerator", iid=18829), answer("Golemagg", iid=18829)]
    asked0 = llm.asked.count(18829)
    r.ask("i:18829", "question")
    tick()
    r.item_info("Deep Earth Spaulders")
    for _ in range(8):
        tick(research.KILL_DEDUP_S)
        kill(11988, "Golemagg the Incinerator")
    f = r.facts(18829)
    check(llm.asked.count(18829) == asked0 + 2 and f["s"][0]["npc"] == 11988 and f["s"][0]["npc_name"] == "Golemagg", f)
    check(r.miss["i:18829"]["11988"] == [8, 1] and f["s"][0]["conf"] == 0.15, (r.miss.get("i:18829"), f))
    check(r.corrections() == [], "the same boss, by a shorter name")
    check(research._same_place({"kind": "drop", "npc": None, "npc_name": "Golemagg"},
                               {"kind": "drop", "npc": 11988, "who": "golemagg the incinerator"}) and
          not research._same_place({"kind": "drop", "npc": None, "npc_name": "Ragnaros"},
                                   {"kind": "drop", "npc": 11988, "who": "golemagg the incinerator"}), "_same_place")
    # a name it could be short for twice over stays a name
    s = r._with_npc(research.make_src("drop", "web", npc_name="Emperor Thaurissan", zone_name="Blackrock Depths"),
                    pack2)
    check(s["npc"] is None, ("two creatures it could be: by its name only", s))

    # a web fact naming a creature by a shorter name the knowledge base doesn't know: its kills still count against it
    llm.answers[19136] = [answer("Gehennas", iid=19136), answer("Gehennas", iid=19136)]
    asked0 = llm.asked.count(19136)
    r.ask("i:19136", "question")
    tick()
    check(r.facts(19136)["s"][0]["npc"] is None, r.facts(19136))
    for _ in range(8):
        tick(research.KILL_DEDUP_S)
        kill(12259, "Gehennas the Fallen")
    check(r.miss.get("i:19136") == {"n:gehennas": [8, 1]} and llm.asked.count(19136) == asked0 + 2,
          r.miss.get("i:19136"))

    # a correction the rules haven't said yet (in combat) survives the coach answering about the item again meanwhile
    r.item_info("Staff of Dominance")                    # (the coach says the Ragnaros fact)
    llm.answers[18842] = [answer("Garr")]
    for _ in range(8):
        tick(research.KILL_DEDUP_S)
        kill(11502, "Ragnaros")
    check(r.told["i:18842"].get("fix"), r.told["i:18842"])
    info = r.item_info("Staff of Dominance")             # ("where does the staff drop?", still in combat)
    check(info["sources"] == ["drops from Garr, Molten Core (web, unconfirmed)"], info)
    got = r.corrections()
    check(got == [{"item": "Staff of Dominance", "old": "drops from Ragnaros, Molten Core",
                   "new": "drops from Garr, Molten Core (web, unconfirmed)"}], got)
    check(r.told["i:18842"]["text"] == "drops from Garr, Molten Core" and "fix" not in r.told["i:18842"] and
          r.corrections() == [], ("what the coach said since is kept", r.told.get("i:18842")))
finally:
    rules_mod.time = real_rules_time
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
