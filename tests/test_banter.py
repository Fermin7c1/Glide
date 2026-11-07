#!/usr/bin/env python3
"""Asides: in character the coach speaks up unprompted now and then (banter.py), only in a calm moment, and builds a
bond: the memory keeps the time played together and how the asides went (Memory.bond), the request carries it and the
player's rapport notes, reflection sees which asides got no reply, and "BANTER: less / off" in a reply is obeyed.

Run: python3 tests/test_banter.py   (prints OK)
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
from coach_deps import CoachDeps  # noqa: E402
from glidedb import GlideDB  # noqa: E402
from state import GameState  # noqa: E402
import banter  # noqa: E402
import coach  # noqa: E402
import coach_tools  # noqa: E402
import memory as memory_mod  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + what)


class A(http.server.BaseHTTPRequestHandler):
    bodies, replies = [], []

    def log_message(self, *a):
        pass

    def do_POST(self):
        A.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        out = {"content": [{"type": "text", "text": A.replies.pop(0)}], "stop_reason": "end_turn",
               "usage": {"input_tokens": 10, "output_tokens": 3}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def user_text(b):
    m = b["messages"][-1]["content"]
    return m if isinstance(m, str) else "".join(x.get("text", "") for x in m if x.get("type") == "text")


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), A)
threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True).start()
os.environ["ANTHROPIC_API_KEY"] = "test"
tmp = tempfile.mkdtemp()
mem = memory_mod.Memory(os.path.join(tmp, "coach_memory.json"))
mem.add("Calls the coach Sarge and loves being called recruit.", "rapport")
saved, said = {}, []
c = coach.Coach({"api_url": "http://127.0.0.1:%d/v1/messages" % srv.server_address[1],
                 "fast_model": "claude-haiku-4-5-20251001", "deep_model": "claude-sonnet-5-5",
                 "stream_speech": False},
                GlideDB("/x.db"), lambda m: said.append(m) if m.get("kind") == "banter" else None, offline_ok=True,
                deps=CoachDeps(memory=mem, on_toggle=lambda k, v: saved.__setitem__(k, v)))
gs = GameState()
gs.update({"t": "1000", "l": "7", "x": "2000", "X": "4500", "cl": "WARRIOR", "mp": "1426", "px": "4650", "py": "5300"},
          {"pg": "B", "cl": "WARRIOR", "ra": "Dwarf", "fa": "Alliance", "nm": "T"})
b = c.banter


def calm():
    """A live frame, nothing said for a while, and the aside due now."""
    c.push([], gs)
    c.last_call = time.time() - 120
    b.since, b.wait_s, b.moment = time.time() - 10, 1.0, None


# the plain coach never banters
calm()
b.tick()
check(not said and b.since is None, "no persona: no asides")

# in character: the first aside is scheduled about banter_minutes out
c.cfg["persona"] = "A grizzled dwarf drill sergeant. Calls you recruit."
b.tick()
check(b.since is not None and 0.5 * 8 * 60 < b.wait_s < 1.5 * 8 * 60, "scheduled ~8 min out")

# not in a busy moment
calm()
c.pending = [object()]
b.tick()
check(not said, "a waiting callout comes first")
c.pending = []
c.last_call = time.time()
b.tick()
check(not said, "not right after another line")

# a calm moment: one aside, in character, carrying the bond and the rapport notes
calm()
A.replies.append("Oi, recruit! Ye run like a gnome chasin' a sheep. Pick up the pace!\nFACTS: none")
b.tick()
check(len(said) == 1 and said[0]["text"].startswith("Oi, recruit!") and said[0]["speak"], "the aside: %r" % said)
body = A.bodies[-1]
check("ASIDE (not a callout" in user_text(body) and "Your bond: new." in user_text(body), "the request and bond")
check("Stay fully in character" in user_text(body), "the stay-in-character line")
check("Calls the coach Sarge" in "\n".join(x["text"] for x in body["system"]), "the rapport notes ride along")
check(mem.bond["asides"] == 1 and mem.bond["sessions"] == 1, "the bond counts it: %r" % mem.bond)
check(b.since is None, "and the next one is rescheduled")

# the player answers it: a chat, kept going in character on the fast model, ending on a hook
A.replies.append("Hurryin'? Ha! That's the spirit, recruit, ye've doubled yer pace since Kharanos. "
                 "So tell me, what got ye into this mess?\nFACTS: none")
r = c.answer_once("Shut it, Sarge, I'm hurrying!", voice=True)["answer"]
body = A.bodies[-1]
check(r["kind"] == "answer" and r.get("chat") and r["text"].endswith("what got ye into this mess?"),
      "a chat reply (an answer, so it is spoken and shown like one): %r" % r)
check("The player is chatting with you" in user_text(body) and "max 25 words" not in user_text(body),
      "the request asks to keep the chat going, not for a short answer")
check(body["model"] == "claude-haiku-4-5-20251001", "a spoken reply keeps the spoken questions' model")
check(c._word_limit(True, True, False, chat=True) == round(35 * 1.6), "chat replies get banter_reply_words")
b.tick()
check(mem.bond["replied"] == 1 and b.awaiting is None, "the reply is counted")
check('"Oi, recruit!' in b.request(25), "recent asides are listed so they don't repeat")

# it goes on while the player keeps answering
A.replies.append("A bet with a gnome? Now THAT I believe. What did ye wager?\nFACTS: none")
r = c.answer_once("Lost a bet with a gnome, honestly", voice=True)["answer"]
check(r.get("chat") and "The player is chatting" in user_text(A.bodies[-1]), "still chatting")
# a typed game question mid-chat keeps its full answer: the deep model, the typed length at least
A.replies.append("Westfall's next, recruit: Sentinel Hill, then the farms.\nFACTS: none")
r = c.answer_once("what's the route after this?")["answer"]
check(A.bodies[-1]["model"] == "claude-sonnet-5-5" and r.get("chat"), "typed: the deep model, still chatting")
check(c._word_limit(True, False, True, chat=True) == round(70 * 1.6), "typed_answer_words, not less")

# the player wraps up: let go in one line, back to plain answers
A.replies.append("Off ye go then, recruit, make me proud!\nCHAT: end\nFACTS: none")
r = c.answer_once("ok gotta focus", voice=True)["answer"]
check(r["kind"] == "answer" and not r.get("chat") and r["text"] == "Off ye go then, recruit, make me proud!",
      "the chat ends: %r" % r)
A.replies.append("Turn in Supplies to Tannok, east.\nFACTS: none")
c.answer_once("where now?", voice=True)
check("The player is chatting" not in user_text(A.bodies[-1]), "after the chat, questions are questions")

# a chat lapses when the player stops answering
c.convo.append({"t": c._now() - 400, "who": "coach", "kind": "banter", "text": "Old aside."})
check(not b.in_chat(list(c.convo)), "an aside 400 s ago isn't a chat any more")
c.convo.append({"t": c._now(), "who": "coach", "kind": "banter", "text": "New aside."})
check(b.in_chat(list(c.convo)), "a fresh one is")

# SKIP: nothing said (counted from here: the chat replies above are banter messages too)
n = len(said)
calm()
A.replies.append("SKIP")
b.tick()
check(len(said) == n, "SKIP says nothing")

# in combat: no aside
calm()
c.state.summary = lambda: {"combat": True}
b.tick()
check(len(said) == n and b.since is not None, "no asides in combat")
del c.state.summary

# the player's say: BANTER: less / off in a reply
c._directives("Fine, I'll hold me tongue more.\nBANTER: less")
check(saved.get("banter_minutes") == 13 and c.cfg["banter_minutes"] == 13, "less: 8 -> 13 minutes")
check(c._directives("Aye, silence it is.\nBANTER: off") == "Aye, silence it is." and saved["banter"] is False,
      "off: stripped and saved")
calm()
b.tick()
check(len(said) == n, "off: no asides")

# reflection sees which asides went unanswered; the bond and the stage survive a restart
lines = coach_tools.reflection_events([{"t": 100, "who": "coach", "kind": "banter", "text": "Nice hat."},
                                       {"t": 500, "who": "player", "kind": "typed", "text": "where now"}], [])
check(lines[0].endswith("(no reply)") and "you (banter)" in lines[0], lines)
mem._bond_saved = 0          # the clock saves at most every BOND_SAVE_S: due now
mem.bond_add(hours=20)
again = memory_mod.Memory(os.path.join(tmp, "coach_memory.json"))
check(again.bond["asides"] == 1 and again.bond["replied"] == 1 and again.bond["hours"] >= 20, str(again.bond))
check(again.bond_stage() == "familiar" and "rapport" in memory_mod.KINDS, "20 hours together: familiar")
check(banter.split("Right.\nBANTER: none")[1] == "off", "BANTER: none means off")
srv.shutdown()
print("OK")
