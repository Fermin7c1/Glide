#!/usr/bin/env python3
"""The characters on the Memory page (the engine's side): GET /memory also gives the characters the coach played
(who each is, its voice, who the player is to it, its scene, its past, its own story threads, its ties to the others)
and which one is played and which is along; POST {"op": "character", name, field, text, index?, other?} changes one
line ("" clears it, or removes an item; index None adds to its past), {"op": "character_forget", name} forgets one.
Changing the played character's description or voice changes the act too; forgetting the sidekick sends it away.

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
    c._directives("Aye!\nPERSONA: A grizzled dwarf drill sergeant.\nNAME: Sarge\nVOICE: Gruff, booming.\n"
                  "LORE: Lost his beard to a rocket.\nROLE: Their drill sergeant; calls them recruit.")
    c._directives("A tinker, eh?\nSIDEKICK: Fizzle | A twitchy gnome tinker.\nTIES: Fizzle was Sarge's apprentice.\n"
                  "SIDEKICK LORE: Grew up in Gnomeregan.")
    mem.character_add("Fizzle", "plot", ["Wants his workshop back."])

    a = glide_app.App.__new__(glide_app.App)
    a.memory, a.coach = mem, c
    a.on_message = lambda m: infos.append(m)
    view = a.on_memory(None)
    check("notes" in view and view["playing"] == "Sarge" and view["along"] == "Fizzle", view)
    sg, fz = {x["name"]: x for x in view["characters"]}["Sarge"], {x["name"]: x for x in view["characters"]}["Fizzle"]
    check(sg["brief"] == "A grizzled dwarf drill sergeant." and sg["voice"] == "Gruff, booming." and
          sg["role"] == "Their drill sergeant; calls them recruit." and sg["lore"] == ["Lost his beard to a rocket."] and
          sg["ties"] == [{"with": "Fizzle", "text": "Fizzle was Sarge's apprentice."}], sg)
    check(fz["lore"] == ["Grew up in Gnomeregan."] and fz["plot"] == ["Wants his workshop back."] and
          fz["ties"] == [{"with": "Sarge", "text": "Fizzle was Sarge's apprentice."}] and fz["role"] == "", fz)
    json.dumps(view)                                     # (what the route sends)

    # one line at a time: set, change an item, add to the past, remove an item, untie, clear
    ch = {x["name"]: x for x in a.on_memory({"op": "character", "name": "Fizzle", "field": "role",
                                            "text": "  The  boss; calls them boss. "})["characters"]}
    check(ch["Fizzle"]["role"] == "The boss; calls them boss.", ch["Fizzle"])
    a.on_memory({"op": "character", "name": "Fizzle", "field": "lore", "index": 0, "text": "Grew up under Gnomeregan."})
    a.on_memory({"op": "character", "name": "Fizzle", "field": "lore", "index": None, "text": "Hates boats."})
    check(mem.character("fizzle")["lore"] == ["Grew up under Gnomeregan.", "Hates boats."], mem.character("fizzle"))
    a.on_memory({"op": "character", "name": "Fizzle", "field": "lore", "index": 0, "text": ""})
    a.on_memory({"op": "character", "name": "Fizzle", "field": "plot", "index": 0, "text": ""})
    check(mem.character("fizzle")["lore"] == ["Hates boats."] and mem.character("fizzle")["plot"] == [], "removed")
    a.on_memory({"op": "character", "name": "Fizzle", "field": "ties", "other": "Sarge",
                 "text": "Old rivals, now friends."})
    check(mem.tie("Sarge", "Fizzle") == "Old rivals, now friends.", "retied on both")
    a.on_memory({"op": "character", "name": "Sarge", "field": "ties", "other": "Fizzle", "text": ""})
    check(mem.tie("Sarge", "Fizzle") == "" and mem.character("sarge")["ties"] == {} and
          mem.character("fizzle")["ties"] == {}, "untied on both")
    a.on_memory({"op": "character", "name": "Sarge", "field": "scene", "text": "Bounty hunters after Hogger."})
    check(mem.character("sarge")["scene"] == "Bounty hunters after Hogger." and mem.character("sarge")["scene_t"] > 0,
          "a scene set here is being played")
    a.on_memory({"op": "character", "name": "Sarge", "field": "scene", "text": ""})
    check(mem.character("sarge")["scene"] == "", "ended")
    for bad in ({"name": "Nobody", "field": "role", "text": "x"}, {"name": "Fizzle", "field": "brief", "text": " "},
                {"name": "Fizzle", "field": "lore", "index": 7, "text": "x"}, {"name": "Fizzle", "field": "hp", "text": "x"},
                {"name": "Fizzle", "field": "ties", "other": "Nobody", "text": "x"}):
        check(raises(a.on_memory, dict(bad, op="character")), bad)

    # the played character's description and voice are the act's too: it plays on as changed
    a.on_memory({"op": "character", "name": "Sarge", "field": "voice", "text": "Gravelly, slow."})
    check(c.persona_voice() == "Gravelly, slow." and saved.get("persona_voice") == "Gravelly, slow.", saved)
    a.on_memory({"op": "character", "name": "Sarge", "field": "brief", "text": "A grizzled dwarf drill sergeant, retired."})
    check(c.persona() == "A grizzled dwarf drill sergeant, retired." and c.persona_name() == "Sarge" and
          mem.character("sarge")["lore"] == ["Lost his beard to a rocket."], (c.persona(), c.persona_name()))
    # the sidekick's voice is its own (no act changes)
    a.on_memory({"op": "character", "name": "Fizzle", "field": "voice", "text": "Squeaky, fast."})
    check(c.cast_voice("Fizzle")[0] == "Squeaky, fast." and c.persona_voice() == "Gravelly, slow.", c.cast_voice("Fizzle"))

    # forgetting: the sidekick goes with it; its ties go from the others; kept on disk
    mem.character_tie("Sarge", "Fizzle", "Old rivals.")
    view = a.on_memory({"op": "character_forget", "name": "Fizzle"})
    check([x["name"] for x in view["characters"]] == ["Sarge"] and view["along"] == "" and saved["sidekick"] == "" and
          mem.character("sarge")["ties"] == {} and infos[-1]["text"] == "Sidekick gone", (view, infos[-1]))
    check(memory_mod.Memory(os.path.join(d, "coach_memory.json")).character("fizzle") is None, "forgotten on disk")
    check(a.on_memory({"op": "character_forget", "name": "Fizzle"})["characters"][0]["name"] == "Sarge", "twice: fine")
    # the one played, forgotten: it plays on, its memory starting over
    view = a.on_memory({"op": "character_forget", "name": "Sarge"})
    check(view["characters"] == [] and c.persona() and view["playing"] == "", view)

    # ---- the voice: each one's own, as its lines go out now (voice_id); one picked is kept; "" its default -------
    c._directives("Aye!\nPERSONA: A grizzled dwarf drill sergeant.\nNAME: Sarge\nVOICE: Gruff.")
    c._directives("A tinker!\nSIDEKICK: Fizzle | A twitchy gnome tinker.")
    view = {x["name"]: x for x in a.on_memory(None)["characters"]}
    fz_voice = mem.character("fizzle")["tts_voice"]
    check(view["Fizzle"]["voice_id"] == view["Fizzle"]["tts_voice"] == fz_voice and fz_voice and
          view["Sarge"]["voice_id"] == "" == view["Sarge"]["tts_voice"], view)
    view = {x["name"]: x for x in a.on_memory({"op": "character", "name": "Fizzle", "field": "tts_voice",
                                              "text": "Microsoft George - English (United Kingdom)"})["characters"]}
    check(view["Fizzle"]["voice_id"] == "Microsoft George - English (United Kingdom)" and
          c.cast_voice("Fizzle")[1] == "Microsoft George - English (United Kingdom)", view["Fizzle"])
    a.on_memory({"op": "character", "name": "Sarge", "field": "tts_voice", "text": "onyx"})
    check(c.cast_voice(None)[1] == "onyx" and c.persona_voice() == "Gruff.", "the one played: its own voice, the act kept")
    a.on_memory({"op": "character", "name": "Fizzle", "field": "tts_voice", "text": ""})
    check(mem.character("fizzle")["tts_voice"] == "" and c.cast_voice("Fizzle")[1] == "",
          "its default beside one with a voice of its own: the app's voice (never the one played's)")

    # ---- a line of its past or story, by the words shown: found if the coach added to the list meanwhile ---------
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

    # ---- exactly the one named: a kept "Sarge Thunderbrew" isn't the one played ("Sarge") nor the sidekick ---------
    mem.character_save("Sarge Thunderbrew", "A dwarf brewer.")
    mem.character_save("Fizzle the Elder", "An old gnome.")
    a.on_memory({"op": "character", "name": "Sarge Thunderbrew", "field": "brief", "text": "A dwarf brewer, retired."})
    check(c.persona_name() == "Sarge" and c.persona() == "A grizzled dwarf drill sergeant.", c.persona())
    a.on_memory({"op": "character_forget", "name": "Fizzle the Elder"})
    check(c.sidekick() == "Fizzle", "forgetting another sends nobody away")

    # ---- ties: never more than MAX_TIES, edited too ------------------------------------------------------------
    m5 = memory_mod.Memory(None)             # (its own: MAX_CHARACTERS keeps 12)
    m5.character_save("Hub", "Knows everyone.")
    for i in range(memory_mod.MAX_TIES + 2):
        m5.character_save("Friend %d" % i, "A friend.")
        m5.character_edit("Hub", "ties", "Old friends, number %d." % i, other="Friend %d" % i)
    check(len(m5.character("hub")["ties"]) == memory_mod.MAX_TIES and
          m5.tie("Hub", "Friend %d" % (memory_mod.MAX_TIES + 1)), m5.character("hub")["ties"])

    # ---- the sidekick forgotten while out of character: Settings forgets it too (never made up again) ----------
    c._directives("Fine.\nPERSONA: off")
    c.cfg["sidekick"] = "Fizzle"             # (still named in Settings, nobody played)
    a.on_memory({"op": "character_forget", "name": "Fizzle"})
    check(c.cfg["sidekick"] == "" and saved["sidekick"] == "", c.cfg.get("sidekick"))
finally:
    shutil.rmtree(d, ignore_errors=True)
print("OK")
