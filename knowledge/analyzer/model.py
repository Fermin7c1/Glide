"""The cost/XP walker shared by route.analyze() and simulate.run().

A Walker steps through a Route with a State (level, xp, clock, position, quest log, hearth, rested pool) and
returns one result dict per route step. Everything numeric comes from knowledge.mechanics (XP formulas, run
speed, time to kill, downtime, death costs) and analyzer.params (interaction costs, search time, drop-rate
default, detour). See README.md for the cost model.

Objective accounting: every (quest, objective) the route lists is paid once, at the first applicable step that
lists it while the quest is in the log (or, for listings that all come before the accept, at the last of
them). RXP runtime gates (isOnQuest, isQuestTurnedIn, .xp skips, hearth cooldown, zone/subzone skips, #requires)
are evaluated against the modelled state, so mutually exclusive branches (e.g. 'rib quest done' vs 'not done')
resolve the way they would in game.
"""
import math
import re

from knowledge.mechanics import combat as MC
from knowledge.mechanics import death as MD
from knowledge.mechanics import xp as MX
from knowledge.mechanics import travel as MT
from . import params as AP
from .kbdata import RANK_ELITE, run_time

MAX_LEVEL = MX.max_level()
LEVEL_XP = [0] + [MX.level_xp(lv) for lv in range(1, MAX_LEVEL)] + [0]
CUM = [0, 0]
for _lv in range(1, MAX_LEVEL + 1):
    CUM.append(CUM[-1] + (LEVEL_XP[_lv] if _lv < len(LEVEL_XP) else 0))
# CUM[L] = total XP from level 1 to the start of level L

class _Transit(dict):
    """Known fast links between maps of one continent: (from_map, to_map) -> (mode, seconds)."""

    def get(self, key, default=None):
        k = dict.get(self, key)
        if k is None:
            return default
        return k[0], float(AP.get(k[1]))


TRANSIT = _Transit({(1455, 1453): ("tram", "analyzer.travel.tram_s"),
                    (1453, 1455): ("tram", "analyzer.travel.tram_s")})

GATE_VERBS = ("isOnQuest", "isNotOnQuest", "isQuestComplete", "isQuestNotComplete", "isQuestTurnedIn",
              "isQuestAvailable", "zoneskip", "subzoneskip", "cooldown", "maxlevel")
INTERACT_VERBS = ("skipgossip", "skipgossipid", "gossipoption", "gossip")
USE_VERBS = ("use", "usespell", "cast")
_WAIT_RE = re.compile(r"wait (?:for )?(?:about |around |up to )?(one|two|three|four|five|\d+) (minute|second)s?", re.I)
_NUMW = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_AMOUNT_RE = re.compile(r"\(x?(\d+)\)|(\d+)\s*/\s*(\d+)")


_QXP = {}


def qxp(full, qlevel, level):
    """mechanics.xp.quest_xp with a cache (it uses exact fractions; the analyzer calls it a lot)."""
    if full is None:
        return None
    key = (full, qlevel, int(level))
    v = _QXP.get(key)
    if v is None:
        if len(_QXP) > 200000:
            _QXP.clear()
        v = _QXP[key] = MX.quest_xp(full, qlevel, int(level)) or 0
    return v


_KXP = {}


def kill_xp_c(level, mob_level, elite=False):
    """mechanics.xp.kill_xp(level, mob_level, elite) cached (exact-fraction arithmetic is slow)."""
    key = (int(level), int(mob_level), bool(elite))
    v = _KXP.get(key)
    if v is None:
        if len(_KXP) > 100000:
            _KXP.clear()
        v = _KXP[key] = MX.kill_xp(int(level), int(mob_level), elite=bool(elite))
    return v


_DECAY = {}


def decay_level(full, qlevel):
    """First player level at which the quest gives less than its full XP (None when unknown)."""
    if not full or qlevel is None:
        return None
    key = (full, qlevel)
    if key not in _DECAY:
        base = qxp(full, qlevel, max(1, qlevel))
        out = None
        for lv in range(max(1, qlevel), MAX_LEVEL):
            if (qxp(full, qlevel, lv) or 0) < base:
                out = lv
                break
        _DECAY[key] = out
    return _DECAY[key]


def level_f(level, xp):
    lx = LEVEL_XP[level] if level < MAX_LEVEL else 0
    return round(level + (xp / lx if lx else 0.0), 3)


def total_xp(level, xp):
    return CUM[min(level, MAX_LEVEL)] + (xp or 0)


def from_total(total):
    lv = 1
    while lv < MAX_LEVEL and total >= CUM[lv + 1]:
        lv += 1
    return lv, (total - CUM[lv]) if lv < MAX_LEVEL else 0


class State:
    """Mutable walker state. to_dict()/from_dict() use the simulate contract keys."""
    __slots__ = ("level", "xp", "time", "pos", "active", "done", "paid", "bind", "hearth_ready_t", "pool",
                 "last_mob", "labels", "held", "dungeon_lumps", "abandoned", "xp_mod")

    def __init__(self, level=1, xp=0, time=0.0, pos=None):
        self.level, self.xp, self.time, self.pos = int(level), int(xp), float(time), pos
        self.active, self.done, self.paid, self.labels = set(), set(), set(), set()
        self.bind, self.hearth_ready_t, self.pool, self.last_mob = None, -1e18, 0, None
        self.held, self.dungeon_lumps, self.abandoned = {}, set(), set()
        self.xp_mod = 0.0

    def copy(self):
        s = State(self.level, self.xp, self.time, self.pos)
        s.active, s.done, s.paid, s.labels = set(self.active), set(self.done), set(self.paid), set(self.labels)
        s.bind, s.hearth_ready_t, s.pool, s.last_mob = self.bind, self.hearth_ready_t, self.pool, self.last_mob
        s.held, s.dungeon_lumps, s.abandoned = dict(self.held), set(self.dungeon_lumps), set(self.abandoned)
        s.xp_mod = self.xp_mod
        return s

    def assign(self, other):
        for k in self.__slots__:
            setattr(self, k, getattr(other, k))

    def total(self):
        return total_xp(self.level, self.xp)

    def add_xp(self, amount):
        if amount <= 0 or self.level >= MAX_LEVEL:
            return
        self.xp += int(round(amount))
        while self.level < MAX_LEVEL and self.xp >= LEVEL_XP[self.level]:
            self.xp -= LEVEL_XP[self.level]
            self.level += 1
        if self.level >= MAX_LEVEL:
            self.xp = 0

    def to_dict(self):
        return {"level": self.level, "xp": self.xp, "time_s": round(self.time, 1),
                "ui_map": self.pos[0] if self.pos else None, "x": self.pos[1] if self.pos else None,
                "y": self.pos[2] if self.pos else None, "quests_done": set(self.done),
                "quests_active": set(self.active), "hearth_ready_t": self.hearth_ready_t if self.hearth_ready_t > -1e17 else 0.0,
                "rested_pool": self.pool, "bind": self.bind, "exp_level": level_f(self.level, self.xp)}

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        pos = (d["ui_map"], d["x"], d["y"]) if d.get("ui_map") is not None and d.get("x") is not None else None
        s = cls(d.get("level") or 1, d.get("xp") or 0, d.get("time_s") or 0.0, pos)
        s.done = set(d.get("quests_done") or ())
        s.active = set(d.get("quests_active") or ())
        s.hearth_ready_t = float(d["hearth_ready_t"]) if d.get("hearth_ready_t") is not None else -1e18
        s.pool = int(d.get("rested_pool") or 0)
        b = d.get("bind")
        s.bind = tuple(b) if b else None
        s.xp_mod = float(d.get("xp_mod") or 0.0)
        return s


def _xp_target(a):
    """`.xp` args -> total XP that satisfies the element (RXP functions.xp semantics)."""
    lvl, xp = int(a.get("level") or 1), a.get("xp") or 0
    if xp < 0:
        return CUM[min(lvl, MAX_LEVEL)] + xp                     # xp away from level `lvl`
    if xp >= 1:
        return CUM[min(lvl, MAX_LEVEL)] + int(xp)
    return CUM[min(lvl, MAX_LEVEL)] + int((LEVEL_XP[lvl] if lvl < MAX_LEVEL else 0) * xp)


def _xp_met(a, st):
    return st.total() >= _xp_target(a)


class Walker:
    """Walks route steps. opts: skip_quests (set), skip_steps (set), grind_at {idx: {"minutes"|"to_level"}},
    hold {qid: until_level}, dungeons (bool: add a run lump for instance objectives), rested (bool)."""

    def __init__(self, data, route, opts=None):
        self.data, self.route = data, route
        p = route.player
        self.cls, self.faction, self.race = p["class"], p["faction"], p.get("race")
        self.opts = opts or {}
        self.skip_quests = set(self.opts.get("skip_quests") or ())
        self.skip_steps = set(self.opts.get("skip_steps") or ())
        self.grind_at = dict(self.opts.get("grind_at") or {})
        self.hold = dict(self.opts.get("hold") or {})
        self._kxp, self._kt = {}, {}
        self.search_s = float(AP.get("analyzer.kill.search_s"))
        self.incidental = float(AP.get("analyzer.kill.incidental_frac"))
        self.calib = float(AP.get("analyzer.calib.%s" % self.cls, 1.0))
        self.is_dungeon_guide = "Dungeon" in (route.guide.get("grp") or "")
        self.attr = self.opts.get("attr") if self.opts.get("attr") is not None else route.attribution()

    # -------------------------------------------------------------------------------------- kills
    def kxp(self, level, cr):
        """Expected XP of one kill of creature cr at player level (mean over its level range)."""
        lo = cr.get("min_level") or cr.get("max_level") or level
        hi = cr.get("max_level") or lo
        elite = (cr.get("rank") or 0) in RANK_ELITE
        key = (level, lo, hi, elite)
        v = self._kxp.get(key)
        if v is None:
            vals = [kill_xp_c(level, ml, elite) for ml in range(int(lo), int(hi) + 1)] or [0]
            v = self._kxp[key] = sum(vals) / len(vals)
        return v

    def ktime(self, level, cr):
        """(ttk_s, seconds per kill incl. downtime and search)."""
        lo = cr.get("min_level") or cr.get("max_level") or level
        hi = cr.get("max_level") or lo
        rank = cr.get("rank") or 0
        key = (level, lo, hi, rank)
        v = self._kt.get(key)
        if v is None:
            ml = (int(lo) + int(hi)) / 2.0
            ttk = MC.time_to_kill_s(self.cls, level, ml, rank)
            v = self._kt[key] = (ttk, ttk + MC.downtime_s(self.cls, level, 1) + self.search_s)
        return v

    def do_kills(self, st, cr, n=None, need_xp=None, purpose="objective"):
        """Kill n creatures (or until need_xp is earned). Returns a kill record; mutates st."""
        rec = {"creature_id": cr.get("id"), "name": cr.get("name"), "level": _lvl(cr), "rank": cr.get("rank") or 0,
               "count": 0.0, "xp_each": 0, "ttk_s": 0.0, "xp": 0, "time_s": 0.0, "purpose": purpose}
        first = True
        remaining = float(n) if n is not None else None
        need = float(need_xp) if need_xp is not None else None
        guard = 0
        while guard < 200:
            guard += 1
            if remaining is not None and remaining <= 1e-9:
                break
            if need is not None and need <= 0.5:
                break
            xp_each = self.kxp(st.level, cr)
            if st.xp_mod:
                xp_each *= 1.0 + st.xp_mod
            ttk, t_each = self.ktime(st.level, cr)
            if first:
                rec["xp_each"], rec["ttk_s"] = int(round(xp_each)), round(ttk, 1)
                first = False
            if xp_each <= 0 or st.level >= MAX_LEVEL:
                if remaining is None:          # grinding a gray mob never reaches the target
                    rec["note"] = "gray"
                    break
                rec["count"] += remaining
                rec["time_s"] += remaining * t_each
                remaining = 0
                break
            eff = xp_each
            if st.pool > 0:
                eff = 2 * xp_each
            to_level = LEVEL_XP[st.level] - st.xp
            k = to_level / eff
            if remaining is not None:
                k = min(k, remaining)
            if need is not None:
                k = min(k, need / eff)
            k = max(k, 1e-6)
            gain = k * eff
            if st.pool > 0:
                used = min(st.pool, int(gain))
                st.pool -= used
            st.add_xp(gain)
            rec["count"] += k
            rec["xp"] += gain
            rec["time_s"] += k * t_each
            if remaining is not None:
                remaining -= k
            if need is not None:
                need -= gain
        rec["count"] = round(rec["count"], 2)
        rec["xp"] = int(round(rec["xp"]))
        rec["time_s"] = round(rec["time_s"], 1)
        if cr.get("id") is not None:
            st.last_mob = cr.get("id")
        return rec

    # -------------------------------------------------------------------------------------- objectives
    def objective_cost(self, st, step, q, key, act, res):
        """Pay one objective: kills / loot clicks / purchase / fixed time. Returns an objective record."""
        d = self.data
        obj = None
        orows = d.objectives.get(q) or []
        okey = key[1]
        if isinstance(okey, int):
            obj = next((o for o in orows if o["idx"] == okey), None)
        elif isinstance(okey, str) and okey.startswith("i"):
            item = int(okey[1:])
            obj = next((o for o in orows if o["target_kind"] == "item" and o["target_id"] == item), None)
        rec = {"quest_id": q, "obj": okey, "type": None, "count": None, "time_s": 0.0, "xp": 0}
        args = act.get("args") or {}
        amount = None
        if obj is not None:
            amount = obj.get("amount")
        if not amount:
            amount = act.get("qty") or args.get("obj_max") or args.get("qty") or _amount_from_text(args.get("comment"))
        amount = max(1, int(amount or 1))
        otype = obj.get("type") if obj else None
        text = ((args.get("comment") or "") + " " + (act.get("text") or "")).lower()
        if otype is None:
            if "kill" in text or "slain" in text:
                otype = "kill"
            elif "collect" in text or "loot" in text:
                otype = "collect"
            else:
                otype = "other"
        mobs = step["mobs"]
        if otype == "kill":
            cid = obj.get("target_id") if obj and obj.get("target_kind") == "npc" else None
            cr = d.creature(cid) if cid else None
            if cr is None and mobs:
                cr = d.creature(mobs[0])
            if cr is None:
                return self._unknown(rec, "kill_target_unknown")
            return self._kill_obj(st, step, rec, cr, amount, 1.0, res)
        if otype == "collect":
            item = obj.get("target_id") if obj and obj.get("target_kind") == "item" else args.get("item_id")
            return self._collect_obj(st, step, rec, obj, item, amount, mobs, text, res)
        if otype in ("explore",):
            rec.update(type="explore", time_s=float(AP.get("analyzer.time.explore_s")))
            return rec
        if "escort" in text or otype in ("escort", "event"):
            rec.update(type="event", time_s=float(AP.get("analyzer.time.event_s")))
            return rec
        rec.update(type="other", time_s=float(AP.get("analyzer.time.other_obj_s")))
        return rec

    def _unknown(self, rec, why):
        self.data.count("objective_" + why, (rec.get("quest_id"), str(rec.get("obj"))))
        rec.update(type="unknown", time_s=float(AP.get("analyzer.time.unknown_obj_s")), note=why)
        return rec

    def _in_instance(self, cr, step):
        if cr is None:
            return False
        if step["pos"] is not None and not self.is_dungeon_guide:
            return False
        if (cr.get("rank") or 0) not in RANK_ELITE:
            return False
        return not self.data.spawns_of(cr["id"])

    def _kill_obj(self, st, step, rec, cr, amount, rate, res):
        n = amount / max(rate, float(AP.get("analyzer.drop.min_rate")))
        if self._in_instance(cr, step):
            rec.update(type="dungeon", creature_id=cr["id"], count=round(n, 2), note="instance objective (dungeon run)")
            self._dungeon_lump(st, res)
            return rec
        kr = self.do_kills(st, cr, n=n, purpose="objective")
        extra = n * self.incidental
        if extra > 0:
            kx = self.do_kills(st, cr, n=extra, purpose="incidental")
            kr["extra"] = round(kx["count"], 2)
            kr["xp"] += kx["xp"]
            kr["time_s"] = round(kr["time_s"] + kx["time_s"], 1)
        res["kills"].append(kr)
        rec.update(type="kill" if rate >= 1.0 else "collect", creature_id=cr["id"], count=round(n, 2),
                   rate=rate if rate < 1.0 else None, time_s=kr["time_s"], xp=kr["xp"])
        return rec

    def _friendly(self, cid):
        cr = self.data.creature(cid) or {}
        ft = cr.get("friendly_to") or ""
        return ft != "AH" and {"Alliance": "A", "Horde": "H"}.get(self.faction, "?") in ft

    def _collect_obj(self, st, step, rec, obj, item, amount, mobs, text, res):
        d = self.data
        # purchase: RXP says "Buy ...", or a vendor step selling the item
        if "buy" in text or "purchase" in text or step["buy_text"] or \
                (item is not None and item in d.vendor_items and "vendor" in step["verbs"]):
            rec.update(type="buy", item_id=item, count=amount, time_s=float(AP.get("analyzer.time.buy_s")))
            return rec
        # objects (chests, baskets, crates)
        orates = []
        if obj and obj.get("drop_from_kind") == "object":
            orates.append((obj.get("drop_from_id"), obj.get("drop_rate")))
        for r in d.object_loot.get(item, ()) if item is not None else ():
            orates.append((r["object_id"], r["rate"]))
        crates = []
        if obj and obj.get("drop_from_kind") == "npc" and obj.get("drop_from_id"):
            crates.append((obj["drop_from_id"], obj.get("drop_rate")))
        for r in d.drops.get(item, ()) if item is not None else ():
            crates.append((r["creature_id"], r["rate"]))
        friendly = [c for c, _ in crates if self._friendly(c)]
        crates = [(c, r) for c, r in crates if not self._friendly(c)]
        if friendly and not crates and not orates:
            # the "drop" comes from a friendly NPC (innkeeper, quest NPC): an interaction, not kills
            rec.update(type="other", item_id=item, count=amount, time_s=float(AP.get("analyzer.time.other_obj_s")))
            return rec
        mobs = [m for m in mobs if not self._friendly(m)]
        if orates and (not crates or not any(c in mobs for c, _ in crates)):
            oid, rate = orates[0]
            if rate is None:
                d.count("drop_rate_default_object", (rec.get("quest_id"), str(rec.get("obj"))))
            rate = max(float(rate or 1.0), float(AP.get("analyzer.drop.min_rate")))
            n = amount / rate
            t = n * (float(AP.get("analyzer.time.interact_s")) + float(AP.get("analyzer.time.object_search_s")))
            rec.update(type="object", object_id=oid, item_id=item, count=round(n, 2), rate=rate, time_s=round(t, 1))
            return rec
        # creature drops: prefer the step's mobs, then the best known rate with spawns near the step
        pick = None
        if crates:
            for cid, rate in crates:
                if cid in mobs:
                    pick = (cid, rate)
                    break
            if pick is None:
                here = step["pos"] or st.pos
                ranked = []
                for cid, rate in crates:
                    cr = d.creature(cid)
                    if not cr:
                        continue
                    on = 1 if here and d.spawns_of(cid, here[0]) else 0
                    ranked.append((on, rate or 0.0, -cid, cid, rate))
                if ranked:
                    ranked.sort(reverse=True)
                    pick = (ranked[0][3], ranked[0][4])
        if pick is None and mobs:
            pick = (mobs[0], None)
        if pick is None:
            return self._unknown(rec, "collect_source_unknown")
        cr = d.creature(pick[0])
        if cr is None:
            return self._unknown(rec, "collect_source_unknown")
        rate = pick[1]
        if rate is None:
            d.count("drop_rate_default", (rec.get("quest_id"), str(rec.get("obj"))))
            rate = float(AP.get("analyzer.drop.default_rate"))
            rec["rate_default"] = True
        rec["item_id"] = item
        out = self._kill_obj(st, step, rec, cr, amount, min(1.0, float(rate)), res)
        if out.get("type") == "kill":
            out["type"] = "collect"
        return out

    def _dungeon_lump(self, st, res):
        g = self.route.guide_key
        if g in st.dungeon_lumps:
            return
        st.dungeon_lumps.add(g)
        t = float(AP.get("analyzer.dungeon.run_s"))
        xp = float(AP.get("analyzer.dungeon.kill_xp_per_level")) * st.level
        st.add_xp(xp)
        res["xp_kills"] += int(xp)
        res["act_s"] += t
        res["notes"].append("dungeon run: %.0f min, ~%d kill XP (analyzer.dungeon.*)" % (t / 60, xp))
        res["params"].update(("analyzer.dungeon.run_s", "analyzer.dungeon.kill_xp_per_level"))

    # -------------------------------------------------------------------------------------- grind
    def grind_creature(self, st, step):
        d = self.data
        gray = MX.gray_level(st.level)
        for cid in step["mobs"]:
            cr = d.creature(cid)
            if cr and (cr.get("max_level") or 0) > gray:
                return cr
        if st.last_mob:
            cr = d.creature(st.last_mob)
            if cr and (cr.get("max_level") or 0) > gray and self._near(cr, step["pos"] or st.pos):
                return cr
        here = step["pos"] or st.pos
        if here is not None:
            from .alternatives import best_grind_spot
            spot = best_grind_spot(d, here, st.level, self.cls, self.faction,
                                   radius_yd=float(AP.get("analyzer.grind.radius_yd")))
            if spot:
                return d.creature(spot["id"])
        if st.last_mob:
            cr = d.creature(st.last_mob)
            if cr and (cr.get("max_level") or 0) > gray:
                return cr
        return None

    def _near(self, cr, here, yd=900.0):
        if here is None:
            return True
        for sp in self.data.spawns_of(cr["id"], here[0])[:200]:
            dd = self.data.dist_yd(here, sp)
            if dd is not None and dd <= yd:
                return True
        return False

    def grind(self, st, step, res, need_xp=None, seconds=None, purpose="grind"):
        cr = self.grind_creature(st, step)
        if cr is None:
            self.data.count("grind_mob_unknown", (self.route.guide_key, step["idx"]))
            cr = {"id": None, "name": "same-level mob", "min_level": st.level, "max_level": st.level, "rank": 0}
            res["notes"].append("grind mob unknown: modelled as same-level normal mobs")
        if seconds is not None:
            _, t_each = self.ktime(st.level, cr)
            n = seconds / t_each if t_each > 0 else 0
            kr = self.do_kills(st, cr, n=n, purpose=purpose)
        else:
            kr = self.do_kills(st, cr, need_xp=need_xp, purpose=purpose)
        res["kills"].append(kr)
        res["xp_grind"] += kr["xp"]
        res["kill_s"] += kr["time_s"]
        return kr

    # -------------------------------------------------------------------------------------- gates
    def gate(self, step, st):
        """Reason the step is skipped at runtime, or None."""
        if step["idx"] in self.skip_steps:
            return "variant_skip"
        req = step["flags"].get("requires")
        if isinstance(req, str) and req and req not in st.labels and req in self.route.labels:
            return "requires:" + req
        for a in step["actions"]:
            v, args = a["verb"], a["args"]
            if v == "isOnQuest":
                ids = [i for i in args.get("quest_ids") or [] if i]
                if ids and not any(i in st.active for i in ids):
                    return "not_on_quest"
            elif v == "isNotOnQuest":
                ids = [i for i in args.get("quest_ids") or [] if i]
                if any(i in st.active for i in ids):
                    return "on_quest"
            elif v in ("isQuestComplete", "isQuestNotComplete"):
                q = args.get("quest_id")
                comp = q in st.active and self.quest_complete(q, st)
                if v == "isQuestComplete" and not comp:
                    return "quest_not_complete"
                if v == "isQuestNotComplete" and comp:
                    return "quest_complete"
            elif v == "isQuestTurnedIn":
                ids = [i for i in args.get("quest_ids") or [] if i]
                if ids and not any(i in st.done for i in ids):
                    return "prereq_not_turned_in"
            elif v == "isQuestAvailable":
                ids = [i for i in args.get("quest_ids") or [] if i]
                if ids and all(i in st.done for i in ids):
                    return "already_turned_in"
            elif v == "xp" and args.get("skipstep") is not None:
                met = _xp_met(args, st)
                if met == (not args.get("reverse")):
                    return "xp_gate"
            elif v == "maxlevel":
                if st.level > int(args.get("level") or 99):
                    return "above_maxlevel"
            elif v == "cooldown" and args.get("id") == 6948:
                left = max(0.0, st.hearth_ready_t - st.time)
                sec = float(args.get("seconds") or 0)
                if (args.get("op") == ">" and left > sec) or (args.get("op") == "<" and left < sec):
                    return "hearth_cooldown"
            elif v == "zoneskip" and st.pos is not None:
                maps = [m for m in args.get("ui_maps") or [] if m]
                inside = st.pos[0] in maps
                if maps and inside != bool(args.get("reverse")):
                    return "zoneskip"
            elif v == "subzoneskip" and st.pos is not None and args.get("area_id"):
                inside = self.data.in_area(args["area_id"], st.pos)
                if inside is not None and inside != bool(args.get("reverse")):
                    return "subzoneskip"
        return None

    def quest_complete(self, q, st):
        keys = self.route.obj_keys.get(q)
        if not keys:
            return True
        return all(k in st.paid for k in keys)

    def satisfied(self, step, st):
        """All quest work of the step is already done (RXP would auto-complete it)."""
        if not step["quest_work"] or step["other_work"]:
            return False
        for a in step["actions"]:
            v, q = a["verb"], a.get("quest_id")
            if v == "accept" and q not in st.active and q not in st.done and q not in self.skip_quests:
                return False
            if v == "turnin" and q not in st.done and q not in self.skip_quests:
                if q in st.active or not (a["args"] or {}).get("skip_if_missing"):
                    return False
            if v in ("complete", "collect") and q and q not in st.done:
                k = self.route.action_key.get((step["idx"], a["line"]))
                if k and k not in st.paid and q not in self.skip_quests:
                    return False
            if v == "xp" and (a["args"] or {}).get("skipstep") is None and not (a["args"] or {}).get("reverse"):
                if not _xp_met(a["args"], st):
                    return False
        return True

    # -------------------------------------------------------------------------------------- travel
    def travel(self, step, st, res):
        path = step["path"]
        tr = {"from_idx": self._last_idx, "dist_yd": 0.0, "time_s": 0.0, "detour_yd": 0.0,
              "mode": "run", "observed": False}
        if not path or st.pos is None:
            if path and st.pos is None:
                st.pos = path[-1]
            res["travel"] = tr
            return
        lvl = st.level
        straight = None
        cur = st.pos
        for p in path:
            dd = self.data.dist_yd(cur, p)
            if dd is None:
                self.data.count("travel_leg_unknown_distance", (cur[0], p[0]))
                if self.data.map_size(p[0])[0] is None:
                    self.data.count("map_size_missing", p[0])
                inst = self.data.map_size(p[0])[0] is None
                tr["time_s"] += float(AP.get("analyzer.travel.instance_s" if inst else "analyzer.travel.unknown_s"))
                tr["unknown"] = True
            elif dd == math.inf:
                tr["time_s"] += float(AP.get("analyzer.travel.cross_continent_s"))
                tr["mode"] = "boat"
                res["params"].add("analyzer.travel.cross_continent_s")
            else:
                transit = TRANSIT.get((cur[0], p[0]))
                obs = self.data.observed_path(cur, p) if dd > 15 and cur[0] == p[0] else None
                rt = run_time(dd, lvl)
                if transit and transit[1] < rt:
                    tr["time_s"] += transit[1]
                    tr["mode"] = transit[0]
                    res["params"].add("analyzer.travel.tram_s")
                elif obs:
                    tr["time_s"] += obs[0]
                    tr["dist_yd"] += obs[1]
                    tr["observed"] = True
                else:
                    tr["time_s"] += rt
                    tr["dist_yd"] += dd
            cur = p
        sd = self.data.dist_yd(st.pos, path[-1])
        if sd is not None and sd != math.inf:
            straight = sd
            tr["detour_yd"] = round(max(0.0, tr["dist_yd"] - sd), 1)
        tr["straight_yd"] = round(straight, 1) if straight is not None else None
        tr["dist_yd"] = round(tr["dist_yd"], 1)
        tr["time_s"] = round(tr["time_s"], 1)
        st.pos = path[-1]
        res["travel"] = tr
        res["travel_s"] += tr["time_s"]
        res["params"].update(("travel.run_speed", "analyzer.travel.detour"))

    # -------------------------------------------------------------------------------------- one step
    def step(self, step, st):
        res = {"idx": step["idx"], "rxp_step_no": step.get("rxp_step_no"), "applied": True, "skip": None,
               "kind": step["kind"], "lvl0": level_f(st.level, st.xp), "level0": st.level, "xp0": st.xp,
               "t0": st.time, "time_s": 0.0, "travel_s": 0.0, "kill_s": 0.0, "act_s": 0.0,
               "xp_now": 0, "xp_quests": 0, "xp_kills": 0, "xp_grind": 0, "travel": None, "turnins": [],
               "accepts": [], "objectives": [], "kills": [], "grind": None, "notes": [], "params": set(),
               "pos0": st.pos}
        skip = self.gate(step, st)
        if skip is None and self.satisfied(step, st):
            skip = "already_done"
        if skip:
            res.update(applied=False, skip=skip, lvl1=res["lvl0"], level1=st.level, xp1=st.xp, pos1=st.pos)
            self._finish_held(st, res)
            return res
        if step["label"]:
            st.labels.add(step["label"])
        self.travel(step, st, res)
        p_t = AP.get
        accepts_here = {a["quest_id"] for a in step["actions"] if a["verb"] == "accept"}
        for a in step["actions"]:
            v, q, args = a["verb"], a.get("quest_id"), a["args"] or {}
            if q is not None and q in self.skip_quests and v in ("accept", "turnin", "complete", "collect"):
                continue
            if v == "accept":
                if q in st.done or q in st.active:
                    continue
                st.active.add(q)
                res["accepts"].append(q)
                res["act_s"] += float(p_t("analyzer.time.accept_s"))
                res["params"].add("analyzer.time.accept_s")
            elif v == "turnin":
                if q in st.done or q in st.held:
                    continue
                carried = q not in st.active
                if carried and (args.get("skip_if_missing") or q in self.route.accept_idx or q in st.abandoned):
                    # RXP skips a turn-in the player cannot make: not on the quest (negative id), or the
                    # route accepts it elsewhere (later, or in a branch that did not apply)
                    continue
                if q in self.hold and st.level < int(self.hold[q]):
                    st.held[q] = int(self.hold[q])
                    st.active.discard(q)
                    res["notes"].append("turn-in of %s held until level %s" % (q, self.hold[q]))
                    continue
                self.turnin(st, q, res)
                res["act_s"] += float(p_t("analyzer.time.turnin_s"))
                res["params"].add("analyzer.time.turnin_s")
                if carried:
                    res["notes"].append("quest %s turned in without an accept in this route (carried in)" % q)
            elif v in ("complete", "collect") and q is not None:
                if args.get("profession"):
                    continue
                key = self.route.action_key.get((step["idx"], a["line"]))
                if key is None or key in st.paid or q in st.done:
                    continue
                if not self._payable(q, key, step, st, accepts_here):
                    continue
                st.paid.add(key)
                rec = self.objective_cost(st, step, q, key, a, res)
                res["objectives"].append(rec)
                if rec.get("type") in ("kill", "collect"):
                    res["kill_s"] += rec["time_s"]
                    res["xp_kills"] += rec["xp"]
                    res["params"].update(("analyzer.kill.search_s", "combat.ttk_scale.%s" % self.cls,
                                          "analyzer.kill.incidental_frac"))
                    if rec.get("rate_default"):
                        res["params"].add("analyzer.drop.default_rate")
                else:
                    res["act_s"] += rec["time_s"]
                    if rec.get("type") == "object":
                        res["params"].update(("analyzer.time.interact_s", "analyzer.time.object_search_s"))
            elif v == "collect" and q is None and not args.get("profession"):
                if "vendor" in step["verbs"] or "buy" in (a.get("text") or "").lower() or step["buy_text"]:
                    res["act_s"] += float(p_t("analyzer.time.buy_s"))
                    res["params"].add("analyzer.time.buy_s")
            elif v == "xp":
                if args.get("skipstep") is not None or args.get("reverse"):
                    continue
                need = _xp_target(args) - st.total()
                if need > 0:
                    kr = self.grind(st, step, res, need_xp=need)
                    res["grind"] = {"target_level": args.get("level"), "target_xp": args.get("xp"),
                                    "need_xp": int(need), "creature_id": kr["creature_id"],
                                    "kills": kr["count"], "time_s": kr["time_s"]}
                    res["params"].update(("analyzer.kill.search_s", "combat.ttk_scale.%s" % self.cls))
            elif v in ("train", "trainer"):
                if "_train" not in res:
                    res["_train"] = True
                    res["act_s"] += float(p_t("analyzer.time.train_s"))
                    res["params"].add("analyzer.time.train_s")
            elif v in ("vendor", "buy", "buyAll", "buyUntilBroke"):
                if "_vendor" not in res:
                    res["_vendor"] = True
                    res["act_s"] += float(p_t("analyzer.time.vendor_s"))
                    res["params"].add("analyzer.time.vendor_s")
            elif v == "home":
                st.bind = st.pos
                res["act_s"] += float(p_t("analyzer.time.home_s"))
                res["params"].add("analyzer.time.home_s")
            elif v == "hs":
                self.hearth(st, step, a, res)
            elif v == "fly":
                self.fly(st, step, a, res)
            elif v == "fp":
                res["act_s"] += float(p_t("analyzer.time.fp_s"))
            elif v == "deathskip":
                self.deathskip(st, res, step)
            elif v in USE_VERBS:
                res["act_s"] += float(p_t("analyzer.time.use_s"))
            elif v in INTERACT_VERBS:
                res["act_s"] += float(p_t("analyzer.time.gossip_s"))
            elif v == "abandon":
                for qq in args.get("quest_ids") or []:
                    if qq in st.active:
                        st.active.discard(qq)
                        st.abandoned.add(qq)
        w = step["wait_s"]
        if w:
            res["act_s"] += w
            res["notes"].append("step text asks to wait %d s" % w)
        g = self.grind_at.get(step["idx"])
        if g:
            self.extra_grind(st, step, res, g)
        self._finish_held(st, res)
        if self.calib != 1.0:
            for k in ("travel_s", "kill_s", "act_s"):
                res[k] *= self.calib
            res["params"].add("analyzer.calib.%s" % self.cls)
        res["time_s"] = res["travel_s"] + res["kill_s"] + res["act_s"]
        st.time += res["time_s"]
        res["xp_now"] = res["xp_quests"] + res["xp_kills"] + res["xp_grind"]
        res.update(lvl1=level_f(st.level, st.xp), level1=st.level, xp1=st.xp, pos1=st.pos)
        return res

    def _payable(self, q, key, step, st, accepts_here):
        if self.attr.get(key) != step["idx"]:
            return False                         # paid at its designated (last applicable) listing
        if q in st.done or q in st.abandoned or q in st.held:
            return False
        if q in st.active or q in accepts_here:
            return True
        acc = self.route.accept_idx.get(q)
        if acc is None:
            return True                          # quest from an earlier guide (or item-started)
        return acc > step["idx"]                 # collected before the accept (pre-collect); else the
        #                                          accept did not apply (branch/gate): nothing to do

    def turnin(self, st, q, res, level=None):
        qr = self.data.quest(q)
        full = (qr or {}).get("xp")
        qlevel = (qr or {}).get("level")
        if full is None:
            self.data.count("quest_xp_unknown", q)
            xp = 0
        else:
            xp = qxp(full, qlevel, st.level) or 0
        lvl_at = st.level
        st.add_xp(xp)
        st.done.add(q)
        st.active.discard(q)
        res["xp_quests"] += xp
        res["turnins"].append({"quest_id": q, "xp": xp, "level": lvl_at, "xp_full": full})
        res["params"].update(("xp.quest.full_until", "xp.quest.factors", "xp.quest.rounding"))
        return xp

    def _finish_held(self, st, res):
        for q, until in list(st.held.items()):
            if st.level >= until:
                del st.held[q]
                ender = self.data.giver(q, "end")
                extra = 0.0
                if ender and ender.get("x") is not None and st.pos is not None:
                    dd = self.data.dist_yd(st.pos, (ender["ui_map"], ender["x"], ender["y"]))
                    if dd is not None and dd != math.inf:
                        extra = 2 * run_time(dd, st.level)
                    elif dd == math.inf:
                        extra = 2 * float(AP.get("analyzer.travel.cross_continent_s"))
                xp = self.turnin(st, q, res)
                res["act_s"] += extra + float(AP.get("analyzer.time.turnin_s"))
                res["notes"].append("held turn-in of %s released at level %d: %d XP, %.0f s extra trip"
                                    % (q, st.level, xp, extra))

    def release_all_held(self, st, res):
        for q in list(st.held):
            st.held[q] = 0
        self._finish_held(st, res)

    # -------------------------------------------------------------------------------------- moves
    def hearth(self, st, step, a, res):
        if st.hearth_ready_t > st.time:
            res["notes"].append("hearthstone on cooldown (%.0f s left): not used" % (st.hearth_ready_t - st.time))
            return
        dest = st.bind or self._hearth_from_text(a.get("text") or step.get("text"))
        res["act_s"] += float(MT.hearth_time_s()) + float(AP.get("analyzer.hs.load_s"))
        res["params"].update(("travel.hearth_cast_s", "analyzer.hs.load_s", "travel.hearth_cd_s"))
        st.hearth_ready_t = st.time + res["travel_s"] + res["act_s"] + float(MT.hearth_cooldown_s())
        if dest is None:
            self.data.count("hearth_destination_unknown", (self.route.guide_key, step["idx"]))
            res["notes"].append("hearth destination unknown")
            return
        res["travel"] = dict(res["travel"] or {}, mode="hearth")
        st.pos = dest

    def _hearth_from_text(self, text):
        """'Hearth to Thelsamar' -> the inn nearest to the Thelsamar sub-zone box (or the box centre)."""
        m = re.search(r"[Hh]earth(?:stone)? (?:to|back to) ([A-Z][\w']*(?: [A-Z][\w']*)*)", text or "")
        if not m:
            return None
        return self.data.place_named(m.group(1))

    def fly(self, st, step, a, res):
        loc = (a["args"] or {}).get("location")
        d = self.data
        near = self.route.next_pos(step["idx"])
        dest = d.flight_node_named(loc, self.faction, near=near)
        src, sd = d.nearest_flight_node(st.pos, self.faction) if st.pos else (None, None)
        res["params"].add("analyzer.fly.overhead_s")
        if dest is None or src is None:
            d.count("flight_node_unknown", loc)
            res["notes"].append("flight to %s: node unknown" % loc)
            res["act_s"] += float(AP.get("analyzer.fly.overhead_s")) + 120.0
            if dest is not None:
                st.pos = (dest["ui_map"], dest["x"], dest["y"])
            return
        t, how = d.flight_time(self.faction, src["id"], dest["id"])
        if t is None:
            d.count("flight_time_unknown", (src["id"], dest["id"]))
            t = 180.0
        res["act_s"] += t + float(AP.get("analyzer.fly.overhead_s"))
        res["travel"] = dict(res["travel"] or {}, mode="flight", flight={"from": src["id"], "to": dest["id"],
                                                                          "time_s": round(t, 1), "src": how})
        res["flight"] = {"from": src["id"], "to": dest["id"], "time_s": round(t, 1)}
        st.pos = (dest["ui_map"], dest["x"], dest["y"])

    def deathskip(self, st, res, step=None):
        gy = self.deathskip_graveyard(st.pos, self.route.next_pos(step["idx"]) if step else None)
        t = float(AP.get("analyzer.death.time_to_die_s")) + MD.death_cost_s(st.level, None, spirit_res=True)
        res["act_s"] += t
        res["params"].update(("analyzer.death.time_to_die_s", "death.spirit_res_s", "death.sickness.free_through"))
        if gy is not None:
            st.pos = (gy["ui_map"], gy["x"], gy["y"])
            res["graveyard"] = gy["id"]
        res["travel"] = dict(res["travel"] or {}, mode="death")

    def deathskip_graveyard(self, here, nxt):
        """Graveyard a deathskip lands at. RestedXP places deathskips where the respawn is useful, so among the
        graveyards of the continent within ~2500 yd of the death spot take the one nearest to the next step;
        without a next position, the nearest one (graveyard zone links are not in the KB)."""
        d = self.data
        if here is None:
            return None
        if nxt is None:
            return d.nearest_graveyard(here)[0]
        best, bk = None, None
        for r in d.pois["graveyard"]:
            if r["x"] is None:
                continue
            gp = (r["ui_map"], r["x"], r["y"])
            dh = d.dist_yd(here, gp)
            if dh is None or dh == math.inf or dh > 2500:
                continue
            dn = d.dist_yd(gp, nxt)
            if dn is None or dn == math.inf:
                continue
            if bk is None or dn < bk:
                best, bk = r, dn
        return best or d.nearest_graveyard(here)[0]

    def extra_grind(self, st, step, res, g):
        if g.get("to_level"):
            need = CUM[min(int(g["to_level"]), MAX_LEVEL)] - st.total()
            if need > 0:
                kr = self.grind(st, step, res, need_xp=need, purpose="extra_grind")
                res["notes"].append("extra grind to level %s: %d kills, %.0f s" % (g["to_level"], kr["count"], kr["time_s"]))
        elif g.get("minutes"):
            kr = self.grind(st, step, res, seconds=float(g["minutes"]) * 60.0, purpose="extra_grind")
            res["notes"].append("extra grind %s min: %d XP" % (g["minutes"], kr["xp"]))

    # -------------------------------------------------------------------------------------- run
    _last_idx = None

    def run(self, st, start=0, end=None):
        """Walk route.steps[start:end] (list positions). Returns the result list; st is mutated."""
        out = []
        steps = self.route.steps
        end = len(steps) if end is None else end
        self._last_idx = None
        for i in range(start, end):
            r = self.step(steps[i], st)
            out.append(r)
            if r["applied"] and steps[i]["path"]:
                self._last_idx = steps[i]["idx"]
        return out


def walk(data, route, st, start=0, end=None, opts=None, max_iter=3):
    """Walk route.steps[start:end] from st, refining the objective attribution until the steps that do not
    apply (gates, branches) no longer hold designated objectives. st is updated to the final end state.
    Returns (walker, results)."""
    base = st.copy()
    attr = (opts or {}).get("attr") or route.attribution()
    for it in range(max_iter):
        cur = base.copy()
        W = Walker(data, route, dict(opts or {}, attr=attr))
        res = W.run(cur, start, end)
        gated = {r["idx"] for r in res if not r["applied"]}
        new = route.attribution(excluded=gated)
        if new == attr or it == max_iter - 1:
            break
        attr = new
    st.assign(cur)
    return W, res


def _lvl(cr):
    lo, hi = cr.get("min_level"), cr.get("max_level")
    if lo and hi:
        return round((lo + hi) / 2.0, 1)
    return lo or hi


def _amount_from_text(text):
    if not text:
        return None
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    if m.group(1):
        return int(m.group(1))
    return int(m.group(3))


def wait_seconds(text):
    m = _WAIT_RE.search(text or "")
    if not m:
        return 0
    n = _NUMW.get(m.group(1).lower())
    n = int(m.group(1)) if n is None else n
    s = n * (60 if m.group(2).lower().startswith("minute") else 1)
    return min(s, float(AP.get("analyzer.time.wait_max_s")))
