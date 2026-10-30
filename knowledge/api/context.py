"""The live context of one coach turn (Ctx) and the readers of the live summary it is built on."""
import time

from knowledge.api._layer import R
from knowledge.api.values import as_int, as_num


def s0_quests(s):
    """Quest ids the live S0 page lists for the current step (empty when it describes another step)."""
    steps = s.get("steps") or []
    s0 = steps[0] if steps and isinstance(steps[0], dict) else None
    if not s0 or (s.get("step") is not None and as_int(s0.get("index")) not in (None, as_int(s.get("step")))):
        return frozenset()
    return frozenset(as_int(e.get("quest")) for e in s0.get("elements") or []
                     if isinstance(e, dict) and as_int(e.get("quest")))


def step_quests(st):
    """Every quest a route step touches: accepted, turned in, worked on or gated by."""
    q = st["quests"]
    return set(q["accept"]) | set(q["turnin"]) | set(q["objective"]) | set(q["gate"])


def log_quest_ids(s, done_only=False):
    """Quest ids in the live quest log (only the complete ones with done_only)."""
    out = []
    for q, v in (s.get("quests") or {}).items():
        qi = as_int(q)
        if qi is None:
            continue
        if done_only and not (isinstance(v, dict) and v.get("done")):
            continue
        out.append(qi)
    return out


class Ctx:
    """Everything a tool or the block needs about now. Built by KnowledgePack.context()."""

    def __init__(self, pack, summary, player=None, muted=(), triggers=(), book=None, recent=(), now=None, me=None):
        self.pack = pack
        self.kb = pack.kb if pack is not None else None
        self.summary = summary if isinstance(summary, dict) else {}
        self.player = player
        self.muted = list(muted or ())
        self.triggers = list(triggers or ())
        self.book = dict(book or {})
        self.recent = list(recent or ())
        self.now = float(now) if now else time.time()
        self.me = me
        self.guide_key = self.guide = self.route = None
        self.idx = self.rxp_step_no = self.pos = None
        self.error = None
        self._settings = False

    @property
    def settings(self):
        """The player's RestedXP options as a cache key (route.settings_key): the pack's caches carry it, because
        those options change which steps RestedXP shows and so what its step numbers mean."""
        if self._settings is False:
            self._settings = R.settings_key(self.player) if self.player is not None and R is not None else None
        return self._settings

    @property
    def level(self):
        return as_int(self.summary.get("level"))

    @property
    def xp(self):
        return max(0, as_int(self.summary.get("xp")) or 0)

    def p(self):
        """The player, or a neutral default for lookups that only rank results."""
        return self.player or {"class": "WARRIOR", "faction": "Alliance"}

    def here(self):
        s = self.summary
        um, x, y = as_int(s.get("map")), as_num(s.get("x")), as_num(s.get("y"))
        return (um, x, y) if um and x is not None and y is not None else None

    def need(self, guide=True, step=True, player=False):
        """None when the context has what a tool needs, else {"unavailable": reason}."""
        if self.pack is None or not self.pack.ok:
            return {"unavailable": self.pack.reason if self.pack is not None else "no knowledge base"}
        if (player or guide) and self.player is None:
            return {"unavailable": "player class unknown (no live state from the addon yet)"}
        if guide and self.route is None:
            return {"unavailable": self.error or "no RestedXP guide in the live state"}
        if step and self.idx is None:
            return {"unavailable": self.error or "current RestedXP step unknown"}
        return None

    def step_fact(self, idx, guide=None):
        return "s:%s/%s" % (guide or self.guide, idx)

    def completed(self):
        return log_quest_ids(self.summary, done_only=True)
