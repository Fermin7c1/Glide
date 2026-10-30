"""Text as RXP's Lua code sees it: the C-locale character classes (%s, %S), Lua 5.1 tonumber on strings, and the
WoW/RXP colour, icon and link markup stripped down to plain words."""
import re

WS = " \t\n\r\x0b\x0c"
SPACE = r"[ \t\n\r\x0b\x0c]"
NONSPACE = r"[^ \t\n\r\x0b\x0c]"

_RE_NUM = re.compile(r"^[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")
_RE_INT = re.compile(r"^[+-]?[0-9]+$")
_RE_HEX = re.compile(r"^[+-]?0[xX][0-9a-fA-F]+$")

_RE_M_LINK = re.compile(r"\|H[^|]*\|h(.*?)\|h")
_RE_M_ICON = re.compile(r"\|T[^|]*\|t")
_RE_M_ATLAS = re.compile(r"\|A[^|]*\|a")
_RE_M_COLOR = re.compile(r"\|c(?:RXP_[A-Z]+_|[0-9a-fA-F]{8}|n[A-Za-z0-9_]+:)")
_RE_M_RESET = re.compile(r"\|r")
_RE_M_NPCREF = re.compile(r"npc:(.*?):([0-9]+)")
_RE_M_NPCID = re.compile(r"::[0-9]+")
_RE_M_SPACES = re.compile(r"[ \t]+")


def plain(text):
    """Strip WoW/RXP markup, keep the words.

    `|cRXP_ENEMY_Crag Boars|r` -> `Crag Boars`; `|cffRRGGBB..|r` likewise; `|T133970:0|t` (icons) and
    `|A..|a` (atlas) removed; `|H..|h[x]|h` -> `[x]`; `Name::123` -> `Name`; literal `\\n` -> newline.
    """
    if text is None:
        return None
    t = text.replace("||", "\x00")
    t = _RE_M_LINK.sub(r"\1", t)
    t = _RE_M_ICON.sub("", t)
    t = _RE_M_ATLAS.sub("", t)
    t = _RE_M_COLOR.sub("", t)
    t = _RE_M_RESET.sub("", t)
    t = _RE_M_NPCREF.sub(r"\1", t)
    t = _RE_M_NPCID.sub("", t)
    t = t.replace("\\n", "\n").replace("\x00", "|")
    lines = [_RE_M_SPACES.sub(" ", ln).strip() for ln in t.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def lua_num(s):
    """tonumber(s) for strings: int for integer literals, float otherwise, None if not a number."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return s
    t = s.strip(WS)
    if not t:
        return None
    if _RE_HEX.match(t):
        return int(t, 16)
    if not _RE_NUM.match(t):
        return None
    if _RE_INT.match(t):
        return int(t)
    return float(t)


def lua_int(s):
    """lua_num(s) when it is a whole number, else None."""
    v = lua_num(s)
    if v is None:
        return None
    if isinstance(v, float):
        return int(v) if v.is_integer() else None
    return v


def lua_float(s):
    """lua_num(s) as a float, None when it is not a number."""
    v = lua_num(s)
    return float(v) if v is not None else None
