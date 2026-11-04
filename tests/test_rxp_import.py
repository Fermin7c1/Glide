#!/usr/bin/env python3
"""RestedXP guide strings added in the app (companion/rxp_import.py, addon RXPImport.lua).

The engine side: RestedXP's cipher (RC4, pinned to RFC 6229's first vector) and the key it makes from a BattleTag;
a string opened with the key RestedXP saved for an account on this PC (as its 16 numbers, or as the BattleTag it
sometimes leaves there), never with another account's; the guides kept for the knowledge build (tools/build_db.py
reads them); the addon's Data/RXPImports.lua written as Lua the game loads, and emptied once the addon noted
RestedXP took the string. Strings that aren't one, or are another account's, say why. Only the latest pack an
account added stays: it replaces the account's earlier ones in the store, the knowledge build and the hand-off,
carries the guides it replaces (drop), and is handed over again when a replaced pack had reached RestedXP too.

The addon side, under Lua 5.1 with a mocked WoW API and a stand-in for RestedXP's ImportGuide: after login each waiting
pack's decrypted guides go to RestedXP's ImportGuide in batches, the outcome is noted in GlideRecorderDB.rxpImports,
nothing goes twice, a failure is retried only by /glide rxpimport, and without RestedXP nothing breaks. Once RestedXP
took a whole pack, its cache keeps one copy of each of the pack's guides (the latest) and none of the guides the
pack replaces; a pack it didn't take whole leaves the cache alone. Needs lupa.

Run: python3 tests/test_rxp_import.py   (prints OK)
"""
import base64
import json
import os
import shutil
import sys
import tempfile
import zlib

from lupa import lua51

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
sys.path.append(os.path.join(ROOT, "companion"))      # after ROOT: `knowledge` stays the package
sys.path.append(os.path.join(ROOT, "tools"))
import wowmock  # noqa: E402
import rxp_import as R  # noqa: E402
import build_db  # noqa: E402

# ---- the cipher and the key -------------------------------------------------------------------------------------
# RC4 with the 128-bit key 01..10 (RFC 6229, offset 0): RestedXP's cipher is plain RC4 with a 16-byte key
assert R._rc4(list(range(1, 17)), bytes(16)).hex() == "9ac7cc9a609d1ef7b2932899cde41b97"
# the key from a BattleTag: lower case (ASCII only), its last 16 bytes right-aligned, mixed in place
assert R.key_from_battletag("Tester#1234") == [69, 6, 4, 84, 82, 68, 49, 101, 7, 101, 115, 1, 87, 85, 23, 68]
assert R.key_from_battletag("TESTER#1234") == R.key_from_battletag("tester#1234")
assert R.key_from_battletag("Ünïcode#1") != R.key_from_battletag("ünïcode#1")     # Lua's lower() leaves Ü alone
long_tag = "AVeryLongBattleTagName#12345"
assert R.key_from_battletag(long_tag) == R.key_from_battletag("Zzzzz" + long_tag[-16:]), "only the last 16 bytes count"
assert all(len(R.key_from_battletag(t)) == 16 and all(0 <= x <= 255 for x in R.key_from_battletag(t))
           for t in ("a#1", "Tester#1234", "AVeryLongBattleTagName#12345"))
# RXPData.cache: the 16 numbers (keys 0..15 as the SavedVariables reader gives them), or the tag itself
k = R.key_from_battletag("Tester#1234")
assert R.key_from_cache({i: v for i, v in enumerate(k)}) == k
assert R.key_from_cache("Tester#1234") == k
assert R.key_from_cache({0: 1}) is None and R.key_from_cache(None) is None and R.key_from_cache({i: 300 for i in range(16)}) is None


def encode(texts, tag, version=40000):
    """An import string as RestedXP makes one for this BattleTag: <count>|<adler32>:<base64 of RC4(zlib)>%|<ver>."""
    plain = "\0".join(texts).encode("utf-8")
    enc = R._rc4(R.key_from_battletag(tag), zlib.compress(plain))
    return "%d|%d:%s%%|%d" % (len(texts), zlib.adler32(plain), base64.b64encode(enc).decode("ascii"), version)


G1 = ("#version 3\n#group RestedXP Horde 1-30 << Horde\n#name 1-6 Durotar\nstep\n    .accept 4641 >>Accept Your "
      "Place In The World\nstep\n    .goto Durotar,43.3,68.5\n    .turnin 4641 >>Turn in Your Place In The World\n")
G2 = "#group RestedXP Horde 1-30\n#name 6-11 Durotar\n#next 11-14 The Barrens\nstep\n    .goto Durotar,52.0,68.0\n"
G3 = "#group RestedXP Horde 30-60\n#name 30-32 Thousand Needles \"quoted\"\nstep\n    .accept 1149\n"

# ---- a WoW folder with two accounts that run RestedXP -------------------------------------------------------------
tmp = tempfile.mkdtemp(prefix="glide_rxp_import_")
try:
    wow, home = os.path.join(tmp, "wow"), os.path.join(tmp, "home")
    os.makedirs(os.path.join(wow, "Interface", "AddOns", "Glide", "Data"))
    sv = {}
    for acct in ("MAIN", "ALT"):
        sv[acct] = os.path.join(wow, "WTF", "Account", acct, "SavedVariables")
        os.makedirs(sv[acct])
    key_lines = "\n".join("\t\t%s, -- [%d]" % (v, i) for i, v in enumerate(R.key_from_battletag("Main#1111")) if i)
    with open(os.path.join(sv["MAIN"], "RXPGuides.lua"), "w", encoding="utf-8") as fh:   # as RestedXP saves the key
        fh.write('RXPData = {\n\t["cache"] = {\n\t\t[0] = %d,\n%s\n\t},\n}\nRXPDB = {\n}\n'
                 % (R.key_from_battletag("Main#1111")[0], key_lines))
    with open(os.path.join(sv["ALT"], "RXPGuides.lua"), "w", encoding="utf-8") as fh:    # the BattleTag left there
        fh.write('RXPData = {\n\t["cache"] = "Alt#2222",\n}\n')
    keys = dict(R.account_keys(wow))
    assert keys == {"ALT": R.key_from_battletag("Alt#2222"), "MAIN": R.key_from_battletag("Main#1111")}, keys

    # ---- strings that can't be added say why -------------------------------------------------------------------
    def problem(text, wow_dir=wow):
        try:
            R.add(home, wow_dir, text)
        except R.ImportProblem as e:
            return str(e)
        raise AssertionError("added: %r" % text[:40])

    assert "isn't a RestedXP guide string" in problem("   ")
    assert "incomplete" in problem("hello world 12")
    assert "incomplete" in problem(encode([G1], "Main#1111").rsplit("%", 1)[0])          # cut short
    assert "different Battle.net account" in problem(encode([G1], "Stranger#9999"))
    assert "Set the WoW folder" in problem(encode([G1], "Main#1111"), wow_dir=None)
    nokeys = os.path.join(tmp, "wow-nokeys")
    os.makedirs(os.path.join(nokeys, "WTF"))
    assert "hasn't saved your account" in problem(encode([G1], "Main#1111"), wow_dir=nokeys)
    assert not os.path.exists(os.path.join(home, R.STORE)), "nothing kept from a string that wasn't added"

    # ---- a string for MAIN, pasted with the line breaks a chat window adds ----------------------------------------
    s1 = encode([G1, G2], "Main#1111")
    pasted = "  " + s1[:50] + "\r\n" + s1[50:] + "\n"
    r = R.add(home, wow, pasted, now=1_790_000_000)
    assert r["ok"] and r["account"] == "MAIN" and r["guides"] == ["1-6 Durotar", "6-11 Durotar"], r
    assert r["handoff"] and not r["again"], r
    # the same string as RestedXP's downloaded guide file, read by the app as text: a BOM, wrapped lines, a newline
    as_file = "\ufeff" + "\n".join(s1[i:i + 76] for i in range(0, len(s1), 76)) + "\r\n"
    assert R.parse(as_file)["string"] == s1
    again = R.add(home, wow, as_file, now=1_790_000_500)
    assert again["again"] and again["id"] == r["id"], again
    assert R.load_store(home)["imports"][r["id"]]["added"] == 1_790_000_000, "added keeps the first time"
    # a string for ALT (whose RestedXP left the BattleTag in RXPData.cache)
    r3 = R.add(home, wow, encode([G3], "Alt#2222"), now=1_790_000_100)
    assert r3["account"] == "ALT" and r3["guides"] == ['30-32 Thousand Needles "quoted"'], r3

    # the knowledge build reads the guide texts, keyed group||name (the "<< Horde" of a group dropped)
    texts = build_db.rxp_imported(os.path.join(home, R.STORE))
    assert set(texts) == {"RestedXP Horde 1-30||1-6 Durotar", "RestedXP Horde 1-30||6-11 Durotar",
                          'RestedXP Horde 30-60||30-32 Thousand Needles "quoted"'}, sorted(texts)
    assert texts["RestedXP Horde 1-30||1-6 Durotar"]["text"] == G1
    assert texts["RestedXP Horde 1-30||1-6 Durotar"]["_t"] == 1_790_000_000
    assert build_db.rxp_imported(os.path.join(tmp, "missing.json")) == {}

    # the addon's file: Lua the game loads, with the decrypted guide texts (never the encrypted string)
    handoff = os.path.join(wow, R.HANDOFF)
    handoff_text = open(handoff, encoding="utf-8").read()
    assert s1 not in handoff_text, "the encrypted string is already decrypted before writing Data/RXPImports.lua"
    L = lua51.LuaRuntime(unpack_returned_tuples=True)
    ns = L.table()
    L.eval("function(src, ns) local f, e = loadstring(src, '@RXPImports.lua') if not f then error(e) end f('Glide', ns) end")(
        handoff_text, ns)
    got = {e.id: (e.n, e.k, [e.guides[i] for i in range(1, len(e.guides) + 1)]) for e in ns.rxpImports.values()}
    # each pack carries its buyer account's key check: the addon loads it only into that account
    k_main, k_alt = R.key_check(R.key_from_battletag("Main#1111")), R.key_check(R.key_from_battletag("Alt#2222"))
    assert got == {r["id"]: (2, k_main, [G1, G2]), r3["id"]: (1, k_alt, [G3])}, got
    assert k_main != k_alt and 'id = "x"' not in R.handoff_lua([("x", {"guides": [{"text": G1}]})]), \
        "a pack with no account check isn't written"

    st = {x["id"]: x for x in R.status(home, wow)}
    assert st[r["id"]]["rxp"] == "waiting" and st[r["id"]]["guides"] == ["1-6 Durotar", "6-11 Durotar"], st
    assert [it["steps"] for it in st[r["id"]]["items"]] == [2, 1], st[r["id"]]["items"]
    gd = R.guide_detail(home, r["id"], 0)
    assert gd["name"] == "1-6 Durotar" and len(gd["steps"]) == 2 and gd["steps"][1]["x"] == 43.3, gd
    assert gd["steps"][0]["actions"][0]["verb"] == "accept" and gd["steps"][0]["actions"][0]["quest_id"] == 4641
    assert [x["id"] for x in R.status(home, wow)] == [r3["id"], r["id"]], "newest first"

    # the addon noted the hand-offs (GlideRecorderDB.rxpImports): MAIN's string failed on ALT but RestedXP took it
    # on MAIN; ALT's failed. The file keeps only what RestedXP doesn't have.
    with open(os.path.join(sv["MAIN"], "Glide.lua"), "w", encoding="utf-8") as fh:
        fh.write('GlideRecorderDB = {\n\t["rxpImports"] = {\n\t\t["%s"] = {\n\t\t\t["t"] = 1790000900,\n'
                 '\t\t\t["ok"] = true,\n\t\t},\n\t},\n}\n' % r["id"])
    with open(os.path.join(sv["ALT"], "Glide.lua"), "w", encoding="utf-8") as fh:
        fh.write('GlideRecorderDB = {\n\t["rxpImports"] = {\n\t\t["%s"] = {\n\t\t\t["t"] = 1790000950,\n'
                 '\t\t\t["ok"] = false,\n\t\t\t["err"] = "Account mismatch",\n\t\t},\n\t\t["%s"] = {\n'
                 '\t\t\t["t"] = 1790000960,\n\t\t\t["ok"] = false,\n\t\t\t["err"] = "Incompatible guide",\n\t\t},\n'
                 '\t},\n}\n' % (r["id"], r3["id"]))
    st = {x["id"]: x for x in R.status(home, wow)}
    assert st[r["id"]]["rxp"] == "done", st[r["id"]]
    # Regression (review of 7d21f49): another account's note never marks a pack delivered. With only an ok note
    # from ALT for MAIN's pack, it stays waiting and in the file, so MAIN still gets it.
    main_sv = open(os.path.join(sv["MAIN"], "Glide.lua"), encoding="utf-8").read()
    os.remove(os.path.join(sv["MAIN"], "Glide.lua"))
    alt_sv = open(os.path.join(sv["ALT"], "Glide.lua"), encoding="utf-8").read()
    with open(os.path.join(sv["ALT"], "Glide.lua"), "w", encoding="utf-8") as fh:
        fh.write(alt_sv.replace('["ok"] = false,\n\t\t\t["err"] = "Account mismatch",', '["ok"] = true,', 1))
    assert {x["id"]: x for x in R.status(home, wow)}[r["id"]]["rxp"] == "waiting"
    R.sync(home, wow)
    assert r["id"] in open(handoff, encoding="utf-8").read(), "the buyer's pack stays for the buyer's account"
    with open(os.path.join(sv["MAIN"], "Glide.lua"), "w", encoding="utf-8") as fh:
        fh.write(main_sv)
    with open(os.path.join(sv["ALT"], "Glide.lua"), "w", encoding="utf-8") as fh:
        fh.write(alt_sv)
    st = {x["id"]: x for x in R.status(home, wow)}
    assert st[r["id"]]["rxp"] == "done", st[r["id"]]
    assert st[r3["id"]]["rxp"] == "failed" and st[r3["id"]]["error"] == "Incompatible guide", st[r3["id"]]
    assert R.sync(home, wow) == handoff
    left = open(handoff, encoding="utf-8").read()
    assert r["id"] not in left and r3["id"] in left, left[:300]
    # an addon install puts back the shipped, empty file: sync writes the waiting guides again
    shutil.copy(os.path.join(ROOT, "addon", "Glide", "Data", "RXPImports.lua"), handoff)
    assert r3["id"] not in open(handoff, encoding="utf-8").read()
    R.sync(home, wow)
    assert r3["id"] in open(handoff, encoding="utf-8").read()
    # no Glide addon in the folder: nothing written there, the guides are still the coach's
    assert R.sync(home, os.path.join(tmp, "elsewhere")) is None
    # an older addon (before 0.17.2) reads encrypted strings: its file is left alone until the addon is updated
    toc = os.path.join(wow, "Interface", "AddOns", "Glide", "Glide.toc")
    with open(toc, "w", encoding="utf-8") as fh:
        fh.write("## Interface: 16001\n## Version: 0.17.1\n")
    before = open(handoff, encoding="utf-8").read()
    with open(handoff, "w", encoding="utf-8") as fh:
        fh.write("-- an 0.17.1 file\n")
    assert R.sync(home, wow) is None and open(handoff, encoding="utf-8").read() == "-- an 0.17.1 file\n"
    with open(toc, "w", encoding="utf-8") as fh:
        fh.write("## Interface: 16001\n## Version: 0.17.3\n")
    assert R.sync(home, wow) == handoff and open(handoff, encoding="utf-8").read() == before
    # a record with no guide text is skipped; control bytes + following digits round-trip through Lua 5.1 intact
    assert "ns.rxpImports = {\n}" in R.handoff_lua([("x", {"guides": []})])
    assert R._lua_string('a"b\\c\n0\x019') == '"a\\"b\\\\c\\n0\\0019"'
    assert L.eval(R._lua_string('a"b\\c\n0\x019')) == 'a"b\\c\n0\x019'

    # ---- RestedXP's free guides (the starter zones to ~20) come with its addon, not in a bought string ----------
    # the Explorer lists them after the added strings, read from the installed addon's Guides/forever
    rxp = os.path.join(wow, "Interface", "AddOns", "RXPGuides")
    os.makedirs(os.path.join(rxp, "Guides", "forever", "dungeon"))
    os.makedirs(os.path.join(rxp, "Guides", "classic"))
    with open(os.path.join(rxp, "RXPGuides.toc"), "w", encoding="utf-8") as fh:
        fh.write("## Interface: 11507\n## Version: v4.11.15\n")
    free_durotar = ("RXPGuides.RegisterGuide([[\n#classic\n#group RestedXP Forever Guide (H)\n#name 1-6 Durotar\n"
                    "step\n    .goto Durotar,43.3,68.5\n]])\nRXPGuides.RegisterGuide([[\n#group RestedXP Forever Guide (H)\n"
                    "#name 6-12 Durotar\nstep\n    .accept 4641\n]])\n"
                    # the same group and name as a bought guide (G1): the bought one wins, the free one isn't listed
                    "RXPGuides.RegisterGuide([[\n#group RestedXP Horde 1-30\n#name 1-6 Durotar\nstep\n    .accept 1\n]])\n")
    with open(os.path.join(rxp, "Guides", "forever", "Horde-01-12_Durotar.lua"), "w", encoding="utf-8") as fh:
        fh.write(free_durotar)
    with open(os.path.join(rxp, "Guides", "forever", "RestedXP-Skyborne.lua"), "w", encoding="utf-8") as fh:
        fh.write("RXPGuides.RegisterGuide([[\n#group RestedXP Forever Guide (A) << Alliance\n#group RestedXP Forever "
                 "Guide (H) << Horde\n#name 1-9 Zephras Isle\nstep\n    .goto Zephras Isle,50,50\n]])\n")
    with open(os.path.join(rxp, "Guides", "forever", "dungeon", "RestedXP-Skyborne.lua"), "w", encoding="utf-8") as fh:
        fh.write("RXPGuides.RegisterGuide([[\n#group RestedXP Forever Dungeon Guide (A)\n#name 1-9 Zephras Isle\n"
                 "step\n    .goto Zephras Isle,50,50\n]])\n")
    with open(os.path.join(rxp, "Guides", "classic", "Classic-Horde-01-12_Durotar.lua"), "w", encoding="utf-8") as fh:
        fh.write("RXPGuides.RegisterGuide([[\n#group RestedXP Classic\n#name 1-6 Not Forever\nstep\n]])\n")
    listed = R.status(home, wow)
    assert [x["id"] for x in listed] == [r3["id"], r["id"], R.FREE_ID], "the free guides come last, once"
    free = listed[-1]
    assert free["free"] and free["source"] == "RestedXP v4.11.15" and free["rxp"] == "done", free
    got = sorted((it["group"], it["name"], it["faction"]) for it in free["items"])
    assert got == [("RestedXP Forever Dungeon Guide (A)", "1-9 Zephras Isle", "Alliance"),
                   ("RestedXP Forever Guide (A)", "1-9 Zephras Isle", "Alliance"),          # Zephras: both factions
                   ("RestedXP Forever Guide (H)", "1-6 Durotar", "Horde"),
                   ("RestedXP Forever Guide (H)", "1-9 Zephras Isle", "Horde"),
                   ("RestedXP Forever Guide (H)", "6-12 Durotar", "Horde")], got
    zeph = [it["index"] for it in free["items"] if it["name"] == "1-9 Zephras Isle" and "Dungeon" not in it["group"]]
    assert len(zeph) == 2 and zeph[0] == zeph[1], "both factions open the same guide"
    # the Explorer opens a free guide by its index, as it opens a bought one
    first = next(it for it in free["items"] if it["name"] == "1-6 Durotar")
    d = R.guide_detail(home, R.FREE_ID, first["index"], wow)
    assert d["name"] == "1-6 Durotar" and d["id"] == R.FREE_ID and len(d["steps"]) == 1, d
    try:
        R.guide_detail(home, R.FREE_ID, 99, wow)
        raise AssertionError("an index past the free guides opened something")
    except R.ImportProblem:
        pass
    # the free guides never go to the addon for RestedXP (it has them already)
    R.sync(home, wow)
    assert "Zephras" not in open(handoff, encoding="utf-8").read() and R.FREE_ID not in open(handoff, encoding="utf-8").read()
    # without RestedXP installed: Glide's copy (the seed snapshot the knowledge build falls back to)
    source, seed_free = R.free_guides(ROOT, None)
    names = {g["name"] for g in seed_free}
    assert source == "Glide's copy" and len(seed_free) >= 20 and "1-6 Durotar" in names, (source, len(seed_free))
    zephras = [g for g in seed_free if g["name"].endswith("Zephras Isle") and "dungeon" not in g["file"]]
    assert zephras and all(len(g["groups"]) == 2 for g in zephras), [g["groups"] for g in zephras]
    # Regression: RestedXP's Forever guides give positions in world coordinates (".goto 1411/1,-4251.46,-607.35":
    # map, continent, world), which the parser keeps as wx/wy with x/y empty, so the Explorer's map drew nothing.
    # With the knowledge base's map bounds (data/kb.db map_bounds) they land on the map, as the knowledge build's
    # finalize step puts them for the coach; a .loop's world points too.
    with open(os.path.join(rxp, "Guides", "forever", "Horde-World.lua"), "w", encoding="utf-8") as fh:
        fh.write("RXPGuides.RegisterGuide([[\n#group RestedXP Forever Guide (H)\n#name 7-9 World Coords\nstep\n"
                 "    .goto 1411/1,-4251.46,-607.35\n    .accept 4641\nstep\n    .goto 1411/1,-4281.07,-720.15\n"
                 "    .goto 1411/1,-4300.00,-800.00\n    .loop 25,1411/1,-4200,-600,-4250,-650,-4300,-600\n]])\n")
    free = R.status(home, wow)[-1]
    wi = next(it["index"] for it in free["items"] if it["name"] == "7-9 World Coords")
    no_kb = R.guide_detail(home, R.FREE_ID, wi, wow)
    assert no_kb["world_only"] == 2 and no_kb["placed"] == 0 and all(s["x"] is None for s in no_kb["steps"]), no_kb
    # Durotar's bounds, made up so the parser's columns read as is land on the map and swapped ones don't
    B = {"wx0": 1000.0, "wx1": -1500.0, "wy0": -3800.0, "wy1": -5000.0}
    import sqlite3
    os.makedirs(os.path.join(home, "data"), exist_ok=True)
    kb = sqlite3.connect(os.path.join(home, "data", "kb.db"))
    kb.execute("CREATE TABLE map_bounds(ui_map INTEGER PRIMARY KEY, continent INT, wx0 REAL, wy0 REAL, wx1 REAL, "
               "wy1 REAL, src TEXT)")
    kb.execute("INSERT INTO map_bounds VALUES (1411, 1, ?, ?, ?, ?, 'db2')", (B["wx0"], B["wy0"], B["wx1"], B["wy1"]))
    kb.commit()
    kb.close()

    def on_map(wx, wy):         # knowledge.store.world_to_map: x spans world Y, y spans world X
        return (round((wy - B["wy0"]) / (B["wy1"] - B["wy0"]) * 100, 2), round((wx - B["wx0"]) / (B["wx1"] - B["wx0"]) * 100, 2))
    d = R.guide_detail(home, R.FREE_ID, wi, wow)
    s1, s2 = d["steps"]
    assert (s1["x"], s1["y"]) == on_map(-607.35, -4251.46), (s1["x"], s1["y"], on_map(-607.35, -4251.46))
    assert d["placed"] >= 2 and 0 <= s1["x"] <= 100 and 0 <= s1["y"] <= 100, d["placed"]
    assert [(w["x"], w["y"]) for w in s2["waypoints"]] == [on_map(-720.15, -4281.07), on_map(-800.0, -4300.0)], s2
    loop = s2["loops"][0]
    assert loop["kind"] == "loop" and loop["points"] == [list(on_map(-600, -4200)), list(on_map(-650, -4250)),
                                                         list(on_map(-600, -4300))], loop

    # no addon and no seed: nothing extra listed, the added strings still are
    shutil.rmtree(rxp)
    assert [x["id"] for x in R.status(home, wow)] == [r3["id"], r["id"]]

    # ---- only the latest pack an account added stays (the player asked, 2026-11-04) -------------------------------
    # MAIN's first pack (pack_a: G1, G2) is in RestedXP already (MAIN's note above). A newer pack for MAIN has a new
    # copy of G1 (same group and name), a guide pack_a doesn't have, and no G2.
    home2 = os.path.join(tmp, "home2")
    pack_a = encode([G1, G2], "Main#1111")       # s1 again (encoding is deterministic: the same id)
    G1b = G1.replace("Place In The World", "Place In The World, again")
    G4 = "#group RestedXP Horde 1-30\n#name 11-14 The Barrens\nstep\n    .accept 840\n"
    first = R.add(home2, wow, pack_a, now=1_790_100_000)
    assert first["id"] == r["id"] and first["replaced"] == 0, first
    alt = R.add(home2, wow, encode([G3], "Alt#2222"), now=1_790_100_050)
    newer = R.add(home2, wow, encode([G1b, G4], "Main#1111"), now=1_790_100_100)
    assert newer["account"] == "MAIN" and newer["replaced"] == 2 and not newer["again"], newer
    kept = R.load_store(home2)["imports"]
    assert set(kept) == {newer["id"], alt["id"]}, "MAIN's earlier pack is gone; ALT's (another account's) stays"
    # what it replaces: G2, named as RestedXP keys it ("6-11" -> "06-11"); G1 it has itself
    assert kept[newer["id"]]["drop"] == [["RestedXP Horde 1-30", "06-11 Durotar"]], kept[newer["id"]]
    assert "hand" not in kept[newer["id"]], "never handed over yet: its own id"
    assert [x["id"] for x in R.status(home2, wow)] == [newer["id"], alt["id"]], "the Addon page lists one per account"
    texts = build_db.rxp_imported(os.path.join(home2, R.STORE))     # the coach learns the latest only
    assert set(texts) == {"RestedXP Horde 1-30||1-6 Durotar", "RestedXP Horde 1-30||11-14 The Barrens",
                          'RestedXP Horde 30-60||30-32 Thousand Needles "quoted"'}, sorted(texts)
    assert texts["RestedXP Horde 1-30||1-6 Durotar"]["text"] == G1b

    def handed():
        """{hand id: (drop, n)} in the addon's file, as the game loads it."""
        got_ns = L.table()
        L.eval("function(src, ns) local f, e = loadstring(src, '@RXPImports.lua') if not f then error(e) end "
               "f('Glide', ns) end")(open(handoff, encoding="utf-8").read(), got_ns)
        return {e.id: ([list(d.values()) for d in e.drop.values()] if e.drop else None, e.n)
                for e in got_ns.rxpImports.values()}
    # the addon's file: the newer pack with what it replaces, never the replaced pack
    assert handed() == {newer["id"]: ([["RestedXP Horde 1-30", "06-11 Durotar"]], 2), alt["id"]: (None, 1)}, handed()

    # adding the older pack again makes it the latest: it replaces the newer one and is dated now. RestedXP still
    # has it (the newer one never reached the game), so it isn't handed over again.
    back = R.add(home2, wow, pack_a, now=1_790_100_200)
    assert back["id"] == r["id"] and back["replaced"] == 2, back
    kept = R.load_store(home2)["imports"]
    assert set(kept) == {r["id"], alt["id"]} and kept[r["id"]]["added"] == 1_790_100_200, kept.keys()
    assert kept[r["id"]]["drop"] == [["RestedXP Horde 1-30", "11-14 The Barrens"]], kept[r["id"]]
    assert {x["id"]: x["rxp"] for x in R.status(home2, wow)}[r["id"]] == "done"
    assert r["id"] not in handed()

    # a store an older Glide kept: both of MAIN's packs (and one from before accounts were kept), and an older addon
    # loaded both into RestedXP. The next sync keeps the newest and hands it over again under a new id with what it
    # replaces, so RestedXP ends up with that pack only.
    def note_main(*ids):
        with open(os.path.join(sv["MAIN"], "Glide.lua"), "w", encoding="utf-8") as fh:
            fh.write('GlideRecorderDB = {\n\t["rxpImports"] = {\n' + "".join(
                '\t\t["%s"] = {\n\t\t\t["t"] = 1790200000,\n\t\t\t["ok"] = true,\n\t\t},\n' % i for i in ids) + '\t},\n}\n')
    home3, home_b = os.path.join(tmp, "home3"), os.path.join(tmp, "homeB")
    b = R.add(home_b, wow, encode([G1b, G4], "Main#1111"), now=1_790_100_100)
    no_acct = {"added": 1_780_000_000, "guides": [{"group": "X", "name": "1-2 Y", "text": "#group X\n#name 1-2 Y\n"}]}
    os.makedirs(os.path.dirname(os.path.join(home3, R.STORE)))
    with open(os.path.join(home3, R.STORE), "w", encoding="utf-8") as fh:
        json.dump({"imports": {r["id"]: R.load_store(home)["imports"][r["id"]], "old0": no_acct,
                               b["id"]: R.load_store(home_b)["imports"][b["id"]]}}, fh)
    note_main(r["id"], b["id"])
    assert R.sync(home3, wow) == handoff
    kept = R.load_store(home3)["imports"]
    assert set(kept) == {b["id"]}, kept.keys()
    hand = kept[b["id"]]["hand"]
    assert hand.startswith(b["id"] + ".") and kept[b["id"]]["drop"] == [["RestedXP Horde 1-30", "06-11 Durotar"],
                                                                         ["X", "01-02 Y"]], kept[b["id"]]
    assert handed() == {hand: ([["RestedXP Horde 1-30", "06-11 Durotar"], ["X", "01-02 Y"]], 2)}, handed()
    assert [(x["id"], x["rxp"]) for x in R.status(home3, wow)] == [(b["id"], "waiting")]
    # once the game noted the new hand-off it's done, leaves the file, and nothing changes again
    note_main(r["id"], b["id"], hand)
    assert [(x["id"], x["rxp"]) for x in R.status(home3, wow)] == [(b["id"], "done")]
    R.sync(home3, wow)
    assert handed() == {} and R.load_store(home3)["imports"][b["id"]]["hand"] == hand
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ---- the addon hands each waiting pack's decrypted guides to RestedXP's ImportGuide --------------------------------
L = lua51.LuaRuntime(unpack_returned_tuples=True)
L.execute(wowmock.MOCK)
L.execute(r"""
PRINTED = {}
function print(...) local t = {} for i = 1, select("#", ...) do t[#t+1] = tostring(select(i, ...)) end PRINTED[#PRINTED+1] = table.concat(t, " ") end
function last() return PRINTED[#PRINTED] or "" end
COMBAT_LOCK = false
function InCombatLockdown() return COMBAT_LOCK end
-- RestedXP's ImportGuide: takes one decrypted guide text and returns the parsed guide table (with .name), or fails
-- when REFUSE is set; workerFrame:IsShown() delays Glide while RestedXP's own worker is busy
CALLS = {}
MENUS = 0
REFUSE = nil
local worker = { shown = false }
function worker:IsShown() return self.shown end
function setBusy(v) worker.shown = v end
RXP = {
  workerFrame = worker,
  RXPFrame = { GenerateMenuTable = function() end },
  ScheduleTask = function(_, fn) if fn then MENUS = MENUS + 1 end end,
  guideImporter = { UpdateImportUI = function() end },
}
function RXP.ImportGuide(text)
  CALLS[#CALLS+1] = text
  if REFUSE then error(REFUSE, 0) end
  local name = text:match("#name%s+([^\r\n]+)")
  if not name then return nil end
  return { name = name, imported = true }
end
function ticks(n) for _ = 1, n do tick() end end
""")
ns = L.table()
# this account's RestedXP key (ReadCacheData's 16 numbers, keys 0..15) and another account's
MINE, OTHER = R.key_from_battletag("Main#1111"), R.key_from_battletag("Alt#2222")
L.execute("KEY = {" + ", ".join("[%d] = %d" % (i, v) for i, v in enumerate(MINE)) + "}")
L.execute("""RXP.ReadCacheData = function(mode) if mode then return nil end return KEY end
-- debugprofilestop: a clock RestedXP's parsing moves on, 10 ms a guide
CLOCK = 0
function debugprofilestop() return CLOCK end
local import = RXP.ImportGuide
RXP.ImportGuide = function(text) CLOCK = CLOCK + 10 return import(text) end""")
# "aaa" has 20 guides (more than one tick's 40 ms holds), "bbb" 1; "ccc" was bought on another account
g_many = [{"text": "#group G\n#name Guide %d\nstep\n" % i} for i in range(1, 21)]
handoff_src = R.handoff_lua([("aaa", {"guides": g_many}), ("bbb", {"guides": [{"text": G3}]}),
                             ("ccc", {"guides": [{"text": G2}]})],
                            {"aaa": R.key_check(MINE), "bbb": R.key_check(MINE), "ccc": R.key_check(OTHER)})
run = L.eval("function(src, name, a, ns) local f, e = loadstring(src, name) if not f then error(e) end return f(a, ns) end")
for name in ("Core.lua", "Data/RXPImports.lua", "RXPImport.lua"):
    src = handoff_src if name == "Data/RXPImports.lua" else open(os.path.join(ROOT, "addon", "Glide", name), encoding="utf-8").read()
    run(src, "@" + name, "Glide", ns)
G = L.globals()
calls = lambda: list(G.CALLS.values())
G.fire("ADDON_LOADED", "Glide")
G.COMBAT_LOCK = True                 # in combat at login: nothing starts until it's over
G.fire("PLAYER_LOGIN")
G.ticks(4)
assert len(G.CALLS) == 0, "waits for the end of combat"
G.COMBAT_LOCK = False
G.setBusy(True)                      # RestedXP's worker is busy: waits for it too
G.ticks(4)
assert len(G.CALLS) == 0, "waits for RestedXP's worker"
G.setBusy(False)
G.ticks(1)                           # one tick: guides until 40 ms of RestedXP's parsing (4 at 10 ms each)
assert calls() == [g["text"] for g in g_many[:4]], calls()
assert "handing the 20 RestedXP guides" in G.last(), G.last()
G.ticks(4)                           # the rest of "aaa", 4 a tick, in order, then done
assert calls() == [g["text"] for g in g_many] and G.MENUS == 1, (len(G.CALLS), G.MENUS)
notes = G.GlideRecorderDB.rxpImports
assert notes["aaa"].ok and notes["aaa"].err is None, dict(notes["aaa"])
assert any("RestedXP now has the 20 guides" in p for p in G.PRINTED.values())
G.REFUSE = "Error parsing guide: bad step"
G.ticks(1)                           # next tick tries "bbb", which fails
assert calls()[-1] == G3 and len(G.CALLS) == 21
assert notes["bbb"].ok is False and "Error parsing guide" in notes["bbb"].err, dict(notes["bbb"])
assert "/glide rxpimport tries again" in G.last(), G.last()
G.ticks(10)
assert len(G.CALLS) == 21, "a failed pack isn't retried by itself"
# Regression (review of 7d21f49): another account's pack never goes into this account's RestedXP, and leaves no note
assert G2 not in calls() and notes["ccc"] is None, "another account's guides were loaded here"

# the next login: RestedXP has "aaa", "bbb" failed: nothing goes again
G.fire("PLAYER_LOGIN")
G.ticks(10)
assert len(G.CALLS) == 21, len(G.CALLS)
# /glide rxpimport tries the failed one again, once
G.REFUSE = "Incompatible guide"
G.SlashCmdList.GLIDE("rxpimport")
G.ticks(1)
assert calls()[-1] == G3 and len(G.CALLS) == 22
assert "Incompatible guide" in notes["bbb"].err and "Incompatible guide" in G.last(), G.last()
G.ticks(10)
assert len(G.CALLS) == 22, "tried once per command"
G.REFUSE = None
G.SlashCmdList.GLIDE("rxpimport")
G.ticks(1)
assert notes["bbb"].ok and len(G.CALLS) == 23 and G.MENUS == 2
G.SlashCmdList.GLIDE("rxpimport")
assert "for another Battle.net account" in G.last(), G.last()      # only "ccc" is left, and it isn't this account's
assert G2 not in calls() and notes["ccc"] is None

# without debugprofilestop: one guide a tick, as RestedXP's own importer loads them
G.GlideRecorderDB.rxpImports = L.table()
G.debugprofilestop = None
before = len(G.CALLS)
G.SlashCmdList.GLIDE("rxpimport")
G.ticks(1)
assert len(G.CALLS) == before + 1, len(G.CALLS) - before
G.ticks(30)
assert len(G.CALLS) == before + 21 and G.GlideRecorderDB.rxpImports["aaa"].ok, len(G.CALLS) - before

# RestedXP can't say which account this is: nothing goes, said once
G.GlideRecorderDB.rxpImports = L.table()
G.RXP.ReadCacheData = None
before, said_before = len(G.CALLS), len(G.PRINTED)
G.fire("PLAYER_LOGIN")
G.ticks(10)
said = [p for p in list(G.PRINTED.values())[said_before:] if "can't say which Battle.net account" in p]
assert len(G.CALLS) == before and len(said) == 1, (len(G.CALLS) - before, list(G.PRINTED.values())[said_before:])

# without RestedXP: says so once and stops looking
G.RXP = None
before = len(G.PRINTED)
G.fire("PLAYER_LOGIN")
G.ticks(10)
said = [p for p in list(G.PRINTED.values())[before:] if "RestedXP isn't loaded" in p]
assert len(said) == 1, list(G.PRINTED.values())[before:]

# ---- RestedXP keeps the latest pack only: once it took the whole pack, the older copies of its guides and the
# guides it replaces (its drop) leave RestedXP's cache (db.profile.guides); everything else stays -------------------
L2 = lua51.LuaRuntime(unpack_returned_tuples=True)
L2.execute(wowmock.MOCK)
L2.execute("KEY = {" + ", ".join("[%d] = %d" % (i, v) for i, v in enumerate(MINE)) + "}")
L2.execute(r"""
PRINTED = {}
function print(...) local t = {} for i = 1, select("#", ...) do t[#t+1] = tostring(select(i, ...)) end PRINTED[#PRINTED+1] = table.concat(t, " ") end
function last() return PRINTED[#PRINTED] or "" end
function InCombatLockdown() return false end
function ticks(n) for _ = 1, n do tick() end end
local H = "RestedXP Horde 1-30"
OLD_COPY = { key = H .. "||01-06 Durotar" }
CACHE = {
  [H .. "||01-06 Durotar|1"] = OLD_COPY,                               -- older copies of a guide the pack has
  [H .. "||01-06 Durotar|12"] = { key = H .. "||01-06 Durotar" },
  [H .. "||06-11 Durotar|4"] = { key = H .. "||06-11 Durotar" },       -- a guide the pack replaces, twice
  [H .. "||06-11 Durotar|9"] = { key = H .. "||06-11 Durotar" },
  ["RestedXP Speedrun Guide (H)|" .. H .. "|11-14 The Barrens|5"] =    -- replaced; RestedXP filed its group as the subgroup
    { key = "RestedXP Speedrun Guide (H)|" .. H .. "|11-14 The Barrens" },
  [H .. "||20-21 Keyless|3"] = "compressed text",                      -- replaced; an old entry known by its slot only
  ["Other Guides||06-11 Durotar|6"] = { key = "Other Guides||06-11 Durotar" },   -- another group's guide of that name
  [H .. "||14-16 Mine|7"] = { key = H .. "||14-16 Mine" },             -- the player's own, not replaced
  [H .. "||16-18 Wrong Game|8"] = { key = H .. "||16-18 Wrong Game" }, -- a guide RestedXP doesn't cache again here
}
SEQ = 0
REFUSE_NAME = nil
RXP = { db = { profile = { guides = CACHE } }, RXPFrame = { GenerateMenuTable = function() end },
        ScheduleTask = function() end }
function RXP.ReadCacheData(mode) if mode then return nil end return KEY end
-- RestedXP's ImportGuide: caches a new copy every time, numbering its slots from 1 again each session (so a new copy
-- can take an old copy's slot); a guide for another game ("#wrong") is parsed but not cached
function RXP.ImportGuide(text)
  local group, name = text:match("#group%s+([^\r\n]+)"), text:match("#name%s+([^\r\n]+)")
  name = name:gsub("^(%d)-(%d%d?)", function(a, b) return "0" .. a .. "-" .. (#b == 1 and "0" .. b or b) end)
  if name == REFUSE_NAME then error("Error parsing guide: bad step", 0) end
  local key = group .. "||" .. name
  if not text:find("#wrong") then SEQ = SEQ + 1; CACHE[key .. "|" .. SEQ] = { key = key } end
  return { name = name, key = key }
end
function slots() local t = {} for pk in pairs(CACHE) do t[#t+1] = pk end table.sort(t) return t end
""")
H = "RestedXP Horde 1-30"
P1 = "#group %s\n#name 1-6 Durotar\nstep\n" % H
P2 = "#group %s\n#name 12-14 New Guide\nstep\n" % H
P3 = "#group %s\n#name 16-18 Wrong Game\n#wrong\nstep\n" % H
latest_src = R.handoff_lua([("ppp", {"guides": [{"text": P1}, {"text": P2}, {"text": P3}],
                                     "drop": [[H, "06-11 Durotar"], [H, "11-14 The Barrens"], [H, "20-21 Keyless"]]})],
                           {"ppp": R.key_check(MINE)})
ns2 = L2.table()
run2 = L2.eval("function(src, name, a, ns) local f, e = loadstring(src, name) if not f then error(e) end return f(a, ns) end")
for name in ("Core.lua", "Data/RXPImports.lua", "RXPImport.lua"):
    src = latest_src if name == "Data/RXPImports.lua" else open(os.path.join(ROOT, "addon", "Glide", name), encoding="utf-8").read()
    run2(src, "@" + name, "Glide", ns2)
G2L = L2.globals()
slots = lambda: list(G2L.slots().values())
untouched = sorted(G2L.CACHE.keys())
# a pack RestedXP doesn't take whole leaves the cache alone: the player keeps the guides they had
G2L.REFUSE_NAME = "12-14 New Guide"
G2L.fire("ADDON_LOADED", "Glide")
G2L.fire("PLAYER_LOGIN")
G2L.ticks(30)
assert G2L.GlideRecorderDB.rxpImports["ppp"].ok is False, dict(G2L.GlideRecorderDB.rxpImports["ppp"])
assert slots() == untouched, slots()          # the first guide's new copy took slot 1: nothing else changed
assert G2L.CACHE[H + "||01-06 Durotar|1"] != G2L.OLD_COPY
# tried again and taken whole: one copy of each of its guides (the latest), and none of what it replaces
G2L.REFUSE_NAME = None
G2L.SlashCmdList.GLIDE("rxpimport")
G2L.ticks(30)
assert G2L.GlideRecorderDB.rxpImports["ppp"].ok, dict(G2L.GlideRecorderDB.rxpImports["ppp"])
assert slots() == sorted([H + "||01-06 Durotar|2", H + "||12-14 New Guide|3", "Other Guides||06-11 Durotar|6",
                          H + "||14-16 Mine|7", H + "||16-18 Wrong Game|8"]), slots()
assert "RestedXP now has the 3 guides" in G2L.last() and "The 3 older guides they replace" in G2L.last() \
    and "/reload" in G2L.last(), G2L.last()
# the next login: nothing goes again, nothing more leaves the cache
G2L.fire("PLAYER_LOGIN")
G2L.ticks(30)
assert G2L.SEQ == 3 and len(slots()) == 5, (G2L.SEQ, slots())
print("OK")
