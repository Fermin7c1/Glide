#!/usr/bin/env python3
"""pyright (basic mode) over the engine (companion/): a file may not gain type errors. The coach and its model calls
(coach*.py, llm_client.py, providers.py, openai_compat.py and the tickers) and every other clean file must stay at
none; the files below still carry older errors (Optional members, untyped hooks, tools-only imports) and may not get
more. When one gets fewer, lower its number here.
Skipped (exit 77) when pyright is not installed: py -3 -m pip install pyright.

Run: python3 tests/test_types.py   (prints OK)
"""
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

KNOWN = {"app.py": 13, "capture.py": 3, "dashboard.py": 2, "gear.py": 1, "health.py": 1, "icons.py": 4,
         "kb_refresh.py": 3, "reminders.py": 4, "rules.py": 3, "state.py": 3, "talents.py": 1}

exe = shutil.which("pyright")
if not exe:
    print("SKIP: pyright not installed")
    raise SystemExit(77)
r = subprocess.run([exe, "--outputjson", "companion"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
try:
    out = json.loads(r.stdout)
except ValueError:
    print("SKIP: pyright gave no report (%s)" % (r.stdout + r.stderr).strip()[-300:])
    raise SystemExit(77) from None
errors = {}
for d in out.get("generalDiagnostics") or ():
    if d.get("severity") == "error":
        name = os.path.relpath(d["file"], os.path.join(ROOT, "companion")).replace("\\", "/")
        errors.setdefault(name, []).append("%s:%d: %s" % (name, d["range"]["start"]["line"] + 1,
                                                          d["message"].splitlines()[0]))
worse = {f: e for f, e in errors.items() if len(e) > KNOWN.get(f, 0)}
for f, e in sorted(worse.items()):
    print("%s: %d type errors (allowed %d)\n  %s" % (f, len(e), KNOWN.get(f, 0), "\n  ".join(e)))
better = [f for f, n in KNOWN.items() if len(errors.get(f, ())) < n]
if better:
    print("fewer type errors now (lower KNOWN): " + ", ".join("%s %d" % (f, len(errors.get(f, ()))) for f in better))
assert not worse, "new type errors in " + ", ".join(sorted(worse))
print("OK")
sys.exit(0)
