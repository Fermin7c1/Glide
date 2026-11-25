"""The Glide addon's own saved data, read back from disk: the character's professions, its reputation standings, the
auction house prices it saw and the corpses it looted, for the pursuit planner (companion/pursuit.py) and the ranks.

WoW writes the addon's SavedVariables (WTF/Account/<account>/SavedVariables/Glide.lua, GlideRecorderDB:
docs/RECORDER.md) at logout and /reload only, so this is what the game knew at the last reload, never a live reading.
The file goes through the knowledge build's Lua table reader (knowledge/sources/_lua.py), which parses table
constructors and runs nothing. A file it can't read (WoW halfway through writing it, a damaged file) keeps the last
good reading of that file. A file is parsed again only when its modification time or size changes, and the folder is
looked at no more than every STAT_S seconds. Only the parts read below are kept (SESSION_KEYS, DB_KEYS: not the guide
texts or the trails, most of a 9 MB file). A file is parsed outside the lock: while one thread reads the files, the
others answer from the last reading instead of waiting (except before the first reading is done). GlideLinkDB (the
same file) holds the live link's settings only, so it isn't read.

Where each thing is (a session is one login and names its character, session.char = {name, realm, faction, ...}):
  - professions: session.skills.lines = [{n = name, h = header}] (Recorder.lua snapshotSkills), from the newest
    session of the character that has skill lines. A client that answers no skill lines (session.skills without
    `lines`: the client's GetNumSkillLines answered nothing) leaves the professions unknown (None), never "none".
  - reputation: session.factions[factionId] = {standing, value, t, v0, t0, t1} (addon 0.24.0, UPDATE_FACTION; v0 and
    t0 the value and time of the session's first rise, t1 its last: the grind between them), or the same table under
    GlideRecorderDB.factions["Name-Realm" or "Name"]. The newest t per faction wins.
  - auction prices: GlideRecorderDB.auction["<realm>|<side>"][itemId] = {copper, t} (addon 0.24.0: the lowest buyout
    for one the player saw while browsing, for the items in the plan, per realm and side: each has its own auction
    house), or the same in a session (session.auction, read for that session's realm and side). The newest t wins.
  - kills: the corpses the character looted (session.loot windows whose items came from an NPC). One corpse looted
    again is one kill, by the rules knowledge/sources/observations.py merges windows with for the learned drop rates,
    applied over all of the character's sessions (a /reload between two windows doesn't make a second kill). A kill
    the player didn't loot says nothing about what dropped, so it isn't listed.
A table entry is read named ({standing = 6, value = 9500, t = ...}) or positional ({6, 9500, ...}). Files from before
0.24.0 have no factions or auction tables: those read as nothing known.
"""
import bisect
import collections
import glob
import os
import re
import sys
import threading
import time

import item_db
from log import log, log_once

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if ROOT not in sys.path:
    sys.path.append(ROOT)       # the knowledge package: its values module (stdlib only) reads the saved numbers
from knowledge.values import as_float, as_int  # noqa: E402

SV_FILE = "Glide.lua"
STAT_S = 2.0                 # the folder is looked at again (new files, changed times) at most this often
SECONDARY = ("Cooking", "First Aid", "Fishing")
PROFESSIONS = frozenset(item_db.PROFESSIONS.values()) | frozenset(SECONDARY)
PROFESSION_HEADERS = frozenset(("Professions", "Secondary Skills"))
SESSION_KEYS = ("id", "started", "ended", "addon", "locale", "char", "skills", "factions", "auction", "loot", "kills")
DB_KEYS = ("schema", "factions", "auction")


def _lua():
    """knowledge/sources/_lua.py, the Lua table reader the knowledge build uses."""
    if ROOT not in sys.path:
        sys.path.append(ROOT)
    from knowledge.sources import _lua
    return _lua


def _merge_rules():
    """(LOOT_MERGE_S, REOPEN_S) of the knowledge build's corpse merge (knowledge/sources/observations.py)."""
    if ROOT not in sys.path:
        sys.path.append(ROOT)
    from knowledge.sources import observations
    return observations.LOOT_MERGE_S, observations.REOPEN_S


def _id(v):
    """v as an int (a number or a digit string; not a bool), or None (knowledge.values.as_int)."""
    return as_int(v, bool_ok=False)


def _t(v):
    """v as a finite number (not a bool or text), or None: a time or a value the addon wrote."""
    return as_float(v, finite=True, bool_ok=False) if isinstance(v, (int, float)) else None


def _values(v):
    """The entries of a Lua array the reader gave as a list, or as a dict (an array with holes)."""
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        return [x for _k, x in sorted(v.items(), key=lambda kv: (0, kv[0], "") if isinstance(kv[0], int)
                                      else (1, 0, str(kv[0])))]
    return []


def _norm(s):
    """A name or realm as compared: case, spaces, hyphens and apostrophes left out."""
    return re.sub(r"[\s'\-]", "", str(s or "")).casefold()


def split_key(key):
    """(name, realm) of a character key, as written ("Nine-Skyborne" -> ("Nine", "Skyborne"), "Nine" -> ("Nine", ""))."""
    name, _, realm = str(key or "").partition("-")
    return name, realm


def who(char):
    """(name, realm or None) of a character key ("Name-Realm" or "Name") or {"name", "realm"}, normalised."""
    name, realm = (char.get("name"), char.get("realm")) if isinstance(char, dict) else split_key(char)
    return _norm(name), (_norm(realm) or None)


def _same(want, name, realm):
    """Is (name, realm) the character `want` (who()) asks for? A side without a realm matches any realm."""
    if not want[0] or _norm(name) != want[0]:
        return False
    return want[1] is None or not realm or _norm(realm) == want[1]


def _slim(db):
    """The parts of a GlideRecorderDB this reads (DB_KEYS, and SESSION_KEYS of each session)."""
    out = {k: db[k] for k in DB_KEYS if k in db}
    ss = db.get("sessions")
    if isinstance(ss, dict):
        out["sessions"] = {sid: ({k: s[k] for k in SESSION_KEYS if k in s} if isinstance(s, dict) else s)
                           for sid, s in ss.items()}
    return out


def _newest(cur, cand):
    """The newer of two entries by "t" (an entry without one loses to one with it)."""
    if cur is None:
        return cand
    return cand if (cand.get("t") or float("-inf")) > (cur.get("t") or float("-inf")) else cur


def faction_entry(v, t_default=None):
    """{"standing", "value", "t"} of a recorded faction entry (named or positional), or None without a standing; and
    "v0", "t0", "t1" when the session saw it rise twice or more (named only: the value and time of its first rise, the
    time of its last: the grind between them)."""
    if isinstance(v, dict):
        st, val, t = v.get("standing", v.get("s")), v.get("value", v.get("v")), v.get("t")
    elif isinstance(v, list):
        st, val, t = (list(v) + [None] * 3)[:3]
    else:
        return None
    st = _id(st)
    if st is None:
        return None
    t = _t(t) if _t(t) is not None else _t(t_default)
    out = {"standing": st, "value": _id(val), "t": t}
    if isinstance(v, dict):
        v0, t0, t1 = _id(v.get("v0")), _t(v.get("t0")), _t(v.get("t1"))
        if v0 is not None and t0 is not None and t1 is not None and t1 > t0:
            out.update(v0=v0, t0=t0, t1=t1)
    return out


def price_entry(v, t_default=None):
    """{"copper", "t"} of a recorded auction entry (named, positional, or a bare copper amount), or None."""
    if isinstance(v, dict):
        c, t = v.get("copper", v.get("c")), v.get("t")
    elif isinstance(v, list):
        c, t = (list(v) + [None] * 2)[:2]
    else:
        c, t = v, None
    c = _id(c)
    if c is None or c <= 0:
        return None
    t = _t(t) if _t(t) is not None else _t(t_default)
    return {"copper": c, "t": t}


def corpses(windows, kill_times, gap=None, reopen=None):
    """One NPC's loot windows ({t: {item ids}}) and kill times -> [(t, {item ids})], one per corpse, oldest first.

    knowledge/sources/observations.py loot_events' rules: windows merge while they stay within `gap` of the group's
    first and no kill of the NPC came between them, and a group is as many corpses as the kills since the previous
    group (no more than its windows); an item seen in c of a group's windows went to c of its corpses. Groups no kill
    was recorded for (a kill that gives no XP, as at level 60) are one corpse per window, except a window within
    `reopen` of the one before that holds nothing new (the same corpse looted again)."""
    if gap is None or reopen is None:
        g, r = _merge_rules()
        gap, reopen = g if gap is None else gap, r if reopen is None else reopen
    kills = sorted(k for k in kill_times if k is not None)
    groups = []                                      # [first t, last t, [(t, items)]]
    for t in sorted(w for w in windows if w is not None):
        if not groups or t - groups[-1][0] > gap or \
                bisect.bisect_right(kills, groups[-1][1]) < bisect.bisect_right(kills, t):
            groups.append([t, t, []])
        groups[-1][1] = t
        groups[-1][2].append((t, set(windows[t])))
    out, before, run = [], 0, []
    for t0, t1, wins in groups:
        upto = bisect.bisect_right(kills, t1)
        k, before = upto - before, upto
        if k <= 0:
            run += wins
            continue
        out += _reloots(run, reopen)
        run = []
        n = min(len(wins), k)
        seen = collections.Counter(i for _when, items in wins for i in items)
        bodies = [set() for _ in range(n)]
        for item, c in seen.items():
            for j in range(min(c, n)):
                bodies[j].add(item)
        out += [(t0, b) for b in bodies]
    out += _reloots(run, reopen)
    return out


def _reloots(wins, reopen):
    """Windows no kill was recorded for ([(t, items)], oldest first) as corpses [(t, items)]."""
    out, prev = [], None
    for t, items in wins:
        if prev is None or t - prev[0] > reopen or not items <= prev[1]:
            out.append((t, set()))
        out[-1][1].update(items)
        prev = (t, items)
    return out


class RecorderData:
    """The recorder's SavedVariables for one WoW folder. wow_dir_fn() -> the WoW client folder (None: not set);
    account_fn() -> the account folder to read (None, or no account_fn: every account's); now() -> seconds, for how
    often the folder is looked at. Thread-safe; answers are copies the caller may change."""

    def __init__(self, wow_dir_fn, account_fn=None, now=time.time):
        self.wow_dir_fn = wow_dir_fn
        self.account_fn = account_fn
        self.now = now
        self.lock = threading.Lock()         # the view, memo and times below
        self.reading = threading.Lock()      # one thread reads the files at a time
        self.files = {}              # path -> ((mtime_ns, size), GlideRecorderDB's kept parts); written under reading
        self.checked = None          # now() when the folder was last looked at
        self.loaded = False          # a first reading is done
        self.view = {"sessions": [], "dbs": []}
        self.memo = {}
        self.warned = set()

    # ---- the files ------------------------------------------------------------------------------------------------
    def paths(self):
        """The Glide.lua files read: the account account_fn names, else every account's."""
        try:
            wow = self.wow_dir_fn() if self.wow_dir_fn else None
            acct = self.account_fn() if self.account_fn else None
        except Exception as e:  # noqa: BLE001 - no folder: nothing to read
            log_once("recorder_data.paths", e)
            return []
        if not wow:
            return []
        acct = glob.escape(str(acct)) if acct else "*"
        return sorted(glob.glob(os.path.join(glob.escape(str(wow)), "WTF", "Account", acct, "SavedVariables", SV_FILE)))

    def _parse(self, path, old):
        def warn(text):                         # a newer addon's file: said once, not at every reload
            if text not in self.warned:
                self.warned.add(text)
                log("recorder", text)
        try:
            db = _lua().recorder_db(path, warn=warn)
        except Exception as e:  # noqa: BLE001 - half written or damaged: the last good reading stands
            log_once("recorder_data.read", e)
            return old[1] if old else {}
        return _slim(db) if isinstance(db, dict) else {}

    def refresh(self, force=False):
        """Look at the files again (at most every STAT_S seconds unless forced) and re-read the changed ones. While
        another thread is reading them, return at once (the last reading answers meanwhile), except when forced or
        before the first reading is done. -> True when what is known changed."""
        with self.lock:
            t = self.now()
            if not force and self.checked is not None and 0 <= t - self.checked < STAT_S:
                return False
            wait = force or not self.loaded
        if not self.reading.acquire(blocking=wait):
            return False
        try:
            with self.lock:
                self.checked = t
            known, files, changed = self.files, {}, False
            for p in self.paths():
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                sig, old = (st.st_mtime_ns, st.st_size), known.get(p)
                if old is not None and old[0] == sig:
                    files[p] = old
                    continue
                files[p] = (sig, self._parse(p, old))
                changed = True
            changed = changed or set(files) != set(known)
            if changed:
                view = self._build(files)
                with self.lock:
                    self.files, self.loaded, self.view, self.memo = files, True, view, {}
            else:
                with self.lock:
                    self.files, self.loaded = files, True
            return changed
        finally:
            self.reading.release()

    @staticmethod
    def _build(files):
        """{"sessions": [(id, session)] newest first, "dbs": [GlideRecorderDB] newest file first}. A session in two
        files (an account folder copied) counts once: the newer file's."""
        order = sorted(files.items(), key=lambda kv: kv[1][0][0], reverse=True)
        sessions, seen = [], set()
        for _p, (_sig, db) in order:
            for sid, s in ((db.get("sessions") or {}).items() if isinstance(db.get("sessions"), dict) else ()):
                if not isinstance(s, dict) or not isinstance(s.get("char"), dict):
                    continue
                same = (str(sid), _norm(s["char"].get("name")), _norm(s["char"].get("realm")))
                if same in seen:
                    continue
                seen.add(same)
                sessions.append((str(sid), s))
        sessions.sort(key=lambda x: (_t(x[1].get("started")) or 0, x[0]), reverse=True)
        return {"sessions": sessions, "dbs": [db for _p, (_sig, db) in order]}

    def _view(self):
        self.refresh()
        with self.lock:
            return self.view, self.memo

    def _sessions(self, char, every=False):
        """[(id, session)] of the character, newest first; every character's when every and char is None."""
        view, _ = self._view()
        if char is None and every:
            return list(view["sessions"])
        want = who(char)
        return [(sid, s) for sid, s in view["sessions"] if _same(want, s["char"].get("name"), s["char"].get("realm"))]

    def sessions(self, char=None):
        """[(session id, session)] of a character (every character's for None), newest first: the kept parts of the
        parsed tables (SESSION_KEYS), as they are (read only)."""
        return self._sessions(char, every=True)

    # ---- what the planner asks --------------------------------------------------------------------------------------
    def skills(self, char):
        """The professions the character has, by name ({"Tailoring", "Enchanting"}), as of its newest session with
        skill lines; an empty set when that session lists none; None when unknown (no session with skill lines, or
        a client language whose profession names this doesn't know)."""
        for _sid, s in self._sessions(char):
            sk = s.get("skills")
            lines = _values(sk.get("lines")) if isinstance(sk, dict) else []
            if not lines:
                continue
            got = set()
            for ln in lines:
                if not isinstance(ln, dict) or not isinstance(ln.get("n"), str):
                    continue
                if ln["n"] in PROFESSIONS or ln.get("h") in PROFESSION_HEADERS:
                    got.add(ln["n"])
            english = str(s.get("locale") or "enUS").startswith("en")
            return got if got or english else None
        return None

    def factions(self, char):
        """{faction id: {"standing": 1..8, "value": int or None, "t": seconds}} of the character as of the last
        reload ({} when none were recorded)."""
        out = {}
        for _sid, s in self._sessions(char):
            t0 = _t(s.get("ended")) or _t(s.get("started"))
            self._factions_into(out, s.get("factions"), t0)
        view, _ = self._view()
        want = who(char)
        for db in view["dbs"]:
            per = db.get("factions")
            for key, tbl in (per.items() if isinstance(per, dict) else ()):
                if isinstance(key, str) and _id(key) is None and _same(want, *split_key(key)):
                    self._factions_into(out, tbl, None)
        return {k: dict(v) for k, v in out.items()}

    @staticmethod
    def _factions_into(out, tbl, t0):
        """Each {faction id: entry} of a recorded table into out, the newer entry kept."""
        for fid, v in (tbl.items() if isinstance(tbl, dict) else ()):
            fid, e = _id(fid), faction_entry(v, t0)
            if fid is not None and e is not None:
                out[fid] = _newest(out.get(fid), e)

    def auction(self, item_id, char=None):
        """{"copper", "t"}: the newest auction house price seen for the item, or None. With char, only what was seen
        on that character's realm and side (its newest session's) counts: GlideRecorderDB.auction["<realm>|<side>"]
        and the sessions' own; nothing when no session of it tells them (Alliance and Horde houses are apart, and
        every realm's). Without char, every one."""
        iid = _id(item_id)
        if iid is None:
            return None
        view, _ = self._view()
        where = None
        if char is not None:
            mine = self._sessions(char)
            if not mine:
                return None
            c = mine[0][1]["char"]
            where = (_norm(c.get("realm")), _norm(c.get("faction")))
        best = None
        for _sid, s in view["sessions"]:
            c = s["char"]
            if where is not None and (_norm(c.get("realm")), _norm(c.get("faction"))) != where:
                continue
            best = self._price_from(best, s.get("auction"), iid, _t(s.get("ended")) or _t(s.get("started")))
        for db in view["dbs"]:
            book = db.get("auction")
            for house, tbl in (book.items() if isinstance(book, dict) else ()):
                if not isinstance(house, str) or "|" not in house:
                    continue
                realm, _, side = house.rpartition("|")
                if where is None or (_norm(realm), _norm(side)) == where:
                    best = self._price_from(best, tbl, iid, None)
            if where is None:
                best = self._price_from(best, book, iid, None)      # (a flat table, before the houses: anyone's)
        return dict(best) if best else None

    @staticmethod
    def _price_from(best, tbl, iid, t0):
        if not isinstance(tbl, dict):
            return best
        v = tbl.get(iid, tbl.get(str(iid)))
        e = price_entry(v, t0) if v is not None else None
        return _newest(best, e) if e else best

    def kills(self, char, npc=None):
        """[{"npc", "t", "loot": [item ids], "session"}], oldest first: each corpse of an NPC the character looted
        (module docstring), every character's for None, only that NPC's with npc."""
        _view, memo = self._view()
        key = ("kills", who(char) if char is not None else None)
        hit = memo.get(key)
        if hit is None:
            hit = self._kills(char)
            with self.lock:
                if self.memo is memo:
                    memo[key] = hit
        want = _id(npc) if npc is not None else None
        return [dict(k, loot=list(k["loot"])) for k in hit if want is None or k["npc"] == want]

    def _kills(self, char):
        windows = collections.defaultdict(lambda: collections.defaultdict(set))    # npc -> t -> items
        where = {}                                                                 # (npc, t) -> session id
        kill_t = collections.defaultdict(list)                                     # npc -> kill times
        for sid, s in self._sessions(char, every=True):
            for ev in _values(s.get("loot")):
                t = _t(ev.get("t")) if isinstance(ev, dict) else None
                if t is None:
                    continue
                for it in _values(ev.get("items")):
                    if not isinstance(it, dict) or it.get("sk") != "npc":
                        continue
                    src, iid = _id(it.get("src")), _id(it.get("id"))
                    if src is not None and iid is not None:
                        windows[src][t].add(iid)
                        where.setdefault((src, t), sid)
            for k in _values(s.get("kills")):
                cid = _id(k.get("id")) if isinstance(k, dict) else None
                if cid is not None and _t(k.get("t")) is not None:
                    kill_t[cid].append(_t(k["t"]))
        out = []
        for src, wins in windows.items():
            for t, items in corpses(wins, kill_t.get(src, ())):
                out.append({"npc": src, "t": t, "loot": sorted(items), "session": where.get((src, t))})
        out.sort(key=lambda k: (k["t"], k["npc"]))
        return out
