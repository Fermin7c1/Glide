#!/usr/bin/env python3
"""WoW Forever's quest log holds 40 quests (Classic's held 20; community note n:20261021-quest-log-40): the quest-log
heuristics, the glossary and the evals say 40, and the model sees a whole 40-quest log and how full it is.

Run: python3 tests/test_quest_log_cap.py   (prints OK)
"""
import json
import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
from glidedb import GlideDB  # noqa: E402
import coach  # noqa: E402
import prompt_context  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + what)


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as fh:
        return fh.read()


rules = {r["id"]: r for r in json.loads(read("knowledge", "heuristics", "rules.json"))}
check(rules["route.quest-log-near-cap"]["applies"]["quest_log_count"] == {">=": 38}, "nearly full at 38 of 40")
check(rules["route.quest-log-full"]["applies"]["quest_log_count"] == {">=": 40}, "full at 40")
glossary = json.loads(read("knowledge", "glossary.json"))
terms = glossary if isinstance(glossary, list) else glossary.get("terms", glossary)
cap = next(t for t in terms if t.get("term") == "quest log cap")
check("40" in cap["definition"] and "Forever" in cap["definition"], cap["definition"])
evals = [json.loads(line) for line in read("knowledge", "eval", "questions.jsonl").splitlines() if line.strip()]
for e in evals:
    if "quest log" in e["question"].lower():
        check(not any("caps the log at 20" in f or "unknown on Forever" in f for f in e["gold"]["facts"]),
              "eval %s still expects the Classic cap" % e["id"])

# a 30-quest log reaches the model whole (it used to be cut at 20), with how full it is
check(prompt_context.QUEST_LOG_CAP == 40, "the cap")
c = coach.Coach({}, GlideDB("/x.db"), lambda m: None, offline_ok=True)
s = {"quests": {1000 + i: {"done": False, "obj": [[0, 1]]} for i in range(30)}, "steps": []}
ctx = prompt_context.game_context(s, None, c.k, c.geo, None)
check(len(ctx["quests"]) == 30, "all 30 quests are in the context: %d" % len(ctx["quests"]))
check(ctx["player"]["quest_log"] == "30 of 40 quests", ctx["player"].get("quest_log"))
print("OK")
