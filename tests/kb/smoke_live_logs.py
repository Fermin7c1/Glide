#!/usr/bin/env python3
"""Smoke (report-only, see tests/kb/smoke.py): knowledge/sources/live_logs.py and flags.py on the player's own
companion logs (data/live) and a temp copy of data/kb.db (the real one is never written). Facts of logs recorded
on 2026-10-04; tests/kb/test_live_logs.py checks the same code on the fixture play-through.

Logs used: 20261004-095223 (no pages: guide from the recorder's turn-ins; Northshire -> Elwynn switch; two
recorder sessions) and 20261004-154307 (G/B/S pages; no recorder session of its own: continues 151802).

    py -3 tests/kb/smoke_live_logs.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import smoke  # noqa: E402

ROOT = smoke.ROOT
sys.path.insert(0, ROOT)
from knowledge import rxp_parser as rp  # noqa: E402
from knowledge import store  # noqa: E402
from knowledge.sources import flags, live_logs  # noqa: E402

KB = smoke.KB
LIVE = smoke.LIVE
LOGS = ("20261004-095223.jsonl", "20261004-154307.jsonl")


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def mapping_checks(conn):
    """RXP numbers only the steps it keeps: Dun Morogh step 3 is #hardcore, so RXP's 4 is our idx 5."""
    maps = live_logs.StepMaps(conn)
    key = "RestedXP Forever Guide (A)||5-11 Dun Morogh"
    if not maps.steps(key):
        raise smoke.NotRun("%s not in the KB" % key)
    hoghe = {"soloSelfFound": True, "enableGroupQuests": True, "hardcore": False, "season": 0}
    plain = rp.rxp_index_map({"steps": maps.steps(key)}, cls="WARRIOR", race="Gnome", faction="Alliance")
    m = maps.index_map(key, "WARRIOR", "Gnome", "Alliance", hoghe)
    check(plain[4] == 4 and m[4] == 5 and 96628 in maps.quests(key)[m[4]], "RXP 4 -> idx 5 (turn in 96628) %r"
          % ((plain.get(4), m.get(4)),))
    check(m[10] == 13 and 2160 in maps.quests(key)[13], "RXP 10 -> idx 13 (warlock steps 9, 10 left out)")


def main():
    smoke.need(KB, *[os.path.join(LIVE, name) for name in LOGS])
    tmp = tempfile.mkdtemp(prefix="glide_live_test_")
    try:
        db = os.path.join(tmp, "kb.db")
        shutil.copyfile(KB, db)                                   # copy first: never write data/kb.db
        live = os.path.join(tmp, "live")
        os.makedirs(live)
        for name in LOGS:
            shutil.copyfile(os.path.join(LIVE, name), os.path.join(live, name))
        conn = store.open_rw(db)
        store.create_schema(conn)
        mapping_checks(conn)
        ctx = store.BuildContext(live=live, log=lambda *a: None)
        t0 = time.time()
        res = live_logs.load(conn, ctx)
        conn.commit()
        print("live_logs on %s: %.1fs, %d rows, S pages agree %s" % (", ".join(LOGS), time.time() - t0,
                                                                    res["obs_step_time"], res["s_page_check"]))
        for k, v in res["per_log"].items():
            print("  %s %s" % (k, json.dumps(v)))
        check(res.get("obs_step_time", 0) >= 20, "obs_step_time rows: %r" % res)

        def n(sql, *a):
            return conn.execute(sql, a).fetchone()[0]

        rows = n("SELECT COUNT(*) FROM obs_step_time")
        check(rows == res["obs_step_time"] and rows >= 20, "rows in table %d" % rows)
        check(n("SELECT COUNT(*) FROM obs_step_time WHERE t_end <= t_start") == 0, "t_end > t_start")
        check(n("SELECT COUNT(*) FROM obs_step_time o LEFT JOIN rxp_step s ON s.guide_key=o.guide_key AND "
                "s.idx=o.idx WHERE s.idx IS NULL") == 0, "every row joins an rxp_step")
        check(n("SELECT COUNT(*) FROM obs_step_time WHERE level IS NULL OR level < 1 OR level >= 21") == 0,
              "level between 1 and 20")
        check(n("SELECT COUNT(*) FROM obs_step_time WHERE xp_gain < 0 OR deaths < 0") == 0, "xp_gain, deaths >= 0")
        check(n("SELECT COUNT(*) FROM obs_step_time WHERE t_end - t_start < 1") == 0, "visits under 2 s skipped")
        check(n("SELECT COUNT(*) FROM obs_step_time o LEFT JOIN obs_session s ON s.id=o.session WHERE s.id IS NULL")
              == 0, "every row on a recorder session")
        # 095223: guide from the recorder's turn-ins, Northshire then Elwynn; split across two recorder sessions
        early = [tuple(r) for r in conn.execute("SELECT DISTINCT session, guide_key FROM obs_step_time "
                                                "WHERE t_start < 1791124100 ORDER BY t_start")]
        check(("20261004-095220", "RestedXP Forever Guide (A)||1-6 Northshire") in early and
              ("20261004-102546", "RestedXP Forever Guide (A)||6-11 Elwynn Forest") in early, early)
        # the recorder saw quest 3903 turned in at 10:11:55 (Northshire) and 2158 at 10:27:29 (Elwynn): the visits
        # at those moments are on the steps that turn them in
        for qid, t in ((3903, 1791123115), (2158, 1791124049)):
            r = conn.execute("SELECT o.idx FROM obs_step_time o JOIN rxp_action a ON a.guide_key=o.guide_key AND "
                             "a.idx=o.idx AND a.verb='turnin' AND a.quest_id=? WHERE ? BETWEEN o.t_start AND o.t_end",
                             (qid, t)).fetchone()
            check(r is not None, "visit at the %d turn-in is on its turn-in step" % qid)
        # 154307: no recorder session of its own, continues Swift's 151802; RXP S pages agree with the mapping
        late = {r[0] for r in conn.execute("SELECT DISTINCT session FROM obs_step_time WHERE t_start > 1791143000")}
        check(late == {"20261004-151802"}, "continuation session %r" % late)
        sp = res["s_page_check"]
        check(sp["agree"] >= 200 and sp["agree"] >= 0.95 * (sp["agree"] + sp["disagree"]), "S pages %r" % sp)
        check(res["rxp_settings"].get("Swift", {}).get("soloSelfFound") is True, res["rxp_settings"])
        check(n("SELECT SUM(deaths) FROM obs_step_time WHERE session='20261004-151802'") >= 10, "Swift's deaths")
        # idempotent: a second run replaces the rows
        res2 = live_logs.load(conn, ctx)
        check(res2["obs_step_time"] == rows and n("SELECT COUNT(*) FROM obs_step_time") == rows, "re-run")

        print("top 5 longest steps:")
        for sid, key, idx, dt, lvl, xp, deaths, text in conn.execute(
                "SELECT o.session, o.guide_key, o.idx, o.t_end - o.t_start AS dt, o.level, o.xp_gain, o.deaths, s.text "
                "FROM obs_step_time o JOIN rxp_step s ON s.guide_key=o.guide_key AND s.idx=o.idx "
                "ORDER BY dt DESC LIMIT 5"):
            print("  %5ds  %-22s #%-3d L%-6.2f xp %-5d deaths %d  %s  [%s]" % (
                dt, key.split("||")[-1], idx, lvl, xp, deaths, (text or "").split("\n")[0][:70], sid))

        # feedback marks -> eval_flag, on the session the companion log puts at that time
        spec = importlib.util.spec_from_file_location("glide_feedback", os.path.join(ROOT, "companion", "feedback.py"))
        feedback = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(feedback)
        fb = feedback.Feedback(tmp)                              # writes <tmp>/data/live/flags.jsonl
        msgs = [{"kind": "callout", "text": "Kill Defias Thugs north of the abbey.", "t": 1.0},
                {"kind": "info", "text": "Marked for review: x", "t": 2.0}]
        fb.on_mark("thugs are elsewhere", {"name": "Brannoc", "class": "WARRIOR", "level": 4, "buffs": []},
                   msgs, [("step_changed", {"from": 27, "to": 28})])
        fb.on_mark("", {"name": "Nobody", "level": 1}, [], [])
        with open(fb.path, encoding="utf-8") as fh:
            recs = [json.loads(x) for x in fh]
        recs[0]["t"] = 1791122600.4                               # 10:03:20, inside 095223 / session 095220
        with open(os.path.join(live, "flags.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(r) for r in recs) + "\nnot json\n")
        fres = flags.load(conn, ctx)
        conn.commit()
        print("flags:", json.dumps(fres))
        got = conn.execute("SELECT t, session, kind, text, state, note FROM eval_flag WHERE kind='mark' ORDER BY t") \
            .fetchall()
        check(fres["eval_flag"] == 2 and fres["bad_lines"] == 1 and len(got) == 2, got)
        t, session, kind, text, state, note = got[0]
        check(t == 1791122600 and session == "20261004-095220" and text == "Kill Defias Thugs north of the abbey."
              and note == "thugs are elsewhere" and json.loads(state)["level"] == 4, got[0])
        check(got[1][1] is None and got[1][5] is None and got[1][3] is None, "unmatched mark %r" % (got[1],))
        check(flags.load(conn, ctx)["eval_flag"] == 2 and n("SELECT COUNT(*) FROM eval_flag") == 2, "flags re-run")
        conn.close()

        # every log in data/live (copy of the KB again): which ones map, and why not
        shutil.copyfile(KB, db)
        conn = store.open_rw(db)
        res = live_logs.load(conn, store.BuildContext(log=lambda *a: None))
        print("all logs: %d rows from %d of %d logs; on recorder sessions: %d; simulated: %d; S pages %s" % (
            res["obs_step_time"], res["logs_with_rows"], res["logs"], res["logs_on_recorder_sessions"],
            len(res["logs_simulated"]), res["s_page_check"]))
        check(res["logs_with_rows"] >= 14 and not (set(res["logs_simulated"]) & {
            "20261004-091422", "20261004-095223", "20261004-154307"}), "real logs mapped")
        check({"20261004-082254", "20261004-130021", "20261004-141116", "20261004-142235"} <=
              set(res["logs_simulated"]), "simulator logs skipped: %r" % res["logs_simulated"])
        conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    smoke.run("live_logs", [("the 2026-10-04 logs on a copy of data/kb.db", main)])
