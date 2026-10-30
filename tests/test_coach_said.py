#!/usr/bin/env python3
"""The coach's own lines, named once for the knowledge side (knowledge/vocab.py COACH_SAID) and equal to the
engine's list (companion/msg_kinds.py): a /glide mark made right after a quest briefing or a reminder is stored
with that line (knowledge/sources/flags.py), and an eval replaying a session log sees those lines too
(knowledge/eval/run_eval.py replay).

Run: python3 tests/test_coach_said.py   (prints OK)
"""
import json
import os
import sqlite3
import sys
import tempfile
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.append(os.path.join(ROOT, "companion"))      # after ROOT: `knowledge` stays the package
from knowledge import vocab  # noqa: E402
from knowledge.sources import flags  # noqa: E402
import msg_kinds  # noqa: E402  (companion/msg_kinds.py)

assert vocab.COACH_SAID == msg_kinds.COACH_SAID, (vocab.COACH_SAID, msg_kinds.COACH_SAID)

# a mark whose last coach message is a briefing (an older callout before it, the engine's own note after it)
msgs = [{"kind": "callout", "text": "Kill Defias Thugs north of the abbey.", "t": 1.0},
        {"kind": "briefing", "text": "Next: Brotherhood of Thieves, bandanas from the Defias.", "t": 2.0},
        {"kind": "info", "text": "Marked for review: x", "t": 3.0}]
reminder = [{"kind": "answer", "text": "Yes, the vendor is in Goldshire.", "t": 1.0},
            {"kind": "reminder", "text": "Hearth is ready.", "t": 2.0}]
with tempfile.TemporaryDirectory(prefix="glide-coach-said-") as tmp:
    with open(os.path.join(tmp, "flags.jsonl"), "w", encoding="utf-8") as fh:
        for t, m in ((1791122600.4, msgs), (1791122700.0, reminder)):
            fh.write(json.dumps({"t": t, "note": "wrong", "state": {"name": "Brannoc", "class": "WARRIOR"},
                                 "last_messages": m, "recent_triggers": []}) + "\n")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE obs_session(id TEXT PRIMARY KEY, char TEXT, class TEXT, started REAL, ended REAL)")
    conn.execute("CREATE TABLE eval_flag(t INT, session TEXT, kind TEXT, text TEXT, state TEXT, note TEXT)")
    res = flags.load(conn, types.SimpleNamespace(live=tmp, cache={}))
    texts = [r[0] for r in conn.execute("SELECT text FROM eval_flag WHERE kind='mark' ORDER BY t")]
    conn.close()
    assert res["eval_flag"] == 2 and texts == [msgs[1]["text"], reminder[1]["text"]], (res, texts)

    # an eval replaying a session log keeps briefings and reminders among the coach's lines
    import knowledge.eval.run_eval as run_eval  # noqa: E402
    log = os.path.join(tmp, "companion.jsonl")
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": 10.0, "core": {}}) + "\n")
        for i, m in enumerate(msgs + reminder):
            fh.write(json.dumps({"t": 11.0 + i, "msg": m}) + "\n")
    _state, recent, lines = run_eval.replay(log + "#L99")
    assert lines == [m["text"] for m in msgs + reminder if m["kind"] != "info"], lines
    assert [r["kind"] for r in recent] == ["callout", "briefing", "answer", "reminder"], recent
print("OK")
