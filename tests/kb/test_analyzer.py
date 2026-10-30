"""Analyzer tests on the fixture KB (tests/kb/fixture.py): route applicability and RXP numbering, the per-step
briefs, the simulator, alternatives, the build_kb loader (on a temp copy) and the session review of the fixture's
recorded play-through. The same on the player's data/kb.db is the report-only tests/kb/smoke_analyzer.py.

Run: python3 tests/kb/test_analyzer.py   (prints OK)
"""
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import fixture  # noqa: E402
from knowledge import store  # noqa: E402
from knowledge.analyzer import alternatives as A  # noqa: E402
from knowledge.analyzer import params as AP  # noqa: E402
from knowledge.analyzer import review as V  # noqa: E402
from knowledge.analyzer import route as R  # noqa: E402
from knowledge.analyzer import simulate as S  # noqa: E402
from knowledge.analyzer.kbdata import get_data  # noqa: E402

DM = fixture.DM
WARRIOR = {"class": "WARRIOR", "race": "Dwarf", "faction": "Alliance", "hardcore": False}
WARLOCK = {"class": "WARLOCK", "race": "Gnome", "faction": "Alliance"}
CONTRACT = ("guide_key", "idx", "rxp_step_no", "class", "kind", "exp_level_before", "exp_level_after", "xp_now",
            "xp_unlocked", "time_s", "travel_s", "kill_s", "xp_per_min", "risk", "justification", "alternatives",
            "deps", "facts")
KINDS = ("turnin", "accept", "complete", "collect", "grind", "travel", "hs", "fly", "deathskip", "train", "vendor",
         "dungeon", "other")
QUEST_KEYS = ("name", "level", "type", "xp_full", "xp_at_level", "role", "chain_next", "unlocks", "unlocks_xp",
              "turnin_step", "turnin_guide", "accept_step", "level_gap", "decay_at_level")
FX = {}


def kb():
    return store.KB(FX["kb"])


def test_route():
    k = kb()
    rt = R.Route(k, DM, WARRIOR)
    assert [s["idx"] for s in rt.steps] == [1, 2, 4, 5, 6, 7, 10, 11, 12, 13, 14, 16, 17, 18], [s["idx"] for s in rt.steps]
    assert 8 not in rt.by_idx and 9 not in rt.by_idx, "warlock-only steps must be excluded for a warrior"
    assert 15 not in rt.by_idx, "`step << skip` is never shown"
    wl = R.Route(k, DM, WARLOCK)
    assert 8 in wl.by_idx and 9 in wl.by_idx, "warlock steps must be included for a warlock"
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
    print("analyze: %d briefs in %.2fs; the fixture warrior ends at %.2f after %.1f min" % (len(briefs), dt, last,
                                                                                         total_min))
    assert 8.0 <= last < 9.0, last                                 # the guide ends grinding to level 8
    assert 20 <= total_min <= 180, total_min
    assert [by[i]["kind"] for i in (1, 2, 4, 5, 6, 10, 16, 17, 18)] == [
        "accept", "grind", "deathskip", "turnin", "complete", "collect", "hs", "travel", "grind"], \
        [(b["idx"], b["kind"]) for b in briefs]
    # step 5: turn in Fixture Delivery, accept Fixture Boars (turned in at step 7)
    q = by[5]["justification"]["quests"]
    assert q["90001"]["xp_at_level"] > 0 and q["90001"]["role"] == "turnin"
    assert q["90002"]["role"] == "accept" and q["90002"]["turnin_step"] == 7
    assert any(e["quest"] == 90002 and e["idx"] == 7 for e in by[5]["deps"]["enables"]), by[5]["deps"]
    # step 2: grind to 5+1595 XP on the boars
    b2 = by[2]
    assert b2["xp_now"] == 1595 and any(w.startswith("xp_gate:5") for w in b2["justification"]["why"]), b2
    assert b2["justification"]["kills"] and b2["justification"]["kills"][0]["name"] == "Fixture Boar"
    # the last step's grind to 8 is already done by then
    assert "skipped:already_done" in by[18]["justification"]["why"], by[18]["justification"]["why"]
    assert "hearth_cycle" in by[16]["justification"]["why"]
    # alternatives at the hub: the side quest no guide routes
    hubs = [b for b in briefs if "hub_turnin" in b["justification"]["why"] and b["alternatives"]]
    assert hubs, "no hub step with alternatives"
    assert any(a["kind"] == "quest" and a["id"] == 90006 for b in hubs for a in b["alternatives"])
    for b in hubs:
        for a in b["alternatives"]:
            assert set(a) == {"kind", "id", "name", "level", "xp", "xp_per_min_est", "dist_yd", "why_not"}
    # facts: quest/creature/param ids
    assert all(b["facts"] for b in briefs if b["kind"] == "turnin")
    assert sum(1 for b in briefs if b["facts"]) >= 0.8 * len(briefs)
    allf = {f.split(":")[0] for b in briefs for f in b["facts"]}
    assert {"q", "c", "p", "i"} <= allf, allf
    # kill objectives carry the contract keys
    kills = [kk for b in briefs for kk in b["justification"]["kills"]]
    assert kills and all({"creature_id", "name", "level", "rank", "count", "xp_each", "ttk_s"} <= set(kk) for kk in kills)
    # travel uses observed edges (the recorder walked the fixture route)
    assert any(b["justification"]["travel"].get("observed") for b in briefs)
    # the warlock run has its trainer and vendor steps
    wl = R.analyze(k, R.Route(k, DM, WARLOCK), with_alternatives=False)
    assert [(b["idx"], b["kind"]) for b in wl if b["idx"] in (8, 9)] == [(8, "train"), (9, "vendor")]
    print("analyze: step 5 %s, step 2 grind %d XP, %d hub steps with alternatives"
          % (by[5]["justification"]["why"], b2["xp_now"], len(hubs)))


def test_simulate():
    k = kb()
    rt = R.Route(k, DM, WARRIOR)
    # skipping the first quest of the chain loses every follow-up, through the next guide
    c = S.compare(k, rt, None, None, {"skip_quest": 90001})
    lost = {x["id"] for x in c["delta"]["lost_quests"]}
    assert lost == {90001, 90002, 90003, 90005, 90007, 90008}, lost
    assert c["delta"]["time_s"] != 0 and c["delta"]["notes"]
    print("simulate: skipping 90001 loses %s, %+.1f min, %+d XP" % (sorted(lost), c["delta"]["time_s"] / 60,
                                                                     c["delta"]["xp"]))
    # grind one extra level after a mid-guide step
    mid = rt.steps[len(rt.steps) // 3]["idx"]
    base = S.run(k, rt, None)
    lvl_mid = int(next(e["level_after"] for e in base["timeline"] if e["idx"] == mid))
    g = S.compare(k, rt, None, None, {"grind_here": {"idx": mid, "to_level": lvl_mid + 1}})
    assert g["delta"]["end_level"] > 0 and g["delta"]["time_s"] > 0, g["delta"]
    # holding the level-2 delivery until level 8 loses quest XP (it is 6 levels below by then)
    h = S.compare(k, rt, None, None, {"hold_turnin": {"quest": 90001, "until_level": 8}})
    assert any(x["id"] == 90001 and x["xp_after"] < x["xp_before"] for x in h["delta"]["changed"]), h["delta"]
    # skip a step, leave the zone early, eta
    s = S.compare(k, rt, None, None, {"skip_step": 13})
    assert s["delta"]["time_s"] <= 0 and s["delta"]["xp"] < 0, s["delta"]
    lz = S.compare(k, rt, None, None, {"leave_zone_at": 12})
    assert lz["variant"]["guides"] == lz["baseline"]["guides"] == [DM, fixture.LM], lz["baseline"]["guides"]
    assert lz["delta"]["time_s"] < 0 and [x["id"] for x in lz["delta"]["lost_quests"]] == [90004]
    e = S.eta(k, rt, None, None, 8)
    assert e["reached"] and e["guide_key"] == DM and e["time_s"] > 0
    st = dict(base["start"], level=7, xp=0)
    e2 = S.eta(k, rt, st, 10, 8)
    assert e2["reached"] and 0 < e2["time_s"] < e["time_s"]
    # from a live state in the middle of the guide
    mid_state = {"level": 6, "xp": 1000, "time_s": 0, "ui_map": 1426, "x": 46.0, "y": 52.0,
                 "quests_done": {90001}, "quests_active": {90002}, "hearth_ready_t": 0, "rested_pool": 0}
    r = S.run(k, rt, mid_state, 6)
    assert r["timeline"][0]["idx"] == 6 and r["end"]["exp_level"] > 6.2
    print("simulate: grind_here %+.2f levels %+.1f min; hold 90001: %s; eta 8 = %.0f min" % (
        g["delta"]["end_level"], g["delta"]["time_s"] / 60, h["delta"]["notes"][0], e["time_s"] / 60))


def test_alternatives():
    k = kb()
    t0 = time.time()
    alts = A.alternatives_near(k, 1426, 46.0, 52.0, 7, WARRIOR)
    dt = time.time() - t0
    assert alts, "no alternatives at the hub"
    assert any(a["kind"] == "quest" and a["id"] == 90006 for a in alts) and any(a["kind"] == "grind" for a in alts)
    assert all(isinstance(a["why_not"], list) for a in alts)
    assert any(a["why_not"] for a in alts), "expected why_not codes"
    codes = {w.split(":")[0] for a in alts for w in a["why_not"]}
    known = {"far_objectives", "low_drop_rate", "elite", "group", "long_chain", "out_of_level", "class_restricted",
             "low_xp_per_min", "routed_later", "unknown_objective_location"}
    assert codes <= known, codes - known
    # route exclusions and routed-later marks
    rl = {a["id"]: "%s/%s" % (DM, 99) for a in alts if a["kind"] == "quest"}
    excl = {next(iter(rl))}
    alts2 = A.alternatives_near(k, 1426, 46.0, 52.0, 7, WARRIOR, exclude_quests=excl, routed_later=rl)
    assert not any(a["id"] in excl for a in alts2 if a["kind"] == "quest")
    assert any(w.startswith("routed_later:") for a in alts2 for w in a["why_not"])
    t0 = time.time()
    for x in range(20):
        A.alternatives_near(k, 1432, 30 + x, 45, 9, {"class": "MAGE", "race": "Gnome", "faction": "Alliance"})
    dt2 = (time.time() - t0) / 20
    assert dt < 2.0 and dt2 < 0.2, (dt, dt2)
    print("alternatives: %d at the hub (first call %.0f ms, then %.1f ms/call): %s" % (
        len(alts), dt * 1000, dt2 * 1000, [(a["kind"], a["name"], a["why_not"]) for a in alts[:4]]))


def test_load_and_review():
    path = os.path.join(FX["tmp"], "kb_copy.db")
    shutil.copyfile(FX["kb"], path)
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
    assert rep["n_errors"] == 0 and rep["guides"] == 4, rep["errors"][:3]
    assert dt < 120, dt
    classes = [r[0] for r in conn.execute("SELECT DISTINCT class FROM step_brief WHERE guide_key=? ORDER BY 1", (DM,))]
    assert len(classes) == 8 and "SHAMAN" not in classes, classes          # the Alliance classes of 2026
    assert conn.execute("SELECT COUNT(*) FROM guide_brief WHERE guide_key=?", (DM,)).fetchone()[0] == 8
    assert conn.execute("SELECT COUNT(DISTINCT guide_key) FROM guide_brief").fetchone()[0] == 4
    row = conn.execute("""SELECT kind, rxp_step_no, exp_level, exp_level_after, justification, facts, race
                          FROM step_brief WHERE guide_key=? AND idx=5 AND class='WARRIOR'""", (DM,)).fetchone()
    assert row[0] == "turnin" and row[1] == 4 and row[2] <= row[3] and row[6] == "Dwarf", tuple(row)
    assert "90001" in json.loads(row[4])["quests"] and json.loads(row[5])
    meta = json.loads(conn.execute("SELECT value FROM meta WHERE key='analyzer_params'").fetchone()[0])
    assert "analyzer.kill.search_s" in meta
    conn.close()
    # the review of the fixture's recorded session (obs_step_time from its companion log)
    kbt = store.KB(path)
    rv = V.review_session(kbt, fixture.SESSION)
    t = rv["totals"]
    visits = sum(t1 - t0 for _, _, t0, t1 in FX["run"]["steps"])
    assert abs(t["actual_s"] - visits) <= 2 * len(FX["run"]["steps"]), (t["actual_s"], visits)
    assert t["expected_s"] > 0 and t["ratio"] > 0 and t["deaths"] == 1, t
    assert len(rv["time_sinks"]) <= 5 and rv["facts"]
    assert [v["idx"] for v in rv["steps"]][:4] == [1, 2, 4, 5], rv["steps"][:4]
    print("review: %s" % rv["facts"][:3])
    # runtime API on the built KB
    who = {"class": "WARRIOR", "race": "Dwarf", "faction": "Alliance"}
    b = R.why(kbt, DM, 11, who, live_state={"level": 7, "xp": 500, "quests_complete": [90003]})
    assert b and b["live"]["level_delta"] is not None and b["live"]["pending_turnin_xp"] == 850, b and b["live"]
    assert R.expected_level_at(kbt, DM, 14, "WARRIOR") > 7
    n4 = R.brief_for_rxp_step(kbt, DM, 4, who)
    assert n4["idx"] == 5 and n4["rxp_step_no"] == 4
    assert R.step_quest_type(kbt, DM, 5, "WARRIOR") == "normal"
    assert R.pending_turnin_xp(kbt, DM, 11, [90003, 90004], 7) == 850 + 1000
    assert R.turnin_level_gap(kbt, [90003, 90004], 9) == 2


def test_params():
    rows = AP.all()
    assert rows and all(r["status"] in AP.STATUSES for r in rows)
    assert AP.get("travel.run_speed") > 0                      # mechanics keys are answered too
    assert AP.fact("analyzer.kill.search_s") == "p:analyzer.kill.search_s"


if __name__ == "__main__":
    FX["tmp"] = fixture.tmpdir("analyzer-")
    try:
        FX["kb"], _summary, _root, FX["run"] = fixture.build(FX["tmp"])
        get_data(kb())
        test_params()
        test_route()
        test_analyze()
        test_simulate()
        test_alternatives()
        test_load_and_review()
    finally:
        shutil.rmtree(FX["tmp"], ignore_errors=True)
    print("OK")
