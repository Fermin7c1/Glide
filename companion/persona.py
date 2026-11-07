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
over or they drop it.%s
The coaching underneath stays exact: the same single most useful point, real names, numbers and directions from
the data, nothing invented. Lengths may stretch for flavour: callouts up to about %d words, spoken answers %d,
typed answers %d. The directive lines (TONE, FACTS, WATCH, SKIP, PERSONA, LORE, ROLE, SCENE) keep their exact plain format."""

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
_LINE = re.compile(r"^\s*(?:((?i:PERSONA))|(VOICE|NAME|LORE|TONE|ROLE|SCENE))\s*:\s*(.*)$")
_MIDLINE = re.compile(r"(?<=\S)\s+(?=(?:VOICE|NAME|LORE|TONE|ROLE|SCENE|PERSONA)\s*:\s)")
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
    premise."""
    keep, found = [], {"persona": None, "recall": None, "name": None, "voice": None, "tone": None, "lore": [],
                       "role": None, "scene": None}
    for line in _MIDLINE.sub("\n", text or "").splitlines():     # one written mid-line is a line of its own
        m = _LINE.match(line)
        if not m:
            keep.append(line)
            continue
        what, value = (m.group(1) or m.group(2)).lower(), m.group(3).strip()
        if what == "persona":
            r = _RECALL.match(value)
            if r:
                found["recall"] = r.group(1).strip()
            else:
                found["persona"] = "" if _OFF.match(value) else clean(value)
        elif what == "lore":
            if value:
                found["lore"].append(clean(value))
        elif what == "scene":
            found["scene"] = "" if _SCENE_END.match(value) else clean(value)
        elif value:
            found[what] = clean(value).strip(" .")[:80] if what in ("name", "tone") else clean(value)
    return "\n".join(keep).strip(), found


def short_name(brief, words=8):
    """The start of a brief, for the per-request reminder: 'a grizzled dwarf drill sergeant from Ironforge'."""
    first = re.split(r"(?<=[.!?;:])\s", brief or "", maxsplit=1)[0].strip().rstrip(".!?;:")
    w = first.split()
    return " ".join(w[:words]) + ("…" if len(w) > words else "")


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
