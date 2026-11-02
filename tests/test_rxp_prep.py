#!/usr/bin/env python3
""""Before you start" on the Guides page (desktop/src/renderer/rxp-prep.ts, compiled with the project's TypeScript once
for all the tests (desktop_ts.py) and run under Node), fed with the engine's own prep list (knowledge/api/explorer.py
guide_facts over a small guide, no knowledge base) and kb-shaped entries: a chip's words (count and name, where it's
needed), its card (what, where, who sells it and for how much, what for), copper as the game writes it, and which items
the picked character gets (the same need offered at two steps in a row counts once).

Needs node and desktop/node_modules (skips with 77 without them)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import desktop_ts

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DESK = os.path.join(ROOT, "desktop")
SRC = os.path.join(DESK, "src", "renderer", "rxp-prep.ts")
sys.path.insert(0, ROOT)
for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY"):
    os.environ[k] = ""

node = desktop_ts.node
if not node or not desktop_ts.available():
    print("SKIP: needs node and desktop/node_modules (cd desktop && npm ci)")
    sys.exit(77)

from knowledge.api import explorer as X  # noqa: E402
from knowledge.rxp_parser.guide import parse_guide  # noqa: E402

GUIDE = """#group Test Guide (A)
#name 1-3 Testvale
step
    #ah
    >>Buy a [Linen Bandage] from the Auction House
    .collect 700,2 --Linen Bandage (2)
step << Mage
    .vendor 800 >>Buy [Refreshing Spring Water]
    .collect 159,10,100 --Collect Refreshing Spring Water
step
    .bankwithdraw 702
step
    .turnin 100 >>Turn in Thirst
"""
PREP = X.guide_facts(None, parse_guide(GUIDE))["prep"]
SOLD = dict(PREP[1], vendors=[{"id": 800, "name": "Brother Danil", "subname": "General Supplies", "price": 125,
                               "ui_map": 1429, "x": 41, "y": 61}], price=125)
TWICE = [dict(PREP[0], idx=5, for_guide="G||Next", qty=3), dict(PREP[0], idx=6, for_guide="G||Next", qty=3),
         dict(PREP[0], idx=7, qty=3)]
CALLS = {
    "labels": "prep.map(prepLabel)",
    "needs": "prep.map((p) => prepNeed(p, (s) => String(s + 10)))",
    "needHidden": "prepNeed(prep[1], () => null)",
    "titles": "prep.map((p) => prepTitle(p, (s) => String(s + 10), (q) => q === 100 ? 'Thirst' : null))",
    "sold": "prepTitle(sold, (s) => String(s), null)",
    "money": "[money(25), money(125), money(10000), money(123456), money(0), money(null)]",
    "shown": "prepShown(prep, (p) => p.kind !== 'buy').map((p) => p.item_id)",
    "twice": "prepShown(twice, () => true).map((p) => p.idx)",
    "twiceFirstHidden": "prepShown(twice, (p) => p.idx !== 5).map((p) => p.idx)",
}

tmp = tempfile.mkdtemp(prefix="glide-rxp-prep-")
try:
    mod = os.path.join(desktop_ts.renderer(), "renderer", "rxp-prep.js")     # (the tests' shared compile)
    body = "const prep = %s, sold = %s, twice = %s;\nconst out = {};\n%s\nconsole.log(JSON.stringify(out));" % (
        json.dumps(PREP), json.dumps(SOLD), json.dumps(TWICE),
        "\n".join("out[%s] = %s;" % (json.dumps(k), v) for k, v in CALLS.items()))
    script = os.path.join(tmp, "run.mjs")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write("import { money, prepLabel, prepNeed, prepShown, prepTitle } from %s;\n%s" % (
            json.dumps(desktop_ts.url(mod)), body))
    r = subprocess.run([node, script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, "running rxp-prep failed: %s" % r.stderr[-2000:]
    got = json.loads(r.stdout)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

assert [p["kind"] for p in PREP] == ["ah", "buy", "bank_out"], PREP
# the chip: the count when the guide gives one, the name (the guide's words without a kb), where it's needed
assert got["labels"] == ["2× Linen Bandage", "10× Refreshing Spring Water", "item #702"], got["labels"]
assert got["needs"] == ["", "for step 14", ""] and got["needHidden"] == "", (got["needs"], got["needHidden"])
assert got["titles"] == ["Buy 2× Linen Bandage at the Auction House at step 11.",
                         "Buy 10× Refreshing Spring Water at step 12.\nNeeded at step 14: Thirst.",
                         "Take item #702 from the bank at step 13."], got["titles"]
assert got["sold"] == ("Buy 10× Refreshing Spring Water at step 2.\nBrother Danil <General Supplies> sells it for 1s 25c.\n"
                       "Needed at step 4."), got["sold"]
assert got["money"] == ["25c", "1s 25c", "1g", "12g 34s 56c", "", ""], got["money"]
assert got["shown"] == [700, 702], got["shown"]
# the same need offered at two steps counts once (the first the character gets); another need stays
assert got["twice"] == [5, 7] and got["twiceFirstHidden"] == [6, 7], (got["twice"], got["twiceFirstHidden"])
print("rxp-prep: ok")
