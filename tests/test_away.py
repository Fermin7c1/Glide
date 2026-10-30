#!/usr/bin/env python3
"""Moving away from the waypoint: judged from the recent track against the waypoint RestedXP's arrow points at."""
import os, sys, types
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "companion"))
import rules as rules_mod
from state import GameState
from rules import Rules

clock = [1000000.0]
rules_mod.time = types.SimpleNamespace(time=lambda: clock[0])
MAP = 1429                                   # 3000 x 2000 yd: 30 yd per map % across, 20 down


def run(path, arrow=None, step_gotos=((50.0, 20.0),), combat=lambda t: 0, cfg=None):
    """path(t) -> (x, y) in map %, one frame a second for len seconds. Returns [(t, trigger)]."""
    st, r = GameState(), Rules(cfg or {})
    st.map_sizes[MAP] = (3000.0, 2000.0)
    el = ",".join("go:%d:%.1f:%.1f" % (MAP, x, y) for x, y in step_gotos)
    st.update({"t": "1", "l": "10"}, {"pg": "S0", "i": "5", "el": el, "tx": "Kill boars"}, now=clock[0])
    st.update({"t": "1", "l": "10"}, {"pg": "Z", "zn": "Elwynn", "mm": str(MAP), "mw": "3000", "mh": "2000"},
              now=clock[0])
    out, t0 = [], clock[0]
    for t, (x, y) in path:
        clock[0] = t0 + t
        core = {"t": str(int(t)), "l": "10", "x": "1", "X": "100", "c": str(combat(t)), "mp": str(MAP),
                "px": str(int(x * 100)), "py": str(int(y * 100)), "f": "314"}   # facing south
        a = arrow(t) if arrow else None
        if a:
            core["aw"] = "%d:%.1f:%.1f" % (a[2] if len(a) > 2 else MAP, a[0], a[1])
        st.update(core, {}, now=clock[0])
        out += [(t, tr) for tr in r.check(st, []) if tr.kind == "moving_away_from_waypoint"]
    return out, st


def line(x0, y0, vx, vy, secs):            # 7 yd/s running, given in map % per second
    return [(t, (x0 + vx * t, y0 + vy * t)) for t in range(secs)]


RUN_X, RUN_Y = 7 / 30.0, 7 / 20.0          # map % per second at running speed, along x and along y

# heading straight for the waypoint (north, at 50,20 from 50,80): never called out
out, _ = run(line(50, 80, 0, -RUN_Y, 120))
assert out == [], out

# running straight away from it (south): called out once the track shows it for 30 s, with yards and direction
out, _ = run(line(50, 30, 0, RUN_Y, 90))
assert len(out) == 1, out
t, tr = out[0]
assert 30 <= t <= 32, t
assert tr.facts["direction"] == "north" and tr.facts["relative"] == "behind you", tr.facts   # facing south, it is north
assert tr.facts["yards"] >= 300 and tr.facts["yards_further_than_before"] >= 150, tr.facts
assert tr.facts["seconds_moving_away"] >= 30, tr.facts

# a step with two gotos: you've reached the first (50,20) and run on to the second (50,70). The step page still
# lists the first one first, but the arrow points at the second, so heading for it is not "moving away"
path = line(50, 20, 0, RUN_Y, 120)
out, st = run(path, step_gotos=((50.0, 20.0), (50.0, 70.0)), arrow=lambda t: (50.0, 70.0))
assert out == [], out
assert st.goto_target()["y"] == 70.0 and st.goto_target()["arrow"], st.goto_target()
# without the arrow (an older addon) the first goto is all there is, and that run does move away from it
out, _ = run(path, step_gotos=((50.0, 20.0), (50.0, 70.0)))
assert out, out

# the arrow moving on resets the track: 25 s running away from the waypoint you just reached (50,30), then the arrow
# moves on to the next one (50,95), straight ahead
out, _ = run(line(50, 50, 0, RUN_Y, 85), arrow=lambda t: (50.0, 30.0) if t < 25 else (50.0, 95.0))
assert out == [], out

# a detour: 25 s away round a hill, then back toward it; the last 10 s close in, so nothing is said
path = line(50, 50, 0, RUN_Y, 25) + [(25 + t, (50, 50 + RUN_Y * 25 - RUN_Y * t)) for t in range(40)]
out, _ = run(path, arrow=lambda t: (50.0, 20.0))
assert out == [], out

# going sideways past it at a distance grows slowly: not enough in 30 s to count
out, _ = run(line(10, 20, RUN_X * 0.3, 0, 60), arrow=lambda t: (50.0, 60.0))
assert out == [], out

# combat in the middle restarts the window: 20 s away, combat, 20 s away is never 30 s in a row
path = line(50, 30, 0, RUN_Y, 60)
out, _ = run(path, arrow=lambda t: (50.0, 20.0), combat=lambda t: 1 if 20 <= t < 25 else 0)
assert out and out[0][0] >= 24 + 30, out      # the last combat frame starts the window

# close to the waypoint, wandering is left to RestedXP (under 100 yd)
out, _ = run(line(50, 21, 0, 0.05, 90), arrow=lambda t: (50.0, 20.0))
assert out == [], out

# the waypoint on another map: no judgement
out, _ = run(line(50, 30, 0, RUN_Y, 90), arrow=lambda t: (50.0, 20.0, 1436))
assert out == [], out

print("away ok")
