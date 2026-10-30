#!/usr/bin/env python3
"""RestedXP numbers its steps differently with SSF on/off and group quests on/off. The route cache keyed on class,
race and faction only, and the knowledge pack's caches on no setting at all: the first player cached decided the
numbering for everyone after, so the coach briefed the wrong step (order-dependent), and a group toggle made the
block fail ("step analysis failed: 40"). Stored briefs (built with SSF off, group quests off) were also served to
players with other settings. Synthetic guide first, then the real data/kb.db read-only when it is around."""
import json
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
from knowledge import api, store  # noqa: E402
from knowledge.analyzer import route as R  # noqa: E402

GRP = "Test Guide (A)"
GK = GRP + "||1-5 Testland"
BASE = {"class": "WARRIOR", "race": "Human", "faction": "Alliance", "hardcore": False}
# idx: flags. ssf on: 1 2 4 5 6 -> idx 5 is step 4; ssf off: 1 4 5 6 -> idx 5 is step 3; group off drops idx 4
STEPS = {1: {}, 2: {"ssf": True}, 3: {"ah": True, "ssf": True}, 4: {"group": True}, 5: {}, 6: {}, 7: {}}


def synthetic_kb(meta_rxp=None):
    path = os.path.join(tempfile.mkdtemp(prefix="glide-rxpset-"), "kb.db")
    c = store.open_rw(path)
    store.create_schema(c)
    R.ensure_schema(c)
    text = "#name 1-5 Testland\n" + "".join("step\n    .goto Elwynn Forest,%d,50\n" % (40 + i) for i in STEPS)
    c.execute("INSERT INTO rxp_guide(key, name, grp, level_min, level_max, faction, text) VALUES(?,?,?,?,?,?,?)",
              (GK, "1-5 Testland", GRP, 1, 5, "Alliance", text))
    for i, f in STEPS.items():
        c.execute("INSERT INTO rxp_step(guide_key, idx, text, flags) VALUES(?,?,?,?)",
                  (GK, i, "step %d" % i, json.dumps(f)))
    if meta_rxp is not None:
        store.set_meta(c, R.BRIEF_RXP_META, meta_rxp)
    c.commit()
    c.close()
    # stored briefs as load() writes them: the build's numbering (SSF off unless meta_rxp says otherwise)
    kb = store.open_ro(path)
    rt = R.Route(kb, GK, dict(R.BRIEF_RXP if meta_rxp is None else meta_rxp, **BASE))
    c = sqlite3.connect(path)
    for st in rt.steps:
        c.execute("INSERT INTO step_brief(guide_key, idx, class, exp_level, xp_now, xp_unlocked, time_s, travel_s, "
                  "kill_s, xp_per_min, risk, justification, alternatives, deps, computed, rxp_step_no, kind, race) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (GK, st["idx"], "WARRIOR", 1.0, 0, 0, 1, 0, 0, 999.0, 0, "{}", "[]", "{}", "{}", st["rxp_step_no"],
                   "other", "Human"))
    c.commit()
    c.close()
    R.clear_caches()
    return store.open_ro(path)


def fresh(kb, gk, p):
    return R.Route(kb, gk, p).rxp_to_idx


def check_routes(kb, gk, a, b, label):
    """get_route for a then b (and b then a) in one process gives each its own numbering."""
    want_a, want_b = fresh(kb, gk, a), fresh(kb, gk, b)
    assert want_a != want_b, "%s: the two settings must number differently for this test to mean anything" % label
    for first, second in ((a, b), (b, a)):
        R.clear_caches()
        r1, r2 = R.get_route(kb, gk, first), R.get_route(kb, gk, second)
        assert r1 is not r2, "%s: one cached route served both settings" % label
        assert r1.rxp_to_idx == fresh(kb, gk, first) and r2.rxp_to_idx == fresh(kb, gk, second), label


def check_pack(kb, summary, a_set, b_set, gk, label):
    """KnowledgePack.context/block for two players differing only in RestedXP settings, both orders."""
    n = summary["step"]
    want = {k: R.Route(kb, gk, dict(BASE, race=summary["race"], **v)).rxp_to_idx.get(n)
            for k, v in (("a", a_set), ("b", b_set))}
    assert want["a"] != want["b"], "%s: live step %s means the same idx either way" % (label, n)
    for order in (("a", "b"), ("b", "a")):
        R.clear_caches()
        pack = api.KnowledgePack(kb)
        for k in order:
            ctx = pack.context(dict(summary, rxp_settings=a_set if k == "a" else b_set), coach_flags={})
            assert ctx.error is None, (label, k, ctx.error)
            assert ctx.idx == want[k], "%s %s after %s: idx %s, want %s" % (label, k, order[0], ctx.idx, want[k])
            blk = pack.block(ctx)
            assert "error" not in (blk.get("step") or {}), (label, k, blk.get("step"))
    return want


# ---------------------------------------------------------------- synthetic guide
kb = synthetic_kb()
check_routes(kb, GK, dict(BASE, ssf=True), dict(BASE, ssf=False), "synthetic ssf")
check_routes(kb, GK, dict(BASE, ssf=True, group_quests=True), dict(BASE, ssf=True, group_quests=False),
             "synthetic group")
assert R.settings_key({"group_quests": False}) != R.settings_key({}), "unset group quests is not 'off'"
summ = {"class": "WARRIOR", "race": "Human", "faction": "Alliance", "guide": "1-5 Testland", "guide_group": GRP,
        "step": 4, "level": 3, "xp": 100, "xp_max": 1000}
w = check_pack(kb, summ, {"ssf": True, "group_quests": True}, {"ssf": False, "group_quests": True}, GK, "pack ssf")
assert w == {"a": 5, "b": 6}, w
check_pack(kb, summ, {"ssf": True, "group_quests": True}, {"ssf": True, "group_quests": False}, GK, "pack group")

# stored briefs: served only to players with the build's settings (SSF off, group quests off), computed otherwise
off, on = dict(BASE, ssf=False), dict(BASE, ssf=True)
b = R.why(kb, GK, 5, off)
assert b and b["xp_per_min"] == 999.0 and b["rxp_step_no"] == 2, b            # the stored one
b = R.why(kb, GK, 5, on)
assert b and b["xp_per_min"] != 999.0 and b["rxp_step_no"] == 3, b            # computed for SSF on
assert R.why(kb, GK, 3, on) is None, "an AH step SSF hides must not get a brief"
assert R.step_xp_per_min(kb, GK, 5, "WARRIOR", on) != 999.0
# a build recorded with SSF on: the SSF player gets the stored briefs, an SSF-off player never a hidden step's
kb2 = synthetic_kb(meta_rxp={"hardcore": False, "ssf": True})
b = R.why(kb2, GK, 2, on)
assert b and b["xp_per_min"] == 999.0 and b["rxp_step_no"] == 2, b
assert R.why(kb2, GK, 2, off) is None, "stored brief served for a step the player's RestedXP hides"
print("synthetic guide: ok")

# ---------------------------------------------------------------- the real knowledge base (read-only)
kb_path = os.path.join(ROOT, "data", "kb.db")
real = store.open_ro(kb_path) if os.path.exists(kb_path) else None
if real is None or not real.one("SELECT key FROM rxp_guide WHERE key=?", "RestedXP Forever Guide (A)||6-11 Elwynn Forest"):
    skipped = "the real-KB part (no data/kb.db with the Elwynn guide); the synthetic part passed"
else:
    skipped = None
    R.clear_caches()
    EL = "RestedXP Forever Guide (A)||6-11 Elwynn Forest"
    check_routes(real, EL, dict(BASE, ssf=True, group_quests=True), dict(BASE, ssf=False, group_quests=True),
                 "Elwynn ssf")
    s = {"class": "WARRIOR", "race": "Human", "faction": "Alliance", "guide": "06-11 Elwynn Forest",
         "guide_group": "RestedXP Forever Guide (A)", "step": 218, "level": 8, "xp": 100, "xp_max": 1000}
    w = check_pack(real, s, {"ssf": True, "group_quests": True}, {"ssf": False, "group_quests": True}, EL,
                   "Elwynn pack")
    assert w["b"] == 356, w
    RR = "RestedXP Forever Guide (A)||19-20 Redridge"
    rg = R.Route(real, RR, dict(BASE, ssf=True, group_quests=True))
    gidx = [st["idx"] for st in rg.steps if st["flags"].get("group")]
    if gidx:
        s2 = dict(s, guide="19-20 Redridge", step=rg.idx_to_rxp[gidx[0]], level=19)
        check_pack(real, s2, {"ssf": True, "group_quests": True}, {"ssf": True, "group_quests": False}, RR,
                   "Redridge group")
    print("real kb.db: ok")
print("rxp settings cache: ok")
if skipped:
    print("SKIP: " + skipped)
    raise SystemExit(77)
