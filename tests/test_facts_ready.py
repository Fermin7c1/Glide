#!/usr/bin/env python3
"""facts_ready (companion/rules.py, Rules._live): when the engine wrote new item facts into the addon's Data/Live.lua
(live_data.LiveData.news, handed out once), the coach says so at a quiet moment: out of combat, alive, and resting or
on a flight. The writer is asked only then (so a /reload of the player's own in between isn't announced), and at most
every LIVE_POLL_S. restart when the game only sees them after a WoW restart. It is an upkeep callout about gear (the
/coach upkeep switch mutes it; the callout gets the gear topic's state), and the coach without a model says the
player text.

Run: python3 tests/test_facts_ready.py   (prints OK)
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "companion"))
import offline_coach  # noqa: E402
import prompt_context  # noqa: E402
import rules as rules_mod  # noqa: E402
from rules import Rules, Trigger  # noqa: E402
from state import GameState  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


class FakeLive:
    """news() hands out what was written since the last news, once; calls are counted."""
    def __init__(self):
        self.pending, self.calls = None, 0

    def news(self):
        self.calls += 1
        news, self.pending = self.pending, None
        return news


clock = [1000000.0]
real_time = rules_mod.time
rules_mod.time = types.SimpleNamespace(time=lambda: clock[0])
try:
    st, r = GameState(), Rules({})
    live = FakeLive()

    def frame(combat=0, dead=0, resting=0, taxi=0, step=6):
        """One frame `step` seconds on; -> the facts_ready triggers it raised."""
        clock[0] += step
        st.update({"t": str(int(clock[0]) % 1000000), "l": "60", "x": "1", "X": "100", "c": str(combat),
                   "d": str(dead), "rs": str(resting), "tx": str(taxi)}, {}, now=clock[0])
        return [t for t in r.check(st, []) if t.kind == "facts_ready"]

    # without a Live.lua writer: nothing
    check(frame(resting=1) == [], "no writer")
    r.live = live

    # new facts: not asked for in combat, in the open or dead; said once resting in town
    live.pending = {"n": 12, "restart": False}
    check(frame(combat=1, resting=1) == [] and frame() == [] and frame(dead=1, resting=1) == [], "not now")
    check(live.calls == 0, ("asked only at a quiet moment", live.calls))
    got = frame(resting=1)
    check(len(got) == 1 and got[0].facts == {"n": 12, "restart": False} and got[0].priority == 3, got)
    check(frame(resting=1) == [] and live.calls == 2, "said once")

    # on a flight is quiet too; restart when the game needs one
    live.pending = {"n": 3, "restart": True}
    got = frame(taxi=1)
    check(len(got) == 1 and got[0].facts == {"n": 3, "restart": True}, ("on a flight", got))

    # asked at most every LIVE_POLL_S, not on every frame (10 a second)
    calls = live.calls
    for _ in range(30):
        frame(resting=1, step=0.1)
    check(live.calls - calls <= 1, live.calls - calls)

    # a writer that fails doesn't break the rules
    class Broken:
        def news(self):
            raise OSError("live.json")
    r.live = Broken()
    check(frame(resting=1, step=rules_mod.LIVE_POLL_S) == [], "a failing writer")

    # an upkeep callout about gear
    check(rules_mod.CATEGORY["facts_ready"] == "upkeep", rules_mod.CATEGORY.get("facts_ready"))
    check(rules_mod.allowed("facts_ready", {"coach": True}) and
          not rules_mod.allowed("facts_ready", {"coach": True, "upkeep": False}), "the upkeep switch")
    check(prompt_context.TRIGGER_TOPIC["facts_ready"] == "gear", "the gear topic")

    # the player text without a model
    ctx = {"player": {"level": 60}}
    check(offline_coach.callout([Trigger("facts_ready", {"n": 4, "restart": False}, 3)], ctx) ==
          "New item facts are in the game after a /reload.", "reload line")
    check(offline_coach.callout([Trigger("facts_ready", {"n": 4, "restart": True}, 3)], ctx) ==
          "Glide learned where your gear comes from; restart WoW once to see it in the game.", "restart line")
finally:
    rules_mod.time = real_time
print("OK")
