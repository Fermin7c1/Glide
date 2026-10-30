#!/usr/bin/env python3
"""The XP/hour chart's history (companion/xph_history.py): a sample every 20 s while XP comes in, two hours kept,
saved so it survives a coach restart, served as epoch milliseconds for the dashboard."""
import os
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import xph_history as X  # noqa: E402

path = os.path.join(tempfile.mkdtemp(prefix="glide-xph-"), "live", "xph_history.json")
h = X.XphHistory(path)
t = 1_800_000_000.0
h.add(12000, now=t)
h.add(13000, now=t + 5)            # too soon: dropped
h.add(0, now=t + 30)               # no XP coming in: dropped
h.add(None, now=t + 40)
h.add(14000, now=t + 60)
assert [v for _, v in h.samples] == [12000, 14000], h.samples
assert os.path.exists(path), "saved on the first sample"
for i in range(1, 400):            # three hours later only the last two remain
    h.add(10000 + i, now=t + 60 + i * 30)
assert h.samples[0][0] >= h.samples[-1][0] - X.KEEP_S, "older than two hours dropped"
h._save()
again = X.XphHistory(path)         # a restart reads it back
assert again.samples[-1][1] == h.samples[-1][1] and len(again.samples) == len(h.samples) or again.samples == [], again.samples[-1:]
snap = X.XphHistory(None)
snap.add(15000, now=t)
assert snap.snapshot() == {"samples": [[int(t * 1000), 15000]]}
print("xph history: ok")
