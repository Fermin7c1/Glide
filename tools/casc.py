#!/usr/bin/env python3
r"""What the game's CASC storage holds (the reader is wowfiles/casc.py).

    python tools/casc.py "<WoW folder>" [product]
"""
import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from wowfiles.casc import main  # noqa: E402

if __name__ == "__main__":
    main(sys.argv[1:])
