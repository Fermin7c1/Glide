#!/usr/bin/env python3
"""Ctrl+C stops tests/run_all.py at once (regression: since the runner went parallel, every queued test kept starting
after Ctrl+C, for as long as the run had left, and it ended in a traceback). A run with a long test in it (navmesh,
about a minute) gets Ctrl+C a few seconds in, as a terminal sends it to the whole console: the runner exits within
seconds with 130 and says how far it got.

POSIX only (a test can't send a console Ctrl+C to one process group on Windows): skips there with 77."""
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if os.name != "posix":
    print("SKIP: sends Ctrl+C to a process group (POSIX only)")
    sys.exit(77)

env = dict(os.environ, ANTHROPIC_API_KEY="", GEMINI_API_KEY="", GOOGLE_API_KEY="", OPENROUTER_API_KEY="",
           OPENAI_API_KEY="")
p = subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "run_all.py"), "-j", "2", "navmesh", "sv_field",
                      "lua_strings", "memory_gate", "xph_history"], cwd=ROOT, stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT, text=True, start_new_session=True, env=env)
time.sleep(3)
t0 = time.time()
os.killpg(p.pid, signal.SIGINT)
try:
    out, _ = p.communicate(timeout=30)
except subprocess.TimeoutExpired:
    os.killpg(p.pid, signal.SIGKILL)
    raise AssertionError("run_all.py still running 30 s after Ctrl+C") from None
took = time.time() - t0
assert p.returncode == 130, (p.returncode, out[-800:])
assert "interrupted:" in out and "Traceback" not in out, out[-800:]
assert took < 15, "took %.1f s to stop" % took
print("OK")
