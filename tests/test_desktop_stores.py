#!/usr/bin/env python3
"""The desktop app's own settings and API key files (desktop_stores_check.cjs, the main process compiled (the tests'
shared compile, desktop_ts.py) and run under Node without Electron): an unreadable app-settings.json or secrets.json is never saved over
(a corrupt secrets.json used to lose every saved key at the next key change), a .bad copy is kept, and the reason
is there to show."""
import os
import subprocess
import sys

import desktop_ts

HERE = os.path.dirname(os.path.abspath(__file__))

node = desktop_ts.node
if not node or not desktop_ts.available():
    print("skipped: needs node and desktop/node_modules")
    sys.exit(0)
out = os.path.join(desktop_ts.main(), "main")    # rootDir is src/, so the main process compiles into <outDir>/main
r = subprocess.run([node, os.path.join(HERE, "desktop_stores_check.cjs"), out], capture_output=True, text=True, encoding="utf-8",
                   timeout=120)
print(r.stdout + r.stderr)
assert r.returncode == 0 and "ok" in r.stdout, "desktop_stores_check failed"
