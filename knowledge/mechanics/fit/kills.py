"""Kill XP: the hypotheses about Forever's formula scored on recorded kills (glide.db and the live logs), rounding,
the zero-difference band, rested, Well Fed, elites, the README's level-14 anchors; and the overrides they support."""
import bisect
import collections
from fractions import Fraction

from knowledge.mechanics import params as P
from knowledge.mechanics import xp as X
from knowledge.mechanics.fit.common import as_int, median_of, rounded

WELL_FED_SPELL = "1248422"
ELITE_TC = ("elite", "rareelite", "worldboss")
PARTIAL_RATIO = 0.6

# Manual observations quoted in knowledge/README.md (session 20261003-165647, level 14 paladin). They are
# NOT in obs_kills (that session recorded no kills), so they are evaluated separately.
README_ANCHORS = [
    {"name": "Riverpaw Outrunner", "pl": 14, "ml": 10, "elite": False, "xp": 96},
    {"name": "Riverpaw Outrunner", "pl": 14, "ml": 9, "elite": False, "xp": 68},
    {"name": "Hogger", "pl": 14, "ml": 11, "elite": True, "xp": 250},
]



def _entities(conn):
    by_id, by_name = {}, collections.defaultdict(list)
    for r in conn.execute("SELECT id, name, min_level, max_level, rank FROM entities WHERE kind='npc'"):
        e = {"id": r[0], "name": r[1], "min": r[2], "max": r[3], "rank": r[4]}
        by_id[r[0]] = e
        if r[1]:
            by_name[r[1].split("\x00")[0]].append(e)
    return by_id, by_name


def _live_kill_frame(live, t, xp):
    """Index of the live frame where the kill counter stepped with this XP (or None)."""
    best = None
    for i in live.window(t - 3, t + 4):
        if i == 0:
            continue
        a, b = live.frames[i - 1][2], live.frames[i][2]
        ka, kb = as_int(a.get("kc")), as_int(b.get("kc"))
        if ka is not None and kb == ka + 1 and as_int(b.get("kx")) == xp and live.frames[i][1] == live.frames[i - 1][1]:
            if best is None or abs(live.frames[i][0] - t) < abs(live.frames[best][0] - t):
                best = i
    return best


def _live_target(live, i, npc_id=None):
    """(npc id, target level) of the mob just killed at live frame i."""
    fidx = live.frames[i][1]
    for j in range(i, max(-1, i - 40), -1):
        if live.frames[j][1] != fidx:
            break
        c = live.frames[j][2]
        tg, tl = c.get("tg"), as_int(c.get("tl"))
        if not tg or tl is None or tg == "P":
            continue
        if npc_id is not None and as_int(tg) != npc_id:
            continue
        if j == i and c.get("td") != "1":
            continue
        return as_int(tg), tl
    return None, None


def _kills_glide(conn, live, ents):
    by_id, by_name = ents
    cls = {r[0]: r[1] for r in conn.execute("SELECT id, class FROM sessions")}
    xp_rows = collections.defaultdict(list)
    for r in conn.execute("SELECT session, t, lvl, xp, max, rest FROM obs_xp ORDER BY session, t"):
        xp_rows[r[0]].append(r)
    out = []
    for r in conn.execute("SELECT session, t, npc_id, name, xp, rest, mob_level, lvl, ui_map, x, y "
                          "FROM obs_kills ORDER BY session, t"):
        k = {"src": "glide", "session": r[0], "t": r[1], "npc": r[2], "name": r[3], "xp": r[4], "rest_col": r[5],
             "ml_rec": r[6], "pl": r[7], "cls": cls.get(r[0])}
        rows = xp_rows.get(r[0], [])
        ts = [x[1] for x in rows]
        j = bisect.bisect_left(ts, r[1])
        before = rows[j - 1] if j > 0 else None
        after = rows[j] if j < len(rows) else None
        k["pool_xp"] = (before[5] or 0) if before else None
        k["pool_after_xp"] = (after[5] or 0) if after and after[1] - r[1] <= 3 else None
        k["ml_live"] = k["pool_live"] = k["pool_after_live"] = None
        k["wf"] = None
        if live.ok():
            i = _live_kill_frame(live, r[1], r[4])
            if i is not None:
                _, tl = _live_target(live, i, r[2])
                k["ml_live"] = tl
                prev, cur = live.frames[i - 1][2], live.frames[i][2]
                k["pool_live"] = as_int(prev.get("r"))
                k["pool_after_live"] = as_int(cur.get("r"))
            auras = live.auras_at(r[1]) if live.covered(r[1]) else None
            k["wf"] = None if auras is None else (WELL_FED_SPELL in auras)
            k["auras"] = sorted(set(auras.values())) if auras else []
        _finish_kill(k, by_id, by_name, live)
        out.append(k)
    return out


def _finish_kill(k, by_id, by_name, live):
    e = by_id.get(k["npc"]) if k.get("npc") else None
    if e is None and k.get("name"):
        cands = by_name.get(k["name"]) or []
        e = cands[0] if cands else None
    k["ent_min"], k["ent_max"], k["rank"] = (e["min"], e["max"], e["rank"]) if e else (None, None, None)
    if k.get("ml_rec") is not None:
        k["ml"], k["ml_src"] = k["ml_rec"], "rec"
    elif k.get("ml_live") is not None:
        k["ml"], k["ml_src"] = k["ml_live"], "live"
    elif e and e["min"] is not None and (e["max"] is None or e["max"] == e["min"]):
        k["ml"], k["ml_src"] = e["min"], "ent"
    else:
        k["ml"], k["ml_src"] = None, None
    seen = [v for v in (k.get("ml_rec"), k.get("ml_live")) if v is not None]
    if not seen and k["ent_min"] is not None:
        seen = list(range(k["ent_min"], (k["ent_max"] or k["ent_min"]) + 1))
    k["ml_cands"] = sorted(set(seen))
    tc = live.classification(k.get("name")) if live.ok() and k.get("name") else None
    k["tc"] = tc
    k["elite"] = bool((k["rank"] in (1, 2, 3)) or (tc in ELITE_TC))
    k["pool"] = k.get("pool_live") if k.get("pool_live") is not None else k.get("pool_xp")
    k["pool_after"] = k.get("pool_after_live") if k.get("pool_after_live") is not None else k.get("pool_after_xp")


def _kills_live_only(live, glide_kills, ents):
    """Kills seen only in the live logs (sessions whose SavedVariables were never archived)."""
    by_id, by_name = ents
    if not live.ok():
        return []
    known = collections.defaultdict(list)
    for k in glide_kills:
        known[k["xp"]].append(k["t"])
    out = []
    for i in range(1, len(live.frames)):
        (t0, f0, a), (t1, f1, b) = live.frames[i - 1], live.frames[i]
        ka, kb = as_int(a.get("kc")), as_int(b.get("kc"))
        if f0 != f1 or ka is None or kb != ka + 1 or t1 - t0 > 3:
            continue
        xp = as_int(b.get("kx"))
        if xp is None or any(abs(t - t1) <= 4 for t in known.get(xp, ())):
            continue
        npc, tl = _live_target(live, i)
        e = by_id.get(npc)
        name = e["name"].split("\x00")[0] if e and e["name"] else None
        k = {"src": "live", "session": live.files[f1], "t": int(t1), "npc": npc, "name": name,
             "xp": xp, "rest_col": None, "ml_rec": None, "ml_live": tl, "pl": as_int(a.get("l")), "cls": a.get("cl") or None,
             "pool_live": as_int(a.get("r")), "pool_after_live": as_int(b.get("r")), "pool_xp": None,
             "pool_after_xp": None}
        auras = live.auras_at(t1)
        k["wf"] = WELL_FED_SPELL in auras
        k["auras"] = sorted(set(auras.values()))
        _finish_kill(k, by_id, by_name, live)
        out.append(k)
    return out


def _pred(k, hyp, ml=None, fit=None):
    """Predicted XP for kill k under hypothesis `hyp` (None if no level)."""
    ml = k["ml"] if ml is None else ml
    if ml is None or k.get("pl") is None:
        return None
    pl = k["pl"]
    elite = k.get("elite", False)
    pool = k.get("pool") or 0
    if hyp == "classic":
        with P.classic_scope():
            return X.kill_xp(pl, ml, elite)
    if hyp == "classic_floor":
        with P.classic_scope({"xp.kill.rounding": "floor"}):
            return X.kill_xp(pl, ml, elite)
    if hyp == "classic_rested":
        with P.classic_scope():
            return X.kill_xp(pl, ml, elite, rest_pool=pool)
    if hyp == "classic_ml+1":
        with P.classic_scope():
            return X.kill_xp(pl, ml + 1, elite)
    if hyp == "classic_ml-1":
        with P.classic_scope():
            return X.kill_xp(pl, ml - 1, elite)
    if hyp == "classic_flat":
        with P.classic_scope():
            return X.round_xp(Fraction(X.kill_xp(pl, ml, elite)) * Fraction(str(fit)))
    if hyp == "classic_zdfit":
        table = list(P.classic("xp.zd.table"))
        for lv, zd in (fit or {}).items():
            table[int(lv)] = zd
        with P.classic_scope({"xp.zd.table": table}):
            return X.kill_xp(pl, ml, elite)
    if hyp == "mobbase":
        with P.classic_scope({"xp.kill.low_base": "mob"}):
            return X.kill_xp(pl, ml, elite)
    if hyp == "mobbase_rested":
        with P.classic_scope({"xp.kill.low_base": "mob"}):
            return X.kill_xp(pl, ml, elite, rest_pool=pool)
    if hyp.startswith("forever"):
        rnd = hyp.split(":")[1] if ":" in hyp else "half_even"
        wfv = fit if fit is not None else 0.05
        with P.classic_scope({"xp.kill.low_base": "mob", "xp.kill.rounding": rnd}):
            return X.kill_xp(pl, ml, elite, rest_pool=pool, xp_mod=wfv if k.get("wf") else 0.0)
    raise ValueError(hyp)


HYPOTHESES = [
    ("classic", "Classic formula as documented (base from player level), no rested bonus"),
    ("classic_floor", "Classic formula, integer floor rounding (emulator style)"),
    ("classic_rested", "Classic formula + rested bonus from the recorded pool (recorder missed the bonus text)"),
    ("classic_ml+1", "Classic formula with the mob level off by one (recorded level + 1)"),
    ("classic_ml-1", "Classic formula with the mob level off by one (recorded level - 1)"),
    ("classic_flat", "Classic formula times one fitted flat multiplier"),
    ("classic_zdfit", "Classic formula with a ZD table fitted per player level"),
    ("mobbase", "lower-level mobs use base = 5*mob_level+45, no rested bonus"),
    ("mobbase_rested", "base from mob level + rested bonus from the pool"),
    ("forever", "base from mob level + rested bonus + Well Fed (1248422) +5%"),
]
SIMPLE = {"classic": 0, "classic_floor": 0, "classic_rested": 1, "classic_ml+1": 0, "classic_ml-1": 0,
          "classic_flat": 1, "classic_zdfit": 2, "mobbase": 1, "mobbase_rested": 2, "forever": 3}


def _score(kills, hyp, fit=None):
    n = ex = w1 = 0
    err = []
    for k in kills:
        p = _pred(k, hyp, fit=fit)
        if p is None:
            continue
        n += 1
        ex += p == k["xp"]
        w1 += abs(p - k["xp"]) <= 1
        err.append(abs(p - k["xp"]))
    return {"n": n, "exact": ex, "rate": rounded(ex / n if n else None), "within1": rounded(w1 / n if n else None),
            "mae": rounded(sum(err) / n if n else None, 2)}


def _is_partial(k):
    """XP below 60% of the smallest no-bonus prediction (either base rule) over the levels seen for this
    mob (recorded/live, else the entity range): group split, NPC or other-player damage share."""
    cands = k["ml_cands"] or ([k["ml"]] if k["ml"] is not None else [])
    if not cands or k.get("pl") is None:
        return False
    preds = []
    for ml in cands:
        for h in ("classic", "mobbase"):
            p = _pred(dict(k, elite=False, wf=False, pool=0), h, ml=ml)
            if p:
                preds.append(p)
    return bool(preds) and k["xp"] < PARTIAL_RATIO * min(preds)


def _fit_flat(kills):
    num = den = 0
    for k in kills:
        p = _pred(k, "classic")
        if p:
            num += p * k["xp"]
            den += p * p
    return round(num / den, 4) if den else 1.0


def _fit_zd(kills):
    """Best integer ZD per player level for the Classic (player-base) formula on lower-mob kills."""
    by = collections.defaultdict(list)
    for k in kills:
        if k["ml"] is not None and k["ml"] < k["pl"] and not k.get("pool") and not k.get("wf"):
            by[k["pl"]].append(k)
    out = {}
    for pl, ks in by.items():
        best = None
        for zd in range(2, 31):
            table = list(P.classic("xp.zd.table"))
            table[pl] = zd
            with P.classic_scope({"xp.zd.table": table}):
                hit = sum(X.kill_xp(pl, k["ml"], k["elite"]) == k["xp"] for k in ks)
            if best is None or hit > best[1]:
                best = (zd, hit)
        out[pl] = best[0]
    return out


def _zd_consistency(kills, low_base):
    """Per player level, on clean lower-mob kills (no pool, no Well Fed, not elite): how many kills each
    ZD reproduces with the given base rule; the Classic ZD should be the unique best."""
    by = collections.defaultdict(list)
    for k in kills:
        if k["ml"] is not None and k["ml"] < k["pl"] and not k.get("pool") and not k.get("wf") and not k["elite"]:
            by[k["pl"]].append(k)
    res = {}
    for pl, ks in sorted(by.items()):
        hits = {}
        for zd in range(2, 31):
            table = list(P.classic("xp.zd.table"))
            table[pl] = zd
            with P.classic_scope({"xp.zd.table": table, "xp.kill.low_base": low_base}):
                hits[zd] = sum(X.kill_xp(pl, k["ml"]) == k["xp"] for k in ks)
        top = max(hits.values())
        czd = P.classic("xp.zd.table")[pl]
        res[str(pl)] = {"n": len(ks), "classic_zd": czd, "classic_hits": hits[czd],
                        "best_zd": sorted(z for z, h in hits.items() if h == top), "best_hits": top}
    return res


def _half_cases(kills):
    """Kills whose unrounded value is exactly x.5 at a rounding step: which rounding rule fits."""
    rows = []
    for k in kills:
        if k["ml"] is None or k["elite"] or k.get("pool"):
            continue
        with P.classic_scope({"xp.kill.low_base": "mob"}):
            raw = X._raw_kill(k["pl"], k["ml"], False)
        steps = [raw]
        if k.get("wf"):
            steps.append(Fraction(X.round_xp(raw, "half_even")) * Fraction(21, 20))
        if not any(s.denominator == 2 for s in steps):
            continue
        fits = {}
        for rule in ("half_even", "half_up", "floor"):
            with P.classic_scope({"xp.kill.low_base": "mob", "xp.kill.rounding": rule}):
                fits[rule] = X.kill_xp(k["pl"], k["ml"], xp_mod=0.05 if k.get("wf") else 0.0) == k["xp"]
        at_base = raw.denominator == 2
        rows.append({"pl": k["pl"], "ml": k["ml"], "raw": float(raw), "wf": bool(k.get("wf")), "xp": k["xp"],
                     "half_at": "base" if at_base else "well_fed",
                     "half_value": float(raw) if at_base else float(steps[1]), "fits": fits, "src": k["src"]})
    uniq = {}
    for r in rows:
        uniq.setdefault((r["pl"], r["ml"], r["wf"], r["xp"]), dict(r, n=0))["n"] += 1
    return list(uniq.values())


def kill_section(conn, live):
    """The kill XP section -> (section, glide.db kills, live-only kills)."""
    ents = _entities(conn)
    gk = _kills_glide(conn, live, ents)
    lk = _kills_live_only(live, gk, ents)
    for k in gk + lk:
        k["partial"] = _is_partial(k)
    known = [k for k in gk if k["ml"] is not None]
    clean = [k for k in known if not k["partial"]]
    strict = [k for k in clean if k["ml_src"] == "rec"]
    flat = _fit_flat([k for k in clean if not k.get("pool") and not k.get("wf")])
    zdfit = _fit_zd(clean)
    hyps = []
    for hid, desc in HYPOTHESES:
        fit = flat if hid == "classic_flat" else zdfit if hid == "classic_zdfit" else None
        row = {"id": hid, "desc": desc, "clean": _score(clean, hid, fit), "strict": _score(strict, hid, fit),
               "all_known": _score(known, hid, fit)}
        if hid == "classic_flat":
            row["k"] = flat
        if hid == "classic_zdfit":
            row["zd"] = {str(a): b for a, b in sorted(zdfit.items())}
        hyps.append(row)
    best = max(hyps, key=lambda h: (h["clean"]["rate"] or 0, -SIMPLE[h["id"]]))
    classic = next(h for h in hyps if h["id"] == "classic")

    # rounding rules on the full Forever model
    rounding = {rule: _score(clean, "forever:" + rule) for rule in ("half_even", "half_up", "floor")}
    half = _half_cases(clean + [k for k in lk if not k["partial"] and k["ml"] is not None])
    half_tally = {rule: sum(1 for h in half if h["fits"][rule]) for rule in ("half_even", "half_up", "floor")}

    # rested
    rk = [k for k in clean if k.get("pool")]
    rk_l = [k for k in lk if not k["partial"] and k["ml"] is not None and k.get("pool")]
    pool_checks = []
    for k in rk + rk_l:
        with P.classic_scope({"xp.kill.low_base": "mob"}):
            d = X.kill_xp_detail(k["pl"], k["ml"], k["elite"], rest_pool=k["pool"],
                                 xp_mod=0.05 if k.get("wf") else 0.0)
        pool_checks.append({"pl": k["pl"], "ml": k["ml"], "pool": k["pool"], "xp": k["xp"], "pred": d["xp"],
                            "bonus": d["bonus"], "pool_after": k.get("pool_after"), "pred_pool_after": d["pool_after"],
                            "src": k["src"]})
    rested = {"rest_column_null": sum(1 for k in gk if k["rest_col"] is None), "n_kills": len(gk),
              "n_rested_kills": len(pool_checks),
              "xp_exact": sum(1 for c in pool_checks if c["xp"] == c["pred"]),
              "pool_exact": sum(1 for c in pool_checks if c["pool_after"] is not None and c["pool_after"] == c["pred_pool_after"]),
              "pool_known": sum(1 for c in pool_checks if c["pool_after"] is not None),
              "cases": pool_checks}

    # Well Fed
    wk = [k for k in clean + [k for k in lk if not k["partial"] and k["ml"] is not None] if k.get("wf")]
    wf_fit = {}
    for cand in (0.0, 0.03, 0.04, 0.05, 0.06, 0.1):
        wf_fit[str(cand)] = sum(1 for k in wk if _pred(k, "forever", fit=cand) == k["xp"])
    best_wf = max(wf_fit.items(), key=lambda kv: (kv[1], -abs(float(kv[0]) - 0.05)))
    well_fed = {"spell": WELL_FED_SPELL, "n": len(wk), "exact_by_bonus": wf_fit, "best_bonus": float(best_wf[0]),
                "best_exact": best_wf[1], "live_coverage": sum(1 for k in gk if k.get("wf") is not None)}

    # Camp Benefits (1229741): any kill-XP effect?
    ck = [k for k in clean + [k for k in lk if not k["partial"] and k["ml"] is not None]
          if "Camp Benefits" in (k.get("auras") or ()) and not k.get("pool") and not k.get("wf")]
    camp = {"n": len(ck), "exact_no_bonus": sum(1 for k in ck if _pred(k, "forever") == k["xp"])}
    with P.classic_scope({"xp.kill.low_base": "mob"}):
        camp["exact_with_5pct"] = sum(1 for k in ck if X.kill_xp(k["pl"], k["ml"], k["elite"], xp_mod=0.05) == k["xp"])
    well_fed["camp_benefits"] = camp

    # elites and other per-creature multipliers (kills without rested pool or Well Fed only)
    elite = []
    for k in gk + lk:
        if k["ml"] is None or k["partial"] or k.get("pool") or k.get("wf"):
            continue
        base = _pred(dict(k, elite=False, pool=0, wf=False), "mobbase")
        if not base:
            continue
        ratio = k["xp"] / base
        if k["elite"] or ratio > 1.4:
            elite.append({"name": k["name"], "npc": k["npc"], "pl": k["pl"], "ml": k["ml"], "xp": k["xp"],
                          "normal_pred": base, "ratio": round(ratio, 3), "elite_known": k["elite"], "rank": k["rank"],
                          "tc": k["tc"], "src": k["src"]})

    # README level-14 anchors
    anchors = []
    for a in README_ANCHORS:
        row = dict(a)
        with P.classic_scope():
            row["classic"] = X.kill_xp(a["pl"], a["ml"], a["elite"])
            row["classic_rested"] = X.kill_xp(a["pl"], a["ml"], a["elite"], rested=True)
            row["classic_ml+1"] = X.kill_xp(a["pl"], a["ml"] + 1, a["elite"])
        with P.classic_scope({"xp.kill.low_base": "mob"}):
            row["mobbase"] = X.kill_xp(a["pl"], a["ml"], a["elite"])
            row["mobbase_rested"] = X.kill_xp(a["pl"], a["ml"], a["elite"], rested=True)
        anchors.append(row)
    anchor_fit = {h: sum(1 for a in anchors if a[h] == a["xp"])
                  for h in ("classic", "classic_rested", "classic_ml+1", "mobbase", "mobbase_rested")}

    # mismatches under the best model, and the live-only supplement
    mism = []
    for k in clean:
        p = _pred(k, "forever")
        if p is not None and p != k["xp"]:
            mism.append({"session": k["session"], "t": k["t"], "name": k["name"], "pl": k["pl"], "ml": k["ml"],
                         "ml_src": k["ml_src"], "ml_live": k.get("ml_live"), "xp": k["xp"], "pred": p,
                         "classic": _pred(k, "classic"), "pool": k.get("pool"), "wf": k.get("wf"), "elite": k["elite"]})
    disagree = [{"name": k["name"], "pl": k["pl"], "rec": k["ml_rec"], "live": k["ml_live"], "xp": k["xp"]}
                for k in gk if k["ml_rec"] is not None and k["ml_live"] is not None and k["ml_rec"] != k["ml_live"]]
    lclean = [k for k in lk if not k["partial"] and k["ml"] is not None]
    live_sup = {"n_kills": len(lk), "n_partial": sum(1 for k in lk if k["partial"]), "n_clean": len(lclean),
                "levels": sorted({k["pl"] for k in lclean if k["pl"]}),
                "classic": _score(lclean, "classic"), "forever": _score(lclean, "forever"),
                "mismatches": [{"t": k["t"], "name": k["name"], "pl": k["pl"], "ml": k["ml"], "xp": k["xp"],
                                "pred": _pred(k, "forever"), "elite": k["elite"], "pool": k.get("pool"),
                                "wf": k.get("wf")} for k in lclean if _pred(k, "forever") != k["xp"]][:30]}
    by_diff = collections.defaultdict(lambda: {"n": 0, "exact": 0, "levels": set()})
    for k in clean + lclean:
        b = by_diff[k["ml"] - k["pl"]]
        b["n"] += 1
        b["exact"] += _pred(k, "forever") == k["xp"]
        b["levels"].add(k["pl"])
    by_diff = {str(d): {"n": v["n"], "exact": v["exact"], "player_levels": [min(v["levels"]), max(v["levels"])]}
               for d, v in sorted(by_diff.items())}
    zd_mob = _zd_consistency(clean + lclean, "mob")
    zd_player = _zd_consistency(clean + lclean, "player")
    verified_through = 0            # contiguous levels where the Classic ZD is the unique best and fits >= 90%
    for pl, v in sorted(((int(a), b) for a, b in zd_mob.items())):
        if v["best_zd"] == [v["classic_zd"]] and v["classic_hits"] >= 0.9 * v["n"]:
            verified_through = pl
        elif v["n"] >= 3:
            break
    partial = [k for k in gk + lk if k["partial"]]
    sec = {
        "n_kills": len(gk), "n_known_level": len(known), "n_partial": sum(1 for k in gk if k["partial"]),
        "n_clean": len(clean), "n_strict": len(strict),
        "level_source": dict(collections.Counter(k["ml_src"] or "unknown" for k in gk)),
        "classic_agreement": classic["clean"]["rate"], "classic_agreement_all_known": classic["all_known"]["rate"],
        "classic_agreement_strict": classic["strict"]["rate"],
        "hypotheses": hyps, "best": best["id"], "best_rate": best["clean"]["rate"],
        "forever_by_level_diff": by_diff,
        "rounding": {"model_scores": rounding, "half_cases": half, "half_tally": half_tally},
        "zd": {"mob_base": zd_mob, "player_base": zd_player, "verified_through": verified_through},
        "rested": rested, "well_fed": well_fed, "elite": elite,
        "anchors_l14": {"rows": anchors, "fit": anchor_fit,
                        "note": "README observations (paladin level 14, session 20261003-165647 recorded no kills)"},
        "level_disagree_rec_live": disagree, "mismatches": mism[:40], "n_mismatch": len(mism),
        "partial": {"n": len(partial), "ratio_median": rounded(median_of([k["xp"] / max(1, _pred(dict(k, pool=0, wf=False, elite=False), "mobbase") or 1) for k in partial if k["ml"] is not None])),
                    "examples": [{"name": k["name"], "pl": k["pl"], "ml": k["ml"], "xp": k["xp"], "src": k["src"]}
                                 for k in partial[:15]],
                    "note": "XP < 60% of the lowest normal prediction for the levels seen: group split or "
                            "NPC/other damage share; excluded from the formula fit"},
        "live_only": live_sup,
    }
    return sec, gk, lk


def kill_overrides(sec, now):
    """The parameter overrides the kill XP section has strong evidence for."""
    ov = {}
    best = next(h for h in sec["hypotheses"] if h["id"] == sec["best"])
    classic = next(h for h in sec["hypotheses"] if h["id"] == "classic")
    mob = next(h for h in sec["hypotheses"] if h["id"] == "mobbase")
    n = best["clean"]["n"]
    strong = (sec["best"] in ("forever", "mobbase_rested", "mobbase") and n >= 100 and (best["clean"]["rate"] or 0) >= 0.9
              and (best["clean"]["rate"] - classic["clean"]["rate"]) >= 0.1)
    live = sec["live_only"]
    ev_live = "; live-only kills %d/%d exact" % (live["forever"]["exact"], live["forever"]["n"]) if live["forever"]["n"] else ""
    vt = sec["zd"]["verified_through"]
    zrows = [v for lv, v in sec["zd"]["mob_base"].items() if int(lv) <= vt]
    zd_hits = (sum(v["classic_hits"] for v in zrows), sum(v["n"] for v in zrows))
    if strong:
        ov["xp.kill.low_base"] = {
            "value": "mob", "status": "forever-fitted", "updated": now,
            "evidence": "%d clean kills: model with base=5*mob_level+45 for lower mobs %d/%d exact (%.1f%%) vs Classic "
                        "%d/%d (%.1f%%); mob-base alone %d/%d%s; with this base the Classic ZD is the best ZD at every "
                        "player level through %d (%d/%d lower-mob kills)" % (
                            n, best["clean"]["exact"], n, 100 * best["clean"]["rate"], classic["clean"]["exact"],
                            classic["clean"]["n"], 100 * classic["clean"]["rate"], mob["clean"]["exact"],
                            mob["clean"]["n"], ev_live, vt, zd_hits[0], zd_hits[1])}
    info = [h for h in sec["rounding"]["half_cases"] if any(h["fits"].values())]   # cases no rule fits say nothing
    nh = len(info)
    fits = {rule: sum(1 for h in info if h["fits"][rule]) for rule in ("half_even", "half_up", "floor")}
    if strong and nh >= 3 and fits["half_even"] == nh and fits["half_up"] < nh and fits["floor"] < nh:
        ms = sec["rounding"]["model_scores"]
        ov["xp.kill.rounding"] = {
            "value": "half_even", "status": "forever-fitted", "updated": now,
            "evidence": "%d distinct x.5 cases (%d kills) all round half to even; half-up fits %d, floor fits %d; "
                        "full model exact: half_even %d, half_up %d, floor %d of %d" % (
                            nh, sum(h["n"] for h in info), fits["half_up"], fits["floor"], ms["half_even"]["exact"],
                            ms["half_up"]["exact"], ms["floor"]["exact"], ms["half_even"]["n"])}
    rs = sec["rested"]
    if strong and rs["n_rested_kills"] >= 5 and rs["xp_exact"] >= 0.9 * rs["n_rested_kills"]:
        ov["xp.kill.rested_mult"] = {
            "value": 2.0, "status": "forever-fitted", "updated": now,
            "evidence": "rested kills earn a bonus equal to the kill XP while the pool lasts: %d/%d exact; the "
                        "README level-14 kills (96/68/250) are exactly double the mob-base XP" % (
                            rs["xp_exact"], rs["n_rested_kills"])}
        ov["rested.bonus_ratio"] = {
            "value": 0.5, "status": "forever-fitted", "updated": now,
            "evidence": "%d rested kills: bonus = min(xp, pool/2) exact %d/%d; pool drop = 2*bonus %d/%d; recorder "
                        "'rest' column NULL on %d/%d kills (bonus text not parsed)" % (
                            rs["n_rested_kills"], rs["xp_exact"], rs["n_rested_kills"], rs["pool_exact"],
                            rs["pool_known"], rs["rest_column_null"], rs["n_kills"])}
    wf = sec["well_fed"]
    if wf["n"] >= 10 and wf["best_bonus"] == 0.05 and wf["best_exact"] >= 0.9 * wf["n"] and wf["exact_by_bonus"]["0.0"] < 0.5 * wf["n"]:
        ov["xp.mod.well_fed"] = {
            "value": 0.05, "status": "forever-fitted", "updated": now,
            "evidence": "%d kills under Well Fed (spell %s): +5%% on the rounded kill XP, rounded half-even, "
                        "exact %d/%d (without bonus %d/%d)" % (wf["n"], WELL_FED_SPELL, wf["best_exact"], wf["n"],
                                                              wf["exact_by_bonus"]["0.0"], wf["n"])}
    camp = wf.get("camp_benefits") or {}
    if strong and camp.get("n", 0) >= 10 and camp["exact_no_bonus"] >= 0.9 * camp["n"] and \
            camp["exact_with_5pct"] < 0.5 * camp["n"]:
        ov["rested.camp_benefits_xp_mod"] = {
            "value": 0.0, "status": "forever-fitted", "updated": now,
            "evidence": "%d kills under Camp Benefits (1229741) without rested pool or Well Fed: %d exact with no "
                        "XP modifier (%d with +5%%)" % (camp["n"], camp["exact_no_bonus"], camp["exact_with_5pct"])}
    diff = sec["forever_by_level_diff"]
    eq = diff.get("0")
    if strong and eq and eq["n"] >= 30 and eq["exact"] >= 0.95 * eq["n"]:
        for key in ("xp.kill.base_mul", "xp.kill.base_add"):
            ov[key] = {"value": P.classic(key), "status": "forever-fitted", "updated": now,
                       "evidence": "same-level kills = 5*level+45: %d/%d exact at player levels %d-%d" % (
                           eq["exact"], eq["n"], eq["player_levels"][0], eq["player_levels"][1])}
    hi = [(int(d), v) for d, v in diff.items() if int(d) > 0]
    nh_, eh_ = sum(v["n"] for _, v in hi), sum(v["exact"] for _, v in hi)
    if strong and nh_ >= 20 and eh_ >= 0.95 * nh_:
        ov["xp.kill.high_step"] = {
            "value": P.classic("xp.kill.high_step"), "status": "forever-fitted", "updated": now,
            "evidence": "higher-level kills base(player)*(1+0.05*diff): %d/%d exact for diff %s (the +4 cap is "
                        "untested)" % (eh_, nh_, "/".join("+%d:%d" % (d, v["n"]) for d, v in sorted(hi)))}
    if strong and vt >= 2:
        ov["xp.zd.verified_through"] = {
            "value": vt, "status": "forever-fitted", "updated": now,
            "evidence": "with base from the mob level, the Classic ZD is the unique best ZD at every player level "
                        "through %d and reproduces %d/%d clean lower-mob kills (no rested pool, no Well Fed)" % (
                            vt, zd_hits[0], zd_hits[1])}
    return ov
