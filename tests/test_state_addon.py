#!/usr/bin/env python3
"""GameState and what the addon reports about itself and its lists (addon 0.15.3):
- the quest log is taken only when all its chunks are there and match the core's hash qh (a chunk of a new log next
  to a stale one of the old never mixes the two), and a shorter log is taken as soon as the core says so;
- core er and page E show as summary()["addon_errors"], and the health page says so."""
import os
import sys
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import health  # noqa: E402
from protocol import list_hash  # noqa: E402
from state import GameState  # noqa: E402


def core(chunks, **kw):
    return dict({"qn": str(len(chunks)), "qh": str(list_hash(",".join(chunks)))}, **kw)


# ---- the quest log --------------------------------------------------------------------------------------------
st = GameState()
old = ["100:0:1/5,101:0:0/3", "102:1:"]
st.update(core(old), {"pg": "Q0", "q": old[0]})
assert st.quests == {}, "one chunk of two: not taken yet"
st.update(core(old), {"pg": "Q1", "q": old[1]})
assert sorted(st.quests) == [100, 101, 102] and st.quests[100]["obj"] == [(1, 5)], st.quests

new = ["100:0:2/5,103:0:0/1", "102:1:,104:0:0/2"]       # 101 abandoned, 103 and 104 accepted
st.update(core(new), {"pg": "Q0", "q": new[0]})
assert sorted(st.quests) == [100, 101, 102], "new Q0 with the old Q1 still to come: the old log stays whole"
st.update(core(new), {"pg": "Q1", "q": new[1]})
assert sorted(st.quests) == [100, 102, 103, 104] and st.quests[100]["obj"] == [(2, 5)], st.quests

st.update(core(new), {"pg": "Q0", "q": "999:0:"})       # a chunk that doesn't hash to qh (a cut or garbled page)
assert sorted(st.quests) == [100, 102, 103, 104], "a chunk that doesn't match the hash is never taken"

short = ["100:0:2/5,103:0:0/1"]                         # 102, 104 turned in: one chunk, Q0 already sent
st.update(core(new), {"pg": "Q0", "q": short[0]})
st.update(core(short), {})
assert sorted(st.quests) == [100, 103], "a shorter log is taken when the core says so: %s" % sorted(st.quests)
print("quest log OK")

# ---- the addon's errors ---------------------------------------------------------------------------------------
st = GameState()
st.update(core(short), {})
assert st.summary()["addon_errors"] is None
st.update(core(short, er="3"), {})
assert st.summary()["addon_errors"] == {"count": 3, "last": None}
st.update(core(short, er="3"), {"pg": "E", "ex": "Extras.lua:42: attempt to index a nil value"})
assert st.summary()["addon_errors"] == {"count": 3, "last": "Extras.lua:42: attempt to index a nil value"}

app = types.SimpleNamespace(
    args=types.SimpleNamespace(sim=False, app=True), state=st, last_frame_t=st.last_frame, decoded=10, attempts=10,
    rules=types.SimpleNamespace(addon_status={"ok": True, "running": "0.15.3"}), k=types.SimpleNamespace(ok=True),
    coach=types.SimpleNamespace(enabled=False, suspended=None), _kb_status={"ok": False, "reason": "test"})
h = health.Health(app)
h._slow_checks = lambda: {}
got = [c for c in h.checks() if c["id"] == "addon_errors"]
assert got == [{"id": "addon_errors", "area": "addon", "status": "warn", "title": "The addon reported 3 errors",
                "detail": "Last: Extras.lua:42: attempt to index a nil value. In game, /glide debug prints each one "
                          "as it happens; /glide dump shows the count and the last.", "action": None}], got
st.update(core(short), {})
assert not [c for c in h.checks() if c["id"] == "addon_errors"], "no errors (a fresh UI load): no check"
print("addon errors OK")
print("OK")
