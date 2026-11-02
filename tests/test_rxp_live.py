#!/usr/bin/env python3
"""The Guides page follows the live character (desktop/src/renderer/rxp-model.ts, compiled alone and run under Node;
the light stream in companion/dashboard.py; the page forms in companion/rxp_import.py guide_summary).

- The light stream carries who is playing which RestedXP guide step (name, class, race, faction, guide, guide_group,
  step, rxp_settings), and the session bar keeps them in live.game for the Guides page.
- guide_summary gives each guide's name and group as the addon puts them on the strip (page G: RestedXP's
  zero-padded name, ; = | made ',' and cut to 40 bytes), so the page finds the guide the character plays.
- Every free guide is found from its strip form, in its own group, under either faction's name of a shared guide;
  a name cut mid-character (a trailing U+FFFD) still matches; an unknown guide is null.
- The game's race and class become the pickers' values (Scourge is Undead, Skyborne carries its faction).
- The live step N lands on the guide step the coach numbers N for that character and settings (the engine's masks),
  whatever the pickers show; a number past the end is the last step.

Needs node and desktop/node_modules (skips with 77 without them)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DESK = os.path.join(ROOT, "desktop")
TSC = os.path.join(DESK, "node_modules", "typescript", "bin", "tsc")
SRC = os.path.join(DESK, "src", "renderer", "rxp-model.ts")
sys.path.insert(0, ROOT)
sys.path.append(os.path.join(ROOT, "companion"))

import dashboard  # noqa: E402
import rxp_explorer as X  # noqa: E402
import rxp_import as R  # noqa: E402


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as fh:
        return fh.read()


# ---- the light stream and live.game -----------------------------------------------------------------------------
for k in ("name", "class", "race", "faction", "guide", "guide_group", "step", "rxp_settings"):
    assert k in dashboard.LITE_KEYS, k
full = {k: None for k in dashboard.LITE_KEYS}
full.update(guide="10-12 Durotar", step=20, steps=[{"big": 1}], community={"x": 1})
lite = dashboard.lite_state(full)
assert lite["guide"] == "10-12 Durotar" and lite["step"] == 20 and "steps" not in lite and "community" not in lite
assert "game: null as LiveGame | null" in read("desktop", "src", "renderer", "state.ts")
bar = read("desktop", "src", "renderer", "session-bar.ts")
assert "live.game = d.class ?" in bar and "live.game = null;" in bar and bar.count("liveGameChanged()") == 2
html = read("desktop", "src", "renderer", "index.html")
assert re.search(r'<div class="rxpb-timeline">.*?id="rxpb-live"[^>]*hidden.*?</div>', html, re.S), "the button lives in the timeline bar"

# ---- the page forms -----------------------------------------------------------------------------------------------
s = R.guide_summary(0, {"name": "5-11 Dun Morogh", "group": "RestedXP; Forever|Guide", "text": ""})
assert s["rxp_name"] == "05-11 Dun Morogh" and s["rxp_group"] == "RestedXP, Forever,Guide", s
long = R.guide_summary(0, {"name": "1-10 " + "Ä" * 30, "group": "G", "text": ""})["rxp_name"]
assert long.startswith("01-10 ") and len(long.encode("utf-8")) <= 40 and "�" not in long, long

node = shutil.which("node")
if not node or not os.path.exists(TSC):
    print("SKIP: needs node and desktop/node_modules (cd desktop && npm ci)")
    sys.exit(77)

free = R.free_catalog(ROOT, None)
if not free:
    print("SKIP: no free RestedXP guides (data/seed)")
    sys.exit(77)
items = [dict(it, packId=R.FREE_ID) for it in free["items"]]
# what the addon sends for each: its name and group as on the strip, the faction the group plays
live_cases = [{"guide": it["rxp_name"], "group": it["rxp_group"], "faction": it["faction"], "want": [it["group"], it["name"]]}
              for it in items]
# a strip that cut a character in half, and a guide the page doesn't have (the simulator's)
cut = next(it for it in items if it["name"] == "10-12 Durotar" and "Dungeon" not in it["group"])
live_cases.append({"guide": cut["rxp_name"] + "�", "group": cut["rxp_group"], "faction": "Horde",
                   "want": [cut["group"], cut["name"]]})
live_cases.append({"guide": "01-14 Zephras Isle", "group": "RestedXP Forever Guide (A)", "faction": "Alliance", "want": None})
# no group sent: the levelling guide before the dungeon one of the same name
live_cases.append({"guide": cut["rxp_name"], "group": None, "faction": "Horde", "want": [cut["group"], cut["name"]]})

d = R.guide_detail(ROOT, R.FREE_ID, cut["index"])
vis = {"players": d["players"], "masks": d["vis_masks"], "steps": [st["vis"] for st in d["steps"]]}
step_cases = []
for who in ("Orc/WARRIOR", "Troll/SHAMAN", "Skyborne@Horde/MAGE", "Undead/PRIEST"):
    p = vis["players"].index(who)
    for combo in (0, 3, 7):
        num = X.numbering(vis, p, combo)
        for n in (1, 7, len(num), len(num) + 30):
            step_cases.append({"p": p, "combo": combo, "n": n, "want": num[min(n, len(num))] if num else None})
step_cases.append({"p": -1, "combo": 3, "n": 3, "want": None})
step_cases.append({"p": 0, "combo": 3, "n": 0, "want": None})

tmp = tempfile.mkdtemp(prefix="glide-rxp-live-")
try:
    r = subprocess.run([node, TSC, SRC, "--target", "ES2023", "--module", "ES2022", "--strict", "--skipLibCheck",
                        "--outDir", tmp], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, "tsc failed on rxp-model.ts: %s" % (r.stdout + r.stderr)[-2000:]
    mjs = os.path.join(tmp, "rxp-model.mjs")
    os.replace(os.path.join(tmp, "rxp-model.js"), mjs)
    data = os.path.join(tmp, "data.json")
    with open(data, "w", encoding="utf-8") as fh:
        json.dump({"items": items, "live": live_cases, "vis": vis, "steps": step_cases}, fh)
    code = """
import fs from 'fs';
const m = await import(%s);
const D = JSON.parse(fs.readFileSync(%s, 'utf8'));
const found = D.live.map(c => { const g = m.liveGuide(D.items, c.guide, c.group, c.faction); return g ? [g.group, g.name] : null; });
const v = new m.Visibility(D.vis.players, D.vis.masks);
const pos = D.steps.map(c => m.liveStepPos(v, D.vis.steps, c.p, c.combo, c.n));
const races = [['Scourge', 'Horde'], ['Undead', 'Horde'], ['NightElf', 'Alliance'], ['Night Elf', 'Alliance'], ['orc', 'Horde'],
  ['Skyborne', 'Horde'], ['Skyborne', 'Alliance'], ['Skyborne', null], ['Haranir', 'Alliance'], [null, null]].map(([r, f]) => m.liveRacePick(r, f));
const classes = ['mage', 'WARRIOR', 'DEATHKNIGHT', '', null].map(m.liveClassPick);
const names = ['05-11  Dun Morogh', '5-11 dun morogh', '10-12 Durotar\\uFFFD', ' 01 - 06 X '].map(m.normGuideName);
console.log(JSON.stringify({found, pos, races, classes, names}));
""" % (json.dumps("file:///" + mjs.replace("\\", "/").lstrip("/")), json.dumps(data))
    r = subprocess.run([node, "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, "running rxp-model failed: %s" % r.stderr[-2000:]
    out = json.loads(r.stdout)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

wrong = [(c["guide"], c["group"], c["want"], got) for c, got in zip(live_cases, out["found"]) if got != c["want"]]
assert not wrong, wrong[:10]
wrong = [(c, got) for c, got in zip(step_cases, out["pos"]) if got != c["want"]]
assert not wrong, wrong[:10]
assert out["races"] == ["Undead", "Undead", "NightElf", "NightElf", "Orc", "Skyborne@Horde", "Skyborne@Alliance", "", "", ""], out["races"]
assert out["classes"] == ["MAGE", "WARRIOR", "", "", ""], out["classes"]
assert out["names"] == ["5-11 dun morogh", "5-11 dun morogh", "10-12 durotar", "1-6 x"], out["names"]
print("OK")
