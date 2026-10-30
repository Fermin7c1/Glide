#!/usr/bin/env python3
"""The linters stay clean: ruff over the Python (pyproject.toml) and luacheck over the addon (.luacheckrc).
Skipped (exit 77) when a linter is not installed: py -3 -m pip install ruff; luarocks install luacheck.

Run: python3 tests/test_lint.py   (prints OK)
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def ruff():
    exe = shutil.which("ruff")
    if exe:
        return [exe]
    try:
        import ruff as _ruff  # noqa: F401 - the pip package, run as a module
    except ImportError:
        return None
    return [sys.executable, "-m", "ruff"]


tools = {"ruff": ruff(), "luacheck": [shutil.which("luacheck")] if shutil.which("luacheck") else None}
missing = [name for name, cmd in tools.items() if not cmd]
if missing:
    print("skipped: %s not installed" % " and ".join(missing))
    raise SystemExit(77)

failed = []
for name, args in (("ruff", ["check", "--no-cache", "--output-format", "concise", "."]),
                   ("luacheck", ["--codes", "--formatter", "plain", "addon/Glide"])):
    r = subprocess.run(tools[name] + args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode:
        failed.append(name)
        print("%s found problems:\n%s" % (name, (r.stdout + r.stderr).strip()[-4000:]))
assert not failed, "lint: " + ", ".join(failed)
print("OK")
