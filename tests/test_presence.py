#!/usr/bin/env python3
"""Presence across the climb (Phase 4 of the coach's social side): every 10 levels is a milestone (a real moment, more
words, looking back on the stretch); level 60 is the end of the climb, told with the road so far (every era and
chapter); asides the player lets pass in a row stretch the wait (x1.5 each, x4 at most; a reply resets it); greetings
know how long the two have been at it (days, sessions, a week to the day).

Run: python3 tests/test_presence.py   (prints OK)
"""
import os
import sys
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
from coach_deps import CoachDeps  # noqa: E402
from glidedb import GlideDB  # noqa: E402
from rules import Trigger  # noqa: E402
from state import GameState  # noqa: E402
import banter  # noqa: E402
import coach  # noqa: E402
import memory as memory_mod  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


banter.random.random = lambda: 0.0          # every moment gets its reaction
mem = memory_mod.Memory(None)
c = coach.Coach({}, GlideDB("/x.db"), lambda m: None, offline_ok=True, deps=CoachDeps(memory=mem))
c.cfg["persona"] = "A grizzled dwarf drill sergeant. Calls you recruit."
b = c.banter
gs = GameState()
gs.update({"t": "1000", "l": "12", "x": "2000", "X": "4500", "cl": "WARRIOR", "mp": "1436", "px": "4650", "py": "5300"},
          {"pg": "B", "cl": "WARRIOR", "ra": "Dwarf", "fa": "Alliance", "nm": "T"})
real = gs.summary


def ding(level):
    s = dict(real(), level=level, zone="Westfall", subzone="Moonbrook", combat=False, hp=80)
    gs.summary = lambda: s
    gs.vitals = lambda: s
    b.moment = None
    c.push([Trigger("level_up", {}, 1)], gs)
    return b.moment


# ---- a ding, a milestone, the end of the climb ---------------------------------------------------------------
check(ding(13)["kind"] == "level_up", "13: a ding")
m20 = ding(20)
check(m20["kind"] == "milestone" and "milestone" not in b.cooldown, m20)
req = b.request(25, m20)
check("A MILESTONE: Reached level 20" in req and "THE ROAD SO FAR" not in req, "a milestone looks back on the stretch")
check(b.mood_line().startswith("Your mood right now: moved, proud as can be"), b.mood_line())
mem.chapter_add("Hogger chased us across Elwynn.", ["Hogger"], t0=1, t1=2)
mem.chapter_add("We cleared the Deadmines with a pug.", ["Deadmines"], t0=3, t1=4)
m60 = ding(60)
check(m60["kind"] == "the_end", m60)
req = b.request(100, m60)
check("THE CLIMB IS DONE" in req and "THE ROAD SO FAR (oldest first):" in req and
      req.index("Hogger chased") < req.index("Deadmines with a pug"), "the road so far, oldest first")
check(banter.BIG["the_end"] == 4 and banter.BIG["milestone"] == 2, "more words for them")

# ---- asides let pass stretch the wait; a reply resets it -------------------------------------------------------
check(b._factor("questing") == 1.0, "nothing let pass yet")
for _ in range(1, 6):
    b.awaiting = (c._now() - banter.REPLY_S - 1, "questing", "An aside.", c.persona_name())
    b._reaction()
check(b.unanswered == 5 and b._factor("questing") == banter.IGNORED_MAX, "capped at x4: %r" % b._factor("questing"))
b.unanswered = 1
check(abs(b._factor("questing") - 1.5) < 1e-9 and abs(b._factor("flying") - 0.6) < 1e-9, "x1.5 for one let pass")
b.awaiting = (c._now(), "questing", "An aside.", c.persona_name())
c.convo.append({"t": c._now() + 1, "who": "player", "kind": "voice", "text": "ha!"})
b._reaction()
check(b.unanswered == 0, "a reply resets it")

# ---- the greeting knows how long you've been at it ------------------------------------------------------------
now = time.time()
mem.bond["first_seen"], mem.bond["sessions"] = now - 14 * 86400 - 60, 9
b.moment = None
b._greeting(now, now - 3 * 3600)
check("you've been at this together for 14 days, 9 sessions (2 weeks to the day: mark it)" in b.moment["text"],
      b.moment["text"])
mem.bond["first_seen"] = now - 3 * 86400
b.moment = None
b._greeting(now, now - 3600)
check("for 3 days" in b.moment["text"] and "to the day" not in b.moment["text"], b.moment["text"])
print("OK")
