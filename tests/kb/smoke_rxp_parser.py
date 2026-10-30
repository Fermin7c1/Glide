#!/usr/bin/env python3
"""Smoke (report-only, see tests/kb/smoke.py): knowledge/rxp_parser on the player's installed RestedXP guides
(data/glide.db rxp_guides): every guide parses, the guide count, Dun Morogh's steps, the include expansion and the
loader. Facts of today's guides, which change with every RestedXP update; tests/kb/test_rxp_parser.py checks the
same code on the fixture guides.

    py -3 tests/kb/smoke_rxp_parser.py
"""
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import smoke  # noqa: E402

sys.path.insert(0, smoke.ROOT)
from knowledge import rxp_parser as P  # noqa: E402
from knowledge import store  # noqa: E402

GLIDE_DB = smoke.GLIDE
DM = "RestedXP Forever Guide (A)||5-11 Dun Morogh"
state = {}


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def corpus():
    c = sqlite3.connect("file:%s?mode=ro" % GLIDE_DB, uri=True)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT guide_key, source, text FROM rxp_guides").fetchall()
    old = {r[0]: r[1] for r in c.execute("SELECT guide_key, max(step) FROM rxp_steps GROUP BY guide_key")}
    c.close()
    return rows, old


def acts(step, verb):
    return [a for a in step["actions"] if a["verb"] == verb]


# ------------------------------------------------------------------------------------------------

def test_corpus():
    rows, old = corpus()
    t0 = time.time()
    guides, errors = {}, []
    for r in rows:
        try:
            guides[r["guide_key"]] = P.parse_guide(r["text"], source=r["source"])
        except Exception as e:
            errors.append((r["guide_key"], repr(e)))
    dt = time.time() - t0
    check(not errors, errors)
    check(len(guides) == len(rows) == 161, len(guides))
    keymiss = [(k, g["key"]) for k, g in guides.items() if g["key"] != k]
    check(not keymiss, keymiss)
    outliers, within = [], 0
    for k, g in guides.items():
        n, o = len(g["steps"]), old.get(k, 0)
        if abs(n - o) <= 0.1 * o or n == o:
            within += 1
        else:
            outliers.append((k, n, o))
    check(within >= 0.95 * len(guides), outliers)
    nsteps = sum(len(g["steps"]) for g in guides.values())
    nacts = sum(len(s["actions"]) for g in guides.values() for s in g["steps"])
    print("corpus: %d guides, %d steps, %d actions parsed in %.2fs; step-count outliers vs old parse: %s"
          % (len(guides), nsteps, nacts, dt, outliers or "none"))
    return guides


def test_world_axes(guides):
    """Guides annotate some world-form gotos with the map-% equivalent (`--c:Zone,x,y`). Across every map with
    3+ such points, x% must be an exact linear function of wy and y% of wx (store.py's convention)."""
    import re
    import statistics
    pts = {}
    for g in guides.values():
        for st in g["steps"]:
            for a in st["actions"]:
                m = re.match(r"c:([^,]+),([0-9.]+),([0-9.]+)", a["args"].get("comment") or "")
                if a["verb"] == "goto" and m and a["args"].get("wx") is not None:
                    pts.setdefault(a["args"]["ui_map"], []).append(
                        (a["args"]["wx"], a["args"]["wy"], float(m.group(2)), float(m.group(3))))
    checked = 0
    for ui, v in pts.items():
        wx, wy, x, y = zip(*v)
        if len(v) < 3 or len(set(wy)) < 2 or len(set(wx)) < 2:
            continue
        rx, ry = statistics.correlation(wy, x), statistics.correlation(wx, y)
        check(abs(rx) > 0.999 and abs(ry) > 0.999, (ui, rx, ry))
        checked += 1
    check(checked >= 5, checked)


def test_dun_morogh(guides):
    g = guides[DM]
    check(g["name"] == "5-11 Dun Morogh" and g["level_min"] == 5 and g["level_max"] == 11, g["name"])
    check(g["faction"] == "Alliance" and g["default_for"] == "Dwarf/Gnome" and g["season"] == "0,1", g["faction"])
    check(g["next_list"][0] == {"name": "11-12 Elwynn (Dwarf/Gnome)", "group": "RestedXP Forever Guide (A)",
                                "cond": None}, g["next_list"])
    s = g["steps"]
    s1 = s[0]
    check(s1["idx"] == 1 and s1["flags"].get("optional") is True, s1["flags"])
    check(s1["flags"].get("label") == "BoarMeatQuest" and s1["flags"].get("completewith") == "SenirEnd", s1["flags"])
    check("Save all the" in s1["text"] and "Chunks of Boar Meat" in s1["text"], s1["text"])
    check("|c" not in s1["text"] and "|T" not in s1["text"], s1["text"])
    col = [(a["entity_kind"], a["entity_id"], a["qty"], a["quest_id"]) for a in acts(s1, "collect")]
    check(("item", 769, 4, 317) in col and ("item", 2886, 6, 384) in col, col)
    check(any(a["args"]["name"] == "Crag Boar" for a in acts(s1, "mob")), "mob Crag Boar")
    check(any(a["args"]["area_id"] == 131 and a["entity_id"] == 131 for a in acts(s1, "subzoneskip")), "subzoneskip")
    check(acts(s1, "collect")[0]["args"]["comment"] == "Collect Chunk of Boar Meat (x4)", "trailing comment")

    s2 = s[1]
    gotos = acts(s2, "goto")
    check(len(gotos) == 4, len(gotos))
    g4 = gotos[3]["args"]                     # `.goto 1426/0,-499.17,-5644.37`
    # kb axis convention (store.py, binding): wx = world X (north) = 2nd number, wy = world Y (west) = 1st;
    # test_world_axes() verifies that orientation against the guides' own `--c:Zone,x,y` annotations.
    check(g4["continent"] == 0 and g4["wy"] == -499.17 and g4["wx"] == -5644.37 and g4["ui_map"] == 1426, g4)
    check("x" not in g4 and gotos[0]["args"]["x"] == 43.316 and gotos[0]["args"]["mode"] == "path", gotos[0])
    check(s2["ui_map"] == 1426 and s2["x"] == 43.316 and s2["y"] == 56.283 and s2["zone"] == "Dun Morogh", s2)
    xps = acts(s2, "xp")
    check(len(xps) == 2, xps)
    pri = [a for a in xps if a["cond"] and a["cond"]["class"] == ["Priest"]]
    npri = [a for a in xps if a["cond"] and a["cond"]["not_class"] == ["Priest"]]
    check(pri and pri[0]["args"]["level"] == 5 and pri[0]["args"]["xp"] == 1325, pri)
    check(npri and npri[0]["args"]["level"] == 5 and npri[0]["args"]["xp"] == 1595, npri)
    check("--XX 270 from priest quest" in (s2["comments"] or ""), s2["comments"])

    s5 = s[4]
    tg = acts(s5, "target")[0]
    check(tg["args"]["name"] == "Eric Brighthammer" and tg["entity_kind"] == "npc" and tg["entity_id"] == 265813, tg)
    check([a["quest_id"] for a in acts(s5, "turnin")] == [96628], "turnin 96628")
    check([a["quest_id"] for a in acts(s5, "accept")] == [96608], "accept 96608")
    check(s5["wy"] == -498.4 and s5["wx"] == -5648.4 and s5["continent"] == 0, s5)

    s9 = s[8]
    check(s9["cond"] and s9["cond"]["class"] == ["Warlock"], s9["cond"])
    s10 = s[9]
    v = acts(s10, "vendor")[0]
    check(v["entity_kind"] == "npc" and v["entity_id"] == 6328 and v["args"]["npc_id"] == 6328, v)
    m = acts(s10, "money")[0]["args"]
    check(m["op"] == "<" and m["copper"] == 100 and m["skip_when"] == "below", m)
    # RXP's live step numbers skip non-applicable steps: a Dwarf Warrior never sees the Warlock steps 9, 10
    imap = P.rxp_index_map(g, cls="Warrior", race="Dwarf", faction="Alliance")
    check(imap[9] == 11 and 9 not in imap.values() and 10 not in imap.values(), imap)


def test_guide_fields(guides):
    z = guides["RestedXP Forever Guide (A)||1-14 Zephras Isle"]       # `#group (A) << Alliance` + `(H) << Horde`
    check(z["faction"] is None and z["forever_ok"], z["faction"])
    nl = [(n["group"], n["name"], n["cond"]["raw"]) for n in z["next_list"]]
    check(nl == [("RestedXP Forever Guide (A)", "13-15 Westfall", "Alliance"),
                 ("RestedXP Forever Guide (H)", "12-14 Silverpine Forest", "Horde !Hunter"),
                 ("RestedXP Forever Guide (H)", "12-17 The Barrens", "Horde Hunter")], nl)
    s = guides["RestedXP Forever Guide (H)||41-400_41-41 Swamp of Sorrows"]   # range from #displayname
    check((s["level_min"], s["level_max"]) == (41, 41), (s["level_min"], s["level_max"]))
    check(s["next_list"][0]["group"] == "RestedXP Horde 40-50", s["next_list"])
    check(guides[DM]["rxp_name"] == "05-11 Dun Morogh", guides[DM]["rxp_name"])
    w = guides["RestedXP Forever Guide (A)||13-15 Westfall"]
    st = next(x for x in w["steps"] if any(c["tag"] == "label" for c in x["flags"].get("cond_flags", ())))
    check(P.step_flags(st, cls="Warlock", race="Human").get("optional") is True, st["flags"])  # `#optional << Human Warlock`
    check("label" not in P.step_flags(st, cls="Warlock", race="Human"), st["flags"])         # `#label X << !Human/!Warlock`
    check(P.step_flags(st, cls="Mage", race="Gnome").get("label") == "FurlbrowFarm", st["flags"])
    dead = sum(1 for g in guides.values() for x in g["steps"] if x["cond"] and x["cond"].get("never"))
    check(dead >= 358, dead)                                                                  # `step << skip`


def test_includes(guides):
    import copy
    gs = copy.deepcopy(guides)
    stats = P.expand_includes(gs)
    d = gs["RestedXP Forever Dungeon Guide (A)||5-11 Dun Morogh"]["steps"]
    src = guides[DM]["steps"]
    check(len(d) == len(src), (len(d), len(src)))
    check(all(a["flags"]["included_from"] == DM and a["flags"]["included_idx"] == b["idx"] and a["text"] == b["text"]
              and len(a["actions"]) == len(b["actions"]) for a, b in zip(d, src)), "copied steps")
    check(guides["RestedXP Forever Dungeon Guide (A)||5-11 Dun Morogh"]["steps"][0]["flags"]["include"]
          == "RestedXP Forever Guide (A)\\5-11 Dun Morogh", "parse_guide keeps the include flag")
    # label range NormalRouteStart-NormalRouteEnd, then the dungeon guide's own steps
    lm = gs["RestedXP Forever Dungeon Guide (A)||11-13 Loch Modan"]["steps"]
    check(lm[0]["flags"]["label"] == "NormalRouteStart" and lm[0]["flags"]["included_idx"] == 1, lm[0]["flags"])
    inc = [s for s in lm if s["flags"].get("included_from")]
    check(inc[-1]["flags"]["label"] == "NormalRouteEnd" and len(lm) > len(inc), len(lm))
    check([s["idx"] for s in lm] == list(range(1, len(lm) + 1)), "renumbered")
    # include inside `step << NightElf`: included steps inherit the condition
    dk = gs["RestedXP Forever Guide (A)||14-16 Darkshore"]["steps"]
    wf = [s for s in dk if s["flags"].get("included_from") == "RestedXP Forever Guide (A)||13-15 Westfall"]
    check(wf and all(s["cond"] and "NightElf" in s["cond"]["raw"] for s in wf), "inherited cond")
    # Horde dungeon guides name an (A) group; resolved by name and flagged
    hd = gs["RestedXP Forever Dungeon Guide (H)||1-6 Durotar"]["steps"]
    check(hd[0]["flags"]["included_from"] == "RestedXP Forever Guide (H)||1-6 Durotar" and
          "include_note" in hd[0]["flags"], hd[0]["flags"])
    un = gs["RestedXP Forever Dungeon Guide (A)||Skyborne Hunter Class Quests"]["steps"]
    check(len(un) == 1 and un[0]["flags"].get("include_unresolved"), un)
    check(stats["resolved"] >= 20 and len(stats["unresolved"]) == 2, stats["unresolved"])
    print("includes: %d resolved (%d by name), unresolved: %s" % (
        stats["resolved"], len(stats["notes"]), [u["include"] for u in stats["unresolved"]]))


def test_load():
    ctx = store.BuildContext(log=lambda *a: None)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store.create_schema(conn)
    t0 = time.time()
    r = P.load(conn, ctx)
    dt = time.time() - t0
    check(not r["errors"], r["errors"])
    check(r["guides"] == 161 and r["steps"] > 19539 and r["actions"] > 100000, r)
    check(dt < 10, "load took %.1fs" % dt)
    q = lambda sql, *a: conn.execute(sql, a).fetchall()  # noqa: E731
    check(q("SELECT count(*) FROM rxp_guide")[0][0] == 161, "rxp_guide rows")
    row = q("SELECT * FROM rxp_guide WHERE key=?", DM)[0]
    check(row["level_min"] == 5 and row["faction"] == "Alliance" and row["text"].startswith("#forever"), dict(row))
    st = q("SELECT * FROM rxp_step WHERE guide_key=? AND idx=2", DM)[0]
    check(st["ui_map"] == 1426 and "--XX 270 from priest quest" in st["comments"], dict(st))
    check(store.jload(st["flags"]) == {"optional": True}, st["flags"])
    a = q("SELECT * FROM rxp_action WHERE guide_key=? AND idx=1 AND verb='collect' ORDER BY line", DM)
    check(a[0]["entity_id"] == 769 and a[0]["qty"] == 4 and a[0]["quest_id"] == 317, dict(a[0]))
    check(store.jload(a[0]["args"])["raw"] == "769,4,317,1", a[0]["args"])
    n_dm = q("SELECT count(*) FROM rxp_step WHERE guide_key=?", DM)[0][0]
    n_dg = q("SELECT count(*) FROM rxp_step WHERE guide_key=?", "RestedXP Forever Dungeon Guide (A)||5-11 Dun Morogh")[0][0]
    check(n_dm == n_dg == 267, (n_dm, n_dg))
    r2 = P.load(conn, ctx)                                                          # idempotent
    check(r2["steps"] == r["steps"] and q("SELECT count(*) FROM rxp_step")[0][0] == r["steps"], "reload")
    print("load: %d guides, %d steps, %d actions in %.2fs (includes resolved %d, unresolved %d, parse warnings %d)"
          % (r["guides"], r["steps"], r["actions"], dt, r["includes_resolved"], len(r["includes_unresolved"]),
             r["warnings"]))


def guides():
    smoke.need(GLIDE_DB)
    if "g" not in state:
        try:
            state["g"] = test_corpus()
        except AssertionError:
            state["g"] = {r["guide_key"]: P.parse_guide(r["text"], source=r["source"]) for r in corpus()[0]}
            raise
    return state["g"]


def need_dm():
    if DM not in guides():
        raise smoke.NotRun("no %s guide" % DM)
    return guides()


if __name__ == "__main__":
    smoke.run("rxp_parser", [
        ("every guide parses (count, keys, step counts)", guides),
        ("Dun Morogh steps", lambda: test_dun_morogh(need_dm())),
        ("world-form goto axes", lambda: test_world_axes(guides())),
        ("guide fields", lambda: test_guide_fields(need_dm())),
        ("include expansion", lambda: test_includes(need_dm())),
        ("loader into a memory KB", lambda: (need_dm(), test_load())),
    ])
