"""Rules engine: turns state changes into coaching triggers (no AI here)."""
import inspect
import math
import re
import time
from typing import Any

from geo import compass, relative_label
import gear as gear_mod
import offline_coach
import settings
from state import as_int
from log import log_once

# moving away from the waypoint: the distance grew for at least AWAY_WINDOW_S, by more than AWAY_YD, and is still
# growing over the last AWAY_RECENT_S (config away_window_s, away_recent_s, away_yd, away_cooldown_s)
AWAY_WINDOW_S, AWAY_RECENT_S, AWAY_YD = 30, 10, 50


class Trigger:
    # priority: 1 urgent (speak now) .. 3 low (batch)
    def __init__(self, kind, facts, priority=2, deep=False):
        self.kind, self.facts, self.priority, self.deep = kind, facts, priority, deep
        self.t = time.time()

    def __repr__(self):
        return "<%s p%d %s>" % (self.kind, self.priority, self.facts)


CATEGORY = {
    "session_start": "route", "step_changed": "route", "stuck_on_step": "route",
    "moving_away_from_waypoint": "route", "quest_accepted": "route", "quest_turned_in": "route",
    "quest_removed": "route", "zone_changed": "route", "flight_taken": "route", "hearthed": "route",
    "hearth_bound": "route",
    "low_health_in_combat": "combat", "died": "combat", "released": "combat", "resurrected": "combat",
    "buff_expiring": "buffs", "buffs_dropped": "buffs",
    "gear_upgrade": "gear", "reward_choice": "gear", "quests_accepted": "route", "vendor_treasure": "gear",
    # Glide's own best-in-slot ranks at 60 (see Rules._bis); a WoWSims talent build that sims higher (_talent_check)
    "next_upgrade": "gear", "enchant_missing": "gear", "talent_check": "gear",
    "pace_review": "pace", "level_up": "pace", "spell_learned": "pace",
    "bags_nearly_full": "upkeep", "low_durability": "upkeep",
    "addon_outdated": None,                      # always reported
    # knowledge-layer trigger (needs data/kb.db; see Rules._kb_triggers)
    "turnin_decay_soon": "route",
    # addon 0.10 extras (see Rules._extras)
    "training_available": "upkeep", "talent_points_unspent": "upkeep", "consumables_low": "upkeep",
    "flight_path_nearby": "route", "target_tapped": "combat",
    # new item facts in the addon's Data/Live.lua (see Rules._live); a web fact the coach said, shown wrong (_corrected)
    "facts_ready": "upkeep", "fact_corrected": "upkeep",
    # the next move toward the next best-in-slot piece, weighed by effort, and its follow-through (see Rules._pursuit)
    "next_move": "gear", "pursuit_roll": "gear", "pursuit_got": "gear",
}
CONSUMABLE_LOW = {"food": 4, "drink": 4, "ammo": 200}   # warn when a count drops to this or below
TALENT_GRACE_S = 30               # unspent talent points: say so once per level, after this long
DECAY_XP_WINDOW = 0.15            # turnin_decay_soon: within 15% of the level's XP from levelling
DECAY_COOLDOWN_S = 600            # ... at most once per quest every 10 minutes
LIVE_POLL_S = 5                   # facts_ready: how often Data/Live.lua's writer is asked for news
BIS_POLL_S = 5                    # next_upgrade / enchant_missing: how often the best-in-slot ranks are looked at
BIS_ADDON = (0, 22, 0)            # the oldest addon that sends worn enchants: from it on, no EN code means no enchant
ENCHANT_GAP_S = 600               # enchant_missing: one slot at a time, at most once in this long
TALENT_POLL_S = 5                 # talent_check: how often the sims' talent check is looked at
PURSUIT_POLL_S = 15               # next_move: how often the pursuit plan is looked at (the planner works it out)
ROLL_WAIT_S = 120                 # pursuit_roll: after a boss of the move died, the time its loot (and a roll) may take
#                                   (pursuit.LOOT_S, research.LOOT_S: a boss's death to its item in the bags)
KILL_GAP_S = 300                  # ... one kill per boss in this long (its corpse stays targeted; pursuit.KILL_DEDUP_S)
GOT_WAIT_S = 30                   # pursuit_got: how long the plan may take to move past an item that landed
HERE_GAP_S = 1800                 # zoning into the move's dungeon: what drops there, once per place in this long
PLAN_ALTS = 2                     # the alternatives followed through on with the first move (what bis_plan tells)
AH_CITIES = {"Alliance": ("Stormwind City", "Ironforge", "Darnassus"),       # an auction house step is said in these
             "Horde": ("Orgrimmar", "Undercity", "Thunder Bluff")}
TRY_UNITS = {"inst": "runs", "raid": "weeks", "mob": "kills"}                # what a try at a move of this kind is


def version_tuple(v):
    """An addon version as a tuple ("0.22.0" -> (0, 22, 0)); () for none."""
    m = re.match(r"\s*(\d+(?:\.\d+)*)", str(v or ""))
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def pursuit_key(s):
    """The key a character's pursuit is kept under (bis.char_key's rule): "Name-Realm" when the realm is known (its
    spaces left out), else "Name"; "" without a name."""
    name = str((s or {}).get("name") or "")
    realm = str((s or {}).get("realm") or "").replace(" ", "")
    return "%s-%s" % (name, realm) if name and realm else name


def move_key(char, m):
    """next_move's told key for a move (kept by the planner: told / mark_told): the character and the move's own key
    (what to do and its items sorted: the same move, however a kill reorders what it rolls)."""
    own = m.get("key")
    key = own if isinstance(own, str) and own else "%s|%s" % (
        m.get("act"), ",".join(str(i) for i in sorted(offline_coach.move_items(m))))
    return "next_move|%s|%s" % (char, key)


def planner_on(planner):
    """Whether the planner plans now (pursuit.enabled): its enabled() or _on() when it has one, else True."""
    for name in ("enabled", "_on"):
        fn = getattr(planner, name, None)
        if callable(fn):
            try:
                return fn() is not False
            except Exception as e:  # noqa: BLE001 - on then
                log_once("rules.pursuit_on", e)
                return True
    return True


def _takes(fn, name):
    """Whether a callable takes a keyword argument of this name (or any keyword)."""
    try:
        ps = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == name or p.kind is inspect.Parameter.VAR_KEYWORD for p in ps)


def plan_of(planner, char, fresh=False):
    """planner.plan(char) ({"goal", "moves", "alts"}), worked out now (fresh: an item just landed) when the planner can
    be asked to; None when it gives none."""
    fn = planner.plan
    plan = fn(char, fresh=True) if fresh and _takes(fn, "fresh") else fn(char)
    return plan if isinstance(plan, dict) else None


def _count(n):
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else None


def _ledger(planner, char, item_id):
    """planner.ledger(char, item id), the hunt so far ({"tries", "range", "odds", ...}), when it has one; else None."""
    fn = getattr(planner, "ledger", None)
    if not callable(fn):
        return None
    try:
        got = fn(char, item_id)
    except Exception as e:  # noqa: BLE001 - none then
        log_once("rules.pursuit_ledger", e)
        return None
    return got if isinstance(got, dict) else None


def pursuit_runs(planner, char, item_id, seen: int | None = 0):
    """The runs so far at an item without it: the planner's own count from the tries it keeps in data/pursuit.json
    (Planner.ledger(char, item id)["tries"], else Planner.runs(char, item id)) when it has one, else `seen` (the kills
    the caller counted)."""
    n = _count((_ledger(planner, char, item_id) or {}).get("tries"))
    if n is not None:
        return n
    fn = getattr(planner, "runs", None)
    if callable(fn):
        try:
            n = _count(fn(char, item_id))
        except Exception as e:  # noqa: BLE001 - the caller's count then
            log_once("rules.pursuit_runs", e)
            n = None
        if n is not None:
            return n
    return seen


def pursuit_range(planner, char, item_id, move=None):
    """(fewest, most) runs an item usually takes: the planner's ledger for it ("range": [p25, p75]), else the move's
    (odds_range); None when nothing says."""
    return _pair((_ledger(planner, char, item_id) or {}).get("range")) or odds_range(move)


def pursuit_src(planner, char, item_id, move=None):
    """The source the odds of an item's hunt name ("classic's chance", "web, unconfirmed", "a guess"): the planner's
    ledger odds for it, else the move's (offline_coach.odds_source); None when none is named (the player's own kills)."""
    for odds in ((_ledger(planner, char, item_id) or {}).get("odds"), (move or {}).get("odds")):
        if isinstance(odds, str) and odds:
            return offline_coach.odds_source(odds)
    return None


def planner_told(planner, key, char, mark=False):
    """planner.told(key) (mark: mark_told(key)) for the character: with it (char_key_) when the planner takes one, so
    a key said before its first poll is the right character's."""
    fn = planner.mark_told if mark else planner.told
    try:
        takes = len(inspect.signature(fn).parameters) >= 2
    except (TypeError, ValueError):
        takes = False
    return fn(key, char) if takes and char else fn(key)


def _pair(r):
    """(lo, hi) from a [p25, p75] list, or None."""
    if isinstance(r, (list, tuple)) and len(r) >= 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                            for x in r[:2]):
        return int(round(r[0])), int(round(r[1]))
    return None


def _chance(move):
    """A move's chance a try ("p", else "q") when it is one (0 < p < 1), else None."""
    m = move or {}
    q = m.get("p") if m.get("p") is not None else m.get("q")
    return q if isinstance(q, (int, float)) and not isinstance(q, bool) and 0 < q < 1 else None


def odds_range(move):
    """(fewest, most) runs a move usually takes, the 25 to 75% range of tries: the planner's "range" ([p25, p75]),
    else from its chance per try ("p" or "q": effort.quantiles), else from its odds as said ("usually 3 to 10 runs");
    None when none of them says."""
    m = move or {}
    got = _pair(m.get("range"))
    if got:
        return got
    q = _chance(m)
    if q is not None:
        try:
            import effort
            got = _pair(effort.quantiles(q))
            if got:
                return got
        except Exception as e:  # noqa: BLE001 - from the words then
            log_once("rules.odds_range", e)
    hit = re.search(r"(\d+) to (\d+)", str(m.get("odds") or ""))
    return (int(hit.group(1)), int(hit.group(2))) if hit else None


def unlucky(n, move=None, rng=None):
    """Whether n tries without the item is past what most take ("bad luck, not a bad plan"): past the median try for
    the move's chance a try (effort.tries_for), else past the top of the usual range."""
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        return False
    q = _chance(move)
    if q is not None:
        try:
            import effort
            median = effort.tries_for(q, 0.5)
            if isinstance(median, int):
                return n > median
        except Exception as e:  # noqa: BLE001 - by the range then
            log_once("rules.unlucky", e)
    return bool(rng) and n > rng[1]


def same_place(zone, place):
    """Is the zone the player is in the place a move names ("Blackrock Spire" for "Upper Blackrock Spire")?"""
    a, b = (" ".join(re.sub(r"[^a-z0-9' ]+", " ", str(x or "").lower()).split()) for x in (zone, place))
    return bool(a and b) and (a == b or (min(len(a), len(b)) >= 4 and (a in b or b in a)))


def step_reminders(m, name_fn, srcs_fn, faction=None):
    """A move's steps that are best said where they happen, as reminders for Reminders.replace (said once per arrival,
    no nudges): a vendor's, near the vendor (its npc id and name) and in its town; an auction house buy, in the side's
    auction house cities. [] for another move."""
    mk, where = offline_coach.move_act(m)
    names = [n for n in (name_fn(i) for i in offline_coach.move_items(m)) if n]
    if not names or mk not in ("vendor", "ah"):
        return []
    things = " and ".join(offline_coach.the_item(n) for n in names[:2])
    if mk != "vendor":
        cities = AH_CITIES.get(str(faction or "")) or AH_CITIES["Alliance"] + AH_CITIES["Horde"]
        return [{"what": "check the auction house for %s" % things, "when": {"kind": "place", "place": list(cities)},
                 "every_min": 0}]
    src = {}
    for iid in offline_coach.move_items(m):
        src = next((x for x in srcs_fn(iid) if x.get("kind") in ("vendor", "rep")
                    and (not isinstance(where, int) or x.get("npc") == where)), None) or {}
        if src:
            break
    # (the planner's own names fill in what the sources don't say)
    who = src.get("npc_name") or (m.get("npc_name") if isinstance(m.get("npc_name"), str) else None)
    town = src.get("zone_name") or (m.get("place") if isinstance(m.get("place"), str) else None)
    what = "buy %s from %s" % (things, who) if who else "buy %s" % things
    out = []
    if isinstance(where, int) or who:
        out.append({"what": what, "when": {"kind": "npc", "npc": where if isinstance(where, int) else None,
                                           "name": who}, "every_min": 0})
    if town:
        out.append({"what": what, "when": {"kind": "place", "place": town}, "every_min": 0})
    return out


def allowed(kind, flags):
    """May the coach speak about a trigger of this kind under these switches (GameState.coach_flags(): the
    commentary types the player switches off in game, /coach <type> off)?"""
    cat = CATEGORY.get(kind, "route")
    if cat is None:
        return True
    return flags.get("coach", True) and flags.get(cat, True)


# never a callout: own buff cast (state bookkeeping), player feedback mark (app), a new flight path (sent by addons
# before 0.15.3; list F has them)
SILENT_EVENTS = {"cst", "mark", "fpn"}
EVENT_TEXT = {
    "acc": ("quest_accepted", 3), "tin": ("quest_turned_in", 2), "rem": ("quest_removed", 3),
    "lvl": ("level_up", 1), "die": ("died", 1), "alive": ("released", 2), "unghost": ("resurrected", 2),
    "hs": ("hearthed", 3), "bind": ("hearth_bound", 3), "zone": ("zone_changed", 3), "taxi": ("flight_taken", 3),
    "learn": ("spell_learned", 3),
}


class Rules:
    @property
    def opt(self):
        """The rules settings, read through settings_manifest.json (defaults, ranges)."""
        return settings.Settings("rules", self.cfg)

    def __init__(self, cfg):
        self.cfg = cfg
        self.cool = {}
        self.started = False
        self.last_review = time.time()
        self.prev_step = None
        self.away_key, self.away_since = None, 0.0   # waypoint the track is judged against, and since when
        self.watched = set()          # buff names the player asked to be reminded about (lower-case)
        self.buff_ver = 0
        self.prev_buffs = None        # key -> name of relevant buffs last time
        self.pending_drops = []       # buff names that fell off, said once out of combat
        self.warned = set()           # (key, expiry) already warned about
        self.gear = None              # gear.Gear, set by the app
        self.gear_ver = (0, 0, 0, 0, (), ())
        self.gear_told = {}           # item key+name -> level when we last told about it
        self.pending_gear = []
        self.upgrades = []            # latest evaluation, for the coach and dashboard
        self.rewards = []
        self.bag_items = []           # every equippable bag item with a verdict
        self.addon_status = None      # versions.status(), refreshed by the app
        self.kb_fn = None             # -> knowledge.api.KnowledgePack or None (set by the app when data/kb.db loads)
        self.decay_told = {}          # quest id -> time of its last turnin_decay_soon
        self.tp_since = {}            # level -> first time unspent talent points were seen
        self.told_levels = set()      # ("talent"|"train", level) already said
        self.cons_prev = {}           # consumable kind -> last count
        self.fp_told = set()          # flight node ids already pointed out
        self.tapped_told = {}         # npc id -> time
        self.vendor_told = set()      # vendor treasure indexes already pointed out this session
        self.live: Any = None         # live_data.LiveData (the addon's Data/Live.lua), set by the app
        self.research: Any = None     # research.Research (its corrections), set by the app
        self.bis: Any = None          # bis.BisRanker (what the coach was told, kept in data/bis.json), set by the app
        self.bis_said = set()         # next_upgrade / enchant_missing keys said this session
        self.enchant_t = 0.0          # when enchant_missing was last raised
        self.sims: Any = None         # sims.Sims (its talent check, and what was told: data/sims.json), set by the app
        self.pursuit: Any = None      # pursuit.Planner (the next move toward the next BiS piece), set by the app
        self.reminders: Any = None    # reminders.Reminders (the move's vendor and auction house steps), set by the app
        self._pursuit_reset(None)

    def watch(self, name, on=True):
        n = name.strip().lower()
        if on:
            self.watched.add(n)
        else:
            self.watched.discard(n)

    def _relevant(self, b):
        c = settings.Settings("rules.buffs", self.cfg.get("buffs") or {})
        name = b["name"].lower()
        if name in self.watched:
            return True
        if any(i.lower() in name for i in c.get("ignore")):
            return False
        # only buffs the player can recast themselves (not NPC/other players' buffs) unless asked
        if not b.get("castable", b["mine"]) and not c.get("remind_others"):
            return False
        return b["duration"] >= c.get("min_duration") or b["id"] in ("mh", "oh")

    def _buffs(self, state, s, busy, out):
        c = settings.Settings("rules.buffs", self.cfg.get("buffs") or {})
        if not c.get("enabled"):
            return
        now_t = as_int(state.core.get("t"), 0)
        names_now = {b["name"] for b in s["buffs"]}
        # Forever freezes the aura list in combat, so only a list read out of combat can say a buff is gone.
        if state.buffs_version != self.buff_ver and not s.get("buffs_stale", s["buffs_hidden"]):
            self.buff_ver = state.buffs_version
            cur = {b["id"]: b["name"] for b in s["buffs"] if self._relevant(b)}
            if self.prev_buffs is not None:
                for k, name in self.prev_buffs.items():
                    if k not in cur and name not in names_now and name not in (n for n, _ in self.pending_drops):
                        self.pending_drops.append((name, now_t))
            self.prev_buffs = cur
        # a recast (or a late list update) cancels the reminder
        self.pending_drops = [(n, t0) for n, t0 in self.pending_drops if n not in names_now]
        grace = c.get("drop_grace")
        due = [n for n, t0 in self.pending_drops if (now_t - t0) % 1000000 >= grace]
        if due and not busy:
            out.append(Trigger("buffs_dropped", {"buffs": due}, 2))
            self.pending_drops = [(n, t0) for n, t0 in self.pending_drops if n not in due]
        warn = c.get("warn_before")
        for b in s["buffs"]:
            r = b["remaining"]
            if r is None or not self._relevant(b) or b["duration"] < 2 * warn:
                continue
            if 0 < r <= warn and (b["id"], "w") not in self.warned and not s["dead"]:
                self.warned.add((b["id"], "w"))
                out.append(Trigger("buff_expiring", {"buff": b["name"], "seconds": r}, 3))
            elif r > warn:
                self.warned.discard((b["id"], "w"))

    def _gear(self, state, s, busy, out):
        if not self.gear or not s.get("class") or not s.get("level"):
            return
        sk = s.get("skills") or {}
        ver = (state.gear_version["W"], state.gear_version["I"], state.gear_version["R"], s["level"],
               tuple(sorted(sk.get("weapons") or ())), tuple(sorted(sk.get("armor") or ())),
               getattr(self.gear, "sim_version", 0))           # (WoWSims weighed the gear anew)
        if ver == self.gear_ver:
            if self.pending_gear and not busy:
                out.append(Trigger("gear_upgrade", {"upgrades": self.pending_gear[:3]}, 2))
                self.pending_gear = []
            return
        old_r = self.gear_ver[2] if len(self.gear_ver) > 2 else 0
        self.gear_ver = ver
        worn, bags = state.gear["W"], state.gear["I"]
        self.bag_items = self.gear.compare_all(worn, bags, s["class"], s["level"], sk)
        self.upgrades = sorted([u for u in self.bag_items if u["verdict"].startswith("upgrade")],
                               key=lambda x: -x["gain"])
        # an item the pursuit went for (Glide's next move, still missing at its last look, or one that landed: this
        # session's, and the planner's own across restarts) is pursuit_got's to say, wherever it moves in the bags
        ids = self._pursued_ids(s)
        chased = {it.get("key") for it in bags if it.get("id") in ids} if ids else set()
        for u in self.upgrades:
            tag = "%s|%s" % (u["key"], u["item"])
            if u["key"] in chased:
                self.gear_told[tag] = "ok"
                continue
            now_ok = u["wait_level"] is None
            told = self.gear_told.get(tag)
            if told is None or (now_ok and told == "wait"):
                self.gear_told[tag] = "ok" if now_ok else "wait"
                self.pending_gear.append({"item": u["item"], "slot": u["slot_name"], "replaces": u["replaces"],
                                          "gain": u["gain"], "equip_at_level": u["wait_level"]})
        if state.gear_version["R"] != old_r and state.gear["R"]:
            self.rewards = self.gear.rank_rewards(worn, state.gear["R"], s["class"], s["level"], sk)
            out.append(Trigger("reward_choice", {"ranked": self.rewards}, 1))
        elif not state.gear["R"]:
            self.rewards = []
        if self.pending_gear and not busy:
            out.append(Trigger("gear_upgrade", {"upgrades": self.pending_gear[:3]}, 2))
            self.pending_gear = []

    def _ready(self, key, seconds):
        now = time.time()
        if now - self.cool.get(key, 0) < seconds:
            return False
        self.cool[key] = now
        return True

    def check(self, state, ring_events):
        out = []
        s = state.summary()
        if not self.started and s["level"] and state.frames >= 15:
            self.started = True
            out.append(Trigger("session_start", {}, 2, deep=True))
        for _id, kind, arg in ring_events:
            if kind in SILENT_EVENTS:
                continue
            name, prio = EVENT_TEXT.get(kind, (kind, 3))
            out.append(Trigger(name, {"arg": arg}, prio))

        # RXP step changed
        if s["step"] is not None and s["step"] != self.prev_step:
            if self.prev_step is not None:
                out.append(Trigger("step_changed", {"from": self.prev_step, "to": s["step"]}, 3))
            self.prev_step = s["step"]

        busy = s["combat"] or s["on_taxi"] or s["dead"]
        stuck_after = self.opt.get("stuck_minutes") * 60
        if s["step"] is not None and s["on_step_seconds"] > stuck_after and not busy \
                and self._ready("stuck:%s" % s["step"], stuck_after):
            out.append(Trigger("stuck_on_step", {"minutes": s["on_step_seconds"] // 60}, 2))

        self._away(state, s, busy, out)

        st = self.addon_status
        if st and st.get("ok") is False and self._ready("addon_outdated", 600):
            out.append(Trigger("addon_outdated", {"problem": st["problem"], "fix": st["fix"]}, 2))
        self._buffs(state, s, busy, out)
        self._gear(state, s, busy, out)
        self._vendor(state, s, busy, out)
        if s["free_bags"] is not None and s["free_bags"] <= 2 and self._ready("bags", 600):
            out.append(Trigger("bags_nearly_full", {"free": s["free_bags"]}, 3))
        if s["durability"] is not None and s["durability"] <= 20 and self._ready("repair", 900):
            out.append(Trigger("low_durability", {"pct": s["durability"]}, 3))
        if s["combat"] and s["hp"] is not None and 0 <= s["hp"] <= 30 and self._ready("danger", 45):
            out.append(Trigger("low_health_in_combat", {"hp": s["hp"],
                                                         "target": (s["target"] or {}).get("name")}, 1))
        review = self.opt.get("review_minutes") * 60
        if time.time() - self.last_review > review and not busy:
            self.last_review = time.time()
            out.append(Trigger("pace_review", {}, 3, deep=True))
        self._kb_triggers(s, out)
        self._extras(state, s, busy, out)
        self._live(s, out)
        self._corrected(s, out)
        self._bis(state, s, out)
        self._pursuit(state, s, busy, out)
        self._talent_check(s, out)
        return out

    def _talent_check(self, s, out):
        """talent_check: one of WoWSims' own talent builds for the class sims at least 3% higher than the player's on
        their gear, beyond the sims' noise, each with its own rotation (sims.Sims.talent_check: the check runs once per
        WoWSims release and per talent change at 60, in the background) -> {build, pct, link} (link: the player's gear
        with that build on WoWSims' site, for the coach screen), priority 3. Said once per release and talents (kept in
        data/sims.json: Sims.mark_told), at a quiet moment: after the session started, out of combat, alive, in a frame
        that raised nothing else; while gear talk is muted (/coach gear off) it waits, untold."""
        sims = self.sims
        if sims is None or not self.started or out or s.get("combat") or s.get("dead") \
                or (s.get("level") or 0) < 60 or not self._ready("talent_check", TALENT_POLL_S):
            return
        try:
            facts = sims.talent_check(s)
            if not facts or not facts.get("build") or not allowed("talent_check", s.get("coach_flags") or {}):
                return
            sims.mark_told(facts["key"], facts.get("char"))
            out.append(Trigger("talent_check", {"build": facts["build"], "pct": facts.get("pct"),
                                                "link": facts.get("link")}, 3))
        except Exception as e:  # noqa: BLE001 - an extra; never break the rules loop
            log_once("rules.talent_check", e)

    def _bis(self, state, s, out):
        """Glide's own best-in-slot ranks at 60 (bis.BisRanker, handed to the gear with Gear.set_bis), said once per
        change and never again after a restart (what was told is kept in data/bis.json: BisRanker.told / mark_told):
        next_upgrade, the best item the character can get now ({item, slot, dps, source}, with "with" (the weapon it
        is paired with: the gain is the pair's) and "cond" (a bonus the number leaves out) when so; priority 2), and
        enchant_missing, a worn item without an enchant and the enchant worth most on it ({slot, item, enchant, dps},
        priority 3: one slot at a time, at most every ENCHANT_GAP_S; only from addon BIS_ADDON on, since an older one
        doesn't send enchants). Said at a quiet moment: after the session started, out of combat, alive, in a frame that
        raised nothing else. While the player has gear talk muted (/coach gear off) they wait, untold."""
        g, ranker = self.gear, self.bis
        if g is None or ranker is None or not self.started or out or s.get("combat") or s.get("dead") \
                or (s.get("level") or 0) < 60 or not self._ready("bis", BIS_POLL_S):
            return
        try:
            cb = g.bis_for(s.get("name")) if g.bis_on() else None
            worn = state.gear.get("W") or []
            if not cb or not worn:
                return
            flags = s.get("coach_flags") or {}
            sends = version_tuple(s.get("addon_version")) >= BIS_ADDON        # (worn enchants and suffixes)
            # (while the pursuit has a move, or hasn't looked at the plan yet, the next move is the one "next" said:
            # next_move, weighed by effort)
            waits = self.pursuit is not None and (self.p_move is not None or not self.p_looked)
            t = None if waits else self._next_upgrade(cb, state, flags, ranker, sends)
            if t is None and sends:
                t = self._enchant_missing(cb, state, worn, flags, ranker)
            if t is not None:
                out.append(t)
        except Exception as e:  # noqa: BLE001 - an extra; never break the rules loop
            log_once("rules.bis", e)

    def _bis_item(self, iid, state):
        """(name, where it comes from) of an item: the research's brief, else the name the game sent for it."""
        b = {}
        if self.research is not None:
            try:
                b = self.research.brief(iid) or {}
            except Exception as e:  # noqa: BLE001 - the game's name
                log_once("rules.bis_brief", e)
        name = b.get("name")
        if not name:
            for kind in ("W", "I", "R"):
                for it in state.gear.get(kind) or ():
                    if it.get("id") == iid and it.get("name"):
                        name = it["name"]
        return name, b.get("comes_from")

    def _told(self, key, ranker):
        """Whether a next_upgrade / enchant_missing key was said (this session, or before: the ranker keeps it); one
        said before is remembered here, so the ranker isn't asked again every poll."""
        if key in self.bis_said:
            return True
        if ranker.told(key):
            self.bis_said.add(key)
            return True
        return False

    @staticmethod
    def _has(state, iid, sfx, sfx_known):
        """Whether the character wears the item, or has it in the bags (the addon sends no suffix for those: the item
        is enough); a worn one with another random suffix than sfx isn't it. sfx_known: the addon sends worn suffixes
        (from BIS_ADDON: a worn entry without one has none); else a worn one's suffix is unknown, so the item is
        enough."""
        for kind in ("W", "I"):
            for it in state.gear.get(kind) or ():
                if it.get("id") != iid:
                    continue
                worn_sfx = (as_int(it.get("suffix")) or 0 if sfx_known else None) if kind == "W" else None
                if not sfx or worn_sfx is None or abs(worn_sfx) == abs(sfx):
                    return True
        return False

    def _next_upgrade(self, cb, state, flags, ranker, sfx_known=False):
        """next_upgrade from the ranks' "next" (the largest gain beyond the sims' noise among items the character can
        get now, with a source and no profession it lacks): not while it is worn (with that random suffix) or in the
        bags already (not ranked again yet, or gear_upgrade's), once per item and suffix. A weapon simmed with a
        partner (a one-hander and an off hand in place of a two-hander) names it ("with"): the gain is the pair's;
        "cond" when its bonus against a creature type or in an area isn't in the number. A web source said is kept as
        told by the research (Research.note_told), so the game can correct it."""
        nx = cb.get("next")
        if not isinstance(nx, dict) or not isinstance(nx.get("id"), int) or nx.get("avail") not in (None, "now") \
                or not gear_mod.beyond_noise(nx.get("d"), nx.get("s")):
            return None
        iid, sfx = nx["id"], as_int(nx.get("sfx"), 0) or 0
        if self._has(state, iid, sfx, sfx_known):
            return None
        key = "next_upgrade|%d|%d" % (iid, sfx)
        if self._told(key, ranker) or not allowed("next_upgrade", flags):
            return None
        name, source = self._bis_item(iid, state)
        if nx.get("sfx") and isinstance(nx.get("name"), str) and nx["name"]:
            name = nx["name"]                      # (the ranker's, with the random suffix it was simmed with)
        if not name:
            return None                            # (nothing to call it yet: never an id)
        if not source and isinstance(nx.get("source"), str):
            source = nx["source"]
        facts = {"item": name, "slot": gear_mod.slot_word(nx.get("slot")), "dps": round(float(nx["d"]), 1),
                 "source": source or None}
        if nx.get("with"):
            mate = nx.get("with_name") if isinstance(nx.get("with_name"), str) else None
            mate = mate or self._bis_item(nx["with"], state)[0]
            if not mate:
                return None                        # (a pair's gain, and no name for the other half yet)
            facts["with"] = mate
        if nx.get("cond"):
            facts["cond"] = True
        self.bis_said.add(key)
        ranker.mark_told(key)
        self._note_told(iid)
        return Trigger("next_upgrade", facts, 2)

    def _note_told(self, iid):
        """The research keeps a web source the coach said as told (Research.note_told): corrected once if the game
        shows it wrong."""
        note = getattr(self.research, "note_told", None)
        if note is None:
            return
        try:
            note(iid)
        except Exception as e:  # noqa: BLE001 - said; no correction then
            log_once("rules.bis_told", e)

    def _enchant_missing(self, cb, state, worn, flags, ranker):
        """enchant_missing from the ranks' "enchants" (gear.bis_enchants): the worn item it names has no enchant
        now (the addon sends EN only when there is one), the gain is beyond the sims' noise; the largest first. Said
        once per worn item: the told key names the item, not the enchant (two close enchants can trade places in a later
        rank after an unrelated gear change, and nothing about this item changed)."""
        now = time.time()
        if now - self.enchant_t < ENCHANT_GAP_S:
            return None
        by_id = {it.get("id"): it for it in worn}
        best = None
        for e in gear_mod.bis_enchants(cb):
            it = by_id.get(e["item"]) if e["item"] is not None else self._worn_in(worn, e["slot"])
            if it is None or it.get("enchant") or not e["name"] or not gear_mod.beyond_noise(e["d"], e["s"]):
                continue
            key = "enchant_missing|%s" % it.get("id")
            if self._told(key, ranker):
                continue
            if best is None or e["d"] > best[0]["d"]:
                best = (e, it, key)
        if best is None or not allowed("enchant_missing", flags):
            return None
        e, it, key = best
        self.bis_said.add(key)
        ranker.mark_told(key)
        self.enchant_t = now
        return Trigger("enchant_missing", {"slot": gear_mod.slot_word(e["slot"]), "item": it.get("name"),
                                           "enchant": e["name"], "dps": round(e["d"], 1)}, 3)

    # ---- the next move toward the next best-in-slot piece (pursuit.Planner), and its follow-through ----------------
    def _pursuit_reset(self, char):
        """Forget what the pursuit rules saw (at the start, and when another character plays)."""
        self.p_char = char            # the character key the state below is for
        self.p_said = set()           # next_move / pursuit_got keys said this session (the planner keeps them too)
        self.p_looked = False         # the plan was looked at for this character (next_upgrade waits for it)
        self.p_move = None            # the plan's first move at the last look ({"act", "items", "odds", "why", ...})
        self.p_moves = []             # ... it and the PLAN_ALTS alternatives: what the player was told of
        self.p_missing = {}           # item id -> its name: their items the character didn't have at the last look
        self.p_landed = {}            # item id -> {"t", "name", "equip"}: one of those, theirs now (pursuit_got)
        self.p_got = set()            # item ids that landed this session (gear_upgrade never says them)
        self.p_bosses = {}            # npc id -> [item ids]: the bosses that drop those items (a kill is a run)
        self.p_rolls = []             # [{"npc", "t", "items"}]: such a boss died; its roll is said once the loot is in
        self.p_kills = {}             # npc id -> when its kill was last counted
        self.p_base = {}              # item id -> the planner's run count when this session first followed it
        self.p_runs = {}              # item id -> kills counted without it this session
        self.p_zone = None            # the zone the character was last seen in (zoning into the move's place)
        self.p_here = None            # (place, text): the zone-in line waiting for a quiet moment
        self.p_reminded = False       # the first move whose steps are placed as reminders (its key; None: no move)

    def _pursuit(self, state, s, busy, out):
        """Glide's next move toward the character's next best-in-slot piece at 60, weighed by effort as well as DPS
        (pursuit.Planner: plan(char) -> {"goal", "moves", "alts"}), and the follow-through as the player plays, on the
        first move and its PLAN_ALTS alternatives (what the player is told of):
        - next_move {text, odds, why} (priority 2): the plan's first move in words (offline_coach.move_parts), its odds
          as the planner says them (one number and its range, the source named) and the reasons (said only when
          asked), "also" for a run that rolls more (the others: the odds are its lead item's); once per move, never
          again after a restart (the planner keeps what was told: told / mark_told).
          Zoning into the dungeon or raid of the move or an alternative: {text, here: true}, what drops there for
          them, once per place in HERE_GAP_S.
        - pursuit_roll {item, tries, range} (priority 3): a boss that drops one of their items died (the target, dead)
          and, ROLL_WAIT_S on (the loot, a group's roll), the item isn't theirs: the runs so far (the planner's count,
          or this session's kills on top of its count when it missed one), the range most runs take (pursuit_range),
          "src" the source its odds name below 0.9 sure ("classic's chance"), "unit" ("runs", "weeks" for a raid,
          "kills" for a creature outside an instance), "unlucky" past the median try. Once per kill (one per boss in
          KILL_GAP_S); counted whether or not it is said.
        - pursuit_got {item, next} (priority 2): one of their items the character didn't have is in the bags
          ("equip") or worn; the plan is worked out again at once, and it is said once the plan moved past it (at most
          GOT_WAIT_S on) with the next move in words (whose next_move isn't said again); gear_upgrade never says that
          item (_gear leaves it to this).
        The first move's vendor and auction house steps are placed as reminders (source "pursuit", said to that
        character only), replaced when it changes. Nothing below 60, before the session started or while the planner is off (pursuit.enabled); said never
        in combat, on a flight or dead, one a frame, next_move only in a frame that raised nothing else; while gear
        talk is muted (/coach gear off) next_move waits, untold."""
        planner = self.pursuit
        if planner is None or not self.started:
            return
        try:
            char = pursuit_key(s)
            if not char:
                return
            if char != self.p_char:
                self._pursuit_reset(char)              # (the reminders a move placed are its character's alone)
            if (s.get("level") or 0) < 60:
                return
            if not planner_on(planner):
                self._pursuit_off()
                return
            held = self._held(state)
            landed = self._pursuit_watch(state, s, held)
            if landed or self._ready("pursuit", PURSUIT_POLL_S):
                self._pursuit_look(planner, char, state, s, held, fresh=landed)
            if busy:
                return
            flags = s.get("coach_flags") or {}
            t = self._pursuit_got(planner, char, state, held, flags, out) or self._pursuit_roll(char, held, flags) \
                or self._pursuit_here(flags)
            if t is None and not out:
                t = self._next_move(planner, char, state, flags)
            if t is not None:
                out.append(t)
        except Exception as e:  # noqa: BLE001 - an extra; never break the rules loop
            log_once("rules.pursuit", e)

    def _pursuit_off(self):
        """The planner is off: no move (next_upgrade speaks again), its reminders gone."""
        self.p_move, self.p_moves, self.p_missing, self.p_bosses, self.p_looked = None, [], {}, {}, True
        if self.reminders is not None and self.p_reminded is not None:
            self.p_reminded = None
            self.reminders.replace("pursuit", [])

    def _pursued_ids(self, s):
        """The item ids gear_upgrade leaves to the pursuit: the ones it follows still missing, the ones that landed
        (this session's, and the planner's own record of them for this character, kept across restarts)."""
        ids = set(self.p_missing) | set(self.p_landed) | self.p_got
        planner = self.pursuit
        fn = getattr(planner, "owned", None)
        char = pursuit_key(s)
        if callable(fn) and char and (s.get("level") or 0) >= 60 and planner_on(planner):
            try:
                got = fn(char)
                ids |= {i for i in (got if isinstance(got, (list, tuple, set)) else ()) if isinstance(i, int)}
            except Exception as e:  # noqa: BLE001 - this session's then
                log_once("rules.pursuit_owned", e)
        return ids

    @staticmethod
    def _held(state):
        """{item id: "W" (worn) or "I" (in the bags)}."""
        out = {}
        for where in ("I", "W"):
            for it in state.gear.get(where) or ():
                if isinstance(it.get("id"), int):
                    out[it["id"]] = where
        return out

    def _srcs(self, iid):
        """Where an item comes from: the research's sources (Src dicts), or []."""
        r = self.research
        try:
            f = r.facts(iid) if r is not None else None
        except Exception as e:  # noqa: BLE001 - not known then
            log_once("rules.pursuit_facts", e)
            f = None
        return [x for x in (f or {}).get("s") or () if isinstance(x, dict)]

    def _names(self, m, state):
        """-> a move's item names: the planner's own ("names"), else the research's or the game's; never an id (the
        planner's "item 12345" stand-in isn't a name)."""
        given = offline_coach.given_names(m)
        return lambda i: given.get(i) or self._bis_item(i, state)[0]

    def _move_parts(self, m, state):
        """(text, also) of a move in words (offline_coach.move_parts)."""
        return offline_coach.move_parts(m, self._names(m, state), self._srcs)

    @staticmethod
    def _move_key(char, m):
        return move_key(char, m)

    def _p_told(self, key, planner):
        """Whether a pursuit key was said (this session, or before: the planner keeps it)."""
        if key in self.p_said:
            return True
        if planner_told(planner, key, self.p_char):
            self.p_said.add(key)
            return True
        return False

    def _pursuit_watch(self, state, s, held):
        """Every frame: one of the followed items the character didn't have is theirs now (landed), a boss that drops
        one died (a roll to say once the loot is in), the character zoned into the dungeon or raid of the first move or
        an alternative (what drops there for them). -> whether an item landed."""
        now = time.time()
        landed = [i for i in self.p_missing if i in held]
        for iid in landed:
            name = self.p_missing.pop(iid)
            for where in ("W", "I"):
                name = next((it.get("name") for it in state.gear.get(where) or () if it.get("id") == iid
                             and it.get("name")), None) or name                  # (the game's, with its suffix)
            self.p_landed.setdefault(iid, {"t": now, "name": name, "equip": held[iid] == "I"})
            self.p_got.add(iid)
        t = s.get("target") or {}
        npc = as_int(t.get("npc"))
        if t.get("dead") and npc in self.p_bosses and now - self.p_kills.get(npc, now - KILL_GAP_S) >= KILL_GAP_S:
            self.p_kills[npc] = now
            self.p_rolls.append({"npc": npc, "t": now, "items": list(self.p_bosses[npc])})
        zone = s.get("zone")
        if zone and zone != self.p_zone:
            before, self.p_zone, self.p_here = self.p_zone, zone, None      # (left before it could be said: gone)
            for m in self.p_moves if before is not None else ():
                places = offline_coach.move_place(m, self._srcs)
                if any(same_place(zone, p) for p in places):
                    text = offline_coach.here_text(m, self._names(m, state), self._srcs)
                    self.p_here = (places[0], text) if text else None
                    break
        return bool(landed)

    def _pursuit_look(self, planner, char, state, s, held, fresh=False):
        """Look at the plan (fresh: worked out now, an item just landed): its first move and PLAN_ALTS alternatives,
        the items they name that the character doesn't have (and the planner's run count for each one first seen),
        the bosses that drop them; the first move's steps placed as reminders when it changed. No plan (None) leaves
        what was seen."""
        self.p_looked = True                           # (a planner that fails: next_upgrade speaks while no move)
        plan = plan_of(planner, char, fresh)
        if plan is None:
            return
        ok = [m for m in plan.get("moves") or () if isinstance(m, dict) and offline_coach.move_items(m)]
        first = ok[0] if ok and ok[0] is (plan.get("moves") or [None])[0] else None
        moves, keys = [], set()
        for m in ([first] if first else []) + list(plan.get("alts") or ()) + ok[1:]:
            k = move_key(char, m) if isinstance(m, dict) and offline_coach.move_items(m) else None
            if k is not None and k not in keys and len(moves) <= PLAN_ALTS:
                keys.add(k)
                moves.append(m)
        missing, bosses = {}, {}
        for m in moves:
            mk, where = offline_coach.move_act(m)
            name = self._names(m, state)
            for iid in offline_coach.move_items(m):
                if iid not in held and iid not in missing:
                    missing[iid] = self.p_missing.get(iid) or name(iid)
                    if iid not in self.p_base:
                        self.p_base[iid] = pursuit_runs(planner, char, iid, 0) or 0
                for npc in self._bosses_of(m, mk, where, iid):
                    got = bosses.setdefault(npc, [])
                    if iid not in got:
                        got.append(iid)
        self.p_move, self.p_moves, self.p_missing, self.p_bosses = first, moves, missing, bosses
        self._pursuit_reminders(first, char, state, s)

    def _pursuit_got(self, planner, char, state, held, flags, out):
        """pursuit_got for an item that landed, once the plan moved past it (or GOT_WAIT_S on); its gear_upgrade line,
        if one is about to be said, goes (this one says it's theirs)."""
        now = time.time()
        for iid, got in list(self.p_landed.items()):
            moved = self.p_move is None or iid not in offline_coach.move_items(self.p_move)
            if not moved and now - got["t"] < GOT_WAIT_S:
                continue
            del self.p_landed[iid]
            self._drop_gear_line(got["name"], out)
            key = "pursuit_got|%s|%d" % (char, iid)
            if self._p_told(key, planner) or not allowed("pursuit_got", flags):
                continue
            self.p_said.add(key)
            planner_told(planner, key, char, mark=True)
            facts = {"item": got["name"], "next": None}
            if got["equip"] and held.get(iid) == "I":
                facts["equip"] = True
            m = self.p_move
            if moved and m is not None:
                text, also = self._move_parts(m, state)
                if text:
                    facts["next"] = offline_coach.move_line(text, m.get("odds"), also)
                    nk = self._move_key(char, m)              # (said here: no next_move for it after)
                    self.p_said.add(nk)
                    planner_told(planner, nk, char, mark=True)
            return Trigger("pursuit_got", facts, 2)
        return None

    def _drop_gear_line(self, name, out):
        """gear_upgrade doesn't say an item pursuit_got says: out of a trigger raised this frame and the held ones."""
        for t in list(out):
            if t.kind != "gear_upgrade":
                continue
            ups = [u for u in t.facts.get("upgrades") or () if u.get("item") != name]
            if ups:
                t.facts["upgrades"] = ups
            else:
                out.remove(t)
        self.pending_gear = [u for u in self.pending_gear if u.get("item") != name]

    def _pursuit_roll(self, char, held, flags):
        """pursuit_roll ROLL_WAIT_S after a boss of the plan died, when none of its items is theirs (one that is:
        pursuit_got says so). The run counts whether or not it is said (muted gear talk: counted, untold): the
        planner's count, or its count when first followed plus this session's kills when that is more (a kill its
        own poll missed)."""
        now = time.time()
        due = next((k for k in self.p_rolls if now - k["t"] >= ROLL_WAIT_S), None)
        if due is None:
            return None
        self.p_rolls.remove(due)
        if any(i in held or i in self.p_landed for i in due["items"]):
            return None
        for i in due["items"]:
            self.p_runs[i] = self.p_runs.get(i, 0) + 1
        iid = due["items"][0]
        name = self.p_missing.get(iid)
        if not name or not allowed("pursuit_roll", flags):
            return None
        mine = self.p_base.get(iid, 0) + self.p_runs[iid]
        n = max(pursuit_runs(self.pursuit, char, iid, None) or 0, mine, 1)
        move = next((m for m in self.p_moves if iid in offline_coach.move_items(m)), None)
        rng = pursuit_range(self.pursuit, char, iid, move)
        facts = {"item": name, "tries": n, "range": "%d to %d" % rng if rng else None}
        src = pursuit_src(self.pursuit, char, iid, move) if rng else None
        if src:
            facts["src"] = src                         # (where the range comes from, below 0.9 sure)
        unit = TRY_UNITS.get(offline_coach.move_act(move)[0], "runs")
        if unit != "runs":
            facts["unit"] = unit
        if unlucky(n, move, rng):
            facts["unlucky"] = True
        return Trigger("pursuit_roll", facts, 3)

    def _pursuit_here(self, flags):
        """next_move {text, here: true} a moment after zoning into a followed move's dungeon or raid: what drops there,
        once per place in HERE_GAP_S (muted gear talk: not said)."""
        if self.p_here is None:
            return None
        place, text = self.p_here
        self.p_here = None
        if not allowed("next_move", flags) or not self._ready("pursuit_here|%s" % place, HERE_GAP_S):
            return None
        return Trigger("next_move", {"text": text, "odds": None, "why": None, "here": True}, 2)

    def _next_move(self, planner, char, state, flags):
        """next_move for the plan's first move, once per move (a web source it names is kept as told by the research,
        so the game can correct it)."""
        m = self.p_move
        if m is None:
            return None
        key = self._move_key(char, m)
        if self._p_told(key, planner) or not allowed("next_move", flags):
            return None
        text, also = self._move_parts(m, state)
        if not text:
            return None                                # (nothing to call it yet: never an id)
        self.p_said.add(key)
        planner_told(planner, key, char, mark=True)
        for iid in offline_coach.move_items(m):
            self._note_told(iid)
        facts = {"text": text, "odds": m.get("odds"), "why": m.get("why")}
        if also:
            facts["also"] = also                       # (the run's other items: the odds are its lead item's)
        return Trigger("next_move", facts, 2)

    def _pursuit_reminders(self, m, char, state, s):
        """The first move's vendor and auction house steps as reminders (source "pursuit", for this character alone:
        Reminders.check says them to no other), in place of the ones placed for the move before, when it changed:
        near the vendor and in its town; in an auction house city."""
        rem = self.reminders
        key = self._move_key(char, m) if m is not None else None
        if rem is None or key == self.p_reminded:
            return
        self.p_reminded = key
        rem.replace("pursuit", step_reminders(m, self._names(m, state), self._srcs, s.get("faction"))
                    if m is not None else [], char=char)

    def _bosses_of(self, m, mk, where, iid):
        """The creatures whose kill is a run at a move's item: a dungeon or raid boss that drops it (the planner's
        "drops", else the research's sources: there), the creature of a "mob" move (mk: the move's kind)."""
        if mk == "mob":
            return [where] if isinstance(where, int) else []
        if mk not in ("inst", "raid"):
            return []
        out = [as_int(d.get("npc")) for d in m.get("drops") or () if isinstance(d, dict) and d.get("item") == iid]
        for src in self._srcs(iid):
            here = not isinstance(where, int) or src.get("zone") in (None, where)
            if src.get("kind") == "drop" and here:
                out.append(as_int(src.get("npc")))
        return [n for n in dict.fromkeys(out) if n]

    @staticmethod
    def _worn_in(worn, slot):
        """The one worn item in a slot group (by the slot it is worn in), or None (none, or two: rings, trinkets)."""
        group = gear_mod.slot_group(slot)
        hits = [it for it in worn
                if group and gear_mod.slot_group(gear_mod.SLOT_NAMES.get(as_int(it.get("key"), 0) or 0, "")) == group]
        return hits[0] if len(hits) == 1 else None

    def _corrected(self, s, out):
        """fact_corrected: a web fact the coach said about where an item comes from turned out wrong (the creature it
        named kept dying without it, and a new lookup put it elsewhere: research.Research.corrections, each handed
        out once), said once, out of combat and alive."""
        research = self.research
        if research is None or s.get("combat") or s.get("dead") or not self._ready("corrected", LIVE_POLL_S):
            return
        try:
            fixes = research.corrections()
        except Exception as e:  # noqa: BLE001 - an extra; never break the rules loop
            log_once("rules.corrected", e)
            return
        for f in fixes or ():
            out.append(Trigger("fact_corrected", {"item": f.get("item"), "old": f.get("old"), "new": f.get("new")}, 3))

    def _live(self, s, out):
        """facts_ready: the engine wrote new item facts into the addon's Data/Live.lua since the coach last said so
        (live_data.LiveData.news: handed out once, so asked only when it can be said), said at a quiet moment: out of
        combat, alive, and resting or on a flight. restart: the game only sees them after a WoW restart."""
        live = self.live
        quiet = not s.get("combat") and not s.get("dead") and (s.get("resting") or s.get("on_taxi"))
        if live is None or not quiet or not self._ready("live", LIVE_POLL_S):
            return
        try:
            news = live.news()
        except Exception as e:  # noqa: BLE001 - an extra; never break the rules loop
            log_once("rules.live", e)
            return
        if news:
            out.append(Trigger("facts_ready", {"n": news.get("n") or 0, "restart": bool(news.get("restart"))}, 3))

    def _away(self, state, s, busy, out):
        """Moving away from the waypoint, judged from the recent track (state.pos_hist), not two samples: the
        distance grew over at least the last AWAY_WINDOW_S, more than AWAY_YD in all, and is still growing over the
        last AWAY_RECENT_S. Only the track since the waypoint last changed counts, and none of the time in combat,
        on a flight or dead, so a detour you've turned out of or a waypoint that just moved on doesn't count."""
        g = state.goto_target()
        now = time.time()
        key = g and (g.get("map"), g["x"], g["y"])
        mp = s.get("map")
        if busy or not g or key != self.away_key or (g.get("map") and g["map"] != mp):
            self.away_key, self.away_since = key, now
            return
        window = float(self.cfg.get("away_window_s", AWAY_WINDOW_S))
        recent = float(self.cfg.get("away_recent_s", AWAY_RECENT_S))
        size = state.map_size(mp)
        kx, ky = (size[0] / 100.0, size[1] / 100.0) if size else (1.5, 1.0)   # yards per map %, else map % (~3:2)
        far = float(self.cfg.get("away_yd", AWAY_YD)) / (1.0 if size else 25.0)   # ~25 yd per map % without a size
        start = max(self.away_since, now - 2 * window)
        pts = [(t, x, y) for t, m, x, y in state.pos_hist if t >= start and m == mp]
        if len(pts) < 3 or now - pts[0][0] < window:
            return

        def dist(p):
            return math.hypot((g["x"] - p[1]) * kx, (g["y"] - p[2]) * ky)
        mid = min(pts, key=lambda p: abs(now - recent - p[0]))
        d0, dm, d1 = dist(pts[0]), dist(mid), dist(pts[-1])
        # a waypoint you're nearly at is RestedXP's to judge; growth at both ends means you're still going away
        if d1 < 2 * far or d1 - d0 < far or d1 - dm < far / 4 or dm < d0:
            return
        if not self._ready("offroute", float(self.cfg.get("away_cooldown_s", 120))):
            return
        ex, ny = (g["x"] - pts[-1][1]) * kx, -(g["y"] - pts[-1][2]) * ky
        bearing = math.degrees(math.atan2(ex, ny))
        facts = {"direction": compass(bearing), "seconds_moving_away": int(now - pts[0][0])}
        if size:
            facts["yards"] = int(round(d1 / 10.0) * 10)
            facts["yards_further_than_before"] = int(round((d1 - d0) / 10.0) * 10)
        else:
            facts["distance_map_pct"] = round(d1, 1)
        f = (state.player_geo() or {}).get("facing")
        if f is not None:
            facts["relative"] = relative_label((bearing - (360 - math.degrees(f)) % 360 + 180) % 360 - 180)
        out.append(Trigger("moving_away_from_waypoint", facts, 2))

    @staticmethod
    def consumable_counts(items, level=None):
        """{"food", "drink", "ammo", "bandage", ...: usable count} from the addon's consumables list."""
        out = {}
        names = {"f": ("food",), "d": ("drink",), "b": ("food", "drink"), "a": ("ammo",), "h": ("bandage",),
                 "p": ("healing_potion",), "m": ("mana_potion",)}
        for it in items or ():
            if level and it.get("req") and it["req"] > level:
                continue
            for k in names.get(it.get("kind"), ()):
                out[k] = out.get(k, 0) + (it.get("count") or 0)
        return out

    def _vendor(self, state, s, busy, out):
        """A vendor treasure nearby (addon 0.13 list D; companion/vendor_treasures.py): its gear that is an upgrade now
        or within 3 levels, and its potions when the player has few. Said once per vendor per session."""
        vt = s.get("vendor_treasure")
        lvl = s.get("level")
        if not vt or busy or not self.gear or not s.get("class") or not lvl or vt["vendor"] in self.vendor_told:
            return
        import vendor_treasures
        info = vendor_treasures.vendor(vt["vendor"])
        if info is None or not vt["items"]:
            return
        self.vendor_told.add(vt["vendor"])
        gear = [i for i in vt["items"] if i["loc"] != "POTION"]
        ups = self.gear.evaluate(state.gear["W"], gear, s["class"], lvl, s.get("skills") or {}) if gear else []
        ups = [u for u in ups if u["wait_level"] is None or u["wait_level"] <= lvl + 3]
        have = self.consumable_counts(s.get("consumables"), lvl).get("healing_potion", 0)
        potions = [i["name"] for i in vt["items"] if i["loc"] == "POTION" and (i["req"] or 0) <= lvl] if have < 5 else []
        if not ups and not potions:
            return
        facts = {"vendor": info["name"], "upgrades": [{"item": u["item"], "slot": u["slot_name"], "gain": u["gain"],
                                                       "equip_at_level": u["wait_level"]} for u in ups[:3]],
                 "potions": potions[:3]}
        size = state.map_size(info["map"]) if s.get("map") == info["map"] else None
        if size and s.get("x") is not None:
            import math
            from geo import compass
            ex, ny = (info["x"] - s["x"]) / 100.0 * size[0], -(info["y"] - s["y"]) / 100.0 * size[1]
            facts["yards"] = int(round(math.hypot(ex, ny) / 10.0) * 10)
            facts["direction"] = compass(math.degrees(math.atan2(ex, ny)))
        out.append(Trigger("vendor_treasure", facts, 2))

    def _extras(self, state, s, busy, out):
        """Addon 0.10 extras: talent points, training, consumables running low, an unknown flight master nearby,
        a target someone else tapped. Each is said once (per level / per crossing / per node)."""
        lvl = s.get("level")
        now = time.time()
        if not lvl or busy:
            return
        tp = s.get("talent_points") or 0
        if tp > 0:
            first = self.tp_since.setdefault(lvl, now)
            if now - first >= TALENT_GRACE_S and ("talent", lvl) not in self.told_levels:
                self.told_levels.add(("talent", lvl))
                out.append(Trigger("talent_points_unspent", {"points": tp}, 3))
        cons = s.get("consumables")
        if cons is not None:
            counts = self.consumable_counts(cons, lvl)
            want = {"food": True, "drink": s.get("power_type") == "mana", "ammo": s.get("class") == "HUNTER"}
            for k, limit in CONSUMABLE_LOW.items():
                c, prev = counts.get(k, 0), self.cons_prev.get(k)
                self.cons_prev[k] = c
                if want.get(k) and prev is not None and prev > limit >= c and self._ready("cons:" + k, 600):
                    out.append(Trigger("consumables_low", {"kind": k, "left": c}, 3))
        t = s.get("target") or {}
        if t.get("tapped_by_others") and not t.get("dead") and (t.get("reaction") or 9) <= 4:
            npc = t.get("npc")
            if now - self.tapped_told.get(npc, 0) > 60:
                self.tapped_told[npc] = now
                out.append(Trigger("target_tapped", {"target": t.get("name")}, 2))
        pack = self.kb_fn() if self.kb_fn else None
        if pack is None or not getattr(pack, "ok", False):
            return
        try:
            ex = pack.player_extras(s, state.map_size(s["map"]) if s.get("map") else None) or {}
        except Exception as e:  # noqa: BLE001
            log_once("rules.extras", e)
            return
        tr = ex.get("training") or {}
        if tr.get("missing_now") and ("train", lvl) not in self.told_levels:
            self.told_levels.add(("train", lvl))
            out.append(Trigger("training_available", {"spells": [m["spell"] for m in tr["missing_now"][:6]],
                                                      "count": len(tr["missing_now"])}, 3))
        for n in (ex.get("flight_paths") or {}).get("unknown_nearby") or ():
            if n["id"] not in self.fp_told:
                self.fp_told.add(n["id"])
                out.append(Trigger("flight_path_nearby", {"flight_path": n["name"], "yards": n["yards"]}, 2))

    def _kb_triggers(self, s, out):
        """turnin_decay_soon (needs the knowledge base; silent without it): a completed quest in the log whose
        decay_at_level (first level with reduced XP: the KB brief's value, else quest level + 6) is the player's
        next level, while the player is within 15% of the level's XP from levelling. Priority 2, at most once per
        quest every 10 minutes. Facts from knowledge.mechanics.xp.quest_xp: XP now and after the level-up.
        Cheap on the capture thread: pack.decay_info caches one row per (quest, level)."""
        pack = self.kb_fn() if self.kb_fn else None
        if pack is None or not getattr(pack, "ok", False):
            return
        try:
            lvl, xp, xmax = s.get("level"), s.get("xp"), s.get("xp_max")
            if not lvl or xp is None or not xmax or xmax - xp > DECAY_XP_WINDOW * xmax:
                return
            now = time.time()
            for qid, v in (s.get("quests") or {}).items():
                if not (isinstance(v, dict) and v.get("done")):
                    continue
                if now - self.decay_told.get(qid, 0) < DECAY_COOLDOWN_S:
                    continue
                d = pack.decay_info(qid, lvl)
                if not d or d.get("decay_at_level") != lvl + 1 or d.get("xp_now") is None \
                        or d.get("xp_after_level_up") is None or d["xp_after_level_up"] >= d["xp_now"]:
                    continue
                self.decay_told[qid] = now
                out.append(Trigger("turnin_decay_soon", dict(d, xp_to_level=xmax - xp, level=lvl), 2))
        except Exception as e:  # noqa: BLE001 - a knowledge trigger is an extra; never break the rules loop
            log_once("rules.decay", e)
