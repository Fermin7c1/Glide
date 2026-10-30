#!/usr/bin/env python3
"""Settings > Game capture > Check now (GET /calibrate -> App.calibrate_now): the captured corner as a PNG, where
the addon's strip is, what it decodes to, and a plain reason when it can't (no window, no strip, sim mode)."""
import base64
import os
import sys
import time
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import app as glide_app  # noqa: E402
import capture  # noqa: E402
import protocol  # noqa: E402

GRID = {"cols": 96, "rows": 8}
CAP = {"width": 420, "height": 60, "window_title": "World of Warcraft"}


def fake_app(img, sim=False):
    state = types.SimpleNamespace(summary=lambda: {"name": "Testwarrior", "level": 7, "zone": "Zephras Isle",
                                                   "class": "WARRIOR"})
    return types.SimpleNamespace(args=types.SimpleNamespace(sim=sim), cfg={"capture": CAP, "grid": GRID},
                                 state=state, last_img=img, last_img_t=time.time(), last_loc=None,
                                 attempts=12, decoded=10)


def calibrate(a):
    return glide_app.App.calibrate_now(a)


# a frame with the strip, as the addon draws it (cell 3 px, offset like the real corner)
payload = "l=7;cl=WARRIOR|pg=B;nm=Testwarrior"
syms = protocol.encode_to_symbols(42, payload, GRID["cols"] * GRID["rows"] * 3 // 4)
img = protocol.render(syms, GRID["cols"], GRID["rows"], 3, size=(CAP["width"], CAP["height"]))
r = calibrate(fake_app(img))
assert r["ok"], r
assert r["strip"] == {"x": 5, "y": 7, "cell": 3, "kind": "6-bit"}, r["strip"]
assert r["fields"]["name"] == "Testwarrior" and r["fields"]["level"] == 7, r["fields"]
png = base64.b64decode(r["png"])
assert png[:8] == b"\x89PNG\r\n\x1a\n" and (r["w"], r["h"]) == (CAP["width"], CAP["height"]), (r["w"], r["h"])

# WoW is there, but no strip in the corner (addon off, or exclusive fullscreen showing black)
blank = capture.Image(CAP["width"], CAP["height"], bytes([20, 20, 30] * CAP["width"] * CAP["height"]), bgra=False)
r = calibrate(fake_app(blank))
assert not r["ok"] and r["problem"] == "strip" and r.get("png"), r.get("problem")
assert "addon" in r["text"], r["text"]

# no WoW window: a stale frame isn't reused, and a fresh grab finds nothing
a = fake_app(None)
a.last_img_t = 0
orig = capture.WowCapture.frame
capture.WowCapture.frame = lambda self: None
try:
    r = calibrate(a)
finally:
    capture.WowCapture.frame = orig
assert not r["ok"] and r["problem"] == "window" and "png" not in r, r

# simulation mode reads nothing from the game
r = calibrate(fake_app(img, sim=True))
assert not r["ok"] and r["problem"] == "sim", r
print("calibrate: ok")
