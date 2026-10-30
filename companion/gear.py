"""Gear evaluation: score items with RestedXP's Forever stat weights for your class/level and find
upgrades among bag items and quest reward choices."""
import json
import os

import settings

HERE = os.path.dirname(os.path.abspath(__file__))

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


class Gear:
    @property
    def opt(self):
        """The gear settings, read through settings_manifest.json (defaults, ranges)."""
        return settings.Settings("gear", self.cfg)

    def __init__(self, cfg):
        self.cfg = cfg
        with open(os.path.join(HERE, "data", "stat_weights.json"), encoding="utf-8") as fh:
            self.profiles = json.load(fh)["profiles"]

    def weights(self, cls, level):
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
