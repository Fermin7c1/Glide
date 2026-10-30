"""Sections fitted from the live logs alone: rested, hearth, death, training costs, downtime."""
import bisect
import collections
import math
import time

from knowledge.mechanics import economy as E
from knowledge.mechanics import params as P
from knowledge.mechanics.fit.common import as_int, median_of, rounded

CAMP_AURAS = ("Camp Benefits", "Welcoming Campfire", "Campfire Nearby")
FOOD_AURAS = ("Food", "Drink", "Food & Drink", "First Aid")


def rested_section(live, now):
    """In-session accrual while IsResting (intervals between consecutive +2 ticks) and sudden pool jumps
    (campfire, relog)."""
    inc = []
    jumps = []
    last = None                      # (t, pool, level, file) of the previous resting tick
    for idx in range(1, len(live.frames)):
        (t0, f0, a), (t1, f1, b) = live.frames[idx - 1], live.frames[idx]
        r0, r1, lv, xmax = as_int(a.get("r")), as_int(b.get("r")), as_int(b.get("l")), as_int(b.get("X"))
        if f0 != f1 or r0 is None or r1 is None or not xmax:
            last = None
            continue
        if r1 - r0 >= 10 and a.get("l") == b.get("l"):
            names = live.aura_names_between(t1 - 90, t1 + 120)
            jumps.append({"t": int(t1), "level": lv, "d_pool": r1 - r0, "frac": round((r1 - r0) / xmax, 4),
                          "camp": bool(names & set(CAMP_AURAS)), "resting": b.get("rs") == "1",
                          "gap_s": round(t1 - t0, 1)})
        steady = a.get("rs") == "1" and b.get("rs") == "1" and a.get("x") == b.get("x") and a.get("l") == b.get("l")
        if not steady:
            last = None
            continue
        if r1 == r0:
            continue
        if 0 < r1 - r0 <= 4:
            if last and last[2] == lv and last[3] == f1 and t1 - last[0] < 3600:
                dt = t1 - last[0]
                inc.append({"level": lv, "xp_max": xmax, "d_pool": r1 - last[1], "dt": round(dt, 1),
                            "rate_per_8h": round((r1 - last[1]) / xmax / dt * 28800, 5)})
            last = (t1, r1, lv, f1)
        else:
            last = None
    rates = [i["rate_per_8h"] for i in inc]
    camp = [j["frac"] for j in jumps if j["camp"]]
    sec = {"accrual": {"n_intervals": len(inc), "median_rate_per_8h": rounded(median_of(rates), 4),
                       "intervals": inc[:20], "classic": P.classic("rested.rate_per_8h")},
           "jumps": jumps, "camp_fracs": camp}
    ov = {}
    med = median_of(rates)
    if len(inc) >= 3 and med and abs(med / 0.05 - 1) <= 0.05:
        ov["rested.rate_per_8h"] = {
            "value": 0.05, "status": "forever-verified", "updated": now,
            "evidence": "%d in-session resting intervals (IsResting, pool +2 per tick): median %.4f of a level per 8 h "
                        "(levels %s)" % (len(inc), med, sorted({i["level"] for i in inc}))}
    if len(camp) >= 2 and max(camp) - min(camp) <= 0.004:
        ov["rested.camp_bonus"] = {
            "value": round(median_of(camp), 3), "status": "forever-fitted", "updated": now,
            "evidence": "%d campfire sits: pool jumped by %s of a level (Welcoming Campfire / Camp Benefits)" % (
                len(camp), ", ".join("%.3f" % c for c in camp))}
    return sec, ov


def hearth_section(live, now):
    """The hearthstone section -> (section, overrides)."""
    casts = []
    for idx in range(1, len(live.frames)):
        (t0, f0, a), (t1, f1, b) = live.frames[idx - 1], live.frames[idx]
        h0, h1 = as_int(a.get("hs")), as_int(b.get("hs"))
        if f0 == f1 and h0 is not None and h1 is not None and h0 <= 10 and h1 >= 600:
            casts.append({"t": int(t1), "hs_after": h1, "gap_s": round(t1 - t0, 2),
                          "cd_range": [h1, round(h1 + (t1 - t0) + 1, 1)]})
    ov = {}
    if casts:
        lo = max(c["cd_range"][0] for c in casts)
        hi = min(c["cd_range"][1] for c in casts)
        nice = [m for m in range(60, 7260, 60) if lo <= m <= hi]
        if nice:
            ov["travel.hearth_cd_s"] = {"value": float(nice[0]), "status": "forever-verified", "updated": now,
                                        "evidence": "hearthstone cooldown read right after %d cast(s): %s s remaining "
                                                    "-> %d s" % (len(casts), [c["hs_after"] for c in casts], nice[0])}
    return {"casts": casts}, ov


def death_section(live, now):
    """The death section -> (section, overrides)."""
    deaths = []
    cur = None
    for idx in range(1, len(live.frames)):
        (_, f0, a), (t1, f1, b) = live.frames[idx - 1], live.frames[idx]
        if f0 != f1:
            cur = None
            continue
        d0, d1 = a.get("d"), b.get("d")
        if d0 == "0" and d1 in ("1", "2"):
            cur = {"t": int(t1), "level": as_int(a.get("l")), "du_before": as_int(a.get("du")),
                   "map": as_int(a.get("mp")), "pos": (as_int(a.get("px")), as_int(a.get("py")))}
        elif cur and d1 == "2" and "release_t" not in cur:
            cur.update(release_t=int(t1), gy=(as_int(b.get("px")), as_int(b.get("py"))), du_dead=as_int(a.get("du")))
        elif cur and d0 in ("1", "2") and d1 == "0":
            cur.update(alive_t=int(t1), du_after=as_int(b.get("du")),
                       alive_pos=(as_int(b.get("px")), as_int(b.get("py"))))
            deaths.append(cur)
            cur = None
    rows = []
    sizes = live.maps
    for d in deaths:
        gy, ap = d.get("gy"), d.get("alive_pos")
        spirit = None
        if gy and ap and None not in gy and None not in ap:
            spirit = abs(gy[0] - ap[0]) <= 150 and abs(gy[1] - ap[1]) <= 150
        dist = None
        if gy and None not in gy and None not in d["pos"] and d["map"] in sizes:
            w, h = sizes[d["map"]]
            dist = math.hypot((gy[0] - d["pos"][0]) / 10000.0 * w, (gy[1] - d["pos"][1]) / 10000.0 * h)
        run_s = (d["alive_t"] - d["release_t"]) if "release_t" in d else None
        dd = (d["du_before"] - d["du_dead"]) if d.get("du_dead") is not None and d["du_before"] is not None else None
        rows.append({"t": d["t"], "level": d["level"], "spirit_res": spirit, "release_s": (d["release_t"] - d["t"]) if "release_t" in d else None,
                     "dead_to_alive_s": d["alive_t"] - d["t"], "ghost_s": run_s, "corpse_dist_yd": rounded(dist, 0),
                     "implied_ghost_yd_s": rounded(dist / run_s, 2) if dist and run_s and not spirit else None,
                     "du_before": d["du_before"], "du_loss_death": dd,
                     "du_loss_total": (d["du_before"] - d["du_after"]) if d["du_before"] is not None and d.get("du_after") is not None else None})
    low = [r for r in rows if r["level"] is not None and r["level"] < 10]
    high = [r for r in rows if r["level"] is not None and r["level"] >= 10]
    sick = sum(1 for _, _, auras in live.apages for a in auras if "Resurrection Sickness" in a[1])
    sec = {"deaths": rows, "below10": {"n": len(low), "spirit_res": sum(1 for r in low if r["spirit_res"]),
                                       "durability_lost": sum(1 for r in low if r["du_loss_total"])},
           "level10plus": {"n": len(high), "du_loss_death": [r["du_loss_death"] for r in high]},
           "res_sickness_aura_seen": sick}
    ov = {}
    if len(low) >= 5 and not sec["below10"]["durability_lost"] and any((r["du_loss_death"] or 0) > 0 for r in high):
        ov["death.durability.min_level"] = {
            "value": 10, "status": "forever-fitted", "updated": now,
            "evidence": "%d deaths at levels %d-9 (%d spirit-healer resurrections) lost no durability; deaths at 10+ "
                        "lost %s points on the weakest item (10%% rule)" % (
                            len(low), min(r["level"] for r in low), sec["below10"]["spirit_res"],
                            [r["du_loss_death"] for r in high])}
    return sec, ov


PROFESSION_SPELLS = {2580, 2656, 2018, 2550, 3273, 8613, 2575, 2366, 2108, 3908, 7411, 4036, 2259, 2551, 3274}


def training_section(live):
    """Trainer visits: LEARNED_SPELL events within 15 s of each other, money before the first and 2 frames
    after the last (the money update can lag a frame)."""
    visits = []
    cur = None
    for idx in range(1, len(live.frames)):
        t, f, c = live.frames[idx]
        prev = live.frames[idx - 1][2]
        olds = (prev.get("e1"), prev.get("e2"), prev.get("e3"))
        fresh = [e for e in (c.get("e1"), c.get("e2"), c.get("e3")) if e and ":learn:" in e and e not in olds]
        if fresh:
            if cur is None or f != cur["f"] or t - cur["t1"] > 15:
                if cur:
                    visits.append(cur)
                cur = {"f": f, "t0": t, "t1": t, "i1": idx, "g0": as_int(prev.get("g")), "level": as_int(c.get("l")),
                       "cls": c.get("cl") or None, "spells": []}
            cur["t1"], cur["i1"] = t, idx
            cur["spells"] += [as_int(e.split(":")[2]) for e in fresh]
    if cur:
        visits.append(cur)
    rows = []
    for v in visits:
        j = min(len(live.frames) - 1, v["i1"] + 2)
        g1 = as_int(live.frames[j][2].get("g"))
        cls_sp = [s for s in v["spells"] if s and s not in PROFESSION_SPELLS and s < 1000000]
        paid = (v["g0"] - g1) if None not in (v["g0"], g1) else None
        per = E.spell_cost(v["level"] or 1)
        rows.append({"t": int(v["t0"]), "level": v["level"], "cls": v["cls"], "spells": v["spells"],
                     "class_spells": cls_sp, "paid": paid, "per_rank": per,
                     "ranks_implied": round(paid / per, 2) if paid and per else None,
                     "fits": bool(paid and cls_sp and paid % per in (0, 10))})
    cls_visits = [r for r in rows if r["class_spells"] and r["paid"]]
    sec = {"visits": rows, "class_visits": len(cls_visits), "fit": sum(1 for r in cls_visits if r["fits"]),
           "note": "trainer visit = learn events within 15 s; payment should be a multiple of the per-rank price "
                   "(rank upgrades do not always fire LEARNED_SPELL_IN_SKILL_LINE; one 10c old rank allowed)"}
    ov = {}
    if len(cls_visits) >= 5 and sec["fit"] == len(cls_visits):
        lv = sorted({r["level"] for r in cls_visits})
        ov["economy.train.per_spell_low"] = {
            "value": P.classic("economy.train.per_spell_low"), "status": "forever-fitted", "updated": now_date(),
            "evidence": "%d class-trainer visits at levels %s: every payment is a multiple of the per-rank price "
                        "(10c at 1-3, 1s at 4-7, 2s at 8-9, 3s at 10)" % (len(cls_visits), lv)}
    return sec, ov


def now_date():
    return time.strftime("%Y-%m-%d")


def downtime_section(live):
    """Food, drink and first aid time per kill, by class."""
    per_file = collections.defaultdict(lambda: {"s": 0.0, "n": 0, "cls": set()})
    seen = set()
    for t, _, auras in live.apages:
        for spell, name, exp, dur in auras:
            if name in FOOD_AURAS and dur and (spell, exp) not in seen:
                seen.add((spell, exp))
                i = bisect.bisect_left(live.ft, t)
                if i < len(live.frames):
                    f = live.files[live.frames[i][1]]
                    per_file[f]["s"] += dur
                    per_file[f]["n"] += 1
    kills_by_file = collections.Counter()
    cls_by_file = collections.defaultdict(collections.Counter)
    for _, f, c in live.frames:
        cls_by_file[live.files[f]][c.get("cl") or "?"] += 1
    for i in range(1, len(live.frames)):
        (_, f0, a), (_, f1, b) = live.frames[i - 1], live.frames[i]
        if f0 == f1 and as_int(b.get("kc")) is not None and as_int(a.get("kc")) is not None and as_int(b.get("kc")) > as_int(a.get("kc")):
            kills_by_file[live.files[f1]] += as_int(b.get("kc")) - as_int(a.get("kc"))
    by_cls = collections.defaultdict(lambda: [0.0, 0, 0])
    for f, k in kills_by_file.items():
        cls = max((c for c in cls_by_file[f] if c != "?"), key=lambda c: cls_by_file[f][c], default=None)
        if not cls or sum(n for c, n in cls_by_file[f].items() if c != cls and c != "?"):
            continue                       # unknown class or two characters in one log: skip
        by_cls[cls][0] += per_file[f]["s"] if f in per_file else 0.0
        by_cls[cls][1] += per_file[f]["n"] if f in per_file else 0
        by_cls[cls][2] += k
    return {"by_class": {c: {"channel_s": round(v[0]), "uses": v[1], "kills": v[2],
                             "s_per_kill": rounded(v[0] / v[2] if v[2] else None, 2)}
                         for c, v in sorted(by_cls.items())},
            "note": "Food/Drink/First Aid channel time per kill (lower bound; natural regen not visible: health is "
                    "secret). Report only, no override"}
