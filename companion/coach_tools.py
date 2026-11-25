"""The coach's own tools and spoken commands, next to the knowledge layer's: reminders ("remind me to repair"), the
memory of the player across sessions (and the reflection that updates it), where an item comes from (the engine's
research, research.py: "where does Magister's Crown drop?"), Glide's own best-in-slot ranks for a slot (bis.py: "what's
best in slot for my neck?"), and feature switches said out loud ("quest briefings off"). Tool definitions for the
model, the tool calls, and the local handling of clear requests."""
import json
import re

import gear as gear_mod
import memory as memory_mod
import reminders as reminders_mod
from log import log_once

REMINDER_TOOLS = [
    {"name": "reminder_add", "description": (
        "Remember something the player wants to be reminded of. The companion says it when the condition is met "
        "(near such an NPC, in a place, at a level, after some minutes) and nudges about it every every_minutes "
        "until it's done. Not for re-applying buffs (use WATCH)."),
     "input_schema": {"type": "object", "required": ["what"], "properties": {
         "what": {"type": "string", "description": "what to do, as an imperative: 'repair', "
                                                   "'buy 20 Ice Cold Milk', 'train new spells'"},
         "when": {"type": "string", "enum": ["npc", "place", "level", "time", "none"],
                  "description": "npc: near an NPC of `role` or one selling `item`; place: in zone/sub-zone "
                                 "`place`; level: at `level`; time: in `minutes`; none: only periodic nudges. "
                                 "Omit to infer from `what`."},
         "role": {"type": "string", "enum": sorted(reminders_mod.ROLES)},
         "item": {"type": "string", "description": "an item to buy (finds the vendors that sell it)"},
         "place": {"type": "string", "description": "zone, town or sub-zone name, e.g. Ironforge, Goldshire"},
         "level": {"type": "integer"}, "minutes": {"type": "number"},
         "every_minutes": {"type": "integer", "description": "nudge interval; 0 = only when the condition is "
                                                              "met (default 20)"}}}},
    {"name": "reminder_list", "description": "The open reminders (id, what, when).",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "reminder_done", "description": "Close reminders: by id, by words of what they are ('repair'), "
                                             "or 'all'.",
     "input_schema": {"type": "object", "required": ["which"], "properties": {"which": {"type": "string"}}}},
]
MEMORY_TOOLS = [
    {"name": "memory_remember", "description": (
        "Remember something lasting about the player across sessions: how they like to be coached, a goal, a "
        "standing request, a correction of something you said, or a fact about them or their playstyle. One short "
        "third-person note. Not for one-off things (use a reminder or just answer)."),
     "input_schema": {"type": "object", "required": ["text"], "properties": {
         "text": {"type": "string", "description": "the note, e.g. 'Prefers callouts under 10 words.'"},
         "kind": {"type": "string", "enum": list(memory_mod.KINDS)}}}},
    {"name": "memory_forget", "description": "Forget notes that are wrong or no longer apply: by id (12 for "
                                             "[m12]) or by words of the note.",
     "input_schema": {"type": "object", "required": ["which"], "properties": {"which": {"type": "string"}}}},
    {"name": "memory_list", "description": "Everything remembered about the player (id, kind, text).",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
]
ITEM_TOOLS = [
    {"name": "item_info", "description": (
        "Where an item comes from on WoW Forever: the boss or mob that drops it (and the dungeon), the quest that "
        "rewards it, the vendor that sells it, the profession that makes it, the reputation or PvP rank it needs. "
        "For 'where does X drop?', 'how do I get X?', 'who sells X?'. avail: now (it can be had now), later (a later "
        "phase: not in the game yet), nosrc (no known source). Cite its fact_ids in FACTS."),
     "input_schema": {"type": "object", "required": ["item"], "properties": {
         "item": {"type": "string", "description": "the item's name (e.g. Magister's Crown) or its item id"}}}},
]
BIS_TOOLS = [
    {"name": "bis_slot", "description": (
        "Glide's own best-in-slot ranks for one gear slot of the character playing (level 60): WoWSims sims of each "
        "item on the player's own gear and talents. For 'what's best in slot for my neck?', 'what's my best "
        "trinket?'. Rows best first: item, rank (1 = best), d = DPS over what they wear now, source = where it comes "
        "from, avail: now (can be had now), later (a later phase), nosrc (no known source); worn: they wear it; "
        "in_bags: it is in their bags already (say so, not where it comes from); "
        "with_set: best only together with another piece of its item set (d is what it alone would change: say "
        "'with its set', not that number); about_same: within the sims' noise; with: the weapon it was simmed with "
        "(a one-hander and an off hand in place of a two-hander: d is the pair's, name both); cond: its bonus "
        "against undead, demons or the like, or in an area, isn't in d (say so once); needs: a profession it needs."),
     "input_schema": {"type": "object", "required": ["slot"], "properties": {
         "slot": {"type": "string", "description": "the slot: head, neck, shoulders, back, chest, wrists, hands, "
                                                   "waist, legs, feet, ring, trinket, main hand, off hand, ranged"}}}},
]
ITEM_TOOL_NAMES = tuple(t["name"] for t in ITEM_TOOLS + BIS_TOOLS)
# the tools the coach runs itself; every other tool call goes to the knowledge layer (api.dispatch)
LOCAL_TOOLS = ("reminder_add", "reminder_list", "reminder_done", "web_search",
               "memory_remember", "memory_forget", "memory_list") + ITEM_TOOL_NAMES
# the engine's research worker (research.Research), set by the app once it runs: item_info answers from it
research = None
# Glide's best-in-slot ranks for the character playing, set by the app once the ranker runs (bis.py): a callable
# -> the ranks (bis.json's "chars" entry) or (ranks, worn item ids), as gear.Gear.bis_now gives them; bis_slot
# answers from it, with the names and sources from the research
bis = None


def reminder_view(rem):
    """A reminder as the tools report it: id, what, when (in words), nudge interval."""
    return {"id": rem["id"], "what": rem["what"], "when": reminders_mod.describe_when(rem),
            "every_minutes": rem["every_min"]}


def run_reminder_tool(reminders, name, args, summary, now):
    """reminder_add / reminder_list / reminder_done on the open reminders. summary: the game state summary (for
    durability and level conditions). -> the tool result."""
    if reminders is None:
        return {"error": "reminders are not available"}
    if name == "reminder_add":
        when = {k: args.get(k) for k in ("role", "item", "place", "level", "minutes") if args.get(k) not in (None, "")}
        when["kind"] = args.get("when") or "auto"
        rem = reminders.add(args.get("what"), when, every_min=args.get("every_minutes"), now=now,
                            durability=summary.get("durability"), level=summary.get("level"))
        return {"added": reminder_view(rem)}
    if name == "reminder_list":
        return {"reminders": [reminder_view(x) for x in reminders.list()]}
    if name == "reminder_done":
        gone = reminders.remove(args.get("which"))
        return {"closed": [x["what"] for x in gone]} if gone else {"error": "no reminder matches %r; open: %s" % (
            args.get("which"), [x["what"] for x in reminders.list()])}
    return {"error": "unknown tool %r" % name}


def reminder_command(reminders, question, ai_ok, summary_fn, now):
    """'remind me ...', 'my reminders', 'clear reminders', 'done with the repair reminder' handled locally when
    the request is clear (or the AI is off: ai_ok false). summary_fn: -> the game state summary, for a new
    reminder's conditions. Returns the spoken answer, or None to let the AI handle it."""
    r = reminders
    if r is None:
        return None
    q = " ".join((question or "").split()).strip(" .!?")
    ql = q.lower()
    if re.fullmatch(r"(?:what are |list |show |read )?(?:my |the )?reminders(?: list)?|what reminders do i have", ql):
        items = r.list()
        if not items:
            return "No reminders."
        return " ".join("%d. %s%s." % (i + 1, x["what"], (", " + reminders_mod.describe_when(x))
                                        if reminders_mod.describe_when(x) else "") for i, x in enumerate(items))
    if re.fullmatch(r"(?:clear|delete|remove|cancel|forget)(?: all)?(?: (?:my|the))? reminders", ql):
        n = len(r.remove("all"))
        return "Cleared %d reminder%s." % (n, "" if n == 1 else "s")
    m = re.fullmatch(r"(?:i'?m |i am )?(?:done with|finished|did|cancel|delete|remove|forget|drop|stop)"
                     r"(?: reminding me (?:to |about )?)?(?: the| my)? (.+?)(?: reminder)?", ql) \
        or re.fullmatch(r"(?:the )?(.+?) reminder (?:is )?(?:done|finished|off)", ql)
    if m and ("remind" in ql or m.group(1) in " ".join(x["what"].lower() for x in r.list())):
        gone = r.remove(m.group(1))
        if gone:
            return "Done: %s." % ", ".join(x["what"] for x in gone)
    if not re.search(r"\bremind me\b", ql) or re.search(r"\b(re)?(apply|cast|buff)\b", ql):
        return None
    parsed = reminders_mod.parse_request(q, None)
    if not parsed:
        return None
    what, when = parsed
    clear = when.get("kind") in ("npc", "place", "level", "time") or reminders_mod.role_from_words(what)
    if ai_ok and not clear:
        return None                       # an unusual phrasing: the AI files it with reminder_add
    s = summary_fn()
    rem = r.add(what, when, now=now, durability=s.get("durability"), level=s.get("level"))
    cond = reminders_mod.describe_when(rem)
    return "Will remind you to %s%s." % (rem["what"], " " + cond if cond else ", every %d minutes" % rem["every_min"])


# ---- memory: what the coach learns about the player across sessions (memory.py) ------------------------------
def _say_remembered(emit, note, created):
    emit({"kind": "info", "text": "%s: %s" % ("Remembered" if created else "Updated what I remember", note["text"]),
          "speak": False, "memory": note["id"]})


def _forget(memory, which, emit):
    """Remove the notes matching `which`, saying each one -> the removed notes."""
    gone = memory.remove(which)
    for n in gone:
        emit({"kind": "info", "text": "Forgot: %s" % n["text"], "speak": False})
    return gone


def run_memory_tool(memory, name, args, emit):
    """memory_remember / memory_forget / memory_list on the coach's memory; each change is shown (emit)."""
    m = memory
    if m is None:
        return {"error": "memory is not available"}
    if name == "memory_remember":
        note, created = m.add(args.get("text"), args.get("kind"), source="coach")
        if note is None:
            return {"error": "empty note"}
        _say_remembered(emit, note, created)
        return {"remembered" if created else "updated": {"id": note["id"], "kind": note["kind"], "text": note["text"]}}
    if name == "memory_forget":
        gone = _forget(m, args.get("which"), emit)
        return {"forgot": [n["text"] for n in gone]} if gone else {"error": "no note matches %r" % args.get("which")}
    if name == "memory_list":
        return {"notes": [{"id": n["id"], "kind": n["kind"], "text": n["text"]} for n in m.list()]}
    return {"error": "unknown tool %r" % name}


def memory_command(memory, question, emit):
    """'remember that ...', 'from now on ...', 'forget that ...', 'what do you remember about me' handled locally.
    Returns the spoken answer, or None."""
    if memory is None:
        return None
    cmd = memory_mod.parse_command(question)
    if not cmd:
        return None
    if cmd[0] == "list":
        notes = memory.list()
        if not notes:
            return "Nothing yet. Tell me what you like, your goals, or when I get something wrong, and I'll remember it."
        return " ".join("%s." % n["text"].rstrip(".") for n in notes[:8]) + \
            (" And %d more in the app's Memory page." % (len(notes) - 8) if len(notes) > 8 else "")
    if cmd[0] == "forget":
        gone = _forget(memory, cmd[1], emit)
        return ("Forgotten: %s." % "; ".join(n["text"].rstrip(".") for n in gone)) if gone else None
    note, created = memory.add(cmd[2], cmd[1], source="player")
    if note is None:
        return None
    _say_remembered(emit, note, created)
    return "Got it, I'll remember that."


def reflection_events(events, marks):
    """The reflection prompt's "since last reflection" lines: the last 60 exchanges, then the feedback marks."""
    lines = []
    events = events[-60:]
    for i, e in enumerate(events):
        who = "player asked" if e["who"] == "player" else "you (%s)" % e.get("kind", "coach")
        line = "- %s: %s" % (who, " ".join(str(e["text"]).split())[:300])
        if e.get("kind") == "banter" and not any(x["who"] == "player" and 0 < x["t"] - e["t"] <= 180
                                                  for x in events[i + 1:]):
            line += " (no reply)"
        lines.append(line)
    for x in marks:
        lines.append("- player flagged your line \"%s\" as wrong/unhelpful%s" % (
            x["coach_said"][:200], ": " + x["note"] if x["note"] else ""))
    return lines


def apply_reflection(memory, text, emit, since=None, now=None):
    """Apply a reflection reply ({"add", "update", "remove"}, at most 5 of each; the story so far: "chapter" with its
    "topics", "threads_add", "threads_done") to the memory and say what changed. -> {"add": [texts], "update":
    [texts], "remove": [texts]}, plus "chapter" (its text), "threads_add" (ids), "threads_done" (texts) when any."""
    mt = re.search(r"\{.*\}", text or "", re.S)
    try:
        d = json.loads(mt.group(0)) if mt else {}
    except ValueError:
        d = {}
    if not isinstance(d, dict):
        d = {}
    done: dict = {"add": [], "update": [], "remove": []}
    for key in ("add", "update", "remove"):          # a model without structured outputs may send one, not a list
        if not isinstance(d.get(key), list):
            d[key] = []
    for a in (d.get("add") or [])[:5]:
        if isinstance(a, dict):
            n, created = memory.add(a.get("text"), a.get("kind"), source="reflection")
            if n and created:
                done["add"].append(n["text"])
    for u in (d.get("update") or [])[:5]:
        if isinstance(u, dict) and str(u.get("id", "")).isdigit():
            n = memory.update(int(u["id"]), text=u.get("text"))
            if n:
                done["update"].append(n["text"])
    for rid in (d.get("remove") or [])[:5]:
        if str(rid).isdigit():
            for n in memory.remove(int(rid)):
                done["remove"].append(n["text"])
    # the story so far: a chapter of this stretch together, and the threads left open or closed
    chapter = d.get("chapter")
    chapter = chapter.strip() if isinstance(chapter, str) else ""
    topics = d.get("topics")
    topics = [t for t in topics if isinstance(t, str)][:8] if isinstance(topics, list) else []
    if chapter and memory.chapter_add(chapter, topics, t0=since, t1=now):
        done["chapter"] = chapter
    closed = d.get("threads_done")
    if isinstance(closed, list):
        done["threads_done"] = memory.thread_done([t for t in closed if isinstance(t, str)])
    opened = d.get("threads_add")
    if isinstance(opened, list):
        done["threads_add"] = [t for t in (memory.thread_add(x) for x in opened[:3] if isinstance(x, str)) if t]
    for t in done["add"]:
        emit({"kind": "info", "text": "Learned: %s" % t, "speak": False})
    for t in done["update"]:
        emit({"kind": "info", "text": "Updated what I remember: %s" % t, "speak": False})
    for t in done["remove"]:
        emit({"kind": "info", "text": "No longer remembering: %s" % t, "speak": False})
    return done


# ---- where items come from (research.py) ---------------------------------------------------------------------------
def item_tools():
    """The item tool definitions: item_info while the research worker runs, bis_slot while the best-in-slot ranker
    does (none before)."""
    return (list(ITEM_TOOLS) if research is not None else []) + (list(BIS_TOOLS) if bis is not None else [])


def run_item_tool(name, args):
    """item_info on the research worker's facts -> {"id", "name", "sources": [texts], "avail", "fact_ids"}; bis_slot on
    the best-in-slot ranks -> {"slot", "ranks": bis_slot rows}."""
    if name == "bis_slot":
        return bis_tool(args)
    r = research
    if r is None:
        return {"error": "item lookups are not available"}
    if name == "item_info":
        item = str((args or {}).get("item") or "").strip()
        if not item:
            return {"error": "which item?"}
        return r.item_info(item)
    return {"error": "unknown tool %r" % name}


_THE = r"(?:the |an? |my )?"
ITEM_QUESTIONS = [re.compile(p, re.I) for p in (
    r"where (?:does|do|did) %s(.+?) (?:drop|drops|come from|comes from)" % _THE,
    r"where (?:can|could|do|would|should) i (?:get|find|farm|buy) %s(.+?)" % _THE,
    r"where (?:to|can you) (?:get|find|farm|buy) %s(.+?)" % _THE,
    r"where (?:is|are) %s(.+?) (?:sold|from|dropped)" % _THE,
    r"(?:who|what) (?:sells|drops|makes|crafts|gives) %s(.+?)" % _THE,
    r"how (?:do|can|could|would|should) i (?:get|obtain|farm) %s(.+?)" % _THE,
    r"how to (?:get|obtain|farm) %s(.+?)" % _THE)]


THIS_ITEM = re.compile(r"(?:this|that|it)(?: (?:item|one|thing|piece))?", re.I)


def hovered_id(state):
    """The item id the player hovers in the game now (GameState.hovered: the addon holds it 15 s after the tooltip
    closed), or None."""
    fn = getattr(state, "hovered", None)
    try:
        hv = fn() if callable(fn) else None
    except Exception as e:  # noqa: BLE001 - no hovered item
        log_once("coach_tools.hovered", e)
        return None
    iid = hv.get("id") if isinstance(hv, dict) else None
    return iid if isinstance(iid, int) and not isinstance(iid, bool) and iid > 0 else None


def item_question(question, hovered=None):
    """The item a 'where does X drop?' / 'who sells X?' / 'how do I get X?' question asks about, or None. "this",
    "that" or "it" (THIS_ITEM) is the item the player hovers in the game (hovered: its id, GameState.hovered), as
    "#<id>" (research.Research._find reads it); without one, None."""
    q = " ".join((question or "").split()).strip(" .!?")
    for pat in ITEM_QUESTIONS:
        m = pat.fullmatch(q)
        if m:
            item = m.group(1).strip(" '\"") or None
            if item and THIS_ITEM.fullmatch(item):
                return "#%d" % hovered if isinstance(hovered, int) and hovered > 0 else None
            return item
    return None


LOOKING_UP = "I don't know where %s comes from yet; I'm looking it up, ask me again in a minute."


def item_command(question, ai_ok, hovered=None):
    """'where does Magister's Crown drop?' answered from the research facts while the AI is off (ai_ok false; with
    it the model asks item_info): "<item>: <where it comes from>." ("where does this drop?": the hovered item, see
    item_question); and 'what's best in slot for my neck?' from the best-in-slot ranks (bis_command). Returns the
    spoken answer, or None."""
    said = bis_command(question, ai_ok)
    if said:
        return said
    r = research
    if r is None or ai_ok:
        return None
    item = item_question(question, hovered)
    if not item:
        return None
    info = r.item_info(item)
    if not info or info.get("id") is None:
        return None
    if not info.get("sources"):
        return (LOOKING_UP if info.get("looking_up") else "I don't know where %s comes from yet.") % info["name"]
    name = str(info["name"])                     # ("this item" for one nothing names)
    return "%s: %s." % (name[:1].upper() + name[1:], info["sources"][0].rstrip("."))


def item_asked(question, hovered=None):
    """A 'where does X drop?' question, whoever answers it (the model with item_info, or item_command): its item goes
    to the research worker as a question (research.Research.ask_item: looked up on the web before anything else when
    nothing offline knows where it comes from; "this": the hovered item, see item_question). -> the item id, or None
    (no item question, an item no one knows)."""
    r = research
    item = item_question(question, hovered)
    if r is None or not item:
        return None
    try:
        return r.ask_item(item)
    except Exception as e:  # noqa: BLE001 - the question is answered all the same
        log_once("coach_tools.item_asked", e)
        return None


def item_brief(item_id):
    """What the research worker knows of an item without a lookup (research.Research.brief: the hovered item in a
    question's context), or None while it doesn't run."""
    r = research
    return r.brief(item_id) if r is not None else None


# ---- Glide's own best-in-slot ranks (bis.py) -------------------------------------------------------------------------
def _bis_now():
    """(the ranks of the character playing or None, the item ids worn) from the bis hook."""
    b = bis
    if b is None:
        return None, set()
    try:
        got = b() if callable(b) else b
    except Exception as e:  # noqa: BLE001 - no ranks
        log_once("coach_tools.bis", e)
        return None, set()
    return gear_mod.bis_parts(got)


def bis_slot(slot):
    """A slot's best-in-slot ranks for the character playing, best first (the coach's bis_slot tool): [{"item",
    "rank", "d" (DPS over what is worn now), "source" (where it comes from), "avail" (now, later, nosrc)}, with "worn",
    "about_same" and "needs" when so: gear.bis_rows]; [] when nothing is ranked for it."""
    cb, worn = _bis_now()
    if not cb:
        return []
    return gear_mod.bis_rows(cb, slot, worn, item_brief) or []


def bis_tool(args):
    """The bis_slot tool -> {"slot": the slot as the coach says it, "ranks": bis_slot rows}, or {"error"}."""
    slot = str((args or {}).get("slot") or "").strip()
    if not slot:
        return {"error": "which slot?"}
    if gear_mod.slot_group(slot) is None:
        return {"error": "no gear slot called %r" % slot}
    if _bis_now()[0] is None:
        return {"error": "Glide hasn't ranked this character's gear yet (it ranks at level 60, with WoWSims)"}
    return {"slot": gear_mod.slot_word(slot), "ranks": bis_slot(slot)}


BIS_QUESTIONS = [re.compile(p) for p in (
    r"(?:what is |which is |tell me )?(?:my |the )?(?:best in slot|bis)(?: item| gear| pick)? (?:for|in|on) (.+?)",
    r"(?:what is |which is |what are )?(?:my |the )?best (.+?)(?: for me| i can get)?",
    r"(?:what is |which is )?(?:my |the )?(.+?) (?:best in slot|bis)",
    r"(?:best in slot|bis) (.+?)")]


def bis_question(question):
    """The slot a 'what's best in slot for my neck?' / 'what's my best trinket?' / 'bis bracers' question asks about
    (a slot group, gear.slot_group), or None."""
    q = " ".join(str(question or "").lower().replace("best-in-slot", "best in slot").split()).strip(" .!?")
    q = re.sub(r"^what'?s ", "what is ", q)
    for pat in BIS_QUESTIONS:
        m = pat.fullmatch(q)
        if m:
            group = gear_mod.slot_group(m.group(1))
            if group:
                return group
    return None


def bis_command(question, ai_ok):
    """'what's best in slot for my neck?' answered from the best-in-slot ranks while the AI is off (ai_ok false;
    with it the model asks bis_slot): the best item the character can get now for the slot, in one line with one
    number. Returns the spoken answer, or None."""
    if ai_ok or bis is None:
        return None
    group = bis_question(question)
    if not group:
        return None
    said = gear_mod.SLOT_SPOKEN[group]
    cb, worn = _bis_now()
    if cb is None:
        return "I haven't ranked your gear yet; that takes level 60 and WoWSims."
    rows = gear_mod.bis_rows(cb, group, worn, item_brief, ids=True) or []
    best = next((r for r in rows if r.get("item") and (r.get("worn") or r.get("avail") == "now")), None)
    if best is None:
        return "I have nothing ranked for your %s yet." % said
    item = best["item"] + (" with %s" % best["with"] if best.get("with") else "")
    if best.get("worn"):
        return "Best for your %s: %s, and you wear it." % (said, best["item"])
    if best.get("with_set"):
        gain = "with its set"
    elif best.get("about_same") or not (best.get("d") or 0) > 0:
        gain = "about the same as yours"
    else:
        gain = "about +%s DPS" % gear_mod.dps_text(best["d"])
        if best.get("cond"):
            gain += ", not counting its bonus against some creatures"
    if best.get("in_bags"):
        return "Best for your %s: %s, %s, and it's in your bags." % (said, item, gain)
    src = str(best.get("source") or "").strip().rstrip(".")
    if src:
        _note_told(best.get("id"))
    return "Best for your %s: %s, %s%s." % (said, item, gain, (": " + src) if src else "")


def _note_told(item_id):
    """The research keeps a web source said aloud as told (Research.note_told): corrected once if the game shows it
    wrong."""
    note = getattr(research, "note_told", None)
    if note is None or item_id is None:
        return
    try:
        note(item_id)
    except Exception as e:  # noqa: BLE001 - said; no correction then
        log_once("coach_tools.bis_told", e)


# ---- feature switches said or typed ("quest briefings off") --------------------------------------------------------
FEATURES = (("quest_briefings", r"(?:quest\s+)?briefings?", "Quest briefings"),
            ("video_tips", r"(?:video|step video)\s+tips?", "Video tips"),
            ("step_videos", r"(?:step\s+|guide\s+)?videos?", "Step videos"))


def feature_switch(question):
    """'quest briefings off', 'turn on videos', 'stop the videos', 'enable briefings' -> (config key, on, label),
    or None for an ordinary question."""
    q = " ".join((question or "").lower().replace("-", " ").split()).strip(" .!?")
    for key, noun, label in FEATURES:
        on = None
        if re.fullmatch(r"(?:turn\s+|switch\s+)?(?:the\s+)?%s\s+(on|off)" % noun, q):
            on = q.endswith("on")
        elif re.fullmatch(r"(?:turn|switch)\s+(on|off)\s+(?:the\s+)?%s" % noun, q):
            on = q.split()[1] == "on"
        elif re.fullmatch(r"(stop|disable|no more|mute)\s+(?:the\s+)?%s" % noun, q):
            on = False
        elif re.fullmatch(r"(start|enable|resume)\s+(?:the\s+)?%s" % noun, q):
            on = True
        if on is not None:
            return key, on, label
    return None
