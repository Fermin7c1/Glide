"""What-if planner over the route model: walk a route from any state, with deviations.

  run(kb, route, state, start_idx, end_idx=None, variant=None, follow=0) -> {"timeline", "end", "start", ...}
  compare(kb, route, state, start_idx, variant) -> {"baseline", "variant", "delta"}
  eta(kb, route, state, start_idx, target_level) -> {"time_s", "reached", "guide_key", "idx", ...}

state (dict): {"level", "xp", "time_s", "ui_map", "x", "y", "quests_done": set, "quests_active": set,
               "hearth_ready_t", "rested_pool"} (+ optional "bind": (ui_map, x, y)); None = guide start.
variant (dict, one key):
  {"skip_quest": id}                      drop every action of the quest and of everything that becomes
                                          unreachable (follow-ups needing it, in this guide and the next)
  {"skip_step": idx}                      skip one step (or a list of idx)
  {"grind_here": {"idx", "minutes"|"to_level"}}   extra grind after step idx (step mobs or best spot nearby)
  {"hold_turnin": {"quest", "until_level"}}       keep the completed quest until the level, then make the
                                          trip back to the ender (released at the end if never reached)
  {"leave_zone_at": idx}                  stop this guide before idx and continue with the next guide
  {"do_dungeon": guide_key}               run that dungeon guide (group, all dungeons) before start_idx
route: a route.Route, or (guide_key, player).
"""
from . import params as AP
from .model import State, walk, level_f, total_xp, MAX_LEVEL, CUM
from .route import Route, get_route, next_guides, start_state


def _route(kb, route):
    if isinstance(route, Route):
        return route
    if isinstance(route, (tuple, list)) and len(route) == 2:
        return get_route(kb, route[0], route[1])
    raise TypeError("route must be a Route or (guide_key, player)")


def _state(route, state):
    if state is None:
        return start_state(route)
    if isinstance(state, State):
        return state.copy()
    st = State.from_dict(state)
    return st


def _entry(route, r):
    e = {"guide_key": route.guide_key, "idx": r["idx"], "kind": r["kind"], "applied": r["applied"],
         "t0": round(r["t0"], 1), "time_s": round(r["time_s"], 1), "level_before": r["lvl0"],
         "level_after": r["lvl1"], "xp_now": int(r["xp_now"])}
    if r["skip"]:
        e["skip"] = r["skip"]
    if r["turnins"]:
        e["turnins"] = [[t["quest_id"], t["xp"]] for t in r["turnins"]]
    if r["notes"]:
        e["notes"] = r["notes"]
    return e


def unreachable(data, quest_id, scope):
    """Quests in `scope` that can no longer be done once quest_id is skipped: follow-ups that require it
    (next_quest and 'pre' prerequisites; a 'pregroup' only when every alternative is lost)."""
    lost = {quest_id}
    changed = True
    while changed:
        changed = False
        for q in scope:
            if q in lost:
                continue
            pre = data.prereqs.get(q, ())
            req = [p for p, k in pre if k == "pre"]
            grp = [p for p, k in pre if k == "pregroup"]
            prev = [p for p, nq in ((p, (data.quest(p) or {}).get("next_quest")) for p in lost) if nq == q]
            if prev or any(p in lost for p in req) or (grp and all(p in lost or p not in scope for p in grp)
                                                        and any(p in lost for p in grp)):
                lost.add(q)
                changed = True
    return lost


def _route_quests(route):
    qs = set()
    for st in route.steps:
        for k in ("accept", "turnin", "objective"):
            qs.update(st["quests"][k])
    return qs


def _opts(kb, route, variant, follow_routes):
    """Walker options for a variant (applied to this route and the follow-up routes)."""
    opts = {}
    notes = []
    if not variant:
        return opts, notes
    data = route.data
    if "skip_quest" in variant:
        q = int(variant["skip_quest"])
        scope = _route_quests(route)
        for r in follow_routes:
            scope |= _route_quests(r)
        lost = unreachable(data, q, scope)
        opts["skip_quests"] = lost
        names = [(data.quest(x) or {}).get("name") or str(x) for x in sorted(lost) if x != q]
        if names:
            notes.append("skipping %s also drops %d follow-up quest(s): %s"
                         % ((data.quest(q) or {}).get("name") or q, len(names), ", ".join(names[:6])))
    if "skip_step" in variant:
        v = variant["skip_step"]
        opts["skip_steps"] = set(v if isinstance(v, (list, tuple, set)) else [v])
    if "grind_here" in variant:
        g = dict(variant["grind_here"])
        opts["grind_at"] = {int(g.pop("idx")): g}
    if "hold_turnin" in variant:
        h = variant["hold_turnin"]
        opts["hold"] = {int(h["quest"]): int(h.get("until_level") or MAX_LEVEL)}
    return opts, notes


def _walk(kb, route, st, i0, i1, opts, timeline, follow=None):
    W, res = walk(route.data, route, st, i0, i1, opts)
    timeline.extend(_entry(route, r) for r in res)
    return W, res


def _release_held(W, route, st, timeline):
    if st.held:
        dummy = {"xp_quests": 0, "turnins": [], "params": set(), "act_s": 0.0, "notes": []}
        t0, l0 = st.time, level_f(st.level, st.xp)
        W.release_all_held(st, dummy)
        st.time += dummy["act_s"]
        timeline.append({"guide_key": route.guide_key, "idx": None, "kind": "turnin", "applied": True, "t0": round(t0, 1),
                         "time_s": round(dummy["act_s"], 1), "level_before": l0,
                         "level_after": level_f(st.level, st.xp), "xp_now": dummy["xp_quests"],
                         "turnins": [[t["quest_id"], t["xp"]] for t in dummy["turnins"]], "notes": dummy["notes"]})


def _follow(kb, route, n):
    out, cur = [], route
    for _ in range(n):
        nx = next_guides(kb, cur.guide_key, cur.player, data=cur.data)
        if not nx:
            break
        cur = get_route(kb, nx[0], cur.player)
        out.append(cur)
    return out


def run(kb, route, state=None, start_idx=None, end_idx=None, variant=None, follow=0):
    """Walk the route from start_idx (default: first step) to end_idx (inclusive, default: end), then `follow`
    next guides. Returns {"timeline", "start", "end", "guides", "notes"}."""
    route = _route(kb, route)
    variant = variant or {}
    st = _state(route, state)
    start = st.to_dict()
    i0 = route.index_of(start_idx) if start_idx is not None else 0
    i1 = route.index_of(int(end_idx) + 1) if end_idx is not None else len(route.steps)
    routes_after = _follow(kb, route, max(int(follow or 0), 1 if ("leave_zone_at" in variant or "skip_quest" in variant) else 0))
    opts, notes = _opts(kb, route, variant, routes_after)
    timeline, guides = [], [route.guide_key]
    if "do_dungeon" in variant:
        dkey = variant["do_dungeon"]
        p = dict(route.player)
        p.update(dungeons="all", group_quests=True)
        try:
            droute = Route(kb, dkey, p, data=route.data)
        except KeyError:
            droute = None
            notes.append("dungeon guide %s not found" % dkey)
        if droute is not None:
            Wd, res = walk(route.data, droute, st, 0, None, dict(opts))
            Wd.is_dungeon_guide = True
            timeline.extend(_entry(droute, r) for r in res)
            guides.append(dkey)
            if dkey not in st.dungeon_lumps:
                dummy = {"xp_kills": 0, "act_s": 0.0, "notes": [], "params": set()}
                Wd._dungeon_lump(st, dummy)
                st.time += dummy["act_s"]
                notes.extend(dummy["notes"])
    if "leave_zone_at" in variant:
        i1 = min(i1, route.index_of(int(variant["leave_zone_at"])))
        notes.append("left %s before step %s" % (route.guide_key, variant["leave_zone_at"]))
    W, _ = _walk(kb, route, st, i0, i1, opts, timeline)
    # step indices (skip_step, grind_here) belong to this guide; quest-level options carry over
    fopts = {k: v for k, v in opts.items() if k in ("skip_quests", "hold")}
    for r in routes_after[:max(int(follow or 0), 1 if ("leave_zone_at" in variant or "skip_quest" in variant) else 0)]:
        W, _ = _walk(kb, r, st, 0, len(r.steps), fopts, timeline)
        guides.append(r.guide_key)
    _release_held(W, route, st, timeline)
    end = st.to_dict()
    return {"timeline": timeline, "start": start, "end": end, "guides": guides, "notes": notes,
            "time_s": round(st.time - (start.get("time_s") or 0.0), 1),
            "xp_gained": total_xp(st.level, st.xp) - total_xp(start["level"], start["xp"])}


def _turnins(res):
    out = {}
    for e in res["timeline"]:
        for q, xp in e.get("turnins", ()):
            out[q] = xp
    return out


def _grind_rate(kb, route, st_dict, cls):
    """XP per second of grinding at the end state (best spot near the end position, else same-level mobs)."""
    from .alternatives import best_grind_spot
    data = route.data
    lv = st_dict["level"]
    if st_dict.get("ui_map") is not None:
        sp = best_grind_spot(data, (st_dict["ui_map"], st_dict["x"], st_dict["y"]), lv, cls, route.player["faction"])
        if sp:
            return sp["xp_per_min_est"] / 60.0, sp
    from .model import kill_xp_c
    from knowledge.mechanics import combat as MC
    xp = kill_xp_c(lv, lv)
    t = MC.time_to_kill_s(cls, lv, lv) + MC.downtime_s(cls, lv, 1) + float(AP.get("analyzer.kill.search_s"))
    return xp / t, None


def compare(kb, route, state=None, start_idx=None, variant=None):
    """Baseline vs variant from the same state. Both walk the same span (this guide; plus the next guide for
    skip_quest and leave_zone_at). delta.time_s_equal_xp adds the grind time needed to make up any XP shortfall."""
    route = _route(kb, route)
    variant = variant or {}
    follow = 1 if ("leave_zone_at" in variant or "skip_quest" in variant) else 0
    base = run(kb, route, state, start_idx, None, None, follow=follow)
    var = run(kb, route, state, start_idx, None, variant, follow=follow)
    data = route.data
    tb, tv = _turnins(base), _turnins(var)
    lost = [{"id": q, "name": (data.quest(q) or {}).get("name"), "xp": xp} for q, xp in tb.items() if q not in tv]
    gained = [{"id": q, "name": (data.quest(q) or {}).get("name"), "xp": xp} for q, xp in tv.items() if q not in tb]
    changed = [{"id": q, "name": (data.quest(q) or {}).get("name"), "xp_before": tb[q], "xp_after": tv[q]}
               for q in tb if q in tv and tb[q] != tv[q]]
    xb = total_xp(base["end"]["level"], base["end"]["xp"])
    xv = total_xp(var["end"]["level"], var["end"]["xp"])
    dt = round(var["time_s"] - base["time_s"], 1)
    dxp = xv - xb
    notes = list(var["notes"])
    eq = dt
    if dxp < 0:
        rate, spot = _grind_rate(kb, route, var["end"], route.player["class"])
        if rate > 0:
            g = -dxp / rate
            eq = round(dt + g, 1)
            notes.append("making up %d XP by grinding%s takes ~%.0f min" % (
                -dxp, (" " + spot["name"]) if spot else "", g / 60.0))
    if lost:
        notes.append("lost %d quest turn-in(s) worth %d XP" % (len(lost), sum(x["xp"] for x in lost)))
    if gained:
        notes.append("gained %d quest turn-in(s) worth %d XP" % (len(gained), sum(x["xp"] for x in gained)))
    for c in changed[:5]:
        notes.append("%s gives %d XP instead of %d" % (c["name"] or c["id"], c["xp_after"], c["xp_before"]))
    notes.append("%s %.1f min, ends %.2f levels %s" % ("costs" if dt > 0 else "saves", abs(dt) / 60.0,
                                                      abs(var["end"]["exp_level"] - base["end"]["exp_level"]),
                                                      "higher" if xv >= xb else "lower"))

    def summ(r):
        return {"time_s": r["time_s"], "end_level": r["end"]["exp_level"], "xp": r["xp_gained"],
                "guides": r["guides"], "steps": sum(1 for e in r["timeline"] if e["applied"])}
    return {"baseline": summ(base), "variant": summ(var),
            "delta": {"time_s": dt, "end_level": round(var["end"]["exp_level"] - base["end"]["exp_level"], 3),
                      "xp": dxp, "time_s_equal_xp": eq, "lost_quests": lost, "gained": gained,
                      "changed": changed, "notes": notes}}


def eta(kb, route, state=None, start_idx=None, target_level=None, max_guides=8):
    """Time until target_level following the route (and the next guides). When the chain ends first, the rest
    is estimated as grinding at the best spot near the end position."""
    route = _route(kb, route)
    st = _state(route, state)
    target = min(int(target_level or (st.level + 1)), MAX_LEVEL)
    t_start = st.time
    if st.level >= target:
        return {"time_s": 0.0, "reached": True, "guide_key": route.guide_key, "idx": start_idx, "level": st.level}
    cur, i0 = route, (route.index_of(start_idx) if start_idx is not None else 0)
    guides = []
    for _ in range(max_guides):
        guides.append(cur.guide_key)
        _, res = walk(cur.data, cur, st, i0, None)
        for r in res:
            if r["level1"] >= target:
                need = CUM[target] - total_xp(r["level0"], r["xp0"])
                frac = min(1.0, max(0.0, need / r["xp_now"])) if r["xp_now"] else 1.0
                t_hit = r["t0"] + frac * r["time_s"]
                return {"time_s": round(t_hit - t_start, 1), "reached": True, "guide_key": cur.guide_key,
                        "idx": r["idx"], "rxp_step_no": r["rxp_step_no"], "level": target, "guides": guides}
        nx = next_guides(kb, cur.guide_key, cur.player, data=cur.data)
        if not nx:
            break
        cur, i0 = get_route(kb, nx[0], cur.player), 0
    rate, spot = _grind_rate(kb, cur, st.to_dict(), cur.player["class"])
    need = CUM[target] - total_xp(st.level, st.xp)
    extra = need / rate if rate > 0 else None
    return {"time_s": round(st.time - t_start + (extra or 0.0), 1), "reached": False, "guide_key": cur.guide_key,
            "idx": None, "level": st.level, "grind_remaining_s": round(extra, 1) if extra else None,
            "grind_spot": spot["name"] if spot else None, "guides": guides}
