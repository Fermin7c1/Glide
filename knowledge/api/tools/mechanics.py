"""Mechanics tools: kill and quest XP with each parameter's status, and any parameter by key."""
from knowledge import store
from knowledge.api._layer import AP, MAX_LEVEL, MP, MX, decay_level
from knowledge.api.values import CONFIRMED, as_int, clean, cut


def _param_status(keys):
    st = {}
    for k in keys:
        try:
            st[k] = MP.status(k)
        except KeyError:
            try:
                st[k] = AP.status(k)
            except KeyError:
                st[k] = "unknown"
    return st


def t_kill_xp(ctx, mob_level=None, elite=False):
    err = ctx.need(guide=False, step=False)
    if err:
        return err
    lv, ml = ctx.level, as_int(mob_level)
    if not lv or ml is None:
        return {"error": "need the live level and a mob level"}
    elite = elite is True or str(elite).strip().lower() in ("true", "1", "yes", "elite")
    rest = as_int(ctx.summary.get("rested")) or 0
    dt = MX.kill_xp_detail(lv, ml, elite, rest_pool=rest if rest else None)
    keys = ["xp.kill.base_mul", "xp.kill.base_add", "xp.kill.low_base", "xp.kill.high_step", "xp.kill.high_cap",
            "xp.kill.rounding", "xp.zd.table", "xp.gray.table"]
    if elite:
        keys.append("xp.kill.elite_mult")
    if rest:
        keys.append("rested.bonus_ratio")
    status = _param_status(keys)
    with MP.classic_scope():
        classic = MX.kill_xp(lv, ml, elite=elite)
    base = dt.get("base") or 0
    out = {"your_level": lv, "mob_level": ml, "elite": elite, "xp": dt.get("group", base),
           "rested_bonus": dt.get("bonus") or None, "xp_with_rested": dt.get("xp") if dt.get("bonus") else None,
           "gray_level": dt.get("gray"), "gives_xp": base > 0, "zero_difference": dt.get("zd"),
           "classic_xp": classic, "status": status,
           "unverified": [k for k, v in status.items() if v not in CONFIRMED],
           "facts": ["p:%s" % k for k in keys]}
    return clean(out)


def t_quest_xp(ctx, quest=None, at_level=None):
    err = ctx.need(guide=False, step=False)
    if err:
        return err
    qid = ctx.pack.find_quest(ctx, quest)
    if qid is None:
        return {"error": "no quest matches %r" % (quest,)}
    q = ctx.kb.one("SELECT name, level, xp, xp_src FROM quest WHERE id=?", qid) or {}
    full, ql = q.get("xp"), q.get("level")
    lv = as_int(at_level) or ctx.level
    if full is None:
        return {"quest": q.get("name"), "fact": "q:%d" % qid, "xp_full": "unknown",
                "note": "this quest's XP is not in the knowledge base", "facts": ["q:%d" % qid]}
    if not lv:
        return {"error": "need a level (at_level)"}
    keys = ["xp.quest.full_until", "xp.quest.factors", "xp.quest.rounding"]
    status = _param_status(keys)
    with MP.classic_scope():
        classic = MX.quest_xp(full, ql, lv)
    nxt = {str(L): MX.quest_xp(full, ql, L) for L in range(lv + 1, min(MAX_LEVEL, lv + 4))}
    return clean({"quest": q.get("name"), "fact": "q:%d" % qid, "quest_level": ql, "xp_full": full,
                   "xp_source": q.get("xp_src"),
                   "xp_seen_on_forever": bool(q.get("xp_src") and any(t in q["xp_src"] for t in ("obs", "wdb"))),
                   "at_level": lv, "xp": MX.quest_xp(full, ql, lv),
                   "factor": MX.quest_xp_factor(ql, lv) if ql is not None else None,
                   "decay_at_level": decay_level(full, ql), "next_levels": nxt, "classic_xp": classic,
                   "status": status, "unverified": [k for k, v in status.items() if v not in CONFIRMED],
                   "facts": ["q:%d" % qid] + ["p:%s" % k for k in keys]})


def t_param(ctx, key=None):
    err = ctx.need(guide=False, step=False)
    if err:
        return err
    k = str(key or "").strip()
    if k.lower().startswith("p:"):
        k = k[2:]
    if not k:
        return {"error": "empty key"}
    rows = ctx.kb.q("SELECT * FROM param WHERE key=?", k) or \
        ctx.kb.q("SELECT * FROM param WHERE key LIKE ? ORDER BY key LIMIT 40", k + "%") or \
        ctx.kb.q("SELECT * FROM param WHERE key LIKE ? ORDER BY key LIMIT 40", "%" + k + "%")
    lv = ctx.level
    out = []
    for r in rows[:12]:
        v, cv = store.jload(r.get("value"), r.get("value")), store.jload(r.get("classic_value"), r.get("classic_value"))
        e = {"key": r["key"], "unit": r.get("unit"), "status": r.get("status"),
             "confirmed_on_forever": r.get("status") in CONFIRMED, "evidence": cut(r.get("evidence"), 200),
             "fact": "p:%s" % r["key"]}
        for nm, val in (("value", v), ("classic_value", cv)):
            if isinstance(val, list) and len(val) > 12:
                e[nm + "_at_your_level"] = val[lv] if lv is not None and 0 <= lv < len(val) else None
                e[nm] = "list of %d values" % len(val)
            elif isinstance(val, dict) and len(val) > 12:
                e[nm + "_at_your_level"] = val.get(str(lv)) if lv is not None else None
                e[nm] = "table of %d entries" % len(val)
            else:
                e[nm] = val
        out.append(clean(e))
    res = {"params": out, "facts": [e["fact"] for e in out]}
    if len(rows) > 12:
        res["more"] = len(rows) - 12
    if not out:
        res["error"] = "no parameter key matches %r" % k
    return res
