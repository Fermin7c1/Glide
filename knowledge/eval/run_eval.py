#!/usr/bin/env python3
"""Eval runner for the coach's question path: knowledge block + tools + FACTS grounding, scored against
knowledge/eval/questions.jsonl.

  python3 knowledge/eval/run_eval.py --dry --limit 5        print the packs and the tool definitions (no API, no key)
  python3 knowledge/eval/run_eval.py --limit 20             ask Claude (typed path, deep model), score, write a report
  options: --model fast|deep   fast = a push-to-talk question (fast model, 2 tool calls, 12 s, 25 words),
                               deep = a typed question (deep model, 6 tool calls, 40 s, 60 words; default)
           --ids log-01 fact-02 ...  only these questions        --judge  Haiku grades each answer vs gold.facts (0-2)
           --judge-model M (default claude-haiku-4-5-20251001)    --out DIR (default data/eval)
           --api-url URL  another Messages API endpoint (a proxy, or a local test server)

State per question:
  log-*   the referenced companion log (source "data/live/<file>.jsonl#L<n>") is replayed up to line n through the
          companion's GameState on the log's own clock (core frames + pages: guide, step, quests, position, target),
          so the state is the one at the nearest preceding core frame; the log's coach messages and triggers become
          the coach's recent log. Missing file: the synthetic state below.
  others  synthetic, from the context (class, level, zone, situation): the RestedXP guide that covers the zone at
          that level for the class's faction (analysed for the class), the route step whose quests the situation
          names (else the first step planned at that level), that step's position, the quests the route has open
          there (complete when their objective steps are behind), and a few situation cues (hearth cooldown, bind,
          combat, rested).
Ask: the companion Coach with offline_ok=True runs the real question path (context + knowledge block, the tool loop,
the FACTS line and the grounding check; its grounding log goes to <out>/grounding-<ts>.jsonl).
Score: must_mention (case-insensitive substrings, irregular plurals too: wolf ~ wolves; a number also matches any
number in the answer within 10%),
must_not (none may appear), FACTS line present, unknown fact ids, words, tool calls, usage and an estimated cost.
Pass = every must_mention and no must_not. Stops when the coach's billing guard trips (out of credits, bad key).
Writes <out>/<timestamp>.json and prints a summary table by question type.
"""
import argparse
import copy
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
COMPANION = os.path.join(ROOT, "companion")
sys.path.insert(0, ROOT)                 # `knowledge` = the package; companion modules come after it
sys.path.append(COMPANION)

from knowledge import api, store  # noqa: E402
from knowledge.vocab import COACH_SAID  # noqa: E402
from knowledge.analyzer import route as R  # noqa: E402
from knowledge.analyzer.model import LEVEL_XP, MAX_LEVEL  # noqa: E402
import coach as C  # noqa: E402  (companion/coach.py)
import models  # noqa: E402  (companion/models.py: the price table)
import settings  # noqa: E402  (companion/settings.py: the engine's defaults)
import glideconfig  # noqa: E402  (companion/glideconfig.py: config.json)
from glidedb import GlideDB  # noqa: E402  (companion/glidedb.py)
from state import GameState  # noqa: E402  (companion/state.py)

QUESTIONS = os.path.join(HERE, "questions.jsonl")
KB_PATH = os.path.join(ROOT, "data", "kb.db")
GLIDE_DB = os.path.join(ROOT, "data", "glide.db")
JUDGE_MODEL = "claude-haiku-4-5-20251001"
HORDE_ZONES = ("durotar", "mulgore", "tirisfal", "the barrens", "silverpine", "orgrimmar", "thunder bluff",
               "undercity")
# words in a question / situation -> the step kind it is about (synthetic states pick such a step)
KIND_WORDS = (("hearth", "hs"), ("deathskip", "deathskip"), ("die and", "deathskip"), ("flight", "fly"),
              ("fly ", "fly"), ("trainer", "train"), ("train ", "train"), ("vendor", "vendor"), ("turn in", "turnin"),
              ("turn-in", "turnin"), ("grind", "grind"), ("dungeon", "dungeon"))
FLAGS_ON = {"coach": True, "route": True, "combat": True, "buffs": True, "gear": True, "pace": True, "upkeep": True,
            "voice": True}


# ------------------------------------------------------------------------------------------------------
# setup
# ------------------------------------------------------------------------------------------------------
def llm_config():
    """The engine's default "llm" settings (companion/settings_manifest.json) + config.json "companion"."llm"."""
    cfg = settings.defaults()["llm"]
    user = glideconfig.read(ROOT)[0].get("companion")
    user = user if isinstance(user, dict) else {}
    cfg.update(user.get("llm") if isinstance(user.get("llm"), dict) else {})
    return cfg, user.get("rxp")


def load_questions(ids=None, limit=None):
    qs = []
    with open(QUESTIONS, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                qs.append(json.loads(line))
    if ids:
        want = set(ids)
        qs = [q for q in qs if q["id"] in want]
    return qs[:limit] if limit else qs


# ------------------------------------------------------------------------------------------------------
# states
# ------------------------------------------------------------------------------------------------------
class ReplayState:
    """A GameState replayed from a companion log, with the time-dependent summary fields on the log's clock."""

    def __init__(self, st, now, t0):
        self.st, self.now, self.t0 = st, now, t0

    def __getattr__(self, k):
        return getattr(self.st, k)

    def summary(self):
        s = self.st.summary()
        s["session_minutes"] = int(max(0, self.now - self.t0) / 60)
        s["on_step_seconds"] = int(max(0, self.now - self.st.step_since))
        s["fresh"] = True
        return s

    def player_geo(self):
        return self.st.player_geo()


class SynthState:
    """A GameState stand-in built from a summary dict."""

    def __init__(self, summary, now=None):
        self.s, self.now = summary, now or time.time()
        self.core, self.pages = {}, {}

    def summary(self):
        return copy.deepcopy(self.s)

    def player_geo(self):
        s = self.s
        if s.get("map") and s.get("x") is not None:
            return {"map": s["map"], "x": s["x"], "y": s["y"], "facing": None, "map_size": None}
        return None


def replay(source):
    """(ReplayState, recent coach log entries, recent coach lines) or None when the log is missing."""
    m = re.match(r"^(.+?)#L(\d+)$", source or "")
    if not m:
        return None
    path = os.path.join(ROOT, m.group(1))
    upto = int(m.group(2))
    if not os.path.exists(path):
        return None
    st = GameState()
    last_core, t0, now, early = None, None, None, []
    recent, lines = [], []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            if i > upto:
                break
            try:
                o = json.loads(line)
                t = float(o.get("t") or 0) or None
            except (ValueError, TypeError):
                continue
            if t:
                now = t
                t0 = t0 or t
            if isinstance(o.get("core"), dict):
                last_core = o["core"]
                for pg in early:                       # pages logged before the first core frame
                    st.update(last_core, pg, now=t)
                early = []
                st.update(last_core, {}, now=t)
            elif isinstance(o.get("page"), dict):
                if last_core is None:
                    early.append(o["page"])
                else:
                    st.update(last_core, o["page"], now=t)
            elif isinstance(o.get("msg"), dict):
                mm = o["msg"]
                if mm.get("kind") in COACH_SAID and mm.get("text"):
                    recent.append({"t": t, "type": "message", "kind": mm["kind"], "text": mm["text"]})
                    lines.append(mm["text"])
                elif mm.get("kind") == "question" and mm.get("text"):
                    recent.append({"t": t, "type": "question", "kind": "typed", "text": mm["text"]})
            elif isinstance(o.get("triggers"), list):
                for k in o["triggers"]:
                    if isinstance(k, (list, tuple)) and k:
                        recent.append({"t": t, "type": "trigger", "kind": k[0],
                                       "facts": json.dumps(k[1] if len(k) > 1 else {}, default=str)[:300]})
    if last_core is None:
        return None
    return ReplayState(st, now or time.time(), t0 or now or time.time()), recent[-40:], lines[-10:]


class Synth:
    """Synthetic states from a question's context."""

    def __init__(self, kb, pack):
        self.kb, self.pack = kb, pack
        self.data = pack.data
        self._zones = None

    def zones(self):
        if self._zones is None:
            z = {}
            for r in self.kb.q("SELECT guide_key, zone, COUNT(*) AS n FROM rxp_step WHERE zone IS NOT NULL "
                               "GROUP BY guide_key, zone"):
                z.setdefault(r["guide_key"], {})[(r["zone"] or "").lower()] = r["n"]
            self._zones = z
        return self._zones

    def pick_guide(self, zone, level, cls, faction, situation):
        zl = (zone or "").lower()
        sit = (situation or "").lower()
        mine = self.pack.class_guides(cls)
        best = None
        for key, row in self.data.guide_rows.items():
            if key not in mine:
                continue
            grp = (row.get("grp") or "")
            if row.get("faction") not in (None, faction):
                continue
            if "dungeon" in grp.lower() and "dungeon" not in sit:
                continue
            lo, hi = row.get("level_min"), row.get("level_max")
            if lo is None or hi is None:
                continue
            zn = self.zones().get(key, {}).get(zl, 0) + (50 if zl and zl in (row.get("name") or "").lower() else 0)
            if not zn:
                continue
            gap = 0 if lo <= level <= hi else min(abs(level - lo), abs(level - hi))
            special = any(w in (row.get("name") or "") + grp for w in ("AoE", "ADV", "LAUNCH", "SoD", "Hunter",
                                                                         "Class Quests"))
            aoe_ok = cls == "MAGE" and "aoe" in sit
            rank = (gap > 1, special and not aoe_ok, gap, -zn, key)
            if best is None or rank < best[0]:
                best = (rank, key)
        return best[1] if best else None

    def pick_step(self, route, level, situation, zone):
        sit = (situation or "").lower()
        exp = {r["idx"]: r["exp_level"] for r in self.kb.q(
            "SELECT idx, exp_level FROM step_brief WHERE guide_key=? AND class=?", route.guide_key,
            route.player["class"])}
        zl = (zone or "").lower()
        rows = {r["idx"]: (r["zone"] or "").lower() for r in self.kb.q(
            "SELECT idx, zone FROM rxp_step WHERE guide_key=?", route.guide_key)}
        named = []
        for st in route.steps:
            qs = st["quests"]
            for kind in ("turnin", "objective", "accept"):
                for q in qs[kind]:
                    n = ((self.data.quest(q) or {}).get("name") or "").lower()
                    if len(n) > 4 and n in sit:
                        named.append(({"turnin": 0, "objective": 1, "accept": 2}[kind],
                                      abs((exp.get(st["idx"]) or level) - level), st["idx"]))
        if named:
            return min(named)[2]
        cands = [st["idx"] for st in route.steps if not zl or rows.get(st["idx"]) == zl] or \
            [st["idx"] for st in route.steps]
        kinds = {k for w, k in KIND_WORDS if w in sit}
        if kinds:                                   # "why hearth now?" -> a hearth step near this level
            for pool in (cands, [st["idx"] for st in route.steps]):     # (a hearth step may sit in another zone)
                hits = [i for i in pool if route.by_idx[i]["kind"] in kinds]
                if hits:
                    return min(hits, key=lambda i: (abs((exp.get(i) or 0) - level), i))
        for i in cands:
            if (exp.get(i) or 0) >= level - 0.05:
                return i
        return min(cands, key=lambda i: abs((exp.get(i) or 0) - level))

    def _route_log(self, route, idx, situation=""):
        """Quests the route has open at step idx: accepted before it and not yet turned in; complete when their
        objective steps are behind (or this step turns them in). "N quests complete" in the situation wins."""
        st = route.by_idx[idx]
        here_i = route.index_of(idx)
        log = {}
        for q, a in route.accept_idx.items():
            t = route.turnin_idx.get(q)
            if route.index_of(a) >= here_i or (t is not None and route.index_of(t) < here_i):
                continue
            objs = [x["idx"] for x in route.steps if q in x["quests"]["objective"]]
            done = (not objs or max(route.index_of(i) for i in objs) < here_i) or q in st["quests"]["turnin"]
            log[q] = {"done": bool(done), "obj": [] if done else [(0, 1)]}
        m = re.search(r"(\d+) quests? (?:are )?complete", (situation or "").lower())
        if m:
            n_done = int(m.group(1))
            ordered = sorted(log, key=lambda q: route.index_of(route.turnin_idx.get(q, idx)))
            for i, q in enumerate(ordered):
                log[q] = {"done": i < n_done, "obj": [] if i < n_done else [(0, 1)]}
        return dict(list(log.items())[:20])

    @staticmethod
    def _s0(route, idx, pos):
        st = route.by_idx[idx]
        els = [{"tag": "goto", "map": pos[0], "x": pos[1], "y": pos[2]}] if pos else []
        for kind, tag in (("turnin", "turnin"), ("accept", "accept"), ("objective", "complete")):
            for q in st["quests"][kind]:
                els.append({"tag": tag, "quest": q, "done": False})
        return [{"index": st["rxp_step_no"], "text": (st.get("text") or "").split("\n")[0][:120], "elements": els}]

    @staticmethod
    def _player(cls, zone, situation):
        faction = "Horde" if any(z in (zone or "").lower() for z in HORDE_ZONES) or "horde" in situation.lower() \
            else "Alliance"
        race = R.DEFAULT_RACE.get(faction, R.DEFAULT_RACE["Alliance"]).get(cls, "Human")
        if zone == "Zephras Isle" or "skyborne" in situation.lower():
            race = "Skyborne"
        return race, faction

    def state(self, item):
        c = item.get("context") or {}
        cls = str(c.get("class") or "WARRIOR").upper()
        level = max(1, min(MAX_LEVEL, int(c.get("level") or 1)))
        zone = c.get("zone") or ""
        sit = c.get("situation") or ""
        race, faction = self._player(cls, zone, sit)
        xmax = LEVEL_XP[level] if level < MAX_LEVEL else 0
        s = {"name": "Evaltester", "class": cls, "race": race, "faction": faction, "level": level,
             "xp": int(xmax * 0.3), "xp_max": xmax, "rested": 0, "hp": 100, "power": 100, "power_type": None,
             "combat": False, "dead": 0, "zone": zone, "subzone": None, "map": None, "x": None, "y": None,
             "guide": None, "guide_group": None, "step": None, "steps": [], "quests": {},
             "coach_flags": dict(FLAGS_ON), "xp_per_hour": None, "ttl_seconds": None, "xp_session": 0,
             "session_minutes": 30, "on_step_seconds": 60, "buffs": [], "buffs_hidden": False, "bind": None,
             "hearth_cd": 0, "money": 5000 * level, "free_bags": 8, "durability": 90, "kills": 0, "target": None,
             "mounted": False, "on_taxi": False}
        low = sit.lower()
        m = re.search(r"cooldown\s*(\d+)\s*min", low)
        if m:
            s["hearth_cd"] = int(m.group(1)) * 60
        m = re.search(r"bound (?:at|in) ([A-Z][\w' ]+?)(?:,|;|$)", sit)
        if m:
            s["bind"] = m.group(1).strip()
        if "in combat" in low:
            s["combat"] = True
        if "rested" in low and "no rested" not in low:
            s["rested"] = xmax // 2
        player = {"class": cls, "race": race, "faction": faction, "ssf": True, "group_quests": True}
        gk = self.pick_guide(zone, level, cls, faction, sit)
        if gk is None:
            return SynthState(s), None
        route = R.get_route(self.kb, gk, player)
        idx = self.pick_step(route, level, "%s %s" % (sit, item.get("question") or ""), zone)
        row = self.data.guide_rows.get(gk) or {}
        s["guide"] = api.RP.affix_name(row.get("name") or "")        # the G page shows RestedXP's padded name
        s["guide_group"] = row.get("grp")
        s["step"] = route.by_idx[idx]["rxp_step_no"]
        pos = route.by_idx[idx].get("pos") or route.next_pos(idx) or route.prev_pos(idx)
        if pos:
            s["map"], s["x"], s["y"] = pos
        s["quests"] = self._route_log(route, idx, sit)
        s["steps"] = self._s0(route, idx, pos)
        return SynthState(s), gk

    def complete(self, item, s, t):
        """A replayed summary with its gaps filled (early logs have core frames only): class, race, faction and
        guide from the recorder session overlapping the log time (obs_session), else the question's context;
        the quest log and the S0 page from the route at the replayed step. None when nothing is missing."""
        if s.get("class") and s.get("race") and s.get("guide") and s.get("quests"):
            return None
        c = item.get("context") or {}
        out = dict(s)
        sess = self.kb.one("""SELECT char, class, race, faction, guide FROM obs_session WHERE started <= ?
                              AND COALESCE(ended, started) + 900 >= ? ORDER BY started DESC LIMIT 1""", t, t) or {}
        cls = str(s.get("class") or sess.get("class") or c.get("class") or "WARRIOR").upper()
        race, faction = self._player(cls, c.get("zone"), c.get("situation") or "")
        out.update(**{"class": cls, "race": s.get("race") or sess.get("race") or race,
                      "faction": s.get("faction") or sess.get("faction") or faction,
                      "name": s.get("name") or sess.get("char") or "Evaltester"})
        if not out.get("zone"):
            out["zone"] = c.get("zone") or self.data.map_name(out.get("map"))
        if not out.get("coach_flags"):
            out["coach_flags"] = dict(FLAGS_ON)
        if not out.get("guide") and sess.get("guide"):
            out["guide"] = sess["guide"]
            out["guide_group"] = "RestedXP Forever Guide (%s)" % ("H" if out["faction"] == "Horde" else "A")
        if not out.get("guide"):                               # no guide known: the context's guide and step
            base, _ = self.state(dict(item, context=dict(c, **{"class": cls, "level": out.get("level") or
                                                                 c.get("level")})))
            b = base.summary()
            for k in ("guide", "guide_group", "step", "steps", "quests", "map", "x", "y"):
                if b.get(k) is not None and (k in ("guide", "guide_group", "step", "steps", "quests")
                                             or out.get(k) is None):
                    out[k] = b[k]
            return out
        gk = self.pack.guide_key(out["guide"], out.get("guide_group"), out["faction"])
        if gk is None:
            return out
        player = {"class": cls, "race": out["race"], "faction": out["faction"], "ssf": True, "group_quests": True}
        route = R.get_route(self.kb, gk, player)
        idx = route.rxp_to_idx.get(out.get("step") or -1)
        if idx is None:
            return out
        pos = route.by_idx[idx].get("pos")
        if not out.get("quests"):
            out["quests"] = self._route_log(route, idx, c.get("situation") or "")
        if not [x for x in out.get("steps") or [] if x]:
            out["steps"] = self._s0(route, idx, pos or (out.get("map"), out.get("x"), out.get("y")))
        return out


# ------------------------------------------------------------------------------------------------------
# scoring
# ------------------------------------------------------------------------------------------------------
_NUM = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")


def _numbers(text):
    out = []
    for m in _NUM.finditer(text or ""):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    return out


def _forms(m):
    """The term and its irregular plural ('wolf' -> 'wolves', 'knife' -> 'knives', 'body' -> 'bodies')."""
    t = m.lower()
    out = [t]
    if t.endswith("fe"):
        out.append(t[:-2] + "ves")
    elif t.endswith("f"):
        out.append(t[:-1] + "ves")
    elif t.endswith("y") and len(t) > 2 and t[-2] not in "aeiou":
        out.append(t[:-1] + "ies")
    return out


def mention_ok(m, text):
    """Case-insensitive substring (or its irregular plural); a numeric must_mention ('195', '5%', '1,250') also
    matches any number in the answer within 10%."""
    low = (text or "").lower()
    if any(f in low for f in _forms(m)):
        return True
    mm = re.fullmatch(r"\s*(-?\d[\d,]*(?:\.\d+)?)\s*%?\s*", m)
    if not mm:
        return False
    want = float(mm.group(1).replace(",", ""))
    return any(abs(x - want) <= 0.1 * abs(want) for x in _numbers(text)) if want else False


def score(item, text, facts, unknown, had_facts):
    gold = item.get("gold") or {}
    must = gold.get("must_mention") or []
    hits = [m for m in must if mention_ok(m, text)]
    bad = [m for m in gold.get("must_not") or [] if m.lower() in (text or "").lower()]
    return {"pass": bool(text) and len(hits) == len(must) and not bad, "must_hit": hits,
            "must_missing": [m for m in must if m not in hits], "must_not_hit": bad, "facts_line": bool(had_facts),
            "facts": facts, "unknown_ids": unknown, "words": len((text or "").split())}


def price(model, usage):
    # an estimate: the engine's Anthropic list prices; a 5-minute cache write is 1.25 x input for every model
    p = models.anthropic_price(model) or {"in": 2.0, "out": 10.0, "cache_read": 0.20}
    pin, pout, pread = p["in"], p["out"], p["cache_read"]
    u = usage or {}
    return ((u.get("input_tokens") or 0) * pin + (u.get("cache_read_input_tokens") or 0) * pread
            + (u.get("cache_creation_input_tokens") or 0) * pin * 1.25 + (u.get("output_tokens") or 0) * pout) / 1e6


def judge(coach, model, item, text):
    """Haiku grades the answer against gold.facts: 2 right and covers them, 1 partly, 0 wrong or misses."""
    prompt = ("Grade a World of Warcraft leveling coach's answer against reference facts.\nQuestion: %s\n"
              "Reference facts:\n- %s\nAnswer: %s\n\nScore 2 if the answer is correct and covers the key reference "
              "facts, 1 if partly correct or incomplete, 0 if wrong, contradicting the facts, or not answering. "
              "Reply with JSON only: {\"score\": 0|1|2, \"why\": \"<10 words>\"}"
              % (item["question"], "\n- ".join(item["gold"].get("facts") or []), text or "(no answer)"))
    coach.llm.last_usage = {}
    data, _ = coach.llm.post({"model": model, "max_tokens": 200, "messages": [{"role": "user", "content": prompt}]}, {}, "fast")
    if data is None:
        return None, None, {}
    out = "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
    m = re.search(r"\{.*\}", out, re.S)
    try:
        j = json.loads(m.group(0)) if m else {}
        return int(j.get("score")), j.get("why"), dict(coach.llm.last_usage)
    except (ValueError, TypeError):
        d = re.search(r"[012]", out)
        return (int(d.group(0)) if d else None), out[:80], dict(coach.llm.last_usage)


# ------------------------------------------------------------------------------------------------------
# main
# ------------------------------------------------------------------------------------------------------
def build(coach, synth, item):
    """Install the question's state on the coach -> (state, how, guide key)."""
    coach.recent.clear()
    coach.history = []
    coach.clock = None
    rp = replay(item.get("source")) if str(item.get("id", "")).startswith("log") else None
    if rp is not None:
        st, recent, lines = rp
        coach.recent.extend(recent)
        coach.history = lines[-5:]
        coach.clock = (lambda t=st.now: t)
        merged = synth.complete(item, st.summary(), st.now)
        if merged is not None:
            st = SynthState(merged, st.now)
        coach.state = st
        return st, "replay" if merged is None else "replay+context", None
    st, gk = synth.state(item)
    coach.state = st
    return st, "synthetic", gk


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--model", choices=("fast", "deep"), default="deep")
    ap.add_argument("--dry", action="store_true", help="print packs and tool definitions; no API calls")
    ap.add_argument("--ids", nargs="+")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--judge-model", default=JUDGE_MODEL)
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "eval"))
    ap.add_argument("--api-url", help="Messages API endpoint (default: the companion's; e.g. a proxy or a test server)")
    a = ap.parse_args(argv)
    items = load_questions(a.ids, a.limit)
    if not items:
        print("no questions selected")
        return 1
    kb = store.open_ro(KB_PATH)
    if not kb.ok:
        print("knowledge base missing (%s): run build-kb.cmd" % KB_PATH)
        return 1
    cfg, rxp = llm_config()
    if a.api_url:
        cfg["api_url"] = a.api_url
    ts = time.strftime("%Y%m%d-%H%M%S")
    if not a.dry:
        os.makedirs(a.out, exist_ok=True)
    cfg["grounding_log"] = os.path.join(a.out, "grounding-%s.jsonl" % ts) if not a.dry else ""
    coach = C.Coach(cfg, GlideDB(GLIDE_DB), lambda m: None, kb=kb, offline_ok=True, rxp=rxp)
    if coach.pack is None:
        print("knowledge layer unavailable: %s" % coach.kb_reason)
        return 1
    synth = Synth(kb, coach.pack)
    voice = a.model == "fast"
    model = cfg["fast_model"] if voice else cfg["deep_model"]
    if a.dry:
        for it in items:
            st, how, gk = build(coach, synth, it)
            s = st.summary()
            ctx = coach.context(deep=not voice, triggers=["question"])
            k = ctx.get("knowledge") or {}
            print("=" * 100)
            print("%s [%s] %s" % (it["id"], it.get("type"), it["question"]))
            print("state: %s | %s L%s %s | guide %s / %s step %s | map %s %s,%s | %d quests in log" % (
                how, s.get("class"), s.get("level"), s.get("zone"), s.get("guide_group"), s.get("guide"),
                s.get("step"), s.get("map"), s.get("x"), s.get("y"), len(s.get("quests") or {})))
            print("pack (%d tokens):" % api.approx_tokens(k))
            print(json.dumps(k, indent=1, ensure_ascii=False))
        defs = api.tool_definitions()
        print("=" * 100)
        print("TOOLS (%d definitions, ~%d tokens):" % (len(defs), api.approx_tokens(defs)))
        print(json.dumps(defs, indent=1))
        print("\ndry run: %d question(s), model would be %s (%s path); no API calls made" % (
            len(items), model, "voice" if voice else "typed"))
        return 0
    if not coach.enabled:
        print("no API key (%s); use --dry to inspect the packs" % cfg.get("api_key_env", "ANTHROPIC_API_KEY"))
        return 1
    rows, stopped = [], None
    for n, it in enumerate(items, 1):
        st, how, gk = build(coach, synth, it)
        res = coach.answer_once(it["question"], voice=voice)
        if coach.suspended:                         # the billing guard tripped: this answer is the offline one
            stopped = coach.suspended
            print("stopping at %s: %s (billing guard)" % (it["id"], stopped))
            break
        msg = res.get("answer") or {}
        offline = str(msg.get("model") or "").startswith("offline")
        text = "" if offline else (msg.get("text") or "")
        sc = score(it, text, msg.get("facts") or [], msg.get("facts_unknown") or [],
                   bool(text) and not msg.get("facts_missing"))
        row = {"id": it["id"], "type": it.get("type"), "kind": it["id"].split("-")[0], "question": it["question"],
               "state": how, "guide": gk or (st.summary().get("guide")), "answer": text, "model": msg.get("model"),
               "tool_calls": msg.get("tool_calls", 0), "tools": msg.get("tools") or [], "latency_s": res["latency_s"],
               "usage": res.get("usage") or {}, "cost_usd": round(price(model, res.get("usage")), 5), **sc}
        if a.judge and text:
            j, why, ju = judge(coach, a.judge_model, it, text)
            row.update(judge=j, judge_why=why, judge_cost_usd=round(price(a.judge_model, ju), 5))
        if offline:
            row["offline"] = True
        rows.append(row)
        print("%3d/%d %-11s %s  words %3d  tools %d  facts %s%s  %s" % (
            n, len(items), it["id"], "PASS" if row["pass"] else "fail", row["words"], row["tool_calls"],
            "ok" if row["facts_line"] and not row["unknown_ids"] else ("unknown %s" % row["unknown_ids"]
                                                                       if row["unknown_ids"] else "missing"),
            ("  judge %s" % row.get("judge")) if a.judge else "", (text or "(no answer)")[:90].replace("\n", " ")))
        if coach.suspended:
            stopped = coach.suspended
            print("stopping: %s (billing guard)" % stopped)
            break
    if not rows:
        print("no answers%s" % (": %s" % stopped if stopped else ""))
        return 1
    summary = summarize(rows, a.judge)
    out = {"t": ts, "model": model, "path": "voice" if voice else "typed", "n": len(rows), "stopped": stopped,
           "judge_model": a.judge_model if a.judge else None, "summary": summary, "items": rows}
    path = os.path.join(a.out, "%s.json" % ts)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    print_table(summary)
    print("report: %s" % path)
    return 0


def summarize(rows, with_judge=False):
    groups = {}
    for r in rows:
        for g in (r["kind"], "ALL"):
            groups.setdefault(g, []).append(r)
    out = {}
    for g, rs in groups.items():
        n = len(rs)
        e = {"n": n, "pass_rate": round(sum(r["pass"] for r in rs) / n, 3),
             "unsupported_rate": round(sum(1 for r in rs if r["unknown_ids"] or not r["facts_line"]) / n, 3),
             "avg_words": round(sum(r["words"] for r in rs) / n, 1),
             "avg_tool_calls": round(sum(r["tool_calls"] for r in rs) / n, 2),
             "cost_usd": round(sum(r["cost_usd"] + r.get("judge_cost_usd", 0) for r in rs), 4),
             "avg_latency_s": round(sum(r["latency_s"] for r in rs) / n, 2)}
        if with_judge:
            js = [r["judge"] for r in rs if r.get("judge") is not None]
            e["judge_avg"] = round(sum(js) / len(js), 2) if js else None
        out[g] = e
    return out


def print_table(summary):
    cols = ("n", "pass_rate", "unsupported_rate", "avg_words", "avg_tool_calls", "avg_latency_s", "cost_usd")
    has_j = any("judge_avg" in v for v in summary.values())
    hdr = "%-8s" % "type" + "".join("%17s" % c for c in cols) + ("%11s" % "judge" if has_j else "")
    print("\n" + hdr)
    print("-" * len(hdr))
    for g in sorted(summary, key=lambda k: (k == "ALL", k)):
        v = summary[g]
        print("%-8s" % g + "".join("%17s" % (("%.0f%%" % (v[c] * 100)) if c.endswith("rate") else v[c])
                                   for c in cols) + ("%11s" % v.get("judge_avg") if has_j else ""))


if __name__ == "__main__":
    sys.exit(main())
