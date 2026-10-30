"""The runtime API for the coach: cached routes and briefs (stored by the build, else computed), the live state
compared with the plan, and the step facts the heuristics engine reads."""
from knowledge import store
from knowledge.analyzer.kbdata import as_kb, get_data
from knowledge.analyzer.model import LEVEL_XP, MAX_LEVEL, level_f
from knowledge.analyzer.route.briefs import analyze, public
from knowledge.analyzer.route.steps import Route, norm_player, settings_key
from knowledge.mechanics import xp as MX
from knowledge.vocab import glide_race


_ROUTE_CACHE = {}
# The RestedXP options load() builds the stored briefs with: RestedXP's own defaults (norm_player's for anything
# unset: SSF, group quests and dungeons off; a Dungeon guide's `.enabledungeon` lines still turn its dungeons on).
# Recorded in meta BRIEF_RXP_META; "rule": 2 marks a build since R18. A KB built before that numbered Dungeon-group
# guides with group quests and every dungeon on (LEGACY_DUNGEON_GROUP), the rest with these.
BRIEF_RXP = {"hardcore": False, "rule": 2}
BRIEF_RXP_META = "step_brief_rxp"
LEGACY_DUNGEON_GROUP = {"group_quests": True, "dungeons": True}


def get_route(kb, guide_key, player):
    data = get_data(kb)
    g = data.guide_rows.get(guide_key)
    p = norm_player(player, g)
    # every option that changes RestedXP's step numbering is in the key: two players differing only in SSF must
    # never share a route (the first one cached would number the other's steps)
    key = (id(data), guide_key, p["class"], p["race"], p["faction"]) + settings_key(p, g)
    r = _ROUTE_CACHE.get(key)
    if r is None:
        if len(_ROUTE_CACHE) > 64:
            _ROUTE_CACHE.clear()
        r = _ROUTE_CACHE[key] = Route(kb, guide_key, p, data=data)
    return r


_BRIEF_CACHE = {}
_BRIEF_RXP_CACHE = {}


def clear_caches():
    """Forget every cached route and computed brief (a build replaces the knowledge they came from)."""
    _ROUTE_CACHE.clear()
    _BRIEF_CACHE.clear()
    _BRIEF_RXP_CACHE.clear()


def brief_settings(kb, guide=None):
    """The RestedXP options the stored briefs (of `guide`, a guide row) were built with: meta step_brief_rxp, else
    the pre-R1 build's (BRIEF_RXP without "rule"), cached; a pre-R18 build's Dungeon-group guides with
    LEGACY_DUNGEON_GROUP on top."""
    data = get_data(kb)
    v = _BRIEF_RXP_CACHE.get(id(data))
    if v is None:
        v = store.jload(as_kb(kb).meta(BRIEF_RXP_META), None)
        if not isinstance(v, dict):
            v = {"hardcore": False}
        if len(_BRIEF_RXP_CACHE) > 8:
            _BRIEF_RXP_CACHE.clear()
        _BRIEF_RXP_CACHE[id(data)] = v
    if (v.get("rule") or 1) < 2 and "Dungeon" in ((guide or {}).get("grp") or ""):
        return dict(v, **LEGACY_DUNGEON_GROUP)
    return v


def stored_matches(kb, guide_key, player):
    """Were the stored briefs of this guide built with the player's RestedXP options? Only then do their step
    numbers, their step set and their plan match what the player's RestedXP shows."""
    g = get_data(kb).guide_rows.get(guide_key)
    return settings_key(player, g) == settings_key(brief_settings(kb, g), g)


def split_level(level_f_value):
    """A fractional level -> (level, XP into it)."""
    lv = max(1, min(MAX_LEVEL, int(level_f_value)))
    lx = LEVEL_XP[lv] if lv < MAX_LEVEL else 0
    return lv, int(round((level_f_value - lv) * lx)) if lx else 0



def guide_start(kb, guide_key, cls):
    """(level, xp) the stored analysis started this guide from (chained by load()), or None."""
    r = as_kb(kb).one("SELECT start_level FROM guide_brief WHERE guide_key=? AND class=?", guide_key,
                      (cls or "").upper())
    return split_level(r["start_level"]) if r and r.get("start_level") else None


def briefs_for(kb, guide_key, player, with_alternatives=True, start=None):
    """All briefs of a guide for a player (computed, cached per process). The start state is `start`
    ((level, xp)), else the one load() used for this class (guide_brief), else the guide's level_min."""
    route = get_route(kb, guide_key, player)
    if start is None:
        start = guide_start(kb, guide_key, route.player["class"])
    key = (id(route), with_alternatives, tuple(start) if start else None)
    b = _BRIEF_CACHE.get(key)
    if b is None:
        if len(_BRIEF_CACHE) > 32:
            _BRIEF_CACHE.clear()
        lv, xp = start if start else (None, 0)
        b = _BRIEF_CACHE[key] = analyze(kb, route, start_level=lv, start_xp=xp, with_alternatives=with_alternatives)
    return b


def stored_brief(kb, guide_key, idx, cls, player=None):
    """The brief stored by load() (None when absent, or when `player` is given and plays with other RestedXP
    options than the build used: then its numbering and plan are not the player's)."""
    kb = as_kb(kb)
    if player is not None and not stored_matches(kb, guide_key, player):
        return None
    r = kb.one("SELECT * FROM step_brief WHERE guide_key=? AND idx=? AND class=?", guide_key, idx, (cls or "").upper())
    if not r:
        return None
    j = store.jload
    return {"guide_key": guide_key, "idx": idx, "rxp_step_no": r.get("rxp_step_no"), "class": r["class"],
            "kind": r.get("kind"), "exp_level_before": r["exp_level"], "exp_level_after": r.get("exp_level_after"),
            "xp_now": r["xp_now"], "xp_unlocked": r["xp_unlocked"], "time_s": r["time_s"],
            "travel_s": r["travel_s"], "kill_s": r["kill_s"], "xp_per_min": r["xp_per_min"], "risk": r["risk"],
            "justification": j(r["justification"], {}), "alternatives": j(r["alternatives"], []),
            "deps": j(r["deps"], {}), "facts": j(r.get("facts"), []), "_computed": j(r["computed"], {}),
            "_race": r.get("race")}


def why(kb, guide_key, idx, player, live_state=None):
    """Brief for one step (stored one when it matches the player's class, race and RestedXP options, else
    computed), enriched with the live state: {"level", "xp"[, "quests_complete": [ids]]}. None for a step the
    player's RestedXP hides."""
    kb = as_kb(kb)
    p = norm_player(player)
    b = stored_brief(kb, guide_key, idx, p["class"], player)
    if b is not None and player.get("race") and b.get("_race") and \
            (b["_race"] or "").lower() != str(glide_race(player.get("race"))).lower():
        b = None
    if b is None:
        for x in briefs_for(kb, guide_key, player):
            if x["idx"] == idx:
                b = public(x)
                break
    if b is None:
        return None
    b = {k: v for k, v in b.items() if not k.startswith("_")}
    if live_state:
        b["live"] = live_compare(kb, b, live_state)
    return b


def live_compare(kb, b, live):
    """Plain facts comparing the live character with the plan at this step."""
    lv = int(live.get("level") or 0)
    xp = int(live.get("xp") or 0)
    actual = level_f(lv, xp) if lv else None
    planned = b.get("exp_level_before")
    out = {"level": lv, "exp_level": actual, "planned_level": planned, "level_delta": None, "quests": [], "notes": []}
    if actual is None or planned is None:
        return out
    delta = round(actual - planned, 2)
    out["level_delta"] = delta
    if abs(delta) >= 0.5:
        out["notes"].append("you are %.1f levels %s the plan at this step" % (abs(delta), "above" if delta > 0 else "below"))
    for qid, q in (b.get("justification") or {}).get("quests", {}).items():
        full, ql = q.get("xp_full"), q.get("level")
        if full is None:
            continue
        now = MX.quest_xp(full, ql, lv) or 0
        planned_xp = q.get("xp_at_level")
        e = {"quest_id": int(qid), "name": q.get("name"), "planned_xp": planned_xp, "xp_now": now,
             "level_gap": (lv - ql) if ql is not None else None}
        out["quests"].append(e)
        if planned_xp is not None and now != planned_xp:
            out["notes"].append("you are %d level%s %s the plan: %s now gives %d XP instead of %d"
                                % (abs(round(delta)) or 1, "" if abs(round(delta)) == 1 else "s",
                                   "above" if delta > 0 else "below", q.get("name") or qid, now, planned_xp))
        dl = q.get("decay_at_level")
        if dl and lv < dl <= lv + 1:
            out["notes"].append("%s loses XP at level %d (turn it in before dinging)" % (q.get("name") or qid, dl))
    lx = LEVEL_XP[lv] if 0 < lv < MAX_LEVEL else 0
    pend = live.get("quests_complete")
    if pend:
        tot = pending_turnin_xp(kb, b["guide_key"], b["idx"], pend, lv)
        out["pending_turnin_xp"] = tot
        if lx and tot >= lx - xp:
            out["notes"].append("completed quests in the log (%d XP) are enough to level" % tot)
    return out


def expected_level_at(kb, guide_key, idx, cls, player=None):
    """Planned fractional level when step idx starts (stored brief, else computed with the default race, or for
    `player` when given)."""
    b = stored_brief(kb, guide_key, idx, cls, player)
    if b is not None:
        return b["exp_level_before"]
    for x in briefs_for(kb, guide_key, dict(player or {}, **{"class": cls}), with_alternatives=False):
        if x["idx"] >= idx:
            return x["exp_level_before"]
    return None


def step_xp_per_min(kb, guide_key, idx, cls, player=None):
    b = stored_brief(kb, guide_key, idx, cls, player)
    if b is not None:
        return b["xp_per_min"]
    for x in briefs_for(kb, guide_key, dict(player or {}, **{"class": cls}), with_alternatives=False):
        if x["idx"] == idx:
            return x["xp_per_min"]
    return None


def pending_turnin_xp(kb, guide_key, idx, quest_ids_complete, level):
    """XP of the given completed quests if turned in now at `level` (int; quests with unknown XP count 0)."""
    data = get_data(kb)
    tot = 0
    for q in quest_ids_complete or ():
        qr = data.quest(int(q))
        if qr and qr.get("xp"):
            tot += MX.quest_xp(qr["xp"], qr.get("level"), int(level)) or 0
    return int(tot)


def turnin_level_gap(kb, quest_ids_complete, level, guide_key=None, idx=None):
    """Largest (level - quest level) among the completed quests (None when no level is known)."""
    data = get_data(kb)
    gaps = []
    for q in quest_ids_complete or ():
        qr = data.quest(int(q))
        if qr and qr.get("level") is not None:
            gaps.append(int(level) - int(qr["level"]))
    return max(gaps) if gaps else None


def step_quest_type(kb, guide_key, idx, player=None):
    """Type of the step's main quest (turn-in first, then objective, then accept); None when no quest."""
    data = get_data(kb)
    p = player if isinstance(player, dict) else {"class": player or "WARRIOR"}
    route = get_route(kb, guide_key, p)
    st = route.by_idx.get(idx)
    if st is None:
        g = data.guide(guide_key)
        raw = next((s for s in (g or {}).get("steps", []) if s["idx"] == idx), None)
        qs = [a.get("quest_id") for a in (raw or {}).get("actions", []) if a.get("quest_id")]
    else:
        q = st["quests"]
        qs = q["turnin"] + q["objective"] + q["accept"]
    for q in qs:
        qr = data.quest(q)
        if qr:
            t = qr.get("type")
            if t == "normal" and qr.get("suggested_group"):
                return "group"
            return t
    return None


def state_fields(kb, guide_key, idx, player, level=None, quests_complete=None, xp_per_min=None):
    """The heuristics-engine STATE_FIELDS this lane supplies for the current step (None when unknown):
    step_quest_type, step_quest_level_gap, step_xp_per_min, step_xp_ratio, step_flags, pending_turnin_xp,
    turnin_level_gap. `level` = live level; `quests_complete` = completed quest ids in the log;
    `xp_per_min` = the player's current XP/min (for step_xp_ratio)."""
    data = get_data(kb)
    p = player if isinstance(player, dict) else {"class": player or "WARRIOR"}
    route = get_route(kb, guide_key, p)
    st = route.by_idx.get(idx)
    out = {"step_quest_type": step_quest_type(kb, guide_key, idx, p), "step_quest_level_gap": None,
           "step_xp_per_min": step_xp_per_min(kb, guide_key, idx, route.player["class"], p), "step_xp_ratio": None,
           "step_flags": [], "pending_turnin_xp": None, "turnin_level_gap": None}
    if st is not None:
        f = st["flags"]
        out["step_flags"] = [k for k in ("optional", "sticky", "completewith", "hardcore", "softcore", "loop")
                             if f.get(k)]
        if level is not None:
            q = st["quests"]
            for qid in q["turnin"] + q["objective"] + q["accept"]:
                qr = data.quest(qid)
                if qr and qr.get("level") is not None:
                    out["step_quest_level_gap"] = int(level) - int(qr["level"])
                    break
    if out["step_xp_per_min"] and xp_per_min:
        out["step_xp_ratio"] = round(out["step_xp_per_min"] / float(xp_per_min), 2)
    if level is not None and quests_complete:
        out["pending_turnin_xp"] = pending_turnin_xp(kb, guide_key, idx, quests_complete, level)
        out["turnin_level_gap"] = turnin_level_gap(kb, quests_complete, level)
    return out


def brief_for_rxp_step(kb, guide_key, rxp_step_no, player):
    """Brief for RestedXP's live step number (core.st) for this player."""
    route = get_route(kb, guide_key, player)
    idx = route.rxp_to_idx.get(int(rxp_step_no))
    if idx is None:
        return None
    b = why(kb, guide_key, idx, player)
    if b is None:                                   # numbered step hidden at runtime (e.g. hardcore-only)
        return {"guide_key": guide_key, "idx": idx, "rxp_step_no": int(rxp_step_no), "class": route.player["class"],
                "kind": "other", "hidden": True}
    return b
