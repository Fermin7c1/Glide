#!/usr/bin/env python3
"""The sims the coach runs for the player (Phase 3) as the app and the coach wire them (app.App._start_sims after
_start_bis; coach.Coach's local answers, offline callouts and tool budget), on a fake WoWSims (no network, no runner),
a scratch engine home and WoW folder, and no threads (every worker's start stubbed):
- one Sims on the app's one sim queue, WoWSims, item database and game state, with the gear settings
  (gear.sim_questions, gear.talent_check, gear.sim_seconds: their defaults and range from the manifest), the research's
  names, the character's WoWSims weights; set as the coach's hook (coach_tools.sims) and the rules' (talent_check);
- the addon's /glide sim request (GameState.sim_request) answered by the worker's poll through the app's message path,
  spoken, with the Open in WoWSims link;
- Data/Live.lua's chars section: the BiS ranks' fields merged with the sims' (link, sim: {d, suffix, slot, the worn
  item there, the other slot of its pair}), the sims' alone while gear.bis is off;
- the coach: "sim this" answered without a model, with the link; with a model that takes tools (the one that answers:
  the deep model, or the fast one for a spoken question when set so) the question is the model's, whether or not
  wowsims_sim is offered, so a route question ("what if I go to Westfall instead?") is never taken for a sim; a model
  without tools or without its key leaves it to the coach; a talent_check callout said offline carries its link only
  when its line was said; the link never reaches the prompt (the triggers' text, the recent log); the tool gets the
  answer's time left, and only on a turn that answers the player's question (a deep session_start turn: refused).

Run: python3 tests/test_sims_wiring.py   (prints OK)
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
          "TAVILY_API_KEY", "BRAVE_API_KEY"):
    os.environ[k] = ""
import app as glide_app  # noqa: E402
import bis  # noqa: E402
import offline_coach  # noqa: E402
import coach as coach_mod  # noqa: E402
import coach_tools  # noqa: E402
import live_data  # noqa: E402
import research  # noqa: E402
import settings  # noqa: E402
import sim_queue  # noqa: E402
import sim_weights  # noqa: E402
import sims as sims_mod  # noqa: E402
import versions  # noqa: E402
from gear import Gear  # noqa: E402
from glidedb import GlideDB  # noqa: E402
from knowledge_layer import KnowledgeHandle  # noqa: E402
from rules import Rules, Trigger  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


with open(os.path.join(ROOT, "tests", "fixtures", "wowsims_db_small.json"), encoding="utf-8") as fh:
    FX = json.load(fh)

ARCANE = "050205223101311531-5500003-003"
LINK = sims_mod.SITE_PREFIX + "mage/dps/?i=gtm#"
CREDIT = "WoWSims (wowsims.com/forever)"


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


def talent_list(s):
    out = []
    for tab, run in enumerate(s.split("-")):
        for j, c in enumerate(run):
            out.append({"pos": "%d%d%d" % (tab + 1, j // 4 + 1, j % 4 + 1), "rank": int(c), "max": 5, "name": "t"})
    return out


class FakeState:
    """A level-60 Alliance gnome mage wearing the Magister's Crown and the Briarwood Reed, out in the world; the
    addon's sim requests waiting in `asks`, the item hovered in `hover`."""

    def __init__(self):
        self.core = {"c": "0", "d": "0", "mp": "1453", "px": "4512", "py": "6633"}
        self.last_frame = time.time()
        self.addon_restarts, self.addon_restart_t = 0, None
        self.pages = {"B": {"nm": "Nine"}}
        self.gear = {"W": [{"key": "1", "id": 16686, "name": "Magister's Crown"},
                           {"key": "13", "id": 19990, "name": "Briarwood Reed"}], "I": [], "R": []}
        self.asks, self.hover = [], None

    def summary(self):
        return {"name": "Nine", "class": "MAGE", "level": 60, "faction": "Alliance", "race": "Gnome", "target": {},
                "resting": True, "talents": self.talents()}

    def talents(self):
        return talent_list(ARCANE)

    def sim_request(self, now=None):
        return self.asks.pop(0) if self.asks else None

    def hovered(self, now=None):
        return {"id": self.hover, "t": time.time()} if self.hover else None


def make_app(home, gear_cfg):
    a = glide_app.App.__new__(glide_app.App)
    a.cfg = {"research": {"enabled": True, "web": False}}
    a.state, a.rules, a.kb_handle, a.versions = FakeState(), Rules({}), KnowledgeHandle(), versions
    a.rules.gear = Gear(gear_cfg)
    a._ws, a._ws_lock, a._idb = FakeWoWSims(), threading.Lock(), None
    a.research = a.live = None
    a.sim_queue = a.sim_weights = a.bis = a.sims = None
    a.msgs = []
    a.on_message = a.msgs.append
    return a


CALLS = []


def fake_compare(char, variants, queue, ws, db=None, budget_s=30, prio=sim_queue.PRIO_ASKED, weights=None):
    """sim_compare.compare's stand-in: Eye of the Beast +8.2 over the Briarwood Reed."""
    CALLS.append({"variants": [dict(v) for v in variants], "budget": budget_s, "prio": prio, "weights": weights,
                  "queue": queue, "ws": ws})
    return [{"label": "Eye of the Beast", "d": 8.2, "s": 0.3, "iters": 10000, "variant": dict(v), "id": v.get("id"),
             "sfx": v.get("sfx") or 0, "slot": 13, "over": "Briarwood Reed", "over_id": 19990, "base": 629.1,
             "pct": 1.3, "tag": "v1.0.4"} for v in variants]


def fake_link(char, req):
    return LINK + req["raid"]["parties"][0]["players"][0]["talentsString"]


tmp = tempfile.mkdtemp(prefix="glide-sims-wiring-")
saved = (glide_app.ROOT, research.Research.start, live_data.LiveData.start, sim_weights.SimWeights.start,
         bis.BisRanker.start, sims_mod.Sims.start, coach_tools.research, coach_tools.bis, coach_tools.sims,
         coach_tools._last_link)
try:
    for cls in (research.Research, live_data.LiveData, sim_weights.SimWeights, bis.BisRanker, sims_mod.Sims):
        cls.start = lambda self: self                     # (no threads)
    wow = os.path.join(tmp, "wow")
    glide = os.path.join(wow, "Interface", "AddOns", "Glide")
    os.makedirs(os.path.join(glide, "Data"))
    with open(os.path.join(glide, "Glide.toc"), "w", encoding="utf-8") as fh:
        fh.write("## Title: Glide\n## Version: 0.23.0\n")
    shutil.copy(os.path.join(ROOT, "addon", "Glide", "Data", "Live.lua"), os.path.join(glide, "Data", "Live.lua"))
    home = os.path.join(tmp, "home")
    os.makedirs(home)
    with open(os.path.join(home, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"wow_dir": wow}, fh)
    glide_app.ROOT = home

    # ---- the settings: through the manifest -------------------------------------------------------------------------
    for key, want in (("gear.sim_questions", True), ("gear.talent_check", True), ("gear.sim_seconds", 30)):
        check(key in settings.ENTRIES and settings.default(key) == want, (key, settings.ENTRIES.get(key)))
    check(settings.Settings("gear", {"sim_seconds": 500}).get("sim_seconds") == 120 and
          settings.Settings("gear", {"sim_seconds": 1}).get("sim_seconds") == 5, "gear.sim_seconds: 5 to 120")

    # ---- wired after the BiS ranks, on the same queue, WoWSims, item data and game state ---------------------------
    gear_cfg = {}
    a = make_app(home, gear_cfg)
    coach_tools.sims = coach_tools.bis = None
    a._start_sim_weights()
    a._start_research()
    s, q = a.sims, a.sim_queue
    check(isinstance(s, sims_mod.Sims) and isinstance(q, sim_queue.SimQueue), "the sims, on the sim queue")
    check(s.queue is q and s.ws is a._ws and s.item_db_fn is a._item_db() and s.state_fn() is a.state,
          "one queue, one WoWSims, one item database, the game state")
    check(s._on("sim_questions") is True and s._on("talent_check") is True and s._on("sim_seconds") == 30,
          "the gear settings, their defaults")
    gear_cfg.update(sim_seconds=500, talent_check=False)
    check(s._on("sim_seconds") == 120 and s._on("talent_check") is False, "read as set now, clamped")
    gear_cfg.clear()
    check(coach_tools.sims is s and a.rules.sims is s and isinstance(a.bis, bis.BisRanker), "the coach's and rules'")
    check(s.emit == a.on_message, "no coach yet: the app's message path")
    check(s.find_fn == a.research._find and s.name_fn is not None, "the research's names")
    a.sim_weights.chars["Nine"] = {"class": "MAGE", "w": {"SP": 1.0, "IN": 0.4}}
    check(s.weights_fn("Nine") == {"SP": 1.0, "IN": 0.4} and s.weights_fn("Other") is None, "the weights' entries")
    check("chars" in a.live.providers and a.live.providers["chars"] == a._live_chars, "one chars provider")
    s.compare_fn, s.link_fn = fake_compare, fake_link

    # ---- the addon's /glide sim: answered on the worker's poll, spoken, with the link -------------------------------
    a.state.asks.append({"id": 13968, "sfx": 0, "ench": 0, "n": 1})
    s.tick(background=False)
    check(len(a.msgs) == 1, a.msgs)
    m = a.msgs[0]
    check(m["kind"] == "answer" and m["speak"] is True and m["model"] == "WoWSims" and
          m["text"] == "WoWSims: Eye of the Beast over your Briarwood Reed, about +8 DPS on your gear.", m)
    check(m["link"] == {"text": "Open in WoWSims", "url": LINK + ARCANE, "credit": CREDIT}, m)
    c = CALLS[-1]
    check(c["prio"] == sim_queue.PRIO_ASKED and c["budget"] == 30 and c["queue"] is q and
          c["weights"] == {"SP": 1.0, "IN": 0.4}, ("asked, within gear.sim_seconds, with the weights", c))
    check(a.state.asks == [], "taken once")

    # ---- Data/Live.lua: the BiS ranks merged with the sims' --------------------------------------------------------
    with s.lock:
        e = s.data["chars"]["Nine"]
        e["sim"]["14154"] = dict(e["sim"]["13968"], d=5.1, sfx=1234)     # (an item simmed with a random suffix)
    with a.bis.lock:
        a.bis.data["chars"]["Nine"] = {"tag": "v1.0.4", "gear_key": "k", "slots": {"Head": [
            {"id": 226935, "sfx": 0, "rank": 1, "d": 8.1, "s": 0.3, "how": "sim", "avail": "now", "req": None}]}}
    live = a.live.providers["chars"]()
    sim = {13968: [8.2, 0, 13, 19990, 0], 14154: [5.1, 1234, 13, 19990, 0]}
    check(live == {"Nine": {"bis": {226935: ["Head", 1, 8.1]}, "link": LINK + ARCANE, "sim": sim}}, live)
    text = a.live.text()
    check('["Nine"] = { bis = { [226935] = { "Head", 1, 8.1 } }, link = "%s", sim = { [13968] = { 8.2, 0, 13, '
          '19990, 0 }, [14154] = { 5.1, 1234, 13, 19990, 0 } } }' % (LINK + ARCANE) in text, text[-500:])
    gear_cfg["bis"] = False
    check(a.live.providers["chars"]() == {"Nine": {"link": LINK + ARCANE, "sim": sim}},
          "gear.bis off: the sims' fields alone")
    gear_cfg.pop("bis")

    # ---- the coach: "sim this" without a model, with the link -----------------------------------------------------
    emitted = []
    no_tools = set()                                   # models the catalog says take no tools
    fake = types.SimpleNamespace(convo=[], cfg={"fast_model": "claude-haiku-4-5-20251001",
                                                "deep_model": "claude-sonnet-4-5-20250929"},
                                 opt={"voice_question_model": "deep"}, pack=None, state=a.state,
                                 llm=types.SimpleNamespace(enabled=False, keys={}, web_off=True, catalog=types.
                                                           SimpleNamespace(supports_tools=lambda m: m not in no_tools)),
                                 deps=types.SimpleNamespace(reminders=None, memory=None), emit=emitted.append,
                                 _now=lambda: 0.0, _question_turn=True)
    fake._local_command = fake._memory_command = fake._reminder_command = lambda question: None
    fake._item_command = lambda question: None
    fake._sim_command = lambda question, voice=False: coach_mod.Coach._sim_command(fake, question, voice)
    fake._answer_model = lambda voice: coach_mod.Coach._answer_model(fake, voice)
    fake._tools_ok = lambda model: coach_mod.Coach._tools_ok(fake, model)
    a.state.hover = 13968
    got = coach_mod.Coach._answer_locally(fake, "sim this", False)
    check(got is not None and got == emitted[-1] and got["model"] == "WoWSims" and got["speak"] is True and
          got["text"].startswith("WoWSims: Eye of the Beast") and got["link"]["url"] == LINK + ARCANE, got)
    check(coach_mod.Coach._answer_locally(fake, "how far to Ironforge?", False) is None, "another question")
    for q in ("what if I go to Westfall instead?", "what if I use my hearthstone?", "how much would a mount give me?"):
        check(coach_mod.Coach._answer_locally(fake, q, False) is None, ("the AI off: not a sim either", q))
    fake.llm.enabled, fake.llm.keys = True, {"anthropic": "x"}
    fake.pack = object()
    check(coach_mod.Coach._sim_command(fake, "sim this") is None, "a model with the tool answers it")
    gear_cfg["sim_questions"] = False
    check(coach_mod.Coach._sim_command(fake, "sim this") is None, "switched off: the model says so")
    gear_cfg.pop("sim_questions")
    # levelling (no WoWSims runner yet: it comes with the weights at 60): the model answers, never "I can't sim that"
    real_ws = s.ws
    s.ws = types.SimpleNamespace(current=lambda: None)
    check(coach_tools.sim_tools() == [], "no runner: no tool")
    for q in ("what if I go to Westfall instead?", "what if I use my hearthstone?", "how much would a mount give me?",
              "what if I went back to town?", "sim this"):
        check(coach_mod.Coach._answer_locally(fake, q, False) is None and
              coach_mod.Coach._answer_locally(fake, q, True) is None, ("the model's question", q))
    s.ws = real_ws
    # the model that answers decides: the deep one for a typed question (and a spoken one, unless set to fast)
    fake.cfg["deep_model"] = "openrouter/some-model"
    check(coach_mod.Coach._sim_command(fake, "sim this")["text"].startswith("WoWSims: Eye of the Beast"),
          "no key for the deep model: the coach sims it")
    check(coach_mod.Coach._sim_command(fake, "sim this", True)["text"].startswith("WoWSims: Eye"), "spoken: the deep")
    fake.opt["voice_question_model"] = "fast"
    check(coach_mod.Coach._sim_command(fake, "sim this", True) is None, "spoken, the fast model's: it has its key")
    fake.opt["voice_question_model"] = "deep"
    fake.cfg["deep_model"] = "claude-sonnet-4-5-20250929"
    no_tools.add("claude-sonnet-4-5-20250929")
    check(coach_mod.Coach._sim_command(fake, "sim this")["text"].startswith("WoWSims: Eye of the Beast"),
          "a deep model without tools: the coach sims it")
    no_tools.clear()

    # the tool: the answer's time left caps the sim
    n = len(CALLS)
    got = coach_mod.Coach._local_tool(fake, "wowsims_sim", {"items": ["13968"]}, 9.5)
    check(len(CALLS) == n + 1 and CALLS[-1]["budget"] == 9.5 and got["results"][0]["d"] == 8.2 and
          got["link"] == "shown on the coach screen as Open in WoWSims", (CALLS[-1], got))
    check(coach_tools.sim_message_link((), ["wowsims_sim"])["url"] == LINK + ARCANE, "the answer's link")
    s.compare_fn = lambda *x, **kw: (_ for _ in ()).throw(sims_mod.SimError("broken"))
    got = coach_mod.Coach._local_tool(fake, "wowsims_sim", {"items": ["13968"]}, 9.5)
    check(got.get("say") == sims_mod.FAILED and coach_tools.sim_message_link((), ["wowsims_sim"]) is None,
          ("a failed sim leaves no link (never an earlier one)", got))
    s.compare_fn = fake_compare
    # only a turn that answers the player's question sims (a deep session_start or pace_review turn: refused)
    fake._question_turn = False
    n = len(CALLS)
    got = coach_mod.Coach._local_tool(fake, "wowsims_sim", {"items": ["13968"]}, 9.5)
    check(got.get("error") and len(CALLS) == n, ("refused off a question", got))
    fake._question_turn = True
    c = coach_mod.Coach({}, GlideDB(os.path.join(tmp, "x.db")), lambda m: None, offline_ok=True)
    c.llm.enabled, c.llm.keys = True, {"anthropic": "x", "openrouter": "", "gemini": "", "endpoint": ""}
    seen = []

    def ask_model(model, user, kctx, question, voice, deep, speech):
        seen.append((bool(question), c._run_tool("wowsims_sim", {"items": ["13968"]}, None, 10.0)))
        return "Fine.", {}

    c._ask_model = ask_model
    c._emit_raw = lambda m: None
    c._respond([Trigger("session_start", {}, 2, deep=True)])
    c._respond([], question="is the Eye of the Beast good for me?")
    check([q for q, _ in seen] == [False, True] and seen[0][1].get("error") and len(CALLS) == n + 1 and
          seen[1][1]["results"][0]["d"] == 8.2 and not c._question_turn, ("a session start sims nothing", seen))

    # ---- a talent_check callout: its link on the coach screen, never in the prompt ----------------------------------
    t = Trigger("talent_check", {"build": "Arcane Fire", "pct": 11.1, "link": LINK + "x"}, 3)
    fake.emit = emitted.append
    msg = coach_mod.Coach._emit_offline(fake, None, "WoWSims' Arcane Fire build sims about 11% higher on your gear, "
                                        "with its own rotation.", False, "offline", [t])
    check(msg["link"] == {"text": "Open in WoWSims", "url": LINK + "x", "credit": CREDIT}, msg)
    # batched with three other lines: the talent line is never the one cut, and a text without it has no link
    batch = [Trigger("buffs_dropped", {"buffs": ["Arcane Intellect"]}, 2), Trigger("bags_nearly_full", {}, 3),
             Trigger("low_durability", {}, 3), t]
    said = offline_coach.callout(batch, {"player": {}})
    check(said.endswith("WoWSims' Arcane Fire build sims about 11% higher on your gear, with its own rotation.") and
          said.count(".") == 3, said)
    check(coach_mod.Coach._emit_offline(fake, None, said, False, "offline", batch)["link"]["url"] == LINK + "x",
          "its line said: its link")
    check("link" not in coach_mod.Coach._emit_offline(fake, None, "Rebuff: Arcane Intellect.", False, "offline",
                                                      batch), "its line not said: no link")
    check("link" not in coach_mod.Coach._emit_offline(fake, "q?", "an answer", True, "offline", [t]),
          "an offline answer has no callout's link")
    text = coach_mod.Coach._turn_text([t], None, False)
    check("Arcane Fire" in text and "wowsims.com" not in text and '"link"' not in text, text)
    c2 = types.SimpleNamespace(lock=threading.Lock(), pending=[], recent_triggers=[], recent=[], _now=lambda: 0.0,
                               wake=threading.Event())
    coach_mod.Coach.queue_triggers(c2, [t])
    check("wowsims.com" not in json.dumps(c2.recent) and c2.pending == [t], c2.recent)

    # ---- a coach: the sims answer through its emit (its conversation knows what it said) --------------------------
    b = make_app(os.path.join(tmp, "home"), {})
    b.coach = types.SimpleNamespace(emit=lambda m: None)
    b._start_sim_weights()
    b._start_research()
    check(b.sims is not None and b.sims.emit == b.coach.emit, "the coach's emit")

    # ---- no sim queue (WoWSims couldn't be set up): no sims ---------------------------------------------------------
    coach_tools.sims = None
    d = make_app(home, {})
    d._start_research()
    check(d.sims is None and coach_tools.sims is None and "chars" not in d.live.providers, "no queue, no sims")
finally:
    (glide_app.ROOT, research.Research.start, live_data.LiveData.start, sim_weights.SimWeights.start,
     bis.BisRanker.start, sims_mod.Sims.start, coach_tools.research, coach_tools.bis, coach_tools.sims,
     coach_tools._last_link) = saved
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
