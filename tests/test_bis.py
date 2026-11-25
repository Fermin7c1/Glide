#!/usr/bin/env python3
"""Glide's own best-in-slot ranks (companion/bis.py) against a fake sim queue whose DPS follows a small model of a
level-60 mage: spell damage 1, intellect 0.5, spirit nothing, hit 2 a point up to a cap of 16, a 2-piece set worth 15,
an on-use trinket worth 10, on the same random seed the same noise (paired runs). It pins:
- the sim beats EP order (a spirit cloak EP likes, the sim doesn't);
- past the hit cap an item drops: the head with hit is the best swap today, but once the neck brings hit the set
  takes the head with spell damage;
- no unique item is worn twice, and two rings of one limitCategory never together (in any sim);
- the 2-piece set is found as a bundle (each piece alone is worse than what is worn);
- a gain under 2 sigma reads "~" (about the same) in Live.lua's chars[key].bis;
- a later-phase item is never ranked, one with no source is unranked, and neither (nor one whose profession the
  character lacks) is the next upgrade; new sources and skills re-pick it without new sims;
- none of those is ever in the best set (simmed on its own only), so none decides how what can be had ranks: a hood
  with hit no one sells doesn't fill the hit cap there and turn the pendant with hit, the next upgrade, into #2;
- the worn enchant moves to the new item, a ring enchant only for an enchanter; a worn item without an enchant gets
  the enchant worth most; a random-suffix item is simmed with its best suffix;
- an on-use trinket with no EP is still simmed (and makes the set); a two-hander replaces main hand and off hand;
- an item with a bonus vs a creature type is flagged ("cond": nothing on the raid boss);
- the worker: ranks once the gear settled, again for a new WoWSims, new bag items on their own, stops a rank when the
  gear changes mid-way; told-once state (per character) and the ranks survive a restart;
- the default request is simbuild's, with the item data's enchants and suffixes.

Run: python3 tests/test_bis.py   (prints OK)
"""
import concurrent.futures
import json
import os
import queue
import shutil
import sys
import tempfile
import threading

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
for _k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
           "TAVILY_API_KEY", "BRAVE_API_KEY"):
    os.environ[_k] = ""
import bis  # noqa: E402
import gear as gear_mod  # noqa: E402
import item_db  # noqa: E402
import sim_weights  # noqa: E402
import simbuild  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


# ---- the item data (WoWSims' db.json shape) ---------------------------------------------------------------------
STAT = {"SA": 2, "IN": 3, "SP": 5, "SI": 13, "HT": 17, "CR": 18}
VENDOR = [{"soldBy": {"npcId": 230319, "npcName": "Deliana", "zoneId": 1537, "faction": 1}}]
ITEMS = []


def item(iid, name, typ, stats, **kw):
    ITEMS.append(record(iid, name, typ, stats, **kw))
    return iid


def record(iid, name, typ, stats, **kw):
    rec = {"id": iid, "name": name, "type": typ, "reqLvl": 55, "phase": 1, "quality": 3, "sources": VENDOR,
           "scalingOptions": {"0": {"ilvl": 60, "randPropPoints": 40,
                                    "stats": {str(STAT[k]): v for k, v in stats.items()}}}}
    if typ in (1, 3, 4, 5, 6, 7, 8, 9, 10):
        rec["armorType"] = 1
    rec.update(kw)
    if rec.get("sources") is None:
        del rec["sources"]
    return rec


H0, HA, HB, HL = (item(1001, "Worn Hood", 1, {"SP": 10}), item(1002, "Hood of Aim", 1, {"HT": 8, "SP": 4}),
                  item(1003, "Hood of Power", 1, {"SP": 13}), item(1004, "Raid Crown", 1, {"SP": 50}, phase=2))
N0, NX, NB = (item(1101, "Worn Chain", 2, {"SP": 5}), item(1102, "Pendant of Aim", 2, {"HT": 6, "SP": 20}),
              item(1103, "Looted Choker", 2, {"SP": 22}, phase=2))
S0, S1 = item(1201, "Worn Mantle", 3, {"SP": 8}), item(1202, "Twin Mantle", 3, {"SP": 7}, setId=77, setName="Twins")
BK0, BK1, BK2, BKS = (item(1301, "Worn Cape", 4, {"SP": 2}), item(1302, "Cape of Spirit", 4, {"SI": 40}),
                      item(1303, "Cape of Power", 4, {"SP": 10}),
                      item(1304, "Cape", 4, {}, randomSuffixOptions=[12718, 13001]))
C0, CR = item(1401, "Worn Robe", 5, {"SP": 10}), item(1402, "Tailored Robe", 5, {"SP": 40}, requiredProfession=11,
                                                       sources=[{"crafted": {"profession": 11, "spellId": 1}}])
W0, W1 = item(1501, "Worn Bracers", 6, {"IN": 5}), item(1502, "Bracers of Wit", 6, {"IN": 9})
HD0 = item(1601, "Worn Gloves", 7, {"SP": 5})
WA0, WN, WT = (item(1701, "Worn Sash", 8, {"SP": 5}), item(1702, "Lost Sash", 8, {"SP": 40}, sources=None),
               item(1703, "Sash of Spirit", 8, {"SP": 5, "SI": 3}))
L0, L1 = item(1801, "Worn Pants", 9, {"SP": 8}), item(1802, "Twin Pants", 9, {"SP": 7}, setId=77, setName="Twins")
F0, FB = item(1901, "Boots of Aim", 10, {"HT": 10}), item(1902, "Bag Boots", 10, {"SP": 12})
RU, R4, R3, R2 = (item(2001, "Unique Band", 11, {"SP": 12}, unique=True),
                  item(2002, "Signet A", 11, {"SP": 14}, limitCategory=475),
                  item(2003, "Signet B", 11, {"SP": 16}, limitCategory=475), item(2004, "Plain Band", 11, {"SP": 6}))
T0, T1 = item(2101, "Worn Charm", 12, {"SP": 4}), item(2102, "Old Charm", 12, {"SP": 2}, mobTypes=[8])
TS = [item(2103 + i, "Charm %d" % (3 + i), 12, {"SP": 3 + i}) for i in range(6)]        # SP 3 .. 8
TFX = item(2109, "Eye of the Beast", 12, {}, itemEffects=[{"buffId": 1, "buffName": "Fire",
                                                           "effectDurationMs": 20000, "onUse": {"cooldownMs": 120000}}])
WEAPON = {"weaponSpeed": 2.0}
D0 = item(2201, "Worn Dagger", 13, {"SP": 10}, weaponType=2, handType=2, **WEAPON)
O0 = item(2202, "Worn Orb", 13, {"SP": 5}, weaponType=5, handType=3)
ST = item(2203, "Staff of Power", 13, {"SP": 20}, weaponType=8, handType=4, **WEAPON)
D1 = item(2204, "Better Dagger", 13, {"SP": 12}, weaponType=2, handType=2, **WEAPON)
O1 = item(2205, "Better Orb", 13, {"SP": 8}, weaponType=5, handType=3)
WD = item(2301, "Wand", 14, {"SP": 3}, rangedWeaponType=5)
ENCH = [{"effectId": 1883, "name": "Enchant Bracer - Greater Intellect", "type": 6, "stats": {3: 7}},
        {"effectId": 9001, "name": "Enchant Gloves - Spell Power", "type": 7, "stats": {5: 20}},
        {"effectId": 9002, "name": "Enchant Gloves - Spirit", "type": 7, "stats": {13: 10}},
        {"effectId": 9101, "name": "Enchant Ring - Spell Power", "type": 11, "stats": {5: 5}}]
for e in ENCH:
    e["stats"] = [e["stats"].get(i, 0) for i in range(39)]
    e.update(phase=1, sources=[{"crafted": {"profession": 3, "spellId": 2}}])
SUFFIXES = [{"id": 12718, "name": "of the Eagle", "stats": [0, 0, 6666, 6666] + [0] * 35},
            {"id": 13001, "name": "of Sorcery", "stats": [0] * 5 + [5000] + [0] * 33}]
DB_JSON = json.dumps({"items": ITEMS, "enchants": ENCH, "randomSuffixes": SUFFIXES,
                      "zones": [{"id": 1537, "name": "Ironforge"}]}).encode()
DB = item_db.ItemDB.load(DB_JSON, None, "v1.0.4")
# the staff scenario's: an off hand with no source, and one a vendor sells (in DB2 only: DB's ranks stay as they are)
LOST_ORB, BRIGHT_ORB = 2206, 2208
EXTRA2 = [dict(ITEMS[[r["id"] for r in ITEMS].index(O1)], id=LOST_ORB, name="Lost Orb", sources=[],
               scalingOptions={"0": {"ilvl": 60, "stats": {"5": 60}}}),
          dict(ITEMS[[r["id"] for r in ITEMS].index(O1)], id=BRIGHT_ORB, name="Bright Orb",
               scalingOptions={"0": {"ilvl": 60, "stats": {"5": 40}}})]
DB2 = item_db.ItemDB.load(json.dumps({
    "items": ITEMS + EXTRA2, "enchants": ENCH, "randomSuffixes": SUFFIXES,
    "zones": [{"id": 1537, "name": "Ironforge"}]}).encode(), None, "v1.0.4")
# the can't-have scenario's (DB3: the worn items, the pendant, a choker, and a hood with hit no one sells: the only hit
# item besides the pendant, so whether the pendant's hit counts in the set turns on whether the hood is in it)
LOST_HOOD, CHOKER = 1005, 1104
EXTRA3 = [record(LOST_HOOD, "Lost Hood of Aim", 1, {"HT": 6, "SP": 20}, sources=None),
          record(CHOKER, "Choker of Power", 2, {"SP": 24})]
DB3 = item_db.ItemDB.load(json.dumps({
    "items": [r for r in ITEMS if r["id"] in {H0, N0, S0, BK0, C0, W0, HD0, WA0, L0, F0, RU, R4, T0, T1, D0, O0, NX}]
    + EXTRA3, "enchants": ENCH, "randomSuffixes": SUFFIXES, "zones": [{"id": 1537, "name": "Ironforge"}]}).encode(),
    None, "v1.0.4")
# the fake sim's: every scenario's items
MODEL = item_db.ItemDB.load(json.dumps({
    "items": ITEMS + EXTRA2 + EXTRA3, "enchants": ENCH,
    "randomSuffixes": SUFFIXES, "zones": [{"id": 1537, "name": "Ironforge"}]}).encode(), None, "v1.0.4")
SET_77 = (S1, L1)
CAP, LONG_SD = 16, 40.0


def model_dps(worn, seed):
    """The fake sim: DPS from the stats of what a request wears, the hit cap, the set, the on-use trinket."""
    tot, ids = {}, set()
    for it in worn:
        iid, slot = int(it["id"]), int(it["key"])
        ids.add(iid)
        parts = [MODEL.stats(iid)]                              # (MODEL: every scenario's items)
        if it.get("suffix"):
            parts.append(MODEL.suffix_stats(iid, it["suffix"]))
        if it.get("enchant"):
            parts.append((MODEL.enchant(it["enchant"], slot) or {}).get("stats") or {})
        for p in parts:
            for k, v in p.items():
                tot[k] = tot.get(k, 0) + v
    dps = 500 + tot.get("SP", 0) + 0.5 * tot.get("IN", 0) + 2 * min(tot.get("HT", 0), CAP) + 0.6 * tot.get("CR", 0)
    dps += 15 if all(i in ids for i in SET_77) else 0
    dps += 10 if TFX in ids else 0
    return dps + (seed * 7919 % 101) * 0.01                    # (the seed's noise: paired runs share it)


class FakeWS:
    def __init__(self):
        self.tag = "v1.0.4"

    def current(self):
        return self.tag

    def file(self, path, tag=None):
        check(path.startswith("ui/specs/mage/dps/apls/"), path)
        return b'{"type": "TypeAPL", "priorityList": []}'


class FakeQueue:
    """sim_queue.SimQueue's submit: the model's DPS at once (delay: from a thread, a little later; fail(req) -> an
    exception to fail that run with, or None)."""

    def __init__(self, delay=False, on_submit=None, fail=None):
        self.ws, self.calls, self.on_submit, self.fail = FakeWS(), [], on_submit, fail
        self.futs = []
        self.jobs = queue.Queue() if delay else None
        if delay:
            threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        while True:
            f, req = self.jobs.get()
            threading.Event().wait(0.005)
            e = self.fail(req) if self.fail else None
            if e is not False and f.set_running_or_notify_cancel():
                self._finish(f, req, e)

    def _finish(self, f, req, e):
        if e is not None:
            f.set_exception(e)
        else:
            f.set_result(self._sim(req))

    @staticmethod
    def _sim(req):
        return {"dps": model_dps(req["worn"], req["seed"]), "stdev": LONG_SD, "iters": req["iters"]}

    def submit(self, req, prio, iterations=None, seed=None, tag=None, background=True):
        check(prio == bis.PRIO_BIS and background and iterations == req["iters"] and seed == req["seed"],
              ("submit", prio, background, iterations, seed))
        check(tag == self.ws.tag, ("tag", tag))
        self.calls.append(req)
        if self.on_submit:
            self.on_submit(len(self.calls))
        f = concurrent.futures.Future()
        self.futs.append(f)
        if self.jobs is not None:
            self.jobs.put((f, req))
        else:
            e = self.fail(req) if self.fail else None
            if e is not False:                                  # (False: left pending, a runner that hung)
                f.set_running_or_notify_cancel()
                self._finish(f, req, e)
        return f


def fake_request(char, rotation, iterations, seed, db):
    check(rotation == {"type": "TypeAPL", "priorityList": []} and db in (DB, DB2, DB3), "request inputs")
    return {"worn": [dict(it) for it in char["worn"]], "iters": iterations, "seed": seed, "skills": char["skills"]}


WORN = [(1, H0), (2, N0), (3, S0), (15, BK0), (5, C0), (9, W0), (10, HD0), (6, WA0), (7, L0), (8, F0), (11, RU),
        (12, R4), (13, T0), (14, T1), (16, D0), (17, O0)]


class FakeState:
    def __init__(self, worn=None):
        self.level, self.realm = 60, None
        self.gear = {"W": worn or self.worn(), "I": [{"key": "0.1", "id": FB, "stats": {"SP": 12}}], "R": []}
        self.tal = sim_weights.talent_list(sim_weights.SMOKE_TALENTS)

    @staticmethod
    def worn(head=H0, items=None, extra=None):
        """The strip's worn entries: WORN (or items), the head swapped; extra: {slot: fields} added to an entry."""
        out = []
        for s, i in items or WORN:
            iid = head if s == 1 else i
            it = {"key": str(s), "id": iid, "loc": "", "cls": 4, "sub": 1, "stats": dict(MODEL.stats(iid))}
            if s == 9:
                it["enchant"] = 1883
            if s == 11:
                it["enchant"] = 9101
            it.update((extra or {}).get(s) or {})
            out.append(it)
        return out

    def summary(self):
        s = {"name": "Nine", "class": "MAGE", "race": "Human", "faction": "Alliance", "level": self.level}
        if self.realm:
            s["realm"] = self.realm
        return s

    def talents(self):
        return self.tal


WEIGHTS = {"class": "MAGE", "spec": "Arcane", "wowsims": "v1.0.4", "w": {"SP": 1.0, "IN": 0.5, "SI": 0.4,
                                                                          "HT": 1.1, "CR": 0.6}}


def ranker(home, q, st, clock, srcs=None, skills=None, db=DB, weights=None):
    rk = bis.BisRanker(home, q, lambda: db, lambda: st,
                       weights or (lambda name: WEIGHTS if name == "Nine" else None),
                       sources_fn=(lambda iid: srcs.get(iid)) if srcs is not None else None,
                       now=lambda: clock[0])
    rk.request_fn = fake_request
    rk.skills_fn = lambda name: skills[0] if skills else None
    return rk


def entries(cb, group):
    return {x["id"]: x for x in cb["slots"][group]}


def order(cb, group):
    return [x["id"] for x in cb["slots"][group]]


def worn_rings(req):
    return [it for it in req["worn"] if int(it["key"]) in (11, 12)]


tmp = tempfile.mkdtemp(prefix="glide-bis-")
try:
    clock, srcs, skills, changes = [1000.0], {}, [None], []
    st, q = FakeState(), FakeQueue()
    home = os.path.join(tmp, "engine")
    rk = ranker(home, q, st, clock, srcs, skills)
    rk.on_change = lambda key, cb: changes.append((key, cb))

    # ---- the worker: ranked once the gear has settled ------------------------------------------------------
    rk.tick()
    check(not q.calls, "ranked before the gear settled")
    clock[0] += bis.SETTLE_S - 1
    rk.tick()
    check(not q.calls, "ranked before SETTLE_S")
    clock[0] += 2
    rk.tick()
    runs = len(q.calls)
    check(runs > 50, "no rank once settled (%d sims)" % runs)
    check(runs < 400, "a rank took %d sims" % runs)
    cb = json.loads(open(os.path.join(home, "data", "bis.json"), encoding="utf-8").read())["chars"]["Nine"]
    check(changes and changes[-1][0] == "Nine" and changes[-1][1]["slots"] == cb["slots"], "on_change")
    check(cb["tag"] == "v1.0.4" and cb["class"] == "MAGE" and cb["spec"] == "Arcane" and cb["skills"] is None, cb)
    check(abs(cb["dps"] - model_dps([{"key": str(s), "id": i, **({"enchant": 1883} if s == 9 else {})}
                                     for s, i in WORN], bis.SEED)) < 0.01, ("base dps", cb["dps"]))
    rk.tick()
    clock[0] += 600
    rk.tick()
    check(len(q.calls) == runs, "ranked again with nothing changed")
    seen_keys = [json.dumps(sorted((it["key"], it["id"], it.get("enchant"), it.get("suffix")) for it in r["worn"]))
                 + "|%d|%d" % (r["iters"], r["seed"]) for r in q.calls]
    check(len(seen_keys) == len(set(seen_keys)), "the same gear simmed twice in a rank")

    # ---- the sim beats EP order ----------------------------------------------------------------------------
    r = bis.Ranking(rk, rk.character(st, st.summary()), DB, WEIGHTS["w"], None, "v1.0.4")
    check(r.ep(BK1) > r.ep(BK2), "the fixture: EP likes the spirit cape")
    back = order(cb, "Back")
    check(back.index(BK2) < back.index(BK1), ("sim order over EP", back))
    check(entries(cb, "Back")[BK1]["d"] < 0 < entries(cb, "Back")[BK2]["d"], entries(cb, "Back"))

    # ---- a random-suffix item, simmed with its best suffix ---------------------------------------------------
    bks = entries(cb, "Back")[BKS]
    check(bks["sfx"] == 13001 and abs(bks["d"] - 18) < 0.01 and back[0] == BKS, ("best suffix", bks, back))
    check(any(it["id"] == BKS and it.get("suffix") == 13001 for r_ in q.calls for it in r_["worn"]), "suffix sent")
    check(bks.get("name") == "Cape of Sorcery" and "name" not in entries(cb, "Back")[BK2],
          ("named with its suffix (the coach says the item it simmed)", bks))

    # ---- past the hit cap: the head with hit drops once the neck brings hit -----------------------------------
    head = entries(cb, "Head")
    check(head[HA]["d"] > head[HB]["d"] > 2 * head[HB]["s"], ("single swaps", head))
    check(cb["set"]["Head"] == HB and cb["set"]["Neck"] == NX, ("the set", cb["set"]))
    check(head[HB]["rank"] == 1 and head[HA]["rank"] > head[HB]["rank"], ("hit cap ranks", order(cb, "Head")))
    check(HL not in head, "a later-phase item was ranked")

    # ---- unique and limitCategory rings: never worn twice or together -----------------------------------------
    for r_ in q.calls:
        ids = [int(it["id"]) for it in worn_rings(r_)]
        check(ids.count(RU) < 2, ("unique ring twice", ids))
        check(not (R3 in ids and R4 in ids), ("one limitCategory twice", ids))
    check({cb["set"]["Ring 1"], cb["set"]["Ring 2"]} == {RU, R3}, ("rings", cb["set"]))
    rings = entries(cb, "Ring")
    check(rings[R3]["d"] > 2 * rings[R3]["s"], ("the signet replaces its twin", rings[R3]))

    # ---- the set bundle ------------------------------------------------------------------------------------
    check(cb["set"]["Shoulder"] == S1 and cb["set"]["Legs"] == L1, ("set bundle", cb["set"]))
    check(entries(cb, "Shoulder")[S1]["d"] < 0 and entries(cb, "Shoulder")[S1]["rank"] == 1, entries(cb, "Shoulder"))
    # each piece is best only with the other (alone it loses): marked, and the tooltip row says "with its set"
    # instead of a loss next to "#1"
    check(entries(cb, "Shoulder")[S1].get("together") == 1 and entries(cb, "Legs")[L1].get("together") == 1
          and "together" not in entries(cb, "Shoulder")[S0], ("together", entries(cb, "Shoulder")))
    check(rk.live_section()["Nine"]["bis"][S1] == ["Shoulder", 1, None, "s"], rk.live_section()["Nine"]["bis"][S1])

    # ---- the on-use trinket with no EP is simmed and makes the set -------------------------------------------
    check(r.ep(TFX) == 0, "the fixture: no EP on the on-use trinket")
    tr = entries(cb, "Trinket")
    check(TFX in tr and tr[TFX]["how"] == "sim" and tr[TFX]["d"] > 2 * tr[TFX]["s"], ("on-use trinket", tr.get(TFX)))
    check({cb["set"]["Trinket 1"], cb["set"]["Trinket 2"]} == {TFX, TS[-1]}, ("trinkets", cb["set"]))
    check(tr[T1].get("cond") == 1 and "cond" not in tr[T0], ("a bonus vs undead flagged", tr[T1]))
    check(abs(tr[TFX]["s"] - max(2 ** 0.5 * LONG_SD / 100, bis.MIN_S)) < 0.01, ("long-run sigma", tr[TFX]))
    check(abs(entries(cb, "Head")[HB]["s"] - bis.MIN_S) < 1e-9, "a paired gain's sigma floor")

    # ---- weapons: the staff replaces main hand and off hand ---------------------------------------------------
    check(cb["set"]["Main Hand"] == ST and "Off Hand" not in cb["set"], ("weapons", cb["set"]))
    check(all(not any(int(it["key"]) == 17 for it in r_["worn"]) for r_ in q.calls
              if any(int(it["key"]) == 16 and it["id"] == ST for it in r_["worn"])), "a staff with an off hand")
    check(cb["set"]["Ranged"] == WD, "an empty slot filled")

    # ---- about the same: "~" -------------------------------------------------------------------------------
    live = rk.live_section()["Nine"]["bis"]
    check(abs(entries(cb, "Waist")[WT]["d"]) < 2 * entries(cb, "Waist")[WT]["s"] and live[WT][-1] == "~",
          ("about the same", live.get(WT)))
    check(live[HB] == ["Head", 1, round(head[HB]["d"], 1)], ("live row", live[HB]))
    # the random suffix it was ranked with, sixth (the tooltip shows the rank on that suffix only)
    check(live[BKS] == ["Back", 1, 18.0, None, None, 13001], ("a suffix item's live row", live[BKS]))
    check(live[H0][0] == "Head" and live[H0][2] == 0.0 and "~" not in live[H0], ("worn row", live[H0]))
    check(all(isinstance(k, int) and isinstance(v[1], int) for k, v in live.items()), "live keys")

    # ---- later, no source, unmet profession: never next ------------------------------------------------------
    waist = entries(cb, "Waist")
    check(waist[WN]["avail"] == "nosrc" and waist[WN]["rank"] == 0 and WN not in live, ("no source", waist[WN]))
    check(waist[WN]["d"] > entries(cb, "Neck")[NX]["d"], "the fixture: the lost sash gains most")
    chest = entries(cb, "Chest")
    check(chest[CR]["req"] == "Tailoring" and chest[CR]["rank"] == 1 and chest[CR]["d"] > 2 * chest[CR]["s"], chest)
    # neither is in the best set (the character can't have them now), yet each ranks by its gain
    check(cb["set"]["Waist"] != WN and cb["set"]["Chest"] == C0 and order(cb, "Chest")[:2] == [CR, C0],
          ("what can't be had in the set", cb["set"]))
    check(cb["next"]["id"] == NX and cb["next"]["slot"] == "Neck" and cb["next"]["avail"] == "now"
          and cb["next"]["source"] == "sold by Deliana, Ironforge", ("next", cb["next"]))
    check(set(cb["enchants"]) == {"10"} and cb["enchants"]["10"]["item"] == HD0, cb["enchants"])
    nxt = rk.next_upgrade()
    check(nxt["id"] == NX and nxt["name"] == "Pendant of Aim" and nxt["source"] == "sold by Deliana, Ironforge"
          and abs(nxt["d"] - 27) < 0.01, ("next_upgrade", nxt))
    check(rk.next_upgrade({"name": "Nine", "skills": {"Tailoring"}})["id"] == CR, "a tailor's next upgrade")

    # ---- the enchant moves over; a ring enchant only for an enchanter ----------------------------------------
    check(any(it["id"] == W1 and it.get("enchant") == 1883 for r_ in q.calls for it in r_["worn"]), "enchant moved")
    check(all(not it.get("enchant") for r_ in q.calls for it in worn_rings(r_)), "a ring enchant without Enchanting")
    miss = cb["enchants"]["10"]
    check((miss["slot"], miss["id"], miss["name"], miss["item"]) == ("Hands", 9001, "Spell Power", HD0)
          and abs(miss["d"] - 20) < 0.01 and miss["d"] > 2 * miss["s"], ("enchants", cb["enchants"]))

    # ---- new sources and skills re-pick the next upgrade without sims -----------------------------------------
    n = len(q.calls)
    # a fact the research found for the other side (it keeps one per item, for the faction it last played) isn't
    # this character's (regression: an Alliance alt got a Horde vendor's item as its next upgrade)
    srcs[WN] = {"avail": "now", "fa": "Horde", "s": [item_db.src("vendor", npc_name="Mokvar", zone_name="Orgrimmar")],
                "bond": None}
    rk.tick()
    check(entries(rk.data["chars"]["Nine"], "Waist")[WN]["avail"] == "nosrc"
          and rk.data["chars"]["Nine"]["next"]["id"] == NX and rk.source_text(WN, "Alliance") is None
          and rk.source_text(WN, "Horde") == "sold by Mokvar, Orgrimmar", "the other side's fact")
    srcs[WN] = {"avail": "now", "s": [item_db.src("drop", npc_name="Ras Frostwhisper", zone_name="Scholomance",
                                                  instance="Scholomance", conf=0.3, src="web")], "bond": None}
    rk.tick()
    cb = rk.data["chars"]["Nine"]
    check(len(q.calls) == n and cb["next"]["id"] == WN and entries(cb, "Waist")[WN]["rank"] == 1, cb["next"])
    check(rk.next_upgrade()["source"] == "drops from Ras Frostwhisper, Scholomance (web, unconfirmed)",
          rk.next_upgrade())
    check(changes[-1][1]["next"]["id"] == WN, "on_change after a re-pick")
    del srcs[WN]
    skills[0] = {"Tailoring"}
    rk.tick()
    check(len(q.calls) == n and rk.data["chars"]["Nine"]["next"]["id"] == CR, "skills re-pick")
    skills[0] = None
    rk.tick()

    # ---- told once, across a restart -----------------------------------------------------------------------
    check(not rk.told("next_upgrade|%d|0" % NX), "told before")
    rk.mark_told("next_upgrade|%d|0" % NX)
    check(rk.told("next_upgrade|%d|0" % NX, "Nine") and not rk.told("next_upgrade|%d|0" % NX, "Other"), "per char")
    rk2 = ranker(home, FakeQueue(), st, clock, srcs, skills)
    check(rk2.told("next_upgrade|%d|0" % NX) and not rk2.told("enchant_missing|1601"), "told across a restart")
    check(rk2.live_section() == rk.live_section(), "ranks across a restart")
    handed = []
    rk2.on_change = lambda key, cb_: handed.append((key, cb_))
    rk2.tick()
    rk2.tick()
    check(len(handed) == 1 and handed[0][0] == "Nine" and handed[0][1]["slots"] == rk.data["chars"]["Nine"]["slots"]
          and not rk2.queue.calls, ("the kept ranks handed to the coach at the first poll", len(handed)))
    for i in range(bis.TOLD_KEEP + 5):
        clock[0] += 1
        rk2.mark_told("k%d" % i)
    told = rk2.data["chars"]["Nine"]["told"]
    check(len(told) == bis.TOLD_KEEP and "k%d" % (bis.TOLD_KEEP + 4) in told and "k0" not in told, len(told))

    # ---- a new WoWSims: ranked again at once (the gear has long settled) --------------------------------------
    n = len(q.calls)
    q.ws.tag = "v1.0.5"
    rk.tick()
    check(len(q.calls) > n and rk.data["chars"]["Nine"]["tag"] == "v1.0.5", "a new WoWSims re-ranks")
    check(rk.told("next_upgrade|%d|0" % NX), "told kept through a rank")

    # ---- a new bag item: simmed on its own once it stays -------------------------------------------------------
    n = len(q.calls)
    st.gear["I"].append({"key": "0.2", "id": NB, "stats": {"SP": 22}})
    rk.tick()
    clock[0] += bis.SETTLE_S + 1
    rk.tick()
    added = len(q.calls) - n
    neck = entries(rk.data["chars"]["Nine"], "Neck")
    check(0 < added <= 6 and NB in neck and neck[NB].get("bag") == 1, ("bag item", added, neck.get(NB)))
    check(abs(neck[NB]["d"] - 17) < 0.01 and rk.data["chars"]["Nine"]["next"]["id"] == NX, "a bag item is not next")
    rk.tick()
    check(len(q.calls) == n + added, "the bag item simmed again")

    # ---- gear worn again (a mount trinket on, then off): its ranks again, without sims (regression: every swap
    # back ranked from nothing, the ranker running almost nonstop) ------------------------------------------------
    gk_a = rk.data["chars"]["Nine"]["gear_key"]
    st.gear["W"] = FakeState.worn(head=HB)
    rk.tick()
    clock[0] += bis.SETTLE_S + 1
    rk.tick()
    n_b = len(q.calls)
    check(n_b > n + added and rk.data["chars"]["Nine"]["gear_key"] != gk_a, "the new gear ranked")
    st.gear["W"] = FakeState.worn()
    rk.tick()
    check(rk.data["chars"]["Nine"]["gear_key"] != gk_a, "back at once: settling first")
    clock[0] += bis.SETTLE_S + 1
    rk.tick()
    back_cb = rk.data["chars"]["Nine"]
    check(len(q.calls) == n_b and back_cb["gear_key"] == gk_a and NB in entries(back_cb, "Neck")
          and back_cb["next"]["id"] == NX and rk.told("next_upgrade|%d|0" % NX), "ranked again from nothing")
    check(changes[-1][1]["gear_key"] == gk_a, "handed to the coach")

    # ---- the realm in the key ---------------------------------------------------------------------------------
    check(bis.char_key({"name": "Nine", "realm": "Argent Dawn"}) == "Nine-ArgentDawn", "realm key")

    # ---- an enchanter: the ring enchant counts, and a ring without one gets one ------------------------------
    q3, st3 = FakeQueue(), FakeState()
    rk3 = ranker(os.path.join(tmp, "enchanter"), q3, st3, [5000.0], {}, [{"Enchanting"}])
    cb3 = rk3.rank(rk3.character(st3, st3.summary()))
    base = [r_ for r_ in q3.calls if sorted(int(it["id"]) for it in r_["worn"]) == sorted(i for _, i in WORN)]
    check(base and all(any(int(it["key"]) == 11 and it.get("enchant") == 9101 for it in r_["worn"]) for r_ in base),
          "an enchanter's ring enchant")
    check(cb3["enchants"].get("12", {}).get("id") == 9101 and cb3["enchants"]["12"]["slot"] == "Ring",
          cb3["enchants"])
    check(cb3["skills"] == ["Enchanting"], cb3["skills"])

    # ---- the gear changes while a rank runs: it stops, nothing is kept -----------------------------------------
    st4, clock4 = FakeState(), [9000.0]
    q4 = FakeQueue(delay=True, on_submit=lambda n_: n_ == 3 and st4.gear.update(W=FakeState.worn(head=HB)))
    rk4 = ranker(os.path.join(tmp, "abort"), q4, st4, clock4, {}, [None])
    rk4.wait_s = 0.02
    rk4.tick()
    clock4[0] += bis.SETTLE_S + 1
    rk4.tick()
    check(q4.calls and "Nine" not in rk4.data["chars"] and not os.path.exists(os.path.join(tmp, "abort", "data",
                                                                                           "bis.json")), "aborted")
    check(not rk4.failed, "an aborted rank counted as a failure")
    rk4.tick()                                          # (the new gear: settling from now)
    check("Nine" not in rk4.data["chars"], "ranked before the new gear settled")
    clock4[0] += bis.SETTLE_S + 1
    rk4.tick()
    check(rk4.data["chars"]["Nine"]["set"]["Head"] == HB, "ranked once the new gear settled")

    # ---- the default request: simbuild's, with the item data (a ring enchant only for an enchanter) ---------
    rk6 = bis.BisRanker(os.path.join(tmp, "req"), FakeQueue(), lambda: DB, lambda: st, lambda n: WEIGHTS)
    worn = [{"key": "9", "id": W1, "enchant": 1883}, {"key": "15", "id": BKS, "suffix": 13001},
            {"key": "11", "id": RU, "enchant": 9101}]
    req = rk6.request({"name": "Nine", "class": "MAGE", "race": "Human", "faction": "Alliance", "talents": st.tal,
                       "skills": None, "worn": worn}, {}, 1000, 7, DB)
    items = req["raid"]["parties"][0]["players"][0]["equipment"]["items"]
    check(items[5] == {"id": W1, "enchant": 1883} and items[3] == {"id": BKS, "randomSuffix": 13001}
          and items[10] == {"id": RU} and req["simOptions"] == {"iterations": 1000, "randomSeed": 7}, req)

    # ---- below 60, or no weights yet: nothing --------------------------------------------------------------
    st5, q5 = FakeState(), FakeQueue()
    st5.level = 59
    rk5 = ranker(os.path.join(tmp, "low"), q5, st5, [0.0])
    rk5.tick()
    rk5._now = lambda: 1000.0
    rk5.tick()
    check(not q5.calls, "ranked below 60")
    st5.level = 60
    rk5.weights_fn = lambda name: None
    rk5.tick()
    check(not q5.calls and not rk5.failed, "ranked without weights")

    # ---- a two-hander worn: a one-hander comes with an off hand the character can have, the pair is named and its
    # gain said once (regression: the partner was the best off hand by EP whatever its source, here a Lost Orb no one
    # sells: the dagger was "+52 DPS" alone, and the dagger and the orb each got the pair's gain in the prompt) -----
    staff_worn = [(s, i) for s, i in WORN if s not in (16, 17)] + [(16, ST)]
    st7 = FakeState(FakeState.worn(items=staff_worn))
    rk7 = ranker(os.path.join(tmp, "staff"), FakeQueue(), st7, [7000.0], {}, [None], db=DB2)
    cb7 = rk7.rank(rk7.character(st7, st7.summary()))
    mh7, oh7 = entries(cb7, "Main Hand"), entries(cb7, "Off Hand")
    check(LOST_ORB in oh7 and oh7[LOST_ORB]["avail"] == "nosrc" and oh7[LOST_ORB]["rank"] == 0, oh7.get(LOST_ORB))
    check(not any(x.get("with") == LOST_ORB for xs in cb7["slots"].values() for x in xs), "a partner with no source")
    check(mh7[D1]["with"] == BRIGHT_ORB and mh7[D1]["with_name"] == "Bright Orb" and abs(mh7[D1]["d"] - 32) < 0.01
          and oh7[BRIGHT_ORB]["with"] == D1 and abs(oh7[BRIGHT_ORB]["d"] - 32) < 0.01, (mh7[D1], oh7[BRIGHT_ORB]))
    nx7 = cb7["next"]
    check(nx7["id"] == D1 and nx7["slot"] == "Main Hand" and nx7["with"] == BRIGHT_ORB
          and nx7["with_name"] == "Bright Orb" and abs(nx7["d"] - 32) < 0.01, nx7)
    check(rk7.next_upgrade()["with"] == "Bright Orb" and rk7.next_upgrade()["name"] == "Better Dagger",
          rk7.next_upgrade())
    live7 = rk7.live_section()["Nine"]["bis"]
    check(live7[D1] == ["Main Hand", mh7[D1]["rank"], 32.0, None, None, None, "Bright Orb"], live7[D1])

    def brief7(iid):
        return {"name": DB2.name(iid), "comes_from": "sold by Deliana, Ironforge"}
    nu7 = gear_mod.next_upgrades(cb7, {it["id"] for it in st7.gear["W"]}, brief7)
    hands = [u for u in nu7 if u["slot"] in ("main hand", "off hand")]
    check(len(hands) == 1 and hands[0] == nu7[0] and nu7[0]["item"] == "Better Dagger" and nu7[0]["with"] == "Bright Orb"
          and nu7[0]["dps"] == 32.0, ("the pair once", nu7))
    rows7 = gear_mod.bis_rows(cb7, "off hand", {it["id"] for it in st7.gear["W"]}, brief7)
    check(any(r_["item"] == "Bright Orb" and r_.get("with") == "Better Dagger" for r_ in rows7), rows7)

    # ---- a worn item with a random suffix: its best suffix is a candidate too, something to get (regression: the
    # pool kept one candidate per item, so a worn "of the Eagle" hid the "of Sorcery") --------------------------------
    eagle_worn = FakeState.worn(items=[(s, BKS if s == 15 else i) for s, i in WORN], extra={15: {"suffix": 12718}})
    st8 = FakeState(eagle_worn)
    rk8 = ranker(os.path.join(tmp, "eagle"), FakeQueue(), st8, [8000.0], {}, [None])
    cb8 = rk8.rank(rk8.character(st8, st8.summary()))
    capes = [x for x in cb8["slots"]["Back"] if x["id"] == BKS]
    check(sorted((x["sfx"], x["how"]) for x in capes) == [(12718, "worn"), (13001, "sim")], capes)
    sorcery = next(x for x in capes if x["sfx"] == 13001)
    check(abs(sorcery["d"] - 6.5) < 0.01 and sorcery["rank"] == 1 and sorcery["name"] == "Cape of Sorcery", sorcery)
    check(rk8.live_section()["Nine"]["bis"][BKS] == ["Back", 1, 6.5, None, None, 13001], "the better suffix's row")
    rows8 = gear_mod.bis_rows(cb8, "cloak", {it["id"] for it in eagle_worn}, brief7)
    check([(r_["item"], bool(r_.get("worn"))) for r_ in rows8[:2]] ==
          [("Cape of Sorcery", False), ("Cape of the Eagle", True)], rows8)
    nu8 = gear_mod.next_upgrades(cb8, {it["id"] for it in eagle_worn}, brief7, n=15)
    check(any(u["item"] == "Cape of Sorcery" for u in nu8), ("another suffix of a worn item is something to get", nu8))

    # ---- what the character can't have now is simmed on its own but kept out of the best set, so it doesn't decide
    # the ranks of what it can have (regression: the set took a hood with hit that no one sells, the pendant's hit
    # counted for nothing in it, and the choker read #1 for Neck while the coach named the pendant the next upgrade) --
    st14, srcs14 = FakeState(), {}
    q14 = FakeQueue()
    rk14 = ranker(os.path.join(tmp, "cant-have"), q14, st14, [60000.0], srcs14, [None], db=DB3)
    char14 = rk14.character(st14, st14.summary())
    cb14 = rk14.rank(char14)
    head14, neck14 = entries(cb14, "Head"), entries(cb14, "Neck")
    check(head14[LOST_HOOD]["avail"] == "nosrc" and head14[LOST_HOOD]["rank"] == 0
          and abs(head14[LOST_HOOD]["d"] - 22) < 0.01, ("simmed on its own, unranked", head14.get(LOST_HOOD)))
    check(cb14["set"]["Head"] == H0 and cb14["set"]["Neck"] == NX, ("the set holds what can be had", cb14["set"]))
    check(order(cb14, "Neck")[:2] == [NX, CHOKER] and neck14[NX]["rank"] == 1 and neck14[CHOKER]["rank"] == 2
          and cb14["next"]["id"] == NX, ("the ranks agree with the next upgrade", cb14["slots"]["Neck"], cb14["next"]))
    worn14 = sorted((s, i) for s, i in WORN if s != 1)
    for r_ in q14.calls:
        if any(int(it["id"]) == LOST_HOOD for it in r_["worn"]):
            check(sorted((int(it["key"]), int(it["id"])) for it in r_["worn"] if int(it["key"]) != 1) == worn14,
                  ("the hood no one sells simmed with other new gear", r_["worn"]))
    # a source found for it later: ranked by its gain among the rest, without sims
    n14 = len(q14.calls)
    srcs14[LOST_HOOD] = {"avail": "now", "s": [item_db.src("vendor", npc_name="Deliana", zone_name="Ironforge")],
                         "bond": None}
    rk14.refresh(char14)
    head14 = entries(rk14.data["chars"]["Nine"], "Head")
    check(len(q14.calls) == n14 and head14[LOST_HOOD]["rank"] == 1 and head14[H0]["rank"] == 2,
          ("a new source ranks it by its gain", head14))

    # ---- a rank that fails after running long: tried again RETRY_S after it failed, not at once (regression: the
    # failure was stamped with the time the rank started) ----------------------------------------------------------
    clock9 = [20000.0]
    q9 = FakeQueue(on_submit=lambda n_: clock9.__setitem__(0, clock9[0] + 60),
                   fail=lambda req: simbuild.SimError("the runner died"))
    st9 = FakeState()
    rk9 = ranker(os.path.join(tmp, "fail"), q9, st9, clock9, {}, [None])
    rk9.tick()
    clock9[0] += bis.SETTLE_S + 1
    rk9.tick()
    gk9 = rk9.gear_key(rk9.character(st9, st9.summary()))
    n9 = len(q9.calls)
    check(n9 * 60 > bis.RETRY_S and rk9.failed[gk9]["n"] == 1 and rk9.failed[gk9]["t"] == clock9[0],
          ("stamped when it failed", n9, rk9.failed))
    clock9[0] += bis.POLL_S
    rk9.tick()
    check(len(q9.calls) == n9, "tried again at once")
    clock9[0] += bis.RETRY_S
    rk9.tick()
    check(len(q9.calls) > n9 and rk9.failed[gk9]["n"] == 2, "tried again after RETRY_S")

    # ---- the worn gear's run fails: the batch stops at once, its other runs dropped (regression: it waited for every
    # run of the batch, 300 s each on a runner that hangs) -----------------------------------------------------------
    base10 = sorted((s, i) for s, i in WORN)

    def hung(req):
        if sorted((int(it["key"]), int(it["id"])) for it in req["worn"]) == base10:
            return simbuild.SimError("the runner hung")
        return False                                            # (every other run never ends)
    q10, st10 = FakeQueue(fail=hung), FakeState()
    rk10 = ranker(os.path.join(tmp, "hung"), q10, st10, [9000.0], {}, [None])
    rk10.wait_s = 0.05
    got10 = []

    def rank10():
        try:
            rk10.rank(rk10.character(st10, st10.summary()))
            got10.append(None)
        except Exception as e:  # noqa: BLE001 - what the rank raised is the check
            got10.append(e)
    t10 = threading.Thread(target=rank10, daemon=True)
    t10.start()
    t10.join(10)
    check(not t10.is_alive() and got10 and isinstance(got10[0], simbuild.SimError), ("stopped at once", got10))
    check(all(f.done() for f in q10.futs) and any(f.cancelled() for f in q10.futs), "the batch's other runs dropped")

    # ---- a gear change is found at the next batch, without waiting wait_s (regression: a batch that ran in less than
    # wait_s never looked, and the rank ran on to keep the old gear's ranks) ----------------------------------------
    st11, clock11 = FakeState(), [30000.0]
    q11 = FakeQueue(on_submit=lambda n_: n_ == 60 and st11.gear.update(W=FakeState.worn(head=HB)))
    rk11 = ranker(os.path.join(tmp, "stale"), q11, st11, clock11, {}, [None])
    rk11.tick()
    clock11[0] += bis.SETTLE_S + 1
    rk11.tick()
    check(60 <= len(q11.calls) < 100 and "Nine" not in rk11.data["chars"] and not rk11.failed,
          ("stopped at the next batch", len(q11.calls)))

    # ---- the lens switched while a rank runs: it stops, and the ranks kept are the new lens's, under its key
    # (regression: the "now" pool's ranks were kept under the "all" key, never redone) ---------------------------------
    st12, clock12, lens12 = FakeState(), [40000.0], ["now"]
    q12 = FakeQueue(on_submit=lambda n_: n_ == 20 and lens12.__setitem__(0, "all"))
    rk12 = ranker(os.path.join(tmp, "lens"), q12, st12, clock12, {}, [None])
    rk12.lens_fn = lambda: lens12[0]
    rk12.tick()
    clock12[0] += bis.SETTLE_S + 1
    rk12.tick()
    check("Nine" not in rk12.data["chars"], "ranked on past the lens switch")
    rk12.tick()
    clock12[0] += bis.SETTLE_S + 1
    rk12.tick()
    cb12 = rk12.data["chars"]["Nine"]
    check(json.loads(cb12["gear_key"])[-1] == "all" and HL in entries(cb12, "Head"), "the new lens's ranks")
    check(entries(cb12, "Head")[HL]["rank"] == 0 and cb12["set"]["Head"] == HB,
          ("a later phase's item simmed, unranked and out of the set", cb12["set"]))
    char12 = rk12.character(st12, st12.summary())
    gk12 = rk12.gear_key(char12, "now")
    check(rk12.rank(char12, gk12, "now")["gear_key"] == gk12 and HL not in entries(rk12.data["chars"]["Nine"],
                                                                                  "Head"),
          "kept under the key it was ranked for")

    # ---- weights of another spec: no rank until the spec's own; weights of other gear: waited for a while
    # (regression: a Fire mage's pool was screened with its Arcane weights, and nothing ranked again after) ----------
    st13, clock13, w13 = FakeState(), [50000.0], [dict(WEIGHTS, spec="Fire")]
    q13 = FakeQueue()
    rk13 = ranker(os.path.join(tmp, "weights"), q13, st13, clock13, {}, [None], weights=lambda name: w13[0])
    rk13.tick()
    clock13[0] += bis.SETTLE_S + 1
    rk13.tick()
    check(not q13.calls and not rk13.failed, "ranked with another spec's weights")
    key13 = sim_weights.SimWeights.key(sim_weights.SimWeights.character(st13, st13.summary()))
    w13[0] = dict(WEIGHTS, key="the gear before")
    rk13.tick()
    check(not q13.calls, "ranked with the weights of other gear while this gear's may still come")
    w13[0] = dict(WEIGHTS, key=key13)
    rk13.tick()
    n13 = len(q13.calls)
    check(n13 and "Nine" in rk13.data["chars"], "ranked once this gear's weights came")
    st13.gear["W"] = FakeState.worn(head=HB)
    rk13.tick()
    clock13[0] += bis.SETTLE_S + 1
    rk13.tick()
    check(len(q13.calls) == n13, "the new gear waits for its weights")
    clock13[0] += bis.WEIGHTS_WAIT_S
    rk13.tick()
    check(len(q13.calls) > n13, "ranked with what there is after WEIGHTS_WAIT_S")

    # ---- WoWSims' item data is never loaded under the ranker's lock (regression: a download there held the strip's
    # capture thread, which asks the ranker what was told) ----------------------------------------------------------
    owned = []

    def db_fn():
        owned.append(rk8.lock._is_owned())
        return DB
    rk8.item_db_fn = db_fn
    rk8.tick()
    rk8.next_upgrade()
    rk8.refresh(rk8.character(st8, st8.summary()))
    rk8.source_text(WN, "Alliance")
    check(owned and not any(owned), ("item data loaded under the lock", owned))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("OK")
