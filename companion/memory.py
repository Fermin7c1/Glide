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

The bond with the player (banter.py: hours together, sessions, how the asides went, the diary of shared moments, the
zones visited together, the characters played with their lore) lives beside it in coach_bond.json, a file older
engines never read or write, so going back to an older version keeps it. "rapport" notes are written to
coach_memory.json as kind "fact" with "rapport": true (older engines keep and show them as facts), and notes of a kind
or top-level keys this engine doesn't know are written back as they were.
"""
import json
import os
import re
import sys
import threading
import time

import jsonfile
from log import log

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if ROOT not in sys.path:
    sys.path.append(ROOT)       # the knowledge package: its values module (stdlib only) reads stored numbers
from knowledge.values import as_float, as_int  # noqa: E402

KINDS = ("preference", "request", "goal", "feedback", "fact", "rapport")
KIND_LABEL = {"preference": "How they like to be coached", "request": "Standing requests",
              "goal": "Goals", "feedback": "Corrections (don't repeat these mistakes)",
              "fact": "About the player",
              "rapport": "Your bond with them (what lands, inside jokes, nicknames, shared moments)"}
BACKUPS = 14                  # daily copies kept beside the file
MAX_NOTES = 200               # oldest unpinned notes beyond this are dropped
MAX_TEXT = 300                # characters per note
BLOCK_CHARS = 2400            # the prompt block's budget (about 600 tokens)
PER_KIND = {"preference": 12, "request": 10, "goal": 6, "feedback": 10, "fact": 10, "rapport": 12}
BOND_SAVE_S = 300             # the bond clock is saved at most this often (asides and replies at once)
# how long the coach and the player have played together -> the stage of their bond (banter.py speaks to it)
BOND_STAGES = ((3, "new"), (15, "getting to know each other"), (50, "familiar"), (None, "old friends"))
MAX_DIARY = 80                # shared moments kept (newest)
MAX_LORE = 24                 # facts a character told about itself, per character
MAX_CHARACTERS = 12           # characters kept (most recently played)


def memory_dir():
    """The folder that holds the memory, the same for every install and engine home of this Windows user."""
    d = os.environ.get("GLIDE_MEMORY_DIR")
    if d:
        return d
    base = os.environ.get("APPDATA")
    return os.path.join(base, "Glide", "memory") if base else os.path.join(os.path.expanduser("~"), ".glide", "memory")


def default_path():
    return os.path.join(memory_dir(), "coach_memory.json")


def _typed(d, key, kind):
    """d[key] when it is a `kind` (list, dict), else an empty one: a stored file's part, checked."""
    v = d.get(key)
    return v if isinstance(v, kind) else kind()


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
        self.bond_path = os.path.join(os.path.dirname(path), "coach_bond.json") if path else None
        self._foreign = []                 # notes of kinds this engine doesn't know: written back untouched
        self._extra = {}                   # top-level keys this engine doesn't know: written back untouched
        self.zones = set()                 # the zones visited together (not capped like the diary)
        # time played together and how the coach's asides went (banter.py): {"hours", "sessions", "asides", "replied"}
        self.bond = {"hours": 0.0, "sessions": 0, "asides": 0, "replied": 0, "last_seen": 0.0, "by_activity": {}}
        # the diary of shared moments: [{"t", "kind", "text", "session"}] (banter.py writes it, the asides recall it)
        self.diary = []
        # the characters the coach played for them, by key: {"name", "brief", "voice", "lore": [...], "last"}
        self.characters = {}
        self._bond_saved = 0.0
        self._load()

    # ---- storage --------------------------------------------------------------------------------------------------
    _KNOWN = ("notes", "next_id", "learning", "last_reflect")

    @staticmethod
    def _read(path):
        """A JSON object from `path`, or {} (missing, unreadable, not an object)."""
        if not path or not os.path.exists(path):
            return {}
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            return {}
        return d if isinstance(d, dict) else {}

    def _load(self):
        d = self._read(self.path)
        notes = _typed(d, "notes", list)
        for n in notes:
            if not isinstance(n, dict) or not isinstance(n.get("text"), str) or not n["text"].strip():
                continue
            if n.get("kind") == "fact" and n.get("rapport"):
                n = {k: v for k, v in n.items() if k != "rapport"}
                n["kind"] = "rapport"
            if n.get("kind") not in KINDS:
                self._foreign.append(n)
                continue
            try:
                n["id"], n["updated"] = int(n.get("id") or 0), float(n.get("updated") or n.get("created") or 0)
            except (TypeError, ValueError):
                continue
            self.notes.append(n)
        ids = [n["id"] for n in self.notes] + [x.get("id") for x in self._foreign if isinstance(x.get("id"), int)]
        self.next_id = max([as_int(d.get("next_id"), 1, bool_ok=False)] + [i + 1 for i in ids])
        self.learning = bool(d.get("learning", True))
        self.last_reflect = as_float(d.get("last_reflect"), 0.0, finite=True, bool_ok=False)
        self._extra = {k: v for k, v in d.items() if k not in self._KNOWN}
        # the bond: coach_bond.json (an earlier build of this engine kept it in coach_memory.json: read from there once)
        b = self._read(self.bond_path) or {k: d.get(k) for k in ("bond", "diary", "characters", "zones")}
        for k in ("bond", "diary", "characters", "zones"):
            self._extra.pop(k, None)
        self._load_bond(b)

    def _load_bond(self, d):
        """The bond's parts from a parsed coach_bond.json, each checked: a bad entry is dropped, never fatal."""
        bond = _typed(d, "bond", dict)
        for k in ("hours", "last_seen"):
            self.bond[k] = as_float(bond.get(k), 0.0, finite=True, bool_ok=False)
        for k in ("sessions", "asides", "replied"):
            self.bond[k] = as_int(bond.get(k), 0, bool_ok=False)
        acts = _typed(bond, "by_activity", dict)
        for a, r in acts.items():
            if isinstance(a, str) and isinstance(r, list) and len(r) == 2:
                self.bond["by_activity"][a] = [as_int(r[0], 0, bool_ok=False), as_int(r[1], 0, bool_ok=False)]
        diary = _typed(d, "diary", list)
        self.diary = []
        for x in diary:
            if not isinstance(x, dict) or not isinstance(x.get("text"), str) or isinstance(x.get("t"), bool) \
                    or not isinstance(x.get("t"), (int, float)):
                continue
            row = {"t": float(x["t"]), "kind": str(x.get("kind") or "moment"), "text": x["text"][:200],
                   "session": as_int(x.get("session"), 0, bool_ok=False)}
            if isinstance(x.get("zone"), str):
                row["zone"] = x["zone"]
            self.diary.append(row)
        del self.diary[:-MAX_DIARY]
        chars = _typed(d, "characters", dict)
        for k, v in chars.items():
            if isinstance(v, dict) and isinstance(v.get("name"), str) and isinstance(v.get("brief"), str) \
                    and v["name"].strip() and v["brief"].strip():
                lore = _typed(v, "lore", list)
                self.characters[self._key(v["name"]) or str(k)] = {
                    "name": v["name"][:60], "brief": v["brief"], "voice": v.get("voice") if isinstance(
                        v.get("voice"), str) else "", "lore": [x for x in lore if isinstance(x, str)][-MAX_LORE:],
                    "last": as_float(v.get("last"), 0.0, finite=True, bool_ok=False)}
        zones = _typed(d, "zones", list)
        self.zones = {z for z in zones if isinstance(z, str)} | {x["zone"] for x in self.diary if "zone" in x}

    def _save(self):
        """coach_memory.json: the notes ("rapport" ones as facts with "rapport": true, so older engines keep them),
        then the notes and keys this engine doesn't know, as they were."""
        self.version += 1
        if not self.path:
            return
        notes = [dict(n, kind="fact", rapport=True) if n["kind"] == "rapport" else n for n in self.notes]
        try:
            jsonfile.write_atomic(self.path, dict(self._extra, notes=notes + self._foreign, next_id=self.next_id,
                                                  learning=self.learning, last_reflect=self.last_reflect),
                                  ensure_ascii=False, indent=1)
            self._backup(self.path)
        except OSError as e:
            log("memory", "could not save %s: %s" % (self.path, e))

    def _save_bond(self):
        """coach_bond.json: the bond, the diary, the zones and the characters."""
        self.version += 1
        if not self.bond_path:
            return
        try:
            jsonfile.write_atomic(self.bond_path, {"bond": self.bond, "diary": self.diary, "zones": sorted(self.zones),
                                                   "characters": self.characters}, ensure_ascii=False, indent=1)
            self._backup(self.bond_path)
        except OSError as e:
            log("memory", "could not save %s: %s" % (self.bond_path, e))

    @staticmethod
    def _backup(path):
        """One dated copy a day (coach_memory.YYYYMMDD.json, coach_bond.YYYYMMDD.json), the newest BACKUPS kept."""
        folder, base = os.path.dirname(path), os.path.splitext(os.path.basename(path))[0]
        day = os.path.join(folder, "%s.%s.json" % (base, time.strftime("%Y%m%d")))
        if not os.path.exists(day):
            try:
                with open(path, "rb") as src, open(day, "wb") as dst:
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
            if str(which).strip().lower() in ("all", "everything"):      # and the history together, all of it
                self.bond = {"hours": 0.0, "sessions": 0, "asides": 0, "replied": 0, "last_seen": 0.0,
                             "by_activity": {}}
                self.diary, self.zones, self.characters = [], set(), {}
                self._save_bond()
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

    # ---- the bond: time together and how the asides went (banter.py) ----------------------------------------------
    def bond_add(self, hours=0.0, sessions=0, asides=0, replied=0, activity=None):
        """Count time played together and asides (and, with `activity`, per what the player was doing then); saved at
        once for a session, aside or reply, the clock every BOND_SAVE_S."""
        with self.lock:
            b = self.bond
            b["hours"] = round(b["hours"] + hours, 4)
            b["sessions"] += sessions
            b["asides"] += asides
            b["replied"] += replied
            if hours:
                b["last_seen"] = time.time()
            if activity and (asides or replied):
                row = b["by_activity"].setdefault(activity, [0, 0])
                row[0] += asides
                row[1] += replied
            if sessions or asides or replied or time.time() - self._bond_saved >= BOND_SAVE_S:
                self._bond_saved = time.time()
                self._save_bond()

    def bond_stage(self):
        """'new', 'getting to know each other', 'familiar' or 'old friends', from the hours played together."""
        h = self.bond["hours"]
        return next(name for top, name in BOND_STAGES if top is None or h < top)

    def bond_line(self):
        """The bond for the aside request: time together, how the asides landed, the stage."""
        b = self.bond
        return ("You and this player: %.0f hours played together over %d sessions; %d of your %d asides got a reply. "
                "Your bond: %s." % (b["hours"], max(1, b["sessions"]), b["replied"], b["asides"], self.bond_stage()))

    def reply_rate(self, activity, least=4):
        """How often the player answers an aside while doing `activity` (0..1), or None before `least` asides."""
        row = self.bond["by_activity"].get(activity) or [0, 0]
        return row[1] / float(row[0]) if row[0] >= least else None

    def activity_line(self):
        """When they answer the asides: 'flying 4 of 5, questing 1 of 9', or ''."""
        rows = sorted(self.bond["by_activity"].items(), key=lambda kv: -kv[1][0])
        return ", ".join("%s %d of %d" % (k, v[1], v[0]) for k, v in rows if v[0])

    # ---- the diary of shared moments (banter.py) --------------------------------------------------------------
    def diary_add(self, kind, text, now=None):
        """A moment worth remembering together (a death, a ding, a close call, a new zone): kept, newest MAX_DIARY."""
        with self.lock:
            self.diary.append({"t": time.time() if now is None else now, "kind": kind,
                               "text": " ".join(str(text).split())[:200], "session": self.bond["sessions"]})
            del self.diary[:-MAX_DIARY]
            self._save_bond()

    def diary_since(self, t):
        with self.lock:
            return [dict(x) for x in self.diary if x["t"] > t]

    def diary_recent(self, n=8):
        with self.lock:
            return [dict(x) for x in self.diary[-n:]]

    def zones_seen(self):
        """The zones visited together."""
        with self.lock:
            return set(self.zones)

    def zone_seen(self, zone, now=None):
        """Note a zone first visited together (a diary moment of its own). -> True the first time."""
        if not zone:
            return False
        with self.lock:
            if zone in self.zones:
                return False
            self.zones.add(zone)
            self.diary.append({"t": time.time() if now is None else now, "kind": "zone", "zone": zone,
                               "text": "First time in %s together" % zone, "session": self.bond["sessions"]})
            del self.diary[:-MAX_DIARY]
            self._save_bond()
        return True

    # ---- the characters played for them (persona.py) ----------------------------------------------------------
    @staticmethod
    def _key(name):
        """A character's key: its name casefolded, letters (any script), digits and spaces only."""
        return " ".join(re.sub(r"[^\w ]", "", str(name or "").casefold()).replace("_", " ").split())[:40]

    def character_save(self, name, brief, voice=""):
        """Keep the character being played (by name) so "bring back <name>" restores it, its lore and all."""
        key = self._key(name)
        if not key or not brief:
            return None
        with self.lock:
            ch = self.characters.setdefault(key, {"name": name, "lore": []})
            ch.update({"name": name, "brief": brief, "voice": voice or ch.get("voice") or "", "last": time.time()})
            if len(self.characters) > MAX_CHARACTERS:
                for k in sorted(self.characters, key=lambda k: self.characters[k].get("last") or 0)[:-MAX_CHARACTERS]:
                    del self.characters[k]
            self._save_bond()
            return dict(ch)

    def character(self, name):
        """A saved character by name (exact, else the one whose name contains it), or None."""
        key = self._key(name)
        if not key:
            return None
        with self.lock:
            ch = self.characters.get(key) or (next((v for k, v in self.characters.items()
                                                     if key in k or (len(k) >= 3 and k in key)), None)
                                               if len(key) >= 3 else None)
            return dict(ch, lore=list(ch.get("lore") or ())) if ch else None

    def character_lore(self, name, fact):
        """A fact the character made up about itself: kept so it never contradicts itself."""
        key = self._key(name)
        fact = " ".join(str(fact or "").split())[:200]
        with self.lock:
            ch = self.characters.get(key)
            if ch is None or not fact or any(_similar(fact, x) for x in ch.get("lore") or ()):
                return False
            ch.setdefault("lore", []).append(fact)
            del ch["lore"][:-MAX_LORE]
            self._save_bond()
            return True

    def characters_block(self):
        """The prompt's list of characters played before ('' when none): what "bring back <name>" can recall."""
        with self.lock:
            rows = sorted(self.characters.values(), key=lambda v: -(v.get("last") or 0))
        if not rows:
            return ""
        return "CHARACTERS YOU HAVE PLAYED FOR THIS PLAYER (bring one back with PERSONA: recall <name>):\n" + "\n".join(
            "- %s: %s" % (v["name"], v["brief"][:160]) for v in rows)

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
