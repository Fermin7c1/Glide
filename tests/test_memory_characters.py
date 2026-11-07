#!/usr/bin/env python3
"""The characters on the Memory page (the engine's side): GET /memory also gives the characters the coach played
(who each is, its voice, who the player is to it, its scene, its past, its own story threads) and which one is played;
POST {"op": "character", name, field, text, index?, was?} changes one line ("" clears it, or removes an item; index
None adds to its past; was: the item's words when the page was drawn, found by them if the list moved), {"op":
"character_forget", name} forgets one. Changing the played character's description or voice changes the act too;
names are exact.

Run: python3 tests/test_memory_characters.py   (prints OK)
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
from coach_deps import CoachDeps  # noqa: E402
from glidedb import GlideDB  # noqa: E402
import app as glide_app  # noqa: E402
import coach  # noqa: E402
import memory as memory_mod  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


def raises(fn, *a):
    try:
        fn(*a)
    except ValueError:
        return True
    return False


d = tempfile.mkdtemp(prefix="glide-memory-characters-")
try:
    mem = memory_mod.Memory(os.path.join(d, "coach_memory.json"))
    saved, infos = {}, []
    c = coach.Coach({}, GlideDB("/x.db"), lambda m: infos.append(m), offline_ok=True,
                    deps=CoachDeps(memory=mem, on_toggle=lambda k, v: saved.__setitem__(k, v)))
    c._directives("Aye!\nPERSONA: A twitchy gnome tinker.\nNAME: Fizzle\nLORE: Grew up in Gnomeregan.")
    c._directives("Aye!\nPERSONA: A grizzled dwarf drill sergeant.\nNAME: Sarge\nVOICE: Gruff, booming.\n"
                  "LORE: Lost his beard to a rocket.\nROLE: Their drill sergeant; calls them recruit.")
    mem.character_add("Fizzle", "plot", ["Wants his workshop back."])

    a = glide_app.App.__new__(glide_app.App)
    a.memory, a.coach = mem, c
    a.on_message = lambda m: infos.append(m)
    view = a.on_memory(None)
    check("notes" in view and view["playing"] == "Sarge" and [x["name"] for x in view["characters"]] ==
          ["Sarge", "Fizzle"], view)
    sg, fz = view["characters"]
    check(sg == {"name": "Sarge", "brief": "A grizzled dwarf drill sergeant.", "voice": "Gruff, booming.",
                 "role": "Their drill sergeant; calls them recruit.", "scene": "", "lore": ["Lost his beard to a rocket."],
                 "plot": [], "last": sg["last"]}, sg)
    check(fz["lore"] == ["Grew up in Gnomeregan."] and fz["plot"] == ["Wants his workshop back."] and fz["role"] == "",
          fz)
    json.dumps(view)                                     # (what the route sends)

    # one line at a time: set, change an item, add to the past, remove an item, clear
    ch = {x["name"]: x for x in a.on_memory({"op": "character", "name": "Fizzle", "field": "role",
                                            "text": "  The  boss; calls them boss. "})["characters"]}
    check(ch["Fizzle"]["role"] == "The boss; calls them boss.", ch["Fizzle"])
    a.on_memory({"op": "character", "name": "Fizzle", "field": "lore", "index": 0, "text": "Grew up under Gnomeregan."})
    a.on_memory({"op": "character", "name": "Fizzle", "field": "lore", "index": None, "text": "Hates boats."})
    check(mem.character("fizzle")["lore"] == ["Grew up under Gnomeregan.", "Hates boats."], mem.character("fizzle"))
    a.on_memory({"op": "character", "name": "Fizzle", "field": "lore", "index": 0, "text": ""})
    a.on_memory({"op": "character", "name": "Fizzle", "field": "plot", "index": 0, "text": ""})
    check(mem.character("fizzle")["lore"] == ["Hates boats."] and mem.character("fizzle")["plot"] == [], "removed")
    a.on_memory({"op": "character", "name": "Sarge", "field": "scene", "text": "Bounty hunters after Hogger."})
    check(mem.character("sarge")["scene"] == "Bounty hunters after Hogger." and mem.character("sarge")["scene_t"] > 0,
          "a scene set here is being played")
    a.on_memory({"op": "character", "name": "Sarge", "field": "scene", "text": ""})
    check(mem.character("sarge")["scene"] == "", "ended")
    for bad in ({"name": "Nobody", "field": "role", "text": "x"}, {"name": "Fizzle", "field": "brief", "text": " "},
                {"name": "Fizzle", "field": "lore", "index": 7, "text": "x"}, {"name": "Fizzle", "field": "hp", "text": "x"}):
        check(raises(a.on_memory, dict(bad, op="character")), bad)

    # the played character's description and voice are the act's too: it plays on as changed
    a.on_memory({"op": "character", "name": "Sarge", "field": "voice", "text": "Gravelly, slow."})
    check(c.persona_voice() == "Gravelly, slow." and saved.get("persona_voice") == "Gravelly, slow.", saved)
    a.on_memory({"op": "character", "name": "Sarge", "field": "brief", "text": "A grizzled dwarf drill sergeant, retired."})
    check(c.persona() == "A grizzled dwarf drill sergeant, retired." and c.persona_name() == "Sarge" and
          mem.character("sarge")["lore"] == ["Lost his beard to a rocket."], (c.persona(), c.persona_name()))
    a.on_memory({"op": "character", "name": "Sarge", "field": "voice", "text": ""})
    check(c.persona_voice() == "" and mem.character("sarge")["voice"] == "", "a voice cleared: the act's too")
    # another's voice is its own (the act unchanged)
    a.on_memory({"op": "character", "name": "Fizzle", "field": "voice", "text": "Squeaky, fast."})
    check(mem.character("fizzle")["voice"] == "Squeaky, fast." and c.persona_voice() == "", "not the act's")

    # a line of its past or story, by the words shown: found if the coach added to the list meanwhile
    threads = ["Wants his workshop back.", "Owes a goblin money.", "Is building a flying machine.",
               "Hides a map of Uldaman."]
    for x in threads:
        mem.character_edit("Fizzle", "plot", x)
    check(mem.character("fizzle")["plot"] == threads, mem.character("fizzle")["plot"])
    mem.character_add("Fizzle", "plot", ["Searches for his lost brother."])     # the coach: the oldest drops
    a.on_memory({"op": "character", "name": "Fizzle", "field": "plot", "index": 1, "text": "",
                 "was": "Owes a goblin money."})                                 # (the page drew it 2nd: now 1st)
    check(mem.character("fizzle")["plot"] == ["Is building a flying machine.", "Hides a map of Uldaman.",
                                              "Searches for his lost brother."], mem.character("fizzle")["plot"])
    check(raises(a.on_memory, {"op": "character", "name": "Fizzle", "field": "plot", "index": 0, "text": "x",
                               "was": "Wants his workshop back."}), "gone meanwhile: refused, nothing else changed")
    check(len(mem.character("fizzle")["plot"]) == 3, mem.character("fizzle")["plot"])
    # added twice (a double Enter): once
    a.on_memory({"op": "character", "name": "Fizzle", "field": "lore", "index": None, "text": "Fears water."})
    a.on_memory({"op": "character", "name": "Fizzle", "field": "lore", "index": None, "text": "Fears water."})
    check(mem.character("fizzle")["lore"].count("Fears water.") == 1, mem.character("fizzle")["lore"])

    # exactly the one named: a kept "Sarge Thunderbrew" isn't the one played ("Sarge")
    mem.character_save("Sarge Thunderbrew", "A dwarf brewer.")
    a.on_memory({"op": "character", "name": "Sarge Thunderbrew", "field": "brief", "text": "A dwarf brewer, retired."})
    check(c.persona_name() == "Sarge" and c.persona() == "A grizzled dwarf drill sergeant, retired.", c.persona())

    # forgetting: gone, on disk too; twice is fine; the one played plays on, its memory starting over
    view = a.on_memory({"op": "character_forget", "name": "Fizzle"})
    check({x["name"] for x in view["characters"]} == {"Sarge", "Sarge Thunderbrew"}, view["characters"])
    check(memory_mod.Memory(os.path.join(d, "coach_memory.json")).character("fizzle") is None, "forgotten on disk")
    check(len(a.on_memory({"op": "character_forget", "name": "Fizzle"})["characters"]) == 2, "twice: fine")
    view = a.on_memory({"op": "character_forget", "name": "Sarge"})
    check([x["name"] for x in view["characters"]] == ["Sarge Thunderbrew"] and c.persona() and view["playing"] == "",
          view)
finally:
    shutil.rmtree(d, ignore_errors=True)
print("OK")
