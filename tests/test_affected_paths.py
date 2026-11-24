#!/usr/bin/env python3
"""tests/affected.py (the fast loop's map from changed files to tests) on path-like text: a string in the code that
looks like a path on another drive ('D:/Games', a fact id 'q:96608') is outside the repository, not a crash
(os.path.relpath raises ValueError across Windows drives, which stopped `affected.py --why` and `run_all.py --changed`).

Run: python3 tests/test_affected_paths.py   (prints OK)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import affected  # noqa: E402

norm = affected.Repo.norm           # (reads no repository state)
if os.name == "nt":                 # (elsewhere "Q:/Games" is a relative path, and relpath never raises)
    other = "Q:" if os.path.splitdrive(affected.ROOT)[0].upper() != "Q:" else "R:"
    for text in (other + "/Games/World of Warcraft", other + "/Games", other.lower() + "96608"):
        assert norm(None, text) is None, (text, norm(None, text))
assert norm(None, os.path.join(affected.ROOT, "companion", "app.py")) == "companion/app.py"
assert norm(None, "companion/app.py") == "companion/app.py" and norm(None, affected.ROOT) == ""
assert norm(None, os.path.join(affected.ROOT, "..", "elsewhere.py")) is None
print("OK")
