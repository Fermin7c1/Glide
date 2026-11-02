#!/usr/bin/env python3
"""The desktop app keeps the Logs page on disk (desktop_logfile_check.cjs, the main process compiled (the tests'
shared compile, desktop_ts.py) and run under Node without Electron): userData/logs/engine-YYYYMMDD.log gets every line appended with a header
per coach start, a new file each day, the 7 newest kept, and a failed write is counted instead of thrown. Before,
the coach's output (its tracebacks) lived only in memory and was gone with the app."""
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
r = subprocess.run([node, os.path.join(HERE, "desktop_logfile_check.cjs"), out], capture_output=True, text=True, encoding="utf-8",
                   timeout=120)
print(r.stdout + r.stderr)
assert r.returncode == 0 and "ok" in r.stdout, "desktop_logfile_check failed"
