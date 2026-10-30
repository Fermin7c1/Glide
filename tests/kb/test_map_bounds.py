#!/usr/bin/env python3
"""map_bounds loader (R28): a map with an exact bound (recorder SavedVariables, conf 1) is not fitted, since a fit
(conf <= 0.95) could never replace it; a map without one still is. GLIDE_MAP_FIT_ALL=1 fits both for the statistics
(meta map_bounds:fit, with the error against the exact bound) and still stores only the exact bound. Synthetic
glide.db / recorder file in a temp folder; no config.json, so no live client files.

    python3 tests/kb/test_map_bounds.py
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from knowledge import store  # noqa: E402
from knowledge.sources import map_bounds  # noqa: E402

EXACT, FITTED = 1429, 1426          # Elwynn (recorder bound), Dun Morogh (fitted only)
# map % = a + b*wy (x), c + d*wx (y): the transform the synthetic guide coordinates follow
TRUTH = {EXACT: (40.0, -0.03, 45.0, -0.04), FITTED: (50.0, -0.02, 55.0, -0.03)}


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def make(tmp):
    """glide.db with world gotos + targets, a recorder SV with EXACT's bound, a kb with creatures and spawns."""
    data = os.path.join(tmp, "data")
    os.makedirs(os.path.join(data, "archive", "sv"))
    g = sqlite3.connect(os.path.join(data, "glide.db"))
    g.execute("CREATE TABLE rxp_steps(guide_key TEXT, step INT, line INT, verb TEXT, args TEXT)")
    kb = sqlite3.connect(":memory:")
    store.create_schema(kb)
    cid = 0
    for um, (a, b, c, d) in TRUTH.items():
        kb.execute("INSERT INTO map(ui_map, continent) VALUES(?, 0)", (um,))
        for i in range(12):
            cid += 1
            wy, wx = -300.0 * (i % 4) - 50 * i, -250.0 * (i // 4) - 40 * i
            x, y = a + b * wy, c + d * wx
            kb.execute("INSERT INTO creature(id, name) VALUES(?, ?)", (cid, "Npc %d" % cid))
            kb.execute("INSERT INTO creature_spawn(creature_id, ui_map, x, y, src, conf) VALUES(?,?,?,?,'wdb',1)",
                       (cid, um, x, y))
            g.execute("INSERT INTO rxp_steps VALUES('g', ?, 1, 'goto', ?)", (cid, "%d/0,%s,%s" % (um, wy, wx)))
            g.execute("INSERT INTO rxp_steps VALUES('g', ?, 2, 'target', ?)", (cid, "Npc %d" % cid))
    g.commit()
    g.close()
    a, b, c, d = TRUTH[EXACT]
    exact = (0, -c / d, -a / b, (100.0 - c) / d, (100.0 - a) / b)       # instance, wx0, wy0, wx1, wy1
    with open(os.path.join(data, "archive", "sv", "Glide_test.lua"), "w", encoding="utf-8") as fh:
        fh.write('GlideRecorderDB = {\n["mapBounds"] = {\n[%d] = {%s},\n},\n}\n' % (EXACT, ", ".join(map(str, exact))))
    return kb, store.BuildContext(root=tmp, data=data, log=lambda *a: None), exact


def run(tmp, fit_all):
    kb, ctx, exact = make(tmp)
    calls = []
    real_fit = map_bounds.fit

    def counting(P, *a, **k):
        calls.append(P[0])
        return real_fit(P, *a, **k)

    map_bounds.fit = counting
    old = os.environ.pop("GLIDE_MAP_FIT_ALL", None)
    if fit_all:
        os.environ["GLIDE_MAP_FIT_ALL"] = "1"
    try:
        res = map_bounds.load(kb, ctx)
    finally:
        map_bounds.fit = real_fit
        os.environ.pop("GLIDE_MAP_FIT_ALL", None)
        if old is not None:
            os.environ["GLIDE_MAP_FIT_ALL"] = old
    rows = {r[0]: r[1:] for r in kb.execute("SELECT ui_map, wx0, wy0, wx1, wy1, src, conf FROM map_bounds")}
    stats = json.loads(kb.execute("SELECT value FROM meta WHERE key='map_bounds:fit'").fetchone()[0])
    return res, rows, stats, exact, len(calls)


def main():
    tmp = tempfile.mkdtemp(prefix="glide_mb_")
    try:
        res, rows, stats, exact, n = run(os.path.join(tmp, "a"), fit_all=False)
        check(n == 1, "only the map without an exact bound is fitted, got %d fits" % n)
        check(rows[EXACT][4:] == ("obs", 1.0) and all(abs(v - e) < 1e-6 for v, e in zip(rows[EXACT], exact[1:])),
              "exact bound stored as recorded %r" % (rows[EXACT],))
        check(rows[FITTED][4] == "fit" and rows[FITTED][5] < 1, "the other map is fitted %r" % (rows[FITTED],))
        check(stats[str(EXACT)].get("exact") == "obs" and stats[str(EXACT)].get("fit", 0) is None,
              "stats say why EXACT was not fitted %r" % stats[str(EXACT)])
        check(res["fitted_maps"] == 1 and res["exact_maps_not_fitted"] == 1, "summary %r" % res)

        res2, rows2, stats2, _, n2 = run(os.path.join(tmp, "b"), fit_all=True)
        check(n2 == 2, "GLIDE_MAP_FIT_ALL fits both maps, got %d" % n2)
        check(rows2 == rows, "the validation fit changes no stored bound")
        s = stats2[str(EXACT)]
        check(s.get("exact") == "obs" and s.get("conf") and s.get("exact_err_pct") is not None
              and s["exact_err_pct"] < 0.5, "validation statistics for EXACT %r" % s)
        check(res2["fitted_maps"] == 1, "the validation fit is not counted as a fitted map %r" % res2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    main()
