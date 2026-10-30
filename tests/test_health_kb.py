#!/usr/bin/env python3
"""Status page, knowledge base: it said "out of date" all the time. A build made against a staged glide.db was
compared with the old data/glide.db ("glide.db changed"), and every new session log or archived SavedVariables file
counted as a change. Now: the databases in use are compared, play data is noted (the next start merges it), and
only real input changes warn."""
import json
import os
import sqlite3
import sys
import tempfile
import time
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
sys.path.insert(1, ROOT)
import importlib  # noqa: E402
import health  # noqa: E402

build_kb = importlib.import_module("knowledge.build_kb")    # ROOT/knowledge (the temp root below has none of its own)

root = tempfile.mkdtemp(prefix="glide-health-")
old = time.time() - 3600


def write(rel, text, t=None):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)
    if t:
        os.utime(p, (t, t))
    return p


write("knowledge/heuristics/rules.json", "[]", old)
write("knowledge/glossary.json", "{}", old)
write("data/glide.db", "old quests", old)
staged = write("data/glide.next-20261018-182448.db", "new quests", old + 600)
write("data/live/20261018-180400.jsonl", "{}", old + 100)
kb = os.path.join(root, "data", "kb.next-20261018-182448.db")
c = sqlite3.connect(kb)
c.execute("create table meta (key text primary key, value text)")
c.execute("insert into meta values ('built', '2026-10-18 18:25:28')")
c.execute("insert into meta values ('inputs', ?)",
          (json.dumps({"root": root, "items": build_kb.collect_inputs(root, glide=staged)}),))
c.commit()
c.close()

app = types.SimpleNamespace(live_db={"glide": staged, "kb": kb}, log=None, coach=None)


def check():
    h = health.Health(app, root=root)
    return h._slow_checks()["kb"]


k = check()
assert k["stale"] is False, "the build is current against the databases in use: %s" % k

# you keep playing: a new session log and an archived SavedVariables file
write("data/live/20261018-190000.jsonl", "{}")
write("data/archive/sv/RXPGuides_1_20261018-190500.lua", "x")
k = check()
assert k["stale"] is False and set(k["play"]) >= {"live logs", "recorder SV"}, k

# a real change still warns, and the reason names only it
time.sleep(0.05)
write("knowledge/heuristics/rules.json", '[{"id": 1}]')
k = check()
assert k["stale"] and "rules.json" in k["reason"] and "live logs" not in k["reason"], k
print("status kb staleness: ok")
