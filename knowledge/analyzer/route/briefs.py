"""The analysis: one brief per route step (why it exists, what it costs and yields, what depends on it, what else
could be done there) and the guide summary built from them. The brief contract is in ../README.md."""
import math
import re

from knowledge.analyzer import params as AP
from knowledge.analyzer.model import LEVEL_XP, MAX_LEVEL, State, decay_level, qxp, walk
from knowledge.analyzer.route.steps import ALT_KINDS, OBJ_VERBS, applies, next_guides


def start_state(route, start_level=None, start_xp=0):
    lvl = start_level if start_level is not None else (route.guide.get("level_min") or 1)
    lvl = max(1, min(MAX_LEVEL, int(lvl)))
    return State(lvl, int(start_xp or 0))


def analyze(kb, route, start_level=None, start_xp=0, state=None, with_alternatives=True, ctx=None):
    """Walk the route from (start_level, start_xp) at the guide start (or from `state`, a simulate-style dict)
    and return one brief dict per route step."""
    data = route.data
    st = State.from_dict(state) if isinstance(state, dict) else (state or start_state(route, start_level, start_xp))
    W, results = walk(data, route, st)
    return build_briefs(route, results, W, with_alternatives=with_alternatives, ctx=ctx)



def _co_located_index(route):
    """Grid of route steps with quest work: (ui_map, cx, cy) -> [(idx, pos, kind, quests)]."""
    grid = {}
    for st in route.steps:
        if st["pos"] is None or not (st["quests"]["accept"] or st["quests"]["turnin"] or st["quests"]["objective"]):
            continue
        um, x, y = st["pos"]
        grid.setdefault((um, int(x // 2), int(y // 2)), []).append(st)
    return grid


def _notes_lines(step):
    out = []
    for line in (step["comments"] or "").splitlines():
        line = line.strip().lstrip("-").strip()
        if line:
            out.append(line[:120])
    for a in step["raw"]["actions"]:
        if a["verb"] != "text" or not a.get("text"):
            continue
        raw = (a["args"] or {}).get("text_raw") or ""
        t = a["text"]
        if "RXP_WARN" in raw or re.search(r"\d", t) or re.search(r"\b(careful|avoid|don't|do not|skip|note|"
                                                                 r"wait|before|until|only)\b", t, re.I):
            out.append(t[:120])
    seen, res = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            res.append(x)
    return res[:4]


def build_briefs(route, results, W, with_alternatives=True, ctx=None):
    grid = _co_located_index(route)
    co_r = float(AP.get("analyzer.colocated.radius_yd"))
    risk_r = float(AP.get("analyzer.risk.radius_yd"))
    turn_at, acc_at = {}, {}
    for r in results:
        for t in r["turnins"]:
            turn_at.setdefault(t["quest_id"], (r["idx"], t["xp"]))
        for q in r["accepts"]:
            acc_at.setdefault(q, r["idx"])
    later_turnin = _later_guide_turnins(route, ctx)
    applied_maps = [(r["idx"], (r["pos1"] or (None,))[0]) for r in results if r["applied"]]
    last_on_map = {}
    for idx, um in applied_maps:
        if um is not None:
            last_on_map[um] = idx
    briefs = []
    alt_cache = (ctx or {}).get("alt_cache") if isinstance(ctx, dict) else None
    if alt_cache is None:
        alt_cache = {}
    for step, r in zip(route.steps, results):
        b = _brief(route, step, r, W, grid, co_r, risk_r, turn_at, acc_at, later_turnin, last_on_map,
                   with_alternatives, alt_cache)
        briefs.append(b)
    return briefs


def _later_guide_turnins(route, ctx):
    """{quest_id: guide_key} for quests turned in by the next guide(s) of this player (one hop)."""
    data = route.data
    key = ("later", route.guide_key, route.player["class"], route.player["race"], route.player["faction"])
    cache = (ctx or {}).get("later_cache") if isinstance(ctx, dict) else None
    if cache is not None and key in cache:
        return cache[key]
    out = {}
    try:
        nxt = next_guides(data.kb, route.guide_key, route.player, data=data)
    except Exception:  # noqa: BLE001 - next-guide resolution is best effort
        nxt = []
    p = route.player
    for k in nxt[:3]:
        g = data.guide(k)
        if not g:
            continue
        for s in g["steps"]:
            if not applies(s["cond"], p["class"], p["race"], p["faction"]):
                continue
            for a in s["actions"]:
                if a["verb"] == "turnin" and a.get("quest_id") and applies(a["cond"], p["class"], p["race"], p["faction"]):
                    out.setdefault(a["quest_id"], (k, s["idx"]))
    if cache is not None:
        cache[key] = out
    return out


def _brief(route, step, r, W, grid, co_r, risk_r, turn_at, acc_at, later_turnin, last_on_map, with_alt, alt_cache):
    data = route.data
    p = route.player
    cls = p["class"]
    idx = step["idx"]
    level0 = r["level0"]
    facts, params_used = [], set(r["params"])
    quests = {}
    order = []
    for a in step["actions"]:
        q = a.get("quest_id")
        if q is None:
            continue
        v = a["verb"]
        role = "turnin" if v == "turnin" else "accept" if v == "accept" else \
            "objective" if v in OBJ_VERBS else "gate" if v.startswith("is") else None
        if role is None:
            continue
        if q not in quests:
            quests[q] = []
            order.append(q)
        if role not in quests[q]:
            quests[q].append(role)
    qinfo = {}
    t_xp = {t["quest_id"]: t for t in r["turnins"]}
    xp_unlocked = 0
    for q in order:
        qr = data.quest(q) or {}
        full, ql = qr.get("xp"), qr.get("level")
        at = t_xp[q]["xp"] if q in t_xp else (qxp(full, ql, level0) if full is not None else None)
        unl = data.unlocks(q)
        unl_xp = 0
        for u in unl:
            ur = data.quest(u) or {}
            if ur.get("xp"):
                unl_xp += qxp(ur["xp"], ur.get("level"), level0) or 0
        ti = turn_at.get(q)
        roles = quests[q]
        if "accept" in roles and ti:
            xp_unlocked += ti[1]
        if "turnin" in roles:
            xp_unlocked += sum((turn_at.get(u) or (None, 0))[1] for u in unl if u in acc_at and acc_at[u] > idx)
        tg = None
        if ti is None and q in later_turnin:
            tg = later_turnin[q][0]
        qinfo[str(q)] = {
            "name": qr.get("name"), "level": ql, "type": qr.get("type"), "xp_full": full, "xp_at_level": at,
            "role": "+".join(roles), "chain_next": qr.get("next_quest"), "unlocks": unl[:6], "unlocks_xp": unl_xp,
            "turnin_step": ti[0] if ti else None, "turnin_guide": tg,
            "accept_step": acc_at.get(q, route.accept_idx.get(q)),
            "level_gap": (level0 - ql) if ql is not None else None,
            "decay_at_level": decay_level(full, ql)}
        facts.append("q:%d" % q)
    kills = []
    for k in r["kills"]:
        if k.get("count", 0) <= 0:
            continue
        e = {"creature_id": k["creature_id"], "name": k["name"], "level": k["level"], "rank": k["rank"],
             "count": round(k["count"], 1), "xp_each": k["xp_each"], "ttk_s": k["ttk_s"]}
        if k.get("extra"):
            e["extra"] = k["extra"]
        e["xp"] = k["xp"]
        e["purpose"] = k.get("purpose")
        kills.append(e)
        if k["creature_id"] is not None:
            facts.append("c:%d" % k["creature_id"])
    tr = r.get("travel") or {}
    travel = {"from_idx": tr.get("from_idx"), "dist_yd": tr.get("dist_yd"), "time_s": tr.get("time_s"),
              "detour_yd": tr.get("detour_yd")}
    if tr.get("mode") and tr.get("mode") != "run":
        travel["mode"] = tr["mode"]
    if tr.get("observed"):
        travel["observed"] = True
    if r.get("flight"):
        travel["flight"] = r["flight"]
        facts += ["poi:flight:%s" % r["flight"]["from"], "poi:flight:%s" % r["flight"]["to"]]
    if r.get("graveyard"):
        facts.append("poi:graveyard:%s" % r["graveyard"])
    # co-located route actions
    co = []
    pos = step["pos"]
    if pos is not None:
        um, x, y = pos
        cx, cy = int(x // 2), int(y // 2)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for o in grid.get((um, cx + dx, cy + dy), ()):
                    if o["idx"] == idx:
                        continue
                    dd = data.dist_yd(pos, o["pos"])
                    if dd is None or dd > co_r:
                        continue
                    q = (o["quests"]["turnin"] or o["quests"]["accept"] or o["quests"]["objective"] or [None])[0]
                    co.append({"idx": o["idx"], "kind": o["kind"], "quest_id": q,
                               "name": (data.quest(q) or {}).get("name") if q else None, "dist_yd": round(dd, 1)})
        co.sort(key=lambda c: (c["dist_yd"], c["idx"]))
        co = co[:4]
    # risks
    risks = []
    if pos is not None:
        risks = _risks_near(data, pos, risk_r, p["faction"], level0)
        for rk in risks:
            if rk.get("creature_id") is not None:
                facts.append("c:%d" % rk["creature_id"])
    risk = 0.0
    for rk in risks:
        risk += 0.25 if rk.get("elite") else 0.12
        if rk.get("level") and rk["level"] >= level0 + 2:
            risk += 0.1
    for k in kills:
        if k["level"] and k["level"] - level0 >= 2:
            risk += 0.1 * (k["level"] - level0)
        if (k["rank"] or 0) in (1, 2, 3):
            risk += 0.2
    risk = round(min(1.0, risk), 2)
    # why
    why = _why_codes(route, step, r, qinfo, co, turn_at, acc_at, last_on_map)
    deps = _deps(route, step, quests, turn_at, acc_at, last_on_map)
    if deps["last_chance"] and "last_chance_before_zone_change" not in why:
        why.append("last_chance_before_zone_change")
    # alternatives
    alts = []
    if with_alt and r["applied"] and pos is not None and step["kind"] in ALT_KINDS:
        alts = _alternatives_for(route, step, r, alt_cache, W)
        for a_ in alts:
            if a_["kind"] == "quest":
                facts.append("q:%s" % a_["id"])
            elif a_["kind"] == "grind" and a_.get("id") is not None:
                facts.append("c:%s" % a_["id"])
    for o in r["objectives"]:
        if o.get("item_id"):
            facts.append("i:%s" % o["item_id"])
    for key in sorted(params_used):
        facts.append(AP.fact(key))
    time_s = round(r["time_s"], 1)
    xp_now = int(r["xp_now"])
    brief = {
        "guide_key": route.guide_key, "idx": idx, "rxp_step_no": step["rxp_step_no"], "class": cls,
        "kind": step["kind"], "exp_level_before": round(r["lvl0"], 2), "exp_level_after": round(r["lvl1"], 2),
        "xp_now": xp_now, "xp_unlocked": int(xp_unlocked), "time_s": time_s, "travel_s": round(r["travel_s"], 1),
        "kill_s": round(r["kill_s"], 1), "xp_per_min": round(xp_now / (time_s / 60.0), 1) if time_s > 0 else 0.0,
        "risk": risk,
        "justification": {"quests": qinfo, "kills": kills, "travel": travel, "co_located": co,
                          "rxp_notes": _notes_lines(step), "why": why, "risks": risks},
        "alternatives": alts,
        "deps": deps,
        "facts": list(dict.fromkeys(facts)),
    }
    brief["_t0"] = round(r["t0"], 1)
    brief["_skip"] = r["skip"]
    brief["_notes"] = r["notes"]
    brief["_objectives"] = r["objectives"]
    brief["_xp"] = {"quests": r["xp_quests"], "kills": r["xp_kills"], "grind": r["xp_grind"]}
    brief["_grind"] = r["grind"]
    return brief


def _risks_near(data, pos, radius, faction, level):
    cache = data.__dict__.setdefault("_risk_cache", {})
    key = (pos[0], round(pos[1], 1), round(pos[2], 1), faction, int(level), int(radius))
    v = cache.get(key)
    if v is None:
        v = _risks_near_raw(data, pos, radius, faction, level)
        if len(cache) > 100000:
            cache.clear()
        cache[key] = v
    return [dict(r) for r in v]


def _risks_near_raw(data, pos, radius, faction, level):
    out = {}
    um = pos[0]
    for kind, ref, x, y in data.danger_on_map(um, faction):
        dd = data.dist_yd(pos, (um, x, y))
        if dd is None or dd > radius:
            continue
        if kind == "note":
            n = data.notes[ref]
            key = ("c", n["creature_id"]) if n["creature_id"] is not None else ("n", n["name"])
            if key in out:
                continue
            out[key] = {"creature_id": n["creature_id"], "name": n["name"], "level": n["level"], "note": n["note"],
                        "dist_yd": round(dd, 1)}
            cr = data.creature(n["creature_id"])
            if cr and (cr.get("rank") or 0) in (1, 2, 3):
                out[key]["elite"] = True
        else:
            key = ("c", ref)
            if key in out:
                continue
            cr = data.creature(ref) or {}
            lvl = cr.get("max_level") or cr.get("min_level")
            if lvl is not None and lvl < level - 6:
                continue
            out[key] = {"creature_id": ref, "name": cr.get("name"), "level": lvl,
                        "note": {1: "elite", 2: "rare elite", 3: "boss"}.get(cr.get("rank"), "elite"),
                        "elite": True, "dist_yd": round(dd, 1)}
    res = sorted(out.values(), key=lambda r: (r["dist_yd"], str(r.get("name"))))
    for r in res:
        if r.get("note") and len(r["note"]) > 80:
            r["note"] = r["note"][:77] + "..."
    return res[:3]


def _why_codes(route, step, r, qinfo, co, turn_at, acc_at, last_on_map):
    data = route.data
    why = []
    q = step["quests"]
    v = step["verbs"]
    if not r["applied"]:
        why.append("skipped:%s" % r["skip"])
    if len(set(q["turnin"])) >= 2 or (q["turnin"] and sum(1 for c in co if c["kind"] in ("turnin", "accept")) >= 2):
        why.append("hub_turnin")
    obj_quests = {o["quest_id"] for o in r["objectives"]} or set(q["objective"])
    if len(obj_quests) >= 2 or (obj_quests and any(c["kind"] in ("complete", "collect") and c["quest_id"] not in obj_quests
                                                   for c in co)):
        why.append("stacks_objectives")
    enables = False
    for qq in set(q["turnin"]) | set(q["accept"]):
        for u in data.unlocks(qq):
            if u in acc_at and acc_at[u] > step["idx"]:
                enables = True
    if enables:
        why.append("enables_chain")
    if step["pos"] is not None:
        prev, nxt = route.prev_pos(step["idx"]), route.next_pos(step["idx"])
        if prev and nxt:
            a, b, c = data.dist_yd(prev, step["pos"]), data.dist_yd(step["pos"], nxt), data.dist_yd(prev, nxt)
            if None not in (a, b, c) and math.inf not in (a, b, c) and c >= 100.0 and min(a, b) >= 20.0:
                extra = a + b - c
                if extra < max(40.0, float(AP.get("analyzer.on_the_way.ratio")) * c):
                    why.append("on_the_way")
    for a in step["actions"]:
        args = a["args"] or {}
        if a["verb"] == "xp":
            if args.get("skipstep") is not None:
                why.append("level_gate:%s" % args.get("level"))
            elif not args.get("reverse"):
                why.append("xp_gate:%s" % args.get("level"))
        elif a["verb"] == "maxlevel":
            why.append("level_gate:%s" % args.get("level"))
    for qq in q["accept"]:
        qr = data.quest(qq) or {}
        ml = qr.get("min_level")
        if ml and ml > (route.guide.get("level_min") or 0) and ml >= r["lvl0"] - 0.5 and ml <= r["lvl0"] + 0.01:
            why.append("level_gate:%s" % ml)
    if r["grind"] and r["grind"].get("need_xp", 0) > 0:
        why.append("grind_filler")
    if v & {"hs", "home"}:
        why.append("hearth_cycle")
    if "fp" in v:
        why.append("flight_unlock")
    cond = (step["raw"]["cond"] or {})
    if any((data.quest(qq) or {}).get("type") == "class" or (data.quest(qq) or {}).get("classes")
           for qq in set(q["accept"]) | set(q["turnin"]) | set(q["objective"])) or \
            (cond.get("class") and (q["accept"] or q["turnin"] or q["objective"])):
        why.append("class_quest")
    if v & {"train", "trainer"}:
        why.append("training_stop")
    if v & {"vendor", "buy", "buyAll", "buyUntilBroke"} or step["kind"] == "vendor":
        why.append("vendor_supplies")
    if "deathskip" in v:
        why.append("deathskip_travel")
    if step["kind"] == "dungeon" or any(o.get("type") == "dungeon" for o in r["objectives"]):
        why.append("dungeon_run")
    if step["optional"]:
        why.append("optional")
    return list(dict.fromkeys(why))


def _deps(route, step, quests, turn_at, acc_at, last_on_map):
    data = route.data
    needs, enables = [], []
    idx = step["idx"]
    for q, roles in quests.items():
        if "turnin" in roles or "objective" in roles:
            needs.append({"quest": q, "from_idx": acc_at.get(q, route.accept_idx.get(q))})
        if "accept" in roles:
            for pre, kind in data.prereqs.get(q, ()):
                if kind in ("pre", "pregroup") and pre in turn_at:
                    needs.append({"quest": pre, "from_idx": turn_at[pre][0]})
            ti = turn_at.get(q)
            if ti and ti[0] != idx:
                enables.append({"quest": q, "idx": ti[0]})
        if "turnin" in roles:
            for u in data.unlocks(q):
                if u in acc_at and acc_at[u] >= idx:
                    enables.append({"quest": u, "idx": acc_at[u]})
    last = False
    pos = step["pos"]
    if pos is not None and last_on_map.get(pos[0]) == idx and (quests or step["kind"] in ("turnin", "accept")):
        nxt = route.next_pos(idx)
        if nxt is not None and nxt[0] != pos[0]:
            last = True
    # one entry per quest: a known source step wins over an unknown one; same-step links are not dependencies
    nd = {}
    for n in needs:
        if n["from_idx"] == idx:
            continue
        if n["quest"] not in nd or (nd[n["quest"]]["from_idx"] is None and n["from_idx"] is not None):
            nd[n["quest"]] = n
    en, seen = [], set()
    for e in enables:
        k = (e["quest"], e["idx"])
        if e["idx"] != idx and k not in seen:
            seen.add(k)
            en.append(e)
    return {"needs": list(nd.values()), "enables": en, "last_chance": last}


def _alternatives_for(route, step, r, cache, W):
    """Up to 3 alternatives at the step: quests the route has not accepted by now (those it takes later are
    marked routed_later), the best grind spot, a dungeon. The heavy parts are cached inside alternatives."""
    from knowledge.analyzer.alternatives import alternatives_near
    pos = step["pos"]
    lv = r["level0"]
    idx = step["idx"]
    later = cache.get("_later")
    if later is None or cache.get("_route") is not route:
        firsts = {}
        for st in route.steps:
            for q in st["quests"]["accept"]:
                firsts.setdefault(q, st["idx"])
        cache.clear()
        cache["_route"], cache["_later"] = route, firsts
        later = firsts
    done = {q for q, i in later.items() if i <= idx}
    routed = {q: "%s/%s" % (route.guide_key, i) for q, i in later.items() if i > idx}
    try:
        res = alternatives_near(route.data, pos[0], pos[1], pos[2], lv, route.player, exclude_quests=done,
                                routed_later=routed, limit=3)
    except Exception:  # noqa: BLE001 - alternatives are advisory
        res = []
    return [{k: a.get(k) for k in ("kind", "id", "name", "level", "xp", "xp_per_min_est", "dist_yd", "why_not")}
            for a in res]


# ----------------------------------------------------------------------------------------------
# guide summary
# ----------------------------------------------------------------------------------------------
def guide_summary(route, briefs):
    tot = sum(b["time_s"] for b in briefs)
    xq = sum(b["_xp"]["quests"] for b in briefs)
    xk = sum(b["_xp"]["kills"] for b in briefs)
    xg = sum(b["_xp"]["grind"] for b in briefs)
    hubs = {}
    for b in briefs:
        tq = [q for q, v in b["justification"]["quests"].items() if "turnin" in v["role"]]
        st = route.by_idx.get(b["idx"])
        if not tq or st is None or st["pos"] is None:
            continue
        um, x, y = st["pos"]
        k = (um, int(x // 3), int(y // 3))
        h = hubs.setdefault(k, {"idx": b["idx"], "ui_map": um, "x": round(x, 1), "y": round(y, 1), "turnins": 0,
                                "xp": 0, "zone": st["zone"]})
        h["turnins"] += len(tq)
        h["xp"] += b["_xp"]["quests"]
    hub_list = sorted(hubs.values(), key=lambda h: (-h["turnins"], h["idx"]))[:6]
    notes = {"skipped_steps": sum(1 for b in briefs if b["_skip"]),
             "skipped_by_reason": {}}
    for b in briefs:
        if b["_skip"]:
            k = b["_skip"].split(":")[0]
            notes["skipped_by_reason"][k] = notes["skipped_by_reason"].get(k, 0) + 1
    carried = [n for b in briefs for n in b["_notes"] if "carried in" in n]
    if carried:
        notes["carried_in_turnins"] = len(carried)
    disc = sum(1 for st in route.steps if st["verbs"] & {"subzone", "explore"})
    if disc:
        notes["discovery_steps_xp_not_modelled"] = disc
    grind_big = [b["idx"] for b in briefs if b["_grind"] and b["_grind"].get("need_xp", 0) > LEVEL_XP[
        max(1, min(MAX_LEVEL - 1, int(b["exp_level_before"])))]]
    if grind_big:
        notes["grind_over_one_level"] = grind_big[:10]
    return {"guide_key": route.guide_key, "class": route.player["class"],
            "start_level": briefs[0]["exp_level_before"] if briefs else None,
            "end_level": briefs[-1]["exp_level_after"] if briefs else None,
            "time_s": round(tot, 1), "xp_total": int(xq + xk + xg), "xp_quests": int(xq), "xp_kills": int(xk),
            "xp_grind": int(xg), "hubs": hub_list, "notes": notes}


def public(b):
    """The brief without the private (_*) keys."""
    return {k: v for k, v in b.items() if not k.startswith("_")}
