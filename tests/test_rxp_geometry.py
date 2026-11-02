#!/usr/bin/env python3
"""Yards, bearings and moving points between maps on the Guides page (desktop/src/renderer/rxp-geometry.ts, compiled
with the project's TypeScript once for all the tests (desktop_ts.py) and run under Node).

Regressions: the arrow's "~N yd" treated every zone as 3500 x 2400 yd (The Barrens is about 10,000 yd across), and its
bearing was worked out in map percent, where 1 % of x is 1.5 times 1 % of y: a move 10 % right and 10 % up read 45°
when it is 56°. Without the zone's size (no knowledge base yet) the page shows no yard figure rather than a guess.

Needs node and desktop/node_modules (skips with 77 without them)."""
import json
import math
import os
import subprocess
import sys

import desktop_ts

HERE = os.path.dirname(os.path.abspath(__file__))
DESK = os.path.join(HERE, "..", "desktop")
SRC = os.path.join(DESK, "src", "renderer", "rxp-geometry.ts")

node = desktop_ts.node
if not node or not desktop_ts.available():
    print("SKIP: needs node and desktop/node_modules (cd desktop && npm ci)")
    sys.exit(77)

# Durotar-sized (5250 x 3500 yd) on a continent, its corners at (60, 45) and (75, 60) of the continent; a neighbour
# beside it on the same continent; a dungeon with no parent rectangle
MAPS = {"1411": {"name": "Durotar", "parent": 1414, "w_yd": 5250, "h_yd": 3500, "rect": [60, 45, 75, 60]},
        "1413": {"name": "The Barrens", "parent": 1414, "w_yd": 10500, "h_yd": 7000, "rect": [45, 45, 75, 75]},
        "1414": {"name": "Kalimdor", "parent": 947, "w_yd": 36000, "h_yd": 24000, "rect": None},
        "1581": {"name": "The Deadmines", "parent": None, "w_yd": None, "h_yd": None, "rect": None}}
CALLS = [
    ["yards", MAPS, 1411, 10, 0], ["yards", MAPS, 1411, 0, 10], ["yards", MAPS, 1411, 3, 4], ["yards", MAPS, 1581, 3, 4],
    ["yards", {}, 1411, 3, 4], ["yards", None, 1411, 3, 4],
    ["bearingDeg", MAPS, 1411, 10, -10], ["bearingDeg", None, 1411, 10, -10], ["bearingDeg", MAPS, 1411, 0, -5],
    ["bearingDeg", MAPS, 1411, 5, 0], ["bearingDeg", MAPS, 1411, 0, 5], ["bearingDeg", MAPS, 1411, -5, 0],
    ["toParent", MAPS, 1411, 50, 50], ["toParent", MAPS, 1414, 50, 50], ["fromParent", MAPS, 1411, 67.5, 52.5],
    ["between", MAPS, 1411, 1413, 0, 0], ["between", MAPS, 1411, 1414, 100, 100], ["between", MAPS, 1414, 1411, 75, 60],
    ["between", MAPS, 1411, 1411, 12, 34], ["between", MAPS, 1411, 1581, 50, 50], ["between", MAPS, 1411, None, 1, 1],
    ["clipToBox", 50, 50, 150, 50], ["clipToBox", -50, 50, 50, 50], ["clipToBox", 110, 110, 120, 130],
    ["clipToBox", 10, 10, 20, 20], ["clipToBox", -10, -10, 110, 110],
    ["yardsText", 412.4], ["yardsText", 46.4], ["yardsText", None],
]

mjs = os.path.join(desktop_ts.renderer(), "renderer", "rxp-geometry.js")     # (the tests' shared compile)
code = ("import(%s).then(m => console.log(JSON.stringify(%s.map(([f, ...a]) => m[f](...a)))))"
        % (json.dumps(desktop_ts.url(mjs)), json.dumps(CALLS)))
r = subprocess.run([node, "--input-type=module", "-e", code], capture_output=True, text=True, encoding="utf-8",
                   timeout=60)
assert r.returncode == 0, "running rxp-geometry failed: %s" % r.stderr[-2000:]
got = json.loads(r.stdout)


def near(a, b, tol=1e-6):
    if isinstance(b, (list, tuple)):
        return a is not None and len(a) == len(b) and all(near(x, y, tol) for x, y in zip(a, b))
    if isinstance(b, dict):
        return isinstance(a, dict) and a.keys() == b.keys() and all(near(a[k], b[k], tol) for k in b)
    if b is None or isinstance(b, str):
        return a == b
    return a is not None and abs(a - b) <= tol


WANT = [
    525.0, 350.0, math.hypot(157.5, 140.0), None, None, None,
    math.degrees(math.atan2(10 * 5250, 10 * 3500)), math.degrees(math.atan2(100.2, 66.8)), 0.0, 90.0, 180.0, 270.0,
    {"um": 1414, "x": 67.5, "y": 52.5}, None, {"x": 50, "y": 50},
    {"x": 50, "y": 0}, {"x": 75, "y": 60}, {"x": 100, "y": 100}, {"x": 12, "y": 34}, None, None,
    [50, 50, 100, 50], [0, 50, 50, 50], None, [10, 10, 20, 20], [0, 0, 100, 100],
    "~410 yd", "~46 yd", "",
]
wrong = [(c, g, w) for c, g, w in zip(CALLS, got, WANT) if not near(g, w)]
assert not wrong, wrong
assert round(got[6]) == 56 and round(got[7]) == 56, "a move 10 % right and 10 % up is 56° east of north, not 45°"
print("OK")
