#!/usr/bin/env python3
"""tools/lua_sandbox.py: other projects' Lua data files (build_seed.py) read their data, but can't reach the machine:
no os/io/load/require, no Python bridge. Needs lupa."""
import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
try:
    import lua_sandbox
except ImportError:
    print("SKIP: needs lupa")
    sys.exit(77)

lua = lua_sandbox.runtime()
load = lua_sandbox.loader(lua)

# a data file as ForeverGuide writes them: `local _, ns = ...`, tables, the plain library functions
ns = lua.table()
chunk = load('local _, ns = ...\nns.QuestDB = { [1] = { "A", select(2, 1, 5) } }\nns.n = #string.rep("x", 3)', "@QuestDB.lua")
chunk("FG", ns)
assert ns.QuestDB[1][1] == "A" and ns.QuestDB[1][2] == 5 and ns.n == 3

# anything else fails inside Lua
for evil in ('os.execute("calc")', 'io.open("C:/x.txt", "w")', 'require("os")', 'load("return 1")()',
             'dofile("x.lua")', 'python.eval("1")', 'debug.getinfo(1)', 'package.loadlib("x", "y")',
             'string.dump(print)'):
    f = load(evil, "@evil.lua")
    try:
        f()
        raise AssertionError("ran: " + evil)
    except AssertionError:
        raise
    except Exception:  # noqa: BLE001 - lupa's LuaError: attempt to index a nil value / call a nil value
        pass
bridge = lua.globals().python                 # lupa's own table: no eval, no builtins, and the chunks can't see it
assert bridge is None or (bridge.eval is None and bridge.builtins is None), "no Python bridge"
assert load("\x1bLua bytecode", "@b") is None, "bytecode isn't loaded"
print("OK")
