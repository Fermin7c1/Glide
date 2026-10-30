#!/usr/bin/env python3
"""Store lane: build kb.db from the fixture corpus (tests/kb/fixture.py) into a temp folder and check what every
loader produced. Also: the --if-stale decision (temp files), the RestedXP fallback to the installed addon (temp
config.json), the kb.new.db path when kb.db is in use, and tools/kb_report.py --json on the KB built here.
The same build from the player's real data is the report-only tests/kb/smoke_store.py.

    python3 tests/kb/test_store.py          (plain python, prints OK)
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import fixture  # noqa: E402
from knowledge import build_kb, store  # noqa: E402
from knowledge.sources import _lua, rxp_db  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_lua_reader():
    src = """local _, addon = ...
if addon.game ~= "FOREVER" then return end
--[[ addon.ignored = {1, 2} ]]
addon.T = { [1] = 5, ["name"] = "A, B", nested = {x = -1.5, 'two', L"loc"}, [10] = {wx = -1, wy = 2} } -- c
if locale == "enUS" then addon.R = {"en"} elseif locale == "deDE" then addon.R = {"de"} end
local f = { fn = function() return 1 end }
"""
    d = _lua.assignments(src)
    check(d["addon.T"][1] == 5 and d["addon.T"]["name"] == "A, B", "lua: keys")
    check(d["addon.T"]["nested"] == {"x": -1.5, 1: "two", 2: "loc"}, "lua: nested %r" % d["addon.T"]["nested"])
    check(d["addon.R"] == ["en"], "lua: first (enUS) branch wins")
    check("addon.ignored" not in d, "lua: block comment skipped")
    check(d["__errors__"] and d["__errors__"][0][0] == "f", "lua: function table reported, not guessed")


def test_coords():
    b = {"wx0": -7939.58, "wy0": 1535.42, "wx1": -10254.16, "wy1": -1935.41}    # Elwynn Forest (client values)
    x, y = store.world_to_map(b, -8949.95, -132.493)                              # Northshire Abbey area
    check(40 < x < 50 and 40 < y < 50, "world_to_map %r" % ((x, y),))
    wx, wy = store.map_to_world(b, x, y)
    check(abs(wx + 8949.95) < 1e-6 and abs(wy + 132.493) < 1e-6, "map_to_world round trip")
    check(store.map_size_yd(b) == (abs(b["wy1"] - b["wy0"]), abs(b["wx1"] - b["wx0"])), "map size")


def test_db2_import(kb_path, tmpdir):
    """The optional DB2 importer on synthetic CSVs (in a scratch copy: never in the real KB)."""
    from wowfiles import db2_import
    folder = os.path.join(tmpdir, "db2")
    os.makedirs(folder)
    files = {
        "UiMapAssignment.csv": "UiMapID,MapID,OrderIndex,UiMin[0],UiMin[1],UiMax[0],UiMax[1],Region[0],Region[1],"
                               "Region[2],Region[3],Region[4],Region[5]\n"
                               "99999,0,0,0,0,1,1,-10254.16,-1935.41,0,-7939.58,1535.42,100\n",
        "QuestXP.csv": "ID," + ",".join("Difficulty_%d" % i for i in range(10)) + "\n99," + ",".join(str(i * 10) for i in range(10)) + "\n",
        "TaxiNodes.csv": "ID,Name_lang,Pos_0,Pos_1,Pos_2,ContinentID,Flags\n9001,Test A,0,0,0,0,1\n9002,Test B,300,400,0,0,3\n",
        "TaxiPath.csv": "ID,FromTaxiNode,ToTaxiNode,Cost\n7001,9001,9002,120\n",
        "TaxiPathNode.csv": "ID,PathID,NodeIndex,ContinentID,Loc_0,Loc_1,Loc_2,Flags,Delay\n1,7001,0,0,0,0,0,0,0\n2,7001,1,0,300,400,0,0,0\n",
        "UiMap.csv": "ID,Name_lang,ParentUiMapID,Type\n99999,Test Map,1415,3\n",
    }
    for name, text in files.items():
        with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    scratch = os.path.join(tmpdir, "kb_db2.db")
    shutil.copyfile(kb_path, scratch)
    c = sqlite3.connect(scratch)
    res = db2_import.import_dir(c, folder)
    c.commit()
    b = c.execute("SELECT continent, wx0, wy0, wx1, wy1, src FROM map_bounds WHERE ui_map=99999").fetchone()
    check(b == (0, -7939.58, 1535.42, -10254.16, -1935.41, "db2"), "UiMapAssignment bounds %r" % (b,))
    check(c.execute("SELECT d5 FROM quest_xp_table WHERE level=99").fetchone()[0] == 50, "QuestXP")
    fp = c.execute("SELECT faction, cost, time_s FROM flight_path WHERE from_id=9001 AND to_id=9002").fetchall()
    check(fp == [("Alliance", 120, None)], "TaxiPath (no RXP time to fit a speed against -> no time) %r" % fp)
    check(c.execute("SELECT name FROM map WHERE ui_map=99999").fetchone()[0] == "Test Map", "UiMap")
    c.close()
    return res


def test_sv_bounds(kb_path, tmpdir):
    """Exact bounds from a recorder SavedVariables file (future addon feature), on a scratch copy."""
    from knowledge.sources import map_bounds
    data = os.path.join(tmpdir, "data")
    os.makedirs(os.path.join(data, "archive", "sv"))
    with open(os.path.join(data, "archive", "sv", "Glide_test.lua"), "w", encoding="utf-8") as fh:
        fh.write('GlideRecorderDB = {\n["mapBounds"] = {\n[2521] = {2991, 1200.5, 2400.25, -800.5, -600.75},\n'
                 '[1429] = {["instance"] = 0, ["x0"] = -7939.58, ["y0"] = 1535.42, ["x1"] = -10254.16, ["y1"] = -1935.41},\n'
                 '},\n["sessions"] = {},\n}\n')
    scratch = os.path.join(tmpdir, "kb_sv.db")
    shutil.copyfile(kb_path, scratch)
    c = sqlite3.connect(scratch)
    ctx = store.BuildContext(root=tmpdir, data=data, log=lambda *a: None)   # no config.json: no live client SVs
    res = map_bounds.sv_bounds(c, ctx)
    check(res.get("rows") == 2, "sv mapBounds %r" % res)
    r = c.execute("SELECT continent, wx0, wy0, wx1, wy1, src, conf FROM map_bounds WHERE ui_map=2521").fetchone()
    check(r == (2991, 1200.5, 2400.25, -800.5, -600.75, "obs", 1.0), "obs bounds 2521 %r" % (r,))
    r = c.execute("SELECT wx0, wy0, src FROM map_bounds WHERE ui_map=1429").fetchone()
    check(r == (-7939.58, 1535.42, "obs"), "obs bounds override the fit %r" % (r,))
    c.close()


def test_staleness(tmpdir):
    """build_kb.collect_inputs / staleness on a temp project tree: missing, fresh, newer, changed, gone, FAT
    rounding, a KB without recorded inputs, partial build, no build record, unreadable file."""
    root = os.path.join(tmpdir, "stale_root")
    t0 = time.time() - 10000

    def put(rel, t, text="x"):
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.utime(p, (t, t))
        return p

    rels = ["data/glide.db", "data/params.json", "knowledge/heuristics/rules.json", "knowledge/glossary.json",
            "knowledge/schema.sql", "knowledge/store.py", "knowledge/sources/x.py", "data/live/20261001-100000.jsonl",
            "data/live/20261002-100000.jsonl", "data/archive/sv/Glide_live_1.lua",
            "knowledge/community/intel/2026-10-08.jsonl", "knowledge/community/sources.json", "data/db2/UiMap.csv",
            "wowfiles/wdb.py"]
    for i, rel in enumerate(rels):
        put(rel, t0 + i)
    put("data/live/overlay.json", t0 + 99)                       # not an input pattern: ignored
    inputs = build_kb.collect_inputs(root)
    by = {i["key"]: i for i in inputs}
    check([i["key"] for i in inputs] == [k for k, _ in build_kb.STALE_INPUTS], "one input per STALE_INPUTS entry")
    check(by["code"]["path"] == "knowledge/sources/x.py" and by["code"]["n"] == 2, "newest code %r" % by["code"])
    check(by["live logs"]["path"] == "data/live/20261002-100000.jsonl" and by["live logs"]["n"] == 2,
          "newest live log %r" % by["live logs"])
    check(all(i["path"] and i["mtime"] for i in inputs), "every input found")

    kb = os.path.join(root, "data", "kb.db")

    def decide():
        d = build_kb.staleness(kb, build_kb.collect_inputs(root), root=root)
        return d, [(c["key"], c["why"]) for c in d["changes"]]

    d, _ = decide()
    check(d["stale"] and "does not exist" in d["reason"] and "does not exist" in build_kb.describe(d)[0],
          "missing kb.db %r" % d["reason"])

    def make_kb(t, items=inputs, args=None, built="2026-10-05 10:00:00"):
        if os.path.exists(kb):
            os.remove(kb)
        c = sqlite3.connect(kb)
        c.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
        if built:
            c.execute("INSERT INTO meta VALUES('built', ?)", (built,))
        if items is not None:
            c.execute("INSERT INTO meta VALUES('inputs', ?)", (json.dumps({"root": root, "items": items}),))
        c.execute("INSERT INTO meta VALUES('build_args', ?)", (json.dumps(args or {"only": None, "skip": None}),))
        c.commit()
        c.close()
        os.utime(kb, (t, t))

    make_kb(t0 + 100)
    d, ch = decide()
    check(not d["stale"] and not ch, "fresh KB judged stale: %r" % d["reason"])
    check("up to date" in build_kb.describe(d)[0], "describe fresh %r" % build_kb.describe(d))
    put("knowledge/glossary.json", t0 + 200)                     # copied again after the build, same bytes
    check(decide()[1] == [], "hashed input with the same content judged changed %r" % decide()[1])
    put("knowledge/glossary.json", t0 + 200, "y")                # edited after the build
    d, ch = decide()
    check(d["stale"] and ch == [("glossary.json", "newer")], "newer input %r" % ch)
    check("knowledge/glossary.json" in "\n".join(build_kb.describe(d)), "the reason names the input")
    put("knowledge/glossary.json", t0 + 3)
    put("data/glide.db", t0 + 50)                                # another copy, older than kb.db (timestamp kept)
    check(decide()[1] == [("glide.db", "changed")], "copy with an older timestamp %r" % decide()[1])
    put("data/glide.db", t0 + 1.5)                               # FAT/exFAT rounding is not a change
    check(decide()[1] == [], "mtime within MTIME_SLACK %r" % decide()[1])
    put("data/glide.db", t0)
    p = put("data/live/20261003-100000.jsonl", t0 + 60)          # a newer log copied in with an old timestamp
    d, ch = decide()
    check(ch == [("live logs", "changed")] and d["changes"][0]["was_path"] == "data/live/20261002-100000.jsonl",
          "new newest log %r" % d["changes"])
    os.remove(p)
    os.remove(os.path.join(root, "data", "params.json"))
    check(decide()[1] == [("params.json", "gone")], "removed input %r" % decide()[1])
    put("data/params.json", t0 + 1)
    check(decide()[1] == [], "back to the recorded inputs")

    make_kb(t0 + 100, items=None)                                # built before inputs were recorded: mtimes only
    put("data/glide.db", t0 + 50)
    check(not decide()[0]["stale"], "no record: an older input is not a change")
    put("data/glide.db", t0 + 150)
    check(decide()[1] == [("glide.db", "newer")], "no record: newer input")
    put("data/glide.db", t0)
    make_kb(t0 + 100, args={"only": ["rxp_db"], "skip": None})
    d = decide()[0]
    check(d["stale"] and "partial build (--only rxp_db)" in d["reason"], "partial build %r" % d["reason"])
    make_kb(t0 + 100, built=None)
    check("no build record" in (decide()[0]["reason"] or ""), "no build record")
    with open(kb, "w", encoding="utf-8") as fh:
        fh.write("not a database\n" * 200)
    d = decide()[0]
    check(d["stale"] and d["reason"].startswith("cannot read"), "unreadable kb.db %r" % d["reason"])


def test_rxp_fallback(tmpdir, fxroot):
    """BuildContext.rxp(): reference/rxp first, else <wow_dir>/Interface/AddOns/RXPGuides (config.json wow_dir),
    per file; and rxp_db reads the installed addon exactly like the repo copy (the player's PC has no reference/).
    The repo copy here is the fixture root's reference/rxp."""
    root = os.path.join(tmpdir, "pc")
    os.makedirs(root)
    wow = os.path.join(tmpdir, "World of Warcraft", "_classic_beta_")
    addon = os.path.join(wow, "Interface", "AddOns", "RXPGuides")
    repo = store.BuildContext(root=fxroot, log=lambda *a: None)
    shutil.copytree(repo.rxp("DB"), os.path.join(addon, "DB"), copy_function=shutil.copyfile)
    ctx = store.BuildContext(root=root, log=lambda *a: None)
    ref = os.path.join(root, "reference", "rxp")
    check(ctx.wow_dir() is None and not ctx.rxp_available() and ctx.rxp_source() == (None, None),
          "no config.json: no RestedXP source")
    check(ctx.rxp("DB") == os.path.join(ref, "DB"), "nothing found -> the (missing) reference path")
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"wow_dir": wow}, fh)
    check(ctx.wow_dir() == wow and ctx.rxp_source() == ("addon", addon), "addon source %r" % (ctx.rxp_source(),))
    fd = os.path.join(addon, "DB", "forever", "flightData.lua")
    check(ctx.rxp_available("DB", "forever", "flightData.lua") and ctx.rxp("DB", "forever", "flightData.lua") == fd,
          "addon file %r" % ctx.rxp("DB", "forever", "flightData.lua"))
    check(not ctx.rxp_available("DB", "nope.lua") and ctx.rxp("DB", "nope.lua") == os.path.join(ref, "DB", "nope.lua"),
          "missing everywhere -> reference path")

    def run(c):
        conn = sqlite3.connect(":memory:")
        store.create_schema(conn)
        res = rxp_db.load(conn, c)
        conn.close()
        return res

    via_addon, via_repo = run(ctx), run(repo)
    check(via_addon["from"] == os.path.join(addon, "DB") and via_repo["from"] == repo.rxp("DB"), "rxp_db from")
    check({k: v for k, v in via_addon.items() if k != "from"} == {k: v for k, v in via_repo.items() if k != "from"},
          "rxp_db via the addon differs from the repo copy")
    check(via_addon["flight"]["flight_path"] == 2, "flight paths via the addon %r" % via_addon["flight"])
    os.makedirs(os.path.join(ref, "DB", "forever"))               # a (partial) repo copy appears: it wins per file
    with open(os.path.join(ref, "DB", "forever", "db.lua"), "w", encoding="utf-8") as fh:
        fh.write("-- repo copy\n")
    check(ctx.rxp_source() == ("reference", ref), "reference first")
    check(ctx.rxp("DB", "forever", "db.lua") == os.path.join(ref, "DB", "forever", "db.lua"), "reference file wins")
    check(ctx.rxp("DB", "forever", "flightData.lua") == fd, "file only in the addon still resolves there")
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"wow_dir": os.path.join(tmpdir, "no such folder")}, fh)
    check(ctx.wow_dir() is None and [k for k, _ in ctx.rxp_roots()] == ["reference"], "wow_dir missing on disk")


def test_install_in_use(tmpdir):
    """Windows: os.replace over a kb.db the companion holds open raises PermissionError -> kb.new.db and a
    message; install_pending (the next --if-stale run) moves it over kb.db once the companion is closed."""
    folder = os.path.join(tmpdir, "inuse")
    os.makedirs(folder)
    target = os.path.join(folder, "kb.db")

    def write(p, text, t=None):
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        if t is not None:
            os.utime(p, (t, t))

    def read(p):
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    write(target, "old", time.time() - 100)
    tmp = os.path.join(folder, "build.tmp")
    write(tmp, "new")
    real_replace, real_sleep = os.replace, time.sleep

    def locked(src, dst):
        if os.path.abspath(dst) == os.path.abspath(target):
            raise PermissionError(13, "The process cannot access the file because it is being used", dst)
        return real_replace(src, dst)

    msgs = []
    os.replace, time.sleep = locked, (lambda s: None)
    try:
        written = build_kb._install(tmp, target, lambda *a: None, warn=msgs.append)
        still = build_kb.install_pending(target, lambda *a: None, warn=msgs.append)
    finally:
        os.replace, time.sleep = real_replace, real_sleep
    pend = build_kb.pending_path(target)
    check(written == pend == os.path.join(folder, "kb.new.db"), "in use -> kb.new.db, got %r" % written)
    check(read(target) == "old" and read(pend) == "new" and not os.path.exists(tmp), "files after a locked install")
    text = " ".join(msgs)
    check("in use" in text and "kb.new.db" in text and "start-glide.cmd" in text, "message %r" % msgs)
    check(still == "in use" and os.path.exists(pend), "still locked -> stays pending")
    check(build_kb.install_pending(target, lambda *a: None) == "installed", "companion closed -> installed")
    check(read(target) == "new" and not os.path.exists(pend), "kb.new.db moved over kb.db")
    write(pend, "stale", time.time() - 1000)                      # an obsolete leftover never replaces a newer kb.db
    check(build_kb.install_pending(target, lambda *a: None) == "older" and read(target) == "new"
          and not os.path.exists(pend), "older leftover removed")
    check(build_kb.install_pending(target, lambda *a: None) is None, "nothing pending")


def test_build_meta(kb_path, summary, root):
    """What build() records for --if-stale and the report: inputs, build_args, rxp_source."""
    c = sqlite3.connect(kb_path)
    meta = dict(c.execute("SELECT key, value FROM meta WHERE key IN ('inputs', 'build_args', 'rxp_source')"))
    c.close()
    inputs = json.loads(meta["inputs"])["items"]
    found = {i["key"] for i in inputs if i["path"]}                # the fixture root has data, not the code
    check([i["key"] for i in inputs] == [k for k, _ in build_kb.STALE_INPUTS] and found == {
        "glide.db", "live logs", "community intel", "community sources", "client map tables"}, "meta inputs %r" % inputs)
    check(json.loads(meta["build_args"]) == {"only": None, "skip": None, "force": False}, "meta build_args")
    rx = json.loads(meta["rxp_source"])
    check(rx["kind"] == "reference" and rx == summary["rxp_source"], "meta rxp_source %r" % rx)
    check(summary["installed"] and summary["loaders"]["rxp_db"]["from"] == store.BuildContext(root=root).rxp("DB"),
          "installed / rxp_db from")
    d = build_kb.staleness(kb_path, inputs, root=root)                      # judged against what it was built from: fresh
    check(not d["stale"], "fresh build judged stale against its own inputs: %r" % d["reason"])


def test_kb_report(kb_path, counts):
    """tools/kb_report.py --json on the KB built here (counts must match), the text form, and the default path."""
    script = os.path.join(ROOT, "tools", "kb_report.py")

    def run(*args):
        p = subprocess.run([sys.executable, script] + list(args), capture_output=True, text=True, timeout=300)
        check(p.returncode == 0, "kb_report %s failed: %s %s" % (" ".join(args), p.stdout[-500:], p.stderr[-2000:]))
        return p.stdout

    r = json.loads(run("--json", "--db", kb_path))
    for t in ("quest", "creature", "creature_spawn", "quest_giver", "obs_kill", "obs_session", "quest_xp_obs",
              "flight_path", "creature_note", "travel_edge", "trainer", "map_bounds"):
        check(r["counts"].get(t) == counts[t], "kb_report count %s: %r vs %r" % (t, r["counts"].get(t), counts[t]))
    cov = r["coverage"]
    check(cov["quests_with_xp"]["n"] == counts["quest_with_xp"] and cov["quests_with_xp"]["of"] == counts["quest"],
          "coverage quests_with_xp %r" % cov["quests_with_xp"])
    check(cov["creatures_with_spawns"]["of"] == counts["creature"] and 0 < cov["creatures_with_spawns"]["n"] <= counts["creature"],
          "coverage creatures")
    check(cov["guides_with_briefs"]["n"] == counts["guides"] and cov["maps_with_bounds"]["n"] == counts["map_bounds"],
          "coverage guides/maps %r %r" % (cov["guides_with_briefs"], cov["maps_with_bounds"]))
    check(r["build"]["rxp_source"]["kind"] == "reference" and r["build"]["errors"] == [] and r["build"]["inputs"],
          "report build section %r" % {k: v for k, v in r["build"].items() if k != "inputs"})
    p = r["params"]
    check(p["total"] == r["counts"]["param"] and p["by_status"].get("forever-verified") == len(p["forever_verified"]) > 0,
          "params %r" % p["by_status"])
    check(r["observations"]["counts"]["obs_kill"] == counts["obs_kill"], "observations")
    u = r["unknowns"]
    check(len(u["quests_without_xp"]["top"]) <= 10 and len(u["creatures_without_spawns"]["top"]) <= 10,
          "unknowns top lists")
    check(u["quests_without_xp"]["total"] == cov["routed_quests_with_xp"]["of"] - cov["routed_quests_with_xp"]["n"],
          "unknown quests = routed quests without xp")
    txt = run("--db", kb_path)
    check(all(s in txt for s in ("Tables (", "Loaders", "Parameters:", "Coverage", "Observations:", "Unknowns")),
          "text report sections")
    return r


def main():
    test_lua_reader()
    test_coords()
    tmpdir = fixture.tmpdir("store-")
    try:
        test_staleness(tmpdir)
        test_install_in_use(tmpdir)
        t0 = time.time()
        out, summary, root, run = fixture.build(os.path.join(tmpdir, "fixture"))
        dt = time.time() - t0
        test_rxp_fallback(tmpdir, root)
        print("staleness, RestedXP fallback, kb.new.db: ok")
        print("fixture build: %.1fs, loader errors: %s" % (dt, summary["errors"] or "none"))
        for name, res in summary["loaders"].items():
            print("  %-15s %6.2fs %s" % (name, res.get("_s", 0), "ERROR " + res["error"] if "error" in res else ""))
        check(dt < 90, "build took %.1fs (limit 90s)" % dt)
        check(summary["written"] == out and not summary["errors"], "a loader failed: %s" % summary["errors"])
        c = sqlite3.connect(out)

        def n(sql, *a):
            return c.execute(sql, a).fetchone()[0]

        counts = {t: n("SELECT COUNT(*) FROM %s" % t) for t in (
            "quest", "creature", "creature_spawn", "quest_giver", "obs_kill", "obs_session", "quest_xp_obs",
            "flight_path", "creature_note", "travel_edge", "trainer", "map_bounds")}
        counts.update(quest_with_xp=n("SELECT COUNT(*) FROM quest WHERE xp IS NOT NULL"),
                      graveyards=n("SELECT COUNT(*) FROM poi WHERE kind='graveyard'"),
                      guides=n("SELECT COUNT(*) FROM rxp_guide"))
        print("counts:", json.dumps(counts))
        # everything the fixture world and play-through hold arrives in the KB
        w = fixture.world()
        npcs, mobs = w["npcs"], w["creatures"]
        check(counts["quest"] == len(w["quests"]), "quest")
        check(counts["quest_with_xp"] == sum(1 for q in w["quests"] if q.get("xp")), "quest xp")
        check(counts["creature"] == len(npcs) + len(mobs), "creature")
        # one spawn per NPC, a ring of 12 per creature camp, and dangerousMobs.lua's `.pin` for the Fixture Wolf
        check(counts["creature_spawn"] == len(npcs) + 12 * len(mobs) + 1, "creature_spawn %d" % counts["creature_spawn"])
        check(counts["quest_giver"] == 2 * len(w["quests"]), "quest_giver: a start and an end NPC per quest")
        check(counts["obs_kill"] == len(run["kills"]) and counts["obs_session"] == 1, "obs_kill / obs_session")
        check(counts["quest_xp_obs"] == sum(1 for q in run["quests"].values() if q["turned_t"]), "quest_xp_obs")
        check(counts["graveyards"] == 2 and counts["creature_note"] == 1, "graveyards / creature_note")
        check(counts["travel_edge"] > 10, "travel_edge from the recorded trail")
        check(counts["trainer"] == sum(1 for x in npcs if "Trainer" in (x.get("subname") or "")), "trainer")
        check(n("SELECT COUNT(*) FROM obs_kill WHERE ttk_s IS NOT NULL") == counts["obs_kill"], "obs_kill ttk")
        check(n("SELECT COUNT(*) FROM map WHERE width_yd > 0") == len(w["bounds"]), "map sizes")
        check(n("SELECT COUNT(*) FROM obs_step_time") > 10, "obs_step_time from the companion log")

        # flight paths: every FPDB edge of the RestedXP flightData.lua is in the KB
        fpdb = _lua.read_file(store.BuildContext(root=root).rxp("DB", "forever", "flightData.lua"))["addon.FPDB"]
        edges = sum(1 for t in fpdb.values() for e in t.values() if isinstance(e, dict) for k in e if isinstance(k, int))
        check(counts["flight_path"] == edges == n("SELECT COUNT(*) FROM flight_path WHERE src LIKE '%rxp%'"),
              "flight_path vs FPDB")
        # map bounds: the client's UiMapAssignment (data/db2) wins, exactly
        for um, b in w["bounds"].items():
            r = c.execute("SELECT wx0, wy0, wx1, wy1, src FROM map_bounds WHERE ui_map=?", (int(um),)).fetchone()
            check(r is not None and r[4] == "db2" and all(abs(a - b[k]) < 1e-3 for a, k in zip(r, ("wx0", "wy0", "wx1", "wy1"))),
                  "map_bounds %s %r" % (um, r))

        # world-form RXP gotos got map coordinates, the ones the guide notes beside them (`--c:Zone,x,y`)
        got = 0
        for args, x, y in c.execute("""SELECT a.args, s.x, s.y FROM rxp_action a JOIN rxp_step s ON s.guide_key=a.guide_key
                                       AND s.idx=a.idx WHERE a.verb='goto' AND a.guide_key=? AND s.wx IS NOT NULL""",
                                    (fixture.DM,)):
            cm = (json.loads(args).get("comment") or "")
            if cm.startswith("c:") and json.loads(args).get("wx") is not None:
                _, ax, ay = cm[2:].split(",")
                if x is not None and abs(x - float(ax)) < 0.05 and abs(y - float(ay)) < 0.05:
                    got += 1
        check(got >= 2, "world gotos -> map coordinates: %d" % got)

        # provenance and meta
        for t in ("quest", "creature"):
            check(n("SELECT COUNT(*) FROM %s WHERE src IS NULL OR src=''" % t) == 0, "empty src in %s" % t)
        check(n("SELECT COUNT(*) FROM meta WHERE key='built'") == 1, "meta built")
        check(c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() == (str(store.SCHEMA_VERSION),),
              "meta schema_version")
        for loader in ("glide_entities", "seed_items", "rxp_db", "observations", "quest_xp", "map_bounds",
                       "travel_edges", "finalize"):
            check(n("SELECT COUNT(*) FROM meta WHERE key=?", "loader:" + loader) == 1, "meta loader:" + loader)
        c.close()

        # read-only access for the companion
        kb = store.open_ro(out)
        rows = kb.q("SELECT COUNT(*) AS n FROM quest")
        check(rows and rows[0]["n"] == counts["quest"], "KB().q")
        check(kb.meta("built"), "KB().meta")
        check(kb.q("SELECT * FROM no_such_table") == [], "KB().q swallows errors")
        x, y = kb.to_map(1426, -5584.417, -463.417)                        # the hub, Bram Ender
        check(x is not None and abs(x - 46.0) < 0.01 and abs(y - 52.0) < 0.01, "KB().to_map %r" % ((x, y),))
        cont, wx, wy = kb.to_world(1426, x, y)
        check(cont == 0 and abs(wx + 5584.417) < 0.01 and abs(wy + 463.417) < 0.01, "KB().to_world")
        check(store.open_ro(os.path.join(tmpdir, "missing.db")).q("SELECT 1") == [], "missing KB -> []")

        res = test_db2_import(out, tmpdir)
        print("db2 importer (synthetic CSVs):", json.dumps(res))
        test_sv_bounds(out, tmpdir)
        test_build_meta(out, summary, root)
        t = time.time()
        rep = test_kb_report(out, counts)
        cov = rep["coverage"]
        print("kb_report (%.1fs): %s" % (time.time() - t, ", ".join("%s %d/%d" % (k, cov[k]["n"], cov[k]["of"]) for k in (
            "quests_with_xp", "routed_quests_with_xp", "creatures_with_spawns", "guides_with_briefs", "maps_with_bounds"))))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    main()
