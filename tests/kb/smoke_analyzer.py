#!/usr/bin/env python3
"""Smoke (report-only, see tests/kb/smoke.py): the analyzer on the player's data/kb.db: routes, briefs, the
simulator, alternatives, the loader (on a temp copy) and the review, with the facts of today's Dun Morogh guide.
tests/kb/test_analyzer.py checks the same code on the fixture KB.

    py -3 tests/kb/smoke_analyzer.py
"""
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import smoke  # noqa: E402

ROOT = smoke.ROOT
sys.path.insert(0, ROOT)

from knowledge import store  # noqa: E402
from knowledge.analyzer import alternatives as A  # noqa: E402
from knowledge.analyzer import params as AP  # noqa: E402
from knowledge.analyzer import review as V  # noqa: E402
from knowledge.analyzer import route as R  # noqa: E402
from knowledge.analyzer import simulate as S  # noqa: E402

KB_PATH = os.path.join(ROOT, "data", "kb.db")
DM = "RestedXP Forever Guide (A)||5-11 Dun Morogh"
WARRIOR = {"class": "WARRIOR", "race": "Human", "faction": "Alliance", "hardcore": False}
CONTRACT = ("guide_key", "idx", "rxp_step_no", "class", "kind", "exp_level_before", "exp_level_after", "xp_now",
            "xp_unlocked", "time_s", "travel_s", "kill_s", "xp_per_min", "risk", "justification", "alternatives",
            "deps", "facts")
KINDS = ("turnin", "accept", "complete", "collect", "grind", "travel", "hs", "fly", "deathskip", "train", "vendor",
         "dungeon", "other")
QUEST_KEYS = ("name", "level", "type", "xp_full", "xp_at_level", "role", "chain_next", "unlocks", "unlocks_xp",
              "turnin_step", "turnin_guide", "accept_step", "level_gap", "decay_at_level")


def kb():
    smoke.need(KB_PATH)
    k = store.KB(KB_PATH)
    if not k.one("SELECT key FROM rxp_guide WHERE key=?", DM):
        raise smoke.NotRun("no %s guide in data/kb.db" % DM)
    return k


def test_route():
    k = kb()
    rt = R.Route(k, DM, WARRIOR)
    assert len(rt.steps) > 100, len(rt.steps)
    assert 9 not in rt.by_idx and 10 not in rt.by_idx, "warlock-only steps must be excluded for a warrior"
    wl = R.Route(k, DM, {"class": "WARLOCK", "race": "Gnome", "faction": "Alliance"})
    assert 9 in wl.by_idx and 10 in wl.by_idx, "warlock steps must be included for a warlock"
    for r in (rt, wl):
        nums = [s["rxp_step_no"] for s in r.steps]
        assert nums == sorted(nums) and len(set(nums)) == len(nums), "rxp_step_no must be strictly increasing"
        assert all(r.rxp_to_idx[n] == s["idx"] for n, s in zip(nums, r.steps))
        idxs = [s["idx"] for s in r.steps]
        assert idxs == sorted(idxs)
    # hardcore-only step 3 is hidden for softcore and takes no RXP number; softcore deathskip 4 is kept
    assert 3 not in rt.by_idx and 4 in rt.by_idx
    hc = R.Route(k, DM, dict(WARRIOR, hardcore=True))
    assert 3 in hc.by_idx and 4 not in hc.by_idx
    assert rt.idx_to_rxp[5] == hc.idx_to_rxp[5] == rt.idx_to_rxp[4] + 1 == hc.idx_to_rxp[3] + 1
    # step 2: only the !Priest .xp threshold applies to a warrior
    xps = [a["args"] for a in rt.by_idx[2]["actions"] if a["verb"] == "xp"]
    assert len(xps) == 1 and xps[0]["xp"] == 1595, xps
    print("route: %d steps for the warrior (%d numbered, %d hidden), %d for the warlock"
          % (len(rt.steps), len(rt.rxp_to_idx), len(rt.excluded), len(wl.steps)))


def test_analyze():
    k = kb()
    rt = R.Route(k, DM, WARRIOR)
    t0 = time.time()
    briefs = R.analyze(k, rt)
    dt = time.time() - t0
    by = {b["idx"]: b for b in briefs}
    assert len(briefs) == len(rt.steps)
    for b in briefs:
        assert all(key in b for key in CONTRACT), [key for key in CONTRACT if key not in b]
        assert b["kind"] in KINDS, b["kind"]
        assert set(b["justification"]) >= {"quests", "kills", "travel", "co_located", "rxp_notes", "why", "risks"}
        assert set(b["deps"]) == {"needs", "enables", "last_chance"}
        assert b["exp_level_after"] >= b["exp_level_before"] - 1e-9
        assert 0.0 <= b["risk"] <= 1.0
        for q in b["justification"]["quests"].values():
            assert all(key in q for key in QUEST_KEYS)
        json.dumps(R.public(b))                                    # JSON-serialisable
    last = briefs[-1]["exp_level_after"]
    total_min = sum(b["time_s"] for b in briefs) / 60.0
    print("analyze: %d briefs in %.2fs; Dun Morogh warrior ends at %.2f after %.1f min" % (len(briefs), dt, last,
                                                                                         total_min))
    assert 10.0 <= last <= 12.5, last
    assert 45 <= total_min <= 240, total_min
    # step 5: turn in The Adventurer, accept The Great Outdoors (turned in at step 7)
    b5 = by[5]
    assert b5["kind"] == "turnin"
    q = b5["justification"]["quests"]
    assert "96628" in q and q["96628"]["xp_at_level"] > 0 and q["96628"]["role"] == "turnin"
    assert q["96608"]["role"] == "accept" and q["96608"]["turnin_step"] == 7
    assert any(e["quest"] == 96608 and e["idx"] == 7 for e in b5["deps"]["enables"]), b5["deps"]
    # step 2: grind to 5+1595 XP
    b2 = by[2]
    assert b2["kind"] == "grind" and b2["xp_now"] > 0, (b2["kind"], b2["xp_now"])
    assert any(w.startswith("xp_gate:5") for w in b2["justification"]["why"])
    assert b2["justification"]["kills"] and b2["justification"]["kills"][0]["name"] == "Crag Boar"
    # a hearth step
    hs = [b for b in briefs if any(a["verb"] == "hs" for a in rt.by_idx[b["idx"]]["actions"])]
    assert hs and all(b["kind"] == "hs" for b in hs)
    assert any("hearth_cycle" in b["justification"]["why"] for b in hs)
    # alternatives at a hub
    hubs = [b for b in briefs if "hub_turnin" in b["justification"]["why"] and b["alternatives"]]
    assert hubs, "no hub step with alternatives"
    assert any(a["kind"] in ("quest", "grind") for b in hubs for a in b["alternatives"])
    for b in hubs:
        for a in b["alternatives"]:
            assert set(a) == {"kind", "id", "name", "level", "xp", "xp_per_min_est", "dist_yd", "why_not"}
    # facts: quest/creature/param ids
    assert all(b["facts"] for b in briefs if b["kind"] == "turnin")
    assert sum(1 for b in briefs if b["facts"]) >= 0.8 * len(briefs)
    allf = {f.split(":")[0] for b in briefs for f in b["facts"]}
    assert {"q", "c", "p"} <= allf, allf
    # kill objectives carry the contract keys
    kills = [kk for b in briefs for kk in b["justification"]["kills"]]
    assert kills and all({"creature_id", "name", "level", "rank", "count", "xp_each", "ttk_s"} <= set(kk) for kk in kills)
    # travel uses observed edges somewhere in Dun Morogh (the recorder walked it)
    assert any(b["justification"]["travel"].get("observed") for b in briefs)
    # the warlock run has its trainer steps and differs from the warrior's
    wl = R.analyze(k, R.Route(k, DM, {"class": "WARLOCK", "race": "Gnome", "faction": "Alliance"}),
                   with_alternatives=False)
    assert any(b["idx"] == 9 and b["kind"] == "train" for b in wl)
    print("analyze: step 5 %s, step 2 grind %d XP, %d hub steps with alternatives"
          % (b5["justification"]["why"], b2["xp_now"], len(hubs)))


def _chain_quest(route):
    data = route.data
    acc = {}
    for st in route.steps:
        for q in st["quests"]["accept"]:
            acc.setdefault(q, st["idx"])
    for st in route.steps:
        for q in st["quests"]["turnin"]:
            nq = (data.quest(q) or {}).get("next_quest")
            if nq and nq in acc and q in acc and acc[q] < acc[nq] and nq in route.turnin_idx:
                return q, nq
    return None, None


def test_simulate():
    k = kb()
    rt = R.Route(k, DM, WARRIOR)
    q, nq = _chain_quest(rt)
    assert q, "no chained quest found in the guide"
    c = S.compare(k, rt, None, None, {"skip_quest": q})
    lost = {x["id"] for x in c["delta"]["lost_quests"]}
    assert q in lost and nq in lost, (q, nq, lost)
    assert c["delta"]["time_s"] != 0 and c["delta"]["xp"] < 0
    assert c["delta"]["notes"]
    print("simulate: skipping %s loses %s, %+.1f min, %+d XP (equal-XP %+.1f min)" % (
        q, sorted(lost), c["delta"]["time_s"] / 60, c["delta"]["xp"], c["delta"]["time_s_equal_xp"] / 60))
    # grind one extra level after a mid-guide step
    mid = rt.steps[len(rt.steps) // 3]["idx"]
    base = S.run(k, rt, None)
    lvl_mid = int(next(e["level_after"] for e in base["timeline"] if e["idx"] == mid))
    g = S.compare(k, rt, None, None, {"grind_here": {"idx": mid, "to_level": lvl_mid + 1}})
    assert g["delta"]["end_level"] > 0 and g["delta"]["time_s"] > 0, g["delta"]
    # holding an early turn-in until far above the quest level loses quest XP
    h = S.compare(k, rt, None, None, {"hold_turnin": {"quest": 420, "until_level": 13}})
    assert h["delta"]["xp"] != 0, h["delta"]
    assert any(x["id"] == 420 and x["xp_after"] < x["xp_before"] for x in h["delta"]["changed"]), h["delta"]
    # skip a step, leave the zone early, eta
    s = S.compare(k, rt, None, None, {"skip_step": 53})
    assert s["delta"]["time_s"] <= 0
    lz = S.compare(k, rt, None, None, {"leave_zone_at": 150})
    assert lz["variant"]["guides"] == lz["baseline"]["guides"] and len(lz["baseline"]["guides"]) == 2
    assert lz["delta"]["time_s"] < 0 and lz["delta"]["lost_quests"]
    e = S.eta(k, rt, None, None, 10)
    assert e["reached"] and e["guide_key"] == DM and e["time_s"] > 0
    st = dict(base["start"], level=9, xp=0)
    e2 = S.eta(k, rt, st, 150, 10)
    assert e2["reached"] and 0 < e2["time_s"] < e["time_s"]
    # from a live state in the middle of the guide
    mid_state = {"level": 8, "xp": 1000, "time_s": 0, "ui_map": 1426, "x": 30.2, "y": 45.7,
                 "quests_done": {317, 318}, "quests_active": {319, 384}, "hearth_ready_t": 0, "rested_pool": 2000}
    r = S.run(k, rt, mid_state, 76)
    assert r["timeline"][0]["idx"] == 76 and r["end"]["exp_level"] > 8.2
    print("simulate: grind_here %+.2f levels %+.1f min; hold 420: %s; eta 10 = %.0f min" % (
        g["delta"]["end_level"], g["delta"]["time_s"] / 60, h["delta"]["notes"][0], e["time_s"] / 60))


def test_alternatives():
    k = kb()
    t0 = time.time()
    alts = A.alternatives_near(k, 1426, 46.9, 52.5, 7, WARRIOR)
    dt = time.time() - t0
    assert alts, "no alternatives at Kharanos"
    assert any(a["kind"] == "quest" for a in alts) and any(a["kind"] == "grind" for a in alts)
    assert all(isinstance(a["why_not"], list) for a in alts)
    assert any(a["why_not"] for a in alts), "expected why_not codes"
    codes = {w.split(":")[0] for a in alts for w in a["why_not"]}
    known = {"far_objectives", "low_drop_rate", "elite", "group", "long_chain", "out_of_level", "class_restricted",
             "low_xp_per_min", "routed_later", "unknown_objective_location"}
    assert codes <= known, codes - known
    # route exclusions and routed-later marks
    rl = {a["id"]: "%s/%s" % (DM, 99) for a in alts if a["kind"] == "quest"}
    excl = {next(iter(rl))}
    alts2 = A.alternatives_near(k, 1426, 46.9, 52.5, 7, WARRIOR, exclude_quests=excl, routed_later=rl)
    assert not any(a["id"] in excl for a in alts2 if a["kind"] == "quest")
    assert any(w.startswith("routed_later:") for a in alts2 for w in a["why_not"])
    t0 = time.time()
    for x in range(20):
        A.alternatives_near(k, 1432, 30 + x, 50, 13, {"class": "MAGE", "race": "Gnome", "faction": "Alliance"})
    dt2 = (time.time() - t0) / 20
    assert dt < 2.0 and dt2 < 0.2, (dt, dt2)
    print("alternatives: %d at Kharanos (first call %.0f ms, then %.1f ms/call): %s" % (
        len(alts), dt * 1000, dt2 * 1000, [(a["kind"], a["name"], a["why_not"]) for a in alts[:4]]))


def test_load_and_review():
    kb()
    tmp = tempfile.mkdtemp(prefix="glide_analyzer_")
    path = os.path.join(tmp, "kb.db")
    try:
        shutil.copyfile(KB_PATH, path)
        conn = store.open_rw(path)
        conn.execute("DELETE FROM step_brief")
        conn.execute("DROP TABLE IF EXISTS guide_brief")
        conn.execute("DELETE FROM meta WHERE key LIKE 'analyzer%'")
        conn.commit()
        t0 = time.time()
        rep = R.load(conn, None)
        dt = time.time() - t0
        print("load: %d guides, %d routes, %d briefs in %.1fs; unknowns %s; fitted %s" % (
            rep["guides"], rep["routes"], rep["briefs"], dt, rep["unknowns"], rep["fitted"]))
        assert rep["n_errors"] == 0, rep["errors"][:3]
        assert dt < 120, dt
        classes = [r[0] for r in conn.execute("SELECT DISTINCT class FROM step_brief WHERE guide_key=? ORDER BY 1", (DM,))]
        assert len(classes) == 8 and "SHAMAN" not in classes, classes
        gb = conn.execute("SELECT COUNT(*) FROM guide_brief WHERE guide_key=?", (DM,)).fetchone()[0]
        assert gb == 8, gb
        assert conn.execute("SELECT COUNT(DISTINCT guide_key) FROM guide_brief").fetchone()[0] >= 150
        row = conn.execute("""SELECT kind, rxp_step_no, exp_level, exp_level_after, justification, facts, race
                              FROM step_brief WHERE guide_key=? AND idx=5 AND class='WARRIOR'""", (DM,)).fetchone()
        assert row[0] == "turnin" and row[1] and row[2] <= row[3] and row[6] == "Dwarf"
        assert "96628" in json.loads(row[4])["quests"] and json.loads(row[5])
        meta = json.loads(conn.execute("SELECT value FROM meta WHERE key='analyzer_params'").fetchone()[0])
        assert "analyzer.kill.search_s" in meta
        # synthetic recorded session: a warrior on the Dun Morogh guide
        conn.execute("INSERT INTO obs_session(id, char, class, race, faction, level_start, level_end) "
                     "VALUES('synthetic-1', 'Test', 'WARRIOR', 'Dwarf', 'Alliance', 5, 7)")
        visits = [(5, 0, 90, 5.4, 410), (7, 90, 150, 5.7, 410), (13, 150, 400, 6.0, 110), (56, 400, 1700, 6.3, 1800),
                  (58, 1700, 2300, 6.9, 1100), (60, 2300, 2400, 7.1, 625)]
        conn.executemany("INSERT INTO obs_step_time(session, guide_key, idx, t_start, t_end, level, xp_gain, deaths) "
                         "VALUES('synthetic-1', ?, ?, ?, ?, ?, ?, ?)",
                         [(DM, i, 1000 + a, 1000 + b, lv, xp, 1 if i == 56 else 0) for i, a, b, lv, xp in visits])
        conn.commit()
        conn.close()
        kbt = store.KB(path)
        rv = V.review_session(kbt, "synthetic-1")
        assert rv["totals"]["actual_s"] == 2400.0, rv["totals"]
        assert rv["totals"]["expected_s"] > 0 and rv["totals"]["ratio"] > 0
        assert rv["totals"]["deaths"] == 1
        assert len(rv["time_sinks"]) <= 5 and rv["facts"]
        assert any(v["idx"] == 13 and v["window"][0] < 13 for v in rv["steps"]), "windows cover skipped steps"
        print("review: %s" % rv["facts"][:3])
        # runtime API on the built KB
        b = R.why(kbt, DM, 76, {"class": "WARRIOR", "race": "Dwarf", "faction": "Alliance"},
                  live_state={"level": 9, "xp": 500, "quests_complete": [319]})
        assert b and b["live"]["level_delta"] is not None and b["live"]["pending_turnin_xp"] > 0
        assert R.expected_level_at(kbt, DM, 100, "WARRIOR") > 5
        n4 = R.brief_for_rxp_step(kbt, DM, 4, {"class": "WARRIOR", "race": "Dwarf", "faction": "Alliance"})
        assert n4["idx"] == 5 and n4["rxp_step_no"] == 4
        assert R.step_quest_type(kbt, DM, 5, "WARRIOR") == "normal"
        assert R.pending_turnin_xp(kbt, DM, 100, [319, 384], 8) == 525 + 625
        assert R.turnin_level_gap(kbt, [319, 384], 9) == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_params():
    rows = AP.all()
    assert rows and all(r["status"] in AP.STATUSES for r in rows)
    assert AP.get("travel.run_speed") > 0                      # mechanics keys are answered too
    assert AP.fact("analyzer.kill.search_s") == "p:analyzer.kill.search_s"


if __name__ == "__main__":
    smoke.run("analyzer", [("parameters", test_params), ("route", test_route), ("briefs", test_analyze),
                           ("simulator", test_simulate), ("alternatives", test_alternatives),
                           ("loader and review on a copy", test_load_and_review)])
