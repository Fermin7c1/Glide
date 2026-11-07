#!/usr/bin/env python3
"""What an older version left in a player's files is taken out when this one starts, so none of it reaches the coach:
a setting no longer used (settings.RETIRED) out of the config the engine reads and out of config.json (written once,
only then); in the memory (coach_bond.json), a second character kept beside the one played and never played itself,
the fields only that version kept, and the lines its replies gave another speaker ("[Name] ...") out of the
characters' lines and the kept conversation. The file is saved once, cleaned; a clean one is never rewritten.

Run: python3 tests/test_older_leftovers.py   (prints OK)
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
import coach  # noqa: E402
import glideconfig  # noqa: E402
import memory as memory_mod  # noqa: E402
import settings  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


d = tempfile.mkdtemp(prefix="glide-older-leftovers-")
try:
    # ---- config.json: a setting no longer used, out of what's read and out of the file ------------------------------
    cfg_path = os.path.join(d, "config.json")
    old = {"wow_dir": "C:/WoW", "companion": {"llm": {"sidekick": "Pip", "persona": "A grizzled dwarf.",
                                                      "persona_name": "Sarge"}, "voice": {"enabled": True}}}
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump(old, fh)
    check(all(k.count(".") == 1 for k in settings.RETIRED) and "llm.sidekick" in settings.RETIRED, settings.RETIRED)
    cfg = glideconfig.load(d)
    check("sidekick" not in cfg["llm"] and cfg["llm"]["persona"] == "A grizzled dwarf.", cfg["llm"])
    check(glideconfig.drop_retired(d) == ["llm.sidekick"], "taken out of the file")
    now = json.load(open(cfg_path, encoding="utf-8"))
    check(now == {"wow_dir": "C:/WoW", "companion": {"llm": {"persona": "A grizzled dwarf.", "persona_name": "Sarge"},
                                                     "voice": {"enabled": True}}}, now)
    t0 = os.path.getmtime(cfg_path)
    check(glideconfig.drop_retired(d) == [] and os.path.getmtime(cfg_path) == t0, "nothing left: not written again")

    # ---- the memory: a second character never played, the fields only it had, lines given another speaker ----------
    bond = {
        "characters": {
            "sarge": {"name": "Sarge", "brief": "A grizzled dwarf.", "voice": "Gruff.", "lore": ["Lost his beard."],
                      "last": 2, "tts_voice": "onyx", "ties": {"pip": "Pip was Sarge's apprentice."},
                      "lines": ["Move it, recruit!"],
                      "favorites": ["Twenty levels, recruit.\n[Pip] And only three explosions!", "[Pip] Boom?"],
                      "samples": ["Move it!", "Up, recruit."]},
            "pip": {"name": "Pip", "brief": "A twitchy gnome tinker.", "voice": "", "lore": ["Grew up in Gnomeregan."],
                    "last": 3, "tts_voice": "sage", "ties": {"sarge": "Pip was Sarge's apprentice."},
                    "samples": ["Boom?"], "role": "The boss."},
            # one that came along and was played after (a line of its own): kept, without the fields
            "grim": {"name": "Grim", "brief": "A dour ranger.", "voice": "", "lore": [], "last": 1, "tts_voice": "echo",
                     "lines": ["Quiet."]}},
        "convo": [
            {"t": 1000.0, "who": "player", "kind": "typed", "text": "[Pip] what do you think?"},
            {"t": 1001.0, "who": "coach", "kind": "banter", "text": "Up, recruit!\n[Pip] Boom time?"},
            {"t": 1002.0, "who": "coach", "kind": "banter", "text": "[Pip] Only me talking."},
            {"t": 1003.0, "who": "coach", "kind": "answer", "text": "See [Wowhead](https://wowhead.com) for it."}],
        "bond": {"sessions": 3}}
    bond_path = os.path.join(d, "coach_bond.json")
    with open(bond_path, "w", encoding="utf-8") as fh:
        json.dump(bond, fh)
    with open(os.path.join(d, "coach_memory.json"), "w", encoding="utf-8") as fh:
        json.dump({"notes": [], "next_id": 1}, fh)
    mem = memory_mod.Memory(os.path.join(d, "coach_memory.json"))
    check(set(mem.characters) == {"sarge", "grim"}, sorted(mem.characters))
    sg = mem.character("sarge")
    check(sg["favorites"] == ["Twenty levels, recruit."] and sg["lines"] == ["Move it, recruit!"] and
          sg["lore"] == ["Lost his beard."] and "tts_voice" not in sg and "ties" not in sg, sg)
    check("tts_voice" not in mem.character("grim") and mem.character("grim")["lines"] == ["Quiet."], "kept, tidied")
    check([(r["who"], r["text"]) for r in mem.convo] == [
        ("player", "[Pip] what do you think?"), ("coach", "Up, recruit!"),
        ("coach", "See [Wowhead](https://wowhead.com) for it.")], mem.convo)
    saved = open(bond_path, encoding="utf-8").read()
    check("tts_voice" not in saved and '"ties"' not in saved and "[Pip] Boom" not in saved and '"pip"' not in saved and
          '"sessions": 3' in saved, "saved cleaned, at once")
    t0 = os.path.getmtime(bond_path)
    again = memory_mod.Memory(os.path.join(d, "coach_memory.json"))
    check(not again._tidied and os.path.getmtime(bond_path) == t0 and set(again.characters) == {"sarge", "grim"},
          "clean: never rewritten")

    # ---- what reaches the coach: none of it --------------------------------------------------------------------------
    c = coach.Coach({"persona": "A grizzled dwarf.", "persona_name": "Sarge", "persona_voice": "Gruff."},
                    GlideDB("/x.db"), lambda m: None, offline_ok=True, deps=CoachDeps(memory=again))
    block = "\n".join(b["text"] for b in c._system_blocks())
    check("Pip" not in block and "[" not in block.split("YOU ARE IN CHARACTER")[-1] and "Twenty levels, recruit." in block,
          block[-1500:])
finally:
    shutil.rmtree(d, ignore_errors=True)
print("OK")
