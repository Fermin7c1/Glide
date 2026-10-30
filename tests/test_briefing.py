#!/usr/bin/env python3
"""Quest briefings (objective read + the coach's explanation when a quest is accepted) and step videos (a RestedXP
step's YouTube link opens in a new browser window), their switches (dashboard settings, push-to-talk phrases) and the
app routing accepted quests to briefings instead of callouts.

Run: python3 tests/test_briefing.py   (prints OK)
"""
import http.server
import json
import os
import re
import sys
import tempfile
import threading
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
sys.path.append(ROOT)
import briefing  # noqa: E402
import tuning  # noqa: E402
from glidedb import GlideDB  # noqa: E402
from state import GameState  # noqa: E402

# ---- quest text placeholders ------------------------------------------------------------------------------
p = {"name": "Testwarrior", "class": "WARRIOR", "race": "Dwarf"}
assert briefing.clean_quest_text("Greetings, $N.$B$BA $c like you, a $R $Glad:lass;!", p) == \
    "Greetings, Testwarrior. A warrior like you, a Dwarf lad!"
assert briefing.clean_quest_text(None) == ""
assert briefing.clean_quest_text("Slay $2oa Roiling Winds and $1oa Converts.", p, [{"amount": 7}, {"amount": 5}]) == \
    "Slay 5 Roiling Winds and 7 Converts."

# ---- accepted quests are batched: one briefing once accepts stop for a moment, not again within 10 min ----
qb = briefing.QuestBriefs(delay=2.5)
qb.add(101, now=100.0)
qb.add("102", now=101.0)
qb.add(101, now=101.5)                       # duplicate in the queue
assert qb.due(now=103.0) == [] and qb.due(now=103.6) == [101, 102]
qb.add(101, now=200.0)                        # re-accepted soon after: no second briefing
assert qb.due(now=300.0) == []
qb.add(101, now=800.0)
assert qb.due(now=803.0) == [101]
qb.add("x", now=900.0)
assert qb.queue == []

# ---- tuning: the two switches -------------------------------------------------------------------------------
cfg = {}
tmp = tempfile.mkdtemp()
cp = os.path.join(tmp, "config.json")
assert tuning.apply(cfg, {"quest_briefings": False, "step_videos": True}, cp) == \
    {"quest_briefings": False, "step_videos": True}
assert json.load(open(cp))["companion"]["llm"] == {"quest_briefings": False, "step_videos": True}
try:
    tuning.apply(cfg, {"step_videos": "yes"}, cp)
    raise AssertionError("a non-boolean switch was accepted")
except ValueError:
    pass

# a known browser gets --new-window (a window of its own, not a tab in the one you have open)
fake = os.path.join(tmp, "chrome")
outf = os.path.join(tmp, "args.txt")
with open(fake, "w") as fh:
    fh.write("#!/bin/sh\necho \"$@\" > %s\n" % outf)
os.chmod(fake, 0o755)
if os.name != "nt":
    assert briefing.open_new_window("https://youtu.be/x", browser=fake) is True
    for _ in range(50):
        if os.path.exists(outf):
            break
        time.sleep(0.05)
    assert open(outf).read().strip() == "--new-window https://youtu.be/x"

KB = os.path.join(ROOT, "data", "kb.db")
if not os.path.exists(KB):
    print("SKIP: knowledge base part (no data/kb.db); the rest passed")
    raise SystemExit(77)

from coach import Coach, load_knowledge_layer  # noqa: E402

_kbo, _api, _why = load_knowledge_layer(KB)
kb = _kbo
pack = _api.KnowledgePack(kb)          # the engine reads kb.db through the pack
GK = "RestedXP Forever Dungeon Guide (A)||5-11 Dun Morogh"
links = briefing.step_links(pack, GK, 103)
assert links and all(briefing.YOUTUBE.match(u) for u, _ in links), links
assert briefing.step_links(pack, GK, None) == [] and briefing.step_links(None, GK, 103) == []
# RestedXP instructions for a quest: its work steps from the current step on, video/"click here" lines dropped
vag = kb.q("SELECT DISTINCT quest_id FROM rxp_action WHERE guide_key=? AND idx=105 AND quest_id IS NOT NULL", GK)
ins = briefing.rxp_instructions(pack, GK, 100, vag[0]["quest_id"])
assert len(ins) == 1 and ins[0]["text"].startswith("Kill Vagash. Loot him for his Fang.") and "CLICK" not in " ".join(i["text"] for i in ins) \
    and "reference" not in " ".join(i["text"] for i in ins).lower(), ins
assert briefing.rxp_instructions(pack, GK, 9999, vag[0]["quest_id"]) == [], "steps already behind you aren't read"
assert not briefing.YOUTUBE.match("https://www.wow-petopia.com/classic/training.php")

# ---- step videos: not the step the session starts on, once per clip, one window per step, never in combat ----
opened = []
sv = briefing.StepVideos(opener=opened.append)
res = lambda idx: (lambda: (pack, GK, idx))  # noqa: E731
st = {"guide": "5-11 Dun Morogh", "step": 40}
assert sv.check(dict(st), res(103)) == [] and opened == []            # session start: nothing
st["step"] = 41
assert sv.check(dict(st, combat=True), res(103)) == [] and opened == []  # in combat: waits
out = sv.check(dict(st), res(103))
assert len(opened) == 1 and opened[0] == links[0][0] and out[0][2] is True, (out, opened)
assert all(o[2] is False for o in out[1:]) and len(out) == len(links), "other clips are listed, not opened"
assert sv.check(dict(st), res(103)) == []                             # same step: nothing more
st["step"] = 42
assert sv.check(dict(st), res(105)) == [] and len(opened) == 1, "the same clip on a later step isn't reopened"
# a later step with a clip of its own (found in the data: RestedXP updates move steps around)
seen = {u for u, _ in links}
other = next(i for i in range(106, 400) if any(u not in seen for u, _ in briefing.step_links(pack, GK, i)))
st["step"] = 43
assert sv.check(dict(st), res(other), enabled=False, notes=False) == [] and len(opened) == 1   # switched off
st["step"] = 44
assert sv.check(dict(st), res(other)) and len(opened) == 2, (other, opened)

# ---- what a clip shows: its note comes with it, with or without the browser window ----------------------------
assert briefing.clip_key("https://youtu.be/Zg4FNWw-P5k?t=3815") == "Zg4FNWw-P5k@3815"
assert briefing.clip_key("https://www.youtube.com/watch?v=TTZZT3jpv1s") == "TTZZT3jpv1s@0"
assert briefing.clip_key("https://youtu.be/70PX093soq4?si=abc&t=1h2m3s") == "70PX093soq4@3723"
assert briefing.clip_key("https://www.youtube.com/shorts/AbCdEf12345") == "AbCdEf12345@0"
assert briefing.clip_key("https://example.com/") is None
sv2 = briefing.StepVideos(opener=opened.append)
notes = {}
st2 = {"guide": "5-11 Dun Morogh", "step": 1}
assert sv2.check(dict(st2), res(103)) == []
st2["step"] = 2
note = {"instruction": "Jump onto the cart.", "detail": "From the cart, the roof.", "confidence": "high"}
out = sv2.check(dict(st2), res(103), enabled=False, note_fn=lambda u: notes.setdefault(u, note))
assert out and all(o[2] is False and o[3] is note for o in out), "notes without opening a window"
assert len(opened) == 2, "the browser stays shut when only notes are on"
st2["step"] = 3
assert sv2.check(dict(st2), res(other), notes=False, note_fn=lambda u: "x")[0][3] is None
# the shipped notes file: every note belongs to a clip RestedXP links, and reads as one short instruction
import os as _os
NOTES = _os.path.join(ROOT, "knowledge", "step_videos.json")
if _os.path.exists(NOTES):
    shipped = json.load(open(NOTES, encoding="utf-8"))["clips"]
    for key, n in shipped.items():
        assert re.fullmatch(r"[\w-]{6,}@\d+", key), key
        assert n["instruction"] and len(n["instruction"]) <= 400 and "\n" not in n["instruction"], key
        assert not n.get("detail") or len(n["detail"]) <= 1500, key
        assert n["steps"], key
        assert n["confidence"] in ("high", "medium", "low"), key
    for key, n in shipped.items():
        url = "https://youtu.be/%s?t=%s" % tuple(key.split("@"))
        said = briefing.video_note(url, path=NOTES)
        assert (said is None) if n["confidence"] == "low" else said["instruction"] == n["instruction"], key

# ---- coach: briefing through a fake Claude API -------------------------------------------------------------


class A(http.server.BaseHTTPRequestHandler):
    bodies = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        A.bodies.append(b)
        out = {"content": [{"type": "text", "text": "Eric's campfire is 30 yards north. Sit, wait about ten seconds for "
                            "Boosted Rest, then turn in.\nFACTS: q:96608"}],
               "stop_reason": "end_turn", "usage": {"input_tokens": 40, "output_tokens": 20}}
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
           "deep_model": "claude-opus-5-5", "briefing_delay_s": 0}, GlideDB("/x.db"), msgs.append, kb=KB,
          offline_ok=True)
gs = GameState()
core = {"t": "1000", "l": "7", "x": "2000", "X": "4500", "cl": "WARRIOR", "mp": "1426", "px": "4650", "py": "5300",
        "st": "5"}
for page in ({"pg": "B", "cl": "WARRIOR", "ra": "Dwarf", "fa": "Alliance", "nm": "Testwarrior"},
             {"pg": "G", "gn": "05-11 Dun Morogh", "gg": "RestedXP Forever Guide (A)"}, {}):
    gs.update(core, page)
c.state = gs
m = c.briefings.brief([96608])
assert m and m["kind"] == "briefing" and m["speak"], m
# RestedXP's own instructions for the quest are read (its "complete" step), then the coach's explanation
assert m["text"].startswith('New quest: The Great Outdoors. RestedXP: Type "/sit" in chat and wait for one minute '
                            "around the campfire. Eric's campfire is 30 yards north."), m["text"]
assert "FACTS" not in m["text"] and m["facts"] == ["q:96608"] and m["quests"] == [96608], m
body = A.bodies[-1]
user = body["messages"][0]["content"]
user = user if isinstance(user, str) else "".join(b["text"] for b in user)      # REFERENCE + live blocks
assert "QUEST BRIEFING (not a callout)" in user and "do not repeat" in user and '"The Great Outdoors"' in user
assert "rxp_instructions" in user
assert body["tool_choice"] == {"type": "none"}, "briefings share the callouts' cached tools + system prefix"
# several quests accepted together: one message, each objective, one explanation
m2 = c.briefings.brief([96608, 179])
assert m2["text"].startswith('2 new quests. The Great Outdoors. RestedXP: Type "/sit"'), m2["text"]
# a quest this guide doesn't handle: the game's objective text instead
assert "Dwarven Outfitters. Sten Stoutarm would like 8 pieces of Tough Wolf Meat." in m2["text"], m2["text"]
# AI unavailable: objective + a rule-based explanation from the knowledge base
c.llm.keys["anthropic"] = ""
m3 = c.briefings.brief([96608])
assert m3["model"] == "offline" and m3["text"].startswith("New quest: The Great Outdoors."), m3
m4 = c.briefings.brief([179])                          # a drop quest: the offline line names the drop source and rate
assert m4["model"] == "offline" and "Tough Wolf Meat drops from" in m4["text"], m4["text"]
print("offline:", m4["text"])
c.llm.keys["anthropic"] = "test"
# switched off: nothing
c.cfg["quest_briefings"] = False
assert c.briefings.brief([96608]) is None
c.brief(96608)
assert c.briefings.accepted.queue == []
c.cfg["quest_briefings"] = True

# ---- quests picked up back to back: listed quietly, each briefed when the player gets to it -----------------
hb = briefing.HeldBriefs(gap_s=20)
log = {101: {"name": "A", "obj": [[0, 8]]}, 102: {"name": "B", "obj": [[0, 5]]}, 103: {"name": "C"}}
hb.hold([101, 102, 103], now=0.0)
base = {"quests": log, "steps": [{"index": 4, "elements": [{"tag": "accept", "quest": 101}]}]}
assert hb.check(dict(base), now=5.0) is None, "nothing relates to them yet"
tf = lambda npc, qids: [102] if npc == 99 else []            # noqa: E731  creature 99 counts for quest 102
fight = dict(base, target={"npc": "99"}, combat=True)
assert hb.check(fight, now=6.0, target_fn=tf) is None and hb.quests[102]["armed"] == "target", "not mid-fight"
assert hb.check(dict(base), now=9.0, target_fn=tf) == (102, "target"), "after the fight"
step = dict(base, steps=[{"index": 5, "elements": [{"tag": "complete", "quest": 101}]}])
assert hb.check(step, now=15.0) is None, "at most one per gap_s"
assert hb.check(step, now=30.0) == (101, "step")
near = lambda q, s: q == 103                                 # noqa: E731
assert hb.check(dict(base), now=60.0, near_fn=near) == (103, "near") and not hb.quests
hb.hold([101, 102], now=100.0)
assert hb.check(dict(base, quests={102: {"name": "B", "done": True}}), now=200.0) is None and not hb.quests, \
    "done or abandoned quests drop out"
assert hb.check(dict(base, steps=[{"elements": [{"tag": "isOnQuest", "quest": 101}]}]), now=300.0) is None

# the coach: a burst is held (one quiet line, nothing spoken), then briefed at the target that counts for it
drops = c.pack.kb.q("SELECT d.creature_id AS c FROM quest_objective o JOIN creature_drop d ON d.item_id = o.target_id "
             "WHERE o.quest_id = 179 AND o.target_kind = 'item' LIMIT 1")
wolf = drops[0]["c"] if drops else None
assert wolf and c.pack.target_quest_ids(wolf, [96608, 179]) == [179], (wolf, c.pack.target_quest_ids(wolf, [179]))
real_summary = gs.summary
gs.summary = lambda: dict(real_summary(), quests={96608: {"name": "The Great Outdoors", "done": False, "obj": [[0, 1]]},
                                                   179: {"name": "Dwarven Outfitters", "done": False, "obj": [[0, 8]]}})
n0, said = len(msgs), []
c.deps.speak_fn = lambda t, kind, urgent: said.append(t)
c.brief(96608)
c.brief(179)
due = c.briefings.accepted.due(c._now() + 10)
assert due == [96608, 179]
c.briefings.hold(due)
assert msgs[-1]["kind"] == "info" and not msgs[-1]["speak"] and "Picked up 2 quests: The Great Outdoors, " \
    "Dwarven Outfitters." in msgs[-1]["text"] and not said, msgs[-1]
c.cfg["briefing_near_yd"] = 0                # proximity is checked on its own below
assert c.briefings.held_tick() is None, "nothing related yet (the step's quest isn't one of them)"
c.briefings.held_t = 0
gs.summary = lambda: dict(real_summary(), quests={96608: {"name": "The Great Outdoors", "done": False, "obj": [[0, 1]]},
                                                   179: {"name": "Dwarven Outfitters", "done": False, "obj": [[0, 8]]}},
                          target={"npc": str(wolf), "name": "wolf"})
m5 = c.briefings.held_tick()
assert m5 and m5["kind"] == "briefing" and m5["quests"] == [179] and m5["text"].startswith("Quest: Dwarven Outfitters."), m5
assert said and said[0].startswith("Quest: Dwarven Outfitters."), said
assert "accepted this quest earlier" in json.dumps(A.bodies[-1]) and "targeting a mob" in json.dumps(A.bodies[-1])
assert list(c.briefings.held.quests) == [96608]
# near its objective spots (The Great Outdoors: the campfire area the test character stands in)
c.cfg["briefing_near_yd"] = 150
c.briefings.held_t, c.briefings.held.last_t = 0, -1e9
gs.summary = lambda: dict(real_summary(), quests={96608: {"name": "The Great Outdoors", "done": False, "obj": [[0, 1]]}})
assert c.briefings.quest_spots.get((96608, 1426)), "spots from the knowledge base"
m6 = c.briefings.held_tick()
assert m6 and m6["quests"] == [96608] and "near its objectives" in json.dumps(A.bodies[-1]), m6
c.deps.speak_fn = None
gs.summary = real_summary
c.briefings.held.quests.clear()

# ---- push-to-talk switches: handled locally, saved through the app's callback ------------------------------
saved = []
c.deps.on_toggle = lambda k, v: saved.append((k, v))
n = len(A.bodies)
for phrase, key, val in (("quest briefings off", "quest_briefings", False), ("Turn on briefings.", "quest_briefings", True),
                         ("stop the videos", "step_videos", False), ("step videos on", "step_videos", True),
                         ("Switch off the quest briefings", "quest_briefings", False)):
    r = c._respond([], question=phrase)
    assert r and r["model"] == "local" and c.cfg[key] is val and saved[-1] == (key, val), (phrase, r, saved)
assert len(A.bodies) == n, "switch phrases never reach the API"
assert c._local_command("should I watch the video for this step?") is None
assert c._local_command("what are quest briefings?") is None
c.cfg["quest_briefings"] = True

# ---- the app: accepted quests go to briefings, not to a callout; switched off they stay callouts -----------
import app  # noqa: E402
from rules import Trigger  # noqa: E402

trig = [Trigger("quest_accepted", {"arg": 96608}, 3), Trigger("step_changed", {"from": 4, "to": 5}, 3)]
briefs, rest = app.split_briefings(trig, {"quest_briefings": True})
assert briefs == [96608] and [t.kind for t in rest] == ["step_changed"], (briefs, rest)
assert app.split_briefings(trig, {})[0] == [96608], "briefings are on by default"
briefs, rest = app.split_briefings(trig, {"quest_briefings": False})
assert briefs == [] and rest == trig, "switched off, an accepted quest stays a callout"
assert app.split_briefings([], {"quest_briefings": True}) == ([], [])
snap = tuning.snapshot(c)
assert snap["toggles"] == {"quest_briefings": True, "step_videos": True, "video_tips": True, "web_search": True,
                           "web_search_voice": False}, snap["toggles"]
srv.shutdown()

# the app's Settings page has both switches (the schema as the page gets it)
import desktop_schema  # noqa: E402
keys = desktop_schema.keys()
if keys is None:
    print("SKIP: the Settings page check (needs node and desktop/node_modules); the rest passed")
    raise SystemExit(77)
assert {"companion.llm.quest_briefings", "companion.llm.step_videos"} <= keys, "the switches are in the app's Settings"
print("OK")
