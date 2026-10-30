#!/usr/bin/env python3
"""_lua.saved_variable_field, the reader map_bounds.sv_bounds uses for GlideRecorderDB.mapBounds: it finds the table
with a bracket scan and parses only it, and must give what the full SavedVariables parse gives. Fixtures put
mapBounds at several nestings (only GlideRecorderDB's own field counts), hide brackets, quotes, escaped quotes and
fake `["mapBounds"] = {` text in strings and comments, and use a decoy value "mapBounds" and a mapBoundsBuild key.

Run: python3 tests/test_sv_field.py   (prints OK)
"""
import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
from knowledge.sources import _lua  # noqa: E402


def old(src):
    """What sv_bounds read before: the whole file parsed."""
    v = (_lua.saved_variables(src).get("GlideRecorderDB") or {}).get("mapBounds")
    return v if isinstance(v, (dict, list)) else None


def new(src):
    return _lua.saved_variable_field(src, "GlideRecorderDB", "mapBounds")


TRICKY = r'''-- a comment with { and ["mapBounds"] = { 9, 9, 9, 9, 9 }
--[==[ long comment { { ["mapBounds"] = { ]] } ]==]
GlideLinkDB = {
	["mapBounds"] = { [1] = { 0, 1, 2, 3, 4 } },     -- another global's field: ignored
}
GlideRecorderDB = {
	["note"] = "a } and a { and [\"mapBounds\"] = { 7 }",
	['single'] = 'it\'s { here',
	["decoy"] = "mapBounds",
	["long"] = [=[ ["mapBounds"] = { } ]] }]=],
	["sessions"] = {
		{
			["mapBounds"] = { [5] = { 1, 1, 1, 1, 1 } },   -- nested deeper: ignored
			["text"] = "\\\"{",
		}, -- [1]
	},
	["mapBoundsBuild"] = "1.60.1.70338",
	["mapBounds"] = {
		[1429] = {
			0, -- [1]
			-7939.58, -- [2]
			1535.42, -- [3]
			-10254.16, -- [4]
			-1935.41, -- [5]
			"Elwynn \"Forest\" {}", -- [6]
		},
		[2521] = { ["instance"] = 2991, ["x0"] = 1200.5, ["y0"] = 2400.25, ["x1"] = -800.5, ["y1"] = -600.75 },
		[14] = { nil, 1, 2, 3, 4 },
	},
	["after"] = { "}" },
}
GlideVersion = 3
'''

want = new(TRICKY)
assert want == old(TRICKY), (want, old(TRICKY))
assert sorted(want) == [14, 1429, 2521] and want[1429][5] == 'Elwynn "Forest" {}', want
assert want[14] == [None, 1, 2, 3, 4], want[14]                # a nil keeps its array position

cases = {
    "name key": 'GlideRecorderDB = { mapBounds = { { 1, 2, 3, 4, 5 } }, x = 1 }',
    "list form": 'GlideRecorderDB = {\n["mapBounds"] = {\n{ 1, 2.5, -3, 4, 5 }, -- [1]\n},\n}\n',
    "escaped key": 'GlideRecorderDB = { ["map\\066ounds"] = { [3] = { 1, 2, 3, 4, 5 } } }',
    "absent": 'GlideRecorderDB = { ["mapBoundsBuild"] = "x", ["sessions"] = { ["mapBounds"] = { 1 } } }',
    "no recorder": 'GlideDB = { ["mapBounds"] = { [1] = { 1, 2, 3, 4, 5 } } }',
    "not a table": 'GlideRecorderDB = { ["mapBounds"] = "none" }',
    "last field wins": 'GlideRecorderDB = { ["mapBounds"] = { [1] = { 1 } }, ["mapBounds"] = { [2] = { 2 } } }',
    "last global wins": 'GlideRecorderDB = { ["mapBounds"] = { [1] = { 1 } } }\nGlideRecorderDB = { ["y"] = 1 }',
    "empty": 'GlideRecorderDB = { ["mapBounds"] = {} }',
}
for label, src in cases.items():
    assert new(src) == old(src), (label, new(src), old(src))
assert new(cases["name key"]) == [[1, 2, 3, 4, 5]] and new(cases["escaped key"]) == {3: [1, 2, 3, 4, 5]}
assert new(cases["absent"]) is None and new(cases["last global wins"]) is None

# damage the scan can see is an error, as the full parse raised one
for bad in ('GlideRecorderDB = { ["mapBounds"] = { [1] = { 1 } }', 'GlideRecorderDB = { } }',
            'GlideRecorderDB = { ["a"] = "never closed }\n}', 'GlideRecorderDB = { ["mapBounds"] = { f = function() end } }'):
    try:
        new(bad)
        raise AssertionError("read damaged input: %r" % bad)
    except _lua.LuaError:
        pass
print("OK")
