#!/usr/bin/env python3
"""Fast start: the companion starts on the databases it has; glide.db / kb.db are rebuilt in the background
(data/*.next-<time>.db, one file per build), switched to in place when ready and the newest promoted over
data/*.db at the next start.

Run: python3 tests/test_kb_refresh.py   (prints OK)
"""
import os
import sys
import tempfile
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
sys.path.append(ROOT)
import kb_refresh  # noqa: E402
from glidedb import GlideDB  # noqa: E402

tmp = tempfile.mkdtemp(prefix="glide_refresh_")
data = os.path.join(tmp, "data")
os.makedirs(data)


def write(name, text, t=None):
    p = os.path.join(data, name)
    with open(p, "w") as fh:
        fh.write(text)
    if t:
        os.utime(p, (t, t))
    return p


# ---- promote: *.next.db replace *.db; an obsolete kb.new.db is dropped -------------------------------------
now = time.time()
write("glide.db", "old glide", now - 100)
write("glide.next.db", "new glide", now - 50)
write("kb.db", "old kb", now - 100)
write("kb.next.db", "new kb", now - 40)
write("kb.new.db", "older pending", now - 200)
logs = []
done = kb_refresh.promote(data, log=logs.append)
assert done == ["glide.next.db", "kb.next.db"], done
assert open(os.path.join(data, "glide.db")).read() == "new glide" and open(os.path.join(data, "kb.db")).read() == "new kb"
assert not os.path.exists(os.path.join(data, "kb.new.db")) and not os.path.exists(os.path.join(data, "kb.next.db"))
assert kb_refresh.promote(data, log=logs.append) == []

# ---- refresher: build_db to glide.next.db (compared with glide.db), then build_kb against kb.db -> kb.next.db ----
calls = []


def runner_factory(glide_changes, kb_changes):
    def run(args, timeout=1800):
        calls.append(args)
        if args[0].endswith("extract_maps.py"):
            return 0, "[maps] client 1.60: up to date"
        if args[0].endswith("build_db.py"):
            if glide_changes:
                write(os.path.basename(args[args.index("--out") + 1]), "rebuilt glide")
            return 0, "ok"
        if kb_changes:
            write(os.path.basename(args[args.index("--out") + 1]), "rebuilt kb")
        return 0, ""
    return run


ready = []
r = kb_refresh.Refresher(tmp, on_ready=lambda g, k: ready.append((g, k)), log=logs.append, delay_s=0,
                         ignore=["/x/session.jsonl"], runner=runner_factory(True, True))
st = r.run()
maps_args, db_args, kb_args = calls
assert maps_args[0].endswith("extract_maps.py"), "the client's map data is checked first (re-extracted after a patch)"
gout, kout = db_args[db_args.index("--out") + 1], kb_args[kb_args.index("--out") + 1]
assert os.path.basename(gout).startswith("glide.next-") and db_args[db_args.index("--compare") + 1] \
    .endswith(os.path.join("data", "glide.db")), db_args
assert "--if-stale" in kb_args and kb_args[kb_args.index("--against") + 1].endswith(os.path.join("data", "kb.db"))
assert os.path.basename(kout).startswith("kb.next-") and kb_args[kb_args.index("--glide") + 1] == gout \
    and kb_args[kb_args.index("--ignore") + 1] == "/x/session.jsonl", kb_args
assert st["glide"] == "rebuilt" and st["kb"] == "rebuilt" and st["state"] == "done", st
assert ready == [(gout, kout)], ready
# nothing changed: no switch-over
kb_refresh.promote(data, log=logs.append)
calls.clear()
ready.clear()
st = kb_refresh.Refresher(tmp, on_ready=lambda g, k: ready.append((g, k)), log=logs.append, delay_s=0,
                          runner=runner_factory(False, False)).run()
assert st["glide"] == "up to date" and st["kb"] == "up to date" and ready == [], (st, ready)
assert "--glide" not in calls[2], "no staged glide.db: build_kb reads data/glide.db"

# ---- GlideDB.reopen: the companion's glide.db reader switches files ----------------------------------------
import sqlite3  # noqa: E402
a, b = os.path.join(tmp, "a.db"), os.path.join(tmp, "b.db")
for p, name in ((a, "Old Map"), (b, "New Map")):
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE maps (ui_map INTEGER, name TEXT)")
    c.execute("INSERT INTO maps VALUES (1, ?)", (name,))
    c.commit()
    c.close()
k = GlideDB(a)
assert k.map_name(1) == "Old Map"
k.reopen(b)
assert k.map_name(1) == "New Map"

# ---- build_kb: staleness judged against kb.db with a staged glide.db and the live log left out -------------
# (its own process: build_kb reads the root it is given)
import json  # noqa: E402
import subprocess  # noqa: E402
root = os.path.join(tmp, "root")
os.makedirs(os.path.join(root, "data", "live"))
open(os.path.join(root, "data", "glide.db"), "w").write("g")
staged = os.path.join(root, "data", "glide.next.db")
open(staged, "w").write("g2")
os.utime(staged, (now + 5, now + 5))
live = os.path.join(root, "data", "live", "20261011-200000.jsonl")
open(live, "w").write("{}")
code = ("import sys, json; sys.path.insert(0, %r); from knowledge import build_kb as bk; "
        "print(json.dumps([bk.collect_inputs(%r, glide=%r, ignore=[%r]), bk.collect_inputs(%r)]))"
        % (ROOT, root, staged, live, root))
out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp)
assert out.returncode == 0, out.stderr[-1500:]
with_stage, plain = json.loads(out.stdout)
items = {i["key"]: i for i in with_stage}
assert items["glide.db"]["path"] == "data/glide.db" and abs(items["glide.db"]["mtime"] - (now + 5)) < 0.01, \
    "the staged glide.db stands in under the path it will be promoted to"
assert items["live logs"]["path"] is None and items["live logs"]["n"] == 0, "the running session's log is left out"
items = {i["key"]: i for i in plain}
assert items["live logs"]["n"] == 1 and abs(items["glide.db"]["mtime"] - (now + 5)) > 0.01
print("OK")
