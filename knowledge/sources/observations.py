"""Loader 4: recorder observations (glide.db sessions + obs_* tables) -> kb obs_* tables, plus what they imply.

Raw:      obs_session, obs_kill (ttk from the combat start/previous kill; creature_id resolved from a unique
          name when the recorder had no target, flagged id_inferred), obs_xp, obs_trail, obs_death
          (death -> release (PLAYER_ALIVE) -> alive again (PLAYER_UNGHOST); corpse_run_s = alive_t - t
          minus time the character was logged off),
          obs_gossip (per-session union of what an NPC offered; no time: the session start is used),
          obs_loot, quest_xp_obs.
Derived:  creature_drop / object_loot rates (drops / max(kills, loot events) of that source in the
          sessions where it was looted), obs_respawn (time between kills at the same spot), quest_giver
          rows from gossip (avail -> start, active -> end), quest_prereq inference (src 'inf', conf 0.5).

Prerequisite rule (sound, deliberately conservative): for one character, NPC N did not offer Q in session
Si (Q neither offered nor active, not yet offered/accepted/turned in) and did offer Q in a later session
Sj. Something unlocked Q in between: after the last moment N's quest options were visible in Si (a quest
turned in to N or accepted from N; else the start of Si) and before Q was offered/accepted in Sj (else the
end of Sj). If the player's level cannot explain it (Q's min level known and met at the window start, or
no level change across the window; levels from the XP timeline) and exactly one quest P was turned in
inside that window, then P is a prerequisite of Q. Anything else is reported as ambiguous, never written. Community prerequisites contradicted by a character whose whole history was recorded (first
session at level 1) are reported in the loader result (possible Forever changes), not deleted.
"""
import collections
import statistics

from knowledge import store
from knowledge.values import as_int

OBS = store.SOURCE_CONF["obs"]


def load(conn, ctx):
    if not ctx.has_glide():
        return {"skipped": "no glide.db at %s" % ctx.glide_db}
    g = ctx.glide()
    have = {r[0] for r in g.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "sessions" not in have:
        g.close()
        return {"skipped": "glide.db has no sessions table"}
    res = {}
    sess = load_sessions(conn, g)
    res["obs_session"] = len(sess)
    res["obs_kill"] = load_kills(conn, g)
    res["obs_xp"] = copy(conn, g, "obs_xp", "SELECT session, t, lvl, xp, max, rest FROM obs_xp",
                         ["session", "t", "level", "xp", "xp_max", "rested"])
    res["obs_trail"] = copy(conn, g, "obs_trail", "SELECT session, t, ui_map, x, y, combat FROM obs_trail ORDER BY session, t",
                            ["session", "t", "ui_map", "x", "y", "combat"])
    res["obs_death"] = load_deaths(conn, g, sess)
    res["obs_gossip"] = load_gossip(conn, g, sess)
    res["obs_loot"] = load_loot(conn, g)
    res["quest_xp_obs"] = copy(conn, g, "quest_xp_obs",
                               "SELECT quest_id, turned_lvl, xp, money, session, turned_t FROM obs_quests WHERE xp IS NOT NULL",
                               ["quest_id", "player_level", "xp", "money", "session", "t"])
    res["drops"] = derive_drops(conn)
    res["obs_respawn"] = derive_respawn(conn)
    res["gossip_givers"] = gossip_givers(conn)
    res["prereq_inference"] = infer_prereqs(conn, g, sess)
    g.close()
    return res


def copy(conn, g, table, sql, cols):
    rows = [tuple(r) for r in g.execute(sql)]
    return store.insert_rows(conn, table, rows, cols)


# ------------------------------------------------------------------------------------------------
def load_sessions(conn, g):
    lv = collections.defaultdict(list)
    for sql in ("SELECT session, lvl FROM obs_xp", "SELECT session, lvl FROM obs_events",
                "SELECT session, lvl FROM obs_kills", "SELECT session, turned_lvl FROM obs_quests",
                "SELECT session, accepted_lvl FROM obs_quests"):
        for s, l in g.execute(sql):
            if l is not None:
                lv[s].append(l)
    for s, to in g.execute("SELECT session, data FROM obs_events WHERE event='level'"):
        d = store.jload(to, {}) or {}
        if as_int(d.get("to")):
            lv[s].append(as_int(d["to"]))
    guide = collections.defaultdict(collections.Counter)
    for s, gd in g.execute("SELECT session, rxp_guide FROM obs_quests WHERE rxp_guide IS NOT NULL"):
        guide[s][gd] += 1
    rows, out = [], {}
    for r in g.execute("SELECT * FROM sessions ORDER BY started"):
        levels = lv.get(r["id"], []) + ([r["level_start"]] if r["level_start"] else [])
        row = {"id": r["id"], "char": r["char"], "class": r["class"], "race": r["race"], "faction": r["faction"],
               "level_start": r["level_start"], "level_end": max(levels) if levels else r["level_start"],
               "started": r["started"], "ended": r["ended"],
               "play_s": (r["ended"] - r["started"]) if r["ended"] and r["started"] else None,
               "guide": guide[r["id"]].most_common(1)[0][0] if guide[r["id"]] else None}
        rows.append(row)
        out[r["id"]] = dict(row, realm=r["realm"], who=(r["char"], r["realm"]))
    store.insert_rows(conn, "obs_session", rows)
    return out


def load_kills(conn, g):
    combat = collections.defaultdict(list)          # session -> [(t, 'combat'|'ooc')]
    for s, t, e in g.execute("SELECT session, t, event FROM obs_events WHERE event IN ('combat','ooc') ORDER BY session, t"):
        combat[s].append((t, e))
    known = collections.defaultdict(set)            # name -> ids seen with that name in kills
    for n, i in g.execute("SELECT DISTINCT name, npc_id FROM obs_kills WHERE npc_id IS NOT NULL"):
        known[n].add(i)
    by_name = collections.defaultdict(list)
    for cid, name in conn.execute("SELECT id, name FROM creature WHERE name IS NOT NULL"):
        by_name[name].append(cid)
    maps = collections.defaultdict(set)
    for cid, um in conn.execute("SELECT DISTINCT creature_id, ui_map FROM creature_spawn"):
        maps[cid].add(um)
    rows, inferred, last_kill = [], 0, {}
    for r in g.execute("SELECT * FROM obs_kills ORDER BY session, t"):
        cid, flag = r["npc_id"], 0
        if cid is None and r["name"]:
            ids = known.get(r["name"]) or set(by_name.get(r["name"], []))
            if len(ids) > 1 and r["ui_map"]:
                ids = {i for i in ids if r["ui_map"] in maps.get(i, ())}
            if len(ids) == 1:
                cid, flag = next(iter(ids)), 1
                inferred += 1
        ttk = None
        start = None
        for t, e in combat.get(r["session"], []):
            if t > r["t"]:
                break
            start = t if e == "combat" else (None if t < r["t"] else start)
        if start is not None:
            prev = last_kill.get(r["session"])
            if prev is not None and prev >= start:
                start = prev
            ttk = float(r["t"] - start)
        last_kill[r["session"]] = r["t"]
        rows.append({"session": r["session"], "t": r["t"], "creature_id": cid, "name": r["name"],
                     "mob_level": r["mob_level"], "player_level": r["lvl"], "xp": r["xp"], "rested": r["rest"],
                     "ui_map": r["ui_map"], "x": r["x"], "y": r["y"], "ttk_s": ttk, "id_inferred": flag})
    store.insert_rows(conn, "obs_kill", rows)
    return {"rows": len(rows), "creature_id_from_name": inferred,
            "with_ttk": sum(1 for r in rows if r["ttk_s"] is not None),
            "unresolved": sum(1 for r in rows if r["creature_id"] is None)}


def load_deaths(conn, g, sess):
    evs = collections.defaultdict(list)
    for r in g.execute("SELECT * FROM obs_events WHERE event IN ('death','alive','unghost') ORDER BY t"):
        s = sess.get(r["session"])
        if not s:
            continue
        login = r["event"] == "alive" and s["started"] is not None and abs(r["t"] - s["started"]) <= 5
        evs[s["who"]].append({"t": r["t"], "e": r["event"], "login": login, "session": r["session"],
                              "m": r["ui_map"], "x": r["x"], "y": r["y"], "lvl": r["lvl"]})
    sess_of = collections.defaultdict(list)
    for s in sess.values():
        sess_of[s["who"]].append((s["started"], s["ended"]))
    rows = []
    for who, lst in evs.items():
        for i, e in enumerate(lst):
            if e["e"] != "death":
                continue
            release = alive = None
            for f in lst[i + 1:]:
                if f["e"] == "death":
                    break
                if f["e"] == "alive" and not f["login"] and release is None:
                    release = f["t"]
                if f["e"] == "unghost":
                    alive = f["t"]
                    break
            run = None
            if alive:                                       # online time only: drop logouts while dead
                run = float(alive - e["t"]) - offline_between(sess_of.get(who, []), e["t"], alive)
            rows.append({"session": e["session"], "t": e["t"], "ui_map": e["m"], "x": e["x"], "y": e["y"],
                         "level": e["lvl"], "killer": None, "release_t": release, "alive_t": alive,
                         "corpse_run_s": run})
    store.insert_rows(conn, "obs_death", rows)
    return {"rows": len(rows), "with_corpse_run": sum(1 for r in rows if r["corpse_run_s"] is not None)}


def offline_between(spans, t0, t1):
    """Seconds between t0 and t1 not covered by any recorded session of the character."""
    spans = sorted((a, b) for a, b in spans if a and b and b > t0 and a < t1)
    covered, cur = 0.0, t0
    for a, b in spans:
        a, b = max(a, cur), min(b, t1)
        if b > a:
            covered += b - a
            cur = b
    return max(0.0, (t1 - t0) - covered) if spans else 0.0


def load_gossip(conn, g, sess):
    rows = []
    for r in g.execute("SELECT session, key, json FROM obs_misc WHERE kind='gossip'"):
        d = store.jload(r["json"], {}) or {}
        npc = as_int(r["key"])
        t = (sess.get(r["session"]) or {}).get("started")
        for state in ("avail", "active"):
            v = d.get(state) or {}
            for q in (v.keys() if isinstance(v, dict) else v):
                if as_int(q):
                    rows.append((r["session"], t, npc, store.clean_name(d.get("n")), as_int(q), state))
    return store.insert_rows(conn, "obs_gossip", rows, ["session", "t", "npc_id", "npc_name", "quest_id", "state"])


def load_loot(conn, g):
    rows = []
    for r in g.execute("SELECT session, json FROM obs_misc WHERE kind='loot'"):
        d = store.jload(r["json"], {}) or {}
        for it in d.get("items") or []:
            if isinstance(it, dict) and as_int(it.get("id")):
                rows.append((r["session"], as_int(d.get("t")), it.get("sk"), as_int(it.get("src")), as_int(it.get("id")),
                             as_int(it.get("c")), as_int(d.get("m")), d.get("x"), d.get("y")))
    return store.insert_rows(conn, "obs_loot", rows,
                             ["session", "t", "src_kind", "src_id", "item_id", "qty", "ui_map", "x", "y"])


# ------------------------------------------------------------------------------------------------
# derived
# ------------------------------------------------------------------------------------------------
def derive_drops(conn):
    events = collections.defaultdict(set)          # (session, kind, src) -> loot event times
    hits = collections.defaultdict(set)            # (session, kind, src, item) -> event times with the item
    for s, t, k, src, item in conn.execute("SELECT session, t, src_kind, src_id, item_id FROM obs_loot WHERE src_id IS NOT NULL"):
        events[(s, k, src)].add(t)
        hits[(s, k, src, item)].add(t)
    kills = collections.Counter()
    for s, cid in conn.execute("SELECT session, creature_id FROM obs_kill WHERE creature_id IS NOT NULL"):
        kills[(s, cid)] += 1
    agg = collections.defaultdict(lambda: [0, 0, 0])   # (kind, src, item) -> [drops, events, kills]
    for (_s, k, src, item), ts in hits.items():
        a = agg[(k, src, item)]
        a[0] += len(ts)
    looted_sessions = collections.defaultdict(set)
    for (s, k, src) in events:
        looted_sessions[(k, src)].add(s)
    n_c = n_o = 0
    for (k, src, item), a in agg.items():
        sessions = looted_sessions[(k, src)]
        ev = sum(len(events[(s, k, src)]) for s in sessions)
        kl = sum(kills[(s, src)] for s in sessions) if k == "npc" else 0
        denom = max(ev, kl)
        rate = round(min(1.0, a[0] / denom), 3) if denom else None
        conf = round(OBS * denom / (denom + 5.0), 3) if denom else 0.3
        if k == "npc":
            store.upsert(conn, "creature_drop", {"creature_id": src, "item_id": item, "rate": rate, "n_obs": denom,
                                                 "src": "obs", "conf": conf}, ["creature_id", "item_id"])
            n_c += 1
        elif k == "object":
            store.upsert(conn, "object_loot", {"object_id": src, "item_id": item, "rate": rate, "n_obs": denom,
                                               "src": "obs", "conf": conf}, ["object_id", "item_id"])
            n_o += 1
    # quest objectives: fill the drop source / rate from what we actually looted
    filled = 0
    for qid, idx, item, dk, di in conn.execute("""SELECT quest_id, idx, target_id, drop_from_kind, drop_from_id
                                                  FROM quest_objective WHERE type='collect' AND target_id IS NOT NULL""").fetchall():
        if dk is None:
            srcs = conn.execute("""SELECT 'npc', creature_id, rate FROM creature_drop WHERE item_id=? AND src LIKE '%obs%'
                                   UNION ALL SELECT 'object', object_id, rate FROM object_loot WHERE item_id=? AND src LIKE '%obs%'""",
                                (item, item)).fetchall()
            if len(srcs) == 1:
                conn.execute("UPDATE quest_objective SET drop_from_kind=?, drop_from_id=?, drop_rate=? WHERE quest_id=? AND idx=?",
                             (srcs[0][0], srcs[0][1], srcs[0][2], qid, idx))
                filled += 1
        else:
            tbl, col = ("creature_drop", "creature_id") if dk == "npc" else ("object_loot", "object_id")
            r = conn.execute("SELECT rate FROM %s WHERE %s=? AND item_id=? AND rate IS NOT NULL" % (tbl, col), (di, item)).fetchone()
            if r:
                conn.execute("UPDATE quest_objective SET drop_rate=? WHERE quest_id=? AND idx=?", (r[0], qid, idx))
                filled += 1
    return {"creature_drop": n_c, "object_loot": n_o, "objectives_with_observed_drop": filled}


def derive_respawn(conn, radius=0.5, lo=45, hi=1800):
    by = collections.defaultdict(list)
    for s, t, cid, um, x, y in conn.execute("""SELECT session, t, creature_id, ui_map, x, y FROM obs_kill
                                               WHERE creature_id IS NOT NULL AND x IS NOT NULL ORDER BY t"""):
        by[(s, cid, um)].append((t, x, y))
    spots = collections.defaultdict(list)          # (cid, um) -> [[cx, cy, [intervals]]]
    for (_s, cid, um), kills in by.items():
        for i, (t, x, y) in enumerate(kills):
            prev = None
            for t0, x0, y0 in reversed(kills[:i]):
                if abs(x - x0) <= radius and abs(y - y0) <= radius:
                    prev = t0
                    break
            if prev is None:
                continue
            dt = t - prev
            if not lo <= dt <= hi:
                continue
            for sp in spots[(cid, um)]:
                if abs(sp[0] - x) <= radius and abs(sp[1] - y) <= radius:
                    sp[2].append(dt)
                    break
            else:
                spots[(cid, um)].append([x, y, [dt]])
    rows = []
    for (cid, um), lst in spots.items():
        for cx, cy, dts in lst:
            rows.append((cid, um, round(cx, 2), round(cy, 2), float(statistics.median(dts)), len(dts)))
    store.insert_rows(conn, "obs_respawn", rows, ["creature_id", "ui_map", "x", "y", "respawn_s", "n"])
    return {"rows": len(rows), "intervals": sum(r[5] for r in rows),
            "median_s": statistics.median([r[4] for r in rows]) if rows else None}


def gossip_givers(conn):
    pos = {}
    for cid, um, x, y in conn.execute("""SELECT creature_id, ui_map, x, y FROM creature_spawn
                                         WHERE src='obs' AND conf >= 0.9"""):
        pos.setdefault(cid, []).append((um, x, y))
    added = merged = 0
    for npc, name, qid, state in conn.execute("SELECT DISTINCT npc_id, npc_name, quest_id, state FROM obs_gossip").fetchall():
        role = "start" if state == "avail" else "end"
        conf = OBS if role == "start" else 0.85      # 'active' = in the log and this NPC involved: usually the ender
        cur = conn.execute("SELECT rowid, src, pos_src FROM quest_giver WHERE quest_id=? AND role=? AND kind='npc' AND entity_id=?",
                           (qid, role, npc)).fetchone()
        p = pos.get(npc)
        one = p[0] if p and len(p) == 1 else None
        if cur:
            conn.execute("UPDATE quest_giver SET src=?, conf=MAX(COALESCE(conf, 0), ?) WHERE rowid=?",
                         (store.merge_src(cur[1], "obs"), conf, cur[0]))
            if one and (cur[2] or "") != "obs":
                conn.execute("UPDATE quest_giver SET ui_map=?, x=?, y=?, pos_src='obs' WHERE rowid=?", (one[0], one[1], one[2], cur[0]))
            merged += 1
        else:
            conn.execute("""INSERT INTO quest_giver(quest_id, role, kind, entity_id, name, ui_map, x, y, src, conf, pos_src)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                         (qid, role, "npc", npc, name, one[0] if one else None, one[1] if one else None,
                          one[2] if one else None, "obs", conf, "obs" if one else None))
            added += 1
    return {"added": added, "merged": merged}


def infer_prereqs(conn, g, sess):
    first = collections.defaultdict(lambda: collections.defaultdict(dict))   # who -> kind -> quest -> earliest t
    for r in g.execute("SELECT session, quest_id, offered_t, accepted_t, turned_t FROM obs_quests"):
        s = sess.get(r["session"])
        if not s:
            continue
        for kind in ("offered_t", "accepted_t", "turned_t"):
            t = r[kind]
            if t:
                cur = first[s["who"]][kind].get(r["quest_id"])
                first[s["who"]][kind][r["quest_id"]] = t if cur is None else min(cur, t)
    levels = collections.defaultdict(list)                                   # who -> [(t, level)]
    for sid, t, lvl in g.execute("SELECT session, t, lvl FROM obs_xp WHERE lvl IS NOT NULL"):
        if sid in sess:
            levels[sess[sid]["who"]].append((t, lvl))
    for v in levels.values():
        v.sort()

    def level_at(who, t, fallback):
        best = fallback
        for tt, lv in levels.get(who, []):
            if tt > t:
                break
            best = lv
        return best

    gossip = collections.defaultdict(lambda: collections.defaultdict(lambda: {"avail": set(), "active": set()}))
    for s, npc, q, state in conn.execute("SELECT session, npc_id, quest_id, state FROM obs_gossip"):
        gossip[s][npc][state].add(q)
    ender, giver = collections.defaultdict(set), collections.defaultdict(set)   # npc -> quests it ends / starts
    for q, role, npc in conn.execute("SELECT quest_id, role, entity_id FROM quest_giver WHERE kind='npc' AND entity_id IS NOT NULL"):
        (ender if role == "end" else giver)[npc].add(q)
    minlvl = dict(conn.execute("SELECT id, min_level FROM quest WHERE min_level IS NOT NULL"))
    known = collections.defaultdict(lambda: collections.defaultdict(set))
    for q, p, k in conn.execute("SELECT quest_id, pre_id, kind FROM quest_prereq WHERE kind IN ('pre','pregroup')"):
        known[q][k].add(p)
    by_who = collections.defaultdict(list)
    for _sid, s in sess.items():
        by_who[s["who"]].append(s)
    inferred, ambiguous, consistent, level_gated, contradictions = {}, [], 0, 0, []
    for who, slist in by_who.items():
        slist.sort(key=lambda s: s["started"] or 0)
        f = first[who]
        done = f["turned_t"]
        for j, sj in enumerate(slist):
            for npc, gj in gossip.get(sj["id"], {}).items():
                for q in gj["avail"]:
                    neg = None
                    for si in reversed(slist[:j]):
                        gi = gossip.get(si["id"], {}).get(npc)
                        if gi is None:
                            continue
                        end_i = si["ended"] or si["started"] or 0
                        if q in gi["avail"] or q in gi["active"] or min(
                                f["offered_t"].get(q, 1e18), f["accepted_t"].get(q, 1e18), done.get(q, 1e18)) <= end_i:
                            break                       # offered / in the log / done already: no unlock to explain
                        neg = si
                        break
                    if neg is None:
                        continue
                    # window: from the last moment N's quest options were visible in the negative session
                    # (a quest turned in to N or accepted from N) to the moment Q was offered or accepted
                    lo_i, hi_i = neg["started"] or 0, neg["ended"] or neg["started"] or 0
                    visits = [t for qq, t in done.items() if qq in ender.get(npc, ()) and lo_i <= t <= hi_i] + \
                             [t for qq, t in f["accepted_t"].items() if qq in giver.get(npc, ()) and lo_i <= t <= hi_i]
                    t_lo = max(visits) if visits else lo_i
                    t_hi = min([t for t in (f["offered_t"].get(q), f["accepted_t"].get(q))
                                if t and (sj["started"] or 0) <= t <= (sj["ended"] or 1e18)] or [sj["ended"] or 1e18])
                    lv_lo = level_at(who, t_lo, neg["level_end"])
                    lv_hi = level_at(who, t_hi, sj["level_start"])
                    ml = minlvl.get(q)
                    if (ml is not None and ml > (lv_lo or 0)) or (ml is None and lv_lo != lv_hi):
                        level_gated += 1
                        continue
                    cands = sorted(p for p, t in done.items() if p != q and t_lo <= t <= t_hi)
                    case = {"quest": q, "npc": npc, "char": who[0], "not_offered": neg["id"], "offered": sj["id"],
                            "window_s": t_hi - t_lo, "candidates": cands[:12], "n_candidates": len(cands)}
                    if len(cands) == 1:
                        inferred[(q, cands[0])] = dict(case, pre=cands[0])
                    else:
                        kp = known[q]["pre"] | known[q]["pregroup"]
                        case["known_prereq_in_window"] = sorted(kp & set(cands))
                        consistent += 1 if case["known_prereq_in_window"] else 0
                        ambiguous.append(case)
        # contradictions: only for characters whose whole quest history was recorded
        if slist and (slist[0]["level_start"] or 99) <= 1:
            for s in slist:
                for npc, gs in gossip.get(s["id"], {}).items():
                    for q in gs["avail"]:
                        pre, grp = known[q]["pre"], known[q]["pregroup"]
                        done_by = {p for p, t in done.items() if t <= (s["ended"] or 1e18)}
                        if (pre and not pre & done_by) or (grp and not grp <= done_by):
                            contradictions.append({"quest": q, "npc": npc, "session": s["id"], "char": who[0],
                                                   "pre": sorted(pre), "pregroup": sorted(grp)})
    for (q, p), info in inferred.items():
        store.upsert(conn, "quest_prereq", {"quest_id": q, "pre_id": p, "kind": "pre", "src": "inf", "conf": 0.5},
                     ["quest_id", "pre_id", "kind"])
        info["already_known"] = p in (known[q]["pre"] | known[q]["pregroup"])
    return {"inferred": len(inferred), "inferred_list": list(inferred.values())[:50],
            "ambiguous": len(ambiguous), "ambiguous_cases": ambiguous[:20],
            "ambiguous_consistent_with_known_prereq": consistent, "level_gated": level_gated,
            "community_prereq_contradicted": contradictions[:50], "n_contradicted": len(contradictions)}
