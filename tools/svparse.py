#!/usr/bin/env python3
"""Parse a WoW SavedVariables file into Python values: knowledge/sources/_lua.py's SavedVariables reader, the one
Lua table reader of the project, under the name the tools used.

    python tools/svparse.py <SavedVariables file>      # prints the start of it as JSON
"""
import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from knowledge.sources._lua import read_saved_variables as load  # noqa: E402,F401
from knowledge.sources._lua import saved_variables as parse_saved_variables  # noqa: E402,F401

if __name__ == "__main__":
    import json
    print(json.dumps(load(sys.argv[1]), indent=1, default=str)[:4000])
