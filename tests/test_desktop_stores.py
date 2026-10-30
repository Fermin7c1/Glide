#!/usr/bin/env python3
"""The desktop app's own settings and API key files (desktop_stores_check.cjs, the main process compiled into a temp
folder and run under Node without Electron): an unreadable app-settings.json or secrets.json is never saved over
(a corrupt secrets.json used to lose every saved key at the next key change), a .bad copy is kept, and the reason
is there to show."""
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
    print("skipped: needs node and desktop/node_modules")
    sys.exit(0)
tmp = tempfile.mkdtemp(prefix="glide-desktop-stores-")
try:
    out = os.path.join(tmp, "main")         # rootDir is src/, so the main process compiles into <outDir>/main
    r = subprocess.run([node, TSC, "-p", os.path.join(DESK, "tsconfig.main.json"), "--outDir", tmp, "--sourceMap", "false"],
                       capture_output=True, text=True)
    assert r.returncode == 0, "tsc failed: %s" % (r.stdout + r.stderr)[-2000:]
    r = subprocess.run([node, os.path.join(HERE, "desktop_stores_check.cjs"), out], capture_output=True, text=True,
                       timeout=120)
    print(r.stdout + r.stderr)
    assert r.returncode == 0 and "ok" in r.stdout, "desktop_stores_check failed"
finally:
    shutil.rmtree(tmp, ignore_errors=True)
