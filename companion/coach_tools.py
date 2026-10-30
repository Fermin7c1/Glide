"""The coach's own tools and spoken commands, next to the knowledge layer's: reminders ("remind me to repair"), the
memory of the player across sessions (and the reflection that updates it), and feature switches said out loud
("quest briefings off"). Tool definitions for the model, the tool calls, and the local handling of clear requests."""
import json
import re

import memory as memory_mod
import reminders as reminders_mod

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
# the tools the coach runs itself; every other tool call goes to the knowledge layer (api.dispatch)
LOCAL_TOOLS = ("reminder_add", "reminder_list", "reminder_done", "web_search",
               "memory_remember", "memory_forget", "memory_list")


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
    for e in events[-60:]:
        who = "player asked" if e["who"] == "player" else "you (%s)" % e.get("kind", "coach")
        lines.append("- %s: %s" % (who, " ".join(str(e["text"]).split())[:300]))
    for x in marks:
        lines.append("- player flagged your line \"%s\" as wrong/unhelpful%s" % (
            x["coach_said"][:200], ": " + x["note"] if x["note"] else ""))
    return lines


def apply_reflection(memory, text, emit):
    """Apply a reflection reply ({"add", "update", "remove"}, at most 5 of each) to the memory and say what
    changed. -> {"add": [texts], "update": [texts], "remove": [texts]}."""
    mt = re.search(r"\{.*\}", text or "", re.S)
    try:
        d = json.loads(mt.group(0)) if mt else {}
    except ValueError:
        d = {}
    done = {"add": [], "update": [], "remove": []}
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
    for t in done["add"]:
        emit({"kind": "info", "text": "Learned: %s" % t, "speak": False})
    for t in done["update"]:
        emit({"kind": "info", "text": "Updated what I remember: %s" % t, "speak": False})
    for t in done["remove"]:
        emit({"kind": "info", "text": "No longer remembering: %s" % t, "speak": False})
    return done


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
