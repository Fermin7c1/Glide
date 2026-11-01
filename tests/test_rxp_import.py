#!/usr/bin/env python3
"""RestedXP guide strings added in the app (companion/rxp_import.py, addon RXPImport.lua).

The engine side: RestedXP's cipher (RC4, pinned to RFC 6229's first vector) and the key it makes from a BattleTag;
a string opened with the key RestedXP saved for an account on this PC (as its 16 numbers, or as the BattleTag it
sometimes leaves there), never with another account's; the guides kept for the knowledge build (tools/build_db.py
reads them); the addon's Data/RXPImports.lua written as Lua the game loads, and emptied once the addon noted
RestedXP took the string. Strings that aren't one, or are another account's, say why.

The addon side, under Lua 5.1 with a mocked WoW API and a stand-in for RestedXP's ImportGuide: after login each waiting
pack's decrypted guides go to RestedXP's ImportGuide in batches, the outcome is noted in GlideRecorderDB.rxpImports,
nothing goes twice, a failure is retried only by /glide rxpimport, and without RestedXP nothing breaks. Needs lupa.

Run: python3 tests/test_rxp_import.py   (prints OK)
"""
import base64
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
    got = {e.id: (e.n, [e.guides[i] for i in range(1, len(e.guides) + 1)]) for e in ns.rxpImports.values()}
    assert got == {r["id"]: (2, [G1, G2]), r3["id"]: (1, [G3])}, got

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
    # a record with no guide text is skipped; control bytes + following digits round-trip through Lua 5.1 intact
    assert "ns.rxpImports = {\n}" in R.handoff_lua([("x", {"guides": []})])
    assert R._lua_string('a"b\\c\n0\x019') == '"a\\"b\\\\c\\n0\\0019"'
    assert L.eval(R._lua_string('a"b\\c\n0\x019')) == 'a"b\\c\n0\x019'
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
# "aaa" has 20 guides (> BATCH = 15, so it spans two ticks); "bbb" has 1 guide
g_many = [{"text": "#group G\n#name Guide %d\nstep\n" % i} for i in range(1, 21)]
handoff_src = R.handoff_lua([("aaa", {"guides": g_many}),
                             ("bbb", {"guides": [{"text": G3}]})])
run = L.eval("function(src, name, a, ns) local f, e = loadstring(src, name) if not f then error(e) end return f(a, ns) end")
for name in ("Core.lua", "Data/RXPImports.lua", "RXPImport.lua"):
    src = handoff_src if name == "Data/RXPImports.lua" else open(os.path.join(ROOT, "addon", "Glide", name), encoding="utf-8").read()
    run(src, "@" + name, "Glide", ns)
G = L.globals()
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
G.ticks(1)                           # first batch of "aaa": 15 of 20 guides
assert len(G.CALLS) == 15, len(G.CALLS)
assert "handing the 20 RestedXP guides" in G.last(), G.last()
G.ticks(1)                           # second batch of "aaa": remaining 5 guides, then done
assert len(G.CALLS) == 20 and G.MENUS == 1, (len(G.CALLS), G.MENUS)
notes = G.GlideRecorderDB.rxpImports
assert notes["aaa"].ok and notes["aaa"].err is None, dict(notes["aaa"])
assert any("RestedXP now has the 20 guides" in p for p in G.PRINTED.values())
G.REFUSE = "Error parsing guide: bad step"
G.ticks(1)                           # next tick tries "bbb", which fails
assert list(G.CALLS.values())[-1] == G3 and len(G.CALLS) == 21
assert notes["bbb"].ok is False and "Error parsing guide" in notes["bbb"].err, dict(notes["bbb"])
assert "/glide rxpimport tries again" in G.last(), G.last()
G.ticks(10)
assert len(G.CALLS) == 21, "a failed pack isn't retried by itself"

# the next login: RestedXP has "aaa", "bbb" failed: nothing goes again
G.fire("PLAYER_LOGIN")
G.ticks(10)
assert len(G.CALLS) == 21, len(G.CALLS)
# /glide rxpimport tries the failed one again, once
G.REFUSE = "Incompatible guide"
G.SlashCmdList.GLIDE("rxpimport")
G.ticks(1)
assert list(G.CALLS.values())[-1] == G3 and len(G.CALLS) == 22
assert "Incompatible guide" in notes["bbb"].err and "Incompatible guide" in G.last(), G.last()
G.ticks(10)
assert len(G.CALLS) == 22, "tried once per command"
G.REFUSE = None
G.SlashCmdList.GLIDE("rxpimport")
G.ticks(1)
assert notes["bbb"].ok and len(G.CALLS) == 23 and G.MENUS == 2
G.SlashCmdList.GLIDE("rxpimport")
assert "already has every guide" in G.last(), G.last()

# without RestedXP: says so once and stops looking
G.GlideRecorderDB.rxpImports = L.table()
G.RXP = None
before = len(G.PRINTED)
G.fire("PLAYER_LOGIN")
G.ticks(10)
said = [p for p in list(G.PRINTED.values())[before:] if "RestedXP isn't loaded" in p]
assert len(said) == 1, list(G.PRINTED.values())[before:]
print("OK")
