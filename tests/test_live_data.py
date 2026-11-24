#!/usr/bin/env python3
"""The facts file the Glide app writes for the addon (companion/live_data.py, Data/Live.lua): with no data it is the
empty file the addon ships, byte for byte; with data, the exact Lua (escaped text, the web flag, per-character
tables, sections a later release adds), which Lua 5.1 runs into ns.live; gen goes up once per real write and survives
a restart (and a lost live.json); written only for an installed addon from FORMAT on with a Glide folder, again after
an addon install put the empty file back, never while there is nothing to write; kept under the size cap and the
constants cap, the lowest priority number kept first; a provider that fails writes nothing. Needs lupa.

Run: python3 tests/test_live_data.py   (prints OK)
"""
import json
import os
import shutil
import sys
import tempfile

from lupa import lua51

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import live_data  # noqa: E402

EMPTY = ("-- Written by the Glide app (companion/live_data.py): facts Glide found about items, quests and more, read at\n"
         "-- login and /reload. The app rewrites this file.\n"
         "local _, ns = ...\n"
         "ns.live = { v = 1, gen = 0, t = 0, items = {}, chars = {} }\n")


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


def run_lua(text):
    """The file run as the game runs it (local _, ns = ...) -> ns.live."""
    L = lua51.LuaRuntime(unpack_returned_tuples=True)
    ns = L.table()
    L.eval("function(src, a, ns) local f, e = loadstring(src, '@Live.lua') if not f then error(e) end "
           "f(a, ns) end")(text, "Glide", ns)
    return ns.live


def read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


logged = []
live_data.log = lambda tag, msg: logged.append(msg)
tmp = tempfile.mkdtemp(prefix="glide-live-data-")
try:
    home, wow = os.path.join(tmp, "engine"), os.path.join(tmp, "wow")
    glide = os.path.join(wow, "Interface", "AddOns", "Glide")
    os.makedirs(os.path.join(glide, "Data"))
    path = os.path.join(wow, live_data.HANDOFF)
    clock, version, wow_at = [1764000000.0], ["0.20.0"], [wow]
    items, chars = {}, {}

    def make(**kw):
        ld = live_data.LiveData(home, lambda: wow_at[0], lambda: version[0], now=lambda: clock[0], **kw)
        ld.register("items", lambda: items)
        ld.register("chars", lambda: chars)
        return ld

    # ---- no data: exactly the file the addon ships ------------------------------------------------------------------
    ld = make()
    check(ld.text() == EMPTY, ld.text())
    shipped = os.path.join(ROOT, "addon", "Glide", "Data", "Live.lua")
    check(os.path.isfile(shipped) and read(shipped) == EMPTY, "the addon ships the empty file")
    check(live_data.FORMAT == "0.20.0" and live_data.HANDOFF == os.path.join("Interface", "AddOns", "Glide", "Data",
                                                                            "Live.lua"), "the interface")
    open(path, "w", encoding="utf-8", newline="\n").write(EMPTY)
    check(ld.write() is False and ld.gen == 0 and read(path) == EMPTY, "nothing to write over the shipped file")
    lv = run_lua(EMPTY)
    check(lv.v == 1 and lv.gen == 0 and lv.t == 0 and lv["items"] is not None and lv.chars is not None, "it runs")

    # ---- data: the exact text, and what Lua makes of it -------------------------------------------------------------
    items.update({
        16686: {"t": "drops from Darkmaster Gandling, Scholomance", "a": "n", "w": 0, "p": 2},
        123: {"t": 'a "quoted" back\\slash\nand a new line', "a": "x", "w": 1, "p": 1},
        19019: {"t": "drops from Ragnaros’s lair, Molten Core", "a": "l", "p": 3},
        5: {"t": "", "a": "n", "p": 0},                            # no text: not written
        "777": {"t": "sold by Deliana, Ironforge", "a": "n", "p": 4},
    })
    chars["Nine"] = {"bis": {226935: ["Head", 1, 8.1], 16686: ["Head", 4, 2.0, "~"]}, "goal": 13938, "x": None}
    chars["Nine-Realm"] = {"next": {"t": "Scholomance", "end": True, "m": 1438, "x": 0.5}}
    clock[0] += 5
    check(ld.write() is True and ld.gen == 1 and ld.t == 1764000005, (ld.gen, ld.t))
    text = read(path)
    check(text == EMPTY[:EMPTY.index("ns.live")] +
          'ns.live = { v = 1, gen = 1, t = 1764000005, items = {\n'
          '    [123] = { "a \\"quoted\\" back\\\\slash\\nand a new line", a = "x", w = 1 },\n'
          '    [777] = { "sold by Deliana, Ironforge", a = "n" },\n'
          '    [16686] = { "drops from Darkmaster Gandling, Scholomance", a = "n" },\n'
          '    [19019] = { "drops from Ragnaros’s lair, Molten Core", a = "l" },\n'
          '}, chars = {\n'
          '    ["Nine"] = { bis = { [16686] = { "Head", 4, 2.0, "~" }, [226935] = { "Head", 1, 8.1 } }, '
          'goal = 13938 },\n'
          '    ["Nine-Realm"] = { next = { ["end"] = true, m = 1438, t = "Scholomance", x = 0.5 } },\n'
          '} }\n', text)
    check(text == ld.text(), "text() is what was written")
    check(not os.path.exists(path + ".part"), "no .part left behind")
    lv = run_lua(text)
    check(lv.gen == 1 and lv.t == 1764000005 and lv.v == 1, "header")
    check(lv["items"][123][1] == 'a "quoted" back\\slash\nand a new line' and lv["items"][123].a == "x" and
          lv["items"][123].w == 1, "escaped text, web flag")
    check(lv["items"][16686][1].startswith("drops from Darkmaster") and lv["items"][16686].a == "n" and
          lv["items"][16686].w is None, "no web flag")
    check(lv["items"][19019][1] == "drops from Ragnaros’s lair, Molten Core" and lv["items"][19019].a == "l", "UTF-8")
    check(lv["items"][5] is None and lv["items"][777].a == "n", "no text: left out; a string id: its number")
    check(lv.chars["Nine"].bis[226935][3] == 8.1 and lv.chars["Nine"].bis[16686][4] == "~" and
          lv.chars["Nine"].goal == 13938 and lv.chars["Nine-Realm"].next["end"] is True, "chars")
    check(ld.ids == [123, 777, 16686, 19019], ld.ids)
    check(logged and "gen 1, 4 items" in logged[-1], logged)

    # ---- unchanged: no write; changed: gen + 1; a restart keeps gen; a lost live.json doesn't send it back --------
    clock[0] += 15
    check(ld.write() is False and ld.gen == 1, "unchanged")
    items[16686] = dict(items[16686], t="drops from Darkmaster Gandling, Scholomance (Headmaster's Study)")
    check(ld.write() is True and ld.gen == 2 and "gen = 2, t = 1764000020" in read(path), "changed: gen 2")
    saved = json.load(open(os.path.join(home, "data", "research", "live.json"), encoding="utf-8"))
    check(saved["gen"] == 2 and saved["ids"] == [123, 777, 16686, 19019] and saved["writes"][-1] == [2, 1764000020],
          saved)
    again = make()
    check(again.gen == 2 and again.write() is False, "a restart: nothing to write")
    os.remove(os.path.join(home, "data", "research", "live.json"))
    lost = make()
    check(lost.write() is True and lost.gen == 3, "gen read back from the file: %d" % lost.gen)
    ld = make()
    check(ld.gen == 3 and ld.write() is False, "picked up")

    # ---- an addon install put the empty file back: written again; the older gens are gone from the self-test ------
    open(path, "w", encoding="utf-8", newline="\n").write(EMPTY)
    clock[0] += 15
    check(ld.write() is True and ld.gen == 4 and "gen = 4" in read(path), "written again after the install")
    check(ld.writes == [[4, int(clock[0])]], ld.writes)
    os.remove(path)
    check(ld.write() is True and ld.gen == 5 and os.path.isfile(path), "a deleted file comes back")

    # ---- nothing to write: the file keeps what it has ---------------------------------------------------------------
    before = read(path)
    saved_items, saved_chars = dict(items), dict(chars)
    items.clear()
    chars.clear()
    check(ld.write() is False and read(path) == before and ld.gen == 5, "empty data never empties the file")
    items.update(saved_items)
    chars.update(saved_chars)

    # ---- the version gate, the Glide folder ---------------------------------------------------------------------
    items[42] = {"t": "made with Tailoring", "a": "n", "p": 1}
    for v in ("0.19.9", None, "", "unknown"):
        version[0] = v
        check(ld.write() is False and "[42]" not in read(path), "addon %r: not written" % v)
    version[0] = (0, 21, 0)
    check(ld.write() is True and "[42] = " in read(path), "a newer addon (tuple)")
    del items[42]
    version[0] = "0.20.1-beta"
    check(ld.write() is True and "[42]" not in read(path), "a newer addon (string)")
    wow_at[0] = None
    check(ld.write() is False, "no WoW folder")
    bare = os.path.join(tmp, "wow-without-glide")
    os.makedirs(os.path.join(bare, "Interface", "AddOns"))
    wow_at[0] = bare
    check(ld.write() is False and not os.path.exists(os.path.join(bare, live_data.HANDOFF)) and
          os.listdir(os.path.join(bare, "Interface", "AddOns")) == [], "no Glide folder: nothing created")
    wow_at[0] = wow

    # ---- a provider that fails: nothing written (the worker's loop logs it once; the next poll tries again) --------
    n, before = ld.gen, read(path)
    ld.register("items", lambda: 1 / 0)
    try:
        ld.tick()
    except ZeroDivisionError:
        pass
    else:
        check(False, "a provider's error reaches the worker's loop")
    check(ld.gen == n and read(path) == before, "nothing written")
    ld.register("items", lambda: items)

    # ---- a later section: written after chars when it has data, left out when empty --------------------------------
    quests = {}
    ld.register("quests", lambda: quests)
    check(ld.write() is False and "quests = " not in read(path), "an empty extra section: no change")
    quests[5501] = {"n": "General Drakkisath's Demise", "c": [5501, 5502]}
    check(ld.write() is True, "an extra section with data")
    check('}, quests = {\n    [5501] = { c = { 5501, 5502 }, n = "General Drakkisath\'s Demise" },\n} }\n' in
          read(path), read(path)[-200:])
    check(run_lua(read(path)).quests[5501].c[2] == 5502, "the extra section runs")
    for bad in ("not a name", "end", "gen", ""):
        try:
            ld.register(bad, dict)
        except ValueError:
            continue
        check(False, "section %r accepted" % bad)
    del ld.providers["quests"]

    # ---- the size cap: the lowest priority number first, the rest left out; the file under the cap ---------------
    big = {i: {"t": "drops from Boss Number %04d, Some Long Instance Name" % i, "a": "n", "p": 1000 - i}
           for i in range(1, 401)}
    capped = live_data.LiveData(os.path.join(tmp, "engine2"), lambda: wow, lambda: "0.20.0", now=lambda: clock[0],
                                cap=8 * 1024)
    capped.register("items", lambda: big)
    capped.register("chars", lambda: {"Nine": {"goal": 1}})
    check(capped.write() is True, "written capped")
    text = read(path)
    size = len(text.encode("utf-8"))
    check(size <= 8 * 1024 and size > 7 * 1024, size)
    kept = capped.ids
    check(kept and kept == sorted(kept) and kept[-1] == 400 and kept[0] == 400 - len(kept) + 1, kept[:3])
    check(text.index("[%d]" % kept[0]) < text.index("[%d]" % kept[-1]) and '["Nine"]' in text, "by id; chars kept")
    lv = run_lua(text)
    check(lv["items"][400] is not None and lv["items"][1] is None, "the highest priority kept, the lowest left out")

    # ---- the constants cap: shared texts cost one constant -------------------------------------------------------
    saved_max = live_data.MAX_CONSTANTS
    try:
        live_data.MAX_CONSTANTS = 60
        many = {i: {"t": "PvP reward", "a": "n", "p": i} for i in range(1, 101)}
        cc = live_data.LiveData(os.path.join(tmp, "engine3"), lambda: wow, lambda: "0.20.0", now=lambda: clock[0])
        cc.register("items", lambda: many)
        check(cc.write() is True, "written")
        consts = {("s", "PvP reward"), ("s", "n")} | {("n", i) for i in cc.ids}
        check(20 < len(cc.ids) < 60 and cc.ids == list(range(1, len(cc.ids) + 1)) and len(consts) <= 60, cc.ids)
    finally:
        live_data.MAX_CONSTANTS = saved_max

    # ---- a full band loads as one chunk (thousands of items) -------------------------------------------------------
    band = {i: {"t": "drops from Boss %d, Instance %d" % (i % 300, i % 40), "a": "n", "p": 3} for i in range(1, 4001)}
    full = live_data.LiveData(os.path.join(tmp, "engine4"), lambda: wow, lambda: "0.20.0", now=lambda: clock[0])
    full.register("items", lambda: band)
    check(full.write() is True and len(read(path).encode("utf-8")) <= 256 * 1024, "the default cap")
    lv = run_lua(read(path))
    check(lv["items"][full.ids[-1]] is not None and len(full.ids) > 3000, len(full.ids))

    # ---- lua_value: what Lua can't hold isn't written as junk -------------------------------------------------------
    check(live_data.lua_value({"a": float("nan"), "b": [1, None, "x"], 3: False}) ==
          '{ [3] = false, a = 0, b = { 1, nil, "x" } }', live_data.lua_value({"a": float("nan")}))
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
