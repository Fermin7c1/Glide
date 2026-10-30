"""What the model is told about the game: the game context (game_context: player, guide, route steps, quests with
their places described, gear, extras) and the user message built from it (user_message): a REFERENCE part that
changes only with the quest log or the kit (cached), then the live part, each shrunk by structure to its budget."""
import json

import coach_prompts as prompts
from gear import ARMOR_LABEL, SLOT_NAMES, WEAPON_LABEL
from geo import Geo
from log import log_once
from state import as_int


def compact_json(obj):
    """Compact JSON for prompts. Non-ASCII stays as is: '·' is one character, '\\u00b7' six (and more tokens)."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str)


def short_json(obj, limit=6000):
    s = compact_json(obj)
    return s if len(s) <= limit else s[:limit] + "..."


def progress_text(pr):
    """{"complete": bool, "objectives": [[have, need], ...]} -> "done" or "3/8, 0/1" (objectives in the order the
    quest lists them)."""
    if not isinstance(pr, dict):
        return pr
    if pr.get("complete"):
        return "done"
    objs = []
    for o in pr.get("objectives") or ():
        if isinstance(o, (list, tuple)) and len(o) == 2:
            objs.append("%s/%s" % (o[0], o[1]))
    return ", ".join(objs) or "in progress"


def fit(obj, limit, steps):
    """Shrink a prompt section to `limit` characters by structure, not by cutting the text: apply each step (a
    function that drops or shortens the least important part in place and returns False when it has nothing left
    to drop) until it fits. A text cut stays the last resort. -> the JSON text."""
    s = compact_json(obj)
    for step in steps:
        while len(s) > limit and step(obj):
            s = compact_json(obj)
        if len(s) <= limit:
            return s
    return s if len(s) <= limit else s[:limit] + "..."


# The live sections a callout can do without, and which ones each trigger topic needs.
LIVE_SECTIONS = ("buffs", "target", "heuristics", "nearby", "route", "progress", "next_pace")
TOPIC_NEEDS = {"route": set(LIVE_SECTIONS),
               "upkeep": {"heuristics", "nearby", "route", "progress", "next_pace"},
               "combat": {"target", "heuristics"},
               "buffs": {"buffs"}, "gear": set(), "system": set()}
# What each live callout trigger is about (the live game state is cut to what that topic needs; questions, reviews
# and briefings always get everything). Trigger kinds not listed count as "route" (everything).
TRIGGER_TOPIC = {"low_health_in_combat": "combat", "target_tapped": "combat",
                 "buffs_dropped": "buffs", "buff_expiring": "buffs",
                 "gear_upgrade": "gear", "reward_choice": "gear", "vendor_treasure": "gear", "addon_outdated": "system",
                 "bags_nearly_full": "upkeep", "low_durability": "upkeep", "consumables_low": "upkeep",
                 "training_available": "upkeep", "talent_points_unspent": "upkeep"}


def game_context(s, state, knowledge, geo, pack=None, deep=False, watched_fn=None, gear_fn=None, system_fn=None,
                 talent_plan=None):
    """Everything the model sees about the game for summary s: the player (buffs, kit, extras), the guide step,
    the current and next RestedXP steps with their places described, up to 20 quests in quest id order (with
    quest_priority for trimming), gear and the addon status. state: the GameState (or None); knowledge: glide.db's
    reader, read only while there is no knowledge pack; geo: the place describer; pack: the knowledge layer (quests
    and map names, names instead of ids in the extras); watched_fn, gear_fn, system_fn: the app's getters for watched
    buffs, gear and addon status; talent_plan: the next talent pick."""
    st = state
    # quests and map names from the knowledge base (kb.db); glide.db only without one
    if pack is not None and getattr(pack, "ok", False):
        quest_of, map_name = pack.quest_detail, pack.map_name
    else:
        quest_of, map_name = knowledge.quest, getattr(knowledge, "map_name", lambda m: None)
    near = s.get("map")
    me = st.player_geo() if st else None
    fac = s.get("faction")

    def where(m, x, y):
        """{"where": fixed text (zone · area, landmark), "coords"} plus "_rel": the live distance and direction
        when the place is on the player's map. The fixed part stays byte-identical while the player moves, so it
        can sit in the cached part of the prompt; user_message moves each "_rel" to the live NEARBY list."""
        if m is None or x is None:
            return None
        d = geo.describe(m, x, y, player=me, faction=fac)
        fixed = {k: d[k] for k in ("area", "landmark") if d.get(k)}
        fixed["zone"] = d.get("zone") or map_name(m)
        out = {"where": Geo.text(fixed) or d.get("text"), "coords": "%.1f,%.1f" % (x, y)}
        if d.get("yards") is not None or d.get("direction"):
            rel = Geo.text({k: d[k] for k in ("yards", "direction", "relative") if d.get(k) is not None})
            if rel:
                out["_rel"] = rel
        return out

    steps = []
    qids = []                              # the current step's quests first, then the next steps', then the log
    for i, step in enumerate(s.get("steps") or []):
        if not step:
            continue
        els = []
        for e in step["elements"]:
            e = dict(e)
            if e["tag"] == "goto":
                e.update(where(e.get("map") or near, e["x"], e["y"]) or {})
                e.pop("x", None), e.pop("y", None), e.pop("map", None)
            if e.get("quest") and e["quest"] not in qids:
                qids.append(e["quest"])
            els.append(e)
        steps.append({"which": ["current", "next", "after"][i], **step, "elements": els})
    for q in (s.get("quests") or {}):
        if q not in qids and as_int(q) not in qids:
            qids.append(q)
    quests = {}
    # up to 20 quests (a full quest log plus the current step's), in quest id order so the set and its order
    # don't change when the step does (the REFERENCE cache); the priority order goes along for trimming
    qids = qids[:20]
    for q in sorted(qids, key=lambda v: (as_int(v) is None, as_int(v) or 0, str(v))):
        info = quest_of(q, near_map=near, detail=deep)
        for key in ("giver_at", "turn_in_at"):
            pos = info.get(key)
            if isinstance(pos, dict):
                info[key] = where(pos["map"], pos["x"], pos["y"])
        for o in info.get("objectives", []):
            pos = o.get("where")
            if isinstance(pos, dict):
                w = where(pos["map"], pos["x"], pos["y"]) or {}
                o["where"] = "%s (%d spawn points)" % (w.get("where"), pos["spawn_points"])
                if w.get("_rel"):
                    o["_rel"] = w["_rel"]
        log = (s.get("quests") or {}).get(q)
        if log:
            info["progress"] = {"complete": log["done"], "objectives": log["obj"]}
        quests[q] = info
    player = {k: s.get(k) for k in ("name", "class", "race", "faction", "level", "xp", "xp_max", "rested",
                                      "hp", "power", "power_type", "combat", "dead", "zone", "subzone",
                                      "money", "free_bags", "durability", "hearth_cd", "bind", "mounted",
                                      "on_taxi", "target", "kills", "xp_per_hour", "ttl_seconds",
                                      "xp_session", "session_minutes", "on_step_seconds")}
    if player.get("zone") is None and near:
        player["zone"] = map_name(near)
    player["buffs"] = [{"name": b["name"], "mine": b["mine"],
                        "seconds_left": b["remaining"]}
                       for b in s.get("buffs", [])]
    if s.get("buffs_stale") or s.get("buffs_hidden"):
        player["buffs_frozen"] = True          # what it means is in the system prompt (Data conventions)
    if watched_fn:
        player["buffs_you_asked_me_to_watch"] = sorted(watched_fn())
    player_extras(s, player, st, pack, talent_plan)
    gear = None
    if gear_fn:
        ups, rewards, worn, bag_items = gear_fn()
        gear = {"equipped": {SLOT_NAMES.get(int(i["key"]), i["key"]): i["name"] for i in worn
                             if str(i["key"]).isdigit()},
                "upgrades_in_bags": [{k: u[k] for k in ("item", "slot_name", "replaces", "gain", "wait_level")}
                                     for u in ups[:5]],
                "equippable_items_in_bags": [{k: u[k] for k in ("item", "slot_name", "replaces", "verdict",
                                                               "gain", "score", "replaces_score", "stats", "req")}
                                             for u in bag_items[:12]],
                "trained_skills": trained_skills(s.get("skills")),
                "quest_reward_choices": rewards or None}
    if me:
        area, _ = geo.area_at(me["map"], me["x"], me["y"])
        player["standing_in"] = area
        player["coords"] = "%.1f,%.1f" % (me["x"], me["y"])
    off = [k for k, v in (s.get("coach_flags") or {}).items() if not v and k not in ("coach", "voice")]
    if off:
        player["commentary_turned_off"] = off
    guide = {"name": s.get("guide"), "group": s.get("guide_group"), "step": s.get("step")}
    cur = s.get("step")
    skipped = [i for i in s.get("rxp_skipped") or () if cur is None or cur - 10 <= i <= cur + 40]
    if skipped:
        guide["skipped_in_restedxp"] = skipped
    ctx = {"player": player, "guide": guide,
           "rxp_steps": steps, "quests": quests, "quest_priority": [str(q) for q in qids],
           "quests_in_log": [str(q) for q in (s.get("quests") or {})], "gear": gear,
           "system": {"glidelink": system_fn()} if system_fn else None}
    return ctx


def player_extras(s, player, st, pack=None, talent_plan=None):
    """Add to `player` the addon 0.10 extras: training, talent points, flight paths, consumables, corpse, target quest relevance.
    With the knowledge base the ids become names and distances (knowledge/extras.py); without it, raw."""
    ex = {}
    if pack is not None and getattr(pack, "ok", False):
        try:
            mp = s.get("map")
            ex = pack.player_extras(s, st.map_size(mp) if st and mp else None) or {}
        except Exception as e:  # noqa: BLE001
            log_once("context.player_extras", e)
            ex = {}
    if s.get("talent_points"):
        player["talent_points_unspent"] = s["talent_points"]
    if talent_plan:
        player["talent_plan"] = talent_plan
    tr = ex.get("training")
    if tr is not None:
        player["training"] = {"not_trained_yet": tr["missing_now"], "coming_next": tr["next"]}
    elif s.get("training") and s["training"].get("missing"):
        player["training"] = {"not_trained_spell_ids": s["training"]["missing"]}
    fp = ex.get("flight_paths")
    if fp is not None:
        player["flight_paths"] = fp
    elif s.get("flight_paths") is not None:
        player["flight_path_node_ids"] = s["flight_paths"]
    cons = ex.get("consumables")
    if cons is None and s.get("consumables") is not None:
        cons = [{"kind": c["kind"], "name": c["name"], "count": c["count"]} for c in s["consumables"]]
    if cons is not None:
        player["consumables"] = cons
    if s.get("dead"):
        cp = ex.get("corpse") or ({"corpse_at": "%.1f,%.1f" % (s["corpse"]["x"], s["corpse"]["y"])}
                                  if s.get("corpse") else None)
        if cp:
            player["corpse"] = cp
    if s.get("res_sickness_s"):
        player["res_sickness_seconds_left"] = s["res_sickness_s"]
    if isinstance(player.get("target"), dict):
        t = dict(player["target"])
        for k in ("tapped_by_others", "quest_mob", "classification"):
            if t.get(k) is None:
                t.pop(k, None)
        if ex.get("target_quests") is not None:
            t["counts_for_quests"] = ex["target_quests"]
        player["target"] = t

def trained_skills(sk):
    """The weapon and armor types the character has trained, by name, and the skill ranks."""
    if not sk:
        return None
    out = {}
    if sk.get("weapons") is not None:
        out["weapons"] = [WEAPON_LABEL[i] for i in sorted(sk["weapons"]) if i in WEAPON_LABEL]
    if sk.get("armor") is not None:
        out["armor"] = [ARMOR_LABEL[i] for i in sorted(sk["armor"]) if i in ARMOR_LABEL]
    if sk.get("ranks"):
        out["ranks"] = sk["ranks"]
    return out or None


# The user message, in two parts. REFERENCE first: what changes only when the quest log or the kit changes
# (quests with fixed place text and without their progress, gear, training, flight paths, community notes, addon
# status). It ends with its own cache marker, so consecutive callouts read it back at a tenth of the price instead
# of paying for it again. Then the live part: the knowledge block, the player state, the guide step and the route
# steps, quest progress, NEARBY distances, recent lines and the trigger. Each part is shrunk by structure (least
# important first) to fit its budget.
REF_CHARS = 14000
LIVE_CHARS = 8000
REF_PLAYER_KEYS = ("training", "flight_paths", "flight_path_node_ids")


def pop_nearby(steps, quests, order=()):
    """Remove the "_rel" live directions from the route steps and quests -> NEARBY lines (label, where), the
    current step's waypoint first, then the quests in route priority order."""
    out = []
    for st in steps or ():
        for e in st.get("elements") or ():
            rel = e.pop("_rel", None)
            if rel and st.get("which") == "current":
                out.append(("current step waypoint", rel))
    quests = quests or {}
    keys = [q for q in order if q in quests] + [q for q in quests if q not in order]
    for qk in keys:
        q = quests[qk]
        name = q.get("name") or "quest %s" % q.get("id")
        for key, what in (("giver_at", "pick up"), ("turn_in_at", "turn in")):
            pos = q.get(key)
            if isinstance(pos, dict) and pos.get("_rel"):
                who = (q.get("giver" if key == "giver_at" else "turn_in") or [None])[0]
                out.append(("%s %s%s" % (what, name, (" at " + who) if who else ""), pos.pop("_rel")))
        for o in q.get("objectives") or ():
            rel = o.pop("_rel", None)
            if rel:
                out.append(("%s objective: %s" % (name, o.get("target") or o.get("text") or o.get("type")), rel))
    return out

def user_message(ctx, trigger, recent, question=False, triggers=None, cc=None):
    """The request's user content from game context ctx (game_context plus the knowledge block and the lookahead),
    the trigger or question text and the recent lines section `recent`. -> a string, or with `cc` (cache_control
    for the REFERENCE part) two text blocks."""
    ctx = json.loads(compact_json(ctx))          # a deep copy: the sections below are moved and trimmed
    k = ctx.pop("knowledge", None)
    look = ctx.pop("guide_lookahead", None)
    order = ctx.pop("quest_priority", None) or []
    in_log = set(ctx.pop("quests_in_log", None) or ())
    player = ctx.get("player") or {}
    quests = ctx.pop("quests", None) or {}
    # quests the route steps name but the log doesn't hold yet change with every step: they go in the live part
    step_quests = {q: quests.pop(q) for q in list(quests) if in_log and q not in in_log}
    nearby = pop_nearby(ctx.get("rxp_steps"), dict(quests, **step_quests), order)
    progress = {}
    for qk in [q for q in order if q in quests] + [q for q in quests if q not in order]:
        pr = quests[qk].pop("progress", None)
        if pr is not None:
            progress[quests[qk].get("name") or qk] = progress_text(pr)
    ref = {"quests": quests, "quest_priority": order or None,
           "player": {kk: player.pop(kk) for kk in REF_PLAYER_KEYS if kk in player} or None,
           "gear": ctx.pop("gear", None), "system": ctx.pop("system", None)}
    if isinstance(k, dict) and k.get("community"):
        ref["community"] = k.pop("community")
    ref = {kk: v for kk, v in ref.items() if v not in (None, {}, [])}
    ref.pop("quest_priority", None)       # used for trimming only
    # a live callout gets the live sections its trigger is about (questions, reviews, briefings get everything)
    keep = needed_sections(triggers) if triggers and not question else None
    drop = set(LIVE_SECTIONS) - keep if keep is not None else set()
    drop_sections(drop, ctx, player, k)
    if "nearby" in drop:
        nearby = []
    if "route" in drop:
        step_quests = {}
    if "progress" in drop:
        progress = {}
    if keep is not None and isinstance(k, dict) and isinstance(k.get("heuristics"), list):
        # a one-sentence callout uses the advice; the rationale is for questions and reviews
        k["heuristics"] = [{kk: v for kk, v in h.items() if kk != "rationale"} if isinstance(h, dict) else h
                           for h in k["heuristics"]]
    live = {"player": player}
    if progress:
        live["quest_progress"] = progress
    if step_quests:
        live["route_quests_not_in_log"] = step_quests
    live.update({kk: v for kk, v in ctx.items() if kk != "player" and v not in (None, {}, [])})
    ref_txt = fit(ref, REF_CHARS, reference_trims(ref, order))
    live_txt = fit(live, LIVE_CHARS, [drop_list(player, "buffs", 6)])
    head = ""
    if k is not None:
        head = ("KNOWLEDGE (computed by the knowledge layer for this moment; cite its fact ids):\n%s\n\n"
                % fit(k, 6000, [drop_list(k, "heuristics", 2), drop_list(k, "facts", 20)]))
    near = ""
    if nearby:
        near = "NEARBY (live, from where the player stands and faces):\n%s\n\n" % "\n".join(
            "- %s: %s" % (a, b) for a, b in nearby[:14])
    route = ""
    if look:           # its own section: the game-state cap below must never cut the route in half
        route = "UPCOMING RESTEDXP STEPS:\n%s\n\n" % compact_json(look)
    reference = "REFERENCE (route steps, quests, character kit; changes when the step or the kit changes):\n%s\n\n" \
        % ref_txt
    rest = "%sGAME STATE (live):\n%s\n\n%s%s%s\n\n%s\n\n%s" % (head, live_txt, near, route, recent, trigger,
                                                            prompts.REPLY_NOTE)
    if cc:
        return [{"type": "text", "text": reference, "cache_control": cc}, {"type": "text", "text": rest}]
    return reference + rest

def needed_sections(triggers):
    """The live sections a callout for these trigger kinds needs (TRIGGER_TOPIC, TOPIC_NEEDS)."""
    keep = set()
    for t in triggers:
        keep |= TOPIC_NEEDS[TRIGGER_TOPIC.get(t, "route")]
    return keep


def drop_sections(drop, ctx, player, k):
    """Remove the live sections in `drop` from the game context, its player and the knowledge block k (the NEARBY
    list, route quests and quest progress are the caller's)."""
    if "buffs" in drop:
        player.pop("buffs", None), player.pop("buffs_frozen", None)
    if "target" in drop:
        player.pop("target", None)
        if isinstance(k, dict):
            k.pop("target", None)
    if "heuristics" in drop and isinstance(k, dict):
        k.pop("heuristics", None)
    if "route" in drop:
        ctx.pop("rxp_steps", None)
    if "next_pace" in drop and isinstance(k, dict):
        k.pop("next", None), k.pop("pace", None)


def drop_list(d, key, keep):
    """A fit() step: shorten list d[key] one item at a time down to `keep` items."""
    def step(_obj):
        v = d.get(key) if isinstance(d, dict) else None
        if isinstance(v, list) and len(v) > keep:
            v.pop()
            return True
        return False
    return step

def reference_trims(ref, order=()):
    """fit() steps for REFERENCE, least important first: quest flavour text, then the quests furthest down the
    route (quest_priority: the current step's quests first, then the next steps', then the rest of the log),
    bag items beyond 6, community notes beyond 2, then quests down to 3."""
    quests = ref.get("quests") or {}
    gear = ref.get("gear") or {}
    order = [q for q in order if q in quests]
    order += [q for q in quests if q not in order]

    def details(_obj):
        for q in reversed(order):
            if q in quests and quests[q].pop("details", None) is not None:
                return True
        return False

    def quest(keep):
        def step(_obj):
            left = [q for q in order if q in quests]
            if len(left) > keep:
                quests.pop(left[-1])
                return True
            return False
        return step
    return [details, quest(8), drop_list(gear, "equippable_items_in_bags", 6),
            drop_list(ref, "community", 2), quest(3)]
