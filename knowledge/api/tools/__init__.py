"""The Claude tools: the Messages API tool table and dispatch(), which runs one tool on the live ctx under a time
budget. Each tool takes the live ctx first and returns a compact dict of facts with their ids."""
import json
import threading

from knowledge.api.results import RESULT_TOKENS, jsonable, trim_result
from knowledge.api.tools.advice import t_community, t_heuristics
from knowledge.api.tools.lookup import t_creature_info, t_glossary, t_quest_info, t_search
from knowledge.api.tools.mechanics import t_kill_xp, t_param, t_quest_xp
from knowledge.api.tools.route import t_guide_summary, t_route_lookahead, t_step_brief, t_why_this_step
from knowledge.api.tools.session import t_recent_log, t_session_review
from knowledge.api.tools.simulation import t_alternatives_here, t_eta, t_skip_impact, t_what_if
from knowledge.api.tools.travel import t_path_time, t_walk_route

TOOL_BUDGET_S = 5.0             # hard time budget of one tool call (the coach passes less near its deadline)


def _ps(desc):
    return {"type": "string", "description": desc}


def _pi(desc):
    return {"type": "integer", "description": desc}


TOOLS = (
    ("quest_info", t_quest_info,
     "Facts about one quest: level, XP (full value, at your level, the level where it starts to drop), objectives "
     "with their targets, drop sources and nearest spawns, quest giver and turn-in NPC with directions, "
     "prerequisites and follow-ups, whether it's in your log, and where your RestedXP route accepts / turns it in. "
     "Give id or name.",
     {"id": _pi("quest id"), "name": _ps("quest name (approximate is fine)")}, [],
     {"quest": "name", "quest_id": "id", "id_or_name": "name", "quest_name": "name"}),
    ("creature_info", t_creature_info,
     "Facts about one mob or NPC: level range, rank (elite / rare), type, flees / caster / abilities, RestedXP "
     "danger notes, kill XP at your level, estimated seconds per kill and XP per minute, quest items it drops with "
     "rates, nearest spawns. Give id or name.",
     {"id": _pi("creature (NPC / mob) id"), "name": _ps("creature name (approximate or plural is fine)")}, [],
     {"creature": "name", "mob": "name", "npc": "name", "creature_id": "id", "id_or_name": "name"}),
    ("step_brief", t_step_brief,
     "Why any RestedXP step exists: kind, XP it earns and unlocks, its quests (XP, role, level where XP drops, "
     "turn-in step), reason codes, RestedXP notes, co-located work, dependencies, kills, travel, dangers, "
     "alternatives with why_not codes, modelled time and the planned level. Give rxp_step_no (the number "
     "RestedXP shows) or idx (from an s:<guide>/<idx> fact id); guide defaults to your current guide.",
     {"rxp_step_no": _pi("step number as RestedXP shows it"),
      "idx": _pi("the guide's internal step index, as in an s:<guide>/<idx> fact id"),
      "guide": _ps("guide name such as '5-11 Dun Morogh' (default: your current guide)")}, [],
     {"step": "rxp_step_no", "step_no": "rxp_step_no", "n": "rxp_step_no"}),
    ("why_this_step", t_why_this_step,
     "The full analysis of the step you are on (more detail than the KNOWLEDGE block): every quest, kills, travel, "
     "dependencies, top 3 alternatives with why_not, risks, and your level vs the plan with plain notes. Use it for "
     "'why am I doing this?'.", {}, [], {}),
    ("skip_impact", t_skip_impact,
     "Simulate skipping one quest from the current step to the end of this guide and the next: time saved or "
     "lost, level at the end, follow-up quests lost with their XP, and the grind time to make the XP back.",
     {"quest_id": _pi("quest id"), "name": _ps("quest name")}, [],
     {"quest": "name", "id": "quest_id", "id_or_name": "name"}),
    ("what_if", t_what_if,
     "Simulate one deviation from the current step and compare it with following the route: grind_here (minutes "
     "or to_level), hold_turnin (quest, until_level), leave_zone (at step; default now), do_dungeon (dungeon name "
     "or code such as DM), skip_step (step). Returns time and end-level deltas, lost / gained quests and notes.",
     {"variant": {"type": "string", "enum": ["grind_here", "hold_turnin", "leave_zone", "do_dungeon", "skip_step"],
                  "description": "which deviation"},
      "minutes": {"type": "number", "description": "grind_here: minutes of grinding"},
      "to_level": _pi("grind_here: grind until this level"),
      "quest": _ps("hold_turnin: quest name or id"), "until_level": _pi("hold_turnin: keep it until this level"),
      "step": _pi("RestedXP step number for grind_here / leave_zone / skip_step (default: current)"),
      "dungeon": _ps("do_dungeon: dungeon name or code")}, ["variant"],
     {"kind": "variant", "type": "variant", "level": "to_level", "at_step": "step"}),
    ("alternatives_here", t_alternatives_here,
     "Other quests, grind spots and dungeons near you that the route doesn't do now, each with estimated XP, "
     "XP per minute and why_not codes (why RestedXP skips or delays it).",
     {"radius": _pi("search radius in yards (default 600, 100-1500)")}, [], {"radius_yd": "radius"}),
    ("eta", t_eta,
     "Time to reach a level following the route from here (the model, across the next guides) and at your "
     "current XP/hour; XP still needed, the rested pool and the XP of completed quests in your log.",
     {"target_level": _pi("level to reach")}, ["target_level"], {"level": "target_level"}),
    ("kill_xp", t_kill_xp,
     "Kill XP at your level for a mob of the given level (Forever-fitted formula; elites give double), the rested "
     "bonus, your gray level, and each parameter's status (unverified = not confirmed on Forever) with the "
     "Classic value.",
     {"mob_level": _pi("the mob's level"), "elite": {"type": "boolean", "description": "elite mob"}}, ["mob_level"],
     {"level": "mob_level", "is_elite": "elite"}),
    ("quest_xp", t_quest_xp,
     "XP a quest gives at your level (or at_level), the reduction factor, the level where it starts to drop, the "
     "next levels' values, and the parameters' status with the Classic value.",
     {"quest": _ps("quest name or id"), "at_level": _pi("player level (default: yours)")}, ["quest"],
     {"quest_id": "quest", "name": "quest", "id": "quest", "level": "at_level"}),
    ("path_time", t_path_time,
     "Travel time from you to a target: run time (from recorded paths when available), the flight option between "
     "the nearest flight points, and the hearthstone option. target = 'step <n>', a quest name (its giver, or "
     "its turn-in when it's in your log), an NPC or mob name, a town / flight point / inn name, or coordinates "
     "'x,y' on your map.",
     {"target": _ps("what to travel to")}, ["target"], {"to": "target", "destination": "target"}),
    ("walk_route", t_walk_route,
     "The walking route from you to a target over the real terrain (mountains, cliffs, water, plus the paths "
     "RestedXP and you have walked: tunnels, bridges, towns): total yards vs straight line, run time, the legs "
     "with compass direction, distance and where each ends, the areas passed through, swimming and climbing. "
     "target = 'step <n>', a quest, NPC or mob name, a town / flight point / inn name, or 'x,y' on your map.",
     {"target": _ps("where to go")}, ["target"], {"to": "target", "destination": "target"}),
    ("route_lookahead", t_route_lookahead,
     "The next n RestedXP steps starting with the current one: step number, kind, text, XP, quests, modelled time, "
     "planned level and where.",
     {"n": _pi("how many steps (default 5, max 12)")}, [], {"steps": "n", "count": "n"}),
    ("guide_summary", t_guide_summary,
     "The current guide as modelled for your class: level range, start / end level, total time, XP from quests, "
     "kills and grinding, the main turn-in hubs and the next guides.", {}, [], {}),
    ("heuristics", t_heuristics,
     "Expert leveling rules that apply to your state now (muted topics excluded), optionally only those for a "
     "topic or tags: route, quest, hearth, travel, pace, xp, rested, grind, combat, pull, survival, death, buffs, "
     "gear, economy, vendor, training, talents, dungeon, professions, forever, class.",
     {"topic": _ps("one topic word"), "tags": {"type": "array", "items": {"type": "string"}, "description": "tags"}},
     [], {"tag": "tags", "query": "topic"}),
    ("community", t_community,
     "What the WoW Forever community reports (daily scan of patch notes, Blizzard posts, forums, guides, videos, "
     "addons, data projects): leveling tips, route tricks, patch and hotfix changes, XP and mechanics findings, "
     "dungeon and class notes, each with status (reported / corroborated / official / contradicted) and sources. "
     "Default: notes for your level, zone and class; query searches all notes.",
     {"query": _ps("words to search for, e.g. 'dungeon xp' or 'rested camp'"),
      "kind": _ps("patch, tip, route, mechanic, class, dungeon, addon, data, news or warning"),
      "level": _pi("level to filter for (default: yours)"), "zone": _ps("zone name (default: yours)")},
     [], {"q": "query", "text": "query", "topic": "query"}),
    ("glossary", t_glossary,
     "Definition of a leveling term (breadcrumb, deathskip, hub, gray mob, zero difference, rested, XP gate, ...).",
     {"term": _ps("the term")}, ["term"], {"word": "term"}),
    ("session_review", t_session_review,
     "This session against the model: XP/hour vs the benchmark, deaths, time on recent steps vs modelled time, "
     "and the biggest time sinks when the knowledge base has this session's recorded step times.", {}, [], {}),
    ("param", t_param,
     "A mechanics or model parameter by key or key prefix (xp.kill, xp.quest, rested, travel, mount, combat, "
     "analyzer, ...): value, Classic value, unit, status (forever-verified / forever-fitted / classic / "
     "unverified) and evidence.",
     {"key": _ps("parameter key or prefix, e.g. xp.kill or p:travel.run_speed")}, ["key"],
     {"key_prefix": "key", "name": "key"}),
    ("search", t_search,
     "Find quests, creatures / NPCs, items and points of interest (inns, flight points, graveyards, trainers, "
     "vendors) by approximate name; up to 10 results with ids. Use it when unsure of a name or a misheard word.",
     {"text": _ps("name or part of it")}, ["text"], {"name": "text", "query": "text", "q": "text"}),
    ("recent_log", t_recent_log,
     "The last n coach messages and triggers (newest last) with how long ago; use it when the player asks what you "
     "meant or what you said.", {"n": _pi("how many entries (default 5, max 20)")}, [], {"count": "n"}),
)
_SPEC = {t[0]: t for t in TOOLS}
_DEFS = None


def tool_definitions():
    """The Messages API `tools` list (name, description, input_schema)."""
    global _DEFS
    if _DEFS is None:
        _DEFS = [{"name": n, "description": desc,
                  "input_schema": {"type": "object", "properties": props, "required": list(req)}}
                 for n, _fn, desc, props, req, _al in TOOLS]
    return [dict(d) for d in _DEFS]


def tool_names():
    return [t[0] for t in TOOLS]


def dispatch(name, args=None, ctx=None, budget_s=None):
    """Run one tool by name with its JSON arguments on the live ctx, under a hard time budget. Never raises:
    errors come back as {"error": ...}; the result is trimmed to RESULT_TOKENS (len/4)."""
    spec = _SPEC.get(name)
    if spec is None:
        return {"error": "unknown tool %r; tools: %s" % (name, ", ".join(tool_names()))}
    if ctx is None:
        return {"unavailable": "no live context"}
    _n, fn, _d, props, req, aliases = spec
    if not isinstance(args, dict):
        try:
            args = json.loads(args) if isinstance(args, str) and args.strip() else {}
        except ValueError:
            args = {}
        if not isinstance(args, dict):
            args = {}
    kwargs = {}
    for k, v in args.items():
        k2 = k if k in props else aliases.get(k)
        if k2 in props and kwargs.get(k2) in (None, ""):
            kwargs[k2] = v
    missing = [k for k in req if kwargs.get(k) in (None, "", [])]
    if missing:
        return {"error": "missing argument(s): %s" % ", ".join(missing)}
    box = {}

    def run():
        try:
            box["r"] = fn(ctx, **kwargs)
        except Exception as e:  # noqa: BLE001 - a tool error is an answer, not a crash
            box["r"] = {"error": "%s: %s" % (type(e).__name__, e)}
    budget = TOOL_BUDGET_S if budget_s is None else max(0.5, float(budget_s))
    t = threading.Thread(target=run, name="glide-tool-%s" % name, daemon=True)
    t.start()
    t.join(budget)
    if t.is_alive():
        return {"error": "%s took longer than %.0f s; answer without it" % (name, budget)}
    r = box.get("r")
    if not isinstance(r, dict):
        r = {"result": r}
    try:
        return trim_result(jsonable(r), RESULT_TOKENS)
    except Exception as e:  # noqa: BLE001
        return {"error": "result not serialisable: %s" % e}
