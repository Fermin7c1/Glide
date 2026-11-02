#!/usr/bin/env python3
"""Time, XP and hazards on the Guides page (desktop/src/renderer/rxp-plan.ts, compiled with the project's TypeScript
once for all the tests (desktop_ts.py) and run under Node): the time and XP between two listed steps (either way), the
next level-up ("45 min to 23"), durations said roughly, the dangerous stretches of the guide (a hazard or the coach's
risk, one safe step joining two runs, the hazards met most first), and a hazard said in a line with RestedXP's note (the
engine's "elite / Patrols the road / hits hard").

Needs node and desktop/node_modules (skips with 77 without them)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import desktop_ts

HERE = os.path.dirname(os.path.abspath(__file__))
DESK = os.path.join(HERE, "..", "desktop")
SRC = os.path.join(DESK, "src", "renderer", "rxp-plan.ts")

node = desktop_ts.node
if not node or not desktop_ts.available():
    print("SKIP: needs node and desktop/node_modules (cd desktop && npm ci)")
    sys.exit(77)


def ps(t, xp=0, l0=None, l1=None, risk=0.1):
    return {"n": None, "t0": None, "time_s": t, "travel_s": 0, "kill_s": 0, "xp_now": xp, "xp_unlocked": 0, "lvl0": l0,
            "lvl1": l1, "kind": None, "risk": risk, "skip": None, "why": [], "leg": None, "quests_xp": {}}


# the listed steps (st.idx) in list order; step 4 isn't in the plan (another version of the guide)
ORDER = [1, 2, 3, 4, 5, 6, 7, 8]
PLAN = {"1": ps(60, 0, 20.5, 20.6), "2": ps(600, 1200, 20.6, 20.9), "3": ps(900, 800, 20.9, 21.2, risk=0.7),
        "5": ps(1200, 1500, 21.2, 21.6), "6": ps(1500, 2000, 21.6, 22.1), "7": ps(30, 0, 22.1, 22.1), "8": ps(30, 0, 22.1, 22.2)}
HOGGER = {"name": "Hogger", "creature_id": 448, "level": 11, "note": "elite / Patrols the road / calls for help",
          "elite": True, "dist_yd": 90.4, "why": "risk"}
TARGET = {"name": "Gruff", "creature_id": 9, "level": 8, "note": "rare elite", "elite": True, "dist_yd": None, "why": "kill"}
QUEST = {"name": "Riverpaw Bounty", "creature_id": None, "quest_id": 11, "level": 12, "note": "group of 3", "elite": False,
         "dist_yd": None, "why": "quest"}
HAZ = {"2": [HOGGER], "5": [HOGGER, TARGET], "8": [QUEST]}
CALLS = {
    "dur": "[roughDur(20), roughDur(60), roughDur(8 * 60 + 20), roughDur(70 * 60), roughDur(3 * 3600), roughDur(NaN)]",
    "span": "span(plan, order, 0, 4)",
    "spanBack": "spanText(span(plan, order, 4, 0), true)",
    "spanFwd": "spanText(span(plan, order, 0, 4))",
    "spanXp": "spanText(span(plan, order, 0, 1))",
    "spanNoXp": "spanText(span(plan, order, 6, 7))",
    "spanNone": "[span(plan, order, 2, 2), span({}, order, 0, 3)]",
    "ding": "nextDing(plan, order, 0)",
    "dingMid": "dingText(nextDing(plan, order, 4))",
    "dingEnd": "[nextDing(plan, order, 7), dingText(null)]",
    "lines": "[hogger, target, quest].map(hazardLine)",
    "where": "[hogger, target, quest].map(hazardWhere)",
    "bare": "hazardLine({name: null, creature_id: null, level: null, note: null, elite: true, dist_yd: null, why: 'near'})",
    "danger": "isDangerous(3, haz, plan) && !isDangerous(3, haz, null) && isDangerous(2, haz, null)",
    "runs": "dangerRuns(order, haz, plan).map((r) => [r.from, r.to, r.hz.map((h) => h.name)])",
    "runsNoPlan": "dangerRuns(order, haz, null).map((r) => [r.from, r.to])",
}

tmp = tempfile.mkdtemp(prefix="glide-rxp-plan-")
try:
    mod = os.path.join(desktop_ts.renderer(), "renderer", "rxp-plan.js")     # (the tests' shared compile)
    body = "const order = %s, plan = %s, haz = %s, hogger = %s, target = %s, quest = %s;\nconst out = {};\n%s\n" \
           "console.log(JSON.stringify(out));" % (
               json.dumps(ORDER), json.dumps(PLAN), json.dumps(HAZ), json.dumps(HOGGER), json.dumps(TARGET),
               json.dumps(QUEST), "\n".join("out[%s] = %s;" % (json.dumps(k), v) for k, v in CALLS.items()))
    script = os.path.join(tmp, "run.mjs")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write("import { roughDur, span, spanText, nextDing, dingText, hazardLine, hazardWhere, isDangerous, "
                 "dangerRuns } from %s;\n%s" % (json.dumps(desktop_ts.url(mod)), body))
    r = subprocess.run([node, script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, "running rxp-plan failed: %s" % r.stderr[-2000:]
    got = json.loads(r.stdout)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

assert got["dur"] == ["under a minute", "1 min", "8 min", "1 h 10 min", "3 h", "under a minute"], got["dur"]
# between list positions 0 and 4: the steps after the first up to the fifth (step 4 has no plan row), the level on
# leaving step 1 and on finishing step 5
assert got["span"] == {"s": 2700, "xp": 3500, "lvlA": 20.6, "lvlB": 21.6, "steps": 4}, got["span"]
assert got["spanFwd"] == "+45 min · level 20 → 21" and got["spanBack"] == "−45 min · level 21 → 20", (got["spanFwd"], got["spanBack"])
assert got["spanXp"] == "+10 min · +1,200 XP" and got["spanNoXp"] == "under a minute", (got["spanXp"], got["spanNoXp"])
assert got["spanNone"] == [None, None], got["spanNone"]
# the next level: 21 is reached at list position 2 (step 3), 25 minutes on
assert got["ding"] == {"level": 21, "pos": 2, "s": 1500}, got["ding"]
assert got["dingMid"] == "25 min to 22" and got["dingEnd"] == [None, ""], (got["dingMid"], got["dingEnd"])
assert got["lines"] == ["Hogger, level 11 elite: patrols the road, calls for help", "Gruff, level 8 rare elite",
                        "Riverpaw Bounty, level 12: group of 3"], got["lines"]
assert got["where"] == ["Within 90 yd of the way", "The step's own target", "The quest itself"], got["where"]
assert got["bare"] == "Something dangerous elite", got["bare"]
assert got["danger"] is True, got["danger"]
# steps 2-3 (a hazard, then the coach's risk) and 5 join over the safe step 4; step 8 stands alone; Hogger met most
assert got["runs"] == [[1, 4, ["Hogger", "Gruff"]], [7, 7, ["Riverpaw Bounty"]]], got["runs"]
assert got["runsNoPlan"] == [[1, 1], [4, 4], [7, 7]], got["runsNoPlan"]
print("rxp-plan: ok")
