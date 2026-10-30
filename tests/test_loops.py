#!/usr/bin/env python3
"""The engine's background work stays up: xp_per_hour comes from a running window (no scan of a deque the capture
thread is changing) with the numbers the old full-history scan gave, and the capture loop and the dashboard state
loop survive an error, logged once per distinct error."""
import contextlib
import io
import os
import random
import sys
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import app as glide_app  # noqa: E402
import protocol  # noqa: E402
from state import GameState  # noqa: E402

BASE = {"t": "100", "l": "7", "x": "10", "X": "100"}


# ---- xp_per_hour: the running window matches the old scan of the whole history -------------------------------
def old_xph(hist, window=900):
    """The scan summary() used to run (state.py before R8), over (t, xp_gained) samples kept for an hour."""
    if len(hist) < 2:
        return 0
    now = hist[-1][0]
    start = next(((t, g) for t, g in hist if now - t <= window), hist[0])
    dt = now - start[0]
    return 0 if dt < 60 else int((hist[-1][1] - start[1]) * 3600 / dt)


rnd = random.Random(7)
st, hist, t, lvl, xp = GameState(), [], 1000.0, 5, 0
for n in range(6000):
    t += rnd.choice((0.1, 0.1, 0.2, 0.5, 3.0, 40.0))
    xp += rnd.choice((0, 0, 0, 0, 35, 120))
    if xp >= 2000:
        lvl, xp = lvl + 1, xp - 2000
    st.update(dict(BASE, l=str(lvl), x=str(xp), X="2000"), {}, now=t)
    hist.append((t, st.xp_gained))
    while hist and t - hist[0][0] > 3600:
        hist.pop(0)
    assert st.xp_per_hour() == old_xph(hist), (n, st.xp_per_hour(), old_xph(hist))
assert st.xp_per_hour() > 0 and st.summary()["xp_per_hour"] == st.xp_per_hour()

# ---- background loops survive an error ----------------------------------------------------------------------
glide_app.LOOP_ERROR_PAUSE = 0
glide_app.time = types.SimpleNamespace(time=lambda: 0.0, sleep=lambda s: None)


class Stop(BaseException):
    """Ends a test loop (not an Exception, so the loops' guard lets it through)."""


calls = {"state": 0, "published": 0}


def dashboard_state():
    calls["state"] += 1
    if calls["state"] <= 3:
        raise KeyError("overlay_step")               # the same error three times: logged once
    return {"xp_per_hour": 0, "overlay_step": None}


def publish(kind, s):
    calls["published"] += 1
    if calls["published"] == 2:
        raise Stop()


fake = types.SimpleNamespace(dashboard_state=dashboard_state, xph=types.SimpleNamespace(add=lambda v: None),
                             hub=types.SimpleNamespace(publish=publish), overlay=None)
buf = io.StringIO()
try:
    with contextlib.redirect_stdout(buf):
        glide_app.App.publish_state(fake)
    raise AssertionError("publish_state ended")
except Stop:
    pass
assert calls == {"state": 5, "published": 2}, calls
assert buf.getvalue().count("[state] error (still running): KeyError") == 1, buf.getvalue()

frames, decoded = [], []


class Cap:
    def __init__(self, *a):
        pass

    def frame(self):
        frames.append(1)
        if len(frames) <= 2:
            raise OSError("grab failed")             # the window went away mid-grab, twice
        if len(frames) == 3:
            return protocol.Image(1, 1, b"\0\0\0", bgra=False)
        raise Stop()

    def resize(self, w, h):
        return False


sys.modules["capture"] = types.SimpleNamespace(WowCapture=Cap)
fake = types.SimpleNamespace(cfg={"capture": {"width": 420, "height": 60, "window_title": "x", "fps": 10},
                                  "grid": {"cols": 96, "rows": 8}},
                             attempts=0, decoded=0, on_frame=lambda *a: decoded.append(a))
buf = io.StringIO()
try:
    with contextlib.redirect_stdout(buf):
        glide_app.App.capture_loop(fake)
    raise AssertionError("capture_loop ended")
except Stop:
    pass
out = buf.getvalue()
assert len(frames) == 4 and fake.attempts == 1, (len(frames), fake.attempts)
assert out.count("[capture] error (still running): OSError: grab failed") == 1, out
assert "frame" in out, "the log says where the error came from"
print("OK")
