#!/usr/bin/env python3
"""Classic drop chances from the player's AtlasLootClassic install (companion/atlasloot_rates.py): the fixture (in
droprate.lua's shape: comments, both separators, a boss name key, an exponent) reads as chances 0..1 with the ones
outside (0, 100] left out; every file that isn't the shape (a call, an expression, a name, a string, a hex number,
nesting too deep or too shallow, an unclosed table, a second table, plain code, a file too big, none at all) is {};
nothing in the file is ever executed (the code after the data would raise, and the reader runs no interpreter); find()
looks only where AtlasLoot's dungeon module lives; load() reads again only when the file changes.

Run: python3 tests/test_atlasloot_rates.py   (prints OK)
"""
import builtins
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import atlasloot_rates  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "atlasloot_droprate.lua")


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


# ---- nothing runs: no interpreter, no process, no eval while reading
def refuse(*_a, **_k):
    raise AssertionError("atlasloot_rates ran something")


saved = (subprocess.Popen, os.system, builtins.eval, builtins.exec)
subprocess.Popen, os.system, builtins.eval, builtins.exec = refuse, refuse, refuse, refuse  # type: ignore[assignment]
try:
    table = atlasloot_rates.read(FIXTURE)
finally:
    subprocess.Popen, os.system, builtins.eval, builtins.exec = saved  # type: ignore[assignment]
check("lupa" not in sys.modules, "a Lua runtime was loaded")
src = open(os.path.join(ROOT, "companion", "atlasloot_rates.py"), encoding="utf-8").read()
for bad in ("import subprocess", "lupa", "exec(", "eval(", "loadstring", "dofile"):
    check(bad not in src, "the reader mentions %s" % bad)

# ---- the fixture
check(table == {1853: {13938: 0.1293, 16686: 0.1384}, 10508: {16689: 0.1881, 90003: 0.05},
                "Bug Trio": {90004: 0.15}}, table)
check(atlasloot_rates.rate(table, 1853, 16686) == 0.1384, "the crown")
check(atlasloot_rates.rate(table, "1853", "13938") == 0.1293, "ids as text")
check(atlasloot_rates.rate(table, "Bug Trio", 90004) == 0.15, "a boss name key")
check(atlasloot_rates.rate(table, 1853, 90001) is None, "a 0 chance is no chance")
check(atlasloot_rates.rate(table, 1853, 90002) is None, "over 100 is no chance")
check(atlasloot_rates.rate(table, 1853, 16686) != 0.9999, "the phase fix-up after the data is code, not read")
check(atlasloot_rates.rate(None, 1853, 16686) is None and atlasloot_rates.rate(table, None, 1) is None, "no table")
check(atlasloot_rates.rate(table, 1853, "crown") is None, "an item that isn't an id")

# ---- whatever isn't the shape is {}
GOOD = "local DropRateData = {\n\t[1853] = {\n\t\t[16686] = 13.84,\n\t},\n}\n"
check(atlasloot_rates.parse(GOOD) == {1853: {16686: 0.1384}}, "the bare shape, no markers")
check(atlasloot_rates.parse("--##START-DATA##\n" + GOOD + "--##END-DATA##\nprint(1)\n") == {1853: {16686: 0.1384}},
      "between the markers")
BAD = {
    "a call": 'DropRateData = { [1853] = { [16686] = loadstring("return 13.84")() } }',
    "an expression": "DropRateData = { [1853] = { [16686] = 10 + 3.84 } }",
    "a name as a value": "DropRateData = { [1853] = { [16686] = rate } }",
    "a string as a value": 'DropRateData = { [1853] = { [16686] = "13.84" } }',
    "a hex number": "DropRateData = { [1853] = { [16686] = 0x0D } }",
    "a name as an item key": "DropRateData = { [1853] = { crown = 13.84 } }",
    "a string as an item key": 'DropRateData = { [1853] = { ["16686"] = 13.84 } }',
    "too deep": "DropRateData = { [1853] = { [16686] = { 13.84 } } }",
    "too shallow": "DropRateData = { [1853] = 13.84 }",
    "a field without a key": "DropRateData = { { [16686] = 13.84 } }",
    "unclosed": "DropRateData = { [1853] = { [16686] = 13.84, }",
    "two tables": "A = { [1] = { [2] = 3 } }\nB = { [1] = { [2] = 3 } }",
    "no separator": "DropRateData = { [1853] = { [16686] = 13.84 [13938] = 12.93 } }",
    "a fractional id": "DropRateData = { [1853.5] = { [16686] = 13.84 } }",
    "code": "local Droprate = _G.AtlasLoot.Data.Droprate\nDroprate:AddData({})\n",
    "a function": "DropRateData = { [1853] = function() return {} end }",
    "empty": "",
    "only a comment": "-- nothing here\n",
}
for what, text in BAD.items():
    got = atlasloot_rates.parse(text)
    check(got == {}, "%s read as %r" % (what, got))
check(atlasloot_rates.parse(None) == {}, "None")       # type: ignore[arg-type]

tmp = tempfile.mkdtemp()
try:
    check(atlasloot_rates.read(os.path.join(tmp, "missing.lua")) == {}, "no file")
    check(atlasloot_rates.read(None) == {}, "no path")
    odd = os.path.join(tmp, "odd.lua")
    with open(odd, "wb") as fh:        # bytes that aren't UTF-8 in a comment don't stop the read
        fh.write(b"-- caf\xe9 \xff\nDropRateData = { [1853] = { [16686] = 13.84 } }\n")
    check(atlasloot_rates.read(odd) == {1853: {16686: 0.1384}}, "non-UTF-8 comment")
    bom = os.path.join(tmp, "bom.lua")
    with open(bom, "wb") as fh:        # a byte-order mark and Windows line ends
        fh.write(b"\xef\xbb\xbf" + GOOD.replace("\n", "\r\n").encode("utf-8"))
    check(atlasloot_rates.read(bom) == {1853: {16686: 0.1384}}, "a byte-order mark")
    big = os.path.join(tmp, "big.lua")
    with open(big, "w", encoding="utf-8") as fh:
        fh.write(GOOD)
    keep = atlasloot_rates.MAX_BYTES
    atlasloot_rates.MAX_BYTES = 16
    try:
        check(atlasloot_rates.read(big) == {}, "a file over the size cap")
    finally:
        atlasloot_rates.MAX_BYTES = keep

    # ---- find: only AtlasLoot's dungeon module, under the WoW folder given
    wow = os.path.join(tmp, "_forever_")
    check(atlasloot_rates.find(wow) is None and atlasloot_rates.find(None) is None and atlasloot_rates.find("") is None,
          "no WoW folder")
    addon = os.path.join(wow, "Interface", "AddOns", atlasloot_rates.ADDON)
    os.makedirs(addon)
    check(atlasloot_rates.find(wow) is None, "the module without its droprate.lua")
    path = os.path.join(addon, atlasloot_rates.FILE)
    shutil.copyfile(FIXTURE, path)
    check(atlasloot_rates.find(wow) == path, atlasloot_rates.find(wow))

    # ---- load: read again only when the file changes
    first = atlasloot_rates.load(wow)
    check(first == table, "load reads the file")
    check(atlasloot_rates.load(wow) is first, "unchanged: the kept table")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("local DropRateData = {\n\t[1853] = {\n\t\t[16686] = 20,\n\t},\n}\n")
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    second = atlasloot_rates.load(wow)
    check(second == {1853: {16686: 0.2}}, "changed: read again (%r)" % second)
    check(atlasloot_rates.load(os.path.join(tmp, "nowhere")) == {}, "load with no file")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("OK")
