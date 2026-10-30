#!/usr/bin/env python3
"""Reminders: "remind me to repair when I'm near a repair vendor" and friends. Parsing, conditions (near an NPC of
a role or one selling an item, a place, a level, a time), when they're said (on arrival, re-armed after leaving,
periodic nudges, never while busy), auto-done repairs, persistence, the coach's local commands and tools, and the
real NPC lookup in kb.db.

Run: python3 tests/test_reminders.py   (prints OK)
"""
import http.server
import json
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
sys.path.append(ROOT)
import reminders as R  # noqa: E402
from glidedb import GlideDB  # noqa: E402
from state import GameState  # noqa: E402

# ---- parsing --------------------------------------------------------------------------------------------
P = R.parse_request
assert P("remind me to repair when I'm near a repair vendor") == ("repair", {"kind": "npc", "role": "repair"})
assert P("Remind me to buy 20 Ice Cold Milk") == ("buy 20 Ice Cold Milk", {"kind": "auto"})
assert P("remind me to train when I get to Ironforge") == ("train", {"kind": "place", "place": "Ironforge"})
assert P("when I get to Goldshire remind me to pick up the flight path") == \
    ("pick up the flight path", {"kind": "place", "place": "Goldshire"})
assert P("remind me to go back to Loch Modan at level 18") == ("go back to Loch Modan", {"kind": "level", "level": 18})
assert P("remind me in 15 minutes to check the auction house") == ("check the auction house",
                                                                    {"kind": "time", "minutes": 15})
assert P("when I hit 10 remind me to train") == ("train", {"kind": "level", "level": 10})
assert P("remind me to check my mail in 15 minutes") == ("check my mail", {"kind": "time", "minutes": 15})
assert P("remind me to sell junk next time I'm at a vendor") == ("sell junk", {"kind": "npc", "role": "vendor"})
assert P("what's my next step") is None
assert P("coach, remind me to train") == ("train", {"kind": "auto"}) and P("hey coach remind me to train")[0] == "train"
t0 = time.time()
assert R._REQ.match("coach" + "  coach" * 40 + "!") is None and time.time() - t0 < 1, "no runaway backtracking (py/redos)"
assert P("remind me to hand in the wolf meat") == ("hand in the wolf meat", {"kind": "auto"}), "no place in 'hand in'"
assert P("remind me to buy bandages at the vendor") == ("buy bandages", {"kind": "npc", "role": "vendor"})
assert R.role_from_words("buy arrows") == "ammo" and R.role_from_words("visit my class trainer") == "class_trainer"

# ---- adding infers the condition from what to do ----------------------------------------------------------
tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "reminders.json")
rs = R.Reminders(path)
rep = rs.add("repair", {"kind": "auto"}, now=1000, durability=30)
assert rep["when"] == {"kind": "npc", "role": "repair"} and rep["every_min"] == 20 and rep["dur_low"] == 30
milk = rs.add("buy 20 Ice Cold Milk", {"kind": "auto"}, now=1000)
assert milk["when"] == {"kind": "npc", "item": "Ice Cold Milk", "role": "food"}, milk["when"]   # role: fallback
arrows = rs.add("buy arrows", None, now=1000)
assert arrows["when"] == {"kind": "npc", "role": "ammo"}, arrows["when"]
lvl = rs.add("go back to Loch Modan", {"kind": "level", "level": 18}, every_min=0, now=1000)
assert R.describe_when(lvl) == "at level 18" and R.describe_when(milk) == "near a vendor that sells Ice Cold Milk"
assert [r["id"] for r in R.Reminders(path).list()] == [1, 2, 3, 4], "kept across restarts"


# ---- when they're said -------------------------------------------------------------------------------------
class FakeEnv:
    def __init__(self, near=None, place=None, level=10, durability=30, busy=False):
        self.near, self.place, self.level, self.durability, self.busy = near or {}, place, level, durability, busy
        self.near_yd = 40

    def nearest(self, role=None, item=None):
        return self.near.get(item or role)

    def in_place(self, p):
        return bool(self.place and p and p.lower() == self.place.lower())


smith = {"name": "Durnan Furcutter", "subname": "Cloth & Leather Armor Merchant", "yards": 22, "direction": "east"}
rs2 = R.Reminders(None)
r1 = rs2.add("repair", None, now=0, durability=30)
assert rs2.check(FakeEnv(), now=10) == []
ev = rs2.check(FakeEnv(near={"repair": smith}), now=20)
assert len(ev) == 1 and ev[0][1] == "condition", ev
assert R.speech(*ev[0]) == "Reminder: Repair. Durnan Furcutter (Cloth & Leather Armor Merchant) is 20 yards east.", \
    R.speech(*ev[0])
assert rs2.check(FakeEnv(near={"repair": smith}), now=30) == [], "said once per arrival"
assert rs2.check(FakeEnv(near={"repair": dict(smith, yards=300)}), now=60) == []
assert rs2.check(FakeEnv(near={"repair": smith}), now=100) == [], "re-armed only after 2 minutes away"
assert rs2.check(FakeEnv(), now=110) == []
assert rs2.check(FakeEnv(), now=240) == []                     # away long enough: armed again
assert rs2.check(FakeEnv(near={"repair": smith}, busy=True), now=250) == [], "never in combat"
assert rs2.check(FakeEnv(near={"repair": smith}), now=260)[0][1] == "condition"
# periodic nudge after 20 minutes without the condition
ev = rs2.check(FakeEnv(), now=260 + 20 * 60 + 1)
assert len(ev) == 1 and ev[0][1] == "periodic" and R.speech(*ev[0]) == "Still to do: Repair (near a repair vendor).", ev
assert rs2.check(FakeEnv(busy=True), now=260 + 40 * 60 + 2) == [], "no nudges while busy"
# repaired: durability back up -> done, and gone
ev = rs2.check(FakeEnv(durability=100), now=5000)
assert ev and ev[0][1] == "done" and rs2.list() == [] and R.speech(*ev[0]) == "Reminder done: repair."
# place, level (one-shot when every_min=0), time
rs3 = R.Reminders(None)
rs3.add("train", {"kind": "place", "place": "Ironforge"}, now=0)
rs3.add("go back to Loch Modan", {"kind": "level", "level": 18}, every_min=0, now=0)
rs3.add("check the mail", {"kind": "time", "minutes": 5}, every_min=0, now=0)
assert rs3.check(FakeEnv(place="Dun Morogh", level=17), now=10) == []
ev = rs3.check(FakeEnv(place="Ironforge", level=18), now=20)
assert sorted(R.speech(*e) for e in ev) == ["Reminder for level 18: Go back to Loch Modan.",
                                            "Reminder, you're in Ironforge: Train."], ev
assert [r["what"] for r in rs3.list()] == ["train", "check the mail"], "a met one-shot level reminder is closed"
ev = rs3.check(FakeEnv(place="Ironforge"), now=301)
assert [R.speech(*e) for e in ev] == ["Reminder: Check the mail."] and [r["what"] for r in rs3.list()] == ["train"]
# closing by words / id / all
rs4 = R.Reminders(None)
for w in ("repair", "buy 20 Ice Cold Milk", "train"):
    rs4.add(w, None, now=0)
assert [x["what"] for x in rs4.remove("ice cold milk")] == ["buy 20 Ice Cold Milk"]
assert [x["what"] for x in rs4.remove("3")] == ["train"] and rs4.remove("nothing like it") == []
assert len(rs4.remove("all")) == 1 and rs4.list() == []

# ---- coach: local commands, tools, the tick with real NPCs from kb.db ---------------------------------------
KB = os.path.join(ROOT, "data", "kb.db")
if not os.path.exists(KB):
    print("SKIP: coach part (no data/kb.db); the rest passed")
    raise SystemExit(77)
from coach import Coach, CoachDeps  # noqa: E402


class A(http.server.BaseHTTPRequestHandler):
    bodies = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        A.bodies.append(b)
        last = b["messages"][-1]["content"]
        has_result = isinstance(last, list) and any(x.get("type") == "tool_result" for x in last)
        if not has_result and (b.get("tool_choice") or {}).get("type") == "auto":
            out = {"content": [{"type": "tool_use", "id": "t1", "name": "reminder_add",
                                "input": {"what": "hand in the wolf meat", "when": "place", "place": "Kharanos"}}],
                   "stop_reason": "tool_use", "usage": {"input_tokens": 10, "output_tokens": 5}}
        else:
            out = {"content": [{"type": "text", "text": "Will do, in Kharanos."}], "stop_reason": "end_turn",
                   "usage": {"input_tokens": 10, "output_tokens": 5}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), A)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["ANTHROPIC_API_KEY"] = "test"
msgs = []
c = Coach({"api_url": "http://127.0.0.1:%d/v1/messages" % port, "fast_model": "claude-haiku-4-5-20251001",
           "deep_model": "claude-haiku-4-5-20251001", "reminder_check_s": 0}, GlideDB("/x.db"), msgs.append,
          kb=KB, offline_ok=True, deps=CoachDeps(reminders=R.Reminders(None)))
gs = GameState()
# standing in Coldridge Valley / Dun Morogh, a few yards from Durnan Furcutter (armor merchant, repairs) at 28.78,66.44
core = {"t": "1000", "l": "7", "x": "2000", "X": "4500", "cl": "WARRIOR", "mp": "1426", "px": "2890", "py": "6650",
        "st": "5", "du": "35"}
for page in ({"pg": "B", "cl": "WARRIOR", "ra": "Dwarf", "fa": "Alliance", "nm": "T"},
             {"pg": "G", "gn": "05-11 Dun Morogh", "gg": "RestedXP Forever Guide (A)"}, {}):
    gs.update(core, page)
c.state = gs
n = len(A.bodies)
r = c._respond([], question="Remind me to repair when I'm near a repair vendor")
assert r["model"] == "local" and r["text"] == "Will remind you to repair near a repair vendor.", r
assert len(A.bodies) == n, "a clear request is filed without the AI"
assert c._respond([], question="what are my reminders")["text"] == "1. repair, near a repair vendor."
# an unusual phrasing goes to the AI, which files it with the reminder_add tool
r = c._respond([], question="remind me to hand in the wolf meat")
assert len(A.bodies) == n + 2 and "reminder_done" in [t["name"] for t in A.bodies[n]["tools"]], \
    "reminder tools are offered"
assert [x["what"] for x in c.deps.reminders.list()] == ["repair", "hand in the wolf meat"], c.deps.reminders.list()
assert R.describe_when(c.deps.reminders.list()[1]) == "in Kharanos"
# the game state the model sees lists the open reminders
assert '"reminders"' in json.dumps(c.context(), default=str)
# the tick: near Durnan Furcutter -> the repair reminder is said, with his name and the distance
ev = c.reminder_ticker.tick()
durability = c.state.summary().get("durability")
assert ev and ev[0]["kind"] == "reminder" and ev[0]["text"].startswith("Reminder: Repair. "), (ev, durability)
assert "yards" in ev[0]["text"], ev[0]["text"]
assert c.reminder_ticker.tick() == []
# closing by voice
r = c._respond([], question="done with the repair reminder")
assert r["model"] == "local" and r["text"] == "Done: repair." and [x["what"] for x in c.deps.reminders.list()] == \
    ["hand in the wolf meat"], r
assert c._respond([], question="clear reminders")["text"] == "Cleared 1 reminder." and c.deps.reminders.list() == []
# buff reminders still go to the buff watch, not here
assert c._reminder_command("remind me to reapply Battle Shout") is None
# offline (no key): every "remind me" is filed locally, even an unusual one
c.llm.keys["anthropic"] = ""
r = c._respond([], question="remind me to hand in the wolf meat")
assert r["model"] == "local" and "every 20 minutes" in r["text"], r
srv.shutdown()
print("OK")
