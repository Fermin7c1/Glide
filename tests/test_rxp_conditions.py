#!/usr/bin/env python3
"""The Guides page's race picker and the steps it lists (desktop/src/renderer/rxp-model.ts, compiled with the project's
TypeScript once for all the tests (desktop_ts.py) and run under Node), read from the masks the engine sends with each
guide (companion/rxp_explorer.py; tests/test_rxp_explorer.py checks those against the coach's numbering).

- The picker offers Skyborne (WoW Forever) under both factions, the value naming its side (regression: it listed
  Skyborne only under Horde and read every Skyborne as Horde).
- One character picked: exactly the steps, and the numbers, the coach gives that character (regression: the page's
  own copy of RestedXP's rules disagreed with the coach on 41 of 80 free guides for a Human Mage).
- Only a race or only a class picked: a step shows when a character that pick allows gets it (regression: "<< !Warlock"
  hid for "Human, all classes"); a Gnome never gets a Druid's step.
- "Show all": the steps only other characters get come dimmed and unnumbered; steps nobody gets never come.

Needs node and desktop/node_modules (skips with 77 without them)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import desktop_ts

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DESK = os.path.join(ROOT, "desktop")
SRC = os.path.join(DESK, "src", "renderer", "rxp-model.ts")
sys.path.insert(0, ROOT)
sys.path.append(os.path.join(ROOT, "companion"))

# the picker: Skyborne under both factions, each value naming its side; every other race under its own faction
html = open(os.path.join(DESK, "src", "renderer", "index.html"), encoding="utf-8").read()
picker = re.search(r'<select id="rxpb-race".*?</select>', html, re.S).group(0)
groups = {m.group(1): re.findall(r'<option value="([^"]+)">', m.group(2))
          for m in re.finditer(r'<optgroup label="(\w+)">(.*?)</optgroup>', picker, re.S)}
assert groups == {"Alliance": ["Human", "Dwarf", "NightElf", "Gnome", "Skyborne@Alliance"],
                  "Horde": ["Orc", "Undead", "Tauren", "Troll", "Skyborne@Horde"]}, groups

# the tight page: one toolbar row (the character picks in a popover), the step list a listbox where the current step
# opens (no separate current-step box), timeline ticks above the slider, no legend row, no 900 px stacking rule
page = re.search(r'<section id="page-guides".*?</section>', html, re.S).group(0)
assert 'class="rxpb-toolbar"' in page and 'class="pagehead' not in page
assert re.search(r'id="rxpb-char-pop"[^>]*role="dialog"[^>]*hidden', page) and page.index("rxpb-char-pop") < page.index('id="rxpb-race"')
assert 'role="listbox"' in page and 'id="rxpb-active-step"' not in page and "rxpb-legend" not in page
css = open(os.path.join(DESK, "src", "renderer", "app.css"), encoding="utf-8").read()
assert "@media (max-width: 900px) { .rxpb-workspace" not in css and "@container guides" in css
assert "object-fit: fill" in re.search(r"#rxpb-map-img \{[^}]*\}", css).group(0)

# map images go by URL (glide://map/<id>.png, served by the main process), not as base64 over IPC on every open
csp = re.search(r'Content-Security-Policy"\s+content="([^"]+)"', html).group(1)
assert "glide:" in re.search(r"img-src([^;]+)", csp).group(1), csp
ipc_src = open(os.path.join(DESK, "src", "main", "app", "ipc.ts"), encoding="utf-8").read()
assert "base64" not in ipc_src and "glide://map/" in ipc_src
main_src = open(os.path.join(DESK, "src", "main", "main.ts"), encoding="utf-8").read()
assert 'registerSchemesAsPrivileged([{ scheme: "glide"' in main_src and 'protocol.handle("glide"' in main_src

node = desktop_ts.node
if not node or not desktop_ts.available():
    print("SKIP: needs node and desktop/node_modules (cd desktop && npm ci)")
    sys.exit(77)

import rxp_explorer as X  # noqa: E402
import rxp_import as R  # noqa: E402

source, free = R.free_guides(ROOT, None)
WANT = (("14-16 Darkshore", "RestedXP Forever Guide (A)"), ("10-12 Durotar", "RestedXP Forever Guide (H)"),
        ("9-13 Zephras Isle", "RestedXP Forever Guide (A)"), ("1-6 Shadowglen", "RestedXP Forever Dungeon Guide (A)"))
guides = []
for name, group in WANT:
    i = next(i for i, g in enumerate(free) if g["name"] == name and g["groups"][0] == group)
    d = R.guide_detail(ROOT, R.FREE_ID, i)
    guides.append({"name": name, "players": d["players"], "masks": d["vis_masks"], "steps": [s["vis"] for s in d["steps"]],
                   "acts": [a.get("vis") for s in d["steps"] for a in s["actions"]][:400]})
players = guides[0]["players"]


def expect(g, who, combo, show_all=False):
    """What the page should list: [(position, number or None)], from the masks themselves."""
    w = (len(g["players"]) + 3) // 4
    out, n = [], 0
    for pos, m in enumerate(g["steps"]):
        hexm = g["masks"][m]
        if set(hexm) == {"0"}:
            continue
        chunk = int(hexm[combo * w:(combo + 1) * w], 16)
        if any(chunk >> p & 1 for p in who):
            n += 1
            out.append([pos, n])
        elif show_all:
            out.append([pos, None])
    return out


def who(race, cls):
    return [i for i, p in enumerate(players)
            if (not race or p.split("/")[0] == race or (race == "Skyborne" and p.startswith("Skyborne@")))
            and (not cls or p.split("/")[1] == cls)]


PICKS = [("Human", "MAGE"), ("Orc", "WARLOCK"), ("Skyborne@Alliance", "PALADIN"), ("Skyborne@Horde", "SHAMAN"),
         ("Human", ""), ("", "MAGE"), ("", ""), ("Skyborne", ""), ("Gnome", "DRUID"), ("Undead", "PRIEST")]
cases = []
for g in guides:
    for race, cls in PICKS:
        for combo in (0, 3, 7):
            cases.append({"g": guides.index(g), "race": race, "cls": cls, "combo": combo, "all": combo == 3,
                          "want": expect(g, who(race, cls), combo, combo == 3)})

# the coach's numbering for one character is what the page lists (tests/test_rxp_explorer.py: masks == coach)
hm = players.index("Human/MAGE")
coach = X.numbering({"players": players, "masks": guides[0]["masks"], "steps": guides[0]["steps"]}, hm, 3)
assert [[pos, n] for n, pos in coach.items()] == expect(guides[0], [hm], 3), "the test's own reading of the masks"
assert who("Gnome", "DRUID") == [] and len(who("Human", "")) == 6 and len(who("Skyborne", "")) == 16

tmp = tempfile.mkdtemp(prefix="glide-rxp-model-")
try:
    mjs = os.path.join(desktop_ts.renderer(), "renderer", "rxp-model.js")     # (the tests' shared compile)
    data = os.path.join(tmp, "data.json")
    with open(data, "w", encoding="utf-8") as fh:
        json.dump({"guides": guides, "cases": cases}, fh)
    code = """
import fs from 'fs';
const m = await import(%s);
const D = JSON.parse(fs.readFileSync(%s, 'utf8'));
const vs = D.guides.map(g => new m.Visibility(g.players, g.masks));
const got = D.cases.map(c => { const v = vs[c.g]; return v.list(D.guides[c.g].steps, c.combo, v.pick(c.race, c.cls), c.all)
  .map(s => [s.pos, s.num]); });
const v0 = vs[0], hm = v0.pick('Human', 'MAGE'), orc = v0.pick('Orc', '');
const acts = D.guides[0].acts.map(a => [v0.actionShown(a, hm), v0.actionShown(a, orc)]);
console.log(JSON.stringify({got, acts, combo: [m.comboOf(true, true, false), m.comboOf(false, false, true)],
  picks: ['Skyborne@Alliance', 'Skyborne@Horde', 'Skyborne', 'Undead', 'Gnome', ''].map(m.racePick)}));
""" % (json.dumps(desktop_ts.url(mjs)), json.dumps(data))
    r = subprocess.run([node, "--input-type=module", "-e", code], capture_output=True, text=True, encoding="utf-8",
                       timeout=60)
    assert r.returncode == 0, "running rxp-model failed: %s" % r.stderr[-2000:]
    out = json.loads(r.stdout)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

wrong = [(guides[c["g"]]["name"], c["race"], c["cls"], c["combo"]) for c, got in zip(cases, out["got"]) if got != c["want"]]
assert not wrong, wrong[:10]
# one-sided picks see what any allowed character sees: never fewer steps than one of them
by = {(c["g"], c["race"], c["cls"], c["combo"]): got for c, got in zip(cases, out["got"])}
for gi in range(len(guides)):
    one = {p for p, n in by[(gi, "Human", "MAGE", 0)]}
    assert one <= {p for p, n in by[(gi, "Human", "", 0)]} and one <= {p for p, n in by[(gi, "", "MAGE", 0)]}
    assert by[(gi, "Gnome", "DRUID", 0)] == [], "no Gnome plays a Druid"
    assert all(n is None for p, n in by[(gi, "Gnome", "DRUID", 3)]), "show all: dimmed and unnumbered"
# "Show all" never brings a step nobody gets (`step << skip` in Darkshore)
skip = [pos for pos, m in enumerate(guides[0]["steps"]) if set(guides[0]["masks"][m]) == {"0"}]
assert skip and not set(skip) & {p for p, n in by[(0, "", "", 3)]}
# actions with their own condition
for a, (human_mage, orc) in zip(guides[0]["acts"], out["acts"]):
    if a is None:
        assert human_mage and orc
    else:
        assert human_mage == bool(int(a, 16) >> hm & 1)
assert out["combo"] == [3, 4], out["combo"]
assert out["picks"] == [{"race": "Skyborne", "faction": "Alliance"}, {"race": "Skyborne", "faction": "Horde"},
                        {"race": "Skyborne", "faction": ""}, {"race": "Scourge", "faction": "Horde"},
                        {"race": "Gnome", "faction": "Alliance"}, {"race": "", "faction": ""}], out["picks"]
print("OK")
