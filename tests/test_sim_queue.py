#!/usr/bin/env python3
"""The sim queue (companion/sim_queue.py) against a fake WoWSims, then through wowsims.WoWSims with a fake runner (a
script standing in for wowsimcli, reporting the environment and process priority it got): one run at a time, the
most urgent first (asked, reward, BiS, weights; first come first served within one); background runs below normal
priority on a quarter of the cores (GOMAXPROCS), asked ones at normal priority on half, never with the player's API
keys; background runs wait while the player is in combat, dead or in an instance (busy() on the game state), asked
ones don't, and a run already going always finishes; iterations and seed replace the request's (the caller's request
unchanged); errors come back as SimError and the queue goes on; cancelled jobs are skipped; WoWSims.run's defaults
(a run outside the queue: a new release's smoke test) are a background run's.

Run: python3 tests/test_sim_queue.py   (prints OK)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import sim_queue  # noqa: E402
import wowsims  # noqa: E402
from sim_queue import PRIO_ASKED, PRIO_BIS, PRIO_REWARD, PRIO_WEIGHTS, SimQueue  # noqa: E402
from simbuild import SimError  # noqa: E402

T = 5                                                    # seconds any wait may take before the test fails
sim_queue.WAIT_S = 0.02                                  # (a paused queue looks again quickly here)
WIN = sys.platform == "win32"
os.environ["GLIDE_SIMQ_MARK"] = "engine-env"
os.environ["GLIDE_SIMQ_TEST_API_KEY"] = "not-a-real-key"   # (made up: shows the runner never gets *_API_KEY)


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


def req(label, iterations=1000, seed=1):
    return {"label": label, "simOptions": {"iterations": iterations, "randomSeed": seed}}


def result(avg, stdev=30.0, n=None):
    dps = {"avg": avg, "stdev": stdev}
    if n is not None:
        dps["aggregatorData"] = {"n": n, "sumSq": 0}
    return {"raidMetrics": {"parties": [{"players": [{"dps": dps}]}]}}


class FakeWS:
    """WoWSims: DPS 600 + the seed; a gate per label holds that run until it's set; a few labels fail."""

    def __init__(self):
        self.calls, self.gates, self.started = [], {}, {}
        self.lock = threading.Lock()
        self.running, self.most = 0, 0
        self.queue = None

    def gate(self, label):
        self.gates[label], self.started[label] = threading.Event(), threading.Event()
        return self.gates[label]

    def run(self, request, tag=None, timeout=wowsims.SIM_TIMEOUT_S, env=None, creationflags=None):
        label = request["label"]
        with self.lock:
            self.running += 1
            self.most = max(self.most, self.running)
            self.calls.append({"label": label, "tag": tag, "env": env, "flags": creationflags,
                               "opts": dict(request["simOptions"]), "thread": threading.current_thread().name})
        try:
            if label in self.started:
                self.started[label].set()
                check(self.gates[label].wait(T), "the %s run was let go" % label)
            if label == "simerr":
                raise SimError("WoWSims v1 couldn't sim: unknown field")
            if label == "oserr":
                raise OSError("no such file: wowsimcli.exe")
            if label == "slow":
                raise subprocess.TimeoutExpired(["wowsimcli"], 300)
            if label == "nodps":
                return {"raidMetrics": {}}
            if label == "reenter":
                return result(self.queue.sim(req("inner"), "v1"))
            opts = request["simOptions"]
            return result(600.0 + opts["randomSeed"], n=None if label == "noagg" else opts["iterations"])
        finally:
            with self.lock:
                self.running -= 1

    def labels(self):
        return [c["label"] for c in self.calls]


def done_all(futs):
    for f in futs:
        try:
            f.result(T)
        except SimError:
            pass


# ---- priority: one run at a time, the most urgent first, first come first served within one -----------------------
ws = FakeWS()
q = SimQueue(ws, lambda: False, cores=32)
ws.queue = q
first = ws.gate("first")
futs = [q.submit(req("first"), PRIO_WEIGHTS)]
check(ws.started["first"].wait(T), "the first job started")
for label, prio in (("w2", PRIO_WEIGHTS), ("bis1", PRIO_BIS), ("reward", PRIO_REWARD), ("bis2", PRIO_BIS),
                    ("asked", PRIO_ASKED), ("w3", PRIO_WEIGHTS)):
    futs.append(q.submit(req(label), prio))
check(q.pending() == 6 and q.pending(PRIO_BIS) == 2, "six jobs wait, two of them BiS: %d" % q.pending())
st = q.status()
check(st["running"] == PRIO_WEIGHTS and st["queued"] == 6, "status: the weights run going, six queued: %r" % st)
first.set()
done_all(futs)
check(ws.labels() == ["first", "asked", "reward", "bis1", "bis2", "w2", "w3"],
      "the order: the run going, then asked, reward, BiS, weights: %r" % ws.labels())
check(ws.most == 1, "never two runs at once")
check({c["thread"] for c in ws.calls} == {"sim-queue"}, "the runs are the queue's own thread's")
check(q.status() == {"running": None, "queued": 0, "paused_since": None}, "idle afterwards: %r" % q.status())

# ---- how a run starts: GOMAXPROCS, process priority, no API keys --------------------------------------------------
bg = next(c for c in ws.calls if c["label"] == "bis1")
ask = next(c for c in ws.calls if c["label"] == "asked")
check(bg["env"]["GOMAXPROCS"] == "8", "background: a quarter of 32 cores: %r" % bg["env"]["GOMAXPROCS"])
check(ask["env"]["GOMAXPROCS"] == "16", "asked: half of 32 cores: %r" % ask["env"]["GOMAXPROCS"])
for c in (bg, ask):
    check(not [k for k in c["env"] if k.upper().endswith("_API_KEY")], "no API key reaches the runner")
    check(c["env"].get("GLIDE_SIMQ_MARK") == "engine-env", "the engine's environment otherwise")
if WIN:
    check(bg["flags"] == 0x08000000 | 0x00004000, "background: no window, below normal: %#x" % bg["flags"])
    check(ask["flags"] == 0x08000000 | 0x00000020, "asked: no window, normal priority: %#x" % ask["flags"])
else:
    check(bg["flags"] == 0 and ask["flags"] == 0, "no Windows flags elsewhere")
for cores, b, a in ((32, 8, 16), (8, 2, 4), (6, 1, 3), (2, 1, 1), (1, 1, 1), (None, 1, 1), (0, 1, 1)):
    sq = SimQueue(ws, lambda: False, cores=cores)
    check((sq.threads(True), sq.threads(False)) == (b, a), "%r cores: %r" % (cores, (sq.threads(True),
                                                                                  sq.threads(False))))
check(SimQueue(ws, lambda: False).cores == max(1, os.cpu_count() or 1), "the PC's cores by default")

# ---- results: iterations and seed replace the request's; the caller's request is unchanged ------------------------
base = req("opts", iterations=10000, seed=1)
r = q.submit(base, PRIO_BIS, iterations=1000, seed=7, tag="v1.0.4").result(T)
check(r == {"dps": 607.0, "stdev": 30.0, "iters": 1000}, "the result: %r" % r)
call = ws.calls[-1]
check(call["opts"] == {"iterations": 1000, "randomSeed": 7} and call["tag"] == "v1.0.4", "as run: %r" % call)
check(base == req("opts", iterations=10000, seed=1), "the caller's request unchanged: %r" % base)
r = q.submit(req("noagg", iterations=2500, seed=3), PRIO_BIS).result(T)
check(r == {"dps": 603.0, "stdev": 30.0, "iters": 2500}, "iterations as asked when the result doesn't count: %r" % r)
check(q.sim(req("plain", seed=9), "v2") == 609.0 and ws.calls[-1]["tag"] == "v2", "sim(): the DPS, like WoWSims.sim")
check(ws.calls[-1]["env"]["GOMAXPROCS"] == "8", "sim(): a background run (the weights')")

# ---- errors come back as SimError, and the queue goes on -----------------------------------------------------------
for label, says in (("simerr", "unknown field"), ("oserr", "no such file"), ("slow", "took longer"),
                    ("nodps", "no DPS"), ("reenter", "can't wait for the queue")):
    e = q.submit(req(label), PRIO_BIS).exception(T)
    check(isinstance(e, SimError) and says in str(e), "%s: a SimError saying %r: %r" % (label, says, e))
check(isinstance(q.submit(req("oserr"), PRIO_BIS).exception(T).__cause__, OSError), "the cause kept")
check(q.submit(req("after"), PRIO_BIS).result(T)["dps"] == 601.0, "the queue goes on after errors")
check("inner" not in ws.labels(), "a run never waits on the queue it runs in")

# ---- cancelled jobs are skipped -------------------------------------------------------------------------------------
hold = ws.gate("hold")
f_hold = q.submit(req("hold"), PRIO_BIS)
check(ws.started["hold"].wait(T), "the held run started")
f_a, f_b = q.submit(req("a"), PRIO_BIS), q.submit(req("b"), PRIO_BIS)
f_c, f_d = q.submit(req("c"), PRIO_WEIGHTS), q.submit(req("d"), PRIO_WEIGHTS)
check(f_b.cancel(), "a queued job can be cancelled")
check(q.cancel(PRIO_WEIGHTS) == 2 and q.pending() == 1, "cancel(prio) drops that priority's queued jobs")
check(not f_hold.cancel() and q.cancel() == 1, "a run going can't be cancelled; cancel() drops the rest")
f_e = q.submit(req("e"), PRIO_BIS)
hold.set()
check(f_hold.result(T)["dps"] == 601.0 and f_e.result(T)["dps"] == 601.0, "the held run and a later job ran")
check(all(f.cancelled() for f in (f_a, f_b, f_c, f_d)), "the cancelled futures say so")
check(not {"a", "b", "c", "d"} & set(ws.labels()), "cancelled jobs never run: %r" % ws.labels())

# ---- the player is busy: background runs wait between runs, asked ones don't, a run going finishes -----------------
ws2, busy = FakeWS(), [True]
q2 = SimQueue(ws2, lambda: busy[0], now=lambda: 5000.0, cores=8)
f_bg = q2.submit(req("bg"), PRIO_BIS)
time.sleep(0.2)
check(ws2.calls == [] and not f_bg.done(), "a background job waits while the player is busy")
check(q2.status()["paused_since"] == 5000.0 and q2.status()["queued"] == 1, "waiting since: %r" % q2.status())
check(q2.submit(req("asked"), PRIO_ASKED).result(T)["dps"] == 601.0, "an asked sim runs anyway")
check(q2.submit(req("fg"), PRIO_WEIGHTS, background=False).result(T)["dps"] == 601.0, "so does background=False")
check(ws2.calls[0]["env"]["GOMAXPROCS"] == "4" and ws2.calls[1]["env"]["GOMAXPROCS"] == "4", "half of 8 cores")
check(not f_bg.done() and ws2.labels() == ["asked", "fg"], "the background job still waits: %r" % ws2.labels())
busy[0] = False
check(f_bg.result(T)["dps"] == 601.0 and q2.status()["paused_since"] is None, "it runs once the player is free")
long_run = ws2.gate("long")
f_long = q2.submit(req("long"), PRIO_BIS)
check(ws2.started["long"].wait(T), "a background run started")
busy[0] = True                                          # (combat starts mid-run)
f_next = q2.submit(req("next"), PRIO_BIS)
long_run.set()
check(f_long.result(T)["dps"] == 601.0, "the run going finishes (never killed)")
time.sleep(0.2)
check(not f_next.done() and "next" not in ws2.labels(), "the next background run waits")
busy[0] = False
check(f_next.result(T)["dps"] == 601.0, "and runs after")


def boom():
    raise RuntimeError("no game state")


check(SimQueue(FakeWS(), boom).submit(req("x"), PRIO_BIS).result(T)["dps"] == 601.0,
      "game state that can't be read doesn't hold the sims")


# ---- busy(): combat, dead or a ghost, an instance (the map without a position); not a closed game ------------------
class St:
    def __init__(self, core, last=1000.0):
        self.core, self.last_frame = core, last


OUT = {"c": "0", "d": "0", "mp": "1453", "px": "4512", "py": "6633"}
for core, want, what in ((OUT, False, "out in the world"),
                         (dict(OUT, c="1"), True, "in combat"),
                         (dict(OUT, d="1"), True, "dead"),
                         (dict(OUT, d="2"), True, "a ghost"),
                         (dict(OUT, mp="1581", px="", py=""), True, "in an instance (no position)"),
                         ({"c": "0", "d": "0", "mp": ""}, True, "no map at all"),
                         ({"c": "0", "d": "0"}, False, "an addon that sends no map"),
                         ({}, False, "no frame yet")):
    check(sim_queue.busy(St(core), now=1001.0) is want, "busy(): %s" % what)
check(sim_queue.busy(St(dict(OUT, c="1")), now=1000.0 + sim_queue.STALE_S + 1) is False, "a closed game isn't busy")
check(sim_queue.busy(None) is False, "no game state")

# ---- through wowsims.WoWSims: the runner gets the environment and the priority; run()'s defaults a background run's
FAKE = r'''
import ctypes, json, os, sys
_, _, inp, _, out = sys.argv[1:6]
req = json.load(open(inp, encoding="utf-8"))
prio = None
if os.name == "nt":
    k = ctypes.windll.kernel32
    k.GetCurrentProcess.restype = ctypes.c_void_p
    k.GetPriorityClass.argtypes = [ctypes.c_void_p]
    k.GetPriorityClass.restype = ctypes.c_uint32
    prio = k.GetPriorityClass(k.GetCurrentProcess())
dps = {"avg": 500.0 + req["simOptions"]["randomSeed"], "stdev": 20.0,
       "aggregatorData": {"n": req["simOptions"]["iterations"], "sumSq": 0}}
probe = {"gomaxprocs": os.environ.get("GOMAXPROCS"), "prio": prio, "mark": os.environ.get("GLIDE_SIMQ_MARK"),
         "keys": sorted(k for k in os.environ if k.upper().endswith("_API_KEY"))}
json.dump({"raidMetrics": {"parties": [{"players": [{"dps": dps}]}]}, "probe": probe}, open(out, "w"))
'''


class ProbeWS(wowsims.WoWSims):
    def run(self, request, tag=None, **kw):
        res = super().run(request, tag, **kw)
        self.probes.append(res["probe"])
        return res


tmp = tempfile.mkdtemp(prefix="glide-simq-")
try:
    runner = os.path.join(tmp, "fake_wowsimcli.py")
    with open(runner, "w", encoding="utf-8") as fh:
        fh.write(FAKE)
    pws = ProbeWS(tmp, command=lambda tag: [sys.executable, "-I", runner])
    pws.probes = []
    check(pws.sim(req("default", seed=4), "v1") == 504.0, "WoWSims.sim as before")
    p = pws.probes[-1]
    # a run outside the queue (the smoke test of a new release) is a background one too: no API keys, a quarter of
    # the cores, below normal priority (regression: it had the engine's environment and priority)
    check(p["mark"] == "engine-env" and p["keys"] == [] and p["gomaxprocs"] == (
        os.environ.get("GOMAXPROCS") or str(max(1, (os.cpu_count() or 1) // 4))), "run()'s default: %r" % p)
    if WIN:
        check(p["prio"] == 0x4000, "run()'s default: below normal priority: %r" % p["prio"])
    pq = SimQueue(pws, lambda: False, cores=32)
    r = pq.submit(req("bg", iterations=300, seed=2), PRIO_BIS, tag="v1").result(T * 4)
    check(r == {"dps": 502.0, "stdev": 20.0, "iters": 300}, "a background run's result: %r" % r)
    p = pws.probes[-1]
    check(p["gomaxprocs"] == "8" and p["mark"] == "engine-env" and p["keys"] == [], "background environment: %r" % p)
    if WIN:
        check(p["prio"] == 0x4000, "the background runner is below normal priority: %r" % p["prio"])
    pq.submit(req("ask"), PRIO_ASKED, tag="v1").result(T * 4)
    p = pws.probes[-1]
    check(p["gomaxprocs"] == "16" and p["keys"] == [], "asked environment: %r" % p)
    if WIN:
        check(p["prio"] == 0x20, "the asked runner is at normal priority: %r" % p["prio"])
    gone = SimQueue(wowsims.WoWSims(tmp, command=lambda tag: [os.path.join(tmp, "missing", wowsims.EXE)]),
                    lambda: False, cores=4)
    e = gone.submit(req("gone"), PRIO_BIS, tag="v1").exception(T * 4)
    check(isinstance(e, SimError), "a runner that can't start: SimError: %r" % e)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("OK")
