"""Session tools: this session against the model, and what the coach said recently."""
from knowledge.api._layer import E, R, V
from knowledge.api.values import as_int, clean, uniq


def _live_session(ctx):
    """The recorder session (obs_session) this live session is, when the KB has its step times."""
    name = ctx.summary.get("name")
    if not name:
        return None
    return ctx.kb.one("""SELECT s.id, s.started, s.ended FROM obs_session s
                         WHERE lower(s.char)=lower(?) AND s.started <= ? AND COALESCE(s.ended, s.started) + 900 >= ?
                         AND EXISTS (SELECT 1 FROM obs_step_time o WHERE o.session = s.id)
                         ORDER BY s.started DESC LIMIT 1""", name, ctx.now, ctx.now)


def t_session_review(ctx):
    err = ctx.need(guide=False, step=False, player=True)
    if err:
        return err
    pk, s, book = ctx.pack, ctx.summary, ctx.book
    lv = ctx.level
    bench = E.benchmark_xph(lv) if lv else None
    xph = as_int(s.get("xp_per_hour"))
    live = {"xp_per_hour": xph or None, "benchmark_xp_per_hour": bench,
            "pace_vs_benchmark": round(xph / float(bench), 2) if xph and bench else None,
            "session_minutes": s.get("session_minutes"), "xp_session": s.get("xp_session"),
            "deaths_session": book.get("deaths_session", 0), "deaths_last_30min": book.get("deaths_recent", 0),
            "on_step_s": s.get("on_step_seconds")}
    visits, facts = [], []
    for v in (book.get("visits") or [])[-8:]:
        e = {"guide": v.get("guide"), "step": v.get("step"), "seconds": int((v.get("t1") or 0) - (v.get("t0") or 0))}
        if ctx.route is not None and v.get("guide") == s.get("guide"):
            idx = ctx.route.rxp_to_idx.get(as_int(v.get("step")) or -1)
            b = pk.brief(ctx, idx) if idx is not None else None
            if b:
                e["model_s"], e["kind"] = b.get("time_s"), b.get("kind")
                facts.append(ctx.step_fact(idx))
        visits.append(clean(e))
    live["recent_steps"] = visits
    out = {"live": clean(live)}
    sess = _live_session(ctx)
    if sess:
        rv = V.review_session(ctx.kb, sess["id"], ctx.player)
        sinks = []
        for v in rv.get("time_sinks") or []:
            try:
                step = R.get_route(ctx.kb, v["guide_key"], ctx.player).idx_to_rxp.get(v["idx"])
            except Exception:  # noqa: BLE001
                step = None
            sinks.append(clean({"guide": pk.guide_name(v.get("guide_key")), "step": step, "kind": v.get("kind"),
                                 "quests": v.get("quests"), "actual_s": v.get("actual_s"),
                                 "expected_s": v.get("expected_s"), "delta_s": v.get("delta_s")}))
            facts.append(ctx.step_fact(v["idx"], pk.guide_name(v.get("guide_key"))))
        out["recorded"] = clean({"session": sess["id"], "guides": [pk.guide_name(g) for g in rv.get("guides") or []],
                                  "totals": rv.get("totals"), "time_sinks": sinks[:4],
                                  "summary": (rv.get("facts") or [])[:6]})
        out["source"] = "recorded step times of this session (obs_step_time) + companion stats"
    else:
        out["source"] = "companion session stats (this session has no recorded step times in the knowledge base yet)"
    out["facts"] = uniq(facts)
    return out


def t_recent_log(ctx, n=None):
    k = max(1, min(20, as_int(n) or 5))
    rows = []
    for e in ctx.recent[-k:]:
        r = {k2: v for k2, v in e.items() if k2 != "t"}
        if e.get("t"):
            r["ago_s"] = int(max(0, ctx.now - e["t"]))
        rows.append(clean(r))
    return {"entries": rows, "count": len(rows),
            "note": None if rows else "nothing said or triggered yet this session", "facts": []}
