"""Reading numbers from loose input: the one home of the int / float parsers every part of Glide uses (the engine,
the knowledge layer, tools/ and wowfiles/). Standard library only, and importing it loads nothing else of the
knowledge package, so the engine can use it when the knowledge layer itself won't load.

The parsers never raise. What they accept, by option:

    as_int(v)                         int(v): '3', ' 3 ', 7.9 -> 7, True -> 1; '7.9', '1e3', '', None -> default
    as_int(v, loose=True)             decimal and exponent strings too: '7.9' -> 7, '1e3' -> 1000; nan / inf -> default
    as_int(v, bool_ok=False)          True / False -> default (a bool is not a count)
    as_float(v)                       float(v): '7.9', ' 3 ', '1e3', True -> 1.0, 'inf' -> inf; '', None -> default
    as_float(v, finite=True)          nan / inf -> default

Lua-parsed saved variables give booleans for flags, and their loaders count on True -> 1: bool_ok stays on unless a
caller asks otherwise. Model input and live state go through knowledge/api/values.py, which reads them loosely and
without bools."""
import math


def as_int(v, default=None, *, loose=False, bool_ok=True):
    """An int from loose input, or `default` (see the module docstring for what each option accepts)."""
    if v is None or (not bool_ok and isinstance(v, bool)):
        return default
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        if not loose:
            return default
    f = as_float(v, finite=True)
    return default if f is None else int(f)


def as_float(v, default=None, *, finite=False, bool_ok=True):
    """A float from loose input, or `default` (finite: nan and inf are `default` too)."""
    if v is None or (not bool_ok and isinstance(v, bool)):
        return default
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return default
    return default if finite and not math.isfinite(f) else f
