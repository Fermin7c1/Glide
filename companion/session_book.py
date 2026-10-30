"""What the coach keeps track of over a session for the knowledge layer, from the raw strip core on every frame:
deaths, whether the player has been seen mounted, and the RestedXP step visits (guide, step, from, to)."""
import time
from collections import deque

from state import as_int


class SessionBook:
    def __init__(self):
        self._deaths, self._was_dead, self._mounted_seen = [], False, False
        self._visit, self._visits = None, deque(maxlen=50)

    def observe(self, state, now):
        """Note one frame of game state (cheap: runs on every frame)."""
        core = getattr(state, "core", None) or {}
        if not core:
            return
        dead = (as_int(core.get("d")) or 0) > 0
        if dead and not self._was_dead:
            self._deaths.append(now)
            del self._deaths[:-50]
        self._was_dead = dead
        if core.get("mo") == "1":
            self._mounted_seen = True
        st = core.get("st")
        if st is None:
            return
        g = ((getattr(state, "pages", None) or {}).get("G") or {}).get("gn")
        key = (g, st)
        v = self._visit
        if v is None or v["key"] != key:
            if v is not None and now - v["t0"] >= 2:
                self._visits.append({"guide": v["key"][0], "step": as_int(v["key"][1]), "t0": v["t0"], "t1": now})
            self._visit = {"key": key, "t0": now}

    def summary(self, now=None):
        """deaths_session, deaths_recent (last 30 minutes), has_mount, in_group, visits."""
        now = time.time() if now is None else now
        return {"deaths_session": len(self._deaths), "deaths_recent": sum(1 for t in self._deaths if now - t <= 1800),
                "has_mount": True if self._mounted_seen else None, "in_group": False, "visits": list(self._visits)}
