#!/usr/bin/env python3
"""Smoke (report-only, see tests/kb/smoke.py): kb.db built from the player's real data into a temp file, and the
sizes and fits the real RestedXP guides, recordings and client data should give. tests/kb/test_store.py checks
the same loaders on the fixture corpus.

    py -3 tests/kb/smoke_store.py
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import smoke  # noqa: E402

sys.path.insert(0, smoke.ROOT)
from knowledge import build_kb, store  # noqa: E402
from knowledge.sources import _lua  # noqa: E402

CLASSIC_ZONES = set(range(1411, 1459))      # Classic zone and city UiMaps
state = {}


def built():
    """kb.db from the real inputs, into a temp folder (never data/)."""
    if "out" not in state:
        smoke.need(smoke.GLIDE)
        if not store.BuildContext().rxp_available("DB"):
            raise smoke.NotRun("no RestedXP DB files (reference/rxp, or the addon in config.json's wow_dir)")
        state["tmp"] = tempfile.mkdtemp(prefix="glide_kb_smoke_")
        out = os.path.join(state["tmp"], "kb.db")
        t0 = time.time()
        os.environ["GLIDE_MAP_FIT_ALL"] = "1"      # map_fits: also fit the maps with exact bounds (statistics only)
        try:
            state["summary"] = build_kb.build(out, quiet=True)
        finally:
            os.environ.pop("GLIDE_MAP_FIT_ALL", None)
        state["dt"] = time.time() - t0
        state["out"] = out
    return state["out"]


def n(sql, *a):
    c = sqlite3.connect(built())
    try:
        return c.execute(sql, a).fetchone()[0]
    finally:
        c.close()


def build_ok():
    built()
    print("    build: %.1fs, loader errors: %s" % (state["dt"], state["summary"]["errors"] or "none"))
    assert state["dt"] < 90, "build took %.1fs (limit 90s)" % state["dt"]
    assert state["summary"]["written"] == state["out"], "a store loader failed: %s" % state["summary"]["errors"]


def sizes():
    want = {"quest": ("SELECT COUNT(*) FROM quest", 7000), "quest_with_xp": ("SELECT COUNT(*) FROM quest WHERE xp IS NOT NULL", 4000),
            "creature": ("SELECT COUNT(*) FROM creature", 9000), "creature_spawn": ("SELECT COUNT(*) FROM creature_spawn", 60000),
            "quest_giver": ("SELECT COUNT(*) FROM quest_giver", 5000), "obs_kill": ("SELECT COUNT(*) FROM obs_kill", 299),
            "quest_xp_obs": ("SELECT COUNT(*) FROM quest_xp_obs", 50),
            "graveyards": ("SELECT COUNT(*) FROM poi WHERE kind='graveyard'", 50),
            "creature_note": ("SELECT COUNT(*) FROM creature_note", 200), "travel_edge": ("SELECT COUNT(*) FROM travel_edge", 100),
            "trainer": ("SELECT COUNT(*) FROM trainer", 100),
            "obs_kill ttk": ("SELECT COUNT(*) FROM obs_kill WHERE ttk_s IS NOT NULL", 250),
            "map sizes": ("SELECT COUNT(*) FROM map WHERE width_yd > 0", 44)}
    got = {k: n(sql) for k, (sql, _) in want.items()}
    print("    counts:", json.dumps(got))
    low = ["%s %d (expected > %d)" % (k, got[k], lo) for k, (_, lo) in want.items() if got[k] <= lo]
    assert not low, "; ".join(low)


def flight_paths():
    built()
    fpdb = _lua.read_file(store.BuildContext().rxp("DB", "forever", "flightData.lua"))["addon.FPDB"]
    edges = sum(1 for t in fpdb.values() for e in t.values() if isinstance(e, dict) for k in e if isinstance(k, int))
    rxp_paths = n("SELECT COUNT(*) FROM flight_path WHERE src LIKE '%rxp%'")
    print("    flight_path: %d from RXP, FPDB has %d edges" % (rxp_paths, edges))
    assert rxp_paths == edges and n("SELECT COUNT(*) FROM flight_path") > 500, "flight_path vs FPDB"


def map_fits():
    fit = json.loads(n("SELECT value FROM meta WHERE key='map_bounds:fit'"))
    c = sqlite3.connect(built())
    names = dict(c.execute("SELECT ui_map, name FROM map"))
    in_kb = {r[0] for r in c.execute("SELECT ui_map FROM map_bounds WHERE src LIKE '%fit%'")}
    c.close()
    good = []
    for um, s in sorted(fit.items(), key=lambda kv: int(kv[0])):
        if s.get("conf") is None:
            continue
        print("    %5s %-22s pairs %3d inliers %3d rms %.3f max %.3f %s%s" % (
            um, (names.get(int(um)) or "")[:22], s["n"], s["inliers"], s["rms"], s["max"], s["scale"],
            "  vs exact (%s) %.2f%%" % (s["exact"], s["exact_err_pct"]) if s.get("exact") else ""))
        # a fit in the KB, or (GLIDE_MAP_FIT_ALL) one that agrees with the map's exact bound within 1 percent
        if int(um) in CLASSIC_ZONES and s["rms"] < 3 and s["max"] <= 3 and (
                int(um) in in_kb or (s.get("exact") and s["exact_err_pct"] <= 1.0)):
            good.append(int(um))
    assert len(good) >= 15, "fitted Classic maps under 3%%: %d" % len(good)


def sten_stoutarm():
    c = sqlite3.connect(built())
    st = c.execute("""SELECT x, y FROM rxp_step WHERE guide_key LIKE '%Forever Guide (A)||1-5 Coldridge Valley'
                      AND wx IS NOT NULL AND x IS NOT NULL ORDER BY idx LIMIT 1""").fetchone()
    c.close()
    if not st:
        raise smoke.NotRun("no world-form goto in 1-5 Coldridge Valley")
    assert abs(st[0] - 29.9) < 1 and abs(st[1] - 71.2) < 1, "world goto -> map %r" % (st,)


def report_default():
    import subprocess
    smoke.need(smoke.KB)
    p = subprocess.run([sys.executable, os.path.join(smoke.ROOT, "tools", "kb_report.py"), "--json"],
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0 and json.loads(p.stdout)["counts"].get("quest", 0) > 0, "kb_report on data/kb.db"


if __name__ == "__main__":
    try:
        smoke.run("store", [("build from the real inputs", build_ok), ("table sizes", sizes),
                            ("flight paths = RestedXP FPDB", flight_paths), ("map bounds fits", map_fits),
                            ("Coldridge world goto", sten_stoutarm), ("kb_report on data/kb.db", report_default)])
    finally:
        if state.get("tmp"):
            shutil.rmtree(state["tmp"], ignore_errors=True)
