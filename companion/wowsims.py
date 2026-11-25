"""WoWSims Forever (github.com/wowsims/forever, MIT): the DPS simulator Glide runs on the player's PC. None of it ships
in the installer (its item and spell data come from Blizzard's game files): the engine downloads its command-line
runner, wowsimcli, from the project's official GitHub releases, and keeps it on the newest one without an app update.

- At most once a day (CHECK_S), and on first use, the latest release is looked up (api.github.com). A newer one is
  downloaded (the runner for this PC only), checked against the release's checksums.txt (SHA-256), unpacked into
  data/wowsims/<tag>/ and tried (smoke) before it is used: WoWSims changes its input format often, so a release the
  requests don't run on (the sim ran and failed) is set aside ("failed") and the last one that ran stays in use.
  What may pass (no network, a download cut short, a sim that timed out or couldn't start, a checksum that didn't
  match fewer than MISSES times) is tried again after RETRY_S, never sooner.
- Files a request needs from the project at that release (a spec's rotation, ui/specs/.../*.apl.json) come from
  raw.githubusercontent.com at the release's tag, kept beside its runner; one a newer release no longer has falls
  back to an older release's copy.
- Data files read without simming (the item database research.py reads) are read at data_tag(): the release in use,
  else the latest release's tag, looked up at most once a day without downloading its runner.
- data/wowsims/state.json: {"current": tag, "checked": time, "failed": {tag: why}, "data": {"tag", "t"}} ("data":
  the latest tag data_tag() looked up, and when). On disk are kept the release in use, the one whose data is read
  (prune: research.ItemDBSource calls it when it takes a new tag, so a player who never sims doesn't keep every
  release's item database) and the newest other one.
"""
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

import jsonfile
from log import log, log_once
from simbuild import SimError

REPO = "wowsims/forever"
LATEST = "https://api.github.com/repos/%s/releases/latest" % REPO
RAW = "https://raw.githubusercontent.com/%s/%s/%s"          # repo, tag, path
SITE = "https://wowsims.com/forever/"                        # the project's site: Glide links back to it
CHECK_S = 24 * 3600
RETRY_S = 3600                                                # after a check or download that failed
MISSES = 3                                                    # checksum mismatches before a release is set aside
KEEP = 2                                                      # releases kept on disk: in use, and the one before
                                                              # (and the one whose data is read, when another)
MAX_JSON = 2 * 1024 * 1024                                   # the release listing, checksums
MAX_ZIP = 96 * 1024 * 1024                                   # the runner's zip (about 10 MB today)
MAX_FILE = 8 * 1024 * 1024                                   # a file from the repository (a rotation: a few KB)
TIMEOUT_S = 60
SIM_TIMEOUT_S = 300
TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")        # a release tag, as a folder name (never . or ..)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


def runner_env(threads=None):
    """The runner's environment: the engine's without its API keys (WoWSims' runner never sees them), with GOMAXPROCS
    (WoWSims' runner honours it): threads, else what the engine's environment sets, else a quarter of the cores."""
    env = {k: v for k, v in os.environ.items() if not k.upper().endswith("_API_KEY")}
    if threads is not None:
        env["GOMAXPROCS"] = str(max(1, int(threads)))
    elif not str(env.get("GOMAXPROCS") or "").strip().isdigit():
        env["GOMAXPROCS"] = str(max(1, (os.cpu_count() or 1) // 4))
    return env


class FetchError(SimError):
    """A file couldn't be fetched (and no kept release had it): may pass."""


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    """Redirects stay on https (GitHub sends release downloads to its CDN)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise ValueError("a WoWSims download redirected to a non-https address")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_HttpsOnly)


def _get(url, limit):
    """The bytes at an https URL, at most `limit` of them."""
    if not url.lower().startswith("https://"):
        raise ValueError("WoWSims is fetched over https only (%s)" % url[:80])
    req = urllib.request.Request(url, headers={"User-Agent": "Glide-coach"})
    with _opener.open(req, timeout=TIMEOUT_S) as r:
        data = r.read(limit + 1)
        size = r.headers.get("Content-Length") or ""
    if len(data) > limit:
        raise ValueError("%s is bigger than expected" % url.rsplit("/", 1)[-1])
    if size.isdigit() and int(size) != len(data):
        raise ValueError("%s came cut short" % url.rsplit("/", 1)[-1])
    return data


def asset_name():
    """The release zip with the runner for this PC (wowsimcli_<os>_<arch>.zip)."""
    osname = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
    return "wowsimcli_%s_%s.zip" % (osname, arch)


EXE = "wowsimcli.exe" if sys.platform == "win32" else "wowsimcli"


class WoWSims:
    """The runner on this PC: ensure() keeps it on the newest release that runs; sim() runs a request with it.
    get(url, limit) -> bytes and command(tag) -> the runner's argv prefix are the tests' seams; smoke(self, tag)
    raises when a new release can't run Glide's requests."""

    def __init__(self, home, get=_get, command=None, smoke=None, now=time.time):
        self.dir = os.path.join(home, "data", "wowsims")
        self._get, self._command, self._smoke, self._now = get, command, smoke, now
        self.lock = threading.RLock()
        self.state = self._load()
        self._tried = None                                    # when the last check ran (RETRY_S between)
        self._data_tried = None                               # ... and data_tag()'s last look-up
        self._data_used = None                                # the tag whose data files are read (prune keeps it)
        self._misses = {}                                     # tag -> checksum mismatches

    # ---- state on disk ----------------------------------------------------------------------------------------
    def _load(self):
        try:
            d = jsonfile.read(os.path.join(self.dir, "state.json"))
        except (OSError, ValueError):
            d = {}
        d = d if isinstance(d, dict) else {}
        cur = d.get("current")
        data = d.get("data")
        dtag, dt = (data.get("tag"), data.get("t")) if isinstance(data, dict) else (None, None)
        ok = isinstance(dtag, str) and TAG.match(dtag) and isinstance(dt, (int, float))
        return {"current": cur if isinstance(cur, str) and TAG.match(cur) else None,
                "checked": d.get("checked") if isinstance(d.get("checked"), (int, float)) else 0,
                "failed": {k: str(v) for k, v in (d.get("failed") or {}).items() if isinstance(k, str)}
                if isinstance(d.get("failed"), dict) else {},
                "data": {"tag": dtag, "t": dt} if ok else None}

    def _save(self):
        os.makedirs(self.dir, exist_ok=True)
        jsonfile.write_atomic(os.path.join(self.dir, "state.json"), self.state, indent=1)

    def _tagdir(self, tag):
        if not TAG.match(tag or ""):
            raise SimError("not a release tag: %r" % (tag,))
        return os.path.join(self.dir, tag)

    def exe(self, tag=None):
        return os.path.join(self._tagdir(tag or self.state["current"]), EXE)

    def current(self):
        """The release in use, if its runner is on disk."""
        cur = self.state["current"]
        return cur if cur and os.path.exists(self.exe(cur)) else None

    def data_tag(self):
        """The release whose repository files to read for data (db.json): the one in use, else the latest release's
        tag, looked up at most once a day (after a look-up that failed, not before RETRY_S) and without downloading
        its runner. -> the tag, or None (never looked up, offline)."""
        cur = self.current()
        if cur:
            return cur
        with self.lock:
            known, now = self.state.get("data"), self._now()
            if known and now - known["t"] < CHECK_S:
                return known["tag"]
            if self._data_tried is not None and now - self._data_tried < RETRY_S:
                return known["tag"] if known else None
            self._data_tried = now
            try:
                tag = self.latest()["tag"]
            except Exception as e:  # noqa: BLE001 - offline, blocked: the tag looked up before, after RETRY_S again
                log_once("wowsims.data_tag", e)
                return known["tag"] if known else None
            self.state["data"] = {"tag": tag, "t": now}
            self._save()
            return tag

    # ---- keeping it current -----------------------------------------------------------------------------------
    def latest(self):
        """{"tag", "assets": {name: url}} of the project's latest release."""
        d = json.loads(self._get(LATEST, MAX_JSON).decode("utf-8"))
        tag = d.get("tag_name") if isinstance(d, dict) else None
        if not isinstance(tag, str) or not TAG.match(tag):
            raise ValueError("the latest WoWSims release has no usable tag")
        return {"tag": tag, "assets": {a.get("name"): a.get("browser_download_url") for a in d.get("assets") or ()
                                       if isinstance(a, dict)}}

    def ensure(self, force=False):
        """The release to sim with, checked at most once a day (force: now): a newer one is installed and tried
        first. -> its tag, or None when there is none that runs yet."""
        with self.lock:
            cur, now = self.current(), self._now()
            if not force and ((cur and now - self.state["checked"] < CHECK_S) or
                              (self._tried is not None and now - self._tried < RETRY_S)):
                return cur
            self._tried = now
            tag = None
            try:
                rel = self.latest()
                tag = rel["tag"]
                if tag != cur and tag not in self.state["failed"]:
                    self._install(tag, rel["assets"])
                    if self._smoke:
                        self._smoke(self, tag)
                    self.state["current"] = tag
                    self._prune()
                    log("wowsims", "simulating with WoWSims %s" % tag)
            except FetchError as e:                           # (a file the smoke test needs: may pass)
                log_once("wowsims.check", e)
                return cur
            except SimError as e:                             # it can't run Glide's requests: the last one stays
                if tag:
                    self._set_aside(tag, e)
            except Exception as e:  # noqa: BLE001 - offline, cut short, timed out, blocked: after RETRY_S
                log_once("wowsims.check", e)
                return cur
            self.state["checked"] = now
            self._save()
            return self.current()

    def _set_aside(self, tag, why):
        self.state["failed"][tag] = str(why)[:300]
        if tag != self.state["current"]:
            shutil.rmtree(self._tagdir(tag), ignore_errors=True)
        log("wowsims", "WoWSims %s isn't used: %s" % (tag, why))

    def _install(self, tag, assets):
        """The release's runner for this PC, checked against its checksums.txt, into data/wowsims/<tag>/."""
        name = asset_name()
        url, sums_url = assets.get(name), assets.get("checksums.txt")
        if not url or not sums_url:
            raise SimError("WoWSims %s has no runner for this PC (%s) or no checksums" % (tag, name))
        sums = self._get(sums_url, MAX_JSON).decode("utf-8", "replace")
        want = next((ln.split()[0].lower() for ln in sums.splitlines()
                     if len(ln.split()) == 2 and ln.split()[1].lstrip("*") == name), None)
        if not want:
            raise SimError("WoWSims %s's checksums don't list %s" % (tag, name))
        data = self._get(url, MAX_ZIP)
        if hashlib.sha256(data).hexdigest() != want:
            self._misses[tag] = self._misses.get(tag, 0) + 1
            if self._misses[tag] >= MISSES:
                raise SimError("WoWSims %s's %s doesn't match its checksum" % (tag, name))
            raise ValueError("WoWSims %s's %s didn't match its checksum (tried again later)" % (tag, name))
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            member = next((m for m in z.namelist() if os.path.basename(m) == EXE), None)
            if member is None:
                raise SimError("WoWSims %s's %s holds no %s" % (tag, name, EXE))
            body = z.read(member)
        d = self._tagdir(tag)
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, EXE + ".part")
        with open(tmp, "wb") as fh:
            fh.write(body)
        if sys.platform != "win32":
            os.chmod(tmp, 0o755)
        os.replace(tmp, os.path.join(d, EXE))

    def _prune(self):
        self.prune()

    def prune(self, data_tag=None):
        """Keep the release in use, the one whose data files are read (data_tag, remembered for the next call) and
        the newest other one; the other release folders go."""
        with self.lock:
            if data_tag:
                self._data_used = data_tag
            try:
                tags = [t for t in os.listdir(self.dir) if TAG.match(t) and os.path.isdir(os.path.join(self.dir, t))]
            except OSError:
                return
            keep = {self.state["current"], self._data_used} - {None}
            older = sorted((t for t in tags if t not in keep),
                           key=lambda t: os.path.getmtime(os.path.join(self.dir, t)), reverse=True)
            keep.update(older[:KEEP - 1])
            for t in tags:
                if t not in keep:
                    shutil.rmtree(os.path.join(self.dir, t), ignore_errors=True)

    # ---- files from the repository ---------------------------------------------------------------------------
    def file(self, path, tag=None):
        """A file of WoWSims' repository at the release (a spec's rotation): fetched once and kept beside its runner;
        one the release no longer has (or that can't be fetched) is the copy an older kept release has."""
        tag = tag or self.state["current"]
        if not tag or ".." in path or path.startswith(("/", "\\")):
            raise SimError("no WoWSims file %r" % path)
        here = os.path.join(self._tagdir(tag), "files", *path.split("/"))
        if not os.path.exists(here):
            try:
                data = self._get(RAW % (REPO, tag, path), MAX_FILE)
                os.makedirs(os.path.dirname(here), exist_ok=True)
                with open(here + ".part", "wb") as fh:
                    fh.write(data)
                os.replace(here + ".part", here)
            except Exception as e:  # noqa: BLE001 - an older release's copy, if one is kept
                for t in sorted(os.listdir(self.dir), reverse=True) if os.path.isdir(self.dir) else ():
                    old = os.path.join(self.dir, t, "files", *path.split("/"))
                    if TAG.match(t) and t != tag and os.path.exists(old):
                        here = old
                        break
                else:
                    raise FetchError("WoWSims %s has no %s (%s)" % (tag, path, e)) from e
        with open(here, "rb") as fh:
            return fh.read()

    # ---- simming ------------------------------------------------------------------------------------------------
    def run(self, request, tag=None, timeout=SIM_TIMEOUT_S, env=None, creationflags=None):
        """The RaidSimResult (protojson) for a RaidSimRequest, run by the release's runner. env: the runner's
        environment (None: runner_env(), the engine's without API keys, on a quarter of the cores); creationflags:
        how Windows starts it (None: no window, below normal priority, as a background run of sim_queue.py; a caller
        outside the queue, such as the smoke test of a new release, never runs at full speed beside it)."""
        tag = tag or self.current()
        if not tag:
            raise SimError("WoWSims isn't installed yet")
        argv = (self._command(tag) if self._command else [self.exe(tag)])
        if env is None:
            env = runner_env()
        if creationflags is None:
            creationflags = NO_WINDOW | BELOW_NORMAL
        with tempfile.TemporaryDirectory(prefix="glide-sim-") as tmp:
            inp, out = os.path.join(tmp, "request.json"), os.path.join(tmp, "result.json")
            with open(inp, "w", encoding="utf-8") as fh:
                json.dump(request, fh)
            r = subprocess.run(argv + ["sim", "--infile", inp, "--outfile", out], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout, env=env,
                               creationflags=creationflags)
            if r.returncode != 0 or not os.path.exists(out):
                raise SimError("WoWSims %s couldn't sim: %s" % (tag, ((r.stderr or r.stdout or "").strip()
                                                                       or "exit %d" % r.returncode)[-300:]))
            with open(out, encoding="utf-8") as fh:
                result = json.load(fh)
        if result.get("error"):
            err = result["error"]
            raise SimError("WoWSims %s couldn't sim: %s" % (tag, str(err.get("message") or err if isinstance(err, dict)
                                                                       else err)[:300]))
        return result

    def sim(self, request, tag=None, timeout=SIM_TIMEOUT_S, env=None, creationflags=None):
        """The player's average DPS for a request."""
        return float(dps(self.run(request, tag, timeout=timeout, env=env, creationflags=creationflags))["avg"])


def dps(result):
    """The first player's DPS metrics ({"avg", "stdev", ...}) in a RaidSimResult."""
    try:
        return result["raidMetrics"]["parties"][0]["players"][0]["dps"]
    except (KeyError, IndexError, TypeError) as e:
        raise SimError("WoWSims' result has no DPS for the player") from e
