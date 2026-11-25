"""The next move toward the next best-in-slot piece, weighed by effort (Phase 4): of everything Glide's BiS ranks
(bis.py) find better than what a level-60 character wears and can have now, which thing to do in the game gets the
most DPS for the hours it takes: a dungeon run that rolls several upgrades, a vendor, a quest, a reputation, a craft or
the auction house. The coach says the move (rules._pursuit: next_move, pursuit_roll after a boss kill without the item,
pursuit_got when it lands; events() hands them over), Data/Live.lua carries it (live_section: chars[key].next for the
overlay line, /glide next and its pin, chars[key].goal for the tooltip's "your next BiS goal"), and the player steers
it ("chase the robe": pursue; "skip that": skip; both kept across restarts). Told, never shown: nothing here is a list,
panel or screen of ranks, plans or odds; a spoken line carries one number and its range, the reasons ("why") come when
asked.

Ranking (plan):
1. Candidates: each ranked item of the character's CharBis (bis_fn) it can have now (avail "now": a source on its side
   or bind on equip), not worn or in the bags (the game state's own bags too: what just landed), not owned before
   (landed here once), not skipped, with no profession to wear it that the character lacks, gaining more than 2s; the
   pursued goal whatever it gains.
2. The ways to get each one (its sources: research.Research.facts for this side, else WoWSims' own through the item
   data), each an activity ("act"): "inst:<zone>" a dungeon boss (Blackrock Spire, one zone in WoWSims, by its halves:
   "inst:1583:Upper" and "inst:1583:Lower"; a zone known by name only: "inst:<name>"), "raid:<zone>", "mob:<npc>" a
   creature outside an instance, "quest:<id>", "vendor:<npc>", "rep:<faction id, else its name>", "craft:<item>",
   "ah:<item>" (an item that binds when equipped, or one the auction house was seen to have: the Recorder's prices),
   "pvp:<item>" (not estimated: "I can't estimate PvP rewards yet."). Trash and world drops come only through the
   auction house.
3. The chance a try gets it, p: for a drop, effort.drop_chance from the player's own kills of that boss (the tries kept
   here, deduplicated; only those whose loot was seen, or that brought the item: a roll lost on a corpse never opened
   says nothing about the drop), AtlasLoot classic's rate (rates_fn, else the player's own AtlasLootClassic install while
   pursuit.atlasloot_rates is on) and a web rate (research's "r:<npc>:<item>" job), else a labelled guess of GUESS_Q;
   times effort.win_chance (pursuit.group); for an item with random suffixes, times the share of them worth at least
   the one ranked (weights_fn: the character's WoWSims weights; without them one in the number of them). Quests,
   vendors, reputations, crafts and the auction house: 1.
4. The hours a try takes, T: effort.try_hours of its kind (TRY_KW: what each is given; _hours_fallback, its rule, when
   it can't be called), from the player's own run times (kept here: zone in to zone out, effort.run_minutes shrinks
   them toward analyzer.dungeon.run_s), pursuit.form_minutes and pursuit.group (effort: half as long again for 10-player
   Upper Blackrock Spire, none solo), the travel (travel_fn, the route planner, else TRAVEL_MIN, or NEAR_MIN on the
   player's map), the gold missing at the player's gold per hour (money samples kept here), the reputation missing at
   its rate per hour (the Recorder's rise within a session, first turn-in to last, else its standings over reloads),
   the quest chain left (the knowledge base's prerequisites, the quest log; a quest ready to turn in: the way to whom
   it goes, the quickest move there is). Raids wait for the calendar, and once a boss of theirs died, for the weekly
   reset (its lockout). A vendor whose price isn't known: the travel ("I don't know its price yet"), its "v:" job asked
   on the web. What a try takes is said in effort's words (say_effort, say_odds, say_tries;
   the planner's own when an effort.py lacks them).
5. Dominance: an item's gain d' is its d over the better of what the character wears (and its bags) and the best item
   of its slot group that takes fewer expected hours (T/p): the Magister's Crown isn't chased while the vendor's
   Sorcerer's Crown is reachable. An item with a known way to get it is weighed against the others' known ways only
   (a guessed chance never makes it the slower one). Rings and trinkets are worn two at a time: the second best of those (and of the bag
   items) is the one to beat. Everything is worked out again at every poll, so once an item lands the next one is
   weighed against it.
6. value(act) = the sum over slot groups of the best p*d' among the items it rolls (a quest gives one reward: only its
   best), over T(act): DPS per hour. Moves by value; one whose chance is the guess never above a known one that gains
   at least as much; those whose hours can't be told (a price, a rep rate, a pattern unknown; a raid before it opens)
   after all of them, by their gain. A pursued goal's ways go first, the quickest first. "alts": the next ALTS moves.

Following through (tick, every POLL_S, which works the plan out again and keeps it: next_move and last_plan read that,
cheaply, for the rules on the strip's thread; plan() answers from it for PLAN_KEEP_S; between polls watch, every
WATCH_S, looks at the game state alone, so a corpse targeted a few seconds is still a try): money samples (while frames
come); the instance a
move names entered and left (a run, its minutes kept per act, a wipe's corpse run or a hearth out and back within
RUN_GRACE_S part of it; a "zone" event); a boss a candidate drops from killed (the
target dead: a try, KILL_DEDUP_S apart; LOOT_S later without the item a "roll" event for the move's item: "No
Bonecreeper Stylus. That's run 2; it usually takes 3 to 10."); a candidate landing in the bags or worn ("got", with the
next move); the Recorder's kills (after a /reload: tries the strip missed, and the loot seen) and its standings (rep
samples). ledger(char, item) is the hunt so far ("how's the stylus hunt?": its tries and their usual range). The
numbers a top move lacks go to research's web lookups (enqueue_web, why "pursued": "r:", "v:", "q:", "f:" jobs), once
per session each.

data/pursuit.json: {"v": 1, "chars": {key: {"goal": item id or null (the player's goal, else the plan's pick: the
item the first move leads with), "pursued": bool (the goal is the player's), "moves": [Move] (the MOVES_KEEP first),
"tag" (the plan's: the player's goal|act|its items sorted), "tries": [{"t", "act", "npc", "items", "seen", "got",
"looked" (its loot was seen: the Recorder's loot window)}], "skipped": [id], "owned": [id], "runs": {act: [minutes]},
"gold": [[t, copper]], "rep": {faction id: [[t, value]]}, "grind": {faction id: [[t0, t1, gain]]} (a session's rise,
its first turn-in to its last), "rec_t" (the newest Recorder kill read), "told": {key: t}, "pinned": bool}}}, key
"Name-Realm" when the engine knows the realm, else "Name" (bis.char_key).

A Plan: {"char", "goal", "pursued", "tag", "moves": [Move], "alts": [Move, Move]}; a Move: {"act", "key" (act|its items
sorted: the same move, however a kill reorders what it rolls), "kind", "items" (ids, the most worth first), "names"
({id as text: name}), "value" (DPS per hour, or null), "hours" (per try, or null), "odds" (effort.say_odds' clause for
a drop; for the others what it takes, as a clause: a price, a rep rate, the calendar), "why" (the reasons), "text" (the
short line: "Scholomance: Bonecreeper Stylus"), "say" (the spoken line), "where" ({"map": uiMapID, "x": 0..1, "y":
0..1} or null), "place", "zone" (the zone name the strip reports there), "npc", "npc_name", "p" (the lead item's chance
a try), "tries" (the lead item's tries so far), "range" ([p25, p75] tries), "jobs" (its web lookups), "drops"
([{"item", "name", "npc", "npc_name"}]: what drops there, for zoning in) and "done" (true: a quest ready to turn in;
its npc_name and where are whom it goes to)}.
"""
import calendar
import json
import math
import os
import re
import threading
import time
from collections.abc import Callable
from typing import Any, TypeGuard

import jsonfile
from bis import char_key
from log import log, log_once
from state import as_int

POLL_S = 15
WATCH_S = 3                         # between polls the game state is looked at (a boss's corpse, a zone, a landed item)
V = 1
SAVE_S = 60                         # money samples saved at most this often; a try, a goal or a skip at once
LOOT_S = 120                        # a boss's death to its item in the bags: it landed (research.LOOT_S)
KILL_DEDUP_S = 300                  # kills of one creature this close are one (research.KILL_DEDUP_S)
RUN_MIN_S = 300                     # a visit to an instance shorter than this isn't a run
RUN_GRACE_S = 900                   # out of a run's instance this long (a wipe's corpse run, a hearth to repair): the
#                                     same run when the player comes back in; dead or a ghost, it waits for them
MIN_HOURS = 1 / 60.0                # a try said to take no time (a quest ready to turn in): this long, the quickest
GOLD_EVERY_S = 240                 # a money sample at least this often while frames come (an idle gap is longer)
TRIES_KEEP, RUNS_KEEP, GOLD_KEEP, REP_KEEP, OWNED_KEEP, TOLD_KEEP = 400, 12, 2000, 60, 200, 64
MOVES_KEEP = 3                      # moves kept per character (pursuit.json, Data/Live.lua): the next and ALTS more
ALTS = 2
GUESS_Q = 0.1                       # a boss drop nothing gives a chance for: the plan's labelled 1 in 10
TRAVEL_MIN = 10                     # travel to another map when no route planner says (travel_fn)
NEAR_MIN = 3                        # travel on the map the player is on (a vendor or auction house visit in it)
MOB_MIN = 15                      # finding and killing a creature outside an instance (a guess)
WHERE_KEEP_S = 3600                 # an instance's entrance, as looked up, kept this long
UBRS = "Upper Blackrock Spire"
BRS_ZONE = 1583                     # Blackrock Spire: one zone in WoWSims, two runs in the game
RAID_OPEN = calendar.timegm((2026, 12, 9, 0, 0, 0))     # Forever's raids open (effort.RAID_OPENS; the fallback's)
RAID_WAIT = "earliest December 9, then one roll a week"
RAID_LOCKED = "Your raid lockout is used; next week brings one more roll."
# the weekly raid reset: a kill of a raid boss the plan follows uses the raid's lockout until then (weekday, 0 Monday,
# and hour, UTC: the Americas' Tuesday 15:00; Forever's own not announced, so one week after the kill at the latest)
RAID_RESET_WDAY, RAID_RESET_HOUR = 1, 15
# effort.try_hours' rule when it can't be called (a different effort.py): its numbers, for _hours_fallback only
ENTRIES_PER_HOUR, BIG_FORM, RAID_MIN, RAID_FORM_MIN, QUEST_MIN = 5, 1.5, 180, 30, 15
# what plan() gives effort.try_hours for each kind (its docstring's names; the fallback reads the same)
TRY_KW = {"inst": ("instance", "history", "prior_s", "run_min", "run_src", "form_min", "group", "travel_min"),
          "raid": ("now", "instance", "history", "travel_min", "locked_until"),
          "quest": ("steps", "done", "travel_min"),
          "vendor": ("price", "price_src", "money", "gph", "gph_src", "travel_min"),
          "rep": ("target", "value", "standing", "rate", "rate_src"),
          "crafted": ("profession", "have", "money", "gph", "gph_src"),
          "ah": ("price", "money", "gph", "gph_src", "travel_min"), "pvp": ()}
UNITS = {"inst": "runs", "raid": "weeks", "mob": "kills"}       # effort.say_odds / say_tries' unit per kind
PLAN_KEEP_S = POLL_S + 5            # plan() without fresh: the last poll's plan while this young (the rules ask often)
EVENTS_KEEP = 32                    # follow-through events kept for events() (the newest)
PRIO_WEB = 2                        # research's PRIO_WORN: a pursued item's number before the band's
WEB_TOP = 3                         # web lookups only for the numbers of this many top moves
DUNGEON, RAID = 1, 2                # WoWSims' drop difficulty
TWO_SLOTS = ("Ring", "Trinket")     # the BiS ranks' slot groups worn twice (bis.GROUPS)
BOE = 2                             # ItemSparse Bonding: binds when equipped
DUNGEON_QUEST = 81                  # kb quest.quest_info
REP_VALUE = {"Neutral": 0, "Friendly": 3000, "Honored": 9000, "Revered": 21000, "Exalted": 42000}
REP_STANDING = {"Hated": 1, "Hostile": 2, "Unfriendly": 3, "Neutral": 4, "Friendly": 5, "Honored": 6, "Revered": 7,
                "Exalted": 8}
# the source words of effort's numbers, as a player reads them (anything else: said as it is)
SRC_WORDS = {"obs": "your own runs", "own": "your own runs", "player": "your own runs", "prior": "a guess",
             "guess": "a guess", "unknown": "a guess", "default": "a guess", "calendar": "the raid calendar",
             "kb": "Glide's data", "ws": "WoWSims", "web": "web, unconfirmed", "classic": "classic's"}
_EMPTY: dict[str, Any] = {"char": None, "goal": None, "pursued": False, "tag": "", "moves": [], "alts": []}


def _finite(v: object) -> TypeGuard[int | float]:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _ids(xs):
    return [int(x) for x in xs if isinstance(x, int) and not isinstance(x, bool)] if isinstance(xs, list) else []


def _pairs(xs):
    return [[x[0], x[1]] for x in xs if isinstance(x, list) and len(x) == 2 and _finite(x[0]) and _finite(x[1])] \
        if isinstance(xs, list) else []


def _where(m, x, y):
    """A pin: {"map", "x", "y"} with x and y 0..1, from the knowledge base's map percent (0..100); or None."""
    if as_int(m) is None or not _finite(x) or not _finite(y):
        return None
    return {"map": int(m), "x": round(float(x) / 100.0, 4), "y": round(float(y) / 100.0, 4)}


def gold_text(copper):
    """12g / 12g 50s / 50s / 75c."""
    c = max(0, int(copper))
    g, s, cc = c // 10000, (c // 100) % 100, c % 100
    if g:
        return "%dg %ds" % (g, s) if s else "%dg" % g
    return "%ds" % s if s else "%dc" % cc


def parse_gold(text):
    """A price as a page says it ("12g 50s", "12 gold", "1,200 g") in copper, or None when it names no gold, silver or
    copper (tokens, marks)."""
    t = str(text or "").lower().replace(",", "")
    total, found = 0, False
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(gold|g|silver|s|copper|c)\b", t):
        found = True
        total += float(amount) * {"g": 10000, "s": 100, "c": 1}[unit[0]]
    return int(total) if found else None


def age_text(s):
    s = max(0, int(s))
    if s < 3600:
        return "within the hour"
    if s < 86400:
        h = s // 3600
        return "%d hour%s ago" % (h, "" if h == 1 else "s")
    d = s // 86400
    return "yesterday" if d == 1 else "%d days ago" % d


def hours_text(h):
    m = int(round(h * 60))
    if m < 90:
        return "%d minutes" % max(1, m)
    return "%.1f hours" % h


def src_words(src):
    s = str(src or "")
    return SRC_WORDS.get(s.lower(), s or "a guess")


def faction_text(name):
    """"the League of Arathor" for WoWSims' "The League of Arathor" (item_text's way), "a faction" for none."""
    n = str(name or "").strip()
    return "the " + n[4:] if n.startswith("The ") else n or "a faction"


def plural(name):
    """Whether an item's name is a plural ("Ghostloom Leggings", "Robes of the Exalted"; not "Bonecreeper Stylus" or
    "Magister's Mantle"): its head word (the one before " of ", else the last) ends in a lone s."""
    head = str(name or "").split(" of ")[0].split()
    w = head[-1].lower() if head else ""
    return w.endswith("s") and not w.endswith(("ss", "us", "is", "'s"))


def is_are(name):
    return "are" if plural(name) else "is"


def _clause(sentence):
    """A sentence as a clause after "; ": its period gone, its first letter small unless it is "I" ("It costs 5g."
    -> "it costs 5g"; "I don't know its price yet." stays "I ...")."""
    t = str(sentence or "").strip().rstrip(".")
    if not t or t.startswith(("I ", "I'")):
        return t
    return t[:1].lower() + t[1:]


def possessive(name):
    return name + ("'" if name.endswith("s") else "'s")


def join_names(names):
    names = [n for n in names if n]
    if len(names) <= 1:
        return "".join(names)
    return "%s and %s" % (", ".join(names[:-1]), names[-1])


def _hours_fallback(kind, instance=None, run_min=None, form_min=None, group=None, travel_min=None, now=None,
                    steps=None, done=None, price=None, money=None, gph=None, target=None, value=None, rate=None,
                    locked_until=None, **_):
    """effort.try_hours' rule, for when it can't be called (a different effort.py): (hours or None, src)."""
    travel = float(travel_min or 0)
    if kind == "inst":
        form = 0.0 if group == "solo" else float(form_min or 0) * (BIG_FORM if instance == UBRS else 1.0)
        return max(float(run_min or 0) + form + travel, 60.0 / ENTRIES_PER_HOUR) / 60.0, "prior"
    if kind == "raid":
        if now is not None and now < RAID_OPEN:
            return None, "calendar"
        if _finite(locked_until) and (now is None or now < locked_until):
            return None, "lockout"
        return (RAID_MIN + RAID_FORM_MIN + travel) / 60.0, "prior"
    if kind == "quest":
        return ((travel if done else float(steps or 1) * QUEST_MIN + travel) / 60.0), "prior"
    if kind in ("vendor", "ah"):
        if not _finite(price):
            return (travel / 60.0, "noprice") if kind == "vendor" else (None, "noprice")
        missing = max(0.0, float(price) - float(money or 0))
        if not missing:
            return travel / 60.0, "obs"
        if not _finite(gph) or gph <= 0:
            return None, "unknown"
        return travel / 60.0 + missing / float(gph), "obs"
    if kind == "rep":
        if target not in REP_VALUE or not _finite(value):
            return None, "nostanding"
        need = REP_VALUE[target] - float(value)
        if need <= 0:
            return 0.0, "obs"
        return (need / float(rate), "obs") if _finite(rate) and rate > 0 else (None, "unknown")
    if kind == "crafted":
        return None, "crafter"
    return None, kind


class Planner:
    """The worker: start() polls in its own thread; tick() is one poll (the tests call it). bis_fn(char key) -> the
    BisRanker's CharBis or None; research: research.Research (facts, web_facts, enqueue_web, db) or None; effort_mod:
    effort.py (drop_chance, win_chance, run_minutes, try_hours, quantiles, say_odds, gold_per_hour, rep_per_hour);
    kb_fn -> the knowledge pack or None; state_fn -> the GameState; recorder: recorder_data.RecorderData (skills,
    factions, auction, kills) or None. opt: the pursuit settings (.get(name): enabled, pin, atlasloot_rates, group,
    form_minutes; None: the manifest's defaults); item_db_fn -> the item_db.ItemDB (None: research's); wow_dir_fn -> the
    WoW folder (AtlasLootClassic's rates). Seams the app may set: travel_fn(here, where) -> minutes (the route planner;
    here and where {"map", "x", "y"} or None), entrance_fn(zone id, name) -> {"map", "x", "y"} (None: the knowledge
    pack's instance_entrance), rates_fn() -> AtlasLoot's {npc id or boss name: {item id: rate}}, weights_fn(name) ->
    the character's WoWSims weights {code: EP}."""

    def __init__(self, home, bis_fn, research, effort_mod, kb_fn, state_fn, recorder, now=time.time, opt=None,
                 item_db_fn=None, wow_dir_fn=None):
        self.path = os.path.join(home, "data", "pursuit.json")
        self.bis_fn, self.research, self.effort, self.kb_fn, self.state_fn, self.recorder = (
            bis_fn, research, effort_mod, kb_fn, state_fn, recorder)
        self.opt, self.item_db_fn, self.wow_dir_fn = opt, item_db_fn, wow_dir_fn
        self._now = now
        self.travel_fn: Callable[..., object] | None = None
        self.entrance_fn: Callable[..., object] | None = None
        self.rates_fn: Callable[[], object] | None = None
        self.weights_fn: Callable[[str], object] | None = None
        self.on_goal_cleared: Callable[[str], object] | None = None
        self.lock = threading.RLock()
        self.data = self._load()
        self.current = None                 # the key of the character playing, at the last poll
        self.seen = {}                      # key -> this session's view: {"have", "zone", "run", "kill", "dead"}
        self.waiting = []                   # tries waiting LOOT_S for their item: {"key", "t", "try"}
        self.pending = []                   # events for the rules (events())
        self.npcs = {}                      # key -> {npc: (act, [item ids])}: the bosses the candidates drop from
        self.chance = {}                    # key -> {item id: (p, src, win, share)}: the lead chance a try, last plan
        self.asked = set()                  # web jobs asked this session
        self._rates = (None, None, {})      # (path, mtime, AtlasLoot's rates)
        self._rates_t = 0.0
        self._run_s = None                  # analyzer.dungeon.run_s ([value] once read)
        self._wheres = {}                   # (zone, name) -> (when, an instance's entrance or None)
        self._plans = {}                    # key -> (when, the plan last worked out): plan() within PLAN_KEEP_S
        self._dirty, self._urgent, self._saved_t = False, False, 0.0     # (urgent: saved at this poll)
        self.status = {"state": "idle", "error": None}

    # ---- pursuit.json -------------------------------------------------------------------------------------------
    @staticmethod
    def _blank():
        return {"goal": None, "pursued": False, "moves": [], "tag": "", "tries": [], "skipped": [], "owned": [],
                "runs": {}, "gold": [], "rep": {}, "grind": {}, "rec_t": 0, "told": {}, "pinned": False}

    def _load(self):
        try:
            d = jsonfile.read(self.path)
        except (OSError, ValueError):
            d = None
        chars = d.get("chars") if isinstance(d, dict) and d.get("v") == V else None
        out = {}
        for k, c in (chars if isinstance(chars, dict) else {}).items():
            if not isinstance(k, str) or not k or not isinstance(c, dict):
                continue
            e = self._blank()
            goal = c.get("goal")
            e["goal"] = goal if isinstance(goal, int) and not isinstance(goal, bool) else None
            e["pursued"] = bool(c.get("pursued")) and e["goal"] is not None
            e["moves"] = [m for m in c.get("moves") or () if isinstance(m, dict) and isinstance(m.get("act"), str)
                          and isinstance(m.get("items"), list)][:MOVES_KEEP] if isinstance(c.get("moves"), list) else []
            e["tag"] = c["tag"] if isinstance(c.get("tag"), str) else ""
            for t in c.get("tries") or () if isinstance(c.get("tries"), list) else ():
                if isinstance(t, dict) and _finite(t.get("t")) and isinstance(t.get("act"), str):
                    e["tries"].append({"t": t["t"], "act": t["act"], "npc": as_int(t.get("npc")),
                                       "items": _ids(t.get("items")), "seen": _ids(t.get("seen")),
                                       "got": bool(t.get("got")), "looked": bool(t.get("looked"))})
            e["skipped"], e["owned"] = _ids(c.get("skipped")), _ids(c.get("owned"))
            runs = c["runs"] if isinstance(c.get("runs"), dict) else {}
            e["runs"] = {str(a): [float(m) for m in ms if _finite(m)][-RUNS_KEEP:] for a, ms in runs.items()
                         if isinstance(ms, list)}
            e["gold"] = _pairs(c.get("gold"))[-GOLD_KEEP:]
            rep = c["rep"] if isinstance(c.get("rep"), dict) else {}
            e["rep"] = {str(f): _pairs(xs)[-REP_KEEP:] for f, xs in rep.items()}
            grind = c["grind"] if isinstance(c.get("grind"), dict) else {}
            e["grind"] = {str(f): [[float(x) for x in g] for g in xs if isinstance(g, list) and len(g) == 3
                                   and all(_finite(x) for x in g)][-REP_KEEP:]
                          for f, xs in grind.items() if isinstance(xs, list)}
            e["rec_t"] = c["rec_t"] if _finite(c.get("rec_t")) else 0
            told = c["told"] if isinstance(c.get("told"), dict) else {}
            e["told"] = {str(t): v for t, v in told.items() if _finite(v)}
            e["pinned"] = bool(c.get("pinned"))
            out[k] = e
        return {"v": V, "chars": out}

    def _save(self):
        with self.lock:
            jsonfile.write_atomic(self.path, self.data, ensure_ascii=False, separators=(",", ":"))
            self._dirty, self._urgent, self._saved_t = False, False, self._now()

    def _char(self, key):
        """(Under the lock) The character's entry, made when new."""
        c = self.data["chars"].get(key)
        if c is None:
            c = self.data["chars"][key] = self._blank()
        return c

    # ---- seams --------------------------------------------------------------------------------------------------
    def _opt(self, name):
        """The pursuit.<name> setting (None when it can't be read: a manifest without it)."""
        o = self.opt
        if o is None:
            try:
                import settings
                o = self.opt = settings.Settings("pursuit", {})
            except Exception as e:  # noqa: BLE001 - every setting unset
                log_once("pursuit.settings", e)
                return None
        try:
            return o.get(name)
        except Exception as e:  # noqa: BLE001 - (KeyError: a manifest without the pursuit settings yet)
            log_once("pursuit.opt.%s" % name, e)
            return None

    def _on(self):
        return self._opt("enabled") is not False

    def enabled(self):
        """Whether the planner plans now (pursuit.enabled): off, nothing is planned, said or written."""
        return self._on()

    def _call(self, fn_name, *a, **kw):
        """effort.<fn_name>(...), or None when it isn't there or fails (logged once)."""
        fn = getattr(self.effort, fn_name, None)
        if not callable(fn):
            return None
        try:
            return fn(*a, **kw)
        except Exception as e:  # noqa: BLE001 - that number unknown
            log_once("pursuit.effort.%s" % fn_name, e)
            return None

    def _try_hours(self, kind, src=None, **kw):
        """effort.try_hours(kind, **kw) -> (hours or None, src); its fallback rule when it can't be called (its
        numbers' source then: src, else "guess")."""
        r = self._call("try_hours", kind, **kw)
        if isinstance(r, (tuple, list)) and len(r) == 2 and (r[0] is None or _finite(r[0])):
            return (float(r[0]) if r[0] is not None else None), str(r[1] or src or "")
        h, s = _hours_fallback(kind, **kw)
        return h, (src or s) if h is not None else s

    def _db(self):
        try:
            if self.item_db_fn is not None:
                return self.item_db_fn()
        except Exception as e:  # noqa: BLE001 - research's then
            log_once("pursuit.item_db", e)
        return getattr(self.research, "db", None)

    def _pack(self):
        try:
            pack = self.kb_fn() if self.kb_fn is not None else None
        except Exception as e:  # noqa: BLE001
            log_once("pursuit.kb", e)
            return None
        return pack if pack is not None and getattr(pack, "ok", False) else None

    def _rec(self, method, key, *a):
        """The Recorder's answer for a character: asked by its key, then by its name alone."""
        fn = getattr(self.recorder, method, None)
        if not callable(fn):
            return None
        out = None
        for who in dict.fromkeys((key, str(key or "").split("-")[0])):
            try:
                out = fn(who, *a)
            except Exception as e:  # noqa: BLE001 - not known
                log_once("pursuit.recorder.%s" % method, e)
                return None
            if out:
                return out
        return out

    def _auction(self, iid, key=None):
        """The newest auction house price the Recorder saw for the item ({"copper", "t"}; on the character's realm and
        side when it can tell), or None."""
        fn = getattr(self.recorder, "auction", None)
        if not callable(fn):
            return None
        try:
            try:
                # (the character's realm and side only: another's price, another house, is no price for them)
                a = fn(iid, key) if key else fn(iid)
            except TypeError:
                a = fn(iid)                         # (a recorder without the character argument)
        except Exception as e:  # noqa: BLE001
            log_once("pursuit.recorder.auction", e)
            return None
        return a if isinstance(a, dict) and _finite(a.get("copper")) and a["copper"] > 0 else None

    def _atlasloot(self):
        """AtlasLoot classic's drop chances: rates_fn's, else read from the player's install (re-read when it
        changes, looked at every minute) while pursuit.atlasloot_rates is on."""
        if self.rates_fn is not None:
            try:
                r = self.rates_fn()
                return r if isinstance(r, dict) else {}
            except Exception as e:  # noqa: BLE001
                log_once("pursuit.rates", e)
                return {}
        if self._opt("atlasloot_rates") is False or self.wow_dir_fn is None:
            return {}
        now = self._now()
        if now - self._rates_t < 60:
            return self._rates[2]
        self._rates_t = now
        try:
            import atlasloot_rates
            path = atlasloot_rates.find(self.wow_dir_fn())
            mtime = os.path.getmtime(path) if path else None
            if (path, mtime) != self._rates[:2]:
                rates = atlasloot_rates.read(path) if path else {}
                self._rates = (path, mtime, rates if isinstance(rates, dict) else {})
        except Exception as e:  # noqa: BLE001 - no classic chances
            log_once("pursuit.atlasloot", e)
        return self._rates[2]

    def _run_prior(self):
        """analyzer.dungeon.run_s (None when the knowledge layer can't say)."""
        if self._run_s is None:
            try:
                from knowledge.analyzer import params
                v = params.get("analyzer.dungeon.run_s", None)
                self._run_s = [float(v) if _finite(v) else None]
            except Exception as e:  # noqa: BLE001
                log_once("pursuit.run_prior", e)
                self._run_s = [None]
        return self._run_s[0]

    # ---- the poll -----------------------------------------------------------------------------------------------
    def start(self):
        def loop():
            last = None
            while True:
                try:
                    t = time.monotonic()
                    if last is None or t - last >= POLL_S:
                        last = t
                        self.tick()
                    else:
                        self.watch()
                except Exception as e:  # noqa: BLE001 - the next poll tries again
                    log_once("pursuit.tick", e)
                    self.status = {"state": "error", "error": str(e)[:300]}
                time.sleep(WATCH_S)
        threading.Thread(target=loop, daemon=True, name="pursuit").start()
        return self

    def tick(self):
        """One poll: what the game state shows, the Recorder read, the plan worked out again and kept."""
        self._poll(True)

    def watch(self):
        """Between polls (every WATCH_S): only what the game state shows, so a boss's corpse targeted for a few
        seconds is still a try; an item that landed works the plan out at once (its "got" event names the next
        move)."""
        self._poll(False)

    def _poll(self, full):
        if not self._on():
            return
        st = self.state_fn()
        s = (st.summary() if st is not None else None) or {}
        if not s.get("name"):
            return
        key, now = char_key(s), self._now()
        gear = getattr(st, "gear", None) or {}
        self.current = key
        got = self._observe(key, s, gear, now)
        if not full and not got:
            with self.lock:
                if self._dirty and self._urgent:
                    self._save()                    # (a try: kept at once)
            return
        if key not in self.npcs:
            self.plan(key, fresh=True)              # (the bosses the Recorder's kills are matched against, first)
        self._read_recorder(key, now)
        plan = self.plan(key, fresh=True)
        with self.lock:
            c = self._char(key)
            if got:
                nxt = plan["moves"][0]["text"] if plan["moves"] else None
                for e in got:
                    e["next"] = nxt
                    e["say"] = "%s %s yours." % (e["name"], is_are(e["name"])) + (" Next: %s." % nxt if nxt else "")
                for e in got:
                    self._event(e)
                self._urgent = True
            moves = [dict(m) for m in plan["moves"][:MOVES_KEEP]]
            if moves != c["moves"] or plan["goal"] != c["goal"] or plan["tag"] != c["tag"]:
                if plan["tag"] != c["tag"]:
                    log("pursuit", "%s: next move %s" % (key, plan["moves"][0]["text"] if plan["moves"] else "none"))
                    self._urgent = True
                c["moves"], c["goal"], c["tag"] = moves, plan["goal"], plan["tag"]
                self._dirty = True
            if self._dirty and (self._urgent or now - self._saved_t >= SAVE_S):
                self._save()
        if any(e.get("goal") for e in got):
            self._goal_cleared(key)
        self._want_numbers(key, plan)
        self.status = {"state": "ready", "error": None}

    def _goal_cleared(self, key):
        """The player's goal is reached (it landed) or dropped (skipped): on_goal_cleared(key), the app's seam (the
        coach's memory note of the goal goes with it)."""
        fn = self.on_goal_cleared
        if fn is None:
            return
        try:
            fn(key)
        except Exception as e:  # noqa: BLE001 - the note stays until the plan is asked for
            log_once("pursuit.goal_cleared", e)

    def _observe(self, key, s, gear, now):
        """What the game state shows since the last poll: money; an instance a move names entered or left; a boss a
        candidate drops from killed; a candidate in the bags or worn. -> the "got" events (their next move filled in
        once the plan is worked out again)."""
        got = []
        with self.lock:
            c = self._char(key)
            v = self.seen.setdefault(key, {"have": None, "zone": None, "run": None, "away": None, "kill": None,
                                           "dead": False})
            # (money only while frames come: a closed game's last frame isn't play, and would read as no gold made)
            money = as_int(s.get("money"), bool_ok=False) if s.get("fresh", True) else None
            g = c["gold"]
            if money is not None and (not g or money != g[-1][1] or now - g[-1][0] >= GOLD_EVERY_S):
                g.append([int(now), money])
                del g[:-GOLD_KEEP]
                self._dirty = True
            have = {as_int(it.get("id")) for k in ("W", "I") for it in gear.get(k) or ()} - {None}
            tracked = {i for xs in (self.npcs.get(key) or {}).values() for i in xs[1]}
            tracked |= {i for m in c["moves"] for i in m.get("items") or ()}
            if c["goal"]:
                tracked.add(c["goal"])
            if v["have"] is not None:
                for iid in sorted((have - v["have"]) & tracked):
                    got.append(self._landed(key, c, iid, now))
            v["have"] = have
            self._zone(key, c, v, s.get("zone"), now, bool(s.get("dead")))
            t = s.get("target") or {}
            npc = as_int(t.get("npc"))
            dead = bool(t.get("dead") and npc)
            if dead and not (v["dead"] and v["kill"] == npc):
                self._kill(key, c, npc, now)
                v["kill"] = npc
            v["dead"] = dead
            self._settle(now)
        return got

    def _landed(self, key, c, iid, now):
        """A candidate came into the bags or was put on: its try (a kill LOOT_S before) got it, it is owned (never
        chased again), a goal it was is reached. -> the "got" event."""
        for w in self.waiting:
            if w["key"] == key and iid in w["try"]["items"] and now - w["t"] <= LOOT_S:
                w["try"]["got"] = True
                if iid not in w["try"]["seen"]:
                    w["try"]["seen"].append(iid)
        c["owned"] = ([i for i in c["owned"] if i != iid] + [iid])[-OWNED_KEEP:]
        goal = c["goal"] == iid and c["pursued"]
        if c["goal"] == iid:
            c["goal"], c["pursued"] = None, False
        self._dirty = True
        name = self._name(iid)
        log("pursuit", "%s: %s landed" % (key, name))
        return {"kind": "got", "char": key, "item": iid, "name": name, "tries": self._tries(c, iid), "goal": goal}

    def _zone(self, key, c, v, zone, now, ghost=False):
        """A zone change: leaving a run's instance, the run waits RUN_GRACE_S outside (while the player is dead or a
        ghost, until they are alive again) and goes on when they come back in (a wipe's corpse run, a hearth out to
        repair and back: one run); past that it ends, its minutes (zone in to the last zone out) kept for its act when
        RUN_MIN_S or longer. Entering a move's instance starts a run (and a "zone" event: what drops there)."""
        away = v.get("away")                        # [the run, when it left, when its grace started]
        if away is not None and ghost:
            away[2] = now                           # (the corpse run: the grace starts once alive again)
        elif away is not None and now - away[2] > RUN_GRACE_S and zone != away[0][2]:
            self._run_done(key, c, away[0], away[1])
            v["away"] = away = None
        if zone == v["zone"]:
            return
        if v["run"] is not None:
            v["away"] = away = [v["run"], now, now]
        v["run"] = None
        prev, v["zone"] = v["zone"], zone
        if not zone or prev is None:
            return                                  # (the first poll: already inside, the run's start unknown)
        for m in c["moves"]:
            if m.get("kind") in ("inst", "raid") and m.get("zone") == zone:
                if away is not None and away[0][0] == m["act"]:
                    v["run"], v["away"] = away[0], None         # (back in: the same run goes on)
                    return
                if away is not None:
                    self._run_done(key, c, away[0], away[1])    # (another instance: that run is over)
                    v["away"] = None
                v["run"] = (m["act"], now, zone)
                drops = [d for d in m.get("drops") or () if d.get("name")][:2] or [
                    {"item": i, "name": (m.get("names") or {}).get(str(i)) or self._name(i)}
                    for i in (m.get("items") or [])[:2]]
                self._event({"kind": "zone", "char": key, "act": m["act"], "zone": zone,
                             "items": [d["item"] for d in drops], "say": self._drops_line(drops)})
                return

    def _run_done(self, key, c, run, end):
        """(Under the lock) A run (act, start, zone) that ended at `end`: its minutes kept when RUN_MIN_S or longer."""
        if end - run[1] < RUN_MIN_S:
            return
        ms = c["runs"].setdefault(run[0], [])
        ms.append(round((end - run[1]) / 60.0, 1))
        del ms[:-RUNS_KEEP]
        self._dirty = True
        log("pursuit", "%s: a %s run, %.0f minutes" % (key, run[2], (end - run[1]) / 60.0))

    @staticmethod
    def _drops_line(drops):
        parts = []
        for d in drops:
            who = d.get("npc_name")
            parts.append("The %s %s %s." % (d["name"], is_are(d["name"]), possessive(who)) if who else
                         "The %s drop%s here." % (d["name"], "" if plural(d["name"]) else "s"))
        return " ".join(parts)

    def _kill(self, key, c, npc, now):
        """The target died: a try when it drops a candidate (one per KILL_DEDUP_S), watched LOOT_S for its item."""
        hit = (self.npcs.get(key) or {}).get(npc)
        if hit is None:
            return
        if any(t["npc"] == npc and abs(now - t["t"]) <= KILL_DEDUP_S for t in c["tries"]):
            return
        t = {"t": int(now), "act": hit[0], "npc": npc, "items": list(hit[1]), "seen": [], "got": False,
             "looked": False}
        c["tries"].append(t)
        del c["tries"][:-TRIES_KEEP]
        self.waiting.append({"key": key, "t": now, "try": t})
        self._dirty = self._urgent = True

    def _settle(self, now):
        """(Under the lock) Tries watched LOOT_S whose item didn't come: a "roll" event for the item the plan leads
        with (or the goal) when the boss drops it."""
        due = [w for w in self.waiting if now - w["t"] > LOOT_S]
        if not due:
            return
        self.waiting = [w for w in self.waiting if now - w["t"] <= LOOT_S]
        for w in due:
            t = w["try"]
            if t["got"]:
                continue
            c = self._char(w["key"])
            lead = [i for m in c["moves"] for i in m.get("items") or ()]
            item = c["goal"] if c["goal"] in t["items"] else next((i for i in lead if i in t["items"]), None)
            if item is None:
                continue
            n = self._tries(c, item)
            p = ((self.chance.get(w["key"]) or {}).get(item) or (None, None))[0]
            rng = self._range(p)
            name = self._name(item)
            unit = UNITS.get(t["act"].split(":")[0], "runs")
            line = self._call("say_tries", n, p, unit=unit) if _finite(p) else None
            if not isinstance(line, str) or not line:
                line = "That's %s %d" % (unit.rstrip("s"), n) + (
                    "; it usually takes %d to %d." % tuple(rng) if rng else ".")
            self._event({"kind": "roll", "char": w["key"], "item": item, "name": name, "tries": n, "range": rng,
                         "say": "No %s. %s" % (name, line)})

    def _event(self, e):
        """(Under the lock) One follow-through event for events(), the EVENTS_KEEP newest kept."""
        self.pending.append(e)
        del self.pending[:-EVENTS_KEEP]

    def _read_recorder(self, key, now):
        """The Recorder's kills (written at a /reload: tries the strip missed, the loot seen: the corpse was looked
        at) and standings (rep samples; a session's rise, its "grind"), newer than read before. Its kills wait (rec_t
        kept) until the plan has said which bosses the candidates drop from."""
        if self.recorder is None:
            return
        kills = self._rec("kills", key)
        facs = self._rec("factions", key)
        with self.lock:
            c = self._char(key)
            known = self.npcs.get(key)
            npcs = known or {}
            newest = c["rec_t"]
            for k in kills if isinstance(kills, list) and known is not None else ():
                t, npc = (k.get("t"), as_int(k.get("npc"))) if isinstance(k, dict) else (None, None)
                if not _finite(t) or npc is None or t <= c["rec_t"]:
                    continue
                newest = max(newest, t)
                hit = npcs.get(npc)
                if hit is None:
                    continue
                loot = set(_ids(k.get("loot")))
                same = next((x for x in c["tries"] if x["npc"] == npc and abs(t - x["t"]) <= KILL_DEDUP_S), None)
                if same is not None:
                    same["seen"] = sorted(set(same["seen"]) | (loot & set(same["items"])))
                    same["looked"] = True
                else:
                    c["tries"].append({"t": int(t), "act": hit[0], "npc": npc, "items": list(hit[1]),
                                       "seen": sorted(loot & set(hit[1])), "got": False, "looked": True})
                self._dirty = True
            if newest != c["rec_t"]:
                c["tries"].sort(key=lambda x: x["t"])
                del c["tries"][:-TRIES_KEEP]
                c["rec_t"], self._dirty = newest, True
            for fid, f in (facs if isinstance(facs, dict) else {}).items():
                if not isinstance(f, dict) or not _finite(f.get("t")) or not _finite(f.get("value")):
                    continue
                xs = c["rep"].setdefault(str(fid), [])
                if not xs or xs[-1][0] != f["t"]:
                    xs.append([f["t"], f["value"]])
                    del xs[:-REP_KEEP]
                    self._dirty = True
                # a session's rise (the Recorder's v0 and t0 at its first rise, t1 its last): one stretch of the grind
                t0, t1, v0 = f.get("t0"), f.get("t1"), f.get("v0")
                if _finite(t0) and _finite(t1) and _finite(v0) and t1 > t0 and f["value"] > v0:
                    gs = c["grind"].setdefault(str(fid), [])
                    g = [float(t0), float(t1), float(f["value"] - v0)]
                    same = next((x for x in gs if x[0] == g[0]), None)
                    if same != g:
                        if same is not None:
                            gs.remove(same)             # (the same session read again, further along)
                        gs.append(g)
                        gs.sort()
                        del gs[:-REP_KEEP]
                        self._dirty = True

    def _want_numbers(self, key, plan):
        """The numbers the top moves lack, looked up on the web (research.enqueue_web, why "pursued"), once per
        session each."""
        fn = getattr(self.research, "enqueue_web", None)
        if not callable(fn):
            return
        for m in plan["moves"][:WEB_TOP]:
            for job in m.get("jobs") or ():
                if job in self.asked:
                    continue
                self.asked.add(job)
                try:
                    fn(job, "pursued", PRIO_WEB)
                except Exception as e:  # noqa: BLE001 - not looked up
                    log_once("pursuit.enqueue_web", e)

    # ---- the plan -----------------------------------------------------------------------------------------------
    def _key(self, char):
        if isinstance(char, dict):
            return char_key(char)
        return str(char) if char else self.current

    def _cb(self, key):
        try:
            cb = self.bis_fn(key) if key else None
        except Exception as e:  # noqa: BLE001 - no ranks: no plan
            log_once("pursuit.bis", e)
            return None
        return cb if isinstance(cb, dict) and isinstance(cb.get("slots"), dict) else None

    def plan(self, char=None, fresh=False):
        """The character's plan (a character dict, its key, or None: the one playing): {"char", "goal", "pursued",
        "tag", "moves": [Move], "alts": [Move, Move]} (the module's steps 1 to 6); moves [] while it has no BiS ranks
        or nothing to chase. Worked out at most every PLAN_KEEP_S (the rules ask every few seconds): the poll, the
        player's word and fresh work it out now. Nothing while pursuit.enabled is off."""
        key = self._key(char)
        if not key or not self._on():
            return dict(_EMPTY)
        now = self._now()
        with self.lock:
            hit = self._plans.get(key)
            if not fresh and hit is not None and 0 <= now - hit[0] < PLAN_KEEP_S:
                return json.loads(json.dumps(hit[1]))
            c = json.loads(json.dumps(self.data["chars"].get(key) or self._blank()))
        cb = self._cb(key)
        out = dict(_EMPTY, char=key, goal=c["goal"] if c["pursued"] else None, pursued=c["pursued"])
        if cb is not None:
            try:
                out.update(self._build(key, cb, c))
            except Exception as e:  # noqa: BLE001 - no plan this time
                log_once("pursuit.plan", e)
                self.status = {"state": "error", "error": str(e)[:300]}
        with self.lock:
            self._plans[key] = (now, json.loads(json.dumps(out)))
        return out

    def next_move(self, char=None):
        """The first move of the plan the last poll (or the player's last word) worked out, or None: cheap, for the
        rules on the strip's thread (plan() works it out now)."""
        key = self._key(char)
        with self.lock:
            moves = (self.data["chars"].get(key or "") or {}).get("moves") or []
            return json.loads(json.dumps(moves[0])) if moves else None

    def last_plan(self, char=None):
        """The plan the last poll worked out, as kept ({"char", "goal", "pursued", "tag", "moves" (the MOVES_KEEP
        first), "alts"}): cheap, like next_move."""
        key = self._key(char)
        with self.lock:
            c = json.loads(json.dumps(self.data["chars"].get(key or "") or self._blank()))
        return dict(_EMPTY, char=key, goal=c["goal"], pursued=c["pursued"], tag=c["tag"], moves=c["moves"],
                    alts=c["moves"][1:1 + ALTS])

    def _context(self, key, cb, c):
        """What the plan is worked out from: side, skills, money, what the character has, where it is, its quests."""
        ctx = {"key": key, "name": key.split("-")[0], "faction": cb.get("faction"), "class": cb.get("class"),
               "money": c["gold"][-1][1] if c["gold"] else None, "map": None, "x": None, "y": None, "zone": None,
               "quests": {}, "now": self._now()}
        have = {x["id"] for xs in cb["slots"].values() for x in xs if x.get("how") == "worn" or x.get("bag")}
        try:
            st = self.state_fn()
            s = (st.summary() if st is not None else None) or {}
        except Exception as e:  # noqa: BLE001
            log_once("pursuit.state", e)
            st, s = None, {}
        if s.get("name") and char_key(s) == key:
            gear = getattr(st, "gear", None) or {}
            have |= {as_int(it.get("id")) for k in ("W", "I") for it in gear.get(k) or ()} - {None}
            ctx.update(money=as_int(s.get("money"), bool_ok=False), map=as_int(s.get("map")), x=s.get("x"),
                       y=s.get("y"), zone=s.get("zone"), quests=getattr(st, "quests", None) or {},
                       faction=ctx["faction"] or s.get("faction"))
        ctx["have"] = have | set(c["owned"])
        sk = self._rec("skills", key)
        if sk is None and isinstance(cb.get("skills"), list):
            sk = cb["skills"]
        ctx["skills"] = {str(x) for x in sk} if isinstance(sk, (set, frozenset, list, tuple)) else None
        facs = self._rec("factions", key)
        ctx["factions"] = facs if isinstance(facs, dict) else {}
        return ctx

    def _candidates(self, cb, ctx, c, goal):
        """Step 1: {item id: {"id", "group", "d", "s", "sfx", "name"}} (an item ranked with two suffixes: the better);
        and the bag items' gains per group."""
        out: dict = {}
        bags: dict[str, list[float]] = {}
        skipped = set(c["skipped"])
        for g, xs in cb["slots"].items():
            for x in xs:
                iid = as_int(x.get("id"))
                if iid is None or not _finite(x.get("d")):
                    continue
                d, s = float(x["d"]), float(x.get("s") or 0)
                if (x.get("bag") or iid in ctx["have"]) and x.get("how") != "worn":
                    bags.setdefault(g, []).append(d)    # (what just landed counts before the ranks are redone)
                if x.get("how") == "worn" or x.get("bag") or iid in ctx["have"] or iid in skipped:
                    continue
                if x.get("avail") != "now" or not x.get("rank"):
                    continue
                if x.get("req") and (ctx["skills"] is None or x["req"] not in ctx["skills"]):
                    continue
                if iid != goal and not d > 2 * s:
                    continue
                if iid in out and out[iid]["d"] >= d:
                    continue
                out[iid] = {"id": iid, "group": g, "d": d, "s": s, "sfx": as_int(x.get("sfx")) or 0,
                            "name": x.get("name") if x.get("sfx") and isinstance(x.get("name"), str) else None}
        ranked = {as_int(x.get("id")) for xs in cb["slots"].values() for x in xs}
        if goal and goal not in out and goal not in ctx["have"] and goal not in ranked:
            out[goal] = {"id": goal, "group": "goal", "d": 0.0, "s": 0.0, "sfx": 0, "name": None}
        return out, bags                            # (a goal the ranks hold that can't be had now: not a move)

    def _build(self, key, cb, c):
        goal = c["goal"] if c["pursued"] else None
        ctx = self._context(key, cb, c)
        cands, bags = self._candidates(cb, ctx, c, goal)
        opts = {iid: self._options(cand, ctx, c) for iid, cand in cands.items()}
        # the bosses the candidates drop from (tries) and the lead chance of each (rolls, ledger)
        npcs, chance = {}, {}
        for iid, os_ in opts.items():
            for o in os_:
                if o["kind"] not in ("inst", "raid", "mob"):
                    continue
                if o.get("npc") is not None:
                    items = npcs.setdefault(o["npc"], (o["act"], []))[1]
                    if iid not in items:
                        items.append(iid)
                if o["p"] > 0 and (iid not in chance or o["p"] > chance[iid][0]):
                    chance[iid] = (o["p"], o.get("q_src"), o.get("win", 1.0), o.get("share", 1.0))
        with self.lock:
            self.npcs[key], self.chance[key] = npcs, chance
        # step 5: each item's expected hours, then its gain over what is quicker (an item with a known way to get it
        # is weighed against the others' known ways only: a guessed chance never makes a known item look slower)
        eh, ehk = {}, {}
        for iid, os_ in opts.items():
            eh[iid] = min((max(o["hours"], MIN_HOURS) / o["p"] for o in os_ if o["hours"] is not None and o["p"] > 0),
                          default=math.inf)
            ehk[iid] = min((max(o["hours"], MIN_HOURS) / o["p"] for o in os_
                            if o["hours"] is not None and o["p"] > 0 and not o.get("guess")), default=math.inf)
        dp, over = {}, {}
        for iid, cand in cands.items():
            g = cand["group"]
            quick = ehk if ehk[iid] < math.inf else eh
            pool = sorted([(d, None) for d in bags.get(g, ())] + [
                (other["d"], jid) for jid, other in cands.items()
                if jid != iid and other["group"] == g and quick[jid] < quick[iid]], key=lambda b: -b[0])
            k = 2 if g in TWO_SLOTS else 1          # (a ring has to beat the second best quicker one: both are worn)
            base, by = pool[k - 1] if len(pool) >= k and pool[k - 1][0] > 0 else (0.0, None)
            dp[iid] = cand["d"] if iid == goal else cand["d"] - base
            over[iid] = by
        # step 6: the activities
        acts = {}
        for iid, os_ in opts.items():
            if dp[iid] <= 0 and iid != goal:
                continue
            for o in os_:
                a = acts.setdefault(o["act"], {"act": o["act"], "kind": o["kind"], "opts": {}, "first": o})
                if iid not in a["opts"] or o["p"] > a["opts"][iid]["p"]:
                    a["opts"][iid] = o
        for a in acts.values():
            best = {}
            for iid, o in a["opts"].items():
                v = o["p"] * max(dp[iid], 0.0)
                g = cands[iid]["group"]
                if g not in best or v > best[g][0] or (v == best[g][0] and iid == goal):
                    best[g] = (v, iid)
            picks = sorted(best.values(), key=lambda b: (-b[0], b[1]))
            if a["kind"] == "quest":
                picks = picks[:1]
            a["items"] = [i for _, i in picks]
            a["lead"] = a["items"][0]
            a["gain"] = sum(v for v, _ in picks)
            a["dmax"] = max(dp[i] for i in a["items"])
            # (what a known act gains for sure: its items whose chance there isn't the guess)
            a["dknown"] = max((dp[i] for i in a["items"] if not a["opts"][i].get("guess")), default=0.0)
            lo = a["opts"][a["lead"]]
            a["guess"] = bool(lo.get("guess"))
            a["hours"], a["h_src"] = self._act_hours(a, ctx)
            # (a try that takes no time, a quest ready to turn in: the quickest there is, not one without hours)
            a["value"] = a["gain"] / max(a["hours"], MIN_HOURS) if a["hours"] is not None and a["gain"] > 0 \
                else None
        known = [a for a in acts.values() if a["value"] is not None and not a["guess"]]
        for a in acts.values():
            if a["value"] is not None and a["guess"]:
                caps = [k["value"] for k in known if k["dknown"] >= a["dmax"]]
                if caps and a["value"] >= min(caps):
                    a["value"] = min(caps) * 0.999
        ranked = sorted((a for a in acts.values() if a["value"]), key=lambda a: (-a["value"], a["act"]))
        rest = sorted((a for a in acts.values() if not a["value"]), key=lambda a: (-a["dmax"], a["act"]))
        order = ranked + rest
        if goal is not None:
            mine = sorted((a for a in order if goal in a["opts"]),
                          key=lambda a: (max(a["hours"], MIN_HOURS) / a["opts"][goal]["p"]
                                         if a["hours"] is not None and a["opts"][goal]["p"] else math.inf, a["act"]))
            order = mine + [a for a in order if goal not in a["opts"]]
            for a in mine:                          # (the goal leads its moves)
                a["items"] = [goal] + [i for i in a["items"] if i != goal]
                a["lead"] = goal
        moves = [self._move(a, cands, dp, over, ctx, c) for a in order]
        pick = goal if goal is not None else (moves[0]["items"][0] if moves else None)
        # (the tag changes with the move, not with the order of what it rolls: a kill's update reorders them)
        tag = "%s|%s|%s" % (goal or "", moves[0]["act"] if moves else "",
                            ",".join(str(i) for i in sorted(moves[0]["items"])) if moves else "")
        return {"goal": pick, "tag": tag, "moves": moves, "alts": moves[1:1 + ALTS]}

    def _act_hours(self, a, ctx):
        """The hours a try of the activity takes with the items it is chosen for: a vendor's or the auction house's
        prices added up, a reputation's furthest standing; else its lead option's."""
        lo = a["opts"][a["lead"]]
        if a["kind"] in ("vendor", "ah") and len(a["items"]) > 1:
            prices = [a["opts"][i].get("price") for i in a["items"]]
            if all(p is not None for p in prices):
                kw = {"price": sum(prices), "money": ctx["money"] or 0, "gph": lo.get("gph"),
                      "gph_src": lo.get("gph_src"), "travel_min": lo.get("travel")}
                if a["kind"] == "vendor":           # (a price the web gave makes the sum the web's)
                    kw["price_src"] = "web" if any(a["opts"][i].get("price_src") == "web" for i in a["items"]) \
                        else "obs"
                return self._try_hours(a["kind"], **kw)
        if a["kind"] == "rep":
            hs = [a["opts"][i]["hours"] for i in a["items"]]
            if all(h is not None for h in hs):
                return max(hs), lo.get("h_src")
            return None, lo.get("h_src")
        return lo["hours"], lo.get("h_src")

    # ---- the ways to get an item --------------------------------------------------------------------------------
    def _sources(self, iid, faction):
        """(sources, bond, vendor price in copper) of an item for this side: research's fact, else WoWSims'."""
        fn = getattr(self.research, "facts", None)
        f = None
        if callable(fn):
            try:
                f = fn(iid)
            except Exception as e:  # noqa: BLE001
                log_once("pursuit.facts", e)
        bond, price = None, None
        if isinstance(f, dict) and not (f.get("fa") and faction and f["fa"] != faction):
            srcs = [s for s in f.get("s") or () if isinstance(s, dict) and s.get("kind")]
            if self._web_gone(f):
                srcs = [s for s in srcs if s.get("src") != "web"]     # (what the web said, past its ttl)
            bond, price = f.get("bond"), as_int(f.get("price"))
            if srcs or bond == BOE:
                return srcs, bond, price
        db = self._db()
        try:
            srcs = db.sources(iid, faction) if db is not None else []
        except Exception as e:  # noqa: BLE001
            log_once("pursuit.sources", e)
            srcs = []
        return srcs, bond, price

    def _web_gone(self, f):
        """Whether a fact holds what the web said and is past its ttl (research's own rule: it isn't said then)."""
        fn = getattr(self.research, "_expired_web", None)
        try:
            return bool(fn(f, self._now())) if callable(fn) else False
        except Exception as e:  # noqa: BLE001 - kept then
            log_once("pursuit.expired_web", e)
            return False

    def _options(self, cand, ctx, c):
        """Every way to get the candidate: [Option] ({"act", "kind", "p", "hours", "h_src", "where", "place", "zone",
        "npc", "npc_name", "odds", "note", "jobs", ...})."""
        iid = cand["id"]
        srcs, bond, price = self._sources(iid, ctx["faction"])
        out, seen = [], set()
        for s in srcs:
            k = s.get("kind")
            o = None
            if k == "drop":
                o = self._drop(cand, s, ctx, c, bond)
            elif k == "quest":
                o = self._quest(cand, s, ctx)
            elif k == "vendor":
                o = self._vendor(cand, s, price, ctx, c)
            elif k == "rep":
                o = self._rep(cand, s, price, ctx, c)
            elif k == "crafted":
                o = self._craft(cand, s, ctx)
            elif k == "pvp":
                o = self._pvp(cand)
            if o is not None and o["act"] not in seen:
                seen.add(o["act"])
                out.append(o)
        if bond == BOE or any(o["kind"] == "craft" for o in out) or self._auction(iid, ctx["key"]):
            o = self._ah(cand, bond, ctx, c, ctx["key"])
            if o is not None and o["act"] not in seen:
                out.append(o)
        # (a boss the knowledge base knows without its instance, beside WoWSims' drop there: the instance's only)
        bosses = {o["npc"] for o in out if o["kind"] in ("inst", "raid") and o["npc"] is not None}
        return [o for o in out if not (o["kind"] == "mob" and o["npc"] in bosses)]

    def _option(self, act, kind, **kw):
        o = {"act": act, "kind": kind, "p": 1.0, "q": 1.0, "q_src": None, "hours": None, "h_src": None,
             "where": None, "place": None, "zone": None, "npc": None, "npc_name": None, "odds": None, "note": None,
             "words": None, "jobs": [], "guess": False, "travel": None}
        o.update(kw)
        return o

    def _travel(self, ctx, where):
        if self.travel_fn is not None:
            try:
                here = _where(ctx["map"], ctx["x"], ctx["y"])
                m = self.travel_fn(here, where)
                if _finite(m):
                    return float(m)
            except Exception as e:  # noqa: BLE001 - the guess then
                log_once("pursuit.travel", e)
        return float(NEAR_MIN if where and ctx["map"] == where["map"] else TRAVEL_MIN)

    def _spawn(self, npc):
        """Where a creature stands (the knowledge base's first spawn), or None."""
        pack = self._pack()
        if pack is None or npc is None:
            return None
        try:
            for um, x, y in pack.data.spawns_of(npc) or ():
                w = _where(um, x, y)
                if w:
                    return w
        except Exception as e:  # noqa: BLE001
            log_once("pursuit.spawn", e)
        return None

    def _entrance(self, zone, name):
        """An instance's entrance: entrance_fn's, else the knowledge pack's instance_entrance (by its zone id, then
        its name), or None; kept WHERE_KEEP_S."""
        k, now = (zone, name), self._now()
        hit = self._wheres.get(k)
        if hit is not None and now - hit[0] < WHERE_KEEP_S:
            return hit[1]
        w = self._entrance_now(zone, name)
        if len(self._wheres) > 256:
            self._wheres.clear()
        self._wheres[k] = (now, w)
        return w

    def _entrance_now(self, zone, name):
        fn = self.entrance_fn
        try:
            if fn is not None:
                e = fn(zone, name)
            else:
                pack = self._pack()
                look = getattr(pack, "instance_entrance", None)
                e = None
                if callable(look):
                    e = (look(zone) if zone else None) or (look(name) if name else None)
        except Exception as e2:  # noqa: BLE001 - no pin
            log_once("pursuit.entrance", e2)
            return None
        return _where(e.get("map"), e.get("x"), e.get("y")) if isinstance(e, dict) else None

    def _prior(self, npc, npc_name, iid):
        """AtlasLoot classic's drop chance of the item from this boss (0..1), or None."""
        rates = self._atlasloot()
        if not rates:
            return None
        per = None
        for k in (npc, str(npc) if npc is not None else None, npc_name):
            if k is not None and isinstance(rates.get(k), dict):
                per = rates[k]
                break
        r = (per or {}).get(iid, (per or {}).get(str(iid)))
        if not _finite(r) or r <= 0:
            return None
        return r / 100.0 if r > 1 else float(r)

    def _web(self, job):
        fn = getattr(self.research, "web_facts", None)
        try:
            w = fn(job) if callable(fn) else None
        except Exception as e:  # noqa: BLE001
            log_once("pursuit.web_facts", e)
            return None
        return w if isinstance(w, dict) else None

    def _web_rate(self, npc, iid, s):
        if _finite(s.get("rate")):
            return float(s["rate"])
        if npc is None:
            return None
        for x in (self._web("r:%d:%d" % (npc, iid)) or {}).get("s") or ():
            if _finite(x.get("rate")):
                return float(x["rate"])
        return None

    def _obs(self, c, npc, iid):
        """The player's own kills of the boss while the item was looked for, and how many showed it: only the kills
        whose loot was seen (the corpse looted: the Recorder's loot window) or that brought the item (it landed). A
        kill that ended in a group's roll the player lost, its corpse never opened, says nothing about the drop (and
        the roll is counted apart: win_chance); it is still a run (_tries)."""
        if npc is None:
            return None
        ts = [t for t in c["tries"] if t["npc"] == npc and iid in t["items"] and (t.get("looked") or iid in t["seen"])]
        if not ts:
            return None
        return {"kills": len(ts), "got": sum(1 for t in ts if iid in t["seen"])}

    def _share(self, cand, ctx):
        """Of an item's random suffixes, the share worth at least the one it was ranked with (1 for none)."""
        sfx = cand.get("sfx") or 0
        db = self._db()
        rec = db.item(cand["id"]) if db is not None and sfx else None
        options = [o for o in (rec or {}).get("randomSuffixOptions") or () if isinstance(o, int)]
        if len(options) < 2:
            return 1.0
        w = None
        if self.weights_fn is not None:
            try:
                w = self.weights_fn(ctx["name"])
            except Exception as e:  # noqa: BLE001
                log_once("pursuit.weights", e)
        if not isinstance(w, dict) or not w or db is None:
            return 1.0 / len(options)

        def ep(o):
            return sum(float(v) * float(w.get(k) or 0) for k, v in (db.suffix_stats(cand["id"], o) or {}).items())
        mine = ep(abs(sfx))
        return max(1, sum(1 for o in options if ep(o) >= mine - 1e-9)) / float(len(options))

    def _win(self, iid, ctx, size=None, bond=None):
        """effort.win_chance for the item (WoWSims' record, with how it binds when known), the pursuit.group setting
        and a 10-player group's size; 1 when it can't say."""
        db = self._db()
        rec = dict((db.item(iid) if db is not None else None) or {"id": iid})
        if bond is not None:
            rec["bond"] = bond
        group = self._opt("group") or "pug"
        w = self._call("win_chance", rec, group=group, size=size) if size else None
        if w is None:
            w = self._call("win_chance", rec, group=group)
        return float(w) if _finite(w) and 0 < w <= 1 else 1.0

    def _odds(self, p, src, unit="runs"):
        """effort.say_odds' clause for a chance a try ("about 1 in 8, usually 3 to 10 runs; that's classic's
        chance"), or None."""
        o = self._call("say_odds", p, src, unit=unit)
        if not isinstance(o, str):
            o = self._call("say_odds", p, src)
        return o if isinstance(o, str) and o else None

    @staticmethod
    def _with_rolls(odds, win, share):
        """The odds clause of a chance a try that counts the group's rolls (win) or the random suffix's share: "about 1
        in 16 with the rolls, usually 5 to 21 runs; that's classic's chance" (the source names the drop chance it
        started from); as it was when both are 1."""
        what = [w for w, x in (("the rolls", win), ("the right suffix", share)) if _finite(x) and x < 0.995]
        if not isinstance(odds, str) or not what:
            return odds
        return re.sub(r"^(about [^,;]+)", lambda m: "%s with %s" % (m.group(1), " and ".join(what)), odds, count=1)

    def _words(self, kind, src, hours, **kw):
        """effort.say_effort's sentence for what a try takes (its gates: "I don't know its price yet.", the raid
        calendar, a rep rate not learned), or None (an effort.py without it: the planner's own words then)."""
        o = self._call("say_effort", kind, src, hours, **kw)
        return o if isinstance(o, str) and o else None

    def _range(self, p):
        """[p25, p75] tries for a chance a try (effort.quantiles), or None."""
        if not _finite(p) or not 0 < p <= 1:
            return None
        r = self._call("quantiles", p)
        if isinstance(r, (tuple, list)) and len(r) >= 2 and _finite(r[0]) and _finite(r[1]):
            return [int(round(r[0])), int(round(r[1]))]
        return None

    def _drop(self, cand, s, ctx, c, bond=None):
        iid, npc = cand["id"], as_int(s.get("npc"))
        diff, zone = as_int(s.get("difficulty")) or 0, as_int(s.get("zone"))
        inst, zname, who = s.get("instance"), s.get("zone_name"), s.get("npc_name")
        place = inst or zname
        where = None
        if diff == RAID or diff == DUNGEON:
            kind = "raid" if diff == RAID else "inst"
            act = "%s:%s" % (kind, zone if zone else place)
            if zone == BRS_ZONE and isinstance(inst, str) and inst.endswith(" Blackrock Spire"):
                act += ":" + inst.split()[0]
            where = self._entrance(zone, zname or inst)
        elif not diff and npc is None and zname and self._entrance(None, zname):
            kind, act = "inst", "inst:%s" % zname           # (a web fact: a place that has an entrance)
            where = self._entrance(None, zname)
        elif npc is not None:
            kind, act = "mob", "mob:%d" % npc
            where = self._spawn(npc)
        else:
            return None                                       # (a drop known by its place only: nothing to farm)
        jobs = []
        r = self._call("drop_chance", npc, iid, obs=self._obs(c, npc, iid), prior=self._prior(npc, who, iid),
                       web=self._web_rate(npc, iid, s))
        q0, src = (r[0], r[2]) if isinstance(r, (tuple, list)) and len(r) == 3 else (None, "unknown")
        guess = not (_finite(q0) and 0 < q0 <= 1) or str(src).split("+")[0] == "guess"
        q = float(q0) if _finite(q0) and 0 < q0 <= 1 else GUESS_Q
        if not (_finite(q0) and 0 < q0 <= 1):
            src = "guess"
        if guess and npc is not None:
            jobs.append("r:%d:%d" % (npc, iid))
        big = 10 if place == UBRS or kind == "raid" else None
        win, share = self._win(iid, ctx, big, bond), self._share(cand, ctx)
        p = q * win * share
        travel = self._travel(ctx, where)
        hist = [{"min": m} for m in c["runs"].get(act) or ()]
        form = self._opt("form_minutes")
        words = None
        if kind == "mob":
            hours, h_src = (MOB_MIN + travel) / 60.0, "prior"
        elif kind == "raid":
            hours, h_src = self._try_hours("raid", now=ctx["now"], instance=place, history=hist, travel_min=travel,
                                           locked_until=self._locked_until(c, act))
            words = self._words("raid", h_src, hours) or (RAID_LOCKED if h_src == "lockout" else None)
        else:
            m = self._call("run_minutes", place, history=hist, prior_s=self._run_prior())
            run_min, run_src = (float(m[0]), str(m[1])) if isinstance(m, (tuple, list)) and len(m) == 2 \
                and _finite(m[0]) else (None, "prior")
            hours, h_src = self._try_hours("inst", src=run_src, instance=place, history=hist,
                                           prior_s=self._run_prior(), run_min=run_min, run_src=run_src,
                                           form_min=form, group=self._opt("group"), travel_min=travel)
            words = self._words("inst", h_src, hours, form_min=form if self._opt("group") != "solo" else None)
        if kind == "raid" and hours is None:
            odds = words or "Raid gear: %s." % RAID_WAIT
        else:
            odds = self._with_rolls(self._odds(p, src, UNITS[kind]), win, share)
        return self._option(act, kind, p=p, q=q, q_src=src, win=win, share=share, guess=guess, hours=hours,
                            h_src=h_src, where=where, place=place, zone=zname, npc=npc, npc_name=who, odds=odds,
                            jobs=jobs, travel=travel, words=words)

    @staticmethod
    def _locked_until(c, act):
        """When the raid's lockout, used by the newest kill of one of its bosses the plan followed, ends: the weekly
        reset after that kill (RAID_RESET_WDAY, RAID_RESET_HOUR UTC), a week after it at the latest; None for no
        kill."""
        last = max((t["t"] for t in c["tries"] if t["act"] == act), default=None)
        if last is None:
            return None
        day = math.floor(last / 86400.0)
        weekday = (day + 3) % 7                     # (1970-01-01 was a Thursday: 3 with Monday 0)
        reset = (day + (RAID_RESET_WDAY - weekday) % 7) * 86400.0 + RAID_RESET_HOUR * 3600.0
        if reset <= last:
            reset += 7 * 86400.0
        return reset

    def _chain(self, qid, ctx):
        """The quests of its chain still to do, ending with it (the knowledge base's prerequisites; the ones before a
        quest in the log count as done), whether that one is done (in the log, complete: turn it in), and the giver
        row of where to go (or None): the first one's, or for a done quest the one it is turned in to."""
        pack = self._pack()
        chain = [qid]
        done = bool((ctx["quests"].get(qid) or ctx["quests"].get(str(qid)) or {}).get("done"))
        if pack is None:
            return chain, done, None
        try:
            data = pack.data
            seen, cur = {qid}, qid
            while len(chain) < 12:
                pre = [p for p, kind in (data.prereqs.get(cur) or ()) if kind in ("pre", "pregroup") and p not in seen]
                if not pre:
                    break
                cur = pre[0]
                seen.add(cur)
                chain.insert(0, cur)
            log_ids = {as_int(k) for k in ctx["quests"]}
            for i, q in enumerate(chain):
                if q in log_ids:
                    chain = chain[i:]
                    break
            if done:
                return [qid], done, data.giver(qid, "end")
            return chain, done, data.giver(chain[0], "start")
        except Exception as e:  # noqa: BLE001 - the quest alone
            log_once("pursuit.chain", e)
            return [qid], done, None

    def _quest(self, cand, s, ctx):
        qid = as_int(s.get("quest"))
        if qid is None:
            return None
        chain, done, giver = self._chain(qid, ctx)
        where = _where(giver.get("ui_map"), giver.get("x"), giver.get("y")) if isinstance(giver, dict) else None
        pack, place, who, by_web = self._pack(), None, None, False
        if isinstance(giver, dict):
            who = giver.get("name")
            try:
                if pack is not None and giver.get("ui_map") is not None:
                    place = pack.map_name(giver["ui_map"])
                if not who and pack is not None and giver.get("kind") == "npc":
                    who = (pack.data.creature(giver.get("entity_id")) or {}).get("name")
            except Exception as e:  # noqa: BLE001
                log_once("pursuit.quest", e)
        elif not done:                              # (where the chain starts, as the web said: research's "q:" job)
            for x in (self._web("q:%d" % chain[0]) or {}).get("s") or ():
                if isinstance(x, dict) and x.get("npc_name"):
                    who, place, by_web = x["npc_name"], x.get("zone_name"), True
                    break
        jobs = [] if giver or by_web or done else ["q:%d" % chain[0]]
        travel = self._travel(ctx, where)
        hours, h_src = self._try_hours("quest", steps=len(chain), done=done, travel_min=travel)
        words = "The quest is ready to turn in." if done else self._words("quest", h_src, hours)
        return self._option("quest:%d" % qid, "quest", hours=hours, h_src=h_src, where=where, place=place,
                            npc_name=who, quest=qid, quest_name=s.get("quest_name"), steps=len(chain), jobs=jobs,
                            travel=travel, words=words, giver_web=by_web, done=done)

    def _gph(self, c):
        r = self._call("gold_per_hour", [tuple(x) for x in c["gold"]])
        return (float(r[0]), str(r[1])) if isinstance(r, (tuple, list)) and len(r) == 2 and _finite(r[0]) \
            and r[0] > 0 else (None, "unknown")

    def _vendor(self, cand, s, price, ctx, c, standing=None):
        iid, npc = cand["id"], as_int(s.get("npc"))
        who, place = s.get("npc_name"), s.get("zone_name")
        jobs, cost, psrc = [], None, "obs"
        if price is None and npc is not None:
            for x in (self._web("v:%d:%d" % (npc, iid)) or {}).get("s") or ():
                if x.get("cost"):
                    cost, psrc = str(x["cost"]), "web"
                    price = parse_gold(cost)
                    break
            if cost is None:
                jobs.append("v:%d:%d" % (npc, iid))
        where = self._spawn(npc)
        travel = self._travel(ctx, where)
        gph, gsrc = self._gph(c)
        money = ctx["money"] or 0
        hours, h_src = self._try_hours("vendor", price=price, price_src=psrc, money=money, gph=gph, gph_src=gsrc,
                                       travel_min=travel)
        words = self._words("vendor", h_src, hours, price=price, price_src=psrc, gold=money, travel_min=travel,
                            gph_src=gsrc)
        if words is None:
            words = "It costs %s%s." % (gold_text(price), " (web, unconfirmed)" if cost else "") if price is not None \
                else "I don't know its price yet."
        if cost and price is None:
            words = "The web says it costs %s (unconfirmed)." % cost
        if standing:
            words = "You're %s already. %s" % (standing, words)
        act = "vendor:%d" % npc if npc is not None else "vendor:%s" % (who or place or iid)
        return self._option(act, "vendor", hours=hours, h_src=h_src, where=where, place=place, zone=place, npc=npc,
                            npc_name=who, note=words, odds=_clause(words), price=price, price_src=psrc, gph=gph,
                            gph_src=gsrc, jobs=jobs, travel=travel)

    def _faction_id(self, name):
        db = self._db()
        for fid, n in (getattr(db, "factions", None) or {}).items():
            if isinstance(n, str) and name and n.lower() == str(name).lower():
                return fid
        return None

    def _rep(self, cand, s, price, ctx, c):
        faction, level = s.get("rep_faction"), s.get("rep_level")
        fid = self._faction_id(faction)
        f = ctx["factions"].get(fid) or ctx["factions"].get(str(fid)) if fid is not None else None
        f = f if isinstance(f, dict) else None
        if level in REP_STANDING and f and as_int(f.get("standing"), 0) >= REP_STANDING[level]:
            if s.get("npc") is not None or s.get("npc_name"):        # (reached: now it is a vendor's)
                return self._vendor(cand, s, price, ctx, c, standing="%s with %s" % (level, faction_text(faction)))
            return None
        act = "rep:%s" % (fid if fid is not None else faction or "?")
        # (a session's rise, first turn-in to last, when the Recorder kept them; else its readings across reloads)
        grind = [{"gain": g[2], "s": g[1] - g[0]} for g in c["grind"].get(str(fid)) or ()] if fid is not None else []
        samples = grind or ([tuple(x) for x in c["rep"].get(str(fid)) or ()] if fid is not None else [])
        r = self._call("rep_per_hour", samples)
        rate, rsrc = (float(r[0]), str(r[1])) if isinstance(r, (tuple, list)) and len(r) == 2 and _finite(r[0]) \
            and r[0] > 0 else (None, "unknown")
        jobs = ["f:%s" % faction] if rate is None and faction else []
        hours, h_src = self._try_hours("rep", src=rsrc, target=level, value=(f or {}).get("value"),
                                       standing=(f or {}).get("standing"), rate=rate, rate_src=rsrc)
        words = self._words("rep", h_src, hours, faction=faction)
        if words is None:
            bare = str(faction or "")[4:] if str(faction or "").startswith("The ") else (faction or "")
            if h_src == "nostanding":
                words = "I don't know your %s standing yet; it shows after a /reload." % (bare or "reputation")
            elif hours is None:
                words = "I don't know %s rep rates yet; I'll learn them from your turn-ins." % (faction or "its")
            else:
                words = "About %s of %s rep, as of your last reload." % (hours_text(hours), bare or "that")
        return self._option(act, "rep", hours=hours, h_src=h_src, place=s.get("zone_name"), npc=as_int(s.get("npc")),
                            npc_name=s.get("npc_name"), note=words, odds=_clause(words), faction=faction, level=level,
                            jobs=jobs)

    def _craft(self, cand, s, ctx):
        prof = s.get("profession")
        has = bool(prof and ctx["skills"] is not None and prof in ctx["skills"])
        hours, h_src = self._try_hours("crafted", profession=prof, have=has, money=ctx["money"] or 0)
        words = self._words("crafted", h_src, hours, profession=prof)
        if words is None:
            words = ("You can craft it; I don't know its reagents yet." if has else
                     "Ask a %s crafter or check the auction house." % prof if prof else
                     "Ask a crafter or check the auction house.")
        return self._option("craft:%d" % cand["id"], "craft", hours=hours, h_src=h_src, note=words,
                            odds=_clause(words), profession=prof)

    def _pvp(self, cand):
        """A PvP reward: not estimated (no hours, after every move that has them), said so."""
        hours, h_src = self._try_hours("pvp")
        words = self._words("pvp", h_src, hours) or "I can't estimate PvP rewards yet."
        return self._option("pvp:%d" % cand["id"], "pvp", hours=None, h_src=h_src, note=words, odds=_clause(words))

    def _ah(self, cand, bond, ctx, c, key=None):
        a = self._auction(cand["id"], key)
        if a is None:
            if bond != BOE:
                return None
            words = "It binds when equipped, so the auction house may have it."
            return self._option("ah:%d" % cand["id"], "ah", note=words, odds=_clause(words),
                                place="the auction house")
        price = int(a["copper"])
        gph, gsrc = self._gph(c)
        travel = self._travel(ctx, None)
        money = ctx["money"] or 0
        hours, h_src = self._try_hours("ah", price=price, money=money, gph=gph, gph_src=gsrc, travel_min=travel)
        seen = a["t"] if _finite(a.get("t")) else None
        words = self._words("ah", h_src, hours, price=price, seen_t=seen, now=ctx["now"], gold=money,
                            travel_min=travel, gph_src=gsrc)
        if words is None:
            words = "The auction house had it for %s%s." % (
                gold_text(price), ", %s" % age_text(ctx["now"] - seen) if seen is not None else "")
        return self._option("ah:%d" % cand["id"], "ah", hours=hours, h_src=h_src, note=words, odds=_clause(words),
                            price=price, gph=gph, gph_src=gsrc, place="the auction house", travel=travel)

    # ---- the words ----------------------------------------------------------------------------------------------
    def _name(self, iid, sfx_name=None):
        """An item's name: the BiS ranks' (with its suffix), WoWSims', research's, or "item <id>"."""
        if sfx_name:
            return sfx_name
        db = self._db()
        try:
            n = db.name(iid) if db is not None else None
        except Exception as e:  # noqa: BLE001
            log_once("pursuit.name", e)
            n = None
        if not n:
            fn = getattr(self.research, "brief", None)
            try:
                b = fn(iid) if callable(fn) else None
                n = b.get("name") if isinstance(b, dict) else None
            except Exception as e:  # noqa: BLE001
                log_once("pursuit.brief", e)
        return n or "item %s" % iid

    def _tries(self, c, iid):
        return sum(1 for t in c["tries"] if iid in t["items"])

    def _move(self, a, cands, dp, over, ctx, c):
        lead = a["lead"]
        lo = a["opts"][lead]
        names = {i: self._name(i, cands[i].get("name")) for i in a["items"]}
        ln = names[lead]
        kind, place, who = a["kind"], lo.get("place"), lo.get("npc_name")
        more = [names[i] for i in a["items"][1:]]
        if kind in ("inst", "raid"):
            what = ln if not more else ("%s, %s" % (ln, more[0]) if len(more) == 1 else "%s and %d more" % (
                ln, len(more)))
            text = "%s: %s" % (place or "An instance", what)
            if kind == "raid" and a["hours"] is None:
                say = lo.get("odds") or "Raid gear: %s." % RAID_WAIT
            else:
                whose = "%s %s" % (is_are(ln), possessive(who)) if who else \
                    "drop%s there" % ("" if plural(ln) else "s")
                say = "Next: %s. The %s %s" % (place or "that instance", ln, whose)
                say += (", %s." % lo["odds"]) if lo.get("odds") else "."
        elif kind == "mob":
            text = "%s%s: %s" % (who or "A creature", ", %s" % place if place else "", ln)
            say = "Next: %s%s drops the %s" % (who or "a creature", " in %s" % place if place else "", ln)
            say += (", %s." % lo["odds"]) if lo.get("odds") else "."
        elif kind == "vendor":
            text = "%s%s: %s" % (who or "A vendor", ", %s" % place if place else "", ln)
            say = "Next: %s%s sells the %s" % (who or "a vendor", " in %s" % place if place else "", ln)
            say += "; %s." % lo["odds"] if lo.get("odds") else "."
        elif kind == "rep":
            fac, lvl = faction_text(lo.get("faction")), lo.get("level")
            text = "%s%s: %s" % ("%s with " % lvl if lvl else "", fac, ln)
            say = "Next: the %s need%s %s with %s" % (ln, "" if plural(ln) else "s", lvl or "reputation", fac)
            say += "; %s." % lo["odds"] if lo.get("odds") else "."
        elif kind == "quest" and lo.get("done"):
            qn = lo.get("quest_name")
            text = "Turn in %s: %s" % (qn or "the quest", ln)
            say = "Next: turn in %s for the %s" % ("the quest %s" % qn if qn else "the quest", ln) + (
                "; %s%s takes it." % (who, " in %s" % place if place else "") if who else ".")
        elif kind == "quest":
            qn = lo.get("quest_name") or "a quest"
            text = "%s: %s" % (qn, ln)
            steps = lo.get("steps") or 1
            say = "Next: the quest %s gives the %s" % (qn, ln) + (
                ", the end of a %d-quest chain" % steps if steps > 1 else "") + (
                "; it starts with %s%s%s." % (who, " in %s" % place if place else "",
                                             " (web, unconfirmed)" if lo.get("giver_web") else "") if who else ".")
        elif kind == "craft":
            text = "%s: %s" % (lo.get("profession") or "Crafted", ln)
            say = "Next: the %s %s made with %s" % (ln, is_are(ln), lo.get("profession") or "a profession")
            say += "; %s." % lo["odds"] if lo.get("odds") else "."
        elif kind == "pvp":
            text = "PvP: %s" % ln
            say = "Next: the %s %s a PvP reward" % (ln, is_are(ln))
            say += "; %s." % lo["odds"] if lo.get("odds") else "."
        else:
            text = "Auction house: %s" % ln
            say = "Next: the auction house for the %s" % ln
            say += "; %s." % lo["odds"] if lo.get("odds") else "."
        p = lo["p"] if kind in ("inst", "raid", "mob") else None
        tries = self._tries(c, lead)
        move = {"act": a["act"], "key": "%s|%s" % (a["act"], ",".join(str(i) for i in sorted(a["items"]))),
                "kind": kind, "items": list(a["items"]),
                "names": {str(i): n for i, n in names.items()}, "value": round(a["value"], 4) if a["value"] else None,
                "hours": round(a["hours"], 2) if a["hours"] is not None else None, "odds": lo.get("odds"),
                "why": self._why(a, cands, dp, over, names), "text": text, "say": say, "where": lo.get("where"),
                "place": place, "zone": lo.get("zone"), "npc": lo.get("npc"), "npc_name": who,
                "p": round(p, 4) if p else None, "tries": tries, "range": self._range(p) if p else None,
                "jobs": sorted({j for o in a["opts"].values() for j in o.get("jobs") or ()})}
        if kind in ("inst", "raid"):
            move["drops"] = [{"item": i, "name": names[i], "npc": a["opts"][i].get("npc"),
                              "npc_name": a["opts"][i].get("npc_name")} for i in a["items"]]
        if kind == "quest" and lo.get("done"):
            move["done"] = True                     # (the quest is complete: npc_name and where are its turn-in)
        return move

    def _why(self, a, cands, dp, over, names):
        """The reasons, said when asked: the gain (and over which quicker item), the chance and where it comes from,
        the time a try takes and its source, the DPS an hour, what else the try rolls."""
        lead = a["lead"]
        lo = a["opts"][lead]
        cand = cands[lead]
        parts = []
        if cand["d"] > 0:
            g = cand["group"]
            gain = "+%.1f DPS for %s" % (cand["d"], g) if g != "goal" else "+%.1f DPS" % cand["d"]
            if over.get(lead) is not None and 0 < dp[lead] <= cand["d"] - 0.05:     # (not "12.8 more" than ~0)
                gain += ", %.1f more than the %s, which is quicker to get" % (
                    dp[lead], self._name(over[lead], cands[over[lead]].get("name")))
            parts.append(gain)
        else:
            parts.append("your goal")
        if lo.get("odds") and not (a["kind"] == "raid" and a["hours"] is None):
            parts.append(lo["odds"])                      # (a vendor's, a rep's, a craft's: what it takes, said)
        if a["kind"] in ("inst", "raid", "mob", "quest"):
            if lo.get("words") and a["hours"] is not None:
                parts.append(_clause(lo["words"]))        # (effort's words for the time: its source named)
            elif a["hours"]:
                parts.append("a try takes about %s (%s)" % (hours_text(a["hours"]), src_words(a.get("h_src"))))
            elif a["kind"] == "raid":
                parts.append(_clause(lo.get("odds")) or "raid gear: %s" % RAID_WAIT)
        if a["value"]:
            v = a["value"]                                # (two figures: "about 92", "about 4.5", "about 0.24")
            parts.append("about %s DPS an hour of play" % ("%d" % round(v) if v >= 99.5 else "%.2g" % v))
        if len(a["items"]) > 1:
            parts.append("it also rolls the %s" % join_names([names[i] for i in a["items"][1:4]]))
        return "; ".join(parts) + "."

    # ---- the player's word --------------------------------------------------------------------------------------
    def _resolve(self, key, item):
        """An item id from an id, its text, or a name (the BiS ranks' names first: "the robe" -> the ranked item whose
        name has those words, the most worth; then research's names)."""
        if isinstance(item, int) and not isinstance(item, bool):
            return item
        q = " ".join(str(item or "").split()).strip(" .?!'\"")
        m = re.fullmatch(r"(?:item\s*)?#?(\d+)", q, re.I)
        if m:
            return int(m.group(1))
        q = re.sub(r"^(?:the|my|that|this)\s+", "", q, flags=re.I).lower()
        if not q:
            return None
        cb = self._cb(key)
        hits = []
        for xs in (cb or {}).get("slots", {}).values():
            for x in xs:
                if x.get("how") == "worn" or not isinstance(x.get("id"), int):
                    continue
                n = (x.get("name") if isinstance(x.get("name"), str) else None) or self._name(x["id"])
                words = set(re.findall(r"[a-z']+", n.lower()))
                if n.lower() == q:
                    return x["id"]
                if set(re.findall(r"[a-z']+", q)) <= words or any(w.rstrip("s") == q.rstrip("s") for w in words):
                    hits.append((-(x.get("d") or 0), x["id"]))
        if hits:
            return min(hits)[1]
        fn = getattr(self.research, "_find", None)
        try:
            return as_int(fn(q)) if callable(fn) else None
        except Exception as e:  # noqa: BLE001
            log_once("pursuit.find", e)
            return None

    def pursue(self, char, item_id):
        """The player's goal ("chase the robe"): an item id, its text or a name; None clears it. Kept across
        restarts; a skipped item is no longer skipped. -> the new plan (nothing kept while pursuit.enabled is off)."""
        key = self._key(char)
        if not key or not self._on():
            return dict(_EMPTY)
        iid = self._resolve(key, item_id) if item_id is not None else None
        if item_id is not None and iid is None:
            return self.plan(key)                   # (an item nobody knows: the plan as it was)
        with self.lock:
            c = self._char(key)
            if iid is None:
                c["goal"], c["pursued"] = None, False
            else:
                c["goal"], c["pursued"] = iid, True
                c["skipped"] = [i for i in c["skipped"] if i != iid]
                c["owned"] = [i for i in c["owned"] if i != iid]
            self._save()
        log("pursuit", "%s: goal %s" % (key, self._name(iid) if iid else "cleared"))
        return self._replan(key)

    def skip(self, char, item_id, skip=True):
        """The player's "skip that": the item (an id, its text or a name; None: the plan's goal now) is never chased
        (kept across restarts); skip=False takes it back. -> the new plan (nothing kept while pursuit.enabled is
        off). A skipped goal of the player's is dropped (on_goal_cleared)."""
        key = self._key(char)
        if not key or not self._on():
            return dict(_EMPTY)
        iid = self._resolve(key, item_id) if item_id is not None else self.plan(key, fresh=True)["goal"]
        if iid is None:
            return self.plan(key)                   # (nothing to skip)
        with self.lock:
            c = self._char(key)
            c["skipped"] = [i for i in c["skipped"] if i != iid] + ([iid] if skip else [])
            dropped = skip and c["goal"] == iid and c["pursued"]
            if skip and c["goal"] == iid:
                c["goal"], c["pursued"] = None, False
            self._save()
        log("pursuit", "%s: %s %s" % (key, "skipped" if skip else "no longer skipped", self._name(iid)))
        if dropped:
            self._goal_cleared(key)
        return self._replan(key)

    def _replan(self, key):
        plan = self.plan(key, fresh=True)
        with self.lock:
            c = self._char(key)
            c["moves"], c["tag"] = [dict(m) for m in plan["moves"][:MOVES_KEEP]], plan["tag"]
            c["goal"] = plan["goal"] if not c["pursued"] else c["goal"]
            self._save()
        return plan

    def ledger(self, char, item_id):
        """How the hunt for an item goes ("how's the stylus hunt?"): {"item", "name", "tries" (kills of its bosses
        while it was looked for), "got" (it landed), "range" ([p25, p75] tries), "p90", "odds"}, or None."""
        key = self._key(char)
        iid = self._resolve(key, item_id) if key else None
        if iid is None:
            return None
        with self.lock:
            c = json.loads(json.dumps(self.data["chars"].get(key) or self._blank()))
            p, src, win, share = (self.chance.get(key) or {}).get(iid) or (None, None, 1.0, 1.0)
        r = self._call("quantiles", p) if _finite(p) and 0 < p <= 1 else None
        p90 = int(round(r[2])) if isinstance(r, (tuple, list)) and len(r) >= 3 and _finite(r[2]) else None
        return {"item": iid, "name": self._name(iid), "tries": self._tries(c, iid),
                "got": iid in c["owned"], "range": self._range(p), "p90": p90,
                "odds": self._with_rolls(self._odds(p, src), win, share) if p else None}

    def owned(self, char=None):
        """The candidates that landed for the character (kept across restarts): the items the plan went for that are
        theirs now ([] for none)."""
        key = self._key(char)
        with self.lock:
            return list((self.data["chars"].get(key or "") or {}).get("owned") or [])

    def events(self):
        """The follow-through since the last call, each handed out once: [{"kind": "roll" | "got" | "zone", "char",
        "item", "name", "tries", "range", "next", "say", ...}]."""
        with self.lock:
            out, self.pending = self.pending, []
        return out

    # ---- told once ----------------------------------------------------------------------------------------------
    def told(self, key, char_key_=None):
        """Whether the coach said this (a rules key, e.g. "next_move|<tag>") to the character (default: the one
        playing). Kept in data/pursuit.json, so it holds across restarts."""
        ck = char_key_ or self.current
        with self.lock:
            return bool(ck) and str(key) in ((self.data["chars"].get(ck) or {}).get("told") or {})

    def mark_told(self, key, char_key_=None):
        """Remember the coach said this (the TOLD_KEEP newest per character are kept)."""
        ck = char_key_ or self.current
        if not ck:
            return
        with self.lock:
            told = self._char(ck)["told"]
            told[str(key)] = int(self._now())
            for k in sorted(told, key=lambda k: told[k])[:max(0, len(told) - TOLD_KEEP)]:
                del told[k]
            self._save()

    # ---- what the addon gets ------------------------------------------------------------------------------------
    def live_section(self):
        """live_data's "chars" provider (merged with the BiS ranks' and the sims'): {char key: {"next": {"t": the
        move's short line, "m": uiMapID, "x": 0..1, "y": 0..1 (where it is, when known), "alt": [the next two
        moves' lines]}, "goal": item id}}, for each character with a move or a goal; {} while pursuit.enabled is
        off."""
        if not self._on():
            return {}
        out = {}
        with self.lock:
            for key, c in sorted(self.data["chars"].items()):
                e = {}
                moves = c.get("moves") or []
                if moves:
                    m = moves[0]
                    nxt = {"t": m.get("text") or m["act"]}
                    w = m.get("where")
                    if isinstance(w, dict) and as_int(w.get("map")) and _finite(w.get("x")) and _finite(w.get("y")):
                        nxt.update(m=int(w["map"]), x=w["x"], y=w["y"])
                    alt = [x.get("text") or x["act"] for x in moves[1:1 + ALTS]]
                    if alt:
                        nxt["alt"] = alt
                    e["next"] = nxt
                if isinstance(c.get("goal"), int):
                    e["goal"] = c["goal"]
                if e:
                    out[key] = e
        return out
