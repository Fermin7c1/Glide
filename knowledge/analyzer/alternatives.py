"""What else could be done near a point, at a level, and why the RestedXP route does not do it there.

  alternatives_near(kb, ui_map, x, y, level, player, exclude_quests=(), radius_yd=600, done=None,
                    routed_later=None, limit=8, ref_xp_per_min=None) -> [alternative]
  grind_spots(data, pos, level, cls, faction, radius_yd) -> [spot]   (best_grind_spot -> the best one)
  dungeons_for(data, level, faction, cls) -> [dungeon alternative]

alternative = {"kind": "quest"|"grind"|"dungeon", "id", "name", "level", "xp", "xp_per_min_est", "dist_yd",
               "why_not": [codes], "est_time_s", ...}
why_not codes: far_objectives, low_drop_rate, elite, group, long_chain, out_of_level, class_restricted,
low_xp_per_min, routed_later:<guide_key>/<idx>, unknown_objective_location.

Quest time estimate = walk to the giver + accept + objectives (kills x (ttk + downtime + search), loot clicks)
+ walk giver -> objective area -> ender (x detour) + turn-in. Objective areas come from quest_poi, else the
spawn of the target (or drop source) nearest to the giver. Deterministic; one call stays well under 200 ms
because spawns and givers are indexed per map and cached.
"""
import math
import re

from knowledge.mechanics import combat as MC
from knowledge.mechanics import xp as MX
from . import params as AP
from knowledge.vocab import CLASS_BITS, FACTION_RACE_MASKS, RACE_BITS, SKYBORNE_BITS, glide_race
from .kbdata import get_data, KBData, RANK_ELITE, run_time

# Classic dungeon level ranges (recommended), RXP dungeonList codes
DUNGEONS = {
    "RFC": ("Ragefire Chasm", 13, 18, "Horde"), "WC": ("Wailing Caverns", 17, 24, None),
    "DM": ("The Deadmines", 17, 26, "Alliance"), "SFK": ("Shadowfang Keep", 22, 30, None),
    "BFD": ("Blackfathom Deeps", 24, 32, None), "STOCKS": ("The Stockade", 24, 32, "Alliance"),
    "GNOMER": ("Gnomeregan", 29, 38, None), "RFK": ("Razorfen Kraul", 29, 38, None),
    "SM": ("Scarlet Monastery", 34, 45, None), "RFD": ("Razorfen Downs", 37, 46, None),
    "ULDA": ("Uldaman", 41, 51, None), "ZF": ("Zul'Farrak", 44, 54, None), "MARA": ("Maraudon", 46, 55, None),
    "ST": ("Sunken Temple", 50, 56, None), "BRD": ("Blackrock Depths", 52, 60, None),
    "LBRS": ("Lower Blackrock Spire", 55, 60, None), "DME": ("Dire Maul", 55, 60, None),
    "SCHOLO": ("Scholomance", 58, 60, None), "STRAT": ("Stratholme", 58, 60, None),
}
_NPC_ROLE_CACHE = {}
_KR = {}
_QE = {}


def _side_ok(q, faction):
    s = q.get("side")
    return s in (None, "Both", "", faction)


def _race_ok(q, race, faction):
    m = q.get("races")
    if not m:
        return True
    if race == "Skyborne":
        # Skyborne join a faction: their own bit, or the faction's whole Classic race mask (unverified)
        if m & SKYBORNE_BITS.get(faction, 0):
            return True
        return m == FACTION_RACE_MASKS["Alliance" if faction == "Alliance" else "Horde"]
    bit = RACE_BITS.get(race, 0)
    return bool(m & bit) if bit else True


def _class_state(q, cls):
    """None = no class restriction, True = restricted and allowed, False = restricted and not allowed."""
    m = q.get("classes")
    if not m:
        return None
    return bool(m & CLASS_BITS.get(cls, 0))


def _prereqs_ok(data, qid, done):
    if done is None:
        return True
    pre = data.prereqs.get(qid, ())
    req = [p for p, k in pre if k == "pre"]
    grp = [p for p, k in pre if k == "pregroup"]
    if any(p not in done for p in req):
        return False
    if grp and not any(p in done for p in grp):
        return False
    for p, k in pre:
        if k == "exclusive" and p in done:
            return False
    return True


def _npc_roles(data):
    key = id(data)
    s = _NPC_ROLE_CACHE.get(key)
    if s is None:
        s = set()
        for rows in data.givers.values():
            for lst in rows.values():
                for r in lst:
                    if r.get("kind") == "npc" and r.get("entity_id"):
                        s.add(r["entity_id"])
        for npcs in data.vendor_items.values():
            s.update(npcs)
        for r in data.kb.q("SELECT npc_id FROM trainer"):
            s.add(r["npc_id"])
        if len(_NPC_ROLE_CACHE) > 8:
            _NPC_ROLE_CACHE.clear()
        _NPC_ROLE_CACHE[key] = s
    return s


def _kill_rate(cls, level, cr, search_s=None, data=None):
    """(xp per kill, seconds per kill) for a creature at player level (mean over its level range); cached."""
    s = float(AP.get("analyzer.kill.search_s")) if search_s is None else search_s
    key = (cls, int(level), cr.get("id"), cr.get("min_level"), cr.get("max_level"), cr.get("rank"), round(s, 1))
    v = _KR.get(key)
    if v is not None:
        return v
    lo = cr.get("min_level") or cr.get("max_level") or level
    hi = cr.get("max_level") or lo
    elite = (cr.get("rank") or 0) in RANK_ELITE
    from .model import kill_xp_c
    vals = [kill_xp_c(level, ml, elite) for ml in range(int(lo), int(hi) + 1)] or [0]
    xp = sum(vals) / len(vals)
    ml = (int(lo) + int(hi)) / 2.0
    ttk = MC.time_to_kill_s(cls, level, ml, cr.get("rank") or 0)
    v = (xp, ttk + MC.downtime_s(cls, level, 1) + s)
    if len(_KR) > 200000:
        _KR.clear()
    _KR[key] = v
    return v


# ----------------------------------------------------------------------------------------------
# grind spots
# ----------------------------------------------------------------------------------------------
def grind_spots(data, pos, level, cls, faction, radius_yd=600.0, limit=3):
    """Creature clusters within radius whose level is in [gray+1, level+2]: density, kill XP, XP/min.
    Computed around the centre of pos's 3%-cell (cached, deterministic); dist_yd is from pos itself."""
    data = data if isinstance(data, KBData) else get_data(data)
    if pos is None:
        return []
    um = pos[0]
    w, h = data.map_size(um)
    if not w:
        return []
    base = _grind_base(data, um, int(pos[1] // 3), int(pos[2] // 3), int(level), cls, faction, float(radius_yd))
    out = []
    for sp in base[:max(limit, 1) + 2]:
        sp = dict(sp)
        c = sp["center"]
        sp["dist_yd"] = round(math.hypot((c[1] - pos[1]) / 100.0 * w, (c[2] - pos[2]) / 100.0 * h), 1)
        sp["why_not"] = list(sp["why_not"])
        out.append(sp)
    return out[:limit]


def _spawn_agg(data, um, cx, cy, radius_yd):
    """{creature_id: [n, sum_x, sum_y]} of spawns within radius of the 3%-cell centre (cached, class-free)."""
    cache = data.__dict__.setdefault("_spawn_agg", {})
    key = (um, cx, cy, int(radius_yd))
    v = cache.get(key)
    if v is None:
        v = {}
        for cid, x, y, _ in data.spawns_near((um, cx * 3 + 1.5, cy * 3 + 1.5), radius_yd):
            e = v.get(cid)
            if e is None:
                e = v[cid] = [0, 0.0, 0.0]
            e[0] += 1
            e[1] += x
            e[2] += y
        if len(cache) > 50000:
            cache.clear()
        cache[key] = v
    return v


def _grind_base(data, um, cx, cy, level, cls, faction, radius_yd):
    cache = data.__dict__.setdefault("_grind_cache", {})
    key = (um, cx, cy, level, cls, faction, int(radius_yd))
    v = cache.get(key)
    if v is not None:
        return v
    gray = MX.gray_level(level)
    letter = {"Alliance": "A", "Horde": "H"}.get(faction, "?")
    roles = _npc_roles(data)
    creatures = data.creatures
    out = []
    area = math.pi * radius_yd * radius_yd
    base_search = float(AP.get("analyzer.kill.search_s"))
    for cid, (n, sx, sy) in _spawn_agg(data, um, cx, cy, radius_yd).items():
        if n < 4 or cid in roles:
            continue
        cr = creatures.get(cid)
        if not cr or (cr.get("rank") or 0) != 0:
            continue
        ft = cr.get("friendly_to") or ""
        lo, hi = cr.get("min_level") or 0, cr.get("max_level") or cr.get("min_level") or 0
        if (letter in ft and ft != "AH") or hi <= gray or lo > level + 2 or hi <= 0:
            continue
        spacing = math.sqrt(area / n)
        search = max(base_search * 0.5, min(base_search * 2.5, spacing / 7.0 + 2.0))
        xp, t = _kill_rate(cls, level, cr, search_s=round(search, 1))
        if xp <= 0 or t <= 0:
            continue
        out.append({"kind": "grind", "id": cid, "name": cr["name"], "level": cr.get("max_level") or cr.get("min_level"),
                    "xp": int(round(xp)), "xp_per_min_est": round(xp / t * 60.0, 1), "spawns": n, "dist_yd": None,
                    "center": [um, round(sx / n, 2), round(sy / n, 2)], "sec_per_kill": round(t, 1),
                    "why_not": []})
    out.sort(key=lambda sp: (-sp["xp_per_min_est"], sp["id"]))
    out = out[:5]
    if len(cache) > 50000:
        cache.clear()
    cache[key] = out
    return out


def best_grind_spot(data, pos, level, cls, faction, radius_yd=450.0):
    spots = grind_spots(data, pos, level, cls, faction, radius_yd=radius_yd, limit=1)
    return spots[0] if spots else None


# ----------------------------------------------------------------------------------------------
# quests
# ----------------------------------------------------------------------------------------------
def _nearest(data, origin, pts):
    """Nearest point (same map: planar math; other maps: world distance)."""
    best, bd = None, None
    um = origin[0]
    w, h = data.map_size(um)
    for p in pts:
        if p[0] == um and w:
            d = math.hypot((p[1] - origin[1]) * w / 100.0, (p[2] - origin[2]) * h / 100.0)
        else:
            d = data.dist_yd(origin, p)
            if d is None or d == math.inf:
                continue
        if bd is None or d < bd:
            best, bd = p, d
    return best, bd


def quest_estimate(data, qid, giver_pos, level, cls, faction):
    """Time/XP estimate of doing quest qid from its giver (cached per quest, giver, level, class)."""
    key = (id(data), qid, giver_pos, int(level), cls)
    v = _QE.get(key)
    if v is None:
        v = _quest_estimate(data, qid, giver_pos, int(level), cls, faction)
        if len(_QE) > 100000:
            _QE.clear()
        _QE[key] = v
    return v


def _quest_estimate(data, qid, giver_pos, level, cls, faction):
    from .model import qxp
    q = data.quest(qid) or {}
    full, ql = q.get("xp"), q.get("level")
    xp = qxp(full, ql, level) if full is not None else None
    far, unknown, low_drop, elite = 0.0, False, False, False
    t = float(AP.get("analyzer.time.accept_s")) + float(AP.get("analyzer.time.turnin_s"))
    kill_xp = 0.0
    obj_pts = []
    pois = [(r["ui_map"], r["x"], r["y"]) for r in data.quest_poi.get(qid, ()) if (r.get("role") or "").startswith("objective")]
    for o in data.objectives.get(qid) or ():
        amount = max(1, int(o.get("amount") or 1))
        if o["type"] == "kill" and o.get("target_kind") == "npc":
            cr = data.creature(o["target_id"])
            if not cr:
                unknown = True
                continue
            if (cr.get("rank") or 0) in RANK_ELITE:
                elite = True
            p, d = _nearest(data, giver_pos, pois or data.spawns_of(cr["id"]))
            if p is None:
                unknown = True
            else:
                obj_pts.append((p, d))
            kx, kt = _kill_rate(cls, level, cr)
            kill_xp += kx * amount
            t += kt * amount
        elif o["type"] == "collect" and o.get("target_kind") == "item":
            item = o["target_id"]
            srcs = []
            if o.get("drop_from_kind") == "npc" and o.get("drop_from_id"):
                srcs.append(("npc", o["drop_from_id"], o.get("drop_rate")))
            if o.get("drop_from_kind") == "object" and o.get("drop_from_id"):
                srcs.append(("object", o["drop_from_id"], o.get("drop_rate")))
            for r in data.drops.get(item, ()):
                srcs.append(("npc", r["creature_id"], r["rate"]))
            for r in data.object_loot.get(item, ()):
                srcs.append(("object", r["object_id"], r["rate"]))
            if not srcs:
                if item in data.vendor_items:
                    t += float(AP.get("analyzer.time.buy_s"))
                    continue
                unknown = True
                t += float(AP.get("analyzer.time.unknown_obj_s"))
                continue
            best = None
            for kind, sid, rate in srcs[:6]:
                pts = data.spawns_of(sid) if kind == "npc" else data.object_spawns(sid)
                p, d = _nearest(data, giver_pos, pois or pts)
                k = (0 if p is not None else 1, d if d is not None else 1e9, -(rate or 0))
                if best is None or k < best[0]:
                    best = (k, kind, sid, rate, p, d)
            _, kind, sid, rate, p, d = best
            if p is None:
                unknown = True
            else:
                obj_pts.append((p, d))
            if rate is None:
                rate = float(AP.get("analyzer.drop.default_rate"))
            rate = max(float(rate), float(AP.get("analyzer.drop.min_rate")))
            if rate < float(AP.get("analyzer.alt.low_drop")):
                low_drop = True
            n = amount / min(1.0, rate)
            if kind == "npc":
                cr = data.creature(sid)
                if cr:
                    if (cr.get("rank") or 0) in RANK_ELITE:
                        elite = True
                    kx, kt = _kill_rate(cls, level, cr)
                    kill_xp += kx * n
                    t += kt * n
            else:
                t += n * (float(AP.get("analyzer.time.interact_s")) + float(AP.get("analyzer.time.object_search_s")))
        elif o["type"] in ("explore",):
            p, d = _nearest(data, giver_pos, pois)
            if p is not None:
                obj_pts.append((p, d))
            t += float(AP.get("analyzer.time.explore_s"))
        else:
            t += float(AP.get("analyzer.time.other_obj_s"))
    # walking: giver -> farthest objective -> ender
    far = max([d for _, d in obj_pts] or [0.0])
    ender = data.giver(qid, "end")
    end_pos = (ender["ui_map"], ender["x"], ender["y"]) if ender and ender.get("x") is not None else giver_pos
    if obj_pts:
        fp = max(obj_pts, key=lambda pd: pd[1])[0]
        d1 = data.dist_yd(giver_pos, fp) or 0.0
        d2 = data.dist_yd(fp, end_pos)
        if d2 is None or d2 == math.inf:
            d2 = d1
        walk = d1 + d2
    else:
        d2 = data.dist_yd(giver_pos, end_pos)
        walk = d2 if d2 not in (None, math.inf) else 0.0
    t += run_time(walk, level)
    return {"xp": xp, "kill_xp": int(round(kill_xp)), "time_s": round(t, 1), "far_yd": round(far, 1),
            "unknown_location": unknown, "low_drop": low_drop, "elite": elite, "end_pos": end_pos}


def _quest_candidates(data, pos, level, cls, race, faction, radius_yd):
    """Quests startable near pos for this character (no route exclusions), with cached estimates. Cached per
    2%-cell so neighbouring steps and other guides covering the same zone share the work."""
    um = pos[0]
    w, h = data.map_size(um)
    if not w:
        return []
    cx, cy = int(pos[1] // 3), int(pos[2] // 3)
    key = (um, cx, cy, int(level), cls, race, faction, int(radius_yd))
    cache = data.__dict__.setdefault("_altq", {})
    v = cache.get(key)
    if v is not None:
        return v
    gcache = data.__dict__.setdefault("_altg", {})
    gkey = (um, cx, cy, int(radius_yd), race, faction)
    givers = gcache.get(gkey)
    if givers is None:
        center = (um, cx * 3 + 1.5, cy * 3 + 1.5)
        margin = math.hypot(1.5 * w / 100.0, 1.5 * h / 100.0)
        givers, seen0 = [], set()
        for g, _ in sorted(data.givers_near(center, radius_yd + margin), key=lambda gd: (gd[1], gd[0]["quest_id"])):
            q = data.quest(g["quest_id"])
            if g["quest_id"] in seen0 or not q or q.get("removed") or q.get("hidden"):
                continue
            if not _side_ok(q, faction) or not _race_ok(q, race, faction):
                continue
            seen0.add(g["quest_id"])
            givers.append(g)
        if len(gcache) > 50000:
            gcache.clear()
        gcache[gkey] = givers
    over = int(AP.get("analyzer.alt.max_level_over"))
    full_until = int(AP.get("xp.quest.full_until"))
    far_yd = float(AP.get("analyzer.alt.far_objective_yd"))
    long_chain = int(AP.get("analyzer.alt.long_chain"))
    out, seen = [], set()
    for g in givers:
        qid = g["quest_id"]
        if qid in seen:
            continue
        q = data.quest(qid)
        cstate = _class_state(q, cls)
        if cstate is False:
            continue
        ml, ql = q.get("min_level") or 0, q.get("level")
        if ml > level or (ql is not None and ql > level + over):
            continue
        if q.get("type") in ("raid", "pvp"):
            continue
        seen.add(qid)
        gpos = (g["ui_map"], g["x"], g["y"])
        est = quest_estimate(data, qid, gpos, level, cls, faction)
        why = []
        if est["far_yd"] > far_yd:
            why.append("far_objectives")
        if est["low_drop"]:
            why.append("low_drop_rate")
        if q.get("type") == "elite" or est["elite"]:
            why.append("elite")
        if q.get("type") in ("group", "dungeon") or (q.get("suggested_group") or 0) > 1:
            why.append("group")
        if data.chain_length(qid) > long_chain:
            why.append("long_chain")
        if ql is not None and (ql > level + 2 or level - ql > full_until):
            why.append("out_of_level")
        if cstate is True or q.get("type") == "class":
            why.append("class_restricted")
        if est["unknown_location"]:
            why.append("unknown_objective_location")
        out.append({"qid": qid, "giver": gpos, "est": est, "why": why, "name": q.get("name"), "level": ql,
                    "type": q.get("type")})
    if len(cache) > 100000:
        cache.clear()
    cache[key] = out
    return out


def alternatives_near(kb, ui_map, x, y, level, player, exclude_quests=(), radius_yd=None, done=None,
                      routed_later=None, limit=8, ref_xp_per_min=None, step_xp_per_min=None, with_grind=True,
                      with_dungeons=True):
    """Quests (givers within radius the player can take), grind spots and dungeons near (ui_map, x, y).
    exclude_quests: quests already done/active/routed earlier; done: turned-in set (prerequisites are checked
    only when given); routed_later: {quest_id: "<guide_key>/<idx>"} where the route does it later."""
    data = kb if isinstance(kb, KBData) else get_data(kb)
    p = player or {}
    cls = (p.get("class") or "WARRIOR").upper()
    faction = (p.get("faction") or "Alliance").capitalize()
    race = glide_race(p.get("race") or ("Human" if faction == "Alliance" else "Orc"))
    level = int(level)
    radius_yd = float(radius_yd if radius_yd is not None else AP.get("analyzer.alt.radius_yd"))
    pos = (ui_map, float(x), float(y))
    excl = set(exclude_quests or ())
    routed_later = routed_later or {}
    if ref_xp_per_min is None:
        ref_xp_per_min = step_xp_per_min
    if ref_xp_per_min is None:
        try:
            from knowledge.heuristics.engine import benchmark_xph
            b = benchmark_xph(level)
            ref_xp_per_min = b / 60.0 if b else None
        except Exception:  # noqa: BLE001
            ref_xp_per_min = None
    w, h = data.map_size(ui_map)
    quests = []
    for c in _quest_candidates(data, pos, level, cls, race, faction, radius_yd):
        qid = c["qid"]
        if qid in excl or (done is not None and (qid in done or not _prereqs_ok(data, qid, done))):
            continue
        g = c["giver"]
        d = math.hypot((g[1] - pos[1]) * w / 100.0, (g[2] - pos[2]) * h / 100.0) if w else None
        if d is None or d > radius_yd:
            continue
        est = c["est"]
        t = est["time_s"] + run_time(d, level)
        xpm = round(((est["xp"] or 0) + est["kill_xp"]) / (t / 60.0), 1) if t > 0 else 0.0
        why = list(c["why"])
        if ref_xp_per_min and xpm < 0.8 * ref_xp_per_min:
            why.append("low_xp_per_min")
        if qid in routed_later:
            why.append("routed_later:%s" % routed_later[qid])
        quests.append({"kind": "quest", "id": qid, "name": c["name"], "level": c["level"], "xp": est["xp"],
                       "kill_xp": est["kill_xp"], "xp_per_min_est": xpm, "dist_yd": round(d, 1),
                       "est_time_s": round(t, 1), "why_not": why, "type": c["type"]})
    quests.sort(key=lambda a: (len([w_ for w_ in a["why_not"] if not w_.startswith("routed_later")]) > 2,
                               -a["xp_per_min_est"], a["id"]))
    extra = []
    if with_grind:
        for sp in grind_spots(data, pos, level, cls, faction, radius_yd=radius_yd, limit=2):
            sp = dict(sp)
            if ref_xp_per_min and sp["xp_per_min_est"] < ref_xp_per_min:
                sp["why_not"] = ["low_xp_per_min"]
            extra.append(sp)
    if with_dungeons:
        extra.extend(dungeons_for(data, level, faction, cls, pos=pos)[:1])
    if not limit:
        return quests + extra
    keep_q = max(limit - min(len(extra), 2 if limit >= 4 else 1), 0)
    res = quests[:keep_q]
    for e in extra:
        if len(res) >= limit:
            break
        res.append(e)
    for q in quests[keep_q:]:
        if len(res) >= limit:
            break
        res.append(q)
    return res


# ----------------------------------------------------------------------------------------------
# dungeons
# ----------------------------------------------------------------------------------------------
def _dungeon_guides(data):
    """{code or name: [guide_key]} for Dungeon Guide group guides that are dungeon runs (name not a zone)."""
    cache = data.__dict__.get("_dguides")
    if cache is not None:
        return cache
    from knowledge.rxp_parser import ZONE_UIMAP
    zones = {z.lower() for z in ZONE_UIMAP}
    out = {}
    for k, r in data.guide_rows.items():
        if "Dungeon" not in (r.get("grp") or ""):
            continue
        nm = re.sub(r"^\d+-\d+\s+", "", r.get("name") or "")
        parts = [x.strip().lower() for x in re.split(r"[/()]", nm) if x.strip()]
        if any(pp in zones or pp.split(" ")[0] in ("dun", "elwynn", "loch", "the") and pp in zones for pp in parts):
            continue
        if any(pp in zones for pp in parts):
            continue
        if nm.lower() in ("random stuff",) or "class quests" in nm.lower():
            continue
        out.setdefault(nm, []).append(k)
    data.__dict__["_dguides"] = out
    return out


def dungeons_for(data, level, faction, cls, pos=None):
    data = data if isinstance(data, KBData) else get_data(data)
    stats = ((data.meta_rxp.get("dungeonStats") or {}).get(faction) or {})
    out = []
    run_s = float(AP.get("analyzer.dungeon.run_s"))
    kxp = float(AP.get("analyzer.dungeon.kill_xp_per_level")) * level
    for code, (name, lo, hi, side) in DUNGEONS.items():
        if side not in (None, faction) or not (lo - 1 <= level <= hi):
            continue
        st = stats.get(code) or {}
        why = ["group"]
        if st.get("travel") is not None and st["travel"] <= 1:
            why.append("far_objectives")
        if st.get("quest") is not None and st["quest"] <= 1:
            why.append("low_xp_per_min")
        if st.get(cls) is not None and st[cls] <= 1:
            why.append("class_restricted")
        out.append({"kind": "dungeon", "id": code, "name": name, "level": lo, "xp": int(kxp),
                    "xp_per_min_est": round(kxp / (run_s / 60.0), 1), "dist_yd": None, "why_not": why,
                    "rating": st or None})
    for nm, keys in _dungeon_guides(data).items():
        r = data.guide_rows[keys[0]]
        lo, hi = r.get("level_min"), r.get("level_max")
        if lo is None or not (lo - 1 <= level <= (hi or lo) + 1):
            continue
        if r.get("faction") not in (None, faction):
            continue
        out.append({"kind": "dungeon", "id": keys[0], "name": nm, "level": lo, "xp": int(kxp),
                    "xp_per_min_est": round(kxp / (run_s / 60.0), 1), "dist_yd": None, "why_not": ["group"],
                    "guide": keys[0]})
    out.sort(key=lambda d: (len(d["why_not"]), -d["xp_per_min_est"], str(d["id"])))
    return out
