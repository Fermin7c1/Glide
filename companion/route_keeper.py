"""Keeping the knowledge block and the way to the current step fresh, off the capture loop: one of the coach thread's
tickers (Coach._loop calls tick about once a second). The route search itself runs on a worker thread."""
import math
import threading
import time
from typing import Any

from log import log_once


class RouteKeeper:
    """coach: the Coach whose knowledge pack and game state this keeps warm. last_route (api.plan_to_step: the way
    to the current step, for the map) and last_walk (api.walk_to_step: the walking route, for questions) are the
    latest results."""

    def __init__(self, coach):
        self.coach = coach
        self.last_walk: Any = None                 # walking route to the current step (api.walk_to_step), for questions
        self.last_route: Any = None                # the way to the current step (api.plan_to_step), for the map
        self.busy = False
        self.key, self.pos, self.t = None, None, 0.0
        self.warm_key, self.warm_t, self.warmed = None, 0.0, False

    def tick(self):
        self.warm()
        self.route_tick()

    def warm(self):
        """Keep the knowledge block (and the dashboard's why panel) fresh in this thread: rebuilt when the guide,
        step or level changes, else every 30 s. Never in the capture loop."""
        c = self.coach
        if c.pack is None or c.state is None:
            return
        if not self.warmed:
            self.warmed = True
            c.pack.warm()
        core = getattr(c.state, "core", None) or {}
        if not core:
            return
        g = (getattr(c.state, "pages", None) or {}).get("G") or {}
        key = (g.get("gn"), g.get("gg"), core.get("st"), core.get("l"))
        if key == self.warm_key and time.time() - self.warm_t < 30:
            return
        self.warm_key, self.warm_t = key, time.time()
        try:
            s = c.state.summary()
            k = c.kctx(s)
            c.pack.block(k, None)
        except Exception as e:  # noqa: BLE001 - a background refresh; the next callout retries
            log_once("route.warm", e)

    def route_tick(self):
        """Keep the way to the current step fresh for the map (api.plan_to_step): again when the step, RestedXP's
        arrow, a flight or the map changes, when you've moved ~12 yd (at most every 2 s), and every 20 s. The search
        runs on a worker thread so a long route never holds up a callout."""
        c = self.coach
        if c.pack is None or c.state is None or self.busy or not hasattr(c.api, "plan_to_step"):
            return
        s = c.state.summary()
        if s.get("x") is None:
            return
        arrow = s.get("arrow") or {}
        key = (s.get("guide"), s.get("step"), arrow.get("map"), arrow.get("x"), arrow.get("y"), s.get("fly_to"),
               s.get("on_taxi"), s.get("taxi_to"), s.get("map"))
        now = time.time()
        moved = self.pos is None or math.hypot(s["x"] - self.pos[0], s["y"] - self.pos[1]) > 0.4
        if now - self.t < 2 or (key == self.key and not moved and now - self.t < 20):
            return
        self.key, self.pos, self.t = key, (s["x"], s["y"]), now
        self.busy = True

        def work():
            try:
                k = c.kctx(s)
                plan = c.api.plan_to_step(k)
                self.last_route = plan
                self.last_walk = c.api.walk_to_step(k, plan)
            except Exception as e:  # noqa: BLE001 - the next tick tries again
                log_once("route.plan", e)
            finally:
                self.busy = False
        threading.Thread(target=work, name="route", daemon=True).start()

    def view(self, s):
        """last_route for the dashboard, its walk starting where you are now (the search may be a few seconds old)."""
        r = self.last_route
        if not r or not r.get("segments") or s.get("x") is None or r.get("map") != s.get("map"):
            return r
        segs = [dict(sg) for sg in r["segments"]]
        pts = segs[0].get("points") or []
        if len(pts) >= 2:
            k = min(range(len(pts)), key=lambda i: (pts[i][0] - s["x"]) ** 2 + ((pts[i][1] - s["y"]) * 0.67) ** 2)
            # keep the point you're heading for: the nearest one, unless you're already past it
            if k + 1 < len(pts):
                ax, ay = pts[k]
                bx, by = pts[k + 1]
                if (s["x"] - ax) * (bx - ax) + (s["y"] - ay) * (by - ay) > 0:
                    k += 1
            segs[0]["points"] = [[s["x"], s["y"]]] + pts[k:]
        return dict(r, segments=segs)
