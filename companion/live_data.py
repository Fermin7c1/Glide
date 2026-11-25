"""The facts Glide found (where items come from; later phases add more) handed to the addon in
<wow>/Interface/AddOns/Glide/Data/Live.lua. This is the one writer of that file: each section has a provider
(register: "items" is research.Research.items_section, "chars" per-character data), and the file is written again
whenever it differs (tick, every POLL_S; an addon update puts back the empty one the addon ships), through a .part
file and os.replace. The game reads it at login and /reload, and only an addon from FORMAT on lists it in its TOC
(WoW reads a TOC when it starts, so the first file needs one game restart).

Every write raises gen, which the file carries; the addon reports the gen it loaded (core key "lg"). That's the
/reload self-test: a reload the engine saw (GameState.addon_restarts; dated by the last frame that still showed the
addon's old ring of events, since the restart itself is only noticed at the new load's first event) that came
RELOAD_AFTER_S or more after a write and still reports an older gen didn't read the file again. STALE_RELOADS of those
in a row, once the addon has loaded a written file at all, set reload_ok False, and news() (the nudge rules says at a
quiet moment) asks for a WoW restart instead of a /reload; a later reload that has the newest gen sets it back. The
nudge is said once per wording ("told_kind": the restart one, then the /reload one), not per batch of facts.

The file stays under `cap` bytes and MAX_CONSTANTS distinct Lua constants (one Lua 5.1 chunk holds 262143): items
with the lowest priority number are kept first. Nothing is written while every section is empty (the engine just
started, nothing looked up yet): the file keeps what it has.

data/research/live.json: {"v", "gen", "t", "sha", "writes", "ids", "told", "told_kind", "loaded", "reload_ok",
"stale"}: the last write (gen, time, the text's sha1), the recent writes ([[gen, t]]), the item ids in the file and in
it at the last news, the wording the nudge last said ("restart" or "reload"), whether the addon ever loaded a written
file (a gen above 0), and the self-test's verdict and stale count.
"""
import hashlib
import math
import os
import re
import threading
import time

import jsonfile
from log import log, log_once
from sim_weights import lua_string
from state import as_int

HANDOFF = os.path.join("Interface", "AddOns", "Glide", "Data", "Live.lua")
FORMAT = "0.20.0"                  # the oldest addon version that has the file (its TOC line)
V = 1                              # the file's layout (ns.live.v)
POLL_S = 15
RELOAD_AFTER_S = 30                # a reload judges the gens written at least this long before it
STALE_RELOADS = 2                  # reloads in a row that kept an older gen: /reload doesn't read the file again
LG_WAIT_S = 20                     # no "lg" this long after a reload: the addon loaded gen 0 (it sends none then)
KEEP_WRITES = 32                   # recent writes kept for the self-test
MAX_CONSTANTS = 100000
CORE = ("items", "chars")          # the sections every file has (the empty one the addon ships has just these)
HEAD = ("-- Written by the Glide app (companion/live_data.py): facts Glide found about items, quests and more, read at\n"
        "-- login and /reload. The app rewrites this file.\n"
        "local _, ns = ...\n")
NUM_ROOM = 40                      # bytes kept for gen and t, so the items kept don't depend on them
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LUA_WORDS = frozenset("and break do else elseif end false for function if in local nil not or repeat return then "
                       "true until while".split())


def _version(v):
    """A version as a tuple ("0.20.0" -> (0, 20, 0)), or None."""
    if isinstance(v, tuple):
        return tuple(int(x) for x in v)
    m = re.match(r"\s*(\d+(?:\.\d+)*)", str(v or ""))
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


_FORMAT = tuple(int(x) for x in FORMAT.split("."))


def lua_value(v, consts=None):
    """A Lua expression for a JSON-like value (dict keys sorted, so the text is stable). consts, when given, collects
    the distinct constants it uses."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            v = 0
        if consts is not None:
            consts.add(("n", v))
        return str(v) if isinstance(v, int) else repr(v)
    if isinstance(v, str):
        if consts is not None:
            consts.add(("s", v))
        return lua_string(v)
    if isinstance(v, (list, tuple)):
        return "{ %s }" % ", ".join("nil" if x is None else lua_value(x, consts) for x in v) if v else "{}"
    if isinstance(v, dict):
        parts = ["%s = %s" % (lua_key(k, consts), lua_value(x, consts))
                 for k, x in sorted(v.items(), key=lambda kv: _key_order(kv[0])) if x is not None]
        return "{ %s }" % ", ".join(parts) if parts else "{}"
    if v is None:
        return "nil"
    raise TypeError("no Lua for %r" % type(v).__name__)


def lua_key(k, consts=None):
    """A table key: a bare name when it is one, [n] for a number, ["..."] otherwise."""
    if isinstance(k, str) and _IDENT.match(k) and k not in _LUA_WORDS:
        if consts is not None:
            consts.add(("s", k))
        return k
    return "[%s]" % lua_value(k, consts)


def _key_order(k):
    return (0, k, "") if isinstance(k, (int, float)) and not isinstance(k, bool) else (1, 0, str(k))


def _block(rows):
    return "{\n" + "".join(rows) + "}" if rows else "{}"


def _item_row(item_id, e, consts=None):
    """One items row: [id] = { "text", a = "n", w = 1 }, or None for an entry without text. A "|" in the text is
    doubled: the game's tooltips read "|c", "|n", "|T" as colours, line breaks and textures, "||" as a "|"."""
    text = e.get("t")
    if not text or not isinstance(text, str):
        return None
    text = text.replace("|", "||")
    if consts is not None:
        consts.update((("n", item_id), ("s", text)))
    row = "    [%d] = { %s" % (item_id, lua_string(text))
    if e.get("a") in ("n", "l", "x"):
        row += ", a = %s" % lua_string(e["a"])
        if consts is not None:
            consts.add(("s", e["a"]))
    if e.get("w"):
        row += ", w = 1"
    return row + " },\n"


class LiveData:
    """The writer: register() the sections, then start() (or tick() / write() from a poll); observe() what the addon
    loaded, news() once new facts are written."""

    def __init__(self, home, wow_dir_fn, addon_version_fn, now=time.time, cap=256 * 1024, state_fn=None):
        self.path = os.path.join(home, "data", "research", "live.json")
        self.wow_dir_fn, self.addon_version_fn, self._now, self.cap = wow_dir_fn, addon_version_fn, now, cap
        self.state_fn = state_fn               # the GameState: tick() observes the addon's reloads and "lg" itself
        self.providers = {}
        self._lock = threading.Lock()
        d = self._load()
        self.gen = as_int(d.get("gen")) or 0
        self.t = as_int(d.get("t")) or 0
        self.sha = d.get("sha") if isinstance(d.get("sha"), str) else None
        self.writes = []                       # [[gen, t]], oldest first
        for w in d.get("writes") or []:
            g, wt = (as_int(w[0]), as_int(w[1])) if isinstance(w, list) and len(w) == 2 else (None, None)
            if g is not None and wt is not None:
                self.writes = (self.writes + [[g, wt]])[-KEEP_WRITES:]
        self.ids = [i for i in (d.get("ids") or []) if isinstance(i, int)]
        self.told = [i for i in (d.get("told") or []) if isinstance(i, int)]
        self.told_kind = d.get("told_kind") if d.get("told_kind") in ("restart", "reload") else None
        self.loaded = bool(d.get("loaded"))
        self.reload_ok = d.get("reload_ok") if d.get("reload_ok") in (True, False) else None
        self.stale = as_int(d.get("stale")) or 0
        self.lg = None                         # the gen the running addon reports (None: not seen since its start)
        self._pending: tuple[float, float] | None = None   # a reload to judge: (seen, when observe() heard of it)
        self._restarts = None                  # GameState.addon_restarts at the last tick

    def _load(self):
        try:
            d = jsonfile.read(self.path)
        except (OSError, ValueError):
            return {}
        return d if isinstance(d, dict) else {}

    def _save(self):
        jsonfile.write_atomic(self.path, {"v": 1, "gen": self.gen, "t": self.t, "sha": self.sha,
                                          "writes": self.writes, "ids": self.ids, "told": self.told,
                                          "told_kind": self.told_kind, "loaded": self.loaded,
                                          "reload_ok": self.reload_ok, "stale": self.stale},
                              separators=(",", ":"))

    def register(self, section, provider):
        """provider() -> the section's data: "items" {item_id: {"t": text, "a": "n"|"l"|"x", "w": 0|1, "p":
        priority, lower kept first}}, any other {key: JSON-like value} (written as ns.live.<section>[key])."""
        if not _IDENT.match(section) or section in _LUA_WORDS or section in ("v", "gen", "t"):
            raise ValueError("not a section name: %r" % section)
        self.providers[section] = provider

    # ---- the file ---------------------------------------------------------------------------------------------
    def _sections(self):
        """Each section's data, from its provider (core sections without one: empty)."""
        out = {name: {} for name in CORE}
        for name, fn in list(self.providers.items()):
            data = fn()
            out[name] = data if isinstance(data, dict) else {}
        return out

    def _rows(self, data):
        """The rows of each section ({name: [row]}) and the item ids kept: items cut to the size and constants caps,
        the lowest priority number first."""
        consts = {("s", s) for s in ("live", "v", "gen", "t", "a", "w")} | {("n", 1)} | {("s", n) for n in data}
        rows = {}
        for name, d in data.items():
            if name != "items":
                rows[name] = ["    [%s] = %s,\n" % (lua_value(k, consts), lua_value(v, consts))
                              for k, v in sorted(d.items(), key=lambda kv: _key_order(kv[0])) if v is not None]
        size = len(self._render(0, 0, dict(rows, items=[])).encode("utf-8")) + NUM_ROOM + 1
        items = {}
        for k, e in (data.get("items") or {}).items():
            if as_int(k) is not None and not isinstance(k, bool) and isinstance(e, dict):
                items[as_int(k)] = e
        keep = []
        for item_id in sorted(items, key=lambda i: (as_int(items[i].get("p")) or 0, i)):
            mine = set()
            row = _item_row(item_id, items[item_id], mine)
            if row is None:
                continue
            n = len(row.encode("utf-8"))
            if size + n > self.cap or len(consts) + len(mine - consts) > MAX_CONSTANTS:
                break
            size += n
            consts |= mine
            keep.append((item_id, row))
        keep.sort()
        rows["items"] = [r for _, r in keep]
        return rows, [i for i, _ in keep]

    @staticmethod
    def _render(gen, t, rows):
        extra = "".join(", %s = %s" % (name, _block(r)) for name, r in rows.items() if name not in CORE and r)
        return HEAD + "ns.live = { v = %d, gen = %d, t = %d, items = %s, chars = %s%s }\n" % (
            V, gen, t, _block(rows.get("items") or []), _block(rows.get("chars") or []), extra)

    def text(self):
        """The file as it would be now: the providers' data at the current gen and time (with no data and gen 0,
        exactly the empty file the addon ships)."""
        return self._render(self.gen, self.t, self._rows(self._sections())[0])

    def _format_ok(self):
        inst = _version(self.addon_version_fn())
        return inst is not None and inst >= _FORMAT

    def write(self):
        """Live.lua written again when the installed addon (FORMAT or newer) has a Glide folder and the file differs
        from what the providers give now. -> True when it wrote."""
        wow = self.wow_dir_fn()
        if not wow or not os.path.isdir(os.path.join(wow, "Interface", "AddOns", "Glide")) or not self._format_ok():
            return False
        rows, ids = self._rows(self._sections())
        path = os.path.join(wow, HANDOFF)
        try:
            with open(path, encoding="utf-8", newline="") as fh:
                disk = fh.read()
        except (OSError, ValueError):
            disk = None
        with self._lock:
            if disk is not None:
                m = re.search(r"\bgen = (\d+)", disk)
                if m:
                    self.gen = max(self.gen, int(m.group(1)))     # (live.json lost: gen never goes back)
            if disk == self._render(self.gen, self.t, rows):
                return False
            if not any(rows.values()):
                return False                                   # nothing yet: the file keeps what it has
            if disk is None or hashlib.sha1(disk.encode("utf-8")).hexdigest() != self.sha:
                self.writes = []                               # replaced (an addon install): older gens are gone
            self.gen, self.t = self.gen + 1, int(self._now())
            text = self._render(self.gen, self.t, rows)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".part", "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            os.replace(path + ".part", path)
            self.sha = hashlib.sha1(text.encode("utf-8")).hexdigest()
            self.writes = (self.writes + [[self.gen, self.t]])[-KEEP_WRITES:]
            self.ids = ids
            self._save()
        log("live_data", "wrote Data/Live.lua: gen %d, %d item%s" % (self.gen, len(self.ids),
                                                                       "" if len(self.ids) == 1 else "s"))
        return True

    # ---- what the addon loaded --------------------------------------------------------------------------------
    def observe(self, lg, reloaded, t=None):
        """What the running addon loaded: lg, the gen it reports (core key "lg"; None when the frame has none), and
        reloaded, True when the engine saw it start again (a reload, relog or game restart) since the last call; t,
        the latest it can have started (GameState.addon_restart_t; default now)."""
        now = self._now()
        with self._lock:
            changed = False
            if reloaded:
                self._pending = (t if t is not None else now, now)
                self.lg = None
            if lg is not None:
                self.lg = lg
                if lg > 0 and not self.loaded:
                    self.loaded = changed = True
            if self._pending is not None:
                changed = self._judge(lg, now) or changed
            if changed:
                self._save()

    def _judge(self, lg, now):
        """The pending reload against the newest gen written RELOAD_AFTER_S before it. -> True when that changed
        the self-test."""
        if self._pending is None:
            return False
        seen, heard = self._pending
        expected = max((g for g, wt in self.writes if wt <= seen - RELOAD_AFTER_S), default=None)
        if expected is None:
            self._pending = None
            return False
        if lg is None:
            if now - heard < LG_WAIT_S:
                return False                       # (the addon leaves "lg" out of a full core; the next frame has it)
            lg = 0
        self._pending = None
        if lg >= expected:
            changed = self.stale != 0 or self.reload_ok is not True
            if self.reload_ok is False:
                log("live_data", "a reload read the newest Data/Live.lua: the nudge says /reload again")
            self.stale, self.reload_ok = 0, True
            return changed
        if not self.loaded:
            return False                           # the TOC line isn't read yet: that wants a restart anyway
        self.stale += 1
        if self.stale >= STALE_RELOADS and self.reload_ok is not False:
            self.reload_ok = False
            log("live_data", "%d reloads in a row kept an older Data/Live.lua: the nudge asks for a WoW restart"
                % self.stale)
        return True

    def news(self):
        """{"n": items written since the last news, "restart": True when the game needs a restart to see them (the
        addon never loaded a written file, so its TOC line is new, or a /reload doesn't read the file again)}, or
        None. Each batch is handed out once, and each wording only once (told_kind, kept across engine restarts): the
        restart nudge, then the /reload one; again only when the wording changes (call it when it can be said)."""
        with self._lock:
            new = set(self.ids) - set(self.told)
            if not new:
                return None
            self.told = list(self.ids)
            kind = "restart" if not self.loaded or self.reload_ok is False else "reload"
            if (self.lg is not None and self.lg >= self.gen) or kind == self.told_kind:
                self._save()
                return None                        # the addon has them already, or the player was told so before
            self.told_kind = kind
            self._save()
            return {"n": len(new), "restart": kind == "restart"}

    # ---- the worker -------------------------------------------------------------------------------------------
    def _observe_state(self):
        st = self.state_fn() if self.state_fn is not None else None
        if st is None:
            return
        n, t = st.addon_restarts, getattr(st, "addon_restart_t", None)
        lg = as_int((st.core or {}).get("lg"))
        reloaded = self._restarts is not None and n > self._restarts
        self._restarts = n
        self.observe(lg, reloaded, t)

    def tick(self):
        """One poll: what the addon loaded (with state_fn), then the file written when it differs."""
        if self.state_fn is not None:
            self._observe_state()
        try:
            self.write()
        except OSError as e:
            log_once("live_data.write", e)

    def start(self):
        def loop():
            while True:
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001 - the next poll tries again
                    log_once("live_data.tick", e)
                time.sleep(POLL_S)
        threading.Thread(target=loop, daemon=True, name="live-data").start()
        return self
