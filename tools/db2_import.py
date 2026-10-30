#!/usr/bin/env python3
"""Import client DB2 tables exported as CSV into the knowledge base (the importer, and the tables it reads, are in
wowfiles/db2_import.py).

    python tools/db2_import.py [--db2 data/db2]               # report what the folder contains
    python tools/db2_import.py --kb data/kb.db [--db2 ...]    # import into an existing KB file
"""
import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from wowfiles.db2_import import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
