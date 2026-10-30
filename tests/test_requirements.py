#!/usr/bin/env python3
"""The dependency pins say the same thing everywhere, and the engine refuses an old Python with a clear message.

requirements.txt pins what the desktop app bundles (desktop/scripts/fetch-python.ps1: numpy) and installs on first
use (desktop/src/main/speech.ts: faster-whisper); setup-voice.cmd installs the same faster-whisper; requirements-dev.txt
pins every package; the guard at the top of companion/app.py stops Python < 3.12 before the engine's imports.

Run: python3 tests/test_requirements.py   (prints OK)
"""
import ast
import os
import re
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def pins(rel):
    """{package: version} from a requirements file; every requirement line must be pinned with ==."""
    out = {}
    for line in read(rel).splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-r"):
            continue
        m = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.]+)", line)
        assert m, "%s: not pinned with ==: %r" % (rel, line)
        out[m.group(1).lower()] = m.group(2)
    return out


run = pins("requirements.txt")
dev = pins("requirements-dev.txt")
bundled = re.search(r'\[string\[\]\]\$Packages = @\(([^)]*)\)', read("desktop/scripts/fetch-python.ps1")).group(1)
assert '"numpy==%s"' % run["numpy"] in bundled, (run["numpy"], bundled)
assert dev["numpy"] == run["numpy"], (dev["numpy"], run["numpy"])
speech = re.search(r'const FASTER_WHISPER = "([^"]+)"', read("desktop/src/main/speech.ts")).group(1)
assert run["faster-whisper"] == speech, (run["faster-whisper"], speech)
assert "faster-whisper==%s" % speech in read("setup-voice.cmd"), "setup-voice.cmd installs another faster-whisper"
assert {"lupa", "ruff", "pyright"} <= set(dev), dev
assert "-r ../requirements-dev.txt" in read("tests/requirements.txt")

# the version guard: the first module-level `if sys.version_info < ...` in app.py, run against a fake sys
tree = ast.parse(read("companion/app.py"))
guard = next(n for n in tree.body if isinstance(n, ast.If) and "version_info" in ast.unparse(n.test))
code = compile(ast.Module(body=[guard], type_ignores=[]), "app.py", "exec")


def exit_message(version):
    fake = types.SimpleNamespace(version_info=version, version="%d.%d.%d (fake)" % version)

    def leave(msg):
        raise SystemExit(msg)
    fake.exit = leave
    try:
        exec(code, {"sys": fake})
    except SystemExit as e:
        return str(e.code)
    return None


msg = exit_message((3, 11, 9))
assert msg and "3.12" in msg and "3.11.9" in msg and "winget" in msg, msg
assert exit_message((3, 12, 0)) is None and exit_message((3, 13, 1)) is None
assert "sys.version_info < (3, 12)" in read("start-glide.cmd"), "start-glide.cmd doesn't check the Python version"
print("OK")
