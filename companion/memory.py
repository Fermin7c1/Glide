"""The coach's long-term memory of the player: coach_memory.json in memory_dir().

What the player tells or teaches the coach outlives the session: preferences about how to be coached, goals, standing
requests, corrections of the coach's mistakes, and facts about the player and their playstyle. Each note:

    {"id": 7, "kind": "preference", "text": "Wants callouts under 10 words.", "source": "player" | "coach" |
     "reflection" | "mark", "created": <unix>, "updated": <unix>, "pinned": false, "used": 3}

Notes come from the player ("remember that ...", "from now on ...", the app's Memory page), from the coach's
memory_remember tool, from "/glide mark <note>" feedback, and from reflection: now and then the deep model reviews the
recent conversation and feedback against the notes and proposes additions, rewrites and removals (Coach.reflect).

Where it lives (memory_dir): GLIDE_MEMORY_DIR when set, else %APPDATA%\\Glide\\memory on Windows (~/.glide/memory
elsewhere): outside the app's install folder and the engine home, so it survives app updates, reinstalls, a moved
engine home and the plain start-glide.cmd companion alike. A dated copy is kept once a day (the last 14).

block() is the prompt section every coach call carries (a system block of its own, so it stays in the prompt cache
until a note changes). Pure stdlib; thread-safe; a missing or broken file starts an empty memory.
"""
import json
import os
import re
import threading
import time

import jsonfile
from log import log

KINDS = ("preference", "request", "goal", "feedback", "fact")
KIND_LABEL = {"preference": "How they like to be coached", "request": "Standing requests",
              "goal": "Goals", "feedback": "Corrections (don't repeat these mistakes)",
              "fact": "About the player"}
BACKUPS = 14                  # daily copies kept beside the file
MAX_NOTES = 200               # oldest unpinned notes beyond this are dropped
MAX_TEXT = 300                # characters per note
BLOCK_CHARS = 2400            # the prompt block's budget (about 600 tokens)
PER_KIND = {"preference": 12, "request": 10, "goal": 6, "feedback": 10, "fact": 10}


def memory_dir():
    """The folder that holds the memory, the same for every install and engine home of this Windows user."""
    d = os.environ.get("GLIDE_MEMORY_DIR")
    if d:
        return d
    base = os.environ.get("APPDATA")
    return os.path.join(base, "Glide", "memory") if base else os.path.join(os.path.expanduser("~"), ".glide", "memory")


def default_path():
    return os.path.join(memory_dir(), "coach_memory.json")


def _norm(text):
    return re.sub(r"[^a-z0-9 ]", "", " ".join(str(text or "").lower().split()))


def _similar(a, b):
    """Rough duplicate test: same normalised text, or one contains the other, or 80% of the words shared."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    wa, wb = set(a.split()), set(b.split())
    return len(wa & wb) / max(1, min(len(wa), len(wb))) >= 0.8 and min(len(wa), len(wb)) >= 4


def guess_kind(text):
    """The kind a free-text note most likely is (the player's own "remember that ..." words)."""
    t = (text or "").lower()
    if re.search(r"\b(you were wrong|that was wrong|incorrect|you said|mistake|actually|not true|wrong about)\b", t):
        return "feedback"
    if re.search(r"\b(always|every time|whenever|each time|remind me when|tell me when)\b", t):
        return "request"
    if re.search(r"\b(goal|aim|aiming|want to (?:reach|hit|get)|by (?:mon|tue|wed|thu|fri|sat|sun)\w*|plan to|planning)\b", t):
        return "goal"
    if re.search(r"\b(prefer|don'?t|do not|never|stop|less|more|shorter|longer|quiet|only|rather|no need|skip|hate|love|like|dislike|annoy\w*|can't stand|enjoy)\b", t):
        return "preference"
    return "fact"


class Memory:
    def __init__(self, path):
        """path: the JSON file; None keeps the memory in this process only (simulation, tests)."""
        self.path = path
        self.lock = threading.Lock()
        self.notes = []
        self.next_id = 1
        self.learning = True               # reflection on (the app's Memory page switch; saved with the notes)
        self.last_reflect = 0.0
        self.version = 0                   # bumped on every change (the prompt block is rebuilt then)
        self._load()

    # ---- storage --------------------------------------------------------------------------------------------------
    def _load(self):
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            return
        self.notes = [n for n in d.get("notes") or () if isinstance(n, dict) and n.get("text") and n.get("kind") in KINDS]
        self.next_id = max([int(d.get("next_id") or 1)] + [int(n.get("id", 0)) + 1 for n in self.notes])
        self.learning = bool(d.get("learning", True))
        self.last_reflect = float(d.get("last_reflect") or 0)

    def _save(self):
        self.version += 1
        if not self.path:
            return
        try:
            jsonfile.write_atomic(self.path, {"notes": self.notes, "next_id": self.next_id, "learning": self.learning,
                                              "last_reflect": self.last_reflect}, ensure_ascii=False, indent=1)
            self._backup()
        except OSError as e:
            log("memory", "could not save %s: %s" % (self.path, e))

    def _backup(self):
        """One dated copy a day (coach_memory.YYYYMMDD.json), the newest BACKUPS kept."""
        folder, base = os.path.dirname(self.path), os.path.splitext(os.path.basename(self.path))[0]
        day = os.path.join(folder, "%s.%s.json" % (base, time.strftime("%Y%m%d")))
        if not os.path.exists(day):
            try:
                with open(self.path, "rb") as src, open(day, "wb") as dst:
                    dst.write(src.read())
            except OSError:
                return
        olds = sorted(f for f in os.listdir(folder) if re.fullmatch(re.escape(base) + r"\.\d{8}\.json", f))
        for f in olds[:-BACKUPS]:
            try:
                os.remove(os.path.join(folder, f))
            except OSError:
                pass

    # ---- changes ----------------------------------------------------------------------------------------------
    def add(self, text, kind=None, source="player", pinned=False, now=None):
        """Add a note, or refresh the matching one. Returns (note, created: bool), or (None, False) for empty text."""
        text = " ".join(str(text or "").split())[:MAX_TEXT].strip()
        if not text:
            return None, False
        kind = kind if kind in KINDS else guess_kind(text)
        now = time.time() if now is None else now
        with self.lock:
            for n in self.notes:
                if n["kind"] == kind and _similar(n["text"], text):
                    n["text"], n["updated"] = text, now
                    n["pinned"] = n.get("pinned") or pinned
                    self._save()
                    return dict(n), False
            n = {"id": self.next_id, "kind": kind, "text": text, "source": source, "created": now, "updated": now,
                 "pinned": bool(pinned), "used": 0}
            self.next_id += 1
            self.notes.append(n)
            if len(self.notes) > MAX_NOTES:
                unpinned = sorted((x for x in self.notes if not x.get("pinned")), key=lambda x: x["updated"])
                drop = {x["id"] for x in unpinned[:len(self.notes) - MAX_NOTES]}
                self.notes = [x for x in self.notes if x["id"] not in drop]
            self._save()
            return dict(n), True

    def update(self, note_id, text=None, kind=None, pinned=None):
        with self.lock:
            for n in self.notes:
                if n["id"] == int(note_id):
                    if text is not None and " ".join(str(text).split()):
                        n["text"] = " ".join(str(text).split())[:MAX_TEXT]
                    if kind in KINDS:
                        n["kind"] = kind
                    if pinned is not None:
                        n["pinned"] = bool(pinned)
                    n["updated"] = time.time()
                    self._save()
                    return dict(n)
        return None

    def remove(self, which):
        """Remove by id, by words of the note ("the gear one"), or "all" (pinned notes too). -> removed notes."""
        with self.lock:
            if isinstance(which, int) or (isinstance(which, str) and which.strip().isdigit()):
                gone = [n for n in self.notes if n["id"] == int(which)]
            elif str(which).strip().lower() in ("all", "everything"):
                gone = list(self.notes)
            else:
                words = [w for w in _norm(which).split() if len(w) > 2 and w not in ("the", "that", "about", "one", "note")]
                gone = [n for n in self.notes if words and all(w in _norm(n["text"]) for w in words)]
            if gone:
                ids = {n["id"] for n in gone}
                self.notes = [n for n in self.notes if n["id"] not in ids]
                self._save()
            return gone

    def set_learning(self, on):
        with self.lock:
            self.learning = bool(on)
            self._save()

    def mark_reflected(self, now=None):
        with self.lock:
            self.last_reflect = time.time() if now is None else now
            self._save()

    # ---- reading ----------------------------------------------------------------------------------------------
    def list(self):
        with self.lock:
            return [dict(n) for n in sorted(self.notes, key=lambda n: (KINDS.index(n["kind"]), -n["updated"]))]

    def snapshot(self):
        """For the app's Memory page."""
        return {"notes": self.list(), "learning": self.learning, "last_reflect": self.last_reflect or None,
                "kinds": [{"id": k, "label": KIND_LABEL[k]} for k in KINDS], "persistent": bool(self.path)}

    def block(self):
        """The prompt section: pinned notes first, then each kind's newest notes, within BLOCK_CHARS. '' when empty."""
        with self.lock:
            notes = list(self.notes)
        if not notes:
            return ""
        out, size = [], 0
        for kind in KINDS:
            rows = sorted((n for n in notes if n["kind"] == kind), key=lambda n: (not n.get("pinned"), -n["updated"]))
            rows = rows[:PER_KIND[kind]]
            if not rows:
                continue
            lines = ["%s:" % KIND_LABEL[kind]] + ["- [m%d] %s" % (n["id"], n["text"]) for n in rows]
            chunk = "\n".join(lines)
            if size + len(chunk) > BLOCK_CHARS:
                break
            out.append(chunk)
            size += len(chunk)
        return ("WHAT YOU REMEMBER ABOUT THIS PLAYER (learned in earlier sessions; ids in brackets):\n" +
                "\n".join(out) +
                "\nFollow their preferences and standing requests in every callout and answer, use goals and facts to "
                "tailor advice, and never repeat a corrected mistake. Don't recite this list unless they ask what you "
                "remember.")

    def mentioned(self, ids):
        """Count notes the coach cited (used), for the Memory page."""
        with self.lock:
            hit = False
            for n in self.notes:
                if n["id"] in ids:
                    n["used"] = int(n.get("used") or 0) + 1
                    hit = True
            if hit:
                self._save()


# ---- the player's own words ---------------------------------------------------------------------------------------
def parse_command(question):
    """Clear memory requests in the player's words -> ("add", kind, text) / ("forget", which) / ("list",) / None.
    'remember to <do something>' is a reminder, not a memory, and is left alone."""
    q = " ".join((question or "").split()).strip(" .!?")
    ql = q.lower()
    if re.fullmatch(r"(?:what do you (?:remember|know) about me|what have you learned(?: about me)?|"
                    r"what do you remember|(?:show|list|read) (?:me )?(?:your|my) (?:memory|memories|notes))", ql):
        return ("list",)
    if "reminder" in ql:
        return None
    m = re.fullmatch(r"(?:please )?(?:forget|stop remembering|delete the note|remove the note)(?: (?:that|about))? (.+)", q, re.I)
    if m and not re.match(r"(?:it|about it)$", m.group(1), re.I):
        return ("forget", m.group(1).strip())
    m = re.fullmatch(r"(?:please |coach,? )?(?:remember|note|keep in mind|don'?t forget)(?: that|:)? (.+)", q, re.I)
    if m and not re.match(r"to\s", m.group(1), re.I):
        text = m.group(1).strip()
        return ("add", guess_kind(text), text[0].upper() + text[1:])
    m = re.fullmatch(r"(?:from now on|going forward|in the future)[, ]+(.+)", q, re.I)
    if m:
        text = m.group(1).strip()
        kind = "preference" if re.search(r"\b(don'?t|do not|never|stop|less|more|shorter|longer|only)\b", text, re.I) \
            else "request"
        return ("add", kind, text[0].upper() + text[1:])
    return None
