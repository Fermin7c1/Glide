"""Glide's own best-in-slot ranks: where each item ranks for a level-60 character, worked out by sims of its own gear
and talents (WoWSims, through sim_queue.SimQueue), the next upgrade, and the enchant a worn item lacks. The addon's
item tooltips say an item's rank (live_section: Data/Live.lua's chars[key].bis, read at login and /reload: "Glide BiS
#2 for Head, +6.1 DPS: ..."), and the coach names the next upgrade and a missing enchant (gear.set_bis through
on_change, rules._bis; what it said is kept here: told / mark_told).

Every POLL_S the worker reads the game state. A character WoWSims sims (simbuild.simmable) is ranked again when its
gear key (the worn items with their enchants and random suffixes, talents, race, whether it enchants, the lens)
changed and stayed so for SETTLE_S, or when the WoWSims release in use changed: with weights for the spec its talents
make (an older spec's value other stats), once the weights of the gear worn now came in (sim_weights.SimWeights.key;
they screen the pool) or WEIGHTS_WAIT_S after the gear settled without them. Gear worn again that was ranked this
session (a mount trinket on and off: the RECENT_KEEP last ranks replaced, per character) gets its ranks back without
sims. One character at a time: the one playing. New bag items it can wear are simmed on their own once they stay
SETTLE_S (no full rank). Ranking (rank):
1. Pool: every item of the character's level band (item_db.ItemDB.band: the other side's PvP gear left out) it can
   wear at its level, in the lens (lens_fn, the gear.bis_lens setting: "now", what WoWSims places in the game now, its
   phase 1; "all" adds the phase it calls Unknown), by slot group (GROUPS); what it wears; every bag item it can wear.
   A candidate is an item with a random suffix (ck): one with random suffixes is in the pool with its best one also
   when another of its suffixes is worn or in the bags.
2. EP screen with its WoWSims weights (sim_weights.py, weights_fn): an item with random suffixes is scored with its
   best one (and simmed with it: "sfx"), on-use stats count for their duration over their cooldown.
3. Candidates per group: what is worn, every bag item, and of what the character can have now (can_have: avail "now",
   as step 6 has it, and no profession it lacks; skills unknown: lacks) the TOP_EP best by EP and up to TOP_FX more
   with item effects whatever their EP (EP can't see them). Of the TOP_EP and TOP_FX of the whole pool, the ones it
   can't have now (no source, a later phase, a profession) are candidates too: simmed on their own (step 4) and kept
   out of the descent (step 5), so a source or a skill found later ranks them without sims, and what can't be had
   never takes the place of what can.
4. Each candidate swapped into the worn gear and simmed against it. The worn enchant moves to the new item when it
   fits it (a two-hand, staff or shield enchant only onto one); a ring enchant counts only for an enchanter (skills_fn:
   the recorder's professions; unknown: not), as WoWSims has it. A ring or trinket replaces the worn one that adds
   less in the sim, or the one it can't be worn with (both unique, or one limitCategory: WoWSims gives no count, so
   one). A one-hander replacing a two-hander comes with the best off hand the character can have now (worn, in the
   bags, or avail "now" with no profession it lacks: can_have), an off hand with the best such one-hander ("with": its
   d is the pair's, and the tooltip and the coach name the partner). A stat-only swap: BLOCKS paired blocks of
   BLOCK_ITERS iterations (seeds SEED + k*SEED_STEP, the worn gear once per block), d the mean gain,
   s = max(sd/sqrt(BLOCKS), MIN_S). One with item effects or a weapon (pairing barely helps them): LONG_ITERS
   iterations, s = max(sqrt(2)*stdev/sqrt(n), MIN_S), the unpaired bound.
5. Coordinate descent from the worn gear, up to PASSES passes, among what the character can have now (can_have: what
   it can't never makes the set, so it never decides how the rest ranks, as a hit-cap item no one sells did): each
   group's DESCENT_TOP best swaps (not clearly worse than what it wears: d > -2s) simmed in the gear as it stands (one
   paired block, s QUICK_S; or LONG_ITERS), the best kept when it gains more than 2s; for rings and trinkets the pairs
   among the PAIR_TOP best; then each item set (setId with 2 or more pieces among the candidates and the worn) as a
   bundle of its best k pieces, every k. That settles the hit cap and set bonuses: the result is the set ("set").
6. Ranks per group: the set's items first, then those the last pass simmed in the set (by their gain there), then the
   rest by d; one kept out of the descent goes before the first of them with a lower d. Only an item that can be had
   now (avail "now": a source on the player's side or binds when equipped, by sources_fn, research.Research.facts
   found for this side, else WoWSims' own; worn; in the bags) gets a rank; one with no known source ("nosrc") or of a
   later phase is kept unranked (rank 0) until research finds it a source.
7. Next upgrade: the largest d over 2s among items with a rank, not worn or in the bags, whose required profession
   (WoWSims' requiredProfession: "req") the character has (skills unknown: not met); a weapon's names its partner.
   New sources, bags and skills re-rank and re-pick it at every poll without new sims.
8. Enchants: each worn item without one (an addon from 0.22.0 sends the enchant when there is one; an older one never
   does, so rules._bis says it only from then) is simmed with the ENCH_TOP best enchants by EP for its slot that can
   be had (WoWSims' phase 1 and a source), the better one kept when it gains more than 2s ("enchants").

A rank's sims go through the queue at its "bis" priority (background: low process priority, a quarter of the cores,
waiting while the player fights, is dead or in an instance), the worn gear's runs once (the same gear on the same seed
is never simmed twice in a rank). A gear change (or another lens) while they run stops the rank, looked at before
every batch and while one waits (it starts again once the new gear settles); a batch whose worn-gear run fails stops at
once, its other runs dropped. A rank that fails is tried again RETRY_S after it failed, and after GIVE_UP failures
only with another WoWSims. WoWSims' item data is never loaded under the lock (that may download; the strip's capture
thread asks told()).

data/bis.json: {"v": 1, "chars": {key: CharBis}}, key "Name-Realm" when the engine knows the realm (spaces left out),
else "Name". CharBis: {"tag" (WoWSims), "gear_key", "dps" (the worn gear's), "t", "class", "spec", "faction",
"skills" ([profession], or null for unknown), "slots": {group: [{"id", "sfx", "rank", "d", "s", "how" ("sim" or
"worn"), "avail" ("now", "later", "nosrc"), "req" (a profession, or null), "name" (with a suffix: the item's name
with it, "Master's Cloak of Intellect"), "bag": 1 (in the bags), "with" and "with_name" (the partner a weapon was
simmed with, and its name), "cond": 1 (a bonus vs a creature type or in an area, which counts nothing on WoWSims' raid
boss: the coach says so), "together": 1 (a set piece the best set holds with another new piece of its set, gaining
nothing alone)}]} (an item ranked with two random suffixes: two entries), "set": {slot name: id}, "next": {"id",
"slot", "d", "s", "sfx", "name" (with a suffix), "with", "with_name" and "cond" (when so), "avail", "source" (where it
comes from, as the tooltip says it)} or null, "enchants": {inventory slot: {"slot", "item", "id", "name", "d", "s"}},
"told": {key: when}}.
"""
import concurrent.futures
import itertools
import json
import math
import os
import statistics
import threading
import time
from collections.abc import Callable, Iterable

import item_db
import item_text
import jsonfile
import sim_weights
import simbuild
from log import log, log_once
from research import WEB_NOTE
from state import as_int

POLL_S = 15
SETTLE_S = 120
RETRY_S = 600
GIVE_UP = 3
V = 1                                   # bis.json's layout and how ranks are worked out (in the gear key)
PRIO_BIS = 2                            # sim_queue.PRIO_BIS: after asked sims and reward choices, before weights
SEED = 1
SEED_STEP = 1000003                     # paired blocks this far apart share no iteration (iteration n: seed + n)
BLOCKS, BLOCK_ITERS = 3, 1000           # a stat-only swap
LONG_ITERS = 10000                      # a swap with item effects, or of a weapon
MIN_S = 0.15                            # the least s a gain is given
QUICK_S = 0.4                           # s of one paired block (the descent; measured: 0.28 at 2,000 iterations)
TOP_EP, TOP_FX = 5, 3                   # candidates per group: by EP; and with item effects, whatever their EP
DESCENT_TOP, PAIR_TOP = 3, 4            # the descent's swaps per group; ring and trinket pairs among the best
PASSES = 2
ENCH_TOP = 2                            # enchants simmed on a worn item without one
TOLD_KEEP = 64                          # told-once keys kept per character (the newest)
RECENT_KEEP = 3                         # finished ranks kept per character this session (gear worn again: no sims)
WEIGHTS_WAIT_S = 900                    # how long a settled gear waits for its own weights before ranking without
LENSES = ("now", "all")
FISHING_POLE = (2, 20)                  # item class, subclass
ENCHANTING = "Enchanting"

# slot groups (the tooltip's "for <group>"), each with the game's inventory slots it fills
GROUPS = (("Head", (1,)), ("Neck", (2,)), ("Shoulder", (3,)), ("Back", (15,)), ("Chest", (5,)), ("Wrist", (9,)),
          ("Hands", (10,)), ("Waist", (6,)), ("Legs", (7,)), ("Feet", (8,)), ("Ring", (11, 12)),
          ("Trinket", (13, 14)), ("Main Hand", (16,)), ("Off Hand", (17,)), ("Ranged", (18,)))
GROUP_SLOTS = dict(GROUPS)
SLOT_GROUP = {s: g for g, slots in GROUPS for s in slots}
SLOT_NAME = {**{s: g for g, slots in GROUPS for s in slots if len(slots) == 1},
             11: "Ring 1", 12: "Ring 2", 13: "Trinket 1", 14: "Trinket 2"}
TYPE_GROUP = {1: "Head", 2: "Neck", 3: "Shoulder", 4: "Back", 5: "Chest", 6: "Wrist", 7: "Hands", 8: "Waist",
              9: "Legs", 10: "Feet", 11: "Ring", 12: "Trinket", 14: "Ranged"}        # WoWSims' ItemType; 13 weapons
RING_SLOTS = (11, 12)
MH, OH = 16, 17
TWO_HAND_LOC = "INVTYPE_2HWEAPON"
# WoWSims' EnchantType -> the WeaponType it needs (two-hand: a two-handed item; 3 kits and 0 go on any)
ENCH_TWO_HAND, ENCH_NEEDS = 1, {2: 7, 4: 8, 5: 5}                                # shield, staff, off-hand frill
HELD = (5, 7)                                                                   # WeaponType frill, shield


class _Load:
    """source_text's db not given: loaded there (never under the lock)."""


_LOAD = _Load()


class _Abort(Exception):
    """The gear changed while a rank's sims ran (the rank starts again once it settles)."""


def char_key(char):
    """The key a character's data is kept under: "Name-Realm" when the realm is known (its spaces left out), else
    "Name"."""
    name = str((char or {}).get("name") or "")
    realm = str((char or {}).get("realm") or "").replace(" ", "")
    return "%s-%s" % (name, realm) if name and realm else name


def _slot(it):
    """The inventory slot of a strip gear entry ("16", "16.2" -> 16), or 0."""
    try:
        return int(str(it.get("key") or 0).split(".")[0] or 0)
    except ValueError:
        return 0


def _gkey(gear):
    """A gear map ({slot: {"id", "enchant"?, "suffix"?}}) as a key: the same gear, the same key."""
    return tuple(sorted((s, e["id"], e.get("enchant") or 0, e.get("suffix") or 0) for s, e in gear.items() if e))


def ck(c):
    """A candidate's key: its item and the random suffix it is simmed with, without its sign (one item with two
    suffixes is two candidates)."""
    return c["id"], abs(c.get("sfx") or 0)


def ek(e):
    """A gear entry's key, as ck: its item and suffix (without its sign); None for no entry."""
    return (e["id"], abs(e.get("suffix") or 0)) if e else None


def _apply(gear, move):
    """The gear with a move ({slot: entry, or None to empty it}) made."""
    out = dict(gear)
    for s, e in move.items():
        if e:
            out[s] = e
        else:
            out.pop(s, None)
    return out


def enchant_name(name):
    """An enchant's name as a player says it: "Enchant Bracer - Greater Intellect" -> "Greater Intellect"."""
    n = str(name or "").strip()
    for dash in (" - ", " – "):
        if n.startswith("Enchant ") and dash in n:
            return n.split(dash, 1)[1].strip()
    return n


class Ranking:
    """One rank of one character (BisRanker.rank): the pool, the EP screen, the swaps, the descent, the enchants.
    rk: the BisRanker (its queue, request builder and stop check); w: the weights ({code: EP})."""

    def __init__(self, rk, char, db, w, rotation, tag, lens="now"):
        self.rk, self.char, self.db, self.w, self.rotation, self.tag, self.lens = (
            rk, char, db, w, rotation, tag, lens)
        skills = char.get("skills")
        self.enchanter = skills is not None and ENCHANTING in skills
        self.ch = {"class": char["class"], "level": as_int(char.get("level")) or 60, "faction": char.get("faction")}
        self.cls_id = (item_db.CLASS_GEAR.get(char["class"]) or {}).get("id")
        self.sim_char = {k: char.get(k) for k in ("name", "class", "race", "faction", "talents")}
        self.sim_char["skills"] = set(skills) if skills is not None else None
        self.runs = {}                  # (gear key, iterations, seed) -> Future: no gear simmed twice
        self.cands = {}                 # group -> [cand]
        self.by_k = {}                  # cand key (ck) -> cand
        self.single = {}                # cand key -> (d, s): its swap into the worn gear
        self.partner = {}               # cand key -> the weapon (a cand) it was simmed with
        self.worth = {}                 # ring / trinket slot -> what the worn one adds
        self.ctx = {}                   # group -> {cand key: d in the set, as the last pass simmed it}
        self._avail = {}                # id -> whether it can be had now (a weapon's partner must be)
        self.locs = {}                  # id -> the strip's equip location (a worn item WoWSims doesn't know)
        self.G0 = self.worn_gear()
        self.G = dict(self.G0)

    # ---- items ------------------------------------------------------------------------------------------------
    def rec(self, iid):
        return self.db.item(iid) or {}

    def ep_stats(self, stats):
        return sum(float(v) * float(self.w.get(code) or 0) for code, v in (stats or {}).items())

    def ep(self, iid, sfx=0):
        """An item's EP: its stats, its suffix's, and on-use stats for their duration over their cooldown."""
        rec = self.db.item(iid)
        if rec is None:
            return 0.0
        total = self.ep_stats(self.db.stats(iid))
        if sfx:
            total += self.ep_stats(self.db.suffix_stats(iid, sfx))
        for fx in rec.get("itemEffects") or ():
            use = fx.get("onUse") if isinstance(fx, dict) else None
            stats = ((fx.get("scalingOptions") or {}).get("0") or {}).get("stats") if use else None
            dur, cd = (fx.get("effectDurationMs") or 0), ((use or {}).get("cooldownMs") or 0)
            if stats and dur > 0 and cd > 0:
                total += self.ep_stats(item_db.codes(stats)) * min(1.0, dur / cd)
        return total

    def best_suffix(self, iid):
        """The random suffix worth most in EP on the item (0 for none)."""
        best, bv = 0, -math.inf
        for sfx in self.rec(iid).get("randomSuffixOptions") or ():
            v = self.ep_stats(self.db.suffix_stats(iid, sfx))
            if v > bv:
                best, bv = sfx, v
        return best

    def guess_suffix(self, iid, stats):
        """The suffix an item without one in the strip carries (an older addon, a bag item): the option whose stats,
        with the item's own, the strip's show; 0 when none does."""
        opts = self.rec(iid).get("randomSuffixOptions") or ()
        if not opts or not isinstance(stats, dict):
            return 0
        own, hits = self.db.stats(iid), []
        for sfx in opts:
            add = self.db.suffix_stats(iid, sfx)
            if add and all(abs(float(stats.get(c) or 0) - (own.get(c) or 0) - v) < 0.5 for c, v in add.items()):
                hits.append((len(add), sfx))
        return max(hits)[1] if hits else 0

    def fx(self, iid):
        """Swaps of it are simmed long: it has item effects, or it is a weapon."""
        rec = self.rec(iid)
        return bool(rec.get("itemEffects")) or rec.get("type") == item_db.WEAPON

    def two_hand(self, iid):
        rec = self.db.item(iid)
        if rec is not None:
            return rec.get("handType") == item_db.TWO_HAND
        return self.locs.get(iid) == TWO_HAND_LOC

    def group_of(self, iid):
        rec = self.rec(iid)
        if rec.get("type") == item_db.WEAPON:
            return "Off Hand" if rec.get("handType") == item_db.OFF_HAND else "Main Hand"
        return TYPE_GROUP.get(as_int(rec.get("type")) or 0)

    def clash(self, a, b):
        """Can't be worn together: the same unique item, or one limitCategory (count unknown: one)."""
        ra, rb = self.rec(a), self.rec(b)
        if a == b:
            return bool(ra.get("unique"))
        la = ra.get("limitCategory")
        return bool(la) and la == rb.get("limitCategory")

    def wearable(self, iid, level, lens=True):
        rec = self.db.item(iid)
        if rec is None or not self.db.eligible(iid, self.ch) or item_db.required_level(rec) > level:
            return False
        return not lens or self.lens == "all" or (rec.get("phase") or 1) != item_db.PHASE_LATER

    def cand(self, iid, group, sfx, worn=False, bag=False):
        rec = self.rec(iid)
        if sfx is None:
            sfx = self.best_suffix(iid) if rec.get("randomSuffixOptions") else 0
        prof = rec.get("requiredProfession")
        return {"id": iid, "group": group, "sfx": int(sfx or 0), "ep": self.ep(iid, sfx), "fx": self.fx(iid),
                "eff": bool(rec.get("itemEffects")), "ilvl": int((rec.get("scalingOptions") or {}).get("0", {})
                                                                  .get("ilvl") or 0),
                "req": (item_db.PROFESSIONS.get(prof) or "profession %s" % prof) if prof else None,
                "worn": worn, "bag": bag}

    # ---- the gear ---------------------------------------------------------------------------------------------
    def worn_gear(self):
        """{slot: {"id", "enchant"?, "suffix"?}}: what the character wears (a suffix the strip didn't send, guessed
        from its stats)."""
        gear = {}
        for it in self.char.get("worn") or ():
            slot, iid = _slot(it), as_int(it.get("id"))
            if slot not in SLOT_GROUP or not iid:
                continue
            self.locs[iid] = it.get("loc")
            e = {"id": iid}
            ench = as_int(it.get("enchant"))
            if ench:
                e["enchant"] = ench
            sfx = as_int(it.get("suffix"))
            if sfx is None:
                sfx = self.guess_suffix(iid, it.get("stats"))
            if sfx:
                e["suffix"] = sfx
            gear[slot] = e
        return gear

    def enchant_fits(self, eid, slot, iid):
        """Whether an enchant moves onto another item in the slot: a ring's only for an enchanter; a two-hand, staff,
        shield or frill enchant only onto one; a weapon enchant not onto a frill or shield. One WoWSims doesn't know
        moves (the request leaves it out)."""
        if slot in RING_SLOTS and not self.enchanter:
            return False
        t = item_db.INV_TO_TYPE.get(slot)
        recs = [e for e in self.db.enchants.get(int(eid)) or ()
                if t in [e.get("type")] + list(e.get("extraTypes") or ())]
        if not recs:
            return True
        return any(self.enchant_on(e, self.rec(iid)) for e in recs)

    @staticmethod
    def enchant_on(e, rec):
        et = e.get("enchantType") or 0
        if et == ENCH_TWO_HAND:
            return rec.get("handType") == item_db.TWO_HAND
        if et in ENCH_NEEDS:
            return rec.get("weaponType") == ENCH_NEEDS[et]
        return not (e.get("type") == item_db.WEAPON and rec.get("weaponType") in HELD)

    def entry(self, c, slot, gear):
        """The candidate as worn in the slot of this gear: its suffix, and the enchant there when it moves over."""
        old = gear.get(slot)
        if old and ek(old) == ck(c):
            return old
        e = {"id": c["id"]}
        if c.get("sfx"):
            e["suffix"] = c["sfx"]
        if old and old.get("enchant") and self.enchant_fits(old["enchant"], slot, c["id"]):
            e["enchant"] = old["enchant"]
        return e

    def value_in(self, gear, s):
        """What the item in a ring or trinket slot adds, about (the worn one: simmed; another: its gain on top)."""
        e = gear.get(s)
        if not e:
            return -math.inf
        if self.G0.get(s) == e:
            return self.worth.get(s, 0.0)
        return max(self.worth.values(), default=0.0) + self.single.get(ek(e), (0.0, 0.0))[0]

    def can_have(self, c):
        """Whether the character can have the candidate now: worn, in the bags, or with a source on its side (avail
        "now", as the ranks have it) and no profession it lacks (skills unknown: lacks). Only such an item is a
        partner, in the descent or in the best set (the module's steps 3 to 5)."""
        if c["worn"] or c["bag"]:
            return True
        skills = self.sim_char["skills"]
        if c["req"] and (skills is None or c["req"] not in skills):
            return False
        if c["id"] not in self._avail:
            self._avail[c["id"]] = self.rk.avail(c["id"], self.ch["faction"], self.db) == "now"
        return self._avail[c["id"]]

    def pick_partner(self, group, by, one_hand=False):
        """The weapon a one-hander (or an off hand) replacing a two-hander is worn with: the best of the other group
        (by EP, or by its simmed gain) that the character can have now (can_have); None for none."""
        best, bv = None, -math.inf
        for c in self.cands.get(group) or ():
            if (one_hand and self.two_hand(c["id"])) or not self.can_have(c):
                continue
            r = self.single.get(ck(c)) if by == "sim" else (c["ep"], 0.0)
            if r is not None and r[0] > bv:
                best, bv = c, r[0]
        return best

    def swap_move(self, gear, c, group, by="sim"):
        """The move that puts a candidate in its group's slot (see the module's step 4), or None (it is worn there,
        or can't be)."""
        slots = GROUP_SLOTS[group]
        if any(ek(gear.get(s)) == ck(c) for s in slots):
            return None
        if len(slots) == 2:
            clash = [s for s in slots if gear.get(s) and self.clash(c["id"], gear[s]["id"])]
            if len(clash) == 2:
                return None
            empty = [s for s in slots if not gear.get(s)]
            t = clash[0] if clash else empty[0] if empty else min(slots, key=lambda s: self.value_in(gear, s))
            return {t: self.entry(c, t, gear)}
        slot = slots[0]
        move = {slot: self.entry(c, slot, gear)}
        if group == "Main Hand":
            if self.two_hand(c["id"]):
                move[OH] = None
            elif gear.get(MH) and self.two_hand(gear[MH]["id"]) and not gear.get(OH):
                p = self.pick_partner("Off Hand", by)
                if p is not None:
                    move[OH] = self.entry(p, OH, gear)
        elif group == "Off Hand" and gear.get(MH) and self.two_hand(gear[MH]["id"]):
            p = self.pick_partner("Main Hand", by, one_hand=True)
            if p is None:
                return None
            move[MH] = self.entry(p, MH, gear)
        return move

    def partner_in(self, move, group):
        """The candidate a weapon's move brings along in the other hand (swap_move's partner), or None."""
        other = OH if group == "Main Hand" else MH if group == "Off Hand" else None
        e = move.get(other) if other else None
        return self.by_k.get(ek(e)) if e else None

    # ---- sims -------------------------------------------------------------------------------------------------
    def run(self, gear, iters, seed):
        """A Future for the gear's sim (one per gear, iterations and seed in a rank)."""
        k = (_gkey(gear), iters, seed)
        f = self.runs.get(k)
        if f is None:
            worn = []
            for s, e in sorted(gear.items()):
                it = {"key": str(s), "id": e["id"]}
                if e.get("enchant") and (s not in RING_SLOTS or self.enchanter):
                    it["enchant"] = e["enchant"]
                if e.get("suffix"):
                    it["suffix"] = e["suffix"]
                worn.append(it)
            req = self.rk.request(dict(self.sim_char, worn=worn), self.rotation, iters, seed, self.db)
            f = self.rk.queue.submit(req, PRIO_BIS, iterations=iters, seed=seed, tag=self.tag, background=True)
            self.runs[k] = f
        return f

    def cancel(self):
        """Every run of the rank not started yet dropped from the queue."""
        for f in self.runs.values():
            f.cancel()

    def check_stale(self):
        """Stop the rank (_Abort) when its gear changed (BisRanker.stale)."""
        if self.rk.stale():
            self.cancel()
            raise _Abort()

    @staticmethod
    def _failed(f):
        return f.done() and not f.cancelled() and f.exception() is not None

    def wait(self, futs, bases=()):
        """Wait for a batch of runs. A base run (the gear the batch is measured on) that fails stops it at once (the
        rest dropped, its error raised); so does a gear change (_Abort), looked at whenever a run fails and at least
        every wait_s."""
        pending = {f for f in futs if not f.done()}
        while True:
            for f in bases:
                if self._failed(f):
                    self.cancel()
                    raise f.exception()
            if not pending:
                return
            _, pending = concurrent.futures.wait(pending, timeout=self.rk.wait_s,
                                                 return_when=concurrent.futures.FIRST_EXCEPTION)
            if pending:
                self.check_stale()

    @staticmethod
    def outcome(f, base=False):
        """A run's {"dps", "stdev", "iters"}; a variant's that failed: None (logged once); the base's: raised."""
        try:
            r = f.result()
            return {"dps": float(r["dps"]), "stdev": float(r.get("stdev") or 0.0),
                    "iters": as_int(r.get("iters")) or 0}
        except concurrent.futures.CancelledError as e:
            raise _Abort() from e
        except Exception as e:  # noqa: BLE001 - one item WoWSims can't sim: left out
            if base:
                raise
            log_once("bis.sim", e)
            return None

    def is_fx(self, gear, other):
        for s in set(gear) | set(other):
            a, b = gear.get(s), other.get(s)
            if a != b and any(e and self.fx(e["id"]) for e in (a, b)):
                return True
        return False

    def measure(self, gear, moves, quick=False):
        """Each move's gain on the gear: [(d, s), or None for a move that is None or whose sim failed]. quick: one
        paired block for a stat-only move (the descent), else BLOCKS."""
        self.check_stale()                      # (a batch may take less than wait_s: the gear is looked at first)
        base, jobs, futs, bases = _gkey(gear), [], [], []

        def need(g, iters, seed):
            f = self.run(g, iters, seed)
            futs.append(f)
            if g is gear:
                bases.append(f)
            return f
        for m in moves:
            other = _apply(gear, m) if m is not None else None
            if other is None:
                jobs.append(None)
            elif _gkey(other) == base:
                jobs.append(("same",))
            elif self.is_fx(gear, other):
                jobs.append(("long", need(gear, LONG_ITERS, SEED), need(other, LONG_ITERS, SEED)))
            else:
                seeds = [SEED + k * SEED_STEP for k in range(1 if quick else BLOCKS)]
                jobs.append(("short", [(need(gear, BLOCK_ITERS, sd), need(other, BLOCK_ITERS, sd)) for sd in seeds]))
        self.wait(futs, bases)
        out = []
        for j in jobs:
            if j is None or j[0] == "same":
                out.append(None if j is None else (0.0, 0.0))
                continue
            if j[0] == "long":
                b, v = self.outcome(j[1], base=True), self.outcome(j[2])
                if b is None or v is None:
                    out.append(None)
                    continue
                sd, n = v["stdev"] or b["stdev"], max(1, v["iters"] or LONG_ITERS)
                out.append((v["dps"] - b["dps"], max(math.sqrt(2) * sd / math.sqrt(n), MIN_S)))
            else:
                diffs = []
                for fb, fv in j[1]:
                    b, v = self.outcome(fb, base=True), self.outcome(fv)
                    if b is None or v is None:
                        break
                    diffs.append(v["dps"] - b["dps"])
                if len(diffs) < len(j[1]):
                    out.append(None)
                    continue
                s = max(statistics.stdev(diffs) / math.sqrt(len(diffs)), MIN_S) if len(diffs) > 1 else QUICK_S
                out.append((statistics.fmean(diffs), s))
        return out

    def base_dps(self):
        """The worn gear's DPS: its long run, else the mean of its blocks."""
        k = _gkey(self.G0)
        long_ = self.runs.get((k, LONG_ITERS, SEED))
        if long_ is not None and long_.done() and not long_.cancelled() and not long_.exception():
            return float(long_.result()["dps"])
        vals = [float(f.result()["dps"]) for (g, n, _), f in self.runs.items()
                if g == k and n == BLOCK_ITERS and f.done() and not f.cancelled() and not f.exception()]
        return statistics.fmean(vals) if vals else 0.0

    # ---- the steps --------------------------------------------------------------------------------------------
    def pool(self):
        """{group: {cand key: cand}}: what is worn, the bag items it can wear, the band's items in the lens (an item
        with random suffixes with its best one, also when another of its suffixes is worn or in the bags)."""
        level = self.ch["level"]
        groups = {g: {} for g, _ in GROUPS}
        for slot, e in sorted(self.G0.items()):
            c = self.cand(e["id"], SLOT_GROUP[slot], e.get("suffix") or 0, worn=True)
            groups[SLOT_GROUP[slot]].setdefault(ck(c), c)
        worn = {ek(e) for e in self.G0.values()}
        for c in self.bag_cands(worn):
            groups[c["group"]].setdefault(ck(c), c)
        for iid in self.db.band(self.ch):
            if self.wearable(iid, level):
                g = self.group_of(iid)
                if not g:
                    continue
                c = self.cand(iid, g, None)
                if ck(c) not in worn and ck(c) not in groups[g]:
                    groups[g][ck(c)] = c
        return groups

    def bag_cands(self, skip):
        """The bag items the character can wear (any phase), as candidates; skip: cand keys left out."""
        out, seen = [], set(skip)
        for it in self.char.get("bags") or ():
            iid = as_int(it.get("id"))
            if not iid or not self.wearable(iid, self.ch["level"], lens=False):
                continue
            g = self.group_of(iid)
            if g:
                sfx = as_int(it.get("suffix"))
                c = self.cand(iid, g, sfx if sfx is not None else self.guess_suffix(iid, it.get("stats")), bag=True)
                if ck(c) not in seen:
                    seen.add(ck(c))
                    out.append(c)
        return out

    @staticmethod
    def _top(cs):
        """The TOP_EP first of candidates sorted by EP, and up to TOP_FX more with item effects (EP can't see them)."""
        return cs[:TOP_EP] + [c for c in cs[TOP_EP:] if c["eff"]][:TOP_FX]

    def candidates(self, groups):
        """Each group's candidates (the module's step 3): what is worn, the bag items, the _top of what the character
        can have now (can_have), then those of the whole pool's _top it can't (simmed on their own only)."""
        for g, by in groups.items():
            cs = list(by.values())
            free = sorted((c for c in cs if not c["worn"] and not c["bag"]),
                          key=lambda c: (-c["ep"], -c["ilvl"], c["id"]))
            pick = self._top([c for c in free if self.can_have(c)])
            pick += [c for c in self._top(free) if not self.can_have(c)]
            self.cands[g] = [c for c in cs if c["worn"]] + [c for c in cs if c["bag"]] + pick
            for c in self.cands[g]:
                self.by_k[ck(c)] = c

    def swaps(self, only=None):
        """Each candidate (only: these) swapped into the worn gear: self.single. First what each worn ring and
        trinket adds (which one a new one replaces)."""
        cs = [c for g, _ in GROUPS for c in self.cands.get(g) or () if only is None or c in only]
        if any(len(GROUP_SLOTS[c["group"]]) == 2 for c in cs) and not self.worth:
            rem = [s for g in ("Ring", "Trinket") for s in GROUP_SLOTS[g] if s in self.G0]
            for s, r in zip(rem, self.measure(self.G0, [{s: None} for s in rem])):
                self.worth[s] = -r[0] if r else 0.0
        moves, who = [], []
        for c in cs:
            if c["worn"]:
                self.single[ck(c)] = (0.0, 0.0)
                continue
            m = self.swap_move(self.G0, c, c["group"], by="ep")
            if m is not None:
                moves.append(m)
                who.append(c)
                p = self.partner_in(m, c["group"])
                if p is not None:
                    self.partner[ck(c)] = p
        for c, r in zip(who, self.measure(self.G0, moves)):
            if r is not None:
                self.single[ck(c)] = r

    def _ok(self, c):
        r = self.single.get(ck(c))
        return r is not None and (c["worn"] or r[0] > -2 * r[1])

    def _d(self, c):
        return self.single[ck(c)][0]

    def step(self, gear, group):
        """One group of the descent: its best swaps of what the character can have now (can_have) simmed in the gear;
        the best kept when it gains over 2s."""
        slots = GROUP_SLOTS[group]
        here = {ek(gear[s]) for s in slots if gear.get(s)}
        cs = sorted((c for c in self.cands.get(group) or () if self._ok(c) and self.can_have(c)),
                    key=lambda c: -self._d(c))
        moves, who = [], []
        if len(slots) == 2:
            top = cs[:PAIR_TOP]
            top += [self.by_k[k] for k in sorted(k for k in here if k is not None)
                    if k in self.by_k and self.by_k[k] not in top]
            a, b = slots
            for x, y in itertools.combinations(top, 2):
                if ck(x) == ck(y) or self.clash(x["id"], y["id"]):
                    continue
                if ek(gear.get(b)) == ck(x) or ek(gear.get(a)) == ck(y):
                    x, y = y, x
                moves.append({a: self.entry(x, a, gear), b: self.entry(y, b, gear)})
                who.append((x, y))
        else:
            for c in [c for c in cs if ck(c) not in here][:DESCENT_TOP]:
                m = self.swap_move(gear, c, group)
                if m is not None:
                    moves.append(m)
                    who.append((c,))
        if not moves:
            return gear
        res = self.measure(gear, moves, quick=True)
        ctx = {k: 0.0 for k in here}
        for cs_, r in zip(who, res):
            for c in cs_:
                if r is not None and ck(c) not in here:
                    ctx[ck(c)] = max(ctx.get(ck(c), -math.inf), r[0])
        self.ctx[group] = ctx
        best = max(((r, m) for r, m in zip(res, moves) if r is not None and r[0] > 2 * r[1]),
                   key=lambda rm: rm[0][0], default=None)
        return _apply(gear, best[1]) if best else gear

    def bundles(self):
        """Each item set with pieces in 2 or more groups among the candidates the character can have now (can_have)
        and the worn: its best k pieces (one per group, by d), for every k from 2."""
        sets = {}
        for g, cs in self.cands.items():
            for c in cs:
                sid = self.rec(c["id"]).get("setId")
                if sid and ck(c) in self.single and self.can_have(c):
                    sets.setdefault(sid, {}).setdefault(g, []).append(c)
        out = []
        for _, groups in sorted(sets.items()):
            if len(groups) < 2:
                continue
            pieces = sorted((max(cs, key=self._d) for cs in groups.values()), key=lambda c: -self._d(c))
            out += [pieces[:k] for k in range(2, len(pieces) + 1)]
        return out

    def bundle_step(self, gear, bundles):
        moves = []
        for pieces in bundles:
            move, tmp = {}, dict(gear)
            for c in pieces:
                m = self.swap_move(tmp, c, c["group"])
                if m:
                    move.update(m)
                    tmp = _apply(tmp, m)
            if move:
                moves.append(move)
        if not moves:
            return gear
        best = max(((r, m) for r, m in zip(self.measure(gear, moves, quick=True), moves)
                    if r is not None and r[0] > 2 * r[1]), key=lambda rm: rm[0][0], default=None)
        return _apply(gear, best[1]) if best else gear

    def descent(self):
        gear, bundles = dict(self.G0), self.bundles()
        for _ in range(PASSES):
            start = _gkey(gear)
            for g, _slots in GROUPS:
                gear = self.step(gear, g)
            gear = self.bundle_step(gear, bundles)
            if _gkey(gear) == start:
                break
        self.G = gear

    def enchant_options(self, slot, iid):
        """The ENCH_TOP best enchants by EP for a worn item in the slot that can be had (a ring's only for an
        enchanter)."""
        if slot in RING_SLOTS and not self.enchanter:
            return []
        t, rec, seen, out = item_db.INV_TO_TYPE.get(slot), self.rec(iid), set(), []
        for recs in self.db.enchants.values():
            for e in recs:
                if (e["effectId"] in seen or t not in [e.get("type")] + list(e.get("extraTypes") or ())
                        or not e.get("sources") or not self.enchant_on(e, rec)
                        or (self.lens != "all" and (e.get("phase") or 1) == item_db.PHASE_LATER)
                        or (e.get("classAllowlist") and self.cls_id not in e["classAllowlist"])):
                    continue
                ep = self.ep_stats(item_db.codes(e.get("stats") or (), e.get("pseudoStats")))
                if ep > 0:
                    seen.add(e["effectId"])
                    out.append((ep, e))
        out.sort(key=lambda pe: (-pe[0], pe[1]["effectId"]))
        return [e for _, e in out[:ENCH_TOP]]

    def enchants(self):
        """{slot: {"slot", "item", "id", "name", "d", "s"}}: the enchant worth most on each worn item without one."""
        moves, who = [], []
        for slot, e in sorted(self.G0.items()):
            if e.get("enchant"):
                continue
            for en in self.enchant_options(slot, e["id"]):
                moves.append({slot: dict(e, enchant=en["effectId"])})
                who.append((slot, en))
        out = {}
        for (slot, en), r in zip(who, self.measure(self.G0, moves)):
            if r is None or not r[0] > 2 * r[1]:
                continue
            k = str(slot)
            if k not in out or r[0] > out[k]["d"]:
                out[k] = {"slot": SLOT_GROUP[slot], "item": self.G0[slot]["id"], "id": en["effectId"],
                          "name": enchant_name(en.get("name")), "d": round(r[0], 2), "s": round(r[1], 2)}
        return out

    def entries(self, group, cs):
        out = []
        for c in cs:
            d, s = self.single[ck(c)]
            e = {"id": c["id"], "sfx": c["sfx"], "rank": 0, "d": round(d, 2), "s": round(s, 2),
                 "how": "worn" if c["worn"] else "sim", "avail": None, "req": c["req"]}
            if c["sfx"]:
                e["name"] = self.rk.item_name(c["id"], c["sfx"])     # (with the suffix it was simmed with)
            if c["bag"]:
                e["bag"] = 1
            p = self.partner.get(ck(c))
            if p is not None:                   # (its d is the pair's: the tooltip and the coach name the partner)
                e["with"], e["with_name"] = p["id"], self.rk.item_name(p["id"], p["sfx"])
            rec = self.rec(c["id"])
            sc = (rec.get("scalingOptions") or {}).get("0") or {}
            if rec.get("mobTypes") or rec.get("areaTypes") or sc.get("mobTypeStats") or sc.get("areaStats"):
                e["cond"] = 1                   # (its bonus vs a creature type or in an area: nothing on the boss)
            out.append(e)
        return out

    def result(self):
        """The CharBis's ranks: {"dps", "slots", "set", "enchants"} (avail, rank and next: BisRanker._rerank)."""
        slots = {}
        for g, gslots in GROUPS:
            cs = [c for c in self.cands.get(g) or () if ck(c) in self.single]
            if not cs:
                continue
            in_set = {ek(self.G[s]) for s in gslots if self.G.get(s)}
            ctx = self.ctx.get(g) or {}

            def order(c, in_set=in_set, ctx=ctx):
                if ck(c) in in_set:
                    return (0, -self._d(c), ck(c))
                if ck(c) in ctx:
                    return (1, -ctx[ck(c)], ck(c))
                return (2, -self._d(c), ck(c))
            xs = sorted((c for c in cs if self.can_have(c)), key=order)
            for c in sorted((c for c in cs if not self.can_have(c)), key=lambda c: (-self._d(c), ck(c))):
                at = next((i for i, x in enumerate(xs) if self._d(x) < self._d(c)), len(xs))
                xs.insert(at, c)                # (kept out of the descent: before the first with a lower d)
            slots[g] = self.entries(g, xs)
        self.mark_together(slots)
        return {"dps": round(self.base_dps(), 1), "slots": slots,
                "set": {SLOT_NAME[s]: e["id"] for s, e in sorted(self.G.items())}, "enchants": self.enchants()}

    def mark_together(self, slots):
        """Mark "together": 1 on a piece of an item set that the best set holds with another new piece of its set and
        that gains nothing beyond the noise on its own (its d, what it alone changes, is a loss: the gain is the
        set's). The tooltip says "with its set" for it instead of that d."""
        worn = {ek(e) for e in self.G0.values()}
        new = [k for k in map(ek, self.G.values()) if k is not None and k not in worn]
        by_set = {}
        for k in new:
            sid = self.rec(k[0]).get("setId")
            if sid:
                by_set.setdefault(sid, set()).add(k)
        for xs in slots.values():
            for x in xs:
                sid = self.rec(x["id"]).get("setId")
                if (x["how"] != "worn" and ck(x) in new and sid and len(by_set.get(sid, ())) > 1
                        and not x["d"] > 2 * x["s"]):
                    x["together"] = 1

    def go(self):
        self.candidates(self.pool())
        self.swaps()
        self.descent()
        return self.result()


class BisRanker:
    """The worker: start() polls in its own thread; tick() is one poll (the tests call it). queue: the
    sim_queue.SimQueue (its ws: the WoWSims runner, for the release in use and the spec's rotation); item_db_fn -> the
    item_db.ItemDB or None (research.ItemDBSource); state_fn -> the GameState; weights_fn(name) -> the character's
    sim_weights entry or None; sources_fn(item id) -> research's facts for it or None. Seams the app may set:
    skills_fn(name) -> the professions the character has (names) or None (unknown); enabled_fn -> the gear.bis
    setting (None: on); lens_fn -> "now" or "all" (gear.bis_lens; None: "now"); on_change(key, CharBis) after a rank
    or a re-pick (gear.set_bis); request_fn(char, rotation, iterations, seed, db) -> the request (None:
    simbuild.request)."""

    def __init__(self, home, queue, item_db_fn, state_fn, weights_fn, sources_fn=None, now=time.time):
        self.path = os.path.join(home, "data", "bis.json")
        self.queue, self.item_db_fn, self.state_fn, self.weights_fn, self.sources_fn = (
            queue, item_db_fn, state_fn, weights_fn, sources_fn)
        self._now = now
        self.ws = getattr(queue, "ws", None)
        self.skills_fn: Callable[[str], Iterable[str] | None] | None = None
        self.enabled_fn: Callable[[], object] | None = None
        self.lens_fn: Callable[[], str] | None = None
        self.on_change: Callable[[str, dict], object] | None = None
        self.request_fn: Callable[..., dict] | None = None
        self.wait_s = POLL_S
        self.lock = threading.RLock()
        self._rank_lock = threading.Lock()
        self.data = self._load()
        self.pending, self.pending_t = None, 0.0        # the gear key, and since when (settling)
        self.bag_pending, self.bag_t = None, 0.0        # new bag items, and since when
        self.bag_skip = set()                           # bag items whose sim failed (this session)
        self.failed = {}                                # gear key -> {"t", "tag", "n"}
        self.current = None                             # the key of the character playing, at the last poll
        self._watch = None                              # (key, gear key) a running rank stops for
        self._handed = None                             # the character whose ranks on_change had last
        self.recent = {}                                # key -> the RECENT_KEEP last ranks replaced (_reuse)
        self._rot = {}
        self.status = {"state": "idle", "error": None, "runs": 0}

    # ---- bis.json -----------------------------------------------------------------------------------------------
    def _load(self):
        try:
            d = jsonfile.read(self.path)
        except (OSError, ValueError):
            d = None
        chars = d.get("chars") if isinstance(d, dict) and d.get("v") == V else None
        out = {}
        for k, cb in (chars if isinstance(chars, dict) else {}).items():
            if not isinstance(k, str) or not isinstance(cb, dict):
                continue
            told = {str(t): v for t, v in cb["told"].items() if isinstance(v, (int, float))} \
                if isinstance(cb.get("told"), dict) else {}
            slots = cb.get("slots")
            ok = isinstance(slots, dict) and all(
                isinstance(xs, list) and all(isinstance(x, dict) and isinstance(x.get("id"), int)
                                             and isinstance(x.get("d"), (int, float))
                                             and isinstance(x.get("s"), (int, float)) for x in xs)
                for xs in slots.values())
            if ok:
                out[k] = dict(cb, told=told)
            elif told:
                out[k] = {"told": told}
        return {"v": V, "chars": out}

    def _save(self):
        with self.lock:
            jsonfile.write_atomic(self.path, self.data, ensure_ascii=False, separators=(",", ":"))

    # ---- seams --------------------------------------------------------------------------------------------------
    def request(self, char, rotation, iterations, seed, db):
        if self.request_fn is not None:
            return self.request_fn(char, rotation, iterations, seed, db)
        return simbuild.request(char, rotation, iterations, seed, None, db=db)

    def lens(self):
        v = self.lens_fn() if self.lens_fn is not None else "now"
        return v if v in LENSES else "now"

    def _db(self):
        try:
            return self.item_db_fn()
        except Exception as e:  # noqa: BLE001 - no ranks until it can be had
            log_once("bis.item_db", e)
            return None

    def _tag(self):
        cur = getattr(self.ws, "current", None)
        return cur() if callable(cur) else None

    def _rotation(self, cls, spec, tag):
        path = simbuild.CLASSES[cls]["apl"][spec]
        if self.ws is None:
            raise simbuild.SimError("no WoWSims runner")
        if (path, tag) not in self._rot:
            self._rot = {(path, tag): json.loads(self.ws.file(path, tag).decode("utf-8"))}
        return self._rot[(path, tag)]

    def _skills(self, name):
        if self.skills_fn is None:
            return None
        try:
            sk = self.skills_fn(name)
        except Exception as e:  # noqa: BLE001 - unknown
            log_once("bis.skills", e)
            return None
        return {str(x) for x in sk} if sk is not None else None

    def _changed(self, key, cb):
        self._handed = key
        if self.on_change is not None:
            try:
                self.on_change(key, json.loads(json.dumps(cb)))
            except Exception as e:  # noqa: BLE001 - the coach goes on with what it had
                log_once("bis.on_change", e)

    # ---- the character ------------------------------------------------------------------------------------------
    def character(self, st, s):
        """What is ranked of the player now ({"name", "realm", "class", "race", "faction", "level", "talents",
        "worn", "bags", "skills"}), or None (gear or talents not in yet, a fishing pole in hand)."""
        talents = st.talents()
        gear = getattr(st, "gear", None) or {}
        worn = gear.get("W") or []
        if not talents or not worn or any((it.get("cls"), it.get("sub")) == FISHING_POLE for it in worn):
            return None
        return {"name": s.get("name"), "realm": s.get("realm"), "class": str(s.get("class") or "").upper(),
                "race": s.get("race"), "faction": s.get("faction"), "level": as_int(s.get("level")),
                "talents": talents, "worn": worn, "bags": gear.get("I") or [], "skills": self._skills(s.get("name"))}

    def gear_key(self, char, lens=None):
        """What the ranks depend on: how they're worked out, class, race, side, talents, the worn items with their
        enchants and suffixes, whether it enchants (ring enchants), the lens (None: the one set now)."""
        sk = char.get("skills")
        worn = sorted((str(it.get("key")), as_int(it.get("id")), as_int(it.get("enchant")) or 0,
                       as_int(it.get("suffix")) or 0) for it in char.get("worn") or ())
        return json.dumps([V, simbuild.VERSION, char["class"], char.get("race"), char.get("faction"),
                           simbuild.talents_string(char.get("talents")), worn, sk is not None and ENCHANTING in sk,
                           lens if lens in LENSES else self.lens()])

    def _current_key(self):
        try:
            st = self.state_fn()
            s = (st.summary() if st is not None else None) or {}
        except Exception as e:  # noqa: BLE001
            log_once("bis.state", e)
            s = {}
        return char_key(s) if s.get("name") else self.current

    def stale(self):
        """A running rank's gear changed (its character plays on with other gear)."""
        want = self._watch
        if want is None:
            return False
        try:
            st = self.state_fn()
            s = (st.summary() if st is not None else None) or {}
            char = self.character(st, s) if s.get("name") else None
        except Exception as e:  # noqa: BLE001 - carry on
            log_once("bis.stale", e)
            return False
        return char is not None and (char_key(char), self.gear_key(char)) != want

    # ---- the poll -----------------------------------------------------------------------------------------------
    def start(self):
        def loop():
            while True:
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001 - the next poll tries again
                    log_once("bis.tick", e)
                time.sleep(POLL_S)
        threading.Thread(target=loop, daemon=True, name="bis").start()
        return self

    def tick(self):
        st = self.state_fn()
        s = (st.summary() if st is not None else None) or {}
        if not s.get("name") or (self.enabled_fn is not None and not self.enabled_fn()):
            return
        if not simbuild.simmable(s):
            return
        char = self.character(st, s)
        if char is None:
            return
        key = char_key(char)
        self.current = key
        self.refresh(char)
        tag = self._tag()
        if not tag:
            return
        lens, now = self.lens(), self._now()
        gk = self.gear_key(char, lens)
        f = self.failed.get(gk)
        if f and (now - f["t"] < RETRY_S or (f["n"] >= GIVE_UP and f["tag"] == tag)):
            return
        if gk != self.pending:
            self.pending, self.pending_t = gk, now
        with self.lock:
            cb = self.data["chars"].get(key) or {}
        if cb.get("slots") is not None and cb.get("gear_key") == gk and cb.get("tag") == tag:
            self._bags(char, cb, tag, now)
            return
        if now - self.pending_t < SETTLE_S:
            return                                   # (gear changing: ranked once it settles)
        if self._reuse(char, gk, tag):
            return                                   # (gear back to one ranked this session: no sims)
        weights = self.weights_fn(char["name"])
        db = self._db()
        if db is None or not self._weighed(weights, char):
            return                                   # (WoWSims' item data, or the weights for its spec, not in yet)
        if not self._weights_fresh(weights, st, s) and now - self.pending_t < SETTLE_S + WEIGHTS_WAIT_S:
            return                                   # (the weights of this gear may still come: they screen the pool)
        self._watch = (key, gk)
        try:
            self.rank(char, gk, lens)
        except _Abort:
            log("bis", "%s's gear changed while ranking: ranked again once it settles" % char["name"])
        except Exception as e:  # noqa: BLE001 - tried again after RETRY_S
            n = (self.failed.get(gk) or {}).get("n", 0) + 1
            self.failed[gk] = {"t": self._now(), "tag": tag, "n": n}        # (when it failed: a rank may run long)
            self.status = {"state": "error", "error": str(e)[:300], "runs": 0}
            log("bis", "couldn't rank %s's gear: %s" % (char["name"], e))
        finally:
            self._watch = None

    @staticmethod
    def _weighed(w, char):
        """Weights to screen with: the character's class and the spec its talents make now (an older spec's weights
        value other stats)."""
        if not (isinstance(w, dict) and isinstance(w.get("w"), dict) and w.get("class") == char["class"]):
            return False
        try:
            return w.get("spec") in (None, simbuild.spec_of(char["class"], char["talents"]))
        except Exception as e:  # noqa: BLE001 - talents WoWSims can't name a spec for: the class is all there is
            log_once("bis.spec", e)
            return True

    @staticmethod
    def _weights_fresh(w, st, s):
        """Whether the weights were measured on the gear and talents worn now (sim_weights.SimWeights.key; an entry
        without a key: taken as fresh)."""
        if not isinstance(w, dict) or w.get("key") is None:
            return True
        try:
            char = sim_weights.SimWeights.character(st, s)
            return char is not None and w["key"] == sim_weights.SimWeights.key(char)
        except Exception as e:  # noqa: BLE001 - can't tell: fresh
            log_once("bis.weights_key", e)
            return True

    def _reuse(self, char, gk, tag):
        """Gear back to one ranked this session (a mount trinket on and off): its ranks again, re-picked from today's
        sources, bags and skills, without sims. -> True when so."""
        key = char_key(char)
        with self.lock:
            hit = next((c for c in self.recent.get(key) or () if c.get("gear_key") == gk and c.get("tag") == tag),
                       None)
        if hit is None:
            return False
        db = self._db()
        skills = char.get("skills")
        with self.lock:
            cb = json.loads(json.dumps(hit))
            self._keep_recent(key, self.data["chars"].get(key))
            cb["told"] = (self.data["chars"].get(key) or {}).get("told") or {}
            cb["skills"] = sorted(skills) if skills is not None else None
            self._rerank(cb, skills, char.get("faction"), self._bag_ids(char), db)
            self.data["chars"][key] = cb
            self._save()
            out = json.loads(json.dumps(cb))
        log("bis", "%s: gear ranked before this session, its ranks again" % char["name"])
        self._changed(key, out)
        return True

    def _keep_recent(self, key, cb):
        """(Under the lock) A character's ranks about to be replaced, kept among its RECENT_KEEP last (_reuse)."""
        if not cb or cb.get("slots") is None or not cb.get("gear_key"):
            return
        old = {k: v for k, v in cb.items() if k != "told"}
        xs = [c for c in self.recent.get(key) or () if (c.get("gear_key"), c.get("tag")) != (cb["gear_key"],
                                                                                              cb.get("tag"))]
        self.recent[key] = [json.loads(json.dumps(old))] + xs[:RECENT_KEEP - 1]

    def rank(self, char, gk=None, lens=None):
        """Rank the character now (the module's steps 1 to 8). -> its CharBis, kept in data/bis.json under the gear key
        it was ranked for (gk: the poll's, else worked out now; lens: the lens it is in). Raises simbuild.SimError when
        it can't (no item data, weights or WoWSims; the worn gear's sim failed)."""
        lens = lens if lens in LENSES else self.lens()
        gk = gk or self.gear_key(char, lens)
        db = self._db()
        if db is None:
            raise simbuild.SimError("WoWSims' item data isn't loaded yet")
        weights = self.weights_fn(char.get("name"))
        if not self._weighed(weights, char):
            raise simbuild.SimError("WoWSims hasn't weighed this character (in this spec) yet")
        tag = self._tag()
        if not tag:
            raise simbuild.SimError("WoWSims isn't installed yet")
        spec = simbuild.spec_of(char["class"], char["talents"])
        rotation = self._rotation(char["class"], spec, tag)
        key, t0 = char_key(char), self._now()
        with self._rank_lock:
            self.status = {"state": "running", "error": None, "runs": 0}
            r = Ranking(self, char, db, weights["w"], rotation, tag, lens)
            try:
                cb = r.go()
            finally:
                self.status["runs"] = len(r.runs)
            skills = char.get("skills")
            cb.update(tag=tag, gear_key=gk, t=int(self._now()), spec=spec,
                      faction=char.get("faction"), skills=sorted(skills) if skills is not None else None,
                      **{"class": char["class"]})
            with self.lock:
                self._keep_recent(key, self.data["chars"].get(key))
                cb["told"] = (self.data["chars"].get(key) or {}).get("told") or {}
                self._rerank(cb, skills, char.get("faction"), self._bag_ids(char), db)
                self.data["chars"][key] = cb
                self._save()
                out = json.loads(json.dumps(cb))
            self.status = {"state": "ready", "error": None, "runs": len(r.runs)}
        nxt = cb.get("next")
        log("bis", "%s (%s): ranked %d items in %d sims, %.0f s (WoWSims %s); next upgrade: %s" % (
            char.get("name"), spec, sum(len(xs) for xs in cb["slots"].values()), len(r.runs), self._now() - t0, tag,
            "%s (+%.1f DPS)" % (self.item_name(nxt["id"], nxt.get("sfx")), nxt["d"]) if nxt else "none"))
        self._changed(key, out)
        return out

    @staticmethod
    def _bag_ids(char):
        return {as_int(it.get("id")) for it in char.get("bags") or ()} - {None}

    def _bags(self, char, cb, tag, now):
        """Bag items the character can wear that its ranks don't hold: simmed against the worn gear once they stayed
        SETTLE_S, and put in their group after the set's items, by d."""
        db, weights = self._db(), self.weights_fn(char["name"])
        if db is None or not self._weighed(weights, char):
            return
        have = {ck(x) for xs in cb["slots"].values() for x in xs} | self.bag_skip
        r = Ranking(self, char, db, weights["w"], None, tag, self.lens())
        new = r.bag_cands(have | {ek(e) for e in r.G0.values()})
        ids = tuple(sorted(ck(c) for c in new))
        if not ids:
            self.bag_pending = None
            return
        if ids != self.bag_pending:
            self.bag_pending, self.bag_t = ids, now
            return
        if now - self.bag_t < SETTLE_S:
            return
        r.rotation = self._rotation(char["class"], cb.get("spec") or simbuild.spec_of(char["class"], char["talents"]),
                                    tag)
        r.candidates(r.pool())                      # (the partners a weapon is simmed with, by EP)
        new = [r.by_k[ck(c)] for c in new if ck(c) in r.by_k]
        self._watch = (char_key(char), self.gear_key(char))
        try:
            r.swaps(only=new)
        except _Abort:
            return
        except Exception as e:  # noqa: BLE001 - these items aren't ranked this session
            log("bis", "couldn't sim the new bag items: %s" % e)
            self.bag_skip.update(ids)
            return
        finally:
            self._watch = None
        key = char_key(char)
        with self.lock:
            cb = self.data["chars"].get(key)
            if cb is None or cb.get("slots") is None:
                return
            in_set = set((cb.get("set") or {}).values())
            for c in new:
                if ck(c) not in r.single:
                    self.bag_skip.add(ck(c))
                    continue
                e = r.entries(c["group"], [c])[0]
                xs = cb["slots"].setdefault(c["group"], [])
                i = 0
                while i < len(xs) and (xs[i]["id"] in in_set or xs[i]["d"] >= e["d"]):
                    i += 1
                xs.insert(i, e)
            self._rerank(cb, char.get("skills"), char.get("faction"), self._bag_ids(char), db)
            self._save()
            out = json.loads(json.dumps(cb))
        self.bag_pending = None
        log("bis", "%s: simmed %d new bag item%s" % (char["name"], len(new), "" if len(new) == 1 else "s"))
        self._changed(key, out)

    # ---- ranks without sims -------------------------------------------------------------------------------------
    def _fact(self, iid, faction):
        """The research's fact for the item (sources_fn), or None: also None for one found for the other side (the
        research keeps one per item, for the faction it last played)."""
        f = None
        if self.sources_fn is not None:
            try:
                f = self.sources_fn(iid)
            except Exception as e:  # noqa: BLE001 - WoWSims' own then
                log_once("bis.sources", e)
        if not isinstance(f, dict) or (f.get("fa") and faction and f["fa"] != faction):
            return None
        return f

    def avail(self, iid, faction, db):
        """Whether the item can be had now for this side: "now", "later" or "nosrc" (the research's fact, else
        WoWSims' own data)."""
        f = self._fact(iid, faction)
        if f is not None and f.get("avail") in ("now", "later", "nosrc"):
            return f["avail"]
        if db is not None and db.item(iid) is not None:
            return db.avail(iid, faction)
        return "nosrc"

    def _rerank(self, cb, skills, faction, bags=None, db=None):
        """Avail, ranks and the next upgrade worked out again from what is known now (sources, bags, skills), without
        sims (db: WoWSims' item data, taken before the lock: loading it may download). -> True when anything
        changed."""
        changed = False
        for xs in (cb.get("slots") or {}).values():
            n = 0
            for x in xs:
                if bags is not None and x["how"] != "worn" and bool(x.get("bag")) != (x["id"] in bags):
                    changed = True
                    if x["id"] in bags:
                        x["bag"] = 1
                    else:
                        x.pop("bag", None)
                av = "now" if x["how"] == "worn" or x.get("bag") else self.avail(x["id"], faction, db)
                rank = 0
                if av == "now":
                    n += 1
                    rank = n
                if av != x.get("avail") or rank != x.get("rank"):
                    x["avail"], x["rank"], changed = av, rank, True
        nxt = self._pick_next(cb, skills)
        if nxt is not None:
            nxt.update(avail="now", source=self.source_text(nxt["id"], faction, db))
        if nxt != cb.get("next"):
            cb["next"], changed = nxt, True
        return changed

    @staticmethod
    def _pick_next(cb, skills):
        """The next upgrade: the largest gain beyond 2s among ranked items the character can get now, not worn or in
        the bags, whose profession it has; a weapon simmed with a partner ("with") names it, its d is the pair's."""
        best = None
        for g, xs in (cb.get("slots") or {}).items():
            for x in xs:
                if x["how"] == "worn" or x.get("bag") or not x.get("rank") or x.get("avail") != "now":
                    continue
                if x.get("req") and (skills is None or x["req"] not in skills):
                    continue
                if x["d"] > 2 * x["s"] and (best is None or x["d"] > best["d"]):
                    best = {"id": x["id"], "slot": g, "d": x["d"], "s": x["s"], "sfx": x.get("sfx") or 0}
                    if x.get("sfx") and isinstance(x.get("name"), str):
                        best["name"] = x["name"]
                    if x.get("with"):
                        best["with"], best["with_name"] = x["with"], x.get("with_name")
                    if x.get("cond"):
                        best["cond"] = 1
        return best

    def refresh(self, char):
        """The character's ranks and next upgrade re-picked from today's sources, bags and skills (no sims); saved
        when they changed, and handed on then and at the character's first poll (the ranks kept from before)."""
        key, skills = char_key(char), char.get("skills")
        db = self._db()                                 # (before the lock: loading it may download)
        with self.lock:
            cb = self.data["chars"].get(key)
            if not cb or cb.get("slots") is None:
                return
            sk = sorted(skills) if skills is not None else None
            changed = cb.get("skills") != sk
            cb["skills"] = sk
            changed = self._rerank(cb, skills, char.get("faction") or cb.get("faction"), self._bag_ids(char), db) \
                or changed
            if changed:
                self._save()
            elif key == self._handed:
                return
            out = json.loads(json.dumps(cb))
        self._changed(key, out)

    # ---- what the addon and the coach get -----------------------------------------------------------------------
    def live_section(self):
        """live_data's "chars" provider: {char key: {"bis": {item id: [group, rank, d, mark, need, suffix, with]}}}
        for every ranked item of every character (an item ranked with two random suffixes: the better rank's). mark:
        "~" when |d| is under 2s (about the same as what is worn), or "s" for a piece best only with its set
        ("together", d nil); need: the profession it needs to wear while the character isn't known to have it;
        suffix: the random suffix it was ranked with, without its sign (the tooltip shows the rank on that suffix
        only); with: the name of the weapon its d was simmed with (a one-hander and an off hand in place of a
        two-hander: the pair's d). Fields after the last one given are left out, nil (None) fills the others."""
        out = {}
        with self.lock:
            for key, cb in sorted(self.data["chars"].items()):
                bis, skills = {}, cb.get("skills")
                for g, xs in (cb.get("slots") or {}).items():
                    for x in xs:
                        if not x.get("rank") or x["id"] in bis:
                            continue
                        d, mark = round(float(x["d"]), 1) or 0.0, None
                        if x["how"] != "worn" and x.get("together"):
                            d, mark = None, "s"              # (best with its set: no d of its own to say)
                        elif x["how"] != "worn" and abs(x["d"]) < 2 * x["s"]:
                            mark = "~"
                        req = x.get("req")
                        need = req if isinstance(req, str) and req and x["how"] != "worn" and (
                            not isinstance(skills, list) or req not in skills) else None
                        sfx = abs(as_int(x.get("sfx")) or 0) or None
                        mate = x.get("with_name") if x["how"] != "worn" and isinstance(x.get("with_name"), str) \
                            else None
                        row = [g, int(x["rank"]), d, mark, need, sfx, mate]
                        while row[-1] is None:
                            row.pop()
                        bis[x["id"]] = row
                if bis:
                    out[key] = {"bis": bis}
        return out

    def _entry(self, char):
        """The CharBis (a copy, its next upgrade re-picked) for a character dict, a key, or None (the one playing)."""
        given = isinstance(char, dict) and "skills" in char
        key = char_key(char) if isinstance(char, dict) else (char or self._current_key())
        db = self._db()                                 # (before the lock: loading it may download)
        with self.lock:
            cb = self.data["chars"].get(key or "")
            if not cb or cb.get("slots") is None:
                return None
            if given:
                skills = char["skills"]
            else:
                skills = set(cb["skills"]) if cb.get("skills") is not None else None
            if self._rerank(cb, skills, cb.get("faction"), None, db):
                self._save()
            return json.loads(json.dumps(cb))

    def item_name(self, iid, sfx=0):
        """The item's name, with its suffix ("Willow Pants of the Eagle"), or "item <id>"."""
        db = self._db()
        name = db.name(iid) if db is not None else None
        if db is None or not name:
            return "item %s" % iid
        suffix = (db.suffixes.get(sfx) or db.suffixes.get(abs(sfx)) or {}) if sfx else {}
        return "%s %s" % (name, suffix["name"]) if suffix.get("name") else name

    def source_text(self, iid, faction=None, db: "item_db.ItemDB | _Load | None" = _LOAD):
        """Where the item comes from, as the tooltip says it (" (web, unconfirmed)" after a web fact), or None (the
        research's fact for this side, else WoWSims' data: db, None for none, or loaded here when not given)."""
        f = self._fact(iid, faction)
        if f is not None:
            srcs, bond = f.get("s") or [], f.get("bond")
        else:
            if isinstance(db, _Load):
                db = self._db()
            srcs, bond = (db.sources(iid, faction) if db is not None else []), None
        text = item_text.line(srcs, None, bond)
        if text and (item_text.best(srcs) or {}).get("src") == "web":
            text += WEB_NOTE
        return text

    def next_upgrade(self, char=None):
        """The character's next upgrade (a character dict, its key, or None: the one playing): {"id", "name", "sfx",
        "slot", "d", "s", "source"}, and "with" (the partner weapon's name: d is the pair's) when so; or None."""
        cb = self._entry(char)
        nxt = (cb or {}).get("next")
        if not nxt:
            return None
        out = {"id": nxt["id"], "name": self.item_name(nxt["id"], nxt.get("sfx")), "sfx": nxt.get("sfx") or 0,
               "slot": nxt["slot"], "d": nxt["d"], "s": nxt["s"], "source": nxt.get("source")}
        if nxt.get("with"):
            out["with"] = nxt.get("with_name") or self.item_name(nxt["with"])
        return out

    # ---- told once ----------------------------------------------------------------------------------------------
    def told(self, key, char_key_=None):
        """Whether the coach said this (a rules key, e.g. "next_upgrade|<id>|<suffix>") to the character (default: the one
        playing). Kept in data/bis.json, so it holds across restarts."""
        ck = char_key_ or self._current_key()
        with self.lock:
            return bool(ck) and str(key) in ((self.data["chars"].get(ck) or {}).get("told") or {})

    def mark_told(self, key, char_key_=None):
        """Remember the coach said this (the TOLD_KEEP newest per character are kept)."""
        ck = char_key_ or self._current_key()
        if not ck:
            return
        with self.lock:
            cb = self.data["chars"].setdefault(ck, {})
            told = cb.setdefault("told", {})
            told[str(key)] = int(self._now())
            for k in sorted(told, key=lambda k: told[k])[:max(0, len(told) - TOLD_KEEP)]:
                del told[k]
            self._save()
