"""Roleplay: the character the coach plays when the player asks for one ("talk like a pirate", "be a grumpy dwarf
drill sergeant", "you're Thrall now"). The model takes the request in any wording: it answers already in character
and adds a PERSONA: line (a brief of the character, for itself) and a VOICE: line (how a voice actor would perform
it), which the engine strips, applies and saves (llm.persona, llm.persona_voice). While one is set, every call gets
the PERSONA block as its own system block, and every spoken line carries the VOICE direction to the narrator (the
OpenAI voice performs it). "Drop the act" -> PERSONA: off. Offline (no model) there is no persona: the rule-based
lines stay plain."""
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
The coaching underneath stays exact: the same single most useful point, real names, numbers and directions from
the data, nothing invented. Lengths may stretch for flavour: callouts up to about %d words, spoken answers %d,
typed answers %d. The directive lines (FACTS, WATCH, SKIP, PERSONA) keep their exact plain format."""

# the last line of each request while a persona is set: small models drift back to the plain voice without it
REMINDER = "Stay fully in character (%s): say it the way they would."

_LINE = re.compile(r"^\s*(PERSONA|VOICE)\s*:\s*(.*)$", re.I)
_OFF = re.compile(r"^(?:off|none|normal|default|clear|no(?:ne)? persona|-)\W*$", re.I)


def clean(text):
    """A brief or a voice direction as kept: one line, at most MAX_CHARS."""
    t = " ".join(str(text or "").split()).strip()
    return t if len(t) <= MAX_CHARS else t[:MAX_CHARS - 1].rstrip(" ,;") + "…"


def split(text):
    """Pull the PERSONA: / VOICE: lines out of a reply -> (the rest of the text, persona, voice). persona: None (no
    line), "" (PERSONA: off) or the brief; voice: None or the direction."""
    keep, persona, voice = [], None, None
    for line in (text or "").splitlines():
        m = _LINE.match(line)
        if not m:
            keep.append(line)
            continue
        value = m.group(2).strip()
        if m.group(1).upper() == "PERSONA":
            persona = "" if _OFF.match(value) else clean(value)
        elif value:
            voice = clean(value)
    return "\n".join(keep).strip(), persona, voice


def short_name(brief, words=8):
    """The start of a brief, for the per-request reminder: 'a grizzled dwarf drill sergeant from Ironforge'."""
    first = re.split(r"(?<=[.!?;:])\s", brief or "", maxsplit=1)[0].strip().rstrip(".!?;:")
    w = first.split()
    return " ".join(w[:words]) + ("…" if len(w) > words else "")


def block(brief, limits):
    """The system block for a set persona; limits = (callout, spoken answer, typed answer) words."""
    return BLOCK % ((clean(brief),) + tuple(int(x) for x in limits))
