"""What the coach does with a model's reply text: the FACTS line split off (machine-only fact ids), WATCH/UNWATCH
directive lines found, SKIP recognised, the length capped at whole sentences, and a streamed reply spoken sentence by
sentence while it arrives (EarlySpeech)."""
import re


_FACTS_LINE = re.compile(r"^\s*[*_`>-]*\s*facts?\s*[*_`]*\s*:\s*(.*)$", re.I)
_FACT_START = re.compile(r"^(?:q|c|i|p|h|s|g|n|poi|web):", re.I)


def parse_fact_ids(text):
    """'q:1, s:5-11 Dun Morogh/7, poi:flight:2' -> ids (fragments that don't start a fact join the previous one)."""
    t = (text or "").strip().strip("`*").strip()
    if not t or t.lower().rstrip(".") in ("none", "-", "n/a"):
        return []
    out = []
    for part in t.split(","):
        p = part.strip().strip("`*[]()").rstrip(".;").strip()
        if not p:
            continue
        if _FACT_START.match(p) or not out:
            out.append(p)
        else:
            out[-1] += "," + p
    return list(dict.fromkeys(out))


def split_facts(text):
    """Strip "FACTS: ..." lines (also one glued to the end of the last line). -> (text, fact ids, had_line)."""
    keep, ids, had = [], [], False
    for line in (text or "").splitlines():
        m = _FACTS_LINE.match(line)
        if m:
            had = True
            ids += parse_fact_ids(m.group(1))
            continue
        i = line.find("FACTS:")
        if i > 0:
            had = True
            ids += parse_fact_ids(line[i + 6:])
            line = line[:i].rstrip()
        keep.append(line)
    return "\n".join(keep).strip(), list(dict.fromkeys(ids)), had


def trim_words(text, limit):
    """Hard cap on length: keep whole sentences while they fit; always keep the first one, cut at the limit."""
    if len(text.split()) <= limit:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    out, n = [], 0
    for sen in sentences:
        w = len(sen.split())
        if out and n + w > limit:
            break
        out.append(sen)
        n += w
    res = " ".join(out)
    words = res.split()
    if len(words) > limit + 6:                     # one run-on first sentence: cut it
        res = " ".join(words[:limit]).rstrip(",;:") + "."
    return res


def is_skip(text):
    """The model's "nothing worth saying" reply (SKIP, give or take a full stop)."""
    return text.strip().strip(".!").upper() == "SKIP"


def split_watch(text):
    """Pull the WATCH: / UNWATCH: <buff> lines out of a reply -> (the rest of the text, [(buff name, on), ...])."""
    keep, found = [], []
    for line in text.splitlines():
        t = line.strip()
        up = t.upper()
        if up.startswith("WATCH:") or up.startswith("UNWATCH:"):
            name = t.split(":", 1)[1].strip().strip(".")
            if name:
                found.append((name, up.startswith("WATCH:")))
        else:
            keep.append(line)
    return "\n".join(keep).strip(), found


class EarlySpeech:
    """Speaks a streamed reply sentence by sentence while it arrives, instead of after the whole reply (and its
    FACTS line) is done. Stops at SKIP / FACTS / WATCH lines and at the word limit the reply will be trimmed to, so it
    never says what the final text won't contain. `spoken` (text already said, from the start of the reply, after
    `prefix`) goes on the final message; the app then says only the rest."""
    _END = re.compile(r"[.!?](?=\s)")

    def __init__(self, speak, limit_words, urgent=False, prefix=""):
        self.speak, self.limit, self.urgent = speak, limit_words, urgent
        self.buf, self.used, self.words, self.stopped = "", 0, 0, False
        self.spoken = prefix.strip()

    def __call__(self, chunk):
        if self.stopped:
            return
        self.buf += chunk
        while not self.stopped:
            rest = self.buf[self.used:]
            m = self._END.search(rest)
            if not m:
                return
            sentence = " ".join(rest[:m.end()].split())
            self.used += m.end()
            up = sentence.upper()
            n = len(sentence.split())
            if not sentence or up.startswith(("SKIP", "FACTS", "WATCH:", "UNWATCH:")) or "FACTS:" in up \
                    or self.words + n > self.limit:
                self.stopped = True
                return
            self.speak(sentence, self.urgent and not self.words)
            self.words += n
            self.spoken = (self.spoken + " " + sentence).strip()

    @property
    def said(self):
        """Whether any of the reply itself was spoken early."""
        return self.words > 0
