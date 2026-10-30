"""Simulation tools: what skipping, grinding, holding a turn-in, leaving or a dungeon would change; the time to a
level; the alternatives near the player."""
import json
import re

from knowledge.api._layer import A, CUM, LEVEL_XP, MAX_LEVEL, R, S, total_xp
from knowledge.api.reasons import WHY_NOT_TEXT
from knowledge.api.values import as_int, as_num, clean, id_or_name, uniq


def _compare(ctx, variant):
    pk = ctx.pack
    res = S.compare(ctx.kb, ctx.route, pk.sim_state(ctx), ctx.idx, variant)
    d = res["delta"]

    def named(lst):
        return [clean({"name": x.get("name"), "fact": "q:%s" % x["id"], "xp": x.get("xp")}) for x in lst]
    return clean({
        "from_step": ctx.rxp_step_no, "guides": [pk.guide_name(g) for g in res["baseline"]["guides"]],
        "baseline": {"time_s": res["baseline"]["time_s"], "end_level": res["baseline"]["end_level"]},
        "variant": {"time_s": res["variant"]["time_s"], "end_level": res["variant"]["end_level"]},
        "delta": {"time_s": d["time_s"], "end_level": d["end_level"], "xp": d["xp"],
                  "time_s_equal_xp": d["time_s_equal_xp"]},
        "lost_quests": named(d.get("lost_quests") or [])[:10], "gained_quests": named(d.get("gained") or [])[:6],
        "changed": [clean({"name": c.get("name"), "fact": "q:%s" % c["id"], "xp_before": c.get("xp_before"),
                            "xp_after": c.get("xp_after")}) for c in (d.get("changed") or [])[:5]],
        "notes": (d.get("notes") or [])[:6],
        "facts": uniq(["q:%s" % x["id"] for x in (d.get("lost_quests") or []) + (d.get("gained") or [])]
                       + [ctx.step_fact(ctx.idx)])})


def t_skip_impact(ctx, quest_id=None, name=None):
    err = ctx.need()
    if err:
        return err
    qid_in, qname_in = id_or_name(quest_id, name, "q")
    if qid_in is None and not qname_in:
        return {"error": "give quest_id or name"}
    qid = ctx.pack.find_quest(ctx, qid_in if qid_in is not None else qname_in)
    if qid is None:
        return {"error": "no quest matches %r" % (qid_in or qname_in,)}
    out = _compare(ctx, {"skip_quest": qid})
    out = dict({"quest": (ctx.pack.data.quest(qid) or {}).get("name"), "fact": "q:%d" % qid}, **out)
    out["facts"] = uniq(["q:%d" % qid] + out.get("facts", []))
    out.setdefault("lost_quests", [])
    return out


def _dungeon_guide(ctx, name):
    d = ctx.pack.data
    t = str(name or "").strip()
    if t in d.guide_rows:
        return t
    names = [t]
    if t.upper() in A.DUNGEONS:
        names.insert(0, A.DUNGEONS[t.upper()][0])
    fac = ctx.p().get("faction")
    for n in names:
        n = re.sub(r"^the\s+", "", n.strip(), flags=re.I).lower()
        if not n:
            continue
        for k, r in sorted(d.guide_rows.items()):
            if "Dungeon" in (r.get("grp") or "") and n in (r.get("name") or "").lower() \
                    and r.get("faction") in (None, fac):
                return k
    return None


def t_what_if(ctx, variant=None, minutes=None, to_level=None, quest=None, until_level=None, step=None, dungeon=None):
    err = ctx.need()
    if err:
        return err
    pk, route = ctx.pack, ctx.route
    if isinstance(variant, str) and variant.strip().startswith("{"):
        try:
            variant = json.loads(variant)
        except ValueError:
            return {"error": "variant must be one of grind_here, hold_turnin, leave_zone, do_dungeon, skip_step"}
    if isinstance(variant, dict) and len(variant) == 1:     # nested form {"grind_here": {...}}
        (k, v), = variant.items()
        variant = k
        if isinstance(v, dict):
            minutes = v.get("minutes", minutes)
            to_level = v.get("to_level", to_level)
            quest = v.get("quest", quest)
            until_level = v.get("until_level", until_level)
            step = v.get("step", v.get("at", step))
            dungeon = v.get("dungeon", dungeon)
        elif k in ("skip_step", "leave_zone", "leave_zone_at"):
            step = v
        elif k == "do_dungeon":
            dungeon = v
        elif k in ("hold_turnin", "skip_quest"):
            quest = v
        elif k == "grind_here":
            minutes = v
    v = str(variant or "").strip().lower()
    if v == "skip_quest":
        return t_skip_impact(ctx, name=quest)

    def step_idx(n):
        if n is None:
            return ctx.idx
        return route.rxp_to_idx.get(as_int(n))
    if v == "grind_here":
        i = step_idx(step)
        if i is None:
            return {"error": "unknown step number %r" % step}
        gv = {"idx": i}
        if to_level is not None and as_int(to_level):
            gv["to_level"] = as_int(to_level)
        else:
            gv["minutes"] = as_num(minutes) or 10.0
        var = {"grind_here": gv}
    elif v == "hold_turnin":
        qid = pk.find_quest(ctx, quest) if quest is not None else None
        if qid is None:
            return {"error": "hold_turnin needs the quest (name or id)"}
        var = {"hold_turnin": {"quest": qid, "until_level": as_int(until_level) or ((ctx.level or 1) + 1)}}
    elif v in ("leave_zone", "leave_zone_at"):
        i = step_idx(step)
        if i is None:
            return {"error": "unknown step number %r" % step}
        var = {"leave_zone_at": i}
    elif v == "do_dungeon":
        dk = _dungeon_guide(ctx, dungeon)
        if dk is None:
            have = sorted({r.get("name") for r in pk.data.guide_rows.values()
                           if "Dungeon" in (r.get("grp") or "") and (r.get("level_min") or 0) >= 15})
            return {"error": "no RestedXP dungeon guide matches %r" % (dungeon,), "dungeon_guides": have}
        var = {"do_dungeon": dk}
    elif v == "skip_step":
        i = step_idx(step)
        if i is None:
            return {"error": "unknown step number %r" % step}
        var = {"skip_step": [i]}
    else:
        return {"error": "variant must be one of grind_here, hold_turnin, leave_zone, do_dungeon, skip_step"}
    out = _compare(ctx, var)
    out["variant_asked"] = clean({"variant": v, "minutes": minutes, "to_level": to_level, "quest": quest,
                                   "until_level": until_level, "step": step, "dungeon": dungeon})
    return out


def t_alternatives_here(ctx, radius=None):
    err = ctx.need()
    if err:
        return err
    pk, route = ctx.pack, ctx.route
    here = ctx.here() or route.by_idx[ctx.idx].get("pos")
    lv = ctx.level
    if not here or not lv:
        return {"unavailable": "player position or level unknown"}
    st = pk.sim_state(ctx)
    done = set(st["quests_done"])
    routed = {q: "%s/%s" % (route.guide_key, i) for q, i in route.accept_idx.items() if i >= ctx.idx}
    r = max(100, min(1500, as_int(radius) or 600))
    res = A.alternatives_near(ctx.kb, here[0], here[1], here[2], lv, ctx.player,
                              exclude_quests=done | set(st["quests_active"]), radius_yd=r, done=done,
                              routed_later=routed, limit=8)
    alts = [pk.alternative(ctx, a, route) for a in res]
    return {"radius_yd": r, "level": lv, "alternatives": alts,
            "why_not_meaning": {k: t for k, t in WHY_NOT_TEXT.items()
                                if any(w.split(":")[0] == k for a in alts for w in a.get("why_not", []))},
            "facts": uniq(a.get("fact") for a in alts)}


def t_eta(ctx, target_level=None):
    err = ctx.need()
    if err:
        return err
    s = ctx.summary
    tl, lv = as_int(target_level), ctx.level
    if not tl or not lv:
        return {"error": "need a target level and the live level"}
    tl = min(tl, MAX_LEVEL)
    if tl <= lv:
        return {"error": "you are already level %d" % lv}
    r = S.eta(ctx.kb, ctx.route, ctx.pack.sim_state(ctx), ctx.idx, tl)
    out = {"target_level": tl, "time_s": r.get("time_s"), "reached_on_route": r.get("reached"),
           "at_step": r.get("rxp_step_no"), "in_guide": ctx.pack.guide_name(r.get("guide_key")) if r.get("guide_key") else None,
           "guides": [ctx.pack.guide_name(g) for g in r.get("guides") or []],
           "grind_remaining_s": r.get("grind_remaining_s"), "grind_spot": r.get("grind_spot")}
    xph = as_num(s.get("xp_per_hour"))
    need = CUM[tl] - total_xp(lv, ctx.xp)
    out["xp_needed"] = need
    if xph and need > 0:
        out["your_pace_s"] = round(need / xph * 3600.0)
        out["your_xp_per_hour"] = int(xph)
    xmax = as_int(s.get("xp_max")) or LEVEL_XP[lv]
    out["xp_to_next_level"] = max(0, xmax - ctx.xp)
    rest = as_int(s.get("rested")) or 0
    if rest:
        out["rested_pool"] = rest
    done = ctx.completed()
    if done:
        pend = R.pending_turnin_xp(ctx.kb, ctx.guide_key, ctx.idx, done, lv)
        out["pending_turnin_xp"] = pend
        out["turnins_will_level"] = pend >= out["xp_to_next_level"]
    facts = [ctx.step_fact(ctx.idx)]
    if r.get("idx") is not None and r.get("guide_key"):
        facts.append(ctx.step_fact(r["idx"], ctx.pack.guide_name(r["guide_key"])))
    out["facts"] = facts + ["q:%d" % q for q in done][:6]
    return clean(out)
