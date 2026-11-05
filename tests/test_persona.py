#!/usr/bin/env python3
"""Roleplay: the player asks the coach to play a character; the model answers in character with PERSONA: / VOICE:
lines, which the coach strips, applies and saves (llm.persona, llm.persona_voice). While it is set every call gets the
persona block last in the system prompt and a stay-in-character line, lines may run longer, and the app's speak events
carry the voice direction. "PERSONA: off" drops the act.

Run: python3 tests/test_persona.py   (prints OK)
"""
import http.server
import json
import os
import sys
import threading

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
from coach_deps import CoachDeps  # noqa: E402
from glidedb import GlideDB  # noqa: E402
from replies import EarlySpeech  # noqa: E402
from state import GameState  # noqa: E402
import coach  # noqa: E402
import persona  # noqa: E402
import tuning  # noqa: E402
import voice  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + what)


# ---- parsing the directive lines ---------------------------------------------------------------
text, brief, vdir = persona.split("Arr, turn in Supplies to Tannok, matey!\nPERSONA: A salty pirate captain.\n"
                                  "VOICE: Gravelly, booming, rolling R's.")
check(text == "Arr, turn in Supplies to Tannok, matey!", "directive lines stripped: %r" % text)
check(brief == "A salty pirate captain." and vdir == "Gravelly, booming, rolling R's.", "brief and voice read")
check(persona.split("Fine, back to normal.\npersona: OFF")[1] == "", "PERSONA: off clears")
check(persona.split("Go east.")[1:] == (None, None), "no directive: nothing changes")
check(len(persona.clean("x" * 2000)) == persona.MAX_CHARS, "a brief is capped")
check(persona.short_name("A grizzled dwarf drill sergeant from Ironforge. Barks.") ==
      "A grizzled dwarf drill sergeant from Ironforge", "short name is the first sentence")

# ---- early speech never says the directive lines -----------------------------------------------
said = []
es = EarlySpeech(lambda t, u: said.append(t), 50)
es("Yarr, east ye go! ")
es("\nPERSONA: A pirate. ")
check(said == ["Yarr, east ye go!"], "early speech stops at PERSONA: %r" % said)

# ---- the live setting takes text -----------------------------------------------------------------
cfg = {}
check(tuning.apply(cfg, {"persona": "  a   pirate ", "persona_voice": "gruff"}) ==
      {"persona": "a pirate", "persona_voice": "gruff"}, "persona and its voice are live text settings")
try:
    tuning.apply({}, {"persona": 3})
    check(False, "a non-text persona is refused")
except ValueError:
    pass


# ---- the whole turn, against a fake Claude ---------------------------------------------------------
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


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), A)
threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True).start()
os.environ["ANTHROPIC_API_KEY"] = "test"
saved, infos = {}, []
c = coach.Coach({"api_url": "http://127.0.0.1:%d/v1/messages" % srv.server_address[1],
                 "fast_model": "claude-haiku-4-5-20251001", "deep_model": "claude-haiku-4-5-20251001",
                 "stream_speech": False},
                GlideDB("/x.db"), lambda m: infos.append(m) if m.get("kind") == "info" else None, offline_ok=True,
                deps=CoachDeps(on_toggle=lambda k, v: saved.__setitem__(k, v)))
gs = GameState()
gs.update({"t": "1000", "l": "7", "x": "2000", "X": "4500", "cl": "WARRIOR", "mp": "1426", "px": "4650", "py": "5300"},
          {"pg": "B", "cl": "WARRIOR", "ra": "Dwarf", "fa": "Alliance", "nm": "T"})
c.state = gs
plain = c._word_limit(True, False, True)


def system_text(b):
    s = b.get("system")
    return s if isinstance(s, str) else "\n".join(x.get("text", "") for x in s)


def user_text(b):
    m = b["messages"][-1]["content"]
    return m if isinstance(m, str) else "".join(x.get("text", "") for x in m if x.get("type") == "text")


check("Roleplay:" in system_text({"system": c._system_blocks()}), "the coach rules say how to take a role")
check(len(c._system_blocks()) == 1, "no persona block before one is asked for")

A.replies.append("Listen up, recruit! Turn in Supplies to Tannok, 20 yards east, double time!\n"
                 "PERSONA: A grizzled dwarf drill sergeant from Ironforge. Calls you recruit, barks every order.\n"
                 "VOICE: Thick Scottish brogue, gravelly, shouting, impatient.\nFACTS: none")
r = c.answer_once("be my dwarf drill sergeant")["answer"]
check(r and r["text"] == "Listen up, recruit! Turn in Supplies to Tannok, 20 yards east, double time!",
      "the in-character answer, directive lines stripped: %r" % (r and r["text"]))
check(c.persona().startswith("A grizzled dwarf drill sergeant"), "the persona is set")
check(c.persona_voice().startswith("Thick Scottish brogue"), "its voice direction is set")
check(saved.get("persona") == c.persona() and saved.get("persona_voice") == c.persona_voice(), "both saved")
check(infos and infos[-1]["text"].startswith("Coach persona: A grizzled dwarf"), "the chat notes the change")
check(c._word_limit(True, False, True) > plain, "lines may run longer in character")

A.replies.append("Move it, recruit! Kill Wendigos in the cave north.\nFACTS: none")
c.answer_once("what now?")
blocks = c._system_blocks()
check(blocks[-1]["text"].startswith("YOU ARE IN CHARACTER") and "grizzled dwarf" in blocks[-1]["text"],
      "the persona block comes last")
check("YOU ARE IN CHARACTER" in system_text(A.bodies[-1]), "it was sent with the question")
check("Stay fully in character (A grizzled dwarf drill sergeant from Ironforge)" in user_text(A.bodies[-1]),
      "the request ends with the stay-in-character line")

# the app's narrator performs it
events = []
hub = type("Hub", (), {"publish": lambda self, k, d: events.append((k, d)), "on_first_client": lambda self, f: None})()
hv = voice.HubVoice({"enabled": True, "announce": False}, hub)
hv.say("Plain line.")
hv.style_fn = c.persona_voice
hv.say("Move it!")
check("style" not in events[0][1], "no style without a persona")
check(events[1][1]["style"].startswith("Thick Scottish brogue"), "speak events carry the voice direction")

A.replies.append("Sure, back to normal. Turn in Supplies to Tannok.\nPERSONA: off\nFACTS: none")
r = c.answer_once("drop the act")["answer"]
check(r["text"] == "Sure, back to normal. Turn in Supplies to Tannok.", "plain answer when dropping the act")
check(c.persona() == "" and c.persona_voice() == "" and saved["persona"] == "" and saved["persona_voice"] == "",
      "the act is dropped and saved")
check(len(c._system_blocks()) == 1 and c._word_limit(True, False, True) == plain, "back to the plain coach")
check(infos[-1]["text"] == "Coach persona off", "the chat notes it")
hv.say("Plain again.")
check("style" not in events[-1][1], "no style once the act is dropped")
srv.shutdown()
print("OK")
