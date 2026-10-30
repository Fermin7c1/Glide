"""Quest XP: recorded turn-ins against the raw quest XP and the over-level reduction."""
import collections
import json

from knowledge.mechanics import params as P
from knowledge.mechanics import xp as X
from knowledge.mechanics.fit.common import as_int, rounded


def quest_section(conn, live, seed, now):
    """The quest XP section -> (section, overrides)."""
    qinfo = {}
    for qid, lv, qxp, fs in conn.execute("SELECT id, level, xp, field_sources FROM quests"):
        src = None
        try:
            src = (json.loads(fs) if fs else {}).get("xp")
        except ValueError:
            pass
        qinfo[qid] = (lv, qxp, src)
    obs = [{"quest": q, "pl": pl, "xp": x, "t": t, "src": "glide"} for q, pl, x, t in conn.execute(
        "SELECT quest_id, turned_lvl, xp, turned_t FROM obs_quests WHERE turned_t IS NOT NULL AND xp IS NOT NULL")]
    have = collections.defaultdict(list)
    for o in obs:
        have[o["quest"]].append(o["t"])
    n_live = 0
    for t, kind, arg, c in live.events:
        if kind != "tin":
            continue
        try:
            q, x = (int(v) for v in arg.split(",")[:2])
        except ValueError:
            continue
        if any(abs(t - tt) <= 120 for tt in have.get(q, ())):
            continue
        have[q].append(t)
        obs.append({"quest": q, "pl": as_int(c.get("l")), "xp": x, "t": int(t), "src": "live"})
        n_live += 1
    src = (seed or {}).get("sources", {})
    fg = src.get("fg_quests", {})
    cmp = {"glide_db": [0, 0, 0], "fg": [0, 0]}
    rounding_cases = [0, 0]
    over, mism = [], []
    for o in obs:
        if o["pl"] is None:
            continue
        lv, qxp, xsrc = qinfo.get(o["quest"], (None, None, None))
        if qxp is not None and lv is not None:
            p = X.quest_xp(qxp, lv, o["pl"])
            cmp["glide_db"][0] += 1
            cmp["glide_db"][1] += p == o["xp"]
            cmp["glide_db"][2] += xsrc == "obs"
        for name, rec, lk, xk in (("fg", fg.get(str(o["quest"])), "lvl", "xp"),):
            if not rec or rec.get(xk) is None or not rec.get(lk):
                continue
            p = X.quest_xp(rec[xk], rec[lk], o["pl"])
            cmp[name][0] += 1
            cmp[name][1] += p == o["xp"]
            if name == "fg":
                if X.round_quest_xp(rec[xk]) != rec[xk]:
                    rounding_cases[0] += 1
                    rounding_cases[1] += p == o["xp"]
                if o["pl"] - rec[lk] > int(P.get("xp.quest.full_until")):
                    over.append({"quest": o["quest"], "qlvl": rec[lk], "pl": o["pl"], "raw": rec[xk], "xp": o["xp"], "pred": p})
                if p != o["xp"]:
                    mism.append({"quest": o["quest"], "qlvl": rec[lk], "pl": o["pl"], "raw": rec[xk], "xp": o["xp"], "pred": p})
    diffs = collections.Counter()
    for o in obs:
        rec = fg.get(str(o["quest"]))
        if rec and rec.get("lvl") and o["pl"] is not None:
            diffs[o["pl"] - rec["lvl"]] += 1
    sec = {"n_obs": len(obs), "n_live_added": n_live,
           "vs_glide_db": {"n": cmp["glide_db"][0], "exact": cmp["glide_db"][1], "circular_obs_source": cmp["glide_db"][2],
                           "note": "quests.xp prefers recorder values (build_db precedence) so most of this is circular"},
           "vs_community": {k: {"n": v[0], "exact": v[1], "rate": rounded(v[1] / v[0] if v[0] else None)}
                            for k, v in cmp.items() if k != "glide_db"},
           "rounding_cases_fg": {"n": rounding_cases[0], "exact": rounding_cases[1],
                                 "note": "ForeverGuide raw XP differs from the server value until rounded (5/10/25/50)"},
           "player_minus_quest_level": {str(k): v for k, v in sorted(diffs.items())},
           "over_level": {"n": len(over), "cases": over[:20]}, "mismatches": mism[:20]}
    ov = {}
    fgc = cmp["fg"]
    if fgc[0] >= 30 and fgc[1] >= 0.95 * fgc[0] and rounding_cases[0] >= 5 and rounding_cases[1] == rounding_cases[0]:
        ov["xp.quest.rounding"] = {
            "value": "bands", "status": "forever-fitted", "updated": now,
            "evidence": "%d turn-ins vs ForeverGuide raw XP through 5/10/25/50 rounding: %d exact; %d cases where "
                        "rounding changes the value all exact; over-level reduction not exercised (%d cases)" % (
                            fgc[0], fgc[1], rounding_cases[0], len(over))}
    return sec, ov
