#!/usr/bin/env python3
"""Store lane: companion session logs -> obs_step_time (knowledge/sources/live_logs.py) and feedback marks ->
eval_flag (knowledge/sources/flags.py), on the fixture KB (tests/kb/fixture.py): the scripted play-through's
companion log (G/B/S pages, a death, a guide switch) and recorder session, plus a simulator log. The same on the
player's own logs is the report-only tests/kb/smoke_live_logs.py.

    python3 tests/kb/test_live_logs.py          (plain python, prints OK)
"""
import importlib.util
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import fixture  # noqa: E402
from knowledge import rxp_parser as rp  # noqa: E402
from knowledge import store  # noqa: E402
from knowledge.sources import flags, live_logs  # noqa: E402

def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def unit_checks():
    # step visibility (RXP stepLogic) under the player's settings
    ssf = {"soloSelfFound": True, "enableGroupQuests": True, "hardcore": False, "season": 0}
    check(not live_logs.step_shown({"hardcore": True}, ssf), "hardcore step hidden for a softcore player")
    check(live_logs.step_shown({"softcore": True}, ssf), "softcore step shown")
    check(not live_logs.step_shown({"ah": True}, ssf) and live_logs.step_shown({"ssf": True}, ssf), "ah/ssf")
    check(not live_logs.step_shown({"ssf": True}, live_logs.DEFAULT_SETTINGS), "ssf step hidden by default")
    check(live_logs.step_shown({"xprate": "<1.49"}, ssf) and not live_logs.step_shown({"xprate": ">1.59"}, ssf),
          "xprate at rate 1")
    check(live_logs.step_shown({"season": "0,1"}, ssf) and not live_logs.step_shown({"season": "2"}, ssf), "season")
    check(not live_logs.step_shown({"som": True}, ssf) and not live_logs.step_shown({"dungeon": "RFC"}, ssf),
          "som / dungeon steps hidden")
    check(not live_logs.step_shown({"solo": True}, ssf), "solo step hidden with group quests on")
    # names: the page's zero-padded RXP name matches the guide's #name
    check(live_logs.norm_name("05-11 Dun Morogh") == live_logs.norm_name("5-11 Dun Morogh"), "zero padding")
    check(live_logs.norm_name("10-12 X") == "10-12 x", "two-digit range untouched")
    check(live_logs.page_text("a;b=c|d", 5) == "a,b,c", "addon clean()")
    fr = [{"t": t, "l": l, "x": 0, "X": 400, "st": 1, "d": 0, "cl": None} for t, l in ((0, 1), (30, 2), (70, 3))]
    check(live_logs.implausible(fr, 0, 3), "two level-ups 40 s apart = simulator")
    check(live_logs.implausible(fr, 0, 2) is None, "one level-up is fine")
    check(live_logs.xp_delta({"l": 5, "x": 2700, "X": 2800}, {"l": 6, "x": 50, "X": 3600}) == 150, "xp over a level-up")


def mapping_checks(conn):
    """RXP numbers only the steps it keeps: fixture Dun Morogh step 3 is #hardcore, so RXP's 3 is our idx 4."""
    maps = live_logs.StepMaps(conn)
    key = fixture.DM
    defaults = {"soloSelfFound": False, "enableGroupQuests": False, "hardcore": False, "season": 0}
    plain = rp.rxp_index_map({"steps": maps.steps(key)}, cls="WARRIOR", race="Dwarf", faction="Alliance")
    m = maps.index_map(key, "WARRIOR", "Dwarf", "Alliance", defaults)
    check(plain[3] == 3 and m[3] == 4 and m[4] == 5 and 90001 in maps.quests(key)[m[4]],
          "RXP 3 -> idx 4, RXP 4 -> idx 5 (turn in 90001) %r" % ((plain.get(3), m.get(3)),))
    check(m[7] == 10 and 90003 in maps.quests(key)[10], "RXP 7 -> idx 10 (warlock steps 8, 9 left out)")
    hc = maps.index_map(key, "WARRIOR", "Dwarf", "Alliance", dict(defaults, hardcore=True))
    check(hc[3] == 3 and hc[4] == 5, "hardcore: the #softcore deathskip step is the one left out %r" % hc)


def main():
    unit_checks()
    tmp = fixture.tmpdir("live-")
    try:
        db, _summary, root, run = fixture.build(tmp)
        live = os.path.join(root, "data", "live")
        conn = store.open_rw(db)
        mapping_checks(conn)
        ctx = store.BuildContext(root=root, log=lambda *a: None)
        t0 = time.time()
        res = live_logs.load(conn, ctx)
        conn.commit()
        print("live_logs on the fixture logs: %.1fs, %d rows, S pages agree %s" % (
            time.time() - t0, res["obs_step_time"], res["s_page_check"]))
        for k, v in res["per_log"].items():
            print("  %s %s" % (k, json.dumps(v)))

        def n(sql, *a):
            return conn.execute(sql, a).fetchone()[0]

        rows = n("SELECT COUNT(*) FROM obs_step_time")
        visits = len(run["steps"])                                  # one per step the play-through walked
        check(rows == res["obs_step_time"] == visits, "rows %d, steps walked %d" % (rows, visits))
        check(n("SELECT COUNT(*) FROM obs_step_time WHERE t_end <= t_start") == 0, "t_end > t_start")
        check(n("SELECT COUNT(*) FROM obs_step_time o LEFT JOIN rxp_step s ON s.guide_key=o.guide_key AND "
                "s.idx=o.idx WHERE s.idx IS NULL") == 0, "every row joins an rxp_step")
        check(n("SELECT COUNT(*) FROM obs_step_time WHERE level IS NULL OR level < 5 OR level >= 11") == 0,
              "level between 5 and 10")
        check(n("SELECT COUNT(*) FROM obs_step_time WHERE xp_gain < 0 OR deaths < 0") == 0, "xp_gain, deaths >= 0")
        check(n("SELECT COUNT(*) FROM obs_step_time WHERE t_end - t_start < 1") == 0, "visits under 2 s skipped")
        check({r[0] for r in conn.execute("SELECT DISTINCT session FROM obs_step_time")} == {fixture.SESSION},
              "every row on the recorder session")
        # the visits follow the play-through: guide, idx and times
        got = [tuple(r) for r in conn.execute("SELECT guide_key, idx FROM obs_step_time ORDER BY t_start")]
        want = []
        for key, no, _t0, _t1 in run["steps"]:
            want.append((key, live_logs.StepMaps(conn).index_map(key, "WARRIOR", "Dwarf", "Alliance",
                                                                 live_logs.DEFAULT_SETTINGS)[no]))
        check(got == want, "visits %r" % got)
        check(got[0] == (fixture.DM, 1) and got[-1] == (fixture.LM, 5) and (fixture.DM, 4) in got, got)
        # the recorder saw each quest turned in: the visit at that moment is on the step that turns it in
        for q in run["quests"].values():
            if not q["turned_t"]:
                continue
            r = conn.execute("SELECT o.idx FROM obs_step_time o JOIN rxp_action a ON a.guide_key=o.guide_key AND "
                             "a.idx=o.idx AND a.verb='turnin' AND a.quest_id=? WHERE ? BETWEEN o.t_start AND o.t_end",
                             (q["id"], q["turned_t"])).fetchone()
            check(r is not None, "visit at the %d turn-in is on its turn-in step" % q["id"])
        check(n("SELECT SUM(deaths) FROM obs_step_time") == 1, "the deathskip death")
        check(n("SELECT SUM(xp_gain) FROM obs_step_time") > 0, "xp gained")
        sp = res["s_page_check"]
        check(sp["agree"] >= 10 and sp["disagree"] == 0, "S pages %r" % sp)
        check(res["rxp_settings"]["Fixa"]["from"] == "default", res["rxp_settings"])
        check(res["logs_simulated"] == [fixture.SIM_LOG] and res["logs_with_rows"] == 1, "simulator log skipped %r"
              % res["logs_simulated"])
        # idempotent: a second run replaces the rows
        res2 = live_logs.load(conn, ctx)
        check(res2["obs_step_time"] == rows and n("SELECT COUNT(*) FROM obs_step_time") == rows, "re-run")

        print("top 3 longest steps:")
        for _sid, key, idx, dt, lvl, xp, deaths, text in conn.execute(
                "SELECT o.session, o.guide_key, o.idx, o.t_end - o.t_start AS dt, o.level, o.xp_gain, o.deaths, s.text "
                "FROM obs_step_time o JOIN rxp_step s ON s.guide_key=o.guide_key AND s.idx=o.idx "
                "ORDER BY dt DESC LIMIT 3"):
            print("  %5ds  %-22s #%-3d L%-6.2f xp %-5d deaths %d  %s" % (
                dt, key.split("||")[-1], idx, lvl, xp, deaths, (text or "").split("\n")[0][:70]))

        # feedback marks -> eval_flag, on the session the companion log puts at that time
        spec = importlib.util.spec_from_file_location("glide_feedback", os.path.join(ROOT, "companion", "feedback.py"))
        feedback = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(feedback)
        fb = feedback.Feedback(tmp)                              # writes <tmp>/data/live/flags.jsonl
        msgs = [{"kind": "callout", "text": "Kill Fixture Boars south of the hub.", "t": 1.0},
                {"kind": "info", "text": "Marked for review: x", "t": 2.0}]
        fb.on_mark("boars are elsewhere", {"name": "Fixa", "class": "WARRIOR", "level": 6, "buffs": []},
                   msgs, [("step_changed", {"from": 4, "to": 5})])
        fb.on_mark("", {"name": "Nobody", "level": 1}, [], [])
        with open(fb.path, encoding="utf-8") as fh:
            recs = [json.loads(x) for x in fh]
        mark_t = run["steps"][4][2] + 30                          # on RXP step 5, inside the recorder session
        recs[0]["t"] = mark_t + 0.4
        recs[1]["t"] = fixture.T0 - 86400                         # a day before: no session
        with open(os.path.join(live, "flags.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(r) for r in recs) + "\nnot json\n")
        fres = flags.load(conn, ctx)
        conn.commit()
        print("flags:", json.dumps(fres))
        got = conn.execute("SELECT t, session, kind, text, state, note FROM eval_flag WHERE kind='mark' ORDER BY t") \
            .fetchall()
        check(fres["eval_flag"] == 2 and fres["bad_lines"] == 1 and len(got) == 2, got)
        t, session, kind, text, state, note = got[1]
        check(t == mark_t and session == fixture.SESSION and text == "Kill Fixture Boars south of the hub."
              and note == "boars are elsewhere" and json.loads(state)["level"] == 6, got[1])
        check(got[0][1] is None and got[0][5] is None and got[0][3] is None, "unmatched mark %r" % (got[0],))
        check(flags.load(conn, ctx)["eval_flag"] == 2 and n("SELECT COUNT(*) FROM eval_flag") == 2, "flags re-run")
        conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    main()
