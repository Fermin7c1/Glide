#!/usr/bin/env python3
"""Every runner blanks all seven paid-service keys: ANTHROPIC_, GEMINI_, GOOGLE_, OPENROUTER_, OPENAI_, TAVILY_ and
BRAVE_API_KEY, so no development or test run reaches a paid model or a web search (regression: golden.py left
OPENAI_API_KEY set, and affected.py TAVILY_API_KEY and BRAVE_API_KEY).

A runner is whatever starts other processes with keys blanked: Python that builds a child's environment from
os.environ with a key set to "" (dict(os.environ, KEY="") or {**os.environ, "KEY": ""}: tests/golden/golden.py,
tests/affected.py, the run_all tests), a workflow or script env block (KEY: "", set KEY=, $env:KEY = ""), and, on
the development PC, the test lock wrapper (%USERPROFILE%\\Documents\\glide-test-lock.py, CONTRIBUTING.md), checked
when it is there. Whatever blanks one key must blank all seven. A test that clears keys for its own process
(os.environ.pop, os.environ[k] = "") starts nothing, so it isn't a runner.

Run: python3 tests/test_key_blanking.py   (prints OK)
"""
import ast
import os
import re

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SEVEN = {"ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
         "TAVILY_API_KEY", "BRAVE_API_KEY"}
KEY = re.compile(r"[A-Z0-9_]+_API_KEY$")
SKIP_DIRS = {".git", ".claude", "node_modules", "dist", "out", "release", "build", "__pycache__", "data", ".venv",
             "venv", ".ruff_cache", ".impeccable"}
TEXT = {   # extension -> the blanked keys in a file of that kind
    (".yml", ".yaml", ".js", ".cjs", ".mjs", ".ts"): re.compile(r"\b([A-Z0-9_]+_API_KEY)[\"']?\s*:\s*(?:\"\"|'')"),
    (".cmd", ".bat"): re.compile(r"(?im)^\s*set\s+\"?([A-Z0-9_]+_API_KEY)=\"?\s*$"),
    (".ps1",): re.compile(r"(?i)\$env:([A-Z0-9_]+_API_KEY)\s*=\s*(?:\"\"|''|\$null)|Remove-Item\s+env:([A-Z0-9_]+_API_KEY)"),
}
LOCK = os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"), "Documents", "glide-test-lock.py")


def _environ(n):
    return isinstance(n, ast.Attribute) and n.attr == "environ" and isinstance(n.value, ast.Name) and n.value.id == "os"


def _blank(n):
    return isinstance(n, ast.Constant) and n.value == ""


def python_sites(text):
    """-> [(line, {keys set to ""})] for each environment built from os.environ that blanks an API key."""
    out = []
    for n in ast.walk(ast.parse(text)):
        keys = set()
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "dict" and n.args \
                and _environ(n.args[0]):
            keys = {k.arg for k in n.keywords if k.arg and _blank(k.value)}
        elif isinstance(n, ast.Dict) and any(k is None and _environ(v) for k, v in zip(n.keys, n.values)):
            keys = {k.value for k, v in zip(n.keys, n.values)
                    if isinstance(k, ast.Constant) and isinstance(k.value, str) and _blank(v)}
        if any(KEY.match(k) for k in keys):
            out.append((n.lineno, keys))
    return out


def text_keys(ext, text):
    """-> the API keys a workflow, script or JavaScript file blanks (one set for the whole file)."""
    for exts, rx in TEXT.items():
        if ext in exts:
            return {k for m in rx.finditer(text) for k in m.groups() if k}
    return set()


def sites(path, rel):
    """-> [(where, {keys})] for one file"""
    ext = os.path.splitext(path)[1].lower()
    if ext != ".py" and not any(ext in exts for exts in TEXT):
        return []
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if "_API_KEY" not in text:
        return []
    if ext == ".py":
        return [("%s:%d" % (rel, line), keys) for line, keys in python_sites(text)]
    keys = text_keys(ext, text)
    return [(rel, keys)] if keys else []


# ---- the scanner itself: it finds each form, and not a test clearing keys for itself --------------------------------
assert python_sites('import os\nenv = dict(os.environ, ANTHROPIC_API_KEY="", X="1")\n') == [(2, {"ANTHROPIC_API_KEY"})]
assert python_sites('import os\ne = {**os.environ, "TAVILY_API_KEY": "", "N": "2"}\n') == [(2, {"TAVILY_API_KEY"})]
assert python_sites('import os\nos.environ["ANTHROPIC_API_KEY"] = ""\nos.environ.pop("GEMINI_API_KEY", None)\n') == []
assert python_sites('import os\nenv = dict(os.environ, LATEST="v0.4.4", OPENAI_API_KEY="sk-test")\n') == []
assert text_keys(".yml", 'env:\n  ANTHROPIC_API_KEY: ""\n  GEMINI_API_KEY: ""\n  LUACHECK_URL: x\n') == \
    {"ANTHROPIC_API_KEY", "GEMINI_API_KEY"}
assert text_keys(".cjs", 'spawn(py, [], { env: { ...process.env, OPENAI_API_KEY: "" } });') == {"OPENAI_API_KEY"}
assert text_keys(".ts", 'TAVILY_API_KEY: [], BRAVE_API_KEY: [], env: "ANTHROPIC_API_KEY",') == set()
assert text_keys(".cmd", "set ANTHROPIC_API_KEY=\r\nset GLIDE_SIM_SPEED=10\r\n") == {"ANTHROPIC_API_KEY"}
assert text_keys(".ps1", '$env:BRAVE_API_KEY = ""\nRemove-Item env:OPENROUTER_API_KEY\n') == \
    {"BRAVE_API_KEY", "OPENROUTER_API_KEY"}

# ---- every runner in the repository ---------------------------------------------------------------------------------
found = []
for dirpath, dirs, files in os.walk(ROOT):
    dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
    for f in sorted(files):
        path = os.path.join(dirpath, f)
        found += sites(path, os.path.relpath(path, ROOT).replace(os.sep, "/"))
where = {w.split(":")[0] for w, _ in found}
assert {"tests/golden/golden.py", "tests/affected.py", ".github/workflows/ci.yml"} <= where, sorted(where)

# ---- and the test lock wrapper the development PC runs everything through ------------------------------------------
if os.path.exists(LOCK):
    lock = sites(LOCK, LOCK)
    assert lock, "%s no longer blanks the model keys for what it runs" % LOCK
    found += lock

bad = ["%s blanks %s but not %s" % (w, ", ".join(sorted(keys & SEVEN)) or ", ".join(sorted(keys)),
                                     ", ".join(sorted(SEVEN - keys)))
       for w, keys in found if SEVEN - keys]
assert not bad, "every runner blanks all seven keys:\n  " + "\n  ".join(bad)
print("OK (%d runners)" % len(found))
