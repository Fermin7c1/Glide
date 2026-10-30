"""Loader: player feedback marks (data/live/flags.jsonl, written by companion/feedback.py) -> eval_flag.

A flags.jsonl line is {"t", "note", "state" (GameState.summary() without big lists), "last_messages" (the
coach's last messages), "recent_triggers"}. One eval_flag row per line:
  t        epoch seconds (int)
  session  the session the companion log puts at that moment (live_logs leaves its log -> session spans in
           ctx.cache["live_spans"]), else the recorder session (obs_session) of the same character around t
           (+-2 min, or up to 15 min after it ended), else NULL
  kind     'mark'
  text     the coach's last line before the mark (a callout, answer, review, briefing or reminder:
           vocab.COACH_SAID); else its last message; else NULL
  state    the flag's state as JSON
  note     the player's note (NULL when marked from the key binding without one)
The loader replaces the 'mark' rows of eval_flag; other kinds are left alone.
"""
import json
import os

from knowledge import store
from knowledge.vocab import COACH_SAID

SLACK = 120.0
CONTINUE = 900.0


def last_coach_text(msgs):
    msgs = [m for m in (msgs or []) if isinstance(m, dict) and m.get("text")]
    for m in reversed(msgs):
        if m.get("kind") in COACH_SAID:
            return m["text"]
    return msgs[-1]["text"] if msgs else None


def session_at(t, char, cls, spans, sessions):
    """(session id or None, how it was found) for a mark at time t by character `char` (class `cls`)."""
    char = str(char).lower() if char else None
    best = None
    for sp in spans:                              # (t0, t1, session, character, log) from live_logs
        t0, t1, sid, who = sp[0], sp[1], sp[2], sp[3]
        if char and who and str(who).lower() != char:
            continue
        dist = 0.0 if t0 <= t <= t1 else min(abs(t - t0), abs(t - t1))
        if dist <= SLACK and (best is None or dist < best[0]):
            best = (dist, sid)
    if best:
        return best[1], "companion log"

    def same(s):
        return ((not char or not s.get("char") or str(s["char"]).lower() == char)
                and (not cls or not s.get("class") or str(s["class"]).upper() == str(cls).upper()))
    inside = [s for s in sessions if same(s) and s["started"] - SLACK <= t <= (s["ended"] or s["started"]) + SLACK]
    if inside:
        return max(inside, key=lambda s: s["started"])["id"], "recorder session"
    before = [s for s in sessions if same(s) and s["ended"] and s["ended"] < t <= s["ended"] + CONTINUE]
    if before:
        return max(before, key=lambda s: s["ended"])["id"], "recorder session (continued)"
    return None, "none"


def load(conn, ctx):
    path = os.path.join(getattr(ctx, "live", None) or "", "flags.jsonl")
    if not os.path.exists(path):
        return {"skipped": "no %s (no '/glide mark' yet)" % path}
    cur = conn.execute("SELECT id, char, class, started, ended FROM obs_session WHERE started IS NOT NULL")
    cols = [d[0] for d in cur.description]
    sessions = [dict(zip(cols, r)) for r in cur.fetchall()]
    spans = (getattr(ctx, "cache", None) or {}).get("live_spans") or []
    rows, bad, how = [], 0, {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                t = float(rec["t"])
            except (ValueError, TypeError, KeyError, IndexError):
                bad += 1
                continue
            state = rec.get("state") if isinstance(rec.get("state"), dict) else None
            sid, found = session_at(t, (state or {}).get("name"), (state or {}).get("class"), spans, sessions)
            how[found] = how.get(found, 0) + 1
            note = str(rec.get("note") or "").strip() or None
            rows.append((int(round(t)), sid, "mark", last_coach_text(rec.get("last_messages")),
                         store.jdump(state) if state is not None else None, note))
    conn.execute("DELETE FROM eval_flag WHERE kind='mark'")
    store.insert_rows(conn, "eval_flag", rows, ["t", "session", "kind", "text", "state", "note"])
    return {"eval_flag": len(rows), "bad_lines": bad, "session_from": how}
