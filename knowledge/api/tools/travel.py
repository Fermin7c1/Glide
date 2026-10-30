"""Travel tools: placing a target the model names, the travel time there, and the walking route."""
import math
import re

from knowledge.api._layer import AP, MT, run_time
from knowledge.api.navigation import walk_route
from knowledge.api.tools.lookup import t_search
from knowledge.api.values import as_int, as_num, clean, uniq


def t_walk_route(ctx, target=None):
    err = ctx.need(guide=False, step=False, player=True)
    if err:
        return err
    here = ctx.here()
    if here is None:
        return {"unavailable": "player position unknown"}
    dest, label, facts = _place(ctx, target)
    if dest is None:
        return {"error": "can't place %r: give 'step <n>', a quest, NPC or mob name, a town / flight point / inn "
                         "name, or coordinates 'x,y'" % (target,)}
    out = walk_route(ctx, here, dest)
    out = dict({"target": label, "where": ctx.pack.where(ctx, *dest)}, **out)
    out["facts"] = facts
    return out


_COORDS = re.compile(r"^\s*(?:(\d{3,5})\s*[:@ ]\s*)?(\d{1,2}(?:\.\d+)?)\s*[, ]\s*(\d{1,2}(?:\.\d+)?)\s*$")


def _place(ctx, target, depth=0):
    """((ui_map, x, y), label, facts) for a path_time target, or (None, None, [])."""
    pk, d, s = ctx.pack, ctx.pack.data, ctx.summary
    ts = str(target if target is not None else "").strip()
    here = ctx.here()
    route = ctx.route
    if not ts:
        return None, None, []
    m = re.match(r"^(?:rxp\s*)?(?:step\s*#?)?(\d{1,4})$", ts, re.I)
    if m and route is not None:
        idx = route.rxp_to_idx.get(int(m.group(1)))
        st = route.by_idx.get(idx) if idx is not None else None
        if st:
            pos = st.get("pos") or route.next_pos(idx)
            if pos:
                return pos, "step %s" % m.group(1), [ctx.step_fact(idx)]
    m = _COORDS.match(ts)
    if m:
        um = as_int(m.group(1)) or (here[0] if here else None)
        if um:
            return (um, float(m.group(2)), float(m.group(3))), "%s %s,%s" % (d.map_name(um) or um, m.group(2),
                                                                             m.group(3)), []
    mp = re.match(r"^poi:(\w+):(\d+)$", ts)
    if mp:
        r = ctx.kb.one("SELECT * FROM poi WHERE kind=? AND id=?", mp.group(1), int(mp.group(2)))
        if r and r.get("x") is not None:
            return (r["ui_map"], r["x"], r["y"]), r.get("name"), [ts]
    qids = []
    if re.match(r"^q:\d+$", ts):
        qids = [int(ts[2:])]
    elif not re.match(r"^c:\d+$", ts):
        qids = list(pk.name_index("quest").get(ts.lower()) or [])
    if qids:
        qid = min(qids, key=lambda q: pk.quest_rank(ctx, q))
        log = s.get("quests") or {}
        role = "end" if (qid in log or str(qid) in log) else "start"
        g = d.giver(qid, role) or d.giver(qid, "start" if role == "end" else "end")
        if g and g.get("x") is not None:
            lab = "%s %s (%s)" % ("turn-in of" if g.get("role") == "end" else "giver of",
                                  (d.quest(qid) or {}).get("name"), g.get("name"))
            return (g["ui_map"], g["x"], g["y"]), lab, ["q:%d" % qid]
    cid = None
    if re.match(r"^c:\d+$", ts):
        cid = int(ts[2:])
    elif ts.lower() in d.creature_by_name or (ts.lower().endswith("s") and ts.lower()[:-1] in d.creature_by_name):
        cid = d.creature_by_name_on(ts, here[0] if here else None)
    if cid is not None:
        pts = d.spawns_of(cid)
        if pts:
            same = [p for p in pts if here and p[0] == here[0]] or pts
            best = min(same, key=lambda p: (d.dist_yd(here, p) or 1e9) if here else 0)
            return best, (d.creature(cid) or {}).get("name"), ["c:%d" % cid]
    r = ctx.kb.one("""SELECT * FROM poi WHERE lower(name)=lower(?) OR lower(name) LIKE lower(?)
                      ORDER BY kind='flight' DESC, kind='inn' DESC LIMIT 1""", ts, ts + ",%")
    if r and r.get("x") is not None:
        return (r["ui_map"], r["x"], r["y"]), r.get("name"), ["poi:%s:%s" % (r["kind"], r["id"])]
    pos = d.place_named(ts)
    if pos:
        return pos, ts, []
    if depth == 0:
        for e in t_search(ctx, ts).get("results") or []:
            if e.get("fact") and e["kind"] in ("quest", "creature", "poi"):
                got = _place(ctx, e["fact"], depth + 1)
                if got[0] is not None:
                    return got
    return None, None, []


def t_path_time(ctx, target=None):
    err = ctx.need(guide=False, step=False, player=True)
    if err:
        return err
    pk, d, s, p = ctx.pack, ctx.pack.data, ctx.summary, ctx.player
    here = ctx.here()
    if here is None:
        return {"unavailable": "player position unknown"}
    dest, label, facts = _place(ctx, target)
    if dest is None:
        return {"error": "can't place %r: give 'step <n>', a quest, NPC or mob name, a town / flight point / inn "
                         "name, or coordinates 'x,y'" % (target,)}
    lv = ctx.level or 1
    out = {"target": label, "where": pk.where(ctx, *dest)}
    dist = d.dist_yd(here, dest)
    opts = []
    if dist is None:
        out["note"] = "distance unknown (no map bounds for one end)"
    elif dist == math.inf:
        out["note"] = "on another continent: boat, zeppelin or flight chain needed"
    else:
        obs = d.observed_path(here, dest)
        run_s = obs[0] if obs else run_time(dist, lv)
        out.update(dist_yd=int(round(dist)), run_s=int(round(run_s)), observed_path=bool(obs) or None)
        opts.append({"mode": "run", "total_s": int(round(run_s))})
        facts += ["p:travel.run_speed", "p:analyzer.travel.detour"]
    fac = p.get("faction")
    na, da = d.nearest_flight_node(here, fac)
    nb, db = d.nearest_flight_node(dest, fac)
    if na and nb and na["id"] != nb["id"] and da not in (None, math.inf) and db not in (None, math.inf):
        ft, how = d.flight_time(fac, na["id"], nb["id"])
        if ft:
            tot = run_time(da, lv) + float(AP.get("analyzer.fly.overhead_s")) + ft + run_time(db, lv)
            opts.append({"mode": "flight", "from": na.get("name"), "to": nb.get("name"), "flight_s": int(round(ft)),
                         "to_flight_master_yd": int(round(da)), "from_landing_yd": int(round(db)),
                         "total_s": int(round(tot)), "time_source": how, "note": "assumes you know both flight points"})
            facts += ["poi:flight:%s" % na["id"], "poi:flight:%s" % nb["id"]]
    bind = s.get("bind")
    bp = d.place_named(bind) if bind else None
    if bp:
        db2 = d.dist_yd(bp, dest)
        if db2 not in (None, math.inf):
            tot = MT.hearth_time_s() + float(AP.get("analyzer.hs.load_s")) + run_time(db2, lv)
            cd = as_num(s.get("hearth_cd"))
            opts.append(clean({"mode": "hearth", "bind": bind, "from_inn_yd": int(round(db2)),
                                "total_s": int(round(tot)), "cooldown_s": int(cd) if cd else None,
                                "ready": (cd is None or cd <= 0)}))
            facts += ["p:travel.hearth_cast_s", "p:analyzer.hs.load_s"]
    usable = [o for o in opts if o["mode"] != "hearth" or o.get("ready")]
    if usable:
        out["fastest"] = min(usable, key=lambda o: o["total_s"])["mode"]
    out["options"] = sorted(opts, key=lambda o: o["total_s"])
    out["facts"] = uniq(facts)
    return clean(out)
