"""Recorded play vs the route model: where the time went, compared with the briefs.

  review_session(kb, session_id, player=None) -> dict of plain facts
  step_windows(rows, briefs_by_idx, route_idx) -> visits with actual vs expected seconds
  calibration(kb, data=None) -> {class: {"actual_s", "expected_s", "windows", "ratio", "factor"}}

obs_step_time has one row per contiguous visit of a RestedXP step (session, guide_key, idx, t_start, t_end,
level, xp_gain, deaths). RXP's current step is the first unfinished foreground step, so the time logged on a
step also covers the background (#completewith / #sticky) work and the steps it auto-skipped since the
previous visit. Each visit is therefore compared with the modelled time of the route window
(previous visited idx, this idx]; consecutive visits of the same step are merged.
"""
from . import params as AP
from .kbdata import as_kb
from .route import briefs_for


def _brief_map(kb, guide_key, player, cache, use_stored=True):
    key = (guide_key, player["class"], player.get("race"))
    if key not in cache:
        m = {}
        try:
            for b in briefs_for(kb, guide_key, player, with_alternatives=False):
                m[b["idx"]] = b
        except KeyError:
            pass
        cache[key] = m
    return cache[key]


def step_windows(rows, bmap):
    """rows: obs_step_time rows of one session and guide in time order; bmap: {idx: brief}. Returns visits
    [{"idx", "actual_s", "expected_s", "window": [idx...], "xp_gain", "xp_expected", "deaths", "level"}]."""
    visits = []
    for r in rows:
        if r["t_end"] is None or r["t_start"] is None:
            continue
        a = float(r["t_end"] - r["t_start"])
        if visits and visits[-1]["idx"] == r["idx"]:
            v = visits[-1]
            v["actual_s"] += a
            v["xp_gain"] += r["xp_gain"] or 0
            v["deaths"] += r["deaths"] or 0
            continue
        visits.append({"idx": r["idx"], "actual_s": a, "xp_gain": r["xp_gain"] or 0, "deaths": r["deaths"] or 0,
                       "level": r["level"]})
    route_idx = sorted(bmap)
    prev = None
    for v in visits:
        if prev is not None and v["idx"] > prev:
            win = [i for i in route_idx if prev < i <= v["idx"]]
        else:
            win = [v["idx"]] if v["idx"] in bmap else []
        v["window"] = win
        v["expected_s"] = round(sum(bmap[i]["time_s"] for i in win), 1) if win else None
        v["xp_expected"] = sum(bmap[i]["xp_now"] for i in win) if win else None
        v["actual_s"] = round(v["actual_s"], 1)
        prev = v["idx"] if prev is None else max(prev, v["idx"])
    return visits


def _player(kb, session_id, player=None):
    sess = kb.one("SELECT * FROM obs_session WHERE id=?", session_id) or {}
    p = dict(player or {})
    p.setdefault("class", (sess.get("class") or "WARRIOR").upper())
    if sess.get("race") and not p.get("race"):
        p["race"] = sess["race"]
    p.setdefault("faction", sess.get("faction") or "Alliance")
    return p, sess


def review_session(kb, session_id, player=None, top=5):
    kb = as_kb(kb)
    p, sess = _player(kb, session_id, player)
    rows = kb.q("""SELECT guide_key, idx, t_start, t_end, level, xp_gain, deaths FROM obs_step_time
                   WHERE session=? ORDER BY t_start, idx""", session_id)
    cache, visits = {}, []
    by_guide = []
    for r in rows:
        if not by_guide or by_guide[-1][0] != r["guide_key"]:
            by_guide.append((r["guide_key"], []))
        by_guide[-1][1].append(r)
    for gk, grows in by_guide:
        bmap = _brief_map(kb, gk, p, cache)
        for v in step_windows(grows, bmap):
            v["guide_key"] = gk
            b = bmap.get(v["idx"])
            v["kind"] = b.get("kind") if b else None
            if b:
                v["quests"] = [q.get("name") for q in (b.get("justification") or {}).get("quests", {}).values()][:3]
            v["delta_s"] = round(v["actual_s"] - v["expected_s"], 1) if v["expected_s"] is not None else None
            visits.append(v)
    matched = [v for v in visits if v["expected_s"] is not None]
    act = sum(v["actual_s"] for v in visits)
    act_m = sum(v["actual_s"] for v in matched)
    exp_m = sum(v["expected_s"] for v in matched)
    xp_act = sum(v["xp_gain"] for v in visits)
    xp_exp = sum((v["xp_expected"] or 0) for v in matched)
    deaths = sum(v["deaths"] for v in visits)
    drows = kb.q("SELECT t, level, corpse_run_s, killer FROM obs_death WHERE session=?", session_id)
    sinks = sorted(matched, key=lambda v: (-(v["delta_s"] or 0), v["idx"]))[:top]
    facts = []
    if matched:
        facts.append("%d step visits, %d matched to the model: %.1f min played vs %.1f min modelled (x%.2f)"
                     % (len(visits), len(matched), act_m / 60.0, exp_m / 60.0, (act_m / exp_m) if exp_m else 0.0))
    for v in sinks:
        if (v["delta_s"] or 0) <= 0:
            continue
        what = v["kind"] or "step"
        if v.get("quests"):
            what += " (%s)" % ", ".join(q for q in v["quests"] if q)
        span = "" if len(v["window"]) <= 1 else " (covers steps %s-%s)" % (v["window"][0], v["window"][-1])
        facts.append("step %s %s: %.1f min vs %.1f expected (+%.1f min)%s" % (
            v["idx"], what, v["actual_s"] / 60.0, v["expected_s"] / 60.0, v["delta_s"] / 60.0, span))
    xph_act = xp_act / (act / 3600.0) if act > 0 else None
    xph_mod = xp_exp / (exp_m / 3600.0) if exp_m > 0 else None
    if xph_act is not None:
        facts.append("XP/hour %d actual vs %s modelled" % (xph_act, int(xph_mod) if xph_mod else "n/a"))
    n_deaths = max(deaths, len(drows))
    if n_deaths:
        cr = [d["corpse_run_s"] for d in drows if d.get("corpse_run_s")]
        facts.append("%d death(s)%s" % (n_deaths, ", corpse runs %.0f s total" % sum(cr) if cr else ""))
    return {"session": session_id, "class": p["class"], "guides": [g for g, _ in by_guide], "steps": visits,
            "totals": {"actual_s": round(act, 1), "matched_actual_s": round(act_m, 1),
                       "expected_s": round(exp_m, 1), "ratio": round(act_m / exp_m, 3) if exp_m else None,
                       "xp_actual": xp_act, "xp_expected": xp_exp,
                       "xp_per_hour_actual": int(xph_act) if xph_act else None,
                       "xp_per_hour_model": int(xph_mod) if xph_mod else None, "deaths": n_deaths},
            "time_sinks": sinks, "facts": facts}


def calibration(kb, data=None, min_windows=10, prior_h=None):
    """Per-class model time scale from recorded visits: ratio of summed actual to summed modelled seconds over
    matched windows (an AFK-like visit is capped at 4x its window + 5 min), shrunk toward 1.0 with a prior
    worth `prior_h` hours of play (analyzer.calib.prior_h): factor = (h*ratio + prior_h) / (h + prior_h)."""
    kb = as_kb(kb)
    prior_h = float(AP.get("analyzer.calib.prior_h") if prior_h is None else prior_h)
    rows = kb.q("""SELECT o.session, o.guide_key, o.idx, o.t_start, o.t_end, o.level, o.xp_gain, o.deaths,
                   s.class, s.race, s.faction FROM obs_step_time o JOIN obs_session s ON s.id = o.session
                   ORDER BY o.session, o.t_start, o.idx""")
    groups = {}
    for r in rows:
        if not r.get("class") or r["guide_key"] is None:
            continue
        groups.setdefault((r["session"], r["guide_key"]), []).append(r)
    acc, cache = {}, {}
    for (_sess, gk), grows in groups.items():
        r0 = grows[0]
        p = {"class": r0["class"].upper(), "race": r0["race"], "faction": r0["faction"] or "Alliance"}
        try:
            bmap = _brief_map(kb, gk, p, cache)
        except Exception:  # noqa: BLE001 - a guide that cannot be analysed is left out
            continue
        for v in step_windows(grows, bmap):
            if not v["expected_s"] or v["expected_s"] < 5:
                continue
            a = min(v["actual_s"], 4 * v["expected_s"] + 300)
            e = acc.setdefault(p["class"], [0.0, 0.0, 0])
            e[0] += a
            e[1] += v["expected_s"]
            e[2] += 1
    out = {}
    for cls, (a, e, n) in acc.items():
        if n < min_windows or e <= 0:
            continue
        ratio = a / e
        h = a / 3600.0
        factor = (h * ratio + prior_h) / (h + prior_h)
        out[cls] = {"actual_s": round(a, 1), "expected_s": round(e, 1), "windows": n, "ratio": round(ratio, 3),
                    "factor": round(max(0.6, min(1.6, factor)), 3)}
    return out
