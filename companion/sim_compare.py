"""Sims the player asks for (the coach's wowsims_sim tool, /glide sim with a linked item) and the coach's talent check:
WoWSims sims each variant of a level-60 character's setup (an item swapped in, or another talent build) on the
character's own gear, against what it has now (compare). sims.py asks; WoWSims' own builds come from simbuild.presets.

A variant is {"kind": "item", "id", "slot", "sfx", "ench"} or {"kind": "talents", "talents", "apl", "name"}:
- An item. slot: None (the item's own, from WoWSims' item data), a slot's name ("Head", "Ring", "Ring 2", "Trinket 1",
  "Main Hand", "Finger 1", ...) or the game's inventory slot. sfx: its random suffix (None or 0 on an item that has
  them: the SFX_TRY best by EP, the screen weights, are simmed and the best one kept, named in the label). ench: the
  enchant on it (None or 0: the worn item's moves over where it fits, as the BiS ranks have it). A ring or trinket goes
  where a unique or limitCategory clash puts it, else into an empty slot, else it is simmed in both slots and the better
  kept: it is set against the weaker of the two worn. A second copy of a ring or trinket worn in one slot (one that can
  be worn twice) goes in the other. A two-hander empties the off hand; a one-hander replacing a
  two-hander comes with the best off hand in the bags (the PARTNERS best by EP, simmed; none: alone, "alone"); an off
  hand replacing a two-hander, with the best one-hander in the bags (none: left out).
- A talent build: its talents string, simmed with apl (the rotation's path in WoWSims' repository); apl None: the
  rotation of WoWSims' build with those talents (simbuild.presets), else the tree's with the most points ("own_apl"
  False, so the answer can say so). The character's own talents are simmed with WoWSims' build's rotation when they are
  one of its builds, else with their tree's (simbuild.CLASSES): each build with its own rotation, since a build on
  another's rotation loses (WoWSims' Arcane Fire on the Arcane rotation: 11%).

A Result is {"label", "d", "s", "iters", "variant"} (the variant as given) and "base" (the character's DPS as simmed),
"dps", "pct" (d as a percent of base) and "tag" (the WoWSims release); an item's "id", "slot" (the inventory slot it went
in), "slot_name", "over" and "over_id" (the worn item it replaced, or None), "also_over" (an off hand a two-hander
empties), "sfx", "ench", "with" and "with_id" (the weapon in the other hand it was simmed with), "alone" (a one-hander
with nothing in the off hand), "worn" (worn already: that item in the slot asked, in its only slot, in both of a ring's
or trinket's slots, or in one when it can't be worn twice; no sims, d 0, s 0); a build's "talents", "apl" and "own_apl". d is the
DPS it gains, s its sigma: the unpaired bound sqrt(sd_base^2/n_base + sd_variant^2/n_variant), at least MIN_S (runs on
one seed are paired, so for stat items the true spread is smaller: s never claims less than it is).

How (one seed, SEED, for every run: every variant meets the same fights):
1. The base and each variant's options at QUICK iterations; each variant keeps its best option.
2. The base and each variant within NEAR sigma of the leader, again at FULL iterations, the leader first.
3. At budget_s whatever hasn't finished is dropped (a run already going finishes in the queue and is kept): a variant
   keeps what step 1 gave it; one whose step-1 run didn't finish is left out, or, at a priority other than PRIO_ASKED,
   Late is raised (a background caller tries again later: a check never keeps a build left out for time). budget_s
   None: no deadline (a background caller waits for the queue, which waits for the player).
Runs go through the sim queue (sim_queue.SimQueue) at prio: PRIO_ASKED runs at once, at normal process priority on half
the cores; another priority runs in the background. Every run that finishes is kept (the CACHE_KEEP newest, keyed by
the WoWSims release, the request without its sim options, the iterations and the seed): the base is simmed once per
gear, and a sim asked again is answered without one.

A variant that can't be simmed (an item WoWSims doesn't know or the class can't wear, a run that failed) is left out and
logged; when every one is, SimError says why the first was. SimError too when the character can't be simmed at all (no
WoWSims, a class or level WoWSims doesn't sim, no gear or talents, a base run that failed); Late (a SimError) when the
base run didn't finish in time.
"""
import collections
import concurrent.futures
import hashlib
import json
import math
import re
import threading
import time

import bis
import item_db
import simbuild
from log import log, log_once
from sim_queue import PRIO_ASKED
from simbuild import SimError
from state import as_int

SEED = 1
QUICK, FULL = 1000, 10000               # iterations: every variant, then those near the leader
NEAR = 3.0                              # sigma from the leader that still gets FULL iterations
MIN_S = 0.15                            # the least s a gain is given (as bis.py)
SFX_TRY = 3                             # random suffixes simmed on an item whose suffix isn't given
PARTNERS = 3                            # bag weapons tried in the other hand
CACHE_KEEP = 256                        # finished runs kept
ROT_KEEP = 8                            # rotations kept
CUSTOM = "those talents"                # a talent build without a name
# the suffix and partner screen when no weights are given ({code: EP}, against spell damage): rough, the sims decide
SCREEN = {"MAGE": {"SP": 1.0, "HT": 1.1, "IN": 0.85, "CR": 0.65, "SI": 0.4, "MR": 0.4, "AC": 0.5, "FI": 0.5,
                   "FR": 0.5, "CS": 9.0, "HS": 11.0}}
# a slot's name as the player or the coach says it -> the game's inventory slot
SLOT_WORDS = {"head": 1, "neck": 2, "shoulder": 3, "shoulders": 3, "back": 15, "cloak": 15, "chest": 5, "wrist": 9,
              "wrists": 9, "bracers": 9, "hands": 10, "gloves": 10, "waist": 6, "belt": 6, "legs": 7, "feet": 8,
              "boots": 8, "ring 1": 11, "finger 1": 11, "ring 2": 12, "finger 2": 12, "trinket 1": 13,
              "trinket 2": 14, "main hand": 16, "mainhand": 16, "weapon": 16, "off hand": 17, "offhand": 17,
              "ranged": 18, "wand": 18}
WORD_GROUP = {"ring": "Ring", "rings": "Ring", "finger": "Ring", "fingers": "Ring", "trinket": "Trinket",
              "trinkets": "Trinket"}             # either of the two slots
_APL_PATH = re.compile(r"^ui/specs/[\w./-]+\.apl\.(?:json|siml)$")
_TALENTS = re.compile(r"^[0-9]*(?:-[0-9]*){0,2}$")

_runs = collections.OrderedDict()       # (tag, request hash, iterations, seed) -> {"dps", "stdev", "iters"}
_rots = collections.OrderedDict()       # (tag, path) -> rotation
_lock = threading.Lock()
_clock = time.monotonic


class _Skip(Exception):
    """A variant that can't be simmed (why, for the player and the log)."""


class Late(SimError):
    """Runs that didn't finish within the budget: the base's, or (at a priority other than PRIO_ASKED) a variant's
    first. Not WoWSims failing: a background caller tries again later."""


def clear():
    """Forget the kept runs and rotations (a test's fresh start)."""
    with _lock:
        _runs.clear()
        _rots.clear()


# ---- rotations and builds -------------------------------------------------------------------------------------------
def rotation(ws, path, tag=None):
    """The rotation (APL protojson) at a path in WoWSims' repository at the release (tag; None: the one in use): an
    .apl.json as it is, an .apl.siml converted by the runner (wowsims.WoWSims.siml_parse). Kept (ROT_KEEP): don't
    change it. Raises SimError (wowsims.FetchError when it can't be had)."""
    tag = tag or ws.current()
    if not isinstance(path, str) or not _APL_PATH.match(path) or ".." in path.split("/"):
        raise SimError("not a WoWSims rotation: %r" % (path,))
    k = (tag, path)
    with _lock:
        if k in _rots:
            _rots.move_to_end(k)
            return _rots[k]
    if path.endswith(".apl.siml"):
        rot = ws.siml_parse(path, tag)
    else:
        try:
            rot = json.loads(ws.file(path, tag).decode("utf-8-sig"))
        except ValueError as e:
            raise SimError("WoWSims' %s isn't JSON" % path) from e
    if not isinstance(rot, dict):
        raise SimError("WoWSims' %s holds no rotation" % path)
    with _lock:
        _rots[k] = rot
        while len(_rots) > ROT_KEEP:
            _rots.popitem(last=False)
    return rot


def norm_talents(t):
    """A talents string as WoWSims writes it (each tree's trailing zeroes, and trailing dashes, trimmed), or ""."""
    t = str(t or "").strip()
    if not _TALENTS.match(t):
        return ""
    return "-".join(run.rstrip("0") for run in t.split("-")).rstrip("-")


def tree_of(conf, talents):
    """The talent tree (simbuild.CLASSES[cls]["trees"]) a talents string has the most points in (the first on a tie)."""
    runs = (norm_talents(talents).split("-") + [""] * len(conf["trees"]))[:len(conf["trees"])]
    pts = [sum(int(c) for c in run) for run in runs]
    return conf["trees"][pts.index(max(pts))]


def builds(cls, ws, tag=None):
    """WoWSims' talent builds for the class (simbuild.presets), {} when they can't be had (logged once)."""
    try:
        return simbuild.presets(cls, ws, tag)
    except Exception as e:  # noqa: BLE001 - offline, a presets.ts the pattern can't read: the trees' rotations
        log_once("sim_compare.presets", e)
        return {}


def talent_variants(char, ws, tag=None):
    """A talents variant for each of WoWSims' builds for the character's class whose talents aren't the character's
    (what the talent check compares): [{"kind": "talents", "talents", "apl", "name"}]."""
    cls = str((char or {}).get("class") or "").upper()
    conf = simbuild.CLASSES.get(cls)
    if conf is None:
        return []
    own = norm_talents(simbuild.talents_string((char or {}).get("talents"), len(conf["trees"])))
    return [{"kind": "talents", "talents": t, "apl": apl, "name": name}
            for name, (t, apl) in builds(cls, ws, tag).items() if norm_talents(t) and norm_talents(t) != own]


# ---- items ------------------------------------------------------------------------------------------------------------
def _slot(it):
    """The inventory slot of a strip gear entry ("16", "16.2" -> 16), or 0."""
    return as_int(str((it or {}).get("key") or 0).split(".")[0], 0) or 0


def _rec(db, iid):
    return (db.item(iid) if db is not None else None) or {}


def item_name(db, iid, sfx=0):
    """The item's name with its suffix ("Master's Cloak of Intellect"), or "item <id>"."""
    name = db.name(iid) if db is not None else None
    if db is None or not name:
        return "item %s" % iid
    suffix = (db.suffixes.get(sfx) or db.suffixes.get(abs(sfx)) or {}) if sfx else {}
    return "%s %s" % (name, suffix["name"]) if suffix.get("name") else name


def _group(db, iid):
    """The item's slot group (bis.GROUPS) by WoWSims' item data, or None."""
    rec = _rec(db, iid)
    if rec.get("type") == item_db.WEAPON:
        return "Off Hand" if rec.get("handType") == item_db.OFF_HAND else "Main Hand"
    return bis.TYPE_GROUP.get(as_int(rec.get("type"), 0) or 0)


def _two_hand(db, it):
    """Whether a gear entry (or an item id) is a two-hander: WoWSims' item data, else the strip's equip location."""
    iid = as_int(it.get("id") if isinstance(it, dict) else it)
    rec = _rec(db, iid)
    if rec:
        return rec.get("handType") == item_db.TWO_HAND
    return isinstance(it, dict) and it.get("loc") == bis.TWO_HAND_LOC


def _clash(db, a, b):
    """Can't be worn together: the same unique item, or one limitCategory (WoWSims gives no count: one; so two of the
    same item in one either)."""
    ra, rb = _rec(db, a), _rec(db, b)
    la = ra.get("limitCategory")
    if a == b:
        return bool(ra.get("unique") or la)
    return bool(la) and la == rb.get("limitCategory")


def _ep(db, iid, sfx, w):
    total = sum(float(v) * float(w.get(c) or 0) for c, v in db.stats(iid).items())
    if sfx:
        total += sum(float(v) * float(w.get(c) or 0) for c, v in db.suffix_stats(iid, sfx).items())
    return total


def _enchant_fits(db, eid, slot, iid, enchanter):
    """Whether an enchant moves onto another item in the slot (bis.Ranking.enchant_fits): a ring's only for an
    enchanter; a two-hand, staff, shield or frill enchant only onto one. One WoWSims doesn't know moves (the request
    leaves it out)."""
    if slot in bis.RING_SLOTS and not enchanter:
        return False
    if db is None:
        return True
    t = item_db.INV_TO_TYPE.get(slot)
    recs = [e for e in db.enchants.get(int(eid)) or () if t in [e.get("type")] + list(e.get("extraTypes") or ())]
    return not recs or any(bis.Ranking.enchant_on(e, _rec(db, iid)) for e in recs)


def _held(wm, slots, iid, sfx, ench):
    """The slots (of the item's group) that hold this very item: its id, and its random suffix and enchant when given."""
    return [s for s in slots if wm.get(s) and as_int(wm[s].get("id")) == iid
            and (not sfx or abs(as_int(wm[s].get("suffix"), 0) or 0) == abs(sfx))
            and (not ench or ench == as_int(wm[s].get("enchant"), 0))]


def _worn_in(db, iid, slots, held, fixed):
    """The slot where the item asked is worn already (nothing to sim), or None: the slot asked holds it (or the other
    one, and it can't be worn twice), or its only slot does, or both of a ring's or trinket's slots, or one of them and
    it can't be worn twice (_clash; an item WoWSims' item data doesn't know is taken as unique). A second copy of one
    that can goes in the other slot."""
    if not held:
        return None
    once = not _rec(db, iid) or _clash(db, iid, iid)
    if fixed and fixed[1]:
        return fixed[1] if fixed[1] in held else held[0] if once else None
    if len(held) == len(slots) or once:
        return held[0]
    return None


def worn_already(char, v, db=None):
    """Whether an item variant ({"id", "slot"?, "sfx"?, "ench"?}) is worn already as compare has it (_worn_in): nothing
    to sim. Its slot group by WoWSims' item data, else by the slot asked, else by where a copy of it is worn."""
    iid = as_int((v or {}).get("id"))
    if not iid:
        return False
    wm = {_slot(it): it for it in (char or {}).get("worn") or () if _slot(it) in bis.SLOT_GROUP and as_int(it.get("id"))}
    try:
        fixed = _parse_slot(v.get("slot"))
    except _Skip:
        return False
    group = _group(db, iid) or (fixed[0] if fixed else None) or \
        next((bis.SLOT_GROUP[s] for s, it in sorted(wm.items()) if as_int(it.get("id")) == iid), None)
    slots = bis.GROUP_SLOTS.get(group or "") or ()
    sfx, ench = as_int(v.get("sfx"), 0) or 0, as_int(v.get("ench"), 0) or 0
    return _worn_in(db, iid, slots, _held(wm, slots, iid, sfx, ench), fixed) is not None


def _parse_slot(slot):
    """A variant's slot -> (group, inventory slot or None); None when it names none. Raises _Skip for a slot that
    isn't one."""
    if slot is None or str(slot).strip() == "":
        return None
    s = " ".join(str(slot).strip().lower().replace("-", " ").replace("_", " ").split())
    if s in WORD_GROUP:
        return WORD_GROUP[s], None
    n = int(s) if s.isdigit() else SLOT_WORDS.get(s)
    if n is None or n not in bis.SLOT_GROUP:
        raise _Skip("there's no %s slot" % slot)
    return bis.SLOT_GROUP[n], n


class _Char:
    """What every variant of one compare shares: the character, its gear and talents, the base rotation."""

    def __init__(self, char, ws, db, weights):
        self.char = dict(char or {})
        self.cls = str(self.char.get("class") or "").upper()
        conf = simbuild.CLASSES.get(self.cls)
        if conf is None:
            raise SimError("WoWSims has no sim for %s here" % (self.cls.capitalize() or "this class"))
        self.conf: dict = conf
        level = as_int(self.char.get("level"))
        if level is not None and level != 60:
            raise SimError("WoWSims sims level 60 characters only")
        self.tag = ws.current() if ws is not None else None
        if not self.tag:
            raise SimError("WoWSims isn't installed yet")
        self.ws, self.db = ws, db
        self.worn = list(self.char.get("worn") or ())
        self.wm = {_slot(it): it for it in self.worn if _slot(it) in bis.SLOT_GROUP and as_int(it.get("id"))}
        if not self.wm:
            raise SimError("no gear to sim")
        self.talents = norm_talents(simbuild.talents_string(self.char.get("talents"), len(self.conf["trees"])))
        if not self.talents:
            raise SimError("no talents to sim")
        self.enchanter = simbuild.enchanter(self.char.get("skills"))
        self.w = dict(weights or SCREEN.get(self.cls) or {})
        self.ch = {"class": self.cls, "level": 60, "faction": self.char.get("faction")}
        self.builds = builds(self.cls, ws, self.tag)
        self.tree_apl = self.conf["apl"][simbuild.spec_of(self.cls, self.char.get("talents"))]
        own = self.build_for(self.talents)
        self.apl = own[1] if own else self.tree_apl
        try:
            self.rot = rotation(ws, self.apl, self.tag)
        except SimError:
            if self.apl == self.tree_apl:
                raise
            self.apl = self.tree_apl                     # (the build's rotation can't be had: the tree's)
            self.rot = rotation(ws, self.apl, self.tag)

    def build_for(self, talents):
        """(name, apl path) of WoWSims' build with these talents, or None."""
        return next(((name, apl) for name, (t, apl) in self.builds.items() if norm_talents(t) == talents), None)

    def request(self, opt, iters):
        """The RaidSimRequest of an option ({"worn"?, "talents"?, "rot"?}; {}: the base)."""
        worn = opt.get("worn")
        req = simbuild.request(dict(self.char, worn=self.worn if worn is None else worn), opt.get("rot") or self.rot,
                               iters, SEED, db=self.db)
        if opt.get("talents"):
            req["raid"]["parties"][0]["players"][0]["talentsString"] = opt["talents"]
        return req

    # ---- an item's options -----------------------------------------------------------------------------------------
    def entry(self, iid, sfx, ench, slot):
        """The item as worn in the slot: its suffix, and its enchant (none given: the worn one there, when it fits)."""
        e = {"key": str(slot), "id": iid}
        if sfx:
            e["suffix"] = sfx
        old = as_int((self.wm.get(slot) or {}).get("enchant"), 0) or 0
        if ench:
            e["enchant"] = ench
        elif old and _enchant_fits(self.db, old, slot, iid, self.enchanter):
            e["enchant"] = old
        return e

    def worn_with(self, move):
        """The worn gear with a move ({slot: entry, or None to empty it}) made."""
        out = [it for it in self.worn if _slot(it) not in move]
        return out + [e for _, e in sorted(move.items()) if e]

    def partners(self, group, one_hand=False):
        """The PARTNERS best bag weapons of a group by EP (one-handers only, with one_hand) that the character wears:
        [(id, suffix)]."""
        if self.db is None:
            return []
        seen, out = set(), []
        for it in self.char.get("bags") or ():
            iid = as_int(it.get("id"))
            if not iid or iid in seen or _group(self.db, iid) != group or not self.db.eligible(iid, self.ch):
                continue
            if one_hand and _two_hand(self.db, iid):
                continue
            seen.add(iid)
            sfx = as_int(it.get("suffix"), 0) or 0
            out.append((_ep(self.db, iid, sfx, self.w), iid, sfx))
        return [(iid, sfx) for _, iid, sfx in sorted(out, key=lambda x: (-x[0], x[1]))[:PARTNERS]]

    def item_plan(self, v):
        """An item variant's options to sim: {"options": [{"worn", "info"}]}, or {"same": info} when it is worn."""
        iid = as_int(v.get("id"))
        if not iid or iid <= 0:
            raise _Skip("no item to sim")
        db, rec = self.db, _rec(self.db, iid)
        name = item_name(db, iid)
        if db is not None and not rec:
            raise _Skip("WoWSims doesn't know %s" % name)
        if db is not None and rec and not db.eligible(iid, self.ch):
            raise _Skip("a %s can't wear %s" % (self.cls.lower(), name))
        fixed = _parse_slot(v.get("slot"))
        group = _group(db, iid) if rec else None
        if fixed and group and fixed[0] != group:
            raise _Skip("%s doesn't go in the %s slot" % (name, v.get("slot")))
        group = group or (fixed[0] if fixed else None) or \
            next((bis.SLOT_GROUP[s] for s, it in sorted(self.wm.items()) if as_int(it.get("id")) == iid), None)
        if not group:
            raise _Skip("which slot %s goes in isn't known" % name)
        slots = bis.GROUP_SLOTS[group]
        sfx, ench = as_int(v.get("sfx"), 0) or 0, as_int(v.get("ench"), 0) or 0
        held = _held(self.wm, slots, iid, sfx, ench)
        at = _worn_in(db, iid, slots, held, fixed)
        if at is not None:
            it = self.wm[at]
            return {"same": {"slot": at, "sfx": as_int(it.get("suffix"), 0) or 0,
                             "ench": as_int(it.get("enchant"), 0) or 0, "worn": True}}
        opts = rec.get("randomSuffixOptions") or ()
        if sfx:
            sfxs = [sfx]
        elif opts and db is not None:
            sfxs = [s for _, _, s in sorted((-_ep(db, iid, s, self.w), i, s) for i, s in enumerate(opts))[:SFX_TRY]]
        else:
            sfxs = [0]
        if fixed and fixed[1]:
            places = [fixed[1]]
        elif len(slots) == 1:
            places = list(slots)
        else:
            clash = [s for s in slots if s in self.wm and _clash(db, iid, as_int(self.wm[s].get("id")))]
            empty = [s for s in slots if s not in self.wm]
            places = clash[:1] or empty[:1] or [s for s in slots if s not in held]     # (a second copy: the other)
        mh, oh = self.wm.get(bis.MH), self.wm.get(bis.OH)
        extra, partners = {}, [None]
        if group == "Main Hand" and _two_hand(db, iid):
            extra[bis.OH] = None
        elif group == "Main Hand" and mh and _two_hand(db, mh) and not oh:
            partners = [(bis.OH, p) for p in self.partners("Off Hand")] or [None]
        elif group == "Off Hand" and mh and _two_hand(db, mh):
            partners = [(bis.MH, p) for p in self.partners("Main Hand", one_hand=True)]
            if not partners:
                raise _Skip("%s needs a one-hand weapon with it, and there's none in your bags" % name)
        options = []
        for s in sfxs:
            for place in places:
                for partner in partners:
                    e = self.entry(iid, s, ench, place)
                    move = dict(extra)
                    move[place] = e
                    info = {"slot": place, "sfx": s, "ench": e.get("enchant") or 0}
                    if partner is not None:
                        ps, (pid, psfx) = partner
                        move[ps] = self.entry(pid, psfx, 0, ps)
                        info.update({"with": item_name(db, pid, psfx), "with_id": pid})
                    elif group == "Main Hand" and mh and _two_hand(db, mh) and not oh and not extra:
                        info["alone"] = True
                    for gone in (s2 for s2 in move if s2 != place and s2 in self.wm and not move[s2]):
                        info["also_over"] = item_name(db, as_int(self.wm[gone].get("id")),
                                                      as_int(self.wm[gone].get("suffix"), 0) or 0)
                    options.append({"worn": self.worn_with(move), "info": info})
        return {"options": options}

    # ---- a talent build's ------------------------------------------------------------------------------------------
    def talent_plan(self, v):
        t = norm_talents(v.get("talents"))
        if not t:
            raise _Skip("no talents to sim")
        apl, name, own = v.get("apl"), v.get("name"), True
        if not apl:
            found = self.build_for(t)
            if found:
                name, apl = name or found[0], found[1]
            else:
                apl, own = self.conf["apl"][tree_of(self.conf, t)], False
        info = {"talents": t, "apl": apl, "own_apl": own, "label": str(name or CUSTOM)}
        if t == self.talents and apl == self.apl:
            return {"same": info}
        try:
            rot = rotation(self.ws, apl, self.tag)
        except SimError as e:
            raise _Skip(str(e)) from e
        return {"options": [{"talents": t, "rot": rot, "info": info}]}


# ---- runs -------------------------------------------------------------------------------------------------------------
def _key(tag, req, iters):
    body = json.dumps(dict(req, simOptions=None), sort_keys=True, separators=(",", ":"))
    return tag, hashlib.sha256(body.encode("utf-8")).hexdigest(), int(iters), SEED


def _keep(key, fut):
    if fut.cancelled() or fut.exception() is not None:
        return
    r = fut.result()
    with _lock:
        _runs[key] = {"dps": float(r["dps"]), "stdev": float(r.get("stdev") or 0.0), "iters": as_int(r.get("iters"), 0)}
        while len(_runs) > CACHE_KEEP:
            _runs.popitem(last=False)


class _Runs:
    """The runs of one compare: a kept run is answered at once; the same request is never queued twice."""

    def __init__(self, queue, tag, prio):
        self.queue, self.tag, self.prio = queue, tag, int(prio)
        self.background = self.prio != PRIO_ASKED
        self.futs = {}                                  # key -> Future
        self.mine = []                                  # the ones queued (dropped when the compare ends)

    def run(self, req, iters):
        """-> (key, Future for {"dps", "stdev", "iters"})."""
        key = _key(self.tag, req, iters)
        if key in self.futs:
            return key, self.futs[key]
        with _lock:
            got = _runs.get(key)
            if got is not None:
                _runs.move_to_end(key)
        if got is not None:
            f = concurrent.futures.Future()
            f.set_result(dict(got))
        else:
            f = self.queue.submit(req, self.prio, iterations=iters, seed=SEED, tag=self.tag,
                                  background=self.background)
            f.add_done_callback(lambda fut, key=key: _keep(key, fut))
            self.mine.append(f)
        self.futs[key] = f
        return key, f

    def drop(self):
        """Runs not started yet leave the queue (one going finishes, and is kept)."""
        for f in self.mine:
            if not f.done():
                f.cancel()


def _wait(futs, deadline):
    """Wait for the runs until the deadline (None: until they finish)."""
    pending = [f for f in futs if f is not None and not f.done()]
    if pending:
        concurrent.futures.wait(pending, timeout=None if deadline is None else max(0.0, deadline - _clock()))


def _outcome(f):
    """A finished run's {"dps", "stdev", "iters"}; None for one not finished or cancelled; its SimError raised."""
    if f is None or not f.done() or f.cancelled():
        return None
    e = f.exception()
    if e is not None:
        raise e if isinstance(e, SimError) else SimError("WoWSims couldn't sim: %s" % e)
    r = f.result()
    return {"dps": float(r["dps"]), "stdev": float(r.get("stdev") or 0.0), "iters": as_int(r.get("iters"), 0) or 0}


def _gain(b, v, iters):
    nb, nv = max(1, b["iters"] or iters), max(1, v["iters"] or iters)
    return v["dps"] - b["dps"], max(math.sqrt(b["stdev"] ** 2 / nb + v["stdev"] ** 2 / nv), MIN_S)


def _what(v):
    if isinstance(v, dict) and v.get("kind") == "talents":
        return "talents %s" % v.get("talents")
    if isinstance(v, dict) and v.get("id") is not None:
        return "item %s" % v.get("id")
    return repr(v)[:60]


def _late(budget_s):
    return "WoWSims didn't finish in %g s" % float(budget_s) if budget_s is not None else "WoWSims didn't finish"


# ---- compare ----------------------------------------------------------------------------------------------------------
def compare(char, variants, queue, ws, db=None, budget_s=30, prio=PRIO_ASKED, weights=None):
    """Each variant simmed on the character against what it has now (the module says how): [Result], in the variants'
    order, without those that couldn't be simmed. char: {"class", "race", "faction", "talents" (the addon's list),
    "worn" (parse_item dicts), and "level", "skills", "bags", "name" when known} (bis.BisRanker.character's);
    queue: the sim_queue.SimQueue; ws: the wowsims.WoWSims (the release in use, the repository's files); db: WoWSims'
    item data (item_db.ItemDB), for items' slots, names, suffixes and enchants; budget_s: the seconds it may take;
    prio: the queue's priority; weights: the character's sim weights ({code: EP}: sim_weights' "w"), else SCREEN's
    (they only pick which suffixes and partners are simmed); budget_s None: no deadline. Raises SimError, or Late (the
    module says when)."""
    deadline = None if budget_s is None else _clock() + max(0.0, float(budget_s))
    if threading.current_thread() is getattr(queue, "_thread", None):
        raise RuntimeError("a queued sim can't wait for the queue")
    c = _Char(char, ws, db, weights)
    runs = _Runs(queue, c.tag, prio)
    try:
        return _compare(c, list(variants or ()), runs, deadline, budget_s)
    finally:
        runs.drop()


def _compare(c, variants, runs, deadline, budget_s):
    plans, why = [], []

    def skip(v, reason):
        why.append(str(reason))
        log("sim_compare", "left out %s: %s" % (_what(v), reason))

    for v in variants:
        try:
            if not isinstance(v, dict):
                raise _Skip("not a sim")
            kind = v.get("kind") or ("talents" if v.get("talents") else "item")
            p = c.talent_plan(v) if kind == "talents" else c.item_plan(v) if kind == "item" else None
            if p is None:
                raise _Skip("not a sim Glide knows: %s" % kind)
        except _Skip as e:
            skip(v, e)
            continue
        p.update(v=v, kind=kind)
        plans.append(p)
    if not plans:
        if why:
            raise SimError(why[0])
        return []
    if not any(p.get("options") for p in plans):         # (each worn already, or the build had: nothing to run)
        return [_result(c, dict(p, base={"dps": 0.0, "stdev": 0.0, "iters": 0})) for p in plans]

    # stage 1: the base, and each variant's options
    bkey, bfut = runs.run(c.request({}, QUICK), QUICK)
    for p in plans:
        for o in p.get("options") or ():
            try:
                k, o["f1"] = runs.run(c.request(o, QUICK), QUICK)
            except SimError as e:
                o["err"] = e
                continue
            o["same"] = k == bkey
    _wait([bfut] + [o.get("f1") for p in plans for o in p.get("options") or ()], deadline)
    b1 = _outcome(bfut)
    if b1 is None:
        raise Late(_late(budget_s))
    late = []
    for p in plans:
        p["base"] = b1
        if "same" in p:
            continue
        best = None
        for o in p["options"]:
            try:
                r = _outcome(o.get("f1")) if not o.get("err") else None
            except SimError as e:
                o["err"] = e
                continue
            if r is None:
                continue
            d, s = (0.0, 0.0) if o.get("same") else _gain(b1, r, QUICK)
            if best is None or d > best[0]:
                best = (d, s, r, o)
        if best is None:
            err = next((o["err"] for o in p["options"] if o.get("err")), None)
            if err is None:
                late.append(p["v"])
            skip(p["v"], err or _late(budget_s))
            p["out"] = True
            continue
        p["d"], p["s"], p["r"], p["opt"] = best
    if late and runs.background:
        raise Late("%s (%d of %d left out)" % (_late(budget_s), len(late), len(plans)))

    # stage 2: those near the leader, again with FULL iterations
    live = [p for p in plans if "d" in p and not p["opt"].get("same")]
    if live and (deadline is None or _clock() < deadline):
        lead = max(live, key=lambda p: p["d"])
        near = sorted((p for p in live if lead["d"] - p["d"] <= NEAR * math.hypot(lead["s"], p["s"])),
                      key=lambda p: -p["d"])
        _, bfut2 = runs.run(c.request({}, FULL), FULL)
        for p in near:
            try:
                _, p["f2"] = runs.run(c.request(p["opt"], FULL), FULL)
            except SimError:
                continue
        _wait([bfut2] + [p.get("f2") for p in near], deadline)
        try:
            b2 = _outcome(bfut2)
        except SimError as e:
            log_once("sim_compare.full", e)
            b2 = None
        for p in near if b2 is not None else ():
            try:
                r = _outcome(p.get("f2"))
            except SimError as e:
                log_once("sim_compare.full", e)
                r = None
            if r is not None:
                p["d"], p["s"] = _gain(b2, r, FULL)
                p["r"], p["base"] = r, b2

    out = []
    for p in plans:
        if p.get("out"):
            continue
        out.append(_result(c, p))
    if not out and why:
        raise SimError(why[0])
    return out


def _result(c, p):
    base = p["base"]["dps"]
    if "same" in p:
        info, d, s, iters, dps = dict(p["same"]), 0.0, 0.0, 0, base
    else:
        info, d, s, iters, dps = dict(p["opt"]["info"]), p["d"], p["s"], p["r"]["iters"], p["r"]["dps"]
        if p["opt"].get("same"):
            iters = 0
    v = p["v"]
    res = {"variant": dict(v), "d": round(d, 2), "s": round(s, 2), "iters": int(iters or 0), "base": round(base, 1),
           "dps": round(dps, 1), "pct": round(100.0 * d / base, 1) if base > 0 else 0.0, "tag": c.tag}
    if p["kind"] == "talents":
        res["label"] = info.pop("label")
        res.update(info)
        return res
    iid = as_int(v.get("id"))
    slot = as_int(info.get("slot"), 0) or 0
    over = c.wm.get(slot)
    res.update(label=item_name(c.db, iid, info.get("sfx") or 0), id=iid, slot=slot,
               slot_name=bis.SLOT_NAME.get(slot), sfx=info.get("sfx") or 0, ench=info.get("ench") or 0,
               over=item_name(c.db, as_int(over.get("id")), as_int(over.get("suffix"), 0) or 0) if over else None,
               over_id=as_int(over.get("id")) if over else None)
    for k in ("with", "with_id", "alone", "also_over", "worn"):
        if info.get(k):
            res[k] = info[k]
    return res
