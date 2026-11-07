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
<that fact, one short sentence>" so you remember it.%s
The coaching underneath stays exact: the same single most useful point, real names, numbers and directions from
the data, nothing invented. Lengths may stretch for flavour: callouts up to about %d words, spoken answers %d,
typed answers %d. The directive lines (TONE, FACTS, WATCH, SKIP, PERSONA, LORE) keep their exact plain format."""

LORE_HEAD = "\nYOUR OWN STORY SO FAR (what you have told them about yourself: never contradict it, build on it):\n"

# the last line of each request while a persona is set: small models drift back to the plain voice without it
REMINDER = "Stay fully in character (%s): say it the way they would."

_LINE = re.compile(r"^\s*(PERSONA|VOICE|NAME|LORE|TONE)\s*:\s*(.*)$", re.I)
_OFF = re.compile(r"^(?:off|none|normal|default|clear|no(?:ne)? persona|-)\W*$", re.I)
_RECALL = re.compile(r"^(?:recall|restore)\b[\s:]+(.+?)\W*$", re.I)     # "recall Sarge" (not "Recalling...")
# a reply that takes on a character (or recalls one): its persona lines are read even before one is played
STARTS = re.compile(r"^\s*PERSONA\s*:", re.I | re.M)


def clean(text):
    """A brief or a voice direction as kept: one line, at most MAX_CHARS."""
    t = " ".join(str(text or "").split()).strip()
    return t if len(t) <= MAX_CHARS else t[:MAX_CHARS - 1].rstrip(" ,;") + "…"


def split(text):
    """Pull the PERSONA: / NAME: / VOICE: / LORE: / TONE: lines out of a reply -> (the rest of the text, found):
    found["persona"]: None (no line), "" (PERSONA: off) or the brief; "recall": a name (PERSONA: recall <name>);
    "name", "voice", "tone": the line's text; "lore": [facts]."""
    keep, found = [], {"persona": None, "recall": None, "name": None, "voice": None, "tone": None, "lore": []}
    for line in (text or "").splitlines():
        m = _LINE.match(line)
        if not m:
            keep.append(line)
            continue
        what, value = m.group(1).lower(), m.group(2).strip()
        if what == "persona":
            r = _RECALL.match(value)
            if r:
                found["recall"] = r.group(1).strip()
            else:
                found["persona"] = "" if _OFF.match(value) else clean(value)
        elif what == "lore":
            if value:
                found["lore"].append(clean(value))
        elif value:
            found[what] = clean(value).strip(" .")[:80] if what in ("name", "tone") else clean(value)
    return "\n".join(keep).strip(), found


def short_name(brief, words=8):
    """The start of a brief, for the per-request reminder: 'a grizzled dwarf drill sergeant from Ironforge'."""
    first = re.split(r"(?<=[.!?;:])\s", brief or "", maxsplit=1)[0].strip().rstrip(".!?;:")
    w = first.split()
    return " ".join(w[:words]) + ("…" if len(w) > words else "")


def block(brief, limits, lore=()):
    """The system block for a set persona; limits = (callout, spoken answer, typed answer) words; lore = the facts
    the character told about itself before."""
    story = (LORE_HEAD + "\n".join("- " + x for x in lore)) if lore else ""
    return BLOCK % ((clean(brief), story) + tuple(int(x) for x in limits))
