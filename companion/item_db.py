"""WoWSims Forever's item database (its assets/database/db.json and leftover_db.json, MIT, read at a release tag
through wowsims.WoWSims.file): what Glide knows about an item before the player has seen it. Which items a character
can wear (eligible: WoWSims' own rule, ui/sim/proto/items.ts canEquipItem, for the classes in CLASS_GEAR), the items of
a character's level band (band), an item's stats in Glide's stat codes (the addon's Gear.lua STAT_CODE / TOOLTIP,
gear.py), what a random suffix and an enchant add, where an item comes from on the player's side (sources) and whether
it can be had now (avail). Pure: no files, no network (research.py fetches and caches; item_text.py words a source for
the player).

db.json is WoWSims' UIDatabase in protojson (proto/ui.proto): items [{id, name, type, armorType, weaponType, handType,
rangedWeaponType, reqLvl, phase, classAllowlist, factionRestriction, sources, scalingOptions {"0": {ilvl,
randPropPoints, stats {Stat index: value}}}, pseudoStats, randomSuffixOptions}], randomSuffixes, enchants, zones, npcs
and factions. leftover_db.json holds more low-level items the same way (WoWSims merges it; an id in both is db.json's).

A source is a Src: {"kind": "drop"|"trash"|"world"|"quest"|"vendor"|"crafted"|"rep"|"pvp", "npc", "npc_name", "zone",
"zone_name", "instance", "difficulty", "quest", "quest_name", "profession", "rep_faction", "rep_level", "conf", "src"},
every key present (None where it doesn't apply). WoWSims lists a vendor and the reputation it needs as two sources; they
are one "rep" Src here, with the vendor in it. An "instance" is the dungeon or raid a drop is in (Blackrock Spire split
into its Upper and Lower halves by BRS_WING), "difficulty" WoWSims' 1 dungeon, 2 raid. Names are read cleaned (_text:
a few keep a backslash before a quote).
"""
import json
import math
import re

WS_CONF = 0.8                       # how sure a WoWSims fact is (the plan's rule 1: obs 0.95, client 0.9, kb 0.6, ...)
SOURCE_CONF = {"obs": 0.95, "client": 0.9, "ws": WS_CONF, "kb": 0.6, "web": 0.3}
BOND_EQUIP = 2                      # the client's ItemSparse Bonding: binds when equipped
BAND_BELOW, BAND_ABOVE = 10, 5      # a character's band: items it can wear from 10 levels below to 5 above its own
LEVEL_CAP = 60
PHASE_LATER = 2                     # WoWSims' ContentPhaseUnknown: not to be had yet (raids not open, AQ war effort)
SIDES = {"Alliance": 1, "Horde": 2}  # WoWSims' Faction (an item's factionRestriction, a source's side)

# per class, as WoWSims has it (capabilities_auto_gen.ts): its Class id, the heaviest ArmorType (1 cloth .. 4 plate),
# the WeaponTypes it wields (-> whether two-handed too), the RangedWeaponTypes, and whether it dual wields
CLASS_GEAR = {
    "MAGE": {"id": 8, "armor": 1, "weapons": {2: False, 5: False, 8: True, 9: False}, "ranged": (5,), "dual": False},
    "PALADIN": {"id": 2, "armor": 4, "weapons": {1: True, 4: True, 5: False, 6: True, 7: False, 9: True},
                "ranged": (7,), "dual": False},
}
FINGER, TRINKET, WEAPON, RANGED = 11, 12, 13, 14        # ItemType (1 head .. 10 feet are armor)
OFF_HAND, TWO_HAND = 3, 4                               # HandType
HELD_IN_OFF_HAND = (5, 7)                               # WeaponType: off-hand frill, shield

# WoWSims' Stat index -> Glide's stat code. Spell damage (5) is SP; healing (4) repeats it on "damage and healing"
# items and is HL only where it differs. HO, PEN, HA, BR and BV are codes the strip doesn't carry (holy damage, spell
# penetration, haste, block rating and value); health, mana, resistances and the like aren't kept.
STAT_CODE = {0: "ST", 1: "AG", 2: "SA", 3: "IN", 5: "SP", 6: "AC", 7: "FI", 8: "FR", 9: "HO", 10: "NA", 11: "SH",
             12: "PEN", 13: "SI", 14: "AP", 15: "RA", 17: "HT", 18: "CR", 19: "HA", 22: "DF", 23: "BR", 24: "BV",
             25: "DG", 26: "PA", 27: "AR", 28: "AR", 31: "MR"}
HEALING, SPELL_DAMAGE = 4, 5
# PseudoStat index -> code: the older "+1% ..." lines, as Gear.lua's TOOLTIP patterns read them
PSEUDO_CODE = {12: "HT", 13: "HS", 14: "CR", 15: "CS"}

PROFESSIONS = {1: "Alchemy", 2: "Blacksmithing", 3: "Enchanting", 4: "Engineering", 5: "Herbalism", 6: "Inscription",
               8: "Leatherworking", 9: "Mining", 10: "Skinning", 11: "Tailoring"}
REP_LEVELS = {1: "Hated", 2: "Hostile", 3: "Unfriendly", 4: "Neutral", 5: "Friendly", 6: "Honored", 7: "Revered",
              8: "Exalted"}

# Blackrock Spire is one zone (1583) in WoWSims: which half each of its bosses is in (classic's layout); others and
# trash are just "Blackrock Spire"
BRS = 1583
BRS_WING = {9196: "Lower", 9217: "Lower", 9218: "Lower", 9219: "Lower", 9236: "Lower", 9237: "Lower", 9568: "Lower",
            9596: "Lower", 9718: "Lower", 9736: "Lower", 10220: "Lower", 10263: "Lower", 10268: "Lower",
            10376: "Lower", 10584: "Lower", 10596: "Lower", 16080: "Lower",
            9816: "Upper", 10264: "Upper", 10339: "Upper", 10363: "Upper", 10429: "Upper", 10430: "Upper",
            10509: "Upper", 10899: "Upper", 16042: "Upper"}
# the honor vendors' zones, which db.json's zones leave out: their sources read "PvP vendor"
PVP_VENDOR_ZONES = {2917, 2918}
PVP_VENDOR = "PvP vendor"
# WoWSims gives most PvP items no factionRestriction and a source {"pvp": {}} with no side (v1.0.4: none of the 87
# High Warlord's and Grand Marshal's items has one): the side is in the PvP rank title of the name ("Premier High
# Warlord's Spellblade": Horde), or "of the Horde" / "of the Alliance" (the insignia). Each side's titles, the longer
# ones first; "Champion's" isn't Alliance's "Knight-Champion's" (no letter or hyphen before a title)
PVP_TITLES = (
    (2, re.compile(r"(?<![\w-])(?:High Warlord|Warlord|Lieutenant General|General|Champion|Centurion|Legionnaire|"
                   r"Blood Guard|Stone Guard|First Sergeant|Senior Sergeant)'s\b|\bof the Horde$")),
    (1, re.compile(r"(?<![\w-])(?:Grand Marshal|Field Marshal|Marshal|Lieutenant Commander|Commander|"
                   r"Knight-Champion|Knight-Captain|Knight-Lieutenant|Sergeant Major|Master Sergeant)'s\b|"
                   r"\bof the Alliance$")))

# the game's inventory slot (the strip's worn key) -> the ItemType an enchant names
INV_TO_TYPE = {1: 1, 2: 2, 3: 3, 15: 4, 5: 5, 9: 6, 10: 7, 6: 8, 7: 9, 8: 10, 11: 11, 12: 11, 13: 12, 14: 12,
               16: 13, 17: 13, 18: 14}
MAIN_HAND, OFF_HAND_SLOT = 16, 17
# EnchantType: 1 two-hand, 2 shield, 4 staff, 5 off-hand frill: which hand each can't go on
NOT_IN = {MAIN_HAND: (2, 5), OFF_HAND_SLOT: (1, 4)}

SRC_KEYS = ("kind", "npc", "npc_name", "zone", "zone_name", "instance", "difficulty", "quest", "quest_name",
            "profession", "rep_faction", "rep_level", "conf", "src")
KINDS = ("drop", "trash", "world", "quest", "vendor", "crafted", "rep", "pvp")


def _text(v):
    """A name from WoWSims' data, cleaned: some keep a backslash before a quote ('Eric \\"The Swift\\"')."""
    return str(v or "").replace('\\"', '"').replace("\\'", "'").strip()


def src(kind, **fields):
    """A Src: every key, None where not given; a WoWSims fact unless conf and src say otherwise."""
    out: dict = dict.fromkeys(SRC_KEYS)
    out.update(kind=kind, conf=WS_CONF, src="ws")
    out.update(fields)
    return out


def side(faction):
    """WoWSims' Faction for "Alliance" / "Horde" (1 / 2), else 0 (either)."""
    return SIDES.get(str(faction or "").capitalize(), 0)


def _scaling(rec):
    return ((rec or {}).get("scalingOptions") or {}).get("0") or {}


def _sided(s):
    """The side of a WoWSims source that has one: a vendor's, a quest's or a reputation's faction (1, 2); 0 for one
    with no side; None for a source of another kind (a drop, a craft, PvP)."""
    for kind, field in (("soldBy", "faction"), ("quest", "faction"), ("rep", "factionId")):
        if isinstance(s.get(kind), dict):
            return s[kind].get(field) or 0
    return None


def pvp_side(rec):
    """The side a PvP source of the item ({"pvp": {}}, which WoWSims gives no side) is for: the rank title in its name
    (PVP_TITLES), else the one side every vendor, quest and reputation it lists is on; 0 when it can't be told."""
    name = _text((rec or {}).get("name"))
    for side_id, pat in PVP_TITLES:
        if pat.search(name):
            return side_id
    sides = {_sided(s) for s in (rec or {}).get("sources") or () if isinstance(s, dict)} - {None, 0}
    return sides.pop() if len(sides) == 1 else 0


def item_side(rec):
    """The only side that can have the item (1 Alliance, 2 Horde), or 0 for either: its factionRestriction; else, for
    a PvP item every source of which has a side (PvP, vendors, quests, reputations; no drop or craft), pvp_side."""
    restricted = (rec or {}).get("factionRestriction") or 0
    if restricted:
        return restricted
    raw = [s for s in (rec or {}).get("sources") or () if isinstance(s, dict) and s]
    if not any("pvp" in s for s in raw):
        return 0
    if any("pvp" not in s and not _sided(s) for s in raw):
        return 0                                    # (a drop, a craft, a vendor of either side: anyone)
    return pvp_side(rec)


def required_level(rec):
    """The level that wears an item: its reqLvl, or for one without (a quest reward: the quest carries it) 60 from item
    level 60 and 5 below its item level under that (WoWSims' Player.requiredLevelOf)."""
    if rec.get("reqLvl"):
        return int(rec["reqLvl"])
    ilvl = int(_scaling(rec).get("ilvl") or rec.get("ilvl") or 0)
    return LEVEL_CAP if ilvl >= LEVEL_CAP else max(1, ilvl - 5)


def _number(v):
    return int(v) if float(v).is_integer() else round(float(v), 2)


def codes(stats, pseudo=None):
    """Glide's stat codes for WoWSims stats ({index: value}, keys as text or numbers, or a list by index) and
    pseudo-stats (a list by index). -> {code: value}, zeroes left out."""
    by = dict(enumerate(stats)) if isinstance(stats, list) else {int(k): v for k, v in (stats or {}).items()}
    out = {}
    for i, v in by.items():
        code = STAT_CODE.get(i)
        if code and v:
            out[code] = out.get(code, 0) + v
    heal, dmg = by.get(HEALING) or 0, by.get(SPELL_DAMAGE) or 0
    if heal and heal != dmg:
        out["HL"] = heal
    for i, v in enumerate(pseudo or ()):
        code = PSEUDO_CODE.get(i)
        if code and v:
            out[code] = out.get(code, 0) + v
    return {k: _number(v) for k, v in out.items() if v}


class ItemDB:
    """One WoWSims release's items (tag: its release tag). Built by load() and not changed after (its band, name
    and quest side caches aside, which any thread may fill), so threads may share it."""

    def __init__(self, tag):
        self.tag = tag
        self.items, self.suffixes, self.enchants = {}, {}, {}      # id -> record; effectId -> [records]
        self.zones, self.npcs, self.factions = {}, {}, {}          # id -> record; id -> name
        self.leftover = False                                      # whether leftover_db.json came in
        self._bands, self._names, self._quest_sides = {}, None, None

    @classmethod
    def load(cls, db, leftover, tag):
        """The database from db.json's bytes and leftover_db.json's (or None). Raises ValueError for a db.json that
        isn't one; a leftover_db.json that isn't is left out (db.json alone still answers for most gear)."""
        d = json.loads(db)
        if not isinstance(d, dict) or not isinstance(d.get("items"), list):
            raise ValueError("not a WoWSims database (no items)")
        self = cls(tag)
        self._add(d)
        if leftover:
            try:
                extra = json.loads(leftover)
            except ValueError:
                extra = None
            if isinstance(extra, dict) and isinstance(extra.get("items"), list):
                self._add(extra)
                self.leftover = True
        return self

    def _add(self, d):
        for it in d.get("items") or ():
            if isinstance(it, dict) and isinstance(it.get("id"), int):
                self.items.setdefault(it["id"], it)
        for s in d.get("randomSuffixes") or ():
            if isinstance(s, dict) and isinstance(s.get("id"), int):
                self.suffixes.setdefault(s["id"], s)
        for e in d.get("enchants") or ():
            if isinstance(e, dict) and isinstance(e.get("effectId"), int):
                self.enchants.setdefault(e["effectId"], []).append(e)
        for z in d.get("zones") or ():
            if isinstance(z, dict) and isinstance(z.get("id"), int):
                self.zones.setdefault(z["id"], z)
        for n in d.get("npcs") or ():
            if isinstance(n, dict) and isinstance(n.get("id"), int):
                self.npcs.setdefault(n["id"], n)
        for f in d.get("factions") or ():
            if isinstance(f, dict) and isinstance(f.get("id"), int) and _text(f.get("name")):
                self.factions.setdefault(f["id"], _text(f["name"]))

    def __len__(self):
        return len(self.items)

    # ------------------------------------------------------------------------------------------------ lookups
    def item(self, item_id):
        """The raw WoWSims record, or None."""
        try:
            return self.items.get(int(item_id))
        except (TypeError, ValueError):
            return None

    def name(self, item_id):
        rec = self.item(item_id)
        return (_text(rec.get("name")) or None) if rec else None

    def npc(self, npc_id):
        """{"id", "name", "zoneId"} or None."""
        try:
            return self.npcs.get(int(npc_id))
        except (TypeError, ValueError):
            return None

    def zone(self, zone_id):
        """{"id", "name", ...} or None (the honor vendors' zones aren't in db.json)."""
        try:
            return self.zones.get(int(zone_id))
        except (TypeError, ValueError):
            return None

    def find(self, name):
        """The ids of the items with this name (any case), lowest required level first; [] when none."""
        if self._names is None:
            names = {}
            for i, rec in self.items.items():
                names.setdefault(_text(rec.get("name")).lower(), []).append(i)
            self._names = {k: sorted(v, key=lambda i: (required_level(self.items[i]), i)) for k, v in names.items()}
        return list(self._names.get(str(name or "").strip().lower(), ()))

    # ------------------------------------------------------------------------------------------------ who wears it
    @staticmethod
    def _fits(rec, gear, dual):
        """WoWSims' canEquipItem for a class, in any slot: rings and trinkets always; a weapon of a type it wields
        (two-handed only where allowed; an off-hand weapon only for a class that dual wields, frills and shields
        aside); a ranged weapon of its kinds; armor up to its heaviest. Items without a type (tabards, seals): no."""
        t = rec.get("type") or 0
        if t in (FINGER, TRINKET):
            return True
        if t == WEAPON:
            wt, hand = rec.get("weaponType") or 0, rec.get("handType") or 0
            if wt not in gear["weapons"]:
                return False
            if hand == OFF_HAND and wt not in HELD_IN_OFF_HAND and not dual:
                return False
            return hand != TWO_HAND or gear["weapons"][wt]
        if t == RANGED:
            return (rec.get("rangedWeaponType") or 0) in gear["ranged"]
        if 1 <= t <= 10:
            return gear["armor"] >= (rec.get("armorType") or 0)
        return False

    def eligible(self, item_id, ch):
        """Whether the character can wear the item within BAND_ABOVE levels: ch = {"class": "MAGE"|"PALADIN"|...,
        "level": int, "faction": "Alliance"|"Horde"}. Its class list, the other side's items, its slot rules (False
        for a class not in CLASS_GEAR) and its required level (level unknown: any)."""
        ch = ch or {}
        rec = self.item(item_id)
        gear = CLASS_GEAR.get(str(ch.get("class") or "").upper())
        if rec is None or gear is None:
            return False
        allow = rec.get("classAllowlist") or ()
        if allow and gear["id"] not in allow:
            return False
        restricted, mine = item_side(rec), side(ch.get("faction"))
        if restricted and mine and restricted != mine:
            return False
        level = ch.get("level")
        if level and required_level(rec) > int(level) + BAND_ABOVE:
            return False
        return self._fits(rec, gear, gear["dual"])

    def band(self, ch):
        """Every item id the character can wear whose required level is from BAND_BELOW below its level to
        BAND_ABOVE above (sorted; remembered per class, level and side). Level unknown: none."""
        ch = ch or {}
        level = int(ch.get("level") or 0)
        if level <= 0:
            return []
        key = (str(ch.get("class") or "").upper(), level, side(ch.get("faction")))
        ids = self._bands.get(key)
        if ids is None:
            lo = level - BAND_BELOW
            ids = sorted(i for i, rec in self.items.items()
                         if required_level(rec) >= lo and self.eligible(i, ch))
            if len(self._bands) > 16:
                self._bands.clear()
            self._bands[key] = ids
        return list(ids)

    # ------------------------------------------------------------------------------------------------ stats
    def stats(self, item_id):
        """The item's own stats in Glide's codes (its random suffix and enchant aside), with a weapon's DPS (DP) and
        speed (WS) and health per 5 (HR). Stats that count only in an area or against a mob type are left out."""
        rec = self.item(item_id)
        if rec is None:
            return {}
        sc = _scaling(rec)
        out = codes(sc.get("stats") or rec.get("stats") or {}, rec.get("pseudoStats"))
        if sc.get("hp5"):
            out["HR"] = _number(sc["hp5"])
        if sc.get("unroundedDps"):
            out["DP"] = round(float(sc["unroundedDps"]), 1)
            if rec.get("weaponSpeed"):
                out["WS"] = _number(rec["weaponSpeed"])
        return out

    def suffix_stats(self, item_id, suffix_id):
        """What a random suffix adds to this item, in Glide's codes: each of its stats times the item's
        randPropPoints / 10000, rounded to the nearest point (WoWSims' equipped_item.ts). The suffix is looked up by its
        id as the item link carries it, then without its sign. {} when the item or suffix isn't known."""
        rec = self.item(item_id)
        try:
            sid = int(suffix_id)
        except (TypeError, ValueError):
            return {}
        suffix = self.suffixes.get(sid) or self.suffixes.get(abs(sid))
        points = (_scaling(rec).get("randPropPoints") or rec.get("randPropPoints") or 0) if rec else 0
        if not suffix or not points:
            return {}
        stats = [math.floor(v * points / 10000 + 0.5) if v else 0 for v in suffix.get("stats") or ()]
        return codes(stats, suffix.get("pseudoStats"))

    def enchant(self, effect_id, slot):
        """The enchant with this effect id (the item link's enchant field) on this inventory slot (the game's: 1 head
        .. 15 back .. 16 main hand, 17 off hand, 18 ranged): the same effect id is a different enchant on another
        slot. -> {"id", "name", "stats" (Glide codes), "phase", "proc", "item_id", "spell_id"} or None."""
        try:
            recs = self.enchants.get(int(effect_id)) or ()
            slot = int(slot)
        except (TypeError, ValueError):
            return None
        t = INV_TO_TYPE.get(slot)
        fits = [e for e in recs if t in [e.get("type")] + list(e.get("extraTypes") or ())
                and (e.get("enchantType") or 0) not in NOT_IN.get(slot, ())]
        if not fits:
            return None
        e = max(fits, key=lambda e: bool(e.get("enchantType")))       # a hand's own (shield, two-hand) first
        return {"id": e["effectId"], "name": e.get("name"), "stats": codes(e.get("stats") or (), e.get("pseudoStats")),
                "phase": e.get("phase") or 1, "proc": e.get("procKind") or 0, "item_id": e.get("itemId") or None,
                "spell_id": e.get("spellId") or None}

    # ------------------------------------------------------------------------------------------------ sources
    def _zone_name(self, zone_id):
        if not zone_id:
            return None
        if zone_id in PVP_VENDOR_ZONES and zone_id not in self.zones:
            return PVP_VENDOR
        return _text((self.zones.get(zone_id) or {}).get("name")) or None

    def _drop(self, dr):
        npc, zone = dr.get("npcId") or None, dr.get("zoneId") or None
        other, group = _text(dr.get("otherName")), _text(dr.get("category"))
        if other == "World Drop" or not (npc or zone or other):
            return src("world")
        kind = "trash" if "Trash" in (other, group) else "drop"
        who = ""
        if kind == "drop" and npc:
            # an AtlasLoot group names the boss of a shared loot table (npc "Bug Trio", group "Lord Kri")
            who = group or _text((self.npcs.get(npc) or {}).get("name"))
        elif kind == "drop" and other != "Multiple NPCs":
            who = other                         # a chest or an event ("Sothos and Jarien's Heirlooms")
        zone_name = self._zone_name(zone)
        diff = dr.get("difficulty") or None
        instance = zone_name if diff else None
        if zone == BRS and kind == "drop" and npc in BRS_WING:
            instance = "%s Blackrock Spire" % BRS_WING[npc]
        return src(kind, npc=npc, npc_name=who.strip() or None, zone=zone, zone_name=zone_name, instance=instance,
                   difficulty=diff)

    def sources(self, item_id, faction):
        """Where the item comes from for a player of this side ("Alliance" / "Horde"; None: either): one Src per way
        to get it, WoWSims' order; quests, vendors and PvP (pvp_side) of the other side left out, and everything for
        an item only the other side can have (item_side)."""
        rec = self.item(item_id)
        if rec is None:
            return []
        mine = side(faction)
        restricted = item_side(rec)
        if restricted and mine and restricted != mine:
            return []

        def ours(f):
            return not f or not mine or f == mine
        raw = [s for s in rec.get("sources") or () if isinstance(s, dict) and s]
        pvp_ours = ours(pvp_side(rec)) if any("pvp" in s for s in raw) else True
        reps = [s["rep"] for s in raw if isinstance(s.get("rep"), dict) and ours(s["rep"].get("factionId"))]
        paired, out = set(), []
        for s in raw:
            if isinstance(s.get("drop"), dict):
                out.append(self._drop(s["drop"]))
            elif isinstance(s.get("quest"), dict):
                q = s["quest"]
                if ours(q.get("faction")) and q.get("id"):
                    out.append(src("quest", quest=q["id"], quest_name=_text(q.get("name")) or None))
            elif isinstance(s.get("soldBy"), dict):
                v = s["soldBy"]
                if not ours(v.get("faction")):
                    continue
                vendor = {"npc": v.get("npcId") or None, "npc_name": _text(v.get("npcName")) or None,
                          "zone": v.get("zoneId") or None, "zone_name": self._zone_name(v.get("zoneId"))}
                rep = next((i for i, r in enumerate(reps) if not r.get("factionId") or not v.get("faction")
                            or r["factionId"] == v["faction"]), None)
                if rep is None:
                    out.append(src("vendor", **vendor))
                else:
                    paired.add(rep)
                    out.append(src("rep", **vendor, **self._rep(reps[rep])))
            elif isinstance(s.get("crafted"), dict):
                out.append(src("crafted", profession=PROFESSIONS.get(s["crafted"].get("profession"))))
            elif "pvp" in s:
                if pvp_ours:
                    out.append(src("pvp"))
        out += [src("rep", **self._rep(r)) for i, r in enumerate(reps) if i not in paired]
        seen, unique = set(), []
        for s in out:
            key = (s["kind"], s["npc"], s["npc_name"], s["zone"], s["quest"], s["profession"], s["rep_faction"])
            if key not in seen:
                seen.add(key)
                unique.append(s)
        return unique

    def _rep(self, r):
        return {"rep_faction": self.factions.get(r.get("repFactionId")), "rep_level": REP_LEVELS.get(r.get("repLevel"))}

    def quest_side(self, quest_id):
        """The side ("Alliance" / "Horde") WoWSims gives a quest in any item's sources, or None (either, or a quest it
        doesn't list)."""
        if self._quest_sides is None:
            sides = {}
            for rec in self.items.values():
                for s in rec.get("sources") or ():
                    q = s.get("quest") if isinstance(s, dict) else None
                    if isinstance(q, dict) and q.get("id") and q.get("faction") in (1, 2):
                        sides.setdefault(str(q["id"]), "Alliance" if q["faction"] == 1 else "Horde")
            self._quest_sides = sides                           # (by the id's text: 5102 and "5102" alike)
        return self._quest_sides.get(str(quest_id).strip())

    def avail(self, item_id, faction, bonding=None, sources=None):
        """"later" for what WoWSims can't place in the game yet (its phase Unknown); "now" for an item with a source
        on the player's side (sources: the ones to judge by, research's merged from everywhere; else WoWSims' own), or
        one that binds when equipped (bonding: the client's ItemSparse Bonding, when known; the auction house may have
        it); else "nosrc" (also for an item only the other side can have: item_side)."""
        rec = self.item(item_id)
        if rec is not None and rec.get("phase") == PHASE_LATER:
            return "later"
        restricted, mine = item_side(rec), side(faction)
        if restricted and mine and restricted != mine:
            return "nosrc"
        if (self.sources(item_id, faction) if sources is None else sources) or bonding == BOND_EQUIP:
            return "now"
        return "nosrc"
