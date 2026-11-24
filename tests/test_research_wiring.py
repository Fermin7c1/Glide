#!/usr/bin/env python3
"""The engine's item research as the app wires it (app.App._start_research), end to end on a cut of WoWSims' item
database (tests/fixtures/wowsims_db_small.json), a fake WoWSims (no network), a scratch engine home and a scratch WoW
folder with addon 0.20.0, and no threads (each worker's poll run by hand): the research worker reads the item data at
WoWSims' data tag through the app's one WoWSims, the facts land in the addon's Data/Live.lua, the addon's "lg" core
key is observed from the game state, the rules' facts_ready nudge says a restart is needed the first time, the coach
offers item_info and answers "where does X drop?" itself without a model, and research.enabled off looks nothing up.

Run: python3 tests/test_research_wiring.py   (prints OK)
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
          "TAVILY_API_KEY", "BRAVE_API_KEY"):
    os.environ[k] = ""
import app as glide_app  # noqa: E402
import coach as coach_mod  # noqa: E402
import coach_tools  # noqa: E402
import live_data  # noqa: E402
import offline_coach  # noqa: E402
import research  # noqa: E402
import versions  # noqa: E402
from knowledge_layer import KnowledgeHandle  # noqa: E402
from rules import Rules  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


with open(os.path.join(ROOT, "tests", "fixtures", "wowsims_db_small.json"), encoding="utf-8") as fh:
    FX = json.load(fh)


class FakeWoWSims:
    """WoWSims' repository files at a tag, from the fixture (never the network)."""

    def __init__(self):
        self.fetched = []

    def data_tag(self):
        return "v1.0.4"

    def file(self, path, tag=None):
        self.fetched.append((path, tag))
        return json.dumps(FX[{"assets/database/db.json": "db", "assets/database/leftover_db.json": "leftover"}[path]]
                          ).encode("utf-8")


class FakeState:
    """The game state the workers read: a level-60 Alliance mage wearing the Magister's Crown."""

    def __init__(self):
        self.core, self.addon_restarts, self.addon_restart_t = {}, 0, None
        self.gear = {"W": [{"id": 16686, "name": "Magister's Crown"}], "I": [], "R": []}

    def summary(self):
        return {"class": "MAGE", "level": 60, "faction": "Alliance", "target": {}, "resting": True}


def make_app(home, enabled=True):
    a = glide_app.App.__new__(glide_app.App)
    a.cfg = {"research": {"enabled": enabled}}
    a.state, a.rules, a.kb_handle, a.versions = FakeState(), Rules({}), KnowledgeHandle(), versions
    a._ws, a._ws_lock = FakeWoWSims(), threading.Lock()
    a.research = a.live = None
    return a


tmp = tempfile.mkdtemp(prefix="glide-research-wiring-")
saved = (glide_app.ROOT, research.Research.start, live_data.LiveData.start, coach_tools.research)
try:
    research.Research.start = lambda self: self              # (no threads: each poll run below)
    live_data.LiveData.start = lambda self: self
    wow = os.path.join(tmp, "wow")
    glide = os.path.join(wow, "Interface", "AddOns", "Glide")
    os.makedirs(os.path.join(glide, "Data"))
    with open(os.path.join(glide, "Glide.toc"), "w", encoding="utf-8") as fh:
        fh.write("## Title: Glide\n## Version: 0.20.0\n")
    shutil.copy(os.path.join(ROOT, "addon", "Glide", "Data", "Live.lua"), os.path.join(glide, "Data", "Live.lua"))
    home = os.path.join(tmp, "home")
    os.makedirs(home)
    with open(os.path.join(home, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"wow_dir": wow}, fh)
    glide_app.ROOT = home

    # ---- wired: one WoWSims, the research worker, the coach's tool, Data/Live.lua's writer, the rules' nudge -------
    a = make_app(home)
    ws = a._ws
    a._start_research()
    check(isinstance(a.research, research.Research) and isinstance(a.live, live_data.LiveData), "both made")
    check(coach_tools.research is a.research and a.rules.live is a.live, "the coach's tool and the rules use them")
    check(a._wowsims() is ws and "items" in a.live.providers, "the app's one WoWSims; the items section registered")
    check(a.live.write() is False, "nothing looked up yet: the shipped file stays")
    a.research.tick()
    check(("assets/database/db.json", "v1.0.4") in ws.fetched and
          ("assets/database/leftover_db.json", "v1.0.4") in ws.fetched, ws.fetched)
    f = a.research.facts(16686)
    check(f and f["avail"] == "now" and f["tag"] == "v1.0.4", f)
    a.live.tick()
    with open(os.path.join(glide, "Data", "Live.lua"), encoding="utf-8") as fh:
        text = fh.read()
    check("gen = 1," in text and '    [16686] = { "drops from Darkmaster Gandling, Scholomance", a = "n" },\n' in text,
          text[:600])
    check('[226935] = { "sold by Deliana, Ironforge", a = "n" }' in text, "the band's items too")
    check(a.live.tick() is None and a.live.gen == 1, "written only when it changed")

    # the first written file needs a WoW restart (its TOC line is new): said once, at a quiet moment
    out = []
    a.rules._live({"resting": True}, out)
    check([t.kind for t in out] == ["facts_ready"] and out[0].facts["restart"] is True and out[0].facts["n"] > 10,
          [(t.kind, t.facts) for t in out])
    check(offline_coach.facts_ready_line(out[0].facts) ==
          "Glide learned where your gear comes from; restart WoW once to see it in the game.", "the restart nudge")
    a.rules.cool.pop("live", None)
    out = []
    a.rules._live({"resting": True, "combat": True}, out)
    check(out == [], "never in combat")

    # the addon reports the gen it loaded (core key lg): seen from the game state on the writer's poll
    a.state.core = {"lg": "1"}
    a.live.tick()
    check(a.live.lg == 1 and a.live.loaded, (a.live.lg, a.live.loaded))

    # the coach: item_info offered and run locally; "where does X drop?" answered without a model
    fake = types.SimpleNamespace(api=None, deps=types.SimpleNamespace(reminders=None, memory=None),
                                 llm=types.SimpleNamespace(web_off=True, enabled=False, keys={}),
                                 cfg={"fast_model": "claude-haiku-4-5-20251001"}, pack=None)
    check("item_info" in [d["name"] for d in coach_mod.Coach._tools(fake)], "the tool is offered")
    info = coach_mod.Coach._local_tool(fake, "item_info", {"item": "magisters crown"})
    check(info["id"] == 16686 and info["sources"][0] == "drops from Darkmaster Gandling, Scholomance" and
          info["fact_ids"] == ["ws:i:16686"], info)
    check(coach_mod.Coach._item_command(fake, "Where does the Magister's Crown drop?") ==
          "Magister's Crown: drops from Darkmaster Gandling, Scholomance.", "answered offline")
    check(coach_mod.Coach._item_command(fake, "how far to Scholomance?") is None, "not an item question")

    # ---- research.enabled off: nothing looked up, nothing written ----------------------------------------------------
    home2 = os.path.join(tmp, "home2")
    shutil.copytree(home, home2, ignore=shutil.ignore_patterns("data"))
    glide_app.ROOT = home2
    shutil.copy(os.path.join(ROOT, "addon", "Glide", "Data", "Live.lua"), os.path.join(glide, "Data", "Live.lua"))
    b = make_app(home2, enabled=False)
    b._start_research()
    b.research.tick()
    check(b._ws.fetched == [] and b.research.facts(16686) is None, "switched off: nothing looked up")
    check(b.live.write() is False, "and nothing to write")
finally:
    glide_app.ROOT, research.Research.start, live_data.LiveData.start, coach_tools.research = saved
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
