#!/usr/bin/env python3
"""The refactor's golden masters (tests/golden/golden.py): every probe's output still matches its snapshot.
Skipped when there are no snapshots (they are recorded per machine from its own data).

Run: python3 tests/test_golden.py   (prints OK)
"""
import os
import sys

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
if not os.path.isdir(os.path.join(HERE, "snapshots")) or not os.listdir(os.path.join(HERE, "snapshots")):
    print("SKIP: no golden snapshots (python tests/golden/golden.py record)")
    raise SystemExit(77)
DATA = os.path.join(HERE, "..", "..", "data")
missing = [n for n in ("glide.db", "kb.db") if not os.path.exists(os.path.join(DATA, n))]
if missing:                                         # the snapshots were recorded from these
    print("SKIP: the snapshots need this machine's data/%s" % " and data/".join(missing))
    raise SystemExit(77)
sys.path.insert(0, HERE)
import golden  # noqa: E402

names = [f[:-5] for f in sorted(os.listdir(golden.SNAP)) if f.endswith(".json")]
assert golden.main(["check"] + names) == 0, "behaviour changed (see above)"
print("OK")
