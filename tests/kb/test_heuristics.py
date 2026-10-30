#!/usr/bin/env python3
"""Heuristics lane: rules.json, engine.py, glossary.json, eval/questions.jsonl. Plain python, prints OK."""
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)

from knowledge.heuristics import engine as E  # noqa: E402

KB = os.path.join(ROOT, "knowledge")
RULES = os.path.join(KB, "heuristics", "rules.json")
GLOSSARY = os.path.join(KB, "glossary.json")
QUESTIONS = os.path.join(KB, "eval", "questions.jsonl")


def ids(hits):
    return [h["id"] for h in hits]


# ---------------------------------------------------------------------------------------------------------
# rules.json
# ---------------------------------------------------------------------------------------------------------
with open(RULES, encoding="utf-8") as fh:
    raw = json.load(fh)
assert isinstance(raw, list) and len(raw) >= 250, len(raw)
assert len({r["id"] for r in raw}) == len(raw), "duplicate rule ids"
for r in raw:
    for k in E.REQUIRED_KEYS:
        assert k in r, (r.get("id"), k)
    assert r["title"] and r["advice"] and r["rationale"], r["id"]
    assert isinstance(r["source"], list) and r["source"] and all(isinstance(s, str) and s for s in r["source"]), r["id"]
    assert isinstance(r["tags"], list) and r["tags"], r["id"]
    assert 0 <= r["conf"] <= 1 and r["verified"] in (0, 1) and r["priority"] in (1, 2, 3), r["id"]
problems = E.validate(json.loads(json.dumps(raw)))
assert not problems, problems[:10]
# every field any predicate references is documented
for r in raw:
    for f in E.predicate_fields(r["applies"]):
        assert f in E.STATE_FIELDS, (r["id"], f)
for name, doc in E.STATE_FIELDS.items():
    assert doc.get("type") and doc.get("meaning"), name
# Forever-specific rules: unverified and conf <= 0.5 unless Blizzard's notes, press with numbers, or our recorder
OFFICIAL = ("https://www.mmo-champion.com", "https://www.pcgamer.com", "obs:", "https://massivelyop",
            "https://classicwow.gg/forever/changes")
for r in raw:
    if "forever" in r["tags"] and r["conf"] > 0.5:
        assert any(s.startswith(OFFICIAL) for s in r["source"]), r["id"]
    if r["verified"] == 1:
        assert any(s.startswith("obs:") for s in r["source"]), r["id"]
# coverage: every Classic class has a low-health rule and some class rules
for c in E.CLASSES:
    cr = [r for r in raw if r["id"].startswith(c.lower() + ".")]
    assert len(cr) >= 6, (c, len(cr))
    assert any("low-health" in r["tags"] for r in cr), c
tags = {t for r in raw for t in r["tags"]}
for t in ("route", "hearth", "death", "rested", "pace", "gear", "economy", "forever", "dungeon", "combat",
          "downtime", "professions", "travel", "xp", "buffs", "quest"):
    assert t in tags, t

# ---------------------------------------------------------------------------------------------------------
# engine helpers
# ---------------------------------------------------------------------------------------------------------
assert E.gray_level(5) == 0 and E.gray_level(10) == 4 and E.gray_level(25) == 18 and E.gray_level(40) == 31
assert E.gray_level(50) == 39 and E.gray_level(59) == 47
assert E.zero_difference(5) == 5 and E.zero_difference(20) == 11 and E.zero_difference(59) == 16
assert E.benchmark_xph(25) == 21000 and E.benchmark_xph(60) is None

rules = E.load_rules()
assert len(rules) == len(raw)

# warrior level 12 in combat at 25% health: a low-health combat rule ranks first
hits = E.evaluate({"level": 12, "class": "WARRIOR", "combat": True, "hp_pct": 25, "power_type": "rage",
                   "power_pct": 30})
assert hits and "low-health" in hits[0]["tags"] and hits[0]["priority"] == 1, hits[:2]
assert "warrior.low-hp" in ids(hits)
assert hits[0]["why"] and all(isinstance(w, str) for w in hits[0]["why"])
# same through the low-health trigger only
hits = E.evaluate({"level": 30, "class": "MAGE", "trigger": "low_health_in_combat"})
assert hits and "low-health" in hits[0]["tags"], hits[:2]

# level 39, no mount, under 100 gold: the mount-saving rule fires; a warlock gets the class-mount rule instead
hits = E.evaluate({"level": 39, "class": "WARRIOR", "has_mount": False, "money_gold": 60})
assert "econ.mount-save" in ids(hits), ids(hits)
hits = E.evaluate({"level": 39, "class": "WARLOCK", "has_mount": False, "money_gold": 60})
assert "econ.mount-save" not in ids(hits) and "warlock.felsteed-40" in ids(hits), ids(hits)
# money in copper (summary form) derives money_gold
hits = E.evaluate({"level": 37, "class": "ROGUE", "has_mount": False, "money": 412345})
assert "econ.mount-save" in ids(hits)

# unknown fields: no error, rule doesn't fire (also through not/any/all)
custom = [{"id": "t.unknown", "title": "t", "tags": ["route"], "applies": {"no_such_field": {">": 1}},
           "advice": "a", "rationale": "r", "source": ["x"], "conf": 0.5, "verified": 0, "priority": 1},
          {"id": "t.not-unknown", "title": "t", "tags": ["route"], "applies": {"not": {"no_such_field": 1}},
           "advice": "a", "rationale": "r", "source": ["x"], "conf": 0.5, "verified": 0, "priority": 1},
          {"id": "t.any", "title": "t", "tags": ["route"],
           "applies": {"any": [{"no_such_field": 1}, {"level": {">=": 10}}]},
           "advice": "a", "rationale": "r", "source": ["x"], "conf": 0.5, "verified": 0, "priority": 2},
          {"id": "t.all", "title": "t", "tags": ["route"],
           "applies": {"all": [{"no_such_field": 1}, {"level": {">=": 10}}]},
           "advice": "a", "rationale": "r", "source": ["x"], "conf": 0.5, "verified": 0, "priority": 2},
          {"id": "t.false-wins", "title": "t", "tags": ["route"],
           "applies": {"all": [{"no_such_field": 1}, {"level": {">=": 50}}]},
           "advice": "a", "rationale": "r", "source": ["x"], "conf": 0.5, "verified": 0, "priority": 2},
          {"id": "t.not-false", "title": "t", "tags": ["route"], "applies": {"not": {"level": {">=": 50}}},
           "advice": "a", "rationale": "r", "source": ["x"], "conf": 0.5, "verified": 0, "priority": 3}]
got = ids(E.evaluate({"level": 20, "class": "WARRIOR"}, rules=custom, limit=0))
assert got == ["t.any", "t.not-false"], got
assert E.evaluate({}, rules=custom) == [] or "t.not-false" not in ids(E.evaluate({}, rules=custom))
assert E.validate(custom[:1]) and "undocumented" in E.validate(custom[:1])[0]
# explicit fallback for a missing field: {"unknown": true} fires, the default does not
fallback = [dict(custom[0], id="t.fallback", applies={"in_group": {"==": False, "unknown": True}}),
            dict(custom[0], id="t.strict", applies={"in_group": False})]
assert ids(E.evaluate({"level": 20}, rules=fallback, limit=0)) == ["t.fallback"]
assert ids(E.evaluate({"level": 20, "in_group": True}, rules=fallback, limit=0)) == []
assert ids(E.evaluate({"level": 20, "group_size": 1}, rules=fallback, limit=0)) == ["t.fallback", "t.strict"]
# safe inferences: alive when health is shown, no mount below 30, mounted means has a mount
d = E.derive({"level": 12, "hp_pct": 40})
assert d["dead"] is False and d["ghost"] is False and d["has_mount"] is False
assert E.derive({"level": 45, "mounted": True})["has_mount"] is True
assert E.derive({"level": 45}).get("has_mount") is None and E.derive({"level": 12, "hp": -1}).get("dead") is None
assert "downtime.caster-drink" in ids(E.evaluate({"level": 26, "class": "MAGE", "combat": False,
                                                  "power_type": "mana", "power_pct": 20, "hp_pct": 80}, limit=0))
try:
    E.compile_predicate({"level": {"~=": 3}})
    raise AssertionError("bad operator accepted")
except ValueError:
    pass

# summary-shaped state (GameState.summary() plus triggers): derived fields
summary = {"class": "WARRIOR", "race": "Gnome", "faction": "Alliance", "level": 6, "xp": 2477, "xp_max": 2800,
           "rested": 700, "hp": 80, "power": 25, "power_type": "RAGE", "combat": False, "dead": 0, "map": 1426,
           "zone": "Dun Morogh", "subzone": "Kharanos", "money": 92, "free_bags": 7, "durability": 100,
           "hearth_cd": 0, "bind": "Coldridge Valley", "resting": False, "mounted": False, "on_taxi": False,
           "target": {"npc": "1125", "name": "Crag Boar", "level": 5, "hp": 100, "reaction": 2, "dead": False},
           "guide": "5-11 Dun Morogh", "guide_group": "RestedXP Forever Guide (A)", "step": 40,
           "steps": [{"index": 40, "elements": [{"tag": "goto"}, {"tag": "turnin", "quest": 1},
                                                 {"tag": "accept", "quest": 2}], "text": "Turn in The Adventurer"}],
           "quests": {"1": {"done": True, "obj": []}, "2": {"done": True, "obj": []},
                      "3": {"done": False, "obj": [(0, 1)]}},
           "buffs": [], "buffs_stale": False, "xp_per_hour": 9000, "session_minutes": 45, "on_step_seconds": 130,
           "trigger": [["question", {}]]}
d = E.derive(summary)
assert d["guide_level_min"] == 5 and d["guide_level_max"] == 11 and d["level_over_guide"] == -5
assert d["step_has"] == ["goto", "turnin", "accept"] and d["quest_log_count"] == 3 and d["quests_complete"] == 2
assert d["hp_pct"] == 80 and d["power_type"] == "rage" and d["target_level_diff"] == -1 and d["target_hostile"]
assert d["target_gray"] is False and d["hearth_ready"] is True and d["money_gold"] == 0.01
assert d["rested_pct"] == 25.0 and d["xp_to_level"] == 323 and d["trigger"] == ["question"] and d["level_even"]
assert d["xp_per_hour_ratio"] == round(9000 / 14000, 2) and d["dead"] is False
assert summary["level"] == 6 and "step_has" not in summary            # input not mutated
hits = E.evaluate(summary, limit=20)
assert "route.turnin-batch-hub" in ids(hits) and "buffs.battle-shout" in ids(hits), ids(hits)
assert all(h["cat"] in ("route", "combat", "buffs", "gear", "pace", "upkeep") for h in hits)
# ranking: priority ascending, then conf descending
keys = [(h["priority"], -h["conf"]) for h in hits]
assert keys == sorted(keys), keys

# buffs: Battle Shout reminder only when missing, known (not frozen) and affordable
base = {"class": "WARRIOR", "level": 8, "combat": False, "dead": 0, "power_type": "rage", "power_pct": 20}
assert "buffs.battle-shout" in ids(E.evaluate(dict(base, buffs=[]), limit=0))
assert "buffs.battle-shout" not in ids(E.evaluate(dict(base, buffs=[{"name": "Battle Shout"}]), limit=0))
assert "buffs.battle-shout" not in ids(E.evaluate(dict(base, buffs=[], buffs_stale=True), limit=0))
assert "buffs.battle-shout" not in ids(E.evaluate(dict(base, buffs=[], power_pct=5), limit=0))
assert "buffs.battle-shout" not in ids(E.evaluate(base, limit=0))         # buff list unknown

# death: corpse run at 24; spirit res under 11; hardcore never deathskips
hits = E.evaluate({"level": 24, "class": "WARRIOR", "dead": 2})
assert hits[0]["id"] == "death.res-sickness-20plus", ids(hits)
assert "death.spirit-res-under-11" in ids(E.evaluate({"level": 8, "dead": 1}, limit=0))
hits = E.evaluate({"level": 9, "step_has": ["deathskip"], "hardcore": True, "dead": False})
assert hits[0]["id"] == "death.deathskip-hardcore", ids(hits)

# XP mechanics: gray target, turn-in decay window, hearth
assert "combat.gray-target" in ids(E.evaluate({"level": 30, "target_level": 20, "target_dead": False}, limit=0))
assert "combat.gray-target" not in ids(E.evaluate({"level": 30, "target_level": 25, "target_dead": False},
                                                  limit=0))
assert "route.turnin-before-outlevel" in ids(E.evaluate({"level": 20, "turnin_level_gap": 5}, limit=0))
assert "route.abandon-gray-quests" in ids(E.evaluate({"level": 30, "turnin_level_gap": 11}, limit=0))
hits = E.evaluate({"level": 30, "step_has": ["hs"], "hearth_cd": 1500}, limit=0)
assert "hearth.on-cooldown" in ids(hits) and "hearth.after-loop" not in ids(hits)

# pace ratio derived from XP/hour and the band benchmark
d = E.derive({"level": 25, "xp_per_hour": 12000, "session_minutes": 30})
assert d["xp_per_hour_ratio"] == round(12000 / 21000, 2)
assert "pace.behind" in ids(E.evaluate({"level": 25, "xp_per_hour": 12000, "session_minutes": 30,
                                        "trigger": "pace_review"}, limit=0))

# tag filters (the coach's muted categories)
hits = E.evaluate(summary, limit=0, tags=["hearth"])
assert hits and all("hearth" in h["tags"] for h in hits), ids(hits)
hits = E.evaluate({"level": 12, "class": "WARRIOR", "combat": True, "hp_pct": 25}, limit=0, exclude_tags=["combat"])
assert all(h["cat"] != "combat" for h in hits), [(h["id"], h["cat"]) for h in hits]

# Forever: Zephras Isle rules fire only there; the hearth rule is verified
hits = E.evaluate({"level": 5, "race": "Skyborne", "faction": "Alliance", "zone": "Zephras Isle",
                   "combat": False, "hp_pct": 50, "step_has": ["goto"]}, limit=0)
assert {"forever.zephras-windstones", "forever.zephras-tornado", "forever.skyborne-walk-on-air"} <= set(ids(hits))
assert all(h["verified"] == 0 for h in hits if "forever" in h["tags"])
hearth = [r for r in raw if r["id"] == "hearth.cooldown-60-forever"][0]
assert hearth["verified"] == 1

# ---------------------------------------------------------------------------------------------------------
# glossary.json
# ---------------------------------------------------------------------------------------------------------
with open(GLOSSARY, encoding="utf-8") as fh:
    gl = json.load(fh)
assert isinstance(gl, list) and len(gl) >= 60, len(gl)
assert len({g["term"].lower() for g in gl}) == len(gl), "duplicate glossary terms"
names = set()
for g in gl:
    assert g["term"] and g["definition"] and isinstance(g["aliases"], list), g
    names.add(g["term"].lower())
    names.update(a.lower() for a in g["aliases"])
for t in ("breadcrumb", "chain", "hub", "loop", "gray mob", "tag", "hearth", "rested", "ding", "xp/hour", "grind",
          "zone-out", "deathskip", "spirit res", "res sickness", "aggro radius", "daze", "pull", "add", "los",
          "kiting", "fp", "flight path", "boat", "zeppelin", "class quest", "elite", "group quest", "escort",
          "turn-in", "pre-quest", "exclusive quest", "quest xp decay", "zero difference", "zd", "elite xp",
          "quest log cap", "hearth cooldown", "downtime", "rotation", "gcd", "rage", "energy", "mana", "first aid",
          "cooking", "weapon skill", "proficiency", "soulbound", "boe", "vendor trash", "ah", "speedrun", "splits",
          "aoe leveling", "dungeon leveling", "boosting", "skyborne", "zephras isle", "camp benefits",
          "boosted rest", "restedxp", "step", "sticky step", "optional step", "completewith"):
    assert t in names, t

# ---------------------------------------------------------------------------------------------------------
# eval/questions.jsonl
# ---------------------------------------------------------------------------------------------------------
qs = []
with open(QUESTIONS, encoding="utf-8") as fh:
    for n, line in enumerate(fh, 1):
        line = line.strip()
        if not line:
            continue
        q = json.loads(line)
        for k in ("id", "question", "context", "gold", "type", "source"):
            assert k in q, (n, k)
        for k in ("class", "level", "zone", "situation"):
            assert k in q["context"], (q["id"], k)
        for k in ("facts", "must_mention", "must_not"):
            assert isinstance(q["gold"].get(k), list), (q["id"], k)
        assert q["gold"]["facts"] and q["question"] and q["source"], q["id"]
        assert q["type"] in ("why", "whatif", "how", "fact", "alt"), q["id"]
        assert q["context"]["class"] in E.CLASSES and 1 <= q["context"]["level"] <= 60, q["id"]
        qs.append(q)
assert len(qs) >= 120, len(qs)
assert len({q["id"] for q in qs}) == len(qs), "duplicate question ids"
assert sum(1 for q in qs if q["source"].startswith("data/live/")) >= 25
assert len({q["type"] for q in qs}) == 5
rule_ids = {r["id"] for r in raw}
for q in qs:
    for s in q["source"].split(";"):
        s = s.strip()
        if s.startswith("rule:"):
            assert s[5:] in rule_ids, (q["id"], s)
assert sum(1 for q in qs if q["id"].startswith("forever-")) >= 15
# items mined from the companion logs point at real player questions (file#Lline, 1-based)
refs = {}
for q in qs:
    for s in q["source"].split(";"):
        s = s.strip()
        if s.startswith("data/live/") and "#L" in s:
            f, ln = s.split("#L")
            refs.setdefault(f, {})[int(ln)] = q["id"]
for f, lines in refs.items():
    path = os.path.join(ROOT, f)
    if not os.path.exists(path):
        continue
    want = set(lines)
    with open(path, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            if n in want:
                assert '"kind": "question"' in line, (lines[n], f, n)
                want.discard(n)
                if not want:
                    break
    assert not want, (f, sorted(want))

# ---------------------------------------------------------------------------------------------------------
# build_kb loader into a fresh schema
# ---------------------------------------------------------------------------------------------------------
from knowledge import store  # noqa: E402
conn = sqlite3.connect(":memory:")
store.create_schema(conn)
res = E.load(conn, store.BuildContext())
assert res["heuristic"] == len(raw) and res["glossary"] == len(gl), res
res = E.load(conn, store.BuildContext())                                 # idempotent
assert conn.execute("SELECT COUNT(*) FROM heuristic").fetchone()[0] == len(raw)
row = conn.execute("SELECT applies, tags, source, verified FROM heuristic WHERE id='econ.mount-save'").fetchone()
assert json.loads(row[0])["level"] == {">=": 35, "<=": 39} and "economy" in json.loads(row[1]) and row[3] == 0
assert json.loads(conn.execute("SELECT aliases FROM glossary WHERE term='deathskip'").fetchone()[0])

# ---------------------------------------------------------------------------------------------------------
# timing: < 5 ms per evaluation with the full rule set
# ---------------------------------------------------------------------------------------------------------
states = [summary,
          {"level": 12, "class": "WARRIOR", "combat": True, "hp_pct": 25, "power_type": "rage", "power_pct": 30},
          {"level": 39, "class": "WARRIOR", "has_mount": False, "money_gold": 60, "trigger": "pace_review",
           "xp_per_hour": 20000, "session_minutes": 90}]
E.evaluate(summary)
t0 = time.perf_counter()
N = 200
for i in range(N):
    E.evaluate(states[i % len(states)])
avg_ms = (time.perf_counter() - t0) * 1000 / N
assert avg_ms < 5.0, avg_ms
print("rules=%d glossary=%d questions=%d avg_eval=%.2fms" % (len(raw), len(gl), len(qs), avg_ms))
print("OK")
