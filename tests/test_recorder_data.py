#!/usr/bin/env python3
"""The addon's saved data read back for the pursuit planner (companion/recorder_data.py): professions from the newest
session with skill lines (a session without lines, as the client gives today, doesn't count as "none"; another
client language is unknown), reputation from the sessions' and the per-character factions tables (newest t wins,
named and positional entries, another character's never leaks in), auction prices (newest wins, only the character's
realm and side with a character, the table kept outside the sessions for everyone), and looted corpses (a corpse
looted again is one kill, across a /reload too; objects don't count). A file from before 0.24.0 (no factions, no
auction) reads as nothing known. Malformed files keep the last good reading, or read as nothing; nothing in a file
is ever run; a file is parsed again only when its time or size changes, and the folder is looked at no more than
every STAT_S seconds. Only the parts read are kept; a copied account folder counts once, while one session id of two
characters is two sessions; while one thread reads the files, another answers from the last reading without waiting.
Fixtures: tests/fixtures/recorder_sv_new.lua and recorder_sv_old.lua.

Run: python3 tests/test_recorder_data.py   (prints OK)
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import recorder_data  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")
NEW, OLD = os.path.join(FIX, "recorder_sv_new.lua"), os.path.join(FIX, "recorder_sv_old.lua")
A1, A2 = "12345#1", "12345#2"
GANDLING, STYLUS, ROBE = 1853, 13938, 14514


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


def sv(wow, acct):
    return os.path.join(wow, "WTF", "Account", acct, "SavedVariables", "Glide.lua")


def put(path, text, bump=0):
    """Write a SavedVariables file; bump: seconds added to its modification time (a time of its own)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    if bump:
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + int(bump * 1e9)))


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# count the parses: the reader's own recorder_db, wrapped
real_lua = recorder_data._lua()
parses = []


class Counting:
    @staticmethod
    def recorder_db(path, warn=None):
        parses.append(os.path.basename(os.path.dirname(os.path.dirname(path))))
        return real_lua.recorder_db(path, warn=warn)


recorder_data._lua = lambda: Counting

tmp = tempfile.mkdtemp(prefix="glide-recdata-")
try:
    wow = os.path.join(tmp, "WoW [test]", "_forever_")       # glob characters in the folder are taken literally
    put(sv(wow, A1), read(NEW))
    put(sv(wow, A2), read(OLD))
    clock = [1000.0]
    rd = recorder_data.RecorderData(lambda: wow, now=lambda: clock[0])

    # ---- professions --------------------------------------------------------------------------------------------
    want = {"Tailoring", "Enchanting", "Cooking"}
    check(rd.skills("Nine-Skyborne") == want, rd.skills("Nine-Skyborne"))     # newest session has no lines: older
    check(rd.skills("Nine") == want and rd.skills("nine-skyborne") == want, "name only, any case")
    check(rd.skills({"name": "Nine", "realm": "Skyborne"}) == want, "a character dict")
    check(rd.skills("Nine-Elsewhere") is None, "another realm's Nine has no sessions")
    check(rd.skills("Dragon-Othershore") == set(), rd.skills("Dragon-Othershore"))   # lines, none a profession
    check(rd.skills("Oldtimer") is None, "an old session without skill lines: unknown, not none")
    check(rd.skills("Hans") is None, "a client language whose names this doesn't know: unknown")
    check(rd.skills("Nobody") is None and rd.skills(None) is None and rd.skills("") is None, "no such character")
    got = rd.skills("Nine")
    got.add("Mining")
    check(rd.skills("Nine") == want, "answers are copies")

    # ---- reputation ---------------------------------------------------------------------------------------------
    f = rd.factions("Nine-Skyborne")
    check(f == {529: {"standing": 7, "value": 21500, "t": 1795090000.0,       # the newer session's, and its grind:
                      "v0": 20100, "t0": 1795083000.0, "t1": 1795089600.0},  # (its first rise to its last)
                609: {"standing": 5, "value": 4000, "t": 1795001500.0},        # positional, and newer by t
                270: {"standing": 5, "value": 3500, "t": 1795003600.0},        # no t: the session's end
                910: {"standing": 4, "value": 100, "t": 1795060000.0}}, f)    # the per-character table
    check(rd.factions("Nine") == f, "name only")
    check(rd.factions("Somebody-Skyborne") == {529: {"standing": 8, "value": 42999, "t": 1799999999.0}},
          rd.factions("Somebody-Skyborne"))
    check(rd.factions("Oldtimer") == {} and rd.factions("Dragon-Othershore") == {}, "nothing recorded")
    f[529]["standing"] = 1
    check(rd.factions("Nine")[529]["standing"] == 7, "answers are copies")

    # ---- auction prices -----------------------------------------------------------------------------------------
    check(rd.auction(STYLUS) == {"copper": 999999, "t": 1795099999.0}, rd.auction(STYLUS))   # newest anywhere
    check(rd.auction(STYLUS, "Nine-Skyborne") == {"copper": 400000, "t": 1795091000.0},       # Nine's side only
          rd.auction(STYLUS, "Nine-Skyborne"))
    check(rd.auction(str(STYLUS), "Nine") == {"copper": 400000, "t": 1795091000.0}, "an id as text")
    check(rd.auction(14152, "Nine") == {"copper": 1200000, "t": 1795002100.0}, rd.auction(14152, "Nine"))
    check(rd.auction(20000, "Nine-Skyborne") == {"copper": 5000, "t": 1795050000.0}, "the realm's and side's table")
    # regression: a price seen on another realm or side (another auction house) is no price for this character
    check(rd.auction(20000, "Dragon-Othershore") is None and rd.auction(20001, "Nine") is None, "another house's")
    check(rd.auction(20001, "Dragon-Othershore") == {"copper": 3300, "t": 1795050100.0}, "Dragon's house")
    check(rd.auction(20000) == {"copper": 5000, "t": 1795050000.0} and rd.auction(20001)["copper"] == 3300,
          "without a character: every house")
    check(rd.auction(20000, "Nobody") is None, "a character no session names: its house unknown")
    check(rd.auction(STYLUS, "Dragon-Othershore") == {"copper": 999999, "t": 1795099999.0}, "Dragon's own")
    check(rd.auction(1) is None and rd.auction(None) is None and rd.auction("x") is None, "never seen")
    check(rd.auction(14152, "Oldtimer") == {"copper": 1200000, "t": 1795002100.0}, "Nine's: same realm and side")

    # ---- kills ----------------------------------------------------------------------------------------------------
    g = rd.kills("Nine-Skyborne", npc=GANDLING)
    check([(k["npc"], k["t"], k["loot"]) for k in g] ==
          [(GANDLING, 1795002500.0, [STYLUS, ROBE]),                    # looted again 4 s later: the same corpse
           (GANDLING, 1795093000.0, [ROBE])], g)                        # the next session's run
    check(g[0]["session"] == "20261120-100000" and g[1]["session"] == "20261121-090000", g)
    every = rd.kills("Nine")
    check(sorted((k["npc"], k["loot"][0]) for k in every) ==
          sorted([(GANDLING, STYLUS), (GANDLING, ROBE), (10507, 14047), (10507, 14047), (10508, 14048),
                  (10509, 14047)]), every)                               # an AoE window: one corpse per NPC
    check(all(k["npc"] != 175404 for k in every), "an object (a vein, a chest) isn't a kill")
    check([k["t"] for k in every] == sorted(k["t"] for k in every), "oldest first")
    d = rd.kills("Dragon-Othershore")
    check([(k["t"], k["loot"]) for k in d] == [(1794900100.0, [2589]), (1794900150.0, [1468])], d)  # 2 XP kills
    check([(k["npc"], k["loot"]) for k in rd.kills("Oldtimer")] == [(10813, [13360])], rd.kills("Oldtimer"))
    check(len(rd.kills(None)) == 6 + 2 + 1, len(rd.kills(None)))
    check(rd.kills("Nobody") == [] and rd.kills("Nine", npc=99) == [], "no kills")
    rd.kills("Nine", npc=GANDLING)[0]["loot"].append(1)
    check(rd.kills("Nine", npc=GANDLING)[0]["loot"] == [STYLUS, ROBE], "answers are copies")

    # ---- each file parsed once; again only when its time or size changes --------------------------------------
    check(sorted(parses) == [A1, A2], parses)
    clock[0] += 60
    rd.skills("Nine")
    check(sorted(parses) == [A1, A2], "unchanged files aren't parsed again")
    put(sv(wow, A1), read(NEW).replace('["copper"] = 5000,', '["copper"] = 7000,'), bump=10)
    clock[0] += 0.5
    check(rd.auction(20000)["copper"] == 5000, "the folder isn't looked at again within STAT_S")
    clock[0] += recorder_data.STAT_S
    check(rd.auction(20000)["copper"] == 7000, "a changed file is read again")
    check(sorted(parses) == [A1, A1, A2], parses)                       # only the changed one
    check(rd.refresh(force=True) is False and len(parses) == 3, "forced, nothing changed")

    # ---- malformed: the last good reading stands; alone, nothing is known ------------------------------------------
    text = read(NEW)
    put(sv(wow, A1), text[:len(text) // 2], bump=20)                    # WoW halfway through writing it
    clock[0] += recorder_data.STAT_S + 1
    check(rd.skills("Nine") == want and rd.auction(20000)["copper"] == 7000, "the last good reading stands")
    check(len(parses) == 4, parses)
    clock[0] += recorder_data.STAT_S + 1
    rd.skills("Nine")
    check(len(parses) == 4, "a bad file isn't parsed again until it changes")
    put(sv(wow, A1), read(NEW), bump=30)
    clock[0] += recorder_data.STAT_S + 1
    check(rd.auction(20000)["copper"] == 5000, "written whole again: read")

    bad = os.path.join(tmp, "bad")
    for i, junk in enumerate(["GlideRecorderDB = { [\"sessions\"] = { ", "\x00\x01 not lua at all {{{",
                              "GlideRecorderDB = 5", "GlideRecorderDB = { [\"sessions\"] = 7 }",
                              "GlideRecorderDB = { [\"sessions\"] = { [\"x\"] = { [\"char\"] = 3 } } }", ""]):
        put(sv(bad, "A%d" % i), junk)
    rb = recorder_data.RecorderData(lambda: bad, now=lambda: clock[0])
    check(rb.skills("Nine") is None and rb.factions("Nine") == {} and rb.auction(STYLUS) is None
          and rb.kills("Nine") == [] and rb.sessions() == [], "malformed files: nothing known")

    # ---- nothing is run ---------------------------------------------------------------------------------------------
    ran = os.path.join(tmp, "ran.txt")
    os.environ["GLIDE_TEST_SECRET"] = "leaked"
    code = ('GlideRecorderDB = { ["sessions"] = { ["s1"] = { ["started"] = 1, ["char"] = {\n'
            '    ["name"] = "Nine", ["realm"] = os.getenv("GLIDE_TEST_SECRET") } } } }\n')
    exe = os.path.join(tmp, "exe")
    put(sv(exe, "A0"), code)
    put(sv(exe, "A1"), code.replace("Nine", "Ten") + 'io.open([[%s]], "w"):write("ran")\nos.execute("exit 3")\n' % ran)
    put(sv(exe, "A2"), 'GlideRecorderDB = { ["x"] = function() io.open([[%s]], "w"):write("ran") end }\n' % ran)
    re_ = recorder_data.RecorderData(lambda: exe, now=lambda: clock[0])
    names = [(s["char"]["name"], s["char"]["realm"]) for _sid, s in re_.sessions()]
    check(names == [("Nine", "GLIDE_TEST_SECRET")], names)              # a call's text at most, never its result
    check(not os.path.exists(ran), "a file's code ran")
    check("lupa" not in sys.modules, "no Lua runtime was loaded")

    # ---- one account, no folder ---------------------------------------------------------------------------------
    one = recorder_data.RecorderData(lambda: wow, account_fn=lambda: A2, now=lambda: clock[0])
    check(one.skills("Nine") is None and len(one.kills("Oldtimer")) == 1, "only the named account")
    check(one.auction(14152, "Oldtimer") is None and one.factions("Oldtimer") == {}, "an old file: no auction, "
          "no factions")
    none = recorder_data.RecorderData(lambda: None)
    check(none.skills("Nine") is None and none.factions("Nine") == {} and none.auction(STYLUS) is None
          and none.kills("Nine") == [], "no WoW folder")

    def boom():
        raise RuntimeError("config unreadable")
    check(recorder_data.RecorderData(boom).kills("Nine") == [], "a failing folder lookup: nothing known")

    # ---- only what is read is kept; a copied account counts once; one session id, two characters: both --------------
    old = rd.sessions("Oldtimer")[0][1]
    check("trail" not in old and "char" in old and "loot" in old, sorted(old))
    check(all(set(db) <= set(recorder_data.DB_KEYS) | {"sessions"} for _sig, db in rd.files.values()),
          "settings, guide texts and map bounds aren't kept")
    twin = os.path.join(tmp, "twin")
    put(sv(twin, A1), read(NEW))
    put(sv(twin, A2), read(OLD))
    put(sv(twin, "copy"), read(OLD), bump=5)
    put(sv(twin, "other"), read(OLD).replace('"Oldtimer"', '"Newcomer"'))
    rt = recorder_data.RecorderData(lambda: twin, now=lambda: clock[0])
    check(len(rt.kills("Oldtimer")) == 1 and len(rt.sessions("Oldtimer")) == 1, "a copied account: once")
    check(len(rt.kills("Newcomer")) == 1, "the same session id for another character is its own")

    # ---- while one thread reads the files, the others answer from the last reading --------------------------------
    rt.reading.acquire()
    try:
        put(sv(twin, A1), read(NEW).replace('["copper"] = 5000,', '["copper"] = 6000,'), bump=40)
        clock[0] += recorder_data.STAT_S + 1
        check(rt.refresh() is False and rt.auction(20000)["copper"] == 5000, "answered without waiting")
    finally:
        rt.reading.release()
    clock[0] += recorder_data.STAT_S + 1
    check(rt.auction(20000)["copper"] == 6000, "read once the other thread is done")

    # ---- the corpse rules, alone --------------------------------------------------------------------------------
    A, B, C = 1, 2, 3
    got = recorder_data.corpses({0: {A, B}, 5: {A}, 30: {A}, 31: {C}}, [], gap=300, reopen=15)
    check(got == [(0, {A, B}), (30, {A}), (31, {C})], got)              # again within 15 s with nothing new: one
    got = recorder_data.corpses({100: {A}, 104: {A}, 150: {B}}, [95, 140], gap=300, reopen=15)
    check(got == [(100, {A}), (150, {B})], got)                         # a kill between: two corpses
    got = recorder_data.corpses({100: {A}, 101: {A, B}}, [90, 95], gap=300, reopen=15)
    check(got == [(100, {A, B}), (100, {A})], got)                      # two kills, two windows: A on both
    check(recorder_data.corpses({}, [1, 2]) == [], "no windows: no looted corpse")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
