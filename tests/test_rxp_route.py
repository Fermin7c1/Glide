#!/usr/bin/env python3
"""The route on the Guides map (desktop/src/renderer/rxp-route.ts with rxp-geometry.ts, compiled with the project's
TypeScript once for all the tests (desktop_ts.py) and run under Node): where a step's pin goes, the legs between stops
(the same plan as the engine's routes command, companion/rxp_routes.py), a leg's drawable pieces on one map, clipping at
the map's edge, the words of a leg's card, the outline of an objective cluster, the timeline's tick layout and how faint
a step ahead is drawn.

Regressions: a step's pin sat on its first goto, often one of RestedXP's hidden path points rather than where the
step happens; the timeline's glyph ticks piled on top of each other in a busy hub.

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
SRC = os.path.join(DESK, "src", "renderer", "rxp-route.ts")

node = desktop_ts.node
if not node or not desktop_ts.available():
    print("SKIP: needs node and desktop/node_modules (cd desktop && npm ci)")
    sys.exit(77)


def wp(x, y, mode="wp", um=1411):
    return {"x": x, "y": y, "zone": "Durotar", "ui_map": um, "mode": mode}


def step(idx, wps=(), verbs=(), x=None, y=None, um=None):
    return {"idx": idx, "line": idx, "text": None, "comments": None, "cond": None, "flags": {}, "zone": None,
            "ui_map": um, "x": x, "y": y, "waypoints": list(wps), "loops": [],
            "actions": [{"verb": v, "line": idx} for v in verbs]}


MAPS = {"1411": {"name": "Durotar", "parent": 1414, "w_yd": 5250, "h_yd": 3500, "rect": [60, 45, 75, 60]},
        "1413": {"name": "The Barrens", "parent": 1414, "w_yd": 10500, "h_yd": 7000, "rect": [45, 45, 60, 75]},
        "1414": {"name": "Kalimdor", "parent": None, "w_yd": 36000, "h_yd": 24000, "rect": None}}
STEPS = [
    step(1, [wp(10, 10)]),
    # path points first, then the place the step happens (its pin), then another stop; a dynamic goto is not a stop
    step(2, [wp(20, 10, "path"), wp(30, 10, "path"), wp(40, 10), wp(50, 10, "arrive"), wp(5, 5, "dynamic")]),
    step(3, [], ["fly"]),                         # no position: its .fly makes the next leg a flight
    step(4, [wp(60, 60)]),
    step(5, [], ["hs"]),
    step(6, x=70, y=70, um=1411),                 # only its own position (waypoint index -1)
    step(7, [wp(10, 50, um=1413)], ["deathskip"]),
    step(8, [wp(20, 50, um=1413)]),
]
ENG = {"from": 1, "a": 0, "to": 2, "b": 0, "mode": "walk", "yd": 600, "s": 85, "approx": False,
       "segments": [{"mode": "walk", "maps": {"1411": [[10, 10], [15, 12], [20, 10]], "1414": [[61.5, 46.5], [63, 46.5]]},
                     "yd": 600, "s": 85, "straight_yd": 525, "swim_yd": None, "through": ["Razor Hill"],
                     "dirs": [{"go": "east", "yd": 300, "to": "Razor Hill"}, {"go": "north-east", "yd": 300, "to": None}],
                     "source": "walkable-space graph (nav 0)"}]}
FLY = {"from": 2, "a": 3, "to": 4, "b": 0, "mode": "fly", "yd": 2400, "s": 120, "approx": False,
       "segments": [{"mode": "walk", "maps": {"1411": [[50, 10], [52, 11]]}, "yd": 40, "s": 6},
                    {"mode": "fly", "maps": {"1411": [[52, 11], [120, 40]]}, "yd": 2300, "s": 110, "from": "Razor Hill",
                     "to": "Crossroads", "hops": 2},
                    {"mode": "walk", "maps": {"1411": [[58, 58], [60, 60]]}, "yd": 60, "s": 4}]}
PLAN = "planLegs(steps).map(l => [l.key, l.kind, l.pos])"
CALLS = {
    "stops2": "stopsOf(steps[1]).map(s => [s.wi, s.path])",
    "pin2": "pinStop(steps[1])",
    "pin6": "pinStop(steps[5])",
    "pinNone": "pinStop(steps[2])",
    "plan": PLAN,
    "clipIn": "clipPolyline([[10,10],[20,20],[30,10]])",
    "clipCross": "clipPolyline([[-20,50],[50,50],[150,50]])",
    "clipOut": "clipPolyline([[-20,-20],[-10,-30]])",
    "clipBack": "clipPolyline([[50,50],[150,50],[150,60],[50,60]])",
    "engPieces": "legPieces(planLegs(steps)[0], eng, 1411, maps).map(p => [p.mode, p.runs, p.enter, p.exit])",
    "engOnCont": "legPieces(planLegs(steps)[0], eng, 1414, maps).map(p => [p.mode, p.runs.length])",
    "engOther": "legPieces(planLegs(steps)[0], eng, 1413, maps).length",
    "flyPieces": "legPieces(planLegs(steps).find(l => l.to === 4), fly, 1411, maps).map(p => [p.mode, p.exit])",
    "guessWalk": "legPieces(planLegs(steps)[1], undefined, 1411, maps).map(p => [p.mode, p.runs])",
    "guessCross": "legPieces(planLegs(steps).find(l => l.to === 7), undefined, 1411, maps).map(p => [p.mode, p.enter, p.exit])",
    "guessDeath": "legPieces(planLegs(steps).find(l => l.to === 8), undefined, 1413, maps).map(p => [p.mode, p.runs[0].length])",
    "arcEnds": "(() => { const a = arcPoints([10, 10], [50, 10]); return [a[0], a[a.length - 1], a[8][1] < 10]; })()",
    "dur": "[durText(12), durText(85), durText(600), durText(3900), durText(null), durText(0)]",
    "infoEng": "legInfo(planLegs(steps)[0], eng, maps, (u) => maps[u].name)",
    "infoFly": "legInfo(planLegs(steps).find(l => l.to === 4), fly, maps, (u) => maps[u].name).lines",
    "infoGuess": "legInfo(planLegs(steps).find(l => l.to === 7), undefined, maps, (u) => maps[u].name)",
    "hull": "hull([[0,0],[10,0],[10,10],[0,10],[5,5],[5,1]]).length",
    "hullFew": "hull([[1,1],[2,2]])",
    "ahead": "[0, 1, 2, 3, 10, 50, -3].map(aheadOpacity)",
    "ticks": "layoutTicks([{x:0,rank:1},{x:5,rank:1},{x:8,rank:6},{x:30,rank:1},{x:31,rank:1},{x:32,rank:1}], 13)",
}

tmp = tempfile.mkdtemp(prefix="glide-rxp-route-")
try:
    mod = os.path.join(desktop_ts.renderer(), "renderer", "rxp-route.js")     # (the tests' shared compile)
    body = "const steps = %s, maps = %s, eng = %s, fly = %s;\nconst out = {};\n%s\nconsole.log(JSON.stringify(out));" % (
        json.dumps(STEPS), json.dumps(MAPS), json.dumps(ENG), json.dumps(FLY),
        "\n".join("out[%s] = %s;" % (json.dumps(k), v) for k, v in CALLS.items()))
    script = os.path.join(tmp, "run.mjs")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write("import { %s } from %s;\n%s" % (
            "stopsOf, pinStop, planLegs, clipPolyline, legPieces, arcPoints, durText, legInfo, hull, layoutTicks, aheadOpacity",
            json.dumps(desktop_ts.url(mod)), body))
    r = subprocess.run([node, script], capture_output=True, text=True, encoding="utf-8",
                       timeout=60)
    assert r.returncode == 0, "running rxp-route failed: %s" % r.stderr[-2000:]
    got = json.loads(r.stdout)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# stops: walk-mode waypoints in order (path points marked), never a dynamic one
assert got["stops2"] == [[0, True], [1, True], [2, False], [3, False]], got["stops2"]
# Regression: the pin goes where the step happens (its first stop that isn't a path point), not on the first goto
assert got["pin2"]["wi"] == 2 and got["pin2"]["x"] == 40, got["pin2"]
assert got["pin6"] == {"wi": -1, "um": 1411, "x": 70, "y": 70, "path": False}, got["pin6"]
assert got["pinNone"] is None, got["pinNone"]
# the plan: the engine's legs (keys from:a>to:b), every stop to the next, kinds from the steps between
assert got["plan"] == [
    ["1:0>2:0", "walk", 1], ["2:0>2:1", "walk", 1], ["2:1>2:2", "walk", 1], ["2:2>2:3", "walk", 1],
    ["2:3>4:0", "fly", 3], ["4:0>6:-1", "hs", 5], ["6:-1>7:0", "walk", 6], ["7:0>8:0", "deathskip", 7]], got["plan"]
# clipping: inside stays whole; crossing the map gives the edge points where it comes in and goes out
assert got["clipIn"] == {"runs": [[[10, 10], [20, 20], [30, 10]]], "enter": None, "exit": None}, got["clipIn"]
assert got["clipCross"] == {"runs": [[[0, 50], [50, 50], [100, 50]]], "enter": [0, 50], "exit": [100, 50]}, got["clipCross"]
assert got["clipOut"] == {"runs": [], "enter": None, "exit": None}, got["clipOut"]
assert len(got["clipBack"]["runs"]) == 2 and got["clipBack"]["enter"] == [100, 60], got["clipBack"]
# the engine's walked segment on each map it touches, nothing on a map it doesn't
assert got["engPieces"] == [["walk", [[[10, 10], [15, 12], [20, 10]]], None, None]], got["engPieces"]
assert got["engOnCont"] == [["walk", 1]] and got["engOther"] == 0, (got["engOnCont"], got["engOther"])
# a flight leaving the map: walk, fly (cut at the edge: no landing circle here), walk
assert [m for m, _e in got["flyPieces"]] == ["walk", "fly", "walk"] and got["flyPieces"][1][1] is not None, got["flyPieces"]
# not worked out yet: a straight guess; across zones through the continent, cut at the edge; a deathskip as an arc
assert got["guessWalk"] == [["straight", [[[20, 10], [30, 10]]]]], got["guessWalk"]
assert got["guessCross"] and got["guessCross"][0][0] == "straight" and got["guessCross"][0][1] is None \
    and got["guessCross"][0][2] is not None and got["guessCross"][0][2][0] == 0, got["guessCross"]
assert got["guessDeath"] == [["death", 17]], got["guessDeath"]
assert got["arcEnds"][0] == [10, 10] and abs(got["arcEnds"][1][0] - 50) < 1e-9 and got["arcEnds"][2], got["arcEnds"]
assert got["dur"] == ["10 s", "1 min", "10 min", "1 h 05 min", "", ""], got["dur"]
# the leg's card: how, how far, how long; the directions, the places passed, what it was routed over
info = got["infoEng"]
assert info["head"] == "Walk · ~600 yd · about 1 min" and not info["approx"], info
assert info["lines"] == ["Go east 300 yd to Razor Hill, then north-east 300 yd.", "Through Razor Hill.",
                         "Routed over the walking map."], info["lines"]
assert "Fly from Razor Hill to Crossroads (2 hops), 2 min." in got["infoFly"], got["infoFly"]
assert got["infoGuess"]["approx"] and "Durotar → The Barrens" in got["infoGuess"]["head"], got["infoGuess"]
assert got["hull"] == 4 and got["hullFew"] == [[1, 1], [2, 2]], (got["hull"], got["hullFew"])
# steps ahead fade like 1/x (the player asked: the map was crowded by the rest of the guide): the current step and
# the next at full strength, half two ahead, a tenth ten ahead, never below the floor; done steps keep their own look
a = got["ahead"]
assert a[:3] == [1, 1, 0.5] and abs(a[3] - 1 / 3) < 1e-9 and a[4] == 0.1 and a[5] == 0.07 and a[6] == 1, a
# Regression: crowded ticks share two rows, the most important first, the rest left out instead of piling up
assert got["ticks"] == [1, -1, 0, 0, 1, -1], got["ticks"]
print("rxp-route: ok")
