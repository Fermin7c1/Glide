"""Rules engine: turns state changes into coaching triggers (no AI here)."""
import math
import time

from geo import compass, relative_label
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
    "gear_upgrade": "gear", "reward_choice": "gear",
    "pace_review": "pace", "level_up": "pace", "spell_learned": "pace",
    "bags_nearly_full": "upkeep", "low_durability": "upkeep",
    "addon_outdated": None,                      # always reported
    # knowledge-layer trigger (needs data/kb.db; see Rules._kb_triggers)
    "turnin_decay_soon": "route",
    # addon 0.10 extras (see Rules._extras)
    "training_available": "upkeep", "talent_points_unspent": "upkeep", "consumables_low": "upkeep",
    "flight_path_nearby": "route", "target_tapped": "combat",
}
CONSUMABLE_LOW = {"food": 4, "drink": 4, "ammo": 200}   # warn when a count drops to this or below
TALENT_GRACE_S = 30               # unspent talent points: say so once per level, after this long
DECAY_XP_WINDOW = 0.15            # turnin_decay_soon: within 15% of the level's XP from levelling
DECAY_COOLDOWN_S = 600            # ... at most once per quest every 10 minutes


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
               tuple(sorted(sk.get("weapons") or ())), tuple(sorted(sk.get("armor") or ())))
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
        return out

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
