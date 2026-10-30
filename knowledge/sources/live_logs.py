"""Loader: companion session logs (data/live/*.jsonl) -> obs_step_time; obs_session.guide / play_s where NULL.

Log lines (companion/app.py), `t` = epoch seconds:
  {"core": {...}}   about once a second: l level, x xp, X xp_max, st RXP step number (RXPCData.currentStep),
                    d 0 alive / 1 dead / 2 ghost, cl class token (newer logs), mp/px/py position
  {"page": {...}}   whenever a page's content changes: G (gn RXP's guide name, zero-padded '05-11 Dun Morogh';
                    gg group; gx next), B (nm name, cl, ra race, fa faction), S0..S2 (i RXP step index, el
                    elements 'tag:questId[!]', tx text); older logs have no pages
  {"triggers": ...}, {"msg": ...}   not used here

Pipeline
  1. Frames split into character segments at level drops, class changes, B page name changes and gaps
     > 10 min. A segment with two level-ups less than a minute apart is a simulator run (app.py --sim): skipped.
  2. Session per frame: the obs_session (recorder) row of the same character (name / class when the log
     knows them, level within the session's range +-1) whose [started, ended] holds the frame, else the
     nearest one within 2 min; else that character's session which ended before the frame, when the segment
     began at most 15 min after it ended (the recorder's last save came before the companion stopped). Other
     frames keep the session id "live:<log name>" (no obs_session row is made for them).
  3. Guide per frame: the G page in effect; a change applies from the step-number drop it trails (the page
     follows the core by a second or two). Logs without pages use the recorder's turn-ins (glide.db
     obs_quests.rxp_guide / rxp_step at turned_t) that agree with the frames' step numbers, per run of
     non-decreasing step numbers. Name + group -> rxp_guide.key ('05-11 X' matches '5-11 X'; the page cuts
     names to 40 bytes; without a group the groups the logs show win, then non-dungeon, then most steps).
  4. RXP step number -> idx: RestedXP Forever numbers only the steps it keeps for the character: the
     `step << cond` applies AND its stepLogic shows the step under the character's RXP settings (#hardcore/
     #softcore, #ah under solo self-found / #ssf, #group/#solo, #season/#som/#era, #xprate, #phase, .dungeon
     with the guide's own .enabledungeon / #disabledungeons, #beta): rxp_parser.rxp_numbering, the numbering
     the live coach uses too. rxp_index_map alone agrees with the addon's S pages
     for 52 of 251 samples, with this filter for 248 (the rest: different quest ids, a page race at a
     guide switch). Settings: the character's RXPSettings profile in RestedXP's SavedVariables (archived
     copies in data/archive/sv, then the live client's), else RXP's defaults; when the S pages disagree
     (>= 3 samples) and toggling solo-self-found / group quests / hardcore agrees clearly better, that wins.
  5. One obs_step_time row per contiguously observed visit of a step: t_start = first frame on it, t_end =
     first frame after it (the last frame on it when frames stop for > 2 min), level = level + xp/xp_max at
     t_start, xp_gain = XP gained in [t_start, t_end] (level-ups counted), deaths = alive -> dead/ghost
     transitions in it. Visits under 2 s are skipped; a visit overlapping an earlier row of the same session
     (a duplicate log) is skipped.

The loader replaces obs_step_time. obs_session.guide (RXP name of the guide with the most observed time) and
play_s (observed frame time, gaps > 2 min left out) are filled only where NULL. Spans (log time -> session)
are left in ctx.cache["live_spans"] for the flags loader.
"""
import bisect
import collections
import glob
import itertools
import json
import os
import re
import sqlite3

from knowledge import rxp_parser as rp
from knowledge import store
from knowledge.sources import _lua
from knowledge.values import as_float, as_int

RUN_GAP = 120.0         # frames further apart end a visit (nothing observed in between: alt-tab, logged out)
SEG_GAP = 600.0         # ... and start a new character segment
SLACK = 120.0           # a frame this far outside a recorder session's [started, ended] still belongs to it
CONTINUE = 900.0        # a segment starting this soon after its character's session ended continues that session
MIN_STEP_S = 2.0        # shorter visits are RXP auto-advancing, not time spent on the step
FAST_LEVELUP_S = 60.0   # two level-ups closer than this: not a real game
SWITCH_S = 15.0         # a G/B page this soon after a step/character change describes the frames since the change
ANCHOR_S = 10.0         # a recorder turn-in vouches for its guide when a frame this close in time ...
ANCHOR_STEPS = 2        # ... shows a step number this close to the one RXP had at the turn-in
BACKSTEP = 5            # a drop this small without turn-in evidence is a step back inside the same guide
S_PAGE_S = 5.0          # an S page takes the identity of a frame this close in time

# RestedXP settings that decide which steps a guide has (RXPSettings profile, RestedXP's names: rxp_parser
# RxpSettings.from_profile). AceDB saves only values that differ from the defaults, so a missing key means
# RestedXP's default (rxp_parser.RxpSettings()): DEFAULT_SETTINGS spells the ones the S-page check toggles.
SETTING_KEYS = rp.PROFILE_KEYS
DEFAULT_SETTINGS = {"soloSelfFound": False, "hardcore": False, "enableGroupQuests": False, "season": 0}
VARIANT_KEYS = ("soloSelfFound", "enableGroupQuests", "hardcore")     # toggled when the S pages disagree
STEP_COLS = ["session", "guide_key", "idx", "t_start", "t_end", "level", "xp_gain", "deaths"]


# ------------------------------------------------------------------------------------------------
# small helpers
# ------------------------------------------------------------------------------------------------


def _rows(conn, sql, args=()):
    cur = conn.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


_PAGE_BAD = re.compile(r"[;=|\n\r]")


def page_text(s, limit=40):
    """What the addon's clean() makes of a text on a page: ; = | and newlines -> ',' and cut to `limit` bytes."""
    s = _PAGE_BAD.sub(",", s or "")
    b = s.encode("utf-8")
    return b[:limit].decode("utf-8", "ignore") if len(b) > limit else s


def norm_name(s):
    """Guide name for matching: case, spacing and the level range's zero padding ignored ('05-11 X' == '5-11 x')."""
    s = re.sub(r"\s+", " ", (s or "").strip()).lower()
    return re.sub(r"^0*(\d+)\s*-\s*0*(\d+)", r"\1-\2", s)


def _norm_ws(s):
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


# ------------------------------------------------------------------------------------------------
# RXP step numbering
# ------------------------------------------------------------------------------------------------
def step_shown(flags, settings, dungeons=None):
    """RestedXP's stepLogic for one step's effective flags under RXP settings in RestedXP's own names (a
    SavedVariables profile dict): rxp_parser.step_shown, the rule the live coach numbers by too. dungeons: the
    guide's own dungeon rules (rxp_parser.guide_dungeons)."""
    return rp.step_shown(flags, rp.RxpSettings.from_profile(settings), dungeons)


class StepMaps:
    """RXP live step number -> rxp_step.idx per (guide, character, settings); quest ids per step."""

    def __init__(self, conn):
        self.conn = conn
        self._steps, self._maps, self._quests, self._guides = {}, {}, {}, {}

    def steps(self, key):
        if key not in self._steps:
            self._steps[key] = [{"idx": i, "cond": store.jload(c), "flags": store.jload(f, {}) or {}}
                                for i, c, f in self.conn.execute(
                                    "SELECT idx, cond, flags FROM rxp_step WHERE guide_key=? ORDER BY idx", (key,))]
        return self._steps[key]

    def guide(self, key):
        """{"text", "steps"} as rxp_parser.visible_steps reads it: the steps with their `.enabledungeon` actions
        (the guide's own dungeon switches), the text for its header (#disabledungeons)."""
        g = self._guides.get(key)
        if g is None:
            acts = collections.defaultdict(list)
            for idx, args, cond in self.conn.execute("SELECT idx, args, cond FROM rxp_action WHERE guide_key=? AND "
                                                     "verb='enabledungeon' ORDER BY idx, line", (key,)):
                acts[idx].append({"verb": "enabledungeon", "args": store.jload(args, {}) or {},
                                  "cond": store.jload(cond)})
            row = self.conn.execute("SELECT text FROM rxp_guide WHERE key=?", (key,)).fetchone()
            g = self._guides[key] = {"text": (row[0] if row else None) or "",
                                     "steps": [dict(s, actions=acts.get(s["idx"], [])) for s in self.steps(key)]}
        return g

    def index_map(self, key, cls, race, faction, settings):
        rs = rp.RxpSettings.from_profile(settings)
        ck = (key, cls, race, faction, rs.key())
        m = self._maps.get(ck)
        if m is None:
            m = self._maps[ck] = rp.rxp_numbering(self.guide(key), rs, cls=cls, race=race, faction=faction)
        return m

    def quests(self, key):
        q = self._quests.get(key)
        if q is None:
            q = self._quests[key] = collections.defaultdict(set)
            for idx, qid in self.conn.execute("SELECT idx, quest_id FROM rxp_action WHERE guide_key=? AND "
                                              "quest_id IS NOT NULL", (key,)):
                q[idx].add(qid)
        return q


class Guides:
    """rxp_guide lookup by the guide name (and group) the addon's G page or the recorder reports."""

    def __init__(self, conn):
        self.grp, self.name, self.faction = {}, {}, {}
        self.by_name = collections.defaultdict(list)
        self.n_steps = dict(conn.execute("SELECT guide_key, COUNT(*) FROM rxp_step GROUP BY guide_key").fetchall())
        for key, name, grp, faction in conn.execute("SELECT key, name, grp, faction FROM rxp_guide").fetchall():
            grp = grp or ""
            self.grp[key], self.name[key] = grp, name or ""
            self.faction[key] = faction or ("Alliance" if re.search(r"\(A\)|Alliance", grp) else
                                            "Horde" if re.search(r"\(H\)|Horde", grp) else None)
            for form in {norm_name(name), norm_name(page_text(rp.affix_name(name or "")))}:
                if form and key not in self.by_name[form]:
                    self.by_name[form].append(key)

    def resolve(self, name, group=None, prefer=(), faction=None):
        keys = self.by_name.get(norm_name(name)) or []
        if group and len(keys) > 1:
            g = _norm_ws(group)
            keys = [k for k in keys if g in (_norm_ws(self.grp[k]), _norm_ws(page_text(self.grp[k])))] or keys
        if len(keys) <= 1:
            return keys[0] if keys else None
        rank = {g: i for i, g in enumerate(prefer)}
        return min(keys, key=lambda k: (bool(faction and self.faction[k] and self.faction[k] != faction),
                                        rank.get(self.grp[k], len(rank)), "dungeon" in self.grp[k].lower(),
                                        -self.n_steps.get(k, 0), k))

    def rxp_name(self, key):
        """RXP's internal guide name ('05-11 Dun Morogh'), the form obs_session.guide holds."""
        return rp.affix_name(self.name.get(key) or "") or None


# ------------------------------------------------------------------------------------------------
# inputs
# ------------------------------------------------------------------------------------------------
def read_log(path):
    """One companion log -> {"name", "frames": [{"t","l","x","X","st","d","cl"}], "G"/"B"/"S": [(t, page)],
    "bad": unreadable lines}, everything sorted by time."""
    out = {"name": os.path.splitext(os.path.basename(path))[0], "frames": [], "G": [], "B": [], "S": [], "bad": 0}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
                t = float(o["t"])
            except (ValueError, TypeError, KeyError, IndexError):
                out["bad"] += 1
                continue
            core, page = o.get("core"), o.get("page")
            if isinstance(core, dict):
                out["frames"].append({"t": t, "l": as_int(core.get("l")), "x": as_int(core.get("x")),
                                      "X": as_int(core.get("X")), "st": as_int(core.get("st")), "d": as_int(core.get("d")),
                                      "cl": str(core.get("cl") or "").upper() or None})
            elif isinstance(page, dict):
                pg = str(page.get("pg") or "")
                if pg in ("G", "B"):
                    out[pg].append((t, page))
                elif pg[:1] == "S" and pg[1:].isdigit():
                    out["S"].append((t, page))
    out["frames"].sort(key=lambda f: f["t"])
    for k in ("G", "B", "S"):
        out[k].sort(key=lambda p: p[0])
    return out


def rxp_settings(ctx):
    """{character name (lower case): RXP settings} from RestedXP's SavedVariables: archived copies
    (data/archive/sv/RXPGuides*.lua, by name), then the live client's (later files win)."""
    files = sorted(glob.glob(os.path.join(getattr(ctx, "archive_sv", "") or "", "RXPGuides*.lua")))
    wow = ctx.wow_dir() if hasattr(ctx, "wow_dir") else None
    if wow:
        files += sorted(glob.glob(os.path.join(wow, "WTF", "Account", "*", "SavedVariables", "RXPGuides.lua")))
    out = {}
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                src = fh.read()
            m = re.search(r"(?m)^RXPSettings\s*=", src)
            rs = _lua.assignments(src[m.start():]).get("RXPSettings") if m else None
        except (OSError, ValueError, RecursionError):
            continue
        if not isinstance(rs, dict):
            continue
        profiles = rs.get("profiles") if isinstance(rs.get("profiles"), dict) else {}
        for who, prof in (rs.get("profileKeys") or {}).items():
            p = profiles.get(prof)
            if isinstance(p, dict):
                out[str(who).split(" - ")[0].strip().lower()] = {k: p[k] for k in SETTING_KEYS if k in p}
    return out


def recorder_anchors(ctx):
    """{recorder session id: [(t, RXP guide name, RXP step number)]}: where RXP stood at each quest turn-in
    (glide.db obs_quests, recorded by the GlideRecorder addon)."""
    if not (hasattr(ctx, "has_glide") and ctx.has_glide()):
        return {}
    out = collections.defaultdict(list)
    try:
        g = ctx.glide()
        try:
            for sid, t, name, step in g.execute("SELECT session, turned_t, rxp_guide, rxp_step FROM obs_quests WHERE "
                                                "rxp_guide IS NOT NULL AND rxp_step IS NOT NULL AND turned_t IS NOT NULL"):
                if as_int(step) is not None and as_float(t) is not None:
                    out[sid].append((float(t), name, int(step)))
        finally:
            g.close()
    except sqlite3.Error:
        return {}
    for v in out.values():
        v.sort()
    return dict(out)


# ------------------------------------------------------------------------------------------------
# attribution: character segments, sessions, guides
# ------------------------------------------------------------------------------------------------
def segments(log):
    """Character segments of a log's frames -> [{"i0","i1","t0","t1","l0","name","cls","race","faction"}]."""
    fr = log["frames"]
    if not fr:
        return []
    ts = [f["t"] for f in fr]
    cuts = {0}
    for i in range(1, len(fr)):
        a, b = fr[i - 1], fr[i]
        if (b["t"] - a["t"] > SEG_GAP or (a["l"] is not None and b["l"] is not None and b["l"] < a["l"])
                or (a["cl"] and b["cl"] and a["cl"] != b["cl"])):
            cuts.add(i)
    prev = None
    for tb, p in log["B"]:                    # another character's B page without a level/class change at it
        nm = p.get("nm") or None
        if nm and prev and nm != prev:
            j, lo = bisect.bisect_left(ts, tb), bisect.bisect_left(ts, tb - SWITCH_S)
            if 0 < j < len(fr) and not any(lo <= c <= j for c in cuts):
                cuts.add(j)
        prev = nm or prev
    order = sorted(cuts) + [len(fr)]
    segs = []
    for n, (a, b) in enumerate(zip(order, order[1:])):
        t0, t1 = ts[a], ts[b - 1]
        until = min(t0 + SWITCH_S, ts[order[n + 1]] - 1e-3 if n + 2 < len(order) else float("inf"))
        bp = {}
        for tb, p in log["B"]:                # the B page in effect: the last one up to SWITCH_S into the segment,
            if tb > until:                    # else the first one inside it (a name change inside would have cut it)
                if not bp and tb <= t1:
                    bp = p
                break
            bp = p
        cls = collections.Counter(f["cl"] for f in fr[a:b] if f["cl"]).most_common(1)
        segs.append({"i0": a, "i1": b, "t0": t0, "t1": t1,
                     "l0": next((f["l"] for f in fr[a:b] if f["l"] is not None), None),
                     "name": bp.get("nm") or None, "cls": cls[0][0] if cls else (bp.get("cl") or "").upper() or None,
                     "race": bp.get("ra") or None, "faction": bp.get("fa") or None})
    return segs


def implausible(fr, a, b):
    """Why frames a..b-1 cannot be a real game (None when they can)."""
    last_up = None
    for i in range(a + 1, b):
        p, q = fr[i - 1], fr[i]
        if p["l"] is None or q["l"] is None or q["l"] <= p["l"]:
            continue
        if q["l"] - p["l"] > 1:
            return "level %d -> %d between two frames (simulated run)" % (p["l"], q["l"])
        if last_up is not None and q["t"] - last_up < FAST_LEVELUP_S:
            return "level-ups %.0f s apart (simulated run)" % (q["t"] - last_up)
        last_up = q["t"]
    return None


def _lvl_ok(s, level):
    lo, hi = s.get("level_start"), s.get("level_end")
    if level is None or lo is None:
        return True
    return lo - 1 <= level <= (hi if hi is not None else lo) + 1


def _same_char(seg, s):
    if seg["name"] and s.get("char") and seg["name"].lower() != str(s["char"]).lower():
        return False
    if seg["cls"] and s.get("class") and seg["cls"].upper() != str(s["class"]).upper():
        return False
    return True


def frame_session(f, seg, cands):
    """The recorder session a frame belongs to (see module doc, step 2), or None."""
    t = f["t"]
    ok = [s for s in cands if _lvl_ok(s, f["l"])]
    inside = [s for s in ok if s["started"] <= t <= (s["ended"] or s["started"])]
    if inside:
        return max(inside, key=lambda s: s["started"])

    def dist(s):
        return max(s["started"] - t, t - (s["ended"] or s["started"]), 0)
    near = [s for s in ok if dist(s) <= SLACK]
    if near:
        return min(near, key=dist)
    after = [s for s in cands if s["ended"] and s["ended"] < t and seg["t0"] <= s["ended"] + CONTINUE
             and _lvl_ok(s, seg["l0"])]
    return max(after, key=lambda s: s["ended"]) if after else None


def page_timeline(log):
    """G pages -> [(t_from, guide name, group)] (name '' = no guide). A switch applies from the step-number
    drop (or change to/from no step) in the SWITCH_S before the page; the first page covers the log's start."""
    fr = log["frames"]
    ts = [f["t"] for f in fr]
    out = []
    for tg, p in log["G"]:
        cur = ((p.get("gn") or "").strip(), (p.get("gg") or "").strip())
        if out and out[-1][1:] == cur:
            continue
        if not out:
            out.append((float("-inf"),) + cur)
            continue
        t_from = tg
        k = bisect.bisect_right(ts, tg) - 1
        while k > 0 and ts[k] >= tg - SWITCH_S:
            a, b = fr[k - 1]["st"], fr[k]["st"]
            if a != b and (a is None or b is None or b < a):
                t_from = ts[k]
                break
            k -= 1
        out.append((max(t_from, out[-1][0]),) + cur)
    return out


def timeline_at(tl, t):
    """(name, group) of the guide in effect at t, or None."""
    i = bisect.bisect_right([x[0] for x in tl], t) - 1
    if i < 0 or not tl[i][1]:
        return None
    return tl[i][1], tl[i][2] or None


def anchor_guides(log, a, b, anchors):
    """Logs without G pages: guide name per frame of a..b-1 from the recorder's turn-ins (module doc, step 3)."""
    fr, out = log["frames"], log["guide"]
    ts = [f["t"] for f in fr]
    runs, start, last = [], a, None              # runs of non-decreasing step numbers
    for i in range(a, b):
        st = fr[i]["st"]
        if st is None:
            continue
        if last is not None and st < last:
            runs.append((start, i))
            start = i
        last = st
    runs.append((start, b))
    prev_guide, prev_last = None, None
    for i0, i1 in runs:
        on = [i for i in range(i0, i1) if fr[i]["st"] is not None]
        if not on:
            continue
        votes = collections.Counter()
        t0, t1 = fr[on[0]]["t"], fr[on[-1]]["t"]
        for sid in {log["sess"][i]["id"] for i in on if log["sess"][i]}:
            for ta, name, step in anchors.get(sid, ()):
                if not t0 - ANCHOR_S <= ta <= t1 + ANCHOR_S:
                    continue
                j = bisect.bisect_left(ts, ta, i0, i1)
                near = [k for k in (j - 1, j) if i0 <= k < i1 and fr[k]["st"] is not None]
                k = min(near, key=lambda k: abs(ts[k] - ta)) if near else None
                if k is not None and abs(ts[k] - ta) <= ANCHOR_S and abs(fr[k]["st"] - step) <= ANCHOR_STEPS:
                    votes[name] += 1
        if votes:
            guide = votes.most_common(1)[0][0]
        elif prev_guide and prev_last is not None and prev_last - fr[on[0]]["st"] <= BACKSTEP:
            guide = prev_guide
        else:
            guide = None
        for i in on:
            out[i] = (guide, None) if guide else None
        prev_guide, prev_last = guide, fr[on[-1]]["st"]


def attribute(log, sessions, anchors):
    """Fill per-frame lists: seg_of (segment index, None = skipped), sess (session row or None), guide."""
    fr = log["frames"]
    n = len(fr)
    log["seg_of"], log["sess"], log["guide"] = [None] * n, [None] * n, [None] * n
    log["segs"] = segments(log)
    for si, seg in enumerate(log["segs"]):
        seg["skip"] = implausible(fr, seg["i0"], seg["i1"])
        if seg["skip"]:
            continue
        cands = [s for s in sessions if _same_char(seg, s)]
        for i in range(seg["i0"], seg["i1"]):
            log["seg_of"][i] = si
            log["sess"][i] = frame_session(fr[i], seg, cands)
    if log["G"]:
        log["guide_from"], log["tl"] = "pages", page_timeline(log)
        for i in range(n):
            if log["seg_of"][i] is not None:
                log["guide"][i] = timeline_at(log["tl"], fr[i]["t"])
    else:
        log["guide_from"], log["tl"] = "recorder", None
        for seg in log["segs"]:
            if not seg["skip"]:
                anchor_guides(log, seg["i0"], seg["i1"], anchors)


def page_quests(el):
    out = set()
    for e in (el or "").split(","):
        p = e.split(":")
        if len(p) >= 2 and p[0] != "go" and p[1].rstrip("!").isdigit():
            out.add(int(p[1].rstrip("!")))
    return out


def score(maps, samples, settings):
    """(agree, disagree): S pages whose quest ids appear in the step our mapping gives for RXP's index."""
    agree = 0
    for key, cls, race, faction, i_rxp, qs in samples:
        idx = maps.index_map(key, cls, race, faction, settings).get(i_rxp)
        if idx is not None and qs & maps.quests(key).get(idx, set()):
            agree += 1
    return agree, len(samples) - agree


# ------------------------------------------------------------------------------------------------
# rows
# ------------------------------------------------------------------------------------------------
def xp_delta(a, b):
    """XP gained between two consecutive frames (one level-up allowed)."""
    if None in (a["l"], b["l"], a["x"], b["x"]):
        return 0
    if b["l"] == a["l"]:
        return max(0, b["x"] - a["x"])
    if b["l"] == a["l"] + 1 and a["X"]:
        return max(0, a["X"] - a["x"]) + b["x"]
    return 0


def visit_row(fr, i, e, key):
    """obs_step_time row for frames i..e (e = the first frame after the visit, or its last frame)."""
    t0, t1 = fr[i]["t"], fr[e]["t"]
    if t1 - t0 < MIN_STEP_S:
        return None
    xp = deaths = 0
    for k in range(i + 1, e + 1):
        a, b = fr[k - 1], fr[k]
        xp += xp_delta(a, b)
        if a["d"] == 0 and b["d"] in (1, 2):
            deaths += 1
    f = fr[i]
    level = None
    if f["l"] is not None:
        level = round(f["l"] + (min(f["x"], f["X"]) / float(f["X"]) if f["x"] is not None and f["X"] else 0.0), 3)
    return (key[0], key[1], key[2], int(round(t0)), int(round(t1)), level, xp, deaths)


def load(conn, ctx):
    live = getattr(ctx, "live", None) or ""
    files = sorted(glob.glob(os.path.join(live, "*.jsonl")))
    if not files:
        return {"skipped": "no companion logs (*.jsonl) in %s" % live}
    guides = Guides(conn)
    if not guides.grp:
        return {"skipped": "rxp_guide is empty (the parser loader runs before this one)"}
    sessions = _rows(conn, "SELECT id, char, class, race, faction, level_start, level_end, started, ended, play_s, "
                           "guide FROM obs_session WHERE started IS NOT NULL")
    maps, sv_settings, anchors = StepMaps(conn), rxp_settings(ctx), recorder_anchors(ctx)
    per_log, logs = {}, []
    for path in files:
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            lg = read_log(path)
        except OSError as e:
            per_log[name] = {"skipped": "unreadable: %s" % e}
            continue
        if not lg["frames"]:
            per_log[name] = {"skipped": "no core frames"}
            continue
        logs.append(lg)
    logs.sort(key=lambda lg: lg["frames"][0]["t"])

    # 1. sessions and guide names per frame; groups the player's guides come from
    groups = collections.Counter()
    for lg in logs:
        attribute(lg, sessions, anchors)
        if any(s is not None for s in lg["seg_of"]):
            groups.update((p.get("gg") or "").strip() for _, p in lg["G"] if p.get("gn") and p.get("gg"))
    prefer = [g for g, _ in groups.most_common()]

    # 2. guide keys and who is playing, per frame
    not_in_kb = set()
    for lg in logs:
        n, cache = len(lg["frames"]), {}
        lg["key"], lg["who"] = [None] * n, [None] * n
        for i in range(n):
            g = lg["guide"][i]
            if g is None:
                continue
            seg, s = lg["segs"][lg["seg_of"][i]], lg["sess"][i] or {}
            faction = s.get("faction") or seg["faction"]
            if (g, faction) not in cache:
                cache[(g, faction)] = guides.resolve(g[0], g[1], prefer, faction)
            key = cache[(g, faction)]
            if key is None:
                not_in_kb.add("%s||%s" % (g[1] or "?", g[0]))
                continue
            lg["key"][i] = key
            lg["who"][i] = (s.get("char") or seg["name"], s.get("class") or seg["cls"], s.get("race") or seg["race"],
                            faction or guides.faction.get(key))

    # 3. RXP settings per character, checked against the addon's S pages
    samples = collections.defaultdict(list)
    for lg in logs:
        if not lg["tl"]:
            continue
        ts = [f["t"] for f in lg["frames"]]
        for t, p in lg["S"]:
            i_rxp, qs, g = as_int(p.get("i")), page_quests(p.get("el")), timeline_at(lg["tl"], t)
            if not i_rxp or not qs or g is None:
                continue
            j = bisect.bisect_left(ts, t)
            near = [k for k in (j - 1, j) if 0 <= k < len(ts) and lg["who"][k] and abs(ts[k] - t) <= S_PAGE_S]
            if not near:
                continue
            char, cls, race, faction = lg["who"][min(near, key=lambda k: abs(ts[k] - t))]
            key = guides.resolve(g[0], g[1], prefer, faction)
            if key:
                samples[char].append((key, cls, race, faction, i_rxp, qs))
    chosen, settings_res, check = {}, {}, [0, 0]
    for char in sorted({w[0] for lg in logs for w in lg["who"] if w}, key=lambda c: c or ""):
        base, src = dict(DEFAULT_SETTINGS), "default"
        if (char or "").lower() in sv_settings:
            base.update(sv_settings[(char or "").lower()])
            src = "rxp-savedvariables"
        best = score(maps, samples[char], base)
        if best[1] >= 3:
            for flips in itertools.product((False, True), repeat=len(VARIANT_KEYS)):
                v = dict(base)
                for k, flip in zip(VARIANT_KEYS, flips):
                    v[k] = (not bool(v.get(k))) if flip else v.get(k)
                sc = score(maps, samples[char], v)
                if any(flips) and sc[0] >= best[0] + 3:
                    best, base, src = sc, v, "s-pages"
        chosen[char] = base
        check[0] += best[0]
        check[1] += best[1]
        settings_res[char or "?"] = {"from": src, "s_pages_agree": best[0], "s_pages_disagree": best[1],
                                     **{k: base.get(k) for k in VARIANT_KEYS}}

    # 4. visits -> rows; spans for the flags loader; observed time per session
    rows, last_end, spans = [], {}, []
    play, gtime = collections.Counter(), collections.defaultdict(collections.Counter)
    totals = collections.Counter()
    for lg in logs:
        fr, n = lg["frames"], len(lg["frames"])
        info = {"frames": n, "guide_from": lg["guide_from"]}
        skipped = sorted({seg["skip"] for seg in lg["segs"] if seg["skip"]})
        if skipped:
            info["skipped_segments"] = skipped
        totals["frames"] += n
        step = [None] * n
        for i in range(n):
            if lg["seg_of"][i] is None:
                continue
            s = lg["sess"][i]
            sid = s["id"] if s else "live:" + lg["name"]
            if not spans or spans[-1][2] != sid or spans[-1][4] != lg["name"] or fr[i]["t"] - spans[-1][1] > SEG_GAP:
                who = lg["who"][i][0] if lg["who"][i] else lg["segs"][lg["seg_of"][i]]["name"]
                spans.append([fr[i]["t"], fr[i]["t"], sid, (s or {}).get("char") or who, lg["name"]])
            spans[-1][1] = fr[i]["t"]
            if not s:
                totals["frames_without_session"] += 1
            key = lg["key"][i]
            if fr[i]["st"] is None:
                continue
            if key is None:
                totals["frames_without_guide"] += 1
                continue
            char, cls, race, faction = lg["who"][i]
            idx = maps.index_map(key, cls, race, faction, chosen.get(char, DEFAULT_SETTINGS)).get(fr[i]["st"])
            if idx is None:
                totals["step_number_not_in_guide"] += 1
                continue
            step[i] = (sid, key, idx)
        for i in range(1, n):
            s = lg["sess"][i - 1]
            dt = fr[i]["t"] - fr[i - 1]["t"]
            if s and lg["sess"][i] is s and dt <= RUN_GAP:
                play[s["id"]] += dt
                if lg["key"][i - 1]:
                    gtime[s["id"]][guides.rxp_name(lg["key"][i - 1])] += dt
        mine, i = 0, 0
        while i < n:
            k = step[i]
            if k is None:
                i += 1
                continue
            j = i + 1
            while j < n and step[j] == k and fr[j]["t"] - fr[j - 1]["t"] <= RUN_GAP:
                j += 1
            e = j if j < n and fr[j]["t"] - fr[j - 1]["t"] <= RUN_GAP else j - 1
            row = visit_row(fr, i, e, k)
            if row is None:
                totals["visits_under_2s"] += 1
            elif row[3] < last_end.get(row[0], float("-inf")) - 1:
                totals["visits_overlapping_earlier_rows"] += 1
            else:
                rows.append(row)
                last_end[row[0]] = max(last_end.get(row[0], row[4]), row[4])
                mine += 1
            i = j
        totals["frames_on_steps"] += sum(1 for k in step if k)
        info["rows"] = mine
        info["sessions"] = sorted({s["id"] for s in lg["sess"] if s} |
                                  ({"live:" + lg["name"]} if any(lg["seg_of"][i] is not None and not lg["sess"][i]
                                                                 for i in range(n)) else set()))
        per_log[lg["name"]] = info

    conn.execute("DELETE FROM obs_step_time")
    store.insert_rows(conn, "obs_step_time", rows, STEP_COLS)
    filled = {"guide": {}, "play_s": {}}
    for s in sessions:
        sid = s["id"]
        if s.get("guide") is None and gtime.get(sid):
            g = gtime[sid].most_common(1)[0][0]
            if g and conn.execute("UPDATE obs_session SET guide=? WHERE id=? AND guide IS NULL", (g, sid)).rowcount:
                filled["guide"][sid] = g
        if s.get("play_s") is None and play.get(sid):
            if conn.execute("UPDATE obs_session SET play_s=? WHERE id=? AND play_s IS NULL",
                            (int(round(play[sid])), sid)).rowcount:
                filled["play_s"][sid] = int(round(play[sid]))
    if hasattr(ctx, "cache"):
        ctx.cache["live_spans"] = [tuple(x) for x in spans]

    with_rows = [k for k, v in per_log.items() if v.get("rows")]
    return {"logs": len(files), "obs_step_time": len(rows), "logs_with_rows": len(with_rows),
            "logs_on_recorder_sessions": sum(1 for v in per_log.values()
                                             if any(not s.startswith("live:") for s in v.get("sessions", ()))),
            "logs_simulated": sorted(k for k, v in per_log.items()
                                     if v.get("skipped_segments") and not v.get("sessions")),
            "guide_from": dict(collections.Counter(v["guide_from"] for v in per_log.values() if v.get("rows"))),
            "frames": dict(totals), "s_page_check": {"agree": check[0], "disagree": check[1]},
            "rxp_settings": settings_res, "sessions_filled": filled, "groups_seen": prefer,
            "guides_not_in_kb": sorted(not_in_kb), "per_log": per_log}
