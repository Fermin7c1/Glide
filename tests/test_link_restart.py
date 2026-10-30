#!/usr/bin/env python3
"""The game link across an addon restart: the addon counts its ring event ids from 1 again after every UI reload
(/reload, /glide save, relog, character switch). The engine keeps hearing events afterwards: GameState,
App.on_frame, and the real addon Lua under lupa reloaded mid-session. Ids that leave the ring between two frames
are counted and logged once."""
import contextlib
import io
import os
import sys
import types

from lupa import lua51

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import protocol  # noqa: E402
import wowmock  # noqa: E402
from state import GameState  # noqa: E402

BASE = {"t": "100", "l": "7", "x": "10", "X": "100"}


def ring(*events):
    """Core keys for a ring of (id, kind, arg), newest first (e1 = newest)."""
    return dict(BASE, **{"e%d" % (k + 1): "%d:%s:%s" % e for k, e in enumerate(events)})


def quiet(fn, *a):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        r = fn(*a)
    return r, out.getvalue()


# ---- GameState: ids restart after a reload -------------------------------------------------------------------
s = GameState()
got = []
for i in range(1, 41):                                   # a session with 40 events before the /reload
    got += s.update(ring((i, "acc", 1000 + i)), {})
assert len(got) == 40 and s.event_max == 40, (len(got), s.event_max)
assert s.update(ring((40, "acc", 1040), (39, "acc", 1039)), {}) == []          # the same ring again: nothing new
after, log = [], ""
for i in range(1, 41):                                   # after the reload the addon counts from 1 again
    new, out = quiet(s.update, ring((i, "acc", 5000 + i)), {})
    after += new
    log += out
assert [a for _, _, a in after] == ["%d" % (5000 + i) for i in range(1, 41)], after
assert s.addon_restarts == 1 and "restarted" in log and s.events_missed == 0, (s.addon_restarts, log)
# the first ring after a restart is new whole, oldest first, even with three events in it
s2 = GameState()
s2.update(ring((12, "lvl", 8), (11, "tin", "5,100"), (10, "acc", 5)), {})
new, _ = quiet(s2.update, ring((3, "die", ""), (2, "zone", "Durotar"), (1, "acc", 6)), {})
assert [k for _, k, _ in new] == ["acc", "zone", "die"], new
assert s2.addon_restarts == 1
# ids skipped between frames: counted and logged once per gap
s3 = GameState()
s3.update(ring((12, "acc", 1), (11, "acc", 2), (10, "acc", 3)), {})
new, out = quiet(s3.update, ring((18, "acc", 9), (17, "acc", 8), (16, "acc", 7)), {})
assert [i for i, _, _ in new] == [16, 17, 18] and s3.events_missed == 3 and out.count("lost") == 1, (new, out)
_, out = quiet(s3.update, ring((18, "acc", 9), (17, "acc", 8), (16, "acc", 7)), {})
assert out == "" and s3.events_missed == 3, out
# the first frame the engine sees has no baseline: nothing counts as lost
s4 = GameState()
_, out = quiet(s4.update, ring((90, "acc", 1)), {})
assert out == "" and s4.events_missed == 0

# ---- App.on_frame: rings after a reload reach the rules, the briefings and the marks ----------------------
import app as glide_app  # noqa: E402
from rules import Rules  # noqa: E402

briefed, pushed, marks = [], [], []
fake = types.SimpleNamespace(
    last_seq=None, last_frame_t=0, _last_pages={}, write_log=lambda o: None, state=GameState(), _ring_primed=False,
    on_message=lambda m: None, rules=Rules({}), _update_switches=lambda f: f, trigger_history=[],
    on_mark=marks.append, coach=types.SimpleNamespace(cfg={}, brief=briefed.append,
                                                      push=lambda trig, st: pushed.extend(t.kind for t in trig)),
    last_log=0)


def frame(seq, core):
    payload = ";".join("%s=%s" % kv for kv in core.items()) + "|"
    quiet(glide_app.App.on_frame, fake, seq, payload)


frame(1, ring((7, "acc", 101), (6, "lvl", 5)))          # engine start: the ring is old news
frame(2, ring((8, "acc", 102), (7, "acc", 101), (6, "lvl", 5)))
assert briefed == ["102"], briefed
frame(3, ring((9, "mark", "before"), (8, "acc", 102), (7, "acc", 101)))
assert marks == ["before"], marks
frame(1, ring((1, "zone", "Durotar")))                   # /reload: seq and event ids start over
frame(2, ring((3, "mark", "after reload"), (2, "acc", 103), (1, "zone", "Durotar")))
assert briefed == ["102", "103"] and marks == ["before", "after reload"], (briefed, marks)
assert "zone_changed" in pushed, pushed

# ---- the real addon, reloaded mid-session ---------------------------------------------------------------------
ADDON = ("Core.lua", "Codec.lua", "State.lua", "Gear.lua", "Extras.lua", "Coach.lua", "Strip.lua")


def load_addon():
    """A fresh UI: every addon file run again into a new Lua state, as ReloadUI does."""
    L = lua51.LuaRuntime(unpack_returned_tuples=True)
    L.execute(wowmock.MOCK)
    ns = L.table()
    for f in ADDON:
        src = open(os.path.join(ROOT, "addon", "Glide", f), encoding="utf-8").read()
        L.eval("function(src, name, a, ns) local f, e = loadstring(src, name) if not f then error(e) end "
               "return f(a, ns) end")(src, "@" + f, "Glide", ns)
    G = L.globals()
    G.fire("ADDON_LOADED", "Glide")
    G.fire("PLAYER_LOGIN")
    return G, ns


def send(state, ns):
    ns.RefreshPages()
    core, page = protocol.parse_payload(ns.Core() + "|" + (ns.NextPage(1000) or ""))
    return state.update(core, page)


state = GameState()
G, ns = load_addon()
send(state, ns)
heard = []
for i in range(12):
    G.fire("QUEST_ACCEPTED", 2000 + i)
    heard += send(state, ns)
assert [a for _, k, a in heard if k == "acc"] == ["%d" % (2000 + i) for i in range(12)], heard
G, ns = load_addon()                                      # /reload
G.fire("QUEST_ACCEPTED", 3000)
heard, _ = quiet(send, state, ns)
G.fire("PLAYER_LEVEL_UP", 8)
G.SlashCmdList.GLIDE("mark after the reload")
more, _ = quiet(send, state, ns)
heard += more
assert [(k, a) for _, k, a in heard] == [("acc", "3000"), ("lvl", "8"), ("mark", "after the reload")], heard
assert state.addon_restarts == 1 and state.events_missed == 0, (state.addon_restarts, state.events_missed)
print("OK")
