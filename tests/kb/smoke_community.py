#!/usr/bin/env python3
"""Smoke (report-only, see tests/kb/smoke.py): the community tool on the player's data/kb.db with today's intel
(knowledge/community/intel): notes that were relevant when this was written. tests/kb/test_community.py checks the
same code on the fixture notes.

    py -3 tests/kb/smoke_community.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import smoke  # noqa: E402

sys.path.insert(0, smoke.ROOT)
from knowledge import api  # noqa: E402

KB_PATH = smoke.KB


def real_kb():
    smoke.need(KB_PATH)
    pack = api.KnowledgePack(KB_PATH)
    if not pack.kb.q("SELECT id FROM community_note LIMIT 1"):
        raise smoke.NotRun("kb.db has no community notes (build_kb.py first)")
    assert "community" in api.tool_names()
    summary = {"name": "T", "class": "MAGE", "race": "Human", "faction": "Alliance", "level": 27, "xp": 100, "xp_max": 30000,
               "map": 1437, "x": 50.0, "y": 50.0, "zone": "Wetlands", "guide": None, "step": None, "steps": [], "quests": {}}
    kc = pack.context(summary, triggers=["question"])
    r = api.dispatch("community", {"query": "dungeon xp"}, kc)
    facts = [n["fact"] for n in r["notes"]]
    assert "n:20261008-dungeon-quest-bonus-xp-halved" in facts, r
    assert all(f.startswith("n:") for f in r["facts"]) and "community reports" in r["note"], r
    r = api.dispatch("community", {}, kc)
    assert "n:20261008-phase2-cap-30" in [n["fact"] for n in r["notes"]], r            # Wetlands, level 27
    assert "n:20261008-druid-wrath-buff" not in [n["fact"] for n in r["notes"]], "another class's note"
    block = pack.block(kc, ["question"])
    assert any(n["fact"].startswith("n:") for n in block.get("community") or []), block.get("community")
    known, unknown = pack.verify_facts(["n:20261008-phase2-cap-30", "n:nope"])
    assert known == ["n:20261008-phase2-cap-30"] and unknown == ["n:nope"], (known, unknown)
    rec = pack.community_recent(5)
    assert rec["total"] >= 10 and rec["last_scan"] and len(rec["notes"]) == 5, rec


if __name__ == "__main__":
    smoke.run("community", [("community tool, block and facts on data/kb.db", real_kb)])
