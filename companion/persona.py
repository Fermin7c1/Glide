"""Roleplay: the character the coach plays when the player asks for one ("talk like a pirate", "be a grumpy dwarf
drill sergeant", "you're Thrall now"). The model takes the request in any wording: it answers already in character
and adds a PERSONA: line (a brief of the character, for itself), a NAME: line (what the player can call it back by)
and a VOICE: line (how a voice actor would perform it), which the engine strips, applies and saves (llm.persona,
llm.persona_name, llm.persona_voice; and the character in the player memory, Memory.characters). "PERSONA: recall
<name>" brings a character played before back, with everything it said about itself (LORE: lines, kept so it never
contradicts itself). While one is set, every call gets the PERSONA block as part of the memory's system block, every
reply starts with a TONE: line (how to deliver this line, performed by the OpenAI voice with the VOICE direction),
and every spoken line carries the voice direction to the narrator. "Drop the act" -> PERSONA: off. Offline (no
model) there is no persona: the rule-based lines stay plain."""
import re

MAX_CHARS = 600            # a brief or a voice direction longer than this is cut (they ride along on every call)

# the system block sent while a persona is set (after the coach rules: it overrides their tone)
BLOCK = """YOU ARE IN CHARACTER. The player asked you to play this role, and they want to believe it:
%s

Play it with total commitment, in every line you say: callouts, answers, briefings, reviews. This overrides the
style rules' tone ("no praise", "plain and direct", "a tool, not a friend"): the character's attitude, vocabulary,
rhythm, dialect, catchphrases, pet names, exclamations, insults or flattery are what the player asked for. Sound
like the genuine article, never a polite imitation: no hedging, no winking at the act, no "as a pirate I'd say",
never step out of character or mention that you are playing one.
Everything is spoken aloud: express it in words alone (interjections, sounds spelled out like "Arrr" or "Hmph",
dialect spellings, pauses with "..."); no stage directions, asterisks, brackets or emoji.
Start every reply (except SKIP) with a line "TONE: <2-6 words: how to deliver this line, e.g. muttering, bellowing,
mock-whisper, laughing, choked up>" (not spoken; vary it with the moment and your mood).
When you make up something about yourself (your past, scars, family, opinions, habits), add a final line "LORE:
<that fact, one short sentence>" so you remember it. Once it's clear who the player is to you (your recruit, your
captain, your apprentice, your rival), add a final line "ROLE: <that, and what you call them>" (once; again only if
it changes). When the player sets up a scene to play together ("let's be bounty hunters after Hogger"), play it and
add a final line "SCENE: <the premise, 1-2 sentences>" (once; again only if it changes), and "SCENE: end" when it's
over or they drop it. When the player wants a second character along (a sidekick, a rival, a pet: "bring Fizzle
along", "add a gnome tinker"), add a final line "SIDEKICK: <its name> | <a vivid brief of it, 1-3 sentences, with
all the player said of it: its past, how it and you are tied, who the player is to it>" (just "SIDEKICK: <its name>"
for one CHARACTERS YOU HAVE PLAYED lists), and "SIDEKICK: off" when they send it away.%s
The coaching underneath stays exact: the same single most useful point, real names, numbers and directions from
the data, nothing invented. Lengths may stretch for flavour: callouts up to about %d words, spoken answers %d,
typed answers %d. The directive lines (TONE, FACTS, WATCH, SKIP, PERSONA, LORE, ROLE, SCENE, SIDEKICK) keep their exact plain
format."""

LORE_HEAD = "\nYOUR OWN STORY SO FAR (what you have told them about yourself: never contradict it, build on it):\n"
SAMPLES_HEAD = "\nHOW YOU SOUND (lines in your own voice: keep this voice, never reuse them word for word):\n"
PLOT_HEAD = ("\nYOUR OWN STORY THREADS (what you want, what's going on with you: let it move forward slowly as they "
             "level, now and then):\n")
ROLE_HEAD = "\nWHO THEY ARE TO YOU: "
SCENE_HEAD = "\nTHE SCENE YOU'RE PLAYING TOGETHER (keep it going; tie it to their real quests and fights): "

# the last line of each request while a persona is set: small models drift back to the plain voice without it
REMINDER = "Stay fully in character (%s): say it the way they would."

# PERSONA in any case ("persona: off"); the others only as written, upper-case: an answer's own "Role: tank or
# healer" or "Name: Hogger" line is the answer's
# (the sidekick's: SIDEKICK LORE, SIDEKICK ROLE, and TIES between the two characters)
_LINE = re.compile(r"^\s*(?:((?i:PERSONA))|(VOICE|NAME|LORE|TONE|ROLE|SCENE|SIDEKICK\s+LORE|SIDEKICK\s+ROLE|SIDEKICK|"
                   r"TIES))\s*:\s*(.*)$")
_MIDLINE = re.compile(r"(?<=\S)(?<!SIDEKICK)\s+(?=(?:VOICE|NAME|LORE|TONE|ROLE|SCENE|SIDEKICK|TIES|PERSONA)\s*:\s"
                      r"|SIDEKICK\s+(?:LORE|ROLE)\s*:\s)")
_SCENE_END = re.compile(r"^(?:end(?:ed|s)?|over|off|none|done|finished|stop(?:ped)?)\b", re.I)
_OFF = re.compile(r"^(?:off|none|normal|default|clear|no(?:ne)? persona|-)\W*$", re.I)
_RECALL = re.compile(r"^(?:recall|restore)\b[\s:]+(.+?)\W*$", re.I)     # "recall Sarge" (not "Recalling...")
# a reply that takes on a character (or recalls one): its persona lines are read even before one is played
STARTS = re.compile(r"^\s*PERSONA\s*:", re.I | re.M)


def clean(text):
    """A brief or a voice direction as kept: one line, at most MAX_CHARS."""
    t = " ".join(str(text or "").split()).strip()
    return t if len(t) <= MAX_CHARS else t[:MAX_CHARS - 1].rstrip(" ,;") + "…"


def split(text):
    """Pull the PERSONA: / NAME: / VOICE: / LORE: / TONE: / ROLE: / SCENE: lines out of a reply -> (the rest of the
    text, found): found["persona"]: None (no line), "" (PERSONA: off) or the brief; "recall": a name (PERSONA: recall
    <name>); "name", "voice", "tone", "role": the line's text; "lore": [facts]; "scene": None, "" (SCENE: end) or the
    premise; "sidekick" (SIDEKICK: below); the sidekick's "side_lore": [facts], "side_role" and the two characters'
    "ties": the line's text."""
    keep, found = [], {"persona": None, "recall": None, "name": None, "voice": None, "tone": None, "lore": [],
                       "role": None, "scene": None, "sidekick": None, "side_lore": [], "side_role": None, "ties": None}
    for line in _MIDLINE.sub("\n", text or "").splitlines():     # one written mid-line is a line of its own
        m = _LINE.match(line)
        if not m:
            keep.append(line)
            continue
        what, value = " ".join((m.group(1) or m.group(2)).split()).lower(), m.group(3).strip()
        if what == "persona":
            r = _RECALL.match(value)
            if r:
                found["recall"] = r.group(1).strip()
            else:
                found["persona"] = "" if _OFF.match(value) else clean(value)
        elif what in ("lore", "sidekick lore"):
            if value:
                found["side_lore" if what == "sidekick lore" else "lore"].append(clean(value))
        elif what in ("sidekick role", "ties"):
            if value:
                found["side_role" if what == "sidekick role" else "ties"] = clean(value)
        elif what == "scene":
            found["scene"] = "" if _SCENE_END.match(value) else clean(value)
        elif what == "sidekick":
            # "SIDEKICK: off" -> ("", ""); "SIDEKICK: <name> | <brief>" a new one; "SIDEKICK: <name>" one played before
            if _OFF.match(value) or _SCENE_END.match(value):
                found["sidekick"] = ("", "")
            else:
                name, _, brief = value.partition("|")
                found["sidekick"] = (clean(name).strip(" .")[:60], clean(brief))
        elif value:
            found[what] = clean(value).strip(" .")[:80] if what in ("name", "tone") else clean(value)
    return "\n".join(keep).strip(), found


def short_name(brief, words=8):
    """The start of a brief, for the per-request reminder: 'a grizzled dwarf drill sergeant from Ironforge'."""
    first = re.split(r"(?<=[.!?;:])\s", brief or "", maxsplit=1)[0].strip().rstrip(".!?;:")
    w = first.split()
    return " ".join(w[:words]) + ("…" if len(w) > words else "")


COMPANION = """YOUR COMPANION ON THE ROAD: %(name)s, a second character along with you: %(brief)s%(more)s
Now and then (mostly in your unprompted asides, at big moments, or when the player speaks to them) give them a line
of their own: on a line of its own that starts with "[%(name)s]", in their voice, not yours; they can tease you, argue,
chime in, bring up their past or yours. At most two of their lines in a reply, never in plain directions (the coaching
is yours). When the player wants %(name)s to take the lead ("let me talk to %(name)s"), add a final line "PERSONA:
recall %(name)s": you swap places, and you come along as theirs.
Their past and your history together are real to both of you: never contradict them, build on them. When %(name)s
reveals something new about their past (or the player gives them one), add a final line "SIDEKICK LORE: <that fact,
one short sentence>". When the player says how the two of you are tied (your history, what you feel about each other,
a debt, a rivalry), add a final line "TIES: <that, 1-2 sentences, by name>"; when it's clear who the player is to
%(name)s, add "SIDEKICK ROLE: <that, and what they call them>" (each once; again only if it changes). Those lines
keep their exact plain format."""


def companion(name, ch, ties=""):
    """The block's part for the sidekick (cast.py): who it is, how it sounds, its past and its own story, how it and
    the character played are tied (ties: Memory.tie), who the player is to it."""
    ch = ch or {}
    more = ""
    if ch.get("samples"):
        more += "\nHow they sound: " + " / ".join('"%s"' % x for x in (ch["samples"] or [])[:3])
    lore = list(ch.get("lore") or ())
    if lore:      # its past first (kept for good: Memory.LORE_KEPT), then the newest
        more += "\nTheir past and their story so far: " + "; ".join(lore if len(lore) <= 12 else lore[:6] + lore[-6:])
    if ch.get("plot"):
        more += "\nTheir own story threads: " + "; ".join(ch["plot"])
    if ties:
        more += "\nYou and %s: %s" % (name, clean(ties))
    if ch.get("role"):
        more += "\nWho the player is to them: " + clean(ch["role"])
    return COMPANION % {"name": name, "brief": clean(ch.get("brief") or ""), "more": more}


def side_seed_request(name, ch, main, main_ch, ties=""):
    """The SIDE_SEED request for a sidekick: who it is and what is known of it, the character it travels with, and
    how they are tied when that is known already (stay consistent). ch None: only its name is known (one named in
    Settings): who it is is asked for too ("brief", SIDE_SEED_NEW_SCHEMA)."""
    new = not (ch or {}).get("brief")
    ch, main_ch = ch or {}, main_ch or {}
    known = list(ch.get("lore") or ())[:24] + (["Who the player is to them: " + ch["role"]] if ch.get("role") else [])
    main_known = (["Who the player is to %s: %s" % (main, main_ch["role"])] if main_ch.get("role") else []) + \
        (["How the two are tied: " + ties] if ties else [])
    return SIDE_SEED % {
        "name": name, "main": main, "main_brief": clean(main_ch.get("brief")),
        "brief": ("(only the name is known: make them up, someone who fits beside %s)" % main) if new else
        clean(ch.get("brief")), "ask_brief": ('"brief": "who %s is, 1-3 vivid sentences", ' % name) if new else "",
        "known": ("\nWhat is known of them already:\n" + "\n".join("- " + x for x in known)) if known else "",
        "main_known": ("\n" + "\n".join("- " + x for x in main_known)) if main_known else ""}


def seed_request(brief, lore=(), role=""):
    """The SEED request for a character: its brief, and what it already said about itself (stay consistent)."""
    known = list(lore or ()) + (["Who the player is to it: " + role] if role else [])
    return SEED % (clean(brief), ("\nStay consistent with what it has already said about itself:\n" +
                                  "\n".join("- " + x for x in known[:24])) if known else "")


def block(brief, limits, lore=(), samples=(), plot=(), role="", scene=""):
    """The system block for a set persona; limits = (callout, spoken answer, typed answer) words; lore = the facts
    the character told about itself before; samples = lines in its voice (its sample lines and the ones the player
    liked); plot = its own story threads; role = who the player is to it; scene = the premise being played."""
    story = (LORE_HEAD + "\n".join("- " + x for x in lore)) if lore else ""
    story += (SAMPLES_HEAD + "\n".join("- " + x for x in samples)) if samples else ""
    story += (PLOT_HEAD + "\n".join("- " + x for x in plot)) if plot else ""
    story += (ROLE_HEAD + clean(role)) if role else ""
    story += (SCENE_HEAD + clean(scene)) if scene else ""
    return BLOCK % ((clean(brief), story) + tuple(int(x) for x in limits))


# the request that gives a new character its sample lines and its own story threads (once, when first played)
SEED = """You write for a character the player asked their World of Warcraft leveling coach to play:
%s%s
Reply with ONLY a JSON object: {"samples": [5 short lines this character would say to the player while they level,
each a different kind: a quip, a scolding, praise, a scrap of its own story, a question about them], "plot": [2 of the
character's own story threads that can move forward slowly over a long climb to level 60: something it wants, something
it's hiding or working on]}. Spoken lines only: no stage directions, no emoji."""

SEED_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["samples", "plot"],
               "properties": {"samples": {"type": "array", "items": {"type": "string"}},
                              "plot": {"type": "array", "items": {"type": "string"}}}}

# the request that gives a sidekick its past, its sample lines, its own story threads, its tie to the character played
# and who the player is to it (once, when it first comes along beside that character; only what it lacks is kept)
SIDE_SEED = """You write for a second character who travels with the one a World of Warcraft leveling coach plays for
the player.
The second character, %(name)s: %(brief)s%(known)s
They travel with %(main)s: %(main_brief)s%(main_known)s
Reply with ONLY a JSON object: {%(ask_brief)s"backstory": [3 facts of %(name)s's past, one short sentence each: where they come
from, what shaped them, something they never talk about], "samples": [5 short lines %(name)s would say while the player
levels, each a different kind: a jab at %(main)s, a reaction to the player, a scrap of their past, a worry, a boast],
"plot": [2 of %(name)s's own story threads that can move forward slowly over a long climb to level 60], "ties": "how
%(name)s and %(main)s are tied, 1-2 sentences, by name: their history, what each feels about the other, a sore spot",
"role": "who the player is to %(name)s, and what %(name)s calls them, one short sentence"}. Stay consistent with
everything known above, their brief above all (what the player said of them). Spoken lines only in samples: no stage
directions, no emoji."""

SIDE_SEED_SCHEMA = {"type": "object", "additionalProperties": False,
                    "required": ["backstory", "samples", "plot", "ties", "role"],
                    "properties": {"backstory": {"type": "array", "items": {"type": "string"}},
                                   "samples": {"type": "array", "items": {"type": "string"}},
                                   "plot": {"type": "array", "items": {"type": "string"}},
                                   "ties": {"type": "string"}, "role": {"type": "string"}}}
SIDE_SEED_NEW_SCHEMA = dict(SIDE_SEED_SCHEMA, required=["brief"] + SIDE_SEED_SCHEMA["required"],
                            properties=dict(SIDE_SEED_SCHEMA["properties"], brief={"type": "string"}))
