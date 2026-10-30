#!/usr/bin/env python3
"""The Lua reader's string tokens (knowledge/sources/_lua.py _TOKEN): written "plain run, then (escape, plain run)*"
for speed, they must read exactly the strings the old "(escape | plain char)*" spelling read: escapes, escaped
quotes, a backslash right before the closing quote, a backslash-newline, and an unclosed string failing with the
same error at the same place.

Run: python3 tests/test_lua_strings.py   (prints OK)
"""
import os
import random
import re
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
from knowledge.sources import _lua  # noqa: E402

# the spelling _TOKEN had before (the reference): same tokens, same matches
OLD_STR = re.compile(r""""(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'""", re.S)
TOKEN_STR = re.search(r"\(\?P<str>(.*?)\)\n", _lua._TOKEN.pattern).group(1)     # _TOKEN's string alternatives
assert TOKEN_STR.startswith(r'"[^"\\\n]*(?:\\.'), TOKEN_STR                     # in the unrolled form
NEW_STR = re.compile(TOKEN_STR, re.S)


def lua_error(src):
    try:
        _lua.saved_variables(src)
    except _lua.LuaError as e:
        return str(e)
    raise AssertionError("read %r" % src)


# values
sv = _lua.saved_variables(
    'A = "tab\\there \\"quoted\\" and \\\\ back"\n'
    "B = 'it\\'s \"double\" inside'\n"
    'C = "ends in a backslash\\\\"\n'
    'D = "line one\\\nline two"\n'
    'E = { "", \'\', "\\65\\066\\x43", "caf\\195\\169" }\n')
assert sv["A"] == 'tab\there "quoted" and \\ back', sv["A"]
assert sv["B"] == "it's \"double\" inside", sv["B"]
assert sv["C"] == "ends in a backslash\\", sv["C"]
assert sv["D"] == "line one\nline two", sv["D"]
assert sv["E"] == ["", "", "ABC", "café"], sv["E"]

# tokens: the string token ends at the right quote
toks = _lua.tokenize('x = "a\\"b" .. \'c\\\\\' .. "d"')
assert [t[1] for t in toks if t[0] == "str"] == ['"a\\"b"', "'c\\\\'", '"d"'], toks

# unclosed strings: the same error as before, at the opening quote
assert lua_error('X = "no end') == "bad character at 4: '\"no end'", lua_error('X = "no end')
assert lua_error('X = "escaped quote\\"') == "bad character at 4: '\"escaped quote\\\\\"'", lua_error('X = "escaped quote\\"')
assert lua_error("X = 'two\nlines'").startswith("bad character at 4: "), lua_error("X = 'two\nlines'")
assert lua_error('X = "trailing backslash\\').startswith("bad character at 4: ")

# the two spellings agree on random text built from the characters that matter
rng = random.Random(7)
alphabet = ['"', "'", "\\", "\n", "a", "b", " ", "é"]
for _ in range(20000):
    s = rng.choice(['"', "'"]) + "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))
    a, b = OLD_STR.match(s), NEW_STR.match(s)
    assert (a and a.group()) == (b and b.group()), (s, a, b)
print("OK")
