"""The committed fixture corpus for the knowledge tests: a small synthetic world that never changes, unlike the
player's installed RestedXP guides, recordings and data/kb.db.

    tests/kb/fixtures/guides/*.txt     four synthetic RestedXP guides (two zones, two dungeon guides with #include)
    tests/kb/fixtures/world.json       quests, NPCs, creatures, maps and map bounds (ids 90001+)
    tests/kb/fixtures/rxp/DB/...       synthetic RestedXP data files (flight paths, spirit healers, dangerous mobs)
    tests/kb/fixtures/community/...    synthetic community notes and source registry

On top of that a scripted play-through (play()) is the recorder session and the companion log of a Dwarf Warrior
levelling 5 -> 10 through both guides, with kill XP from the Forever formulas.

    glide_db(path)          the mini glide.db (tools/build_db.py's schema) from the files above
    make_root(folder)       a project root in `folder`: data/glide.db, data/live/*.jsonl, data/db2/*.csv,
                            reference/rxp/DB, knowledge/community
    build(folder)           make_root + the real knowledge/build_kb.build into folder/data/kb.db; returns
                            (kb path, build summary, root). Never touches the repo's data/.
"""
import csv
import json
import math
import os
import shutil
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
for p in (ROOT, os.path.join(ROOT, "tools")):
    if p not in sys.path:
        sys.path.append(p)

GROUP = "Fixture Forever Guide (A)"
DGROUP = "Fixture Forever Dungeon Guide (A)"
DM = GROUP + "||5-8 Dun Morogh"
LM = GROUP + "||8-10 Loch Modan"
DDM = DGROUP + "||5-8 Dun Morogh"
DLM = DGROUP + "||8-10 Loch Modan"
GUIDE_FILES = {DM: "a_5-8_dun_morogh.txt", LM: "a_8-10_loch_modan.txt", DDM: "d_5-8_dun_morogh.txt",
               DLM: "d_8-10_loch_modan.txt"}
SESSION = "20261005-090000"               # the recorder session of the play-through
SIM_LOG = "20261005-120000"               # a simulator log (two level-ups 30 s apart)
T0 = 1791190800                           # 2026-10-05 09:00 local-ish; any fixed epoch works
CHAR = {"char": "Fixa", "realm": "Fixture", "class": "WARRIOR", "race": "Dwarf", "faction": "Alliance"}
# Forever kill XP as fitted from real recordings (tests/kb/test_mechanics.py FOREVER)
FOREVER = {"xp.kill.low_base": "mob", "xp.kill.rounding": "half_even", "rested.bonus_ratio": 0.5,
           "xp.mod.well_fed": 0.05}


def world():
    with open(os.path.join(FIX, "world.json"), encoding="utf-8") as fh:
        return json.load(fh)


def guide_text(key):
    with open(os.path.join(FIX, "guides", GUIDE_FILES[key]), encoding="utf-8") as fh:
        return fh.read()


def map_size_yd(w, ui_map):
    b = w["bounds"][str(ui_map)]
    return abs(b["wy1"] - b["wy0"]), abs(b["wx1"] - b["wx0"])


# ------------------------------------------------------------------------------------------------
# the play-through: a Dwarf Warrior (softcore, RestedXP defaults) through both guides
# ------------------------------------------------------------------------------------------------
# (guide, RXP step number, where the step is done, actions). Steps RestedXP hides for this character (the
# #hardcore one, the two Warlock steps, `step << skip`) take no number. Actions: accept/turnin quest, kill
# (creature, count[, item looted per kill]), grind (creature, until level, until xp into it), death, hearth,
# eat (Well Fed).
PLAN = [
    (DM, 1, (1426, 40.0, 50.0), [("accept", 90001)]),
    (DM, 2, (1426, 42.0, 56.0), [("grind", 90101, 5, 1595)]),
    (DM, 3, (1426, 43.0, 55.0), [("death",)]),
    (DM, 4, (1426, 46.0, 52.0), [("turnin", 90001), ("accept", 90002)]),
    (DM, 5, (1426, 44.0, 60.0), [("kill", 90101, 8)]),
    (DM, 6, (1426, 46.0, 52.0), [("turnin", 90002), ("accept", 90003)]),
    (DM, 7, (1426, 44.0, 61.0), [("kill", 90101, 5, 90301)]),
    (DM, 8, (1426, 46.0, 52.0), [("turnin", 90003), ("accept", 90005)]),
    (DM, 9, (1426, 30.0, 45.0), [("accept", 90004)]),
    (DM, 10, (1426, 28.0, 40.0), [("kill", 90102, 6)]),
    (DM, 11, (1426, 30.0, 45.0), [("turnin", 90004)]),
    (DM, 12, (1426, 30.0, 45.0), [("hearth", (1426, 46.0, 52.0))]),
    (DM, 13, (1426, 60.0, 50.0), []),
    (DM, 14, (1426, 58.0, 54.0), [("grind", 90101, 8, 0)]),
    (LM, 1, (1432, 35.0, 45.0), [("turnin", 90005), ("accept", 90007), ("eat",)]),
    (LM, 2, (1432, 40.0, 30.0), [("kill", 90103, 6)]),
    (LM, 3, (1432, 35.0, 45.0), [("turnin", 90007), ("accept", 90008)]),
    (LM, 4, (1432, 33.0, 50.0), [("turnin", 90008)]),
    (LM, 5, (1432, 38.0, 32.0), [("grind", 90103, 10, 0)]),
]
RUN_YDS = 7.0
FRAME_S = 2                              # companion log: one core frame every 2 s
TRAIL_S = 3                              # recorder trail: one point every 3 s
WELL_FED_SPELL, WELL_FED_S = "1248422", 1800
REST_POOL = 900                          # rested XP at login: the first kills get the bonus


def play():
    """The play-through as data: {"frames": [...], "pages": [(t, page)], "kills", "xp", "trail", "events",
    "quests", "loot", "end"}; deterministic."""
    from knowledge.mechanics import params, xp as X
    w = world()
    quests = {q["id"]: q for q in w["quests"]}
    mobs = {c["id"]: c for c in w["creatures"]}
    st = {"t": T0, "l": 5, "x": 0, "m": 1426, "px": 40.0, "py": 50.0, "d": 0, "step": None, "rest": REST_POOL,
          "wf_until": 0}
    out = {"frames": [], "pages": [], "kills": [], "xp": [], "trail": [], "events": [], "quests": {}, "loot": [],
           "steps": []}
    last_trail = [None]
    nkill = [0]

    def frame():
        out["frames"].append({"t": st["t"], "l": st["l"], "x": st["x"], "X": X.level_xp(st["l"]), "r": st["rest"],
                              "st": st["step"], "d": st["d"], "cl": CHAR["class"], "mp": st["m"],
                              "px": int(round(st["px"] * 100)), "py": int(round(st["py"] * 100))})
        if last_trail[0] is None or st["t"] - last_trail[0] >= TRAIL_S:
            last_trail[0] = st["t"]
            out["trail"].append((st["t"], st["m"], round(st["px"], 2), round(st["py"], 2), 0))

    def wait(s):
        end = st["t"] + s
        while st["t"] < end:
            st["t"] += FRAME_S
            frame()

    def zone_page():                                              # the addon's Z page: the map's size in yards
        wyd, hyd = map_size_yd(w, st["m"])
        out["pages"].append((st["t"], {"pg": "Z", "mm": str(st["m"]), "mw": "%d" % round(wyd), "mh": "%d" % round(hyd)}))

    def walk(m, x, y):
        if m != st["m"]:                                          # zone change: a short loading screen
            st["m"], st["px"], st["py"] = m, x, y
            zone_page()
            wait(10)
            return
        wyd, hyd = map_size_yd(w, m)
        dist = math.hypot((x - st["px"]) / 100 * wyd, (y - st["py"]) / 100 * hyd)
        n = max(1, int(dist / RUN_YDS / FRAME_S))
        x0, y0 = st["px"], st["py"]
        for i in range(1, n + 1):
            st["px"], st["py"] = x0 + (x - x0) * i / n, y0 + (y - y0) * i / n
            st["t"] += FRAME_S
            frame()

    def gain(amount):
        st["x"] += amount
        while st["x"] >= X.level_xp(st["l"]):
            st["x"] -= X.level_xp(st["l"])
            st["l"] += 1
            out["events"].append((st["t"], "level", st["l"] - 1, st["m"], st["px"], st["py"],
                                  json.dumps({"to": st["l"]})))
        out["xp"].append((st["t"], st["l"], st["x"], X.level_xp(st["l"]), st["rest"]))

    def kill(cid, item=None):
        c = mobs[cid]
        ml = c["min_level"] + nkill[0] % (c["max_level"] - c["min_level"] + 1)
        nkill[0] += 1
        out["events"].append((st["t"], "combat", st["l"], st["m"], st["px"], st["py"], "{}"))
        wait(12)
        with params.classic_scope(FOREVER):                       # rested bonus while the pool lasts
            d = X.kill_xp_detail(st["l"], ml, rest_pool=st["rest"], xp_mod=0.05 if st["t"] < st["wf_until"] else 0.0)
        out["kills"].append((st["t"], cid, c["name"], d["xp"], st["rest"], ml, st["l"], st["m"], round(st["px"], 2),
                             round(st["py"], 2)))
        st["rest"] = d["pool_after"]
        gain(d["xp"])
        out["events"].append((st["t"] + 1, "ooc", st["l"], st["m"], st["px"], st["py"], "{}"))
        if item:
            out["loot"].append({"t": st["t"] + 2, "m": st["m"], "x": round(st["px"], 2), "y": round(st["py"], 2),
                                "lvl": st["l"], "items": [{"id": item, "src": cid, "c": 1, "sk": "npc"}]})
        wait(8)

    frame()
    zone_page()
    out["xp"].append((st["t"], st["l"], st["x"], X.level_xp(st["l"]), st["rest"]))     # the recorder's login row
    for key, no, (m, x, y), acts in PLAN:
        name = key.split("||")[1]
        if st["step"] is None or out["steps"][-1][0] != key:
            rxp_name = "%02d-%s" % (int(name.split("-")[0]), name.split("-", 1)[1])     # RestedXP pads: 05-8 ...
            out["pages"].append((st["t"], {"pg": "G", "gn": rxp_name, "gg": GROUP}))
            if st["step"] is None:
                out["pages"].append((st["t"], {"pg": "B", "nm": CHAR["char"], "cl": CHAR["class"],
                                               "ra": CHAR["race"], "fa": CHAR["faction"]}))
        st["step"] = no
        t_step = st["t"]
        el = ",".join("%s:%d" % (a[0], a[1]) for a in acts if a[0] in ("accept", "turnin"))
        if not el and any(a[0] == "kill" for a in acts):              # the quest the kills are for
            q = max((q for q in out["quests"].values() if q["turned_t"] is None), key=lambda q: q["accepted_t"])
            el = "complete:%d" % q["id"]
        out["pages"].append((st["t"] + 1, {"pg": "S0", "i": str(no), "el": el, "tx": "step %d" % no}))
        walk(m, x, y)
        for a in acts:
            if a[0] == "accept":
                wait(6)
                out["quests"][a[1]] = {"id": a[1], "accepted_t": st["t"], "accepted_lvl": st["l"], "turned_t": None,
                                       "turned_lvl": None, "xp": None, "guide": name, "step": no}
            elif a[0] == "turnin":
                wait(6)
                q = quests[a[1]]
                with params.classic_scope():
                    qxp = X.quest_xp(q["xp"], q["level"], st["l"])
                rec = out["quests"][a[1]]
                rec.update(turned_t=st["t"], turned_lvl=st["l"], xp=qxp, guide=name, step=no)
                gain(qxp)
            elif a[0] == "kill":
                for _ in range(a[2]):
                    kill(a[1], a[3] if len(a) > 3 else None)
            elif a[0] == "grind":
                while st["l"] < a[2] or (st["l"] == a[2] and st["x"] < a[3]):
                    kill(a[1])
            elif a[0] == "death":
                wait(4)
                st["d"] = 1
                out["events"].append((st["t"], "death", st["l"], st["m"], st["px"], st["py"], "{}"))
                wait(6)
                st["d"] = 2
                out["events"].append((st["t"], "alive", st["l"], st["m"], st["px"], st["py"], "{}"))
                st["px"], st["py"] = 45.5, 52.5                    # the spirit healer next to the hub
                wait(20)
                st["d"] = 0
                out["events"].append((st["t"], "unghost", st["l"], st["m"], st["px"], st["py"], "{}"))
            elif a[0] == "eat":                                   # Well Fed (+5% kill XP) for 30 min
                wait(10)
                st["wf_until"] = st["t"] + WELL_FED_S
                out["pages"].append((st["t"], {"pg": "A0", "a": "%s:%d:%d:0:Well Fed" % (
                    WELL_FED_SPELL, st["wf_until"] % 1000000, WELL_FED_S)}))
            elif a[0] == "hearth":
                wait(10)
                out["events"].append((st["t"], "hearth", st["l"], st["m"], st["px"], st["py"], "{}"))
                st["m"], st["px"], st["py"] = a[1]
                wait(4)
        wait(4)
        out["steps"].append((key, no, t_step, st["t"]))
    out["end"] = st["t"]
    return out


def sim_log():
    """A simulator run (companion --sim): two level-ups 30 s apart, which the live_logs loader skips."""
    frames, t = [], T0 + 10800
    for i in range(40):
        lvl = 1 + (i >= 10) + (i >= 25)
        frames.append({"t": t + 2 * i, "l": lvl, "x": 10 * i, "X": 900, "st": 1 + i // 5, "d": 0, "cl": "MAGE"})
    return frames


# ------------------------------------------------------------------------------------------------
# glide.db
# ------------------------------------------------------------------------------------------------
def glide_db(path, run=None):
    """Write the mini glide.db at `path` (replaced if present) and return the play-through it records."""
    import build_db
    run = run or play()
    w = world()
    if os.path.exists(path):
        os.remove(path)
    c = sqlite3.connect(path)
    c.executescript(build_db.SCHEMA)
    ins = lambda t, rows: rows and c.executemany(  # noqa: E731
        "INSERT INTO %s VALUES(%s)" % (t, ",".join("?" * len(rows[0]))), rows)
    ins("meta", [("built", "2026-10-05 12:00:00"), ("fixture", "tests/kb/fixture.py")])
    ins("maps", [(m["ui_map"], m["name"], m["parent"], m["area_id"]) for m in w["maps"]])
    ins("zones", [(z["area_id"], z["name"], z["ui_map"], z["parent"]) for z in w["zones"]])
    ins("map_areas", [(a["ui_map"], a["area_id"], a["name"], a["x1"], a["y1"], a["x2"], a["y2"]) for a in w["map_areas"]])
    ents, spawns = [], []
    for n in w["npcs"]:
        ents.append(("npc", n["id"], n["name"], n["level"], n["level"], "A", 0, n.get("subname"), 1, "fixture"))
        spawns.append(("npc", n["id"], n["ui_map"], n["x"], n["y"], "exact", "fixture"))
    for cr in w["creatures"]:
        ents.append(("npc", cr["id"], cr["name"], cr["min_level"], cr["max_level"], "AH", 0, None, 1, "fixture"))
        for i in range(12):                                       # a ring of spawn points around the camp
            a = i * math.pi / 6
            spawns.append(("npc", cr["id"], cr["ui_map"], round(cr["x"] + 3 * math.cos(a), 2),
                           round(cr["y"] + 3 * math.sin(a), 2), "near", "fixture"))
    ins("entities", ents)
    ins("spawns", spawns)
    npc = {n["id"]: n for n in w["npcs"]}
    qrows, qe, qo, qp, pre = [], [], [], [], []
    for q in w["quests"]:
        qrows.append((q["id"], q["name"], q["level"], q["min_level"], "alliance", 77, None, 1, None, q["ui_map"],
                      q["xp"], 50, q.get("next_id"), q["objective_text"], "Fixture quest text, $N.", None,
                      "Well done.", 1, None, None, 1, "fixture", "{}"))
        for role in ("start", "end"):
            n = npc[q[role]]
            qe.append((q["id"], role, "npc", n["id"], n["name"], "fixture"))
            qp.append((q["id"], role, "npc", n["id"], n["ui_map"], n["x"], n["y"], "fixture"))
        for i, o in enumerate(q.get("objectives", ()), 1):
            qo.append((q["id"], i, o["type"], o["target_id"], o["amount"], "", "fixture"))
            cr = next((cr for cr in w["creatures"] if cr["id"] == o["target_id"]), None)
            if cr is None:                                        # an item: where its creature lives
                it = next(it for it in w["items"] if it["id"] == o["target_id"])
                cr = next(cr for cr in w["creatures"] if cr["id"] == it["drop_from"])
            qp.append((q["id"], "objective%d" % i, None, None, cr["ui_map"], cr["x"], cr["y"], "fixture"))
        for p in q.get("pre", ()):
            pre.append((q["id"], p, "pre"))
    ins("quests", qrows)
    ins("quest_entities", qe)
    ins("quest_objectives", qo)
    ins("quest_points", qp)
    ins("quest_prereqs", pre)
    for key in GUIDE_FILES:
        grp, name = key.split("||")
        c.execute("INSERT INTO rxp_guides VALUES(?,?,?,?,?,?)",
                  (key, name, grp, None, "fixture:" + GUIDE_FILES[key], guide_text(key)))
    s = SESSION
    ins("sessions", [(s, CHAR["char"], CHAR["realm"], CHAR["class"], CHAR["race"], CHAR["faction"], 5,
                      "1.60.1.70000", T0, run["end"], "GlideRecorder_fixture.lua")])
    ins("obs_xp", [(s,) + x for x in run["xp"]])
    ins("obs_kills", [(s,) + k for k in run["kills"]])
    ins("obs_trail", [(s,) + p for p in run["trail"]])
    ins("obs_events", [(s,) + e for e in run["events"]])
    ins("obs_quests", [(s, q["id"], None, q["accepted_t"], q["accepted_lvl"], q["turned_t"], q["turned_lvl"],
                        q["xp"], 50 if q["turned_t"] else None, None, q["guide"], q["step"])
                       for q in run["quests"].values()])
    ins("obs_misc", [(s, "loot", str(i), json.dumps(lt)) for i, lt in enumerate(run["loot"])])
    c.commit()
    c.close()
    return run


# ------------------------------------------------------------------------------------------------
# a project root and kb.db
# ------------------------------------------------------------------------------------------------
SCRATCH = os.path.join(tempfile.gettempdir(), "glide-kb-fixture")


def tmpdir(prefix):
    """A fresh folder for one test run under <temp>/glide-kb-fixture. The KB code keeps read-only connections
    open per thread, so Windows can't delete a run's folder until its process ends: each call removes the
    folders older than an hour instead."""
    os.makedirs(SCRATCH, exist_ok=True)
    for name in os.listdir(SCRATCH):
        p = os.path.join(SCRATCH, name)
        try:
            if time.time() - os.path.getmtime(p) > 3600:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass
    return tempfile.mkdtemp(prefix=prefix, dir=SCRATCH)
def write_log(path, run):
    with open(path, "w", encoding="utf-8") as fh:
        lines = [(f["t"], {"core": {k: str(v) for k, v in f.items() if k != "t"}}) for f in run["frames"]]
        lines += [(t, {"page": p}) for t, p in run["pages"]]
        for t, o in sorted(lines, key=lambda x: x[0]):
            o["t"] = t
            fh.write(json.dumps(o) + "\n")


def make_root(folder):
    """A fixture project root in `folder` (created); returns (root, play-through)."""
    root = os.path.abspath(folder)
    data = os.path.join(root, "data")
    os.makedirs(os.path.join(data, "live"), exist_ok=True)
    run = glide_db(os.path.join(data, "glide.db"))
    write_log(os.path.join(data, "live", SESSION + ".jsonl"), run)
    write_log(os.path.join(data, "live", SIM_LOG + ".jsonl"), {"frames": sim_log(), "pages": []})
    w = world()
    db2 = os.path.join(data, "db2")
    os.makedirs(db2, exist_ok=True)
    with open(os.path.join(db2, "UiMapAssignment.csv"), "w", encoding="utf-8", newline="") as fh:
        cw = csv.writer(fh)
        cw.writerow(["UiMapID", "MapID", "OrderIndex", "UiMin[0]", "UiMin[1]", "UiMax[0]", "UiMax[1]"]
                    + ["Region[%d]" % i for i in range(6)])
        for um, b in sorted(w["bounds"].items()):
            cw.writerow([um, b["continent"], 0, 0, 0, 1, 1, b["wx1"], b["wy1"], 0, b["wx0"], b["wy0"], 100])
    shutil.copytree(os.path.join(FIX, "rxp"), os.path.join(root, "reference", "rxp"))
    shutil.copytree(os.path.join(FIX, "community"), os.path.join(root, "knowledge", "community"))
    return root, run


def build(folder):
    """The fixture kb.db, built by knowledge/build_kb.build from make_root(folder): (kb path, summary, root,
    play-through)."""
    from knowledge import build_kb, store
    root, run = make_root(folder)
    ctx = store.BuildContext(root=root, log=lambda *a: None)
    out = os.path.join(root, "data", "kb.db")
    summary = build_kb.build(out, ctx=ctx, quiet=True)
    return out, summary, root, run
