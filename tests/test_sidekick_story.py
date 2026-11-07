#!/usr/bin/env python3
"""The sidekick's own story (the cast, cast.py): its past, who the player is to it, and how it and the character the
coach plays are tied. A reply's "SIDEKICK LORE: <fact>", "SIDEKICK ROLE: <who the player is to it>" and "TIES: <how
the two are tied>" lines keep them (the ties on both characters, so they hold after a swap); a sidekick first along
beside a character gets what it lacks of them made up once, in the background (persona.SIDE_SEED), never over what was
said. They ride in the companion's part of the block. Against a fake model server (no paid API).

Run: python3 tests/test_sidekick_story.py   (prints OK)
"""
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
from coach_deps import CoachDeps  # noqa: E402
from glidedb import GlideDB  # noqa: E402
from replies import EarlySpeech  # noqa: E402
import coach  # noqa: E402
import memory as memory_mod  # noqa: E402
import persona  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


# ---- the reply's lines ----------------------------------------------------------------------------------------------
text, found = persona.split("Aye.\nSIDEKICK LORE: Grew up in Gnomeregan.\nSIDEKICK ROLE: Their boss; calls them boss."
                            "\nTIES: Fizzle was Sarge's apprentice until the Gnomeregan mess.")
check(text == "Aye." and found["side_lore"] == ["Grew up in Gnomeregan."] and found["lore"] == [] and
      found["side_role"] == "Their boss; calls them boss." and found["role"] is None and
      found["ties"] == "Fizzle was Sarge's apprentice until the Gnomeregan mess.", (text, found))
text, found = persona.split("Done. SIDEKICK LORE: Hates boats. TIES: Old rivals, now friends.")
check(text == "Done." and found["side_lore"] == ["Hates boats."] and found["ties"] == "Old rivals, now friends.",
      (text, found))
text, found = persona.split("Ties: the quest chain ties into Westfall.\nLORE: Lost a bet to a gnome.")
check(text == "Ties: the quest chain ties into Westfall." and found["ties"] is None and
      found["lore"] == ["Lost a bet to a gnome."], "an answer's own \"Ties:\" stays; LORE is still the main's")


def early(*chunks):
    out = []
    sp = EarlySpeech(lambda t, u: out.append(t), 50)
    for x in chunks:
        sp(x)
    return out


check(early("Aye, boss. SIDEKICK LORE: Hates boats. ") == ["Aye, boss."], "never said: mid-line")
check(early("Go. ", "\nTIES: Old rivals. ") == ["Go."] and early("Go. ", "\nSIDEKICK ROLE: Boss. ") == ["Go."],
      "never said: its own line")
check(early("Go. SIDEKICK  LORE: Hates boats. ") == ["Go."] and early("Go. ", "\nTIES : Old rivals. ") == ["Go."] and
      persona.split("Go.\nTIES : Old rivals.")[1]["ties"] == "Old rivals.", "spaced as split reads it: never said")
check("with all the player said of it" in " ".join(persona.BLOCK.split()),
      "the reply that brings one along keeps the player's words in its brief")

# ---- kept: on the sidekick (never the main character), the tie on both ---------------------------------------------
mem = memory_mod.Memory(None)
c = coach.Coach({}, GlideDB("/x.db"), lambda m: None, offline_ok=True, deps=CoachDeps(memory=mem))
c._directives("Aye!\nPERSONA: A grizzled dwarf drill sergeant from Ironforge.\nNAME: Sarge\nVOICE: Gruff.")
c._directives("Ha!\nTIES: Nobody along yet.\nSIDEKICK LORE: Nobody's past.")
check(mem.character("sarge")["lore"] == [] and mem.character("sarge")["ties"] == {}, "no sidekick: nothing kept")
c._directives("A tinker, eh?\nSIDEKICK: Fizzle | A twitchy gnome tinker who blows things up.\n"
              "TIES: Fizzle was Sarge's apprentice until the Gnomeregan mess; they bicker like family.")
check(mem.tie("Sarge", "Fizzle") == mem.tie("fizzle", "sarge") ==
      "Fizzle was Sarge's apprentice until the Gnomeregan mess; they bicker like family.", "the SIDEKICK line's reply")
c._directives("Boom!\n[Fizzle] I once blew up a bank.\nSIDEKICK LORE: Once blew up a bank in Ironforge.\n"
              "SIDEKICK ROLE: The boss; calls them boss.")
fz, sg = mem.character("fizzle"), mem.character("sarge")
check(fz["lore"] == ["Once blew up a bank in Ironforge."] and fz["role"] == "The boss; calls them boss." and
      sg["lore"] == [] and not sg.get("role"), (fz, sg))
check(mem.character_tie("Sarge", "Fizzle", fz["ties"]["sarge"]) is False, "said again: no change")
check(mem.character_tie("Sarge", "Fizzle", "Old rivals.", fill=True) is False, "fill: only when there is none")
check(mem.character_tie("Fizzle", "Sarge", "Fizzle was Sarge's apprentice; now they're even.") and
      mem.tie("Sarge", "Fizzle") == "Fizzle was Sarge's apprentice; now they're even.", "a tie can change")
check(mem.character_tie("Fizzle", "Nobody", "x") is False and mem.character_tie("Fizzle", "fizzle", "x") is False,
      "only between two kept characters")
check(mem.character_tie("Sarge", "Fizzle", "Fizzle was Sarge's apprentice, and now they're even.") is False,
      "said again in nearly the same words: no change (no rewrite every reply)")

# ---- in the block: its past, its threads, the tie, who the player is to it, and how to add to them -----------------
mem.character_add("Fizzle", "plot", ["Wants to rebuild his lost workshop."])
block = c._system_blocks()[-1]["text"]
for want in ("Their past and their story so far: Once blew up a bank in Ironforge.",
             "Their own story threads: Wants to rebuild his lost workshop.",
             "You and Fizzle: Fizzle was Sarge's apprentice; now they're even.",
             "Who the player is to them: The boss; calls them boss.", '"SIDEKICK LORE:', '"TIES:', '"SIDEKICK ROLE:'):
    check(want in block, want)

# swapped: the tie holds from the other side; the player's role goes with each character
c._directives("Me now!\nPERSONA: recall Fizzle")
block = c._system_blocks()[-1]["text"]
check(c.sidekick() == "Sarge" and "You and Sarge: Fizzle was Sarge's apprentice; now they're even." in block and
      "The boss; calls them boss." in block.split("YOUR COMPANION ON THE ROAD")[0], "after the swap")
c._directives("Back.\nPERSONA: recall Sarge")

# the reply that swaps them: its sidekick lines are about the one the model had along (now played), never the other
sarge_role = mem.character("sarge").get("role")
c._directives("Fine, talk to the gnome.\n[Fizzle] Finally!\nPERSONA: recall Fizzle\n"
              "SIDEKICK LORE: Keeps a pet mechanical squirrel.\nSIDEKICK ROLE: The boss who finally listens.")
fz, sg = mem.character("fizzle"), mem.character("sarge")
check(c.persona_name() == "Fizzle" and "Keeps a pet mechanical squirrel." in fz["lore"] and
      fz["role"] == "The boss who finally listens." and "Keeps a pet mechanical squirrel." not in sg["lore"] and
      sg.get("role") == sarge_role, (fz, sg))
c._directives("Back.\nPERSONA: recall Sarge")

# a character set in Settings, not kept yet (its name only the start of its brief): a reply's tie still lands, and
# is what the block shows
m4 = memory_mod.Memory(None)
c4 = coach.Coach({"persona": "Thrall, Warchief of the Horde, weary and wise.", "sidekick": "Fizzle"}, GlideDB("/x.db"),
                 lambda m: None, offline_ok=True, deps=CoachDeps(memory=m4))
m4.character_save("Fizzle", "A twitchy gnome tinker.")
c4.cfg["sidekick"] = "Fizzle"
check(c4.sidekick() == "Fizzle", c4.sidekick())
c4._directives("Lok'tar.\nTIES: Thrall freed Fizzle from a Horde cage; Fizzle has followed him since.")
check(m4.tie(c4.main_name(), "Fizzle").startswith("Thrall freed Fizzle") and
      "You and Fizzle: Thrall freed Fizzle" in c4._system_blocks()[-1]["text"], m4.characters)


# ---- a sidekick first along: what it lacks, made up once, in the background ----------------------------------------
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


def until(cond):
    for _ in range(100):
        if cond():
            return True
        time.sleep(0.05)
    return False


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), A)
threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True).start()
os.environ["ANTHROPIC_API_KEY"] = "test"
c2 = coach.Coach({"api_url": "http://127.0.0.1:%d/v1/messages" % srv.server_address[1],
                  "fast_model": "claude-haiku-4-5-20251001", "deep_model": "claude-sonnet-5-5"}, GlideDB("/x.db"),
                 lambda m: None, offline_ok=True, deps=CoachDeps(memory=mem))
c2.cfg.update(persona=mem.character("sarge")["brief"], persona_name="Sarge", persona_voice="Gruff.")
c2._directives("A ranger, eh?\nSIDEKICK: Grim | A dour night elf ranger with a pet owl.\n"
               "SIDEKICK ROLE: The kid they swore to keep alive; calls them kid.")
A.replies.append(json.dumps({
    "backstory": ["Grew up in Auberdine.", "Lost her sister at Ashenvale.", "Never speaks of the Barrow Dens."],
    "samples": ["Quiet. The owl hears something.", "Sarge, your snoring scares the wolves.", "Not bad, kid.",
                "Ashenvale had trees like these.", "One arrow. That's all it took."],
    "plot": ["Hunts the satyr who killed her sister.", "Is teaching the owl to carry letters."],
    "ties": "Grim and Sarge fought side by side at Ashenvale; she still owes him her life and hates it.",
    "role": "Someone she barely tolerates."}))
c2.banter._seed_side()
check(until(lambda: (mem.character("grim") or {}).get("samples")), "seeded")
check(until(lambda: mem.tie("Sarge", "Grim")), "the tie seeded")
gr = mem.character("grim")
check(gr["lore"] == ["Grew up in Auberdine.", "Lost her sister at Ashenvale.", "Never speaks of the Barrow Dens."] and
      len(gr["samples"]) == 5 and len(gr["plot"]) == 2 and
      gr["role"] == "The kid they swore to keep alive; calls them kid." and
      mem.tie("Grim", "Sarge").startswith("Grim and Sarge fought side by side"), "what it lacked, never what was said: %r"
      % gr)
req = user_text(A.bodies[-1])
check("JSON object" in req and "Grim" in req and "dour night elf ranger" in req and "drill sergeant" in req and
      "The kid they swore to keep alive" in req, "the request knows them both and what was said")
n = len(A.bodies)
c2.banter._seed_side()
check(len(A.bodies) == n, "once a pair")

# one with its past, its lines, who the player is to it and its tie already: nothing asked
mem.character_add("Fizzle", "samples", ["Boom?", "Can I blow it up?"])
c2._directives("Back, gnome.\nSIDEKICK: Fizzle")
c2.banter._seed_side()
time.sleep(0.2)
check(c2.sidekick() == "Fizzle" and len(A.bodies) == n, len(A.bodies))

# a new name typed in Settings (never played): made up whole, its own voice, and the chat says it took
infos = []
c2._emit_raw = infos.append
c2.cfg["sidekick"] = "Bramble"
check(c2.sidekick() == "", "not kept yet")
A.replies.append(json.dumps({
    "brief": "A cheerful halfling-sized gnome herbalist who talks to plants.",
    "backstory": ["Raised in a Dun Morogh greenhouse.", "Poisoned a mayor by accident.", "Fears fire."],
    "samples": ["Peacebloom! Over there!", "Sarge, you're scaring the flowers.", "Lovely work, sprout.",
                "My greenhouse had glass like this.", "Nobody grows Earthroot like me."],
    "plot": ["Seeks the last seed of a lost Gnomeregan herb.", "Writes letters to a mayor she poisoned."],
    "ties": "Bramble once patched Sarge up after Gnomeregan; he pretends he doesn't remember.",
    "role": "Her sprout, whom she fusses over."}))
c2.banter._seed_side()
check(until(lambda: c2.sidekick() == "Bramble"), "made up and along")
check(until(lambda: mem.tie("Sarge", "Bramble")), "and tied")
br = mem.character("bramble")
req = user_text(A.bodies[-1])
check('"brief"' in req and "only the name is known" in req and "drill sergeant" in req, "the request asks who")
check(br["brief"].startswith("A cheerful") and br["tts_voice"] and len(br["lore"]) == 3 and br["role"] and
      any(i.get("text") == "Sidekick: Bramble" for i in infos), (br, infos))
n = len(A.bodies)
c2.cfg["sidekick"] = "Sarg"                  # a name only the one played matches: nothing made up
c2.banter._seed_side()
time.sleep(0.2)
check(len(A.bodies) == n and mem.character("sarg")["name"] == "Sarge", "not the one played")

# the Settings name changed while the model answered: nothing made up for a name no longer asked for
c2.cfg["sidekick"] = "Thistle"
A.replies.append(json.dumps({"brief": "A thorny dryad.", "backstory": [], "samples": [], "plot": [], "ties": "",
                             "role": ""}))
c2.cfg["sidekick"] = "Someone else"
check(c2.banter.seed_side("Thistle", "Sarge") is None and mem.character("thistle") is None, "no longer asked for")
# no brief in the answer: nothing kept, asked again later (not never)
c2.cfg["sidekick"] = "Thistle"
A.replies.append(json.dumps({"backstory": [], "samples": [], "plot": [], "ties": "", "role": ""}))
check(c2.banter.seed_side("Thistle", "Sarge") is None and mem.character("thistle") is None and
      time.time() < c2.banter.side_seeded[("Thistle", "Sarge")] < float("inf"), c2.banter.side_seeded)
srv.shutdown()

# ---- kept on disk; a malformed file's ties are dropped --------------------------------------------------------------
d = tempfile.mkdtemp(prefix="glide-sidekick-story-")
try:
    path = os.path.join(d, "coach_memory.json")
    m1 = memory_mod.Memory(path)
    m1.character_save("Sarge", "A grizzled dwarf.")
    m1.character_save("Fizzle", "A twitchy gnome.")
    m1.character_tie("Sarge", "Fizzle", "Old rivals.")
    m2 = memory_mod.Memory(path)
    check(m2.tie("Fizzle", "Sarge") == "Old rivals." and m2.character("sarge")["ties"] == {"fizzle": "Old rivals."},
          m2.character("sarge"))
    with open(os.path.join(d, "coach_bond.json"), encoding="utf-8") as fh:
        bond = json.load(fh)
    bond["characters"]["sarge"]["ties"] = {"fizzle": 5, "grim": "Owes her.", "x": ""}
    bond["characters"]["fizzle"]["ties"] = ["not", "a", "dict"]
    with open(os.path.join(d, "coach_bond.json"), "w", encoding="utf-8") as fh:
        json.dump(bond, fh)
    m3 = memory_mod.Memory(path)
    check(m3.character("sarge")["ties"] == {"grim": "Owes her."} and m3.character("fizzle")["ties"] == {},
          (m3.character("sarge"), m3.character("fizzle")))
finally:
    shutil.rmtree(d, ignore_errors=True)
print("OK")
