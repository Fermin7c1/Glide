"""Background knowledge refresh: the companion starts on the databases it has and rebuilds them while you play.

start-glide.cmd used to run build_db (glide.db, ~15-30 s) and build_kb (kb.db, ~50 s) before the companion started,
and since every play session adds a log, SavedVariables and cache files, that happened on nearly every start. Now:

1. At startup, promote(): the newest data/glide.next-*.db and data/kb.next-*.db left by the last session's
   background builds become data/glide.db and data/kb.db (nothing has them open yet); older ones are deleted.
2. Refresher (a thread, after startup): tools/extract_maps.py (the client's map data; a no-op unless the game was
   patched since the last extraction), build_db.py --out data/glide.next-<t>.db --compare <the glide.db in use>
   (skips when no input changed), then build_kb.py --if-stale --against <the kb.db in use> --out
   data/kb.next-<t>.db (skips when that one is current). Both run as child processes at below-normal priority so
   the game keeps its frame rate. The running session's own log is left out of the staleness check.
3. When a new build is ready, on_ready(glide_path, kb_path) lets the app switch the coach over to it in place.
   The files it switched away from stay open until the companion exits, so every build gets a name of its own
   (a second re-read in one session can't overwrite the file the coach is using), and the in-use paths are what
   the next build compares against.

A first start without data/kb.db still builds synchronously in start-glide.cmd (the coach needs one to start).
"""
import os
import re
import subprocess
import sys
import threading
import time

from log import log_once

BELOW_NORMAL = 0x00004000          # Windows BELOW_NORMAL_PRIORITY_CLASS


def staged(data_dir, name):
    """Builds waiting to replace data/<name>.db: <name>.next-<t>.db, and the older names <name>.next.db,
    <name>.new.db and <name>.next.new.db (build_kb's fallback when its target was open)."""
    try:
        files = os.listdir(data_dir)
    except OSError:
        return []
    return [os.path.join(data_dir, f) for f in files
            if f.endswith(".db") and (f.startswith(name + ".next") or f == name + ".new.db")]


def promote(data_dir, log=print):
    """Install the newest staged build over data/<name>.db and delete the rest. Returns the names installed."""
    done = []
    for name in ("glide", "kb"):
        cur = os.path.join(data_dir, name + ".db")
        waiting = sorted(staged(data_dir, name), key=os.path.getmtime)
        if not waiting:
            continue
        new = waiting[-1]
        for p in waiting[:-1]:
            _remove(p)
        if os.path.exists(cur) and os.path.getmtime(new) <= os.path.getmtime(cur):
            _remove(new)                     # an obsolete leftover
            continue
        try:
            os.replace(new, cur)
            done.append(os.path.basename(new))
        except OSError as e:
            log("[refresh] could not install %s: %s" % (os.path.basename(new), e))
    if done:
        log("[refresh] installed the knowledge built during the last session: %s" % ", ".join(done))
    return done


def _remove(p):
    try:
        os.remove(p)
    except OSError:
        pass


class Refresher:
    """Runs the two builds once, in the background; on_ready(glide_path, kb_path) when a new kb.next.db exists."""

    def __init__(self, root, on_ready=None, log=print, python=None, ignore=(), delay_s=20.0, runner=None,
                 force=False, live=None):
        """force: re-read everything even when it looks current (the desktop app's "Re-read game data"): the map
        extraction, glide.db and kb.db all rebuild. live: {"glide": path, "kb": path}, the databases the coach uses
        now (default data/glide.db, data/kb.db); builds are compared against them and never written over them."""
        self.root, self.on_ready, self.log = root, on_ready, log
        self.live = dict(live or {})
        self.force = force
        self.python = python or sys.executable
        self.ignore = list(ignore)
        self.delay_s = delay_s
        self.runner = runner or self._run
        self.status = {"state": "waiting", "glide": None, "kb": None, "seconds": None, "force": force,
                       "started": None, "finished": None}

    def start(self):
        threading.Thread(target=self.run, daemon=True, name="kb-refresh").start()
        return self

    def _run(self, args, timeout=1800):
        # stdin=DEVNULL: under the desktop app the companion's own stdin is a pipe its main thread is blocked reading;
        # a child inheriting it hangs at startup on Windows (its stdin query waits behind that pending read)
        kw = {"cwd": self.root, "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "text": True,
              "encoding": "utf-8", "errors": "replace", "timeout": timeout}
        if os.name == "nt":
            kw["creationflags"] = BELOW_NORMAL | 0x08000000      # + CREATE_NO_WINDOW
        r = subprocess.run([self.python] + args, **kw)
        return r.returncode, r.stdout or ""

    def run(self):
        time.sleep(self.delay_s)             # let the companion, the capture and the voice settle first
        t0 = time.time()
        self.status["started"] = t0
        force = ["--force"] if self.force else []
        data = os.path.join(self.root, "data")
        live_glide = self.live.get("glide") or os.path.join(data, "glide.db")
        live_kb = self.live.get("kb") or os.path.join(data, "kb.db")
        for name, keep in (("glide", live_glide), ("kb", live_kb)):
            for p in staged(data, name):     # earlier builds the coach moved past; one still open just stays
                if os.path.abspath(p) != os.path.abspath(keep):
                    _remove(p)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        gnext, knext = os.path.join(data, "glide.next-%s.db" % stamp), os.path.join(data, "kb.next-%s.db" % stamp)
        # the client's map data (zone layout, map images, terrain): re-extracted only when the game was patched
        self.status["state"] = "maps"
        code, out = self.runner([os.path.join("tools", "extract_maps.py")] + force)
        self.status["maps"] = "ok" if code == 0 else "skipped" if code == 2 else "failed"
        if code not in (0, 2):
            self.log("[refresh] map extraction failed (exit %s): %s" % (code, out.strip()[-300:]))
        self.status["state"] = "glide.db"
        code, out = self.runner([os.path.join("tools", "build_db.py"), "--out", gnext, "--compare", live_glide] + force)
        new_glide = os.path.exists(gnext) and code == 0
        self.status["glide"] = "rebuilt" if new_glide else ("up to date" if code == 0 else "failed")
        if code != 0:
            self.log("[refresh] glide.db build failed (exit %s): %s" % (code, out.strip()[-300:]))
        self.status["state"] = "kb.db"
        args = [os.path.join("knowledge", "build_kb.py")] + ([] if self.force else ["--if-stale"]) + [
            "-q", "--against", live_kb, "--out", knext]
        glide_for_kb = gnext if new_glide else live_glide
        if os.path.abspath(glide_for_kb) != os.path.abspath(os.path.join(data, "glide.db")):
            args += ["--glide", glide_for_kb]
        for p in self.ignore:
            args += ["--ignore", p]
        code, out = self.runner(args)
        new_kb = os.path.exists(knext)
        self.status.update(state="done", kb="rebuilt" if new_kb else ("up to date" if code == 0 else "failed"),
                           seconds=round(time.time() - t0, 1), finished=time.time())
        if code != 0 and not new_kb:
            self.log("[refresh] kb.db build failed (exit %s): %s" % (code, out.strip()[-300:]))
        if (new_kb or new_glide) and self.on_ready:
            try:
                self.on_ready(gnext if new_glide else None, knext if new_kb else None)
            except Exception as e:  # noqa: BLE001
                self.log("[refresh] switching to the new knowledge failed: %s" % e)
        return self.status


IDLE = 0x00000040                  # Windows IDLE_PRIORITY_CLASS


class NavBuilder:
    """The walking map (tools/navmesh.py -> data/gamedata/nav/<map>.npz): built once per install, in the
    background, at idle priority with pauses (navmesh --throttle) so the game keeps its frame rate. A map is built
    by itself only when it's missing or older than NAV_REQUIRED (a rebuild forced at development time); game patches
    never trigger it. A map made by an older builder (NAV_VERSION) is listed in status["outdated"] so the app can
    suggest a rebuild; the Settings button runs it with force. The current continent goes first (first_map()).

    status: {"state": "waiting"|"checking"|"building"|"done"|"failed", "map", "tile", "tiles", "maps_done",
    "started", "finished", "force", "outdated": [map ids built by an older builder], "built": [map ids]}"""

    def __init__(self, root, python=None, log=print, first_map=None, wait_for=None):
        self.root, self.log = root, log
        self.python = python or sys.executable
        self.first_map = first_map or (lambda: None)
        self.wait_for = wait_for                    # a Refresher: the map extraction it runs comes first
        self.status = {"state": "waiting", "map": None, "tile": 0, "tiles": 0, "maps_done": 0, "started": None,
                       "finished": None, "force": False, "outdated": [], "built": []}
        self.proc = None
        self.lock = threading.Lock()

    def needed(self):
        """Continents with terrain extracted but no walking map new enough to use (missing, or older than
        NAV_REQUIRED). Also updates status["outdated"] / status["built"]."""
        import importlib.util
        if importlib.util.find_spec("numpy") is None:       # the builder needs it: no walking map without it
            return []
        from wowfiles import navformat      # the format's versions only: no numpy, no builder in the engine
        tdir = os.path.join(self.root, "data", "gamedata", "terrain")
        try:
            maps = sorted(int(f[:-4]) for f in os.listdir(tdir) if f.endswith(".npz") and f[:-4].isdigit())
        except OSError:
            return []
        path = lambda m: navformat.path(self.root, m)  # noqa: E731
        self.status["outdated"] = [m for m in maps if navformat.outdated(path(m))]
        self.status["built"] = [m for m in maps if navformat.current(path(m))]
        return [m for m in maps if not navformat.current(path(m))]

    def start(self, force=False):
        with self.lock:
            if self.status["state"] in ("checking", "building"):
                return dict(self.status, already=True)
            self.status.update(state="checking", force=force, started=time.time(), finished=None, maps_done=0,
                               tile=0, tiles=0, map=None)
        threading.Thread(target=self.run, args=(force,), daemon=True, name="nav-build").start()
        return dict(self.status)

    def run(self, force=False):
        r = self.wait_for
        while r is not None and r.status.get("state") not in ("done",) and not force:
            time.sleep(5)
        maps = self.needed()
        if force:
            maps = None
        if maps == []:
            self.status.update(state="done", finished=time.time())
            return
        first = self.first_map()
        order = sorted(maps or [], key=lambda m: (m != first, m)) if maps else None
        args = [os.path.join("tools", "navmesh.py")]
        if force:
            args.append("--force")
            if first is not None:
                args += ["--first", str(first)]
        else:
            args += ["--maps", ",".join(str(m) for m in order)]
        kw = {"cwd": self.root, "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT,
              "text": True, "encoding": "utf-8", "errors": "replace",
              "env": dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")}
        if os.name == "nt":
            kw["creationflags"] = IDLE | 0x08000000                 # + CREATE_NO_WINDOW
        self.status["state"] = "building"
        self.log("[nav] building the walking map (%s), in the background at idle priority" % (
            "every continent" if force else "continents " + ", ".join(str(m) for m in order)))
        try:
            self.proc = subprocess.Popen([self.python] + args, **kw)
            tail = []
            for line in self.proc.stdout:
                line = line.strip()
                tail = (tail + [line])[-8:]
                m = re.search(r"map (\d+): (\d+)/(\d+) tiles", line)
                if m:
                    self.status.update(map=int(m.group(1)), tile=int(m.group(2)), tiles=int(m.group(3)))
                m = re.search(r"map (\d+): (\d+) tiles \(", line)
                if m:
                    self.status["maps_done"] += 1
                    self.log("[nav] " + line)
            code = self.proc.wait()
        except Exception as e:  # noqa: BLE001
            code, tail = -1, [str(e)]
        self.proc = None
        self.status.update(state="done" if code == 0 else "failed", finished=time.time())
        try:
            self.needed()                           # refresh "outdated" / "built" for the app
        except Exception as e:  # noqa: BLE001
            log_once("nav.needed", e)
        if code != 0:
            self.log("[nav] walking map build failed (exit %s): %s" % (code, " | ".join(tail)[-300:]))

    def stop(self):
        p = self.proc
        if p is not None:
            try:
                p.terminate()
            except OSError:
                pass
