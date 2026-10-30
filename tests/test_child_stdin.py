#!/usr/bin/env python3
"""Regression: under the desktop app the companion's main thread blocks reading its stdin pipe (that's how it notices
the app closing). On Windows a child process that inherits that pipe as its stdin hangs at startup, so every
background rebuild froze at its first step. kb_refresh must give its children their own stdin.

The test runs a parent with a pipe for stdin whose main thread is blocked reading it, while a thread runs
Refresher._run on a trivial script; the child must finish."""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PARENT = r"""
import sys, threading
sys.path.insert(0, %r)
import kb_refresh
out = {}
def job():
    import time
    time.sleep(1.0)               # let the main thread's read be pending first, as it is under the app
    r = kb_refresh.Refresher(%r, delay_s=0)
    out["res"] = r._run(["-c", "print('child ran')"], timeout=20)
    print("RESULT", out["res"][0], out["res"][1].strip(), flush=True)
t = threading.Thread(target=job, daemon=True)
t.start()
sys.stdin.readline()              # blocked on the pipe, like app.py --app, until the test closes stdin
t.join(5)
print("DONE" if not t.is_alive() else "HUNG", flush=True)
""" % (os.path.join(ROOT, "companion"), tempfile.gettempdir())

import threading
import time
p = subprocess.Popen([sys.executable, "-c", PARENT], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT)
lines = []
reader = threading.Thread(target=lambda: lines.extend(p.stdout), daemon=True)
reader.start()
deadline = time.time() + 30          # keep stdin open (the read stays pending) until the child reports or time is up
while time.time() < deadline and not any(b"RESULT" in x for x in lines):
    time.sleep(0.5)
p.stdin.close()
try:
    p.wait(timeout=15)
except subprocess.TimeoutExpired:
    p.kill()
reader.join(5)
out = b"".join(lines)
text = out.decode("utf-8", "replace")
assert "RESULT 0 child ran" in text, "a background job's child hung behind the companion's stdin pipe:\n" + text
print("child stdin: ok")
