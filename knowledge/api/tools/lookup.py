"""Lookup tools: one quest, one creature, a name search, a glossary term."""
import re

from knowledge import store
from knowledge.api._layer import AP, MC, MX, R, decay_level
from knowledge.api.context import log_quest_ids
from knowledge.api.values import RANK, as_int, clean, cut, id_or_name, uniq


def t_quest_info(ctx, id=None, name=None):  # noqa: A002 - tool argument names are the API's
    err = ctx.need(guide=False, step=False)
    if err:
        return err
    pk, d, s = ctx.pack, ctx.pack.data, ctx.summary
    qid_in, qname_in = id_or_name(id, name, "q")
    if qid_in is None and not qname_in:
        return {"error": "give the quest's id or name"}
    qid = pk.find_quest(ctx, qid_in if qid_in is not None else qname_in)
    if qid is None:
        return {"error": "no quest matches %r; try search()" % (qid_in or qname_in,)}
    row = ctx.kb.one("""SELECT id, name, level, min_level, side, type, xp, xp_src, next_quest, suggested_group,
                        objective_text, removed, hidden FROM quest WHERE id=?""", qid) or {}
    lv = ctx.level
    full, ql = row.get("xp"), row.get("level")
    facts = ["q:%d" % qid]
    out = {"name": row.get("name"), "fact": "q:%d" % qid, "level": ql, "min_level": row.get("min_level"),
           "type": row.get("type"), "side": row.get("side"), "group_size": row.get("suggested_group"),
           "xp_full": full if full is not None else "unknown", "xp_source": row.get("xp_src"),
           "xp_seen_on_forever": bool(row.get("xp_src") and any(t in row["xp_src"] for t in ("obs", "wdb"))),
           "your_level": lv, "xp_at_your_level": MX.quest_xp(full, ql, lv) if full is not None and lv else None,
           "decay_at_level": decay_level(full, ql) if full else None,
           "objective_text": cut(row.get("objective_text"), 240),
           "removed_on_forever": bool(row.get("removed")) or None, "hidden": bool(row.get("hidden")) or None}
    objs = []
    for o in d.objectives.get(qid) or ():
        e = {"type": o.get("type"), "amount": o.get("amount"), "text": o.get("text")}
        if o.get("target_kind") == "npc" and o.get("target_id"):
            cr = d.creature(o["target_id"]) or {}
            e.update(target=cr.get("name"), fact="c:%s" % o["target_id"],
                     spawns=pk.spawn_summary(ctx, d.spawns_of(o["target_id"])))
            facts.append("c:%s" % o["target_id"])
        elif o.get("target_kind") == "item" and o.get("target_id"):
            item = o["target_id"]
            e.update(target=d.items.get(item), fact="i:%s" % item)
            facts.append("i:%s" % item)
            srcs = sorted(d.drops.get(item, ()), key=lambda r: -(r.get("rate") or 0))[:3]
            e["drops"] = [clean({"from": (d.creature(r["creature_id"]) or {}).get("name"),
                                  "fact": "c:%s" % r["creature_id"], "rate": r.get("rate"), "seen": r.get("n_obs")})
                          for r in srcs]
            facts += ["c:%s" % r["creature_id"] for r in srcs]
            if srcs:
                e["spawns"] = pk.spawn_summary(ctx, d.spawns_of(srcs[0]["creature_id"]))
            elif d.object_loot.get(item):
                ob = d.object_loot[item][0]
                e["from_object"] = (ctx.kb.one("SELECT name FROM gameobject WHERE id=?", ob["object_id"]) or {}).get("name")
                e["spawns"] = pk.spawn_summary(ctx, d.object_spawns(ob["object_id"]))
        elif o.get("target_kind") == "object" and o.get("target_id"):
            e["target"] = (ctx.kb.one("SELECT name FROM gameobject WHERE id=?", o["target_id"]) or {}).get("name")
            e["spawns"] = pk.spawn_summary(ctx, d.object_spawns(o["target_id"]))
        objs.append(clean(e))
    out["objectives"] = objs
    for role, key in (("start", "giver"), ("end", "turn_in")):
        g = d.giver(qid, role)
        if g:
            out[key] = clean({"name": g.get("name"), "where": pk.where(ctx, g.get("ui_map"), g.get("x"), g.get("y"))})
    out["prerequisites"] = [{"quest": (d.quest(p_id) or {}).get("name") or "quest %s" % p_id, "fact": "q:%s" % p_id,
                             "kind": kind} for p_id, kind in list(d.prereqs.get(qid, ()))[:6]]
    chain, cur, seen = [], qid, {qid}
    for _ in range(6):
        nq = (d.quest(cur) or {}).get("next_quest")
        if not nq or nq in seen:
            break
        seen.add(nq)
        chain.append((d.quest(nq) or {}).get("name") or "quest %s" % nq)
        cur = nq
    out["chain_next"] = chain
    out["unlocks"] = [(d.quest(u) or {}).get("name") or "quest %s" % u for u in d.unlocks(qid)][:6]
    log = s.get("quests") or {}
    lq = log.get(qid) or log.get(str(qid))
    if lq:
        out["in_your_log"] = {"complete": bool(lq.get("done")), "progress": lq.get("obj")}
    out["route"] = _route_role(ctx, qid)
    out["facts"] = uniq(facts + [p for p in ("p:xp.quest.factors", "p:xp.quest.full_until") if full])
    return clean(out)


def _route_role(ctx, qid):
    route = ctx.route
    if route is None:
        return {"status": "no RestedXP guide in the live state"}
    rx = route.idx_to_rxp.get
    cur = ctx.idx
    obj = [st["idx"] for st in route.steps if qid in st["quests"]["objective"]]
    a, t = route.accept_idx.get(qid), route.turnin_idx.get(qid)
    if a is not None or t is not None or obj:
        out = {"guide": ctx.guide, "accept_step": rx(a) if a is not None else None,
               "objective_steps": [rx(i) for i in obj][:4], "turnin_step": rx(t) if t is not None else None}
        if cur is not None:
            last = max([i for i in (a, t) if i is not None] + obj)
            out["status"] = "done in the route" if last < cur else "ahead in the route"
        return clean(out)
    try:
        for nk in R.next_guides(ctx.kb, route.guide_key, ctx.player)[:3]:
            nr = R.get_route(ctx.kb, nk, ctx.player)
            if qid in nr.accept_idx or qid in nr.turnin_idx:
                return clean({"guide": ctx.pack.guide_name(nk), "status": "in the next guide",
                               "accept_step": nr.idx_to_rxp.get(nr.accept_idx.get(qid)),
                               "turnin_step": nr.idx_to_rxp.get(nr.turnin_idx.get(qid))})
    except Exception:  # noqa: BLE001
        pass
    return {"status": "not in your route"}


def t_creature_info(ctx, id=None, name=None):  # noqa: A002
    err = ctx.need(guide=False, step=False)
    if err:
        return err
    pk, d, s = ctx.pack, ctx.pack.data, ctx.summary
    cid_in, cname_in = id_or_name(id, name, "c")
    if cid_in is None and not cname_in:
        return {"error": "give the creature's id or name"}
    cid = pk.find_creature(ctx, cid_in if cid_in is not None else cname_in)
    if cid is None:
        return {"error": "no creature matches %r; try search()" % (cid_in or cname_in,)}
    row = ctx.kb.one("SELECT * FROM creature WHERE id=?", cid) or {}
    lo = row.get("min_level") or row.get("max_level")
    hi = row.get("max_level") or lo
    rank = row.get("rank") or 0
    elite = rank in (1, 2, 3)
    lv = ctx.level
    cls = ctx.p()["class"]
    facts = ["c:%d" % cid]
    abil = store.jload(row.get("abilities"), None) if row.get("abilities") else None
    out = {"name": row.get("name"), "fact": "c:%d" % cid, "subname": row.get("subname"),
           "level": (lo if lo == hi else "%s-%s" % (lo, hi)) if lo else "unknown", "rank": RANK.get(rank, rank),
           "type": row.get("type"), "family": row.get("family"),
           "flees": bool(row["flee"]) if row.get("flee") is not None else None,
           "caster": bool(row["caster"]) if row.get("caster") is not None else None, "abilities": abil,
           "movement": row.get("movement"), "danger_notes": row.get("danger_notes")}
    notes = ctx.kb.q("""SELECT classification, movement, notes, zone FROM creature_note
                        WHERE creature_id=? OR (creature_id IS NULL AND name=?) LIMIT 3""", cid, row.get("name"))
    out["rxp_danger_notes"] = [clean({"note": n.get("notes"), "classification": n.get("classification")
                                       if n.get("classification") not in (None, "Normal") else None,
                                       "movement": n.get("movement"), "zone": n.get("zone")}) for n in notes]
    if lv and lo:
        per = {str(ml): MX.kill_xp(lv, ml, elite=elite) for ml in list(range(int(lo), int(hi) + 1))[:6]}
        mean_lv = (int(lo) + int(hi)) / 2.0
        ttk = MC.time_to_kill_s(cls, lv, mean_lv, rank)
        per_kill = ttk + MC.downtime_s(cls, lv, 1) + float(AP.get("analyzer.kill.search_s"))
        mean_xp = sum(per.values()) / len(per)
        out.update(your_level=lv, gray_level=MX.gray_level(lv), kill_xp_by_mob_level=per,
                   ttk_s=round(ttk, 1), seconds_per_kill=round(per_kill, 1),
                   xp_per_min=round(mean_xp / per_kill * 60.0, 1) if per_kill > 0 else None)
        if as_int(s.get("rested")):
            out["rested_note"] = "kills earn double while the rested pool lasts (%s XP pool)" % s.get("rested")
        facts += ["p:xp.kill.low_base", "p:combat.mob_hp", "p:combat.dps_ref", "p:analyzer.kill.search_s"]
        if elite:
            facts.append("p:xp.kill.elite_mult")
    drops = ctx.kb.q("""SELECT d.item_id, i.name, d.rate, d.n_obs, o.quest_id, q.name AS quest
                        FROM creature_drop d JOIN quest_objective o ON o.target_kind='item' AND o.target_id=d.item_id
                        LEFT JOIN item i ON i.id=d.item_id LEFT JOIN quest q ON q.id=o.quest_id
                        WHERE d.creature_id=? ORDER BY d.rate DESC LIMIT 20""", cid)
    log = set(log_quest_ids(s))
    by_item = {}
    for r in drops:
        e = by_item.setdefault(r["item_id"], {"item": r.get("name"), "fact": "i:%s" % r["item_id"],
                                              "rate": r.get("rate"), "seen": r.get("n_obs"), "quests": [], "_log": False})
        if r.get("quest") and r["quest"] not in e["quests"]:
            e["quests"].append(r["quest"])
        if r.get("quest_id") in log:
            e["_log"] = True
    items = sorted(by_item.values(), key=lambda e: (not e["_log"], -(e["rate"] or 0)))[:6]
    for e in items:
        e.pop("_log", None)
        e["quests"] = e["quests"][:3]
        facts.append(e["fact"])
    out["quest_item_drops"] = [clean(e) for e in items]
    out["spawns"] = pk.spawn_summary(ctx, d.spawns_of(cid))
    out["facts"] = uniq(facts)
    return clean(out)


def t_glossary(ctx, term=None):
    pk = ctx.pack
    if pk is None:
        return {"unavailable": "no knowledge base"}
    t = str(term or "").strip().lower()
    if not t:
        return {"error": "empty term"}
    rows = pk.glossary_rows()
    cands = {t, t[:-1] if t.endswith("s") else t + "s"}
    for r in rows:
        names = [r["term"].lower()] + [a.lower() for a in r["aliases"]]
        if cands & set(names):
            return {"term": r["term"], "definition": r["definition"], "aliases": r["aliases"],
                    "fact": "g:%s" % r["term"], "facts": ["g:%s" % r["term"]]}
    close = [r["term"] for r in rows if t in r["term"].lower() or r["term"].lower() in t
             or any(t in a.lower() for a in r["aliases"])]
    if len(close) == 1:
        return t_glossary(ctx, close[0])
    return {"error": "%r is not in the glossary" % term, "close": close[:6]}


def t_search(ctx, text=None):
    err = ctx.need(guide=False, step=False)
    if err:
        return err
    q = str(text or "").strip()
    if not q:
        return {"error": "empty text"}
    low = q.lower()
    toks = [t for t in re.findall(r"[a-z0-9']+", low) if len(t) > 1] or [low]
    toks = [t[:-1] if len(t) > 3 and t.endswith("s") else t for t in toks]
    like = "%" + max(toks, key=len) + "%"
    here = ctx.here()
    route = ctx.route
    d = ctx.pack.data
    found = []
    sqls = (("quest", "SELECT id, name, level FROM quest WHERE lower(name) LIKE ? LIMIT 300"),
            ("creature", "SELECT id, name, max_level AS level FROM creature WHERE lower(name) LIKE ? LIMIT 300"),
            ("item", "SELECT id, name, req_level AS level FROM item WHERE lower(name) LIKE ? LIMIT 300"),
            ("poi", "SELECT kind AS pkind, id, name, ui_map FROM poi WHERE lower(name) LIKE ? LIMIT 100"))
    for kind, sql in sqls:
        for r in ctx.kb.q(sql, like):
            n = (r.get("name") or "").lower()
            if not n or not all(t in n for t in toks):
                continue
            score = 0 if n == low else 1 if n.startswith(low) else 2 if low in n else 3
            boost = 0
            if kind == "quest" and route is not None and (r["id"] in route.accept_idx or r["id"] in route.turnin_idx):
                boost = -1
            elif kind == "creature" and here and d.spawns_of(r["id"], here[0]):
                boost = -1
            elif kind == "poi" and here and r.get("ui_map") == here[0]:
                boost = -1
            fact = {"quest": "q:%s", "creature": "c:%s", "item": "i:%s"}.get(kind)
            e = {"kind": kind, "name": r["name"], "level": r.get("level"),
                 "fact": fact % r["id"] if fact else "poi:%s:%s" % (r["pkind"], r["id"]),
                 "map": d.map_name(r["ui_map"]) if r.get("ui_map") else None, "poi_kind": r.get("pkind")}
            found.append((score + boost, len(n), kind, e))
    found.sort(key=lambda x: (x[0], x[1], x[2]))
    res, seen = [], set()
    for _, _, kind, e in found:
        k = (kind, e["name"].lower())
        if k in seen:
            continue
        seen.add(k)
        res.append(clean(e))
        if len(res) >= 10:
            break
    return {"query": q, "results": res, "facts": [e["fact"] for e in res if e.get("fact")]}
