#!/usr/bin/env python3
"""The state stream is slim (R26). The engine sent its whole state (~14 KB) every second to three listeners: the
dashboard frame, the app window's session bar and the overlay. Now the community notes (half of it) go on their own
"community" event only when they change (init and /api/state still have them in the state), the overlay and the
session bar read the light stream (/events?lite=1: only the keys they show), and the state's "tuning" is recomputed
only when what it reads changes. Prints the state bytes per second before and after."""
import http.client
import json
import os
import re
import sys
import time
import types
import urllib.request

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import app as glide_app  # noqa: E402
import dashboard  # noqa: E402
import tuning  # noqa: E402

read = lambda *p: open(os.path.join(ROOT, *p), encoding="utf-8").read()  # noqa: E731
init = json.loads(read("tests", "golden", "fixtures", "dashboard_init.json"))
full = init["state"]
assert full.get("community") and full.get("tuning") is not None

# ---- the hub: a light client gets the lite keys, no community event; init and /api/state are whole -----------------
hub = dashboard.Hub()
qf, init_full = hub.connect()
ql, init_lite = hub.connect(lite=True)
state = dict(full)
community = state.pop("community")
hub.publish("community", community)
hub.publish("state", state)
hub.publish("message", {"kind": "info", "text": "hi", "t": 1})
hub.publish("speak", {"text": "hi", "interrupt": False})


def events(q):
    out = []
    while not q.empty():
        kind, data = re.match(r"event: (\w+)\ndata: (.*)\n\n$", q.get_nowait(), re.S).groups()
        out.append((kind, json.loads(data)))
    return out


got_full, got_lite = events(qf), events(ql)
assert [k for k, _ in got_full] == ["community", "state", "message", "speak"], got_full
assert got_full[0][1] == community and "community" not in got_full[1][1] and got_full[1][1]["level"] == full["level"]
assert [k for k, _ in got_lite] == ["state", "message", "speak"], [k for k, _ in got_lite]
lite = got_lite[0][1]
assert set(lite) == set(dashboard.LITE_KEYS) and lite == {k: full[k] for k in dashboard.LITE_KEYS}, sorted(lite)
assert hub.state() == full, "the hub's whole state has the community notes back in"
_, init_after = hub.connect()
assert json.loads(init_after.split("data: ", 1)[1])["state"] == full, "a new client's init is the whole state"
_, init_lite2 = hub.connect(lite=True)
assert json.loads(init_lite2.split("data: ", 1)[1])["state"] == lite
assert json.loads(init_lite.split("data: ", 1)[1])["state"] == {}, "before any state, the light init has none"

# over HTTP: /events?lite=1 and /api/state
srv = dashboard.serve(hub, 0)
port = srv.server_address[1]
c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
c.request("GET", "/events?lite=1")
r = c.getresponse()
head = b""
while not head.endswith(b"\n\n"):
    head += r.read(1)
assert json.loads(head.decode().split("data: ", 1)[1])["state"] == lite
c.close()
api = json.load(urllib.request.urlopen("http://127.0.0.1:%d/api/state" % port))
assert api["state"] == full, "/api/state is whole (contract 2 and 3)"
srv.shutdown()

# ---- bytes per second, before and after (the fixture's state, steady: no community change) ---------------------
before = 3 * len(dashboard.sse("state", full))
after = len(dashboard.sse("state", state)) + 2 * len(dashboard.sse("state", lite))
print("state bytes per second: before %d (3 x %d), after %d (dashboard %d + 2 x %d light), community %d bytes "
      "only when it changes" % (before, len(dashboard.sse("state", full)), after, len(dashboard.sse("state", state)),
                                len(dashboard.sse("state", lite)), len(dashboard.sse("community", community))))
assert after < before / 2.5, (before, after)

# ---- publish_state: the community event only when the notes change ---------------------------------------------
published = []
notes = [{"notes": [], "total": 0, "last_scan": None}] * 3 + [{"notes": [], "total": 1, "last_scan": "x"}]


class Stop(BaseException):              # past publish_state's own error handling
    pass


def dashboard_state():
    if not notes:
        raise Stop()
    return {"xp_per_hour": 0, "overlay_step": None, "level": 5, "community": dict(notes.pop(0))}


fake = types.SimpleNamespace(dashboard_state=dashboard_state, xph=types.SimpleNamespace(add=lambda v: None),
                             hub=types.SimpleNamespace(publish=lambda kind, s: published.append((kind, s))),
                             overlay=None)
glide_app.time = types.SimpleNamespace(time=time.time, sleep=lambda s: None)      # no 1 s waits in the test
try:
    glide_app.App.publish_state(fake)
except Stop:
    pass
finally:
    glide_app.time = time
assert [k for k, _ in published] == ["community", "state", "state", "state", "community", "state"], published
assert all("community" not in s for k, s in published if k == "state")

# ---- tuning: kept in the state, recomputed only when what it reads changes (or once a minute) -------------------
calls = []
_snapshot = tuning.snapshot
tuning.snapshot = lambda coach, rm=15: calls.append(1) or _snapshot(coach, rm)
coach = types.SimpleNamespace(cfg={"fast_model": "claude-sonnet-5-5", "deep_model": "claude-opus-5-5"},
                              q_times=[], convo=[])
cache = tuning.SnapshotCache(ttl=60)
first = cache.get(coach, 15)
assert cache.get(coach, 15) is first and len(calls) == 1, "unchanged inputs: the same snapshot"
coach.cfg["lookahead_steps"] = 20
assert cache.get(coach, 15)["settings"]["lookahead_steps"]["value"] == 20 and len(calls) == 2
coach.q_times.append((time.time(), "voice"))
cache.get(coach, 15)
assert len(calls) == 3, "a question asked: the rates change"
cache._t -= 61
cache.get(coach, 15)
assert len(calls) == 4, "a minute on: the clock-dependent parts are recomputed"
tuning.snapshot = _snapshot
src = read("companion", "app.py")
assert "tuning.SnapshotCache()" in src and "tuning.snapshot(self.coach" not in src
assert "Coach context" not in read("companion", "tuning.py") + src

# ---- the readers --------------------------------------------------------------------------------------------
ts = read("desktop", "src", "shared", "engine.ts")
pick = re.search(r"export type LiteState = Pick<EngineState, (.*?)>;", ts, re.S).group(1)
assert re.findall(r'"(\w+)"', pick) == list(dashboard.LITE_KEYS), pick
assert re.search(r"^  community\?: Community \| null;$", ts, re.M), "community may be missing from a state event"
for f in ("overlay.ts", "coach.ts"):
    assert '/events?lite=1"' in read("desktop", "src", "renderer", f), f
# every key the light stream's readers use is in it
overlay = read("desktop", "src", "renderer", "overlay.ts")
on_state = overlay[overlay.index("const onState"):overlay.index("es.addEventListener")]
bar = read("desktop", "src", "renderer", "session-bar.ts")
for f, text, var in (("overlay.ts", on_state, "s"), ("session-bar.ts", bar, "d")):
    used = set(re.findall(r"\b%s\.(\w+)" % var, text))
    assert used and used <= set(dashboard.LITE_KEYS), (f, used - set(dashboard.LITE_KEYS))
# no app reader of community (only the coach screen, which listens for its event and reads it from init); the
# community feed's settings (companion.community.feed...) are settings, not a reader
for dirpath, _, files in os.walk(os.path.join(ROOT, "desktop", "src", "renderer")):
    for f in files:
        if f.endswith(".ts"):
            assert not re.search(r"\b\w+\.community\b(?![.\w])|['\"]community['\"]", read(dirpath, f)), f
js = read("companion", "static", "dashboard.js")
assert "addEventListener('community'" in js and "if (d.community !== undefined) renderCommunity" in js
assert '"overlay"' not in dashboard.Hub.publish.__doc__
print("OK")
