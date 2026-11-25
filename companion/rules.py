"""Rules engine: turns state changes into coaching triggers (no AI here)."""
import math
import re
import time
from typing import Any

from geo import compass, relative_label
import gear as gear_mod
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
    # Glide's own best-in-slot ranks at 60 (see Rules._bis)
    "next_upgrade": "gear", "enchant_missing": "gear",
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
}
CONSUMABLE_LOW = {"food": 4, "drink": 4, "ammo": 200}   # warn when a count drops to this or below
TALENT_GRACE_S = 30               # unspent talent points: say so once per level, after this long
DECAY_XP_WINDOW = 0.15            # turnin_decay_soon: within 15% of the level's XP from levelling
DECAY_COOLDOWN_S = 600            # ... at most once per quest every 10 minutes
LIVE_POLL_S = 5                   # facts_ready: how often Data/Live.lua's writer is asked for news
BIS_POLL_S = 5                    # next_upgrade / enchant_missing: how often the best-in-slot ranks are looked at
BIS_ADDON = (0, 22, 0)            # the oldest addon that sends worn enchants: from it on, no EN code means no enchant
ENCHANT_GAP_S = 600               # enchant_missing: one slot at a time, at most once in this long


def version_tuple(v):
    """An addon version as a tuple ("0.22.0" -> (0, 22, 0)); () for none."""
    m = re.match(r"\s*(\d+(?:\.\d+)*)", str(v or ""))
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


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
        for u in self.upgrades:
            tag = "%s|%s" % (u["key"], u["item"])
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
        return out

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
            t = self._next_upgrade(cb, state, flags, ranker, sends)
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
