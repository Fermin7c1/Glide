#!/usr/bin/env python3
"""The daily cap on web research (companion/research_budget.py), against a fake clock and fake settings: research.web
off allows nothing; at most research.web_per_day lookups a day; at most research.usd_per_day dollars (none once the
next lookup would likely pass it: today's spending plus the larger of today's average lookup and its own estimate,
the day's first one included); a share of the day for background lookups; the day rolls over at local midnight; the
ledger (data/research/spend.json) survives a restart and one of an earlier day reads as nothing spent, while one of a
later day (the clock set back) is kept, unless it is far ahead; settings that are missing or of the wrong kind spend
nothing. A lookup is reserved before it goes out (saved at once, so the engine stopping mid-lookup still counts it)
and settled after: what it cost, the larger of that and its estimate when a request went unanswered, nothing when
none went out. Through research.WebResolver with a fake LLM client: a lookup is charged what its own calls cost, a
lookup whose search ran through the local Tavily or Brave tool costs nothing but still counts toward the job cap, a
call that never sent a request isn't counted, the lookup is given the day's room, and the resolver asks the budget
before every lookup. Never a real model or search API.

Run: python3 tests/test_research_budget.py   (prints OK)
"""
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
          "TAVILY_API_KEY", "BRAVE_API_KEY"):
    os.environ[k] = ""
import research  # noqa: E402
import research_budget  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


def local_noon(y, m, d, hour=12, minute=0):
    """A time on the local clock (the day rolls over at local midnight, whatever the time zone)."""
    return time.mktime((y, m, d, hour, minute, 0, 0, 0, -1))


GOOD = json.dumps({"facts": [{"kind": "drop", "npc": "Golemagg the Incinerator", "zone": "Molten Core",
                              "url": "https://www.wowhead.com/classic/item=18842"}]})


class FakeClient:
    """The LLM client as WebResolver uses it: one model, never busy; web_research answers with GOOD at `cost`
    (meta as the real client's: local searches when `local`), or fails before any answer when `fail`."""
    cfg = {"fast_model": "claude-haiku-5-5"}
    web_off = None

    def __init__(self, cost=0.05, local=False):
        self.cost, self.local, self.fail, self.calls = cost, local, False, 0

    def available(self, model):
        return True

    def busy(self):
        return False

    def web_search_tools(self, model, n):
        return [{"name": "web_search"}], self.local

    def web_research(self, prompt, system="", max_searches=3, **kw):
        self.calls += 1
        if self.fail:
            return None, {"cost_usd": 0.0, "calls": 0, "searches": 0, "local_searches": 0, "urls": [],
                          "why": "failed"}
        meta = {"cost_usd": 0.0 if self.local else self.cost, "calls": 2, "searches": 0 if self.local else 1,
                "local_searches": 1 if self.local else 0, "urls": ["https://www.wowhead.com/classic/item=18842"],
                "why": None, "stop": "end_turn"}
        return GOOD, meta


tmp = tempfile.mkdtemp(prefix="glide-budget-")
try:
    home = os.path.join(tmp, "engine")
    clock = [local_noon(2026, 11, 24)]
    opt = {"web": True, "web_per_day": 3, "usd_per_day": 0.20}
    b = research_budget.Budget(home, lambda: dict(opt), now=lambda: clock[0])
    ledger = os.path.join(home, "data", "research", "spend.json")

    # ---- nothing spent yet: allowed; off: nothing ------------------------------------------------------------------
    check(b.allow() == (True, None), b.allow())
    check(b.spent() == {"day": "2026-11-24", "n": 0, "usd": 0.0}, b.spent())
    opt["web"] = False
    ok, why = b.allow()
    check(not ok and why == "web research is off", why)
    opt["web"] = True

    # ---- the job cap ---------------------------------------------------------------------------------------------
    for _ in range(3):
        check(b.allow()[0], "under the cap")
        b.charge(0.01)
    ok, why = b.allow()
    check(not ok and "3 lookups" in why, ("job cap", why))
    d = json.load(open(ledger, encoding="utf-8"))
    check(d == {"day": "2026-11-24", "n": 3, "usd": 0.03}, d)

    # ---- the day rolls over at local midnight ----------------------------------------------------------------------
    clock[0] = local_noon(2026, 11, 24, 23, 59)
    check(not b.allow()[0], "still today")
    clock[0] = local_noon(2026, 11, 25, 0, 1)
    check(b.allow() == (True, None) and b.spent() == {"day": "2026-11-25", "n": 0, "usd": 0.0}, b.spent())

    # ---- the dollar cap: none once spent, nor when the next lookup would likely pass it ---------------------------
    opt.update(web_per_day=100, usd_per_day=0.20)
    b.charge(0.08)
    check(b.allow()[0], "0.08 + 0.08 is under 0.20")
    b.charge(0.08)
    ok, why = b.allow()
    check(not ok and "$0.20" in why, ("0.16 + an average 0.08 would pass 0.20", why))
    opt["usd_per_day"] = 0.30
    check(b.allow()[0], "a higher cap, live")
    b.charge(0.20)
    check(not b.allow()[0] and b.spent()["usd"] == 0.36, b.spent())

    # ---- a share of the day for background lookups ---------------------------------------------------------------
    clock[0] = local_noon(2026, 11, 26)
    opt.update(web_per_day=4, usd_per_day=1.0)
    b.charge(0.0)
    b.charge(0.0)
    check(not b.allow(0.5)[0] and b.allow()[0], "half of 4 lookups is for the background, the rest for questions")

    # ---- the ledger survives a restart; another day's reads as nothing spent ---------------------------------------
    again = research_budget.Budget(home, lambda: dict(opt), now=lambda: clock[0])
    check(again.spent() == {"day": "2026-11-26", "n": 2, "usd": 0.0}, again.spent())
    clock[0] = local_noon(2026, 11, 27)
    check(again.spent()["n"] == 0, "a new day")
    with open(ledger, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    broken = research_budget.Budget(home, lambda: dict(opt), now=lambda: clock[0])
    check(broken.spent() == {"day": "2026-11-27", "n": 0, "usd": 0.0} and broken.allow()[0], "a broken ledger")

    # ---- settings missing or of the wrong kind: nothing is spent ------------------------------------------------
    for bad in ({}, {"web": True}, {"web": "yes", "web_per_day": 5, "usd_per_day": 1},
                {"web": True, "web_per_day": 5, "usd_per_day": "lots"}, None):
        nb = research_budget.Budget(os.path.join(tmp, "other"), lambda bad=bad: bad, now=lambda: clock[0])
        check(not nb.allow()[0], ("nothing allowed on", bad))
    nb = research_budget.Budget(os.path.join(tmp, "other"), lambda: 1 / 0, now=lambda: clock[0])
    check(not nb.allow()[0], "settings that can't be read")

    # ---- through WebResolver: charged what its own calls cost, one lookup each ------------------------------------
    clock[0] = local_noon(2026, 12, 1)
    opt.update(web=True, web_per_day=3, usd_per_day=0.20)
    budget = research_budget.Budget(os.path.join(tmp, "r1"), lambda: dict(opt), now=lambda: clock[0])
    client = FakeClient(cost=0.04)
    wr = research.WebResolver(lambda: client, budget, now=lambda: clock[0])
    srcs = wr.resolve("i:18842", {"item": "Staff of Dominance"})
    check(srcs and srcs[0]["npc_name"] == "Golemagg the Incinerator", srcs)
    check(budget.spent() == {"day": "2026-12-01", "n": 1, "usd": 0.04}, budget.spent())

    # a call that never sent a request: not a lookup, not charged (and the job may run again)
    client.fail = True
    check(wr.resolve("i:18843") is None and wr.last_why == "failed", wr.last_why)
    check(budget.spent()["n"] == 1, budget.spent())
    client.fail = False

    # the cap reached: the resolver asks the budget first and calls nothing
    wr.resolve("i:18844")
    wr.resolve("i:18845")
    calls = client.calls
    check(budget.spent()["n"] == 3 and wr.resolve("i:18846") is None, budget.spent())
    check(client.calls == calls and wr.last_why.startswith("not ready: today's 3 lookups"), (client.calls, wr.last_why))

    # ---- a local Tavily / Brave search: no cost, but it counts toward the job cap -------------------------------
    budget2 = research_budget.Budget(os.path.join(tmp, "r2"), lambda: dict(opt), now=lambda: clock[0])
    local = FakeClient(local=True)
    wr2 = research.WebResolver(lambda: local, budget2, now=lambda: clock[0])
    for i in range(3):
        check(wr2.resolve("i:%d" % (20000 + i)), "a local search finds it")
    check(budget2.spent() == {"day": "2026-12-01", "n": 3, "usd": 0.0}, budget2.spent())
    check(wr2.resolve("i:20003") is None and local.calls == 3, ("capped though free", local.calls))

    # ---- booked before it goes out: reserve, then settle ------------------------------------------------------------
    clock[0] = local_noon(2026, 12, 2)
    opt.update(web=True, web_per_day=20, usd_per_day=0.20)
    home3 = os.path.join(tmp, "r3")
    b3 = research_budget.Budget(home3, lambda: dict(opt), now=lambda: clock[0])
    res = b3.reserve(0.06)
    check(res["room"] == 0.20 and b3.spent() == {"day": "2026-12-02", "n": 1, "usd": 0.06}, (res, b3.spent()))
    # the engine stopping mid-lookup (a coach restart, an update, a crash): the reservation is already on disk
    check(research_budget.Budget(home3, lambda: dict(opt), now=lambda: clock[0]).spent()["n"] == 1, "saved at once")
    b3.settle(res, 0.04)
    check(b3.spent() == {"day": "2026-12-02", "n": 1, "usd": 0.04}, ("what it cost, in its place", b3.spent()))
    res = b3.reserve(0.06)
    check(abs(res["room"] - 0.16) < 1e-9, res)
    b3.settle(res, sent=False)
    check(b3.spent() == {"day": "2026-12-02", "n": 1, "usd": 0.04}, ("nothing went out: taken back", b3.spent()))
    # the day's first lookup is held to the cap too (its likely cost), and so is each after (the larger of the two)
    b4 = research_budget.Budget(os.path.join(tmp, "r4"), lambda: dict(opt, usd_per_day=0.05), now=lambda: clock[0])
    check(not b4.allow(1.0, 0.06)[0] and b4.allow(1.0, 0.04)[0] and b4.allow()[0], "the first one, at its estimate")
    b4.charge(0.01)
    check(not b4.allow(1.0, 0.045)[0] and b4.allow(1.0, 0.03)[0], "today's average 0.01, the estimate larger")

    # through WebResolver: a request that went out and got no answer (a timeout) is booked at the larger of what was
    # answered and its estimate; one that never went out, not at all
    class Estimating(FakeClient):
        def research_estimate(self, model, max_searches=3, max_tokens=1500):
            return 0.06

        def web_research(self, prompt, system="", max_searches=3, **kw):
            self.calls += 1
            self.max_usd = kw.get("max_usd")
            return None, {"cost_usd": 0.01, "sent": 2, "calls": 1, "searches": 1, "local_searches": 0, "urls": [],
                          "why": "failed"}
    b5 = research_budget.Budget(os.path.join(tmp, "r5"), lambda: dict(opt), now=lambda: clock[0])
    est_client = Estimating()
    wr5 = research.WebResolver(lambda: est_client, b5, now=lambda: clock[0])
    check(wr5.resolve("i:18842") is None and wr5.last_why == "failed", wr5.last_why)
    check(b5.spent() == {"day": "2026-12-02", "n": 1, "usd": 0.06} and est_client.max_usd == 0.20,
          ("timed out: booked at its estimate, the lookup given the day's room", b5.spent()))

    # ---- the clock set back (a time sync, a time zone, by hand) never gives back what was spent ---------------------
    home6 = os.path.join(tmp, "r6")
    b6 = research_budget.Budget(home6, lambda: dict(opt), now=lambda: clock[0])
    clock[0] = local_noon(2026, 12, 10)
    b6.charge(0.20)
    check(not b6.allow()[0], "today's spent")
    clock[0] = local_noon(2026, 12, 9)                   # (a day back)
    check(not b6.allow()[0] and b6.spent()["day"] == "2026-12-10", ("still that day's ledger", b6.spent()))
    clock[0] = local_noon(2026, 12, 10)                  # (set right again)
    check(not b6.allow()[0] and b6.spent()["usd"] == 0.20, b6.spent())
    clock[0] = local_noon(2026, 12, 9)
    check(research_budget.Budget(home6, lambda: dict(opt), now=lambda: clock[0]).spent()["usd"] == 0.20,
          "after a restart too")
    clock[0] = local_noon(2026, 12, 11)                  # (the next day: a new one)
    check(b6.allow()[0] and b6.spent() == {"day": "2026-12-11", "n": 0, "usd": 0.0}, b6.spent())
    b6.charge(0.05)
    clock[0] = local_noon(2027, 3, 1)
    b6.charge(0.05)
    clock[0] = local_noon(2026, 12, 12)                  # (a clock that was months ahead, put right: a new day)
    check(b6.spent() == {"day": "2026-12-12", "n": 0, "usd": 0.0}, b6.spent())
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
