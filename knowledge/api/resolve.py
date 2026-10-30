"""KnowledgePack, step resolution: the live summary to a guide, route and step, and the analyzer's brief of a
step in the coach's terms."""

from knowledge.api._layer import MX, R
from knowledge.api.context import Ctx, s0_quests, step_quests
from knowledge.api.guides import player_from
from knowledge.api.reasons import why_text
from knowledge.api.values import as_int, clean, cut, first_line


class ResolveMixin:
    """Live context, guide and step resolution, the analyzer's briefs (trimmed for the coach)."""

    # --------------------------------------------------------------------------------------------- context
    def context(self, summary, coach_flags=None, triggers=None, book=None, recent=None, now=None, me=None):
        s = summary if isinstance(summary, dict) else {}
        flags = coach_flags if coach_flags is not None else (s.get("coach_flags") or {})
        muted = [k for k, v in (flags or {}).items() if not v and k not in ("coach", "voice")]
        ctx = Ctx(self, s, player_from(s, self.rxp), muted, triggers, book, recent, now, me)
        if not self.ok:
            ctx.error = self.reason
            return ctx
        if ctx.player is None:
            ctx.error = "player class unknown (no live state from the addon yet)"
            return ctx
        if not s.get("guide"):
            ctx.error = "no RestedXP guide in the live state"
            return ctx
        try:
            self._resolve(ctx)
        except Exception as e:  # noqa: BLE001
            ctx.error = "guide resolution failed: %s: %s" % (type(e).__name__, e)
        return ctx

    def guide_key(self, name, group=None, faction=None):
        return self.guides.resolve(name, group, faction) if self.ok else None

    def _resolve(self, ctx):
        s, p = ctx.summary, ctx.player
        s0 = s0_quests(s)
        key = (s.get("guide"), s.get("guide_group"), p["class"], p.get("race"), p["faction"], s.get("step"), s0,
               ctx.settings)
        hit = self._res.get(key)
        if hit is None:
            gk = self.guide_key(s["guide"], s.get("guide_group"), p["faction"])
            idx = None
            if gk is not None:
                idx = self._step_idx(R.get_route(self.kb, gk, p), s, s0)
            hit = self.cache_put(self._res, key, (gk, idx), 512)
        gk, idx = hit
        if gk is None:
            ctx.error = "guide %r (%s) is not in the knowledge base" % (s.get("guide"), s.get("guide_group") or "?")
            return
        route = R.get_route(self.kb, gk, p)
        ctx.guide_key, ctx.guide, ctx.route = gk, self.guide_name(gk), route
        if idx is None:
            ctx.error = "RestedXP step %s is not in the analysed route of %s" % (s.get("step"), ctx.guide)
            return
        ctx.idx, ctx.rxp_step_no, ctx.pos = idx, route.idx_to_rxp.get(idx), route.index_of(idx)

    @staticmethod
    def _step_idx(route, s, s0):
        if not route.steps:
            return None
        st = as_int(s.get("step"))
        if st is None:
            return route.steps[0]["idx"]
        idx = route.rxp_to_idx.get(st)
        if s0 and (idx is None or not s0 & step_quests(route.by_idx[idx])):
            best = None
            for n in range(st - 6, st + 7):
                i2 = route.rxp_to_idx.get(n)
                if i2 is not None and s0 & step_quests(route.by_idx[i2]):
                    if best is None or abs(n - st) < abs(best[0] - st):
                        best = (n, i2)
            if best:
                idx = best[1]
        if idx is None and route.rxp_to_idx and st > max(route.rxp_to_idx):
            idx = route.steps[-1]["idx"]
        return idx

    def brief(self, ctx, idx, gk=None):
        """The analyzer's brief of one step for this player (stored one when class/race match), cached."""
        gk = gk or ctx.guide_key
        p = ctx.player
        key = (gk, idx, p["class"], p.get("race"), p["faction"], ctx.settings)
        b = self._briefs.get(key)
        if b is None:
            b = self.cache_put(self._briefs, key, R.why(self.kb, gk, idx, p) or {}, 2000)
        return b or None

    def live_compare(self, ctx, b):
        """The live character against the brief's plan (route.live_compare), or None."""
        lv = ctx.level
        if not lv or not b:
            return None
        try:
            return R.live_compare(self.kb, b, {"level": lv, "xp": ctx.xp, "quests_complete": ctx.completed()})
        except Exception:  # noqa: BLE001
            return None

    # --------------------------------------------------------------------------------------------- briefs, trimmed
    def alternative(self, ctx, a, route):
        """An analyzer alternative in the coach's terms: fact id, and why_not with RestedXP step numbers."""
        why_not = []
        for w in a.get("why_not") or []:
            if w.startswith("routed_later:"):
                gk, _, idx = w.split(":", 1)[1].rpartition("/")
                if route is not None and gk == route.guide_key and as_int(idx) is not None:
                    why_not.append("routed_later: step %s" % route.idx_to_rxp.get(int(idx), idx))
                else:
                    why_not.append("routed_later: %s" % self.guide_name(gk))
            else:
                why_not.append(w)
        fact = None
        if a.get("kind") == "quest" and a.get("id") is not None:
            fact = "q:%s" % a["id"]
        elif a.get("kind") == "grind" and a.get("id") is not None:
            fact = "c:%s" % a["id"]
        return clean({"kind": a.get("kind"), "name": a.get("name"), "fact": fact, "level": a.get("level"),
                       "xp": a.get("xp"), "xp_per_min": a.get("xp_per_min_est"), "dist_yd": a.get("dist_yd"),
                       "why_not": why_not})

    def _quest_entry(self, qid, q, rx, lv, full=False):
        e = {"name": q.get("name") or "quest %s" % qid, "fact": "q:%s" % qid, "role": q.get("role"),
             "xp_at_level": q.get("xp_at_level") if q.get("xp_full") is not None else "unknown",
             "unlocks_xp": q.get("unlocks_xp") or None, "decay_at_level": q.get("decay_at_level"),
             "turnin_step": rx(q["turnin_step"]) if q.get("turnin_step") is not None else None}
        if q.get("turnin_step") is None and q.get("turnin_guide"):
            e["turnin_guide"] = self.guide_name(q["turnin_guide"])
        if lv and q.get("xp_full") is not None:
            now = MX.quest_xp(q["xp_full"], q.get("level"), lv)
            if now is not None and now != q.get("xp_at_level"):
                e["xp_your_level"] = now
        if full:
            e.update(level=q.get("level"), type=q.get("type") if q.get("type") not in (None, "normal") else None,
                     unlocks=(q.get("unlocks") or [])[:4] or None)
        return clean(e)

    def trim_brief(self, ctx, b, route, alts=3, full=True):
        """A brief in the coach's terms: RestedXP step numbers, names, fact ids; at full=True everything a 'why'
        question needs, else the compact form of the knowledge block."""
        rx = route.idx_to_rxp.get
        d = self.data
        gname = self.guide_name(route.guide_key)
        j = b.get("justification") or {}
        lv = ctx.level
        qname = lambda q: (d.quest(q) or {}).get("name") or "quest %s" % q  # noqa: E731
        out = {"n": rx(b.get("idx")) or b.get("rxp_step_no"), "guide": gname, "kind": b.get("kind"),
               "xp_now": b.get("xp_now"), "unlocks_xp": b.get("xp_unlocked") or 0,
               "quests": [self._quest_entry(qid, q, rx, lv, full) for qid, q in (j.get("quests") or {}).items()],
               "why": list(j.get("why") or []),
               "rxp_notes": [cut(n, 160) for n in (j.get("rxp_notes") or [])[:4 if full else 3]],
               "co_located": ["%s (%s, step %s)" % (c.get("name") or c.get("kind"), c.get("kind"), rx(c.get("idx")))
                              for c in (j.get("co_located") or [])[:4 if full else 3]],
               "risks": [clean({"name": r.get("name"), "level": r.get("level"), "note": cut(r.get("note"), 120),
                                 "elite": r.get("elite"), "dist_yd": r.get("dist_yd"),
                                 "fact": "c:%s" % r["creature_id"] if r.get("creature_id") else None})
                         for r in (j.get("risks") or [])[:3 if full else 2]],
               "enables": [{"quest": qname(e["quest"]), "step": rx(e.get("idx"))}
                           for e in ((b.get("deps") or {}).get("enables") or [])[:4 if full else 3]],
               "alternatives": [self.alternative(ctx, a, route) for a in (b.get("alternatives") or [])[:alts]],
               "time_s": b.get("time_s"), "xp_per_min": b.get("xp_per_min"),
               "exp_level": b.get("exp_level_before"), "fact": ctx.step_fact(b.get("idx"), gname)}
        if full:
            st = route.by_idx.get(b.get("idx"))
            deps = b.get("deps") or {}
            out.update(
                text=first_line(st["text"] if st else None, 140), exp_level_after=b.get("exp_level_after"),
                needs=[{"quest": qname(n["quest"]), "from_step": rx(n.get("from_idx"))}
                       for n in (deps.get("needs") or [])[:4]],
                last_chance=bool(deps.get("last_chance")) or None,
                kills=[clean({"name": k.get("name"), "count": k.get("count"), "xp_each": k.get("xp_each"),
                               "purpose": k.get("purpose"),
                               "fact": "c:%s" % k["creature_id"] if k.get("creature_id") else None})
                       for k in (j.get("kills") or [])[:3]])
            tr = j.get("travel") or {}
            if (tr.get("dist_yd") or 0) > 0 or tr.get("mode"):
                out["travel"] = clean({"dist_yd": tr.get("dist_yd"), "time_s": tr.get("time_s"),
                                        "mode": tr.get("mode"), "observed": tr.get("observed")})
            if out["why"]:
                out["why_meaning"] = {c: why_text(c) for c in out["why"]}
        why = out["why"]
        out = clean(out)
        out["why"] = why                                   # kept even when empty: "no special reason code"
        return out
