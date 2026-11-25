#!/usr/bin/env python3
"""The next move toward the next BiS piece (Phase 4) as the app wires it (app.App._start_research, which starts the
Recorder's reader, the BiS ranks, the sims and the pursuit planner in turn), on a fake WoWSims (no network, no runner),
a scratch engine home and WoW folder with the addon's saved data (tests/fixtures/recorder_sv_new.lua), and no threads
(every worker's start stubbed):
- the Recorder's reader (recorder_data.RecorderData) on the configured WoW folder; its professions are the BiS ranks'
  and the sims' skills (ring enchants and items that need a profession count when the character has it);
- one Planner with the BiS ranks (none while gear.bis is off), the research (its web lookups for the numbers a move
  lacks), effort.py, the knowledge pack, the game state, the Recorder, the pursuit settings (the manifest's defaults:
  pursuit.enabled, pin, atlasloot_rates, group, form_minutes), the item database and the WoW folder (AtlasLootClassic);
  set as the rules' (with the coach's reminders) and the coach's (coach_tools.pursuit, pursuit_char, goal_memory);
- Data/Live.lua: chars[key].next and goal merged into the same entry as the BiS ranks' bis, and the pursuit section's
  pin (empty while pursuit.enabled is off);
- the overlay line (contract 3's overlay_step): the guide step's while one runs; at 60 without one, the next move;
  nothing below 60 or with the planner off;
- no BiS ranks (no sim queue): no planner.

Run: python3 tests/test_pursuit_wiring.py   (prints OK)
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
import effort  # noqa: E402
import live_data  # noqa: E402
import pursuit  # noqa: E402
import recorder_data  # noqa: E402
import research  # noqa: E402
import sim_weights  # noqa: E402
import sims as sims_mod  # noqa: E402
import versions  # noqa: E402
from gear import Gear  # noqa: E402
from knowledge_layer import KnowledgeHandle  # noqa: E402
from rules import Rules  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


with open(os.path.join(ROOT, "tests", "fixtures", "wowsims_db_small.json"), encoding="utf-8") as fh:
    FX = json.load(fh)
STYLUS, CROWN = 13938, 226935


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
    """A level-60 Alliance mage on Skyborne, out in the world, no guide step."""

    def __init__(self):
        self.core = {"c": "0", "d": "0", "mp": "1453", "px": "4512", "py": "6633"}
        self.last_frame = time.time()
        self.addon_restarts, self.addon_restart_t = 0, None
        self.pages = {"B": {"nm": "Nine"}}
        self.gear = {"W": [{"key": "1", "id": 16686, "name": "Magister's Crown"}], "I": [], "R": []}
        self.quests = {}
        self.level = 60

    def summary(self):
        return {"name": "Nine", "realm": "Skyborne", "class": "MAGE", "level": self.level, "faction": "Alliance",
                "target": {}, "resting": True, "money": 1000000, "map": 1453, "zone": "Stormwind City"}

    def talents(self):
        return None


class FakeMemory:
    """memory.Memory's list / add / remove (the coach's goal note)."""

    def __init__(self):
        self.notes = []

    def list(self):
        return [dict(n) for n in self.notes]

    def add(self, text, kind, source=None):
        self.notes.append({"id": len(self.notes) + 1, "text": text, "kind": kind})

    def remove(self, nid):
        self.notes = [n for n in self.notes if n["id"] != nid]


class FakeReminders:
    def __init__(self):
        self.calls = []

    def replace(self, source, specs, now=None, char=None):
        self.calls.append((source, list(specs), char))
        return [], []


class FakeCoach:
    def __init__(self):
        self.deps = type("Deps", (), {"reminders": FakeReminders()})()
        self.emit = lambda m: None


def make_app(home, gear_cfg, pursuit_cfg=None):
    a = glide_app.App.__new__(glide_app.App)
    a.cfg = {"research": {"enabled": True, "web": False}, "pursuit": pursuit_cfg if pursuit_cfg is not None else {}}
    a.state, a.rules, a.kb_handle, a.versions = FakeState(), Rules({}), KnowledgeHandle(), versions
    a.rules.gear = Gear(gear_cfg)
    a._ws, a._ws_lock, a._idb = FakeWoWSims(), threading.Lock(), None
    a.research = a.live = None
    a.sim_queue = a.sim_weights = a.bis = a.sims = a.recorder_data = a.pursuit = None
    a.coach, a.memory = FakeCoach(), FakeMemory()
    a.on_message = lambda m: None
    return a


MOVE = {"act": "inst:2057", "key": "inst:2057|13938", "kind": "inst", "items": [STYLUS],
        "names": {str(STYLUS): "Bonecreeper Stylus"}, "value": 0.7, "hours": 1.2, "text": "Scholomance: Bonecreeper "
        "Stylus", "say": "Next: Scholomance.", "where": {"map": 1422, "x": 0.696, "y": 0.733}}
ALT = dict(MOVE, act="vendor:230319", key="vendor:230319|226935", kind="vendor", items=[CROWN],
           names={str(CROWN): "Sorcerer's Crown"}, text="Deliana, Ironforge: Sorcerer's Crown", where=None)
CB = {"tag": "v1.0.4", "gear_key": "k", "dps": 629.1, "faction": "Alliance", "skills": None,
      "slots": {"Head": [{"id": CROWN, "sfx": 0, "rank": 1, "d": 8.1, "s": 0.3, "how": "sim", "avail": "now",
                          "req": None},
                         {"id": 16686, "sfx": 0, "rank": 2, "d": 0.0, "s": 0.0, "how": "worn", "avail": "now",
                          "req": None}]},
      "set": {"Head": CROWN}, "next": {"id": CROWN, "slot": "Head", "d": 8.1, "s": 0.3, "sfx": 0, "avail": "now",
                                       "source": "sold by Deliana, Ironforge"},
      "enchants": {}, "told": {}}

tmp = tempfile.mkdtemp(prefix="glide-pursuit-wiring-")
saved = (glide_app.ROOT, research.Research.start, live_data.LiveData.start, sim_weights.SimWeights.start,
         bis.BisRanker.start, sims_mod.Sims.start, pursuit.Planner.start, coach_tools.research, coach_tools.bis,
         coach_tools.sims, coach_tools.pursuit, coach_tools.pursuit_char, coach_tools.goal_memory)
try:
    for cls in (research.Research, live_data.LiveData, sim_weights.SimWeights, bis.BisRanker, sims_mod.Sims,
                pursuit.Planner):
        cls.start = lambda self: self                     # (no threads)
    wow = os.path.join(tmp, "wow")
    glide = os.path.join(wow, "Interface", "AddOns", "Glide")
    os.makedirs(os.path.join(glide, "Data"))
    with open(os.path.join(glide, "Glide.toc"), "w", encoding="utf-8") as fh:
        fh.write("## Title: Glide\n## Version: 0.24.0\n")
    shutil.copy(os.path.join(ROOT, "addon", "Glide", "Data", "Live.lua"), os.path.join(glide, "Data", "Live.lua"))
    sv = os.path.join(wow, "WTF", "Account", "ACCOUNT1", "SavedVariables")
    os.makedirs(sv)
    shutil.copy(os.path.join(ROOT, "tests", "fixtures", "recorder_sv_new.lua"), os.path.join(sv, "Glide.lua"))
    home = os.path.join(tmp, "home")
    os.makedirs(home)
    with open(os.path.join(home, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"wow_dir": wow}, fh)
    glide_app.ROOT = home

    gear_cfg, pursuit_cfg = {}, {}
    a = make_app(home, gear_cfg, pursuit_cfg)
    a._start_sim_weights()
    a._start_research()

    # ---- the Recorder's professions: the ranks' and the sims' skills ---------------------------------------------
    rd, rk, sm, pl = a.recorder_data, a.bis, a.sims, a.pursuit
    check(isinstance(rd, recorder_data.RecorderData) and rd.paths() == [os.path.join(sv, "Glide.lua")], rd)
    want = {"Tailoring", "Enchanting", "Cooking"}
    check(rk.skills_fn == a._skills_of and rk._skills("Nine") == want, "the ranks' professions")
    check(sm.skills_fn == a._skills_of and sm._skills("Nine") == sorted(want), "the sims' professions")
    check(a._skills_of("Nobody") is None and a._skills_of(None) is None, "unknown")

    # ---- one planner on the ranks, the research, effort, the kb, the state, the Recorder ---------------------------
    check(isinstance(pl, pursuit.Planner) and pl.research is a.research and pl.effort is effort
          and pl.kb_fn is a.kb_handle and pl.state_fn() is a.state and pl.recorder is rd, "the planner's parts")
    check(pl.item_db_fn is a._item_db() and pl.wow_dir_fn() == wow, "the item database and the WoW folder")
    check([pl.opt.get(k) for k in ("enabled", "pin", "atlasloot_rates", "group", "form_minutes")]
          == [True, "free", True, "pug", 20], "the pursuit settings' defaults")
    pursuit_cfg["form_minutes"] = 500
    check(pl.opt.get("form_minutes") == 120, "pursuit.form_minutes kept in its range")
    pursuit_cfg.clear()
    sw = a.sim_weights
    sw.chars["Nine"] = {"class": "MAGE", "w": {"SP": 1.0}}
    check(pl.weights_fn("Nine") == {"SP": 1.0} and pl.weights_fn("Other") is None, "the WoWSims weights")
    with rk.lock:
        rk.data["chars"]["Nine-Skyborne"] = json.loads(json.dumps(CB))
    check(pl.bis_fn("Nine-Skyborne")["next"]["id"] == CROWN, "the ranks the planner plans from")
    gear_cfg["bis"] = False
    check(pl.bis_fn("Nine-Skyborne") is None, "gear.bis off: no ranks to plan from")
    gear_cfg.pop("bis")
    check(a.rules.pursuit is pl and isinstance(a.rules.reminders, FakeReminders), "the rules' planner and reminders")
    check(coach_tools.pursuit is pl and coach_tools.pursuit_char() == "Nine-Skyborne"
          and coach_tools.goal_memory is a.memory, "the coach's planner, character and goal note")
    # the player's goal reached or skipped: the planner's on_goal_cleared takes the coach's goal note with it
    coach_tools._goal_note("Sorcerer's Crown")
    check([n["text"] for n in a.memory.list()] == ["Best-in-slot goal: Sorcerer's Crown."], a.memory.notes)
    check(callable(pl.on_goal_cleared), "on_goal_cleared not wired")
    pl.on_goal_cleared("Nine-Skyborne")
    check(a.memory.list() == [], ("the goal's note outlived the goal", a.memory.notes))
    check(a.coach.deps.reminders.calls == [], "a running planner's reminders dropped at the start")

    # ---- Data/Live.lua: next and goal beside bis; the pin setting -----------------------------------------------
    with pl.lock:
        c = pl._char("Nine-Skyborne")
        c["moves"], c["goal"] = [MOVE, ALT], STYLUS
    live = a.live.providers["chars"]()
    me = live["Nine-Skyborne"]
    check(me["next"] == {"t": "Scholomance: Bonecreeper Stylus", "m": 1422, "x": 0.696, "y": 0.733,
                         "alt": ["Deliana, Ironforge: Sorcerer's Crown"]} and me["goal"] == STYLUS
          and CROWN in me["bis"], me)
    check(a.live.providers["pursuit"]() == {"pin": "free"}, a.live.providers["pursuit"]())
    text = a.live.text()
    check('next = {' in text and 'goal = 13938' in text and 'pursuit = {' in text and '["pin"] = "free"' in text,
          text[-800:])
    pursuit_cfg["pin"] = "command"
    check(a.live.providers["pursuit"]() == {"pin": "command"}, "the pin setting read as set")

    # ---- the overlay line: the guide's step, else at 60 the next move ---------------------------------------------
    s = dict(a.state.summary(), steps=[])
    check(a.overlay_line(s) == "L60  Next: Scholomance: Bonecreeper Stylus", a.overlay_line(s))
    step = {"text": "Kill Gandling", "elements": []}
    check(a.overlay_line(dict(s, steps=[step])) == glide_app.App.step_line(dict(s, steps=[step])),
          "a guide step runs: its line")
    check(a.overlay_line(dict(s, level=59)) is None, "below 60: no move")

    # ---- pursuit.enabled off: nothing planned, written or shown ------------------------------------------------------
    pursuit_cfg["enabled"] = False
    check(not pl.enabled() and a.overlay_line(s) is None, "off: no overlay line")
    check("next" not in (a.live.providers["chars"]().get("Nine-Skyborne") or {}), "off: no next in Live.lua")
    check(a.live.providers["pursuit"]() == {}, "off: no pin setting")
    # regression: off, the coach offers, plans and answers nothing, and the planner keeps and writes nothing
    names = [d["name"] for d in coach_tools.item_tools()]
    check("bis_plan" not in names and "pursuit_set" not in names, ("off: the plan's tools offered", names))
    check(coach_tools.plan_brief() is None and coach_tools.bis_plan().get("say") == coach_tools.NO_PLAN, "off: planned")
    check(coach_tools.item_command("What's my next move?", False) is None, "off: answered")
    check(pl.plan("Nine-Skyborne", fresh=True)["moves"] == [] and pl.pursue("Nine-Skyborne", CROWN)["moves"] == []
          and pl.skip("Nine-Skyborne", CROWN)["moves"] == [], "off: planned or kept")
    check(not os.path.exists(os.path.join(home, "data", "pursuit.json")), "off: pursuit.json written")
    pursuit_cfg.pop("enabled")
    check("bis_plan" in [d["name"] for d in coach_tools.item_tools()], "on again: the tools")

    # ---- no sim queue (WoWSims couldn't be set up): no ranks, no planner, and no next move's reminders left ---------
    b = make_app(home, {})
    b._start_research()
    check(b.bis is None and b.pursuit is None and isinstance(b.recorder_data, recorder_data.RecorderData),
          "no planner without the ranks")
    check(b.coach.deps.reminders.calls == [("pursuit", [], None)], b.coach.deps.reminders.calls)
finally:
    (glide_app.ROOT, research.Research.start, live_data.LiveData.start, sim_weights.SimWeights.start,
     bis.BisRanker.start, sims_mod.Sims.start, pursuit.Planner.start, coach_tools.research, coach_tools.bis,
     coach_tools.sims, coach_tools.pursuit, coach_tools.pursuit_char, coach_tools.goal_memory) = saved
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
