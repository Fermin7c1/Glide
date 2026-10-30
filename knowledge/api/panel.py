"""KnowledgePack, the live block: the step, pace, heuristics and target parts of a callout's knowledge block,
and the dashboard's why / pace panels remembered from it."""
import copy
import time

from knowledge.api._layer import E, LEVEL_XP, MAX_LEVEL, MX, R, S, level_f
from knowledge.api.community import community_notes
from knowledge.api.context import log_quest_ids
from knowledge.api.reasons import why_text
from knowledge.api.results import BLOCK_TOKENS, collect_facts, fit_block
from knowledge.api.values import as_int, as_num, clean, cut, uniq


class PanelMixin:
    """The knowledge block of a live callout and the dashboard panel it leaves behind."""

    # --------------------------------------------------------------------------------------------- the block
    def _step_part(self, ctx):
        """(step, next, brief) for the block, cached per (guide, step, level, class, race)."""
        lv = ctx.level
        p = ctx.player
        key = (ctx.guide_key, ctx.idx, lv, p["class"], p.get("race"), p["faction"], ctx.settings)
        hit = self._steps.get(key)
        if hit is None:
            route = ctx.route
            b = self.brief(ctx, ctx.idx)
            if b:
                step = self.trim_brief(ctx, b, route, alts=2, full=False)
            else:
                st = route.by_idx[ctx.idx]
                step = {"n": st["rxp_step_no"], "guide": ctx.guide, "kind": st["kind"], "why": [],
                        "note": "no analysis for this step", "fact": ctx.step_fact(ctx.idx)}
            nxt = None
            if ctx.pos is not None and ctx.pos + 1 < len(route.steps):
                ns = route.steps[ctx.pos + 1]
                nb = self.brief(ctx, ns["idx"]) or {}
                nq = (nb.get("justification") or {}).get("quests") or {}
                nxt = clean({"n": ns["rxp_step_no"], "kind": nb.get("kind") or ns["kind"], "xp": nb.get("xp_now"),
                              "quests": [q.get("name") for q in nq.values() if q.get("name")][:3],
                              "fact": ctx.step_fact(ns["idx"])})
            pfacts = [f for f in (b or {}).get("facts") or [] if f.startswith(("p:", "poi:"))]
            hit = self.cache_put(self._steps, key, (step, nxt, b, pfacts), 256)
        step, nxt, b, pfacts = hit
        step = copy.deepcopy(step)
        if lv:
            step["level_now"] = level_f(lv, ctx.xp)
            live = self.live_compare(ctx, b)
            if live and live.get("notes"):
                step["live"] = live["notes"][:3]
        return step, copy.deepcopy(nxt), pfacts

    def sim_state(self, ctx):
        """simulate-contract state at the start of the current step: the modelled walk up to it, overridden with what
        the live state knows (level, xp, position, quest log, rested pool, hearth cooldown, bind)."""
        route, idx, p = ctx.route, ctx.idx, ctx.player
        key = (route.guide_key, idx, p["class"], p.get("race"), p["faction"], ctx.settings)
        base = self._sim.get(key)
        if base is None:
            prev = [st["idx"] for st in route.steps if st["idx"] < idx]
            base = S.run(self.kb, route, None, None, prev[-1])["end"] if prev else R.start_state(route).to_dict()
            self.cache_put(self._sim, key, base, 64)
        st = dict(base)
        st["quests_done"] = set(base.get("quests_done") or ())
        st["quests_active"] = set(base.get("quests_active") or ())
        s = ctx.summary
        if ctx.level:
            st["level"], st["xp"] = ctx.level, ctx.xp
        here = ctx.here()
        if here:
            st["ui_map"], st["x"], st["y"] = here
        log = set(log_quest_ids(s))
        if log:                               # an empty log is treated as unknown (pages not received yet)
            st["quests_done"] -= log
            st["quests_active"] = log
        rest = as_int(s.get("rested"))
        if rest:
            st["rested_pool"] = rest
        hcd = as_num(s.get("hearth_cd"))
        if hcd is not None and hcd >= 0:
            st["hearth_ready_t"] = (st.get("time_s") or 0.0) + hcd
        if s.get("bind"):
            pos = self.data.place_named(s["bind"])
            if pos:
                st["bind"] = pos
        return st

    def _eta_next(self, ctx):
        """Modelled seconds to the next level from here (cached per step, level and tenth of the bar)."""
        lv = ctx.level
        if not lv or lv >= MAX_LEVEL or ctx.route is None or ctx.idx is None:
            return None
        lx = LEVEL_XP[lv] or 1
        p = ctx.player
        key = (ctx.guide_key, ctx.idx, p["class"], p.get("race"), lv, int(10 * ctx.xp / lx), ctx.settings)
        if key in self._eta:
            return self._eta[key]
        try:
            v = S.eta(self.kb, ctx.route, self.sim_state(ctx), ctx.idx, lv + 1).get("time_s")
        except Exception:  # noqa: BLE001
            v = None
        return self.cache_put(self._eta, key, v, 256)

    def _pace_part(self, ctx):
        s = ctx.summary
        lv = ctx.level
        if not lv:
            return None
        xph = as_int(s.get("xp_per_hour")) or None
        bench = E.benchmark_xph(lv)
        xmax = as_int(s.get("xp_max")) or (LEVEL_XP[lv] if lv < MAX_LEVEL else None)
        out = {"xp_per_hour": xph if xph else "unknown (under a minute of data)", "benchmark_xph": bench,
               "ratio": round(xph / float(bench), 2) if xph and bench else None,
               "xp_to_level": max(0, xmax - ctx.xp) if xmax else None,
               "your_eta_s": int(s["ttl_seconds"]) if as_num(s.get("ttl_seconds")) else None,
               "eta_next_level_s": self._eta_next(ctx)}
        done = ctx.completed()
        if done:
            try:
                out["pending_turnin_xp"] = R.pending_turnin_xp(self.kb, ctx.guide_key, ctx.idx, done, lv)
            except Exception:  # noqa: BLE001
                pass
        return clean(out)

    def heuristic_state(self, ctx, triggers=None):
        """engine STATE_FIELDS: GameState.summary() + route.state_fields() + companion bookkeeping + the target's
        KB card, through engine.derive()."""
        raw = dict(ctx.summary)
        trig = triggers if triggers is not None else ctx.triggers
        if trig:
            raw["trigger"] = list(trig)
        book = ctx.book
        raw["deaths_session"] = int(book.get("deaths_session") or 0)
        raw["deaths_recent"] = int(book.get("deaths_recent") or 0)
        if book.get("has_mount") is not None:
            raw["has_mount"] = bool(book["has_mount"])
        raw["in_group"] = bool(book.get("in_group", False))         # unknown -> solo (the addon has no party data)
        lv = ctx.level
        if ctx.route is not None and ctx.idx is not None and self.ok:
            done = tuple(sorted(ctx.completed()))
            key = (ctx.guide_key, ctx.idx, ctx.player["class"], ctx.player.get("race"), lv, done, ctx.settings)
            sf = self._sf.get(key)
            if sf is None:
                try:
                    sf = R.state_fields(self.kb, ctx.guide_key, ctx.idx, ctx.player, level=lv,
                                        quests_complete=list(done) or None, xp_per_min=None)
                except Exception:  # noqa: BLE001
                    sf = {}
                self.cache_put(self._sf, key, sf, 256)
            raw.update({k: v for k, v in sf.items() if v is not None})
            xph = as_num(ctx.summary.get("xp_per_hour"))
            if sf.get("step_xp_per_min") is not None and xph:
                raw["step_xp_ratio"] = round(sf["step_xp_per_min"] / (xph / 60.0), 2)
            st = ctx.route.by_idx.get(ctx.idx) or {}
            here = ctx.here()
            if here and st.get("pos"):
                dd, _ = self._dist_dir(here, st["pos"])
                if dd is not None:
                    raw["dist_to_goto_yd"] = round(dd, 1)
        tgt = ctx.summary.get("target") if isinstance(ctx.summary.get("target"), dict) else None
        if tgt and self.ok:
            card = self._card(as_int(tgt.get("npc")), tgt.get("name"))
            if card.get("_rank") is not None:
                raw.setdefault("target_classification", {0: "normal", 1: "elite", 2: "rareelite", 3: "boss",
                                                         4: "rare"}.get(card["_rank"], "normal"))
            for k, f in (("target_type", "type"), ("target_flees", "flees"), ("target_caster", "caster"),
                         ("target_abilities", "abilities")):
                if card.get(f) is not None:
                    raw.setdefault(k, card[f])
        return E.derive(raw)

    def _heuristics_part(self, ctx, triggers, limit=5):
        try:
            hits = E.evaluate(self.heuristic_state(ctx, triggers), rules=self.rules(), limit=limit,
                              exclude_tags=ctx.muted or None)
        except Exception:  # noqa: BLE001
            return []
        return [clean({"id": "h:%s" % h["id"], "advice": h.get("advice"), "rationale": cut(h.get("rationale"), 140)})
                for h in hits]

    def _target_part(self, ctx):
        s = ctx.summary
        tgt = s.get("target")
        if not isinstance(tgt, dict) or tgt.get("dead") or not (tgt.get("npc") or tgt.get("name")):
            return None
        reaction = as_int(tgt.get("reaction"))
        hostile = reaction is not None and (reaction <= 3 or (reaction == 4 and bool(s.get("combat"))))
        if not hostile:
            return None
        card = self._card(as_int(tgt.get("npc")), tgt.get("name"))
        tl, lv = as_int(tgt.get("level")), ctx.level
        out = {k: v for k, v in card.items() if not k.startswith("_")}
        if card.get("known") is False:
            out = {"name": tgt.get("name"), "level": tl, "known": False,
                   "note": "not in the knowledge base (rank, abilities and danger unknown)"}
            return clean(out)
        rng = out.pop("level_range", None)
        out["level"] = tl if tl and tl > 0 else ("skull (10+ above you)" if tl == -1 else rng)
        if lv and tl and tl > 0:
            out["kill_xp"] = MX.kill_xp(lv, tl, elite=card.get("_rank") in (1, 2, 3))
        return clean(out)

    def block(self, ctx, triggers=None, budget_tokens=BLOCK_TOKENS):
        """The knowledge block for a live callout: step, next, pace, heuristics, target, facts (see the coach's
        system prompt), at most `budget_tokens` (len/4). Also remembers the dashboard panel."""
        if not self.ok:
            return {"unavailable": self.reason}
        out = {}
        pfacts = []
        if ctx.route is not None and ctx.idx is not None:
            try:
                out["step"], nxt, pfacts = self._step_part(ctx)
                if nxt:
                    out["next"] = nxt
            except Exception as e:  # noqa: BLE001
                out["step"] = {"error": "step analysis failed: %s" % e}
        elif ctx.error:
            out["note"] = ctx.error
        try:
            pace = self._pace_part(ctx)
            if pace:
                out["pace"] = pace
        except Exception:  # noqa: BLE001
            pace = None
        heur = self._heuristics_part(ctx, triggers)
        if heur:
            out["heuristics"] = heur
        try:
            tgt = self._target_part(ctx)
        except Exception:  # noqa: BLE001
            tgt = None
        if tgt:
            out["target"] = tgt
        try:
            comm = community_notes(self.kb, ctx, limit=2, recent_days=45, live=True)
        except Exception:  # noqa: BLE001
            comm = None
        if comm:
            out["community"] = comm
        out["facts"] = uniq(collect_facts(out) + pfacts)
        self._remember_panel(ctx, out)
        return fit_block(out, budget_tokens)

    def _remember_panel(self, ctx, block):
        st = block.get("step") or {}
        why = None
        if st and not st.get("error"):
            why = dict(st)
            why["why_text"] = {c: why_text(c) for c in st.get("why") or []}
            why["next"] = block.get("next")
            why["plan"] = clean({"exp_level": st.get("exp_level"), "level_now": st.get("level_now"),
                                  "delta": round(st["level_now"] - st["exp_level"], 2)
                                  if st.get("level_now") is not None and st.get("exp_level") is not None else None})
        elif block.get("note"):
            why = {"unavailable": block["note"]}
        self._panel = {"key": (ctx.summary.get("guide"), ctx.summary.get("step"), ctx.level),
                       "t": time.time(), "why": why, "pace": block.get("pace")}

    def panel(self, summary=None):
        """{"why", "pace"[, "stale"]} for the dashboard from the last block; never touches the KB."""
        p = self._panel
        if p is None:
            return {"why": None, "pace": None}
        out = {"why": copy.deepcopy(p["why"]), "pace": dict(p["pace"] or {}) or None}
        s = summary or {}
        if s:
            if (s.get("guide"), s.get("step"), as_int(s.get("level"))) != p["key"]:
                out["stale"] = True
            lv = as_int(s.get("level"))
            w = out["why"]
            if lv and w and w.get("plan") is not None and not out.get("stale"):
                now = level_f(lv, max(0, as_int(s.get("xp")) or 0))
                w["plan"]["level_now"] = now
                if w["plan"].get("exp_level") is not None:
                    w["plan"]["delta"] = round(now - w["plan"]["exp_level"], 2)
            if out["pace"] is not None:
                xph = as_int(s.get("xp_per_hour"))
                if xph:
                    out["pace"]["xp_per_hour"] = xph
                    bench = out["pace"].get("benchmark_xph")
                    out["pace"]["ratio"] = round(xph / float(bench), 2) if bench else None
                if as_num(s.get("ttl_seconds")):
                    out["pace"]["your_eta_s"] = int(s["ttl_seconds"])
        return out
