#!/usr/bin/env python3
"""What the Guides page gets from the engine with each guide (companion/rxp_explorer.py, rxp_import.guide_detail).

- #include: a guide's include steps expand over the guides the knowledge build reads, as the build expands them
  (regression: the free Dungeon guides showed as one empty step, kb has 71-847, and "14-16 Darkshore" drifted from
  step 166: 236 steps instead of 340).
- Which steps show, and their numbers: the masks give, for every character and settings combination, exactly the
  numbering the coach uses (knowledge/rxp_parser views.rxp_numbering) on all of RestedXP's free guides (regression:
  the page's own copy of RestedXP's rules disagreed on 41 of 80 guides for a Human Mage: `step << skip`, season,
  dungeon, XP-rate and phase tags).
- Map geometry: a zone's size in yards and its rectangle on its continent from kb.db, the newest kb (a waiting
  data/kb.next-<t>.db too), nothing without one.

Uses the seed's free guides (data/seed); no game data, no model.
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.append(os.path.join(ROOT, "companion"))
import rxp_explorer as X  # noqa: E402
import rxp_import as R  # noqa: E402
from knowledge.rxp_parser.conditions import cond_applies  # noqa: E402
from knowledge.rxp_parser.guide import parse_guide  # noqa: E402
from knowledge.rxp_parser.includes import expand_includes  # noqa: E402
from knowledge.rxp_parser.views import rxp_numbering  # noqa: E402

tmp = tempfile.mkdtemp(prefix="glide-rxp-explorer-")
try:
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, "data"))
    shutil.copytree(os.path.join(ROOT, "data", "seed"), os.path.join(home, "data", "seed"))
    source, free = R.free_guides(home, None)
    assert source == "Glide's copy" and len(free) >= 80, (source, len(free))
    texts = [g["text"] for g in free]

    # ---- #include, as the knowledge build expands it ---------------------------------------------------------
    corpus, parsed = {}, []
    for t in texts:
        p = parse_guide(t)
        parsed.append(corpus.setdefault(p["key"], p))
    expand_includes(corpus)                 # parsed[i]: free guide i as the build has it, its #includes expanded

    def at(name, group):
        return next(i for i, g in enumerate(free) if g["name"] == name and g["groups"][0] == group)
    for name, group, n in (("1-6 Shadowglen", "RestedXP Forever Dungeon Guide (A)", 71),
                           ("14-16 Darkshore", "RestedXP Forever Guide (A)", 342),
                           ("9-13 Zephras Isle", "RestedXP Forever Dungeon Guide (A)", 847)):
        d = R.guide_detail(home, R.FREE_ID, at(name, group))
        build = corpus["%s||%s" % (group, name)]["steps"]
        assert len(d["steps"]) == n == len(build), (name, len(d["steps"]), len(build))
        assert [s["idx"] for s in d["steps"]] == list(range(1, n + 1))
        assert [s["text"] for s in d["steps"]] == [s["text"] for s in build], name
        if "Dungeon" in group:
            assert any(s["flags"].get("included_from") for s in d["steps"]), "included steps say where they came from"
        # the facts command (quest chains, plan) parses the same way, so its step numbers are the page's
        import rxp_facts
        fp = rxp_facts.parsed_guide(home, R.FREE_ID, at(name, group))
        assert [s["idx"] for s in fp["steps"]] == [s["idx"] for s in d["steps"]], name
    plain = R.guide_detail(home, R.FREE_ID, at("1-6 Durotar", "RestedXP Forever Guide (H)"))
    assert not any(s["flags"].get("included_from") for s in plain["steps"])

    # ---- which steps show, and their numbers: the coach's numbering for every character -----------------------
    pl = X.players()
    assert len(pl) == len(set(p[:2] for p in pl)) == 56, len(pl)
    assert ("Skyborne@Alliance", "PALADIN", "Skyborne", "Alliance") in pl and \
        ("Skyborne@Horde", "PALADIN", "Skyborne", "Horde") not in pl and ("Dwarf", "SHAMAN", "Dwarf", "Alliance") not in pl
    combos = (0, 3, 7)            # all off, the coach's default (SSF + group quests), all on
    t0, wrong, guides, details = time.time(), [], 0, {}
    memos = [{} for _ in pl]          # a condition's answer for a character is the same in every guide
    options = {}
    for c in combos:
        options[c] = X.rxp_options(None)
        options[c].update({k: bool(c & (1 << b)) for b, k in enumerate(X.TOGGLES)})
    for i, g in enumerate(free):
        d = details[i] = R.guide_detail(home, R.FREE_ID, i)
        guides += 1
        assert d["players"] == ["%s/%s" % p[:2] for p in pl] and len(d["settings"]) == 8
        assert d["defaults"] == {"ssf": True, "group_quests": True, "hardcore": False}, d["defaults"]
        # the coach's side reads the guide as the knowledge build expands it (the whole corpus at once), not through
        # the page's own include_expanded: the page's steps must be the build's, step for step
        guide = {"text": g["text"], "steps": parsed[i]["steps"]}
        assert [s["text"] for s in d["steps"]] == [s["text"] for s in guide["steps"]], g["name"]
        vis = {"players": d["players"], "masks": d["vis_masks"], "steps": [s["vis"] for s in d["steps"]]}
        for p, (_pick, cls, race, faction) in enumerate(pl):
            memo = memos[p]

            def ok(c, cls=cls, race=race, faction=faction, memo=memo):
                raw = c.get("raw") if isinstance(c, dict) else c
                if raw not in memo:
                    memo[raw] = cond_applies(c, cls=cls, race=race, faction=faction)
                return memo[raw]
            for c in combos:
                want = rxp_numbering(guide, options[c], applies=ok, cls=cls, race=race, faction=faction)
                got = {n: d["steps"][k]["idx"] for n, k in X.numbering(vis, p, c).items()}
                if want != got:
                    wrong.append((g["name"], d["players"][p], c))
    assert not wrong, wrong[:10]
    print("masks match the coach's numbering: %d guides x %d characters x %d settings in %.1f s"
          % (guides, len(pl), len(combos), time.time() - t0))
    # Regression: `step << skip` (RestedXP never loads it) is shown to nobody
    darkshore = details[at("14-16 Darkshore", "RestedXP Forever Guide (A)")]
    width = (len(pl) + 3) // 4
    skip = [s for s in darkshore["steps"] if (s["cond"] or "").strip() == "skip"]
    assert skip and all(set(darkshore["vis_masks"][s["vis"]]) == {"0"} for s in skip), len(skip)
    # an action with its own condition carries who gets it; a waypoint names its action
    acted = [(s, a) for s in darkshore["steps"] for a in s["actions"] if a.get("vis")]
    assert acted and all(len(a["vis"]) == width for _s, a in acted)
    assert all(a["cond"] for _s, a in acted)
    for s in darkshore["steps"]:
        for w in s["waypoints"]:
            assert s["actions"][w["act"]]["verb"] in ("goto", "groundgoto", "flygoto", "waypoint", "pin", "questgoto",
                                                    "treasure", "rare"), w
    human_mage = d["players"].index("Human/MAGE")
    for _s, a in acted:
        bit = int(a["vis"], 16) >> human_mage & 1
        assert bool(bit) == cond_applies(a["cond"], cls="MAGE", race="Human", faction="Alliance"), a["cond"]
    # the config's RestedXP options are the page's defaults
    with open(os.path.join(home, "config.json"), "w", encoding="utf-8") as fh:
        fh.write('{"companion": {"rxp": {"ssf": false, "hardcore": true}}}')
    d = R.guide_detail(home, R.FREE_ID, at("1-6 Durotar", "RestedXP Forever Guide (H)"))
    assert d["defaults"] == {"ssf": False, "group_quests": True, "hardcore": True}, d["defaults"]
    os.remove(os.path.join(home, "config.json"))

    # ---- map geometry -------------------------------------------------------------------------------------------
    assert plain["maps"] == {}, "no knowledge base: no sizes (the page shows no yard figure rather than a guess)"
    kb = sqlite3.connect(os.path.join(home, "data", "kb.db"))
    kb.execute("BEGIN")                     # one commit (one journal file), not one per table
    kb.execute("CREATE TABLE map(ui_map INTEGER PRIMARY KEY, name TEXT, parent INT, area_id INT, continent INT, "
               "width_yd REAL, height_yd REAL)")
    kb.execute("CREATE TABLE map_bounds(ui_map INTEGER PRIMARY KEY, continent INT, wx0 REAL, wy0 REAL, wx1 REAL, "
               "wy1 REAL, src TEXT, conf REAL)")
    # Durotar (1411) on Kalimdor (1414): bounds made up, 5250 x 3500 yd; the continent 36000 x 24000 yd
    kb.execute("INSERT INTO map VALUES (1411, 'Durotar', 1414, 14, 1, NULL, NULL)")
    kb.execute("INSERT INTO map VALUES (1414, 'Kalimdor', 947, 0, 1, NULL, NULL)")
    kb.execute("INSERT INTO map_bounds VALUES (1411, 1, 1000, -3800, -2500, -9050, 'db2', 1)")
    kb.execute("INSERT INTO map_bounds VALUES (1414, 1, 12000, 16000, -12000, -20000, 'db2', 1)")
    kb.commit()
    kb.close()
    d = R.guide_detail(home, R.FREE_ID, at("1-6 Durotar", "RestedXP Forever Guide (H)"))
    m = d["maps"]["1411"]
    assert (m["w_yd"], m["h_yd"], m["parent"], m["name"]) == (5250.0, 3500.0, 1414, "Durotar"), m
    # corner (0, 0) is world (1000, -3800): on Kalimdor x = (-3800 - 16000) / -36000, y = (1000 - 12000) / -24000
    want = [round((-3800 - 16000) / -36000 * 100, 3), round((1000 - 12000) / -24000 * 100, 3),
            round((-9050 - 16000) / -36000 * 100, 3), round((-2500 - 12000) / -24000 * 100, 3)]
    assert m["rect"] == want, (m["rect"], want)
    assert d["maps"]["1414"]["rect"] is None and d["maps"]["1414"]["w_yd"] == 36000.0, d["maps"]["1414"]
    assert X.map_geometry(None, {1411}) == {}
    # the map table's own sizes come first; a waiting refresh (data/kb.next-<t>.db) is the newest kb
    nxt = os.path.join(home, "data", "kb.next-20990101-000000.db")
    shutil.copy(os.path.join(home, "data", "kb.db"), nxt)
    kb = sqlite3.connect(nxt)
    kb.execute("UPDATE map SET width_yd = 5000, height_yd = 3333.3 WHERE ui_map = 1411")
    kb.commit()
    kb.close()
    os.utime(nxt, (time.time() + 60, time.time() + 60))
    assert X.kb_path(home) == nxt
    m = R.guide_detail(home, R.FREE_ID, at("1-6 Durotar", "RestedXP Forever Guide (H)"))["maps"]["1411"]
    assert (m["w_yd"], m["h_yd"]) == (5000.0, 3333.3), m
    # #next carries who it's for
    # (fresh calls with kb.db and kb.next present, not the main loop's: they also open guides whose maps the kb lacks,
    # the only run of map_geometry's unknown-zone branch and place_world_points' off-map branch)
    nl = [n for g in (R.guide_detail(home, R.FREE_ID, i) for i in range(len(free)) if "Zephras" in free[i]["name"])
          for n in g["next_list"] if n["cond"]]
    assert nl and all(n["vis"] and len(n["vis"]) == width for n in nl), nl[:3]
    alliance = next(n for n in nl if n["cond"] == "Alliance")
    assert int(alliance["vis"], 16) >> d["players"].index("Human/MAGE") & 1
    assert not int(alliance["vis"], 16) >> d["players"].index("Orc/WARRIOR") & 1

    # ---- the seed's free guides are read once per process: copies handed out, read again when the file changes ---
    _src, again = R.free_guides(home, None)
    assert again == free and again[0] is not free[0] and again[0]["groups"] is not free[0]["groups"]
    again[0]["groups"].append("changed by a caller")
    assert R.free_guides(home, None)[1][0]["groups"] == free[0]["groups"], "a caller's change stays its own"
    import gzip
    seed = os.path.join(home, "data", "seed", "seed.json.gz")
    with gzip.open(seed, "wt", encoding="utf-8") as fh:
        json.dump({"sources": {"rxp_free": {"forever/one.lua": "RXPGuides.RegisterGuide([[\n" + free[0]["text"]
                                            + "]])\n"}}}, fh)
    os.utime(seed, (time.time() + 120, time.time() + 120))
    _src, one = R.free_guides(home, None)
    assert [(g["name"], g["groups"]) for g in one] == [(free[0]["name"], free[0]["groups"])], one[:2]
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
