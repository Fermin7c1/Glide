"""Classic drop chances from the player's own AtlasLootClassic install (AtlasLootClassic_DungeonsAndRaids/droprate.lua,
GPL): what effort.drop_chance starts from (its "classic" prior, conf 0.5) before the player's own kills say more. The
file is read where the player has it, never shipped with Glide, never executed and never copied: a restricted reader
takes only the one shape the file has, and anything else gives {} (the coach then says it doesn't know the chance).

The shape (AtlasLoot's own, between its --##START-DATA## and --##END-DATA## markers):
    local DropRateData = {
        [1853] = {              -- a creature's id (or a quoted name: a boss of a shared loot table)
            [16686] = 13.84,    -- an item's id = its drop chance in percent
        },
    }
Comments, trailing separators and both quote kinds are read; a value that isn't a number (a call, a name, an
expression), a key that isn't an id or a quoted name, nesting deeper or shallower, or more than the one table makes the
whole file {} (a newer AtlasLoot may have changed it: guessing would put wrong numbers in the coach's mouth). What
comes after the data (its conditional fix-ups for classic's phases before 5, and AddData) is code and is not read.

    find(wow_dir) -> path | None    where the player's file is (None: AtlasLoot isn't installed there)
    read(path) -> {npc or boss key: {item id: chance 0..1}}   ({} for a file that isn't there or isn't the shape)
    load(wow_dir) -> the same, read again only when the file changes (size and mtime)
    rate(table, npc, item) -> chance 0..1 | None
"""
import os
import re
import threading

ADDON = "AtlasLootClassic_DungeonsAndRaids"
FILE = "droprate.lua"
MAX_BYTES = 4 * 1024 * 1024         # the file is ~50 KB; anything this big isn't it
START, END = "--##START-DATA##", "--##END-DATA##"

# the tokens the shape is made of; anything else in the data is a mismatch (an expression, a call: never evaluated)
_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<comment>--\[(?P<eq>=*)\[.*?\](?P=eq)\]|--[^\n]*)
  | (?P<num>-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?(?![\w.]))
  | (?P<str>"(?:[^"\\\n]|\\.){0,200}"|'(?:[^'\\\n]|\\.){0,200}')
  | (?P<name>[A-Za-z_]\w*)
  | (?P<punct>[{}\[\]=,;])
""", re.S | re.X)

_lock = threading.Lock()
_cache: dict = {}                   # path -> ((size, mtime_ns), table)


class _Mismatch(ValueError):
    pass


def find(wow_dir):
    """The player's droprate.lua under the WoW client folder (<wow_dir>/Interface/AddOns/<ADDON>/droprate.lua), or
    None when there is no folder or AtlasLoot's dungeon module isn't installed."""
    if not wow_dir or not isinstance(wow_dir, str):
        return None
    path = os.path.join(wow_dir, "Interface", "AddOns", ADDON, FILE)
    return path if os.path.isfile(path) else None


def _tokens(text):
    """[(kind, value)] for the data, comments and blanks left out; _Mismatch at a character no token starts with."""
    out, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise _Mismatch("unexpected %r at %d" % (text[pos:pos + 12], pos))
        pos = m.end()
        kind = m.lastgroup or "ws"
        if kind in ("ws", "comment", "eq"):
            continue
        if kind == "num":
            out.append(("num", float(m.group("num"))))
        elif kind == "str":
            out.append(("str", m.group("str")[1:-1]))
        else:
            out.append((kind, m.group(kind)))
    return out


class _Reader:
    """The restricted reader over the tokens: [local] NAME = { [key] = { [id] = number, ... }, ... } and nothing
    after it. Keys: an id (a whole number) or a quoted name; values: a table at the first level, a number at the
    second."""

    def __init__(self, toks):
        self.toks, self.i = toks, 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self, kind, value=None):
        k, v = self.peek()
        if k != kind or (value is not None and v != value):
            raise _Mismatch("expected %s %s, got %s %r" % (kind, value or "", k, v))
        self.i += 1
        return v

    def key(self, ids_only):
        """A field's key: [123], ["Name"] (not where ids_only), or a bare name (AtlasLoot's own comment shows
        `itemID = rate`; a name isn't an id, so only where names are allowed)."""
        k, v = self.peek()
        if k == "name" and not ids_only:
            self.i += 1
            return v
        self.take("punct", "[")
        k, v = self.peek()
        self.i += 1
        if k == "num" and isinstance(v, float) and v.is_integer() and v > 0:
            key = int(v)
        elif k == "str" and not ids_only and isinstance(v, str) and v.strip():
            key = int(v) if v.strip().isdigit() else v.strip()
        else:
            raise _Mismatch("bad key %r" % (v,))
        self.take("punct", "]")
        return key

    def fields(self, read_value, ids_only):
        """{key: value} of one table ({ field (, | ;) ... }), the opening brace already taken."""
        out = {}
        while True:
            k, v = self.peek()
            if k == "punct" and v == "}":
                self.i += 1
                return out
            key = self.key(ids_only)
            self.take("punct", "=")
            out[key] = read_value()
            k, v = self.peek()
            if k == "punct" and v in (",", ";"):
                self.i += 1
            elif not (k == "punct" and v == "}"):
                raise _Mismatch("expected , or } after a field, got %r" % (v,))

    def rate(self):
        v = self.take("num")
        if not isinstance(v, float):
            raise _Mismatch("not a number")
        return v

    def boss(self):
        self.take("punct", "{")
        return self.fields(self.rate, True)

    def table(self):
        if self.peek() == ("name", "local"):
            self.i += 1
        self.take("name")
        self.take("punct", "=")
        self.take("punct", "{")
        out = self.fields(self.boss, False)
        if self.i != len(self.toks):
            raise _Mismatch("more after the table")
        return out


def parse(text):
    """{npc or boss key: {item id: chance 0..1}} from droprate.lua's text; {} when it isn't the shape. Chances are
    AtlasLoot's percents over 100 (13.84 -> 0.1384); one outside (0, 100] is left out (no chance known)."""
    if not isinstance(text, str):
        return {}
    a, b = text.find(START), text.find(END)
    data = text[a + len(START):b] if 0 <= a < b else text
    try:
        raw = _Reader(_tokens(data)).table()
    except (_Mismatch, RecursionError, ValueError):
        return {}
    out = {}
    for npc, items in raw.items():
        rates = {i: round(r / 100.0, 6) for i, r in items.items() if 0 < r <= 100}
        if rates:
            out[npc] = rates
    return out


def read(path):
    """The drop chances in the player's droprate.lua at path (see parse); {} for no file, one that can't be read or
    is too big, or one that isn't the shape. The file is only read: never executed, copied or written."""
    if not path:
        return {}
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
    except OSError:
        return {}
    if len(raw) > MAX_BYTES:
        return {}
    return parse(raw.decode("utf-8-sig", errors="replace"))


def load(wow_dir):
    """read(find(wow_dir)), kept while the file's size and modification time stay the same."""
    path = find(wow_dir)
    if not path:
        return {}
    try:
        st = os.stat(path)
    except OSError:
        return {}
    stamp = (st.st_size, st.st_mtime_ns)
    with _lock:
        hit = _cache.get(path)
        if hit and hit[0] == stamp:
            return hit[1]
    table = read(path)
    with _lock:
        _cache[path] = (stamp, table)
    return table


def rate(table, npc, item):
    """The chance (0..1) the table gives the item from this creature (an id as a number or text, or a boss name), or
    None."""
    if not isinstance(table, dict) or npc is None or item is None:
        return None
    try:
        item = int(item)
    except (TypeError, ValueError):
        return None
    keys = [npc]
    if isinstance(npc, str) and npc.strip().isdigit():
        keys.append(int(npc))
    elif isinstance(npc, int):
        keys.append(str(npc))
    for k in keys:
        rates = table.get(k)
        if isinstance(rates, dict) and isinstance(rates.get(item), (int, float)):
            return float(rates[item])
    return None
