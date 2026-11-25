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

What nothing offline knows is looked up on the web (a WebResolver, given with set_web): one background model call with
web search per lookup, one at a time, never while the coach is in a model call of its own, within the daily cap
(research_budget.Budget). A lookup goes out for a question the player asked (ask(): before anything else), and for an
item the player hovers in the game (hover(): GameState.hovered(), while hover_fn, the research.hover setting, allows),
has on screen, wears or may wear soon, as long as their class can use it (eligible; the hovered item also when nothing
can tell, from the background's share of the day) and nothing offline knows where it comes from; later phases add the
numbers a ranked or pursued item lacks (enqueue_web, why "ranked" / "pursued"). Jobs: "i:<id>" where an item comes
from, "r:<npc>:<id>" its drop chance, "v:<npc>:<id>" what a vendor asks for it, "q:<id>" where a quest starts,
"f:<faction>" how its reputation is earned. A fact the model gives without the page that says it (or with a page its
searches never returned), or whose creature, zone or name doesn't read as one, is dropped, and a lookup that made no
search keeps nothing (a failed lookup); the rest are kept TTL_S at conf 0.3 (src "web": never verified; the tooltip and
the coach say "web, unconfirmed"; an item's fact holding one ends with it), and a lookup that found nothing isn't made
again for WEB_NEG_TTL_S. A reply that isn't the JSON asked for counts as a failed lookup (given up after WEB_GIVE_UP
this session). The API (or the model) refusing web search turns web research off for the session. A web source at a
place the game or a database also gives fills nothing into it (merge).

The game checks what the web said. A drop the player sees confirms it (the observed source leads). The creature a web
fact names dying CONTRA_KILLS times without the item (as the player's own loot observation counts kills: of one
creature, KILL_DEDUP_S apart; the larger of the kills seen here and the knowledge base's observed looted kills; a name a
page shortened is the same creature: _same_name) halves the fact's conf and looks it up again, each CONTRA_KILLS more
halving it again. When the coach had said a web fact (item_info's answer), the game cast doubt on it, and the item's
sources no longer hold that place, corrections() hands out {"item", "old", "new"} once: the rules' fact_corrected.

data/research/facts.json: {"v": 1, "faction": the last character's, "facts": {"i:<id>": {"s": [Src], "bond",
"price", "conf", "src", "t", "ttl", "name", "avail", "fa", "tag", "kb"}}, "neg": {"i:<id>": [t, fa]}, "seen":
{"i:<id>": [Src + "t"]}, "web": {job: {"s": [Src + "url", "rate", "cost"], "t", "ttl"}}, "web_neg": {job: t},
"told": {"i:<id>": {"text", "kind", "npc", "who", "t"}}, "miss": {"i:<id>": {npc key: [kills without it,
halvings]}}}. Src: item_db's source shape ({"kind", "npc", "npc_name", "zone", "zone_name", "instance", "difficulty",
"quest", "quest_name", "profession", "rep_faction", "rep_level", "conf", "src"}); a web one also "url", and "rate" (a
drop chance, 0..1) or "cost" (a vendor's price, as the page says it) where it gave them, and "unobtainable" is one
more kind (an item that can't be had: kept, never a source).
"""
import collections
import difflib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
from collections.abc import Callable

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

# ---- the web (WebResolver) ----
PRIO_ASK = 0                        # a question the player asked: before everything
WEB_NEG_TTL_S = 7 * 86400           # a web lookup that found nothing: not made again for a week
WEB_GIVE_UP = 2                     # web lookups that failed or came back as junk: given up this session after
WEB_SEARCHES = 3                    # searches one lookup may make
WEB_KINDS = ("drop", "quest", "vendor", "crafted", "rep", "world", "unobtainable")
UNOBTAINABLE = "unobtainable"
JOB_RE = re.compile(r"[iq]:\d+|[rv]:\d+:\d+|f:[^:\s][^:]{0,79}")
CONTRA_KILLS = 8                    # kills of the creature a web fact names without the item: the fact is doubted
KILL_DEDUP_S = 300                  # kills of one creature this close count once (observations.LOOT_MERGE_S)
BACKGROUND_SHARE = 0.5              # of the day's lookups and dollars, what isn't on screen may use (the rest waits
                                    # for questions and hovered items)
NO_SOURCE_YET = "no known source yet"
BUSY = "the coach is answering"     # WebResolver.ready's why while the coach is in a model call of its own
WEB_NOTE = " (web, unconfirmed)"    # after a web fact, as the addon's tooltip line says it (Compare.lua)
# for an item WoWSims doesn't list, the knowledge base's item row (the game's ItemClass 2 weapon, 4 armor, and its
# ItemSubClass): the weapons each class item_db covers wields, and the armor it wears (0 rings, necks, trinkets; 1 cloth
# and cloaks; 6 shield; 7 libram)
GAME_WEAPONS = {"MAGE": {7, 10, 15, 19}, "PALADIN": {0, 1, 4, 5, 6, 7, 8}}
GAME_ARMOR = {"MAGE": {0, 1}, "PALADIN": {0, 1, 2, 3, 4, 6, 7}}
REP_LEVELS = set(item_db.REP_LEVELS.values())


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
    says needs a reputation is a "rep" source). A web copy fills nothing into one the game or a database gave: what it
    says (a reputation, a zone, a page) would read at the other's trust, without "web, unconfirmed". -> the most
    trusted first."""
    out = {}
    for s in srcs:
        k = _place(s)
        have = out.get(k)
        if have is None:
            out[k] = dict(s)
            continue
        hi, lo = (s, have) if (s.get("conf") or 0) > (have.get("conf") or 0) else (have, s)
        m = dict(hi)
        if not (lo.get("src") == "web" and hi.get("src") != "web"):
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
    source (the place most of them are in; observed when any was). What the web says neither folds nor is folded (a
    page can't outweigh what the game showed). -> the sources, the most trusted first."""
    seen = [s for s in srcs if s.get("kind") == "drop" and s.get("npc") is not None
            and s.get("src") not in ("ws", "web")]
    world = [s for s in srcs if s.get("kind") == "world" and s.get("src") != "web"]
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


# ---- the web ------------------------------------------------------------------------------------------------------
WEB_SYSTEM = (
    "You look up World of Warcraft facts for a levelling coach on WoW Forever, a Classic-era server with changes of "
    "its own. Search the web, then answer with JSON only, no other words:\n"
    '{"facts":[{"kind":"drop|quest|vendor|crafted|rep|world|unobtainable","npc":"","zone":"","name":"",'
    '"standing":"","rate":null,"cost":"","url":""}]}\n'
    "One fact per way to get it. kind: drop (a creature or boss drops it: npc, zone), quest (a quest gives it: name "
    "the quest, npc who gives the quest, zone), vendor (npc sells it, zone, cost the price), crafted (name the "
    "profession), rep (a reputation reward: name the faction, standing the level it needs, npc the vendor), world (a "
    "world drop: zone when it is one zone's), unobtainable (it can't be had). rate: the drop chance as a fraction "
    "(0.12 for 12%) when the page gives one. url: the page that says it; a fact without one is thrown away. Prefer "
    "Forever's own pages (Wowhead's WoW Forever database, the Forever forums), then Classic Era's. Say only what a "
    "page you read says. Nothing found: {\"facts\":[]}")


def job_prompt(job, about=None):
    """What a web lookup asks the model for a job, with the names known (about: {"item", "npc", "quest", "level",
    "faction", "class"}, any of them missing). None for a job it can't ask."""
    a = about or {}
    kind, _, rest = str(job or "").partition(":")
    ids = rest.split(":")

    def named(what, key, n):
        return "%s (%s id %s)" % (a[key], what, n) if a.get(key) else "%s id %s" % (what, n)
    bits = (["level %s" % a["level"]] if a.get("level") else []) + \
        [str(a[k]).capitalize() for k in ("faction", "class") if a.get(k)]
    char = " The player's character: %s." % ", ".join(bits) if bits else ""
    if kind == "i" and rest.isdigit():
        return "Where does %s come from in WoW Forever?%s" % (named("item", "item", rest), char)
    if kind == "r" and len(ids) == 2 and all(x.isdigit() for x in ids):
        return "What is the drop chance of %s from %s? One drop fact with its rate." % (
            named("item", "item", ids[1]), named("npc", "npc", ids[0]))
    if kind == "v" and len(ids) == 2 and all(x.isdigit() for x in ids):
        return ("What does the vendor %s ask for %s: gold, and any items, tokens or reputation it needs? One vendor "
                "fact with the price in cost.%s" % (named("npc", "npc", ids[0]), named("item", "item", ids[1]), char))
    if kind == "q" and rest.isdigit():
        return "Where does the quest %s start? One quest fact: npc the quest giver, zone where.%s" % (
            named("quest", "quest", rest), char)
    if kind == "f" and rest:
        return ("How is reputation with %s earned in WoW Forever? One rep fact per way, cost saying what it takes.%s"
                % (rest, char))
    return None


def _clean(v, n=80):
    """Text from a model's fact on one line, at most n characters ("" for none or a value that isn't text). A "|"
    becomes "/": the game's tooltips read "|c", "|n", "|T" and the like as colours, line breaks and textures."""
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        return ""
    return " ".join(str(v).replace("|", "/").split())[:n]


def _name_like(v, words=6, chars=48, punct=""):
    """Does web text read as a name or a place (a creature, a zone, a faction, a profession; a quest's name with
    punct ":!?" too): at most `words` words and `chars` characters of letters, digits, spaces, apostrophes, hyphens,
    commas and periods? Anything else (a sentence, an instruction) is no name: its fact is dropped. "" is fine."""
    if not v:
        return True
    return len(v) <= chars and len(v.split()) <= words and all(c.isalnum() or c in " '’-,." + punct for c in v)


def _rate(v):
    """A drop chance as a fraction in (0, 1] from 0.12, 12, "12%" or "12.5 %"; None for anything else."""
    pct = False
    if isinstance(v, str):
        s = v.strip()
        pct = s.endswith("%")
        try:
            v = float(s.rstrip("%").strip())
        except ValueError:
            return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return None
    f = float(v) / 100.0 if pct or v > 1 else float(v)
    return round(f, 4) if 0 < f <= 1 else None


def _page(v):
    """A fact's page: an http(s) URL with a host name, else None."""
    s = _clean(v, 500)
    try:
        p = urllib.parse.urlsplit(s)
        host = p.hostname or ""
    except ValueError:
        return None
    return s if p.scheme in ("http", "https") and "." in host else None


def _host(url):
    """A page's site ("wowhead.com" for https://www.wowhead.com/...)."""
    try:
        h = (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def parse_facts(text):
    """The facts in a model's JSON reply (fenced, or with words around it) -> [dict], or None when the reply holds no
    {"facts": [...]}."""
    s = str(text or "")
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(s[i:j + 1])
    except ValueError:
        return None
    if not isinstance(d, dict) or not isinstance(d.get("facts"), list):
        return None
    return [f for f in d["facts"] if isinstance(f, dict)]


def web_src(fact, job, seen=()):
    """One fact of a web lookup as a Src (src "web", conf 0.3, its "url", and "rate" / "cost" when given), or None: a
    kind it doesn't know, no page, a page on a site the lookup's searches never returned (seen: their URLs; none: no
    page can be checked, so none is kept), or a creature, zone or name that doesn't read as one (_name_like: the text
    reaches the coach and the tooltip). The job's own ids go in (the creature of an "r:" or "v:" job, the quest of a
    "q:" job, the faction of an "f:" job)."""
    kind = _clean(fact.get("kind"), 20).lower()
    url = _page(fact.get("url"))
    if kind not in WEB_KINDS or not url:
        return None
    hosts = {_host(u) for u in seen or ()} - {""}
    if _host(url) not in hosts:
        return None                                  # (a page the searches never returned: made up)
    name, npc, zone = _clean(fact.get("name")) or None, _clean(fact.get("npc")) or None, _clean(fact.get("zone"))
    if not (_name_like(npc) and _name_like(zone) and (
            _name_like(name, 8, 60, ":!?") if kind == "quest" else _name_like(name))):
        return None                                  # (not a name: a sentence, an instruction)
    s = make_src(kind, "web", npc_name=None if kind == "world" else npc, zone_name=zone or None)
    if kind == "quest":
        s["quest_name"] = name
    elif kind == "crafted":
        s["profession"] = name
    elif kind == "rep":
        s["rep_faction"] = name
        standing = _clean(fact.get("standing"), 20).capitalize()
        s["rep_level"] = standing if standing in REP_LEVELS else None
    s["url"] = url
    rate, cost = _rate(fact.get("rate")), _clean(fact.get("cost"), 60)
    if rate is not None:
        s["rate"] = rate
    if cost and _name_like(cost, 10, 60):
        s["cost"] = cost                             # (a price as a page says it: kept only when it reads as one)
    k, _, rest = str(job).partition(":")
    ids = rest.split(":")
    if k in ("r", "v") and len(ids) == 2 and ids[0].isdigit():
        s["npc"] = int(ids[0])
    elif k == "q" and rest.isdigit():
        s["quest"] = int(rest)
    elif k == "f" and not s.get("rep_faction"):
        s["rep_faction"] = rest
    return s


def _spawn(fn):
    threading.Thread(target=fn, daemon=True, name="research-web").start()


def _web_held(f):
    """Does a fact hold a source only the web gave?"""
    return any(s.get("src") == "web" for s in (f or {}).get("s") or ())


def _npc_key(s):
    """Which creature a web drop fact names, as its kills are counted: its id, else "n:" and its name."""
    return str(s["npc"]) if s.get("npc") is not None else "n:" + norm_name(s.get("npc_name"))


def _doubted(s, doubts):
    """A web source's conf after its doubts ({npc key: [kills without the item, halvings]}): halved once per
    CONTRA_KILLS kills."""
    halvings = (doubts.get(_npc_key(s)) or [0, 0])[1] if s.get("kind") == "drop" else 0
    return round(CONF["web"] * 0.5 ** halvings, 4)


def _what(s):
    """What a source is besides its creature: its quest, profession, faction or zone, as compared (norm_name)."""
    return norm_name(s.get("quest_name") or s.get("profession") or s.get("rep_faction") or s.get("zone_name"))


def _told_place(s):
    """The place a source the coach said is (compared later with _same_place)."""
    return {"kind": s.get("kind"), "npc": s.get("npc"), "who": norm_name(s.get("npc_name")), "what": _what(s)}


def _same_name(a, b):
    """Are two creature names (norm_name) one creature, as a page may shorten it: the same, or every word of the
    shorter one in the longer one, in order ("golemagg" and "golemagg the incinerator", "emperor thaurissan" and
    "emperor dagran thaurissan"), one of them a word of four letters or more (not "the" alone)?"""
    if not a or not b:
        return False
    if a == b:
        return True
    x, y = a.split(), b.split()
    short, long = (x, y) if len(x) <= len(y) else (y, x)
    rest = iter(long)
    return any(len(w) >= 4 for w in short) and all(w in rest for w in short)


def _same_place(s, told):
    """Is a source the place the coach said (told: _told_place)? Drops with drops and vendors with vendors, by the
    creature's id when both have one, else its name (_same_name: a shortened name is the same creature), else what
    the source is."""
    def group(kind):
        return "drop" if kind in DROPS else "sell" if kind in ("vendor", "rep") else kind
    if group(s.get("kind")) != group(told.get("kind")):
        return False
    if s.get("npc") is not None and told.get("npc") is not None:
        return s["npc"] == told["npc"]
    who = norm_name(s.get("npc_name"))
    if who or told.get("who"):
        return _same_name(who, told.get("who"))
    return _what(s) == told.get("what")


def kb_eligible(pack, iid, ch):
    """Whether the knowledge base's item row says the character can use an item WoWSims doesn't list: a weapon of a
    kind their class wields or armor they wear (GAME_WEAPONS, GAME_ARMOR) needing at most BAND_ABOVE levels more than
    theirs. -> True, False, or None when it can't tell (no row, a row without its class, a class item_db doesn't
    cover)."""
    cls = str((ch or {}).get("class") or "").upper()
    if cls not in GAME_WEAPONS:
        return None
    try:
        r = pack.kb.one("SELECT class, subclass, req_level FROM item WHERE id = ?", iid)
    except Exception as e:  # noqa: BLE001 - a knowledge base without these columns
        log_once("research.kb_eligible", e)
        return None
    if not r or as_int(r.get("class"), bool_ok=False) is None:
        return None
    c, sub = as_int(r.get("class"), bool_ok=False), as_int(r.get("subclass"), bool_ok=False)
    req, level = as_int(r.get("req_level"), 0) or 0, as_int(ch.get("level"), 0) or 0
    if level and req > level + item_db.BAND_ABOVE:
        return False
    return sub in GAME_WEAPONS[cls] if c == 2 else sub in GAME_ARMOR[cls] if c == 4 else False


def _kb_name(pack, table, rid):
    """A creature's or quest's name in the knowledge base, or None."""
    if pack is None:
        return None
    try:
        return (pack.kb.one("SELECT name FROM %s WHERE id = ?" % table, rid) or {}).get("name") or None
    except Exception as e:  # noqa: BLE001 - nameless
        log_once("research.kb_name", e)
        return None


class WebResolver:
    """Research's web lookups: one background model call with web search per job (the LLM client's web_research, on
    the fast model), asked for JSON facts with their pages (WEB_SYSTEM, job_prompt), within the budget
    (research_budget.Budget: asked before with the lookup's likely-high cost, LLMClient.research_estimate; booked at
    that before it goes out, and settled after with what it cost: the larger of the two when a request went out
    unanswered or its price isn't known, nothing when no request went out; the lookup spends at most what the day
    has left). A model whose price isn't known makes no lookup. llm_fn -> the engine's LLM client, or None (no model
    configured: nothing goes out and the job stays queued, silently)."""

    def __init__(self, llm_fn, budget, now=time.time):
        self.llm_fn, self.budget, self._now = llm_fn, budget, now
        self.off = None                 # why web research is off for this session (web search refused)
        # why the last resolve() gave None: "not ready: <why>", "junk", "failed", "refused"
        self.last_why = None
        self.max_searches = WEB_SEARCHES

    def _client(self):
        try:
            return self.llm_fn() if self.llm_fn else None
        except Exception as e:  # noqa: BLE001 - no client: the jobs wait
            log_once("research.llm", e)
            return None

    def ready(self, share=1.0):
        """May a lookup go out now: web research not refused this session, a model configured and reachable (and
        research not backing off), the coach not in a call of its own, a way to search, the model's price known, and
        the budget allowing one at its likely-high cost (share: of the day's lookups and dollars this one may use,
        Budget.allow)? -> (True, None) or (False, why)."""
        return self._ready(share)[:2]

    def _ready(self, share):
        """ready, and the lookup's likely-high cost (LLMClient.research_estimate) -> (ok, why, est)."""
        if self.off:
            return False, self.off, 0.0
        c = self._client()
        if c is None:
            return False, "no model", 0.0
        if getattr(c, "web_off", None):
            self.off = "web search refused: %s" % c.web_off
            log("research", "web lookups off for this session (%s)" % c.web_off)
            return False, self.off, 0.0
        model = (getattr(c, "cfg", None) or {}).get("fast_model")
        available = getattr(c, "available", None)
        if not model or (callable(available) and not available(model)):
            return False, "no model available", 0.0
        tools = getattr(c, "web_search_tools", None)
        found = tools(model, self.max_searches) if callable(tools) else (True, False)
        if isinstance(found, tuple) and not found[0]:
            return False, "no web search for %s" % model, 0.0
        if (getattr(c, "research_backoff_until", 0) or 0) > time.time():
            return False, "backing off", 0.0
        estimate = getattr(c, "research_estimate", None)
        est = estimate(model, self.max_searches) if callable(estimate) else 0.0
        if not isinstance(est, (int, float)) or isinstance(est, bool):
            return False, "no price for %s" % model, 0.0   # (what it costs couldn't be counted against the cap)
        est = float(est)
        if self.budget is None:
            return False, "no budget", 0.0
        ok, why = self.budget.allow(share, est)
        if not ok:
            return ok, why, est
        busy = getattr(c, "busy", None)
        if callable(busy) and busy():
            return False, BUSY, est             # (last: everything else lets it go out once the coach is done)
        return True, None, est

    def resolve(self, job, about=None, share=1.0):
        """Look a job up on the web (about: the names known, see job_prompt; share: see ready). -> the facts found as
        Srcs ([]: the lookup found nothing), or None when it couldn't be made (last_why says why: not ready (also
        the coach starting a call, or the day's dollars, partway), its reply junk, failed (also no search made: an
        answer from memory has no page), web search refused)."""
        ok, why, est = self._ready(share)
        if not ok:
            self.last_why = "not ready: %s" % why
            return None
        prompt = job_prompt(job, about)
        c = self._client()
        if prompt is None or c is None:
            self.last_why = "failed"
            return None
        res = self.budget.reserve(est, share)               # (booked before it goes out: cut short, still counted)
        try:
            text, meta = c.web_research(prompt, system=WEB_SYSTEM, max_searches=self.max_searches,
                                        max_usd=res["room"])
        except Exception as e:  # noqa: BLE001 - counted as a failed lookup, at its reservation
            log_once("research.web_call", e)
            self.last_why = "failed"
            return None
        meta = meta or {}
        sent, answered = meta.get("sent", meta.get("calls") or 0), meta.get("calls") or 0
        cost = float(meta.get("cost_usd") or 0.0)
        if not sent:
            self.budget.settle(res, sent=False)             # (nothing went out: taken back)
        elif sent > answered or meta.get("cost_unknown"):
            self.budget.settle(res, max(cost, est))         # (one unanswered may be billed; a price not known)
        else:
            self.budget.settle(res, cost)
        why = meta.get("why")
        if why in ("refused", "model refused"):
            self.off = "web search refused" if why == "refused" else "the model refused to look it up"
            self.last_why = "refused"
            log("research", "web lookups off for this session (%s)" % self.off)
            return None
        if text is None:
            self.last_why = "failed" if why in ("failed", "empty", "deadline") else "not ready: %s" % why
            return None
        facts = parse_facts(text)
        if facts is None:
            self.last_why = "junk"
            log("research", "web lookup %s: the reply wasn't the facts asked for" % job)
            return None
        urls = meta.get("urls") or ()
        if not ((meta.get("searches") or 0) + (meta.get("local_searches") or 0)) and not urls:
            self.last_why = "failed"                        # (answered without searching: no page says it)
            log("research", "web lookup %s: the model answered without searching" % job)
            return None
        srcs = [s for s in (web_src(f, job, urls) for f in facts) if s is not None]
        self.last_why = None
        log("research", "web lookup %s: %d fact%s kept of %d ($%.3f)" % (
            job, len(srcs), "" if len(srcs) == 1 else "s", len(facts), meta.get("cost_usd") or 0.0))
        return srcs


class Research:
    """The worker: start() polls in its own thread; tick() is one poll (the tests call it). item_db_fn -> the
    item_db.ItemDB (or None), kb_fn -> the knowledge pack (or None), state_fn -> the GameState, enabled_fn -> the
    research.enabled setting (off: nothing is looked up; what is known stays in items_section). bond_fn (item id ->
    ItemSparse Bonding, or None) is the seam for the client's item data; R1's app leaves it None. Web lookups: set_web
    (a WebResolver); hover_fn -> the research.hover setting (None: on); spawn(fn) runs a web lookup beside the poll
    (a thread; the tests run it in place)."""

    def __init__(self, home, item_db_fn, kb_fn, state_fn, enabled_fn, now=time.time):
        self.path = os.path.join(home, "data", "research", "facts.json")
        self.item_db_fn, self.kb_fn, self.state_fn, self.enabled_fn, self._now = (
            item_db_fn, kb_fn, state_fn, enabled_fn, now)
        self.bond_fn = None
        self.lock = threading.RLock()
        self._save_lock = threading.Lock()
        d = self._load()
        self.found, self.neg, self.seen, self._faction = d["facts"], d["neg"], d["seen"], d["faction"]
        self.web_found, self.web_neg = d["web"], d["web_neg"]     # job -> {"s", "t", "ttl"}; job -> when
        self.told, self.miss = d["told"], d["miss"]               # what the coach said; kills without the item
        self.web = None                     # the WebResolver (set_web); None: no web lookups
        self.web_q = {}                     # job -> [prio, seq, why]
        self.web_failed = {}                # job -> failed web lookups (this session)
        self.web_off = None                 # why web lookups are off for this session
        self._web_job = None                # the web lookup running now (one at a time)
        self.spawn = _spawn
        self.hover_fn: Callable[[], object] | None = None    # -> the research.hover setting (None: on)
        self.hovered = None                 # the item the player hovers now (hover)
        self.ch = None                      # the character playing, at the last poll
        self.kill_t = {}                    # npc key -> when its last counted kill was (KILL_DEDUP_S)
        self.watch = []                     # kills of creatures web facts name: {"t", "npc", "items"}, LOOT_S long
        self.corrected = []                 # web facts the coach said that turned out wrong (corrections)
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
        self.status = {"state": "idle", "facts": len(self.found), "queued": 0, "web": "idle", "web_queued": 0}

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
        web = {k: dict(v, s=full(v["s"])) for k, v in (d.get("web") or {}).items()
               if JOB_RE.fullmatch(str(k)) and isinstance(v, dict) and isinstance(v.get("s"), list)
               and isinstance(v.get("t"), (int, float)) and now - v["t"] < v.get("ttl", TTL_S)}
        # (a fact holding the web's word past its ttl goes: the item is found again when it is next asked)
        facts = {k: f for k, f in facts.items() if k in web or not _web_held(f)}
        web_neg = {k: t for k, t in (d.get("web_neg") or {}).items()
                   if JOB_RE.fullmatch(str(k)) and isinstance(t, (int, float)) and now - t < WEB_NEG_TTL_S}
        told = {k: v for k, v in (d.get("told") or {}).items()     # (a correction not said yet is kept)
                if re.fullmatch(r"i:\d+", str(k)) and isinstance(v, dict) and v.get("text")
                and (v.get("fix") or now - (v.get("t") if isinstance(v.get("t"), (int, float)) else 0) < PRUNE_S)}
        miss = {}
        for k, per in (d.get("miss") or {}).items():
            if re.fullmatch(r"i:\d+", str(k)) and isinstance(per, dict):
                per = {str(n): [int(c[0]), int(c[1])] for n, c in per.items() if isinstance(c, list) and len(c) == 2
                       and all(isinstance(x, int) and not isinstance(x, bool) for x in c)}
                if per and k in web:                     # (kills count against a web fact still held)
                    miss[k] = per
        return {"facts": facts, "neg": neg, "seen": seen, "faction": fa, "web": web, "web_neg": web_neg,
                "told": told, "miss": miss}

    def _save(self):
        with self._save_lock:               # (the poll and a web lookup both save: the later state is written last)
            self._write()

    def _write(self):
        def packed(srcs):
            return [{k: v for k, v in s.items() if v is not None} for s in srcs]
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with self.lock:
            data = {"v": 1, "faction": self._faction,
                    "facts": {k: dict(f, s=packed(f["s"])) for k, f in self.found.items()},
                    "neg": {k: list(v) for k, v in self.neg.items()},
                    "seen": {k: packed(v) for k, v in self.seen.items()}}
            if self.web_found or self.web_neg or self.told or self.miss:    # (R2's; an R1 file stays as it was)
                data.update({"web": {k: dict(w, s=packed(w["s"])) for k, w in self.web_found.items()},
                             "web_neg": dict(self.web_neg), "told": {k: dict(v) for k, v in self.told.items()},
                             "miss": {k: {n: list(c) for n, c in per.items()} for k, per in self.miss.items()}})
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
        return bool(f.get("tag")) and not self._tag and not _web_held(f)

    @staticmethod
    def _expired_web(f, now):
        """Is a fact one that holds what the web said, past its ttl (which ends with the web's word: _resolve_item)?
        It isn't shown or said any more (items_section, brief, item_info) until the item is found again."""
        return _web_held(f) and now - f["t"] >= f.get("ttl", TTL_S)

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
        """Find a fact ("i:<item id>") unless it is known: prio 1 on screen now .. 4 the rest; why: what asked. One
        known to have no source offline goes to the web instead, when its gates let it (_want_web)."""
        with self.lock:
            if self._known(fact, self._now()):
                f = self.found.get(fact)
                if (f is None and fact in self.neg) or (f is not None and not f.get("s")):
                    self._want_web(fact, why, prio)
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
            self.status.update(state="idle", facts=len(self.found), queued=len(self.queue),
                               web_queued=len(self.web_q))

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
            self.ch = ch
            if ch and ch["faction"] != self._faction:
                self._faction, self.dirty = ch["faction"], True      # (kept: Data/Live.lua is written for it)
            for kind in ("W", "I", "R"):
                for it in gear.get(kind) or ():
                    iid = as_id(it.get("id"))
                    if iid and it.get("name"):
                        self.names.setdefault(norm_name(it["name"]), iid)
                        self.strip_names[iid] = it["name"]
            self._watch_loot(s, gear, now, set(getattr(st, "quests", None) or ()))
            self._settle_kills(now)
            if ch and db is not None:
                self._band(db, ch, now)
        hovered = getattr(st, "hovered", None)
        on = self.hover_fn() if self.hover_fn is not None else True
        hv = hovered() if callable(hovered) and on else None
        self.hover(hv.get("id") if isinstance(hv, dict) else None)
        for kind, prio in (("R", PRIO_NOW), ("I", PRIO_NOW), ("W", PRIO_WORN)):
            for it in gear.get(kind) or ():
                if as_id(it.get("id")):
                    self.enqueue("i:%d" % as_id(it["id"]), {"W": "worn", "I": "bags", "R": "reward"}[kind], prio)
        self._work(db, pack, now)
        self._web_tick(now)
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
            self._note_kill(self.kill, now)
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
        for w in self.watch:                         # (it dropped: not a kill without it)
            if w["npc"] == kill["npc"]:
                w["items"].pop(key, None)
        per = self.miss.get(key) or {}
        for nk in (str(kill["npc"]), "n:" + norm_name(kill["name"])):
            per.pop(nk, None)                        # (the game confirmed the web: its doubts go)
        self.dirty = True
        self.enqueue(key, "drop seen", PRIO_NOW)

    def _work(self, db, pack, now):
        """Resolve the queue, most pressing first, for at most TICK_BUDGET_S; an item nothing offline knows a source
        for goes to the web when its gates let it (_want_web)."""
        t0 = time.monotonic()
        with self.lock:
            order = sorted(self.queue, key=lambda f: self.queue[f][:2])
        for fact in order:
            if time.monotonic() - t0 > TICK_BUDGET_S:
                break
            with self.lock:
                q = self.queue.pop(fact, None)
                if q is None or self._known(fact, now):
                    continue
                entry = self._resolve(fact, db, pack, now)
                if fact in self.neg or (entry is not None and not entry.get("s")):
                    self._want_web(fact, q[2], q[0])

    def _resolve(self, fact, db, pack, now):
        """Look a fact up and keep what was found. -> the fact found (also one only for this answer), or None."""
        kind, _, rest = fact.partition(":")
        if kind != "i" or not rest.isdigit():
            return None                              # (quests, recipes, vendors, factions: later phases)
        try:
            if fact in self.web_found and pack is not None:
                self._kb_doubts(fact, pack)          # (the observed kills without it: the web facts' conf)
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
        web = self._web_srcs(iid, now)

        def side(fa):
            srcs = [dict(s) for s, ok in rows if fa is None or fa in ok]
            if ws is not None:
                srcs += list(ws.sources(iid, fa) or ())
            merged = fold_world(merge(srcs + [dict(x) for x in seen] + [dict(x) for x in web]))
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
        ttl, w = TTL_S, self.web_found.get("i:%d" % iid)
        if w and any(s.get("src") == "web" for s in merged):
            ttl = max(0, min(TTL_S, w["t"] + w.get("ttl", TTL_S) - now))    # (it ends with the web's word)
        return ({"s": merged, "bond": bond, "price": price, "conf": best["conf"], "src": best["src"], "t": now,
                 "ttl": ttl, "name": name or self.strip_names.get(iid), "avail": avail, "fa": fa, "tag": self._tag,
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
        now = self._now()
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
                if (f.get("fa") and f["fa"] != side) or self._expired_web(f, now):
                    continue                         # (the other side's: its vendor, its quest; the web's, gone by)
                iid = int(key[2:])
                text = self._line(iid, f, pack)
                if not text:
                    continue
                p = order.get(iid)
                if p is None:
                    p, n = OTHER_P + n, n + 1
                web = (item_text.best(f.get("s") or []) or {}).get("src") == "web"     # (the source the line names)
                out[iid] = {"t": text, "a": AVAIL_CODE.get(str(f.get("avail")), "x"), "w": 1 if web else 0, "p": p}
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
        other ways, the most trusted first; a web fact's with WEB_NOTE], "avail": now|later|nosrc, "fact_ids":
        ["ws:i:<id>" for WoWSims' data, "res:i:<id>" for the rest]}, and "looking_up": true when nothing knows where
        it comes from and it is being looked up on the web (a question: ask), or will be once the coach is done
        answering (not while no model, web search or budget lets it: _web_may). A question is found at once (from the
        data the last poll had: never a download here). A web fact it answers with is one the coach said (told):
        corrected once if the game shows it wrong."""
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
            if f is not None and self._expired_web(f, now):
                f = None                                 # (what the web said, gone by)
        srcs = (f or {}).get("s") or []
        line = item_text.line(srcs, pack, f.get("bond")) if f else None
        top = item_text.best(srcs)
        texts = []
        for t, web in [(line, (top or {}).get("src") == "web")] + \
                [(item_text.source_text(s, pack), s.get("src") == "web") for s in srcs[:6]]:
            t = t + WEB_NOTE if t and web else t
            if t and t not in texts:
                texts.append(t)
        with self.lock:
            name = (f or {}).get("name") or self._item_name(iid, pack)
        if not name:                                     # (an id, never said aloud: "#18842" is no name)
            asked = " ".join(str(query or "").split()).strip(" .?!'\"")
            name = "this item" if re.fullmatch(r"(?:item\s*)?#?\d+", asked, re.I) else asked
        ids = sorted({("ws:i:%d" if s.get("src") == "ws" else "res:i:%d") % iid for s in srcs})
        if f and not srcs:
            ids = ["res:i:%d" % iid]
        out = {"id": iid, "name": name, "sources": texts, "avail": (f or {}).get("avail") or "nosrc", "fact_ids": ids}
        with self.lock:
            if line and top is not None and top.get("src") == "web" and f is self.found.get(key):
                prev = self.told.get(key) or {}
                self.told[key] = dict(_told_place(top), text=line, t=now)
                if prev.get("fix"):                      # (a correction not said yet stays to be said)
                    self.told[key]["fix"] = prev["fix"]
                self.dirty = True
            elif not srcs:
                self.ask(key, "question")
                if key == self._web_job or (key in self.web_q and self._web_may()):
                    out["looking_up"] = True
        return out

    def ask_item(self, query):
        """A question the player asked about an item, by its name or id ("where does X drop?": coach_tools.item_asked,
        whoever answers it): ask. -> its id, or None for an item no one knows."""
        iid = self._find(query)
        if iid is not None:
            self.ask("i:%d" % iid, "question")
        return iid

    def _web_may(self):
        """Can a web lookup go out now, or once the coach is done answering (WebResolver.ready: a model, web search,
        the budget)? A queued job that can't ("looking_up" would promise what won't come) stays queued, silently."""
        web = self.web
        if web is None or self.web_off:
            return False
        try:
            ok, why = web.ready()
        except Exception as e:  # noqa: BLE001 - not now
            log_once("research.web_ready", e)
            return False
        return bool(ok) or why == BUSY

    # ---- the web ----------------------------------------------------------------------------------------------
    def set_web(self, resolver):
        """Look up on the web what nothing offline knows: a WebResolver (None: no web lookups)."""
        with self.lock:
            self.web = resolver

    def ask(self, fact, why):
        """A question the player asked about a fact (a job: "i:<id>", "r:<npc>:<id>", "v:<npc>:<id>", "q:<id>",
        "f:<faction>"): looked up on the web before anything else, whatever the item (one something offline knows a
        source for is answered from that), within the budget."""
        fact = str(fact or "")
        with self.lock:
            self._want_web(fact, why or "question", PRIO_ASK, asked=True)

    def hover(self, item_id):
        """The item the player hovers in the game now (None: none): looked up at once offline, and on the web when
        nothing offline knows where it comes from and their class can use it (the same gates as any item)."""
        iid = as_id(item_id)
        with self.lock:
            if iid == self.hovered:
                return
            self.hovered = iid
        if iid:
            self.enqueue("i:%d" % iid, "hover", PRIO_NOW)

    def enqueue_web(self, fact, why, prio):
        """Look a job up on the web, when its gates let it: any job for a question (why "question") or a number a
        ranked or pursued item lacks ("ranked", "pursued"); otherwise only an item ("i:<id>") the character's class
        can use that nothing offline knows a source for."""
        with self.lock:
            self._want_web(str(fact or ""), why, prio)

    def web_facts(self, job):
        """What a web lookup found for a job: {"s": [Src, at its conf after any doubt], "t", "ttl"}, or None (not
        looked up, found nothing, or past its ttl)."""
        with self.lock:
            w = self.web_found.get(str(job))
            if w is None or self._now() - w["t"] >= w.get("ttl", TTL_S):
                return None
            doubts = self.miss.get(str(job)) or {}
            return dict(w, s=[dict(s, conf=_doubted(s, doubts)) for s in w["s"]])

    def corrections(self):
        """The web facts the coach said that the game showed wrong (the creature it named died CONTRA_KILLS times
        without the item, and a new lookup put it elsewhere), each handed out once: [{"item", "old", "new"}], the
        texts as the tooltip says them (new: NO_SOURCE_YET when nothing is known now)."""
        with self.lock:
            out = [v["fix"] for v in self.told.values() if v.get("fix")]
            if out:                                      # (one the coach said again since keeps what it said now)
                self.told = {k: {x: y for x, y in v.items() if x != "fix"} for k, v in self.told.items()
                             if not v.get("fix") or v["fix"].get("old") != v.get("text")}
                self.dirty = True
        return out

    def brief(self, item_id):
        """What the coach is told of an item without a lookup (the item the player hovers): {"id", "name",
        "comes_from": the tooltip's line (WEB_NOTE after a web fact), or None when it isn't known}; None for no
        item."""
        iid = as_id(item_id)
        if not iid:
            return None
        pack = self._pack()
        with self.lock:
            f = self.found.get("i:%d" % iid)
            if f is not None and self._expired_web(f, self._now()):
                f = None                                 # (what the web said, gone by)
            text = self._line(iid, f, pack) if f else None
            if text and (item_text.best((f or {}).get("s") or []) or {}).get("src") == "web":
                text += WEB_NOTE
            return {"id": iid, "name": self._item_name(iid, pack), "comes_from": text}

    def _web_known(self, job, now):
        """Looked up on the web lately (found: its ttl; nothing found: WEB_NEG_TTL_S), or given up this session."""
        if self.web_failed.get(job, 0) >= WEB_GIVE_UP:
            return True
        w = self.web_found.get(job)
        if w is not None and now - w["t"] < w.get("ttl", TTL_S):
            return True
        t = self.web_neg.get(job)
        return t is not None and now - t < WEB_NEG_TTL_S

    def _offline(self, job):
        """Does something offline know where the item of an "i:" job comes from?"""
        f = self.found.get(job) if job.startswith("i:") else None
        return bool(f) and any(s.get("src") != "web" for s in f.get("s") or ())

    def _want_web(self, job, why, prio, asked=False):
        """Queue a web lookup when its gates let it (with the lock held): a job not looked up lately, for an item
        nothing offline knows a source for; a question (asked, or why "question"), or a ranked or pursued item's,
        whatever it is, else only an item the character's class can use (eligible), or the item the player hovers
        when nothing can tell whether they can (no WoWSims row, no class in the knowledge base: often the very items
        no one lists), that one from the background's share of the day (as PRIO_BAND). Without a resolver: none."""
        if self.web is None or self.web_off or not JOB_RE.fullmatch(job) or self._web_known(job, self._now()) \
                or self._offline(job):
            return
        if not (asked or why in ("question", "ranked", "pursued")):
            ok = self._eligible(int(job[2:])) if job.startswith("i:") else False
            if ok is None and self.hovered and job == "i:%d" % self.hovered:
                prio = max(prio, PRIO_BAND)
            elif not ok:
                return
        q = self.web_q.get(job)
        if q is None or prio < q[0]:
            self._seq += 1
            self.web_q[job] = [prio, self._seq, why]

    def _eligible(self, iid):
        """Can the character playing use the item (needing at most BAND_ABOVE levels more)? WoWSims' rule when it lists
        the item; else yes when they wear it, or as the knowledge base's item row says (kb_eligible). -> True,
        False, or None when nothing can tell (no character yet: False)."""
        ch = self.ch
        if not ch:
            return False
        db = self.db
        if db is not None and db.item(iid) is not None:
            try:
                return bool(db.eligible(iid, ch))
            except Exception as e:  # noqa: BLE001 - a class WoWSims' tables don't cover
                log_once("research.eligible", e)
                return None
        if iid in (self.worn_prev or ()):
            return True
        pack = self._pack()
        return kb_eligible(pack, iid, ch) if pack is not None else None

    def _web_tick(self, now):
        """Start the most pressing web lookup, when none is running and one may go out now (the resolver's ready:
        a model, the coach not answering, the budget; what is on screen may use all of the day's budget, the rest
        BACKGROUND_SHARE of it). Jobs that can't go out stay queued, silently."""
        with self.lock:
            if self.web is None or self._web_job is not None or self.web_off or not self.web_q:
                return
            off = getattr(self.web, "off", None)
            if off:
                self.web_off = off
                return
            job = None
            for j in sorted(self.web_q, key=lambda x: self.web_q[x][:2]):
                doubt = self.web_q[j][2] == "doubt"
                if not doubt and j.startswith("i:") and j not in self.found and j not in self.neg:
                    self._resolve(j, self.db, self._pack(), now)     # (offline first: the web for what it lacks)
                if not doubt and (self._web_known(j, now) or self._offline(j)):
                    self.web_q.pop(j)                    # (known by now)
                    continue
                job = j
                break
            if job is None:
                return
            prio, _, why = self.web_q[job]
            share = 1.0 if prio <= PRIO_NOW else BACKGROUND_SHARE
            ok, _why = self.web.ready(share)
            if not ok:
                return
            self.web_q.pop(job)
            self._web_job = job
            about = self._about(job)
        self.status["web"] = "looking up %s" % job
        self.spawn(lambda: self._web_run(job, prio, why, about, share))

    def _web_run(self, job, prio, why, about, share):
        """One web lookup (beside the poll): what it found kept, the item worked out again with it."""
        web = self.web
        try:
            srcs = web.resolve(job, about, share=share) if web is not None else None
        except Exception as e:  # noqa: BLE001 - a failed lookup
            log_once("research.web", e)
            srcs = None
        now, pack = self._now(), self._pack()
        with self.lock:
            self._web_job = None
            self.status["web"] = "idle"
            off = getattr(web, "off", None)
            if off:
                self.web_off = off
            if srcs is None:
                last = str(getattr(web, "last_why", "") or "")
                if last in ("junk", "failed"):
                    n = self.web_failed[job] = self.web_failed.get(job, 0) + 1
                    if n >= WEB_GIVE_UP:
                        log("research", "web lookup %s given up this session" % job)
                        return
                if not self.web_off and job not in self.web_q:
                    self._seq += 1
                    self.web_q[job] = [prio, self._seq, why]     # (it waits for the next chance)
                return
            self.web_failed.pop(job, None)
            self._keep_web(job, srcs, pack, now)
        self._save()

    def _keep_web(self, job, srcs, pack, now):
        """Keep what a web lookup found (a creature's name made its id where the knowledge base or WoWSims knows it)
        and, for an item, work it out again with it: a fact the coach said that it no longer holds is corrected."""
        keep = [self._with_npc(dict(s), pack) for s in srcs]
        if keep:
            self.web_found[job] = {"s": keep, "t": now, "ttl": TTL_S}
            self.web_neg.pop(job, None)
        else:
            self.web_found.pop(job, None)
            self.web_neg[job] = now
        self.dirty = True
        if not job.startswith("i:"):
            return
        self.found.pop(job, None)
        self.neg.pop(job, None)
        self.queue.pop(job, None)
        self._resolve(job, self.db, pack, now)
        self._check_told(job, pack)

    def _with_npc(self, s, pack):
        """A web source's creature by its id, when only one the knowledge base (or WoWSims) knows has its name; else,
        a page having shortened it ("Golemagg"), when only one has a name it is short for (_same_name), the one in the
        fact's zone first."""
        if s.get("npc") is not None or not s.get("npc_name") or s.get("kind") not in ("drop", "vendor", "rep"):
            return s
        want, ids = norm_name(s["npc_name"]), set()
        if pack is not None:
            try:
                ids = {r["id"] for r in pack.kb.q("SELECT id FROM creature WHERE lower(name) = lower(?)",
                                                  s["npc_name"])}
            except Exception as e:  # noqa: BLE001 - by its name only
                log_once("research.npc_id", e)
        npcs = {i: n.get("name") for i, n in (getattr(self.db, "npcs", None) or {}).items() if isinstance(n, dict)}
        if not ids:
            ids = {i for i, n in npcs.items() if norm_name(n) == want}
        if not ids:
            ids = self._npcs_short_for(want, s.get("zone_name"), pack, npcs)
        if len(ids) == 1:
            s["npc"] = next(iter(ids))
        return s

    def _npcs_short_for(self, want, zone, pack, npcs):
        """The creatures (the knowledge base's, else WoWSims') whose names a shortened one (want, norm_name) is short
        for (_same_name); of several, those in the zone (by their spawns) when that leaves fewer."""
        words = sorted((w for w in want.split() if len(w) >= 4), key=len)
        if not words:
            return set()
        found = {}
        if pack is not None:
            try:
                for r in pack.kb.q("SELECT id, name FROM creature WHERE lower(name) LIKE ?", "%" + words[-1] + "%"):
                    if _same_name(norm_name(r.get("name")), want):
                        found[r["id"]] = r.get("name")
            except Exception as e:  # noqa: BLE001 - by its name only
                log_once("research.npc_like", e)
        if not found:
            found = {i: n for i, n in npcs.items() if _same_name(norm_name(n), want)}
        if len(found) > 1 and zone and pack is not None:
            here = {i for i in found if norm_name(_npc_place(pack, i)) == norm_name(zone)}
            if here:
                return here
        return set(found)

    def _web_srcs(self, iid, now):
        """Where the web says an item comes from (not "unobtainable": that is no source), each at its conf after any
        doubt; none past its ttl."""
        w = self.web_found.get("i:%d" % iid)
        if not w or now - w["t"] >= w.get("ttl", TTL_S):
            return []
        doubts = self.miss.get("i:%d" % iid) or {}
        return [dict(s, conf=_doubted(s, doubts)) for s in w["s"] if s.get("kind") != UNOBTAINABLE]

    def _check_told(self, key, pack):
        """After a new web lookup: a web fact the coach said (told) whose place the item's sources no longer hold is
        corrected, once (corrections); to "no known source" only when the game had cast doubt on it."""
        told = self.told.get(key)
        # (the game never cast doubt on it: one page against another isn't the game showing it wrong)
        if not told or told.get("fix") or not self._told_doubted(key, told):
            return
        f = self.found.get(key)
        srcs = (f or {}).get("s") or []
        if any(_same_place(s, told) for s in srcs):
            return
        new = item_text.line(srcs, pack, (f or {}).get("bond")) if srcs else None
        if new and (item_text.best(srcs) or {}).get("src") == "web":
            new += WEB_NOTE
        name = (f or {}).get("name") or self._item_name(int(key[2:]), pack) or "that item"
        told["fix"] = {"item": name, "old": told["text"], "new": new or NO_SOURCE_YET}
        self.dirty = True
        log("research", "%s corrected: %s, not %s" % (name, new or NO_SOURCE_YET, told["text"]))

    def _told_doubted(self, key, told):
        """Did the game cast doubt on the place the coach said (told: a drop whose creature died CONTRA_KILLS times
        or more without the item, by its id or its name)?"""
        for nk, c in (self.miss.get(key) or {}).items():
            if not c[1]:
                continue
            if told.get("npc") is not None and nk == str(told["npc"]):
                return True
            if nk.startswith("n:") and _same_name(nk[2:], told.get("who")):
                return True
        return False

    # ---- the game checks the web: kills of the creature a web fact names, without the item ------------------
    def _named_by_web(self, kill):
        """{item fact: npc key} of the web drop facts that name the creature killed (by its id, else its name, a
        shortened one too: _same_name)."""
        out = {}
        who = norm_name(kill.get("name"))
        for key, w in self.web_found.items():
            if not key.startswith("i:"):
                continue
            for s in w["s"]:
                if s.get("kind") != "drop":
                    continue
                if (s.get("npc") is not None and s["npc"] == kill["npc"]) or (
                        s.get("npc") is None and who and _same_name(norm_name(s.get("npc_name")), who)):
                    out[key] = _npc_key(s)
        return out

    def _note_kill(self, kill, now):
        """A kill of a creature web facts name: watched for LOOT_S (did its item come?). Kills of one creature within
        KILL_DEDUP_S of the first count once, as the loot observation merges them."""
        named = self._named_by_web(kill)
        if not named:
            return
        first = self.kill_t.get(kill["npc"])
        if first is not None and now - first <= KILL_DEDUP_S:
            return
        self.kill_t[kill["npc"]] = now
        self.watch.append({"t": now, "npc": kill["npc"], "items": named})

    def _settle_kills(self, now):
        """Kills watched for LOOT_S whose item didn't come: one more kill without it each; a fact newly doubted is
        worked out again (its conf halved) and looked up again (_doubt)."""
        due = [w for w in self.watch if now - w["t"] > LOOT_S]
        if not due:
            return
        self.watch = [w for w in self.watch if now - w["t"] <= LOOT_S]
        for w in due:
            for key, nk in w["items"].items():
                c = self.miss.setdefault(key, {}).setdefault(nk, [0, 0])
                c[0] += 1
                self.dirty = True
                if self._doubt(key, nk, c):
                    self.found.pop(key, None)
                    self.enqueue(key, "doubt", PRIO_NOW)

    def _kb_doubts(self, key, pack):
        """The knowledge base's count of the creature's looted kills (the player's own loot observation) for each
        web drop fact of an item, when none of them showed the item: the kills without it (_doubt)."""
        iid = int(key[2:])
        for s in self.web_found[key]["s"]:
            if s.get("kind") != "drop" or s.get("npc") is None:
                continue
            try:
                if pack.kb.one("""SELECT 1 AS y FROM creature_drop WHERE creature_id = ? AND item_id = ?
                                  AND src LIKE '%obs%'""", s["npc"], iid):
                    continue                             # (the player saw it drop there: confirmed)
                r = pack.kb.one("""SELECT MAX(n_obs) AS n FROM creature_drop WHERE creature_id = ?
                                   AND src LIKE '%obs%'""", s["npc"])
            except Exception as e:  # noqa: BLE001 - an older knowledge base: the kills seen here only
                log_once("research.kb_kills", e)
                return
            n = as_int((r or {}).get("n"), 0, bool_ok=False) or 0
            if n > ((self.miss.get(key) or {}).get(_npc_key(s)) or [0])[0]:
                c = self.miss.setdefault(key, {}).setdefault(_npc_key(s), [0, 0])
                c[0] = n
                self.dirty = True
                self._doubt(key, _npc_key(s), c)

    def _doubt(self, key, nk, c):
        """A web fact's kills without its item (c: [kills, halvings]): each CONTRA_KILLS more halve its conf once
        and look the item up again. -> whether it was newly halved."""
        due = c[0] // CONTRA_KILLS
        if due <= c[1]:
            return False
        c[1] = due
        self.dirty = True
        self._seq += 1
        self.web_q[key] = [PRIO_WORN, self._seq, "doubt"]
        log("research", "%s: %d kills without it, the web's word on it doubted" % (key, c[0]))
        return True

    def _about(self, job):
        """The names a web lookup's question gives the model (job_prompt's about)."""
        kind, _, rest = job.partition(":")
        ids = rest.split(":")
        ch = self.ch or {}
        pack = self._pack()
        a = {"level": ch.get("level"), "faction": ch.get("faction"), "class": ch.get("class")}
        if kind in ("i", "r", "v") and ids[-1].isdigit():
            a["item"] = self._item_name(int(ids[-1]), pack)
        if kind in ("r", "v") and ids[0].isdigit():
            npc = self.db.npc(int(ids[0])) if self.db is not None and hasattr(self.db, "npc") else None
            a["npc"] = (npc or {}).get("name") or _kb_name(pack, "creature", int(ids[0]))
        if kind == "q" and rest.isdigit():
            a["quest"] = _kb_name(pack, "quest", int(rest))
        return a

    def _item_name(self, iid, pack=None):
        """An item's name: what was found, the game's (the character's items), WoWSims' or the knowledge base's."""
        f = self.found.get("i:%d" % iid)
        name = (f or {}).get("name") or self.strip_names.get(iid)
        if not name and self.db is not None:
            name = self.db.name(iid)
        if not name and pack is not None:
            try:
                name = kb_item_name(pack, iid)
            except Exception as e:  # noqa: BLE001 - nameless
                log_once("research.item_name", e)
        return name or None


def _ilvl(rec):
    """A WoWSims item's item level (0 when it has none)."""
    try:
        return int(next(iter((rec or {}).get("scalingOptions", {}).values())).get("ilvl") or 0)
    except (StopIteration, AttributeError, TypeError, ValueError):
        return 0
