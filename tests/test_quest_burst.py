#!/usr/bin/env python3
"""Quest bursts: turning in and picking up several quests at a hub makes one summary callout, not one per quest.
Quest events wait until none came for settle_s (at most settle_max_s after the first), and while accepted quests are
still gathering for their briefing; a burst of pickups joins as quests_accepted; an urgent trigger meanwhile goes out
alone; the summary gets summary_words and a note asking for one line.

Run: python3 tests/test_quest_burst.py   (prints OK)
"""
import http.server
import json
import os
import sys
import threading
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
from glidedb import GlideDB  # noqa: E402
from rules import Trigger  # noqa: E402
from state import GameState  # noqa: E402
import coach  # noqa: E402
import offline_coach  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + what)


class A(http.server.BaseHTTPRequestHandler):
    bodies = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        A.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        out = {"content": [{"type": "text", "text": "Turned in three; pick the Sturdy Belt. Next: Tannok, east.\n"
                                                     "FACTS: none"}],
               "stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 3}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), A)
threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True).start()
os.environ["ANTHROPIC_API_KEY"] = "test"
c = coach.Coach({"api_url": "http://127.0.0.1:%d/v1/messages" % srv.server_address[1],
                 "fast_model": "claude-haiku-4-5-20251001", "deep_model": "claude-haiku-4-5-20251001",
                 "stream_speech": False}, GlideDB("/x.db"), lambda m: None, offline_ok=True)
gs = GameState()
gs.update({"t": "1000", "l": "7", "x": "2000", "X": "4500", "cl": "WARRIOR", "mp": "1426", "px": "4650", "py": "5300"},
          {"pg": "B", "cl": "WARRIOR", "ra": "Dwarf", "fa": "Alliance", "nm": "T"})
c.state = gs


def trig(kind, ago, priority=2, **facts):
    t = Trigger(kind, facts, priority)
    t.t = time.time() - ago
    return t


def take(*triggers):
    c.pending = list(triggers)
    return c._take_batch()


# one quest event waits for quiet, then goes
check(take(trig("quest_turned_in", 1, arg=1)) is None, "a turn-in a second ago waits")
check(len(take(trig("quest_turned_in", 3, arg=1)) or []) == 1, "a turn-in with 3 s of quiet goes")

# a burst waits for its last event, then goes as one batch
check(take(trig("quest_turned_in", 5, arg=1), trig("quest_turned_in", 1, arg=2)) is None, "still coming: waits")
b = take(trig("quest_turned_in", 6, arg=1), trig("quest_turned_in", 4, arg=2), trig("reward_choice", 3, 1, ranked=[]))
check(b is not None and len(b) == 3, "the settled burst is one batch: %r" % b)

# right after another callout: a lone turn-in waits for min_interval, a settled burst is summed up at once
c.last_call = time.time()
check(take(trig("quest_turned_in", 3, arg=1)) is None, "a lone turn-in keeps min_interval")
check(len(take(trig("quest_turned_in", 4, arg=1), trig("step_changed", 3)) or []) == 2,
      "a settled burst doesn't wait for min_interval")
c.last_call = 0

# a steady stream can't hold them forever
check(len(take(trig("quest_turned_in", 9, arg=1), trig("step_changed", 0.5)) or []) == 2, "settle_max_s caps the wait")

# an urgent trigger goes out at once, alone; the burst keeps settling
b = take(trig("quest_turned_in", 1, arg=1), trig("died", 0, 1))
check([t.kind for t in b or []] == ["died"] and [t.kind for t in c.pending] == ["quest_turned_in"],
      "death goes alone, the turn-in waits: %r / %r" % (b, c.pending))

# accepted quests still gathering for their briefing hold the summary; their burst joins it as quests_accepted
c.briefings.accepted.queue = [(time.time(), 101)]
check(take(trig("quest_turned_in", 4, arg=1)) is None, "waits while accepted quests gather")
c.briefings.accepted.queue = []
c.pending = [trig("quest_turned_in", 4, arg=1)]
c.briefings.hold([101, 102])
check(sorted(t.kind for t in c.pending) == ["quest_turned_in", "quests_accepted"], "the pickups join the burst")
b = c._take_batch()
check(b is not None and len(b) == 2, "and go out with it at once (the burst had settled)")

# the summary: more words, and the request asks for one line covering it all
check(c._word_limit(False, False, False, [trig("quest_turned_in", 0)]) == 18, "one turn-in: a plain callout")
check(c._word_limit(False, False, False, b) == 30, "a burst: summary_words")
c.last_call = 0
c._respond([trig("quest_turned_in", 4, arg=1), trig("quest_turned_in", 4, arg=2),
            trig("reward_choice", 3, 1, ranked=[])])
user = A.bodies[-1]["messages"][-1]["content"]
user = user if isinstance(user, str) else "".join(x.get("text", "") for x in user if x.get("type") == "text")
check(coach.SUMMARY_NOTE % 30 in user, "the request asks for one summary of at most 30 words")

# offline: the same stop summed up by rule
ctx = c.context(triggers=["quest_turned_in"])
line = offline_coach.callout([trig("quest_turned_in", 0, arg=i) for i in range(3)] +
                             [trig("quests_accepted", 0, 3, count=2, quests=["Wendigo Mane", "Beer Basted Boar"])],
                             ctx)
check(line.startswith("Turned in 3 quests. Picked up 2: Wendigo Mane, Beer Basted Boar."), line)
srv.shutdown()
print("OK")
