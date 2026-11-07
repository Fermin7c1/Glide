#!/usr/bin/env python3
"""The cast (Phase 5 of the coach's social side): a second character along with the one the coach plays (the
sidekick: "SIDEKICK: <name> | <brief>", or one played before by name; "SIDEKICK: off"). The replies give it lines of
its own ("[Fizzle] ..."), shown as "Fizzle: ..." and spoken in a voice of its own (the speak event's "voice"; its own
voice direction as the style); "let me talk to Fizzle" swaps them; in downtime some asides are duets, and the big
moments get both. Against a fake model server (no paid API).

Run: python3 tests/test_cast.py   (prints OK)
"""
import http.server
import json
import os
import sys
import threading
import time
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
from coach_deps import CoachDeps  # noqa: E402
from glidedb import GlideDB  # noqa: E402
from replies import EarlySpeech  # noqa: E402
import app as glide_app  # noqa: E402
import banter  # noqa: E402
import cast  # noqa: E402
import coach  # noqa: E402
import memory as memory_mod  # noqa: E402
import persona  # noqa: E402
import voice  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


# ---- the parts of a reply, by who says them ------------------------------------------------------------------
rows = cast.parts("Move it, recruit!\n[Fizzle] Ooh, can I blow it up?\n[fizzle] Please?\n[Sarge] No.\nNow go.",
                  "Sarge", "Fizzle")
check(rows == [{"who": "Sarge", "text": "Move it, recruit!"}, {"who": "Fizzle", "text": "Ooh, can I blow it up? Please?",
                                                                "side": True},
               {"who": "Sarge", "text": "No. Now go."}], rows)
check(cast.display(rows, "Sarge") == "Move it, recruit!\nFizzle: Ooh, can I blow it up? Please?\nNo. Now go.",
      cast.display(rows, "Sarge"))
# what the conversation keeps: the form the model writes
check(cast.script(rows) == "Move it, recruit!\n[Fizzle] Ooh, can I blow it up? Please?\nNo. Now go.", cast.script(rows))
# "[Fizzle]: line" and "[Fizzle] - line", one written mid-line; a link, or another bracketed name, left as it is
rows = cast.parts("[Fizzle]: Boom?\nGo! [Fizzle] - Now?\n[Wowhead](https://x.y) has it.\n[Hogger] is near.\n[Sarge] Move.",
                  "Sarge", "Fizzle")
check(rows == [{"who": "Fizzle", "text": "Boom?", "side": True}, {"who": "Sarge", "text": "Go!"},
               {"who": "Fizzle", "text": "Now?", "side": True},
               {"who": "Sarge", "text": "[Wowhead](https://x.y) has it. [Hogger] is near. Move."}], rows)
check(cast.voice_for("Fizzle") == cast.voice_for("fizzle") and
      cast.voice_for("Fizzle", avoid=cast.voice_for("Fizzle")) != cast.voice_for("Fizzle"), "its own voice, the same each time")
check(persona.split("Aye.\nSIDEKICK: Fizzle | A twitchy gnome tinker who blows things up.")[1]["sidekick"] ==
      ("Fizzle", "A twitchy gnome tinker who blows things up."), "a new sidekick")
check(persona.split("Aye.\nSIDEKICK: Fizzle")[1]["sidekick"] == ("Fizzle", ""), "one played before, by name")
check(persona.split("Bye, gnome.\nSIDEKICK: off")[1]["sidekick"] == ("", ""), "sent away")
said = []
es = EarlySpeech(lambda t, u: said.append(t), 50)
es("Move it, recruit! ")
es("\n[Fizzle] Ooh, can I blow it up? ")
check(said == ["Move it, recruit!"], "the sidekick's line isn't spoken early (its own voice, later): %r" % said)


def early(*chunks):
    out = []
    sp = EarlySpeech(lambda t, u: out.append(t), 50)
    for x in chunks:
        sp(x)
    return out


# the SIDEKICK line is never said (and so the reply isn't said again: its start still matches), on its own line or
# mid-line; nor a sidekick's line written mid-line; a link mid-line is said
check(early("Aye, the gnome comes along! ", "\nSIDEKICK: Fizzle | A twitchy gnome tinker. ") ==
      ["Aye, the gnome comes along!"], "SIDEKICK line")
check(early("Aye, he comes along. SIDEKICK: Fizzle | A twitchy gnome. ") == ["Aye, he comes along."], "mid-line")
check(early("Move it! [Fizzle] Boom? ") == ["Move it!"], early("Move it! [Fizzle] Boom? "))
check(early("See [Wowhead](https://wowhead.com) for it. Go. ") == ["See [Wowhead](https://wowhead.com) for it.", "Go."],
      early("See [Wowhead](https://wowhead.com) for it. Go. "))

# ---- the coach: a sidekick along, its part of the block, a reply's parts --------------------------------------
mem = memory_mod.Memory(None)
infos = []
saved = {}
c = coach.Coach({}, GlideDB("/x.db"), lambda m: infos.append(m), offline_ok=True,
                deps=CoachDeps(memory=mem, on_toggle=lambda k, v: saved.__setitem__(k, v)))
c._directives("Aye!\nPERSONA: A grizzled dwarf drill sergeant.\nNAME: Sarge\nVOICE: Gruff, booming.")
check(c.sidekick() == "", "none yet")
c._directives("A tinker, eh?\nSIDEKICK: Fizzle | A twitchy gnome tinker who blows things up.")
check(c.sidekick() == "Fizzle" and saved["sidekick"] == "Fizzle" and infos[-1]["text"] == "Sidekick: Fizzle", infos[-1])
fz = mem.character("fizzle")
check(fz["brief"].startswith("A twitchy gnome") and fz["tts_voice"] in cast.VOICES, fz)
block = c._system_blocks()[-1]["text"]
check("YOUR COMPANION ON THE ROAD: Fizzle" in block and '"[Fizzle]"' in block and "SIDEKICK:" in persona.BLOCK,
      "the companion's part of the block")
# no voice direction of its own yet: its brief performed (not the coach's calm one)
check(c.cast_voice("Fizzle") == ("Perform this character: " + fz["brief"], fz["tts_voice"]) and
      c.cast_voice(None) == ("Gruff, booming.", ""), c.cast_voice("Fizzle"))
mem.character_save("Fizzle", fz["brief"], "Squeaky, fast.")
check(c.cast_voice("Fizzle") == ("Squeaky, fast.", fz["tts_voice"]), "its own voice direction")
msg = c._cast({"kind": "banter", "text": "Up, recruit!\n[Fizzle] Boom time?", "speak": True})
check(msg["text"] == "Up, recruit!\nFizzle: Boom time?" and msg["parts"][1] == {"who": "Fizzle", "text": "Boom time?",
                                                                               "side": True}, msg)
check(c._cast({"kind": "callout", "text": "Turn in Supplies.", "speak": True}).get("parts") is None, "plain: no parts")
# the conversation the model reads back keeps its own form, not the chat's "Fizzle: ..."
c._emit(c._cast({"kind": "banter", "text": "Up!\n[Fizzle] Boom?", "speak": True}))
check(c.convo[-1]["text"] == "Up!\n[Fizzle] Boom?" and c.recent[-1]["text"] == "Up!\n[Fizzle] Boom?", c.convo[-1])
c._directives("No such one.\nSIDEKICK: Nobody")
check(infos[-1]["text"] == "No saved character called Nobody" and c.sidekick() == "Fizzle", "an unknown name")
n = len(infos)
c._directives("Hm.\nSIDEKICK: Sarg")                 # a name only the one played matches: not its own sidekick
check(c.sidekick() == "Fizzle" and len(infos) == n, infos[n:])

# "let me talk to Fizzle": Fizzle is played, Sarge comes along
c._directives("Right, gnome!\nPERSONA: recall Fizzle")
check(c.persona_name() == "Fizzle" and c.sidekick() == "Sarge", (c.persona_name(), c.sidekick()))
# each keeps its own voice: Fizzle's, now leading; Sarge, along, the app's voice as before
check(c.cast_voice(None) == ("Squeaky, fast.", fz["tts_voice"]) and c.cast_voice("Sarge") == ("Gruff, booming.", ""),
      (c.cast_voice(None), c.cast_voice("Sarge")))
c._directives("Away with ye.\nSIDEKICK: off")
check(c.sidekick() == "" and saved["sidekick"] == "" and infos[-1]["text"] == "Sidekick gone", infos[-1])
c._directives("Sarge again.\nPERSONA: recall Sarge")
c._directives("Back, gnome.\nSIDEKICK: Fizzle")
check(c.sidekick() == "Fizzle", "one played before, by name")
# the sidekick played anew by its name (no recall): they swap all the same, nobody lost
c._directives("Me now!\nPERSONA: %s\nNAME: Fizzle\nVOICE: Squeaky, fast." % fz["brief"])
check(c.persona_name() == "Fizzle" and c.sidekick() == "Sarge", (c.persona_name(), c.sidekick()))
c._directives("Sarge again.\nPERSONA: recall Sarge")
check(c.persona_name() == "Sarge" and c.sidekick() == "Fizzle", (c.persona_name(), c.sidekick()))
# dropping the act sends the sidekick away too (a later character doesn't bring it back unasked)
c._directives("Fine.\nPERSONA: off")
check(saved["sidekick"] == "" and infos[-1]["text"] == "Sidekick gone" and
      any(i["text"] == "Coach persona off" for i in infos[-3:]), infos[-3:])
c._directives("Back.\nPERSONA: recall Sarge")
check(c.persona_name() == "Sarge" and c.sidekick() == "", c.sidekick())
c._directives("Back, gnome.\nSIDEKICK: Fizzle")
c.cfg["persona"] = ""
check(c.sidekick() == "", "no character played: no sidekick")
c.cfg["persona"] = mem.character("sarge")["brief"]

# ---- the app speaks each part in its speaker's voice ----------------------------------------------------------
events = []
hub = types.SimpleNamespace(publish=lambda k, d: events.append(d), on_first_client=lambda f: None)
hv = voice.HubVoice({"enabled": True, "announce": False}, hub)
hv.style_fn, hv.cast_fn = c.persona_voice, c.cast_voice
a = glide_app.App.__new__(glide_app.App)
a.voice = hv
glide_app.App._say(a, {"kind": "banter", "text": "x", "tone": "barking", "parts": [
    {"who": "Sarge", "text": "Up, recruit!"}, {"who": "Fizzle", "text": "Boom time?", "side": True}]})
check(events[0] == {"text": "Up, recruit!", "interrupt": False, "style": "Gruff, booming. This line: barking.",
                    "tone": "barking"}, events[0])
check(events[1] == {"text": "Boom time?", "interrupt": False, "style": "Squeaky, fast.", "voice": fz["tts_voice"]},
      events[1])
events.clear()
glide_app.App._say(a, {"kind": "answer", "text": "x", "spoken": "Up, recruit!", "parts": [
    {"who": "Sarge", "text": "Up, recruit! Now."}, {"who": "Fizzle", "text": "Boom?", "side": True}]})
check([e["text"] for e in events] == ["Now.", "Boom?"], "the start said early isn't said again: %r" % events)
events.clear()
glide_app.App._say(a, {"kind": "banter", "text": "x", "tone": "barking", "parts": [
    {"who": "Fizzle", "text": "Boom?", "side": True}, {"who": "Sarge", "text": "No."}]})
check("tone" not in events[0] and events[1].get("tone") == "barking", "the tone with the main's first line: %r" % events)


# ---- banter: duets in downtime, both at the big moments --------------------------------------------------------
class A(http.server.BaseHTTPRequestHandler):
    bodies = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        A.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        out = {"content": [{"type": "text", "text": "TONE: gruff\nTwenty levels, recruit.\n[Fizzle] And only three "
                                                    "explosions!\nFACTS: none"}],
               "stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 3}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), A)
threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True).start()
os.environ["ANTHROPIC_API_KEY"] = "test"
c2 = coach.Coach({"api_url": "http://127.0.0.1:%d/v1/messages" % srv.server_address[1],
                  "fast_model": "claude-haiku-4-5-20251001", "deep_model": "claude-sonnet-5-5"}, GlideDB("/x.db"),
                 lambda m: infos.append(m), offline_ok=True, deps=CoachDeps(memory=mem))
c2.cfg.update(persona=mem.character("sarge")["brief"], persona_name="Sarge", sidekick="Fizzle")
b = c2.banter
req = b.request(25, {"kind": "milestone", "t": time.time(), "text": "Reached level 20 in Westfall"})
check("Fizzle reacts too, on a line of their own starting with [Fizzle]" in req, "both at a milestone")
banter.random.random = lambda: 0.0
check("a short back-and-forth between you and Fizzle" in b.request(25, None, "flying"), "a duet in downtime")
check("back-and-forth" not in b.request(25, None, "questing"), "not mid-questing")
b.moment = {"kind": "milestone", "t": time.time(), "text": "Reached level 20 in Westfall"}
c2.state = None
got = b.speak()
check(got and got["text"] == "Twenty levels, recruit.\nFizzle: And only three explosions!" and
      got["parts"][1]["side"] and got.get("tone") == "gruff", got)
check("YOUR COMPANION ON THE ROAD: Fizzle" in "\n".join(x["text"] for x in A.bodies[-1]["system"]), "in the block")
srv.shutdown()
print("OK")
