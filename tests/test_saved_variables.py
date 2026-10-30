#!/usr/bin/env python3
"""SavedVariables through the one Lua table reader (knowledge/sources/_lua.py, also tools/svparse.py): every global,
a nil the game wrote inside a table keeps its array position, bytes that aren't UTF-8 (RXPGuides' compressed guides)
survive the read so they still inflate, escapes decode, and what the reader can't read is an error, not a guess.

Run: python3 tests/test_saved_variables.py   (prints OK)
"""
import os
import subprocess
import sys
import tempfile
import zlib

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
from knowledge.sources import _lua  # noqa: E402

guide = "#name 1-6 Northshire\n#group RestedXP Alliance 1-20\nstep\n    .accept 783\n" * 4
blob = zlib.compress(guide.encode("utf-8"))[2:-4]              # raw deflate, as RXPGuides stores it
assert any(b >= 0x80 for b in blob)
body = blob.replace(b"\\", b"\\\\").replace(b'"', b'\\"').replace(b"\n", b"\\n").replace(b"\r", b"\\r")
src = (b'GlideRecorderDB = {\n\t["mapBounds"] = {\n\t\t{ 1, 2.5, -3, 4, 5 }, -- [1]\n\t},\n'
       b'\t["fp"] = {\n\t\tnil, -- [1]\n\t\t"Stormwind  Elwynn", -- [2]\n\t\tnil, -- [3]\n\t\t"Goldshire", -- [4]\n\t},\n'
       b'\t["note"] = "caf\\195\\169 \\"quoted\\"\\\nnext line",\n\t["off"] = false,\n}\n'
       b'RXPCData = {\n\t["guide"] = {\n\t\t["groupOrContent"] = "' + body + b'",\n\t},\n}\nGlideVersion = 3\n')
tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "Glide.lua")
with open(path, "wb") as fh:
    fh.write(src)

sv = _lua.read_saved_variables(path)
assert sorted(sv) == ["GlideRecorderDB", "GlideVersion", "RXPCData"], sorted(sv)
g = sv["GlideRecorderDB"]
assert g["mapBounds"] == [[1, 2.5, -3, 4, 5]], g["mapBounds"]
assert g["fp"] == [None, "Stormwind  Elwynn", None, "Goldshire"], g["fp"]        # positions kept
assert g["note"] == 'caf\u00e9 "quoted"\nnext line' and g["off"] is False, g
text = sv["RXPCData"]["guide"]["groupOrContent"]
got = set()
for enc in ("latin-1", "utf-8"):                                # as tools/build_db.py's rxp_from_own_sv reads it
    try:
        got.add(zlib.decompress(text.encode(enc, "surrogateescape"), -15).decode("utf-8"))
    except (UnicodeError, zlib.error):
        pass
assert got == {guide}, got
assert sv["GlideVersion"] == 3

# the addon-data reader (assignments) still drops nils and reports a table it can't read
a = _lua.assignments('t = { nil, "b" }\nu = { f = function() end }\n')
assert a["t"] == {2: "b"} and [lv for lv, _e in a["__errors__"]] == ["u"], a

try:
    _lua.saved_variables('X = { 1, 2 \nY = 3')
    raise AssertionError("read a table that never closes")
except _lua.LuaError:
    pass

# GlideRecorderDB's readers (tools/build_db.py, knowledge/sources/map_bounds.py) check its schema and the sessions'
# addon versions (docs/RECORDER.md): a layout this code doesn't know is warned about, then read as far as it can
def recorder(text):
    p = os.path.join(tmp, "Rec.lua")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    warned = []
    return _lua.recorder_db(p, warn=warned.append), warned


db, warned = recorder('GlideRecorderDB = { ["schema"] = 1, ["sessions"] = { ["a"] = { ["addon"] = "0.15.3" } } }')
assert db["schema"] == 1 and warned == [], warned
db, warned = recorder('GlideRecorderDB = { ["schema"] = 2, ["sessions"] = { ["a"] = { ["addon"] = "99.1" },'
                      ' ["b"] = { ["addon"] = "0.9.0" } }, ["mapBounds"] = {} }')
assert not db["mapBounds"] and len(warned) == 2, warned
assert "schema 2" in warned[0] and "Glide 99.1, newer than" in warned[1], warned
assert recorder('Other = 1') == ({}, []), "no GlideRecorderDB: an empty one, nothing to warn about"

# tools/svparse.py, the old name, is the same reader
r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "svparse.py"), path], capture_output=True, text=True)
assert r.returncode == 0 and '"Goldshire"' in r.stdout, r.stdout + r.stderr
print("OK")
