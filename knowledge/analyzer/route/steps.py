"""The steps of one guide a player gets (Route: class, race, faction and RestedXP's runtime options decide), with
RestedXP's step numbers, and the guides that follow it."""
import re

from knowledge import rxp_parser as RP
from knowledge.analyzer.kbdata import KBData, get_data
from knowledge.analyzer.model import wait_seconds
from knowledge.vocab import glide_race

DEFAULT_RACE = {
    "Alliance": {"WARRIOR": "Human", "PALADIN": "Human", "HUNTER": "Dwarf", "ROGUE": "Human", "PRIEST": "Human",
                 "MAGE": "Human", "WARLOCK": "Human", "DRUID": "NightElf", "SHAMAN": "Dwarf"},
    "Horde": {"WARRIOR": "Orc", "HUNTER": "Orc", "ROGUE": "Undead", "PRIEST": "Undead", "MAGE": "Undead",
              "WARLOCK": "Undead", "DRUID": "Tauren", "SHAMAN": "Orc", "PALADIN": "Tauren"},
}
OBJ_VERBS = ("complete", "collect")
ALT_KINDS = ("turnin", "accept", "grind")          # decision points that get alternatives
GOTO_VERBS = ("goto", "groundgoto", "flygoto")
OTHER_WORK = ("train", "trainer", "vendor", "buy", "buyAll", "buyUntilBroke", "hs", "fly", "fp", "deathskip",
              "home", "cast")
_COND_CACHE = {}


def applies(cond, cls, race, faction):
    """Does the RestedXP `<<` condition hold for this class, race and faction? (cached)"""
    if cond is None:
        return True
    raw = cond.get("raw") if isinstance(cond, dict) else cond
    if raw is None:
        return True
    k = (raw, cls, race, faction)
    v = _COND_CACHE.get(k)
    if v is None:
        if len(_COND_CACHE) > 200000:
            _COND_CACHE.clear()
        v = _COND_CACHE[k] = bool(RP.cond_applies(cond, cls=cls, race=race, faction=faction))
    return v


def norm_player(player, guide=None):
    """A player dict (class / race / faction + RestedXP options in Glide's names, rxp_parser.RxpSettings) with
    every option filled in. An option left unset is RestedXP's own default (SSF, group quests, dungeons off) in
    every guide group: RestedXP has no per-group defaults; a Dungeon guide turns its dungeons on itself
    (`.enabledungeon`, applied by the step rule). The guide only supplies a missing faction."""
    p = dict(player or {})
    cls = (p.get("class") or p.get("cls") or "WARRIOR").upper()
    fac = p.get("faction") or (guide or {}).get("faction") or "Alliance"
    fac = fac.capitalize()
    race = glide_race(p.get("race") or DEFAULT_RACE.get(fac, DEFAULT_RACE["Alliance"]).get(cls, "Human"))
    rs = RP.RxpSettings.from_options(p)
    out = {"class": cls, "race": race, "faction": fac, "hardcore": rs.hardcore, "season": int(rs.season),
           "phase": int(rs.phase), "xprate": rs.xprate, "ssf": rs.ssf, "group_quests": rs.group_quests,
           "dungeons": "all" if rs.dungeons == "all" else set(rs.dungeons)}
    return out


def settings_key(player, guide=None):
    """The player's RestedXP runtime options, normalised as norm_player does, as a hashable cache key. They decide
    which steps RestedXP shows and so its step numbers: everything cached per player must key on them too.
    Without the guide, group quests and dungeons left unset stay None (kept apart from an explicit off, as
    before R18 when their default differed between guide groups)."""
    raw = player or {}
    p = norm_player(raw, guide)
    gq = p["group_quests"] if guide is not None or raw.get("group_quests") is not None else None
    dn = p["dungeons"] if guide is not None or raw.get("dungeons") is not None else None
    if dn is not None and not isinstance(dn, str):
        dn = tuple(sorted(dn))
    return (p["hardcore"], p["season"], p["phase"], p["xprate"], p["ssf"], gq, dn)


def step_shown(flags, p, dungeons=None):
    """RestedXP's runtime step logic (rxp_parser.step_shown, the one rule the build's step timing uses too) for a
    norm_player dict; dungeons: the guide's own dungeon rules (rxp_parser.guide_dungeons). Steps it hides take
    no RXP step number."""
    return RP.step_shown(flags, RP.RxpSettings.from_options(p), dungeons)


def _pos_of_goto(data, a):
    g = a["args"]
    um = g.get("ui_map")
    if um is None or "error" in g:
        return None
    x, y = g.get("x"), g.get("y")
    if x is None and g.get("wx") is not None and g.get("wy") is not None:
        x, y = data.to_map(um, g["wx"], g["wy"])
        if x is None or not (-1 <= x <= 101 and -1 <= y <= 101):
            x, y = data.to_map(um, g["wy"], g["wx"])
            if x is None or not (-1 <= x <= 101 and -1 <= y <= 101):
                return None
    if x is None or y is None:
        return None
    return (um, round(float(x), 2), round(float(y), 2))     # 2 decimals, as build_kb's finalize stores them


class Route:
    """The applicable steps of one guide for one player (class/race/faction + runtime options)."""

    def __init__(self, kb, guide_key, player, data=None):
        self.data = data if isinstance(data, KBData) else get_data(kb)
        self.kb = self.data.kb
        g = self.data.guide(guide_key)
        if g is None:
            raise KeyError("unknown guide %r" % guide_key)
        self.guide = g
        self.guide_key = guide_key
        self.player = norm_player(player, g)
        p = self.player
        cls, race, fac = p["class"], p["race"], p["faction"]
        self.steps, self.by_idx, self.rxp_to_idx, self.idx_to_rxp = [], {}, {}, {}
        self.labels = {}
        self.excluded = []                  # (idx, reason) for numbered steps hidden at runtime
        self.rxp = RP.RxpSettings.from_options(p)
        n = 0
        guide = {"text": self.data.guide_header(guide_key)["text"], "steps": g["steps"]}     # header: #disabledungeons
        for s, flags, shown in RP.visible_steps(guide, self.rxp, applies=lambda c: applies(c, cls, race, fac),
                                                cls=cls, race=race, faction=fac):
            if not shown:
                # RestedXP Forever leaves steps its stepLogic hides out of the guide: no step number
                # (verified against the addon's pages by the store lane, sources/live_logs.py)
                self.excluded.append((s["idx"], "runtime_flags"))
                continue
            n += 1
            self.rxp_to_idx[n] = s["idx"]
            self.idx_to_rxp[s["idx"]] = n
            st = self._build_step(s, flags, n)
            self.steps.append(st)
            self.by_idx[st["idx"]] = st
            if st["label"]:
                self.labels.setdefault(st["label"], []).append(st["idx"])
        self._prepass()

    # ------------------------------------------------------------------------------------------
    def _build_step(self, s, flags, n):
        p = self.player
        cls, race, fac = p["class"], p["race"], p["faction"]
        acts = [a for a in s["actions"] if a["verb"] and applies(a["cond"], cls, race, fac)]
        d = self.data
        gotos = [(a, _pos_of_goto(d, a)) for a in acts if a["verb"] in GOTO_VERBS]
        gotos = [(a, pp) for a, pp in gotos if pp is not None]
        pos = gotos[0][1] if gotos else None
        if pos is None and not any(a["verb"] in GOTO_VERBS for a in s["actions"]) and s["x"] is not None \
                and s["ui_map"] is not None:
            pos = (s["ui_map"], s["x"], s["y"])
        verbs = {a["verb"] for a in acts}
        mobs, mob_names = [], []
        for a in acts:
            if a["verb"] in ("mob", "unitscan"):
                for u in a["args"].get("units") or []:
                    cid = u.get("id") or d.creature_by_name_on(u.get("name"), pos[0] if pos else None)
                    if cid is None:
                        if u.get("name"):
                            d.count("mob_name_unresolved", u.get("name"))
                        continue
                    if cid not in mobs:
                        mobs.append(cid)
                        mob_names.append(u.get("name") or (d.creature(cid) or {}).get("name"))
        loop = bool(flags.get("loop"))
        has_obj = any(a["verb"] in OBJ_VERBS and a.get("quest_id") and not a["args"].get("profession") for a in acts)
        xp_target = any(a["verb"] == "xp" and a["args"].get("skipstep") is None and not a["args"].get("reverse")
                        for a in acts)
        walk = [pp for a, pp in gotos if (a["args"].get("mode") or "wp") in ("wp", "arrive", "path")]
        if not walk and pos is not None:
            walk = [pos]
        if loop or ((has_obj or xp_target) and len(walk) > 3):
            walk = walk[:1]
        quests = {"accept": [], "turnin": [], "objective": [], "gate": []}
        for a in acts:
            q = a.get("quest_id")
            if q is None:
                continue
            v = a["verb"]
            if v == "accept":
                quests["accept"].append(q)
            elif v == "turnin":
                quests["turnin"].append(q)
            elif v in OBJ_VERBS and not a["args"].get("profession"):
                quests["objective"].append(q)
            elif v.startswith("is") or v in ("questcount", "skipOnQuest"):
                quests["gate"].append(q)
        text = s["text"] or ""
        step = {"idx": s["idx"], "rxp_step_no": n, "text": s["text"], "comments": s["comments"], "flags": flags,
                "optional": bool(flags.get("optional")), "sticky": bool(flags.get("sticky")), "loop": loop,
                "label": flags.get("label") if isinstance(flags.get("label"), str) else None,
                "completewith": flags.get("completewith") if isinstance(flags.get("completewith"), str) else None,
                "pos": pos, "path": walk, "zone": s["zone"], "actions": acts, "verbs": verbs, "mobs": mobs,
                "mob_names": mob_names, "quests": quests, "wait_s": wait_seconds(text),
                "buy_text": "buy" in text.lower(), "raw": s,
                "quest_work": bool(quests["accept"] or quests["turnin"] or has_obj or xp_target),
                "other_work": bool(verbs & set(OTHER_WORK)), "xp_target": xp_target, "has_obj": has_obj,
                "dungeon": flags.get("dungeon") if isinstance(flags.get("dungeon"), str) else None}
        step["kind"] = self._kind(step)
        return step

    def _kind(self, st):
        v = st["verbs"]
        q = st["quests"]
        if st["dungeon"] and st["has_obj"] and not q["turnin"] and not q["accept"]:
            return "dungeon"
        if "deathskip" in v:
            return "deathskip"
        if "hs" in v:
            return "hs"
        if "fly" in v:
            return "fly"
        if q["turnin"]:
            return "turnin"
        if st["has_obj"]:
            types = set()
            for a in st["actions"]:
                if a["verb"] in OBJ_VERBS and a.get("quest_id") and not a["args"].get("profession"):
                    types.add(self._obj_type(a))
            return "collect" if types == {"collect"} else "complete"
        if st["xp_target"]:
            return "grind"
        if q["accept"]:
            return "accept"
        if v & {"train", "trainer"}:
            return "train"
        if v & {"vendor", "buy", "buyAll", "buyUntilBroke"} or ("collect" in v and st["buy_text"]):
            return "vendor"
        if st["path"]:
            return "travel"
        return "other"

    def _obj_type(self, a):
        if a["verb"] == "collect":
            return "collect"
        q, obj = a.get("quest_id"), a["args"].get("obj")
        for o in self.data.objectives.get(q) or ():
            if o["idx"] == obj:
                return "collect" if o["type"] == "collect" else "kill" if o["type"] == "kill" else o["type"]
        txt = (a["args"].get("comment") or "").lower()
        return "collect" if "collect" in txt else "kill"

    def _obj_key(self, a):
        q = a.get("quest_id")
        args = a["args"]
        if a["verb"] == "complete":
            obj = args.get("obj")
            return (q, obj) if isinstance(obj, int) else (q, "a%d" % a["line"])
        objs = args.get("objectives") or []
        if objs:
            return (q, objs[0])
        item = args.get("item_id")
        for o in self.data.objectives.get(q) or ():
            if o["target_kind"] == "item" and o["target_id"] == item:
                return (q, o["idx"])
        return (q, "i%s" % item)

    def _prepass(self):
        self.accept_idx, self.turnin_idx, self.action_key = {}, {}, {}
        self.obj_keys, listings = {}, {}
        for st in self.steps:
            for a in st["actions"]:
                q, v = a.get("quest_id"), a["verb"]
                if q is None:
                    continue
                if v == "accept":
                    self.accept_idx.setdefault(q, st["idx"])
                elif v == "turnin":
                    self.turnin_idx.setdefault(q, st["idx"])
                elif v in OBJ_VERBS and not a["args"].get("profession"):
                    k = self._obj_key(a)
                    self.action_key[(st["idx"], a["line"])] = k
                    listings.setdefault(k, []).append(st["idx"])
        for k, idxs in listings.items():
            q = k[0]
            ti = self.turnin_idx.get(q)
            if ti is None or any(i < ti for i in idxs):
                self.obj_keys.setdefault(q, set()).add(k)
        self.listings = listings
        self._pos_list = [(st["idx"], st["pos"]) for st in self.steps if st["pos"] is not None]
        self._pos_idx = [i for i, _ in self._pos_list]

    def attribution(self, excluded=()):
        """{objective key: step idx where its work is paid}: the last listing before the quest's turn-in
        (RXP's loop/farm step), skipping steps known not to apply (`excluded`)."""
        out = {}
        for k, idxs in self.listings.items():
            ti = self.turnin_idx.get(k[0])
            cand = [i for i in idxs if i not in excluded and (ti is None or i < ti)]
            if not cand:
                cand = [i for i in idxs if i not in excluded]
            if cand:
                out[k] = max(cand)
        return out

    # ------------------------------------------------------------------------------------------
    def index_of(self, idx):
        """List position of step idx (or of the first route step at/after idx)."""
        for i, st in enumerate(self.steps):
            if st["idx"] >= idx:
                return i
        return len(self.steps)

    def next_pos(self, idx):
        import bisect
        j = bisect.bisect_right(self._pos_idx, idx)
        return self._pos_list[j][1] if j < len(self._pos_list) else None

    def prev_pos(self, idx):
        import bisect
        j = bisect.bisect_left(self._pos_idx, idx) - 1
        return self._pos_list[j][1] if j >= 0 else None

    def summary(self):
        return {"guide_key": self.guide_key, "steps": len(self.steps), "numbered": len(self.rxp_to_idx),
                "excluded": len(self.excluded), "player": {k: v for k, v in self.player.items() if k != "dungeons"}}


# ----------------------------------------------------------------------------------------------
# next guides
# ----------------------------------------------------------------------------------------------
def _norm_group(g):
    g = re.sub(r"\((A|H)\)", "", g or "")
    g = re.sub(r"\b(Alliance|Horde|Forever|Dungeon|Guide|RestedXP)\b", "", g)
    return re.sub(r"[\s\d-]+", " ", g).strip().lower()


def next_guides(kb, guide_key, player, data=None):
    """Guide keys that follow guide_key for this player (#next entries whose << cond applies), resolved by
    name within the guide's group first, then any group of the same faction family."""
    data = data or get_data(kb)
    g = data.guide_rows.get(guide_key)
    if not g or not g.get("next"):
        return []
    p = norm_player(player, g)
    hdr = data.guide_header(guide_key)
    own = [(grp, c) for grp, c in hdr["groups"]] if len(hdr["groups"]) > 1 else g["grp"]
    out = []
    for e in RP.parse_next(g["next"], own):
        if not applies(e.get("cond"), p["class"], p["race"], p["faction"]):
            continue
        name = (e.get("name") or "").strip().strip('"')
        grp = e.get("group") or g["grp"]
        key = "%s||%s" % (grp, name)
        if key not in data.guide_rows:
            cands = data.guide_by_name.get(name) or []
            same = [k for k in cands if data.guide_rows[k]["grp"] == g["grp"]]
            fam = [k for k in cands if (data.guide_rows[k]["faction"] in (None, p["faction"]))
                   and ("Dungeon" in data.guide_rows[k]["grp"]) == ("Dungeon" in g["grp"])]
            anyf = [k for k in cands if data.guide_rows[k]["faction"] in (None, p["faction"])]
            pick = (same or fam or anyf or [None])[0]
            if pick is None:
                continue
            key = pick
        if key not in out and key != guide_key:
            out.append(key)
    return out
