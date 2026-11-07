"""The cast: a second character on the road with the one the coach plays (the sidekick, llm.sidekick: a saved character
by name, Memory.characters). The coach's replies give it lines of its own, each on a line of its own that starts with
"[<its name>]" (brackets are never part of what's spoken otherwise: persona.BLOCK); here they are split out (parts),
shown as "<name>: <line>" in the chat, and spoken in a voice of their own (voice_for: another OpenAI voice; the app's
narrator picks another Windows voice). Pure."""
import hashlib
import re

# "[Fizzle] line", "[Fizzle]: line", "[Fizzle] - line"; never a link ("[Wowhead](https://...)")
SPEAKER = re.compile(r"^\s*\[([^\]\n]{1,40})\](?!\()\s*(?::|[-\u2013\u2014](?=\s))?\s*(.*)$")
# one written mid-line ("Move it! [Fizzle] Boom?")
_TAG = re.compile(r"(?<=\S)\s+(?=\[([^\]\n]{1,40})\](?!\())")
# the OpenAI voices a sidekick may get, by its name: the ones every OpenAI voice model has (desktop/src/main/tts.ts
# OPENAI_VOICES; ballad and verse are gpt-4o-mini-tts's only)
VOICES = ("ash", "echo", "fable", "onyx", "sage", "shimmer", "nova", "alloy", "coral")


def same(a, b):
    """Whether two names are one character's ("Fizzle", "fizzle the tinker")."""
    a, b = " ".join(str(a or "").casefold().split()), " ".join(str(b or "").casefold().split())
    return bool(a and b) and (a == b or a.startswith(b + " ") or b.startswith(a + " "))


def _lines(text, main, side):
    """A reply's lines, one either character starts mid-line ("Move it! [Fizzle] Boom?") split off."""
    for line in (text or "").splitlines():
        at = 0
        for m in _TAG.finditer(line):
            if same(m.group(1), side) or same(m.group(1), main):
                yield line[at:m.start()]
                at = m.end()
        yield line[at:]


def parts(text, main, side):
    """A reply's lines by who says them -> [{"who", "text"}, the sidekick's with "side": true] (consecutive lines of
    one speaker joined); lines starting "[<side>]" are the sidekick's, "[<main>]" the main character's (its brackets
    dropped); any other line is the main character's as it is."""
    out = []
    for line in _lines(text, main, side):
        m = SPEAKER.match(line)
        who, said = main, line.strip()
        if m and (same(m.group(1), side) or same(m.group(1), main)):
            said = m.group(2).strip()
            if same(m.group(1), side):
                who = side
        if not said:
            continue
        if out and out[-1]["who"] == who:
            out[-1]["text"] = (out[-1]["text"] + " " + said).strip()
        else:
            out.append(dict({"who": who, "text": said}, **({"side": True} if who == side and side else {})))
    return out


def has_side(rows, side):
    return bool(side) and any(r["who"] == side for r in rows)


def script(rows):
    """The parts as the model writes them (the sidekick's lines "[<name>] <line>"): what the conversation keeps, so
    the model reads back the form it is asked to write, never the chat's "<name>: <line>"."""
    return "\n".join("[%s] %s" % (r["who"], r["text"]) if r.get("side") else r["text"] for r in rows)


def display(rows, main):
    """The chat's text for parts: the main character's lines as they are, the sidekick's as "<name>: <line>"."""
    return "\n".join(r["text"] if r["who"] == main else "%s: %s" % (r["who"], r["text"]) for r in rows)


def voice_for(name, avoid=""):
    """The OpenAI voice a sidekick speaks with (by its name, not `avoid`, the main character's)."""
    pool = [v for v in VOICES if v != avoid] or list(VOICES)
    return pool[int(hashlib.sha1(str(name).casefold().encode("utf-8")).hexdigest(), 16) % len(pool)]
