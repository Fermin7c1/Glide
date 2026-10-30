#!/usr/bin/env python3
"""Regression: a second background rebuild in one session must not write over the database the coach switched to.

The first refresh's kb build became the coach's knowledge base (open, so Windows can't replace it); a re-read then
built to the same data/kb.next.db, failed with "is in use", and judged staleness against data/kb.db (the old one).
Each build now gets its own file, compares against the databases in use, and the next start installs the newest."""
import os
import sqlite3
import sys
import tempfile
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import kb_refresh  # noqa: E402


def make_db(path, tag):
    c = sqlite3.connect(path)
    c.execute("create table t (v text)")
    c.execute("insert into t values (?)", (tag,))
    c.commit()
    c.close()


def arg(args, flag):
    return args[args.index(flag) + 1] if flag in args else None


tmp = tempfile.mkdtemp(prefix="glide-staging-")
data = os.path.join(tmp, "data")
os.makedirs(data)
make_db(os.path.join(data, "glide.db"), "glide v0")
make_db(os.path.join(data, "kb.db"), "kb v0")

calls = []


def runner(args, timeout=1800):
    """Stand-in for the build tools: each build writes its --out (as a real rebuild would)."""
    calls.append(args)
    tool = os.path.basename(args[0])
    if tool in ("build_db.py", "build_kb.py"):
        make_db(arg(args, "--out"), "%s %d" % (tool, len(calls)))
    return 0, ""


got = []
live = {}
r1 = kb_refresh.Refresher(tmp, on_ready=lambda g, k: got.append((g, k)), log=lambda m: None, delay_s=0,
                          runner=runner, live=live)
st1 = r1.run()
assert st1["kb"] == "rebuilt" and st1["glide"] == "rebuilt", st1
g1, k1 = got[-1]
assert arg(calls[-1], "--against") == os.path.join(data, "kb.db"), calls[-1]

# the coach switches to the first build and keeps it open, as coach.swap_kb / GlideDB.reopen do
held = [sqlite3.connect(k1), sqlite3.connect(g1)]
for c in held:
    c.execute("select * from t").fetchall()
live.update(glide=g1, kb=k1)

time.sleep(1.1)                                  # build names carry the time to the second
calls.clear()
r2 = kb_refresh.Refresher(tmp, on_ready=lambda g, k: got.append((g, k)), log=lambda m: None, delay_s=0,
                          runner=runner, live=live, force=True)
st2 = r2.run()
assert st2["kb"] == "rebuilt", "second rebuild failed: %s" % st2
g2, k2 = got[-1]
assert k2 != k1 and g2 != g1, "the second build reused the in-use file names"
kb_args = [a for a in calls if os.path.basename(a[0]) == "build_kb.py"][0]
db_args = [a for a in calls if os.path.basename(a[0]) == "build_db.py"][0]
assert arg(kb_args, "--against") == k1, "staleness must be judged against the kb in use: %s" % kb_args
assert arg(db_args, "--compare") == g1, "glide.db must be compared with the one in use: %s" % db_args
assert os.path.exists(k1), "the in-use build was removed"

# next start: the coach is gone, the newest build is installed, the rest cleaned up
for c in held:
    c.close()
done = kb_refresh.promote(data, log=lambda m: None)
left = sorted(os.listdir(data))
assert left == ["glide.db", "kb.db"], left
c = sqlite3.connect(os.path.join(data, "kb.db"))
v = c.execute("select v from t").fetchone()[0]
c.close()
assert v.startswith("build_kb.py"), "kb.db isn't the newest build: %s" % v
assert os.path.basename(k2) in done, done

# leftovers from older versions (kb.next.db, kb.next.new.db, kb.new.db) are still picked up
make_db(os.path.join(data, "kb.next.new.db"), "legacy")
time.sleep(0.05)
os.utime(os.path.join(data, "kb.next.new.db"))
kb_refresh.promote(data, log=lambda m: None)
assert sorted(os.listdir(data)) == ["glide.db", "kb.db"], os.listdir(data)
print("refresh staging: ok")
