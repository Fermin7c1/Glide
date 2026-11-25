"""Gear evaluation: score items with RestedXP's Forever stat weights for your class/level and find
upgrades among bag items and quest reward choices. At 60 also Glide's own best-in-slot ranks (bis.py's BisRanker,
handed over with Gear.set_bis) and how the coach speaks of them: the slot words, the next upgrades for the prompt
(next_upgrades) and a slot's ranks for the coach's bis_slot tool (bis_rows)."""
import functools
import json
import os
import re
import sys

from log import log_once
import settings

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.append(ROOT)       # the knowledge package: its values module (stdlib only) reads the ranker's numbers
from knowledge.values import as_float  # noqa: E402

SLOT_NAMES = {1: "head", 2: "neck", 3: "shoulders", 5: "chest", 6: "waist", 7: "legs", 8: "feet", 9: "wrists",
              10: "hands", 11: "ring 1", 12: "ring 2", 13: "trinket 1", 14: "trinket 2", 15: "back",
              16: "main hand", 17: "off hand", 18: "ranged"}
LOC_SLOTS = {
    "INVTYPE_HEAD": [1], "INVTYPE_NECK": [2], "INVTYPE_SHOULDER": [3], "INVTYPE_CHEST": [5], "INVTYPE_ROBE": [5],
    "INVTYPE_WAIST": [6], "INVTYPE_LEGS": [7], "INVTYPE_FEET": [8], "INVTYPE_WRIST": [9], "INVTYPE_HAND": [10],
    "INVTYPE_FINGER": [11, 12], "INVTYPE_TRINKET": [13, 14], "INVTYPE_CLOAK": [15],
    "INVTYPE_WEAPON": [16, 17], "INVTYPE_WEAPONMAINHAND": [16], "INVTYPE_2HWEAPON": [16],
    "INVTYPE_WEAPONOFFHAND": [17], "INVTYPE_SHIELD": [17], "INVTYPE_HOLDABLE": [17],
    "INVTYPE_RANGED": [18], "INVTYPE_RANGEDRIGHT": [18], "INVTYPE_THROWN": [18], "INVTYPE_RELIC": [18],
}
RANGED = {"INVTYPE_RANGED", "INVTYPE_RANGEDRIGHT", "INVTYPE_THROWN"}

# proficiency (from RestedXP's Forever item rules): armor subclass / weapon subclass -> min level
ARMOR = {"Cloth": 1, "Leather": 2, "Mail": 3, "Plate": 4, "Shield": 6}
WEAPON = {"Axe1H": 0, "Axe2H": 1, "Bows": 2, "Guns": 3, "Mace1H": 4, "Mace2H": 5, "Polearm": 6, "Sword1H": 7,
          "Sword2H": 8, "Staff": 10, "Unarmed": 13, "Generic": 14, "Dagger": 15, "Thrown": 16, "Crossbow": 18,
          "Wand": 19}
PROF = {
    "WARRIOR": ({"Leather": 1, "Mail": 1, "Plate": 35, "Shield": 1},
                {w: 1 for w in WEAPON if w != "Wand"} | {"Polearm": 18}, 18),
    "PALADIN": ({"Leather": 1, "Mail": 1, "Plate": 35, "Shield": 1},
                {"Axe1H": 1, "Axe2H": 1, "Mace1H": 1, "Mace2H": 1, "Polearm": 18, "Sword1H": 1, "Sword2H": 1}, None),
    "HUNTER": ({"Leather": 1, "Mail": 35},
               {"Axe1H": 1, "Axe2H": 1, "Bows": 1, "Guns": 1, "Polearm": 18, "Sword1H": 1, "Sword2H": 1, "Staff": 1,
                "Unarmed": 1, "Dagger": 1, "Crossbow": 1}, 18),
    "ROGUE": ({"Leather": 1}, {"Bows": 1, "Guns": 1, "Mace1H": 1, "Sword1H": 1, "Unarmed": 1, "Dagger": 1,
                               "Thrown": 1, "Crossbow": 1}, 10),
    "SHAMAN": ({"Leather": 1, "Mail": 35, "Shield": 1},
               {"Axe1H": 1, "Axe2H": 1, "Mace1H": 1, "Mace2H": 1, "Staff": 1, "Unarmed": 1, "Dagger": 1}, None),
    "DRUID": ({"Leather": 1}, {"Mace1H": 1, "Mace2H": 1, "Staff": 1, "Unarmed": 1, "Dagger": 1}, None),
    "MAGE": ({}, {"Sword1H": 1, "Dagger": 1, "Staff": 1, "Wand": 1}, None),
    "PRIEST": ({}, {"Mace1H": 1, "Dagger": 1, "Staff": 1, "Wand": 1}, None),
    "WARLOCK": ({}, {"Sword1H": 1, "Dagger": 1, "Staff": 1, "Wand": 1}, None),
}
WEAPON_LABEL = {0: "One-Handed Axes", 1: "Two-Handed Axes", 2: "Bows", 3: "Guns", 4: "One-Handed Maces",
                5: "Two-Handed Maces", 6: "Polearms", 7: "One-Handed Swords", 8: "Two-Handed Swords", 10: "Staves",
                13: "Fist Weapons", 15: "Daggers", 16: "Thrown", 18: "Crossbows", 19: "Wands"}
ARMOR_LABEL = {2: "Leather", 3: "Mail", 4: "Plate", 6: "Shields"}
ARMOR_BY_ID = {v: k for k, v in ARMOR.items()}
WEAPON_BY_ID = {v: k for k, v in WEAPON.items()}

# Glide's best-in-slot ranks (bis.py) by slot group: the group, the word the coach says for it, and the words a
# player (or the ranker's slot label) may use for it
BIS_SLOTS = (
    ("head", "head", ("head", "helm", "helmet", "hat", "hood", "crown", "circlet", "cap", "cowl", "mask")),
    ("neck", "neck", ("neck", "necklace", "amulet", "pendant", "choker", "collar")),
    ("shoulders", "shoulders", ("shoulder", "shoulders", "mantle", "pauldrons", "spaulders", "amice", "epaulets",
                                "shoulderpads")),
    ("back", "cloak", ("back", "cloak", "cape", "drape")),
    ("chest", "chest", ("chest", "robe", "tunic", "vest", "breastplate", "chestpiece", "jerkin")),
    ("wrists", "bracers", ("wrist", "wrists", "bracer", "bracers", "bracelets", "bindings", "cuffs", "wristguards")),
    ("hands", "gloves", ("hand", "hands", "glove", "gloves", "gauntlets", "handwraps", "handguards", "mitts")),
    ("waist", "belt", ("waist", "belt", "sash", "cord", "girdle", "cinch")),
    ("legs", "legs", ("leg", "legs", "pants", "leggings", "trousers", "kilt", "legguards", "breeches")),
    ("feet", "boots", ("feet", "foot", "boot", "boots", "shoes", "sandals", "slippers", "treads")),
    ("ring", "ring", ("ring", "rings", "finger", "fingers", "band")),
    ("trinket", "trinket", ("trinket", "trinkets")),
    ("main hand", "main hand", ("main hand", "mainhand", "main", "weapon", "weapons", "two hand", "twohand", "2h",
                                "one hand", "onehand", "1h", "staff", "sword", "dagger", "mace")),
    ("off hand", "off hand", ("off hand", "offhand", "shield", "orb", "tome")),
    ("ranged", "ranged weapon", ("ranged", "wand", "bow", "gun", "crossbow", "thrown", "relic", "idol", "libram",
                                 "totem")),
)
SLOT_WORDS = {w: group for group, _, words in BIS_SLOTS for w in words}
SLOT_SPOKEN = {group: said for group, said, _ in BIS_SLOTS}
PLURAL_SLOTS = {"shoulders", "bracers", "gloves", "legs", "boots"}
_SLOT_FILLER = re.compile(r"\b(?:my|the|your|a|an|slot|slots|piece|pieces|item|items|gear|for|me|held|in)\b")


def slot_group(word):
    """The best-in-slot slot group ("head", "wrists", "ring", "main hand", ...: BIS_SLOTS) a word names: a player's
    ("my bracers", "neck piece", "rings") or a slot label ("Head", "Finger1", "MainHand", "ring 1"); None for none."""
    w = str(word or "").lower().replace("'s", " ").replace("-", " ").replace("_", " ")
    w = re.sub(r"([a-z])(\d)", r"\1 \2", w)
    words = _SLOT_FILLER.sub(" ", re.sub(r"[^a-z ]", " ", w)).split()
    if not words or len(words) > 3:
        return None
    if " ".join(words) in SLOT_WORDS:
        return SLOT_WORDS[" ".join(words)]
    for i in range(len(words) - 1, -1, -1):              # the last word that names one ("best neck"), pairs first
        if i and "%s %s" % (words[i - 1], words[i]) in SLOT_WORDS:
            return SLOT_WORDS["%s %s" % (words[i - 1], words[i])]
        if words[i] in SLOT_WORDS:
            return SLOT_WORDS[words[i]]
    return None


def slot_word(label):
    """What the coach calls a slot ("bracers" for Wrist, "ranged weapon"), from a slot label or a player's word."""
    group = slot_group(label)
    return SLOT_SPOKEN[group] if group else (str(label or "").strip().lower() or "gear")


def dps_text(d):
    """A DPS gain as the coach says it: a whole number from 2 up ("8"), one decimal below ("0.8")."""
    d = float(d or 0)
    if abs(d) >= 2:
        return "%d" % round(d)
    t = "%.1f" % d
    return t[:-2] if t.endswith(".0") else t


_num = functools.partial(as_float, finite=True, bool_ok=False)      # a number from the ranker's data, or None


def beyond_noise(d, s):
    """A gain the sims tell apart from nothing: above 0 and above 2 sigma (sigma unknown: above 0)."""
    d, s = _num(d), _num(s)
    return d is not None and d > 0 and (s is None or d > 2 * s)


def about_same(d, s):
    """Within the sims' noise: |gain| below 2 sigma ("about the same as yours")."""
    d, s = _num(d), _num(s)
    return d is not None and s is not None and abs(d) < 2 * s


def bis_parts(got):
    """What a best-in-slot getter hands over -> (the character's ranks, a bis.json "chars" entry, or None; the item
    ids worn). It hands the entry alone, or (entry, worn item ids) (Gear.bis_now)."""
    if isinstance(got, (tuple, list)) and len(got) == 2:
        cb, worn = got
    else:
        cb, worn = got, ()
    ids = set()
    for w in worn or ():
        try:
            ids.add(int(w))
        except (TypeError, ValueError):
            pass
    return (cb if isinstance(cb, dict) else None), ids


def _brief(brief_fn, iid):
    """{"name", "comes_from"} of an item from brief_fn (the research's brief), or {}."""
    if brief_fn is None:
        return {}
    try:
        return brief_fn(iid) or {}
    except Exception as e:  # noqa: BLE001 - an item without a name or source
        log_once("gear.bis_brief", e)
        return {}


def _named(e, b):
    """An entry's item name: the ranker's own for an item simmed with a random suffix ("Master's Cloak of
    Intellect"), else the brief's; None for none."""
    if e.get("sfx") and isinstance(e.get("name"), str) and e["name"] and not e["name"].startswith("item "):
        return e["name"]
    return b.get("name") or None


def _entries(cb):
    """(slot label, entry) for every ranked entry of a character's ranks."""
    for label, entries in ((cb or {}).get("slots") or {}).items():
        for e in entries if isinstance(entries, list) else ():
            if isinstance(e, dict) and isinstance(e.get("id"), int):
                yield label, e


def _rank(e):
    """An entry's rank for sorting: unranked ones (rank 0: no source yet, a later phase) after every ranked one."""
    r = e.get("rank")
    return r if isinstance(r, int) and not isinstance(r, bool) and r >= 1 else 999


def _sfx(e):
    """An entry's random suffix without its sign (0 for none)."""
    v = e.get("sfx")
    return abs(v) if isinstance(v, int) and not isinstance(v, bool) else 0


def _wears(e, worn_ids, cb):
    """Whether the character wears an entry's item: its id is worn, and (an item ranked with a random suffix) the
    ranks' worn copy of it, if any, has that suffix (another suffix of a worn item is something to get)."""
    if e["id"] not in worn_ids:
        return False
    if not _sfx(e):
        return True
    mine = {_sfx(x) for _, x in _entries(cb) if x["id"] == e["id"] and x.get("how") == "worn"}
    return not mine or _sfx(e) in mine


def _mate(e, brief_fn):
    """The name of the weapon an entry was simmed with ("with": a one-hander and an off hand in place of a
    two-hander, the gain the pair's), or None (none, or no name for it)."""
    if not isinstance(e.get("with"), int):
        return None
    if isinstance(e.get("with_name"), str) and e["with_name"] and not e["with_name"].startswith("item "):
        return e["with_name"]
    return _brief(brief_fn, e["with"]).get("name") or None


PAIRED = {"main hand": "off hand", "off hand": "main hand"}     # a pair's other half (next_upgrades lists one)


def next_upgrades(cb, worn_ids, brief_fn, n=3):
    """The prompt's gear.next_upgrades: for up to n slots, the best item to get now from Glide's own best-in-slot sims
    (the ranker's "next" first, then each slot's best that isn't worn or in the bags, can be had now, needs no
    profession and is beyond the sims' noise), largest gain first: [{"slot", "item", "dps", "plus_minus" (2 sigma),
    "source"}, plus "with" (the weapon it is paired with: dps is the pair's, and the pair's other slot isn't listed
    again) and "cond" (a bonus against a creature type or in an area the number leaves out) when so]. Items without
    a name are left out (never an id)."""
    if not isinstance(cb, dict):
        return []
    picks = {}                                           # slot group -> (label, entry, its gain, the ranker's next)
    nx = cb.get("next") if isinstance(cb.get("next"), dict) else None
    if nx and isinstance(nx.get("id"), int) and not _wears(nx, worn_ids, cb) and beyond_noise(nx.get("d"),
                                                                                               nx.get("s")):
        picks[slot_group(nx.get("slot")) or str(nx.get("slot"))] = (nx.get("slot"), nx, _num(nx.get("d"), 0.0), True)
    for label, e in sorted(_entries(cb), key=lambda le: _rank(le[1])):
        group = slot_group(label) or str(label)
        if _wears(e, worn_ids, cb) or e.get("how") == "worn" or e.get("bag") or e.get("avail") != "now" \
                or e.get("req") or not beyond_noise(e.get("d"), e.get("s")):
            continue
        d = _num(e.get("d"), 0.0)
        if group not in picks or (not picks[group][3] and d > picks[group][2]):
            picks[group] = (label, e, d, False)
    out, covered = [], set()
    for label, e, d, _ in sorted(picks.values(), key=lambda p: (not p[3], -p[2])):
        group = slot_group(label) or str(label)
        if group in covered:
            continue                                     # (the other half of a pair listed already: one gain)
        b = _brief(brief_fn, e["id"])
        name = _named(e, b)
        mate = _mate(e, brief_fn)
        if not name or (isinstance(e.get("with"), int) and not mate):
            continue
        row = {"slot": slot_word(label), "item": name, "dps": round(d, 1)}
        if mate:
            row["with"] = mate
            covered.add(PAIRED.get(group))
        covered.add(group)
        s = _num(e.get("s"))
        if s is not None:
            row["plus_minus"] = round(2 * s, 1)
        src = b.get("comes_from") or (e.get("source") if isinstance(e.get("source"), str) else None)
        if src:
            row["source"] = src
        if e.get("cond"):
            row["cond"] = True
        out.append(row)
        if len(out) >= n:
            break
    return out


def bis_rows(cb, slot, worn_ids, brief_fn, n=6, ids=False):
    """A slot's best-in-slot ranks (the coach's bis_slot tool), best first (unranked ones after the ranked):
    [{"item", "rank", "d" (DPS over what is worn now), "source", "avail" (now, later, nosrc)}, plus "worn" (they wear
    it), "in_bags" (it is in their bags), "with_set" (best only with another piece of its item set: d is what it alone
    changes), "about_same" (within the sims' noise), "with" (the weapon it is paired with: d is the pair's), "cond" (a
    bonus against a creature type or in an area d leaves out) and "needs" (a profession it needs) when so; ids: "id"
    too]. None for a word that names no slot."""
    group = slot_group(slot)
    if group is None:
        return None
    best = {}
    for label, e in _entries(cb):
        if slot_group(label) != group:
            continue
        k = (e["id"], _sfx(e))
        if k not in best or _rank(e) < _rank(best[k]):
            best[k] = e
    out = []
    for e in sorted(best.values(), key=lambda x: (_rank(x), -(_num(x.get("d")) or 0)))[:n]:
        b = _brief(brief_fn, e["id"])
        d = _num(e.get("d"))
        row = {"item": _named(e, b), "rank": e.get("rank"), "d": round(d, 1) if d is not None else None,
               "source": b.get("comes_from") or (e.get("source") if isinstance(e.get("source"), str) else None),
               "avail": e.get("avail")}
        worn = _wears(e, worn_ids, cb)
        if worn:
            row["worn"] = True
        elif e.get("together"):
            row["with_set"] = True
        elif about_same(d, e.get("s")):
            row["about_same"] = True
        if e.get("bag") and not worn:
            row["in_bags"] = True
        mate = None if worn else _mate(e, brief_fn)
        if mate:
            row["with"] = mate
        if e.get("cond"):
            row["cond"] = True
        if e.get("req"):
            row["needs"] = e["req"]
        if ids:
            row["id"] = e["id"]
        out.append(row)
    return out


def bis_enchants(cb):
    """The enchants a character's ranks suggest for worn items without one (the ranker's "enchants": {slot: entry} or
    [entry with "slot"]): [{"slot", "item" (the worn item's id, or None), "id" (the enchant's), "name", "d", "s"}]."""
    got = cb.get("enchants") if isinstance(cb, dict) else None
    if isinstance(got, dict):
        rows = [dict(e, slot=e.get("slot", label)) for label, e in got.items() if isinstance(e, dict)]
    elif isinstance(got, list):
        rows = [e for e in got if isinstance(e, dict)]
    else:
        rows = []
    out = []
    for e in rows:
        name = e.get("name") if isinstance(e.get("name"), str) else e.get("enchant")
        eid = e.get("id", e.get("enchant_id"))
        out.append({"slot": e.get("slot"), "item": e.get("item") if isinstance(e.get("item"), int) else None,
                    "id": eid if isinstance(eid, int) else None, "name": name if isinstance(name, str) else None,
                    "d": _num(e.get("d")), "s": _num(e.get("s"))})
    return out


class Gear:
    @property
    def opt(self):
        """The gear settings, read through settings_manifest.json (defaults, ranges)."""
        return settings.Settings("gear", self.cfg)

    def __init__(self, cfg):
        self.cfg = cfg
        with open(os.path.join(HERE, "data", "stat_weights.json"), encoding="utf-8") as fh:
            self.profiles = json.load(fh)["profiles"]
        self.sim = None                 # the weights WoWSims measured on this character (sim_weights.py), or None
        self.sim_version = 0            # bumped when they change (rules.py compares the bags again)
        self._bis = (None, None)        # (character key, its best-in-slot ranks: bis.py's bis.json "chars" entry)
        self._bis_sig = None
        self.bis_version = 0            # bumped when the ranks change (what was told doesn't count)

    def set_sim(self, entry):
        """The character's WoWSims weights ({"class", "spec", "w", "wowsims", ...}), or None."""
        if entry is not self.sim:
            self.sim = entry
            self.sim_version += 1

    def set_bis(self, char_key, char_bis):
        """The best-in-slot ranks of the character playing (bis.BisRanker's CharBis: tag, gear_key, dps, slots, set,
        next, told, and "enchants" when it suggests any; char_key "Name-Realm", or "Name" when the realm isn't known),
        or None. bis_version goes up when they change; what the coach was told (told) isn't a change."""
        cb = char_bis if isinstance(char_bis, dict) and char_bis else None
        sig = (char_key, json.dumps({k: v for k, v in (cb or {}).items() if k != "told"}, sort_keys=True,
                                    default=str) if cb else None)
        self._bis = (char_key if cb else None, cb)
        if sig != self._bis_sig:
            self._bis_sig = sig
            self.bis_version += 1

    @property
    def bis(self):
        """The best-in-slot ranks last handed over (set_bis), or None."""
        return self._bis[1]

    @property
    def bis_key(self):
        return self._bis[0]

    def bis_for(self, name):
        """The best-in-slot ranks when they are the character `name`'s ("Name" or "Name-Realm"), else None."""
        key, cb = self._bis
        if cb is None or not name or not key:
            return None
        return cb if key == name or str(key).split("-", 1)[0] == name else None

    def bis_now(self, state):
        """(the ranks of the character playing or None, the item ids it wears) from a GameState: what the coach's
        bis_slot tool and the prompt's gear.next_upgrades read (coach_tools.bis, prompt_context's gear_fn)."""
        try:
            name = ((getattr(state, "pages", None) or {}).get("B") or {}).get("nm")
            worn = [it.get("id") for it in (getattr(state, "gear", None) or {}).get("W") or ()]
        except AttributeError:
            return None, []
        return self.bis_for(name), [i for i in worn if isinstance(i, int)]

    def bis_on(self):
        """The gear.bis switch: Glide's own best-in-slot ranks (the coach's next upgrade and missing enchants)."""
        try:
            return bool(self.opt.get("bis"))
        except KeyError:                # (a manifest without the switch: nothing can have turned it off)
            return True

    def weights(self, cls, level):
        sim = self.sim
        if sim and self.opt.get("sims") and (level or 0) >= 60 and str(cls or "").upper() == sim.get("class"):
            return {"title": "WoWSims %s (%s, your gear)" % (sim.get("wowsims") or "", sim.get("spec") or ""),
                    "kind": "sim", "spec": sim.get("spec"), "w": sim["w"]}
        cls = (cls or "").capitalize()
        kind = self.opt.get("kind")
        spec = self.cfg.get("spec")
        cands = [p for p in self.profiles if p["class"] == cls and p["min"] <= (level or 1) <= p["max"]]
        for pick in ([p for p in cands if p["kind"] == kind and (not spec or (p["spec"] or "").lower() == spec.lower())],
                     [p for p in cands if p["kind"] == kind], cands):
            if pick:
                return pick[0]
        return None

    @staticmethod
    def proficient(item, cls, level):
        """None = unknown, True/False otherwise."""
        prof = PROF.get((cls or "").upper())
        if not prof:
            return None
        armor, weapons, dual = prof
        if item["cls"] == 4:                       # armor
            name = ARMOR_BY_ID.get(item["sub"])
            if name is None or name == "Cloth" or item["sub"] == 0:
                return True
            need = armor.get(name)
            return need is not None and level >= need
        if item["cls"] == 2:                       # weapon
            name = WEAPON_BY_ID.get(item["sub"])
            if name is None or name in ("Generic",):
                return True
            need = weapons.get(name)
            return need is not None and level >= need
        return True

    @staticmethod
    def untrained(item, skills):
        """Skill name if the addon says this weapon/armor type isn't trained, else None (trained or unknown)."""
        if not skills:
            return None
        if item["cls"] == 2 and item["sub"] in WEAPON_LABEL and skills.get("weapons") is not None:
            return None if item["sub"] in skills["weapons"] else WEAPON_LABEL[item["sub"]]
        if item["cls"] == 4 and item["sub"] in ARMOR_LABEL and skills.get("armor") is not None:
            return None if item["sub"] in skills["armor"] else ARMOR_LABEL[item["sub"]]
        return None

    def usable_now(self, item, cls, level, skills=None):
        """-> (ok: True/False/None, reason). Trained skills from the game beat the class table."""
        miss = self.untrained(item, skills)
        if miss:
            return False, "not trained: %s" % miss
        if skills and ((item["cls"] == 2 and skills.get("weapons") is not None and item["sub"] in WEAPON_LABEL) or
                       (item["cls"] == 4 and skills.get("armor") is not None and item["sub"] in ARMOR_LABEL)):
            return True, None
        prof = self.proficient(item, cls, level)
        return prof, ("armor/weapon type" if prof is False else None)

    def score(self, item, w):
        if not item:
            return 0.0
        st, ws = item["stats"], w["w"]
        total = 0.0
        ranged = item["loc"] in RANGED
        for code, v in st.items():
            if code == "DP":
                total += v * ws.get("DPR" if ranged else "DP", 0)
            elif code == "WS":
                key = "WSR" if ranged else ("WS2" if item["loc"] == "INVTYPE_2HWEAPON" else
                                            ("WSO" if item["loc"] in ("INVTYPE_WEAPONOFFHAND",) else "WSM"))
                total += v * ws.get(key, 0)
            else:
                total += v * ws.get(code, 0)
        return round(total, 1)

    def compare_all(self, worn, candidates, cls, level, skills=None):
        """Every candidate with a verdict: upgrade / small upgrade / not better / can't use / later.
        -> list of dict(item, key, slot, slot_name, replaces, score, replaces_score, gain, verdict, ...)."""
        w = self.weights(cls, level)
        if not w:
            return []
        by_slot = {}
        for it in worn:
            try:
                by_slot[int(it["key"])] = it
            except ValueError:
                pass
        dual = (PROF.get((cls or "").upper()) or (None, None, None))[2]
        out = []
        for it in candidates:
            slots = list(LOC_SLOTS.get(it["loc"], []))
            if it["loc"] == "INVTYPE_WEAPON" and (dual is None or level < dual):
                slots = [16]
            if not slots:
                continue
            new = self.score(it, w)
            best = None
            for slot in slots:
                cur = by_slot.get(slot)
                old = self.score(cur, w)
                if it["loc"] == "INVTYPE_2HWEAPON":
                    old += self.score(by_slot.get(17), w)  # a 2H also replaces the off-hand
                gain = new - old
                if best is None or gain > best[0]:
                    best = (gain, slot, cur, old)
            gain, slot, cur, old = best
            prof, why = self.usable_now(it, cls, level, skills)
            wait = it["req"] if it["req"] > level else None
            threshold = max(0.5, abs(old) * self.opt.get("min_gain_pct") / 100.0)
            if prof is False:
                verdict = "can't use (%s)" % why
            elif not it["usable"] and prof is not True and not wait:
                verdict = "can't use (game says unusable)"
            elif gain > threshold or (cur is None and gain > 0):
                verdict = "upgrade" + (" at level %d" % wait if wait else "") + (" (slot is empty)" if cur is None else "")
            elif gain > 0:
                verdict = "small upgrade" + (" at level %d" % wait if wait else "")
            else:
                verdict = "not better"
            out.append({"item": it["name"], "key": it["key"], "slot": slot, "slot_name": SLOT_NAMES.get(slot),
                        "replaces": cur["name"] if cur else None, "score": new, "replaces_score": round(old, 1),
                        "gain": round(gain, 1), "verdict": verdict, "wait_level": wait,
                        "stats": it["stats"], "ilvl": it["ilvl"], "req": it["req"]})
        return out

    def evaluate(self, worn, candidates, cls, level, skills=None):
        """-> upgrades only (gain above the threshold), sorted by gain."""
        ups = [u for u in self.compare_all(worn, candidates, cls, level, skills) if u["verdict"].startswith("upgrade")]
        return sorted(ups, key=lambda x: -x["gain"])

    def rank_rewards(self, worn, choices, cls, level, skills=None):
        """Quest reward choices ranked by gain over what's worn (negative gain = not an upgrade)."""
        w = self.weights(cls, level)
        if not w or not choices:
            return []
        ranked = []
        ups = {u["key"]: u for u in self.evaluate(worn, choices, cls, level, skills)}
        for it in choices:
            u = ups.get(it["key"])
            ranked.append({"item": it["name"], "key": it["key"], "gain": u["gain"] if u else 0.0,
                           "slot_name": u["slot_name"] if u else SLOT_NAMES.get((LOC_SLOTS.get(it["loc"]) or [0])[0]),
                           "score": self.score(it, w), "usable": it["usable"] and self.usable_now(it, cls, level, skills)[0] is not False,
                           "untrained": self.untrained(it, skills)})
        return sorted(ranked, key=lambda x: (-x["gain"], -x["score"]))
