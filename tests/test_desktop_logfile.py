#!/usr/bin/env python3
"""The desktop app keeps the Logs page on disk (desktop_logfile_check.cjs, the main process compiled into a temp
folder and run under Node without Electron): userData/logs/engine-YYYYMMDD.log gets every line appended with a header
per coach start, a new file each day, the 7 newest kept, and a failed write is counted instead of thrown. Before,
the coach's output (its tracebacks) lived only in memory and was gone with the app."""
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
tmp = tempfile.mkdtemp(prefix="glide-desktop-logfile-")
try:
    out = os.path.join(tmp, "main")         # rootDir is src/, so the main process compiles into <outDir>/main
    r = subprocess.run([node, TSC, "-p", os.path.join(DESK, "tsconfig.main.json"), "--outDir", tmp, "--sourceMap", "false"],
                       capture_output=True, text=True)
    assert r.returncode == 0, "tsc failed: %s" % (r.stdout + r.stderr)[-2000:]
    r = subprocess.run([node, os.path.join(HERE, "desktop_logfile_check.cjs"), out], capture_output=True, text=True,
                       timeout=120)
    print(r.stdout + r.stderr)
    assert r.returncode == 0 and "ok" in r.stdout, "desktop_logfile_check failed"
finally:
    shutil.rmtree(tmp, ignore_errors=True)
