"""A Lua runtime for reading other projects' Lua data files (tools/build_seed.py): the files run with no way out of
Lua. The runtime has no Python bridge (no `python` global, no eval), and each chunk runs in an environment of the
plain functions data files use (pairs, select, string, table, math, ...): no os, io, load, require, dofile, debug or
package, so a file that tries anything else fails instead of reaching the machine.

    lua = runtime()
    chunk = loader(lua)(source_text, "@QuestDB.lua")      # None when it doesn't compile
"""
from lupa import LuaRuntime

SAFE = ("assert", "error", "ipairs", "next", "pairs", "pcall", "rawequal", "rawget", "rawlen", "rawset", "select",
        "setmetatable", "getmetatable", "tonumber", "tostring", "type", "xpcall")


def runtime():
    return LuaRuntime(unpack_returned_tuples=True, register_eval=False, register_builtins=False)


def loader(lua):
    """fn(src, name) -> the compiled chunk (text only, never bytecode) in a fresh sandboxed environment, or None."""
    return lua.execute("""
        local safe = ...
        return function(src, name)
          local env = {}
          for _, k in ipairs(safe) do env[k] = _G[k] end
          env.unpack = table.unpack or unpack
          env.string, env.table, env.math = string, table, math
          env._G = env
          local chunk = load(src, name, "t", env)     -- nil (and a message) when it doesn't compile: just nil here
          return chunk
        end
    """, lua.table_from(list(SAFE)))
