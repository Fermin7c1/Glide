#!/usr/bin/env python3
"""The pursuit planner (companion/pursuit.py, Phase 4): the next move toward the next best-in-slot piece, weighed by
effort, over a level-60 mage's BiS ranks (a CharBis as bis.py keeps it), research's facts, the knowledge pack, the
Recorder and a stand-in for effort.py that follows its interface (the Beta update over AtlasLoot's classic prior, the
geometric quantiles, the 5-entries-an-hour floor). It pins:
- a run that rolls several upgrades outranks one item worth more on its own (Scholomance's three over Stratholme's
  one);
- dominance: the Magister's Crown is never chased while the vendor's Sorcerer's Crown is reachable, and once the
  crown is skipped it is (its gain counted from what is worn again); a trinket (worn two at a time) keeps its gain
  over one quicker trinket and loses some to a second;
- a chance nobody knows (the labelled 1 in 10 guess) never ranks above a known one that gains as much, and its drop
  chance is asked on the web ("r:" job, why "pursued"), once;
- the vendor move without a price ("I don't know its price yet", a "v:" job), the reputation without a standing or a
  rate (an "f:" job) and with them, the craft ("ask a Tailoring crafter") and the auction house once a price was seen,
  the quest chain from the knowledge base (where it starts, else the web's "q:" answer, unconfirmed), the raid calendar
  (nothing before December 9), a PvP reward (not estimated, said so), a web source past its ttl (no source);
- following through: a boss kill is a try (one per 5 minutes), one without the item a "roll" event with the run
  count and its range, a looted item lands ("got": its try got it, it is owned, the next move follows in the event),
  zoning into the move's instance says what drops there, a run's minutes are kept for the next estimate, the
  Recorder's kills (after a /reload) are tries with the loot seen; between polls (watch) a corpse targeted a few
  seconds is a try, kept at once, and an item landing works the plan out at once; money only while frames come;
- the player's word: pursue() sets the goal (a name works: "the magister's crown"), skip() keeps an item out, both
  across a restart; told / mark_told across a restart;
- live_section's shape (chars[key].next {t, m, x, y, alt}, goal), off with pursuit.enabled;
- an effort.try_hours of another signature: the planner's own rule, the same order;
- regressions: a guessed chance never shrinks a known item's gain (so the cap holds); a quest ready to turn in leads
  (0 hours is the quickest, not none) and goes to whom it is turned in; the Recorder's kills waiting at the first poll
  become tries; only kills whose loot was seen (or that brought the item) inform the drop chance; a raid boss killed
  uses the lockout until the weekly reset; a wipe or a hearth out and back is one run; pursuit.enabled off keeps and
  writes nothing; on_goal_cleared when the goal lands or is skipped; a reputation rate from one session's grind.
With effort.py present (the integration), the same character is planned with it too: the vendor's crown still leads.

Run: python3 tests/test_pursuit.py   (prints OK)
"""
import calendar
import importlib
import math
import os
import shutil
import statistics
import sys
import tempfile
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
for _k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
           "TAVILY_API_KEY", "BRAVE_API_KEY"):
    os.environ[_k] = ""
import item_db  # noqa: E402
import offline_coach  # noqa: E402
import pursuit  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


NOW = float(calendar.timegm((2026, 11, 25, 12, 0, 0)))

# ---- items -------------------------------------------------------------------------------------------------------
SC, MC, STYLUS, LEGS, MANTLE, ROBE = 226935, 16686, 13938, 18682, 16689, 13346
NECK, CLOAK, GLOVES, BRACER, TRINKET, PVP_FEET = 13968, 21301, 14112, 18375, 18820, 23290
TRINKET2, TRINKET3 = 11832, 11810
NAMES = {SC: "Sorcerer's Crown", MC: "Magister's Crown", STYLUS: "Bonecreeper Stylus", LEGS: "Ghostloom Leggings",
         MANTLE: "Magister's Mantle", ROBE: "Robes of the Exalted", NECK: "Eye of the Beast",
         CLOAK: "Deep Woodlands Cloak", GLOVES: "Felcloth Gloves", BRACER: "Bracers of the Eclipse",
         TRINKET: "Talisman of Ephemeral Power", PVP_FEET: "Knight-Lieutenant's Silk Walkers",
         TRINKET2: "Burst of Knowledge", TRINKET3: "Force of Will"}
DELIANA, GANDLING, ILLUCIA, RAS, BALNAZZAR, ALZZIN, MEILOSH, GARR = 230319, 1853, 10502, 10508, 10813, 11492, 11557, \
    12057
SCHOLO, STRAT, DM, MC_ZONE = 2057, 2017, 2557, 2717


def drop(npc, who, zone, place, diff=1):
    return item_db.src("drop", npc=npc, npc_name=who, zone=zone, zone_name=place, instance=place, difficulty=diff)


SOURCES = {
    SC: [item_db.src("vendor", npc=DELIANA, npc_name="Deliana", zone=1537, zone_name="Ironforge")],
    MC: [drop(GANDLING, "Darkmaster Gandling", SCHOLO, "Scholomance")],
    STYLUS: [drop(GANDLING, "Darkmaster Gandling", SCHOLO, "Scholomance")],
    LEGS: [drop(ILLUCIA, "Lady Illucia Barov", SCHOLO, "Scholomance")],
    MANTLE: [drop(RAS, "Ras Frostwhisper", SCHOLO, "Scholomance")],
    ROBE: [drop(BALNAZZAR, "Balnazzar", STRAT, "Stratholme")],
    NECK: [item_db.src("quest", quest=5102, quest_name="General Drakkisath's Demise")],
    CLOAK: [item_db.src("rep", npc=MEILOSH, npc_name="Meilosh", zone_name="Felwood", rep_faction="Timbermaw Hold",
                        rep_level="Honored")],
    GLOVES: [item_db.src("crafted", profession="Tailoring")],
    BRACER: [drop(ALZZIN, "Alzzin the Wildshaper", DM, "Dire Maul")],
    TRINKET: [drop(GARR, "Garr", MC_ZONE, "Molten Core", diff=2)],
    PVP_FEET: [item_db.src("pvp")],
    TRINKET2: [item_db.src("vendor", npc=DELIANA, npc_name="Deliana", zone=1537, zone_name="Ironforge")],
    TRINKET3: [item_db.src("vendor", npc=DELIANA, npc_name="Deliana", zone=1537, zone_name="Ironforge")],
}
# AtlasLoot classic's chances, as atlasloot_rates.read gives them (by creature id or boss name; percent or 0..1)
RATES = {GANDLING: {MC: 13.84, STYLUS: 12.93}, "Lady Illucia Barov": {LEGS: 0.1585}, RAS: {MANTLE: 0.1881},
         BALNAZZAR: {ROBE: 12.0}, GARR: {TRINKET: 10.0}}


def entry(iid, d, s=0.3, rank=1, how="sim", avail="now", req=None, **kw):
    e = {"id": iid, "sfx": 0, "rank": rank, "d": d, "s": s, "how": how, "avail": avail, "req": req}
    e.update(kw)
    return e


def charbis(groups=("Head", "Ranged", "Legs", "Shoulder", "Chest")):
    slots = {"Head": [entry(SC, 8.1), entry(MC, 6.0, rank=2), entry(18000, 0.0, s=0.0, rank=3, how="worn")],
             "Ranged": [entry(STYLUS, 3.0, s=0.4), entry(18001, 0.0, s=0.0, rank=2, how="worn")],
             "Legs": [entry(LEGS, 3.0)], "Shoulder": [entry(MANTLE, 2.5)], "Chest": [entry(ROBE, 5.0)],
             "Neck": [entry(NECK, 2.0)], "Back": [entry(CLOAK, 4.0)], "Hands": [entry(GLOVES, 2.5)],
             "Wrist": [entry(BRACER, 5.0)], "Trinket": [entry(TRINKET, 6.0)], "Feet": [entry(PVP_FEET, 3.5)]}
    return {"tag": "v1.0.4", "class": "MAGE", "faction": "Alliance", "skills": None,
            "slots": {g: xs for g, xs in slots.items() if g in groups}}


# ---- the world the planner reads ------------------------------------------------------------------------------------
class FakeDB:
    factions = {576: "Timbermaw Hold"}

    def item(self, iid):
        return {"id": iid, "classAllowlist": [8]} if iid in NAMES else None

    def name(self, iid):
        return NAMES.get(iid)

    def sources(self, iid, faction):
        return []


class FakeResearch:
    def __init__(self):
        self.db = FakeDB()
        self.prices = {}
        self.web = {}
        self.asked = []
        self.gone = set()                       # items whose fact holds what the web said, past its ttl

    def facts(self, iid):
        if iid not in SOURCES:
            return None
        return {"s": SOURCES[iid], "bond": None, "price": self.prices.get(iid), "fa": None, "id": iid}

    def _expired_web(self, f, now):
        return f.get("id") in self.gone

    def web_facts(self, job):
        return self.web.get(job)

    def enqueue_web(self, job, why, prio):
        self.asked.append((job, why, prio))

    def brief(self, iid):
        return {"id": iid, "name": NAMES.get(iid)}


class FakeData:
    prereqs = {5102: [(5089, "pre")]}
    no_givers = False                           # (a knowledge base that doesn't know where a quest starts)

    def spawns_of(self, cid, ui_map=None):
        return {DELIANA: [(1455, 24.5, 74.2)], MEILOSH: [(1448, 65.6, 2.8)]}.get(cid, [])

    def giver(self, qid, role):
        if qid == 5089 and role == "start" and not self.no_givers:
            return {"quest_id": 5089, "role": "start", "kind": "npc", "entity_id": 9560, "name": "Marshal Maxwell",
                    "ui_map": 1428, "x": 84.66, "y": 68.9}
        if qid == 5102 and role == "end" and not self.no_givers:
            return {"quest_id": 5102, "role": "end", "kind": "npc", "entity_id": 9562, "name": "Helendis Riverhorn",
                    "ui_map": 1428, "x": 85.8, "y": 68.9}
        return None

    def creature(self, cid):
        return None


class FakePack:
    ok = True
    data = FakeData()

    @staticmethod
    def map_name(ui_map):
        return {1428: "Burning Steppes"}.get(ui_map)

    @staticmethod
    def instance_entrance(x):
        return {SCHOLO: {"map": 1422, "x": 69.6, "y": 73.3, "src": "hand"},
                STRAT: {"map": 1423, "x": 31.0, "y": 15.4, "src": "hand"}}.get(x)


class FakeRecorder:
    def __init__(self):
        self.skill_set, self.facs, self.prices, self.kill_list = None, {}, {}, []

    def skills(self, char):
        return self.skill_set if char == "Nine" else None

    def factions(self, char):
        return self.facs if char == "Nine" else {}

    def auction(self, iid):
        return self.prices.get(iid)

    def kills(self, char):
        return self.kill_list if char == "Nine" else []


class FakeState:
    def __init__(self):
        self.s = {"name": "Nine", "class": "MAGE", "faction": "Alliance", "level": 60, "money": 1000000,
                  "map": 1453, "x": 0.5, "y": 0.5, "zone": "Stormwind City", "target": None}
        self.gear = {"W": [{"key": "1", "id": 18000}, {"key": "18", "id": 18001}], "I": [], "R": []}
        self.quests = {}

    def summary(self):
        return dict(self.s)


class Opt:
    def __init__(self, **kw):
        self.v = dict({"enabled": True, "pin": "free", "atlasloot_rates": True, "group": "pug", "form_minutes": 20},
                      **kw)

    def get(self, name):
        return self.v[name]


# ---- effort.py's interface, as Phase 4 has it -----------------------------------------------------------------------
TRY_CALLS = []


def quantiles(q):
    def n(p):
        return max(1, math.ceil(math.log(1 - p) / math.log(1 - q))) if q < 1 else 1
    return n(0.25), n(0.75), n(0.9)


def drop_chance(npc, item, obs=None, prior=None, web=None):
    if prior is not None:
        k, base, src = 20, prior, "classic"
    elif web is not None:
        k, base, src = 5, web, "web"
    else:
        return None, 0, "unknown"
    a, b = base * k, (1 - base) * k
    if obs:
        a, b = a + obs["got"], b + obs["kills"] - obs["got"]
    return a / (a + b), k + (obs or {}).get("kills", 0), src


def say_odds(q, src, unit="runs", asked=False):
    p25, p75, _ = quantiles(q)
    why = {"classic": "that's classic's chance", "web": "web, unconfirmed", "guess": "a guess, nobody lists it"}
    return "about 1 in %d, usually %d to %d %s; %s" % (round(1 / q), p25, p75, unit, why.get(src.split("+")[0], src))


def run_minutes(instance, history=None, prior_s=None):
    return (statistics.median(h["min"] for h in history), "obs") if history else (45.0, "prior")


def try_hours(kind, **kw):
    """effort.try_hours' rule for the kwargs its docstring names (pursuit.TRY_KW)."""
    TRY_CALLS.append((kind, tuple(sorted(kw))))
    travel = kw.get("travel_min") or 0
    if kind == "inst":
        form = 0 if kw["group"] == "solo" else kw["form_min"] * (1.5 if kw["instance"] == pursuit.UBRS else 1)
        return max(kw["run_min"] + form + travel, 12.0) / 60.0, kw["run_src"]
    if kind == "raid":
        if kw["now"] < pursuit.RAID_OPEN:
            return None, "calendar"
        if kw.get("locked_until") and kw["now"] < kw["locked_until"]:
            return None, "lockout"
        return (180 + 30 + travel) / 60.0, "prior"
    if kind == "quest":
        return (0.0 if kw["done"] else (kw["steps"] * 15 + travel) / 60.0), "prior"
    if kind in ("vendor", "ah"):
        if kw["price"] is None:
            return (travel / 60.0, "noprice") if kind == "vendor" else (None, "noprice")
        missing = max(0, kw["price"] - kw["money"])
        if missing and not kw["gph"]:
            return None, "unknown"
        return travel / 60.0 + (missing / kw["gph"] if missing else 0), "obs"
    if kind == "rep":
        if kw["value"] is None:
            return None, "nostanding"
        if not kw["rate"]:
            return None, "unknown"
        return ({"Honored": 9000}[kw["target"]] - kw["value"]) / kw["rate"], kw["rate_src"]
    if kind == "crafted":
        return None, "nomats" if kw["have"] else "crafter"
    return None, kind


def per_hour(samples):
    stretches = [x for x in samples if isinstance(x, dict)]
    if stretches:                                           # ({"gain", "s"}: a grind's rep and its seconds)
        span = sum(x["s"] for x in stretches)
        return (sum(x["gain"] for x in stretches) * 3600.0 / span, "obs") if span > 0 else (None, "unknown")
    if len(samples) < 2 or samples[-1][0] <= samples[0][0]:
        return None, "unknown"
    rate = (samples[-1][1] - samples[0][1]) / ((samples[-1][0] - samples[0][0]) / 3600.0)
    return (rate, "obs") if rate > 0 else (None, "unknown")


EFFORT = types.SimpleNamespace(
    drop_chance=drop_chance, win_chance=lambda item, group="pug", needers=None, size=None: 1.0 if (
        item or {}).get("classAllowlist") == [8] else 0.5,
    run_minutes=run_minutes, try_hours=try_hours, quantiles=quantiles, say_odds=say_odds,
    gold_per_hour=per_hour, rep_per_hour=per_hour)


def planner(home, world, effort=EFFORT, opt=None):
    st, res, rec, cb = world["state"], world["research"], world["recorder"], world["cb"]
    p = pursuit.Planner(home, lambda key: cb[0] if key == "Nine" else None, res, effort, lambda: FakePack(),
                        lambda: st, rec, now=lambda: world["clock"][0], opt=opt or Opt())
    p.rates_fn = lambda: RATES
    return p


def acts(plan):
    return [m["act"] for m in plan["moves"]]


def items_of(plan):
    return {i for m in plan["moves"] for i in m["items"]}


def move(plan, act):
    return next((m for m in plan["moves"] if m["act"] == act), None)


tmp = tempfile.mkdtemp(prefix="glide-pursuit-")
try:
    home = os.path.join(tmp, "engine")
    world = {"state": FakeState(), "research": FakeResearch(), "recorder": FakeRecorder(), "cb": [charbis()],
             "clock": [NOW]}
    res, rec, st = world["research"], world["recorder"], world["state"]
    res.prices[SC] = 50000                                  # (5g, the player has 100g)
    pl = planner(home, world)
    keep_s, pursuit.PLAN_KEEP_S = pursuit.PLAN_KEEP_S, 0    # (every plan() worked out now; the cache: at the end)

    # ---- the order: the vendor's crown, then the run that rolls three, then the run that rolls one ------------------
    plan = pl.plan("Nine")
    check(acts(plan) == ["vendor:%d" % DELIANA, "inst:%d" % SCHOLO, "inst:%d" % STRAT], acts(plan))
    v, sch, strat = plan["moves"]
    check(MC not in items_of(plan), "the Magister's Crown is chased while the Sorcerer's Crown is reachable")
    # (the leggings first: 15.85% of 3 DPS is a little more than 18.81% of 2.5)
    check(sch["items"] == [LEGS, MANTLE, STYLUS], sch["items"])
    check(sch["value"] > strat["value"] > 0, (sch["value"], strat["value"]))
    check(strat["items"] == [ROBE], strat)
    # 5 DPS rolled at 12% in 75 minutes (45 a run, 20 to form a group, 10 to get there)
    check(abs(strat["hours"] - 1.25) < 1e-6 and abs(strat["value"] - 0.12 * 5.0 / 1.25) < 1e-3, strat)
    check(plan["goal"] == SC and plan["alts"] == plan["moves"][1:3] and not plan["pursued"], plan)
    check(v["text"] == "Deliana, Ironforge: Sorcerer's Crown", v["text"])
    check(v["say"] == "Next: Deliana in Ironforge sells the Sorcerer's Crown; it costs 5g.", v["say"])
    check(v["where"] == {"map": 1455, "x": 0.245, "y": 0.742}, v["where"])
    check(sch["where"] == {"map": 1422, "x": 0.696, "y": 0.733}, sch["where"])
    check(sch["text"] == "Scholomance: Ghostloom Leggings and 2 more", sch["text"])
    check(sch["say"] == "Next: Scholomance. The Ghostloom Leggings are Lady Illucia Barov's, about 1 in 6, usually 2 "
                        "to 9 runs; that's classic's chance.", sch["say"])
    check("+3.0 DPS for Legs" in sch["why"] and "it also rolls the" in sch["why"] and "DPS an hour" in sch["why"],
          sch["why"])
    check(sch["p"] == 0.1585 and sch["range"] == [2, 9] and sch["tries"] == 0, sch)
    check({d["item"]: d["npc_name"] for d in sch["drops"]} == {STYLUS: "Darkmaster Gandling", LEGS:
                                                               "Lady Illucia Barov", MANTLE: "Ras Frostwhisper"},
          sch["drops"])
    check(pl.next_move("Nine") is None, "a next move before any poll")
    check({k: set(pursuit.TRY_KW[k]) for k in ("inst", "vendor")} == {k: set(kw) for k, kw in TRY_CALLS
                                                                      if k in ("inst", "vendor")}, TRY_CALLS)
    check(not any("2014" in m["say"] + m["why"] + m["text"] for m in plan["moves"]), "an em dash in player text")
    check(pl.plan("Other")["moves"] == [] and pl.plan(None)["moves"] == [], "a plan without BiS ranks")

    # ---- the vendor's price not known: still the next move, said so, asked on the web --------------------------------
    res.prices.pop(SC)
    plan = pl.plan("Nine")
    v = plan["moves"][0]
    check(v["act"] == "vendor:%d" % DELIANA, acts(plan))
    check(v["say"] == "Next: Deliana in Ironforge sells the Sorcerer's Crown; I don't know its price yet.", v["say"])
    check(v["jobs"] == ["v:%d:%d" % (DELIANA, SC)], v["jobs"])
    res.web["v:%d:%d" % (DELIANA, SC)] = {"s": [{"kind": "vendor", "cost": "7g 50s"}]}
    v = pl.plan("Nine")["moves"][0]
    check("costs 7g 50s (web, unconfirmed)" in v["say"] and not v["jobs"], v)
    res.web.clear()
    res.prices[SC] = 50000

    # ---- a chance nobody knows never above a known one that gains as much; asked on the web --------------------------
    world["cb"][0] = charbis(("Head", "Ranged", "Legs", "Shoulder", "Chest", "Wrist"))
    pl.data["chars"].setdefault("Nine", pl._blank())["runs"]["inst:%d" % DM] = [15.0, 15.0]
    plan = pl.plan("Nine")
    dm, strat = move(plan, "inst:%d" % DM), move(plan, "inst:%d" % STRAT)
    check(dm is not None and strat is not None, acts(plan))
    # (alone: 1 in 10 of 5 DPS in 45 minutes would beat Stratholme's 12% of 5 DPS in 75)
    check(acts(plan).index(dm["act"]) == acts(plan).index(strat["act"]) + 1 and dm["value"] < strat["value"],
          (acts(plan), dm["value"], strat["value"]))
    check("a guess" in dm["say"] and dm["jobs"] == ["r:%d:%d" % (ALZZIN, BRACER)], dm)
    pl.data["chars"]["Nine"]["runs"].clear()
    world["cb"][0] = charbis()

    # ---- the worker: a poll keeps the plan; the web asked once for what the top moves lack ---------------------------
    world["cb"][0] = charbis(("Head", "Ranged", "Legs", "Shoulder", "Chest", "Wrist"))
    res.prices.pop(SC)
    pl.tick()
    pl.tick()
    check(res.asked.count(("v:%d:%d" % (DELIANA, SC), "pursued", pursuit.PRIO_WEB)) == 1, res.asked)
    res.prices[SC] = 50000
    world["cb"][0] = charbis()
    pl.tick()
    with open(os.path.join(home, "data", "pursuit.json"), encoding="utf-8") as fh:
        check('"tag":"|vendor:%d|%d"' % (DELIANA, SC) in fh.read(), "pursuit.json without the plan's tag")
    plan = pl.plan("Nine")
    check(pl.next_move("Nine") == plan["moves"][0] and pl.next_move() == plan["moves"][0], "next_move isn't the plan's")
    check(pl.last_plan("Nine")["moves"] == plan["moves"][:3] and pl.last_plan("Nine")["tag"] == plan["tag"],
          pl.last_plan("Nine"))
    live = pl.live_section()
    check(set(live) == {"Nine"} and set(live["Nine"]) == {"next", "goal"} and live["Nine"]["goal"] == SC, live)
    nxt = live["Nine"]["next"]
    check(nxt == {"t": "Deliana, Ironforge: Sorcerer's Crown", "m": 1455, "x": 0.245, "y": 0.742,
                  "alt": ["Scholomance: Ghostloom Leggings and 2 more", "Stratholme: Robes of the Exalted"]}, nxt)
    check(pursuit.Planner(home, lambda k: None, None, EFFORT, None, lambda: None, None,
                          opt=Opt(enabled=False)).live_section() == {}, "live_section with pursuit.enabled off")

    # ---- following through: a run, a kill without the item, the item landing -----------------------------------------
    st.s["zone"] = "Western Plaguelands"
    pl.tick()
    world["clock"][0] += 600
    st.s["zone"] = "Scholomance"
    pl.tick()
    ev = pl.events()
    check([e["kind"] for e in ev] == ["zone"] and ev[0]["act"] == "inst:%d" % SCHOLO, ev)
    check(ev[0]["say"] == "The Ghostloom Leggings are Lady Illucia Barov's. The Magister's Mantle is Ras "
                          "Frostwhisper's.", ev[0]["say"])
    world["clock"][0] += 1500
    st.s["target"] = {"npc": str(GANDLING), "name": "Darkmaster Gandling", "dead": True}
    pl.tick()
    world["clock"][0] += 60
    pl.tick()                                               # (the same corpse targeted still: one kill)
    st.s["target"] = None
    world["clock"][0] += 30
    st.s["target"] = {"npc": str(GANDLING), "name": "Darkmaster Gandling", "dead": True}
    pl.tick()                                               # (targeted again within 5 minutes: the same kill)
    st.s["target"] = None
    tries = pl.data["chars"]["Nine"]["tries"]
    check(len(tries) == 1 and tries[0]["npc"] == GANDLING and tries[0]["act"] == "inst:%d" % SCHOLO
          and set(tries[0]["items"]) == {STYLUS, MC} and not tries[0]["got"], tries)
    world["clock"][0] += pursuit.LOOT_S
    pl.tick()
    ev = pl.events()
    check([e["kind"] for e in ev] == ["roll"], ev)
    # (a run, but no word on the drop: its corpse's loot wasn't seen, so classic's 12.93% stands)
    q1 = drop_chance(GANDLING, STYLUS, None, prior=0.1293)[0]
    check(pl._obs(pl.data["chars"]["Nine"], GANDLING, STYLUS) is None, "a kill whose loot nobody saw counted")
    p25, p75, p90 = quantiles(q1)
    check(ev[0]["item"] == STYLUS and ev[0]["tries"] == 1 and ev[0]["range"] == [p25, p75], ev[0])
    check(ev[0]["say"] == "No Bonecreeper Stylus. That's run 1; it usually takes %d to %d." % (p25, p75), ev[0]["say"])
    led = pl.ledger("Nine", "the stylus")
    check(led["item"] == STYLUS and led["tries"] == 1 and not led["got"] and led["range"] == [p25, p75]
          and led["p90"] == p90 and "classic's chance" in led["odds"], led)
    # the second kill: the stylus comes into the bags within LOOT_S
    world["clock"][0] += 900
    st.s["target"] = {"npc": str(GANDLING), "name": "Darkmaster Gandling", "dead": True}
    pl.tick()
    world["clock"][0] += 20
    st.gear["I"] = [{"key": "0.1", "id": STYLUS}]
    pl.tick()
    ev = pl.events()
    check([e["kind"] for e in ev] == ["got"] and ev[0]["item"] == STYLUS, ev)
    plan = pl.plan("Nine")
    check(STYLUS not in items_of(plan) and ev[0]["next"] == plan["moves"][0]["text"], (ev, acts(plan)))
    check(ev[0]["say"] == "Bonecreeper Stylus is yours. Next: Deliana, Ironforge: Sorcerer's Crown.", ev[0]["say"])
    check(set(move(plan, "inst:%d" % SCHOLO)["items"]) == {LEGS, MANTLE}, move(plan, "inst:%d" % SCHOLO))
    tries = pl.data["chars"]["Nine"]["tries"]
    check(len(tries) == 2 and tries[1]["got"] and tries[1]["seen"] == [STYLUS], tries)
    check(STYLUS in pl.data["chars"]["Nine"]["owned"], "the stylus isn't owned")
    world["clock"][0] += pursuit.LOOT_S + 1
    pl.tick()
    check(pl.events() == [], "a roll after the item landed")
    # leaving: once RUN_GRACE_S passed outside, the run's minutes (to the zone out) are kept and estimate the next run
    st.s["zone"] = "Western Plaguelands"
    pl.tick()
    check("inst:%d" % SCHOLO not in pl.data["chars"]["Nine"]["runs"], "a run ended before its grace passed")
    world["clock"][0] += pursuit.RUN_GRACE_S + 1
    pl.tick()
    runs = pl.data["chars"]["Nine"]["runs"]["inst:%d" % SCHOLO]
    check(len(runs) == 1 and abs(runs[0] - (NOW + 600 + 1500 + 60 + 30 + 120 + 900 + 20 + 121 - (NOW + 600)) / 60.0)
          < 0.2, runs)
    check(abs(move(pl.plan("Nine"), "inst:%d" % SCHOLO)["hours"] - (runs[0] + 20 + 10) / 60.0) < 0.01,
          move(pl.plan("Nine"), "inst:%d" % SCHOLO))
    # the Recorder's kills, read after a /reload: Ras killed (the mantle seen in the loot); the Gandling kill known
    rec.kill_list = [{"npc": RAS, "t": world["clock"][0] - 1200, "loot": [MANTLE, 12345]},
                     {"npc": GANDLING, "t": tries[1]["t"] + 10, "loot": [STYLUS]}]
    pl.tick()
    tries = pl.data["chars"]["Nine"]["tries"]
    check(len(tries) == 3 and [t["npc"] for t in tries] == [GANDLING, RAS, GANDLING], tries)
    check(tries[1]["seen"] == [MANTLE] and tries[1]["items"] == [MANTLE], tries[1])
    pl.tick()
    check(len(pl.data["chars"]["Nine"]["tries"]) == 3, "the Recorder's kills read twice")
    # (the mantle seen once in one kill: the Beta update moves classic's 18.81% up)
    check(pl.chance["Nine"][MANTLE][0] > 0.1881, pl.chance["Nine"])

    # ---- the player's word: skip and pursue, kept across a restart ---------------------------------------------------
    plan = pl.skip("Nine", SC)
    check(SC not in items_of(plan), acts(plan))
    sch = move(plan, "inst:%d" % SCHOLO)
    check(MC in sch["items"], sch)                          # (no quicker crown now: the Magister's is chased)
    plan = pl.pursue("Nine", "the magister's crown")
    check(plan["goal"] == MC and plan["pursued"] and plan["moves"][0]["act"] == "inst:%d" % SCHOLO
          and plan["moves"][0]["items"][0] == MC, plan["moves"][0])
    check(pl.live_section()["Nine"]["goal"] == MC, pl.live_section())
    pl.mark_told("next_move|%s" % plan["tag"], "Nine")
    pl2 = planner(home, world)
    check(pl2.told("next_move|%s" % plan["tag"], "Nine") and not pl2.told("next_move|x", "Nine"), "told lost")
    plan = pl2.plan("Nine")
    check(plan["goal"] == MC and plan["pursued"] and SC not in items_of(plan), "the goal or the skip lost")
    check(pl2.live_section()["Nine"]["goal"] == MC, "the goal not in Live.lua after a restart")
    plan = pl2.pursue("Nine", SC)                           # (pursuing a skipped item takes the skip back)
    check(plan["goal"] == SC and plan["moves"][0]["act"] == "vendor:%d" % DELIANA, acts(plan))
    plan = pl2.pursue("Nine", None)
    check(not plan["pursued"] and plan["goal"] == SC, plan)
    plan = pl2.skip("Nine", None)                           # ("skip that": the plan's goal)
    check(SC not in items_of(plan) and plan["goal"] != SC, plan)
    pl2.skip("Nine", SC, skip=False)

    # ---- quest, reputation, craft, auction house, raid ---------------------------------------------------------------
    world["cb"][0] = charbis(("Neck", "Back", "Hands", "Trinket"))
    home2 = os.path.join(tmp, "engine2")
    pl3 = planner(home2, world)
    plan = pl3.plan("Nine")
    q, rep, craft, raid = (move(plan, "quest:5102"), move(plan, "rep:576"), move(plan, "craft:%d" % GLOVES),
                           move(plan, "raid:%d" % MC_ZONE))
    check(None not in (q, rep, craft, raid), acts(plan))
    check(acts(plan)[0] == "quest:5102" and q["hours"] == round((2 * 15 + 10) / 60.0, 2), q)
    check(q["say"] == "Next: the quest General Drakkisath's Demise gives the Eye of the Beast, the end of a 2-quest "
                      "chain; it starts with Marshal Maxwell in Burning Steppes.", q["say"])
    check(q["where"] == {"map": 1428, "x": 0.8466, "y": 0.689}, q["where"])
    check(rep["value"] is None and "I don't know your Timbermaw Hold standing yet" in rep["say"], rep)
    check(raid["value"] is None and raid["say"] == "Raid gear: earliest December 9, then one roll a week.", raid)
    check(craft["value"] is None and craft["say"] == "Next: the Felcloth Gloves are made with Tailoring; ask a "
                                                     "Tailoring crafter or check the auction house.", craft["say"])
    rec.facs = {576: {"standing": 5, "value": 6000, "t": NOW - 7200}}
    rep = move(pl3.plan("Nine"), "rep:576")
    check(rep["value"] is None and rep["jobs"] == ["f:Timbermaw Hold"] and rep["say"] == (
        "Next: the Deep Woodlands Cloak needs Honored with Timbermaw Hold; I don't know Timbermaw Hold rep rates "
        "yet; I'll learn them from your turn-ins."), rep)
    pl3.tick()                                              # (a rep sample)
    rec.facs = {576: {"standing": 5, "value": 7500, "t": NOW - 3600}}
    pl3.tick()
    rep = move(pl3.plan("Nine"), "rep:576")
    check(rep["hours"] == 1.0 and rep["value"] == 4.0 and "about 60 minutes of Timbermaw Hold rep, as of your last "
          "reload" in rep["say"], rep)
    rec.facs = {576: {"standing": 6, "value": 9100, "t": NOW}}
    plan = pl3.plan("Nine")
    check(move(plan, "rep:576") is None and move(plan, "vendor:%d" % MEILOSH) is not None, acts(plan))
    check("you're Honored with Timbermaw Hold already" in move(plan, "vendor:%d" % MEILOSH)["say"],
          move(plan, "vendor:%d" % MEILOSH))
    rec.prices[GLOVES] = {"copper": 120000, "t": NOW - 3 * 86400}
    ah = move(pl3.plan("Nine"), "ah:%d" % GLOVES)
    check(ah is not None and ah["value"] and ah["say"] == "Next: the auction house for the Felcloth Gloves; the "
                                                          "auction house had it for 12g, 3 days ago.", ah)
    world["clock"][0] = pursuit.RAID_OPEN + 3600
    raid = move(pl3.plan("Nine"), "raid:%d" % MC_ZONE)
    check(raid["value"] and raid["say"].startswith("Next: Molten Core. The Talisman of Ephemeral Power is Garr's"),
          raid)
    # trinkets are worn two at a time: one quicker trinket leaves the raid one its whole gain, the second takes some
    world["cb"][0] = charbis(("Trinket",))
    world["cb"][0]["slots"]["Trinket"].append(entry(TRINKET2, 3.0))
    raid = move(pl3.plan("Nine"), "raid:%d" % MC_ZONE)
    check(abs(raid["value"] - 0.1 * 6.0 / (220 / 60.0)) < 1e-3 and "more than" not in raid["why"], raid)
    world["cb"][0]["slots"]["Trinket"].append(entry(TRINKET3, 2.5))
    raid = move(pl3.plan("Nine"), "raid:%d" % MC_ZONE)
    check(abs(raid["value"] - 0.1 * 3.5 / (220 / 60.0)) < 1e-3
          and "+6.0 DPS for Trinket, 3.5 more than the Force of Will, which is quicker to get" in raid["why"], raid)
    world["cb"][0] = charbis(("Neck", "Back", "Hands", "Trinket"))
    world["clock"][0] = NOW
    # where a chain starts, when the knowledge base doesn't know: asked on the web ("q:"), then said as the web's
    FakeData.no_givers = True
    q = move(pl3.plan("Nine"), "quest:5102")
    check(q["jobs"] == ["q:5089"] and q["where"] is None and q["say"].endswith("2-quest chain."), q)
    res.web["q:5089"] = {"s": [{"kind": "quest", "npc_name": "Marshal Maxwell", "zone_name": "Burning Steppes",
                                "quest": 5089, "src": "web"}]}
    q = move(pl3.plan("Nine"), "quest:5102")
    check(not q["jobs"] and q["say"].endswith("it starts with Marshal Maxwell in Burning Steppes (web, "
                                              "unconfirmed)."), q)
    FakeData.no_givers = False
    res.web.clear()
    # a PvP reward: a move that isn't estimated, after every one that is, and said so
    world["cb"][0] = charbis(("Neck", "Feet"))
    plan = pl3.plan("Nine")
    pvp = move(plan, "pvp:%d" % PVP_FEET)
    check(pvp is not None and pvp["value"] is None and acts(plan) == ["quest:5102", "pvp:%d" % PVP_FEET], acts(plan))
    check(pvp["text"] == "PvP: Knight-Lieutenant's Silk Walkers" and pvp["say"] == (
        "Next: the Knight-Lieutenant's Silk Walkers are a PvP reward; I can't estimate PvP rewards yet."), pvp)
    # what the web said, past its ttl, is no source (research doesn't say it either)
    world["cb"][0] = charbis(("Wrist",))
    saved = SOURCES[BRACER]
    SOURCES[BRACER] = [dict(saved[0], src="web", conf=0.3)]
    check(acts(pl3.plan("Nine")) == ["inst:%d" % DM], acts(pl3.plan("Nine")))
    res.gone.add(BRACER)
    check(pl3.plan("Nine")["moves"] == [], pl3.plan("Nine"))
    res.gone.clear()
    SOURCES[BRACER] = saved

    # ---- between polls: a corpse targeted a few seconds is still a try (watch); money only while frames come ------
    world["cb"][0] = charbis()
    rec.kill_list = []                                      # (another engine home: none of the Recorder's kills)
    pl6 = planner(os.path.join(tmp, "engine5"), world)
    st.s["target"] = None
    pl6.tick()                                              # (the plan: the bosses its items drop from)
    st.s["target"] = {"npc": str(GANDLING), "name": "Darkmaster Gandling", "dead": True}
    world["clock"][0] += 5
    pl6.watch()
    st.s["target"] = None
    world["clock"][0] += 5
    pl6.watch()
    tries = pl6.data["chars"]["Nine"]["tries"]
    check(len(tries) == 1 and tries[0]["npc"] == GANDLING, tries)
    with open(os.path.join(tmp, "engine5", "data", "pursuit.json"), encoding="utf-8") as fh:
        check('"npc":%d' % GANDLING in fh.read(), "a try seen between polls not kept at once")
    # an item landing between polls: the plan worked out at once, its next move in the "got" event
    pl6.events()
    st.gear["I"] = st.gear["I"] + [{"key": "0.2", "id": LEGS}]
    world["clock"][0] += 5
    pl6.watch()
    ev = pl6.events()
    check([e["kind"] for e in ev] == ["got"] and ev[0]["item"] == LEGS and ev[0]["next"], ev)
    check(LEGS not in items_of(pl6.plan("Nine")), "the leggings still planned after they landed")
    st.gear["I"] = [x for x in st.gear["I"] if x["id"] != LEGS]
    gold = pl6.data["chars"]["Nine"]["gold"]
    n, money = len(gold), st.s["money"]
    st.s.update(fresh=False, money=money + 500)
    world["clock"][0] += 30
    pl6.tick()
    check(len(gold) == n, "a money sample from a game that sends no frames")
    st.s["fresh"] = True
    pl6.tick()
    check(len(gold) == n + 1 and gold[-1][1] == money + 500, gold)
    st.s.pop("fresh")
    st.s["money"] = money
    world["clock"][0] = NOW

    # ---- an item that landed counts before the ranks are worked out again (regression: right after the Sorcerer's
    # Crown landed in the bags, the next move led with the Magister's Crown, its gain counted from the worn one) --------
    world["cb"][0] = charbis()
    pl7 = planner(os.path.join(tmp, "engine6"), world)
    st.gear["I"] = st.gear["I"] + [{"key": "0.3", "id": SC}]
    plan = pl7.plan("Nine")
    check(SC not in items_of(plan) and MC not in items_of(plan), [m["items"] for m in plan["moves"]])
    st.gear["I"] = [x for x in st.gear["I"] if x["id"] != SC]

    # ---- odds that count the rolls (or a random suffix's share) say so; the source names the drop chance -------------
    said = "about 1 in 8, usually 3 to 10 runs; that's classic's chance"
    check(pursuit.Planner._with_rolls(said, 0.5, 1.0) == "about 1 in 8 with the rolls, usually 3 to 10 runs; that's "
          "classic's chance", pursuit.Planner._with_rolls(said, 0.5, 1.0))
    check(pursuit.Planner._with_rolls(said, 0.5, 0.25).startswith("about 1 in 8 with the rolls and the right suffix, "),
          pursuit.Planner._with_rolls(said, 0.5, 0.25))
    check(pursuit.Planner._with_rolls(said, 1.0, 1.0) == said and pursuit.Planner._with_rolls(None, 0.5, 1) is None,
          "odds changed with nothing to count")

    # ---- another effort.try_hours: the planner's own rule, the same order ----------------------------------------------
    world["cb"][0] = charbis()
    other = types.SimpleNamespace(**dict(vars(EFFORT), try_hours=lambda kind, minutes: (minutes / 60.0, "x")))
    pl4 = planner(os.path.join(tmp, "engine3"), world, effort=other)
    check(acts(pl4.plan("Nine")) == ["vendor:%d" % DELIANA, "inst:%d" % SCHOLO, "inst:%d" % STRAT],
          acts(pl4.plan("Nine")))

    # ---- the rules ask every few seconds: the last plan while PLAN_KEEP_S young, the poll's fresh ---------------------
    pursuit.PLAN_KEEP_S = keep_s
    pl4 = planner(os.path.join(tmp, "engine3b"), world)
    before = pl4.plan("Nine")
    res.prices.pop(SC)
    check(pl4.plan("Nine") == before, "plan() worked out again within PLAN_KEEP_S")
    check(pl4.plan("Nine", fresh=True) != before, "plan(fresh=True) kept the old plan")
    world["clock"][0] += pursuit.PLAN_KEEP_S
    check("I don't know its price yet" in pl4.plan("Nine")["moves"][0]["say"], "the plan never worked out again")
    res.prices[SC] = 50000
    world["clock"][0] = NOW
    pursuit.PLAN_KEEP_S = 0

    bags_before, st.gear["I"] = st.gear["I"], []

    # ---- regression: a guessed chance never shrinks a known item's gain (one Head slot: the Bracers of the Eclipse,
    # +5 at the guessed 1 in 10 in Dire Maul; the Robes of the Exalted, +6 at a known 5% in Stratholme). Before, the
    # guess counted as the quicker item, cut the robe's gain to 1, and so slipped past the cap and led the plan ---------
    world["cb"][0] = {"tag": "v1.0.4", "class": "MAGE", "faction": "Alliance", "skills": None,
                      "slots": {"Head": [entry(BRACER, 5.0), entry(ROBE, 6.0, rank=2)]}}
    pl8 = planner(os.path.join(tmp, "engine7"), world)
    pl8.rates_fn = lambda: {BALNAZZAR: {ROBE: 5.0}}
    plan = pl8.plan("Nine")
    robe, dm = move(plan, "inst:%d" % STRAT), move(plan, "inst:%d" % DM)
    check(acts(plan) == ["inst:%d" % STRAT, "inst:%d" % DM] and dm["value"] < robe["value"],
          (acts(plan), [m["value"] for m in plan["moves"]]))
    check(abs(robe["value"] - 0.05 * 6.0 / 1.25) < 1e-3 and "more than" not in robe["why"], robe)
    check("a guess" in dm["say"], dm["say"])

    # ---- regression: a quest ready to turn in (0 hours) leads the plan, and goes to whom it is turned in to ----------
    world["cb"][0] = charbis(("Neck", "Chest"))
    pl9 = planner(os.path.join(tmp, "engine8"), world)
    st.quests = {5102: {"done": True}}
    plan = pl9.plan("Nine")
    q = plan["moves"][0]
    check(acts(plan) == ["quest:5102", "inst:%d" % STRAT] and q.get("done") is True and q["hours"] == 0.0
          and q["value"] > plan["moves"][1]["value"], (acts(plan), plan["moves"]))
    check(q["say"] == "Next: turn in the quest General Drakkisath's Demise for the Eye of the Beast; Helendis "
                      "Riverhorn in Burning Steppes takes it.", q["say"])
    check(q["where"] == {"map": 1428, "x": 0.858, "y": 0.689} and not q["jobs"], q)
    said = offline_coach.move_parts(q, NAMES.get, lambda i: SOURCES.get(i, []))[0]
    check(said == "turn in the quest General Drakkisath's Demise for the Eye of the Beast; Helendis Riverhorn in "
                  "Burning Steppes takes it", said)
    plan = pl9.pursue("Nine", NECK)                         # (a goal's ways, the quickest first: 0 hours counts)
    check(plan["moves"][0]["act"] == "quest:5102", acts(plan))
    st.quests = {}

    # ---- regression: the Recorder's kills waiting at the engine's start become tries at its first poll (before, the
    # first poll matched them against no bosses yet and moved past them for good) -----------------------------------
    world["cb"][0] = charbis()
    rec.kill_list = [{"npc": GANDLING, "t": NOW - 7200 + 600 * i, "loot": [STYLUS] if i == 2 else [14514]}
                     for i in range(5)]
    pl10 = planner(os.path.join(tmp, "engine9"), world)
    pl10.tick()
    c10 = pl10.data["chars"]["Nine"]
    check(len(c10["tries"]) == 5 and all(t["looked"] for t in c10["tries"]) and c10["rec_t"] == NOW - 7200 + 2400,
          c10["tries"])
    check(pl10.ledger("Nine", STYLUS)["tries"] == 5 and pl10._obs(c10, GANDLING, STYLUS) == {"kills": 5, "got": 1},
          pl10.ledger("Nine", STYLUS))
    pl10.tick()
    check(len(c10["tries"]) == 5, "the Recorder's kills read twice")
    # no BiS ranks yet: they wait (rec_t kept), and count once the ranks come
    world["cb"][0] = None
    pl11 = planner(os.path.join(tmp, "engine10"), world)
    pl11.tick()
    check(pl11.data["chars"]["Nine"]["rec_t"] == 0 and pl11.data["chars"]["Nine"]["tries"] == [],
          pl11.data["chars"]["Nine"])
    world["cb"][0] = charbis()
    pl11.tick()
    check(len(pl11.data["chars"]["Nine"]["tries"]) == 5, pl11.data["chars"]["Nine"]["tries"])
    # a kill the strip saw, its loot not seen: a run, but no word on the drop (a group's roll it lost, never opened);
    # the Recorder's window for that corpse, after a /reload, is
    c11 = pl11.data["chars"]["Nine"]
    world["clock"][0] += 60
    st.s["target"] = {"npc": str(GANDLING), "name": "Darkmaster Gandling", "dead": True}
    pl11.tick()
    st.s["target"] = None
    strip_try = c11["tries"][-1]
    check(not strip_try["looked"] and pl11._obs(c11, GANDLING, STYLUS)["kills"] == 5
          and pl11._tries(c11, STYLUS) == 6, c11["tries"])
    rec.kill_list = rec.kill_list + [{"npc": GANDLING, "t": strip_try["t"] + 5, "loot": [14514]}]
    world["clock"][0] += 60
    pl11.tick()
    check(strip_try["looked"] and pl11._obs(c11, GANDLING, STYLUS) == {"kills": 6, "got": 1}
          and pl11._tries(c11, STYLUS) == 6, c11["tries"])
    rec.kill_list = []
    world["clock"][0] = NOW

    # ---- regression: a raid boss killed uses the raid's lockout: the raid waits for the weekly reset ----------------
    world["cb"][0] = charbis(("Trinket",))
    pl12 = planner(os.path.join(tmp, "engine11"), world)
    world["clock"][0] = pursuit.RAID_OPEN + 86400           # (Thursday, December 10)
    check(move(pl12.plan("Nine"), "raid:%d" % MC_ZONE)["value"], "the raid before a kill")
    st.s["target"] = {"npc": str(GARR), "name": "Garr", "dead": True}
    pl12.tick()
    st.s["target"] = None
    reset = calendar.timegm((2026, 12, 15, 15, 0, 0))       # (the next Tuesday, 15:00 UTC)
    locked = pl12._locked_until(pl12.data["chars"]["Nine"], "raid:%d" % MC_ZONE)
    check(locked == reset, (locked, reset))
    raid = move(pl12.plan("Nine"), "raid:%d" % MC_ZONE)
    check(raid["value"] is None and raid["say"] == pursuit.RAID_LOCKED, raid)
    world["clock"][0] = reset + 60
    check(move(pl12.plan("Nine"), "raid:%d" % MC_ZONE)["value"], "the raid after the reset")
    check(pursuit.Planner._locked_until({"tries": [{"t": reset + 1, "act": "raid:1"}]}, "raid:1") == reset + 7 * 86400
          and pursuit.Planner._locked_until({"tries": []}, "raid:1") is None, "a kill right after the reset")
    world["clock"][0] = NOW

    # ---- regression: a wipe (out as a ghost, back in) and a hearth out to repair and back are one run, not three ----
    world["cb"][0] = charbis()
    pl14 = planner(os.path.join(tmp, "engine13"), world)
    st.s["zone"] = "Western Plaguelands"
    pl14.tick()
    for step, zone, dead in ((60, "Scholomance", 0), (1800, "Western Plaguelands", 2), (1200, "Western Plaguelands", 2),
                             (180, "Scholomance", 0), (600, "Ironforge", 0), (600, "Scholomance", 0),
                             (600, "Western Plaguelands", 0)):
        world["clock"][0] += step
        st.s.update(zone=zone, dead=dead)
        pl14.tick()
    check(pl14.data["chars"]["Nine"]["runs"] == {}, pl14.data["chars"]["Nine"]["runs"])
    world["clock"][0] += pursuit.RUN_GRACE_S + 1
    pl14.tick()
    check(pl14.data["chars"]["Nine"]["runs"] == {"inst:%d" % SCHOLO: [83.0]}, pl14.data["chars"]["Nine"]["runs"])
    check([e["kind"] for e in pl14.events()] == ["zone"], "zoning back in said what drops there again")
    st.s.pop("dead")
    world["clock"][0] = NOW

    # ---- regression: pursuit.enabled off: nothing planned, kept or written -------------------------------------------
    home_off = os.path.join(tmp, "engine14")
    off = planner(home_off, world, opt=Opt(enabled=False))
    check(off.plan("Nine")["moves"] == [] and off.plan("Nine", fresh=True)["goal"] is None, off.plan("Nine"))
    check(off.pursue("Nine", SC)["moves"] == [] and off.skip("Nine", SC)["moves"] == [], "pursue / skip while off")
    off.tick()
    check(not os.path.exists(os.path.join(home_off, "data", "pursuit.json")), "pursuit.json written while off")

    # ---- the player's goal reached or skipped: on_goal_cleared (the coach's memory note goes) -------------------------
    world["cb"][0] = charbis()
    cleared = []
    pl15 = planner(os.path.join(tmp, "engine15"), world)
    pl15.on_goal_cleared = cleared.append
    pl15.pursue("Nine", MC)
    pl15.skip("Nine", SC)
    check(cleared == [], "a skip of another item cleared the goal")
    pl15.skip("Nine", MC)
    check(cleared == ["Nine"], cleared)
    pl15.skip("Nine", SC, skip=False)
    pl15.pursue("Nine", LEGS)
    pl15.tick()
    st.gear["I"] = [{"key": "0.4", "id": LEGS}]
    world["clock"][0] += 5
    pl15.tick()
    check(cleared == ["Nine", "Nine"] and pl15.owned("Nine") == [LEGS], (cleared, pl15.owned("Nine")))
    world["clock"][0] = NOW
    st.gear["I"] = bags_before

    # ---- with effort.py itself (the integration): the same order, its words --------------------------------------------
    try:
        effort = importlib.import_module("effort")
    except ImportError:
        effort = None
    if effort is not None:
        pl5 = planner(os.path.join(tmp, "engine4"), world, effort=effort)
        plan = pl5.plan("Nine")
        check(acts(plan)[:1] == ["vendor:%d" % DELIANA] and MC not in items_of(plan)
              and acts(plan).index("inst:%d" % SCHOLO) < acts(plan).index("inst:%d" % STRAT), acts(plan))
        check(plan["moves"][0]["say"].startswith("Next: Deliana in Ironforge sells the Sorcerer's Crown; ")
              and "classic's chance" in move(plan, "inst:%d" % SCHOLO)["say"]
              and not any("2014" in m["say"] + m["why"] for m in plan["moves"]), plan["moves"][:2])
        # regression: 5g on hand, no gold rate learned yet, the price the web's: both said (not "at your gold rate")
        res.prices.pop(SC)
        res.web["v:%d:%d" % (DELIANA, SC)] = {"s": [{"kind": "vendor", "cost": "40g"}]}
        money, st.s["money"] = st.s["money"], 50000
        v = move(pl5.plan("Nine"), "vendor:%d" % DELIANA)
        check(v["say"] == "Next: Deliana in Ironforge sells the Sorcerer's Crown; about 3.5 hours of farming for the "
                          "gold, at a guessed 10g an hour; its price is from the web, unconfirmed.", v["say"])
        st.s["money"], res.prices[SC] = money, 50000
        res.web.clear()
        world["cb"][0] = charbis(("Neck", "Back", "Hands", "Trinket"))
        rec.facs = {}
        plan = pl5.plan("Nine")
        check(move(plan, "raid:%d" % MC_ZONE)["say"] == "Raid gear: earliest December 9, then one roll a week.",
              move(plan, "raid:%d" % MC_ZONE))
        check("standing yet" in move(plan, "rep:576")["say"] and move(plan, "rep:576")["value"] is None,
              move(plan, "rep:576"))
        # a quest ready to turn in: the way there (travel), ranked by it (2 DPS in 10 minutes)
        st.quests = {5102: {"done": True}}
        q = move(pl5.plan("Nine"), "quest:5102")
        check(q["hours"] == round(10 / 60.0, 2) and abs(q["value"] - 12.0) < 0.05 and q["done"], q)
        st.quests = {}
        # a grind between two reloads hours apart: effort learns no rate from the readings, but the Recorder's rise
        # within the session (its first turn-in to its last) is one
        pl13 = planner(os.path.join(tmp, "engine12"), world, effort=effort)
        world["cb"][0] = charbis(("Back",))
        rec.facs = {576: {"standing": 5, "value": 5000, "t": NOW - 86400}}
        pl13.tick()
        rec.facs = {576: {"standing": 5, "value": 7500, "t": NOW}}
        pl13.tick()
        check(move(pl13.plan("Nine"), "rep:576")["value"] is None, "a rate from readings a day apart")
        rec.facs = {576: {"standing": 5, "value": 7500, "t": NOW, "v0": 5200, "t0": NOW - 7200, "t1": NOW - 120}}
        pl13.tick()
        rep = move(pl13.plan("Nine"), "rep:576")
        rate = round(2300 * 3600.0 / (7200 - 120))
        check(rep["value"] and abs(rep["hours"] - round(1500.0 / rate, 2)) < 0.011, (rep, rate))
        check(pl13.data["chars"]["Nine"]["grind"] == {"576": [[NOW - 7200, NOW - 120, 2300.0]]},
              pl13.data["chars"]["Nine"]["grind"])
        pl13.tick()
        check(len(pl13.data["chars"]["Nine"]["grind"]["576"]) == 1, "the same session's rise kept twice")
        rec.facs = {}
        world["cb"][0] = charbis()
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
