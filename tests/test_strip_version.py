#!/usr/bin/env python3
"""An addon that speaks another strip version says so (R13). Its frames used to read like corrupt ones: the capture
log asked "Is the addon on?", the Status page said "No game data yet" and the addon check never fired. Now a clean
frame in another version decodes to protocol.OtherVersion, the capture loop counts it and logs the mismatch, and
the Status page names it, in player words under the app. The engine also names the versions in play (the state's
and /health's "versions", with the app's version from GLIDE_APP_VERSION)."""
import contextlib
import io
import os
import sys
import time
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
sys.path.append(ROOT)
import app as glide_app  # noqa: E402
import health  # noqa: E402
import protocol  # noqa: E402
import versions  # noqa: E402
from state import GameState  # noqa: E402

COLS, ROWS, CELL = 96, 8, 3
CAP = (COLS * ROWS - 2) * 3 // 4 - 6


def frame(payload, version=protocol.PROTO, seq=5):
    """The binary strip the addon draws, for a frame that starts with `version`."""
    keep = protocol.PROTO
    protocol.PROTO = version                  # encode_to_symbols writes the module's version byte
    try:
        syms = protocol.encode_to_symbols(seq, payload, CAP + 6)
    finally:
        protocol.PROTO = keep
    return protocol.render(syms, COLS, ROWS, CELL, binary=True)


# ---- decode: a clean frame of another version is told apart from a corrupt one -----------------------------------
res, loc = protocol.decode_image(frame("gv=0.15.2;l=7"), COLS, ROWS)
assert res == (5, "gv=0.15.2;l=7") and loc, res
res, loc = protocol.decode_image(frame("gv=0.20.0;l=7", version=2), COLS, ROWS)
assert isinstance(res, protocol.OtherVersion) and res.version == 2 and not res and loc, res
raw = bytearray([2, 5, 0, 3]) + b"l=7"
raw += bytes(divmod(protocol.fletcher16(raw), 256))
assert protocol.decode_bytes(bytes(raw)).version == 2
raw[5] ^= 1                                                         # a flipped bit: corrupt, whatever its version
assert protocol.decode_bytes(bytes(raw)) is None
assert protocol.decode_bytes(bytes(12)) is None, "an all-black grid is no frame (version 0, checksum 0)"
assert protocol.decode_image(frame("", version=0), COLS, ROWS)[0] is None

# ---- the capture loop counts those frames and logs the mismatch, not "is the addon on?" ---------------------------
other = frame("gv=0.20.0;l=7", version=2)


class Stop(BaseException):              # past the capture loop's own error handling
    pass


grabs = []


class Cap:
    def __init__(self, *a):
        self.w, self.h = other.w, other.h

    def frame(self):
        grabs.append(1)
        if len(grabs) > 30:
            raise Stop()
        return other

    def resize(self, w, h):
        return False


sys.modules["capture"] = types.SimpleNamespace(WowCapture=Cap)
fake = types.SimpleNamespace(cfg={"capture": {"width": other.w, "height": other.h, "window_title": "x", "fps": 1000},
                                  "grid": {"cols": COLS, "rows": ROWS}},
                             attempts=0, decoded=0, on_frame=lambda *a: None, strip_other=None, versions=versions,
                             args=types.SimpleNamespace(app=True))
fake.note_other_version = lambda v: glide_app.App.note_other_version(fake, v)
buf = io.StringIO()
try:
    with contextlib.redirect_stdout(buf):
        glide_app.App.capture_loop(fake)
except Stop:
    pass
out = buf.getvalue()
assert fake.attempts == 30 and fake.decoded == 0, (fake.attempts, fake.decoded)
assert fake.strip_other["version"] == 2 and fake.strip_other["frames"] == 30, fake.strip_other
assert "Is the addon on" not in out, out
assert out.count("[capture] the game's Glide addon sends strip version 2 and this Glide app reads version 1: "
                 "update the Glide app") == 1, out

# ---- the Status page names it ---------------------------------------------------------------------------------
root = os.path.join(ROOT, "tests", "_no_such_root")


def link(under_app, strip_other, frames=0, last=0.0):
    a = types.SimpleNamespace(
        args=types.SimpleNamespace(sim=False, app=under_app),
        state=types.SimpleNamespace(frames=frames, summary=lambda: {"guide": "x"}), last_frame_t=last,
        decoded=frames, attempts=frames + 10, strip_other=strip_other,
        rules=types.SimpleNamespace(addon_status=None), _kb_status={"ok": True}, k=types.SimpleNamespace(ok=True),
        refresher=None, coach=types.SimpleNamespace(enabled=False, suspended=None), cfg={"ptt": {}}, live_db={},
        log=None)
    h = health.Health(a, root=root)
    h._slow, h._slow_t = {"kb": {"stale": False}, "client": None, "maps": ("1.15", ["spells"], 3)}, time.time()
    return {c["id"]: c for c in h.checks()}["link"]


now = time.time()
c = link(True, {"version": 2, "frames": 40, "t": now})
assert c["status"] == "error" and c["title"] == "The Glide addon doesn't match this app", c
assert c["detail"] == ("The game's Glide addon sends strip version 2 and this Glide app reads version 1. Update the "
                       "Glide app, or install its addon on the Addon page and fully restart WoW."), c
assert c["action"] is None and "project" not in c["detail"], c
c = link(False, {"version": 2, "frames": 40, "t": now})
assert c["title"] == "Glide addon speaks strip version 2, the engine expects 1" and "project" in c["detail"], c
# good frames since: the mismatch is over (an addon updated and reloaded), the link reads as usual
c = link(True, {"version": 2, "frames": 40, "t": now - 60}, frames=500, last=now)
assert c["status"] == "ok" and c["title"] == "Reading the game", c
assert link(True, None)["title"] == "No game data yet"
# an addon older than the engine: install the update (the app's Addon page; standalone, a button)
m = versions.strip_mismatch(1, 3, app=True)
assert not m["newer"] and m["fix"].startswith("install the update on the Glide app's Addon page"), m
assert versions.strip_mismatch(1, 3)["action"] == "addon_install"

# ---- the versions in play ------------------------------------------------------------------------------------
st = GameState()
st.core["gv"] = "0.15.2"
info_app = types.SimpleNamespace(app_version="0.30.1", addon_expected="0.15.2", _installed=(0, "0.15.2", []),
                                 state=st, strip_other=None, last_frame_t=0.0)
st.frames = 0
v = glide_app.App.version_info(info_app)
assert v == {"app": "0.30.1", "addon_expected": "0.15.2", "addon_installed": "0.15.2", "addon_running": "0.15.2",
             "strip": 1, "strip_seen": None, "kb_schema": 1, "nav": 1}, v
info_app.strip_other = {"version": 2, "frames": 3, "t": time.time()}
assert glide_app.App.version_info(info_app)["strip_seen"] == 2
# the shape is declared for both UIs (contract 3)
ts = open(os.path.join(ROOT, "desktop", "src", "shared", "engine.ts"), encoding="utf-8").read()
import re  # noqa: E402
body = re.search(r"^export interface Versions \{(.*?)\}", ts, re.S | re.M).group(1)
assert set(re.findall(r"(\w+)\??:", body)) == set(v), body
# the app passes its version, and GLIDE_READY still starts with what the app waits for
eng = open(os.path.join(ROOT, "desktop", "src", "main", "engine.ts"), encoding="utf-8").read()
assert "GLIDE_APP_VERSION: app.getVersion()" in eng
src = open(os.path.join(ROOT, "companion", "app.py"), encoding="utf-8").read()
assert 'console_log("GLIDE_READY port=%d app=%s"' in src and 'os.environ.get("GLIDE_APP_VERSION")' in src
print("OK")
