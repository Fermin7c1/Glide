#!/usr/bin/env python3
"""Quest chains on the Guides page (desktop/src/renderer/rxp-quests.ts, compiled with the project's TypeScript into a
temp folder and run under Node): each quest's steps read from the guide (the same answer as the engine's
knowledge/api/explorer.py quest_steps, so the page lights the right steps when the facts haven't come), the chain a
quest belongs to through the knowledge base's follow-ups (back and forward, cycle-safe, only the guide's quests), the
chain's steps in step order, where the chain crosses into another zone, and what a skip loses in the coach's words
(nothing said without the knowledge base).

Needs node and desktop/node_modules (skips with 77 without them)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DESK = os.path.join(ROOT, "desktop")
TSC = os.path.join(DESK, "node_modules", "typescript", "bin", "tsc")
SRC = os.path.join(DESK, "src", "renderer", "rxp-quests.ts")
sys.path.insert(0, ROOT)
for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY"):
    os.environ[k] = ""

node = shutil.which("node")
if not node or not os.path.exists(TSC):
    print("SKIP: needs node and desktop/node_modules (cd desktop && npm ci)")
    sys.exit(77)

from knowledge.api import explorer as X  # noqa: E402
from knowledge.rxp_parser.guide import parse_guide  # noqa: E402

GUIDE = """#group Test Guide (A)
#name 1-3 Testvale
step
    .goto Elwynn Forest,50,50
    .accept 100 >>Accept Wolves
    .accept 200 >>Accept Letters
step
    >>Kill wolves
    .complete 100,1
    .collect 600,5,100 --Wolf Fang
    .collect 700,2 --a plain collect is prep
step
    .turnin 100 >>Turn in Wolves
    .accept 101 >>Accept More Wolves
    .isOnQuest 200
step
    .turninmultiple 101,200
"""
parsed = parse_guide(GUIDE)
# the guide as the page gets it: steps with their actions (verb, quest_id); the engine's answer without a kb
sys.path.append(os.path.join(ROOT, "companion"))
import rxp_import as R  # noqa: E402
home = tempfile.mkdtemp(prefix="glide-rxp-quests-home-")
try:
    os.makedirs(os.path.dirname(os.path.join(home, R.STORE)))
    with open(os.path.join(home, R.STORE), "w", encoding="utf-8") as fh:
        json.dump({"imports": {"abc": {"added": 1, "guides": [{"group": "Test Guide (A)", "name": "1-3 Testvale",
                                                               "text": GUIDE}]}}}, fh)
    STEPS = R.guide_detail(home, "abc", 0)["steps"]          # the guide exactly as the page gets it
finally:
    shutil.rmtree(home, ignore_errors=True)
ENGINE = {q: r["steps"] for q, r in X.guide_facts(None, parsed)["quests"].items()}


def q(steps, **kw):
    return dict({"steps": dict({"accept": [], "objective": [], "turnin": [], "gate": []}, **steps)}, **kw)


# a chain Wolves 100 -> More Wolves 101 -> Den 102 (next guide holds 103); 300 also names 101 as its next but
# isn't 101's way in; 400 -> 401 -> 400 is a broken loop in the data
QUESTS = {
    "100": q({"accept": [1], "objective": [2], "turnin": [3]}, name="Wolves", next=101, prev=[],
             lost_if_skipped=[{"id": 101, "name": "More Wolves", "accept": 3, "next_guide": False},
                              {"id": 103, "name": "Den Mother", "accept": None, "next_guide": True}]),
    "101": q({"accept": [3], "turnin": [9]}, name="More Wolves", next=102, prev=[300, 100], lost_if_skipped=[]),
    "102": q({"accept": [9], "objective": [12], "turnin": [14]}, name="Den", next=103, prev=[101]),
    "300": q({"accept": [5]}, name="Side", next=101, prev=[], lost_if_skipped=[
        {"id": i, "name": "Q%d" % i, "accept": 20 + i, "next_guide": False} for i in range(1, 7)]),
    "400": q({"accept": [6]}, name="Loop A", next=401, prev=[401]),
    "401": q({"accept": [7]}, name="Loop B", next=400, prev=[400]),
    "500": q({"turnin": [8]}, name="From before"),
}
CALLS = {
    "local": "localQuests(steps)",
    "fallback": "Object.keys(questsOf(null, steps))",
    "fromFacts": "Object.keys(questsOf({quests: quests}, steps))",
    "chainMid": "chainOf(quests, 101)",
    "chainEnd": "chainOf(quests, 102)",
    "chainLoop": "chainOf(quests, 400)",
    "chainAlone": "chainOf(localQuests(steps), 100)",
    "chainUnknown": "chainOf(quests, 999)",
    "after": "[continuesAfter(quests, chainOf(quests, 100)), continuesAfter(quests, [400, 401])]",
    "stops": "chainStops(quests, chainOf(quests, 100))",
    "cross": "chainCrossings([{src: 1, um: 10}, {src: 2, um: 10}, {src: 3, um: 20}, {src: 4, um: 10}, {src: 5, um: null}], 10)",
    "skip": "skipLine(quests, 100, (s) => s === 3 ? '4' : null)",
    "skipNone": "skipLine(quests, 101, () => '1')",
    "skipMany": "skipLine(quests, 300, (s) => String(s))",
    "skipNoKb": "skipLine(localQuests(steps), 100, () => '1')",
    "skipNotHere": "skipLine(quests, 500, () => '1')",
}

tmp = tempfile.mkdtemp(prefix="glide-rxp-quests-")
try:
    r = subprocess.run([node, TSC, SRC, "--target", "ES2023", "--module", "ES2022", "--strict", "--skipLibCheck",
                        "--rootDir", os.path.join(DESK, "src"), "--outDir", tmp], capture_output=True, text=True,
                       encoding="utf-8", timeout=120)
    assert r.returncode == 0, "tsc failed on rxp-quests.ts: %s" % (r.stdout + r.stderr)[-2000:]
    with open(os.path.join(tmp, "package.json"), "w") as fh:
        fh.write('{"type": "module"}')
    mod = os.path.join(tmp, "renderer", "rxp-quests.js")
    body = "const steps = %s, quests = %s;\nconst out = {};\n%s\nconsole.log(JSON.stringify(out));" % (
        json.dumps(STEPS), json.dumps(QUESTS), "\n".join("out[%s] = %s;" % (json.dumps(k), v) for k, v in CALLS.items()))
    script = os.path.join(tmp, "run.js")
    with open(script, "w") as fh:
        fh.write("import { localQuests, questsOf, chainOf, continuesAfter, chainStops, chainCrossings, skipLine } "
                 "from %s;\n%s" % (json.dumps("file:///" + mod.replace("\\", "/").lstrip("/")), body))
    r = subprocess.run([node, script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, "running rxp-quests failed: %s" % r.stderr[-2000:]
    got = json.loads(r.stdout)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# read from the guide: the engine's own answer (gates aside: the page doesn't light a gate), a plain collect left out
local = {k: v["steps"] for k, v in got["local"].items()}
assert local == {k: dict(v, gate=[]) for k, v in ENGINE.items()}, (local, ENGINE)
# Regression: a multi-quest turn-in (.turninmultiple 101,200) lit only its first quest; the page now gets every one
assert local["101"]["turnin"] == [4] and local["200"]["turnin"] == [4], local
assert got["fallback"] == sorted(ENGINE, key=int) and got["fromFacts"] == list(QUESTS), got["fallback"]
# the chain: back through the quest whose follow-up this is (not every quest naming it), forward through next_quest
assert got["chainMid"] == [100, 101, 102] and got["chainEnd"] == [100, 101, 102], got["chainMid"]
assert sorted(got["chainLoop"]) == [400, 401] and len(got["chainLoop"]) == 2, got["chainLoop"]
assert got["chainAlone"] == [100] and got["chainUnknown"] == [999], (got["chainAlone"], got["chainUnknown"])
assert got["after"] == [True, False], got["after"]
# its steps in order, one per step; a step that turns one in and picks up the next says the turn-in
assert got["stops"] == [{"src": 1, "quest": 100, "role": "accept"}, {"src": 2, "quest": 100, "role": "objective"},
                        {"src": 3, "quest": 100, "role": "turnin"}, {"src": 9, "quest": 101, "role": "turnin"},
                        {"src": 12, "quest": 102, "role": "objective"}, {"src": 14, "quest": 102, "role": "turnin"}], got["stops"]
# from map 10: step 2 goes on to map 20, step 4 comes back from it; a step with no map is no crossing
assert got["cross"] == [{"at": 2, "dir": "to", "um": 20}, {"at": 4, "dir": "from", "um": 20}], got["cross"]
# what a skip loses: the coach's list with steps, the next guide's named as such; nothing said when it can't be
assert got["skip"] == "Skip it and you lose More Wolves (step 4) and Den Mother (next guide).", got["skip"]
assert got["skipNone"] == "Skipping it loses nothing later in this guide or the next.", got["skipNone"]
assert got["skipMany"] == "Skip it and you lose Q1 (step 21), Q2 (step 22), Q3 (step 23) and 3 more.", got["skipMany"]
assert got["skipNoKb"] is None and got["skipNotHere"] is None, (got["skipNoKb"], got["skipNotHere"])
print("rxp-quests: ok")
