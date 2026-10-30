"""Saying reminders whose condition just became true, and the periodic nudges (the rules are reminders.py's): one of
the coach thread's tickers (Coach._loop calls tick about once a second; it checks every reminder_check_s)."""
import time

import reminders as reminders_mod
from log import log_once


class ReminderTicker:
    """coach: the Coach whose reminders (deps.reminders), game state and knowledge pack these check."""

    def __init__(self, coach):
        self.coach = coach
        self.index = None                     # reminders.NpcIndex over the current kb.db
        self.t = 0.0                          # when reminders were last checked

    def tick(self):
        """Say reminders whose condition just became true, and the periodic nudges (reminders.py). -> the messages."""
        c = self.coach
        r = c.deps.reminders
        if r is None or c.state is None or not r.items:
            return []
        now = c._now()
        if now - self.t < float(c.opt.get("reminder_check_s")):
            return []
        self.t = now
        try:
            s = c.state.summary()
            me = c.state.player_geo()
        except Exception as e:  # noqa: BLE001
            log_once("reminders.summary", e)
            return []
        if c.pack is not None and (self.index is None or self.index.pack is not c.pack):
            self.index = reminders_mod.NpcIndex(c.pack)
        data = None
        try:
            data = c.pack.data if c.pack is not None else None
        except Exception as e:  # noqa: BLE001
            log_once("reminders.data", e)
        env = reminders_mod.Env(s, me, self.index, data, area_fn=lambda m, x, y: c.geo.area_at(m, x, y)[0],
                                near_yd=float(c.opt.get("reminder_near_yd")))
        flags = c.state.coach_flags() if hasattr(c.state, "coach_flags") else {}
        out = []
        for rem, why, detail in r.check(env, now):
            msg = {"kind": "reminder", "text": reminders_mod.speech(rem, why, detail),
                   "speak": bool(flags.get("coach", True)), "urgent": False, "reminder": rem["id"], "t": time.time()}
            c.emit(msg)
            out.append(msg)
        return out
