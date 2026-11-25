"""The sims the coach runs for the player (Phase 3), with WoWSims on their own gear: an item swap or a talent build they
ask about (the coach's wowsims_sim tool, coach_tools.SIM_TOOLS; "sim this" said or typed; "/glide sim <item link>" in
the game, which the addon sends as core key hv in its request form, GameState.sim_request), and the talent check: once
per WoWSims release and once per talent change at 60, WoWSims' own talent builds for the class (simbuild.presets, each
simmed with its own rotation) against the player's, said once (rules' talent_check) when one sims at least
TALENT_MIN_PCT higher, beyond the sims' noise (2 sigma). Every answer carries the "Open in WoWSims" link
(wowsims_link.link: the character's gear, talents and settings as Glide simmed them, opened on WoWSims' site) and the
credit WoWSims asks for (link_field: LINK_TEXT, CREDIT).

The sims go through sim_compare.compare (one seed for every variant, the worn gear's run cached per gear key, staged
iterations): an asked one at sim_queue.PRIO_ASKED within gear.sim_seconds (it jumps the queue and never waits for the
player), the talent check at PRIO_BIS in the background with no deadline (low process priority, waiting while the
player fights, is dead or in an instance; a check with a build left out for time is never kept: sim_compare.Late, tried
again later, never counted as a failure). Answers without a model are the offline lines (offline_coach.sim_item_line, sim_talent_line,
NO_WOWSIMS): one number, "about", and "about the same as yours" within the sims' noise.

Sims (coach_tools.sims, set by the app) is the hook's object: ask(char_key, variants) -> sim_compare Results, and
link(char_key) -> the character's link; simulate() is what the tool and the spoken command run (names found, the
answer's lines, the link of the gear simmed); talent_check(s) / mark_told() what rules._talent_check says; is_gear()
and build_words() what coach_tools.sim_question takes a question for; live_section() Data/Live.lua's per character
fields (chars[key].link, and chars[key].sim[item id] = {d, suffix, slot, over, pair}: the simmed DPS change on the
gear worn now, 0 within the sims' noise, with what it was simmed against, so the tooltip's upgrade line says it only
while that is still worn), merged with the BiS ranks' (merge_chars). The worker (start) answers the addon's sim
requests within a second, notes other gear (its sims and link go, even while WoWSims can't run), and runs the talent
check on its own thread.

data/sims.json: {"v": 1, "chars": {key: {"gk": the gear key of the last gear seen (gear_key), "link": its link (made
with WoWSims' item data), "sim": {item id: {"d", "s", "gk", "t", "tag", "slot", "over", "pair"?, "sfx"?}} (asked item
sims on that gear: the WoWSims release, the inventory slot the item went in, the worn item there it replaced (0: none)
and, for a ring, trinket or weapon, what the other slot of the pair held (0: nothing); another gear drops them, another
release leaves them out of Live.lua), "asked": [{"t",
"label", "d", "s", "iters", "variant", "gk"}] (the ASKED_KEEP latest asked sims), "checks": {"<tag>|<talents>": {"t",
and when a build sims clearly higher: "build", "talents", "pct", "d", "s", "link"}} (the talent checks run: never again
for the same release and talents), "told": {"talent_check|<tag>|<talents>": when}}}}; key "Name-Realm" when the realm
is known, else "Name" (bis.char_key).
"""
import json
import os
import re
import threading
import time
from collections.abc import Callable
from typing import Any

import bis
import item_db
import jsonfile
import offline_coach
import settings
import sim_compare
import simbuild
from log import log, log_once
from research import closest, norm_name
from sim_queue import PRIO_ASKED, PRIO_BIS
from simbuild import SimError
from state import as_int

V = 1
REQUEST_POLL_S = 1.0                    # the worker looks for the addon's sim requests this often
CHECK_POLL_S = 15                       # ... and at the character (gear key, link, talent check) this often
SETTLE_S = 120                          # talents (or a WoWSims release) unchanged this long before the talent check
RETRY_S = 600                           # a talent check that failed: tried again after this
GIVE_UP = 3                             # ... at most this many times per release and talents
TALENT_MIN_PCT = 3.0                    # the talent check speaks of a build at least this much higher
TALENT_BUDGET_S = None                  # the talent check's sims: no deadline (in the background: they wait for the
                                        # player, as the BiS ranks do; one left out for time is never kept)
ASKED_BUDGET_S = 30                     # an asked sim without gear.sim_seconds
PAIR_SLOT = {11: 12, 12: 11, 13: 14, 14: 13, 16: 17, 17: 16}     # a slot -> the other of its pair (Live.lua's "pair")
BASE_ITERS, SEED = 10000, 1             # the worn gear's own run, for a talent build's percent
BASE_WAIT_S = 120
ASKED_KEEP, SIM_KEEP, CHECK_KEEP, TOLD_KEEP = 20, 64, 16, 32
MAX_ITEMS = 6                           # items one question sims at most
LINK_TEXT = "Open in WoWSims"
CREDIT = "WoWSims (wowsims.com/forever)"
SITE_PREFIX = "https://www.wowsims.com/forever/"     # (wowsims_link.SITE_PREFIX) the only links a message carries
NO_WOWSIMS = offline_coach.NO_WOWSIMS
# what the player hears when a sim can't run (Unsimmable)
NOT_60 = "WoWSims sims level 60 characters only."
NO_CLASS = "WoWSims can't sim your class yet."
NOT_IN = "I can sim that once your gear and talents are in from the game."
NOT_PLAYING = "I can only sim the character you're playing."
OFF = "Sims on request are off in Glide's settings."
FAILED = "WoWSims couldn't sim that."
NO_ITEM = "I don't know an item called %s."
WHICH = "Which item? Point at it in the game, or name it."
NO_BUILD = "WoWSims has no build called %s."
WORN = "You already wear %s."
SAME_BUILD = "That's the build you have."
THIS = re.compile(r"(?:this|that|it)(?: (?:item|one|thing|piece))?", re.I)
TALENT_STRING = re.compile(r"[0-9]*(?:-[0-9]*){0,2}")
BUILD_FILLER = re.compile(r"\b(?:wowsims'?s?|the|build|builds|talents?|spec|preset)\b")


class Unsimmable(SimError):
    """A sim that can't run, its text what the player hears (no WoWSims, not level 60, a class WoWSims doesn't sim, gear
    or talents not in yet)."""


def link_field(url):
    """The coach-screen message field for a WoWSims link ({"text": LINK_TEXT, "url", "credit": CREDIT}), or None for
    anything that isn't a link to WoWSims' site."""
    if not isinstance(url, str) or not url.startswith(SITE_PREFIX):
        return None
    return {"text": LINK_TEXT, "url": url, "credit": CREDIT}


def merge_chars(*sections):
    """Data/Live.lua's "chars" section from several providers' ({char key: {field: value}}: the BiS ranks' "bis", this
    module's "link" and "sim"), merged per character (a later provider's field wins)."""
    out: dict = {}
    for sec in sections:
        for key, fields in (sec or {}).items():
            if isinstance(fields, dict) and fields:
                out.setdefault(key, {}).update(fields)
    return out


def character(st, skills=None):
    """What WoWSims sims of the player now ({"name", "realm", "class", "race", "faction", "level", "talents", "worn",
    "bags", "skills"}, as bis.BisRanker.character has it), or None (no game state, gear or talents not in yet, a fishing
    pole in hand)."""
    if st is None:
        return None
    s = st.summary() or {}
    talents = st.talents()
    gear = getattr(st, "gear", None) or {}
    worn = gear.get("W") or []
    if not s.get("name") or not talents or not worn \
            or any((it.get("cls"), it.get("sub")) == bis.FISHING_POLE for it in worn):
        return None
    return {"name": s.get("name"), "realm": s.get("realm"), "class": str(s.get("class") or "").upper(),
            "race": s.get("race"), "faction": s.get("faction"), "level": as_int(s.get("level")),
            "talents": talents, "worn": worn, "bags": gear.get("I") or [], "skills": skills}


def gear_key(char):
    """What a sim of the character depends on: class, race, side, talents, the worn items with their enchants and
    random suffixes, and whether it enchants (a ring's enchant counts only then). The same key, the same gear: an
    item's simmed change still holds (on the same WoWSims release: live_section leaves another's out)."""
    worn = sorted((str(it.get("key")), as_int(it.get("id")) or 0, as_int(it.get("enchant")) or 0,
                   as_int(it.get("suffix")) or 0) for it in char.get("worn") or ())
    return json.dumps([V, simbuild.VERSION, char.get("class"), char.get("race"), char.get("faction"),
                       simbuild.talents_string(char.get("talents")), worn, simbuild.enchanter(char.get("skills"))])


def tree_of(cls, talents):
    """The talent tree a talent string spends most in ("Arcane"), or None."""
    trees = (simbuild.CLASSES.get(str(cls or "").upper()) or {}).get("trees") or ()
    runs = str(talents or "").split("-")
    pts = [sum(int(c) for c in runs[i] if c.isdigit()) if i < len(runs) else 0 for i in range(len(trees))]
    return trees[pts.index(max(pts))] if trees and max(pts) > 0 else None


def about_same(d, s):
    """Whether a simmed change reads "about the same as yours": within the sims' noise (2 sigma), or nothing simmed at
    all (d and s 0: sim_compare's own build or a request the same as the worn gear's)."""
    return abs(d) < 2 * s or (d == 0 and s == 0)


def _build_word(text):
    """A build name as a player says it, for matching ("the Arcane Fire build" -> "arcane fire")."""
    return " ".join(BUILD_FILLER.sub(" ", norm_name(text)).split())


class Sims:
    """home: the engine's folder (data/sims.json); queue: the sim_queue.SimQueue (its ws: the WoWSims runner);
    item_db_fn -> the item_db.ItemDB or None (research.ItemDBSource: its "names" find items by name); state_fn -> the
    GameState; opt: the gear settings (settings.Settings("gear", ...): sim_questions, talent_check, sim_seconds).
    Seams the app (or a test) may set: emit(message) (the coach's: an answer to the addon's request), find_fn(query)
    -> item id or None (research.Research._find), name_fn(item id) -> its name or None, skills_fn(name) -> the
    character's professions or None; compare_fn (sim_compare.compare), presets_fn (simbuild.presets), link_fn
    (wowsims_link.link), request_fn (simbuild.request's signature)."""

    def __init__(self, home, queue, item_db_fn, state_fn, opt=None, now=time.time):
        self.path = os.path.join(home, "data", "sims.json")
        self.queue, self.item_db_fn, self.state_fn = queue, item_db_fn, state_fn
        self.ws = getattr(queue, "ws", None)
        self.opt = opt if opt is not None else settings.Settings("gear", {})
        self._now = now
        self.emit: Callable[[dict], Any] | None = None
        self.find_fn: Callable[[str], int | None] | None = None
        self.name_fn: Callable[[int], str | None] | None = None
        self.skills_fn: Callable[[str], Any] | None = None
        self.weights_fn: Callable[[str], dict | None] | None = None
        self.compare_fn: Callable[..., list] | None = None
        self.presets_fn: Callable[..., dict] | None = None
        self.link_fn: Callable[[dict, dict], str] | None = None
        self.request_fn: Callable[..., dict] | None = None
        self.lock = threading.RLock()
        self.data = self._load()
        self.last_link = None                   # the link of the last sim simulate() ran (its answer's message)
        self._rot: dict = {}                    # (APL path, tag) -> the rotation
        self._presets: dict = {}                # (class, tag) -> {build: (talents, APL path)}
        self._bases: dict = {}                  # (gear key, tag) -> the worn gear's DPS
        self._settle: dict = {}                 # char key -> (check key, since)
        self.failed: dict = {}                  # check key -> {"t", "n"}
        self._checking = None                   # the talent check running (its check key)
        self._check_t = None

    # ---- data/sims.json -----------------------------------------------------------------------------------------
    def _load(self):
        try:
            d = jsonfile.read(self.path)
        except (OSError, ValueError):
            d = None
        chars = d.get("chars") if isinstance(d, dict) and d.get("v") == V else None
        out = {}
        for k, c in (chars if isinstance(chars, dict) else {}).items():
            if not isinstance(k, str) or not isinstance(c, dict):
                continue
            e = {}
            if isinstance(c.get("gk"), str):
                e["gk"] = c["gk"]
            if isinstance(c.get("link"), str) and c["link"].startswith(SITE_PREFIX):
                e["link"] = c["link"]
            e["sim"] = {str(i): x for i, x in (c.get("sim") or {}).items()
                        if str(i).isdigit() and isinstance(x, dict) and isinstance(x.get("d"), (int, float))} \
                if isinstance(c.get("sim"), dict) else {}
            e["asked"] = [x for x in c.get("asked") or () if isinstance(x, dict)][-ASKED_KEEP:] \
                if isinstance(c.get("asked"), list) else []
            e["checks"] = {str(ck): x for ck, x in c["checks"].items() if isinstance(x, dict)} \
                if isinstance(c.get("checks"), dict) else {}
            e["told"] = {str(t): v for t, v in c["told"].items() if isinstance(v, (int, float))} \
                if isinstance(c.get("told"), dict) else {}
            out[k] = e
        return {"v": V, "chars": out}

    def _save(self):
        with self.lock:
            try:
                jsonfile.write_atomic(self.path, self.data, ensure_ascii=False, separators=(",", ":"))
            except OSError as e:
                log_once("sims.save", e)

    def _char(self, key):
        """(Under the lock) the character's entry in sims.json, made when new."""
        return self.data["chars"].setdefault(key, {"sim": {}, "asked": [], "checks": {}, "told": {}})

    # ---- settings, WoWSims and the character ------------------------------------------------------------------
    def _on(self, name):
        try:
            return self.opt.get(name)
        except Exception as e:  # noqa: BLE001 - a setting that can't be read is off
            log_once("sims.opt." + name, e)
            return None

    def available(self):
        """Whether a WoWSims runner is on this PC (the release in use)."""
        cur = getattr(self.ws, "current", None)
        try:
            return bool(cur()) if callable(cur) else False
        except Exception as e:  # noqa: BLE001 - none then
            log_once("sims.available", e)
            return False

    def offered(self):
        """Whether the coach offers sims on request (the wowsims_sim tool): WoWSims runs here and gear.sim_questions
        is on."""
        return bool(self._on("sim_questions")) and self.available()

    def _tag(self):
        cur = getattr(self.ws, "current", None)
        try:
            return cur() if callable(cur) else None
        except Exception as e:  # noqa: BLE001
            log_once("sims.tag", e)
            return None

    def _db(self):
        try:
            return self.item_db_fn() if self.item_db_fn is not None else None
        except Exception as e:  # noqa: BLE001 - sims go without WoWSims' item data (what it knows is left out)
            log_once("sims.item_db", e)
            return None

    def _state(self):
        try:
            return self.state_fn() if self.state_fn is not None else None
        except Exception as e:  # noqa: BLE001
            log_once("sims.state", e)
            return None

    def _skills(self, name):
        if self.skills_fn is None:
            return None
        try:
            sk = self.skills_fn(name)
        except Exception as e:  # noqa: BLE001 - unknown
            log_once("sims.skills", e)
            return None
        return sorted(str(x) for x in sk) if sk is not None else None

    def _character(self, st=None):
        st = st if st is not None else self._state()
        if st is None:
            return None
        try:
            s = st.summary() or {}
            return character(st, self._skills(s.get("name")))
        except Exception as e:  # noqa: BLE001 - not in yet
            log_once("sims.character", e)
            return None

    def ready_char(self, char_key=None):
        """The character playing, if WoWSims can sim it now (char_key: only that one); else raises Unsimmable with
        what the player hears."""
        if not self.available():
            raise Unsimmable(NO_WOWSIMS)
        st = self._state()
        s = (st.summary() if st is not None else None) or {}
        if s.get("level") is not None and s.get("level") != 60:
            raise Unsimmable(NOT_60)
        if s.get("class") and str(s["class"]).upper() not in simbuild.CLASSES:
            raise Unsimmable(NO_CLASS)
        char = self._character(st)
        if char is None or not simbuild.simmable(s):
            raise Unsimmable(NOT_IN)
        if char_key and char_key not in (bis.char_key(char), char.get("name")):
            raise Unsimmable(NOT_PLAYING)
        return char

    # ---- WoWSims' pieces: the comparison, its builds, the link --------------------------------------------------
    def _weights(self, name):
        """The character's WoWSims weights ({code: EP}, sim_weights' "w": they pick which random suffixes and bag
        weapons a sim tries), or None."""
        if self.weights_fn is None:
            return None
        try:
            w = self.weights_fn(name)
        except Exception as e:  # noqa: BLE001 - sim_compare's rough screen then
            log_once("sims.weights", e)
            return None
        return w if isinstance(w, dict) and w else None

    def _compare(self, char, variants, db, budget_s, prio):
        """sim_compare.compare (compare_fn) on the sim queue; budget_s None: no deadline (the talent check's)."""
        fn = self.compare_fn or sim_compare.compare
        kw: dict[str, Any] = {"budget_s": None if budget_s is None else float(budget_s)}
        w = self._weights(char.get("name"))
        if w is not None:
            kw["weights"] = w
        return list(fn(char, variants, self.queue, self.ws, db=db, prio=prio, **kw) or ())

    def presets(self, cls, tag=None):
        """WoWSims' talent builds for a class at a release: {build name: (talent string, its rotation's path)}."""
        tag = tag or self._tag()
        k = (str(cls or "").upper(), tag)
        with self.lock:
            if k in self._presets:
                return self._presets[k]
        fn = self.presets_fn or getattr(simbuild, "presets", None)
        if fn is None or k[0] not in simbuild.CLASSES:
            return {}
        got = fn(k[0], self.ws, tag) or {}
        out = {}
        for name, v in got.items():
            talents, apl = (tuple(v) + (None, None))[:2] if isinstance(v, (tuple, list)) else (v, None)
            if isinstance(talents, str) and talents.strip("-0"):
                out[str(name)] = (talents, apl)
        with self.lock:
            self._presets = {k: out}
        return out

    def _builds(self, char, tag=None):
        """The class's WoWSims builds that fit the character (a Skyborne build only for a Skyborne)."""
        sky = "skyborne" in str(char.get("race") or "").lower()
        return {n: v for n, v in self.presets(char.get("class"), tag).items()
                if sky or "skyborne" not in n.lower()}

    def _request(self, char, rotation, iterations, seed, db):
        if self.request_fn is not None:
            return self.request_fn(char, rotation, iterations, seed, db)
        return simbuild.request(char, rotation, iterations, seed, None, db=db)

    def _rotation(self, char, tag):
        """The rotation WoWSims gives the character's own spec (its .apl.json, parsed)."""
        cls = char["class"]
        path = simbuild.CLASSES[cls]["apl"][simbuild.spec_of(cls, char.get("talents"))]
        with self.lock:
            got = self._rot.get((path, tag))
        if got is None:
            if self.ws is None:
                raise Unsimmable(NO_WOWSIMS)
            got = json.loads(self.ws.file(path, tag).decode("utf-8"))
            with self.lock:
                self._rot = {(path, tag): got}
        return got

    def _make_link(self, char, db, talents=None):
        """The WoWSims link of the character's setup as Glide sims it (talents: a build's talent string in place of
        theirs), or None."""
        fn = self.link_fn
        if fn is None:
            try:
                import wowsims_link
            except ImportError as e:
                log_once("sims.link_module", e)
                return None
            fn = wowsims_link.link
        try:
            req = self._request(char, {}, 1000, SEED, db)
            if talents:
                for p in req["raid"]["parties"][0]["players"]:
                    p["talentsString"] = talents
            url = fn(char, req)
        except Exception as e:  # noqa: BLE001 - the answer goes without a link
            log_once("sims.link", e)
            return None
        return url if isinstance(url, str) and url.startswith(SITE_PREFIX) else None

    def link(self, char_key=None):
        """The WoWSims link of the character's setup (char_key: its key; None: the one playing): its gear, talents and
        settings as Glide sims them, or None."""
        char = self._character()
        key = bis.char_key(char) if char is not None else None
        if char is not None and (not char_key or char_key in (key, char.get("name"))):
            self._seen(char)
            return self._link_for(char, self._db())
        with self.lock:
            return (self.data["chars"].get(char_key or "") or {}).get("link")

    def _link_for(self, char, db):
        """The WoWSims link of a character's setup as it was simmed (char: the one the sim had, never the gear worn
        since), kept in sims.json while that gear is the one seen last; one made without WoWSims' item data (db None:
        it would keep enchants and suffixes the sims leave out) is given, never kept. -> the url or None."""
        key, gk = bis.char_key(char), gear_key(char)
        with self.lock:
            c = self.data["chars"].get(key) or {}
            if c.get("link") and c.get("gk") == gk:
                return c["link"]
        url = self._make_link(char, db)
        if url and db is not None:
            kept = False
            with self.lock:
                c = self._char(key)
                if c.get("gk") is None:
                    c["gk"] = gk
                if c.get("gk") == gk:
                    c["link"], kept = url, True
            if kept:
                self._save()
        return url

    def _seen(self, char):
        """(The character playing) note its gear: other gear than the last seen drops the old gear's sims and link
        (even while WoWSims can't run, so Live.lua never carries them on). -> whether a link for this gear is kept."""
        key, gk = bis.char_key(char), gear_key(char)
        with self.lock:
            c = self.data["chars"].get(key)
            moved = c is not None and c.get("gk") != gk
            if moved:
                self._new_gear(c, gk)
            fresh = bool(c and c.get("link"))
        if moved:
            self._save()
        return fresh

    @staticmethod
    def _new_gear(c, gk):
        """(Under the lock) the character wears other gear: what was simmed on the old gear and its link go."""
        if c.get("gk") != gk:
            c["gk"] = gk
            c["sim"] = {}
            c.pop("link", None)

    # ---- the hook: asked sims ------------------------------------------------------------------------------------
    def ask(self, char_key, variants, budget_s=None, prio=PRIO_ASKED):
        """Sim the variants (sim_compare's Variant: an item, or a talent build with its rotation) on the character's
        gear (char_key: its key, None: the one playing) within gear.sim_seconds (budget_s: fewer, when the answer has
        less time left) -> the Results ({"label", "d", "s", "iters", "variant"}: d the DPS change, s its sigma). Kept
        in sims.json (an item's change also for the addon's tooltip, Live.lua's chars[key].sim). Raises Unsimmable
        (what the player hears) or simbuild.SimError."""
        return self._ask(self.ready_char(char_key), variants, budget_s, prio, self._db())

    def _ask(self, char, variants, budget_s, prio, db):
        """ask() on a character already found, with WoWSims' item data (or None) as the caller has it."""
        cap = self._on("sim_seconds")
        budget = min(float(budget_s), float(cap)) if budget_s and cap else (budget_s or cap or ASKED_BUDGET_S)
        results = self._compare(char, list(variants or ()), db, budget, prio)
        self._record(char, results, db)
        return results

    def _record(self, char, results, db=None):
        """Keep the Results in sims.json (asked); an item's also for the tooltip (sim) with where it went and what it
        was set against, while the gear simmed is the gear worn (the player may have changed it meanwhile)."""
        key, gk, now = bis.char_key(char), gear_key(char), self._now()
        cur = self._character()
        current = cur is None or bis.char_key(cur) != key or gear_key(cur) == gk
        worn = {as_int(str(it.get("key") or "0").split(".")[0]) or 0: as_int(it.get("id")) or 0
                for it in char.get("worn") or ()}
        with self.lock:
            c = self._char(key)
            if current:
                self._new_gear(c, gk)
            for r in results:
                v = r.get("variant") if isinstance(r.get("variant"), dict) else {}
                try:
                    d, s = float(r["d"]), float(r.get("s") or 0.0)
                except (KeyError, TypeError, ValueError):
                    continue
                c["asked"].append({"t": int(now), "label": str(r.get("label") or ""), "d": round(d, 2),
                                   "s": round(s, 2), "iters": as_int(r.get("iters")), "variant": v, "gk": gk})
                if v.get("kind") != "item" or not isinstance(v.get("id"), int) or r.get("worn") or not current \
                        or c.get("gk") != gk:
                    continue                           # (simmed on gear no longer worn: not for the tooltip)
                slot = as_int(r.get("slot")) or self._inv_slot(dict(v, slot=r.get("slot") or v.get("slot")), db)
                if not slot:
                    continue                           # (where it went isn't known: the tooltip can't say against what)
                e = {"d": round(d, 1), "s": round(s, 2), "gk": gk, "t": int(now), "tag": r.get("tag") or self._tag(),
                     "slot": slot, "over": worn.get(slot, 0)}
                if slot in PAIR_SLOT:
                    e["pair"] = worn.get(PAIR_SLOT[slot], 0)
                sfx = abs(as_int(r.get("sfx")) or as_int(v.get("sfx")) or 0)
                if sfx:
                    e["sfx"] = sfx                     # (the random suffix it was simmed with: the tooltip's only)
                c["sim"][str(v["id"])] = e
            del c["asked"][:-ASKED_KEEP]
            for i in sorted(c["sim"], key=lambda i: c["sim"][i].get("t") or 0)[:max(0, len(c["sim"]) - SIM_KEEP)]:
                del c["sim"][i]
        self._save()

    # ---- what the coach's tool and the spoken command run ---------------------------------------------------------
    def find(self, query, char=None):
        """The item id a question names: "this" (the item the player hovers in the game), an id ("#16686"), one of the
        character's own items by name, else the research's names (find_fn) or WoWSims' (a near miss for a misheard
        name); None for none."""
        q = " ".join(str(query or "").split()).strip(" .?!'\"")
        if not q:
            return None
        if THIS.fullmatch(q):
            st = self._state()
            fn = getattr(st, "hovered", None)
            try:
                hv = fn() if callable(fn) else None
            except Exception as e:  # noqa: BLE001 - nothing hovered
                log_once("sims.hovered", e)
                hv = None
            iid = hv.get("id") if isinstance(hv, dict) else None
            return iid if isinstance(iid, int) and iid > 0 else None
        m = re.fullmatch(r"(?:item\s*)?#?(\d+)", q, re.I)
        if m:
            return int(m.group(1))
        own = {}
        for it in (char or {}).get("worn") or ():
            own.setdefault(norm_name(it.get("name")), it.get("id"))
        for it in (char or {}).get("bags") or ():
            own.setdefault(norm_name(it.get("name")), it.get("id"))
        hit = own.get(norm_name(q))
        if isinstance(hit, int):
            return hit
        if self.find_fn is not None:
            try:
                hit = self.find_fn(q)
            except Exception as e:  # noqa: BLE001 - WoWSims' names then
                log_once("sims.find", e)
                hit = None
            if isinstance(hit, int):
                return hit
        names = getattr(self.item_db_fn, "names", None)
        if isinstance(names, dict) and names:
            hit = closest(q, names)
            if hit is not None:
                return hit
        db = self._db()
        hits = db.find(q) if db is not None else []
        return hits[0] if hits else None

    def is_gear(self, query):
        """Whether a question names gear (coach_tools.sim_question takes "what if I wear X?" for a sim only then): one of
        the character's worn items, a bag item with an equip location, or an item WoWSims' item data knows (it holds
        gear only); "this": the item hovered."""
        char = self._character()
        iid = self.find(query, char)
        if not isinstance(iid, int):
            return False
        if any(it.get("id") == iid for it in (char or {}).get("worn") or ()) or \
                any(it.get("id") == iid and it.get("loc") for it in (char or {}).get("bags") or ()):
            return True
        db = self._db()
        return bool(db is not None and db.item(iid))

    def build_words(self):
        """What a player calls a talent build of the class playing (coach_tools.sim_question takes "what if I go X?"
        for a sim only with one): WoWSims' builds for it ("arcane fire") and its talent trees ("frost"); none before
        its class is known."""
        st = self._state()
        try:
            s = (st.summary() if st is not None else None) or {}
        except Exception as e:  # noqa: BLE001 - not in yet
            log_once("sims.build_words_state", e)
            s = {}
        cls = str(s.get("class") or "").upper()
        conf = simbuild.CLASSES.get(cls)
        if conf is None:
            return set()
        words = {_build_word(t) for t in conf.get("trees") or ()}
        if self.available():
            try:
                words |= {_build_word(n) for n in self.presets(cls)}
            except Exception as e:  # noqa: BLE001 - the trees' names then
                log_once("sims.build_words", e)
        return words - {""}

    def name(self, iid, sfx=None, char=None):
        """An item's name as the coach says it (with a random suffix: "Master's Cloak of the Owl"): the character's own
        items', the research's (name_fn), WoWSims'; else "that item" (never an id)."""
        for kind in ("worn", "bags"):
            for it in (char or {}).get(kind) or ():
                if it.get("id") == iid and it.get("name") and (not sfx or kind == "bags"
                                                               or abs(as_int(it.get("suffix")) or 0) == abs(sfx)):
                    return it["name"]
        nm = None
        if self.name_fn is not None:
            try:
                nm = self.name_fn(iid)
            except Exception as e:  # noqa: BLE001 - WoWSims' then
                log_once("sims.name", e)
        db = self._db()
        if not nm and db is not None:
            nm = db.name(iid)
        if nm and sfx and db is not None:
            sx = db.suffixes.get(sfx) or db.suffixes.get(abs(sfx)) or {}
            if sx.get("name"):
                nm = "%s %s" % (nm, sx["name"])
        return nm or "that item"

    def _variants(self, char, items, talents):
        """(variants, what couldn't be simmed: lines the player hears) for the items (names, ids, "this") and a talent
        build (a WoWSims build's name, or a talent string)."""
        out, problems = [], []
        if isinstance(items, str):
            items = [items]
        for q in list(items or ())[:MAX_ITEMS]:
            iid = self.find(q, char)
            said = " ".join(str(q).split()).strip(" .?!'\"")
            if iid is None:
                problems.append(WHICH if not said or THIS.fullmatch(said) else NO_ITEM % said)
            elif not any(v.get("id") == iid for v in out):
                out.append({"kind": "item", "id": iid, "slot": None, "sfx": None, "ench": None})
        t = " ".join(str(talents or "").split()).strip(" .?!'\"")
        if t:
            own = sim_compare.norm_talents(simbuild.talents_string(char.get("talents")))
            if TALENT_STRING.fullmatch(t) and t.strip("-0"):
                v = {"kind": "talents", "talents": t.rstrip("-"), "apl": None, "name": None}
            else:
                builds = self._builds(char)
                by_word = {_build_word(n): n for n in builds}
                name = by_word.get(_build_word(t)) or (closest(_build_word(t), {w: n for w, n in by_word.items()})
                                                       if by_word else None)
                v = {"kind": "talents", "talents": builds[name][0], "apl": builds[name][1], "name": name} \
                    if name else None
                if v is None:
                    problems.append(NO_BUILD % t)
            if v is not None and sim_compare.norm_talents(v["talents"]) == own:
                problems.append(SAME_BUILD)               # (trailing zeroes as WoWSims trims them: the same build)
            elif v is not None:
                out.append(v)
        return out, problems

    def _worn_for(self, char, v, db):
        """The worn item an item variant replaces (its name), or None: the result's slot, else the item's slot when
        only one item is worn there (a ring or trinket without a slot: none)."""
        inv = self._inv_slot(v, db)
        if inv is None:
            return None
        for it in char.get("worn") or ():
            if (as_int(str(it.get("key") or "0").split(".")[0]) or 0) == inv and it.get("name"):
                return it["name"]
        return None

    @staticmethod
    def _inv_slot(v, db):
        """The inventory slot an item variant went in, or None: its slot (an inventory slot, or a slot's name), else
        the item's own when it has one only (a ring or trinket without a slot: none)."""
        slot = v.get("slot")
        inv = None
        if isinstance(slot, int) or (isinstance(slot, str) and slot.isdigit()):
            inv = int(slot)
        elif isinstance(slot, str) and slot:
            inv = next((s for s, n in bis.SLOT_NAME.items() if n.lower() == slot.strip().lower()), None)
            if inv is None:
                group = next((g for g in bis.GROUP_SLOTS if g.lower() == slot.strip().lower()), None)
                slots = bis.GROUP_SLOTS.get(group or "") or ()
                inv = slots[0] if len(slots) == 1 else None
        if inv is None and db is not None:
            rec = db.item(v.get("id")) or {}
            if rec.get("type") == item_db.WEAPON:
                group = "Off Hand" if rec.get("handType") == item_db.OFF_HAND else "Main Hand"
            else:
                group = bis.TYPE_GROUP.get(as_int(rec.get("type")) or 0)
            slots = bis.GROUP_SLOTS.get(group or "") or ()
            inv = slots[0] if len(slots) == 1 else None
        return inv

    def _base(self, char, db, tag, prio, results=()):
        """The worn gear's DPS (a talent build's percent is of it): a Result's "base" when sim_compare gives one, else
        one run of the character's own build and rotation (kept per gear and release)."""
        for r in results:
            b = r.get("base")
            if isinstance(b, (int, float)) and b > 0:
                return float(b)
        k = (gear_key(char), tag)
        with self.lock:
            if k in self._bases:
                return self._bases[k]
        req = self._request(char, self._rotation(char, tag), BASE_ITERS, SEED, db)
        fut = self.queue.submit(req, prio, tag=tag, background=prio != PRIO_ASKED)
        dps = float(fut.result(BASE_WAIT_S if prio == PRIO_ASKED else None)["dps"])
        if dps <= 0:
            raise SimError("the worn gear simmed no DPS")
        with self.lock:
            self._bases = {k: dps}
        return dps

    @staticmethod
    def _said_name(label):
        """sim_compare's label when it is a name to say ("item 1234" and "those talents" are not)."""
        label = str(label or "").strip()
        return label if label and not re.fullmatch(r"item \d+|those talents", label) else None

    def _over(self, char, r, v, db):
        """The worn item a Result's item replaced, as said ("Old Staff", "Old Staff and Old Orb": a two-hander empties
        the off hand too): sim_compare's "over_id" / "over" (None: nothing was worn there), else found here."""
        if "over_id" not in r and "over" not in r:
            return self._worn_for(char, v, db)
        oid = as_int(r.get("over_id"))
        name = next((it.get("name") for it in char.get("worn") or () if oid and it.get("id") == oid and it.get("name")),
                    None) or self._said_name(r.get("over"))
        also = self._said_name(r.get("also_over"))
        return "%s and %s" % (name, also) if name and also else name

    def describe(self, char, results, prio=PRIO_ASKED):
        """The Results as the coach says them, best first: [{"what", "kind", "d", "plus_minus" (2 sigma),
        "about_same", "said" (the offline line)}, and "pct" (of the worn gear's DPS) for a talent build, "worn" for an
        item worn already]. sim_compare's extras are used when there (its "pct", the worn item replaced, the weapon
        simmed with it, whether a build had its own rotation)."""
        db, tag, rows = self._db(), self._tag(), []
        for r in sorted(results, key=lambda r: -float(r.get("d") or 0)):
            v = r.get("variant") if isinstance(r.get("variant"), dict) else {}
            d, s = float(r.get("d") or 0), float(r.get("s") or 0)
            same = about_same(d, s)
            row: dict = {"what": str(r.get("label") or ""), "kind": v.get("kind"), "d": round(d, 1),
                         "plus_minus": round(2 * s, 1), "about_same": same}
            if v.get("kind") == "talents":
                pct = r.get("pct")
                if not isinstance(pct, (int, float)) or isinstance(pct, bool):
                    pct = 100.0 * d / self._base(char, db, tag, prio, results)
                row["pct"] = round(float(pct), 1)
                own = r.get("own_apl", bool(v.get("name")))
                build = (v.get("name") or self._said_name(r.get("label"))) if own else None
                row["what"] = build or "those talents"
                row["said"] = offline_coach.sim_talent_line(build, row["pct"], same, None if build else tree_of(
                    char.get("class"), r.get("talents") or v.get("talents")))
            else:
                iid = as_int(r.get("id")) or as_int(v.get("id"))
                sfx = as_int(r.get("sfx")) or as_int(v.get("sfx"))
                item = self.name(iid, sfx, char) if iid else "that item"
                if item == "that item":
                    item = self._said_name(r.get("label")) or item
                row["what"] = item
                if r.get("worn"):
                    row["worn"] = row["about_same"] = True
                    row["said"] = WORN % item
                else:
                    mate = self._said_name(r.get("with"))
                    row["said"] = offline_coach.sim_item_line(item + (" with %s" % mate if mate else ""),
                                                              self._over(char, r, v, db), d, same)
            rows.append(row)
        return rows

    def simulate(self, items=None, talents=None, budget_s=None, variants=None):
        """An asked sim from start to answer (the wowsims_sim tool, "sim this", the addon's request): the items (names,
        ids, "this") and a talent build (a WoWSims build's name or a talent string), or variants already made ->
        {"rows": describe's, "said": the answer without a model, "link": the WoWSims link (a build simmed alone: the
        player's gear with that build), "problems": [lines]}. The last link is kept (last_link). Raises Unsimmable
        (with what the player hears), or SimError."""
        if not self._on("sim_questions"):
            raise Unsimmable(OFF)
        char = self.ready_char()
        db = self._db()
        problems = []
        if variants is None:
            variants, problems = self._variants(char, items, talents)
        todo = []
        for v in variants:
            if v.get("kind") == "item" and sim_compare.worn_already(char, v, db):
                problems.append(WORN % self.name(v["id"], v.get("sfx"), char))     # (that very one: nothing to sim)
            else:
                todo.append(v)
        rows = []
        if todo:
            results = self._ask(char, todo, budget_s, PRIO_ASKED, db)
            rows = self.describe(char, results)
        said = " ".join([r["said"] for r in rows[:2]] + problems[:2]) or FAILED
        builds = [v for v in todo if v.get("kind") == "talents"]
        link = self._make_link(char, db, builds[0]["talents"]) if len(todo) == 1 and builds else None
        link = link or (self._link_for(char, db) if rows else None)          # (the gear simmed, not what's worn now)
        self.last_link = link
        return {"rows": rows, "said": said, "link": link, "problems": problems}

    def answer(self, items=None, talents=None, budget_s=None, variants=None):
        """simulate() as one spoken answer: (the line, the link or None); a sim that can't run says why."""
        try:
            got = self.simulate(items, talents, budget_s, variants)
        except Unsimmable as e:
            return str(e), None
        except Exception as e:  # noqa: BLE001 - a sim that failed (WoWSims, a download, its data)
            log("sims", "couldn't sim: %s" % e)
            return FAILED, None
        return got["said"], got["link"]

    # ---- the talent check ----------------------------------------------------------------------------------------
    def _check_key(self, tag, talents_list):
        return "%s|%s" % (tag, simbuild.talents_string(talents_list))

    def talent_check(self, s=None):
        """The talent check to say now for the character playing (s: its game state summary), or None: {"key" (its
        told key), "char" (its key), "build", "pct", "link"}, while gear.talent_check is on, for this WoWSims release
        and the talents it has now, not told yet."""
        if not self._on("talent_check"):
            return None
        if s is None:
            st = self._state()
            s = (st.summary() if st is not None else None) or {}
        if not simbuild.simmable(s) or not s.get("name") or not s.get("talents"):
            return None
        tag = self._tag()
        if not tag:
            return None
        ck = self._check_key(tag, s["talents"])
        with self.lock:
            c = self.data["chars"].get(bis.char_key(s)) or {}
            e = (c.get("checks") or {}).get(ck) or {}
            tk = "talent_check|" + ck
            if not e.get("build") or tk in (c.get("told") or {}):
                return None
            return {"key": tk, "char": bis.char_key(s), "build": e["build"], "pct": e.get("pct"), "link": e.get("link")}

    def mark_told(self, key, char_key=None):
        """The coach said this talent check (its told key, talent_check gives it), to the character playing (char_key:
        another): never again for that release and those talents."""
        ck = char_key
        if not ck:
            st = self._state()
            s = (st.summary() if st is not None else None) or {}
            ck = bis.char_key(s) if s.get("name") else None
        if not ck:
            return
        with self.lock:
            told = self._char(ck)["told"]
            told[str(key)] = int(self._now())
            for k in sorted(told, key=lambda k: told[k])[:max(0, len(told) - TOLD_KEEP)]:
                del told[k]
        self._save()

    def _check_due(self, char, now):
        """The talent check to run for the character now (its check key), or None: gear.talent_check on, a WoWSims
        release in use, the talents unchanged SETTLE_S, not checked for this release and talents, none running, a
        failed one not before RETRY_S and at most GIVE_UP times."""
        if not self._on("talent_check"):
            return None
        tag = self._tag()
        if not tag:
            return None
        key, ck = bis.char_key(char), self._check_key(tag, char["talents"])
        seen = self._settle.get(key)
        if seen is None or seen[0] != ck:
            self._settle[key] = (ck, now)
            return None
        if now - seen[1] < SETTLE_S:
            return None
        with self.lock:
            if ck in (self.data["chars"].get(key) or {}).get("checks", {}) or self._checking is not None:
                return None
        f = self.failed.get(ck)
        if f and (now - f["t"] < RETRY_S or f["n"] >= GIVE_UP):
            return None
        return ck

    def check_talents(self, char, ck, tag):
        """Run the talent check (on the caller's thread): WoWSims' builds for the class that fit the character, each
        with its own rotation, against its own talents on its gear, in the background (PRIO_BIS). The best build at
        least TALENT_MIN_PCT higher and beyond 2 sigma is kept (talent_check says it); none, and the check is kept
        too: never run again for this release and talents. No deadline (TALENT_BUDGET_S): the queue waits for the
        player; a build left out for time raises sim_compare.Late (kept as nothing: tried again later)."""
        key, now = bis.char_key(char), self._now()
        own = sim_compare.norm_talents(simbuild.talents_string(char["talents"]))
        db = self._db()
        builds = self._builds(char, tag)
        variants = [{"kind": "talents", "talents": t, "apl": apl, "name": n}
                    for n, (t, apl) in sorted(builds.items()) if sim_compare.norm_talents(t) != own]
        entry: dict = {"t": int(now)}
        if variants:
            results = self._compare(char, variants, db, TALENT_BUDGET_S, PRIO_BIS)
            base, best = None, None
            for r in results:
                v = r.get("variant") if isinstance(r.get("variant"), dict) else {}
                d, s = float(r.get("d") or 0), float(r.get("s") or 0)
                if v.get("kind") != "talents" or d <= 2 * s:
                    continue
                pct = r.get("pct")
                if not isinstance(pct, (int, float)) or isinstance(pct, bool):
                    base = base or self._base(char, db, tag, PRIO_BIS, results)
                    pct = 100.0 * d / base
                if pct >= TALENT_MIN_PCT and (best is None or pct > best[0]):
                    best = (pct, d, s, v)
            if best is not None:
                pct, d, s, v = best
                entry.update(build=v.get("name") or str(v.get("talents")), talents=v.get("talents"),
                             pct=round(pct, 1), d=round(d, 1), s=round(s, 2),
                             link=self._make_link(char, db, v.get("talents")))
                log("sims", "%s: WoWSims' %s build sims %.1f%% higher" % (char.get("name"), entry["build"], pct))
        with self.lock:
            checks = self._char(key)["checks"]
            checks[ck] = entry
            for k in sorted(checks, key=lambda k: checks[k].get("t") or 0)[:max(0, len(checks) - CHECK_KEEP)]:
                del checks[k]
        self._save()
        return entry

    def _run_check(self, char, ck, tag):
        """check_talents on the worker's thread: one that failed is tried again after RETRY_S, at most GIVE_UP times
        for WoWSims failing (runs that didn't finish in time, sim_compare.Late, don't count)."""
        try:
            self.check_talents(char, ck, tag)
            self.failed.pop(ck, None)
        except Exception as e:  # noqa: BLE001 - tried again after RETRY_S
            late = isinstance(e, sim_compare.Late)
            n = (self.failed.get(ck) or {}).get("n", 0) + (0 if late else 1)
            self.failed[ck] = {"t": self._now(), "n": n}
            log("sims", "couldn't check %s's talents%s: %s" % (char.get("name"), " yet" if late else "", e))
        finally:
            with self.lock:
                self._checking = None

    # ---- the worker ----------------------------------------------------------------------------------------------
    def start(self):
        def loop():
            while True:
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001 - the next poll tries again
                    log_once("sims.tick", e)
                time.sleep(REQUEST_POLL_S)
        threading.Thread(target=loop, daemon=True, name="sims").start()
        return self

    def tick(self, now=None, background=True):
        """One poll: answer the addon's sim request (GameState.sim_request), and every CHECK_POLL_S look at the
        character (other gear: the old one's sims and link go, even while WoWSims can't run; a gear key without a link:
        its link, once WoWSims' item data is in; the talent check, started on its own thread; background False: run
        here, the tests)."""
        now = self._now() if now is None else now
        st = self._state()
        if st is None:
            return
        self._requests(st, now)
        if self._check_t is not None and now - self._check_t < CHECK_POLL_S:
            return
        self._check_t = now
        s = st.summary() or {}
        if not s.get("name") or not simbuild.simmable(s):
            return
        char = self._character(st)
        if char is None:
            return
        fresh = self._seen(char)
        if not self.available():
            return
        if not fresh:
            db = self._db()
            if db is not None:                   # (the new gear's link, for the addon's /glide wowsims)
                self._link_for(char, db)
        ck = self._check_due(char, now)
        if ck is None:
            return
        tag = ck.split("|", 1)[0]
        with self.lock:
            self._checking = ck
        if background:
            threading.Thread(target=self._run_check, args=(char, ck, tag), daemon=True, name="sims-talents").start()
        else:
            self._run_check(char, ck, tag)

    def _requests(self, st, now):
        """The addon's sim request ("/glide sim <item link>": GameState.sim_request, each one once), answered as a
        spoken answer with the line and the link (emit)."""
        fn = getattr(st, "sim_request", None)
        try:
            req = fn() if callable(fn) else None
        except Exception as e:  # noqa: BLE001 - no request
            log_once("sims.request", e)
            return
        if not isinstance(req, dict):
            return
        iid = as_int(req.get("id"))
        if not iid:
            return
        v = {"kind": "item", "id": iid, "slot": None, "sfx": as_int(req.get("sfx")) or None,
             "ench": as_int(req.get("ench")) or None}
        text, url = self.answer(variants=[v])
        msg: dict = {"kind": "answer", "text": text, "speak": True, "model": "WoWSims", "t": now}
        field = link_field(url)
        if field:
            msg["link"] = field
        if self.emit is not None:
            try:
                self.emit(msg)
            except Exception as e:  # noqa: BLE001
                log_once("sims.emit", e)

    # ---- what the addon gets -------------------------------------------------------------------------------------
    def live_section(self):
        """live_data's per-character fields ({char key: {"link": url, "sim": {item id: [DPS change (0 within the sims'
        noise: "about the same"), random suffix without its sign (0: none), the inventory slot it went in, the worn
        item there it replaced (0: none), and for a ring, trinket or weapon what the other slot of the pair held (0:
        nothing)]}}}): the link /glide wowsims shows, and the asked item sims on the gear worn now at the WoWSims
        release in use (the tooltip's upgrade line says them, on that suffix only, while what they were set against is
        still worn)."""
        out, tag = {}, self._tag()
        with self.lock:
            for key, c in sorted(self.data["chars"].items()):
                e: dict = {}
                if c.get("link"):
                    e["link"] = c["link"]
                sim = {}
                for i, x in (c.get("sim") or {}).items():
                    slot = as_int(x.get("slot"))
                    if x.get("gk") != c.get("gk") or not tag or x.get("tag") != tag or not slot:
                        continue
                    d, s = float(x["d"]), float(x.get("s") or 0)
                    row = [0 if about_same(d, s) else d, abs(as_int(x.get("sfx")) or 0), slot,
                           as_int(x.get("over")) or 0]
                    if slot in PAIR_SLOT:
                        row.append(as_int(x.get("pair")) or 0)
                    sim[int(i)] = row
                if sim:
                    e["sim"] = sim
                if e:
                    out[key] = e
        return out
