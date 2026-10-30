"""Quest briefings and step videos (both toggleable: llm.quest_briefings, llm.step_videos).

Quest briefing: when a quest is accepted, the coach reads its objective (the game's own text from the knowledge
base) and adds an explanation of how to do it: where the targets are, what drops the items and how often, what the
RestedXP route does with the quest. A quest picked up on its own is briefed right away; quests picked up back to back
(a hub) are listed quietly and each is briefed later, when the player gets to it (HeldBriefs).

Step videos: RestedXP steps can carry a `.link` to a YouTube clip ("CLICK HERE for an example"). When the guide
moves to such a step, the coach says what the clip shows (knowledge/step_videos.json: a short instruction written
from each clip, see tools/step_videos.py) with a link to it (llm.video_tips), and the clip can open in a new browser
window (llm.step_videos); once per clip per session, never in combat.
"""
import json
import os
import re
import shlex
import subprocess
import sys
import time
import webbrowser
from urllib.parse import parse_qs, urlparse

from log import log_once

YOUTUBE = re.compile(r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch|shorts/|embed/|live/)|youtu\.be/)", re.I)
_GENDER = re.compile(r"\$[Gg]\s*([^:;]*):([^;]*);")


def clean_quest_text(text, player=None, objectives=None):
    """Quest text placeholders ($N name, $C class, $R race, $B line break, $Gmale:female;, $1oa = the amount of
    objective 1) -> plain words."""
    if not text:
        return ""
    p = player or {}
    objs = objectives or []

    def amount(m):
        i = int(m.group(1)) - 1
        n = objs[i].get("amount") if 0 <= i < len(objs) else None
        return str(n) if n else ""
    t = re.sub(r"\$(\d)o[a-z]", amount, text)
    t = _GENDER.sub(lambda m: m.group(1).strip(), t)
    t = re.sub(r"\$[Bb]", " ", t)
    t = re.sub(r"\$[Nn]", p.get("name") or "adventurer", t)
    t = re.sub(r"\$[Cc]", (p.get("class") or "adventurer").lower(), t)
    t = re.sub(r"\$[Rr]", (p.get("race") or "adventurer"), t)
    t = " ".join(t.split())
    return t


DO_VERBS = ("complete", "collect", "mob", "use", "target", "isOnQuest", "isQuestComplete", "unitscan", "cast")
_LINK_LINE = re.compile(r"click here|clicking here|for (?:video )?reference|for an example", re.I)


def rxp_instructions(pack, guide_key, idx, qid, max_steps=3):
    """RestedXP's own instructions for a quest in the current guide: the text of the steps that work on it
    (complete / collect / kill / use ...), from the current step on; for a quest with no such step (a delivery,
    a talk-to) the step that turns it in. pack: the knowledge pack (api.KnowledgePack) or None.
    -> [{"n": RestedXP step index, "text": "Kill Wolves. Loot them for Meat."}]"""
    if pack is None or guide_key is None:
        return []
    try:
        rows = pack.quest_steps(guide_key, qid)
    except Exception as e:  # noqa: BLE001
        log_once("briefing.quest_steps", e)
        return []
    start = (idx or 0) - 3                    # the accept can land just before RestedXP moves to the next step
    rows = [r for r in rows or () if r.get("idx") is not None and r["idx"] >= start]
    work = [r for r in rows if any(v in DO_VERBS for v in (r.get("verbs") or "").split(","))]
    pick = work[:max_steps] or [r for r in rows if "turnin" in (r.get("verbs") or "")][:1]
    out = []
    for r in pick:
        lines = []
        for ln in (r.get("text") or "").splitlines():
            ln = " ".join(ln.split()).strip()
            if not ln or _LINK_LINE.search(ln) or ln in lines:
                continue
            lines.append(ln if ln[-1] in ".!?" else ln + ".")
        text = " ".join(lines)
        if text and all(o["text"] != text for o in out):      # the same step repeated later in the guide
            out.append({"idx": r["idx"], "text": text})
    return out


def quest_brief(api, kctx, qid):
    """{id, name, objective, objectives: [...], facts} for one quest from the knowledge layer, or None."""
    if api is None or kctx is None:
        return None
    try:
        info = api.dispatch("quest_info", {"id": int(qid)}, kctx, budget_s=3.0)
    except Exception as e:  # noqa: BLE001
        log_once("briefing.quest_info", e)
        return None
    if not isinstance(info, dict) or info.get("error") or not info.get("name"):
        return None
    player = getattr(kctx, "player", None) or {}
    info["objective_text"] = clean_quest_text(info.get("objective_text"), player, info.get("objectives"))
    steps = rxp_instructions(getattr(kctx, "pack", None), getattr(kctx, "guide_key", None),
                             getattr(kctx, "idx", None), qid)
    route = getattr(kctx, "route", None)
    for st in steps:
        st["step"] = route.idx_to_rxp.get(st["idx"]) if route is not None else None
    info["rxp_instructions"] = steps
    return dict(info, id=int(qid))


def offline_explanation(b):
    """A rule-based explanation from quest_info when the AI can't be called: targets, drops, where."""
    out = []
    for o in (b.get("objectives") or [])[:3]:
        tgt, n = o.get("target"), o.get("amount")
        drops = [d for d in o.get("drops") or () if d.get("from")]
        if drops:
            # rates are fractions; a rate seen once or twice says little, so prefer well-observed sources
            solid = [d for d in drops if (d.get("seen") or 0) >= 3] or drops
            d = max(solid, key=lambda r: r.get("rate") or 0)
            r = d.get("rate")
            rate = " (%d%%)" % round(r * 100 if r <= 1 else r) if isinstance(r, (int, float)) else ""
            out.append("%s drops from %s%s." % (tgt or "The item", d["from"], rate))
            where = (o.get("spawns") or {}).get("where")
            if where:
                out.append("Nearest: %s." % where)
        elif tgt and o.get("spawns"):
            where = (o.get("spawns") or {}).get("where")
            out.append("%s%s%s." % ("%s x " % n if n and n > 1 else "", tgt, ": " + where if where else ""))
    return " ".join(out)


# what the model gets about each quest it explains (quest_brief fields; empty ones are left out)
PROMPT_KEYS = ("name", "fact", "level", "rxp_instructions", "objective_text", "objectives", "giver", "turn_in",
               "chain_next", "route", "group_size", "xp_at_your_level")
# why a held quest is briefed now (HeldBriefs.check)
LATER_REASON = {"target": "targeting a mob that counts for it", "step": "on the RestedXP step that does it",
                "near": "near its objectives"}


def read_out(b):
    """What gets read for a quest: RestedXP's instructions for it, else the game's objective text."""
    rx = " ".join(st["text"] for st in b.get("rxp_instructions") or ())
    return ("RestedXP: " + rx) if rx else (b.get("objective_text") or "")


def spoken_head(infos, later=None, max_full=4):
    """The briefing's first part, said without a model: the quest's name and what it asks; for several quests the
    first max_full in full and the rest by name. -> (text, infos with the quests past max_full cut to id and name)."""
    if len(infos) == 1:
        b = infos[0]
        head = ("Quest: %s." if later else "New quest: %s.") % b["name"] + (" %s" % read_out(b) if read_out(b) else "")
    else:
        head = "%d new quests. " % len(infos) + " ".join(
            "%s. %s" % (b["name"], read_out(b) or "No instructions.") for b in infos[:max_full])
        if len(infos) > max_full:
            head += " Also: " + ", ".join(b["name"] for b in infos[max_full:]) + "."
        infos = infos[:max_full] + [{"id": b["id"], "name": b["name"]} for b in infos[max_full:]]
    head = head.strip()
    if head and head[-1] not in ".!?":
        head += "."
    return head, infos


def explain_request(infos, later, words, quests_json):
    """The trigger text asking the model to explain how to do the quests (the head was read out already).
    quests_json: the quests' PROMPT_KEYS fields as prompt JSON."""
    when = "just accepted %s" % ("this quest" if len(infos) == 1 else "these quests")
    if later:
        when = "accepted this quest earlier (in a batch, not briefed then) and is now %s" % LATER_REASON.get(
            later, "getting to it")
    return ("QUEST BRIEFING (not a callout). The player %s. RestedXP's instructions "
            "(rxp_instructions; else the objective text) have already been read out loud; do not repeat "
            "them. Explain how to do %s efficiently, adding what they leave out, for speech: "
            "where to go (direction and distance), what to kill, collect or use, drop rates worth knowing, "
            "anything easy to miss, and whether the RestedXP route does it now or later. Plain sentences, "
            "max %d words, no lists.\nQUESTS: %s" % (when, "it" if len(infos) == 1 else "them", words, quests_json))


def prompt_fields(b):
    """A quest_brief cut to what the model needs (PROMPT_KEYS, empty fields left out)."""
    return {k: b.get(k) for k in PROMPT_KEYS if b.get(k) not in (None, [], {})}


NOTES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge", "step_videos.json")


def clip_key(url):
    """'<video id>@<start seconds>' for a YouTube link (the key of knowledge/step_videos.json), or None."""
    u = urlparse(url or "")
    q = parse_qs(u.query)
    if "youtu.be" in u.netloc:
        vid = u.path.strip("/").split("/")[0]
    elif "/watch" in u.path:
        vid = (q.get("v") or [""])[0]
    else:
        m = re.search(r"/(?:shorts|embed|live)/([\w-]{6,})", u.path)
        vid = m.group(1) if m else ""
    if not vid:
        return None
    t = (q.get("t") or q.get("start") or ["0"])[0]
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?", t or "0")
    start = (int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)) if m else 0
    return "%s@%d" % (vid, start)


_notes = None


def video_note(url, path=None):
    """What a step clip shows (knowledge/step_videos.json): {"instruction": said aloud, "detail": the path, skills
    and mechanics for the chat, "confidence"}, or None. Notes whose writer wasn't sure what the clip shows
    ("confidence": "low") aren't used: the clip itself is the better guide there."""
    global _notes
    if _notes is None or path:
        try:
            with open(path or NOTES_PATH, encoding="utf-8") as fh:
                _notes = json.load(fh).get("clips", {})
        except (OSError, ValueError):
            _notes = {}
    n = _notes.get(clip_key(url) or "")
    if not isinstance(n, dict) or n.get("confidence") == "low" or not n.get("instruction"):
        return None
    return n


def step_links(pack, guide_key, idx):
    """[(url, label)] of the YouTube links on one RestedXP step (pack.step_links; pack: api.KnowledgePack or None)."""
    if pack is None or guide_key is None or idx is None:
        return []
    try:
        links = pack.step_links(guide_key, idx)
    except Exception as e:  # noqa: BLE001
        log_once("briefing.step_links", e)
        return []
    return [(url, label) for url, label in links or () if YOUTUBE.match(url)]


def _default_browser_exe():
    """The default browser's .exe on Windows (registry: the https handler's open command), else None."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations"
                            r"\https\UserChoice") as k:
            prog = winreg.QueryValueEx(k, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog + r"\shell\open\command") as k:
            cmd = winreg.QueryValueEx(k, "")[0]
    except OSError:
        return None
    exe = shlex.split(cmd, posix=False)[0].strip('"') if cmd else None
    return exe if exe and os.path.exists(exe) else None


NEW_WINDOW_BROWSERS = ("chrome", "msedge", "firefox", "brave", "vivaldi", "opera", "chromium")


def open_new_window(url, browser=None):
    """Open url in a new browser window (not a tab in the window you have open): the default browser (or `browser`,
    a path to its .exe) with --new-window when it's Chrome, Edge, Firefox, Brave, Vivaldi or Opera; otherwise the
    system's normal way of opening a link."""
    exe = browser or _default_browser_exe()
    if exe and os.path.basename(exe).lower().split(".")[0] in NEW_WINDOW_BROWSERS:
        subprocess.Popen([exe, "--new-window", url], close_fds=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)    # never the companion's stdin pipe
        return True
    return webbrowser.open_new(url)


class StepVideos:
    """A step's YouTube links when the guide moves to it: what each clip shows (its note, said by the coach) and,
    with `open_videos`, the first new clip opened in a browser window. Not on the step the session starts on (that's
    the step you were already doing), once per URL per session, and only out of combat / off a taxi / alive."""

    def __init__(self, opener=None):
        self.opener = opener or open_new_window
        self.key = None               # (guide, step) last seen
        self.opened = set()
        self.pending = []             # [(url, label)] waiting for combat to end

    def check(self, summary, resolve, enabled=True, notes=True, note_fn=video_note):
        """summary: state summary; resolve() -> (pack, guide_key, idx) for the current step (called only on a step
        change). enabled: open clips in the browser; notes: say what they show. Returns [(url, label, opened, note)]:
        the step's first new clip opened now (when enabled), any others only listed; note is the clip's instruction
        when notes are on and one was written for it."""
        want = enabled or notes
        key = (summary.get("guide"), summary.get("step"))
        if key[1] is None:
            return []
        if key != self.key:
            first = self.key is None
            self.key = key
            self.pending = []
            if not first and want:
                pack, gk, idx = resolve()
                self.pending = [(u, t) for u, t in step_links(pack, gk, idx) if u not in self.opened]
        if not want:
            self.pending = []
            return []
        if not self.pending or summary.get("combat") or summary.get("on_taxi") or summary.get("dead"):
            return []
        now, self.pending = self.pending, []
        done = []
        for url, label in now:                 # one window per step: the first clip opens, the rest are listed
            if url in self.opened:
                continue
            self.opened.add(url)
            note = note_fn(url) if notes else None
            if not enabled or any(d[2] for d in done):
                done.append((url, label, False, note))
                continue
            try:
                self.opener(url)
                done.append((url, label, True, note))
            except Exception as e:  # noqa: BLE001
                log_once("briefing.open", e)
                done.append((url, label, False, note))
        return done


WORK_TAGS = ("complete", "collect", "mob", "use", "cast")     # step elements that work on a quest (not conditions)


class HeldBriefs:
    """Quests picked up back to back (a hub) aren't narrated on the spot: each one's briefing waits until the player
    gets to it. check() picks the one to brief now: the target counts for it (a mob to kill, or one that drops its
    item), the current RestedXP step works on it, or the player is near its objectives. Never in combat or on a
    flight (a target seen then is remembered), at most one per `gap_s`. Quests done or gone from the log drop out."""

    def __init__(self, gap_s=20.0, max_age_s=4 * 3600):
        self.gap_s, self.max_age_s = gap_s, max_age_s
        self.quests = {}              # qid -> {"t": held at, "armed": reason seen while busy}
        self.last_t = -1e9

    def hold(self, qids, now=None):
        now = now or time.time()
        for q in qids:
            self.quests.setdefault(int(q), {"t": now, "armed": None})

    def check(self, s, now=None, target_fn=None, near_fn=None):
        """-> (qid, reason) to brief now, or None. reason: "target" | "step" | "near".
        target_fn(npc, qids) -> quest ids the target counts for; near_fn(qid, s) -> True near its objectives."""
        now = now or time.time()
        log = s.get("quests") or {}
        for q in list(self.quests):
            e = log.get(q) or log.get(str(q))
            if not e or e.get("done") or now - self.quests[q]["t"] > self.max_age_s:
                del self.quests[q]
        if not self.quests:
            return None
        qids = sorted(self.quests)
        tgt = s.get("target") or {}
        hit = set()
        if target_fn and tgt.get("npc") and not tgt.get("dead"):
            try:
                hit = set(target_fn(int(tgt["npc"]), qids) or ()) & set(qids)
            except (TypeError, ValueError):
                hit = set()
        busy = s.get("combat") or s.get("dead") or s.get("on_taxi")
        if busy:
            for q in hit:                 # pulled straight away: brief it once the fight is over
                self.quests[q]["armed"] = "target"
            return None
        if now - self.last_t < self.gap_s:
            return None
        pick = next(((q, self.quests[q]["armed"]) for q in qids if self.quests[q]["armed"]), None)
        if pick is None and hit:
            pick = (min(hit), "target")
        if pick is None:
            step = (s.get("steps") or [None])[0] or {}
            for e in step.get("elements") or ():
                if e.get("quest") in self.quests and e.get("tag") in WORK_TAGS:
                    pick = (e["quest"], "step")
                    break
        if pick is None and near_fn:
            pick = next(((q, "near") for q in qids if near_fn(q, s)), None)
        if pick is None:
            return None
        del self.quests[pick[0]]
        self.last_t = now
        return pick


class QuestBriefs:
    """Collects accepted quest ids and releases them as one batch once no new accept came for `delay` seconds."""

    def __init__(self, delay=6.0, repeat_s=600):
        self.delay, self.repeat_s = delay, repeat_s
        self.queue = []               # [(t, qid)]
        self.told = {}                # qid -> time briefed

    def add(self, qid, now=None):
        try:
            q = int(qid)
        except (TypeError, ValueError):
            return
        now = now or time.time()
        if now - self.told.get(q, -1e9) < self.repeat_s or any(x == q for _, x in self.queue):
            return
        self.queue.append((now, q))

    def due(self, now=None):
        now = now or time.time()
        if not self.queue or now - self.queue[-1][0] < self.delay:
            return []
        qs = [q for _, q in self.queue]
        self.queue = []
        for q in qs:
            self.told[q] = now
        return qs
