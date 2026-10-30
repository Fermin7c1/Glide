#!/usr/bin/env python3
"""Smoke (report-only, see tests/kb/smoke.py): knowledge/mechanics/fit on the player's own recordings
(data/glide.db, data/live): what the real kills say about Forever's formulas. tests/kb/test_mechanics.py checks
the fit on the fixture recordings.

    py -3 tests/kb/smoke_mechanics.py
"""
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import smoke  # noqa: E402

sys.path.insert(0, smoke.ROOT)
from knowledge.mechanics import fit, params  # noqa: E402


def test_fit():
    smoke.need(smoke.GLIDE)
    db = smoke.GLIDE
    conn = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
    before = params.overrides()
    rep = fit.run(conn, write=False)
    assert params.overrides() == before          # the fit uses scoped parameters only
    k = rep["kill_xp"]
    assert k["n_kills"] >= 300, k["n_kills"]
    assert k["classic_agreement"] is not None and 0.0 <= k["classic_agreement"] <= 1.0
    ids = {h["id"] for h in k["hypotheses"]}
    assert {"classic", "classic_rested", "classic_ml+1", "classic_flat", "classic_zdfit", "forever"} <= ids
    assert k["best"] == "forever" and k["best_rate"] >= 0.95
    assert k["best_rate"] > k["classic_agreement"] + 0.3
    assert k["anchors_l14"]["fit"]["mobbase_rested"] == 3 and k["anchors_l14"]["fit"]["classic"] == 0
    assert k["zd"]["verified_through"] >= 9
    assert rep["level_xp"]["rows"] and 11 in rep["level_xp"]["verified_levels"]
    assert rep["quest_xp"]["vs_community"]["fg"]["n"] >= 50
    assert rep["speed"]["trail"]["n_segments"] > 100
    assert rep["ttk"]["by_class"]
    json.dumps(rep, default=str)
    ov = rep["overrides"]
    assert ov["xp.kill.low_base"]["value"] == "mob" and ov["xp.kill.rounding"]["value"] == "half_even"


if __name__ == "__main__":
    smoke.run("mechanics", [("kill XP, levels, quest XP, speed and TTK fits on data/glide.db", test_fit)])
