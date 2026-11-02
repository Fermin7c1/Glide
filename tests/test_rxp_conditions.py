#!/usr/bin/env python3
"""The Guide & Route Explorer's race picker and RestedXP's `<<` step conditions (desktop/src/renderer/rxp-conditions.ts,
compiled with the project's TypeScript into a temp folder and run under Node).

Regressions: Skyborne (WoW Forever) joins either faction, but the picker listed it only under Horde and read every
Skyborne as Horde, so an Alliance Skyborne's steps (`<< Alliance`, `<< Skyborne Alliance`) were hidden and the Horde
ones shown. The picker now offers Skyborne under both factions, and the pick carries the faction. And with only a
race or only a class picked, a negated condition ("<< !Warlock") hid the step as if the unpicked side matched it.

Needs node and desktop/node_modules (skips with 77 without them)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DESK = os.path.join(HERE, "..", "desktop")
TSC = os.path.join(DESK, "node_modules", "typescript", "bin", "tsc")
SRC = os.path.join(DESK, "src", "renderer", "rxp-conditions.ts")

# the picker: Skyborne under both factions, each value naming its side; every other race under its own faction
html = open(os.path.join(DESK, "src", "renderer", "index.html"), encoding="utf-8").read()
picker = re.search(r'<select id="rxpb-race".*?</select>', html, re.S).group(0)
groups = {m.group(1): re.findall(r'<option value="([^"]+)">', m.group(2))
          for m in re.finditer(r'<optgroup label="(\w+)">(.*?)</optgroup>', picker, re.S)}
assert groups == {"Alliance": ["Human", "Dwarf", "NightElf", "Gnome", "Skyborne@Alliance"],
                  "Horde": ["Orc", "Undead", "Tauren", "Troll", "Skyborne@Horde"]}, groups

node = shutil.which("node")
if not node or not os.path.exists(TSC):
    print("SKIP: needs node and desktop/node_modules (cd desktop && npm ci)")
    sys.exit(77)

# (condition as RestedXP writes it, race picker value, class picker value) -> shown?
CASES = [
    ("Skyborne Alliance", "Skyborne@Alliance", "", True),
    ("Skyborne Alliance", "Skyborne@Horde", "", False),
    ("Skyborne Horde", "Skyborne@Horde", "", True),
    ("Skyborne Horde", "Skyborne@Alliance", "", False),
    ("Alliance", "Skyborne@Alliance", "", True),
    ("Alliance", "Skyborne@Horde", "", False),
    ("Horde", "Skyborne@Horde", "", True),
    ("Horde", "Skyborne@Alliance", "", False),
    ("Skyborne", "Skyborne@Alliance", "", True),
    ("Skyborne", "Skyborne@Horde", "", True),
    ("Skyborne", "Human", "", False),
    ("!Skyborne", "Skyborne@Alliance", "", False),
    ("!Skyborne", "Gnome", "", True),
    ("Human/Skyborne", "Skyborne@Alliance", "", True),
    ("Mage Skyborne Alliance", "Skyborne@Alliance", "MAGE", True),
    ("Mage Skyborne Alliance", "Skyborne@Alliance", "SHAMAN", False),
    ("Shaman Skyborne Horde", "Skyborne@Horde", "SHAMAN", True),
    ("Alliance", "", "", True),                      # all races: every step
    ("Horde", "", "WARRIOR", True),                  # a class alone says nothing about the faction
    ("Alliance", "Skyborne", "", True),              # a bare Skyborne (no side): either faction's steps
    ("Undead", "Undead", "", True),                  # the game's token for Undead is Scourge; either name works
    ("Scourge", "Undead", "", True),
    ("Orc", "Undead", "", False),
    ("Horde", "Orc", "", True),
    ("Alliance", "Orc", "", False),
    ("Warrior/Rogue", "Human", "ROGUE", True),
    ("!(Warrior/Rogue)", "Human", "ROGUE", False),
    ("", "Skyborne@Horde", "MAGE", True),            # no condition: always
    # Regression (review): with only a race picked, "<< !Warlock" was hidden as if the class were Warlock; a step
    # shows when a character the pick allows would see it (a Human Warrior sees it, a Human Warlock sees "<< Warlock")
    ("!Warlock", "Human", "", True),
    ("Warlock", "Human", "", True),
    ("Druid", "Gnome", "", False),                   # no Gnome plays a Druid
    ("!Undead", "", "MAGE", True),                   # only a class picked: a Gnome Mage sees it
    ("Undead", "", "MAGE", True),
    ("Undead", "", "DRUID", False),                  # no Undead Druid
    ("Shaman", "Skyborne@Alliance", "", False),      # the Alliance has no Shamans, Skyborne or not
    ("Paladin", "Skyborne@Horde", "", False),
    ("Shaman", "Skyborne@Horde", "", True),
    ("!Alliance", "", "PALADIN", False),             # every Paladin is Alliance
    ("Orc Warlock/Human Paladin", "Orc", "", True),
]

tmp = tempfile.mkdtemp(prefix="glide-rxp-conditions-")
try:
    r = subprocess.run([node, TSC, SRC, "--target", "ES2023", "--module", "ES2022", "--strict", "--skipLibCheck",
                        "--outDir", tmp], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, "tsc failed on rxp-conditions.ts: %s" % (r.stdout + r.stderr)[-2000:]
    mjs = os.path.join(tmp, "rxp-conditions.mjs")
    os.replace(os.path.join(tmp, "rxp-conditions.js"), mjs)
    code = ("import(%s).then(m => console.log(JSON.stringify({got: %s.map(c => m.conditionApplies(c[0], c[1], c[2])), "
            "picks: ['Skyborne@Alliance', 'Skyborne@Horde', 'Skyborne', 'Undead', 'Gnome', ''].map(m.racePick)})))"
            % (json.dumps("file:///" + mjs.replace("\\", "/").lstrip("/")), json.dumps(CASES)))
    r = subprocess.run([node, "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, "running rxp-conditions failed: %s" % r.stderr[-2000:]
    out = json.loads(r.stdout)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

wrong = [(c[0], c[1], c[2], "expected %s" % c[3]) for c, got in zip(CASES, out["got"]) if got != c[3]]
assert not wrong, wrong
assert out["picks"] == [{"race": "Skyborne", "faction": "Alliance"}, {"race": "Skyborne", "faction": "Horde"},
                        {"race": "Skyborne", "faction": ""}, {"race": "Scourge", "faction": "Horde"},
                        {"race": "Gnome", "faction": "Alliance"}, {"race": "", "faction": ""}], out["picks"]
print("OK")
