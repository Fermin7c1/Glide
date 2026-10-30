"""Tests for knowledge/rxp_parser.py on synthetic lines and the fixture guides (tests/kb/fixture.py); the same
checks on the player's installed RestedXP guides are the report-only tests/kb/smoke_rxp_parser.py.
Plain python3, no pytest: `python3 tests/kb/test_rxp_parser.py`."""
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import fixture  # noqa: E402
from knowledge import rxp_parser as P  # noqa: E402
from knowledge import store  # noqa: E402

DM = fixture.DM


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def corpus(glide_db):
    c = sqlite3.connect("file:%s?mode=ro" % glide_db.replace("\\", "/"), uri=True)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT guide_key, source, text FROM rxp_guides").fetchall()
    c.close()
    return rows


def acts(step, verb):
    return [a for a in step["actions"] if a["verb"] == verb]


# ------------------------------------------------------------------------------------------------
def test_plain():
    check(P.plain("|cRXP_ENEMY_Crag Boars|r") == "Crag Boars", "enemy colour")
    check(P.plain("Loot |T133970:0|t|cRXP_LOOT_[Chunks of Boar Meat]|r now") == "Loot [Chunks of Boar Meat] now", "icon")
    check(P.plain("|cRXP_WARN_Save all the|r |T133970:0|tmeat") == "Save all the meat", "warn kept")
    check(P.plain("|cFF0070FFShaman|r rules") == "Shaman rules", "|cffRRGGBB")
    check(P.plain("|Tinterface/worldmap/chatbubble_64grey.blp:20|tTalk to |cRXP_FRIENDLY_Eric Brighthammer::265813|r")
          == "Talk to Eric Brighthammer", "npc id suffix")
    check(P.plain("|cRXP_WARN_Prioritize |cRXP_ENEMY_Roiling Winds::251143|r|r") == "Prioritize Roiling Winds", "nested")
    check(P.plain("Use\\n|T1:0|t[Wild Harvest]") == "Use\n[Wild Harvest]", "literal \\n")
    check(P.plain("a || b") == "a | b", "escaped pipe")
    check(P.plain(None) is None and P.plain("") == "", "empty")


def test_conditions():
    c = P.parse_cond("Dwarf/Gnome")
    check(c["race"] == ["Dwarf", "Gnome"] and c["class"] == [] and c["faction"] is None, c)
    c = P.parse_cond("!Warrior !Rogue")
    check(c["not_class"] == ["Warrior", "Rogue"] and "exact" not in c, c)
    c = P.parse_cond("Undead Warrior/Skyborne Warrior")
    check(c["class"] == ["Warrior"] and c["race"] == ["Undead", "Skyborne"] and "exact" not in c, c)
    c = P.parse_cond("Horde Druid Skyborne")
    check(c["faction"] == "Horde" and c["class"] == ["Druid"] and c["race"] == ["Skyborne"], c)
    c = P.parse_cond("NightElf Hunter/Priest")                     # not flat-representable
    check(c.get("exact") is False and c["dnf"] == [["NightElf", "Hunter"], ["Priest"]], c)
    check(P.parse_cond("skip").get("never") and P.parse_cond("Warrior Rogue").get("never"), "never")
    # RXP applies() semantics
    check(P.cond_applies("Undead Warrior/Skyborne Warrior", cls="WARRIOR", race="Scourge", faction="Horde"), "undead")
    check(not P.cond_applies("Undead Warrior/Skyborne Warrior", cls="Warrior", race="Orc", faction="Horde"), "orc")
    check(P.cond_applies("!Warrior !Rogue", cls="Mage") and not P.cond_applies("!Warrior !Rogue", cls="Rogue"), "neg")
    check(P.cond_applies("(Human/Dwarf) Paladin", cls="Paladin", race="Dwarf"), "parens")
    check(not P.cond_applies("(Human/Dwarf) Paladin", cls="Paladin", race="Gnome"), "parens 2")
    check(P.cond_applies("forever", cls="Mage") and not P.cond_applies("sod", cls="Mage", faction="Alliance"), "game")
    check(P.cond_applies("sod", faction="Alliance", season=2), "sod season 2")
    check(P.cond_applies(None) and P.cond_applies("nightelf", race="NightElf") is False, "case-sensitive race")
    both = P.and_cond("Alliance", "Dwarf/Gnome")
    check(both["raw"] == "Alliance Dwarf/Alliance Gnome" and both["faction"] == "Alliance", both)


def test_line_grammar():
    text = "\n".join([
        "#forever",
        "<< Horde",
        "#group RestedXP Test (H)",
        "#name 6-10 Test Zone",
        "#next 10-12 Next A << !Hunter",
        "#next Other Group\\12-17 Next B;12-14 Next C << Hunter",
        "-- header note",
        "step << Warrior/Rogue",
        "    #completewith next << Warrior",
        "    #completewith Foo << Rogue",
        "    #label Bar",
        "    .goto Durotar,52.0,44.5,20 >> Run |cRXP_WARN_now|r --c:note",
        "    .xp 7-1000,1 << Orc",
        "    .xp <8,-1",
        "    .xp 9.5",
        "    +1",
        "    +Click the |cRXP_PICK_thing|r",
        "    *Hidden tip << Troll",
        "    .bogus 1,2",
        "    stray words",
        "--XX between steps",
        "step",
        "    .goto 1411/1,-1234.5,-4567.25",
        "    .goto Durotar,10,20",
        "    .turnin -123,1",
        "    .complete 124,2,0,1",
        "    .target +Thrall::4949;Vol'jin;1234",
        "    .money >1.5,1",
        "    .itemcount 100/101,<=3",
    ])
    g = P.parse_guide(text, source="unit")
    check(g["key"] == "RestedXP Test (H)||6-10 Test Zone" and g["level_min"] == 6 and g["level_max"] == 10, g["key"])
    check(g["faction"] == "Horde" and g["comments"] == "-- header note", (g["faction"], g["comments"]))
    check(g["next"] == "10-12 Next A << !Hunter\nOther Group\\12-17 Next B;12-14 Next C << Hunter", g["next"])
    nl = g["next_list"]
    check([(n["group"], n["name"]) for n in nl] == [("RestedXP Test (H)", "10-12 Next A"),
                                                     ("Other Group", "12-17 Next B"),
                                                     ("RestedXP Test (H)", "12-14 Next C")], nl)
    check(nl[0]["cond"]["not_class"] == ["Hunter"] and nl[2]["cond"]["class"] == ["Hunter"], nl)
    s1, s2 = g["steps"]
    check(s1["cond"]["class"] == ["Warrior", "Rogue"], s1["cond"])
    check(s1["flags"]["label"] == "Bar" and "completewith" not in s1["flags"], s1["flags"])
    check([(c["tag"], c["value"]) for c in s1["flags"]["cond_flags"]] == [("completewith", "next"),
                                                                          ("completewith", "Foo")], s1["flags"])
    check(P.step_flags(s1, cls="Warrior")["completewith"] == "next", "warrior flags")
    check(P.step_flags(s1, cls="Rogue")["completewith"] == "Foo", "rogue flags")
    check("completewith" not in P.step_flags(s1, cls="Mage"), "mage flags")
    go = acts(s1, "goto")[0]
    check(go["args"]["ui_map"] == 1411 and go["args"]["x"] == 52.0 and go["args"]["radius"] == 20.0, go)
    check(go["args"]["mode"] == "arrive" and go["args"]["comment"] == "c:note" and go["text"] == "Run now", go)
    check(s1["ui_map"] == 1411 and s1["zone"] == "Durotar" and s1["x"] == 52.0 and s1["wx"] is None, s1)
    x1, x2, x3 = acts(s1, "xp")
    check(x1["args"]["level"] == 7 and x1["args"]["xp"] == -1000 and x1["args"]["xp_kind"] == "before", x1)
    check(x1["args"]["skip_when"] == "at_or_above" and x1["cond"]["race"] == ["Orc"], x1)
    check(x2["args"].get("reverse") and x2["args"]["skip_when"] == "below", x2)
    check(x3["args"]["level"] == 9 and x3["args"]["xp"] == 0.5 and x3["args"]["xp_kind"] == "frac", x3)
    texts = [(a["args"].get("kind"), a["text"]) for a in acts(s1, "text")]
    check(texts == [("+", "1"), ("+", "Click the thing"), ("*", "Hidden tip")], texts)
    check(s1["text"] == "Run now\nClick the thing\nHidden tip", s1["text"])        # '+1' placeholder skipped
    bogus = acts(s1, "bogus")[0]
    check(bogus["args"]["unknown_verb"] and bogus["args"]["list"] == ["1", "2"], bogus)
    check(any(a["verb"] is None and a["args"]["raw"] == "stray words" for a in s1["actions"]), "ignored line kept")
    check(s1["comments"] == "--XX between steps", s1["comments"])
    check(s2["continent"] == 1 and s2["wy"] == -1234.5 and s2["wx"] == -4567.25 and s2["x"] is None, s2)
    t = acts(s2, "turnin")[0]
    check(t["quest_id"] == 123 and t["args"]["skip_if_missing"] and t["args"]["skip_if_incomplete"], t)
    cp = acts(s2, "complete")[0]
    check(cp["quest_id"] == 124 and cp["args"]["obj"] == 2 and cp["args"]["text_only"] and cp["qty"] is None, cp)
    tg = acts(s2, "target")[0]
    check(tg["args"]["parent"] and tg["args"]["units"] == [{"name": "Thrall", "id": 4949},
                                                            {"name": "Vol'jin", "id": None},
                                                            {"name": None, "id": 1234}], tg)
    check(tg["entity_kind"] == "npc" and tg["entity_id"] == 4949 and tg["args"]["name"] == "Thrall", tg)
    m = acts(s2, "money")[0]["args"]
    check(m["op"] == ">" and m["copper"] == 15000 and m["net_worth"] and m["skip_when"] == "at_or_above", m)
    ic = acts(s2, "itemcount")[0]
    check(ic["args"]["item_ids"] == [100, 101] and ic["args"]["cmp"] == "<=" and ic["qty"] == 3, ic)
    check(any("invalid function call .bogus" in w for w in g["warnings"]), g["warnings"])
    check(P.rxp_index_map(g, cls="Mage") == {1: 2} and P.rxp_index_map(g, cls="Rogue") == {1: 1, 2: 2}, "index map")


def test_corpus(glide_db):
    """Every fixture guide parses under its glide.db key; the step counts are the guides' own `step` lines."""
    rows = corpus(glide_db)
    guides, errors = {}, []
    for r in rows:
        try:
            guides[r["guide_key"]] = P.parse_guide(r["text"], source=r["source"])
        except Exception as e:
            errors.append((r["guide_key"], repr(e)))
    check(not errors, errors)
    check(sorted(guides) == sorted(fixture.GUIDE_FILES), sorted(guides))
    keymiss = [(k, g["key"]) for k, g in guides.items() if g["key"] != k]
    check(not keymiss, keymiss)
    for k, g in guides.items():
        n = sum(1 for line in fixture.guide_text(k).splitlines() if line.split("<<")[0].strip() == "step")
        check(len(g["steps"]) == n and not g["warnings"], (k, len(g["steps"]), n, g["warnings"]))
    print("corpus: %d fixture guides, %d steps" % (len(guides), sum(len(g["steps"]) for g in guides.values())))
    return guides


def test_world_axes(guides):
    """World-form gotos (`.goto 1426/0,wy,wx`) carry the map-% equivalent in `--c:Zone,x,y`: x% must be an exact
    linear function of wy and y% of wx (store.py's convention)."""
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
    check(len(pts.get(1426, ())) >= 3, pts)
    wx, wy, x, y = zip(*pts[1426])
    rx, ry = statistics.correlation(wy, x), statistics.correlation(wx, y)
    check(abs(rx) > 0.999 and abs(ry) > 0.999, (rx, ry))


def test_fixture_guide(guides):
    """The Dun Morogh fixture guide, field by field."""
    g = guides[DM]
    check(g["name"] == "5-8 Dun Morogh" and g["level_min"] == 5 and g["level_max"] == 8, g["name"])
    check(g["faction"] == "Alliance" and g["default_for"] == "Dwarf/Gnome" and g["season"] == "0,1", g["faction"])
    check(g["rxp_name"] == "05-08 Dun Morogh", g["rxp_name"])                     # RestedXP's zero-padded name
    check(g["next_list"] == [{"name": "8-10 Loch Modan", "group": fixture.GROUP, "cond": None}], g["next_list"])
    s = g["steps"]
    check(len(s) == 18 and [x["idx"] for x in s] == list(range(1, 19)), len(s))
    check([a["quest_id"] for a in acts(s[0], "accept")] == [90001] and acts(s[0], "target")[0]["entity_id"] == 90201,
          s[0])
    check(s[0]["ui_map"] == 1426 and (s[0]["x"], s[0]["y"]) == (40.0, 50.0) and s[0]["zone"] == "Dun Morogh", s[0])
    # step 2: an optional grind with a Priest and a !Priest .xp threshold
    check(s[1]["flags"].get("optional") is True and "--XX 270 from the priest quest" in (s[1]["comments"] or ""), s[1])
    xps = acts(s[1], "xp")
    pri = [a for a in xps if a["cond"] and a["cond"]["class"] == ["Priest"]]
    npri = [a for a in xps if a["cond"] and a["cond"]["not_class"] == ["Priest"]]
    check(pri and pri[0]["args"]["level"] == 5 and pri[0]["args"]["xp"] == 1325, pri)
    check(npri and npri[0]["args"]["level"] == 5 and npri[0]["args"]["xp"] == 1595, npri)
    check(any(a["args"]["name"] == "Fixture Boar" for a in acts(s[1], "mob")), "mob")
    check(s[2]["flags"].get("hardcore") is True and s[3]["flags"].get("softcore") is True, "hardcore / softcore")
    check(acts(s[2], "subzone")[0]["entity_id"] == 131 and acts(s[3], "deathskip"), "subzone / deathskip")
    # step 5: a world-form goto: the world position, no map position until the KB build converts it
    g5 = acts(s[4], "goto")[0]["args"]
    check(g5["continent"] == 0 and g5["wy"] == -463.417 and g5["wx"] == -5584.417 and g5["ui_map"] == 1426, g5)
    check(s[4]["x"] is None and s[4]["wx"] == -5584.417 and s[4]["continent"] == 0, s[4])
    check([a["quest_id"] for a in acts(s[4], "turnin")] == [90001] and
          [a["quest_id"] for a in acts(s[4], "accept")] == [90002], "turnin / accept")
    t = acts(s[4], "target")[0]
    check(t["args"]["name"] == "Bram Ender" and t["entity_kind"] == "npc" and t["entity_id"] == 90202, t)
    cp = acts(s[5], "complete")[0]
    check(cp["quest_id"] == 90002 and cp["args"]["obj"] == 1, cp)
    col = acts(s[9], "collect")[0]
    check((col["entity_kind"], col["entity_id"], col["qty"], col["quest_id"]) == ("item", 90301, 5, 90003), col)
    check(col["args"]["comment"] == "Collect Fixture Tusk (x5)" and "|c" not in s[9]["text"], col)
    check(s[7]["cond"]["class"] == ["Warlock"] and acts(s[7], "trainer"), s[7]["cond"])
    v = acts(s[8], "vendor")[0]
    check(v["entity_kind"] == "npc" and v["entity_id"] == 90206 and v["args"]["npc_id"] == 90206, v)
    m = acts(s[8], "money")[0]["args"]
    check(m["op"] == "<" and m["copper"] == 100 and m["skip_when"] == "below", m)
    check(s[14]["cond"].get("never"), "step << skip")
    check(acts(s[15], "hs") and s[15]["flags"].get("completewith") == "next", "hearth step")
    check(acts(s[17], "xp")[0]["args"]["level"] == 8, "grind to 8")
    # RXP's live step numbers skip non-applicable steps: a Dwarf Warrior never sees the Warlock steps 8, 9 or
    # the `step << skip` 15
    imap = P.rxp_index_map(g, cls="Warrior", race="Dwarf", faction="Alliance")
    check(imap[8] == 10 and 8 not in imap.values() and 9 not in imap.values() and 15 not in imap.values(), imap)
    check(P.rxp_index_map(g, cls="Warlock", race="Gnome", faction="Alliance")[8] == 8, "a warlock sees them")


def test_includes(guides):
    import copy
    gs = copy.deepcopy(guides)
    stats = P.expand_includes(gs)
    d = gs[fixture.DDM]["steps"]
    src = guides[DM]["steps"]
    check(len(d) == len(src), (len(d), len(src)))
    check(all(a["flags"]["included_from"] == DM and a["flags"]["included_idx"] == b["idx"] and a["text"] == b["text"]
              and len(a["actions"]) == len(b["actions"]) for a, b in zip(d, src)), "copied steps")
    check(guides[fixture.DDM]["steps"][0]["flags"]["include"] == fixture.GROUP + "\\5-8 Dun Morogh",
          "parse_guide keeps the include flag")
    # label range NormalRouteStart-NormalRouteEnd inside `step << Dwarf/Gnome`, then the dungeon guide's own steps
    lm = gs[fixture.DLM]["steps"]
    inc = [s for s in lm if s["flags"].get("included_from")]
    check(lm[0]["flags"]["label"] == "NormalRouteStart" and lm[0]["flags"]["included_idx"] == 1, lm[0]["flags"])
    check(inc[-1]["flags"]["label"] == "NormalRouteEnd" and len(inc) == 3 and len(lm) == 5, len(lm))
    check(all(s["cond"] and "Dwarf" in s["cond"]["raw"] for s in inc), "included steps inherit the condition")
    check([s["idx"] for s in lm] == list(range(1, len(lm) + 1)), "renumbered")
    check(lm[3]["flags"].get("dungeon") == "RFK", lm[3]["flags"])
    check(lm[-1]["flags"].get("include_unresolved"), lm[-1])
    check(stats["resolved"] == 2 and [u["include"] for u in stats["unresolved"]] ==
          [fixture.GROUP + "\\Fixture Class Quests"], stats)


def test_load(glide_db):
    ctx = store.BuildContext(glide_db=glide_db, log=lambda *a: None)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store.create_schema(conn)
    t0 = time.time()
    r = P.load(conn, ctx)
    dt = time.time() - t0
    check(not r["errors"] and r["guides"] == 4 and r["includes_resolved"] == 2, r)
    check(len(r["includes_unresolved"]) == 1 and r["warnings"] == 0, r)
    check(dt < 10, "load took %.1fs" % dt)
    q = lambda sql, *a: conn.execute(sql, a).fetchall()  # noqa: E731
    check(q("SELECT count(*) FROM rxp_guide")[0][0] == 4, "rxp_guide rows")
    row = q("SELECT * FROM rxp_guide WHERE key=?", DM)[0]
    check(row["level_min"] == 5 and row["faction"] == "Alliance" and row["text"].startswith("#forever"), dict(row))
    st = q("SELECT * FROM rxp_step WHERE guide_key=? AND idx=2", DM)[0]
    check(st["ui_map"] == 1426 and "--XX 270 from the priest quest" in st["comments"], dict(st))
    check(store.jload(st["flags"]) == {"optional": True}, st["flags"])
    a = q("SELECT * FROM rxp_action WHERE guide_key=? AND idx=10 AND verb='collect' ORDER BY line", DM)
    check(a[0]["entity_id"] == 90301 and a[0]["qty"] == 5 and a[0]["quest_id"] == 90003, dict(a[0]))
    check(store.jload(a[0]["args"])["raw"] == "90301,5,90003,1", a[0]["args"])
    n_dm = q("SELECT count(*) FROM rxp_step WHERE guide_key=?", DM)[0][0]
    n_dg = q("SELECT count(*) FROM rxp_step WHERE guide_key=?", fixture.DDM)[0][0]
    check(n_dm == n_dg == 18, (n_dm, n_dg))
    r2 = P.load(conn, ctx)                                                          # idempotent
    check(r2["steps"] == r["steps"] and q("SELECT count(*) FROM rxp_step")[0][0] == r["steps"], "reload")
    print("load: %d guides, %d steps, %d actions in %.2fs" % (r["guides"], r["steps"], r["actions"], dt))


def main():
    test_plain()
    test_conditions()
    test_line_grammar()
    tmp = fixture.tmpdir("rxp-")
    try:
        glide_db = os.path.join(tmp, "glide.db")
        fixture.glide_db(glide_db)
        guides = test_corpus(glide_db)
        test_fixture_guide(guides)
        test_world_axes(guides)
        test_includes(guides)
        test_load(glide_db)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("verbs with argument parsers: %d" % len(P.PARSED_VERBS))
    print("OK")


if __name__ == "__main__":
    main()
