#!/usr/bin/env python3
"""The /reload self-test behind the "new item facts" nudge (companion/live_data.py): the addon reports the gen of
Data/Live.lua it loaded (core key "lg"), and the engine sees it start again (GameState: ring ids start over). A
reload that started RELOAD_AFTER_S or more after a write and still reports an older gen didn't read the file again;
two in a row (once the addon has loaded a written file at all) set reload_ok False, and the nudge asks for a WoW
restart, until a reload has the newest file again. Before the addon ever loaded one (its TOC line is new) the nudge
asks for a restart too, and those reloads don't count against /reload. A reload within RELOAD_AFTER_S of a write is
judged against the gen before; an "lg" missing from one frame (a full core) is waited for. news() says each wording
once (across engine restarts too), and nothing when the addon has the newest file already. GameState dates a restart
by the old load's last frame (it is noticed only at the new load's first event), and tick() picks it up.

Run: python3 tests/test_reload_selftest.py   (prints OK)
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import live_data  # noqa: E402
from state import GameState  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


logged = []
live_data.log = lambda tag, msg: logged.append(msg)
tmp = tempfile.mkdtemp(prefix="glide-reload-selftest-")
try:
    home, wow = os.path.join(tmp, "engine"), os.path.join(tmp, "wow")
    os.makedirs(os.path.join(wow, "Interface", "AddOns", "Glide", "Data"))
    clock = [1000.0]
    items = {}

    def make(state_fn=None):
        ld = live_data.LiveData(home, lambda: wow, lambda: "0.20.0", now=lambda: clock[0], state_fn=state_fn)
        ld.register("items", lambda: items)
        return ld

    def at(t):
        clock[0] = float(t)

    def add(*ids):
        for i in ids:
            items[i] = {"t": "drops from Boss %d" % i, "a": "n", "p": 1}

    ld = make()
    check(ld.news() is None and ld.reload_ok is None, "nothing written yet")

    # ---- the first file: the addon never loaded one (the TOC line is new), so the nudge says restart -------------
    add(1, 2, 3)
    at(1000)
    check(ld.write() and ld.gen == 1, "gen 1")
    check(ld.news() == {"n": 3, "restart": True}, "the first news: restart")
    check(ld.news() is None, "once")
    # reloads while the old TOC is running: no "lg" at all; waited for, then not counted against /reload
    at(1100)
    ld.observe(None, True)
    at(1100 + live_data.LG_WAIT_S - 1)
    ld.observe(None, False)
    check(ld._pending is not None, "waiting for lg")
    at(1100 + live_data.LG_WAIT_S + 1)
    ld.observe(None, False)
    check(ld._pending is None and ld.stale == 0 and ld.reload_ok is None and not ld.loaded, "not /reload's fault")
    at(1200)
    ld.observe(None, True)
    at(1230)
    ld.observe(None, False)
    check(ld.stale == 0 and ld.reload_ok is None, "still not counted")

    # ---- WoW restarted: the addon loads gen 1 -------------------------------------------------------------------
    at(1300)
    ld.observe(1, True)
    check(ld.loaded and ld.reload_ok is True and ld.stale == 0 and ld.lg == 1, (ld.loaded, ld.reload_ok))

    # ---- new facts: the nudge says /reload; once per batch, and once per addon session --------------------------
    add(4, 5)
    at(2000)
    check(ld.write() and ld.gen == 2, "gen 2")
    check(ld.news() == {"n": 2, "restart": False}, "the /reload nudge")
    add(6)
    at(2015)
    check(ld.write() and ld.gen == 3, "gen 3")
    check(ld.news() is None, "said once")

    # a reload within RELOAD_AFTER_S of the gen-3 write is judged against gen 2, which it has
    at(2030)
    ld.observe(3, True)
    check(ld.stale == 0 and ld.reload_ok is True and ld.lg == 3, "good reload")
    check(ld.news() is None, "the addon has the newest file: nothing to say")
    add(7)
    at(2100)
    ld.write()                                                    # gen 4
    at(2110)
    ld.observe(4, False)                                          # (the addon has gen 4: a reload not seen)
    check(ld.news() is None and ld.told == ld.ids, "has it already: told without a word")

    # ---- an "lg" missing from one frame: waited for, then judged ------------------------------------------------
    add(8)
    at(3000)
    ld.write()                                                    # gen 5
    at(3100)
    ld.observe(None, True)
    at(3110)
    ld.observe(5, False)
    check(ld.stale == 0 and ld.lg == 5, "the next frame had it")

    # ---- two stale reloads, not in a row: still fine ------------------------------------------------------------
    add(9)
    at(4000)
    ld.write()                                                    # gen 6
    at(4100)
    ld.observe(5, True)
    check(ld.stale == 1 and ld.reload_ok is True, "one stale reload")
    at(4200)
    ld.observe(6, True)
    check(ld.stale == 0 and ld.reload_ok is True, "a good one in between")
    add(10)
    at(4300)
    ld.write()                                                    # gen 7
    at(4400)
    ld.observe(6, True)
    check(ld.stale == 1 and ld.reload_ok is True, "stale again")
    at(4410)                                                      # (a reload 10 s after a write: judged on gen 7)
    add(11)
    ld.write()                                                    # gen 8
    at(4420)
    ld.observe(7, True)
    check(ld.stale == 0, "lg 7 was the newest written 30 s before")

    # ---- two stale reloads in a row: /reload doesn't read the file; the nudge asks for a restart -----------------
    at(4500)
    ld.observe(7, True)
    check(ld.stale == 1 and ld.reload_ok is True, "stale 1")
    at(4600)
    ld.observe(None, True)                                        # (lg missing: waited for, then counted as 0)
    at(4600 + live_data.LG_WAIT_S + 1)
    ld.observe(None, False)
    check(ld.stale == 2 and ld.reload_ok is False, (ld.stale, ld.reload_ok))
    check(any("restart" in m for m in logged), logged)
    add(12, 13)
    at(5000)
    ld.write()
    check(ld.news() == {"n": 6, "restart": True}, "the restart wording after two stale reloads")

    check(ld.news() is None, "and that once")

    # ---- a restart of the engine keeps the verdict; a reload that has the newest file sets it back -------------------
    back = make()
    check(back.reload_ok is False and back.loaded and back.gen == ld.gen and back.stale == 2, "kept on disk")
    check(back.told_kind == "restart", "and what was said")
    at(6000)
    back.observe(back.gen, True)
    check(back.reload_ok is True and back.stale == 0, "a good reload: /reload works after all")
    check(any("says /reload again" in m for m in logged), logged)
    add(14)
    at(6100)
    back.write()
    at(6200)
    back.observe(back.gen - 1, True)
    check(back.stale == 1 and back.reload_ok is True, "one stale reload")
    check(back.news() == {"n": 1, "restart": False}, "the wording changed back: said once")

    # ---- the nudge once per wording, across engine restarts (regression: said again on every engine start and in
    # every play session) ---------------------------------------------------------------------------------------------
    shutil.rmtree(home)
    items.clear()
    ld = make()
    add(1)
    at(30000)
    ld.write()
    check(ld.news() == {"n": 1, "restart": True}, "the first: restart")
    ld.observe(1, True)                                           # WoW restarted: the addon loads gen 1
    add(2)
    at(30100)
    ld.write()
    check(ld.news() == {"n": 1, "restart": False}, "then the /reload nudge")
    again = make()                                                # the engine starts again, WoW still running
    add(3)
    at(30200)
    again.write()
    check(again.news() is None and again.told_kind == "reload", "not again after an engine restart")
    at(30300)
    again.observe(again.gen, True)                                # a good reload, then new facts
    add(4)
    at(30400)
    again.write()
    check(again.news() is None, "nor in the next play session")

    # ---- GameState: the time of the addon's restart, and tick() noticing it ------------------------------------------
    def ring(i, lg=None):
        core = {"t": "100", "l": "7", "x": "10", "X": "100", "e1": "%d:acc:%d" % (i, i)}
        if lg is not None:
            core["lg"] = str(lg)
        return core

    shutil.rmtree(home)
    items.clear()
    gs = GameState()
    ld = make(state_fn=lambda: gs)
    add(1)
    at(10000)
    ld.tick()                                                     # (no frames yet: written, nothing observed)
    check(ld.gen >= 1 and ld.lg is None and ld._restarts == 0, ld._restarts)
    with contextlib.redirect_stdout(io.StringIO()):
        for i in range(1, 6):
            gs.update(ring(i, ld.gen), {}, now=10010.0 + i)
        check(gs.addon_restarts == 0 and gs.addon_restart_t is None, "no restart yet")
        at(10020)
        ld.tick()
        check(ld.lg == ld.gen and ld.loaded, "lg read from the core")
        g = ld.gen
        add(2)
        at(10100)
        ld.tick()                                                 # the new gen written
        check(ld.gen == g + 1, "gen up")
        gs.update(ring(5, g), {}, now=10150.0)                    # the old load's last frame (its ring)
        gs.update(ring(1, g), {}, now=10200.0)                    # the addon starts again, still on the old gen
        check(gs.addon_restarts == 1 and gs.addon_restart_t == 10150.0, (gs.addon_restarts, gs.addon_restart_t))
        at(10210)
        ld.tick()
        check(ld.stale == 1 and ld.lg == g, "the stale reload seen through the game state")
        gs.update(ring(2, g), {}, now=10220.0)
        at(10225)
        ld.tick()
        check(ld.stale == 1, "one reload counts once")
        gs.update(ring(1, g + 1), {}, now=10300.0)                # again, and this time it has the new file
        check(gs.addon_restart_t == 10220.0, gs.addon_restart_t)
        at(10310)
        ld.tick()
        check(ld.stale == 0 and ld.lg == g + 1, "good reload through the game state")

    # ---- a reload noticed late: dated by the old load's last frame (regression: the addon sends no event at load, so
    # the restart shows only at the new load's first event, minutes on; dated then, a write in between read as a stale
    # reload, and two of those set reload_ok False for good) ------------------------------------------------------------
    def frame(t, i=None, lg=None):
        core = {"t": "100", "l": "60", "x": "0", "X": "0"}
        if i is not None:
            core["e1"] = "%d:acc:%d" % (i, i)
        if lg is not None:
            core["lg"] = str(lg)
        gs.update(core, {}, now=float(t))

    shutil.rmtree(home)
    items.clear()
    gs = GameState()
    ld = make(state_fn=lambda: gs)
    add(1)
    at(20000)
    ld.tick()
    with contextlib.redirect_stdout(io.StringIO()):
        for i in range(1, 6):
            frame(20000 + i, i, ld.gen)
        at(20010)
        ld.tick()
        check(ld.loaded and ld.lg == ld.gen, "loaded")
        for cycle in range(2):
            t0 = 20100 + 1000 * cycle                             # the /reload: it reads the newest gen
            for k in range(5):
                frame(t0 - 5 + k, 5, ld.gen)                      # the old load's last frames (its ring)
            loaded = ld.gen
            frame(t0 + 5, None, loaded)                           # the new load: no ring yet
            at(t0 + 6)
            ld.tick()
            add(100 + cycle)
            at(t0 + 15)
            ld.tick()                                             # a new item written after the reload
            check(ld.gen == loaded + 1, "written after it")
            frame(t0 + 70, None, loaded)
            at(t0 + 70)
            ld.tick()
            frame(t0 + 200, 1, loaded)                            # its first event: the restart noticed
            check(gs.addon_restart_t == t0 - 1, (cycle, gs.addon_restart_t))
            at(t0 + 201)
            ld.tick()
            check(ld.stale == 0 and ld.reload_ok is True, (cycle, ld.stale, ld.reload_ok))
            for i in range(2, 6):
                frame(t0 + 300 + i, i, loaded)                    # more events in this load (ids up to 5)
    check(ld.reload_ok is True and ld.stale == 0, "two late-noticed reloads that read the file: /reload works")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
