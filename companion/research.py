"""Where items come from: the engine looks up each item a character may care about and keeps what it finds. The
addon's item tooltips say it (live_data.py writes items_section into Data/Live.lua, read at login and /reload), and so
does the coach (coach_tools.item_info: "where does Magister's Crown drop?").

Every POLL_S the worker reads the game state and queues the facts to find ("i:<item id>"), most pressing first:
1 on screen now (bag items, reward choices, a question), 2 worn items, 3 the character's level band (every item their
class can use whose required level is from 10 below theirs to 5 above: item_db.ItemDB.band; worked out again on a
level-up, a new WoWSims data tag or a knowledge base swap, queued then and every BAND_S), 4 the rest. It also
watches for a drop: the target died and, within LOOT_S, one or two items came into the bags that the character
hadn't had in its bags or worn this session (not a reward choice, and not in a poll where the worn gear changed, the
money went down or a quest left the log: a swap, a vendor, the auction house, a turn-in): a source the player saw
(obs, kept TTL_S; mail and crafting can't be told apart). Then it
resolves the queue for at most TICK_BUDGET_S, from the knowledge base (kb.db: creature drops, vendors, quest
rewards; rows the player's own recorder saw count as observations), WoWSims' item database (item_db_fn: db.json and
leftover_db.json at WoWSims.data_tag(), see ItemDBSource) and the drops the player saw. The client's own item data
(bond_fn: how the item binds) is a seam the app doesn't set yet (R1 ships without reading ItemSparse), so an item
with no known source stays silent. Sources are merged (of two for the same place the more trusted is kept, its
blanks filled from the other; a vendor and the reputation it needs are one place), filtered by side (a quest the
knowledge base calls "Both" or nothing is one side's when its races, or WoWSims, say so), and the most trusted leads.
An item WoWSims calls a world drop, or seen dropping from WORLD_NPCS or more kinds of creature, is a world drop, not
any one creature's (fold_world). Whether it can be had (avail) follows from what was merged.

A fact holds for one faction ("fa"), or for both (fa None) when the sides make no difference. One that depends on the
side isn't kept while no character was ever seen (the coach's answer is worked out for that question only); the last
character's faction is kept, and Data/Live.lua gets the facts that hold for it. A fact is looked up again after its
ttl (TTL_S), with new WoWSims data (a new tag, or leftover_db.json coming in late) or knowledge base, or for a
character of the other faction (while WoWSims' data can't be had, what it said is kept). An item nothing knows is
asked again after NEG_TTL_S (per side when only one has nothing), or with new data (negative cache); one whose lookup
failed GIVE_UP times isn't asked again this session (what was known of it stays).

data/research/facts.json: {"v": 1, "faction": the last character's, "facts": {"i:<id>": {"s": [Src], "bond",
"price", "conf", "src", "t", "ttl", "name", "avail", "fa", "tag", "kb"}}, "neg": {"i:<id>": [t, fa]}, "seen":
{"i:<id>": [Src + "t"]}}. Src: item_db's source shape ({"kind", "npc", "npc_name", "zone", "zone_name", "instance",
"difficulty", "quest", "quest_name", "profession", "rep_faction", "rep_level", "conf", "src"}).
"""
import collections
import difflib
import json
import os
import re
import threading
import time
import urllib.error

import item_db
import item_text
import jsonfile
from log import log, log_once
from state import as_int

POLL_S = 15
TICK_BUDGET_S = 2.0                 # resolving per poll, at most (a band of about 1,000 items takes well under one)
TTL_S = 30 * 86400
NEG_TTL_S = 86400
BAND_S = 3600                       # the band is queued again this often (only what isn't known is looked up)
PRUNE_S = 90 * 86400                # a fact not found again for this long is dropped
RETRY_S = 600                       # WoWSims' item data that couldn't be had: tried again after
GIVE_UP = 3
LOOT_S = 120                        # a target's death to a new bag item: a drop seen
WORLD_NPCS = 3
BOE = 2                             # ItemSparse Bonding: binds when equipped
SIDES = ("Alliance", "Horde")
# ChrRaces bits by side, as the knowledge base's quest.races carries them (the classic races, and the two bits its
# one-side quests add: 65613 Alliance, 131250 Horde)
ALLIANCE_RACES = 1 | 4 | 8 | 64 | 1024 | 65536
HORDE_RACES = 2 | 16 | 32 | 128 | 512 | 131072
CONF = {"obs": 0.95, "client": 0.9, "ws": 0.8, "kb": 0.6, "web": 0.3}
PRIO_NOW, PRIO_WORN, PRIO_BAND, PRIO_OTHER = 1, 2, 3, 4
# Data/Live.lua keeps these first when it is over its cap (live_data "p", lower kept first): worn, bags, reward
# choices (3 is for ranked items, a later phase), then the band (by item level), then the rest, newest first
LIVE_P = {"W": 0, "I": 1, "R": 2}
BAND_P, OTHER_P = 100, 100000
AVAIL_CODE = {"now": "n", "later": "l", "nosrc": "x"}
DB_FILES = ("assets/database/db.json", "assets/database/leftover_db.json")
SRC_KEYS = ("kind", "npc", "npc_name", "zone", "zone_name", "instance", "difficulty", "quest", "quest_name",
            "profession", "rep_faction", "rep_level", "conf", "src")
DROPS = ("drop", "trash", "world")


def make_src(kind, origin, **kw):
    """A Src of this kind, from this origin (its conf from CONF)."""
    s: dict = {k: None for k in SRC_KEYS}
    s.update(kw)
    s["kind"], s["conf"], s["src"] = kind, CONF[origin], origin
    return s


def _place(s):
    """Which place a source is: a creature, a vendor (whatever reputation it needs: the knowledge base lists the
    vendor, WoWSims the vendor with its reputation), a quest, a profession ..."""
    kind, npc = s.get("kind"), s.get("npc")
    if npc is not None and kind in DROPS:
        return ("drop", npc)
    if npc is not None and kind in ("vendor", "rep"):
        return ("sell", npc)
    return (kind, npc, s.get("quest"), s.get("profession"), s.get("rep_faction"), s.get("zone") if npc is None else None)


def merge(srcs):
    """One source per place (_place): the most trusted copy, its blanks filled from the others (a vendor that one copy
    says needs a reputation is a "rep" source). -> the most trusted first."""
    out = {}
    for s in srcs:
        k = _place(s)
        have = out.get(k)
        if have is None:
            out[k] = dict(s)
            continue
        hi, lo = (s, have) if (s.get("conf") or 0) > (have.get("conf") or 0) else (have, s)
        m = dict(hi)
        for key, v in lo.items():
            if m.get(key) is None and v is not None:
                m[key] = v
        if k[0] == "sell":
            m["kind"] = "rep" if m.get("rep_faction") else "vendor"
        out[k] = m
    return sorted(out.values(), key=lambda s: -(s.get("conf") or 0))


def fold_world(srcs):
    """A world drop isn't any one creature's: when WoWSims says world drop, or the creatures the knowledge base and
    the player saw dropping it are WORLD_NPCS kinds or more, those creatures (any not from WoWSims) go into one world
    source (the place most of them are in; observed when any was). -> the sources, the most trusted first."""
    seen = [s for s in srcs if s.get("kind") == "drop" and s.get("npc") is not None and s.get("src") != "ws"]
    world = [s for s in srcs if s.get("kind") == "world"]
    if not seen or (not world and len({s["npc"] for s in seen}) < WORLD_NPCS):
        return srcs
    out = [s for s in srcs if not any(s is x for x in seen)]
    if not world:
        places = [s.get("zone_name") for s in seen if s.get("zone_name")]
        out.append(make_src("world", "obs" if any(s.get("src") == "obs" for s in seen) else "kb",
                            zone_name=collections.Counter(sorted(places)).most_common(1)[0][0] if places else None))
    return sorted(out, key=lambda s: -(s.get("conf") or 0))


def norm_name(name):
    """An item name as looked up: lower case, letters, digits and single spaces ("magisters crown")."""
    return " ".join(re.sub(r"[^a-z0-9 ]+", "", str(name or "").lower().replace("-", " ")).split())


def closest(name, index):
    """The id of the item in index ({norm_name: id}) named name, or of the one its name is closest to (a misheard
    name), or None."""
    n = norm_name(name)
    if not n:
        return None
    if n in index:
        return index[n]
    hit = difflib.get_close_matches(n, list(index), n=1, cutoff=0.88)
    return index[hit[0]] if hit else None


def name_index(*blobs):
    """{norm_name: id} of the items in WoWSims database files (the first file's wins a name both have)."""
    out = {}
    for raw in blobs:
        if not raw:
            continue
        d = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        for it in (d.get("items") or ()) if isinstance(d, dict) else ():
            if isinstance(it, dict) and isinstance(it.get("id"), int) and it.get("name"):
                out.setdefault(norm_name(it["name"]), it["id"])
    return out


def as_id(v):
    """An id (a positive int) from loose input, or None."""
    n = as_int(v, bool_ok=False)
    return n if n is not None and n > 0 else None


def kb_version(pack):
    """Which knowledge base the pack reads (its build), or None without one."""
    if pack is None or not getattr(pack, "ok", False):
        return None
    try:
        st = pack.status() or {}
    except Exception as e:  # noqa: BLE001 - its path, else "kb"
        log_once("research.kb_version", e)
        st = {}
    return str(st.get("built") or st.get("path") or getattr(pack.kb, "path", None) or "kb")


def _observed(src):
    return "obs" in str(src or "").split(",")


def vendor_sides(ft):
    """The sides a vendor sells to, from the knowledge base's creature.friendly_to ("A", "H", "AH"; none known:
    both)."""
    return set(SIDES) if not ft else {f for f in SIDES if f[0] in str(ft).upper()}


def quest_sides(side, races):
    """The sides that can do a quest: its side in the knowledge base, else (marked "Both" or nothing) one side when its
    races are only that side's, else both."""
    if side in SIDES:
        return {side}
    races = as_int(races, 0, bool_ok=False) or 0
    a, h = races & ALLIANCE_RACES, races & HORDE_RACES
    if a and not h:
        return {"Alliance"}
    if h and not a:
        return {"Horde"}
    return set(SIDES)


def kb_sources(pack, iid):
    """The knowledge base's sources for an item, each with the sides that can have it from there: the creatures it
    drops from (from WORLD_NPCS kinds on, one world drop in the zone most of them are in), the vendors that sell it,
    the quests that reward it. Rows the player's own recorder saw are observations. -> ([(Src, {side})], [(vendor
    price, {side})])."""
    kb = pack.kb
    out, prices = [], []
    both = set(SIDES)
    drops = kb.q("""SELECT d.creature_id AS npc, d.src AS src, c.name AS name FROM creature_drop d
                    LEFT JOIN creature c ON c.id = d.creature_id WHERE d.item_id = ?""", iid)
    if len(drops) >= WORLD_NPCS:
        places = [_npc_place(pack, r["npc"]) for r in drops]
        places = [p for p in places if p]
        out.append((make_src("world", "obs" if any(_observed(r.get("src")) for r in drops) else "kb",
                             zone_name=max(sorted(set(places)), key=places.count) if places else None), both))
    else:
        for r in drops:
            out.append((make_src("drop", "obs" if _observed(r.get("src")) else "kb", npc=r["npc"],
                                 npc_name=r.get("name"), zone_name=_npc_place(pack, r["npc"])), both))
    for r in kb.q("""SELECT v.npc_id AS npc, v.price AS price, v.src AS src, c.name AS name, c.friendly_to AS ft
                     FROM vendor_item v LEFT JOIN creature c ON c.id = v.npc_id WHERE v.item_id = ?""", iid):
        sides = vendor_sides(r.get("ft"))
        out.append((make_src("vendor", "obs" if _observed(r.get("src")) else "kb", npc=r["npc"],
                             npc_name=r.get("name"), zone_name=_npc_place(pack, r["npc"])), sides))
        if r.get("price"):
            prices.append((r["price"], sides))
    for r in kb.q("""SELECT r.quest_id AS quest, q.name AS name, q.side AS side, q.races AS races FROM quest_reward r
                     LEFT JOIN quest q ON q.id = r.quest_id WHERE r.item_id = ?""", iid):
        out.append((make_src("quest", "kb", quest=r["quest"], quest_name=r.get("name")),
                    quest_sides(r.get("side"), r.get("races"))))
    return out, prices


def _npc_place(pack, npc):
    """The name of the map a creature has the most spawn points on, or None."""
    r = pack.kb.one("""SELECT ui_map, COUNT(*) AS n FROM creature_spawn WHERE creature_id = ? GROUP BY ui_map
                       ORDER BY n DESC LIMIT 1""", npc)
    return pack.map_name(r["ui_map"]) if r and r.get("ui_map") is not None else None


def kb_item_name(pack, iid):
    r = pack.kb.one("SELECT name FROM item WHERE id = ?", iid)
    return (r or {}).get("name") or None


def data_version(db):
    """What a fact found with this item data is checked against: its WoWSims tag, with "/db" while its
    leftover_db.json hasn't come in (so what was found without it is found again once it does)."""
    tag = getattr(db, "tag", None)
    return tag + "/db" if tag and getattr(db, "leftover", True) is False else tag


def _absent(e):
    """Whether a WoWSims file failed because the release has no such file (not offline or cut short)."""
    while e is not None:
        if isinstance(e, urllib.error.HTTPError) and e.code == 404:
            return True
        e = e.__cause__
    return False


class ItemDBSource:
    """Research's item_db_fn: WoWSims' item database (db.json, and leftover_db.json when it can be had) at the
    release ws.data_tag() names, loaded once per tag and kept (the files are kept beside WoWSims, so it loads offline
    too); a tag whose files can't be had is tried again after RETRY_S, the last one loaded staying in use, and so is a
    leftover_db.json that failed for a reason that may pass (the database loaded again once it comes). A new tag
    loaded, the older data folders go (ws.prune). Calling it -> the ItemDB or None; names: {norm_name: id} of every
    item in it (Research looks names up there). load: item_db.ItemDB.load (the tests' seam)."""

    def __init__(self, ws, load=None, now=time.time):
        self.ws, self._load, self._now = ws, load, now
        self.db, self.names = None, {}
        self._tried = {}                                    # tag -> when its files were last fetched
        self._raw = None                                    # the loaded tag's db.json (loaded again with leftover)
        self._left_t = None                                 # leftover_db.json failed and may pass: when it was tried
        self._lock = threading.Lock()

    def _leftover(self, tag, now):
        """leftover_db.json at the tag, or None (when it may pass, tried again after RETRY_S)."""
        try:
            left = self.ws.file(DB_FILES[1], tag)
            self._left_t = None
            return left
        except Exception as e:  # noqa: BLE001 - db.json alone has the gear from level 40 on
            log_once("research.leftover", e)
            self._left_t = None if _absent(e) else now
            return None

    def __call__(self):
        if not self._lock.acquire(blocking=False):
            return self.db                                  # (another thread is loading it)
        try:
            tag, now = self.ws.data_tag(), self._now()
            if self.db is not None and (not tag or self.db.tag == tag):
                if self._left_t is not None and now - self._left_t >= RETRY_S and self._raw is not None:
                    left = self._leftover(self.db.tag, now)
                    if left is not None:
                        self.db = (self._load or item_db.ItemDB.load)(self._raw, left, self.db.tag)
                        self.names = name_index(self._raw, left)
                        log("research", "leftover_db.json came in: %d items" % len(self.names))
                return self.db
            if not tag:
                return self.db
            last = self._tried.get(tag)
            if last is not None and now - last < RETRY_S:
                return self.db
            self._tried[tag] = now
            raw = self.ws.file(DB_FILES[0], tag)
            left = self._leftover(tag, now)
            db = (self._load or item_db.ItemDB.load)(raw, left, tag)
            self.names = name_index(raw, left)
            self.db, self._raw = db, raw
            log("research", "item data from WoWSims %s: %d items" % (tag, len(self.names)))
            prune = getattr(self.ws, "prune", None)
            if callable(prune):
                prune(tag)                                  # (the tags before it: their files aren't read again)
        except Exception as e:  # noqa: BLE001 - offline, cut short: after RETRY_S again
            log_once("research.item_db", e)
        finally:
            self._lock.release()
        return self.db


class Research:
    """The worker: start() polls in its own thread; tick() is one poll (the tests call it). item_db_fn -> the
    item_db.ItemDB (or None), kb_fn -> the knowledge pack (or None), state_fn -> the GameState, enabled_fn -> the
    research.enabled setting (off: nothing is looked up; what is known stays in items_section). bond_fn (item id ->
    ItemSparse Bonding, or None) is the seam for the client's item data; R1's app leaves it None."""

    def __init__(self, home, item_db_fn, kb_fn, state_fn, enabled_fn, now=time.time):
        self.path = os.path.join(home, "data", "research", "facts.json")
        self.item_db_fn, self.kb_fn, self.state_fn, self.enabled_fn, self._now = (
            item_db_fn, kb_fn, state_fn, enabled_fn, now)
        self.bond_fn = None
        self.lock = threading.RLock()
        self.found, self.neg, self.seen, self._faction = self._load()
        self.queue = {}                     # fact -> [prio, seq, why]
        self._seq = 0
        self.failed = {}                    # fact -> failed lookups
        self.db = None                      # the item data the last poll had
        self._tag = self._kbv = None
        self.band, self.band_key, self.band_t = [], None, None
        self.names = {}                     # norm_name -> id: the items Glide has a name for
        self.strip_names = {}               # id -> name: the character's items as the game names them
        for k, f in self.found.items():
            if f.get("name"):
                self.names.setdefault(norm_name(f["name"]), int(k[2:]))
        self._lines = {}                    # id -> (fact time, tooltip text)
        self.kill = None                    # {"npc", "name", "zone", "t"}: the last target seen dead
        self.was_dead = False               # (the target was dead at the last poll: the same kill)
        self.bag_prev = self.worn_prev = self.money_prev = self.quests_prev = None
        self.had = set()                    # item ids in the bags or worn at any poll this session (not drops)
        self.reward_ids = set()             # reward choices seen lately (not drops)
        self.dirty = False
        self.status = {"state": "idle", "facts": len(self.found), "queued": 0}

    # ---- the cache on disk ------------------------------------------------------------------------------------
    def _load(self):
        try:
            d = jsonfile.read(self.path)
        except (OSError, ValueError):
            d = {}
        d = d if isinstance(d, dict) else {}
        now = self._now()

        def full(srcs):                              # (saved without their empty keys)
            return [dict(dict.fromkeys(SRC_KEYS), **s) for s in srcs if isinstance(s, dict) and s.get("kind")]
        facts: dict = {k: dict(v, s=full(v["s"])) for k, v in (d.get("facts") or {}).items()
                 if re.fullmatch(r"i:\d+", str(k)) and isinstance(v, dict) and isinstance(v.get("s"), list)
                 and isinstance(v.get("t"), (int, float)) and now - v["t"] < PRUNE_S}
        neg = {}
        for k, v in (d.get("neg") or {}).items():
            t, fa = (v, None) if isinstance(v, (int, float)) else (v[0], v[1]) if (
                isinstance(v, list) and len(v) == 2) else (None, None)
            if isinstance(t, (int, float)) and now - t < NEG_TTL_S and fa in (None,) + SIDES:
                neg[k] = [t, fa]
        seen = {}
        for k, v in (d.get("seen") or {}).items():
            if re.fullmatch(r"i:\d+", str(k)) and isinstance(v, list):
                kept = [dict(s, t=s.get("t") if isinstance(s.get("t"), (int, float)) else now) for s in full(v)]
                kept = [s for s in kept if now - s["t"] < TTL_S]      # (the kb has the recorder's own by then)
                if kept:
                    seen[k] = kept
        fa = d.get("faction") if d.get("faction") in SIDES else None
        return facts, neg, seen, fa

    def _save(self):
        def packed(srcs):
            return [{k: v for k, v in s.items() if v is not None} for s in srcs]
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with self.lock:
            data = {"v": 1, "faction": self._faction,
                    "facts": {k: dict(f, s=packed(f["s"])) for k, f in self.found.items()},
                    "neg": {k: list(v) for k, v in self.neg.items()},
                    "seen": {k: packed(v) for k, v in self.seen.items()}}
        jsonfile.write_atomic(self.path, data, ensure_ascii=False)
        self.dirty = False

    # ---- what is known ----------------------------------------------------------------------------------------
    def facts(self, item_id):
        """What is known of an item: {"s": [Src], "bond", "conf", "src", "t", ...}, or None."""
        with self.lock:
            return self.found.get("i:%s" % as_id(item_id))

    def _fresh(self, f, now):
        """Is a fact still good: for this faction (or both), from the WoWSims data and knowledge base it was found
        with, and not past its ttl (except while WoWSims' data can't be had: what it said is kept)?"""
        if self._faction and f.get("fa") and f["fa"] != self._faction:
            return False
        if (self._tag and f.get("tag") != self._tag) or (self._kbv and f.get("kb") != self._kbv):
            return False
        if now - f["t"] < f.get("ttl", TTL_S):
            return True
        return bool(f.get("tag")) and not self._tag

    def _known(self, fact, now):
        """Found and still good, known to be unknown lately (to either side, or to this one), or given up on (this
        session)."""
        if self.failed.get(fact, 0) >= GIVE_UP:
            return True
        f = self.found.get(fact)
        if f is not None:
            return self._fresh(f, now)
        n = self.neg.get(fact)
        return n is not None and now - n[0] < NEG_TTL_S and n[1] in (None, self._faction)

    def enqueue(self, fact, why, prio):
        """Find a fact ("i:<item id>") unless it is known: prio 1 on screen now .. 4 the rest; why: what asked."""
        with self.lock:
            if self._known(fact, self._now()):
                return
            q = self.queue.get(fact)
            if q is None or prio < q[0]:
                self._seq += 1
                self.queue[fact] = [prio, self._seq, why]

    # ---- the poll ---------------------------------------------------------------------------------------------
    def start(self):
        def loop():
            while True:
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001 - the next poll tries again
                    log_once("research.tick", e)
                time.sleep(POLL_S)
        threading.Thread(target=loop, daemon=True, name="research").start()
        return self

    def _pack(self):
        try:
            pack = self.kb_fn() if self.kb_fn else None
        except Exception as e:  # noqa: BLE001
            log_once("research.kb", e)
            return None
        return pack if pack is not None and getattr(pack, "ok", False) else None

    @staticmethod
    def character(s):
        """{"class", "level", "faction"} of the character playing now, or None (not in yet)."""
        cls, lvl, fa = str(s.get("class") or "").upper(), as_id(s.get("level")), s.get("faction")
        return {"class": cls, "level": lvl, "faction": fa} if cls and lvl and fa in ("Alliance", "Horde") else None

    def tick(self):
        if not self.enabled_fn():
            return
        self.status["state"] = "running"
        try:
            self._poll(self._now())
        finally:
            self.status.update(state="idle", facts=len(self.found), queued=len(self.queue))

    def _poll(self, now):
        try:
            db = self.item_db_fn()
        except Exception as e:  # noqa: BLE001 - the knowledge base alone, until it can be had
            log_once("research.item_db", e)
            db = None
        pack = self._pack()
        st = self.state_fn()
        s = (st.summary() if st is not None else None) or {}
        gear = getattr(st, "gear", None) or {}
        ch = self.character(s)
        with self.lock:
            self.db, self._tag, self._kbv = db, data_version(db), kb_version(pack)
            if ch and ch["faction"] != self._faction:
                self._faction, self.dirty = ch["faction"], True      # (kept: Data/Live.lua is written for it)
            for kind in ("W", "I", "R"):
                for it in gear.get(kind) or ():
                    iid = as_id(it.get("id"))
                    if iid and it.get("name"):
                        self.names.setdefault(norm_name(it["name"]), iid)
                        self.strip_names[iid] = it["name"]
            self._watch_loot(s, gear, now, set(getattr(st, "quests", None) or ()))
            if ch and db is not None:
                self._band(db, ch, now)
        for kind, prio in (("R", PRIO_NOW), ("I", PRIO_NOW), ("W", PRIO_WORN)):
            for it in gear.get(kind) or ():
                if as_id(it.get("id")):
                    self.enqueue("i:%d" % as_id(it["id"]), {"W": "worn", "I": "bags", "R": "reward"}[kind], prio)
        self._work(db, pack, now)
        if self.dirty:
            self._save()

    def _band(self, db, ch, now):
        """The character's level band, worked out again when it changed (a level-up, new WoWSims data or knowledge),
        and queued then and every BAND_S (what is known isn't looked up again)."""
        key = (ch["class"], ch["level"], ch["faction"], self._tag, self._kbv)
        if key != self.band_key:
            if self.band_key is not None and self.band_key[3:] != key[3:]:
                self.neg.clear()                     # (new data may know them)
            try:
                ids = list(db.band(ch))
            except Exception as e:  # noqa: BLE001 - a class WoWSims' tables don't cover
                log_once("research.band", e)
                ids = []
            self.band_key, self.band_t = key, None
            self.band = sorted(ids, key=lambda i: -_ilvl(db.item(i)))
        if self.band_t is not None and now - self.band_t < BAND_S:
            return
        self.band_t = now
        for iid in self.band:
            self.enqueue("i:%d" % iid, "band", PRIO_BAND)

    def _watch_loot(self, s, gear, now, quests=None):
        """A drop seen: the target died, then within LOOT_S one or two items (not reward choices) came into the bags
        that the character hadn't had in its bags or worn this session. Not in a poll where the worn gear changed (the
        piece taken off goes into the bags), the money went down (a vendor, the auction house, a repair) or a quest
        left the log (quests: its quest ids; a turn-in's one reward)."""
        t = s.get("target") or {}
        npc = as_id(t.get("npc"))
        dead = bool(t.get("dead") and npc)
        if dead and not (self.was_dead and self.kill and self.kill["npc"] == npc):
            self.kill = {"npc": npc, "name": t.get("name"), "zone": s.get("zone"), "t": now}
        self.was_dead = dead
        self.reward_ids |= {as_id(it.get("id")) for it in gear.get("R") or ()}
        bag = {as_id(it.get("id")) for it in gear.get("I") or ()} - {None}
        worn = {as_id(it.get("id")) for it in gear.get("W") or ()} - {None}
        money = as_int(s.get("money"), bool_ok=False)
        spent = money is not None and self.money_prev is not None and money < self.money_prev
        quests = set(quests or ())
        turned_in = self.quests_prev is not None and bool(self.quests_prev - quests)
        if (self.bag_prev is not None and self.kill and now - self.kill["t"] <= LOOT_S and worn == self.worn_prev
                and not spent and not turned_in):
            new = bag - self.had - worn - self.reward_ids
            if 0 < len(new) <= 2:
                for iid in new:
                    self._saw_drop(iid, self.kill, now)
        self.had |= bag | worn
        self.bag_prev, self.worn_prev, self.money_prev, self.quests_prev = bag, worn, money, quests

    def _saw_drop(self, iid, kill, now):
        key = "i:%d" % iid
        srcs = self.seen.setdefault(key, [])
        if not any(x.get("npc") == kill["npc"] for x in srcs):
            srcs.append(dict(make_src("drop", "obs", npc=kill["npc"], npc_name=kill["name"], zone_name=kill["zone"]),
                             t=now))
            log("research", "saw item %d drop from %s" % (iid, kill["name"] or kill["npc"]))
        self.found.pop(key, None)                    # (found again with it, first)
        self.neg.pop(key, None)
        self.dirty = True
        self.enqueue(key, "drop seen", PRIO_NOW)

    def _work(self, db, pack, now):
        """Resolve the queue, most pressing first, for at most TICK_BUDGET_S."""
        t0 = time.monotonic()
        with self.lock:
            order = sorted(self.queue, key=lambda f: self.queue[f][:2])
        for fact in order:
            if time.monotonic() - t0 > TICK_BUDGET_S:
                break
            with self.lock:
                if self.queue.pop(fact, None) is None or self._known(fact, now):
                    continue
                self._resolve(fact, db, pack, now)

    def _resolve(self, fact, db, pack, now):
        """Look a fact up and keep what was found. -> the fact found (also one only for this answer), or None."""
        kind, _, rest = fact.partition(":")
        if kind != "i" or not rest.isdigit():
            return None                              # (quests, recipes, vendors, factions: later phases)
        try:
            entry, fa, keep = self._resolve_item(int(rest), db, pack, now)
        except Exception as e:  # noqa: BLE001 - tried again on the next poll; after GIVE_UP not this session
            n = self.failed[fact] = self.failed.get(fact, 0) + 1
            log_once("research.resolve", e)
            if n < GIVE_UP:
                self._seq += 1
                self.queue[fact] = [PRIO_OTHER, self._seq, "retry"]
            return None
        self.failed.pop(fact, None)
        if not keep:
            return entry                             # (no faction known, and the sides differ: this answer only)
        if entry is None:
            self.found.pop(fact, None)
            self.neg[fact] = [now, fa]
        else:
            self.found[fact] = entry
            self.neg.pop(fact, None)
            if entry.get("name"):
                self.names.setdefault(norm_name(entry["name"]), int(rest))
        self.dirty = True
        return entry

    def _resolve_item(self, iid, db, pack, now):
        """Every source of an item: the knowledge base, WoWSims, the client, the drops the player saw; merged, a world
        drop folded (fold_world), for each side. -> (the fact for the character's faction, or None when nothing knows
        a source there and it doesn't bind when equipped; the faction it holds for, None when the sides make no
        difference; whether to keep it: not while no faction was ever known and the sides differ, when the fact is
        what either side has)."""
        name, rows, prices = None, [], []
        if pack is not None:
            rows, prices = kb_sources(pack, iid)
            name = kb_item_name(pack, iid)
        ws = db if db is not None and db.item(iid) is not None else None       # (WoWSims knows the item)
        if ws is not None:
            name = ws.name(iid) or name
        side_of = getattr(db, "quest_side", None)          # (WoWSims' side of a quest, from any item's, wins)
        if callable(side_of):
            rows = [(s, {side_of(s["quest"])} if s["kind"] == "quest" and side_of(s["quest"]) else ok)
                    for s, ok in rows]
        bond = self.bond_fn(iid) if self.bond_fn else None
        seen = [{k: v for k, v in x.items() if k in SRC_KEYS} for x in self.seen.get("i:%d" % iid) or ()]

        def side(fa):
            srcs = [dict(s) for s, ok in rows if fa is None or fa in ok]
            if ws is not None:
                srcs += list(ws.sources(iid, fa) or ())
            merged = fold_world(merge(srcs + [dict(x) for x in seen]))
            if ws is not None:
                avail = ws.avail(iid, fa, bond, merged)
            else:
                avail = "now" if merged or bond == BOE else "nosrc"
            return merged, avail
        per = {fa: side(fa) for fa in SIDES}
        fa, keep = self._faction, True
        if per[SIDES[0]] == per[SIDES[1]]:
            fa = None                                    # (the same for both sides)
            merged, avail = per[SIDES[0]]
        elif fa is None:
            merged, avail = side(None)
            keep = False
        else:
            merged, avail = per[fa]
        if not merged and bond != BOE:
            return None, fa, keep
        price = min((p for p, ok in prices if fa is None or fa in ok), default=None)
        best = merged[0] if merged else {"conf": CONF["client"], "src": "client"}
        return ({"s": merged, "bond": bond, "price": price, "conf": best["conf"], "src": best["src"], "t": now,
                 "ttl": TTL_S, "name": name or self.strip_names.get(iid), "avail": avail, "fa": fa, "tag": self._tag,
                 "kb": self._kbv}, fa, keep)

    # ---- what the addon and the coach get ---------------------------------------------------------------------
    def _line(self, iid, f, kb):
        """The tooltip text of a fact (item_text.line), kept until the fact is found again."""
        hit = self._lines.get(iid)
        if hit and hit[0] == f["t"]:
            return hit[1]
        text = item_text.line(f.get("s") or [], kb, f.get("bond"))
        self._lines[iid] = (f["t"], text)
        return text

    def items_section(self):
        """live_data's "items" section: {id: {"t": the tooltip text, "a": n|l|x (now, a later phase, no source),
        "w": 1 for a web fact, "p": the order kept when over the cap (lower first)}} for every fact with a text that
        holds for the character playing (or the last one: the file is the whole account's, written for one side)."""
        st = self.state_fn()
        gear = getattr(st, "gear", None) or {}
        ch = self.character((st.summary() if st is not None else None) or {})
        pack = self._pack()
        with self.lock:
            side = ch["faction"] if ch else self._faction
            order = {}
            for kind in ("W", "I", "R"):
                for it in gear.get(kind) or ():
                    iid = as_id(it.get("id"))
                    if iid:
                        order.setdefault(iid, LIVE_P[kind])
            for rank, iid in enumerate(self.band):
                order.setdefault(iid, BAND_P + rank)
            out, n = {}, 0
            for key, f in sorted(self.found.items(), key=lambda kv: -kv[1]["t"]):
                if f.get("fa") and f["fa"] != side:
                    continue                         # (the other side's: its vendor, its quest)
                iid = int(key[2:])
                text = self._line(iid, f, pack)
                if not text:
                    continue
                p = order.get(iid)
                if p is None:
                    p, n = OTHER_P + n, n + 1
                out[iid] = {"t": text, "a": AVAIL_CODE.get(str(f.get("avail")), "x"),
                            "w": 1 if f.get("src") == "web" else 0, "p": p}
            return out

    def _find(self, query):
        """The item id a question names: an id, or a name (the facts', the character's items', WoWSims' and the
        knowledge base's names; a near miss for a misheard one)."""
        q = " ".join(str(query or "").split()).strip(" .?!'\"")
        m = re.fullmatch(r"(?:item\s*)?#?(\d+)", q, re.I)
        if m:
            return int(m.group(1))
        with self.lock:
            index = dict(getattr(self.item_db_fn, "names", None) or {})     # (ItemDBSource: all of WoWSims')
            index.update(self.names)
        hit = index.get(norm_name(q))
        if hit:
            return hit
        find = getattr(self.db, "find", None)                                # (ItemDB: ids by exact name)
        hits = find(q) if callable(find) else None
        if isinstance(hits, list) and hits:
            return hits[0]
        pack = self._pack()
        if pack is not None:
            r = pack.kb.one("SELECT id FROM item WHERE lower(name) = lower(?)", q)
            if r:
                return r["id"]
        return closest(q, index)

    def item_info(self, query):
        """The coach's item_info: {"id", "name", "sources": [where it comes from: the tooltip's line first, then the
        other ways, the most trusted first], "avail": now|later|nosrc, "fact_ids": ["ws:i:<id>" for WoWSims' data,
        "res:i:<id>" for the rest]}. A question is found at once (from the data the last poll had: never a download
        here)."""
        iid = self._find(query)
        if iid is None:
            return {"id": None, "name": str(query), "sources": [], "avail": "nosrc", "fact_ids": [],
                    "error": "no item called %r is known" % str(query)}
        key, now = "i:%d" % iid, self._now()
        pack = self._pack()
        with self.lock:
            if self._known(key, now):
                f = self.found.get(key)
            else:
                self.queue.pop(key, None)
                f = self._resolve(key, self.db, pack, now) or self.found.get(key)   # (a failed lookup: what was)
        srcs = (f or {}).get("s") or []
        texts = []
        for t in [item_text.line(srcs, pack, f.get("bond")) if f else None] + \
                [item_text.source_text(s, pack) for s in srcs[:6]]:
            if t and t not in texts:
                texts.append(t)
        name = (f or {}).get("name") or (self.db.name(iid) if self.db is not None else None) or str(query)
        ids = sorted({("ws:i:%d" if s.get("src") == "ws" else "res:i:%d") % iid for s in srcs})
        if f and not srcs:
            ids = ["res:i:%d" % iid]
        return {"id": iid, "name": name, "sources": texts, "avail": (f or {}).get("avail") or "nosrc",
                "fact_ids": ids}


def _ilvl(rec):
    """A WoWSims item's item level (0 when it has none)."""
    try:
        return int(next(iter((rec or {}).get("scalingOptions", {}).values())).get("ilvl") or 0)
    except (StopIteration, AttributeError, TypeError, ValueError):
        return 0
