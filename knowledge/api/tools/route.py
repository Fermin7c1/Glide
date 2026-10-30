"""Route tools: why a step exists, the steps ahead, the guide as modelled."""
from knowledge import store
from knowledge.api._layer import R
from knowledge.api.results import collect_facts
from knowledge.api.values import as_int, clean, first_line, uniq


def t_step_brief(ctx, rxp_step_no=None, idx=None, guide=None):
    err = ctx.need(guide=not guide, step=False, player=True)
    if err:
        return err
    pk = ctx.pack
    if guide:
        gk = pk.guide_loose(ctx, guide)
        if gk is None:
            return {"error": "no RestedXP guide matches %r" % guide}
    else:
        gk = ctx.guide_key
    route = R.get_route(ctx.kb, gk, ctx.player)
    i = None
    if idx is not None and as_int(idx) in route.by_idx:
        i = as_int(idx)
    elif rxp_step_no is not None:
        i = route.rxp_to_idx.get(as_int(rxp_step_no))
        if i is None:
            return {"error": "%s has no RestedXP step %s for you (steps 1-%d)" % (pk.guide_name(gk), rxp_step_no,
                                                                                 len(route.steps))}
    elif gk == ctx.guide_key and ctx.idx is not None:
        i = ctx.idx
    if i is None:
        return {"error": "give rxp_step_no (the number RestedXP shows) or idx"}
    b = pk.brief(ctx, i, gk)
    if not b:
        return {"unavailable": "no analysis for that step"}
    out = pk.trim_brief(ctx, b, route, alts=3, full=True)
    if gk == ctx.guide_key:
        live = pk.live_compare(ctx, b)
        if live and live.get("exp_level") is not None:
            out["you"] = clean({"level_now": live.get("exp_level"), "delta_vs_plan": live.get("level_delta"),
                                 "notes": (live.get("notes") or [])[:4]})
        if ctx.idx is not None and i != ctx.idx:
            out["relative"] = "%d steps %s" % (abs(route.index_of(i) - ctx.pos), "ahead" if i > ctx.idx else "back")
    out["facts"] = uniq(collect_facts(out) + [f for f in b.get("facts") or [] if f.startswith(("p:", "poi:"))])
    return out


def t_why_this_step(ctx):
    err = ctx.need()
    if err:
        return err
    out = t_step_brief(ctx, idx=ctx.idx)
    if isinstance(out, dict) and not out.get("error"):
        out["current"] = True
    return out


def t_route_lookahead(ctx, n=None):
    err = ctx.need()
    if err:
        return err
    return route_steps(ctx, max(1, min(12, as_int(n) or 5)))


def route_steps(ctx, k):
    """The current RestedXP step and the next k-1 shown steps of the analysed route (step number as RestedXP shows
    it, kind, text, XP, quests, time, planned level, where, fact id). Used by the route_lookahead tool (k <= 12) and
    by the coach's question context (spoken: 11, typed: 41). {} when the guide or step isn't resolved."""
    if ctx is None or ctx.route is None or ctx.pos is None:
        return {}
    pk, route = ctx.pack, ctx.route
    k = max(1, int(k))
    out, facts = [], []
    for i, st in enumerate(route.steps[ctx.pos:ctx.pos + k]):
        b = pk.brief(ctx, st["idx"]) or {}
        pos = st.get("pos")
        qs = (b.get("justification") or {}).get("quests") or {}
        out.append(clean({"n": st["rxp_step_no"], "current": i == 0 or None, "kind": b.get("kind") or st["kind"],
                           "text": first_line(st["text"], 100), "xp_now": b.get("xp_now"),
                           "quests": [q.get("name") for q in qs.values() if q.get("name")][:3],
                           "time_s": b.get("time_s"), "exp_level": b.get("exp_level_before"),
                           "where": pk.where(ctx, *pos) if pos else None, "fact": ctx.step_fact(st["idx"])}))
        facts.append(ctx.step_fact(st["idx"]))
        facts += ["q:%s" % q for q in qs]
    return {"guide": ctx.guide, "steps": out, "facts": uniq(facts)}


def t_guide_summary(ctx):
    err = ctx.need(step=False)
    if err:
        return err
    pk, p, gk, route = ctx.pack, ctx.player, ctx.guide_key, ctx.route
    key = (gk, p["class"], p.get("race"), p["faction"], ctx.settings)
    hit = pk.guide_summaries.get(key)
    if hit is not None:
        return hit
    # the stored summary was modelled with the build's RestedXP options: other options get a fresh one
    row = ctx.kb.one("SELECT * FROM guide_brief WHERE guide_key=? AND class=?", gk, p["class"])         if R.stored_matches(ctx.kb, gk, p) else None
    if row:
        hubs, notes = store.jload(row.get("hubs"), []), store.jload(row.get("notes"), {})
    else:
        sm = R.guide_summary(route, R.briefs_for(ctx.kb, gk, p, with_alternatives=False))
        row, hubs, notes = sm, sm.get("hubs") or [], sm.get("notes") or {}
    rx = route.idx_to_rxp.get
    g = pk.data.guide_rows.get(gk) or {}
    nxt = []
    try:
        for nk in R.next_guides(ctx.kb, gk, p)[:3]:
            nr = ctx.kb.one("SELECT start_level, end_level, time_s FROM guide_brief WHERE guide_key=? AND class=?",
                            nk, p["class"]) or {}
            nxt.append(clean({"guide": pk.guide_name(nk), "start_level": nr.get("start_level"),
                               "end_level": nr.get("end_level"), "time_s": nr.get("time_s")}))
    except Exception:  # noqa: BLE001
        pass
    start_from = notes.get("start_from")
    out = clean({
        "guide": pk.guide_name(gk), "group": g.get("grp"), "class": p["class"],
        "level_range": [g.get("level_min"), g.get("level_max")], "route_steps": len(route.steps),
        "start_level": row.get("start_level"), "end_level": row.get("end_level"), "time_s": row.get("time_s"),
        "xp_total": row.get("xp_total"), "xp_quests": row.get("xp_quests"), "xp_kills": row.get("xp_kills"),
        "xp_grind": row.get("xp_grind"),
        "hubs": [clean({"step": rx(h.get("idx")), "zone": h.get("zone"), "turnins": h.get("turnins"),
                         "xp": h.get("xp"), "fact": ctx.step_fact(h["idx"]) if h.get("idx") is not None else None})
                 for h in hubs[:6]],
        "start_from": pk.guide_name(start_from) if "||" in str(start_from or "") else start_from,
        "grind_over_one_level_steps": [rx(i) for i in notes.get("grind_over_one_level") or []][:6],
        "next": nxt})
    out["facts"] = collect_facts(out)
    return pk.cache_put(pk.guide_summaries, key, out, 32)
