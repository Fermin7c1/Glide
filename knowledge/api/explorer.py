"""Per-guide facts for the Guides page: what the knowledge base knows about one RestedXP guide, read-only.

  newest_kb(paths)                              -> (store.KB or None, reason or None)
  guide_facts(kb, guide, cls=None, race=None, faction=None, icons=None) -> dict
  entity_names(kb, quests=(), npcs=(), items=(), objects=()) -> {"quests", "npcs", "items", "objects"}

guide is a parsed guide (knowledge.rxp_parser.parse_guide, after include expansion when the caller did it; a "text" key
with the guide's source lets kb match it by text when the key differs). kb may be
None: the answer then holds what the parse alone gives (each quest's steps, the prep list) and says why the rest is
missing. Nothing here writes and nothing raises; a part that fails is left out and named in "errors".

The answer (every step number is the guide's own idx, the one the Guides page shows):
  kb       {"ok", "reason", "built", "schema"}
  match    "exact" (kb holds this guide's very steps) | "aligned" (another version of it: steps matched by text and
           quests; plan figures are approximate) | "none" (kb has no such guide yet)
  key      the kb guide key matched, or None; "by": "key" | "text" | "name"
  idx_map  {idx: kb idx} for the steps kb knows ({} when match is "none")
  briefs   [{class, race, start_level, end_level, time_s, xp_total}] (the analysed route per class)
  quests   {qid: {"steps": {"accept", "objective", "turnin", "gate": [idx]}, and with kb: name, level, min_level,
           xp, type, suggested_group, removed, hidden, next, prev, requires, requires_one_of, breadcrumbs, exclusive,
           chain {id, pos, n}, decay_at_level, lost_if_skipped [{id, name, accept (idx or None), next_guide}]}}
  targets  {idx: [{kind npc|object|poi, id, name, level, rank, role kill|loot|use|talk|area, quest, obj, item (the
           item looted from it, or None), ui_map, points [[x, y]], groups [[[x, y]]]}]} for steps with objective work
           (map percent; near the step)
  prep     [{kind buy|ah|bank_out|bank_in, item_id, name, qty, idx, quest_id, cond [step, action], for_idx,
           for_guide, price, vendors [{id, name, subname, price, ui_map, x, y}]}] in step order
  plan     {"class", "race", "approx", "steps": {idx: {n, t0, time_s, travel_s, kill_s, xp_now, xp_unlocked, lvl0,
           lvl1, kind, risk, skip, why [text], leg {mode, dist_yd, from_idx, observed}, quests_xp {qid: xp}}}}
           (None without a class or a kb match); n is RestedXP's step number for that class (and race when given)
  hazards  {idx: [{name, creature_id, level, note, elite, dist_yd, why risk|kill|near|quest}]}
  icons    {"item:<id>" | "spell:<id>" | "npc:<id>" | "role:<name>": FileDataID} of the game icons the player's
           client has for what the guide names (icons: a wowfiles.target_icons.IconBook; {} without one): an item's
           own icon, a trained spell's, a creature's family or type icon (kb creature.type / family), and the role
           and quest marks (role:vendor, trainer, flight, innkeeper, quest_available, quest_turnin, elite, ...)
  errors   [text] of the parts that could not be read
"""
import difflib
import json
import os
import re

from knowledge import store
from knowledge.api._layer import R, decay_level, get_data

# --------------------------------------------------------------------------------------------------- verb roles
ACCEPT_VERBS = ("accept", "acceptmultiple", "daily")
TURNIN_VERBS = ("turnin", "turninmultiple", "dailyturnin")
OBJECTIVE_VERBS = ("complete", "collect", "buy", "buyAll", "buyUntilBroke", "addquestitem", "questitemcount",
                   "questgoto", "questwaypoint")
GATE_VERBS = ("isOnQuest", "isNotOnQuest", "isQuestComplete", "isQuestNotComplete", "isQuestTurnedIn",
              "isQuestAvailable", "skipOnQuest", "questcount", "abandon")
BUY_VERBS = ("buy", "buyAll", "buyUntilBroke")
UNIT_VERBS = ("mob", "target", "unitscan")
POS_VERBS = ("goto", "groundgoto", "flygoto", "waypoint", "pin", "questgoto", "questwaypoint")
HAZARD_QUEST_TYPES = ("elite", "group", "dungeon", "escort", "timed", "raid")

# map targets: kept within NEAR_YD of the step's own points, thinned to a GRID_PCT grid, at most MAX_POINTS per target
# and MAX_TARGETS per step; points closer than GROUP_YD form one group (the page outlines each)
NEAR_YD = 300.0
GRID_PCT = 1.0
MAX_POINTS = 40
MAX_TARGETS = 6
GROUP_YD = 60.0
RISK_RADIUS_YD = 150                 # knowledge/analyzer/params.py analyzer.risk.radius_yd, the brief's own radius
CHUNK = 900                          # SQLite's parameter limit is 999


def _role(verb):
    if verb in ACCEPT_VERBS:
        return "accept"
    if verb in TURNIN_VERBS:
        return "turnin"
    if verb in OBJECTIVE_VERBS:
        return "objective"
    if verb in GATE_VERBS:
        return "gate"
    return None


def _ints(vals):
    out = []
    for v in vals or ():
        if isinstance(v, bool):
            continue
        if isinstance(v, int) and v > 0 and v not in out:
            out.append(v)
    return out


def _action_quests(a):
    """Every quest an action names: quest_id and a multi-quest verb's quest_ids."""
    args = a.get("args") or {}
    return _ints([a.get("quest_id")] + list(args.get("quest_ids") or ()))


def _q(kb, sql, ids, *pre):
    """kb.q with an IN (...) list of ids, chunked under SQLite's parameter limit (sql holds one {in})."""
    ids = list(ids)
    out = []
    for i in range(0, len(ids), CHUNK):
        part = ids[i:i + CHUNK]
        out += kb.q(sql.format(**{"in": ",".join("?" * len(part))}), *pre, *part)
    return out


def _jl(text, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------------------------------- the kb
def newest_kb(paths):
    """The newest readable kb.db of `paths` (data/kb.db and the refresh's staged data/kb.next-*.db, which becomes
    kb.db only at the next engine start) as a store.KB, or (None, reason). A kb built with another schema version
    is refused: its columns may mean other things. The caller closes the KB it gets (KB.close)."""
    cands = []
    for p in paths or ():
        try:
            cands.append((os.path.getmtime(p), p))
        except (OSError, TypeError):
            continue
    if not cands:
        return None, "no knowledge base yet"
    reason = None
    for _, p in sorted(cands, reverse=True):
        kb = store.open_ro(p)
        try:
            v = store.schema_version(kb)
        except Exception as e:  # noqa: BLE001 - a damaged file: try the next one
            reason = "knowledge base unreadable (%s)" % type(e).__name__
            kb.close()
            continue
        if v != store.SCHEMA_VERSION:
            reason = "knowledge base has schema %s, this version reads %s" % (v, store.SCHEMA_VERSION)
            kb.close()                      # refused: don't keep the file open (Windows can't replace it then)
            continue
        return kb, None
    return None, reason


def entity_names(kb, quests=(), npcs=(), items=(), objects=()):
    """{"quests": {id: name}, "npcs", "items", "objects"} from kb (names it has; never raises)."""
    out = {"quests": {}, "npcs": {}, "items": {}, "objects": {}}
    if kb is None or not getattr(kb, "ok", False):
        return out
    for bucket, table, ids in (("quests", "quest", quests), ("npcs", "creature", npcs), ("items", "item", items),
                               ("objects", "gameobject", objects)):
        ids = _ints(ids)
        if not ids:
            continue
        try:
            for r in _q(kb, "SELECT id, name FROM %s WHERE id IN ({in}) AND name IS NOT NULL" % table, ids):
                out[bucket][int(r["id"])] = r["name"]
        except Exception:  # noqa: BLE001 - names are a nicety
            continue
    return out


# --------------------------------------------------------------------------------------------------- the match
def _signature(st):
    qs = []
    for a in st.get("actions") or ():
        if a.get("verb") in ("accept", "turnin", "complete"):
            qs += _action_quests(a)
    return "%s|%s" % (st.get("text") or "", ",".join(str(q) for q in qs))


def _kb_signatures(kb, key):
    steps = kb.q("SELECT idx, text FROM rxp_step WHERE guide_key=? ORDER BY idx", key)
    qs = {}
    for r in kb.q("SELECT idx, quest_id, args FROM rxp_action WHERE guide_key=? AND verb IN "
                  "('accept','turnin','complete') ORDER BY idx, line", key):
        ids = [r["quest_id"]] + list((_jl(r["args"], {}) or {}).get("quest_ids") or ())
        qs.setdefault(r["idx"], []).extend(_ints(ids))
    return steps, {s["idx"]: "%s|%s" % (s["text"] or "", ",".join(str(q) for q in qs.get(s["idx"], ())))
                   for s in steps}


def match_guide(kb, guide):
    """(match, key, by, idx_map): which kb guide this is and how its steps line up (see the module docstring)."""
    steps = guide.get("steps") or ()
    row, by = None, None
    if guide.get("key"):
        row, by = kb.one("SELECT key, text FROM rxp_guide WHERE key=?", guide["key"]), "key"
    if row is None and guide.get("text"):
        row, by = kb.one("SELECT key, text FROM rxp_guide WHERE text=?", guide["text"]), "text"
    if row is None and guide.get("name"):
        rows = kb.q("SELECT key, text FROM rxp_guide WHERE name=?", guide["name"])
        grp = guide.get("grp") or ""
        rows.sort(key=lambda r: (not str(r["key"]).startswith(grp + "||"), r["key"]))
        row, by = (rows[0] if rows else None), "name"
    if row is None:
        return "none", None, None, {}
    key = row["key"]
    kb_steps, kb_sig = _kb_signatures(kb, key)
    if [s["text"] for s in kb_steps] == [s.get("text") for s in steps] and \
            [s["idx"] for s in kb_steps] == [s.get("idx") for s in steps]:
        return "exact", key, by, {s["idx"]: s["idx"] for s in steps}
    mine = [_signature(s) for s in steps]
    theirs = [kb_sig[s["idx"]] for s in kb_steps]
    sm = difflib.SequenceMatcher(None, mine, theirs, autojunk=False)
    idx_map = {}
    for i, j, n in sm.get_matching_blocks():
        for k in range(n):
            idx_map[steps[i + k]["idx"]] = kb_steps[j + k]["idx"]
    return "aligned", key, by, idx_map


# --------------------------------------------------------------------------------------------------- quests
def quest_steps(guide):
    """{qid: {"accept", "objective", "turnin", "gate": [idx]}} from the parsed actions (no kb needed)."""
    out = {}
    for st in guide.get("steps") or ():
        for a in st.get("actions") or ():
            role = _role(a.get("verb"))
            if role is None:
                continue
            if a.get("verb") == "collect" and not a.get("quest_id"):
                continue                                   # a plain collect is prep, not quest work
            for q in _action_quests(a):
                lst = out.setdefault(q, {"accept": [], "objective": [], "turnin": [], "gate": []})[role]
                if st["idx"] not in lst:
                    lst.append(st["idx"])
    return out


def _next_keys(guide):
    keys = []
    for n in guide.get("next_list") or ():
        if n.get("name"):
            keys.append("%s||%s" % (n.get("group") or guide.get("grp") or "", n["name"]))
    return keys


def _quests_kb(kb, guide, quests):
    qids = sorted(quests)
    if not qids:
        return
    rows = {r["id"]: r for r in _q(kb, "SELECT id, name, level, min_level, xp, xp_src, type, suggested_group, "
                                       "next_quest, removed, hidden FROM quest WHERE id IN ({in})", qids)}
    prev = {}
    for r in _q(kb, "SELECT id, next_quest FROM quest WHERE next_quest IN ({in})", qids):
        prev.setdefault(r["next_quest"], []).append(r["id"])
    pre = {}
    pre_rows = _q(kb, "SELECT quest_id, pre_id, kind FROM quest_prereq WHERE quest_id IN ({in})", qids)
    pre_rows += _q(kb, "SELECT quest_id, pre_id, kind FROM quest_prereq WHERE pre_id IN ({in})", qids)
    seen = set()
    for r in pre_rows:
        k = (r["quest_id"], r["pre_id"], r["kind"])
        if k in seen:
            continue
        seen.add(k)
        pre.setdefault(r["quest_id"], []).append(r)
        if r["kind"] == "exclusive":
            pre.setdefault(r["pre_id"], []).append({"quest_id": r["pre_id"], "pre_id": r["quest_id"],
                                                    "kind": "exclusive"})
    chains = {}
    for r in _q(kb, "SELECT c.quest_id, c.chain_id, c.pos, (SELECT COUNT(*) FROM quest_chain c2 "
                    "WHERE c2.chain_id = c.chain_id) n FROM quest_chain c WHERE c.quest_id IN ({in})", qids):
        chains[r["quest_id"]] = {"id": r["chain_id"], "pos": r["pos"], "n": r["n"]}
    for q, rec in quests.items():
        r = rows.get(q)
        if r:
            rec.update(name=r["name"], level=r["level"], min_level=r["min_level"], xp=r["xp"], type=r["type"],
                       suggested_group=r["suggested_group"], removed=bool(r["removed"]), hidden=bool(r["hidden"]),
                       next=r["next_quest"] or None)
            try:
                rec["decay_at_level"] = decay_level(r["xp"], r["level"]) if decay_level else None
            except Exception:  # noqa: BLE001
                rec["decay_at_level"] = None
        rec["prev"] = sorted(prev.get(q, ()))
        ps = pre.get(q, ())
        rec["requires"] = sorted({p["pre_id"] for p in ps if p["kind"] == "pre"})
        rec["requires_one_of"] = sorted({p["pre_id"] for p in ps if p["kind"] == "pregroup"})
        # quest_prereq (q, b, 'breadcrumb'): b leads to q; skipping b blocks nothing
        rec["breadcrumbs"] = sorted({p["pre_id"] for p in ps if p["kind"] == "breadcrumb"})
        rec["exclusive"] = sorted({p["pre_id"] for p in ps if p["kind"] == "exclusive"})
        rec["unlocks"] = sorted({r2["quest_id"] for r2 in pre_rows if r2["pre_id"] == q
                                 and r2["kind"] in ("pre", "pregroup")} | ({r["next_quest"]} if r and r["next_quest"]
                                                                            else set()))
        if q in chains:
            rec["chain"] = chains[q]


def _lost_if_skipped(kb, guide, quests):
    """quests[q]["lost_if_skipped"]: what can't be done once q is skipped, by the analyzer's own rule (the coach's
    skip_impact), within this guide's quests and the next guides'."""
    if get_data is None or R is None:
        return
    from knowledge.analyzer import simulate as S
    data = get_data(kb)
    here = {q for q, r in quests.items() if r["steps"]["accept"] or r["steps"]["turnin"]}
    scope = set(here)
    nxt = _next_keys(guide)
    later = {}
    if nxt:
        for r in _q(kb, "SELECT DISTINCT quest_id FROM rxp_action WHERE guide_key IN ({in}) AND "
                        "verb IN ('accept','turnin') AND quest_id IS NOT NULL", nxt):
            later[r["quest_id"]] = True
        scope |= set(later)
    names = {}
    missing = [q for q in scope if q not in quests]
    for r in _q(kb, "SELECT id, name FROM quest WHERE id IN ({in})", missing):
        names[r["id"]] = r["name"]
    for q, rec in quests.items():
        if not rec["steps"]["accept"]:
            continue
        lost = sorted(S.unreachable(data, q, scope) - {q})
        rec["lost_if_skipped"] = [{"id": x, "name": quests[x].get("name") if x in quests else names.get(x),
                                   "accept": (quests[x]["steps"]["accept"] or [None])[0] if x in quests else None,
                                   "next_guide": x not in quests or not quests[x]["steps"]["accept"]}
                                  for x in lost]


# --------------------------------------------------------------------------------------------------- targets
def _step_anchors(st):
    """[(ui_map, x, y)] the step's own points: its goto, waypoints and loop/line points (map percent)."""
    out = []
    if st.get("ui_map") and st.get("x") is not None and st.get("y") is not None:
        out.append((st["ui_map"], st["x"], st["y"]))
    for a in st.get("actions") or ():
        args = a.get("args") or {}
        um = args.get("ui_map") or st.get("ui_map")
        if not um:
            continue
        if a.get("verb") in POS_VERBS and args.get("x") is not None and args.get("y") is not None:
            out.append((um, args["x"], args["y"]))
        elif a.get("verb") in ("loop", "line"):
            for p in args.get("points") or ():
                if isinstance(p, list) and len(p) == 2 and None not in p:
                    out.append((um, p[0], p[1]))
    return out


def _yd(size, a, b):
    w, h = size
    return (((a[0] - b[0]) * w / 100.0) ** 2 + ((a[1] - b[1]) * h / 100.0) ** 2) ** 0.5


def _groups(pts, size):
    """Points within GROUP_YD of each other, as groups (single linkage; at most MAX_POINTS points)."""
    left = list(pts)
    out = []
    while left:
        grp, i = [left.pop(0)], 0
        while i < len(grp):
            near = [p for p in left if (_yd(size, grp[i], p) if size else
                                        ((grp[i][0] - p[0]) ** 2 + (grp[i][1] - p[1]) ** 2) ** 0.5 * 30) <= GROUP_YD]
            for p in near:
                left.remove(p)
            grp += near
            i += 1
        out.append([list(p) for p in grp])
    return out


def _place(raw, anchors, sizes):
    """(ui_map, points) for one target: its spawns on the step's maps, near the step, thinned."""
    maps = [a[0] for a in anchors]
    by_map = {}
    for um, x, y in raw:
        if x is not None and y is not None:
            by_map.setdefault(um, []).append((x, y))
    if not by_map:
        return None, []
    on = [m for m in dict.fromkeys(maps) if m in by_map]
    um = on[0] if on else max(by_map, key=lambda m: len(by_map[m]))
    pts = by_map[um]
    mine = [(a[1], a[2]) for a in anchors if a[0] == um]
    size = sizes.get(um)
    if mine and size:
        near = [p for p in pts if min(_yd(size, p, m) for m in mine) <= NEAR_YD]
        if not near:            # none close: the nearest group
            best = min(pts, key=lambda p: min(_yd(size, p, m) for m in mine))
            near = [p for p in pts if _yd(size, p, best) <= NEAR_YD]
        pts = near
    seen, thin = set(), []
    for x, y in sorted(pts, key=lambda p: min((_yd(size, p, m) if size else 0) for m in mine) if mine else 0):
        cell = (round(x / GRID_PCT), round(y / GRID_PCT))
        if cell in seen:
            continue
        seen.add(cell)
        thin.append((round(x, 2), round(y, 2)))
        if len(thin) >= MAX_POINTS:
            break
    return um, thin


def _targets(kb, guide, errors):
    steps = [st for st in guide.get("steps") or ()
             if any(a.get("verb") in OBJECTIVE_VERBS + UNIT_VERBS for a in st.get("actions") or ())]
    if not steps:
        return {}
    data = get_data(kb) if get_data else None
    qids, items = set(), set()
    for st in steps:
        for a in st.get("actions") or ():
            if a.get("verb") in OBJECTIVE_VERBS:
                qids.update(_action_quests(a))
            if a.get("verb") == "collect" and (a.get("args") or {}).get("item_id"):
                items.add(a["args"]["item_id"])
    objs = {}
    for r in _q(kb, "SELECT quest_id, idx, type, target_kind, target_id, amount, text, drop_from_kind, drop_from_id "
                    "FROM quest_objective WHERE quest_id IN ({in})", sorted(qids)):
        objs.setdefault(r["quest_id"], []).append(r)
        if r["target_kind"] == "item" and r["target_id"]:
            items.add(r["target_id"])
    drops = {}
    for r in _q(kb, "SELECT creature_id, item_id, rate FROM creature_drop WHERE item_id IN ({in}) "
                    "ORDER BY rate DESC", sorted(items)):
        drops.setdefault(r["item_id"], []).append(("npc", r["creature_id"]))
    for r in _q(kb, "SELECT object_id, item_id, rate FROM object_loot WHERE item_id IN ({in}) "
                    "ORDER BY rate DESC", sorted(items)):
        drops.setdefault(r["item_id"], []).append(("object", r["object_id"]))
    pois = {}
    for r in _q(kb, "SELECT quest_id, role, ui_map, x, y FROM quest_poi WHERE quest_id IN ({in}) AND x IS NOT NULL",
                sorted(qids)):
        pois.setdefault(r["quest_id"], []).append(r)

    # what each step asks for: (kind, id, role, quest, obj)
    wants = {}
    for st in steps:
        anchors = _step_anchors(st)
        um0 = anchors[0][0] if anchors else st.get("ui_map")
        lst = []

        def add(kind, eid, role, q=None, o=None, item=None, lst=lst):
            if eid and not any(w[0] == kind and w[1] == eid for w in lst):
                lst.append((kind, eid, role, q, o, item))
        for a in st.get("actions") or ():
            v, args = a.get("verb"), a.get("args") or {}
            if v == "complete" and a.get("quest_id") and args.get("obj"):
                o = next((r for r in objs.get(a["quest_id"], ()) if r["idx"] == args["obj"]), None)
                if o:
                    _obj_targets(o, a["quest_id"], args["obj"], drops, add)
                else:
                    add("poi", a["quest_id"], "area", a["quest_id"], args["obj"])
            elif v == "collect" and args.get("item_id") and a.get("quest_id"):
                o = next((r for r in objs.get(a["quest_id"], ()) if r["target_kind"] == "item"
                          and r["target_id"] == args["item_id"]), None)
                if o:
                    _obj_targets(o, a["quest_id"], o["idx"], drops, add)
                else:
                    for kind, eid in drops.get(args["item_id"], ())[:3]:
                        add(kind, eid, "loot", a["quest_id"], item=args["item_id"])
            elif v in UNIT_VERBS:
                for u in args.get("units") or ():
                    cid = u.get("id")
                    if cid is None and u.get("name") and data is not None:
                        try:
                            cid = data.creature_by_name_on(u["name"], um0)
                        except Exception:  # noqa: BLE001
                            cid = None
                    add("npc", cid, "kill" if v == "mob" else "talk")
        if lst:
            wants[st["idx"]] = (anchors, lst[:MAX_TARGETS])
    npcs = sorted({w[1] for _, lst in wants.values() for w in lst if w[0] == "npc"})
    gobs = sorted({w[1] for _, lst in wants.values() for w in lst if w[0] == "object"})
    maps = sorted({a[0] for anchors, _ in wants.values() for a in anchors})
    sizes = {r["ui_map"]: (r["width_yd"], r["height_yd"]) for r in _q(
        kb, "SELECT ui_map, width_yd, height_yd FROM map WHERE ui_map IN ({in})", maps)
        if r["width_yd"] and r["height_yd"]}
    cre = {r["id"]: r for r in _q(kb, "SELECT id, name, min_level, max_level, rank, friendly_to FROM creature "
                                      "WHERE id IN ({in})", npcs)}
    gob = {r["id"]: r for r in _q(kb, "SELECT id, name FROM gameobject WHERE id IN ({in})", gobs)}
    cspawn, gspawn = {}, {}
    for r in _q(kb, "SELECT creature_id, ui_map, x, y FROM creature_spawn WHERE creature_id IN ({in}) "
                    "AND x IS NOT NULL", npcs):
        cspawn.setdefault(r["creature_id"], []).append((r["ui_map"], r["x"], r["y"]))
    for r in _q(kb, "SELECT object_id, ui_map, x, y FROM gameobject_spawn WHERE object_id IN ({in}) "
                    "AND x IS NOT NULL", gobs):
        gspawn.setdefault(r["object_id"], []).append((r["ui_map"], r["x"], r["y"]))
    out = {}
    for idx, (anchors, lst) in wants.items():
        res = []
        for kind, eid, role, q, o, item in lst:
            if kind == "npc":
                raw = cspawn.get(eid, ())
                c = cre.get(eid) or {}
                rec = {"kind": "npc", "id": eid, "name": c.get("name"), "level": c.get("max_level") or c.get("min_level"),
                       "rank": c.get("rank")}
            elif kind == "object":
                raw = gspawn.get(eid, ())
                rec = {"kind": "object", "id": eid, "name": (gob.get(eid) or {}).get("name"), "level": None,
                       "rank": None}
            else:
                roles = ("objective%s" % o, "objective") if o else ("objective",)
                raw = [(r["ui_map"], r["x"], r["y"]) for r in pois.get(eid, ()) if r["role"] in roles]
                rec = {"kind": "poi", "id": eid, "name": None, "level": None, "rank": None}
            um, pts = _place(raw, anchors, sizes)
            if not pts:
                continue
            rec.update(role=role, quest=q, obj=o, item=item, ui_map=um, points=[list(p) for p in pts],
                       groups=_groups(pts, sizes.get(um)))
            res.append(rec)
        if res:
            out[idx] = res
    return out


def _obj_targets(o, q, obj, drops, add):
    tk, tid = o["target_kind"], o["target_id"]
    if tk == "npc":
        add("npc", tid, "kill" if o["type"] == "kill" else "talk", q, obj)
    elif tk == "object":
        add("object", tid, "use", q, obj)
    elif tk == "item":
        if o["drop_from_kind"] in ("npc", "object") and o["drop_from_id"]:
            add(o["drop_from_kind"], o["drop_from_id"], "loot", q, obj, tid)
        else:
            for kind, eid in drops.get(tid, ())[:3]:
                add(kind, eid, "loot", q, obj, tid)
    add("poi", q, "area", q, obj)


# --------------------------------------------------------------------------------------------------- prep
_BRACKET = re.compile(r"\[([^\]|]+)\]")


def _clean_comment(c):
    if not c:
        return None
    c = re.sub(r"^\s*(collect|buy|purchase)\s+", "", str(c), flags=re.I)
    c = re.sub(r"\s*\(\s*x?\d+\s*\)\s*$", "", c).strip()
    return c or None


def prep_list(guide):
    """The guide's purchases and bank trips in step order (no kb needed); see the module docstring."""
    steps = list(guide.get("steps") or [])
    out = []
    for st in steps:
        verbs = {a.get("verb") for a in st.get("actions") or ()}
        stext = (st.get("text") or "").lower()
        flags = st.get("flags") or {}
        scond = (st.get("cond") or {}).get("raw")
        for a in st.get("actions") or ():
            v, args = a.get("verb"), a.get("args") or {}
            acond = (a.get("cond") or {}).get("raw")
            atext = " ".join(str(x) for x in (a.get("text"), args.get("comment"), args.get("text_raw")) if x).lower()
            kind, ids = None, []
            if v in BUY_VERBS and args.get("item_id"):
                kind, ids = "buy", [args["item_id"]]
            elif v == "collect" and args.get("item_id") and not args.get("profession"):
                if flags.get("ah"):
                    kind = "ah"
                elif "vendor" in verbs or verbs & set(BUY_VERBS) or re.search(r"\b(buy|purchase)", stext + " " + atext):
                    kind = "buy"
                ids = [args["item_id"]]
            elif v in ("bankwithdraw", "bankdeposit"):
                kind = "bank_out" if v == "bankwithdraw" else "bank_in"
                ids = _ints(args.get("items"))
            if not kind:
                continue
            m = _BRACKET.search(args.get("text_raw") or a.get("text") or "")
            for item in ids:
                out.append({"kind": kind, "item_id": item,
                            "name": _clean_comment(args.get("comment")) if len(ids) == 1 else None,
                            "text_name": m.group(1).strip() if m and len(ids) == 1 else None,
                            "qty": args.get("qty") if kind in ("buy", "ah") else None,
                            "idx": st["idx"], "quest_id": a.get("quest_id"), "cond": [scond, acond],
                            "for_idx": _for_idx(steps, st["idx"], item, a.get("quest_id")), "for_guide": None,
                            "price": None, "vendors": []})
    for p in out:
        tn = p.pop("text_name")
        p["name"] = p["name"] or tn
    return out


def _for_idx(steps, idx, item, qid):
    """Where an item is needed: the quest's turn-in later in the guide, else the first later use or equip of it."""
    later = [s for s in steps if s["idx"] > idx]
    if qid:
        for s in later:
            if any(a.get("verb") in TURNIN_VERBS and qid in _action_quests(a) for a in s.get("actions") or ()):
                return s["idx"]
    for s in later:
        for a in s.get("actions") or ():
            args = a.get("args") or {}
            if a.get("verb") in ("use", "equip") and (args.get("item_id") == item or any(
                    isinstance(i, dict) and i.get("id") == item for i in args.get("items") or ())):
                return s["idx"]
    return None


def _prep_kb(kb, guide, prep):
    items = sorted({p["item_id"] for p in prep})
    if not items:
        return
    rows = {r["id"]: r for r in _q(kb, "SELECT id, name, buy_price FROM item WHERE id IN ({in})", items)}
    maps = sorted({s.get("ui_map") for s in guide.get("steps") or () if s.get("ui_map")})
    vend = {}
    if maps:
        for r in _q(kb, "SELECT v.item_id, v.price, c.id, c.name, c.subname, s.ui_map, s.x, s.y FROM vendor_item v "
                        "JOIN creature c ON c.id = v.npc_id JOIN creature_spawn s ON s.creature_id = c.id "
                        "WHERE s.ui_map IN (%s) AND v.item_id IN ({in})" % ",".join(str(int(m)) for m in maps), items):
            lst = vend.setdefault(r["item_id"], [])
            if not any(x["id"] == r["id"] for x in lst):
                lst.append({"id": r["id"], "name": r["name"], "subname": r["subname"], "price": r["price"],
                            "ui_map": r["ui_map"], "x": r["x"], "y": r["y"]})
    steps = {s["idx"]: s for s in guide.get("steps") or ()}
    need_guide = [p for p in prep if p["for_idx"] is None and p["quest_id"]]
    elsewhere = {}
    for r in _q(kb, "SELECT quest_id, guide_key, idx FROM rxp_action WHERE verb = 'turnin' AND quest_id IN ({in}) "
                    "ORDER BY guide_key, idx", sorted({p["quest_id"] for p in need_guide})):
        elsewhere.setdefault(r["quest_id"], r["guide_key"])
    for p in prep:
        r = rows.get(p["item_id"])
        if r and r["name"]:
            p["name"] = r["name"]
        if r and r["buy_price"]:
            p["price"] = r["buy_price"]
        if p["kind"] == "buy":
            st = steps.get(p["idx"]) or {}
            here = (st.get("ui_map"), st.get("x"), st.get("y"))
            vs = vend.get(p["item_id"], [])

            def dist(v, here=here):
                if v["ui_map"] != here[0] or here[1] is None:
                    return 1e9
                return (v["x"] - here[1]) ** 2 + (v["y"] - here[2]) ** 2
            p["vendors"] = sorted(vs, key=dist)[:3]
            if p["price"] is None and p["vendors"]:
                p["price"] = p["vendors"][0]["price"]
        if p["for_idx"] is None and p["quest_id"] in elsewhere:
            p["for_guide"] = elsewhere[p["quest_id"]]


# --------------------------------------------------------------------------------------------------- plan, hazards
def _plan(kb, key, idx_map, match, cls, race, faction):
    back = {}
    for mine, theirs in idx_map.items():
        back.setdefault(theirs, mine)
    rows = kb.q("SELECT idx, rxp_step_no, kind, exp_level, exp_level_after, xp_now, xp_unlocked, time_s, travel_s, "
                "kill_s, xp_per_min, risk, justification, computed, race FROM step_brief WHERE guide_key=? AND class=? "
                "ORDER BY idx", key, cls.upper())
    if not rows:
        return None, {}
    from knowledge.api.reasons import why_text
    steps, just = {}, {}
    for r in rows:
        idx = back.get(r["idx"])
        if idx is None:
            continue
        j = _jl(r["justification"], {}) or {}
        c = _jl(r["computed"], {}) or {}
        tr = j.get("travel") or {}
        leg = None
        if tr:
            leg = {"mode": tr.get("mode") or "walk", "dist_yd": tr.get("dist_yd"),
                   "from_idx": back.get(tr.get("from_idx")) if tr.get("from_idx") is not None else None,
                   "observed": bool(tr.get("observed"))}
        steps[idx] = {"n": r["rxp_step_no"], "t0": c.get("t0"), "time_s": r["time_s"], "travel_s": r["travel_s"],
                      "kill_s": r["kill_s"], "xp_now": r["xp_now"], "xp_unlocked": r["xp_unlocked"],
                      "lvl0": r["exp_level"], "lvl1": r["exp_level_after"], "kind": r["kind"], "risk": r["risk"],
                      "skip": c.get("skip"), "why": [why_text(w) for w in j.get("why") or ()], "leg": leg,
                      "quests_xp": {str(q): v.get("xp_at_level") for q, v in (j.get("quests") or {}).items()
                                    if isinstance(v, dict) and v.get("xp_at_level") is not None}}
        just[idx] = j
    brief_race = next((r["race"] for r in rows if r["race"]), None)
    plan = {"class": cls.upper(), "race": brief_race, "approx": match != "exact", "steps": steps}
    if race and R is not None:
        try:
            route = R.get_route(kb, key, {"class": cls.upper(), "race": race, "faction": faction})
            for kidx, n in route.idx_to_rxp.items():
                idx = back.get(kidx)
                if idx in steps:
                    steps[idx]["n"] = n
            for idx, s in steps.items():
                if idx_map.get(idx) not in route.idx_to_rxp:
                    s["n"] = None                      # this race doesn't see the step
            plan["race"] = race
        except Exception:  # noqa: BLE001 - keep the analysed race's numbers
            pass
    return plan, just


def _hazards(kb, guide, targets, just, quests, faction, errors):
    out = {}

    def put(idx, h):
        lst = out.setdefault(idx, [])
        k = (h.get("creature_id"), h.get("name"), h.get("why"))
        if not any((x.get("creature_id"), x.get("name"), x.get("why")) == k for x in lst):
            lst.append(h)
    for idx, j in just.items():
        for r in j.get("risks") or ():
            put(idx, {"name": r.get("name"), "creature_id": r.get("creature_id"), "level": r.get("level"),
                      "note": r.get("note"), "elite": bool(r.get("elite")), "dist_yd": r.get("dist_yd"), "why": "risk"})
        for k in j.get("kills") or ():
            if (k.get("rank") or 0) in (1, 2, 3):
                put(idx, {"name": k.get("name"), "creature_id": k.get("creature_id"), "level": k.get("level"),
                          "note": {1: "elite", 2: "rare elite", 3: "boss"}.get(k.get("rank")), "elite": True,
                          "dist_yd": None, "why": "kill"})
    for idx, lst in targets.items():
        for t in lst:
            if t["kind"] == "npc" and (t.get("rank") or 0) in (1, 2, 3, 4) and t.get("role") == "kill":
                put(idx, {"name": t.get("name"), "creature_id": t["id"], "level": t.get("level"),
                          "note": {1: "elite", 2: "rare elite", 3: "boss", 4: "rare"}.get(t["rank"]),
                          "elite": t["rank"] in (1, 2, 3), "dist_yd": None, "why": "kill"})
    # steps without a brief: the brief's own rule around the step's position
    if get_data is not None:
        try:
            from knowledge.analyzer.route.briefs import _risks_near     # noqa: PLC0415 - the brief's own rule
            data = get_data(kb)
            lvl = guide.get("level_min") or 1
            for st in guide.get("steps") or ():
                if st["idx"] in just or not st.get("ui_map") or st.get("x") is None or st.get("y") is None:
                    continue
                for r in _risks_near(data, (st["ui_map"], st["x"], st["y"]), RISK_RADIUS_YD, faction, lvl):
                    put(st["idx"], {"name": r.get("name"), "creature_id": r.get("creature_id"),
                                    "level": r.get("level"), "note": r.get("note"), "elite": bool(r.get("elite")),
                                    "dist_yd": r.get("dist_yd"), "why": "near"})
        except Exception as e:  # noqa: BLE001
            errors.append("hazards near steps: %s: %s" % (type(e).__name__, e))
    # RestedXP's dangerous-mob notes on the creatures named
    cids = sorted({h["creature_id"] for lst in out.values() for h in lst if h.get("creature_id")})
    names = sorted({h["name"] for lst in out.values() for h in lst if h.get("name") and not h.get("creature_id")})
    notes = {}
    for r in _q(kb, "SELECT name, creature_id, classification, movement, notes FROM creature_note "
                    "WHERE creature_id IN ({in})", cids):
        notes.setdefault(("c", r["creature_id"]), r)
    for r in _q(kb, "SELECT name, creature_id, classification, movement, notes FROM creature_note "
                    "WHERE creature_id IS NULL AND name IN ({in})", names):
        notes.setdefault(("n", r["name"]), r)
    for lst in out.values():
        for h in lst:
            n = notes.get(("c", h["creature_id"])) if h.get("creature_id") else notes.get(("n", h.get("name")))
            if n:
                parts = [x.strip() for x in (h.get("note") or "").split(" / ") if x.strip()]
                for x in (n["classification"] if n["classification"] not in (None, "Normal") else None,
                          n["movement"], n["notes"]):
                    if x and x.strip().lower() not in {p.lower() for p in parts}:
                        parts.append(x.strip())
                h["note"] = " / ".join(parts) or None
    # quests that are dangerous in themselves, at the steps that do them
    for q, rec in quests.items():
        t = rec.get("type")
        if t in HAZARD_QUEST_TYPES or (rec.get("suggested_group") or 0) > 1:
            note = t if t in HAZARD_QUEST_TYPES else None
            if (rec.get("suggested_group") or 0) > 1:
                note = "%sgroup of %d" % (note + ", " if note else "", rec["suggested_group"])
            for idx in rec["steps"]["objective"] or rec["steps"]["accept"]:
                put(idx, {"name": rec.get("name"), "quest_id": q, "creature_id": None, "level": rec.get("level"),
                          "note": note, "elite": t == "elite", "dist_yd": None, "why": "quest"})
    return out


def _briefs(kb, key):
    out = []
    for r in kb.q("SELECT class, start_level, end_level, time_s, xp_total, notes FROM guide_brief WHERE guide_key=? "
                  "ORDER BY class", key):
        notes = _jl(r.pop("notes"), {}) or {}
        r["race"] = notes.get("race") if isinstance(notes, dict) else None
        out.append(r)
    return out


# --------------------------------------------------------------------------------------------------- the answer
def guide_facts(kb, guide, cls=None, race=None, faction=None, icons=None):
    """Everything kb knows about this parsed guide that changes the next minutes of play (module docstring)."""
    errors = []
    guide = guide if isinstance(guide, dict) else {}
    faction = faction or guide.get("faction")
    out = {"kb": {"ok": False, "reason": None, "built": None, "schema": None}, "match": "none", "key": None,
           "by": None, "idx_map": {}, "briefs": [], "quests": {}, "targets": {}, "prep": [], "plan": None,
           "hazards": {}, "icons": {}, "errors": errors}

    def part(name, fn, *args):
        try:
            return fn(*args)
        except Exception as e:  # noqa: BLE001 - never raises: the part is left out and named
            errors.append("%s: %s: %s" % (name, type(e).__name__, e))
            return None
    out["quests"] = {q: {"steps": s} for q, s in (part("quests", quest_steps, guide) or {}).items()}
    out["prep"] = part("prep", prep_list, guide) or []
    if kb is None or not getattr(kb, "ok", False):
        out["kb"]["reason"] = "no knowledge base yet"
        out["icons"] = part("icons", guide_icons, None, guide, out, icons) or {}
        return _json_keys(out)
    out["kb"].update(ok=True, built=part("kb", kb.meta, "built"), schema=part("kb", store.schema_version, kb))
    m = part("match", match_guide, kb, guide)
    if m:
        out["match"], out["key"], out["by"], idx_map = m
        out["idx_map"] = {str(k): v for k, v in idx_map.items()}
    else:
        idx_map = {}
    if out["match"] == "none":
        out["kb"]["reason"] = "this guide isn't in the knowledge base yet: times and levels come after the next refresh"
    else:
        out["briefs"] = part("briefs", _briefs, kb, out["key"]) or []
    part("quests", _quests_kb, kb, guide, out["quests"])
    part("follow-ups", _lost_if_skipped, kb, guide, out["quests"])
    out["targets"] = part("targets", _targets, kb, guide, errors) or {}
    part("prep", _prep_kb, kb, guide, out["prep"])
    just = {}
    if cls and out["match"] != "none":
        plan = part("plan", _plan, kb, out["key"], idx_map, out["match"], cls, race, faction)
        if plan:
            out["plan"], just = plan
    out["hazards"] = part("hazards", _hazards, kb, guide, out["targets"], just, out["quests"], faction, errors) or {}
    out["icons"] = part("icons", guide_icons, kb, guide, out, icons) or {}
    return _json_keys(out)


HEARTHSTONE = 6948


def guide_icons(kb, guide, facts, book):
    """The icons the Guides page can show for this guide (the module docstring's "icons"), from an IconBook."""
    if not book:
        return {}
    items, spells, npcs = {HEARTHSTONE}, set(), set()
    for st in guide.get("steps") or ():
        for a in st.get("actions") or ():
            kind, eid, args = a.get("entity_kind"), a.get("entity_id"), a.get("args") or {}
            if isinstance(eid, int) and eid > 0:
                {"item": items, "spell": spells, "npc": npcs}.get(kind, set()).add(eid)
            for k in ("item_id", "item_ids", "items"):
                v = args.get(k)
                for i in (v if isinstance(v, list) else [v]):
                    i = i.get("id") if isinstance(i, dict) else i
                    if isinstance(i, int) and i > 0:
                        items.add(i)
    for lst in facts.get("targets", {}).values():
        for t in lst:
            if t.get("item"):
                items.add(t["item"])
            if t.get("kind") == "npc":
                npcs.add(t["id"])
    for p in facts.get("prep") or ():
        items.add(p["item_id"])
        npcs.update(v["id"] for v in p.get("vendors") or ())
    out = {}
    for i in sorted(items):
        if book.item(i):
            out["item:%d" % i] = book.item(i)
    for sp in sorted(spells):
        if book.spell(sp):
            out["spell:%d" % sp] = book.spell(sp)
    for r in _q(kb, "SELECT id, type, family FROM creature WHERE id IN ({in})", sorted(npcs)) if kb is not None else ():
        fid = book.creature(r["type"], r["family"])
        if fid:
            out["npc:%d" % r["id"]] = fid
    for key in book.book.get("named", {}):
        out["role:%s" % key] = book.named(key)
    return out


def _json_keys(out):
    """Step and quest keys as strings, so a caller reading the dict in Python sees what the page sees in JSON."""
    for k in ("quests", "targets", "hazards"):
        out[k] = {str(i): v for i, v in out[k].items()}
    if out["plan"]:
        out["plan"]["steps"] = {str(i): v for i, v in out["plan"]["steps"].items()}
    return out
