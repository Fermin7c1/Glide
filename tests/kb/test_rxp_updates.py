#!/usr/bin/env python3
"""RestedXP update ingestion: the newest guide text wins (not the longest), free Forever guides come from the
installed addon (seed snapshot only as fallback), glide.db is rebuilt only when an input changed, and kb.db is
stale when RestedXP's version or files changed.

Run: python3 tests/kb/test_rxp_updates.py   (prints OK)
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_db  # noqa: E402
from knowledge import build_kb, store  # noqa: E402

# ---- newest wins ------------------------------------------------------------------------------------------
long_old = {"text": "x" * 500, "_t": 100}
short_new = {"text": "y" * 300, "_t": 200}
assert build_db.newer(short_new, long_old) and not build_db.newer(long_old, short_new)
assert build_db.newer({"text": "z" * 600, "_t": 200}, short_new), "same time: the longer text"
assert build_db.newer(long_old, None)

# ---- RestedXP fingerprint ---------------------------------------------------------------------------------
tmp = tempfile.mkdtemp(prefix="glide_rxp_")
rxp_dir = os.path.join(tmp, "wow", "Interface", "AddOns", "RXPGuides")
os.makedirs(os.path.join(rxp_dir, "DB", "forever"))
os.makedirs(os.path.join(rxp_dir, "Guides", "forever"))
os.makedirs(os.path.join(rxp_dir, "Guides", "Classic"))


def write(path, text, t=None):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    if t:
        os.utime(path, (t, t))


write(os.path.join(rxp_dir, "RXPGuides.toc"), "## Interface: 11507\n## Version: v4.11.15\n")
write(os.path.join(rxp_dir, "DB", "forever", "spells.lua"), "local s = {}\n")
GUIDE = ('RXPGuides.RegisterGuide([[\n#version 1\n#group RestedXP Forever Guide (H)\n#name 99-99 Test Isle\n'
         'step\n.accept 92461 >> Accept Harmony in Balance\n%s]])\n')
gpath = os.path.join(rxp_dir, "Guides", "forever", "Horde-Test.lua")
write(gpath, GUIDE % ("step\n.turnin 92461 >> Turn in Harmony in Balance\n" * 20), time.time() - 50)
write(os.path.join(rxp_dir, "Guides", "Classic", "Classic-Other.lua"), "-- not a Forever guide\n")
files = [os.path.relpath(p, rxp_dir).replace(os.sep, "/") for p in store.rxp_files(rxp_dir)]
assert files == ["DB/forever/spells.lua", "Guides/forever/Horde-Test.lua", "RXPGuides.toc"], files
fp1 = store.rxp_fingerprint(rxp_dir)
assert fp1["version"] == "v4.11.15" and fp1["files"] == 3, fp1
assert store.rxp_fingerprint(os.path.join(tmp, "nope")) is None

# ---- build_db: free guides from the installed addon, skip when unchanged, newest text wins -----------------
out = os.path.join(tmp, "glide.db")


def run_build(*extra):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_db.py"), "--wow",
                        os.path.join(tmp, "wow"), "--out", out, *extra], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout


def guide_row():
    c = sqlite3.connect(out)
    try:
        row = c.execute("SELECT text, source FROM rxp_guides WHERE guide_key = ?",
                        ("RestedXP Forever Guide (H)||99-99 Test Isle",)).fetchone()
        meta = dict(c.execute("SELECT key, value FROM meta").fetchall())
    finally:
        c.close()
    return row, meta


log = run_build()
assert "free guide files (starter zones, dungeons) from addon v4.11.15: 1" in log and "premium (your account" in log, log
row, meta = guide_row()
assert row and row[1] == "rxp-free:forever/Horde-Test.lua" and row[0].count(".turnin") == 20, row
assert meta["rxp_version"] == "v4.11.15" and meta["rxp_free_source"] == "addon v4.11.15", meta
assert "up to date" in run_build(), "unchanged inputs: no rebuild"
assert "wrote" in run_build("--force")
# RestedXP update: the guide got SHORTER. The new text must replace the old, longer one.
write(gpath, GUIDE % ("step\n.turnin 92461 >> Turn in Harmony in Balance\n" * 5), time.time())
log = run_build()
assert "wrote" in log, log
row, _ = guide_row()
assert row[0].count(".turnin") == 5, "the newest guide text wins, not the longest"

# ---- build_kb staleness: RestedXP version / files changed ---------------------------------------------------
root = os.path.join(tmp, "root")
ref = os.path.join(root, "reference", "rxp")
shutil.copytree(rxp_dir, ref)
os.makedirs(os.path.join(root, "data"))
kb = os.path.join(root, "data", "kb.db")


def fake_kb(fp_meta):
    if os.path.exists(kb):
        os.remove(kb)
    c = sqlite3.connect(kb)
    c.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    rows = {"built": "2026-10-08 12:00:00", "inputs": json.dumps({"root": root, "items": build_kb.collect_inputs(root)}),
            "build_args": json.dumps({"only": None, "skip": None, "force": False}),
            "rxp_source": json.dumps({"kind": "reference", "path": ref})}
    if fp_meta is not None:
        rows["rxp_fingerprint"] = json.dumps(fp_meta)
    c.executemany("INSERT INTO meta VALUES (?, ?)", list(rows.items()))
    c.commit()
    c.close()


fake_kb(store.rxp_fingerprint(ref))
d = build_kb.staleness(kb, root=root)
assert not d["stale"], d["reason"]
fake_kb(None)
d = build_kb.staleness(kb, root=root)
assert d["stale"] and "before RestedXP updates were tracked" in d["reason"], d["reason"]
fake_kb(store.rxp_fingerprint(ref))
write(os.path.join(ref, "DB", "forever", "spells.lua"), "local s = {} -- changed\n")
d = build_kb.staleness(kb, root=root)
assert d["stale"] and d["reason"] == "RestedXP's data or guide files changed (v4.11.15)", d["reason"]
fake_kb(store.rxp_fingerprint(ref))
write(os.path.join(ref, "RXPGuides.toc"), "## Interface: 11507\n## Version: v4.11.16\n")
d = build_kb.staleness(kb, root=root)
assert d["stale"] and d["reason"] == "RestedXP was updated (v4.11.15 -> v4.11.16)", d["reason"]
assert build_kb.describe(d) == ["kb.db needs a rebuild: RestedXP was updated (v4.11.15 -> v4.11.16)"] or \
    build_kb.describe(d)[0].endswith("RestedXP was updated (v4.11.15 -> v4.11.16)"), build_kb.describe(d)

shutil.rmtree(tmp, ignore_errors=True)
print("OK")
