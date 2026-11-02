#!/usr/bin/env python3
"""Ctrl+C stops tests/run_all.py at once (regression: since the runner went parallel, every queued test kept starting
after Ctrl+C, for as long as the run had left, and it ended in a traceback). The runner (a copy of tests/run_all.py)
runs a scratch tree of six tests that each wait two minutes, two at a time, and gets Ctrl+C once two have started, as
a terminal sends it to the whole console: it exits within seconds with 130, says how far it got, and none of the four
queued tests starts.

POSIX only (a test can't send a console Ctrl+C to one process group on Windows): skips there with 77."""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if os.name != "posix":
    print("SKIP: sends Ctrl+C to a process group (POSIX only)")
    sys.exit(77)

env = dict(os.environ, ANTHROPIC_API_KEY="", GEMINI_API_KEY="", GOOGLE_API_KEY="", OPENROUTER_API_KEY="",
           OPENAI_API_KEY="")
# anything run_all.py imports from beside it still resolves from the real tests folder
env["PYTHONPATH"] = os.pathsep.join(filter(None, [os.path.join(ROOT, "tests"), env.get("PYTHONPATH")]))
tmp = tempfile.mkdtemp(prefix="glide-run-all-interrupt-")
p = None
try:
    tests = os.path.join(tmp, "tests")
    os.makedirs(tests)
    shutil.copy(os.path.join(ROOT, "tests", "run_all.py"), tests)
    marks = os.path.join(tmp, "started")
    os.makedirs(marks)
    long_test = ("import os, sys, time\nopen(os.path.join(%r, os.path.basename(sys.argv[0])), 'w').close()\n"
                 "time.sleep(120)\n" % marks)
    for i in range(6):                                    # two run at a time; four wait in the queue
        with open(os.path.join(tests, "test_long_%d.py" % i), "w", encoding="utf-8") as fh:
            fh.write(long_test)
    p = subprocess.Popen([sys.executable, os.path.join(tests, "run_all.py"), "-j", "2"], cwd=tmp,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         start_new_session=True, env=env)
    deadline = time.time() + 30
    while len(os.listdir(marks)) < 2 and p.poll() is None:
        assert time.time() < deadline, "the runner never started two tests"
        time.sleep(0.02)
    time.sleep(0.2)                                       # both running tests are well into their wait
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
    time.sleep(0.3)                                       # a queued test started after Ctrl+C would have marked by now
    started = sorted(os.listdir(marks))
    assert len(started) == 2, "queued tests started after Ctrl+C: %s" % started
finally:
    if p is not None:
        try:
            os.killpg(p.pid, signal.SIGKILL)                  # the group this test started: nothing of it outlives it
        except (ProcessLookupError, PermissionError):
            pass
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
