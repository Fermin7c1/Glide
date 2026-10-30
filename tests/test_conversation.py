#!/usr/bin/env python3
"""Follow-up context: every question carries RECENT CONVERSATION (the player's last questions and the coach's answers,
plus what else it said in the last 5 minutes), sized by conversation_turns / conversation_minutes (dashboard
sliders, saved in config.json).

Run: python3 tests/test_conversation.py   (prints OK)
"""
import http.server
import json
import os
import sys
import tempfile
import threading

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
from glidedb import GlideDB  # noqa: E402
from state import GameState  # noqa: E402
import coach  # noqa: E402
import tuning  # noqa: E402


class A(http.server.BaseHTTPRequestHandler):
    bodies = []
    n = 0

    def log_message(self, *a):
        pass

    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        A.bodies.append(b)
        A.n += 1
        out = {"content": [{"type": "text", "text": "Answer %d." % A.n}], "stop_reason": "end_turn",
               "usage": {"input_tokens": 10, "output_tokens": 3}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), A)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["ANTHROPIC_API_KEY"] = "test"
clock = [10000.0]
c = coach.Coach({"api_url": "http://127.0.0.1:%d/v1/messages" % port, "fast_model": "claude-haiku-4-5-20251001",
                 "deep_model": "claude-haiku-4-5-20251001"}, GlideDB("/x.db"), lambda m: None, offline_ok=True)
c.clock = lambda: clock[0]
gs = GameState()
gs.update({"t": "1000", "l": "7", "x": "2000", "X": "4500", "cl": "WARRIOR", "mp": "1426", "px": "4650", "py": "5300"},
          {"pg": "B", "cl": "WARRIOR", "ra": "Dwarf", "fa": "Alliance", "nm": "T"})
c.state = gs


def user_text(b):
    m = b["messages"][-1]["content"]
    return m if isinstance(m, str) else "".join(x.get("text", "") for x in m if x.get("type") == "text")


c.answer_once("which quest gives more XP, Dwarven Outfitters or A New Threat?", state=gs)
first = user_text(A.bodies[-1])
assert "RECENT CONVERSATION" in first and "(none)" in first.split("RECENT CONVERSATION")[1][:300], first[-600:]
clock[0] += 120
c.emit({"kind": "callout", "text": "Wolves ahead, pull one at a time.", "t": clock[0]})
clock[0] += 60
c.answer_once("and how long does the second one take?", state=gs, voice=True)
sec = user_text(A.bodies[-1]).split("RECENT CONVERSATION", 1)[1].split("\n\n")[0]
lines = [ln for ln in sec.splitlines() if ln.startswith("- ")]
assert lines[0].startswith("- 3 min ago · you asked (typed): which quest gives more XP"), lines
assert lines[1].startswith("- 3 min ago · coach answered: Answer 1."), lines
assert lines[2] == "- 1 min ago · coach callout: Wolves ahead, pull one at a time.", lines
assert not any("how long does the second one" in ln for ln in lines), "the current question isn't listed twice"
# only the last N exchanges
for i in range(5):
    clock[0] += 30
    c.answer_once("question %d?" % i, state=gs)
c.cfg["conversation_turns"] = 2
clock[0] += 400                                     # the callout is now older than 5 minutes
c.answer_once("what about that?", state=gs)
lines = [ln for ln in user_text(A.bodies[-1]).split("RECENT CONVERSATION", 1)[1].split("\n\n")[0].splitlines()
         if ln.startswith("- ")]
assert [ln.split(": ", 1)[1] for ln in lines] == ["question 3?", "Answer 6.", "question 4?", "Answer 7."], lines
# the time window
c.cfg["conversation_minutes"] = 5
clock[0] += 400
c.answer_once("still there?", state=gs)
assert "(none)" in user_text(A.bodies[-1]).split("RECENT CONVERSATION", 1)[1][:400]
c.cfg["conversation_turns"] = 0
assert c.conversation() == []
# callouts don't carry the conversation
c._respond([__import__("rules").Trigger("step_changed", {"from": 1, "to": 2}, 3)])
assert "RECENT COACH LINES" in user_text(A.bodies[-1]) and "RECENT CONVERSATION" not in user_text(A.bodies[-1])
# dashboard settings: clamped, saved
cfg = {}
p = os.path.join(tempfile.mkdtemp(), "config.json")
assert tuning.apply(cfg, {"conversation_turns": 99, "conversation_minutes": "45"}, p) == \
    {"conversation_turns": 20, "conversation_minutes": 45}
assert json.load(open(p))["companion"]["llm"]["conversation_turns"] == 20
snap = tuning.snapshot(c)
assert snap["settings"]["conversation_turns"]["value"] == 0 and "convo" in snap
srv.shutdown()
import desktop_schema  # noqa: E402  (the Settings page schema as the page gets it)
keys = desktop_schema.keys()
if keys is None:
    print("SKIP: the Settings page check (needs node and desktop/node_modules); the rest passed")
    raise SystemExit(77)
assert {"companion.llm.conversation_turns", "companion.llm.conversation_minutes"} <= keys,     "the sliders are in the app's Settings"
print("OK")
