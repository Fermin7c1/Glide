#!/usr/bin/env python3
"""The shared helpers have one home (R25): knowledge/values.py (number parsing), knowledge/vocab.py (classes,
races, factions), companion/msg_kinds.py (feed message kinds), strip_spec.coach_flags (the cf decoder), and the
offline coach reads the knowledge layer's why phrases. Checks their documented behaviour, that they load nothing
heavy, and that no look-alike copy has come back."""
import glob
import math
import os
import re
import subprocess
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.append(os.path.join(ROOT, "companion"))      # after ROOT: `knowledge` stays the package
from knowledge import values as V  # noqa: E402
from knowledge import vocab  # noqa: E402

# ---- values: every documented case ------------------------------------------------------------------------------
INT_CASES = [  # input, as_int(v), as_int(v, loose=True), as_int(v, loose=True, bool_ok=False) (the API's reading)
    ("7", 7, 7, 7), (" 3 ", 3, 3, 3), (7.9, 7, 7, 7), ("7.9", None, 7, 7), ("1e3", None, 1000, 1000),
    (True, 1, 1, None), (False, 0, 0, None), ("", None, None, None), (None, None, None, None),
    ("abc", None, None, None), ([1], None, None, None), (float("nan"), None, None, None),
    (float("inf"), None, None, None), ("inf", None, None, None), ("-4", -4, -4, -4),
    ("12345678901234567890", 12345678901234567890, 12345678901234567890, 12345678901234567890),
]
for v, strict, loose, api in INT_CASES:
    assert V.as_int(v) == strict, (v, V.as_int(v))
    assert V.as_int(v, loose=True) == loose, (v, V.as_int(v, loose=True))
    assert V.as_int(v, loose=True, bool_ok=False) == api, (v, V.as_int(v, loose=True, bool_ok=False))
assert V.as_int("x", 5) == 5 and V.as_int(None, 255) == 255 and V.as_int("2", 5) == 2
assert V.as_int(True, -1, bool_ok=False) == -1

FLOAT_CASES = [  # input, as_float(v), as_float(v, finite=True, bool_ok=False)
    ("7.9", 7.9, 7.9), (" 3 ", 3.0, 3.0), ("1e3", 1000.0, 1000.0), (True, 1.0, None), ("", None, None),
    (None, None, None), ("abc", None, None), ("inf", math.inf, None), (10 ** 400, None, None),
]
for v, plain, finite in FLOAT_CASES:
    assert V.as_float(v) == plain, (v, V.as_float(v))
    assert V.as_float(v, finite=True, bool_ok=False) == finite, (v, V.as_float(v, finite=True, bool_ok=False))
assert math.isnan(V.as_float("nan")) and V.as_float("nan", finite=True) is None and V.as_float("x", 1.5) == 1.5

# the named readers keep their old behaviour: the API (and the heuristics) read loosely without bools
from knowledge.api import values as AV  # noqa: E402
from knowledge.heuristics import engine as HE  # noqa: E402
for v, _, _, api in INT_CASES:
    assert AV.as_int(v) == api and HE._int(v) == api, v
assert AV.as_num("2.5") == 2.5 and AV.as_num(True) is None and AV.as_num("inf") is None
assert HE._num("2.5") == 2.5 and HE._num(False) is None
# Lua booleans in the build's loaders count as 1 (a SavedVariables flag stored as `true`)
from knowledge.sources import observations  # noqa: E402
assert observations.as_int(True) == 1 and observations.as_int("7.9") is None
from wowfiles import db2_import  # noqa: E402
assert db2_import.inum("12.0") == 12 and db2_import.inum("") is None and db2_import.as_float("0.5") == 0.5

# ---- the shared modules load nothing heavy (the engine uses them when the knowledge layer won't load) -----------
code = ("import sys; sys.path.insert(0, %r); import knowledge.values, knowledge.vocab; "
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('numpy', 'knowledge') "
        "and m not in ('knowledge', 'knowledge.values', 'knowledge.vocab')); print(bad)" % ROOT)
out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
assert out.returncode == 0 and out.stdout.strip() == "[]", (out.stdout, out.stderr)

# ---- vocab: both directions agree, Undead's two names -----------------------------------------------------------
assert tuple(vocab.CLASS_BITS) == vocab.CLASSES
for name, bit in vocab.CLASS_BITS.items():
    assert vocab.BIT_CLASSES[bit] == name and bit & (bit - 1) == 0
for bit, name in vocab.BIT_RACES.items():
    assert vocab.RACE_BITS[name] == bit and bit & (bit - 1) == 0
assert vocab.BIT_RACES[16] == "Scourge" and vocab.RACE_BITS["Undead"] == vocab.RACE_BITS["Scourge"] == 16
assert len(vocab.BIT_RACES) == 8 and set(vocab.RACE_BITS) == set(vocab.BIT_RACES.values()) | {"Undead"}
for fac, mask in vocab.FACTION_RACE_MASKS.items():
    assert mask == sum(vocab.RACE_BITS[r] for r in vocab.FACTION_RACES[fac]), fac
assert vocab.glide_race("Scourge") == "Undead" and vocab.race_token("Undead") == "Scourge"
for r in ("Human", "NightElf", "Skyborne", None):
    assert vocab.glide_race(r) == r and vocab.race_token(r) == r
    assert vocab.glide_race(vocab.race_token(r)) == r
assert set(vocab.HORDE_RACES) == set(vocab.FACTION_RACES["Horde"]) | {"Scourge"}
for fac, classes in vocab.FACTION_CLASSES.items():
    assert set(classes) <= set().union(*(vocab.RACE_CLASSES[r] for r in vocab.FACTION_RACES[fac])), fac
from knowledge.api import guides  # noqa: E402
for raw, want in (("scourge", "Undead"), ("Scourge", "Undead"), ("UNDEAD", "Undead"), ("night elf", "NightElf"),
                  ("Human", "Human"), (" Troll ", "Troll")):
    p = guides.player_from({"class": "warrior", "race": raw})
    assert p is not None and p["race"] == want, (raw, p)
assert guides.player_from({"class": "warrior", "race": "Scourge"})["faction"] == "Horde"

# ---- the coach-flag decoder sits with the strip registry; state no longer reaches into rules ---------------------
import strip_spec  # noqa: E402
from state import GameState  # noqa: E402
st = GameState()
assert st.coach_flags() == strip_spec.coach_flags(None) and all(st.coach_flags().values())
for cf, off in (("6", {"coach", "buffs", "gear", "pace", "upkeep", "voice"}), ("junk", set()), (127, {"voice"})):
    st.core = {"cf": cf}
    assert {k for k, on in st.coach_flags().items() if not on} == off, (cf, st.coach_flags())
with open(os.path.join(ROOT, "companion", "state.py"), encoding="utf-8") as fh:
    src = fh.read()
assert "import rules" not in src and "from rules" not in src

# ---- message kinds and the offline coach's why phrases -----------------------------------------------------------
import msg_kinds  # noqa: E402
assert msg_kinds.RECENT < msg_kinds.COACH_SAID < msg_kinds.HISTORY and msg_kinds.HISTORY - msg_kinds.COACH_SAID == {"info"}
assert vocab.COACH_SAID == msg_kinds.COACH_SAID                       # the knowledge side's copy (it can't import companion)
import offline_coach  # noqa: E402
from knowledge.api import reasons  # noqa: E402
assert offline_coach.WHY_TEXT == reasons.WHY_TEXT                    # the fallback copy says the same words
assert offline_coach.why_phrases() is reasons.WHY_TEXT               # and the knowledge layer's own are used
ctx = {"knowledge": {"step": {"kind": "turnin", "why": ["on_the_way"]}}}
assert offline_coach.why_line(ctx) == "Why this step: turnin step; on the way: little detour.", offline_coach.why_line(ctx)

# ---- no look-alike copy comes back -------------------------------------------------------------------------------
# a little try: return int(x) / float(x) except (TypeError, ValueError) reader, or a hand-written kind / bit table
COPY = re.compile(r"try:\n\s+return (?:int|float)\((?:\w+|\w+\.get\(\w+\))\)\n\s+except \(TypeError, ValueError\)")
TABLE = re.compile(r"\{\s*1: \"WARRIOR\"|\"WARRIOR\": 1,|16: \"Scourge\"|\"Scourge\": 16|\"on the way, little detour\"")
# kind lists: the engine's in companion/msg_kinds.py, the knowledge layer's (sources/flags.py, eval) in vocab.py
KINDS = re.compile(r"_KINDS = \(\"callout\"|\(\"callout\", \"answer\"")
SHARED = {os.path.join("knowledge", "values.py"), os.path.join("knowledge", "vocab.py"),
          os.path.join("companion", "msg_kinds.py"), os.path.join("tests", "test_helpers.py")}
found = []
for folder in ("companion", "knowledge", "tools", "wowfiles"):
    for path in glob.glob(os.path.join(ROOT, folder, "**", "*.py"), recursive=True):
        rel = os.path.relpath(path, ROOT)
        if rel in SHARED:
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        rxs = (COPY, TABLE, KINDS)
        found += ["%s: %s" % (rel, m.group(0).splitlines()[0]) for rx in rxs for m in rx.finditer(text)]
        found += ["%s: def %s" % (rel, n) for n in re.findall(r"^\s*def (ival|to_int|_int|_num|num)\(", text, re.M)
                  if not (rel == os.path.join("knowledge", "heuristics", "engine.py") and n in ("_int", "_num"))]
assert not found, "\n".join(found)
print("OK")
