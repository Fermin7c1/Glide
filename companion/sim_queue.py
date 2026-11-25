"""The one line of WoWSims runs on the player's PC (wowsims.py runs each): Glide's own BiS ranks (bis.py), the stat
weights (sim_weights.py) and the sims the player asks for share it, one wowsimcli process at a time, the most urgent
first (PRIO_ASKED, PRIO_REWARD, PRIO_BIS, PRIO_WEIGHTS; first come, first served within one).

The game comes first. A background run (the default) starts below normal process priority, with no window, on a
quarter of the PC's cores: GOMAXPROCS, which WoWSims' runner honours (v1.0.4, 10000 iterations: 0.36 s on all 32
threads, 0.49 s on 8, 1.9 s on 1, the same DPS on each). While the player is in combat, dead or in an instance
(pause_fn; busy() reads it from the game state) background runs wait between runs; a run already started always
finishes (seconds). An asked sim (PRIO_ASKED, or background=False) never waits for the player: it runs at normal
priority on half the cores. WoWSims' runner never sees the player's API keys.

submit() -> a concurrent.futures.Future for {"dps", "stdev", "iters"}: the player's average DPS, its spread across
iterations (the average's own error is stdev / sqrt(iters)) and the iterations run; or raising simbuild.SimError.
A future cancelled before its run starts is skipped (cancel() drops every queued job of a priority).
"""
import concurrent.futures
import heapq
import itertools
import os
import subprocess
import threading
import time

import wowsims
from log import log_once
from simbuild import SimError

PRIO_ASKED, PRIO_REWARD, PRIO_BIS, PRIO_WEIGHTS = 0, 1, 2, 3
WAIT_S = 2.0                     # a paused queue looks at the player again this often
STALE_S = 60                     # busy(): game state older than this isn't the player now (the game was closed)
CORES = os.cpu_count()           # the PC's logical processors (None: unknown, taken as 1)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
NORMAL = getattr(subprocess, "NORMAL_PRIORITY_CLASS", 0)   # (named: else a runner inherits an engine's lower one)


def busy(st, now=None):
    """Whether the player is busy in the game (st: state.State): in combat, dead (or a ghost) or in an instance. The
    app's pause_fn for the queue. Inside an instance the addon sends the map without a position (State.lua: the game
    gives none there). Game state the game stopped sending (closed) isn't busy."""
    core = getattr(st, "core", None)
    if not core:
        return False
    last = getattr(st, "last_frame", None)
    if isinstance(last, (int, float)) and (time.time() if now is None else now) - last > STALE_S:
        return False
    if core.get("c") == "1" or str(core.get("d") or "0") != "0":
        return True
    return "mp" in core and not str(core.get("px") or "").lstrip("-").isdigit()


def _iters(metrics, req):
    """The iterations a run made: as the result counts them, else as asked."""
    agg = metrics.get("aggregatorData")
    n = agg.get("n") if isinstance(agg, dict) else None
    if isinstance(n, int) and n > 0:
        return n
    return int((req.get("simOptions") or {}).get("iterations") or 0)


class SimQueue:
    """The worker thread starts with the first submit(). ws: the wowsims.WoWSims to run with; pause_fn() -> True while
    the player is busy (combat, dead, an instance): background runs wait; cores: the PC's (CORES)."""

    def __init__(self, ws, pause_fn, now=time.time, cores=CORES):
        self.ws, self.pause_fn, self._now = ws, pause_fn, now
        self.cores = max(1, int(cores or 1))
        self._heap = []                              # (prio, order, job): the most urgent, then the oldest, first
        self._order = itertools.count()
        self._cv = threading.Condition()
        self._thread = None
        self.running = None                          # the job whose run is going
        self.paused_since = None                     # when background runs began waiting for the player

    # ---- how a run starts ---------------------------------------------------------------------------------------
    def threads(self, background):
        """The runner's threads: a quarter of the cores in the background, half for an asked sim."""
        return max(1, self.cores // (4 if background else 2))

    def env(self, background):
        """The runner's environment: the engine's, without API keys, with GOMAXPROCS (wowsims.runner_env)."""
        return wowsims.runner_env(self.threads(background))

    @staticmethod
    def flags(background):
        """Windows' creationflags: no window, below normal priority in the background (normal for an asked sim)."""
        return NO_WINDOW | (BELOW_NORMAL if background else NORMAL)

    # ---- callers --------------------------------------------------------------------------------------------------
    def submit(self, req, prio, iterations=None, seed=None, tag=None, background=True):
        """Queue a RaidSimRequest (simbuild.request). iterations, seed: when given, they replace its simOptions' (the
        caller's request isn't changed); tag: the WoWSims release (None: the one in use); background=False (always
        for PRIO_ASKED): run without waiting for the player, at normal priority. -> a Future for {"dps", "stdev",
        "iters"} (or raising simbuild.SimError)."""
        opts = dict(req.get("simOptions") or {})
        if iterations is not None:
            opts["iterations"] = int(iterations)
        if seed is not None:
            opts["randomSeed"] = int(seed)
        prio, fut = int(prio), concurrent.futures.Future()
        job = {"req": dict(req, simOptions=opts), "prio": prio, "tag": tag, "fut": fut, "t": self._now(),
               "background": bool(background) and prio != PRIO_ASKED}
        with self._cv:
            heapq.heappush(self._heap, (prio, next(self._order), job))
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, daemon=True, name="sim-queue")
                self._thread.start()
            self._cv.notify()
        return fut

    def sim(self, req, tag=None, prio=PRIO_WEIGHTS, iterations=None, seed=None, background=True, timeout=None):
        """submit() and wait: the average DPS, like WoWSims.sim(request, tag) (what sim_weights runs through it)."""
        if threading.current_thread() is self._thread:
            raise RuntimeError("a queued sim can't wait for the queue")
        return self.submit(req, prio, iterations, seed, tag, background).result(timeout)["dps"]

    def cancel(self, prio=None):
        """Drop the queued jobs (of one priority): their futures are cancelled; a run already going finishes.
        -> how many were dropped."""
        n = 0
        with self._cv:
            for _, _, job in self._heap:
                if (prio is None or job["prio"] == prio) and job["fut"].cancel():
                    n += 1
            self._purge()
        return n

    def pending(self, prio=None):
        """Jobs queued and not started (of one priority)."""
        with self._cv:
            return sum(1 for _, _, job in self._heap
                       if not job["fut"].cancelled() and (prio is None or job["prio"] == prio))

    def status(self):
        """{"running": the priority of the run going or None, "queued": jobs waiting, "paused_since": when background
        runs began waiting for the player, or None} (for the log)."""
        with self._cv:
            return {"running": self.running["prio"] if self.running else None,
                    "queued": sum(1 for _, _, job in self._heap if not job["fut"].cancelled()),
                    "paused_since": self.paused_since}

    # ---- the worker ------------------------------------------------------------------------------------------------
    def _purge(self):
        if any(job["fut"].cancelled() for _, _, job in self._heap):
            self._heap = [e for e in self._heap if not e[2]["fut"].cancelled()]
            heapq.heapify(self._heap)

    def _paused(self):
        try:
            return bool(self.pause_fn())
        except Exception as e:  # noqa: BLE001 - game state that can't be read doesn't hold the sims
            log_once("sim_queue.pause", e)
            return False

    def _take(self, paused):
        """(Under the lock) the next job that may run: the most urgent one, or while the player is busy the most
        urgent that doesn't wait for them. -> the job, or None."""
        if not paused:
            return heapq.heappop(self._heap)[2]
        asked = [e for e in self._heap if not e[2]["background"]]
        if not asked:
            return None
        entry = min(asked, key=lambda e: e[:2])
        self._heap.remove(entry)
        heapq.heapify(self._heap)
        return entry[2]

    def _next(self):
        """Wait for the next job to run, marked running."""
        paused = None                                # (looked at only when the next job is a background one)
        while True:
            with self._cv:
                self._purge()
                if not self._heap:
                    self.paused_since = None
                    self._cv.wait()
                    paused = None
                    continue
                if paused is not None or not self._heap[0][2]["background"]:
                    job = self._take(paused)
                    paused = None
                    if job is None:                  # (busy, and only background jobs: they wait)
                        if self.paused_since is None:
                            self.paused_since = self._now()
                        self._cv.wait(WAIT_S)
                        continue
                    if not job["fut"].set_running_or_notify_cancel():
                        continue
                    if job["background"]:
                        self.paused_since = None
                    self.running = job
                    return job
            paused = self._paused()                  # (outside the lock: it reads the game state)

    def _run(self, job):
        fut = job["fut"]
        try:
            try:
                bg = job["background"]
                result = self.ws.run(job["req"], job["tag"], env=self.env(bg), creationflags=self.flags(bg))
                m = wowsims.dps(result)
                out = {"dps": float(m["avg"]), "stdev": float(m.get("stdev") or 0.0), "iters": _iters(m, job["req"])}
            except SimError:
                raise
            except subprocess.TimeoutExpired as e:
                raise SimError("WoWSims took longer than %s s" % e.timeout) from e
            except Exception as e:  # noqa: BLE001 - whatever stopped the run is the job's error
                raise SimError("WoWSims couldn't sim: %s" % e) from e
        except SimError as e:
            fut.set_exception(e)
        else:
            fut.set_result(out)
        finally:
            with self._cv:
                self.running = None

    def _loop(self):
        while True:
            try:
                self._run(self._next())
            except Exception as e:  # noqa: BLE001 - the queue goes on with the next job
                log_once("sim_queue.loop", e)
                time.sleep(WAIT_S)
