"""How much web research the engine may do in a day: the engine's first spending cap. research.WebResolver asks
allow() before each lookup, books it with reserve() before it goes out and settle() after: at most
research.web_per_day lookups and research.usd_per_day dollars a day, and none while research.web is off.

A lookup's dollars are what its own model calls cost (the LLM client prices each answer's usage, web searches on the
Claude API included: llm_client.LLMClient.web_research). A search through the local Tavily or Brave tool costs nothing
here, but its lookup still counts toward the daily number, so those services' own quotas are spared too. A lookup
that would likely pass the dollar cap (today's spending plus the larger of today's average lookup and this one's
likely-high cost, est: LLMClient.research_estimate) isn't allowed, the day's first one included. A lookup may be
allowed only a share of the day (allow(share)): research keeps half for questions and hovered items.

A lookup is booked before it goes out (reserve: one more lookup, at its likely-high cost, saved at once), so one that
times out, fails after the server billed it, or is cut short by the engine stopping still counts; settle() then puts
what it really cost in its place (the larger of the two when a request went out and got no answer), or takes it back
when nothing went out. reserve() also says the dollars left for it (room), which the lookup stays within.

data/research/spend.json: {"day": "2026-11-24", "n": 3, "usd": 0.04}, the player's local day (it rolls over at local
midnight; a ledger of an earlier day reads as nothing spent, and one of a later day, up to CLOCK_SLACK_DAYS ahead, is
kept: a clock set back or a time zone crossed never gives back what was spent). settings_fn() -> {"web": bool,
"web_per_day": int, "usd_per_day": float}, read through settings_manifest.json by the caller; a value that is missing
or of the wrong kind reads as off or zero, so nothing is spent on a setting that isn't there.
"""
import datetime
import os
import threading
import time

import jsonfile
from log import log_once

CLOCK_SLACK_DAYS = 2        # a ledger this many days ahead of the clock is kept (a clock set back, a time zone);
                            # further ahead it was a clock far off, and the day starts afresh


def _number(v):
    """A finite non-negative number from a setting, else 0."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        return 0
    return max(0, v)


class Budget:
    """The daily cap on web research. home: the engine's home (the ledger is <home>/data/research/spend.json);
    settings_fn: the research settings (see the module text); now: the clock (the tests' seam)."""

    def __init__(self, home, settings_fn, now=time.time):
        self.path = os.path.join(home, "data", "research", "spend.json")
        self.settings_fn, self._now = settings_fn, now
        self.lock = threading.Lock()
        self._led = self._load()

    def _today(self):
        return time.strftime("%Y-%m-%d", time.localtime(self._now()))

    def _load(self):
        try:
            d = jsonfile.read(self.path)
        except (OSError, ValueError):
            d = {}
        d = d if isinstance(d, dict) else {}
        n, usd = d.get("n"), d.get("usd")
        return {"day": str(d.get("day") or ""), "n": int(_number(n)), "usd": float(_number(usd))}

    def _ledger(self):
        """Today's ledger: an earlier day's reads as a new day (nothing spent); a later day's (the clock set back, a
        time zone crossed) is kept until the clock passes it, unless it is more than CLOCK_SLACK_DAYS ahead. Called
        with the lock held."""
        today, day = self._today(), self._led["day"]
        if day != today:
            try:
                ahead = (datetime.date.fromisoformat(day) - datetime.date.fromisoformat(today)).days
            except ValueError:
                ahead = 0                                   # (no day, or not a date: a new ledger)
            if not 0 < ahead <= CLOCK_SLACK_DAYS:
                self._led = {"day": today, "n": 0, "usd": 0.0}
        return self._led

    def _settings(self):
        try:
            s = self.settings_fn() or {}
        except Exception as e:  # noqa: BLE001 - a setting that can't be read: nothing is spent
            log_once("research_budget.settings", e)
            s = {}
        return {"web": s.get("web") is True, "web_per_day": int(_number(s.get("web_per_day"))),
                "usd_per_day": float(_number(s.get("usd_per_day")))}

    def allow(self, share=1.0, est=0.0):
        """May a web lookup run now? share: of the day's lookups and dollars it may use (research keeps half of them
        for questions and hovered items); est: what it would likely cost at most (LLMClient.research_estimate), so
        none goes out that would likely pass the cap, the day's first one included. -> (True, None) or (False, why
        not, in a few words for the log)."""
        s = self._settings()
        if not s["web"]:
            return False, "web research is off"
        share = min(1.0, max(0.0, float(share)))
        with self.lock:
            led = self._ledger()
            if led["n"] >= s["web_per_day"] * share:
                return False, "today's %d lookups are done" % int(s["web_per_day"] * share)
            cap, usd = s["usd_per_day"] * share, led["usd"]
            avg = usd / led["n"] if led["n"] else 0.0
            if usd >= cap or usd + max(avg, float(_number(est))) > cap:
                return False, "today's $%.2f is spent" % cap
        return True, None

    def reserve(self, est, share=1.0):
        """Book a lookup before it goes out: one more lookup at est dollars (its likely-high cost), saved at once, so
        one cut short still counts. -> the reservation for settle(): {"day", "usd": est, "room": the dollars this
        share of the day had left before it (what the lookup may spend)}."""
        s = self._settings()
        share = min(1.0, max(0.0, float(share)))
        est = float(_number(est))
        with self.lock:
            led = self._ledger()
            room = max(0.0, s["usd_per_day"] * share - led["usd"])
            led["n"] += 1
            led["usd"] = round(led["usd"] + est, 6)
            data = dict(led)
        self._write(data)
        return {"day": data["day"], "usd": est, "room": room}

    def settle(self, res, usd=0.0, sent=True):
        """After a reserved lookup: what it cost (usd) takes the reservation's place, or (sent False: no request went
        out) the reservation is taken back, lookup and dollars. A reservation of a day that is over stays as booked."""
        with self.lock:
            led = self._ledger()
            if not res or led["day"] != res.get("day"):
                return
            if sent:
                led["usd"] = round(max(0.0, led["usd"] + float(_number(usd)) - res["usd"]), 6)
            else:
                led["n"] = max(0, led["n"] - 1)
                led["usd"] = round(max(0.0, led["usd"] - res["usd"]), 6)
            data = dict(led)
        self._write(data)

    def charge(self, usd, jobs=1):
        """Book what a lookup cost (usd: its model calls' dollars, 0 for a local search) and how many lookups it
        counts as, and save the ledger."""
        with self.lock:
            led = self._ledger()
            led["n"] += max(0, int(jobs or 0))
            led["usd"] = round(led["usd"] + float(_number(usd)), 6)
            data = dict(led)
        self._write(data)

    def _write(self, data):
        try:
            jsonfile.write_atomic(self.path, data)
        except OSError as e:
            log_once("research_budget.save", e)

    def spent(self):
        """Today's ledger: {"day": "YYYY-MM-DD", "n": lookups, "usd": dollars}."""
        with self.lock:
            return dict(self._ledger())
