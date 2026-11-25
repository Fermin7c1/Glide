"""Talking to the model providers: the Claude API, Gemini, OpenRouter and custom OpenAI-compatible endpoints
(providers.py sends each one's requests; openai_compat.py translates for the last three). Every request goes through
the billing guard (a fatal error suspends that provider, transient ones back off), prompt caching markers,
thinking/effort options per model, streamed replies, the tool-use loop, and the token and cost accounting the
dashboard shows.

The coach holds one LLMClient and hands it its system prompt (system_fn), the runner for the tools a model calls
(run_tool) and where status messages go (emit). Each call says which model path it is for ("fast" or "deep"), so a
reflection running beside a callout books its tokens and picks its options for its own path. The engine's item
research makes its own background calls with web search (web_research), booked to RESEARCH_PATH, and waits while the
coach is in a call of its own (busy)."""
import functools
import json
import os
import re
import threading
import time
import urllib.error
from typing import Any, Callable

import models as models_mod
import openai_compat
import providers
import settings
import websearch
from log import log_once


MEMORY_WRITES = ("memory_remember", "memory_forget")   # refused once an answer read web results
RESULT_CHARS = 6000              # hard cap of one tool_result (api.dispatch already trims to ~1,500 tokens)
RESEARCH_PATH = "research"       # the item research's background calls: booked apart from the coach's fast and deep
SEARCH_USD = 0.01                # one server-side web search ($10 per 1,000)
SEARCH_TOKENS = 5000             # input tokens one web search's results may add (an estimate on the high side)
PROMPT_TOKENS = 1500             # a research request's own text: the system text, the question, the tool


def api_message(detail):
    """The 'message' out of an API error body (Anthropic, Gemini and OpenRouter shapes), else the raw text."""
    try:
        d = json.loads(detail)
    except ValueError:
        m = re.search(r'"message"\s*:\s*"([^"]+)', detail or "")
        return m.group(1) if m else (detail or "").strip()
    if isinstance(d, list) and d:
        d = d[0]
    e = d.get("error") if isinstance(d, dict) else None
    return (e.get("message") if isinstance(e, dict) else None) or (detail or "").strip()


API_LABEL = providers.API_LABEL
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY",
           "endpoint": "custom endpoint (Settings > Models)"}
PATH_ROLE = {"fast_model": "callouts and spoken questions", "deep_model": "typed questions and pace reviews"}


def api_label(model):
    """'Claude API' / 'Gemini API' / 'OpenRouter' for a model id."""
    return API_LABEL[models_mod.provider(model)]


def path_of(deep):
    """The model path a call is booked to: "deep" (typed questions, reviews, reflection) or "fast"."""
    return "deep" if deep else "fast"


def api_keys(cfg):
    """The keys of an "llm" config, read from the environment (never stored): ({"anthropic", "openrouter",
    "gemini", "endpoint"}: key or "", {endpoint id: endpoint with its key}). A local endpoint may need no key, so
    "endpoint" reads "configured" whenever one is set up and each request uses its endpoint's own key."""
    env = settings.Settings("llm", cfg).get("api_key_env")
    keys = {"anthropic": os.environ.get(env, "").strip(),
            "openrouter": os.environ.get(cfg.get("openrouter_key_env", "OPENROUTER_API_KEY"), "").strip(),
            "gemini": (os.environ.get(cfg.get("gemini_key_env", "GEMINI_API_KEY"), "")
                       or os.environ.get("GOOGLE_API_KEY", "")).strip()}
    eps = {eid: dict(ep, key=os.environ.get(ep["key_env"], "").strip())
           for eid, ep in openai_compat.endpoints(cfg).items()}
    keys["endpoint"] = "configured" if eps else ""
    return keys, eps


def _no_tool(name, args, ctx, budget_s):
    return {"error": "no tools here"}


def _coach_turn(fn):
    """A model call of the coach's own (call_once, call_tools): counted while it runs, so busy() can tell the
    engine's background research to wait for it."""
    @functools.wraps(fn)
    def run(self, *a, **kw):
        with self._acct:
            self._turns += 1
        try:
            return fn(self, *a, **kw)
        finally:
            with self._acct:
                self._turns -= 1
    return run


def _urls(blocks, out):
    """Add to `out` every page URL in a reply's search results and citations (the server's web_search_tool_result
    blocks, however deep the filtering tool nests them, and the text blocks' citations)."""
    for b in blocks or ():
        if not isinstance(b, dict):
            continue
        u = b.get("url")
        if isinstance(u, str) and u.startswith(("http://", "https://")) and u not in out:
            out.append(u)
        for key in ("content", "citations"):
            v = b.get(key)
            if isinstance(v, list):
                _urls(v, out)
            elif isinstance(v, dict):
                _urls([v], out)


class LLMClient:
    """The model calls behind the coach. emit(message dict): status and error messages for the dashboard;
    system_fn(cache marker) -> the system prompt blocks; run_tool(name, args, ctx, budget_s): one tool call a model
    made. catalog (models.Catalog): prices and tool support; usage_fn(dict): told about every answered call."""

    @property
    def opt(self):
        """The llm settings, read through settings_manifest.json (defaults, ranges)."""
        return settings.Settings("llm", self.cfg)

    def __init__(self, cfg, emit=None, system_fn=None, run_tool=None, catalog=None, usage_fn=None):
        """cfg: the "llm" config (models, keys' environment variables, endpoints, timeouts, tuning switches)."""
        self.cfg = cfg
        self.emit = emit or (lambda m: None)
        self.system_fn = system_fn or (lambda cc=None: [])
        self.run_tool: Callable[..., Any] = run_tool or _no_tool
        self.keys, self.endpoints = api_keys(cfg)
        self.key = self.keys["anthropic"]
        self.enabled = any(self.keys.values()) and self.opt.get("enabled")
        # one provider per API; custom endpoints one each, under their model prefix "ep:<id>"
        self.providers = {"anthropic": providers.AnthropicProvider(cfg, self.key),
                          "gemini": providers.OpenAICompatProvider("gemini", cfg, self.keys["gemini"]),
                          "openrouter": providers.OpenAICompatProvider("openrouter", cfg, self.keys["openrouter"])}
        for eid, ep in self.endpoints.items():
            self.providers["ep:" + eid] = providers.OpenAICompatProvider(
                "endpoint", cfg, ep["key"], url=ep["base_url"] + "/chat/completions", label=ep["label"])
        self.catalog = catalog                # models.Catalog: prices and tool support
        self.usage_fn = usage_fn              # callback(dict) per API call: tokens read/written/uncached, cost
        self.path_stats = {"since": time.time(), "fast": {}, "deep": {}}
        self.stats = {"calls": 0, "errors": 0, "in_tokens": 0, "out_tokens": 0, "tool_calls": 0,
                      "uncached_in_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.0}
        self._acct = threading.Lock()         # the coach and reflection threads both book calls
        self.suspended = None                 # reason the API can't be used (no credits, bad key): stop calling
        self.suspended_for = None             # the provider that suspension applies to
        self.web_off = None                   # why web search is off for this session (e.g. disabled for the org)
        self.web_basic = False                # the API refused the filtering web search tool: use the basic one
        self.backoff_until = 0                # transient errors (overloaded, rate limit, network): pause calls
        self.fails = 0
        self.research_backoff_until = 0       # the same for the item research's background calls alone: a slow or
        self.research_fails = 0               # failing lookup never pauses the coach
        self.last_latency = 0.0
        self.last_usage = {}                  # summed usage of the last turn (evals)
        self._turns = 0                       # the coach's own calls running now (busy)

    def provider_for(self, model):
        """The provider that serves this model id, or None (a custom endpoint that isn't configured)."""
        prov = models_mod.provider(model)
        if prov == "endpoint":
            return self.providers.get("ep:" + openai_compat.split_endpoint(model)[0])
        return self.providers[prov]

    # ---- prompt caching ------------------------------------------------------------------------
    # One stable prefix per model: [tools] + system, marked at the end of the system prompt (tools come first in
    # the cache hierarchy, so that one marker covers both). Callouts send the same tools (tool_choice none) on
    # providers that cache explicitly, so callouts and questions share one warm prefix instead of two. The deep
    # path (pace reviews every 15 min, the odd typed question) is called less often than the 5-minute cache
    # lifetime, so its prefix is written with the 1-hour TTL. Tool loops add a second marker on the newest message
    # so each round reads the previous rounds back instead of paying for them again.
    def caching(self, model):
        """'explicit' (Anthropic, and Anthropic/Google models via OpenRouter: cache_control markers), 'implicit'
        (Gemini direct and other OpenRouter vendors: the provider caches repeated prefixes on its own)."""
        p = self.provider_for(model)
        return p.caching(model) if p is not None else "implicit"

    def cache_marker(self, model, deep):
        """cache_control for the tools + system prefix, or None when the provider caches implicitly."""
        if self.caching(model) != "explicit" or not self.opt.get("prompt_cache"):
            return None
        ttl = self.opt.get("cache_ttl_deep" if deep else "cache_ttl_fast")
        cc = {"type": "ephemeral"}
        if ttl == "1h" and (models_mod.provider(model) == "anthropic" or model.startswith("anthropic/")):
            cc["ttl"] = "1h"
        return cc

    def ref_marker(self, model):
        """cache_control for the user message's REFERENCE part (5 minutes: it changes with the step), or None."""
        if self.caching(model) != "explicit" or not self.opt.get("prompt_cache"):
            return None
        return {"type": "ephemeral"}

    @staticmethod
    def _tail_cached(messages):
        """A copy of the messages with a 5-minute cache marker on the last block (the conversation so far)."""
        if not messages:
            return messages
        last = dict(messages[-1])
        c = last.get("content")
        if isinstance(c, str):
            last["content"] = [{"type": "text", "text": c, "cache_control": {"type": "ephemeral"}}]
        elif isinstance(c, list) and c:
            last["content"] = list(c[:-1]) + [dict(c[-1], cache_control={"type": "ephemeral"})]
        return list(messages[:-1]) + [last]

    @staticmethod
    def model_options(model, deep):
        """Thinking/effort settings per model. Sonnet/Opus 5.x think by default and thinking tokens count
        against max_tokens, which can leave no room for the answer. Live callouts and spoken answers turn
        up-front thinking off and use low effort (fast); reviews and typed questions keep adaptive
        thinking at medium effort."""
        m = model.lower()
        if "/" in m or m.startswith("gemini:") or m.startswith("ep:"):   # OpenRouter / Gemini / custom endpoints
            return {}
        if "haiku" in m and not any(v in m for v in ("haiku-5", "haiku5")):
            return {}                      # Haiku 4.5 and older: no adaptive thinking or effort (400 on effort)
        opts = {"output_config": {"effort": "medium" if deep else "low"}}
        if not deep:
            if "haiku" in m:
                # Haiku 5.x thinks by default (effort medium) and thinking counts against max_tokens: a short
                # callout could spend all of it thinking. "disabled" is accepted at effort high or below.
                opts["thinking"] = {"type": "disabled"}
            elif "opus" in m:
                # Opus 5.x rejects thinking.type "disabled" (400): adaptive + low effort keeps it fast.
                opts["thinking"] = {"type": "disabled"} if "opus-4" in m else {"type": "adaptive"}
            elif "sonnet" in m:
                opts["thinking"] = {"type": "between_tools"}
        return opts

    def post(self, body, opts, path, on_text=None):
        """One API request under the billing guard, for model path `path` ("fast" | "deep": its cost split and, on
        Gemini, its reasoning effort). Tuning options (`opts`) first; if the API rejects them (400), the next
        option set (Gemini: minimal -> low -> none), then plain; the set a Gemini model accepted is remembered. A
        busy model (429/5xx) gets one quick retry. `on_text(chunk)`: stream the reply (Claude API) and hand each
        piece of answer text to it as it arrives (early speech); the result is the same message dict.
        Returns (response dict or None, the options used)."""
        t0 = time.time()
        model = body.get("model") or ""
        p = self.provider_for(model)
        if p is None:
            self.stats["errors"] += 1
            self.emit({"kind": "error", "text": "No custom endpoint '%s' is configured (model %s)."
                       % (openai_compat.split_endpoint(model)[0], model), "speak": False})
            return None, {}
        label = p.label
        attempts = p.option_sets(model, opts, path == "deep")
        i, retried = 0, False
        while i < len(attempts):            # if the API rejects the tuning options, the next set
            attempt = attempts[i]
            req, stream = p.request(body, attempt, on_text)
            try:
                data = p.parse(p.fetch(req, stream, on_text, self.opt.get("timeout")))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                fatal = p.fatal_reason(e.code, detail)
                if fatal:
                    self._suspend(fatal, e.code, detail, model)
                    return None, attempt
                without = self._without_refused_web_tool(e.code, detail, body)
                if without is not None and path == RESEARCH_PATH and self.web_off:
                    return None, attempt           # (research without web search is no research: not sent again)
                if without is not None:
                    body = without
                    continue
                if e.code == 400 and attempt and i + 1 < len(attempts):
                    if not p.quiet_fallback:
                        self.emit({"kind": "info", "text": "%s refused tuning options (%s); retrying plain"
                                   % (label, detail[:120]), "speak": False})
                    i += 1
                    continue
                busy = e.code in (429, 500, 502, 503, 504, 529)
                if busy and not retried and time.time() - t0 < 8:
                    retried = True
                    time.sleep(float(self.opt.get("busy_retry_s")))
                    continue
                self.stats["errors"] += 1
                self._back_off(busy, label, path)
                self.emit({"kind": "error", "text": "%s %s (%s): %s" % (label, e.code, model, api_message(detail)),
                           "speak": False})
                return None, attempt
            except Exception as e:
                self.stats["errors"] += 1
                self._back_off(True, label, path)
                self.emit({"kind": "error", "text": "%s unreachable (%.0fs): %s" % (label, time.time() - t0, e),
                           "speak": False})
                return None, attempt
            p.accepted(model, attempt)
            self._recovered(p.name, label)
            self._account(model, body, data.get("usage") or {}, path)
            return data, attempt
        return None, {}

    def _without_refused_web_tool(self, code, detail, body):
        """A 400 refusing the web search server tool -> the body to send again: the filtering tool swapped for the
        basic one, or (web search not enabled for the organization) without web search for the rest of the
        session. Any other error -> None."""
        if code != 400:
            return None
        srv = [t for t in body.get("tools") or () if str(t.get("type", "")).startswith("web_search")]
        if any(t.get("type") == websearch.FILTERED_TOOL for t in srv) and \
                (websearch.FILTERED_TOOL in detail or "code execution" in detail.lower().replace("_", " ")):
            self.web_basic = True          # the filtering web search isn't available here: the basic one
            self.emit({"kind": "info", "text": "The filtering web search tool was refused (%s); using the "
                       "basic one." % api_message(detail)[:120], "speak": False})
            return dict(body, tools=[self._basic_web(t) for t in body["tools"]])
        if srv and "web search" in detail.lower().replace("_", " "):
            self.web_off = "web search is not enabled for this API organization"
            self.emit({"kind": "info", "text": "Web search is off: %s (Claude Console > Settings > "
                       "Capabilities). Answering without it." % self.web_off, "speak": False})
            body = dict(body, tools=[t for t in body["tools"] if t not in srv] or None)
            if not body["tools"]:
                body.pop("tools")
                body.pop("tool_choice", None)
            return body
        return None

    def _recovered(self, prov, label):
        """A request went through: lift that provider's suspension (saying so) and any back-off."""
        if self.suspended and self.suspended_for == prov:
            self.emit({"kind": "info", "text": "%s works again; live coaching resumed." % label, "speak": False})
        if self.suspended_for in (None, prov):
            self.suspended, self.suspended_for = None, None
        self.fails, self.backoff_until = 0, 0
        self.research_fails, self.research_backoff_until = 0, 0

    def _price(self, model):
        """A model's price ({"in", "out", "cache_read", "cache_write"} $/MTok), or None when it isn't known."""
        price = self.catalog.price(model) if self.catalog is not None else models_mod.anthropic_price(model)
        return price if price and price.get("in") is not None else None

    def _cost(self, model, u):
        """What one answered request cost in dollars (the provider's own figure, else the catalog's price for its
        tokens), its server-side web searches included; None when the price isn't known (a custom endpoint)."""
        cost = u.get("cost_usd")
        if cost is None:
            cost = models_mod.cost_usd(self._price(model), u)
        searches = int(((u.get("server_tool_use") or {}).get("web_search_requests")) or 0)
        if searches:
            cost = (cost or 0.0) + searches * SEARCH_USD          # $10 per 1,000 searches
        return cost

    @staticmethod
    def _round_cost(price, ctx_tokens, searches, max_tokens):
        """What one research request may cost, on the high side: its context (ctx_tokens) and the results of the
        server searches it may run (SEARCH_TOKENS each) read as input, a whole answer of max_tokens, and the
        searches."""
        return models_mod.cost_usd(price, {"input_tokens": ctx_tokens + searches * SEARCH_TOKENS,
                                           "output_tokens": max_tokens}) + searches * SEARCH_USD

    def research_estimate(self, model=None, max_searches=3, max_tokens=1500):
        """What one web_research lookup would likely cost at most, before it goes out (research_budget.Budget.allow
        and reserve): every search it may make, their results read as input (SEARCH_TOKENS each), its own text and a
        whole answer, at the model's price (a local Tavily / Brave search costs nothing here). None when the
        model's price isn't known: no lookup is made on a model whose cost can't be counted."""
        model = model or self.cfg.get("fast_model") or ""
        price = self._price(model)
        if price is None:
            return None
        _, local = self.web_search_tools(model, max_searches)
        return self._round_cost(price, PROMPT_TOKENS + (max_searches * SEARCH_TOKENS if local else 0),
                                0 if local else max_searches, max_tokens)

    def _account(self, model, body, u, path):
        """Count one answered request: tokens (read from cache, written to it, uncached), web searches and the cost,
        in the session totals, its path's totals (`path`: "fast" | "deep" | RESEARCH_PATH), the last turn's usage
        (not for research: it runs beside the coach's turns) and the app's usage log."""
        cost = self._cost(model, u)
        searches = int(((u.get("server_tool_use") or {}).get("web_search_requests")) or 0)
        with self._acct:
            self.stats["calls"] += 1
            self.stats["in_tokens"] += (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
            self.stats["out_tokens"] += u.get("output_tokens") or 0
            self.stats["uncached_in_tokens"] += u.get("input_tokens") or 0
            self.stats["cache_read_tokens"] += u.get("cache_read_input_tokens") or 0
            self.stats["cache_write_tokens"] += u.get("cache_creation_input_tokens") or 0
            lu = self.last_usage if path != RESEARCH_PATH else {}
            for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                lu[k] = lu.get(k, 0) + (u.get(k) or 0)
            if searches:
                self.stats["web_searches"] = self.stats.get("web_searches", 0) + searches
            ps = self.path_stats.setdefault(path, {})
            for k, v in (("calls", 1), ("cache_read", u.get("cache_read_input_tokens") or 0),
                         ("uncached", (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)),
                         ("cache_write", u.get("cache_creation_input_tokens") or 0),
                         ("output", u.get("output_tokens") or 0), ("cost_usd", cost or 0.0)):
                ps[k] = ps.get(k, 0) + v
            if cost:
                self.stats["cost_usd"] += cost
        if self.usage_fn:
            try:
                self.usage_fn({"model": model, "path": path, "tools": bool(body.get("tools")),
                               "in": u.get("input_tokens") or 0, "read": u.get("cache_read_input_tokens") or 0,
                               "write": u.get("cache_creation_input_tokens") or 0,
                               "write_1h": (u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0,
                               "out": u.get("output_tokens") or 0, "cost": round(cost or 0.0, 6)})
            except Exception as e:  # noqa: BLE001 - the usage log is an extra
                log_once("llm.usage", e)

    def count_web_search(self):
        """A web search the coach ran itself (the local search tool), for the session totals."""
        with self._acct:
            self.stats["web_searches"] = self.stats.get("web_searches", 0) + 1

    def available(self, model):
        """Whether calls to this model can go out now: the AI is on, its provider has a key, isn't suspended and
        isn't backing off."""
        prov = models_mod.provider(model)
        return bool(self.enabled and self.keys.get(prov) and not self.suspension_for(prov)
                    and time.time() >= self.backoff_until)

    def busy(self):
        """Whether the coach is in a model call of its own now (a question, a callout, a review, a briefing): the
        engine's background research waits until it isn't."""
        return self._turns > 0

    def web_search_tools(self, model, max_searches):
        """The web search tool a background call on this model gets, as questions get it: Anthropic's server tool on
        Claude models (the basic one where the filtering one was refused or is switched off), else the local
        Tavily / Brave tool when one of their keys is set. -> ([tool definition], local?) or (None, False) when the
        model can't search."""
        if self.web_off:
            return None, False
        if models_mod.provider(model) == "anthropic":
            return [websearch.server_tool(max_searches, model,
                                          basic=self.web_basic or not self.opt.get("web_search_filtered"))], False
        if websearch.backend():
            return [dict(websearch.LOCAL_DEF)], True
        return None, False

    def web_research(self, prompt, system="", max_searches=3, max_tokens=1500, deadline_s=90.0, model=None,
                     max_usd=None):
        """One background call with web search for the engine's item research (research.WebResolver): the prompt
        with a short system text of its own (not the coach's), web search as the questions get it
        (web_search_tools) and no other tool; the fast model unless `model` says otherwise. Booked to RESEARCH_PATH
        in the session's totals and the usage log, never in the coach's last turn, and not counted as the coach
        being busy; its failures back off research alone (never the coach's callouts). A long server search
        (pause_turn) is continued; local searches run here, at most max_searches, then the answer is asked for (also
        after deadline_s). It stops before any request that would pass max_usd (what this lookup may spend, with
        that request at its likely-high cost: _round_cost), before any after the first while the coach is in a call
        of its own (busy), and before continuing a paused search after deadline_s. The API refusing web search turns
        it off for the session (_without_refused_web_tool) and the call stops there.
        -> (the answer text or None, meta): meta {"cost_usd": what this call's answered requests cost, "sent":
        requests sent (one sent and not answered may still be billed), "calls": requests answered, "cost_unknown":
        an answered request whose price isn't known, "searches": server-side searches, "local_searches", "urls":
        every page URL its searches returned or its answer cited, "why": why there is no text ("unavailable", "no
        tools", "no web search", "refused", "failed", "model refused", "empty", "busy", "budget", "deadline"),
        "stop": the last stop_reason}."""
        meta: dict[str, Any] = {"cost_usd": 0.0, "sent": 0, "calls": 0, "cost_unknown": False, "searches": 0,
                                "local_searches": 0, "urls": [], "why": None, "stop": None}
        model = model or self.cfg.get("fast_model") or ""
        if not model or not self.available(model) or time.time() < self.research_backoff_until:
            meta["why"] = "unavailable"
            return None, meta
        if self.catalog is not None and not self.catalog.supports_tools(model):
            meta["why"] = "no tools"
            return None, meta
        tools, local = self.web_search_tools(model, max_searches)
        if not tools:
            meta["why"] = "refused" if self.web_off else "no web search"
            return None, meta
        opts = self.model_options(model, False) if self.opt.get("tune_thinking") else {}
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        price = self._price(model)
        ctx = (len(system or "") + len(str(prompt or "")) + len(json.dumps(tools))) // 3      # (tokens, roughly)
        t0, force, data = time.time(), False, None
        for _ in range(max_searches + 3):
            if meta["sent"] and self.busy():
                meta["why"] = "busy"                # (the coach began a call: this lookup waits for another time)
                return None, meta
            if max_usd is not None and price is not None and meta["cost_usd"] + self._round_cost(
                    price, ctx, 0 if force or local else max_searches, max_tokens) > max_usd:
                meta["why"] = "budget"              # (the next request could pass what the day has left)
                return None, meta
            body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": list(messages),
                                    "tools": tools, "tool_choice": {"type": "none" if force else "auto"}}
            if system:
                body["system"] = [{"type": "text", "text": system}]
            meta["sent"] += 1
            data, opts = self.post(body, opts, RESEARCH_PATH)
            if data is None:
                meta["why"] = "refused" if self.web_off else "failed"
                return None, meta
            u = data.get("usage") or {}
            meta["calls"] += 1
            cost = self._cost(model, u)
            if u.get("cost_usd") is None and price is None:
                meta["cost_unknown"] = True         # (searches priced at most: its tokens' price isn't known)
            meta["cost_usd"] += cost or 0.0
            meta["searches"] += int(((u.get("server_tool_use") or {}).get("web_search_requests")) or 0)
            ctx = sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
                                              "output_tokens")) or ctx
            content = [b for b in (data.get("content") or []) if isinstance(b, dict)]
            _urls(content, meta["urls"])
            meta["stop"] = data.get("stop_reason")
            if meta["stop"] == "refusal":
                meta["why"] = "model refused"
                return None, meta
            if meta["stop"] == "pause_turn" and not force:
                if time.time() - t0 >= deadline_s:
                    meta["why"] = "deadline"        # (a paused server search is resumed or left: left, past it)
                    return None, meta
                messages.append({"role": "assistant", "content": content})
                continue
            uses = [b for b in content if b.get("type") == "tool_use"]
            if force or not uses or meta["stop"] != "tool_use":
                break
            messages.append({"role": "assistant", "content": content})
            results = []
            for b in uses:
                if b.get("name") == "web_search" and local and meta["local_searches"] < max_searches:
                    self.count_web_search()
                    meta["local_searches"] += 1
                    res = websearch.search((b.get("input") or {}).get("query"))
                    _urls(res.get("results") or [], meta["urls"])
                else:
                    res = {"error": "no more searches; answer with what you have"}
                txt = json.dumps(res, separators=(",", ":"), ensure_ascii=False, default=str)[:RESULT_CHARS]
                results.append({"type": "tool_result", "tool_use_id": b.get("id"), "content": txt})
                ctx += len(txt) // 3                # (read with the next request)
            messages.append({"role": "user", "content": results})
            if meta["local_searches"] >= max_searches or time.time() - t0 >= deadline_s:
                force = True
        text = "".join(b.get("text", "") for b in (data or {}).get("content") or [] if isinstance(b, dict)
                       and b.get("type") == "text").strip()
        if not text:
            meta["why"] = "empty"
            return None, meta
        return text, meta

    # ------------------------------------------------------------------------------------------
    def model_name(self, model):
        e = self.catalog.get(model) if self.catalog is not None else None
        return (e or {}).get("name") or (model or "?").split(":", 1)[-1].split("/")[-1]

    def describe_models(self):
        """'callouts and spoken questions: Gemini Flash Latest (Gemini API); typed ...: ...' for startup/status."""
        parts = []
        for key in ("fast_model", "deep_model"):
            m = self.cfg.get(key)
            prov = models_mod.provider(m)
            parts.append("%s: %s (%s%s)" % (PATH_ROLE[key], self.model_name(m), API_LABEL[prov],
                                            "" if self.keys.get(prov) else ", no %s set" % KEY_ENV[prov]))
        return "; ".join(parts)

    def announce_models(self, changes, startup=False):
        """Say which model each path now uses. changes: {"fast_model"|"deep_model": (old id, new id)}. A switch to
        another provider clears that old provider's suspension and back-off, so the new model is tried at once."""
        if startup:
            self.emit({"kind": "info", "text": "Coach models (saved selection) - " + self.describe_models(),
                       "speak": False})
            return
        changed = {k: (o, n) for k, (o, n) in (changes or {}).items() if n and o != n}
        if not changed:
            return
        both = len(changed) == 2 and changed["fast_model"][1] == changed["deep_model"][1]
        items = [("all coaching", changed["fast_model"][1])] if both else \
            [(PATH_ROLE[k], n) for k, (o, n) in sorted(changed.items(), key=lambda kv: kv[0] != "fast_model")]
        lines, warn = [], []
        for role, m in items:
            prov = models_mod.provider(m)
            lines.append("%s now %s %s via %s" % (role[0].upper() + role[1:], "uses" if role == "all coaching" else "use",
                                                   self.model_name(m), API_LABEL[prov]))
            if not self.keys.get(prov):
                warn.append("No %s is set, so it will answer offline." % KEY_ENV[prov])
            elif self.catalog is not None and not self.catalog.supports_tools(m):
                warn.append("%s can't use tools, so questions get no lookups." % self.model_name(m))
        new_provs = {models_mod.provider(n) for _, n in changed.values()}
        if self.suspended and self.suspended_for not in new_provs:
            self.suspended, self.suspended_for = None, None
        if self.backoff_until or self.fails:
            self.fails, self.backoff_until = 0, 0
        self.emit({"kind": "info", "text": ("Model switched. " + ". ".join(lines) + ". " + " ".join(dict.fromkeys(warn))).strip(),
                   "speak": True, "urgent": False})

    def _answer_text(self, data):
        u = data.get("usage") or {}
        text = "".join(b.get("text", "") for b in data.get("content") or [] if isinstance(b, dict)
                       and b.get("type") == "text").strip()
        if not text:
            self.stats["errors"] += 1
            self.emit({"kind": "error", "text": "The model returned no answer text (stop_reason %s, %d output tokens)"
                       % (data.get("stop_reason"), u.get("output_tokens", 0)), "speak": False})
        return text

    @_coach_turn
    def call_once(self, model, user, max_tokens, deep=False, tools=None, schema=None, on_text=None):
        """One-shot answer (live callouts, and everything without the knowledge layer), booked to the deep or fast
        path. `tools`: sent with tool_choice none only to share the questions' cached prefix (see caching). `schema`:
        a JSON schema the reply must follow (structured outputs, Claude API only; if the API refuses it the request
        goes again without it and the caller parses the text as before). `on_text`: streaming callback (see post).
        -> (text, meta)."""
        meta: dict[str, Any] = {"tool_calls": 0, "tools": [], "sources": []}
        path = path_of(deep)
        self.last_usage = {}
        opts = self.model_options(model, deep) if self.opt.get("tune_thinking") else {}
        if schema and models_mod.provider(model) == "anthropic" and self.opt.get("structured_outputs"):
            opts = dict(opts, output_config=dict(opts.get("output_config") or {},
                                                 format={"type": "json_schema", "schema": schema}))
        t0 = time.time()
        body = {"model": model, "max_tokens": max_tokens, "system": self.system_fn(self.cache_marker(model, deep)),
                "messages": [{"role": "user", "content": user}]}
        if tools:
            body["tools"] = [dict(t) for t in tools]
            body["tool_choice"] = {"type": "none"}
        data, opts = self.post(body, opts, path, on_text=on_text)
        data = self._retry_empty(body, opts, data, path)
        self.last_latency = time.time() - t0
        if data is None:
            return None, meta
        return self._answer_text(data), meta

    def _retry_empty(self, body, opts, data, path):
        """An answer cut off at max_tokens before any text (a model that spent the budget thinking): ask once more
        with four times the room (at least 2,400 tokens) instead of showing an empty answer."""
        if not data or data.get("stop_reason") != "max_tokens" or any(
                isinstance(b, dict) and b.get("type") == "text" and (b.get("text") or "").strip()
                for b in data.get("content") or []):
            return data
        retry: dict[str, Any] = dict(body, max_tokens=max(int(body.get("max_tokens") or 0) * 4, 2400))
        if retry.get("tools"):
            retry["tool_choice"] = {"type": "none"}        # just the answer this time
        self.emit({"kind": "info", "speak": False, "text": "The answer ran out of room (%s, %d tokens); asking once more "
                   "with %d" % (body.get("model"), body.get("max_tokens") or 0, retry["max_tokens"])})
        again, _ = self.post(retry, opts, path)
        return again if again is not None else data

    @_coach_turn
    def call_tools(self, model, messages, tools, max_calls, deadline_s, ctx=None, max_tokens=600, deep=False,
                   on_text=None):
        """Tool-use loop for questions and reviews. Sends tools (tool_choice auto); on stop_reason "tool_use" runs
        every tool_use block through run_tool (knowledge.api.dispatch on the live ctx, or the coach's own tools) and
        sends the tool_result blocks back; stops at end_turn. After max_calls tool calls or deadline_s seconds the
        next request forces the final answer (tool_choice none). The thinking/effort options and the billing guard
        are the same as for one-shot calls. -> (text or None, {"tool_calls", "tools", "sources", "rounds"})."""
        meta: dict[str, Any] = {"tool_calls": 0, "tools": [], "sources": [], "rounds": 0}
        path = path_of(deep)
        self.last_usage = {}
        opts = self.model_options(model, deep) if self.opt.get("tune_thinking") else {}
        tools = [dict(t) for t in tools or ()]
        system = self.system_fn(self.cache_marker(model, deep))     # covers tools + system (one prefix)
        tail = self.caching(model) == "explicit" and models_mod.provider(model) == "anthropic" \
            and self.opt.get("prompt_cache")
        messages = list(messages)
        t0 = time.time()
        force, data, body = not tools, None, {}
        for _ in range(max_calls + 2):
            # cache the conversation so far when another round may follow (not on the forced last answer)
            if self.web_off:                       # web search turned out to be disabled: drop the server tool
                tools = [t for t in tools if not str(t.get("type", "")).startswith("web_search")]
            elif self.web_basic:
                tools = [self._basic_web(t) for t in tools]
            sent = self._tail_cached(messages) if tail and not force and messages[-1].get("role") == "user" \
                else messages
            body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": sent}
            if tools:
                body["tools"] = tools
                body["tool_choice"] = {"type": "none" if force else "auto"}
            data, opts = self.post(body, opts, path, on_text=on_text)
            meta["rounds"] += 1
            if data is None:
                self.last_latency = time.time() - t0
                return None, meta
            content = [b for b in (data.get("content") or []) if isinstance(b, dict)]
            if any(b.get("type") == "web_search_tool_result" for b in content):
                meta["read_web"] = True            # the server's search: what it read can't write the memory (below)
            meta.setdefault("web", [])
            for c in websearch.citations(content):
                if c["url"] not in {x["url"] for x in meta["web"]}:
                    meta["web"].append(c)
            if data.get("stop_reason") == "pause_turn" and not force:
                messages.append({"role": "assistant", "content": content})     # a long server-side search:
                continue                                                         # send it back to continue
            uses = [b for b in content if b.get("type") == "tool_use"]
            if force or not uses or data.get("stop_reason") != "tool_use":
                break
            messages.append({"role": "assistant", "content": content})
            results = [self._tool_result(b, ctx, meta, max_calls, deadline_s - (time.time() - t0), deadline_s)
                       for b in uses]
            messages.append({"role": "user", "content": results})
            if meta["tool_calls"] >= max_calls or time.time() - t0 >= deadline_s:
                force = True
        data = self._retry_empty(body, opts, data, path)
        self.last_latency = time.time() - t0
        return self._answer_text(data or {}), meta

    def _tool_result(self, use, ctx, meta, max_calls, left, deadline_s):
        """Run one tool_use block (within the budget: max_calls calls, `left` seconds of deadline_s) -> its
        tool_result block. Counts the call and keeps its text and any web sources in `meta`."""
        if meta["tool_calls"] >= max_calls or left <= 0:
            res = {"error": "tool budget used up (%d calls, %.0f s); answer now with what you have"
                            % (max_calls, deadline_s)}
        elif use.get("name") in MEMORY_WRITES and meta.get("read_web"):
            # text from the web may be written to steer the coach: an answer that read it never changes what the
            # coach remembers about the player (the player can still say it themselves, next time)
            res = {"error": "the memory can't be changed in an answer that read web results; if the player wants "
                            "this remembered, ask them to say it again"}
        else:
            res = self.run_tool(use.get("name"), use.get("input") or {}, ctx, left)
            if use.get("name") == "web_search":
                meta["read_web"] = True
            if use.get("name") == "web_search" and isinstance(res, dict):
                for x in (res.get("results") or [])[:3]:
                    if x.get("url") and x["url"] not in {y["url"] for y in meta.setdefault("web", [])}:
                        meta["web"].append({"url": x["url"], "title": x.get("title")})
            meta["tool_calls"] += 1
            meta["tools"].append(use.get("name"))
            self.stats["tool_calls"] += 1
        txt = json.dumps(res, separators=(",", ":"), ensure_ascii=False, default=str)
        if len(txt) > RESULT_CHARS:
            txt = txt[:RESULT_CHARS]
        meta["sources"].append(txt)
        r = {"type": "tool_result", "tool_use_id": use.get("id"), "content": txt}
        if isinstance(res, dict) and res.get("error"):
            r["is_error"] = True
        return r

    @staticmethod
    def _basic_web(tool):
        """The filtering web search tool definition swapped for the basic one (other tools unchanged)."""
        if tool.get("type") == websearch.FILTERED_TOOL:
            return dict(tool, type=websearch.SERVER_TOOL)
        return tool

    def _suspend(self, reason, code, detail, model=None):
        prov = models_mod.provider(model) if model else "anthropic"
        first = self.suspended != reason or self.suspended_for != prov
        self.suspended, self.suspended_for = reason, prov
        self.stats["errors"] += 1
        if first:
            fix = {"out of API credits": "add credits (console.anthropic.com Plans & Billing, openrouter.ai/credits, or "
                                         "Google AI Studio billing) or pick a cheaper model in the dashboard",
                   "API key rejected": "check ANTHROPIC_API_KEY (OPENROUTER_API_KEY / GEMINI_API_KEY for those models)",
                   "API access denied": "check the API key's organization and permissions"}[reason]
            self.emit({"kind": "error", "text": "Coach AI stopped: %s. No more API calls; basic callouts only. "
                       "To resume, %s, then ask the coach any question (or restart)." % (reason, fix),
                       "speak": True, "urgent": False})
            self.emit({"kind": "info", "text": "%s %s: %s" % (API_LABEL[prov], code, api_message(detail)[:200]),
                       "speak": False})

    def suspension_for(self, prov):
        """Why calls to this provider are suspended (no credits, bad key), or None. A suspension only blocks the
        provider it came from."""
        return self.suspended if self.suspended_for in (None, prov) else None

    def _back_off(self, transient, label="Claude API", path=None):
        if not transient:
            return
        if path == RESEARCH_PATH:               # (the item research's own: the coach's calls go on)
            self.research_fails += 1
            self.research_backoff_until = time.time() + min(600, 15 * 2 ** (self.research_fails - 1))
            return
        self.fails += 1
        wait = min(600, 15 * 2 ** (self.fails - 1))        # 15 s, 30 s, 1 min ... 10 min
        self.backoff_until = time.time() + wait
        if self.fails >= 2:
            self.emit({"kind": "info", "text": "%s failing; pausing AI callouts for %d s" % (label, wait), "speak": False})
