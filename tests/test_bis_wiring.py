#!/usr/bin/env python3
"""Glide's own best-in-slot ranks as the app wires them (app.App._start_sim_weights, _start_research, _start_bis), on a
fake WoWSims (no network, no runner), a scratch engine home and WoW folder, and no threads (every worker's start
stubbed): one sim queue on the app's one WoWSims, paused by the game state (sim_queue.busy); the stat weights run
through it at PRIO_WEIGHTS with the app's one WoWSims item database (the research's ItemDBSource); the BiS ranker on
the same queue and item database, with the research's facts for sources and the weights' entries for its EP screen,
handing its ranks to the gear (Gear.set_bis), telling the rules what was said (rules.bis), answering the coach's
bis_slot (coach_tools.bis) and the prompt's gear.next_upgrades (the gear_fn's fifth element, App._bis_now), and writing
Data/Live.lua's chars section; gear.bis off: nothing shown or said; gear.bis_lens read as set.

Run: python3 tests/test_bis_wiring.py   (prints OK)
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
          "TAVILY_API_KEY", "BRAVE_API_KEY"):
    os.environ[k] = ""
import app as glide_app  # noqa: E402
import bis  # noqa: E402
import coach_tools  # noqa: E402
import live_data  # noqa: E402
import research  # noqa: E402
import sim_queue  # noqa: E402
import sim_weights  # noqa: E402
import versions  # noqa: E402
from gear import Gear  # noqa: E402
from knowledge_layer import KnowledgeHandle  # noqa: E402
from rules import Rules  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


with open(os.path.join(ROOT, "tests", "fixtures", "wowsims_db_small.json"), encoding="utf-8") as fh:
    FX = json.load(fh)


class FakeWoWSims:
    """WoWSims' repository files at a tag, from the fixture (never the network); no runner is ever started."""

    def data_tag(self):
        return "v1.0.4"

    def current(self):
        return "v1.0.4"

    def file(self, path, tag=None):
        return json.dumps(FX[{"assets/database/db.json": "db", "assets/database/leftover_db.json": "leftover"}[path]]
                          ).encode("utf-8")

    def run(self, *a, **k):
        raise AssertionError("no sim runs here")


class FakeState:
    """A level-60 Alliance mage wearing the Magister's Crown, out in the world."""

    def __init__(self):
        self.core = {"c": "0", "d": "0", "mp": "1453", "px": "4512", "py": "6633"}
        self.last_frame = time.time()
        self.addon_restarts, self.addon_restart_t = 0, None
        self.pages = {"B": {"nm": "Nine"}}
        self.gear = {"W": [{"key": "1", "id": 16686, "name": "Magister's Crown"}], "I": [], "R": []}

    def summary(self):
        return {"name": "Nine", "class": "MAGE", "level": 60, "faction": "Alliance", "target": {}, "resting": True}

    def talents(self):
        return None


def make_app(home, gear_cfg):
    a = glide_app.App.__new__(glide_app.App)
    a.cfg = {"research": {"enabled": True, "web": False}}
    a.state, a.rules, a.kb_handle, a.versions = FakeState(), Rules({}), KnowledgeHandle(), versions
    a.rules.gear = Gear(gear_cfg)
    a._ws, a._ws_lock, a._idb = FakeWoWSims(), threading.Lock(), None
    a.research = a.live = None
    a.sim_queue = a.sim_weights = a.bis = None
    a.on_message = lambda m: None
    return a


CB = {"tag": "v1.0.4", "gear_key": "k", "dps": 629.1, "faction": "Alliance", "skills": None,
      "slots": {"Head": [{"id": 226935, "sfx": 0, "rank": 1, "d": 8.1, "s": 0.3, "how": "sim", "avail": "now",
                          "req": None},
                         {"id": 16686, "sfx": 0, "rank": 2, "d": 0.0, "s": 0.0, "how": "worn", "avail": "now",
                          "req": None}],
                "Chest": [{"id": 14152, "sfx": 0, "rank": 1, "d": 9.0, "s": 0.4, "how": "sim", "avail": "now",
                           "req": "Tailoring"}]},
      "set": {"Head": 226935}, "next": {"id": 226935, "slot": "Head", "d": 8.1, "s": 0.3, "sfx": 0, "avail": "now",
                                        "source": "sold by Deliana, Ironforge"},
      "enchants": {}, "told": {}}

tmp = tempfile.mkdtemp(prefix="glide-bis-wiring-")
saved = (glide_app.ROOT, research.Research.start, live_data.LiveData.start, sim_weights.SimWeights.start,
         bis.BisRanker.start, coach_tools.research, coach_tools.bis)
try:
    for cls in (research.Research, live_data.LiveData, sim_weights.SimWeights, bis.BisRanker):
        cls.start = lambda self: self                     # (no threads)
    wow = os.path.join(tmp, "wow")
    glide = os.path.join(wow, "Interface", "AddOns", "Glide")
    os.makedirs(os.path.join(glide, "Data"))
    with open(os.path.join(glide, "Glide.toc"), "w", encoding="utf-8") as fh:
        fh.write("## Title: Glide\n## Version: 0.22.0\n")
    shutil.copy(os.path.join(ROOT, "addon", "Glide", "Data", "Live.lua"), os.path.join(glide, "Data", "Live.lua"))
    home = os.path.join(tmp, "home")
    os.makedirs(home)
    with open(os.path.join(home, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"wow_dir": wow}, fh)
    glide_app.ROOT = home

    gear_cfg = {}
    a = make_app(home, gear_cfg)
    check(a._bis_now() is None, "nothing ranks before the ranker runs (a simulated run): no fifth gear element")

    # ---- the sim queue and the weights through it ----------------------------------------------------------------
    a._start_sim_weights()
    q, sw = a.sim_queue, a.sim_weights
    check(isinstance(q, sim_queue.SimQueue) and q.ws is a._ws and isinstance(sw, sim_weights.SimWeights)
          and sw.ws is a._ws, "one queue and the weights on the app's one WoWSims")
    check(sw.item_db_fn is a._item_db() and isinstance(a._item_db(), research.ItemDBSource),
          "the weights read the app's one item database")
    check(q.pause_fn() is False, "out in the world: not paused")
    a.state.core["c"] = "1"
    check(q.pause_fn() is True, "in combat: paused")
    a.state.core.update(c="0", px="", py="")
    check(q.pause_fn() is True, "in an instance (the map without a position): paused")
    a.state.core.update(px="4512", py="6633")
    ran = []
    q.sim = lambda req, tag=None, prio=None, **k: ran.append((req, tag, prio)) or 600.0
    check(sw.sim_fn({"r": 1}, "v1.0.4") == 600.0 and ran == [({"r": 1}, "v1.0.4", sim_queue.PRIO_WEIGHTS)], ran)
    del q.sim

    # ---- the research, then the BiS ranker on the same queue and item data -----------------------------------------
    coach_tools.bis = None
    a._start_research()
    rk = a.bis
    check(isinstance(rk, bis.BisRanker) and rk.queue is q and rk.ws is a._ws, "the ranker on the one queue")
    check(rk.item_db_fn is a._item_db() and a.research.item_db_fn is rk.item_db_fn, "one item database for all")
    check(rk.sources_fn == a.research.facts, "where items come from: the research's facts")
    sw.chars["Nine"] = {"class": "MAGE", "w": {"SP": 1.0}}
    check(rk.weights_fn("Nine") is sw.chars["Nine"] and rk.weights_fn("Other") is None, "the weights' entries")
    check(rk.state_fn() is a.state and rk.skills_fn is None and rk._skills("Nine") is None,
          "professions unknown (they don't reach the engine)")
    check(rk.enabled_fn == a.rules.gear.bis_on and rk.enabled_fn() is True, "gear.bis: on by default")
    check(rk.lens() == "now", "gear.bis_lens: now by default")
    gear_cfg["bis_lens"] = "all"
    check(rk.lens() == "all", "gear.bis_lens read as set")
    gear_cfg.pop("bis_lens")
    check(rk.on_change == a.rules.gear.set_bis and a.rules.bis is rk and coach_tools.bis == a._bis_now,
          "the gear, the rules and the coach's tool")
    check("chars" in a.live.providers and "items" in a.live.providers, "Live.lua's chars section registered")

    # ---- ranks handed on: the gear, the coach, the addon -----------------------------------------------------------
    with rk.lock:
        rk.data["chars"]["Nine"] = json.loads(json.dumps(CB))
    rk._changed("Nine", CB)
    got = a._bis_now()
    check(got is not None and got[0]["next"]["id"] == 226935 and got[1] == [16686], got)
    rows = coach_tools.bis_slot("head")
    check([r_["rank"] for r_ in rows] == [1, 2] and rows[1].get("worn") is True, rows)
    live = a.live.providers["chars"]()
    check(live == {"Nine": {"bis": {226935: ["Head", 1, 8.1], 16686: ["Head", 2, 0.0],
                                    14152: ["Chest", 1, 9.0, None, "Tailoring"]}}}, live)
    text = a.live.text()
    check('["Nine"] = { bis = {' in text and '[14152] = { "Chest", 1, 9.0, nil, "Tailoring" }' in text, text[-600:])

    # ---- gear.bis off: nothing shown or said --------------------------------------------------------------------
    gear_cfg["bis"] = False
    check(a._bis_now() is None and a.live.providers["chars"]() == {} and not rk.enabled_fn(), "switched off")
    check(coach_tools.bis_tool({"slot": "head"}).get("error"), "the tool has nothing to say")
    out = []
    a.rules.started = True
    a.rules._bis(a.state, dict(a.state.summary(), addon_version="0.22.0"), out)
    check(out == [], "the rules say nothing")
    gear_cfg["bis"] = True

    # ---- no sim queue (WoWSims couldn't be set up): no ranker ------------------------------------------------------
    b = make_app(home, {})
    b._start_research()
    check(b.bis is None and "chars" not in b.live.providers and isinstance(b.research, research.Research),
          "the research without the ranks")
finally:
    (glide_app.ROOT, research.Research.start, live_data.LiveData.start, sim_weights.SimWeights.start,
     bis.BisRanker.start, coach_tools.research, coach_tools.bis) = saved
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
