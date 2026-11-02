#!/usr/bin/env python3
"""Per-guide facts for the Guides page (knowledge/api/explorer.py guide_facts, `rxp_import.py facts`).

Over a fixture kb.db (the real schema, a handful of rows) and a small guide parsed by the real parser: the kb match
(exact, then aligned when kb holds another version of the guide), each quest's accept/objective/turn-in steps, its
follow-ups and what skipping it loses (the analyzer's own rule), the map targets of a .complete step clipped near the
step, the prep list (a vendor buy, an Auction House collect, a bank trip) with names and vendors, the step_brief plan
for a class mapped through the idx map, hazards (an elite kill target with RestedXP's note), the newest kb including a
staged refresh build and refusing another schema, names from kb before glide.db, and the kb-less answer.

Run: python3 tests/test_rxp_facts.py   (prints OK)
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
sys.path.append(os.path.join(ROOT, "companion"))      # after ROOT: `knowledge` stays the package
for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY"):
    os.environ[k] = ""

from knowledge.api import _layer  # noqa: E402
if _layer.IMPORT_ERROR:
    print("SKIP: the knowledge layer does not import here (%s)" % _layer.IMPORT_ERROR)
    sys.exit(77)
from knowledge import store  # noqa: E402
from knowledge.api import explorer as X  # noqa: E402
from knowledge.rxp_parser.guide import parse_guide  # noqa: E402
import rxp_facts  # noqa: E402
import rxp_import as R  # noqa: E402

GUIDE = """#group Test Guide (A)
#name 1-3 Testvale
#next 3-5 Nextvale
step
    .goto Elwynn Forest,50,50
    .accept 100 >>Accept Wolves
step
    .goto Elwynn Forest,52,52
    >>Kill [Young Wolf]. Loot their [Wolf Fang]
    .complete 100,1
    .collect 600,5,100 --Collect Wolf Fang (x5)
    .mob Ragefang
step
    #ah
    .goto Elwynn Forest,40,60
    >>Buy a [Linen Bandage] from the Auction House
    .collect 700,2 --Linen Bandage (2)
step << Mage
    .goto Elwynn Forest,41,61
    .vendor 800 >>Buy [Refreshing Spring Water]
    .collect 159,10 --Collect Refreshing Spring Water
step
    .goto Elwynn Forest,50,50
    .turnin 100 >>Turn in Wolves
    .accept 101 >>Accept More Wolves
    .accept 102 >>Accept Report
step
    .bankwithdraw 702
    .turnin 101 >>Turn in More Wolves
"""
NEXT = """#group Test Guide (A)
#name 3-5 Nextvale
step
    .turnin 102 >>Turn in Report
"""


def assert_closed(path):
    """Regression (the Windows release build): a kb file still open in this process can't be replaced or deleted
    there ("being used by another process"), while Linux allows it, so this checks /proc for an open handle."""
    fd_dir = "/proc/self/fd"
    if not os.path.isdir(fd_dir):
        return
    want = os.path.realpath(path)
    for fd in os.listdir(fd_dir):
        try:
            target = os.readlink(os.path.join(fd_dir, fd))
        except OSError:
            continue
        assert target != want, "%s is still open (Windows could not replace or delete it)" % path


def remove_kb(path):
    assert_closed(path)
    os.remove(path)


def make_kb(path, guide_text, extra_step=False, schema=store.SCHEMA_VERSION, built="2026-11-01"):
    """A kb.db with the real schema and the few rows the facts read. extra_step: kb holds another version of the
    guide, with one more step after the first (so kb idx = guide idx + 1 from step 2 on)."""
    if os.path.exists(path):
        remove_kb(path)
    c = sqlite3.connect(path)
    store.create_schema(c)
    c.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(schema),))
    c.execute("INSERT INTO meta VALUES ('built', ?)", (built,))
    for text in (guide_text, NEXT):
        g = parse_guide(text)
        steps = list(g["steps"])
        if extra_step and g["name"] == "1-3 Testvale":
            steps.insert(1, {"idx": 0, "text": "Hearth to town", "actions": [], "flags": {}, "cond": None})
            for i, s in enumerate(steps, 1):
                s["idx"] = i
        c.execute("INSERT INTO rxp_guide(key, name, grp, text) VALUES (?, ?, ?, ?)",
                  (g["key"], g["name"], g["grp"], text if not extra_step else text + "\n-- other version"))
        for s in steps:
            c.execute("INSERT INTO rxp_step(guide_key, idx, text) VALUES (?, ?, ?)", (g["key"], s["idx"], s["text"]))
            for a in s["actions"]:
                if a.get("verb"):
                    c.execute("INSERT INTO rxp_action(guide_key, idx, line, verb, args, quest_id) VALUES (?,?,?,?,?,?)",
                              (g["key"], s["idx"], a["line"], a["verb"], json.dumps(a["args"]), a.get("quest_id")))
    c.execute("INSERT INTO map(ui_map, name, width_yd, height_yd) VALUES (1429, 'Elwynn Forest', 3000, 2000)")
    q = "INSERT INTO quest(id, name, level, min_level, xp, type, suggested_group, next_quest) VALUES (?,?,?,?,?,?,?,?)"
    c.executemany(q, [(100, "Wolves", 2, 1, 400, "normal", 0, 101), (101, "More Wolves", 3, 2, 500, "elite", 3, None),
                      (102, "Report", 3, 2, 300, "normal", 0, None), (103, "Unrelated", 3, 1, 100, "normal", 0, None)])
    c.execute("INSERT INTO quest_prereq(quest_id, pre_id, kind) VALUES (102, 101, 'pre')")
    c.execute("INSERT INTO quest_prereq(quest_id, pre_id, kind) VALUES (103, 100, 'exclusive')")
    c.executemany("INSERT INTO quest_chain VALUES (?, ?, ?)", [(100, 100, 1), (100, 101, 2)])
    c.execute("INSERT INTO quest_objective(quest_id, idx, type, target_kind, target_id, amount) "
              "VALUES (100, 1, 'kill', 'npc', 500, 6)")
    c.execute("INSERT INTO quest_objective(quest_id, idx, type, target_kind, target_id, amount, drop_from_kind, "
              "drop_from_id) VALUES (100, 2, 'collect', 'item', 600, 5, NULL, NULL)")
    c.execute("INSERT INTO creature_drop(creature_id, item_id, rate) VALUES (501, 600, 0.4)")
    c.executemany("INSERT INTO creature(id, name, min_level, max_level, rank) VALUES (?,?,?,?,?)",
                  [(500, "Young Wolf", 1, 2, 0), (501, "Ragefang", 4, 4, 1), (800, "Brother Danil", 10, 10, 0)])
    near = [(500, 1429, 52.5 + i * 0.3, 52.0, 1) for i in range(8)]      # around step 2
    far = [(500, 1429, 90.0, 10.0, 1)]                                    # across the zone: clipped away
    c.executemany("INSERT INTO creature_spawn(creature_id, ui_map, x, y, n) VALUES (?,?,?,?,?)",
                  near + far + [(501, 1429, 53.0, 53.0, 1), (800, 1429, 41.2, 61.1, 1)])
    c.execute("INSERT INTO creature_note(name, creature_id, classification, movement, notes) "
              "VALUES ('Ragefang', 501, 'Elite', 'Patrols the road', 'hits hard')")
    c.executemany("INSERT INTO item(id, name, buy_price) VALUES (?,?,?)",
                  [(700, "Linen Bandage", None), (159, "Refreshing Spring Water", 25), (702, "Wolf Pelt Bag", None)])
    c.execute("INSERT INTO vendor_item(npc_id, item_id, price) VALUES (800, 159, 25)")
    key = "Test Guide (A)||1-3 Testvale"
    c.execute("INSERT INTO guide_brief(guide_key, class, start_level, end_level, time_s, xp_total) "
              "VALUES (?, 'MAGE', 1, 3, 1800, 1200)", (key,))
    shift = 1 if extra_step else 0
    for idx in range(1, 7):
        kidx = idx + shift if idx >= 2 else idx
        just = {"why": ["hub_turnin"] if idx == 5 else [], "quests": {"100": {"xp_at_level": 400}} if idx == 5 else {},
                "travel": {"mode": "walk", "dist_yd": 80.0, "from_idx": kidx - 1, "observed": False} if idx > 1 else {},
                "risks": [], "kills": [{"creature_id": 501, "name": "Ragefang", "level": 4, "rank": 1}] if idx == 2 else []}
        c.execute("INSERT INTO step_brief(guide_key, idx, class, exp_level, exp_level_after, xp_now, xp_unlocked, "
                  "time_s, travel_s, kill_s, risk, justification, computed, rxp_step_no, kind, race) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (key, kidx, "MAGE", 1.0 + idx / 10, 1.1 + idx / 10, 400 if idx == 5 else 0, 0, 60.0 * idx, 20.0,
                   30.0 if idx == 2 else 0.0, 0.5 if idx == 2 else 0.1, json.dumps(just),
                   json.dumps({"t0": 60.0 * (idx - 1)}), kidx, "turnin" if idx == 5 else "travel", "Human"))
    c.commit()
    c.close()
    return store.open_ro(path)


tmp = tempfile.mkdtemp(prefix="glide-rxp-facts-")
try:
    guide = parse_guide(GUIDE)
    guide["text"] = GUIDE

    # ---- without a knowledge base: each quest's steps and the prep list from the parse alone ----
    bare = X.guide_facts(None, guide, "MAGE", "Human")
    assert bare["match"] == "none" and not bare["kb"]["ok"] and bare["kb"]["reason"], bare["kb"]
    assert bare["quests"]["100"]["steps"] == {"accept": [1], "objective": [2], "turnin": [5], "gate": []}, bare["quests"]
    assert bare["quests"]["101"]["steps"]["accept"] == [5] and bare["quests"]["101"]["steps"]["turnin"] == [6]
    kinds = [(p["kind"], p["item_id"], p["idx"]) for p in bare["prep"]]
    # the quest's own .collect is quest work, not prep; the #ah collect, the vendor buy and the bank trip are
    assert kinds == [("ah", 700, 3), ("buy", 159, 4), ("bank_out", 702, 6)], kinds
    ah, water, bank = bare["prep"]
    assert ah["name"] == "Linen Bandage" and ah["qty"] == 2 and water["name"] == "Refreshing Spring Water", bare["prep"]
    assert water["cond"] == ["Mage", None] and water["qty"] == 10 and bank["qty"] is None, water
    assert bare["plan"] is None and bare["targets"] == {} and bare["errors"] == [], bare
    json.dumps(bare)

    # ---- the guide as kb has it ----
    kb = make_kb(os.path.join(tmp, "kb.db"), GUIDE)
    t0 = time.time()
    f = X.guide_facts(kb, guide, "MAGE", None, "Alliance")
    assert time.time() - t0 < 5, time.time() - t0
    assert f["errors"] == [], f["errors"]
    assert f["kb"]["ok"] and f["kb"]["built"] == "2026-11-01" and f["kb"]["schema"] == store.SCHEMA_VERSION, f["kb"]
    assert f["match"] == "exact" and f["by"] == "key" and f["key"] == "Test Guide (A)||1-3 Testvale", f["match"]
    assert f["idx_map"] == {str(i): i for i in range(1, 7)}, f["idx_map"]
    assert f["briefs"] and f["briefs"][0]["class"] == "MAGE" and f["briefs"][0]["time_s"] == 1800, f["briefs"]
    # quest chain: Wolves -> More Wolves (next_quest) -> Report (a 'pre' row); skipping Wolves loses both, Report
    # being turned in by the next guide; an 'exclusive' row is listed both ways
    q100, q101 = f["quests"]["100"], f["quests"]["101"]
    assert q100["name"] == "Wolves" and q100["level"] == 2 and q100["next"] == 101 and q100["unlocks"] == [101], q100
    assert q100["chain"] == {"id": 100, "pos": 1, "n": 2} and q101["prev"] == [100], (q100, q101)
    assert f["quests"]["102"]["requires"] == [101] and q100["exclusive"] == [103], f["quests"]["102"]
    lost = {x["id"]: x for x in q100["lost_if_skipped"]}
    assert set(lost) == {101, 102}, q100["lost_if_skipped"]
    assert lost[101]["name"] == "More Wolves" and lost[101]["accept"] == 5 and not lost[101]["next_guide"], lost
    assert lost[102]["accept"] == 5, lost
    assert [x["id"] for x in f["quests"]["102"]["lost_if_skipped"]] == [], f["quests"]["102"]
    assert isinstance(q100["decay_at_level"], int) and q100["decay_at_level"] > 2, q100
    # map targets of the .complete step: the kill objective's wolves near the step (the far one clipped), the item
    # objective's dropper, and the .mob
    tg = {(t["kind"], t["id"]): t for t in f["targets"]["2"]}
    wolves = tg[("npc", 500)]
    assert wolves["role"] == "kill" and wolves["quest"] == 100 and wolves["obj"] == 1 and wolves["ui_map"] == 1429
    assert wolves["name"] == "Young Wolf" and all(abs(x - 52) < 5 for x, _ in wolves["points"]), wolves
    assert [90.0, 10.0] not in wolves["points"] and len(wolves["groups"]) == 1, wolves
    assert tg[("npc", 501)]["role"] == "loot" and tg[("npc", 501)]["rank"] == 1, tg
    assert set(f["targets"]) == {"2"}, f["targets"].keys()
    # prep: names from kb, the vendor selling the water near the step, and its price
    water = next(p for p in f["prep"] if p["item_id"] == 159)
    assert water["vendors"] and water["vendors"][0]["id"] == 800 and water["price"] == 25, water
    assert next(p for p in f["prep"] if p["item_id"] == 702)["name"] == "Wolf Pelt Bag"
    # the plan for the class, per step
    plan = f["plan"]
    assert plan["class"] == "MAGE" and plan["race"] == "Human" and not plan["approx"], plan
    p5 = plan["steps"]["5"]
    assert p5["time_s"] == 300.0 and p5["xp_now"] == 400 and p5["t0"] == 240.0 and p5["n"] == 5, p5
    assert p5["why"] == ["several turn-ins in one place"] and p5["quests_xp"] == {"100": 400}, p5
    assert p5["leg"] == {"mode": "walk", "dist_yd": 80.0, "from_idx": 4, "observed": False}, p5
    # hazards: the elite kill target with RestedXP's note; the elite group quest where it's done
    hz = f["hazards"]["2"]
    rage = next(h for h in hz if h["creature_id"] == 501)
    assert rage["elite"] and rage["note"] == "elite / Patrols the road / hits hard", hz
    assert any(h.get("quest_id") == 101 and "group of 3" in h["note"] for h in f["hazards"]["5"]), f["hazards"]["5"]
    json.dumps(f)

    # ---- kb holds another version of the guide: aligned by text and quests, the plan through the idx map ----
    kb2 = make_kb(os.path.join(tmp, "kb2.db"), GUIDE, extra_step=True)
    g2 = dict(guide, key=None)                  # and found by name this time
    f2 = X.guide_facts(kb2, g2, "mage")
    assert f2["errors"] == [], f2["errors"]
    assert f2["match"] == "aligned" and f2["by"] == "name", (f2["match"], f2["by"])
    assert f2["idx_map"]["1"] == 1 and f2["idx_map"]["2"] == 3 and f2["idx_map"]["6"] == 7, f2["idx_map"]
    assert f2["plan"]["approx"] and f2["plan"]["steps"]["5"]["n"] == 6 and f2["plan"]["steps"]["5"]["xp_now"] == 400
    assert f2["plan"]["steps"]["3"]["leg"]["from_idx"] == 2, f2["plan"]["steps"]["3"]   # kb idx 3 -> guide idx 2
    # kb without this guide: no match, but quest facts still come from kb
    g3 = dict(guide, key="Other||9-9 Elsewhere", name="9-9 Elsewhere", text="x")
    f3 = X.guide_facts(kb, g3, "MAGE")
    assert f3["match"] == "none" and f3["plan"] is None and "refresh" in f3["kb"]["reason"], f3["kb"]
    assert f3["quests"]["100"]["name"] == "Wolves", f3["quests"]["100"]

    # ---- the newest kb: a staged refresh build wins, another schema is refused ----
    home = os.path.join(tmp, "home")
    data = os.path.join(home, "data")
    os.makedirs(data)
    make_kb(os.path.join(data, "kb.db"), GUIDE, built="old")
    got, reason = rxp_facts.newest_kb(home)
    assert got is not None and got.meta("built") == "old" and reason is None, reason
    got.close()
    staged = os.path.join(data, "kb.next-1790000000.db")
    make_kb(staged, GUIDE, built="staged")
    os.utime(staged, (time.time() + 60, time.time() + 60))
    got, _ = rxp_facts.newest_kb(home)
    assert got.meta("built") == "staged", got.path
    got.close()
    make_kb(staged, GUIDE, built="future", schema=store.SCHEMA_VERSION + 1)
    os.utime(staged, (time.time() + 60, time.time() + 60))
    got, _ = rxp_facts.newest_kb(home)
    assert got.meta("built") == "old", got.path           # the newer one has a schema this version can't read
    assert_closed(staged)                                 # ...and isn't kept open (newest_kb closes what it refuses)
    got.close()
    remove_kb(os.path.join(data, "kb.db"))
    got, reason = rxp_facts.newest_kb(home)
    assert got is None and "schema" in reason, reason
    remove_kb(staged)
    assert rxp_facts.newest_kb(home) == (None, "no knowledge base yet")

    # ---- the command: an added guide in a temp home, with and without kb ----
    os.makedirs(os.path.dirname(os.path.join(home, R.STORE)), exist_ok=True)
    with open(os.path.join(home, R.STORE), "w", encoding="utf-8") as fh:
        json.dump({"imports": {"abc": {"added": 1, "guides": [{"group": "Test Guide (A)", "name": "1-3 Testvale",
                                                               "text": GUIDE}]}}}, fh)
    out = rxp_facts.facts(home, "abc", 0, "-", "-")
    assert out["match"] == "none" and out["kb"]["reason"] == "no knowledge base yet" and len(out["prep"]) == 3, out
    make_kb(os.path.join(data, "kb.db"), GUIDE)
    out = rxp_facts.facts(home, "abc", 0, "mage", "Human")
    assert out["match"] == "exact" and out["plan"]["class"] == "MAGE" and out["errors"] == [], out["errors"]
    assert out["targets"]["2"] and out["quests"]["100"]["lost_if_skipped"], out["quests"]["100"]
    assert_closed(os.path.join(data, "kb.db"))
    try:
        rxp_facts.facts(home, "abc", 3)
        raise AssertionError("an index past the import's guides opened something")
    except R.ImportProblem:
        pass
    # names for the Guides page's badges: kb first, glide.db for what kb lacks
    g = sqlite3.connect(os.path.join(data, "glide.db"))
    g.execute("CREATE TABLE quests(id INTEGER PRIMARY KEY, name TEXT)")
    g.execute("CREATE TABLE entities(kind TEXT, id INT, name TEXT)")
    g.executemany("INSERT INTO quests VALUES (?, ?)", [(100, "Old name"), (999, "Only in glide.db")])
    g.execute("INSERT INTO entities VALUES ('npc', 501, 'Old Ragefang')")
    g.commit()
    g.close()
    names = R._entity_names(home, {100, 999}, {501}, {159})     # noqa: SLF001
    assert names["quests"] == {100: "Wolves", 999: "Only in glide.db"}, names
    assert names["npcs"] == {501: "Ragefang"} and names["items"] == {159: "Refreshing Spring Water"}, names
    assert_closed(os.path.join(data, "kb.db"))
    # facts never raises: a broken guide still answers
    junk = X.guide_facts(kb, {"steps": [{"idx": 1, "actions": [{"verb": "complete", "args": None}]}]}, "MAGE")
    assert isinstance(junk, dict) and junk["match"] == "none", junk
    assert X.guide_facts(kb, None)["quests"] == {}
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
