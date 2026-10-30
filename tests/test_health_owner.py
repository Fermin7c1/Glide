#!/usr/bin/env python3
"""Status page: each check has one owner. Run by the desktop app, the app checks the install (WoW folder, the addon
installed or left over, RestedXP installed); the engine used to check the same things under other ids, so the page
showed "Addon update available" next to "Glide addon needs attention", and "RestedXP not installed" next to
"RestedXP not found", with a fix ("copy addon\\Glide from the project...") meant for developers. Now, under the app,
the engine reports only the addon the game is running, in words for players; run on its own it keeps its checks.

Run: python3 tests/test_health_owner.py   (prints OK)
"""
import os
import shutil
import sys
import tempfile
import time
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
sys.path.append(ROOT)
import health  # noqa: E402
import versions  # noqa: E402

root = tempfile.mkdtemp(prefix="glide-health-owner-")          # no config.json: no WoW folder set
EXP = "0.15.3"


def fake_app(under_app, addon_status):
    return types.SimpleNamespace(
        args=types.SimpleNamespace(sim=False, app=under_app),
        state=types.SimpleNamespace(frames=50, summary=lambda: {"guide": "05-11 Dun Morogh"}),
        last_frame_t=time.time(), decoded=50, attempts=50,
        rules=types.SimpleNamespace(addon_status=addon_status),
        _kb_status={"ok": True, "built": "today", "guides": 3}, k=types.SimpleNamespace(ok=True), refresher=None,
        coach=types.SimpleNamespace(enabled=True, suspended=None, describe_models=lambda: "models"),
        cfg={"ptt": {}}, live_db={}, log=None)


def checks(under_app, addon_status):
    h = health.Health(fake_app(under_app, addon_status), root=root)
    h._slow, h._slow_t = {"kb": {"stale": False}, "client": None, "maps": ("1.15", ["spells"], 3),
                          "rxp": {"kind": "missing"}}, time.time()
    if under_app:
        h._slow.pop("rxp")                  # as _slow_checks leaves it under the app (checked below)
    return {c["id"]: c for c in h.checks()}


try:
    for st in (versions.status(EXP, 50, EXP, "0.14.0", app=True),):         # another version installed
        got = checks(True, st)
        assert "addon" not in got and "wow_dir" not in got and "rxp" not in got, sorted(got)

    # the game still runs the old addon: only the engine can see that; player wording, nothing about the project
    got = checks(True, versions.status("0.15.1", 50, EXP, EXP, app=True))
    a = got["addon"]
    assert a["status"] == "error" and a["title"] == "Restart WoW to load Glide 0.15.3", a
    assert a["detail"].startswith("The game is running Glide 0.15.1") and "restart WoW" in a["detail"], a
    assert "project" not in a["detail"] and a["action"] is None, a
    assert checks(True, versions.status(EXP, 50, EXP, EXP, app=True))["addon"]["status"] == "ok"

    # on its own (a developer's checkout) the engine keeps every check, with the developer's fix
    got = checks(False, versions.status(EXP, 50, EXP, "0.14.0"))
    assert got["addon"]["title"] == "Glide addon needs attention" and "project" in got["addon"]["detail"], got["addon"]
    assert got["addon"]["action"] == "addon_install"
    assert got["wow_dir"]["status"] == "error" and got["rxp"]["title"] == "RestedXP not found", sorted(got)

    # under the app the slow checks don't look RestedXP up at all
    h = health.Health(fake_app(True, None), root=root)
    assert "rxp" not in h._slow_checks()
    h = health.Health(fake_app(False, None), root=root)
    assert "rxp" in h._slow_checks()

    # the app's checks keep their own ids, so nothing on the page says the same thing twice
    status_ts = open(os.path.join(ROOT, "desktop", "src", "renderer", "status.ts"), encoding="utf-8").read()
    for cid in ("addon_installed", "wow_dir", "rxp_app"):
        assert 'id: "%s"' % cid in status_ts, cid
finally:
    shutil.rmtree(root, ignore_errors=True)
print("OK")
