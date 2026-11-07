"""Asides: while the coach plays a character (persona.py) it speaks up unprompted, in character, like someone who is
there with the player. Mostly because something happened (a moment: a death or a death streak, a level-up, a close
call, an elite or rare kill, a new zone, a long grind, a flight), at a login (a greeting that picks up from last time)
and when the game goes away (a goodbye); between moments now and then (about every banter_minutes, sooner in
downtime: flying, dead, eating or drinking). Only in a calm moment; the model may decline (SKIP).

What makes it organic:
- the diary (Memory.diary): the moments shared, kept across sessions, for callbacks ("ye haven't died since Westfall");
- the mood: the character's state, pushed by moments and settling with time ("grumpy after three deaths"), on every
  request's stay-in-character line;
- variety: each aside asks for a shape (a grunt, a quip, a short story, a callback, a follow-up to its own last line, a
  question...) and names the openers and endings it has been overusing;
- learning when the player likes to talk: the asides answered per activity (Memory.bond's by_activity) make the timer
  shorter where they chat and longer where they don't.

The bond (Memory.bond: hours together, sessions, asides answered) sets the stage (new -> old friends) and reflection
learns "rapport" notes from how the player reacts. Answering an aside starts a chat (in_chat, CHAT_NOTE) that goes on
while the player does. "BANTER: less / more / off / on" in a reply sets banter_minutes or banter. One of the coach
thread's tickers (Coach._loop calls tick about once a second; Coach.push hands every frame to observe)."""
import random
import re
import threading
import time
from typing import Callable, Optional

import models as models_mod
import persona as persona_mod
from replies import is_skip, trim_words
from log import log_once

REPLY_S = 180              # the player answered an aside: they asked or said something within this long after it
FRESH_S = 10               # the game state is live: a frame arrived this recently
QUIET_S = 60               # nothing said by the coach for this long before an unprompted aside
DOWNTIME_QUIET_S = 25      # ... in downtime (flying, dead, eating)
MOMENT_QUIET_S = 8         # a moment: react soon after the coach's practical line about it
MOMENT_TTL_S = 120         # a moment not reacted to by then is let go (it's in the diary anyway)
# the game went away after a while together (a goodbye, once a session): the WoW window closed for this long, or no
# frame at all for GONE_S (logged out to the character screen, or away: an alt-tab hides the strip too, so it's long)
CLOSED_S = 20
GONE_S = 300
BREAK_S = 1200             # time apart before a run counts as a new session with a greeting (a restart doesn't)
MIN_SESSION_S = 600        # ... after at least this long together
MOOD_S = 1200              # a mood settles after this long
STEP = 1.6                 # BANTER: less / more scales banter_minutes by this
CLOSE_CALL_HP = 15         # a fight survived at or below this health percent is a close call
BIG_KILLS = ("elite", "rare", "rareelite", "worldboss")

# moment kind -> (chance the character speaks up, cooldown seconds, mood, mood strength)
MOMENTS = {"died": (0.75, 120, "frustrated", 2), "death_streak": (0.95, 600, "exasperated", 3),
           "level_up": (0.9, 60, "proud", 3), "close_call": (0.8, 300, "rattled but thrilled", 2),
           "big_kill": (0.7, 300, "fired up", 2), "new_zone": (0.6, 300, "curious", 1),
           "long_grind": (0.5, 900, "restless", 1), "flight": (0.5, 600, "relaxed", 1),
           "hearth": (0.3, 900, "glad to be heading back", 1), "greeting": (1.0, 0, "glad to see them", 2),
           "goodbye": (1.0, 0, "wistful", 1)}

# the shapes an unprompted aside takes (weights); the request asks for one, so they don't all sound the same
SHAPES = (("a one- or two-word reaction or grunt, nothing more", 2),
          ("a single short quip about what they're doing right now", 4),
          ("a two- or three-sentence mini story from your own past", 2),
          ("a question about them, their life outside the game, or what they think", 2),
          ("a callback to a moment from your diary together", 3),
          ("a follow-up to your own last aside, as if you'd kept thinking about it", 2),
          ("an opinion about the place they're in or the creatures around", 2),
          ("a tease or a friendly challenge about how they're playing", 2))

REQUEST = """ASIDE (not a callout: nothing asked for this). Say it fully in character, as someone who is there with
this player, the way this character would.
%s
Make it personal: use what you remember about them (above all your bond notes) and your diary.
%s
How close to be: "new" = curious, sizing them up, still a bit formal in the character's way; "getting to know each
other" = first nicknames and teasing, recalling things they told you; "familiar" = easy banter, inside jokes, honest
opinions; "old friends" = warm, shorthand, shared history, ribbing only friends get away with.
YOUR DIARY (moments you shared, oldest first):
%s
This time, the shape: %s.%s
Your recent lines (don't repeat or rephrase them): %s
No route advice unless it is the joke, and never contradict the guide. At most %d words. If nothing fits this
moment, reply exactly SKIP."""

# a question while chatting (the coach's last line was an aside or a chat reply, REPLY_S ago at most): the request's note
CHAT_NOTE = """(The player is chatting with you: they answered your aside, see RECENT CONVERSATION. Keep it going, fully
in character: react to what they actually said, with real enthusiasm; encourage them, warmly and specifically (their
play, their progress, what they just told you, them); then, usually, hand them something to answer: a question, a
tease, a dare, a "what would you do", the start of a story, a guess about them. If they asked something about the
game, answer that first and exactly, then keep chatting. When they wrap up ("gotta focus", "ok", "later", "enough",
or they turn to the game), let them go gracefully in one short line and add the final line "CHAT: end". At most %d
words.%s)"""

_LINE = re.compile(r"^\s*BANTER\s*:\s*(less|more|off|on|fewer|none|stop)\W*$", re.I)
_CMD = {"fewer": "less", "none": "off", "stop": "off"}
_CHAT_END = re.compile(r"^\s*CHAT\s*:\s*(?:end|over|done|stop)\W*$", re.I)
_FOOD = re.compile(r"\b(food|drink|refreshment|eating|drinking)\b", re.I)


def split(text):
    """Pull the BANTER: line out of a reply -> (the rest, "less" | "more" | "off" | "on" | None)."""
    keep, cmd = [], None
    for line in (text or "").splitlines():
        m = _LINE.match(line)
        if m:
            cmd = _CMD.get(m.group(1).lower(), m.group(1).lower())
        else:
            keep.append(line)
    return "\n".join(keep).strip(), cmd


def split_chat(text):
    """Pull the CHAT: end line out of a reply -> (the rest, whether the player wrapped the chat up)."""
    keep = [line for line in (text or "").splitlines() if not _CHAT_END.match(line)]
    return "\n".join(keep).strip(), len(keep) < len((text or "").splitlines())


def activity(s):
    """What the player is doing, for when they like to talk: flying, dead, eating, in combat, resting (inn or city),
    or questing."""
    if s.get("on_taxi"):
        return "flying"
    if s.get("dead"):
        return "dead"
    if any(_FOOD.search(str(b.get("name") or "")) for b in s.get("buffs") or () if isinstance(b, dict)):
        return "eating"
    if s.get("combat"):
        return "in combat"
    return "resting" if s.get("resting") else "questing"


DOWNTIME = ("flying", "dead", "eating")


def ago(seconds):
    """'just now', '25 minutes ago', '3 hours ago', '2 days ago'."""
    s = max(0, int(seconds))
    if s < 120:
        return "just now"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if s >= size * (2 if unit != "minute" else 1):
            n = s // size
            return "%d %s%s ago" % (n, unit, "" if n == 1 else "s")
    return "just now"


def openers(lines, n=5):
    """Words the last `n` lines kept opening with (twice or more) -> ['oi', ...]."""
    seen = {}
    for t in lines[-n:]:
        w = re.findall(r"[A-Za-z']+", t or "")
        if w:
            seen[w[0].lower()] = seen.get(w[0].lower(), 0) + 1
    return [w for w, k in seen.items() if k >= 2]


def variety_note(lines):
    """What the recent lines overused: their opening words, ending on a question."""
    notes = []
    words = openers(lines)
    if words:
        notes.append("Don't open with %s again." % " or ".join('"%s"' % w for w in words))
    if sum((t or "").rstrip().endswith("?") for t in lines[-3:]) >= 2:
        notes.append("Don't end on a question this time.")
    return (" " + " ".join(notes)) if notes else ""


class Banter:
    """coach: the Coach whose character speaks the asides."""

    def __init__(self, coach):
        self.coach = coach
        self.wait_s = None          # how long this round of quiet lasts before an aside (randomised banter_minutes)
        self.since = None           # when it started (time.time())
        self.said = []              # the character's last lines (asides and chat replies): no repeats, variety
        self.awaiting = None        # (coach clock time, activity) of the last aside the player hasn't answered yet
        self.clock_t = None         # when the bond clock last ran
        self.counted = False        # this run counted as a session
        self.session_t = None       # when this session started (first live frame)
        self.moment = None          # the moment waiting for a reaction: {"kind", "text", "t"}
        self.cooldown = {}          # moment kind -> when the character last spoke up for one
        self.deaths = []            # times of recent deaths (death streaks)
        self.mood = None            # (mood, why, time, strength)
        self.fight_min_hp = None    # the lowest health in the fight going on (close calls)
        self.was_combat = False
        self.kills_seen = set()     # elite / rare targets already counted
        self.gone = False           # the game went away (no frames): a goodbye was due
        self.said_goodbye = False   # this session's goodbye was said (one a session)
        self.last_frame = None
        # () -> True when no WoW window exists (the app's capture sets it; None: unknown)
        self.window_gone: Optional[Callable[[], bool]] = None
        self.lock = threading.Lock()    # self.moment: set on the capture thread, taken on the coach thread

    # ---- the frame: moments, the diary, the mood --------------------------------------------------------------
    def observe(self, triggers, state):
        """A frame and its triggers (Coach.push, the capture thread, 5 a second): note the moments worth a reaction
        and the diary. Every frame reads only GameState.vitals(); the whole summary only for a frame with triggers or
        a close call or big kill to describe."""
        if state is None:
            return
        try:
            self._observe(triggers, state)
        except Exception as e:  # noqa: BLE001 - an extra: never costs the frame its triggers
            log_once("banter.observe", e)

    def _observe(self, triggers, state):
        v = state.vitals()
        full = {}

        def summary():
            if not full:
                try:
                    full.update(state.summary())
                except Exception as e:  # noqa: BLE001
                    log_once("banter.summary", e)
            return full
        s = summary() if triggers else {}
        now = time.time()
        zone = s.get("subzone") or s.get("zone") or "somewhere"
        where = s.get("zone") or zone
        for t in triggers or ():
            k, f = getattr(t, "kind", None), getattr(t, "facts", None) or {}
            if k == "died":
                self.deaths = [x for x in self.deaths if now - x < 1800] + [now]
                by = f.get("arg") or f.get("killer")
                if len(self.deaths) >= 3:
                    self._moment("death_streak", "Died for the %s time in half an hour%s, in %s" % (
                        _nth(len(self.deaths)), " (to %s)" % by if by else "", zone), diary=True)
                else:
                    self._moment("died", "Died%s at level %s in %s" % (
                        " to %s" % by if by else "", s.get("level"), zone), diary=True)
            elif k == "level_up":
                self._moment("level_up", "Reached level %s in %s" % (s.get("level"), where), diary=True)
            elif k == "zone_changed":
                m = self.coach.deps.memory
                if m is not None and s.get("zone") and m.zone_seen(s["zone"]):
                    self._moment("new_zone", "First time in %s together" % s["zone"])
            elif k == "stuck_on_step":
                self._moment("long_grind", "Spent %s minutes on one step in %s" % (f.get("minutes"), zone),
                             diary=bool((f.get("minutes") or 0) >= 15))
            elif k == "flight_taken":
                self._moment("flight", "Took a flight%s" % (" to %s" % s["taxi_to"] if s.get("taxi_to") else ""))
            elif k == "hearthed":
                self._moment("hearth", "Hearthed out of %s" % where)
        combat, hp = bool(v.get("combat")), v.get("hp")
        if combat and isinstance(hp, (int, float)) and not isinstance(hp, bool):
            self.fight_min_hp = hp if self.fight_min_hp is None else min(self.fight_min_hp, hp)
        if self.was_combat and not combat:
            if not v.get("dead") and self.fight_min_hp is not None and self.fight_min_hp <= CLOSE_CALL_HP:
                s = summary()
                self._moment("close_call", "Survived a fight at %d%% health in %s" % (
                    self.fight_min_hp, s.get("subzone") or s.get("zone") or "somewhere"), diary=True)
            self.fight_min_hp = None
        self.was_combat = combat
        tg = v.get("target") or {}
        if tg.get("dead") and (tg.get("classification") or "").lower() in BIG_KILLS and tg.get("name"):
            key = (tg["name"], tg.get("level"))
            if key not in self.kills_seen:
                self.kills_seen.add(key)
                s = summary()
                self._moment("big_kill", "Took down %s (%s) in %s" % (
                    tg["name"], tg["classification"], s.get("subzone") or s.get("zone") or "somewhere"), diary=True)

    def _moment(self, kind, text, diary=False):
        """A moment: into the diary (when worth keeping), the mood, and maybe a reaction."""
        c, now = self.coach, time.time()
        chance, cool, mood, strength = MOMENTS[kind]
        if diary and c.deps.memory is not None:
            c.deps.memory.diary_add(kind, text)
        if self.mood is None or strength >= self.mood[3] or now - self.mood[2] > MOOD_S / 2:
            self.mood = (mood, text[:80], now, strength)
        if now - self.cooldown.get(kind, -1e9) < cool or random.random() >= chance:
            return
        self._offer({"kind": kind, "text": text, "t": now})

    def _offer(self, moment):
        """Make `moment` the one waiting, unless a stronger one is still waiting (a goodbye always: the game is gone)."""
        with self.lock:
            cur = self.moment
            if cur is None or moment["kind"] == "goodbye" or MOMENTS[cur["kind"]][3] <= MOMENTS[moment["kind"]][3] \
                    or moment["t"] - cur["t"] > MOMENT_TTL_S:
                self.moment = moment

    def _take(self):
        """The waiting moment, now no longer waiting (or None)."""
        with self.lock:
            moment, self.moment = self.moment, None
            return moment

    def mood_line(self):
        """'Your mood right now: proud (Reached level 12 in Westfall).', or '' once it has settled."""
        if self.mood is None or time.time() - self.mood[2] > MOOD_S:
            return ""
        return "Your mood right now: %s (%s)." % (self.mood[0], self.mood[1])

    def reminder(self):
        """The stay-in-character line every request ends with, with the mood."""
        c = self.coach
        line = persona_mod.REMINDER % persona_mod.short_name(c.persona())
        mood = self.mood_line()
        return line + (" " + mood if mood else "")

    # ---- the clock: time together, sessions, replies, the game going away ---------------------------------------
    def tick(self):
        now = time.time()
        self._clock(now)
        self._reaction()
        if self._due(now):
            self.speak()

    def _live(self, now):
        c = self.coach
        return c.state is not None and now - (c.frame_t or 0) < FRESH_S

    def _clock(self, now):
        """Count the time the game runs with the coach as time played together, this run as a session (a greeting
        is due), and a game that went away after a while together (a goodbye is due)."""
        c = self.coach
        m = c.deps.memory
        live = self._live(now)
        if live and not self.counted:
            self.counted = True
            self.session_t = now
            last = m.bond.get("last_seen") if m is not None else 0
            if not last or now - last >= BREAK_S:         # a coach restart mid-session isn't a new session
                if m is not None:
                    m.bond_add(sessions=1)
                self._greeting(now, last)
        if live and self.gone:
            self.gone = False                          # back: a short "welcome back" if it was a while
            if self.last_frame and now - self.last_frame > GONE_S:
                self.session_t, self.said_goodbye = now, False
                self._greeting(now, self.last_frame)
        if m is not None and live and self.clock_t is not None:
            m.bond_add(hours=min(now - self.clock_t, 5.0) / 3600.0)
        if live:
            self.last_frame = c.frame_t
        elif self.counted and not self.gone and not self.said_goodbye and self.last_frame \
                and self.session_t and self.last_frame - self.session_t >= MIN_SESSION_S \
                and now - self.last_frame > (CLOSED_S if self._closed() else GONE_S):
            self.gone = self.said_goodbye = True
            self._offer({"kind": "goodbye", "t": now, "text": "The player just left the game (%s) after %d minutes "
                         "together" % ("closed it" if self._closed() else "logged out or went away",
                                       (self.last_frame - self.session_t) // 60)})
        self.clock_t = now if live else None

    def _closed(self):
        """Whether the WoW window is gone (the game closed), when the app can tell."""
        try:
            return bool(self.window_gone and self.window_gone())
        except Exception as e:  # noqa: BLE001 - unknown: treated as still there (the long wait applies)
            log_once("banter.window", e)
            return False

    def _greeting(self, now, last):
        """A login: greet them like someone you know, picking up from the diary."""
        m = self.coach.deps.memory
        when = ("you last played together %s" % ago(now - last)) if last else "this is your first session together"
        before = [x["text"] for x in (m.diary_recent(5) if m is not None else ())]
        self._offer({"kind": "greeting", "t": now, "text": "The player just logged in; %s.%s" % (
            when, (" Last time: " + "; ".join(before) + ".") if before else "")})
        self.mood = (MOMENTS["greeting"][2], "they're back", now, MOMENTS["greeting"][3])

    def _reaction(self):
        """The player answered the last aside (said or asked anything within REPLY_S): count it, per activity."""
        c = self.coach
        if self.awaiting is None:
            return
        at, act = self.awaiting
        if any(e["who"] == "player" and e["t"] >= at for e in list(c.convo)):    # (>=: one 15 ms clock tick)
            if c.deps.memory is not None:
                c.deps.memory.bond_add(replied=1, activity=act)
            self.awaiting = None
        elif c._now() - at > REPLY_S:
            self.awaiting = None

    # ---- when -----------------------------------------------------------------------------------------------------
    def _factor(self, act):
        """How much sooner or later than banter_minutes, for what the player is doing: downtime sooner, and what
        the replies taught (they chat while flying, ignore it mid-grind)."""
        f = 0.4 if act in DOWNTIME else 1.0
        m = self.coach.deps.memory
        rate = m.reply_rate(act) if m is not None else None
        if rate is not None:
            f *= 0.7 if rate >= 0.5 else (2.0 if rate < 0.15 else 1.0)
        return f

    def _due(self, now):
        c = self.coach
        if not c.persona() or not c.opt.get("banter"):
            self.since = self.wait_s = None
            self._take()
            return False
        with self.lock:
            if self.moment and now - self.moment["t"] > MOMENT_TTL_S:
                self.moment = None
            moment = self.moment
        if self.since is None:
            self.since, self.wait_s = now, float(c.opt.get("banter_minutes")) * 60 * random.uniform(0.6, 1.4)
        llm = c.llm
        model = c.cfg.get("fast_model")
        if not llm.enabled or llm.suspension_for(models_mod.provider(model)) or now < llm.backoff_until \
                or not llm.keys.get(models_mod.provider(model)):
            return False
        s = c._summary()
        if not (s.get("coach_flags") or {}).get("coach", True):
            return False                                  # "/coach off" in the game: the coach is quiet
        if moment and moment["kind"] == "goodbye":
            return True                                   # the game is gone: nothing else will be said
        # quest briefings gathering come first (a step video waiting for its moment doesn't hold the asides)
        if not self._live(now) or c.pending or not c.asks.empty() or c.briefings.accepted.queue:
            return False
        act = activity(s)
        if s.get("combat"):
            return False
        last = max(c.last_call, c.said_t or 0)            # the coach's last model call or spoken line
        if moment:
            return now - last >= MOMENT_QUIET_S
        if s.get("dead") and not s.get("corpse"):
            return False                                  # just died, not yet released: the callout's moment
        quiet = DOWNTIME_QUIET_S if act in DOWNTIME else QUIET_S
        return now - self.since >= float(self.wait_s or 0) * self._factor(act) and now - last >= quiet

    # ---- what -----------------------------------------------------------------------------------------------------
    def shape(self):
        """The shape this aside asks for (weighted; callbacks need a diary, follow-ups a recent aside)."""
        m = self.coach.deps.memory
        has_diary = bool(m is not None and m.diary_recent(1))
        rows = [(s, w) for s, w in SHAPES if (has_diary or "diary" not in s) and (self.said or "own last" not in s)]
        return random.choices([s for s, _ in rows], [w for _, w in rows])[0]

    def request(self, limit, moment=None, act=None):
        """The aside's request text (the trigger part of the user message)."""
        m = self.coach.deps.memory
        bond = m.bond_line() if m is not None else "You and this player: just met. Your bond: new."
        acts = m.activity_line() if m is not None else ""
        if acts:
            bond += " When they answer your asides: %s." % acts
        if moment is None:
            what = "Nothing in particular just happened: fill a quiet moment%s." % (
                " (they're %s)" % act if act and act != "questing" else "")
        elif moment["kind"] == "greeting":
            what = ("THE PLAYER JUST LOGGED IN: %s Greet them in character like someone you know, at your bond's stage; "
                    "pick up from last time if there is one." % moment["text"])
        elif moment["kind"] == "goodbye":
            what = ("THE PLAYER JUST LEFT: %s. One short goodbye in character, about something from today." %
                    moment["text"])
        else:
            what = "WHAT JUST HAPPENED: %s. React to it the way this character would." % moment["text"]
        now = time.time()
        diary = "\n".join("- %s: %s" % (ago(now - x["t"]), x["text"])
                          for x in (m.diary_recent(8) if m is not None else ())) or "(nothing yet)"
        recent = "; ".join('"%s"' % t for t in self.said[-5:]) or "(none yet)"
        shape = "a reaction to that moment, in whatever shape fits it" if moment else self.shape()
        return REQUEST % (what, bond, diary, shape, variety_note(self.said), recent, limit)

    def speak(self):
        """One aside (fast model, no tools): emitted as a spoken "banter" message, or nothing (SKIP, no answer)."""
        c = self.coach
        moment = self._take()
        if moment:
            self.cooldown[moment["kind"]] = time.time()
        self.since = self.wait_s = None
        model = c.cfg.get("fast_model")
        limit = c._stretch(int(c.opt.get("banter_words")))
        act = activity(c._summary())
        try:
            ctx = c.context(triggers=["banter"])
            trigger = self.request(limit, moment, act) + "\n" + self.reminder()
            user = c._user_message(ctx, trigger, triggers=["banter"], cc=c.llm.ref_marker(model))
            text, meta = c.llm.call_once(model, user, 400)
        except Exception as e:  # noqa: BLE001 - an aside is an extra; never breaks the coach
            log_once("banter.speak", e)
            return None
        c.last_call = time.time()
        text = c._directives(text or "")
        tone = c.tone
        if not text or is_skip(text):
            return None
        text = trim_words(text, limit)
        self.said = (self.said + [text])[-8:]
        c.history = (c.history + [text])[-10:]
        msg = {"kind": "banter", "text": text, "speak": True, "urgent": False,
               "model": c._model_note(model, meta), "t": time.time()}
        if tone:
            msg["tone"] = tone
        c.emit(msg)
        if moment is None or moment["kind"] != "goodbye":
            self.awaiting = (c._now(), act)
        if c.deps.memory is not None:
            c.deps.memory.bond_add(asides=1, activity=act)
        return msg

    # ---- chatting: the player answered ------------------------------------------------------------------------------
    def in_chat(self, before):
        """Whether a question continues a chat: in character with asides on, and the coach's last line (in `before`,
        the conversation before the question) was an aside or a chat reply at most REPLY_S ago."""
        c = self.coach
        if not before or not c.persona() or not c.opt.get("banter"):
            return False
        last = next((e for e in reversed(before) if e["who"] == "coach"), None)
        return last is not None and (last.get("kind") == "banter" or bool(last.get("chat"))) \
            and c._now() - last["t"] <= REPLY_S

    def chat_note(self, limit):
        """The request note for a chat reply (CHAT_NOTE, with what the recent lines overused)."""
        return CHAT_NOTE % (limit, variety_note(self.said))

    def chatted(self, text=None):
        """A chat reply went out: kept for variety, and the next unprompted aside is counted from now."""
        if text:
            self.said = (self.said + [text])[-8:]
        self.since = self.wait_s = None

    # ---- the player's say -----------------------------------------------------------------------------------------
    def adjust(self, cmd):
        """BANTER: less / more / off / on from a reply: saved like a setting."""
        c = self.coach
        if cmd in ("off", "on"):
            changes = {"banter": cmd == "on"}
        else:
            m = int(c.opt.get("banter_minutes"))
            changes = {"banter": True, "banter_minutes": max(2, min(60, int(round(m * STEP if cmd == "less" else
                                                                                  m / STEP))))}
        for k, v in changes.items():
            c.cfg[k] = v
            if c.deps.on_toggle:
                try:
                    c.deps.on_toggle(k, v)
                except Exception as e:  # noqa: BLE001
                    log_once("banter.save", e)
        self.since = self.wait_s = None
        return changes


def _nth(n):
    return {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}.get(n, "%dth" % n)
