#!/usr/bin/env python3
"""The sims the player asks for and the talent check (companion/sim_compare.py) against a fake sim queue whose DPS
follows a small model of a level-60 mage: each talent build has its power and its own rotation, on another rotation it
loses 11% (WoWSims' Arcane Fire build on the Arcane rotation); gear adds spell damage 1, intellect 0.5 a point. Every
run on one seed gets the same noise (paired), with a spread of 30 DPS. It pins:
- one seed for every run (and in every request); the asked priority, not in the background;
- the base run kept per gear: the same compare again queues nothing, other gear simmed again;
- each talent build simmed with its own rotation (the preset's, by its talents when the variant names none; an
  .apl.siml one converted by the runner once); the character's own talents on their build's rotation; a custom string
  on its tree's, said so (own_apl False);
- staging: every variant at 1,000 iterations, then 10,000 for those within 3 sigma of the leader (the leader first),
  the others keep their 1,000; the budget: what hasn't finished by then is dropped (cancelled in the queue), a variant
  keeps its 1,000-iteration numbers, one whose first run didn't finish is left out, a base that didn't finish fails;
- items: a ring or trinket against the weaker of the two worn, unless a limitCategory clash or the slot asked says
  which, an empty slot first; the worn enchant moves over; the best of an item's random suffixes (screened by EP) is
  simmed and named; a two-hander empties the off hand; a one-hander over a two-hander comes with the best off hand in
  the bags, else alone; an item worn already (in the slot asked, its only slot, both of a ring's or trinket's slots, or
  one of them when it can't be worn twice: unique, a limitCategory, or unknown to the item data): no sims, d 0; a
  second copy of one that can be worn twice goes in the other slot (Band of Ten worn in one ring slot: a second one
  over Band of Four);
- a background compare (another priority) never answers with a variant left out for time: Late (a SimError); without
  a deadline (budget_s None) it waits for the queue;
- what can't be simmed is left out (an item WoWSims doesn't know, one a mage can't wear, a run that failed), all of
  them: SimError with the first reason; no WoWSims, a level that isn't 60: SimError.
- with a local WoWSims runner (GLIDE_WOWSIMS_HOME, else %TEMP%/glide-wowsims-home, with the Arcane rotation and the
  item database kept): one stat ring asked through the real queue and runner (a few small runs); else that part skips.

Run: python3 tests/test_sim_compare.py   (prints OK)
"""
import concurrent.futures
import json
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
for _k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
           "TAVILY_API_KEY", "BRAVE_API_KEY"):
    os.environ[_k] = ""
import item_db  # noqa: E402
import sim_compare  # noqa: E402
import sim_queue  # noqa: E402
import sim_weights  # noqa: E402
import simbuild  # noqa: E402
import wowsims  # noqa: E402
from sim_compare import FULL, QUICK, SEED  # noqa: E402
from sim_queue import PRIO_ASKED, PRIO_BIS  # noqa: E402
from simbuild import SimError  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


# ---- the item data (WoWSims' db.json shape) ---------------------------------------------------------------------
STAT = {"IN": 3, "SP": 5, "FR": 8, "SI": 13}
ITEMS = []


def item(iid, name, typ, stats, **kw):
    rec = {"id": iid, "name": name, "type": typ, "reqLvl": 55, "phase": 1, "quality": 3,
           "scalingOptions": {"0": {"ilvl": 60, "randPropPoints": 40,
                                    "stats": {str(STAT[k]): v for k, v in stats.items()}}}}
    if typ in (1, 3, 4, 5, 6, 7, 8, 9, 10):
        rec["armorType"] = 1
    rec.update(kw)
    ITEMS.append(rec)
    return iid


H0, HB, HP = item(1001, "Worn Hood", 1, {"SP": 10}), item(1002, "Hood of Power", 1, {"SP": 15}), \
    item(1003, "Plate Helm", 1, {"SP": 90}, armorType=4)
C0 = item(1301, "Worn Cape", 4, {"SP": 2})
CK = item(1302, "Cape", 4, {}, randomSuffixOptions=[101, 102, 103, 104])
W0, WN = item(1501, "Worn Bracers", 6, {"SP": 30}), item(1502, "Bracers of Nothing", 6, {"SI": 1})
R10, R4, RN = item(2001, "Band of Ten", 11, {"SP": 10}), item(2002, "Band of Four", 11, {"SP": 4}), \
    item(2003, "Band of Seven", 11, {"SP": 7})
L12, L9 = item(2011, "Signet of Twelve", 11, {"SP": 12}, limitCategory=5), \
    item(2012, "Signet of Nine", 11, {"SP": 9}, limitCategory=5)
T3, T5, T6 = item(2101, "Charm of Three", 12, {"SP": 3}), item(2103, "Charm of Five", 12, {"SP": 5}),     item(2102, "Charm of Six", 12, {"SP": 6})
TU = item(2104, "Unique Charm", 12, {"SP": 4}, unique=True)
D0 = item(2201, "Worn Dagger", 13, {"SP": 10}, weaponType=2, handType=2, weaponSpeed=2.0)
O0 = item(2202, "Worn Orb", 13, {"SP": 5}, weaponType=5, handType=3)
ST = item(2203, "Staff of Thirty", 13, {"SP": 30}, weaponType=8, handType=4, weaponSpeed=3.0)
D1 = item(2204, "Dagger of Twelve", 13, {"SP": 12}, weaponType=2, handType=2, weaponSpeed=2.0)
O1, O2 = item(2205, "Orb of Eight", 13, {"SP": 8}, weaponType=5, handType=3), \
    item(2206, "Orb of Two", 13, {"SP": 2}, weaponType=5, handType=3)
BAD = item(2301, "Cursed Hood", 1, {"SP": 50})                       # (its sim fails)
FOCUS = 2544
ENCH = [{"effectId": FOCUS, "name": "Arcanum of Focus", "type": 1, "stats": [0] * 5 + [8] + [0] * 33, "phase": 1}]
SUFFIXES = [{"id": 101, "name": "of Sorcery", "stats": [0] * 5 + [2500] + [0] * 33},          # SP 10
            {"id": 102, "name": "of Intellect", "stats": [0] * 3 + [2500] + [0] * 35},        # IN 10
            {"id": 103, "name": "of Frost Wrath", "stats": [0] * 8 + [2500] + [0] * 30},      # FR 10
            {"id": 104, "name": "of Spirit", "stats": [0] * 13 + [2500] + [0] * 25}]          # SI 10
DB = item_db.ItemDB.load(json.dumps({"items": ITEMS, "enchants": ENCH, "randomSuffixes": SUFFIXES}).encode(),
                         None, "v9.9.9")

# ---- talent builds, their rotations and their power -----------------------------------------------------------------
TA, TF = "050205223101311531-5500003-003", "050205203100311531-525000012001-003"
TFR, TC = "-5-053500011121331251", "050205223101311531-5500003-002"
APL_A, APL_F, APL_FR = ("ui/specs/mage/dps/apls/arcane.apl.json", "ui/specs/mage/dps/apls/arcaneFireFill.apl.json",
                        "ui/specs/mage/dps/apls/frost.apl.siml")
check(simbuild.CLASSES["MAGE"]["apl"]["Arcane"] == APL_A, "the Arcane tree's rotation")
PRESETS = """import * as PresetUtils from '@app/preset_utils';
import arcane from './apls/arcane.apl.json';
import arcaneFire from './apls/arcaneFireFill.apl.json';
import frost from './apls/frost.apl.siml';
export const ArcaneApl = PresetUtils.makePresetAPLRotation('Arcane', arcane);
export const ArcaneFireApl = PresetUtils.makePresetAPLRotation('Arcane Fire', arcaneFire);
export const FrostApl = PresetUtils.makePresetAPLRotation('Frost', frost);
export const ArcaneTalents = PresetUtils.makePresetTalents('Arcane', SavedTalents.create({ talentsString: '%s' }));
export const ArcaneFireTalents = PresetUtils.makePresetTalents('Arcane Fire', SavedTalents.create({ talentsString: '%s' }));
export const FrostTalents = PresetUtils.makePresetTalents('Frost', SavedTalents.create({ talentsString: '%s' }));
export const ArcaneBuild = PresetUtils.makePresetBuild('Arcane', { talents: ArcaneTalents, rotation: ArcaneApl });
export const ArcaneFireBuild = PresetUtils.makePresetBuild('Arcane Fire', { talents: ArcaneFireTalents, rotation: ArcaneFireApl });
export const FrostBuild = PresetUtils.makePresetBuild('Frost', { talents: FrostTalents, rotation: FrostApl });
""" % (TA, TF, TFR)
POWER = {TA: 600.0, TF: 677.0, TFR: 650.0, TC: 610.0}             # talents -> DPS before gear
OWN = {TA: "arcane", TF: "fire", TFR: "frost", TC: "arcane"}     # ... and the rotation it is made for
WRONG = 0.89                                                    # on another rotation: 11% less
W = {"SP": 1.0, "IN": 0.5}
INV_OF = {v: k for k, v in simbuild.INV_TO_SIM.items()}


def ep(stats):
    return sum(W.get(c, 0) * v for c, v in (stats or {}).items())


def model(player):
    gear = 0.0
    for idx, spec in enumerate(player["equipment"]["items"]):
        if spec:
            gear += ep(DB.stats(spec["id"]))
            if spec.get("randomSuffix"):
                gear += ep(DB.suffix_stats(spec["id"], spec["randomSuffix"]))
            if spec.get("enchant"):
                gear += ep((DB.enchant(spec["enchant"], INV_OF[idx]) or {}).get("stats"))
    t = player["talentsString"]
    rot = player["rotation"]["marker"]
    return (POWER.get(t, 600.0) + gear) * (1.0 if rot == OWN.get(t, "arcane") else WRONG)


class FakeWS:
    """WoWSims: the release in use, the repository's files (presets.ts, the .apl.json rotations), an .apl.siml
    converted."""

    def __init__(self, tag="v9.9.9"):
        self.tag, self.asked, self.converted = tag, [], []
        self.files = {simbuild.CLASSES["MAGE"]["presets"]: PRESETS.encode(),
                      APL_A: json.dumps({"type": "TypeAPL", "marker": "arcane"}).encode(),
                      APL_F: json.dumps({"type": "TypeAPL", "marker": "fire"}).encode()}

    def current(self):
        return self.tag

    def file(self, path, tag=None):
        self.asked.append(path)
        if path not in self.files:
            raise wowsims.FetchError("WoWSims %s has no %s" % (tag, path))
        return self.files[path]

    def siml_parse(self, path, tag=None):
        self.converted.append(path)
        check(path == APL_FR and tag == self.tag, (path, tag))
        return {"type": "TypeAPL", "marker": "frost"}


class FakeQueue:
    """sim_queue.SimQueue: each run answered at once by the model (paired noise: the seed's and the iterations'),
    or held (hold(call) -> True: its future never finishes), or failing (an item in fail), or answered after delay
    seconds (a queue waiting for the player)."""

    def __init__(self):
        self.calls, self.held, self.fail, self.hold, self.delay = [], [], set(), None, 0

    def submit(self, req, prio, iterations=None, seed=None, tag=None, background=True):
        p = req["raid"]["parties"][0]["players"][0]
        call = {"prio": prio, "iters": iterations, "seed": seed, "req_seed": req["simOptions"]["randomSeed"],
                "req_iters": req["simOptions"]["iterations"], "tag": tag, "bg": background,
                "talents": p["talentsString"], "rot": p["rotation"]["marker"], "player": p,
                "items": [it.get("id") for it in p["equipment"]["items"]]}
        self.calls.append(call)
        f = concurrent.futures.Future()
        if self.hold is not None and self.hold(call):
            self.held.append(f)
            return f
        bad = self.fail & set(call["items"])
        if bad:
            f.set_exception(SimError("WoWSims v9.9.9 couldn't sim: no item %d" % min(bad)))
            return f
        noise = ((seed * 7919 + iterations) % 13) * 0.1
        res = {"dps": model(p) + noise, "stdev": 30.0, "iters": iterations}
        if self.delay:
            threading.Timer(self.delay, f.set_result, (res,)).start()
        else:
            f.set_result(res)
        return f


def char(worn, talents=TA, **kw):
    return dict({"name": "Nine", "class": "MAGE", "race": "Human", "faction": "Alliance", "level": 60,
                 "talents": sim_weights.talent_list(talents), "worn": worn, "bags": [], "skills": None}, **kw)


def worn(change=None):
    """The worn gear, changed ({slot: id, or a dict with "enchant" or "suffix", or None for nothing there})."""
    base = {1: {"id": H0, "enchant": FOCUS}, 15: C0, 9: W0, 11: R10, 12: R4, 13: T3, 14: T6, 16: D0, 17: O0}
    base.update(change or {})
    out = []
    for s, e in sorted(base.items()):
        if e:
            out.append(dict({"key": str(s)}, **(e if isinstance(e, dict) else {"id": e})))
    return out


def by_id(results):
    return {r["variant"].get("id") or r["variant"].get("talents"): r for r in results}


def near(a, b, tol=0.02):
    return abs(a - b) <= tol


check(simbuild.talents_string(sim_weights.talent_list(TA)) == TA, "the talent list round trip")
GEAR = 18 + 2 + 30 + 10 + 4 + 3 + 6 + 10 + 5                       # what the worn gear adds (the hood's enchant: 8)

# ---- one seed, the asked priority, the base kept per gear --------------------------------------------------------------
sim_compare.clear()
ws, q = FakeWS(), FakeQueue()
CH = char(worn())
res = sim_compare.compare(CH, [{"kind": "item", "id": HB, "slot": None, "sfx": None, "ench": None}], q, ws, db=DB)
check(len(res) == 1 and res[0]["label"] == "Hood of Power" and near(res[0]["d"], 5.0), res)
r = res[0]
check(r["over"] == "Worn Hood" and r["over_id"] == H0 and r["slot"] == 1 and r["slot_name"] == "Head", r)
check(r["ench"] == FOCUS, "the worn enchant moves over: %s" % r)
check(r["iters"] == FULL and near(r["base"], POWER[TA] + GEAR, 1.5) and r["tag"] == "v9.9.9", r)
check(set(r) >= {"label", "d", "s", "iters", "variant", "base", "dps", "pct"} and r["variant"]["id"] == HB, r)
check(near(r["s"], (2 * 30.0 ** 2 / FULL) ** 0.5), "s: the unpaired bound at 10,000 iterations: %s" % r["s"])
check(all(c["seed"] == SEED and c["req_seed"] == SEED for c in q.calls), "one seed for every run")
check(all(c["iters"] == c["req_iters"] for c in q.calls), "iterations in the request too")
check(all(c["prio"] == PRIO_ASKED and c["bg"] is False and c["tag"] == "v9.9.9" for c in q.calls), q.calls[0])
check([c["iters"] for c in q.calls] == [QUICK, QUICK, FULL, FULL], [c["iters"] for c in q.calls])
head = q.calls[1]["player"]["equipment"]["items"][0]
check(head == {"id": HB, "enchant": FOCUS}, head)
n = len(q.calls)
again = sim_compare.compare(CH, [{"kind": "item", "id": HB, "slot": None, "sfx": None, "ench": None}], q, ws, db=DB)
check(len(q.calls) == n and again[0]["d"] == r["d"] and again[0]["iters"] == FULL, "asked again: no runs")
sim_compare.compare(char(worn({1: H0})), [{"kind": "item", "id": HB}], q, ws, db=DB)
new = q.calls[n:]
check([c["iters"] for c in new] == [QUICK, QUICK, FULL, FULL], "other gear (no enchant on the hood): its own base")
check(new[0]["player"]["equipment"]["items"][0] == {"id": H0}, new[0]["player"]["equipment"]["items"][0])

# ---- each build with its own rotation ------------------------------------------------------------------------------
sim_compare.clear()
ws, q = FakeWS(), FakeQueue()
builds = simbuild.presets("MAGE", ws, "v9.9.9")
check(builds == {"Arcane": (TA, APL_A), "Arcane Fire": (TF, APL_F), "Frost": (TFR, APL_FR)}, builds)
vs = [{"kind": "talents", "talents": TF, "apl": APL_F, "name": "Arcane Fire"},
      {"kind": "talents", "talents": TF, "apl": None, "name": None},
      {"kind": "talents", "talents": TFR, "apl": None, "name": None},
      {"kind": "talents", "talents": TC, "apl": None, "name": None},
      {"kind": "talents", "talents": TA, "apl": None, "name": None}]
res = sim_compare.compare(CH, vs, q, ws, db=DB, budget_s=30)
check(len(res) == 5, res)
fire, fire2, frost, custom, mine = res
base = POWER[TA] + GEAR
check(near(fire["d"], POWER[TF] - POWER[TA], 0.5) and near(fire["pct"], 100 * (POWER[TF] - POWER[TA]) / base, 0.2),
      "Arcane Fire on its own rotation: %s" % fire)
check(fire["label"] == "Arcane Fire" and fire["own_apl"] is True and fire["apl"] == APL_F, fire)
check((POWER[TF] + GEAR) * WRONG < base, "(on the Arcane rotation it would lose)")
check(fire2["label"] == "Arcane Fire" and fire2["apl"] == APL_F and fire2["own_apl"] and fire2["d"] == fire["d"],
      "its rotation found by its talents: %s" % fire2)
check(frost["label"] == "Frost" and frost["apl"] == APL_FR and near(frost["d"], POWER[TFR] - POWER[TA], 0.5), frost)
check(ws.converted == [APL_FR], "the .apl.siml converted once: %s" % ws.converted)
check(custom["label"] == sim_compare.CUSTOM and custom["own_apl"] is False and custom["apl"] == APL_A, custom)
check(near(custom["d"], POWER[TC] - POWER[TA], 0.5), custom)
check(mine["d"] == 0 and mine["iters"] == 0 and mine["label"] == "Arcane", "the character's own build: %s" % mine)
runs = {(c["talents"], c["rot"]) for c in q.calls}
check(runs == {(TA, "arcane"), (TF, "fire"), (TFR, "frost"), (TC, "arcane")}, "each with its own rotation: %s" % runs)
check(all(c["iters"] == QUICK for c in q.calls if c["talents"] in (TC,)) and
      [c["talents"] for c in q.calls if c["iters"] == FULL] == [TA, TF], "only the leader near enough for 10,000")
# the character on Arcane Fire's talents: its own base on Arcane Fire's rotation, not the tree's (the same request as
# the Arcane Fire variant above: kept, so the runs start afresh)
sim_compare.clear()
ws2, q2 = FakeWS(), FakeQueue()
res = sim_compare.compare(char(worn(), talents=TF), [{"kind": "talents", "talents": TA}], q2, ws2, db=DB)
check(q2.calls[0]["talents"] == TF and q2.calls[0]["rot"] == "fire", "the base on its build's rotation")
check(near(res[0]["d"], POWER[TA] - POWER[TF], 0.5), res)
vs_all = sim_compare.talent_variants(CH, ws, "v9.9.9")
check([v["name"] for v in vs_all] == ["Arcane Fire", "Frost"] and all(v["kind"] == "talents" for v in vs_all), vs_all)
# no presets to be had: the tree's rotations
sim_compare.clear()
ws3, q3 = FakeWS(), FakeQueue()
del ws3.files[simbuild.CLASSES["MAGE"]["presets"]]
res = sim_compare.compare(CH, [{"kind": "talents", "talents": TF}], q3, ws3, db=DB)
check(res[0]["own_apl"] is False and res[0]["apl"] == APL_A and res[0]["label"] == sim_compare.CUSTOM, res)

# ---- staging and the budget ----------------------------------------------------------------------------------------
sim_compare.clear()
ws, q = FakeWS(), FakeQueue()
STAGED = [{"kind": "item", "id": CK}, {"kind": "item", "id": HB}, {"kind": "item", "id": WN}]
res = by_id(sim_compare.compare(CH, STAGED, q, ws, db=DB))
cape, hood, wrist = res[CK], res[HB], res[WN]
check(cape["sfx"] == 101 and cape["label"] == "Cape of Sorcery" and near(cape["d"], 8.0), cape)
check(near(hood["d"], 5.0) and near(wrist["d"], -30.0), (hood, wrist))
check(cape["iters"] == FULL and hood["iters"] == FULL and wrist["iters"] == QUICK, "10,000 near the leader")
check(near(wrist["s"], (2 * 30.0 ** 2 / QUICK) ** 0.5), wrist["s"])
full = [c["items"] for c in q.calls if c["iters"] == FULL]
check(len(full) == 3 and full[0] == q.calls[0]["items"] and full[1][3] == CK and full[2][0] == HB,
      "the base, then the leader, then the next: %s" % full)
capes = sorted(c["player"]["equipment"]["items"][3].get("randomSuffix") for c in q.calls
               if c["items"][3] == CK and c["iters"] == QUICK)
check(capes == [101, 102, 103], "the 3 best suffixes by EP simmed, Spirit not: %s" % capes)
# the budget: 10,000-iteration runs that never finish
sim_compare.clear()
ws, q = FakeWS(), FakeQueue()
q.hold = lambda c: c["iters"] == FULL
t0 = time.monotonic()
res = by_id(sim_compare.compare(CH, STAGED, q, ws, db=DB, budget_s=0.4))
took = time.monotonic() - t0
check(0.35 <= took < 3, "stopped at the budget: %.2f s" % took)
check(all(r["iters"] == QUICK for r in res.values()) and near(res[CK]["d"], 8.0) and len(res) == 3, res)
check(q.held and all(f.cancelled() for f in q.held), "the runs that didn't finish leave the queue")
# a variant whose first run doesn't finish: left out; the base's: SimError
sim_compare.clear()
q = FakeQueue()
q.hold = lambda c: HB in c["items"]
res = sim_compare.compare(CH, STAGED, q, FakeWS(), db=DB, budget_s=0.3)
check(sorted(r["variant"]["id"] for r in res) == [CK, WN], res)
sim_compare.clear()
q = FakeQueue()
q.hold = lambda c: c is q.calls[0]                                 # (the base's first run)
try:
    sim_compare.compare(CH, STAGED, q, FakeWS(), db=DB, budget_s=0.3)
    check(False, "a base that didn't finish")
except SimError as e:
    check("didn't finish" in str(e) and isinstance(e, sim_compare.Late), e)
check(all(f.cancelled() for f in q.held), "dropped")
# in the background (the talent check) a variant left out for time is no answer: Late, so it's tried again later
sim_compare.clear()
q = FakeQueue()
q.hold = lambda c: HB in c["items"]
try:
    sim_compare.compare(CH, STAGED, q, FakeWS(), db=DB, budget_s=0.3, prio=PRIO_BIS)
    check(False, "a background compare with a variant left out for time")
except sim_compare.Late as e:
    check("1 of 3 left out" in str(e), e)
check(all(f.cancelled() for f in q.held), "dropped too")
# no deadline: a queue that waits for the player (each run 0.4 s late) is waited for, both stages
sim_compare.clear()
q = FakeQueue()
q.delay = 0.4
t0 = time.monotonic()
res = by_id(sim_compare.compare(CH, STAGED, q, FakeWS(), db=DB, budget_s=None, prio=PRIO_BIS))
took = time.monotonic() - t0
check(took >= 0.75 and len(res) == 3 and res[CK]["iters"] == FULL and res[HB]["iters"] == FULL and
      near(res[CK]["d"], 8.0), ("waited for every run: %.2f s" % took, res))

# ---- rings and trinkets: against the weaker worn one, unless a clash or the slot says ---------------------------------
sim_compare.clear()
ws, q = FakeWS(), FakeQueue()
res = sim_compare.compare(CH, [{"kind": "item", "id": RN}, {"kind": "item", "id": RN, "slot": "Ring 1"},
                               {"kind": "item", "id": RN, "slot": 11}, {"kind": "item", "id": T6}], q, ws, db=DB)
weak, asked1, asked11, second_t = res
check(weak["slot"] == 12 and weak["over"] == "Band of Four" and near(weak["d"], 3.0) and weak["slot_name"] == "Ring 2",
      weak)
check(asked1["slot"] == 11 and near(asked1["d"], -3.0) and asked11["slot"] == 11, (asked1, asked11))
check(not second_t.get("worn") and second_t["slot"] == 13 and second_t["over"] == "Charm of Three" and
      near(second_t["d"], 3.0), "a second Charm of Six (it can be worn twice): over the other one, %s" % second_t)
# a second copy of a ring worn in one slot: in the other one (Band of Ten in 11: a second over Band of Four, +6),
# unless the slot asked holds it, or both do, or it can't be worn twice (unique, a limitCategory)
res = sim_compare.compare(CH, [{"kind": "item", "id": R10}, {"kind": "item", "id": R10, "slot": "Ring 2"},
                               {"kind": "item", "id": R10, "slot": "Ring 1"}], q, ws, db=DB)
check(not res[0].get("worn") and res[0]["slot"] == 12 and res[0]["over"] == "Band of Four" and near(res[0]["d"], 6.0)
      and res[1]["slot"] == 12 and near(res[1]["d"], 6.0), ("a second Band of Ten", res[:2]))
check(res[2].get("worn") is True and res[2]["d"] == 0 and res[2]["s"] == 0 and res[2]["iters"] == 0 and
      res[2]["slot"] == 11, ("the slot asked holds it", res[2]))
n = len(q.calls)
for ch, iid, slot in ((char(worn({12: R10})), R10, 11), (char(worn({13: TU})), TU, 13),
                      (char(worn({11: L12})), L12, 11)):
    got = sim_compare.compare(ch, [{"kind": "item", "id": iid}], q, ws, db=DB)
    check(len(got) == 1 and got[0].get("worn") is True and got[0]["slot"] == slot and got[0]["d"] == 0,
          ("worn: both slots, unique, a limitCategory", iid, got))
check(len(q.calls) == n, "worn already: not even a base run")
check(not sim_compare.worn_already(CH, {"id": R10}, DB) and not sim_compare.worn_already(CH, {"id": R10, "slot":
                                                                                                "Ring 2"}, DB)
      and sim_compare.worn_already(CH, {"id": R10, "slot": "Ring 1"}, DB) and
      sim_compare.worn_already(char(worn({12: R10})), {"id": R10}, DB) and
      sim_compare.worn_already(char(worn({13: TU})), {"id": TU}, DB) and
      sim_compare.worn_already(CH, {"id": H0}, DB) and sim_compare.worn_already(CH, {"id": H0}, None) and
      not sim_compare.worn_already(CH, {"id": HB}, DB) and not sim_compare.worn_already(CH, {"id": T6, "sfx": 9}, DB),
      "worn_already as compare has it")
check(sim_compare.worn_already(CH, {"id": R10}, None), "unknown to the item data: taken as unique")
res = sim_compare.compare(char(worn({11: L12})), [{"kind": "item", "id": L9}], q, ws, db=DB)
check(res[0]["slot"] == 11 and res[0]["over"] == "Signet of Twelve" and near(res[0]["d"], -3.0),
      "one limitCategory: it can only replace the other: %s" % res[0])
res = sim_compare.compare(char(worn({12: None})), [{"kind": "item", "id": RN}], q, ws, db=DB)
check(res[0]["slot"] == 12 and res[0]["over"] is None and near(res[0]["d"], 7.0), "an empty slot first: %s" % res[0])
res = sim_compare.compare(CH, [{"kind": "item", "id": T5}, {"kind": "item", "id": T5, "slot": "Trinket 2"}], q, ws,
                          db=DB)
check(res[0]["slot"] == 13 and near(res[0]["d"], 2.0) and res[0]["over"] == "Charm of Three", res[0])
check(res[1]["slot"] == 14 and near(res[1]["d"], -1.0) and res[1]["over"] == "Charm of Six", res[1])

# ---- weapons ---------------------------------------------------------------------------------------------------------
res = sim_compare.compare(CH, [{"kind": "item", "id": ST}], q, ws, db=DB)[0]
check(near(res["d"], 15.0) and res["over"] == "Worn Dagger" and res.get("also_over") == "Worn Orb", res)
staff = char(worn({16: ST, 17: None}), bags=[{"id": O2}, {"id": O1}, {"id": RN}])
res = sim_compare.compare(staff, [{"kind": "item", "id": D1}], q, ws, db=DB)[0]
check(res.get("with") == "Orb of Eight" and res.get("with_id") == O1 and near(res["d"], 12 + 8 - 30.0), res)
res = sim_compare.compare(char(worn({16: ST, 17: None})), [{"kind": "item", "id": D1}], q, ws, db=DB)[0]
check(res.get("alone") is True and "with" not in res and near(res["d"], 12 - 30.0), res)
try:
    sim_compare.compare(staff, [{"kind": "item", "id": O1}], q, ws, db=DB)
    check(False, "an off hand with a two-hander and no one-hander to go with it")
except SimError as e:
    check("one-hand weapon" in str(e), e)

# ---- what can't be simmed ----------------------------------------------------------------------------------------------
q.fail = {BAD}
res = sim_compare.compare(CH, [{"kind": "item", "id": 99999}, {"kind": "item", "id": HP}, {"kind": "item", "id": BAD},
                               {"kind": "item", "id": HB}, {"kind": "frob"}, {"kind": "talents", "talents": "x"}],
                          q, ws, db=DB)
check([r["variant"]["id"] for r in res] == [HB], res)
for vs, why in (([{"kind": "item", "id": 99999}], "doesn't know"), ([{"kind": "item", "id": HP}], "can't wear"),
                ([{"kind": "item", "id": BAD}], "no item %d" % BAD), ([{"kind": "item", "id": HB, "slot": "Neck"}],
                                                                       "doesn't go in")):
    try:
        sim_compare.compare(CH, vs, q, ws, db=DB)
        check(False, "nothing simmed: SimError (%s)" % why)
    except SimError as e:
        check(why in str(e), e)
res = sim_compare.compare(CH, [{"kind": "item", "id": HB, "slot": "Head"}], q, ws, db=None)
check(res[0]["label"] == "item %d" % HB and res[0]["slot"] == 1, "no item data: the slot asked, the id as its name")
for bad_char, bad_ws, why in ((CH, FakeWS(tag=None), "isn't installed"), (dict(CH, level=58), FakeWS(), "level 60"),
                              (dict(CH, **{"class": "PALADIN"}), FakeWS(), "Paladin"),
                              (dict(CH, worn=[]), FakeWS(), "no gear")):
    try:
        sim_compare.compare(bad_char, [{"kind": "item", "id": HB}], q, bad_ws, db=DB)
        check(False, why)
    except SimError as e:
        check(why in str(e), e)
check(sim_compare.compare(CH, [], q, ws, db=DB) == [], "nothing asked")
sim_compare.clear()
n = len(q.calls)
res = sim_compare.compare(CH, [{"kind": "item", "id": HB}], q, ws, db=DB, prio=PRIO_BIS)
check(len(res) == 1 and len(q.calls) > n and all(c["bg"] is True and c["prio"] == PRIO_BIS for c in q.calls[n:]),
      "another priority: in the background")


# ---- the real runner, when there is one --------------------------------------------------------------------------------
def local_release():
    home = os.environ.get("GLIDE_WOWSIMS_HOME") or os.path.join(tempfile.gettempdir(), "glide-wowsims-home")
    base = os.path.join(home, "data", "wowsims")
    try:
        tags = sorted((t for t in os.listdir(base) if wowsims.TAG.match(t)), reverse=True)
    except OSError:
        return None
    for tag in tags:
        need = [wowsims.EXE] + [os.path.join("files", *p.split("/")) for p in
                                (APL_A, "assets/database/db.json")]
        if all(os.path.isfile(os.path.join(base, tag, p)) for p in need):
            return home, tag
    return None


found = local_release()
if not found:
    print("OK (no local WoWSims runner with its Arcane rotation and item data: its part skipped)")
    raise SystemExit(0)
HOME, TAG = found
KEPT = os.path.join(HOME, "data", "wowsims", TAG, "files")


class LocalOnly(wowsims.WoWSims):
    """The local runner, with only the files kept beside it (the test never downloads)."""

    def file(self, path, tag=None):
        p = os.path.join(KEPT, *path.split("/"))
        if not os.path.isfile(p):
            raise wowsims.FetchError("not kept: %s" % path)
        with open(p, "rb") as fh:
            return fh.read()


real_ws = LocalOnly(HOME)
real_ws.state["current"] = TAG                                       # (in memory only: never saved)
with open(os.path.join(KEPT, "assets", "database", "db.json"), "rb") as fh:
    real_db = item_db.ItemDB.load(fh.read(), None, TAG)
real_q = sim_queue.SimQueue(real_ws, lambda: False, cores=8)
smoke = [{"key": str(s), "id": i} for s, i in sim_weights.SMOKE_GEAR]
real_char = char(smoke, talents=sim_weights.SMOKE_TALENTS)
sim_compare.clear()
t0 = time.monotonic()
res = sim_compare.compare(real_char, [{"kind": "item", "id": 19147}], real_q, real_ws, db=real_db, budget_s=60)
check(len(res) == 1, res)
r = res[0]
check(r["slot"] in (11, 12) and r["over_id"] in (272406, 12543) and r["iters"] == FULL, r)
check(0 < r["d"] < 15 and 0 < r["s"] < 2 and r["base"] > 300, r)
print("OK (real runner: %s over %s, %+.2f DPS, s %.2f, %.1f s)" % (r["label"], r["over"], r["d"], r["s"],
                                                                  time.monotonic() - t0))
