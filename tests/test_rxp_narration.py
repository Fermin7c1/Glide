#!/usr/bin/env python3
"""What the Guides page says aloud with Narrate on (desktop/src/renderer/rxp-narration.ts, compiled with the project's
TypeScript once for all the tests (desktop_ts.py) and run under Node): each step's instruction as spoken English, and
the way to the next step only when there's a real way to go.

Checks: RestedXP's markup never reaches the voice (colour codes, |T textures|t, ">>", [item] brackets, quest ids);
the instruction is said once even when the text and the verbs both name it; the transition is said for a walk of
more than TRANSITION_MIN_YD yards, a flight, a hearth, a deathskip, a boat and a new zone, and not for the same NPC,
a few yards or a step without a position; a worked-out route gives its compass words, rounded yards and a landmark,
no route a straight bearing, and no zone size no number.

Needs node and desktop/node_modules (skips with 77 without them)."""
import json
import os
import subprocess
import sys

import desktop_ts

HERE = os.path.dirname(os.path.abspath(__file__))
DESK = os.path.join(HERE, "..", "desktop")
SRC = os.path.join(DESK, "src", "renderer", "rxp-narration.ts")

node = desktop_ts.node
if not node or not desktop_ts.available():
    print("SKIP: needs node and desktop/node_modules (cd desktop && npm ci)")
    sys.exit(77)

MAPS = {"1411": {"name": "Durotar", "parent": 1414, "w_yd": 5250, "h_yd": 3500, "rect": [60, 45, 75, 60]},
        "1413": {"name": "The Barrens", "parent": 1414, "w_yd": 10500, "h_yd": 7000, "rect": [45, 45, 75, 75]},
        "1414": {"name": "Kalimdor", "parent": 947, "w_yd": 36000, "h_yd": 24000, "rect": None}}


def act(verb, raw=None, **kw):
    a = {"line": None, "verb": verb, "text": None, "raw_text": raw, "cond": None, "quest_id": None, "quest_name": None,
         "entity_kind": None, "entity_id": None, "entity_name": None, "units": None, "qty": None, "zone": None,
         "ui_map": None, "x": None, "y": None, "location": None}
    a.update(kw)
    return a


def step(*actions, text=None):
    return {"actions": list(actions), "text": text}


KALTUNK = step(act("goto"), act("text", "|Tinterface/worldmap/chatbubble_64grey.blp:20|tTalk to |cRXP_FRIENDLY_Kaltunk|r"),
               act("accept", "Accept Your Place In The World"), act("target", units=["Kaltunk"], entity_name="Kaltunk"))
TRAIN = step(act("text", "|Tinterface/worldmap/chatbubble_64grey.blp:20|tTalk to |cRXP_FRIENDLY_Frang|r"),
             act("train", "Train |T132333:0|t[Battle Shout]"), act("target", units=["Frang"]))
BUY = step(act("text", "|cRXP_BUY_Buy|r |T132794:0|t[Refreshing Spring Water] |cRXP_BUY_from her|r"),
           act("collect", entity_id=159), act("money"))
ARROWS = step(act("text", ">>Kill |cRXP_ENEMY_Mottled Boars|r. Loot them for |cRXP_LOOT_[Boar Tusk]|r\\nThen come back"))
BY_ID = step(act("accept", quest_id=4641), act("turnin", quest_id=788, quest_name="Cutting Teeth"))
DUP = step(act("text", "Talk to |cRXP_FRIENDLY_Hana'zua|r"), act("text", "Talk to |cRXP_FRIENDLY_Hana'zua|r"),
           act("turnin", quest_id=790, quest_name="Sarkoth"))
BARE = step(act("goto"))
MOB_ONLY = step(act("mob", units=["Vile Familiar"]))
FLY = step(act("fly", location="Orgrimmar"))
VARIANTS = step(act("text", "|cRXP_WARN_Kill |cRXP_ENEMY_Mottled Boars|r. Loot them until you have 35 copper|r"),
                act("text", "|cRXP_WARN_Kill |cRXP_ENEMY_Mottled Boars|r. Loot them until you have 60 copper|r"),
                act("text", "Kill |cRXP_ENEMY_Vile Familiars|r"))
WORLD = step(act("text", "Use |cff00ff00quest #4641|r here"))


def leg(*segments, mode="walk"):
    return {"from": 1, "a": -1, "to": 2, "b": -1, "mode": mode, "yd": None, "s": None, "approx": False,
            "segments": list(segments)}


def seg(mode, yd=None, dirs=None, through=None, to=None):
    return {"mode": mode, "maps": {}, "yd": yd, "s": None, "dirs": dirs or [], "through": through or [], "to": to}


A = {"ui_map": 1411, "x": 50, "y": 50, "zone": "Durotar", "who": "Gornek"}
NEAR = {"ui_map": 1411, "x": 50.4, "y": 50.3, "zone": "Durotar", "who": "Kaltunk"}
NORTH = {"ui_map": 1411, "x": 50, "y": 40, "zone": "Durotar", "who": "Kaltunk"}
SAME_NPC = {"ui_map": 1411, "x": 60, "y": 60, "zone": "Durotar", "who": "gornek"}
NOPOS = {"ui_map": 1411, "x": None, "y": None, "zone": "Durotar", "who": "Kaltunk"}
BARRENS = {"ui_map": 1413, "x": 80, "y": 20, "zone": "The Barrens", "who": None}
ROUTE = leg(seg("walk", 312, [{"go": "northeast", "yd": 250, "to": "Razor Hill"}, {"go": "north", "yd": 62, "to": None}],
                ["Valley of Trials", "Razor Hill", "Burning Blade Coven"]))
TURNS = leg(seg("walk", 900, [{"go": "north", "yd": 450, "to": None}, {"go": "east", "yd": 450, "to": "Sen'jin Village"}]))
SHORT = leg(seg("walk", 30, [{"go": "east", "yd": 30, "to": None}]))
FLIGHT = leg(seg("walk", 40), seg("fly", 4000, to="Orgrimmar"), seg("walk", 210, [{"go": "west", "yd": 210, "to": None}]),
             mode="fly")
HEARTH = leg(seg("hearth", 3000, to="Razor Hill"), mode="hearth")
DEATH = leg(seg("death", 800, to="Razor Hill"), seg("walk", 20), mode="death")
BOAT = leg(seg("boat", 9000, to="Booty Bay"), mode="boat")

CALLS = [
    ["cleanRxpText", "|cRXP_WARN_Kill |cRXP_ENEMY_Mottled Boars|r.|r"],
    ["cleanRxpText", ">>Loot |T1234:0|t[Sarkoth's Mangled Claw]"],
    ["cleanRxpText", "|Hitem:123|h[Rough Arrows]|h from npc:Duokna:3158"],
    ["cleanRxpText", None],
    ["stepLine", KALTUNK], ["stepLine", TRAIN], ["stepLine", BUY], ["stepLine", ARROWS], ["stepLine", BY_ID],
    ["stepLine", BY_ID, None, {"quests": {"4641": "Sarkoth"}}], ["stepLine", DUP], ["stepLine", BARE],
    ["stepLine", MOB_ONLY], ["stepLine", FLY], ["stepLine", WORLD],
    ["stepLine", KALTUNK, [KALTUNK["actions"][2]]], ["stepLine", VARIANTS],
    ["stepWho", KALTUNK], ["stepWho", TRAIN], ["stepWho", ARROWS], ["stepVia", FLY], ["stepVia", KALTUNK],
    ["transitionLine", {"from": A, "to": NORTH, "legs": [ROUTE], "maps": MAPS}],
    ["transitionLine", {"from": A, "to": dict(NORTH, who=None), "legs": [TURNS], "maps": MAPS}],
    ["transitionLine", {"from": A, "to": NORTH, "legs": [SHORT], "maps": MAPS}],
    ["transitionLine", {"from": A, "to": dict(NORTH, who="Thrall"), "legs": [FLIGHT], "maps": MAPS}],
    ["transitionLine", {"from": A, "to": dict(NORTH, who="Thrall"), "legs": [FLIGHT], "viaSaid": True, "maps": MAPS}],
    ["transitionLine", {"from": A, "to": NORTH, "legs": [HEARTH], "maps": MAPS}],
    ["transitionLine", {"from": A, "to": NORTH, "legs": [DEATH], "maps": MAPS}],
    ["transitionLine", {"from": A, "to": NORTH, "legs": [BOAT], "maps": MAPS}],
    ["transitionLine", {"from": A, "to": NORTH, "maps": MAPS}],
    ["transitionLine", {"from": A, "to": NORTH, "maps": None}],
    ["transitionLine", {"from": A, "to": NEAR, "maps": MAPS}],
    ["transitionLine", {"from": A, "to": NEAR, "maps": None}],
    ["transitionLine", {"from": A, "to": SAME_NPC, "maps": MAPS}],
    ["transitionLine", {"from": A, "to": NOPOS, "maps": MAPS}],
    ["transitionLine", {"from": NOPOS, "to": A, "maps": MAPS}],
    ["transitionLine", {"from": A, "to": BARRENS, "maps": MAPS}],
    ["transitionLine", {"from": A, "to": NOPOS, "via": {"mode": "fly", "to": "Orgrimmar"}, "maps": MAPS}],
    ["transitionLine", {"from": A, "to": NORTH, "via": {"mode": "hearth", "to": None}, "viaSaid": True, "maps": MAPS}],
    ["roundYards", 7], ["roundYards", 46], ["roundYards", 312], ["roundYards", 349], ["roundYards", 1234],
    ["spokenYards", 2460],
    ["compassWord", 0], ["compassWord", 44], ["compassWord", 200], ["compassWord", 338], ["compassWord", -10],
]

WANT = [
    "Kill Mottled Boars.",
    "Loot Sarkoth's Mangled Claw.",
    "Rough Arrows from Duokna.",
    "",
    "Talk to Kaltunk. Accept Your Place In The World.",
    "Talk to Frang. Train Battle Shout.",
    "Buy Refreshing Spring Water from her.",
    "Kill Mottled Boars. Loot them for Boar Tusk. Then come back.",
    "Turn in Cutting Teeth.",
    "Accept Sarkoth. Turn in Cutting Teeth.",
    "Talk to Hana'zua. Turn in Sarkoth.",
    "",
    "Kill Vile Familiar.",
    "Fly to Orgrimmar.",
    "Use the quest here.",
    "Accept Your Place In The World.",
    "Kill Mottled Boars. Loot them until you have 35 copper. Kill Vile Familiars.",
    "Kaltunk", "Frang", None, {"mode": "fly", "to": "Orgrimmar"}, None,
    "Head north-east about 300 yards, past Razor Hill, to Kaltunk.",
    "Head north, then east about 900 yards to Sen'jin Village.",
    "",
    "Take the flight to Orgrimmar, then head west about 200 yards to Thrall.",
    "Head west about 200 yards to Thrall.",
    "Hearth to Razor Hill.",
    "Die and run back from the graveyard.",
    "Take the boat to Booty Bay.",
    "Head north about 350 yards to Kaltunk.",
    "Head north to Kaltunk.",
    "",
    "",
    "",
    "",
    "",
    "Head north-east about 650 yards, into The Barrens.",
    "Take the flight to Orgrimmar.",
    "",
    10, 50, 300, 350, 1200,
    "about 2,500 yards",
    "north", "north-east", "south", "north", "north",
]

mod = os.path.join(desktop_ts.renderer(), "renderer", "rxp-narration.js")     # (the tests' shared compile)
code = ("import(%s).then(m => console.log(JSON.stringify(%s.map(([f, ...a]) => m[f](...a)))))"
        % (json.dumps(desktop_ts.url(mod)), json.dumps(CALLS)))
r = subprocess.run([node, "--input-type=module", "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=60)
assert r.returncode == 0, "running rxp-narration failed: %s" % r.stderr[-2000:]
got = json.loads(r.stdout)

assert len(got) == len(WANT) == len(CALLS)
wrong = [(c[0], json.dumps(c[1:])[:120], g, w) for c, g, w in zip(CALLS, got, WANT) if g != w]
assert not wrong, "\n".join("%s %s\n   got:  %r\n   want: %r" % x for x in wrong)
for line in got:
    if isinstance(line, str):
        assert "|" not in line and "#" not in line and "[" not in line and ">>" not in line, line
print("OK")
