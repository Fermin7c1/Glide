#!/usr/bin/env python3
"""A broken knowledge build must not install itself. When the guide parser or the analyzer raised (or could not
be imported), build_kb only marked it "skipped", installed a kb.db without guides or briefs and then called it up
to date until an input changed; every KB query also swallowed its SQL error silently. Now: those loaders fail the
build (kb.failed.db, the old kb.db kept), a KB without rxp_guide or step_brief rows is never installed, staleness
asks for one rebuild per set of inputs when the build recorded a loader error, and a failed query is logged once.
Everything runs in temp folders; the real data/ is never built."""
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
from knowledge import build_kb, store  # noqa: E402

REAL_STEPS, REAL_IMPORT = build_kb.steps, build_kb.importlib.import_module


def boom(conn, ctx):
    raise RuntimeError("analyzer exploded")


def fill_guides(conn, ctx):
    conn.execute("INSERT INTO rxp_guide(key, name) VALUES('G||1-5 Test', '1-5 Test')")
    return {"rxp_guide": 1}


def fill_briefs(conn, ctx):
    from knowledge.analyzer import route as R
    R.ensure_schema(conn)
    conn.execute("INSERT INTO step_brief(guide_key, idx, class) VALUES('G||1-5 Test', 1, 'WARRIOR')")
    return {"briefs": 1}


def run(folder, steps, force=False):
    """build() into folder/kb.db with these loaders; returns (summary, kb.db bytes or None, kb.failed.db exists)."""
    build_kb.steps = lambda: steps
    try:
        out = os.path.join(folder, "kb.db")
        with contextlib.redirect_stdout(io.StringIO()):
            summary = build_kb.build(out, quiet=True, force=force)
    finally:
        build_kb.steps = REAL_STEPS
    data = open(out, "rb").read() if os.path.exists(out) else None
    return summary, data, os.path.exists(os.path.join(folder, "kb.failed.db"))


def fresh(old=b"old kb"):
    folder = tempfile.mkdtemp(prefix="glide-guard-")
    with open(os.path.join(folder, "kb.db"), "wb") as fh:
        fh.write(old)
    return folder


# ---------------------------------------------------------------- the analyzer raises: not installed
f = fresh()
s, data, failed = run(f, [("rxp_parser", fill_guides), ("analyzer", boom)])
assert s["errors"] == ["analyzer"] and not s["installed"], s
assert data == b"old kb" and failed, "a build whose analyzer failed replaced kb.db"

# ---------------------------------------------------------------- the analyzer cannot even be imported
def no_analyzer(name, *a, **k):
    if name == "knowledge.analyzer.route":
        raise ImportError("No module named 'knowledge.analyzer.route'")
    return REAL_IMPORT(name, *a, **k)


build_kb.importlib.import_module = no_analyzer
try:
    f = fresh()
    s, data, failed = run(f, [("rxp_parser", fill_guides), ("analyzer", build_kb._analyzer)])
finally:
    build_kb.importlib.import_module = REAL_IMPORT
assert "analyzer" in s["errors"] and "ImportError" in s["loaders"]["analyzer"]["error"], s["loaders"]["analyzer"]
assert data == b"old kb" and failed and not s["installed"]

# ---------------------------------------------------------------- nothing raised, but no guides / no briefs
f = fresh()
s, data, failed = run(f, [("rxp_parser", lambda c, x: {"skipped": "glide.db not found"}), ("analyzer", fill_briefs)])
assert s["empty"] == ["rxp_guide"] and not s["installed"] and data == b"old kb" and failed, s
f = fresh()
s, data, failed = run(f, [("rxp_parser", fill_guides), ("analyzer", lambda c, x: {})])
assert s["empty"] == ["step_brief"] and not s["installed"] and data == b"old kb" and failed, s

# ---------------------------------------------------------------- a good build installs; --force still overrides
f = fresh()
s, data, failed = run(f, [("rxp_parser", fill_guides), ("analyzer", fill_briefs)])
assert s["installed"] and not s["errors"] and not s["empty"] and data != b"old kb" and not failed, s
f = fresh()
s, data, failed = run(f, [("rxp_parser", fill_guides), ("analyzer", boom)], force=True)
assert s["installed"] and data != b"old kb", s
print("build guard: ok")

# ---------------------------------------------------------------- staleness: a recorded loader error, once
root = tempfile.mkdtemp(prefix="glide-guard-root-")
p = os.path.join(root, "knowledge", "glossary.json")
os.makedirs(os.path.dirname(p))
open(p, "w").write('{"a": 1}')
old = time.time() - 3600
os.utime(p, (old, old))
os.makedirs(os.path.join(root, "data"))
kb = os.path.join(root, "data", "kb.db")


def record(path, loaders):
    if os.path.exists(path):
        os.remove(path)
    c = sqlite3.connect(path)
    c.execute("create table meta (key text primary key, value text)")
    c.execute("insert into meta values ('built', '2026-10-18 17:01:41')")
    c.execute("insert into meta values ('inputs', ?)", (json.dumps({"root": root, "items": build_kb.collect_inputs(root)}),))
    for name, res in loaders.items():
        c.execute("insert into meta values (?, ?)", ("loader:" + name, json.dumps(res)))
    c.commit()
    c.close()


record(kb, {"rxp_parser": {"guides": 3}, "analyzer": {"skipped": "no data"}})
st = build_kb.staleness(kb, root=root)
assert not st["stale"], "skipped is not an error: %s" % st["reason"]
record(kb, {"rxp_parser": {"guides": 3}, "analyzer": {"error": "KeyError: 40"}})
st = build_kb.staleness(kb, root=root)
assert st["stale"] and "loader errors (analyzer)" in st["reason"], st["reason"]
assert any("loader errors" in line for line in build_kb.describe(st))
# the rebuild from these inputs failed too (kb.failed.db records them): no rebuild on every start
record(os.path.join(root, "data", "kb.failed.db"), {"analyzer": {"error": "KeyError: 40"}})
st = build_kb.staleness(kb, root=root)
assert not st["stale"], "asked again for a rebuild that already failed from the same inputs: %s" % st["reason"]
# new inputs: a rebuild is due again
open(p, "w").write('{"a": 2}')
st = build_kb.staleness(kb, root=root)
assert st["stale"] and [c["key"] for c in st["changes"]] == ["glossary.json"], st["changes"]
print("staleness with loader errors: ok")

# ---------------------------------------------------------------- a failed KB query is logged, once
q = os.path.join(tempfile.mkdtemp(prefix="glide-guard-q-"), "kb.db")
c = sqlite3.connect(q)
c.execute("create table meta (key text primary key, value text)")
c.commit()
c.close()
k = store.open_ro(q)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    assert k.q("SELECT nope FROM meta") == [] and k.q("SELECT nope FROM meta WHERE 1") == []
    assert k.one("SELECT value FROM meta") is None
lines = [x for x in buf.getvalue().splitlines() if x]
assert len(lines) == 1 and lines[0].startswith("[kb] query failed") and "no such column: nope" in lines[0], lines
print("kb query errors: ok")
print("kb build guard: ok")
