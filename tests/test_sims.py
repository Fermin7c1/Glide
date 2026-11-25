#!/usr/bin/env python3
"""The sims the coach runs for the player (Phase 3): companion/sims.py's Sims behind the coach's hook
(coach_tools.sims), the wowsims_sim tool and the spoken "sim this" (coach_tools), the addon's /glide sim requests
(GameState.sim_request, answered by the worker), the talent check (rules' talent_check), data/sims.json and the
Data/Live.lua fields. WoWSims itself is faked: sim_compare.compare, simbuild.presets, wowsims_link.link and the sim
queue are stand-ins with fixed numbers, so this runs without the CLI.

- Asked sims: the item found by name, simmed on the player's gear at PRIO_ASKED within gear.sim_seconds; the offline
  line "WoWSims: <item> over your <worn>, about +<d> DPS on your gear." ("about the same as yours" within 2 sigma, and
  for nothing simmed, d and s 0); a talent build with its own rotation and its percent of the worn gear's DPS; a custom
  talent string says its tree's rotation; the item worn already (as sim_compare has it: a second copy of one that can
  be worn twice is simmed), the build had already (also written with WoWSims' trailing zeroes), an unknown item or
  build say so. The answer's link is the gear simmed, even when the player changed it meanwhile.
- The spoken command: "sim ..." always; "what if I wear X?" only for gear, "what if I go X?" only for a build, so
  route questions ("what if I go to Westfall instead?", "what if I use my hearthstone?") are left to the coach.
- The talent check: WoWSims' builds that fit the character (no Skyborne build for a gnome), each with its own
  rotation, in the background (PRIO_BIS) with no deadline; said once per WoWSims release and once per talent change at
  60, only at 3% or more and beyond 2 sigma, never again after a restart, held while gear talk is muted; a check whose
  builds didn't finish in time (sim_compare.Late) is never kept and never counts toward giving up, WoWSims failing does.
- Data/Live.lua's sims: {d, suffix, slot, the worn item there, the other slot of its pair}, 0 within the sims' noise,
  on the gear worn now (other gear drops them even while WoWSims can't run) at the WoWSims release in use; the link
  made only with WoWSims' item data.
- The credit and the link in every sim message (the tool's answer, the spoken command, the addon's request, the
  talent check), and only WoWSims' own links.
- No WoWSims: no tool, and "I can't sim that without WoWSims."

Run: python3 tests/test_sims.py   (prints OK)
"""
import concurrent.futures
import os
import shutil
import sys
import tempfile
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
for _k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
           "TAVILY_API_KEY", "BRAVE_API_KEY"):
    os.environ[_k] = ""
import coach_tools  # noqa: E402
import offline_coach  # noqa: E402
import sim_compare  # noqa: E402
import prompt_context  # noqa: E402
import rules as rules_mod  # noqa: E402
import settings  # noqa: E402
import simbuild  # noqa: E402
import strip_spec  # noqa: E402
import sims as sims_mod  # noqa: E402
from rules import Rules  # noqa: E402
from sim_queue import PRIO_ASKED, PRIO_BIS  # noqa: E402
from state import GameState  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


ARCANE = "050205223101311531-5500003-003"
ARCANE_FIRE = "050205203100311531-525000012001-003"
FFB = "-5550000123011303-2035010311013002"
SKY = "0502052011001-54500001230133031-003"
APL = {"Arcane": "ui/specs/mage/dps/apls/arcane.apl.json",
       "Arcane Fire": "ui/specs/mage/dps/apls/arcaneFireFill.apl.json",
       "FFB": "ui/specs/mage/dps/apls/ffb.apl.json", "Fire Skyborne LT 180": "ui/specs/mage/dps/apls/deepFire.apl.json"}
PRESETS = {"Arcane": (ARCANE, APL["Arcane"]), "Arcane Fire": (ARCANE_FIRE, APL["Arcane Fire"]),
           "FFB": (FFB, APL["FFB"]), "Fire Skyborne LT 180": (SKY, APL["Fire Skyborne LT 180"])}
BASE = 629.1                                          # the worn gear's DPS
LINK = sims_mod.SITE_PREFIX + "mage/dps/?i=gtm#"


def talent_list(s):
    """The addon's talent list ({"pos", "rank"}) a WoWSims talent string comes from (simbuild.talents_string)."""
    out = []
    for tab, run in enumerate(s.split("-")):
        for j, c in enumerate(run):
            out.append({"pos": "%d%d%d" % (tab + 1, j // 4 + 1, j % 4 + 1), "rank": int(c), "max": 5, "name": "t"})
    return out


check(simbuild.talents_string(talent_list(ARCANE)) == ARCANE and simbuild.talents_string(talent_list(FFB)) == FFB,
      "the talent fixture")

WORN = ["1:18000:INVTYPE_HEAD:4:1:50:3:45:1:Old Hat:IN10",
        "13:19990:INVTYPE_TRINKET:4:0:60:3:55:1:Briarwood Reed:SP29",
        "14:18820:INVTYPE_TRINKET:4:0:60:3:55:1:Talisman of Ephemeral Power:",
        "16:18000:INVTYPE_2HWEAPON:4:0:60:3:55:1:Old Staff:IN20"]
NAMES = {13968: "Eye of the Beast", 999: "Robe of the Void", 19990: "Briarwood Reed", 22403: "Diana's Pearl Necklace"}
TYPES = {13968: 12, 999: 5, 22403: 2}                 # WoWSims' ItemType: trinket, chest, neck


class FakeDB:
    suffixes: dict = {}

    def name(self, iid):
        return NAMES.get(iid)

    def item(self, iid):
        return {"id": iid, "type": TYPES[iid]} if iid in TYPES else None

    def find(self, q):
        return [i for i, n in NAMES.items() if n.lower() == str(q).lower()]

    def enchant(self, eid, slot):
        return {}


class ItemDBSource:
    """research.ItemDBSource's shape: called -> the ItemDB; names -> {norm_name: id}."""
    names = {"eye of the beast": 13968, "robe of the void": 999, "dianas pearl necklace": 22403}

    def __call__(self):
        return FakeDB()


class FakeWS:
    def __init__(self, tag="v1.0.4"):
        self.tag = tag

    def current(self):
        return self.tag

    def file(self, path, tag=None):
        return b'{"type": "TypeAPL", "priorityList": []}'


class FakeQueue:
    """The sim queue's submit (the worn gear's own run, for a build's percent)."""
    def __init__(self, ws):
        self.ws, self.runs = ws, []

    def submit(self, req, prio, iterations=None, seed=None, tag=None, background=True):
        self.runs.append({"prio": prio, "tag": tag, "background": background, "req": req})
        f = concurrent.futures.Future()
        f.set_result({"dps": BASE, "stdev": 60.0, "iters": 10000})
        return f


DS = {13968: (8.2, 0.3), 999: (0.4, 0.5), 22403: (3.0, 0.3)}         # item -> (d, s)
TD = {ARCANE_FIRE: (70.0, 0.6), FFB: (10.0, 0.6), SKY: (90.0, 0.6), "0502-55": (25.0, 0.5),
      ARCANE: (5.0, 0.6)}                                                   # talents -> (d, s)
CALLS = []


def fake_compare(char, variants, queue, ws, db=None, budget_s=30, prio=PRIO_ASKED):
    """sim_compare.compare's stand-in: fixed gains; a trinket is set against the first worn one (the weaker)."""
    CALLS.append({"char": char["name"], "variants": [dict(v) for v in variants], "budget": budget_s, "prio": prio,
                  "ws": ws, "queue": queue})
    out = []
    for v in variants:
        if v["kind"] == "item":
            d, s = DS[v["id"]]
            got = dict(v, slot="Trinket 1" if TYPES.get(v["id"]) == 12 else v.get("slot"))
            out.append({"label": NAMES.get(v["id"], "?"), "d": d, "s": s, "iters": 10000, "variant": got})
        else:
            d, s = TD[v["talents"]]
            out.append({"label": v.get("name") or "custom", "d": d, "s": s, "iters": 10000, "variant": dict(v)})
    return out


def fake_link(char, req):
    """wowsims_link.link's stand-in: the talents the request carries end the link."""
    return LINK + req["raid"]["parties"][0]["players"][0]["talentsString"]


def gear_link(char, req):
    """wowsims_link.link's stand-in: the items the request carries end the link."""
    return LINK + "-".join(str(it["id"]) for it in req["raid"]["parties"][0]["players"][0]["equipment"]["items"] if it)


class Opt:
    def __init__(self, **kw):
        self.v = {"sim_questions": True, "talent_check": True, "sim_seconds": 30}
        self.v.update(kw)

    def get(self, name):
        return self.v[name]


clock = [1000000.0]
real_time = rules_mod.time
rules_mod.time = types.SimpleNamespace(time=lambda: clock[0])
saved = (coach_tools.sims, coach_tools.research, coach_tools.bis, coach_tools._last_link)
home = tempfile.mkdtemp(prefix="glide-sims-")
try:
    st = GameState()
    req_box = []                                   # the addon's sim requests waiting (GameState.sim_request)
    hover = [None]
    st.sim_request = lambda now=None: req_box.pop(0) if req_box else None
    st.hovered = lambda now=None: {"id": hover[0], "t": clock[0]} if hover[0] else None

    def frame(talents=ARCANE, level=60, combat=0, dead=0, cf=None, race="Gnome", worn=None, step=6):
        """One frame `step` seconds on."""
        clock[0] += step
        core = {"t": str(int(clock[0]) % 1000000), "l": str(level), "x": "1", "X": "100", "c": str(combat),
                "d": str(dead), "gv": "0.23.0", "cl": "MAGE"}
        if cf is not None:
            core["cf"] = str(cf)
        st.update(core, {"pg": "B", "nm": "Nine", "cl": "MAGE", "ra": race, "fa": "Alliance"}, now=clock[0])
        st.gear["W"] = [GameState.parse_item(e) for e in (worn or WORN)]
        st.gear["I"] = [GameState.parse_item("0.1:22403:INVTYPE_NECK:4:0:60:4:55:1:Diana's Pearl Necklace:IN10")]
        st.lists["N"] = {"items": ["%s:%d:%d:%s" % (t["pos"], t["rank"], t["max"], t["name"])
                                   for t in talent_list(talents)], "page": {}}
        return st

    def make(ws=None, opt=None, where=None, idb=None):
        ws = ws or FakeWS()
        q = FakeQueue(ws)
        s = sims_mod.Sims(where or home, q, idb or ItemDBSource(), lambda: st, opt or Opt(), now=lambda: clock[0])
        s.compare_fn, s.presets_fn, s.link_fn = fake_compare, lambda cls, w, tag: dict(PRESETS), fake_link
        msgs = []
        s.emit = msgs.append
        return s, q, msgs

    frame()
    sims, queue, msgs = make()
    coach_tools.sims = sims
    coach_tools.research = coach_tools.bis = None

    # ---- the tool: offered while WoWSims runs, a local tool ------------------------------------------------------
    check([d["name"] for d in coach_tools.item_tools()] == ["wowsims_sim"], coach_tools.item_tools())
    check("wowsims_sim" in coach_tools.LOCAL_TOOLS and "wowsims_sim" in coach_tools.ITEM_TOOL_NAMES, "a local tool")

    # ---- an asked item sim: found by name, simmed at PRIO_ASKED within gear.sim_seconds ---------------------------
    got = coach_tools.run_item_tool("wowsims_sim", {"items": ["Eye of the Beast"]})
    check(len(CALLS) == 1 and CALLS[-1]["prio"] == PRIO_ASKED and CALLS[-1]["budget"] == 30.0 and
          CALLS[-1]["variants"] == [{"kind": "item", "id": 13968, "slot": None, "sfx": None, "ench": None}], CALLS)
    check(CALLS[-1]["queue"] is queue and CALLS[-1]["ws"] is queue.ws, "the sim queue and its WoWSims")
    line = "WoWSims: Eye of the Beast over your Briarwood Reed, about +8 DPS on your gear."
    check(got["said"] == line and got["results"][0]["said"] == line, got)
    check(got["results"][0]["d"] == 8.2 and got["results"][0]["plus_minus"] == 0.6 and
          got["results"][0]["about_same"] is False, got["results"])
    check(got["credit"] == "WoWSims (wowsims.com/forever)" and got.get("link"), "the credit, and the link is shown")
    field = coach_tools.sim_message_link((), ["wowsims_sim"])
    check(field == {"text": "Open in WoWSims", "url": LINK + ARCANE, "credit": "WoWSims (wowsims.com/forever)"},
          field)
    check(coach_tools.sim_message_link((), ["item_info"]) is None, "no sim, no link")
    check(coach_tools.run_item_tool("wowsims_sim", {"items": ["Eye of the Beast"]}, budget_s=11)["said"] == line and
          CALLS[-1]["budget"] == 11.0, "the answer's own budget")

    # within the sims' noise: "about the same as yours"
    got = coach_tools.run_item_tool("wowsims_sim", {"items": "Robe of the Void"})
    check(got["said"] == "WoWSims: Robe of the Void, about the same as yours on your gear." and
          got["results"][0]["about_same"] is True, got)
    check(offline_coach.sim_item_line("Robe of the Void", "Old Robe", 0.4, True) ==
          "WoWSims: Robe of the Void over your Old Robe, about the same as yours on your gear.", "the noise line")
    check(offline_coach.sim_item_line("X", "Y", -3.4) == "WoWSims: X over your Y, about -3 DPS on your gear.", "less")

    # the item worn already, an unknown one, nothing asked
    n = len(CALLS)
    got = coach_tools.run_item_tool("wowsims_sim", {"items": ["Briarwood Reed"]})
    check(got["said"] == "You already wear Briarwood Reed." and len(CALLS) == n, got)
    got = coach_tools.run_item_tool("wowsims_sim", {"items": ["Sword of Nowhere"]})
    check(got["said"] == "I don't know an item called Sword of Nowhere." and len(CALLS) == n, got)
    # a trinket WoWSims knows (not unique) worn in one slot: a second one goes in the other, simmed
    frame(worn=[WORN[0], "13:13968:INVTYPE_TRINKET:4:0:60:3:55:1:Eye of the Beast:", WORN[2], WORN[3]], step=1)
    got = coach_tools.run_item_tool("wowsims_sim", {"items": ["Eye of the Beast"]})
    check(len(CALLS) == n + 1 and got["results"][0]["d"] == 8.2 and not got["results"][0].get("worn"), got)
    frame(step=1)
    check(coach_tools.run_item_tool("wowsims_sim", {}).get("error"), "nothing to sim")

    # "this": the item the player hovers in the game
    hover[0] = 13968
    check(coach_tools.run_item_tool("wowsims_sim", {"items": ["this"]})["said"] == line, "the hovered item")

    # ---- a talent build: its own rotation, its percent of the worn gear's DPS --------------------------------------
    queue.runs.clear()
    got = coach_tools.run_item_tool("wowsims_sim", {"talents": "the Arcane Fire build"})
    v = CALLS[-1]["variants"]
    check(v == [{"kind": "talents", "talents": ARCANE_FIRE, "apl": APL["Arcane Fire"], "name": "Arcane Fire"}], v)
    talent_line = "WoWSims' Arcane Fire build sims about 11% higher on your gear, with its own rotation."
    check(got["said"] == talent_line and got["results"][0]["pct"] == 11.1, got)
    check(len(queue.runs) == 1 and queue.runs[0]["prio"] == PRIO_ASKED and
          queue.runs[0]["req"]["raid"]["parties"][0]["players"][0]["talentsString"] == ARCANE, "the worn gear's run")
    check(coach_tools.sim_message_link((), ["wowsims_sim"])["url"] == LINK + ARCANE_FIRE, "the link: that build")
    coach_tools.run_item_tool("wowsims_sim", {"talents": "Arcane Fire"})
    check(len(queue.runs) == 1, "the worn gear's run is kept per gear and release")
    got = coach_tools.run_item_tool("wowsims_sim", {"talents": "0502-55"})
    check(CALLS[-1]["variants"] == [{"kind": "talents", "talents": "0502-55", "apl": None, "name": None}] and
          got["said"] == "Those talents sim about 4% higher on your gear, with WoWSims' Fire rotation.", got)
    check(coach_tools.run_item_tool("wowsims_sim", {"talents": "Arcane"})["said"] == "That's the build you have.",
          "the build had")
    n = len(CALLS)
    check(coach_tools.run_item_tool("wowsims_sim", {"talents": ARCANE + "0"})["said"] == "That's the build you have."
          and len(CALLS) == n, "the build had, with a trailing zero")
    same = sims.describe(sims._character(), [
        {"label": "Arcane", "d": 0.0, "s": 0.0, "iters": 0, "pct": 0.0, "own_apl": True,
         "variant": {"kind": "talents", "talents": ARCANE, "apl": APL["Arcane"], "name": "Arcane"}},
        {"label": "Briarwood Reed", "d": 0.0, "s": 0.0, "iters": 0, "variant": {"kind": "item", "id": 999}}])
    check([r["about_same"] for r in same] == [True, True] and same[0]["said"] ==
          "WoWSims' Arcane build sims about the same as yours on your gear, with its own rotation." and
          same[1]["said"] == "WoWSims: Robe of the Void, about the same as yours on your gear.", same)
    check(coach_tools._plain_rows([{"label": "x", "d": 0.0, "s": 0.0}])[0]["about_same"] is True, "plain rows too")
    check(coach_tools.run_item_tool("wowsims_sim", {"talents": "Frost Spam build"})["said"] ==
          "WoWSims has no build called Frost Spam build.", "no such build")
    check(coach_tools.run_item_tool("wowsims_sim", {"talents": "Fire Skyborne LT 180"})["said"] ==
          "WoWSims has no build called Fire Skyborne LT 180.", "a Skyborne build is for a Skyborne")
    check(offline_coach.sim_talent_line("FFB", -2.4) ==
          "WoWSims' FFB build sims about 2% lower on your gear, with its own rotation." and
          offline_coach.sim_talent_line("FFB", 0.3, True) ==
          "WoWSims' FFB build sims about the same as yours on your gear, with its own rotation.", "talent lines")

    # ---- sim_compare's own fields, when it gives them: the worn item replaced (and the off hand a two-hander empties),
    # an item worn already, the percent, a talent string without a build of its own (its tree's rotation) -----------
    def rich_compare(char, variants, queue, ws, db=None, budget_s=30, prio=PRIO_ASKED):
        CALLS.append({"char": char["name"], "variants": [dict(v) for v in variants], "budget": budget_s,
                      "prio": prio, "ws": ws, "queue": queue})
        out = []
        for v in variants:
            if v["kind"] == "talents":
                out.append({"label": "those talents", "d": 30.0, "s": 0.5, "iters": 10000, "variant": dict(v),
                            "base": 600.0, "pct": 5.0, "own_apl": False, "talents": v["talents"]})
            elif v["id"] == 999:
                out.append({"label": "item 999", "d": 5.0, "s": 0.4, "iters": 10000, "variant": dict(v),
                            "base": 600.0, "pct": 0.8, "id": 999, "slot": 16, "over": "Old Staff", "over_id": 99999,
                            "also_over": "Old Orb"})
            else:
                out.append({"label": NAMES[v["id"]], "d": 0.0, "s": 0.0, "iters": 0, "variant": dict(v),
                            "id": v["id"], "slot": 1, "over": "item 18000", "over_id": 18000, "worn": True})
        return out

    sims.compare_fn = rich_compare
    check(coach_tools.run_item_tool("wowsims_sim", {"items": ["Robe of the Void"]})["said"] ==
          "WoWSims: Robe of the Void over your Old Staff and Old Orb, about +5 DPS on your gear.", "over, also_over")
    got = coach_tools.run_item_tool("wowsims_sim", {"items": ["Eye of the Beast"]})
    check(got["said"] == "You already wear Eye of the Beast." and got["results"][0].get("about_same") is True, got)
    got = coach_tools.run_item_tool("wowsims_sim", {"talents": "0502-55"})
    check(got["said"] == "Those talents sim about 5% higher on your gear, with WoWSims' Fire rotation." and
          got["results"][0]["pct"] == 5.0, got)
    sims.compare_fn = fake_compare

    # ---- the spoken command, without a model --------------------------------------------------------------------
    words = sims.build_words()
    check({"arcane", "arcane fire", "ffb", "fire", "frost"} <= words, words)
    check(coach_tools.sim_question("sim this") == ("items", ["this"]), "sim this")
    check(coach_tools.sim_question("What if I wear the Eye of the Beast?", sims.is_gear) ==
          ("items", ["Eye of the Beast"]), "wear")
    check(coach_tools.sim_question("What if I wear the Eye of the Beast?") is None, "nothing to tell gear by")
    check(coach_tools.sim_question("how much would Diana's Pearl Necklace give me", sims.is_gear) ==
          ("items", ["Diana's Pearl Necklace"]), "a bag item with an equip location")
    check(coach_tools.sim_question("sim the Arcane Fire build") == ("talents", "Arcane Fire build"), "a build")
    check(coach_tools.sim_question("what if I go Arcane Fire", sims.is_gear, words) == ("talents", "Arcane Fire") and
          coach_tools.sim_question("what if I switch to frost", sims.is_gear, sims.build_words) ==
          ("talents", "frost"), "go")
    check(coach_tools.sim_question("sim Eye of the Beast and Robe of the Void") ==
          ("items", ["Eye of the Beast", "Robe of the Void"]), "two items")
    check(coach_tools.sim_question("where does this drop?") is None and coach_tools.sim_question("similar") is None,
          "not a sim")
    # questions that only look like one: the coach answers them, never "I can't sim that"
    n = len(CALLS)
    for q in ("what if I go to Westfall instead?", "what if I use my hearthstone?", "how much would a mount give me?",
              "what if I went back to town?", "What if I wear a dress?"):
        check(coach_tools.sim_question(q, sims.is_gear, words) is None and coach_tools.sim_command(q, False) is None,
              q)
    check(len(CALLS) == n, "nothing simmed")
    check(coach_tools.sim_command("sim this", True) is None, "the AI answers with the tool")
    said = coach_tools.sim_command("sim this", False)
    check(said == {"text": line, "link": {"text": "Open in WoWSims", "url": LINK + ARCANE,
                                         "credit": "WoWSims (wowsims.com/forever)"}}, said)
    check(coach_tools.sim_command("how far to Ironforge", False) is None, "another question")
    check(coach_tools.sim_command("what if I wear the Eye of the Beast?", False)["text"] == line, "wear, said")
    check(coach_tools.sim_command("what if I go Arcane Fire", False)["text"] == talent_line, "go, said")

    # ---- the addon's /glide sim request: answered at once, spoken, with the link --------------------------------
    hover[0] = None
    req_box.append({"id": 13968, "sfx": 0, "ench": 0, "n": 3})
    sims.tick(background=False)
    check(len(msgs) == 1, msgs)
    m = msgs[0]
    check(m["kind"] == "answer" and m["speak"] is True and m["text"] == line and m["model"] == "WoWSims", m)
    check(m["link"] == {"text": "Open in WoWSims", "url": LINK + ARCANE, "credit": "WoWSims (wowsims.com/forever)"},
          m)
    check(CALLS[-1]["variants"] == [{"kind": "item", "id": 13968, "slot": None, "sfx": None, "ench": None}],
          "the variant simmed")
    sims.tick(background=False)
    check(len(msgs) == 1, "each request once")
    req_box.append({"id": 22403, "sfx": 0, "ench": 1883, "n": 4})
    sims.tick(background=False)
    check(len(msgs) == 2 and CALLS[-1]["variants"][0]["ench"] == 1883 and
          msgs[1]["text"] == "WoWSims: Diana's Pearl Necklace, about +3 DPS on your gear." and msgs[1]["link"],
          msgs[1])

    # ---- Data/Live.lua: the link and the simmed items on the gear worn now; sims.json -----------------------------
    # each with the slot it went in, the worn item there and the other slot of its pair (Eye of the Beast: trinket 1
    # over the Briarwood Reed, the Talisman in trinket 2; the robe, by sim_compare's slot, over the staff; the neck
    # over nothing)
    live = sims.live_section()
    sim = {13968: [8.2, 0, 13, 19990, 18820], 999: [5.0, 0, 16, 18000, 0], 22403: [3.0, 0, 2, 0]}
    check(live == {"Nine": {"link": LINK + ARCANE, "sim": sim}}, live)
    merged = sims_mod.merge_chars({"Nine": {"bis": {226935: ["Head", 1, 8.1]}}, "Ten": {}}, live)
    check(merged == {"Nine": {"bis": {226935: ["Head", 1, 8.1]}, "link": LINK + ARCANE, "sim": sim}}, merged)
    again, _, _ = make()
    check(again.live_section() == live and len(again.data["chars"]["Nine"]["asked"]) >= 5, "kept in data/sims.json")
    # within the sims' noise: 0 (the tooltip's "no change"); another WoWSims release: left out until simmed again
    with sims.lock:
        e = sims.data["chars"]["Nine"]["sim"]["22403"]
        e["d"], e["s"] = 0.8, 0.6
    check(sims.live_section()["Nine"]["sim"][22403] == [0, 0, 2, 0], sims.live_section())
    with sims.lock:
        e["d"], e["s"] = 3.0, 0.3
    sims.ws.tag = "v1.0.5"
    check("sim" not in sims.live_section()["Nine"], "another release: left out")
    sims.ws.tag = "v1.0.4"
    check(sims.live_section() == live, "back")
    check(sims_mod.gear_key(dict(sims._character(), skills=["Enchanting"])) != sims_mod.gear_key(sims._character()),
          "enchanting changes what a sim of the gear is")
    frame(worn=WORN[:3] + ["16:18001:INVTYPE_2HWEAPON:4:0:60:3:55:1:New Staff:IN25"], step=20)
    sims.tick(background=False)
    live = sims.live_section()
    check(live == {"Nine": {"link": LINK + ARCANE}}, ("other gear: its sims go, a new link", live))
    frame(step=20)
    # other gear while WoWSims can't run (updating, broken): the sims still go, so Live.lua never carries them on
    coach_tools.run_item_tool("wowsims_sim", {"items": ["Eye of the Beast"]})
    sims.tick(background=False)
    check(sims.live_section()["Nine"]["sim"] == {13968: [8.2, 0, 13, 19990, 18820]}, sims.live_section())
    sims.ws.tag = None
    frame(worn=WORN[:3] + ["16:18001:INVTYPE_2HWEAPON:4:0:60:3:55:1:New Staff:IN25"], step=20)
    sims.tick(background=False)
    check(sims.data["chars"]["Nine"]["sim"] == {} and "link" not in sims.data["chars"]["Nine"], "dropped, offline")
    sims.ws.tag = "v1.0.4"
    frame(step=20)
    sims.tick(background=False)

    # the answer's link is the gear simmed, never what the player put on while it ran
    def swapping_compare(char, variants, queue, ws, db=None, budget_s=30, prio=PRIO_ASKED):
        st.gear["W"] = [GameState.parse_item(e) for e in
                        [WORN[0], "13:13968:INVTYPE_TRINKET:4:0:60:3:55:1:Eye of the Beast:", WORN[2], WORN[3]]]
        return fake_compare(char, variants, queue, ws, db, budget_s, prio)

    with sims.lock:
        sims.data["chars"]["Nine"].pop("link", None)
    sims.compare_fn, sims.link_fn = swapping_compare, gear_link
    got = sims.simulate(items=["Eye of the Beast"])
    check(got["said"] == line and got["link"] == LINK + "18000-19990-18820-18000", got["link"])
    check(sims.data["chars"]["Nine"]["sim"] == {}, "simmed on gear no longer worn: not for the tooltip")
    sims.compare_fn, sims.link_fn = fake_compare, fake_link
    frame(step=20)
    sims.tick(background=False)

    # a link made while WoWSims' item data isn't in yet (loading, a failed fetch) is never kept: made again with it
    class LateDB(ItemDBSource):
        ready = False

        def __call__(self):
            return FakeDB() if self.ready else None

    home2 = tempfile.mkdtemp(prefix="glide-sims-db-")
    try:
        ldb = LateDB()
        s2, _, _ = make(where=home2, idb=ldb)
        dbs = []
        s2.request_fn = lambda char, rot, iters, seed, db: (dbs.append(db), simbuild.request(char, rot, iters, seed,
                                                                                             None, db=db))[1]
        s2.tick(background=False)
        check(dbs == [] and s2.live_section() == {}, ("no item data: no link made or kept", dbs, s2.live_section()))
        got = s2.simulate(items=["Eye of the Beast"])
        check(got["link"] == LINK + ARCANE and dbs == [None] and "link" not in s2.live_section()["Nine"] and
              "link" not in s2.data["chars"]["Nine"], "an answer's, not kept")
        ldb.ready = True
        clock[0] += sims_mod.CHECK_POLL_S + 1
        s2.tick(background=False)
        check(isinstance(dbs[-1], FakeDB) and s2.live_section()["Nine"]["link"] == LINK + ARCANE, dbs)
    finally:
        shutil.rmtree(home2, ignore_errors=True)

    # ---- what can't be simmed ------------------------------------------------------------------------------------
    check(sims_mod.link_field("https://evil.example/forever/") is None and sims_mod.link_field(None) is None and
          sims_mod.link_field(LINK)["credit"] == "WoWSims (wowsims.com/forever)", "only WoWSims' own links")
    frame(level=59)
    check(coach_tools.run_item_tool("wowsims_sim", {"items": ["Eye of the Beast"]})["say"] ==
          "WoWSims sims level 60 characters only.", "below 60")
    frame()
    off, _, _ = make(opt=Opt(sim_questions=False))
    coach_tools.sims = off
    check(coach_tools.item_tools() == [] and coach_tools.sim_command("sim this", False)["text"] ==
          "Sims on request are off in Glide's settings.", "sim questions off")
    none_ws, _, _ = make(ws=FakeWS(tag=None))
    coach_tools.sims = none_ws
    check(coach_tools.item_tools() == [] and not none_ws.offered(), "no WoWSims runner: no tool")
    check(coach_tools.sim_command("sim this", False) == {"text": "I can't sim that without WoWSims.", "link": None},
          "no WoWSims")
    coach_tools.sims = None
    check(coach_tools.sim_tools() == [] and coach_tools.sim_command("sim this", False)["text"] ==
          "I can't sim that without WoWSims." and
          coach_tools.run_item_tool("wowsims_sim", {"items": ["x"]})["say"] == "I can't sim that without WoWSims.",
          "no sims at all")
    coach_tools.sims = sims

    # ---- the talent check: in the background, each build with its own rotation ------------------------------------
    check(rules_mod.CATEGORY["talent_check"] == "gear" and prompt_context.TRIGGER_TOPIC["talent_check"] == "gear" and
          not rules_mod.allowed("talent_check", {"gear": False}), "a gear callout")
    shutil.rmtree(os.path.join(home, "data"), ignore_errors=True)
    sims, queue, msgs = make()
    coach_tools.sims = sims
    r = Rules({"review_minutes": 120})
    r.sims = sims

    def poll(n=1, step=sims_mod.CHECK_POLL_S + 1, **kw):
        """n polls of the worker (its talent check run here), each a frame later -> the talent_check triggers the
        rules raised meanwhile."""
        out = []
        for _ in range(n):
            frame(step=step, **kw)
            sims.tick(background=False)
            out += [t for t in r.check(st, []) if t.kind == "talent_check"]
        return out

    def settle(**kw):
        """Polls until the talents settled and the check ran."""
        return poll(n=sims_mod.SETTLE_S // (sims_mod.CHECK_POLL_S + 1) + 2, **kw)

    for _ in range(16):
        frame(step=0.1)
        r.check(st, [])                               # (the session starts)
    n = len(CALLS)
    check(poll(2) == [] and len(CALLS) == n, "nothing until the talents settle")
    got = settle()
    check(len(CALLS) == n + 1, CALLS[n:])
    c = CALLS[n]
    check(c["prio"] == PRIO_BIS and c["budget"] is None and sims_mod.TALENT_BUDGET_S is None, ("no deadline", c))
    check(c["variants"] == [{"kind": "talents", "talents": ARCANE_FIRE, "apl": APL["Arcane Fire"],
                             "name": "Arcane Fire"},
                            {"kind": "talents", "talents": FFB, "apl": APL["FFB"], "name": "FFB"}],
          ("the other builds (not its own, no Skyborne one), each with its own rotation", c["variants"]))
    check(len(got) == 1 and got[0].priority == 3 and
          got[0].facts == {"build": "Arcane Fire", "pct": 11.1, "link": LINK + ARCANE_FIRE}, got)
    check(queue.runs and queue.runs[-1]["prio"] == PRIO_BIS and queue.runs[-1]["background"] is True,
          "its worn-gear run in the background too")
    check(offline_coach.callout(got, {"player": {}}) == talent_line, offline_coach.callout(got, {"player": {}}))
    check(coach_tools.sim_message_link(got) == {"text": "Open in WoWSims", "url": LINK + ARCANE_FIRE,
                                                "credit": "WoWSims (wowsims.com/forever)"}, "the callout's link")
    check(poll(3) == [] and len(CALLS) == n + 1, "said once, checked once")

    # a restart: data/sims.json remembers the check and what was told
    sims, queue, msgs = make()
    r = Rules({"review_minutes": 120})
    r.sims = sims
    for _ in range(16):
        frame(step=0.1)
        r.check(st, [])
    check(settle() == [] and len(CALLS) == n + 1, "never again after a restart")

    # a talent change at 60: checked and said again; and back to it later: told already
    TD[FFB] = (90.0, 0.6)                             # (on these talents FFB is the best build)
    got = settle(talents="0502-55")
    check(len(CALLS) == n + 2 and [t.facts["build"] for t in got] == ["FFB"], (got, CALLS[n + 1:]))
    check([x["name"] for x in CALLS[-1]["variants"]] == ["Arcane", "Arcane Fire", "FFB"], "every other build")
    check(settle() == [], "talents back: this release and those talents were told")
    TD[FFB] = (10.0, 0.6)

    # a new WoWSims release: checked and said again
    sims.ws.tag = "v1.0.5"
    got = settle()
    check(len(CALLS) == n + 3 and [t.facts["build"] for t in got] == ["Arcane Fire"], got)

    # only at 3% or more, and only beyond 2 sigma
    TD[ARCANE_FIRE], TD[FFB] = (15.0, 0.6), (10.0, 0.6)      # 2.4%
    sims.ws.tag = "v1.0.6"
    check(settle() == [] and len(CALLS) == n + 4, "under 3%")
    TD[ARCANE_FIRE] = (70.0, 40.0)                             # 11%, but within 2 sigma
    sims.ws.tag = "v1.0.7"
    check(settle() == [] and len(CALLS) == n + 5, "within the noise")
    check(sims.talent_check(st.summary()) is None, "nothing to say")

    # builds that didn't finish in time (the queue waited for the player): never kept as checked, tried again after
    # RETRY_S however often; WoWSims failing gives up after GIVE_UP
    TD[ARCANE_FIRE] = (70.0, 0.6)

    def late_compare(char, variants, queue, ws, db=None, budget_s=30, prio=PRIO_ASKED):
        CALLS.append({"char": char["name"], "variants": variants, "budget": budget_s, "prio": prio})
        raise sim_compare.Late("WoWSims didn't finish (1 of 2 left out)")

    sims.compare_fn = late_compare
    sims.ws.tag = "v1.0.75"
    k = len(CALLS)
    check(settle() == [] and len(CALLS) == k + 1, "nothing said")
    ck = "v1.0.75|" + ARCANE
    for _ in range(sims_mod.GIVE_UP + 1):
        poll(step=sims_mod.RETRY_S + 1)
    check(len(CALLS) == k + sims_mod.GIVE_UP + 2 and sims.failed[ck]["n"] == 0 and
          ck not in sims.data["chars"]["Nine"]["checks"], ("tried again, never given up", sims.failed))
    sims.compare_fn = fake_compare
    got = poll(step=sims_mod.RETRY_S + 1) + poll(2)
    check([t.facts["build"] for t in got] == ["Arcane Fire"] and ck not in sims.failed, got)

    def broken_compare(char, variants, queue, ws, db=None, budget_s=30, prio=PRIO_ASKED):
        CALLS.append({"char": char["name"], "variants": variants, "budget": budget_s, "prio": prio})
        raise simbuild.SimError("WoWSims couldn't sim: broken")

    sims.compare_fn = broken_compare
    sims.ws.tag = "v1.0.76"
    k = len(CALLS)
    settle()
    for _ in range(sims_mod.GIVE_UP + 1):
        poll(step=sims_mod.RETRY_S + 1)
    check(len(CALLS) == k + sims_mod.GIVE_UP and sims.failed["v1.0.76|" + ARCANE]["n"] == sims_mod.GIVE_UP,
          ("WoWSims failing: given up", len(CALLS) - k, sims.failed))
    sims.compare_fn = fake_compare

    # held while gear talk is muted, untold; said once it isn't
    TD[ARCANE_FIRE] = (70.0, 0.6)
    sims.ws.tag = "v1.0.8"
    gear_off = strip_spec.ALL_FLAGS_ON & ~next(f.bit for f in strip_spec.COACH_FLAGS if f.name == "gear")
    check(settle(cf=gear_off) == [], "muted")
    check(sims.talent_check(st.summary()) is not None, "kept, untold")
    got = poll()
    check([t.facts["build"] for t in got] == ["Arcane Fire"], got)

    # held in combat; never below 60; gear.talent_check off: no check
    sims.ws.tag = "v1.0.9"
    check(settle(combat=1) == [], "held in combat")
    check([t.kind for t in poll()] == ["talent_check"], "said out of combat")
    sims.opt.v["talent_check"] = False
    sims.ws.tag = "v1.0.10"
    k = len(CALLS)
    check(settle() == [] and len(CALLS) == k, "talent_check off: nothing simmed or said")
    sims.opt.v["talent_check"] = True

    # ---- the settings, in the manifest -------------------------------------------------------------------------------
    for key, want in (("gear.sim_questions", True), ("gear.talent_check", True), ("gear.sim_seconds", 30)):
        check(key in settings.ENTRIES and settings.default(key) == want, (key, settings.ENTRIES.get(key)))

    # no em dash in what the player hears
    for text in (line, talent_line, offline_coach.NO_WOWSIMS, sims_mod.NOT_60, sims_mod.NO_CLASS, sims_mod.NOT_IN,
                 sims_mod.OFF, sims_mod.FAILED, sims_mod.NOT_PLAYING, sims_mod.CREDIT, sims_mod.LINK_TEXT):
        check("—" not in text and "–" not in text, text)
finally:
    rules_mod.time = real_time
    coach_tools.sims, coach_tools.research, coach_tools.bis, coach_tools._last_link = saved
    shutil.rmtree(home, ignore_errors=True)
print("OK")
