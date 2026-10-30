"""Player feedback flags: "/glide mark [note]" (or the Glide key binding) -> one line in data/live/flags.jsonl.

The addon puts a "mark" event in the live ring (core e1..e3 = "id:mark:<note>", note <= 40 bytes, cleaned
of ;=|:, empty when marked from the key binding). The app calls Feedback.on_mark() with what the
companion knew at that moment, so the flag can be reviewed later:

    {"t": <unix time>, "note": "...", "state": <GameState.summary() without big lists>,
     "last_messages": [<last 5 coach messages>], "recent_triggers": [<triggers>]}

State values that are lists/dicts/sets with more than BIG items are left out of the copy (the session
log in data/live/ keeps every frame); their sizes are listed under state["_omitted"]. Sets become
sorted lists, objects (e.g. rules.Trigger) their public attributes, anything else str().
Pure file I/O and stdlib only; no imports from the companion.
"""
import json
import math
import os
import threading
import time

from log import log

BIG = 8                 # a state value with more items than this is "big" and left out
ALWAYS_KEEP = {"target", "corpse", "training"}   # small records that happen to have many fields
LAST_MESSAGES = 5       # coach messages kept with a flag
MAX_TRIGGERS = 20       # triggers kept with a flag (the most recent ones)
_DEPTH = 6              # nesting limit when copying values


def _jsonable(v, depth=0):
    """A JSON-safe copy of v (dicts, lists, numbers, strings, None)."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if depth >= _DEPTH:
        return str(v)
    if isinstance(v, dict):
        return {str(k): _jsonable(x, depth + 1) for k, x in v.items()}
    if isinstance(v, (set, frozenset)):
        items = [_jsonable(x, depth + 1) for x in v]
        try:
            return sorted(items)
        except TypeError:
            return items
    if isinstance(v, (list, tuple)) or (hasattr(v, "__iter__") and hasattr(v, "__len__")
                                        and not isinstance(v, (bytes, bytearray))):
        return [_jsonable(x, depth + 1) for x in v]
    if hasattr(v, "__dict__"):
        return {k: _jsonable(x, depth + 1) for k, x in vars(v).items() if not k.startswith("_")}
    return str(v)


def _size(v):
    if isinstance(v, (list, tuple, set, frozenset, dict)):
        return len(v)
    return None


def slim_state(summary):
    """GameState.summary() minus big lists, JSON-safe; sizes of what was left out in "_omitted"."""
    if not isinstance(summary, dict):
        return _jsonable(summary)
    out, omitted = {}, {}
    for k, v in summary.items():
        n = _size(v)
        if n is not None and n > BIG and k not in ALWAYS_KEEP:
            omitted[str(k)] = n
        else:
            out[str(k)] = _jsonable(v)
    if omitted:
        out["_omitted"] = omitted
    return out


class Feedback:
    def __init__(self, root_dir):
        self.path = os.path.join(root_dir, "data", "live", "flags.jsonl")
        self._lock = threading.Lock()

    def on_mark(self, note, state_summary, recent_messages, triggers_recent):
        """Append one flag. Returns the record written, or None if the file could not be written."""
        rec = {
            "t": time.time(),
            "note": (note or "").strip(),
            "state": slim_state(state_summary),
            "last_messages": [_jsonable(m) for m in list(recent_messages or [])[-LAST_MESSAGES:]],
            "recent_triggers": [_jsonable(t) for t in list(triggers_recent or [])[-MAX_TRIGGERS:]],
        }
        line = json.dumps(rec, ensure_ascii=False, default=str)
        try:
            with self._lock:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except OSError as e:
            log("feedback", "could not write %s: %s" % (self.path, e))
            return None
        return rec

    def count(self):
        """Number of flags saved so far."""
        try:
            with self._lock, open(self.path, encoding="utf-8") as fh:
                return sum(1 for line in fh if line.strip())
        except OSError:
            return 0
