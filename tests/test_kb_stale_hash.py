#!/usr/bin/env python3
"""An app update copies the engine's code and data files again: new mtimes, same bytes. kb.db must stay current
then (it used to report "7 inputs changed" and rebuild after every update); a real content change still counts."""
import json
import os
import sqlite3
import sys
import tempfile
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
from knowledge import build_kb  # noqa: E402

root = tempfile.mkdtemp(prefix="glide-stale-")
files = {"knowledge/glossary.json": '{"a": 1}', "knowledge/schema.sql": "create table x (a);",
         "knowledge/heuristics/rules.json": "[]", "knowledge/build_x.py": "print(1)\n",
         "knowledge/community/sources.json": "{}"}
for rel, text in files.items():
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)
old = time.time() - 3600
for rel in files:
    os.utime(os.path.join(root, *rel.split("/")), (old, old))
os.makedirs(os.path.join(root, "data"))
kb = os.path.join(root, "data", "kb.db")


def build_record():
    """A kb.db whose build recorded the inputs as they are now (what build_kb stores in meta)."""
    if os.path.exists(kb):
        os.remove(kb)
    c = sqlite3.connect(kb)
    c.execute("create table meta (key text primary key, value text)")
    c.execute("insert into meta values ('built', '2026-10-18 17:01:41')")
    c.execute("insert into meta values ('inputs', ?)", (json.dumps({"root": root, "items": build_kb.collect_inputs(root)}),))
    c.commit()
    c.close()


build_record()
st = build_kb.staleness(kb, root=root)
assert not st["stale"], st["reason"]

# the update: every shipped file written again, same content, mtime now (newer than kb.db too)
now = time.time() + 5
for rel, text in files.items():
    p = os.path.join(root, *rel.split("/"))
    open(p, "w").write(text)
    os.utime(p, (now, now))
st = build_kb.staleness(kb, root=root)
assert not st["stale"], "re-copied but unchanged inputs made kb.db stale: %s" % st["reason"]

# a real change to one of them
p = os.path.join(root, "knowledge", "glossary.json")
open(p, "w").write('{"a": 2}')
st = build_kb.staleness(kb, root=root)
assert st["stale"] and [c["key"] for c in st["changes"]] == ["glossary.json"], st["changes"]

# a kb built before fingerprints were recorded: mtime rules as before
items = [dict(i, hash=None) for i in build_kb.collect_inputs(root)]
c = sqlite3.connect(kb)
c.execute("update meta set value=? where key='inputs'", (json.dumps({"root": root, "items": items}),))
c.commit()
c.close()
os.utime(p, (now + 10, now + 10))
assert build_kb.staleness(kb, root=root)["stale"]
print("kb staleness by content: ok")
