"""Small value helpers shared by the API: lenient reading of live-state and model input, cutting text to size,
compass words, and the knowledge base's little vocabularies."""
import math
import re

from knowledge import values as base

RANK = {0: "normal", 1: "elite", 2: "rare elite", 3: "boss", 4: "rare"}
CONFIRMED = ("forever-verified", "forever-fitted")      # param statuses that count as confirmed on Forever
COMPASS = ("north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest")


def as_int(v):
    """An int from loose input ('7', '7.9', 7.9 -> 7); None for missing values, bools, non-numbers, inf and nan."""
    return base.as_int(v, loose=True, bool_ok=False)


def as_num(v):
    """A finite float from loose input; None for missing values, bools, non-numbers, inf and nan."""
    return base.as_float(v, finite=True, bool_ok=False)


def clean(obj):
    """Drop None / empty values from dicts (recursively); keeps 0 and False."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            v = clean(v)
            if v is None or v == "" or v == [] or v == {}:
                continue
            out[k] = v
        return out
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def cut(text, n):
    """The text on one line, cut to n characters with '...' (None stays None)."""
    if text is None:
        return None
    t = " ".join(str(text).split())
    return t if len(t) <= n else t[:max(1, n - 3)].rstrip(" ,;:.") + "..."


def first_line(text, limit=120):
    """The first non-empty line of the text, cut to `limit` characters."""
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return cut(line, limit)
    return None


def uniq(seq):
    """The truthy items of seq, each once, in first-seen order."""
    return list(dict.fromkeys(x for x in seq if x))


def compass(east, north):
    """The compass word (8 points) for a direction given as east / north components."""
    return COMPASS[int(((math.degrees(math.atan2(east, north)) % 360) + 22.5) // 45) % 8]


def money_text(copper):
    """Copper as '1g 2s 3c' (None when it is not a number)."""
    c = as_int(copper)
    if c is None:
        return None
    g, s, cu = c // 10000, c % 10000 // 100, c % 100
    parts = (["%dg" % g] if g else []) + (["%ds" % s] if s else []) + (["%dc" % cu] if cu or not (g or s) else [])
    return " ".join(parts)


def parse_id(x, prefix):
    """An id from loose input: a number, '123' or '<prefix>:123'; else None."""
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, (int, float)):
        return int(x)
    m = re.match(r"^(?:%s:)?\s*(\d+)$" % prefix, str(x).strip(), re.I)
    return int(m.group(1)) if m else None


def id_or_name(id_, name, prefix):
    """(id or None, name or None) from loose model input: numbers, 'q:123', or names in either slot."""
    for v in (id_, name):
        n = parse_id(v, prefix)
        if n is not None:
            return n, None
    for v in (name, id_):
        if isinstance(v, str) and v.strip():
            return None, v.strip()
    return None, None
