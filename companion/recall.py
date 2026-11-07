"""Recall: what this moment brings back, for the character's lines (banter.py, Coach._respond in character). Like a
friend's memory, by topic, not by date (Nomi's "topical" recall, Kindroid's journal): the moment's words (the player's
question, the place, the target, the moment the aside reacts to) are matched against everything kept together (the
diary of shared moments, the chapters and eras of the story so far, the notes about the player), ranked by relevance,
then recency; what was brought up lately stays out while anything else fits, so the same memory doesn't come back
every line; the moment itself and what the prompt already holds never come back as a memory. Only the player's words
and the game feed the query, never the character's own lines (they'd loop on themselves); the place counts for a little,
so a zone alone rarely brings anything back.
Pure; the Banter keeps what was recalled lately (and the last line's picks, for "why did you bring that up?")."""
import math
import re
import time

WORD = re.compile(r"[^\W\d_][\w'-]{3,}", re.U)     # words of 4+ letters (any script)
STOP = frozenset("""this that with from have what when where which there their them they your youre about would could
should just like into over then than been were will some more much very only also even back here want need going
know think make take give tell come look right well good really still never ever does doing done said says okay yeah
level quest quests time today again first last next mine yours ours""".split())
HALF_LIFE_DAYS = 10.0       # a memory's recency weight halves every this many days
PHRASE = 3.0                # a topic or zone phrase of the memory said in the moment's words
WHERE_WORD, WHERE_PHRASE = 0.5, 1.0     # ... in the place (zone, subzone, target) only


def words(text):
    """The meaningful words of a text, lowercased."""
    return {w.casefold().strip("'-") for w in WORD.findall(text or "")} - STOP


def diary_id(x):
    return "d:%.3f:%s" % (x["t"], x["kind"])


def story_id(x):
    return "%s:%.3f" % ("e" if x.get("era") else "c", x["t0"])


def items(memory):
    """Everything recallable: [{"id", "text", "t", "keys": set, "phrases": [..], "what"}]."""
    if memory is None:
        return []
    out = []
    for x in memory.diary_recent(10 ** 6):
        out.append({"id": diary_id(x), "text": x["text"], "t": x["t"], "what": "moment",
                    "keys": words(x["text"]), "phrases": [x["zone"]] if x.get("zone") else []})
    for x in memory.story():
        out.append({"id": story_id(x), "text": x["text"], "t": x["t1"],
                    "what": "era" if x.get("era") else "chapter", "keys": words(x["text"]) | words(" ".join(x["topics"])),
                    "phrases": list(x["topics"])})
    for n in memory.list():
        if n["kind"] in ("rapport", "fact", "goal"):
            out.append({"id": "m%d" % n["id"], "text": n["text"], "t": n.get("updated") or 0, "what": "note",
                        "keys": words(n["text"]), "phrases": []})
    return out


def said(phrase, text):
    """Whether `phrase` is in `text` as whole words ("bet" not in "better")."""
    return bool(phrase) and re.search(r"(?<!\w)%s(?!\w)" % re.escape(phrase.casefold()), text) is not None


def pick(memory, query, k=3, recent=(), now=None, where="", exclude=(), before=None, place_alone=False):
    """The k memories this moment brings back (best first), each {"id", "text", "t", "what", "score"}; [] when
    nothing matches. query: the moment's words (a question, the moment an aside reacts to); where: the place (zone,
    subzone, target: weighs little); recent: ids recalled lately (left out while others fit); exclude: ids already in
    the prompt; before: only memories older than this (not the moment itself); place_alone: the place may bring a
    memory back by itself (a quiet-moment aside); otherwise a memory needs a word of the query."""
    q, w = words(query), words(where) - words(query)
    ql, wl = (query or "").casefold(), (where or "").casefold()
    if not q and not w:
        return []
    now = time.time() if now is None else now
    scored = []
    for it in items(memory):
        if it["id"] in exclude or (before is not None and (it["t"] or 0) >= before):
            continue
        hit = len(q & it["keys"]) + PHRASE * sum(1 for p in it["phrases"] if said(p, ql))
        if hit < 1 and not place_alone:
            continue
        hit += WHERE_WORD * len(w & it["keys"]) + WHERE_PHRASE * sum(1 for p in it["phrases"] if said(p, wl))
        if hit < 1:
            continue
        age_days = max(0.0, (now - (it["t"] or now)) / 86400.0)
        scored.append(dict(it, score=round(hit * (0.5 + 0.5 * math.pow(0.5, age_days / HALF_LIFE_DAYS)), 3),
                           lately=it["id"] in recent))
    fresh = [x for x in scored if not x["lately"]]
    scored = fresh or scored                          # what came up lately only when nothing else fits
    scored.sort(key=lambda x: -x["score"])
    picked, seen = [], set()
    for it in scored:                                    # variety: not two memories saying the same thing
        sig = frozenset(list(it["keys"])[:6])
        if sig in seen:
            continue
        seen.add(sig)
        picked.append({kk: it[kk] for kk in ("id", "text", "t", "what", "score")})
        if len(picked) >= k:
            break
    return picked


def ago(seconds):
    """'just now', '25 minutes ago', '3 hours ago', '2 days ago'."""
    s = max(0, int(seconds))
    if s < 120:
        return "just now"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if s >= size * (2 if unit != "minute" else 1):
            n = s // size
            return "%d %s%s ago" % (n, unit, "" if n == 1 else "s")
    return "just now"


def section(picked, now=None):
    """The request's section for what was recalled ('' when nothing)."""
    if not picked:
        return ""
    now = time.time() if now is None else now
    return ("THIS BRINGS BACK (from your time together; weave one in only if it fits naturally, never list them):\n" +
            "\n".join("- %s (%s): %s" % (ago(now - p["t"]) if p["t"] else "a while ago", p["what"], p["text"])
                      for p in picked))
