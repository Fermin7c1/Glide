#!/usr/bin/env python3
"""Game icons on the Guides page (desktop/src/renderer/rxp-icons.ts, compiled with the project's TypeScript into a
temp folder and run under Node): which client icon stands for a map target (a creature its family or type icon, an
object the item it gives, a quest area none), a step badge (accept/turn-in marks, the item to collect or buy, the
trained spell, the vendor, flight master, innkeeper and banker, the Hearthstone, a creature named by .target / .mob),
a prep item and an elite or rare; and that every one falls back to the glyph (null) when the icon is unknown.

Needs node and desktop/node_modules (skips with 77 without them)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DESK = os.path.join(HERE, "..", "desktop")
TSC = os.path.join(DESK, "node_modules", "typescript", "bin", "tsc")
SRC = os.path.join(DESK, "src", "renderer", "rxp-icons.ts")

node = shutil.which("node")
if not node or not os.path.exists(TSC):
    print("SKIP: needs node and desktop/node_modules (cd desktop && npm ci)")
    sys.exit(77)

ICONS = {"item:2589": 132889, "item:6948": 134414, "item:723": 134027, "spell:2018": 136241, "npc:3098": 132184,
         "npc:3158": 134166, "role:vendor": 132060, "role:quest_available": 132049, "role:quest_turnin": 132048,
         "role:flight": 136456, "role:innkeeper": 136458, "role:trainer": 136455, "role:banker": 136453,
         "role:elite": 237678, "role:rare": 1080968}


def target(kind, tid, role="kill", item=None, name=None, rank=0):
    return {"kind": kind, "id": tid, "name": name, "level": None, "rank": rank, "role": role, "quest": 1, "obj": 1,
            "item": item, "ui_map": 1411, "points": [[1, 1]], "groups": []}


def action(verb, kind=None, eid=None, units=None, name=None):
    return {"line": 1, "verb": verb, "text": None, "raw_text": None, "cond": None, "quest_id": None, "quest_name": None,
            "entity_kind": kind, "entity_id": eid, "entity_name": name, "units": units, "qty": None, "zone": None,
            "ui_map": None, "x": None, "y": None}


T = {"boar": target("npc", 3098, name="Mottled Boar"), "unknown": target("npc", 5),
     "crate": target("object", 9, "loot", item=723, name="Crate"), "rock": target("object", 10, "use"),
     "area": target("poi", 1, "area"), "lootboar": target("npc", 3098, "loot", item=723)}
A = {"accept": action("accept"), "turnin": action("turninmultiple"), "collect": action("collect", "item", 2589, name="Linen Cloth"),
     "buy": action("buy", "item", 6948), "collectUnknown": action("collect", "item", 1), "train": action("train", "spell", 2018),
     "trainUnknown": action("train", "spell", 99), "vendor": action("vendor", "npc", 1), "fp": action("fp"),
     "home": action("home"), "hs": action("hs"), "bank": action("bankwithdraw"), "deathskip": action("deathskip"),
     "mobByName": action("mob", "npc", None, ["mottled boar"]), "targetById": action("target", "npc", 3158),
     "mobNotFound": action("mob", None, None, ["Kobold"]), "complete": action("complete")}

CALLS = {"t_%s" % k: "targetIcon(T.%s, I)" % k for k in T}
CALLS.update({"loot_%s" % k: "lootIcon(T.%s, I)" % k for k in T})
CALLS.update({"a_%s" % k: "actionIcon(A.%s, I, [T.boar, T.unknown])" % k for k in A})
CALLS.update({
    "noIcons": "[targetIcon(T.boar, null), actionIcon(A.accept, undefined), prepIcon({item_id: 2589}, {})]",
    "prep": "prepIcon({item_id: 2589, name: 'Linen Cloth'}, I)",
    "ranks": "[0, 1, 2, 3, 4, null].map((r) => rankIcon(r, I))",
    "url": "iconUrl(134414)",
    "steps": "[['accept', []], ['turnin', []], ['travel', []], ['hearth', [A.hs]], ['hearth', [A.home]], "
             "['combat', [A.mobNotFound]], ['combat', [A.complete, A.targetById]], ['step', [A.complete, A.vendor]], "
             "['step', [A.complete]], ['death', [A.deathskip]]].map(([k, acts]) => stepIcon(k, acts, [T.unknown, T.boar], I))",
    "stepsNone": "stepIcon('accept', [], [], null)",
})

tmp = tempfile.mkdtemp(prefix="glide-rxp-icons-")
try:
    r = subprocess.run([node, TSC, SRC, "--target", "ES2023", "--module", "ES2022", "--strict", "--skipLibCheck",
                        "--rootDir", os.path.join(DESK, "src"), "--outDir", tmp], capture_output=True, text=True,
                       encoding="utf-8", timeout=120)
    assert r.returncode == 0, "tsc failed on rxp-icons.ts: %s" % (r.stdout + r.stderr)[-2000:]
    with open(os.path.join(tmp, "package.json"), "w") as fh:
        fh.write('{"type": "module"}')
    mod = os.path.join(tmp, "renderer", "rxp-icons.js")
    body = "const I = %s, T = %s, A = %s;\nconst out = {};\n%s\nconsole.log(JSON.stringify(out));" % (
        json.dumps(ICONS), json.dumps(T), json.dumps(A),
        "\n".join("out[%s] = %s;" % (json.dumps(k), v) for k, v in CALLS.items()))
    script = os.path.join(tmp, "run.js")
    with open(script, "w") as fh:
        fh.write("import { targetIcon, lootIcon, actionIcon, prepIcon, rankIcon, iconUrl, stepIcon } from %s;\n%s" % (
            json.dumps("file:///" + mod.replace("\\", "/").lstrip("/")), body))
    r = subprocess.run([node, script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, "running rxp-icons failed: %s" % r.stderr[-2000:]
    got = json.loads(r.stdout)
finally:
    shutil.rmtree(tmp, ignore_errors=True)


def url(key):
    return {"url": "glide://icon/%d.png" % ICONS[key]}


def u(x):
    return {"url": x["url"]} if x else None


# map targets: a creature its own icon, an object the item it gives, a quest area and an unknown creature the glyph
assert u(got["t_boar"]) == url("npc:3098") and got["t_boar"]["what"] == "Mottled Boar", got["t_boar"]
assert got["t_unknown"] is None and got["t_area"] is None and got["t_rock"] is None, got
assert u(got["t_crate"]) == url("item:723") and u(got["t_lootboar"]) == url("npc:3098"), "a looted boar is the boar"
assert u(got["loot_lootboar"]) == url("item:723") and u(got["loot_crate"]) == url("item:723"), "the card shows the loot"
assert got["loot_boar"] is None and got["loot_area"] is None
# badges
for k, key in (("accept", "role:quest_available"), ("turnin", "role:quest_turnin"), ("collect", "item:2589"),
               ("buy", "item:6948"), ("train", "spell:2018"), ("trainUnknown", "role:trainer"), ("vendor", "role:vendor"),
               ("fp", "role:flight"), ("home", "role:innkeeper"), ("hs", "item:6948"), ("bank", "role:banker"),
               ("mobByName", "npc:3098"), ("targetById", "npc:3158")):
    assert u(got["a_" + k]) == url(key), (k, got["a_" + k])
assert got["a_collect"]["what"] == "Linen Cloth" and got["a_hs"]["what"] == "Hearthstone"
for k in ("collectUnknown", "deathskip", "mobNotFound", "complete"):
    assert got["a_" + k] is None, (k, got["a_" + k])
# no icons yet (the refresh hasn't read the client): every one keeps its glyph
assert got["noIcons"] == [None, None, None], got["noIcons"]
assert u(got["prep"]) == url("item:2589")
assert [u(x) for x in got["ranks"]] == [None, url("role:elite"), url("role:rare"), url("role:elite"), url("role:rare"),
                                         None], got["ranks"]
assert got["url"] == "glide://icon/134414.png"
# step pins: the quest marks, the flight master, the Hearthstone / innkeeper, a fight's first known creature (the boar,
# though the unknown one comes first), a plain step's vendor; a step with nothing to show and a deathskip keep the glyph
assert [u(x) for x in got["steps"]] == [url("role:quest_available"), url("role:quest_turnin"), url("role:flight"),
                                        url("item:6948"), url("role:innkeeper"), url("npc:3098"), url("npc:3098"),
                                        url("role:vendor"), None, None], got["steps"]
assert got["stepsNone"] is None
print("OK")
