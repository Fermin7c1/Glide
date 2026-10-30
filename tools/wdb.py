#!/usr/bin/env python3
"""Decode a folder of the client's WDB caches (the decoder is wowfiles/wdb.py).

    python tools/wdb.py <folder> [-o out.json]
"""
import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from wowfiles.wdb import main  # noqa: E402

if __name__ == "__main__":
    main(sys.argv[1:])
