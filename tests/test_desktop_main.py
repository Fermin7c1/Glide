#!/usr/bin/env python3
"""The desktop app's main process, compiled into a temp folder and run under Node without Electron
(desktop_main_check.cjs): a Python that can't be launched leaves the coach "crashed" with the reason and lets the app
quit (it used to hang in "starting" and stop() never returned); a changed engine home gets the engine code when the
coach starts; code folders Glide didn't install are never deleted; an unreadable config.json is never saved over."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DESK = os.path.join(HERE, "..", "desktop")
TSC = os.path.join(DESK, "node_modules", "typescript", "bin", "tsc")

node = shutil.which("node")
if not node or not os.path.exists(TSC):
    print("SKIP: needs node and desktop/node_modules")
    sys.exit(77)
tmp = tempfile.mkdtemp(prefix="glide-desktop-main-")
try:
    out = os.path.join(tmp, "desktop", "dist")          # tsconfig.main.json emits main/ and shared/ under it
    r = subprocess.run([node, TSC, "-p", os.path.join(DESK, "tsconfig.main.json"), "--outDir", out, "--sourceMap", "false"],
                       capture_output=True, text=True)
    assert r.returncode == 0, "tsc failed: %s" % (r.stdout + r.stderr)[-2000:]
    r = subprocess.run([node, os.path.join(HERE, "desktop_main_check.cjs"), tmp], capture_output=True, text=True,
                       timeout=120)
    print(r.stdout + r.stderr)
    assert r.returncode == 0 and "ok" in r.stdout, "desktop_main_check failed"
finally:
    shutil.rmtree(tmp, ignore_errors=True)
