#!/usr/bin/env python3
"""Community intelligence: the loader (later lines replace earlier ones, supersedes, bad lines), relevance
filtering (level, zone, class), the community tool, the knowledge block part and n: fact ids (on the fixture KB),
and that the committed registry and intel are well formed.

Run: python3 tests/kb/test_community.py   (prints OK)
"""
import json
import os
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)

from knowledge import api, store  # noqa: E402
from knowledge.sources import community  # noqa: E402

# ---- registry and today's intel are well formed --------------------------------------------------------
reg = json.load(open(os.path.join(ROOT, "knowledge", "community", "sources.json"), encoding="utf-8"))
ids = [s["id"] for s in reg["sources"]]
assert len(ids) == len(set(ids)), "duplicate source ids"
for s in reg["sources"]:
    assert s.get("kind") and s.get("url") and s.get("access") and s.get("status"), s
    assert s["kind"] != "git" or s["url"].endswith(".git"), s
notes, bad = community.read_notes(os.path.join(ROOT, "knowledge", "community"))
assert notes and bad == 0, bad
for n in notes.values():
    assert n.get("kind") in community.KINDS and n.get("sources") and n.get("claim"), n["id"]
    assert all(isinstance(s, dict) and s.get("url") for s in n["sources"]), n["id"]

# ---- loader ------------------------------------------------------------------------------------------
tmp = tempfile.mkdtemp()
base = os.path.join(tmp, "knowledge", "community", "intel")
os.makedirs(base)
with open(os.path.join(base, "2026-10-01.jsonl"), "w", encoding="utf-8") as fh:
    fh.write(json.dumps({"id": "a", "kind": "tip", "claim": "old claim", "seen": "2026-10-01",
                         "levels": [20, 25], "zones": ["Wetlands"], "sources": [{"url": "https://x", "medium": "forum"}]}) + "\n")
    fh.write("not json\n")
    fh.write(json.dumps({"id": "b", "kind": "route", "claim": "warrior trick", "classes": ["WARRIOR"], "seen": "2026-10-01",
                         "sources": [{"url": "https://y"}]}) + "\n")
with open(os.path.join(base, "2026-10-02.jsonl"), "w", encoding="utf-8") as fh:
    fh.write(json.dumps({"id": "a", "kind": "tip", "claim": "corrected claim", "seen": "2026-10-02", "status": "corroborated",
                         "levels": [20, 25], "zones": ["Wetlands"], "sources": [{"url": "https://x"}]}) + "\n")
    fh.write(json.dumps({"id": "c", "kind": "patch", "claim": "new patch", "official": True, "seen": "2026-10-02",
                         "supersedes": ["b"], "sources": [{"url": "https://z"}]}) + "\n")
db = os.path.join(tmp, "kb.db")
conn = store.open_rw(db)
store.create_schema(conn)
ctx = store.BuildContext(root=tmp)
res = community.load(conn, ctx)
conn.commit()
assert res["notes"] == 3 and res["bad_lines"] == 1 and res["superseded"] == 1, res
rows = {r[0]: r for r in conn.execute("SELECT id, claim, status, official FROM community_note")}
assert rows["a"][1] == "corrected claim" and rows["a"][2] == "corroborated", rows["a"]
assert rows["b"][2] == "superseded" and rows["c"][2] == "official" and rows["c"][3] == 1, rows
conn.close()
kb = store.KB(db) if hasattr(store, "KB") else store.open_ro(db)


class C:            # minimal live context for community_notes
    def __init__(self, summary, player=None):
        self.summary, self.player = summary, player


got = api.community_notes(kb, C({"level": 22, "zone": "Wetlands", "class": "MAGE"}), live=True)
assert [n["fact"] for n in got] == ["n:a"], got
got = api.community_notes(kb, C({"level": 22, "zone": "Wetlands", "class": "WARRIOR"}))
assert "n:b" not in [n["fact"] for n in got], "superseded notes are left out"
assert "n:b" in [n["fact"] for n in api.community_notes(kb, C({"level": 22}), query="warrior")], "query finds them"
assert api.community_notes(kb, C({"level": 40, "zone": "Tanaris"}), live=True) == []

# ---- the fixture KB (tests/kb/fixture.py, its synthetic notes): tool, block, fact ids --------------------
# (the same on the player's data/kb.db and today's intel: the report-only tests/kb/smoke_community.py)
import shutil  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixture  # noqa: E402

fx = fixture.tmpdir("community-")
try:
    kb_path, summary_, root, _run = fixture.build(fx)
    fnotes, fbad = community.read_notes(os.path.join(root, "knowledge", "community"))
    assert len(fnotes) == 4 and fbad == 0, (len(fnotes), fbad)
    pack = api.KnowledgePack(kb_path)
    assert pack.ok and "community" in api.tool_names()
    summary = {"name": "Fixa", "class": "WARRIOR", "race": "Dwarf", "faction": "Alliance", "level": 9, "xp": 100,
               "xp_max": 6500, "map": 1432, "x": 35.0, "y": 45.0, "zone": "Loch Modan", "guide": None, "step": None,
               "steps": [], "quests": {}}
    kc = pack.context(summary, triggers=["question"])
    r = api.dispatch("community", {"query": "dungeon quest xp"}, kc)
    facts = [n["fact"] for n in r["notes"]]
    assert "n:fx-dungeon-quests" in facts, r
    assert all(f.startswith("n:") for f in r["facts"]) and "community reports" in r["note"], r
    r = api.dispatch("community", {}, kc)
    got = [n["fact"] for n in r["notes"]]
    assert "n:fx-bear-xp" in got, r                                             # Loch Modan, level 9
    assert "n:fx-warlock-trick" not in got, "another class's note"
    block = pack.block(kc, ["question"])
    assert any(n["fact"].startswith("n:") for n in block.get("community") or []), block.get("community")
    known, unknown = pack.verify_facts(["n:fx-bear-xp", "n:nope"])
    assert known == ["n:fx-bear-xp"] and unknown == ["n:nope"], (known, unknown)
    rec = pack.community_recent(3)
    assert rec["total"] == 4 and rec["last_scan"] and len(rec["notes"]) == 3, rec
finally:
    shutil.rmtree(fx, ignore_errors=True)
print("OK")
