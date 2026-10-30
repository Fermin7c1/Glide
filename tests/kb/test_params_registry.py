"""One parameter registry, read from the KB in use (R30): mechanics and analyzer parameters register in one place
(mechanics.params, lanes "mechanics" / "analyzer"); at runtime a KB's values (its param table, built from
params.json and the fitted values) and its heuristic rules are the ones in force, and opening another KB replaces
them: two KBs with different fitted values in one process never leak into each other.

Run: python tests/kb/test_params_registry.py   (prints OK)
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)

from knowledge import store  # noqa: E402
from knowledge.analyzer import params as AP  # noqa: E402
from knowledge.analyzer.kbdata import get_data  # noqa: E402
from knowledge.api import KnowledgePack  # noqa: E402
from knowledge.mechanics import params as MP  # noqa: E402

KEY = "analyzer.kill.search_s"
MKEY = "travel.run_speed"


def make_kb(path, params=None, meta_fitted=None, rules=()):
    """A kb.db with every table and the given param rows ({key: (value, status, evidence)}), meta
    'analyzer_params' and heuristic rules."""
    c = store.open_rw(path)
    store.create_schema(c)
    store.set_meta(c, "built", os.path.basename(path))
    for k, (v, st, ev) in (params or {}).items():
        c.execute("INSERT INTO param(key, value, unit, classic_value, status, evidence, updated) VALUES(?,?,?,?,?,?,?)",
                  (k, json.dumps(v), "s", json.dumps(v), st, ev, "2026-10-26"))
    if meta_fitted is not None:
        store.set_meta(c, "analyzer_params", meta_fitted)
    for r in rules:
        c.execute("INSERT INTO heuristic(id, title, applies, advice, rationale, tags, source, conf, verified, priority) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)", (r, "t " + r, json.dumps({"level": {">=": 1}}), "do " + r, "because",
                                                  json.dumps(["pace"]), json.dumps(["test"]), 0.9, 0, 2))
    c.commit()
    c.close()
    return store.open_ro(path)


tmp = tempfile.mkdtemp(prefix="glide_params_")
try:
    # ---- one registry, two lanes ----
    assert MP.lane(KEY) == "analyzer" and MP.lane(MKEY) == "mechanics"
    assert KEY in AP.keys() and KEY not in MP.keys() and MKEY in MP.keys() and MKEY not in AP.keys()
    assert all(r["key"].startswith("analyzer.") for r in AP.all())
    assert AP.get(MKEY) == MP.get(MKEY) and MP.get(KEY) == AP.get(KEY), "every key answers through either view"
    MP.use_kb(None)
    mech_before = MP.overrides()
    AP.apply({KEY: {"value": 1.5, "status": "forever-fitted", "evidence": "build"}, MKEY: 99})
    assert AP.get(KEY) == 1.5 and MP.get(MKEY) != 99, "the analyzer's apply() only touches its own lane"
    AP.reset()
    assert MP.overrides() == mech_before, "resetting the analyzer keeps params.json's mechanics overrides"
    default = MP.info(KEY)["value"]
    assert AP.get(KEY) == default

    # ---- two KBs, different fitted values, one process ----
    fit = lambda v: {KEY: (v, "forever-fitted", "fit %s" % v)}  # noqa: E731
    kb_a = make_kb(os.path.join(tmp, "a.db"), fit(3.7), rules=("a.one", "a.two"))
    kb_b = make_kb(os.path.join(tmp, "b.db"), fit(9.9), rules=("b.one",))
    kb_c = make_kb(os.path.join(tmp, "c.db"), {KEY: (default, MP.info(KEY)["status"], MP.info(KEY)["evidence"])})
    kb_old = make_kb(os.path.join(tmp, "old.db"), meta_fitted={KEY: {"value": 5.5, "status": "forever-fitted"}})
    get_data(kb_a)
    assert AP.get(KEY) == 3.7 and AP.status(KEY) == "forever-fitted" and MP.current_kb() == kb_a.path
    assert MP.info(KEY)["evidence"] == "fit 3.7" and MP.info(KEY)["prior_evidence"]
    get_data(kb_b)
    assert AP.get(KEY) == 9.9, "the second KB's value"
    get_data(kb_a)
    assert AP.get(KEY) == 3.7, "back on the first KB: its own value again"
    get_data(kb_c)
    assert AP.get(KEY) == default and "prior_evidence" not in MP.info(KEY), "a KB at the default: no leak from b"
    get_data(kb_old)
    assert AP.get(KEY) == 5.5, "a KB built before the analyzer rows went into param: its meta analyzer_params"
    get_data(kb_b)
    with MP.scoped({KEY: 2.0}):
        assert AP.get(KEY) == 2.0, "a scoped value (fits, tests) still wins"
    assert AP.get(KEY) == 9.9

    # ---- heuristic rules come from the KB too ----
    pa, pb = KnowledgePack(kb_a), KnowledgePack(kb_b)
    assert [r["id"] for r in pa.rules()] == ["a.one", "a.two"] and [r["id"] for r in pb.rules()] == ["b.one"]
    assert pa.fact_exists("h:a.one") and not pb.fact_exists("h:a.one"), "h: facts are the KB's rules"
    assert len(KnowledgePack(kb_c).rules()) > 0, "a KB without rules falls back to rules.json"
    print("OK")
finally:
    MP.use_kb(None)
    shutil.rmtree(tmp, ignore_errors=True)
