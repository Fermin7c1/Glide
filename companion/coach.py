"""Coach: turns triggers + state + glide.db knowledge into short spoken/written advice.

With the knowledge layer (data/kb.db, knowledge/api.py; Coach(..., kb=...)) the coach also gets:
  - live callouts: context()["knowledge"], a block computed for this moment by api.KnowledgePack (why the current
    step exists with its computed XP numbers, the next step, pace vs a benchmark and the ETA to the next level, the
    expert heuristics that apply, the hostile target's card, the fact ids), at most ~2,500 tokens. One-shot call,
    fast model, the same brevity rules.
  - questions and reviews: a tool-use loop (_call_tools) over api.tool_definitions() / api.dispatch(); voice
    questions: fast model, 2 tool calls, 12 s; typed questions and pace reviews: deep model, 6 calls, 40 s; past
    the caps the next request forces the final answer (tool_choice none).
  - grounding: every reply ends with "FACTS: <ids>" (stripped like the WATCH line, never spoken or shown); each id
    is checked against kb.db and the result goes to data/live/grounding.jsonl and stats (cited / unsupported).
Without kb.db (kb=None, or the file missing) everything behaves as before.

The coach's parts: llm_client.py (the model calls, through its LLMClient), prompt_context.py (what the model is told),
coach_prompts.py (the fixed prompt texts), replies.py (what is done with a reply), coach_tools.py (its own tools and
spoken commands), offline_coach.py (callouts without a model), session_book.py (deaths, mount, step visits for the
knowledge layer) and coach_deps.py (what it uses from the app). The coach thread's background work is in tickers:
coach_briefings.py (quest briefings, step videos; texts and rules in briefing.py), reminder_ticker.py, reflector.py
(learning about the player) and route_keeper.py (the knowledge block and the way to the step)."""
import json
import os
import queue
import re
import sys
import threading
import time
from collections import deque

import coach_prompts as prompts
import coach_tools
import models as models_mod
import msg_kinds
import offline_coach
import prompt_context
import reminders as reminders_mod
from coach_briefings import Briefings
from coach_deps import CoachDeps
import websearch
from geo import Geo
from knowledge_layer import KnowledgeHandle, handle_view, knowledge_package, load_knowledge_layer, open_pack
from llm_client import LLMClient
from prompt_context import short_json
from reflector import Reflector
from reminder_ticker import ReminderTicker
from replies import EarlySpeech, is_skip, split_facts, split_watch, trim_words
from route_keeper import RouteKeeper
from session_book import SessionBook
from settings import Settings
from log import log_once

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
GROUNDING_LOG = os.path.join(ROOT, "data", "live", "grounding.jsonl")

CONVO_LABEL = {"answer": "coach answered", "callout": "coach callout", "review": "coach pace review",
               "briefing": "coach quest briefing", "reminder": "coach reminder"}


class Coach:
    # the knowledge layer lives in one KnowledgeHandle (self.kb_handle) that the app and the rules read too; these
    # are views of its parts
    pack, api, kb_reason = handle_view("pack"), handle_view("api"), handle_view("reason")

    @property
    def opt(self):
        """The llm settings, read through settings_manifest.json (defaults, ranges)."""
        return Settings("llm", self.cfg)

    def __init__(self, cfg, knowledge, emit, kb=None, offline_ok=False, rxp=None, pack=None, deps=None):
        """cfg: the "llm" config; knowledge: the glide.db reader (glidedb.GlideDB); emit: callback(message dict).
        kb: knowledge.store.KB or a path to kb.db (None: no knowledge layer, the coach runs as before).
        offline_ok: never runs a background thread (evals, tools): use answer_once(); without an API key the
        coach answers offline instead of calling Claude. rxp: RestedXP settings for the knowledge layer (ssf,
        group_quests, hardcore, dungeons). pack: a ready api.KnowledgePack (instead of kb). deps: what it uses
        from the app (coach_deps.CoachDeps). The coach thread starts with start(), once the app is wired."""
        self.cfg = cfg
        self.k = knowledge
        self._emit_raw = emit                 # callback(message dict); self.emit also keeps the recent log
        self.emit = self._emit
        self.deps = deps if deps is not None else CoachDeps()
        # the model calls; their messages go through self.emit as it is at the time (answer_once swaps it)
        self.llm = LLMClient(cfg, emit=lambda m: self.emit(m), system_fn=self._system_blocks, run_tool=self._run_tool,
                             catalog=self.deps.catalog, usage_fn=self.deps.usage_fn)
        self.grounding = {"cited": 0, "unsupported": 0}     # replies citing known facts / with unsupported claims
        self.offline_ok = offline_ok
        self.state = None
        self.clock = None                     # optional () -> now (evals replay old logs)
        self.kb_handle = KnowledgeHandle()    # the current pack / api / reason (see knowledge_layer)
        self.geo = Geo(knowledge, self.kb_handle)    # places from the current pack (glide.db without one)
        # the coach thread's inbox
        self.pending = []
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.asks = queue.Queue()
        self.recent_triggers = []
        self.last_call = 0
        # what was said: continuity, follow-up questions, the dashboard's cost estimates
        self.history = []                     # recent coach lines for continuity
        self.recent = deque(maxlen=40)        # recent messages / triggers / questions (recent_log tool)
        self.q_times = deque(maxlen=500)      # (time, "voice"|"typed") of questions: dashboard cost estimate
        self.convo = deque(maxlen=200)        # the player's questions and what the coach said (follow-up context)
        self.convo_tokens = None              # size of the last RECENT CONVERSATION section (dashboard estimate)
        self.q_rounds = {}                    # kind -> running mean of API rounds per question
        self.look_tokens_per_step = None      # measured size of the last route lookahead (tokens per step)
        # RestedXP's files on disk
        self.wow_dir = None                   # the WoW folder (RestedXP's files); looked up on first use
        self._hist = None                     # (character, read at, rxp_saved.history) for reviews
        self.session = SessionBook()          # deaths, mounted, step visits (knowledge layer bookkeeping)
        # knowledge layer
        self.rxp = rxp
        self._attach_knowledge(kb, pack)
        self.grounding_path = self.opt.get("grounding_log") or GROUNDING_LOG
        self._glock = threading.Lock()
        self._system = None                   # cached system blocks (stable text keeps the prompt cache warm)
        self._turn_kctx = None                # knowledge ctx of the turn being answered (tools use it)
        self._convo_before = None             # the conversation before the question being answered
        # the coach thread's background work, each with tick(): quest briefings and step videos, reminders,
        # reflection, and keeping the knowledge block and the way to the step fresh
        self.briefings = Briefings(self)
        self.reminder_ticker = ReminderTicker(self)
        self.reflector = Reflector(self)
        self.route = RouteKeeper(self)
        # one round of the coach thread after the questions, in this order
        self._jobs = (self.briefings.tick, self.reminder_ticker.tick, self.reflector.tick, self._callouts,
                      self.route.tick)
        self._thread = None

    def start(self):
        """Start the coach thread (the app calls it once the coach is built with everything it uses). A coach built
        with offline_ok never starts one."""
        if self.offline_ok or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="coach")
        self._thread.start()

    def _attach_knowledge(self, kb, pack):
        """Take the knowledge layer: a ready pack, or one opened over kb (an open KB or a path to kb.db). Sets api,
        pack and kb_reason (why there is none), in one go."""
        api = why = None
        if pack is not None:
            api = sys.modules.get(type(pack).__module__)
            pack, why = (pack, None) if pack.ok else (None, pack.reason)
            if pack is not None and pack.geo is None:
                pack.geo = self.geo           # a pack made elsewhere (evals, tests) gets the coach's directions
        elif kb is not None:
            if isinstance(kb, str):
                kb, api, why = load_knowledge_layer(kb)
            else:
                api, why = knowledge_package()
            if api is not None and kb is not None:
                pack, why = open_pack(api, kb, self.rxp, self.geo)
        self.kb_handle.set(pack, api, why)

    # ------------------------------------------------------------------------------------------
    def _now(self):
        try:
            return float(self.clock()) if self.clock else time.time()
        except Exception as e:  # noqa: BLE001
            log_once("coach.clock", e)
            return time.time()

    def _emit(self, msg):
        if msg.get("kind") in msg_kinds.COACH_SAID and msg.get("text"):
            self.convo.append({"t": self._now(), "who": "coach", "kind": msg["kind"],
                               "text": msg["text"]})
        if msg.get("kind") in msg_kinds.RECENT and msg.get("text"):
            self.recent.append({"t": msg.get("t") or self._now(), "type": "message", "kind": msg["kind"],
                                "text": msg["text"]})
        self._emit_raw(msg)

    def push(self, triggers, state):
        """A new frame's game state and the triggers it raised (from the capture thread): queue them for the coach
        thread."""
        self.state = state
        self.session.observe(state, self._now())
        if not triggers:
            return
        now = self._now()
        with self.lock:
            self.pending += triggers
            self.recent_triggers = (self.recent_triggers + [(t.kind, t.facts) for t in triggers])[-12:]
            for t in triggers:
                self.recent.append({"t": now, "type": "trigger", "kind": t.kind, "facts": short_json(t.facts, 300)})
        self.wake.set()

    def brief(self, qid):
        """A quest was accepted: brief it (objective + explanation) once accepts stop coming for a moment."""
        if self.opt.get("quest_briefings"):
            self.briefings.add(qid, self._now())
            self.wake.set()

    def ask(self, question, voice=False):
        """The player asks something (typed in the dashboard, or spoken: voice): answered on the coach thread."""
        self.q_times.append((time.time(), "voice" if voice else "typed"))
        self.recent.append({"t": self._now(), "type": "question", "kind": "voice" if voice else "typed",
                            "text": question})
        self.asks.put((question, voice))
        self.wake.set()

    def book(self):
        """deaths_session, deaths_recent, has_mount, in_group, visits (heuristics state + session_review)."""
        return self.session.summary(self._now())

    # ---- what the app and the dashboard read of the model calls (self.llm) --------------------
    @property
    def stats(self):
        """The session totals the dashboard shows (state["coach"]): the model calls' counters with the grounding
        counts (cited / unsupported) after tool_calls, where they always were."""
        out = {}
        for k, v in self.llm.stats.items():
            out[k] = v
            if k == "tool_calls":
                out.update(self.grounding)
        return out

    @property
    def enabled(self):
        return self.llm.enabled

    @property
    def suspended(self):
        return self.llm.suspended

    @property
    def path_stats(self):
        return self.llm.path_stats

    @property
    def catalog(self):
        return self.llm.catalog

    def describe_models(self):
        return self.llm.describe_models()

    def announce_models(self, changes, startup=False):
        return self.llm.announce_models(changes, startup)

    def model_name(self, model):
        return self.llm.model_name(model)

    # ------------------------------------------------------------------------------------------
    def _loop(self):
        """The coach thread: questions first, then quest briefings and step videos, reminders, reflection, the
        waiting callouts, and keeping the knowledge block and the route fresh (self._jobs). Wakes on new work, else
        every second."""
        while True:
            self.wake.wait(1.0)
            self.wake.clear()
            try:
                while not self.asks.empty():
                    q, voice = self.asks.get_nowait()
                    self._respond([], question=q, voice=voice)
                for job in self._jobs:
                    job()
            except Exception as e:  # never let the coach thread die
                self.emit({"kind": "error", "text": "coach error: %s" % e, "speak": False})
            # re-check soon if things are waiting
            if self.pending or self.briefings.busy():
                time.sleep(0.5)
                self.wake.set()

    def _callouts(self):
        """The waiting callouts, when it is time for them (see _take_batch)."""
        batch = self._take_batch()
        if batch:
            self._respond(batch)

    def _take_batch(self):
        """The waiting triggers, once at least min_interval has passed since the last call (urgent ones at once),
        else None."""
        with self.lock:
            if self.pending:
                urgent = any(t.priority == 1 for t in self.pending)
                gap = time.time() - self.last_call
                if urgent or gap >= self.opt.get("min_interval"):
                    batch, self.pending = self.pending, []
                    return batch
        return None

    def route_view(self, s):
        """The way to the current step for the dashboard (RouteKeeper.view)."""
        return self.route.view(s)

    def note_mark(self, note, last_text):
        """A '/glide mark <note>' feedback flag (Reflector.note_mark)."""
        self.reflector.note_mark(note, last_text)

    def reflect(self, events=None):
        """One reflection pass now (Reflector.reflect); returns the changes."""
        return self.reflector.reflect(events)

    def swap_kb(self, path):
        """Switch to a freshly built kb.db (the background refresh's data/kb.next.db) without a restart.
        Returns None, or why the new one wasn't used (the old pack stays)."""
        kb, api, why = load_knowledge_layer(path)
        if kb is None:
            return why
        pack, why = open_pack(api, kb, self.rxp, self.geo)
        if pack is None:
            return why
        try:
            pack.warm()
        except Exception as e:  # noqa: BLE001 - only a head start; the first use loads what it needs
            log_once("coach.warm", e)
        with self.lock:
            self.kb_handle.set(pack, api, None)
            self._system = None               # the glossary may have changed: rebuild the system prompt
            self.route.warm_key, self.route.warmed = None, True
        return None

    # ---- the model's tools: the knowledge layer's and the coach's own (coach_tools.py) ---------
    def _tools(self, web=False, model=None):
        """The tool definitions sent with questions (and, for the shared cache prefix, with callouts). web: add web
        search (Anthropic's server tool for Claude models, else the local one when a search API key is set)."""
        defs = list(self.api.tool_definitions()) if self.api is not None else []
        if self.deps.reminders is not None:
            defs += coach_tools.REMINDER_TOOLS
        if self.deps.memory is not None:
            defs += coach_tools.MEMORY_TOOLS
        if web and not self.llm.web_off:
            if models_mod.provider(model or "") == "anthropic":
                defs.append(websearch.server_tool(self.opt.get("web_search_max"), model,
                                                  basic=self.llm.web_basic or not self.opt.get("web_search_filtered")))
            elif websearch.backend():
                defs.append(websearch.LOCAL_DEF)
        return defs

    def web_for(self, voice):
        """Is web search offered for this kind of question (dashboard switches)?"""
        return bool(self.opt.get("web_search_voice" if voice else "web_search"))

    def _run_tool(self, name, args, ctx, budget_s):
        """One tool call from the tool-use loop: the coach's own tools (reminders, memory, the local web search) here,
        everything else through the knowledge layer on the turn's live context."""
        if name in coach_tools.LOCAL_TOOLS:
            return self._local_tool(name, args)
        return self.api.dispatch(name, args, ctx, budget_s=min(self.api.TOOL_BUDGET_S, max(1.0, budget_s)))

    def _local_tool(self, name, args):
        """One of the coach's own tools (coach_tools.LOCAL_TOOLS): the local web search, memory, reminders."""
        if name == "web_search":
            self.llm.count_web_search()
            return websearch.search(args.get("query"))
        if name.startswith("memory_"):
            return coach_tools.run_memory_tool(self.deps.memory, name, args, self.emit)
        if self.deps.reminders is None:
            return {"error": "reminders are not available"}
        return coach_tools.run_reminder_tool(self.deps.reminders, name, args, self._summary(), self._now())

    def _summary(self):
        """The game state summary now ({} before the first frame)."""
        return self.state.summary() if self.state is not None else {}

    # ---- memory: what the coach learns about the player across sessions (memory.py) -----------
    def _memory_command(self, question):
        """A spoken or typed memory request handled locally (coach_tools.memory_command), or None."""
        return coach_tools.memory_command(self.deps.memory, question, self.emit)

    # ---- reminders (reminders.py) and switches said out loud --------------------------------
    def _reminder_command(self, question):
        """A spoken or typed reminder request handled locally (coach_tools.reminder_command), or None for the AI."""
        ai_ok = self.llm.enabled and self.llm.keys.get(models_mod.provider(self.cfg.get("fast_model")))
        return coach_tools.reminder_command(self.deps.reminders, question, ai_ok, self._summary, self._now())

    def _local_command(self, question):
        """Push-to-talk / typed switches handled without the AI ('quest briefings off', 'turn on videos'; see
        coach_tools.feature_switch): applied now and saved. Returns the confirmation, or None for an ordinary
        question."""
        switch = coach_tools.feature_switch(question)
        if switch is None:
            return None
        key, on, label = switch
        self.cfg[key] = on
        if not on:
            self.briefings.switched_off(key)
        if self.deps.on_toggle:
            try:
                self.deps.on_toggle(key, on)
            except Exception as e:  # noqa: BLE001
                log_once("coach.on_toggle", e)
        return "%s %s." % (label, "on" if on else "off")

    # ------------------------------------------------------------------------------------------
    def kctx(self, summary, triggers=None):
        """The knowledge layer's live context for this moment."""
        me = None
        try:
            me = self.state.player_geo() if self.state is not None else None
        except Exception as e:  # noqa: BLE001
            log_once("coach.player_geo", e)
        return self.pack.context(summary, triggers=triggers, book=self.book(), recent=list(self.recent),
                                 now=self._now(), me=me)

    def context(self, deep=False, triggers=None, lookahead=None):
        """Everything the model sees about the game; with the knowledge layer also ctx["knowledge"] (the block for
        this moment, computed for these trigger kinds). lookahead = how many RestedXP steps after the current one to
        add as ctx["guide_lookahead"] (spoken questions 10, typed questions and reviews 40; callouts none)."""
        st = self.state
        s = st.summary() if st else {}
        ctx = self._context(deep, s)
        self._turn_kctx = None
        k = None
        if self.pack is not None:
            try:
                k = self.kctx(s, triggers)
                self._turn_kctx = k
                ctx["knowledge"] = self.pack.block(k, triggers)
            except Exception as e:  # noqa: BLE001 - the block is an extra; never block a callout on it
                ctx["knowledge"] = {"unavailable": "knowledge block failed: %s" % e}
        rem = self.deps.reminders
        if rem is not None and rem.items:
            ctx["reminders"] = [{"id": x["id"], "what": x["what"], "when": reminders_mod.describe_when(x)}
                                for x in rem.list()]
        if deep:
            hist = self.level_history(s)
            if hist:
                ctx["level_history"] = hist
        if lookahead and k is not None and self.api is not None and hasattr(self.api, "walk_to_step"):
            # how to get to the current step (walk over the walkable-space graph, flights as flown): the route
            # worker's, or computed now when it has none yet
            w = self.route.last_walk if self.route.last_route is not None else self.api.walk_to_step(k)
            if w:
                ctx["walk_to_step"] = {kk: vv for kk, vv in w.items() if kk not in ("points", "map")}
        if lookahead:
            look = self._lookahead(k, s, lookahead)
            if look:
                ctx["guide_lookahead"] = look
                n = len(look.get("steps") or look.get("lines") or ())
                if n and look.get("steps"):
                    self.look_tokens_per_step = len(json.dumps(look, separators=(",", ":"), default=str)) / 4.0 / n
        return ctx

    def _lookahead(self, kctx, s, n):
        """The current RestedXP step plus the next n, from the knowledge layer's analysed route (RestedXP's own step
        numbers, kind, XP, quests, where); without it, the guide's raw accept/turn-in/complete lines from the current
        step on (glide.db, step numbers approximate)."""
        if kctx is not None and self.api is not None:
            try:
                r = self.api.route_steps(kctx, n + 1)
                if r.get("steps"):
                    return {"note": "the current step first, then the next %d RestedXP steps in order" % n,
                            "steps": r["steps"]}
            except Exception as e:  # noqa: BLE001 - glide.db's steps below instead
                log_once("coach.route_steps", e)
        if not s.get("guide"):
            return None
        cur = s.get("step") or 0
        rows = [r for r in self._rxp_upcoming(s) if (r.get("step") or 0) >= cur]
        if not rows:
            return None
        last = sorted({r["step"] for r in rows})[:n + 1][-1]
        return {"note": "raw guide lines from about the current step on (step numbers approximate)",
                "lines": [r for r in rows if r["step"] <= last]}

    def _rxp_upcoming(self, s):
        """The guide's raw accept / turn-in / complete lines: from the knowledge pack, glide.db without one."""
        pack = self.pack
        if pack is not None:
            return pack.rxp_upcoming(s.get("guide"), s.get("guide_group"), s.get("faction"))
        return self.k.rxp_upcoming(s.get("guide"))

    def _quest_name(self, qid):
        """A quest's name: from the knowledge pack, glide.db without one."""
        pack = self.pack
        if pack is not None:
            return pack.quest_name(qid)
        return self.k.quest(qid, detail=False).get("name")

    def _context(self, deep=False, s=None):
        """The game context for the model (prompt_context.game_context) for summary s (default: now)."""
        st = self.state
        if s is None:
            s = st.summary() if st else {}
        d = self.deps
        return prompt_context.game_context(s, st, self.k, self.geo, self.pack, deep=deep, watched_fn=d.watched_fn,
                                           gear_fn=d.gear_fn, system_fn=d.system_fn,
                                           talent_plan=self.talent_pick(s))

    def _wow_folder(self):
        """The WoW folder (RestedXP's saved files), looked up once ("" when it isn't found)."""
        if self.wow_dir is None:
            import health
            self.wow_dir = health.wow_dir() or ""
        return self.wow_dir

    def level_history(self, s, n=6):
        """RestedXP's record of this character's last n levels (time, XP sources, deaths) and the account's fastest
        time for each, plus the best time for the current level (rxp_saved.py; from disk, at most once a minute)."""
        name = s.get("name")
        if not name:
            return None
        hit = self._hist
        if hit and hit[0] == name and time.time() - hit[1] < 60:
            h = hit[2]
        else:
            try:
                import rxp_saved
                h = rxp_saved.history(self._wow_folder(), name)
            except Exception as e:  # noqa: BLE001 - history is an extra
                log_once("coach.history", e)
                h = None
            self._hist = (name, time.time(), h)
        if not h:
            return None
        out = {"note": "from RestedXP's levelling tracker (minutes of /played per level; best = the account's "
                       "fastest character for that level)", "levels": h["levels"][-n:]}
        b = h["bests"].get(s.get("level"))
        if b:
            out["best_for_current_level"] = b
        return out

    def talent_pick(self, s):
        """The next talent in RestedXP's build for this character (talents.next_pick), or None."""
        try:
            import talents as talents_mod
            return talents_mod.next_pick(talents_mod.builds(self._wow_folder(), s.get("class")), s.get("talents"),
                                         s.get("level"))
        except Exception as e:  # noqa: BLE001 - an extra; never breaks the context
            log_once("coach.talent_pick", e)
            return None

    # ------------------------------------------------------------------------------------------
    def _respond(self, batch, question=None, voice=False):
        """One coach turn (callout batch or question). Returns the emitted message dict, or None."""
        if question:
            done = self._answer_locally(question, voice)
            if done:
                return done
        # spoken questions mid-play go to the fast model with a short spoken answer, unless configured
        deep = (bool(question) and not (voice and self.opt.get("voice_question_model") == "fast")) \
            or any(t.deep for t in batch)
        triggers = [t.kind for t in batch] if batch else (["question"] if question else None)
        # route lookahead: spoken questions the next 10 RestedXP steps, typed questions and reviews the next 40
        lookahead = self.opt.get("voice_lookahead_steps") if (question and voice) else \
            (self.opt.get("lookahead_steps") if (question or deep) else None)
        ctx = self.context(deep=deep, triggers=triggers, lookahead=lookahead)
        kctx = self._turn_kctx
        trigger = self._turn_text(batch, question, voice)
        # out of credits / bad key: no automatic calls at all. A question the player asks tries once more
        # (that's how it resumes after topping up); transient errors back off instead of hammering.
        model = self.cfg.get("deep_model" if deep else "fast_model")
        llm = self.llm
        prov = models_mod.provider(model)
        held = llm.suspension_for(prov)
        blocked = (held and not question) or (time.time() < llm.backoff_until and not question)
        if not llm.enabled or blocked:
            self.last_call = time.time()
            return self._emit_offline(question, self.offline(batch, ctx, question),
                                      bool(batch) and min(t.priority for t in batch) <= 2 or bool(question),
                                      "offline" + (" (%s)" % held if held else ""))
        if not llm.keys.get(prov):
            text = self.offline(batch, ctx, question)
            if question:
                text = "No %s for %s; pick another model in the dashboard. %s" % (
                    {"openrouter": "OPENROUTER_API_KEY", "gemini": "GEMINI_API_KEY"}.get(prov, "ANTHROPIC_API_KEY"),
                    model, text or "")
            return self._emit_offline(question, text, bool(question), "offline (no %s key)" % prov)
        user = self._user_message(ctx, trigger, question=bool(question), triggers=triggers if batch else None,
                                  cc=llm.ref_marker(model))
        limit = self._word_limit(question, voice, deep)
        urgent = (bool(batch) and min(t.priority for t in batch) == 1) or voice
        msg_kind = "answer" if question else ("review" if deep else "callout")
        speaks = bool(question) or not deep or self.opt.get("speak_reviews")
        speak_fn = self.deps.speak_fn
        speech = EarlySpeech(lambda t, u: speak_fn(t, msg_kind, u), limit, urgent) \
            if speak_fn and speaks else None
        text, meta = self._ask_model(model, user, kctx, question, voice, deep, speech)
        self.last_call = time.time()
        if not text and question and llm.suspended and llm.suspended_for == prov:
            msg = {"kind": "answer", "text": self.offline([], ctx, question), "speak": bool(voice),
                   "model": "offline (%s)" % llm.suspended}
            self.emit(msg)
            return msg
        if question and text:
            self._count_rounds("voice" if voice else "typed", meta)
        facts, had_facts = [], False
        if text:
            text, facts, had_facts = self._directives(text, with_facts=True)
        if text and not question and is_skip(text):
            return None
        if text:
            text = trim_words(text, limit)
        if not text:
            return None
        self.history.append(text)
        self.history = self.history[-10:]
        msg = {"kind": msg_kind, "text": text, "speak": speaks, "urgent": urgent,
               "model": self._model_note(model, meta), "t": time.time()}
        if speech is not None and speech.said:
            msg["spoken"] = speech.spoken          # already said while it streamed: the app says only the rest
        if meta.get("tool_calls"):
            msg["tool_calls"], msg["tools"] = meta["tool_calls"], meta["tools"]
        if meta.get("web"):
            msg["web_sources"] = meta["web"][:5]
            msg["model"] += " · web search"
        if self.pack is not None:
            self._ground(msg, facts, had_facts)
        self.emit(msg)
        return msg

    def _answer_locally(self, question, voice):
        """Note the question in the conversation, and answer it at once when it is a switch, memory or reminder
        request the coach handles itself. -> the emitted answer, or None."""
        self._convo_before = list(self.convo)
        self.convo.append({"t": self._now(), "who": "player", "kind": "voice" if voice else "typed",
                           "text": question})
        done = self._local_command(question) or self._memory_command(question) or self._reminder_command(question)
        if not done:
            return None
        msg = {"kind": "answer", "text": done, "speak": True, "model": "local", "t": time.time()}
        self.emit(msg)
        return msg

    @staticmethod
    def _turn_text(batch, question, voice):
        """The request's last part: the player's question (with a note on spoken ones), or the triggers."""
        if not question:
            return "Triggers since last callout: " + short_json([{"kind": t.kind, **t.facts} for t in batch], 800)
        text = "The player asks: " + question
        if voice:
            text += ("\n(Asked out loud mid-play via push-to-talk; the question is a speech-to-text "
                     "transcript and may contain misheard names. Answer for speech: max 25 words, "
                     "the direct answer first.)")
        return text

    def _emit_offline(self, question, text, speak, model_note):
        """Send an offline answer or callout (when there is one). -> the message, or None."""
        if not text:
            return None
        msg = {"kind": "answer" if question else "callout", "text": text, "speak": speak, "model": model_note}
        self.emit(msg)
        return msg

    def _word_limit(self, question, voice, deep):
        """How many words the reply may have: spoken and typed answers, pace reviews, callouts."""
        if question:
            return self.opt.get("answer_words") if voice else self.opt.get("typed_answer_words")
        return self.opt.get("review_words") if deep else self.opt.get("callout_words")

    def _ask_model(self, model, user, kctx, question, voice, deep, speech):
        """Questions and reviews with the knowledge layer go through the tool-use loop; callouts (and models
        without tools) are one call. -> (text, meta)."""
        max_tokens = 3000 if deep else 600
        if self.pack is not None and kctx is not None and (question or deep) and self._tools_ok(model):
            calls, secs = self._tool_caps(voice)
            web = bool(question) and self.web_for(voice)
            return self.llm.call_tools(model, [{"role": "user", "content": user}], self._tools(web, model),
                                       calls, secs, ctx=kctx, max_tokens=max_tokens, deep=deep, on_text=speech)
        return self.llm.call_once(model, user, max_tokens, deep=deep, tools=self._shared_tools(model), on_text=speech)

    def _tools_ok(self, model):
        """Whether the model can use tools (the catalog says; unknown models are assumed to)."""
        catalog = self.llm.catalog
        return catalog.supports_tools(model) if catalog is not None else True

    def _shared_tools(self, model):
        """The tools a one-shot call sends (tool_choice none) only to share the questions' cached prefix, on
        providers that cache explicitly; else None."""
        share = self.pack is not None and self._tools_ok(model) and self.opt.get("callout_tools") \
            and self.llm.caching(model) == "explicit"
        return self._tools(self.web_for(True), model) if share else None

    def _count_rounds(self, kind, meta):
        """Keep the running mean of API rounds per question kind (the dashboard's cost estimate)."""
        r = (meta.get("tool_calls") or 0) + 1
        prev = self.q_rounds.get(kind)
        self.q_rounds[kind] = r if prev is None else prev * 0.8 + r * 0.2

    def _model_note(self, model, meta):
        """'<model> 1.2s · 2 tool calls': which model answered, how fast, with how many lookups."""
        note = "%s %.1fs" % (model, self.llm.last_latency)
        if meta.get("tool_calls"):
            note += " · %d tool call%s" % (meta["tool_calls"], "" if meta["tool_calls"] == 1 else "s")
        return note

    def conversation(self, items=None, now=None):
        """The last question/answer exchanges (and, briefly, what else the coach said around them) within
        conversation_minutes, oldest first -> list of lines. conversation_turns = how many of the player's questions
        (with the coach's replies) to include; 0 = none."""
        turns = int(self.opt.get("conversation_turns") if items is None else items)
        mins = float(self.opt.get("conversation_minutes"))
        if turns <= 0 or mins <= 0:
            return []
        now = self._now() if now is None else now
        src = self._convo_before
        src = list(self.convo) if src is None else src
        win = [e for e in src if now - e["t"] <= mins * 60]
        qs = [i for i, e in enumerate(win) if e["who"] == "player"]
        start = qs[-turns] if len(qs) >= turns else (qs[0] if qs else len(win))
        keep = []
        for i, e in enumerate(win):
            if i >= start and (e["who"] == "player" or e["kind"] == "answer"):
                keep.append(e)
            elif e["who"] == "coach" and e["kind"] != "answer" and now - e["t"] <= 300:
                keep.append(e)                 # other things the coach said in the last 5 minutes
        others = [e for e in keep if e["who"] == "coach" and e["kind"] != "answer"]
        drop = {id(e) for e in others[:-4]}     # at most the 4 latest callouts / briefings / reminders
        lines = []
        for e in keep:
            if id(e) in drop:
                continue
            ago = int(round((now - e["t"]) / 60.0))
            when = "just now" if ago <= 0 else "%d min ago" % ago
            who = ("you asked (%s)" % ("spoken" if e["kind"] == "voice" else "typed")) if e["who"] == "player" \
                else CONVO_LABEL.get(e["kind"], "coach")
            txt = " ".join(str(e["text"]).split())
            lines.append("- %s · %s: %s" % (when, who, txt if len(txt) <= 400 else txt[:399] + "…"))
        return lines

    def _user_message(self, ctx, trigger, question=False, triggers=None, cc=None):
        """The user content for this turn (prompt_context.user_message) with its recent section: for a question
        the conversation so far, for a callout the coach's last lines."""
        if question:
            conv = self.conversation()
            sec = ("RECENT CONVERSATION (oldest first; the question may refer to these: \"it\", \"that quest\", "
                   "\"the other one\", \"you said\"; spoken questions are speech-to-text):\n%s" %
                   ("\n".join(conv) if conv else "(none)"))
            self.convo_tokens = len(sec) / 4.0
            self._convo_before = None
        else:
            sec = "RECENT COACH LINES:\n%s" % ("\n".join(self.history[-5:]) or "(none)")
        return prompt_context.user_message(ctx, trigger, sec, question=question, triggers=triggers, cc=cc)

    # ------------------------------------------------------------------------------------------
    def _ground(self, msg, facts, had_facts):
        """Grounding verifier: check each FACTS id against kb.db, annotate the message, count, and log the record
        to data/live/grounding.jsonl. Never blocks or changes the answer."""
        mem_ids = [f for f in facts if re.fullmatch(r"m\d+", f.lower())]
        if mem_ids and self.deps.memory is not None:
            self.deps.memory.mentioned({int(f[1:]) for f in mem_ids})
        facts = [f for f in facts if f not in mem_ids]
        web = [f for f in facts if f.lower().startswith("web:")]
        try:
            known, unknown = self.pack.verify_facts([f for f in facts if f not in web])
        except Exception as e:  # noqa: BLE001
            log_once("coach.verify_facts", e)
            known, unknown = [], [f for f in facts if f not in web]
        known = list(known) + web
        had_numbers = bool(re.search(r"\d", msg.get("text") or ""))
        msg["facts"] = facts
        if unknown:
            msg["facts_unknown"] = unknown
        if not had_facts:
            msg["facts_missing"] = True
        if known:
            self.grounding["cited"] += 1
        if unknown or (had_numbers and not had_facts):
            self.grounding["unsupported"] += 1
        self._log_grounding({"t": msg.get("t") or time.time(), "kind": msg.get("kind"), "text": msg.get("text"),
                             "facts": facts, "unknown_ids": unknown, "had_numbers": had_numbers})

    def _log_grounding(self, rec):
        path = self.grounding_path
        if not path:
            return
        try:
            with self._glock:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    def _system_blocks(self, cc=None):
        """System prompt blocks: the coach rules, plus with the knowledge layer its rules and glossary. The text is
        built once so the prompt cache entry stays valid; `cc` is this call's cache marker (see cache_marker)."""
        if self._system is None:
            rows = None
            if self.pack is not None:
                try:
                    rows = self.pack.glossary_rows()
                except Exception as e:  # noqa: BLE001
                    log_once("coach.glossary", e)
                    rows = []
            self._system = prompts.system_prompt(rows)
        blocks = [dict({"type": "text", "text": self._system}, **({"cache_control": cc} if cc else {}))]
        mem = self.deps.memory.block() if self.deps.memory is not None else ""
        if mem:      # its own cached block: a changed note re-writes only this part of the prompt cache
            blocks.append(dict({"type": "text", "text": mem}, **({"cache_control": cc} if cc else {})))
        return blocks

    def _directives(self, text, with_facts=False):
        """Strip WATCH:/UNWATCH: lines (applied) and the FACTS: line (machine-only) from a reply.
        -> text, or (text, fact ids, had_facts_line) with with_facts=True."""
        text, facts, had = split_facts(text)
        out, watches = split_watch(text)
        d = self.deps
        for name, on in watches:
            if d.on_watch:
                already = d.watched_fn and (name.lower() in d.watched_fn()) == on
                d.on_watch(name, on)
                if not already:
                    self.emit({"kind": "info", "text": ("Watching " if on else "Stopped watching ") + name,
                               "speak": False})
        return (out, facts, had) if with_facts else out

    def _tool_caps(self, voice):
        """(max tool calls, deadline seconds): voice questions 2 / 12 s, typed questions and reviews 6 / 40 s."""
        if voice:
            return int(self.opt.get("voice_tool_calls")), float(self.opt.get("voice_tool_seconds"))
        return int(self.opt.get("tool_calls")), float(self.opt.get("tool_seconds"))

    # ------------------------------------------------------------------------------------------
    def offline(self, batch, ctx, question):
        """No API key: plain rule-based callouts so the pipeline can be tested."""
        if question:
            m = re.search(r"remind me (?:to (?:re)?(?:apply|cast|buff) )(.+)", question, re.I)
            if m and self.deps.on_watch:
                name = m.group(1).strip(" .?!")
                self.deps.on_watch(name, True)
                return "Will remind you when %s drops." % name
            why = self.llm.suspended or "no API key for %s" % self.cfg.get("fast_model")
            if re.search(r"\bwhy\b", question, re.I):
                line = offline_coach.why_line(ctx)
                if line:
                    return "Coach AI is off (%s). %s" % (why, line)
            return "Coach AI is off (%s). Current step: %s" % (why, offline_coach.step_line(ctx))
        return offline_coach.callout(batch, ctx)

    def answer_once(self, question, state=None, voice=False):
        """Synchronous question through the real answer path (context, knowledge block, tools, grounding) for evals.
        Returns {"answer": message dict or None, "messages": [...], "latency_s", "stats": per-call stats delta,
        "usage": summed API usage of the turn}."""
        if state is not None:
            self.state = state
        got, emit0 = [], self.emit

        def cap(m):
            got.append(m)
            emit0(m)
        before = dict(self.stats)
        t0 = time.time()
        self.emit = cap
        self.llm.last_usage = {}
        try:
            msg = self._respond([], question=question, voice=voice)
        finally:
            self.emit = emit0
        delta = {k: self.stats.get(k, 0) - before.get(k, 0) for k in self.stats}
        return {"answer": msg if msg and msg.get("kind") == "answer" else None, "messages": got,
                "latency_s": round(time.time() - t0, 2), "stats": delta, "usage": dict(self.llm.last_usage)}
