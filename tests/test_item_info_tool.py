#!/usr/bin/env python3
"""The coach's item_info tool (companion/coach_tools.py) over the engine's research (research.Research, faked here):
offered only while the research runs, run as one of the coach's own tools, an empty or unknown request answered with
an error; "where does X drop?" and the like recognised, and answered from the research facts while the AI is off
("<item>: <where it comes from>."; one being looked up on the web says so); "this", "that" or "it" in such a question
is the item the player hovers in the game (none hovered: not an item question); and grounding takes the research's fact ids (ws:..., res:...) the way the coach
takes web: ones (knowledge/api/facts.py).

Run: python3 tests/test_item_info_tool.py   (prints OK)
"""
import os
import sys
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.append(os.path.join(ROOT, "companion"))      # after ROOT: `knowledge` stays the package
import coach_tools  # noqa: E402
from knowledge.api.facts import FactsMixin  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


class FakeResearch:
    def __init__(self):
        self.asked = []

    def item_info(self, query):
        self.asked.append(query)
        q = query.lower().replace("'", "")
        if q in ("magisters crown", "16686", "#16686"):
            return {"id": 16686, "name": "Magister's Crown", "avail": "now", "fact_ids": ["ws:i:16686"],
                    "sources": ["drops from Darkmaster Gandling, Scholomance", "sold by nobody"]}
        if q == "staff of hale magefire":
            return {"id": 13000, "name": "Staff of Hale Magefire", "sources": [], "avail": "nosrc", "fact_ids": []}
        if q == "staff of dominance":                 # (nothing offline knows it: being looked up on the web)
            return {"id": 18842, "name": "Staff of Dominance", "sources": [], "avail": "nosrc", "fact_ids": [],
                    "looking_up": True}
        return {"id": None, "name": query, "sources": [], "avail": "nosrc", "fact_ids": [], "error": "unknown"}


saved = coach_tools.research
try:
    # ---- offered while the research runs, and run by the coach itself ----------------------------------------------
    coach_tools.research = None
    check(coach_tools.item_tools() == [], "nothing to offer without the research")
    check(coach_tools.run_item_tool("item_info", {"item": "x"}).get("error"), "not available")
    check(coach_tools.item_command("where does Magister's Crown drop?", False) is None, "no research: no answer")
    fake = coach_tools.research = FakeResearch()
    defs = coach_tools.item_tools()
    check([d["name"] for d in defs] == ["item_info"] and defs[0]["input_schema"]["required"] == ["item"], defs)
    check("item_info" in coach_tools.LOCAL_TOOLS, "a local tool, not the knowledge layer's")
    check(all(d["name"] in coach_tools.ITEM_TOOL_NAMES for d in defs), coach_tools.ITEM_TOOL_NAMES)
    got = coach_tools.run_item_tool("item_info", {"item": " Magister's Crown "})
    check(got["id"] == 16686 and got["fact_ids"] == ["ws:i:16686"] and fake.asked[-1] == "Magister's Crown", got)
    check(coach_tools.run_item_tool("item_info", {}).get("error") and
          coach_tools.run_item_tool("item_other", {"item": "x"}).get("error"), "bad requests")

    # ---- the questions that ask where an item comes from ---------------------------------------------------------
    for q, want in (("Where does Magister's Crown drop?", "Magister's Crown"),
                    ("where does the Magister's Crown come from", "Magister's Crown"),
                    ("Where can I get Robe of the Archmage?", "Robe of the Archmage"),
                    ("where do i find a Nacreous Shell Necklace", "Nacreous Shell Necklace"),
                    ("Who sells Elixir of the Mongoose?", "Elixir of the Mongoose"),
                    ("what drops Neltharion's Tear", "Neltharion's Tear"),
                    ("How do I get Belt of the Archmage?", "Belt of the Archmage"),
                    ("how to get Shard of the Defiler", "Shard of the Defiler"),
                    ("where is Magister's Crown sold", "Magister's Crown")):
        check(coach_tools.item_question(q) == want, (q, coach_tools.item_question(q)))
    for q in ("where am I", "what should I do next", "how much xp do I need", "where does this quest start",
              "drops", ""):
        check(coach_tools.item_question(q) is None, (q, coach_tools.item_question(q)))

    # ---- answered from the facts while the AI is off; with it, the model asks the tool ----------------------------
    check(coach_tools.item_command("Where does Magister's Crown drop?", True) is None, "the AI answers")
    check(coach_tools.item_command("Where does Magister's Crown drop?", False) ==
          "Magister's Crown: drops from Darkmaster Gandling, Scholomance.", fake.asked)
    check(coach_tools.item_command("How do I get Staff of Hale Magefire?", False) ==
          "I don't know where Staff of Hale Magefire comes from yet.", "no source yet")
    check(coach_tools.item_command("where does Sword of Nowhere drop", False) is None, "an unknown item: not mine")
    check(coach_tools.item_command("what should I do next", False) is None, "not an item question")
    # R2: nothing offline knows it, and the web is being asked: say so (the model hears it from item_info's looking_up)
    check(coach_tools.item_command("Where does Staff of Dominance drop?", False) ==
          "I don't know where Staff of Dominance comes from yet; I'm looking it up, ask me again in a minute.",
          "looking it up")

    # ---- "where does this drop?": the item the player hovers in the game (GameState.hovered) ----------------------
    for q in ("Where does this drop?", "who sells that item", "how do I get it", "where does this one come from"):
        check(coach_tools.item_question(q) is None, ("nothing hovered: not an item", q))
        check(coach_tools.item_question(q, 16686) == "#16686", (q, coach_tools.item_question(q, 16686)))
    check(coach_tools.item_question("Where does Magister's Crown drop?", 2361) == "Magister's Crown", "named: that one")
    check(coach_tools.item_question("where does this quest start", 16686) is None, "not an item question")
    check(coach_tools.item_command("Where does this drop?", False, 16686) ==
          "Magister's Crown: drops from Darkmaster Gandling, Scholomance.", fake.asked[-1])
    check(coach_tools.item_command("Where does this drop?", False) is None, "nothing hovered: the coach's own answer")
    check(coach_tools.hovered_id(None) is None, "no game state")
    check(coach_tools.hovered_id(types.SimpleNamespace(hovered=lambda: {"id": 16686, "t": 1.0})) == 16686 and
          coach_tools.hovered_id(types.SimpleNamespace(hovered=lambda: None)) is None, "GameState.hovered")

    def broken():
        raise RuntimeError("x")
    check(coach_tools.hovered_id(types.SimpleNamespace(hovered=broken)) is None, "a failing read: none")

    # ---- grounding: the research's fact ids are taken as they come (like web:), even without kb.db -----------------
    class NoKB(FactsMixin):
        ok = False
    p = NoKB()
    for fid in ("ws:i:16686", "res:i:2000", "WS:i:16686", "res:q:5102"):
        check(p.fact_exists(fid), fid)
    for fid in ("ws:", "ws:16686", "res:i:", "ws:i:16 686", "web:wowhead.com", "i:16686", "xx:i:1", None):
        check(not p.fact_exists(fid), fid)
    known, unknown = p.verify_facts(["ws:i:16686", "q:5102"])
    check(known == ["ws:i:16686"] and unknown == ["q:5102"], (known, unknown))
finally:
    coach_tools.research = saved
print("OK")
