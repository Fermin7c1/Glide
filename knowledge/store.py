"""kb.db access: build context for loaders, read-only connections for the engine, provenance helpers.

Contract (see README.md): loaders are `load(conn, ctx) -> dict`; the engine opens the KB read-only
through `open_ro()`; nothing at runtime writes to kb.db.

World coordinates (binding for every table with continent/wx/wy and for map_bounds):
  wx = world X (north is +X, the map's vertical axis),  wy = world Y (west is +Y, the map's horizontal
  axis), in yards, as C_Map.GetWorldPosFromMapPos(...):GetXY() and RestedXP's SpiritHealerWorld/taxiPos
  name them. RestedXP's world-form `.goto 1426/0,A,B` has A = wy and B = wx (HereBeDragons' x/y order).
  map_bounds stores the world position of the map's (0,0) corner (wx0, wy0) and (100,100) corner
  (wx1, wy1); x% grows from wy0 to wy1, y% from wx0 to wx1. Use world_to_map / map_to_world.
"""
import glob
import gzip
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SCHEMA = os.path.join(HERE, "schema.sql")
DEFAULT_KB = os.path.join(ROOT, "data", "kb.db")
# The kb.db layout this code reads and writes. The build stamps it into meta 'schema_version'; KnowledgePack refuses
# a kb.db with another one ("rebuild needed"). Bump it when a table or column the engine reads changes meaning or
# goes away (additions keep it). A kb.db built before the stamp existed counts as schema 1.
SCHEMA_VERSION = 1

# Confidence by source tag (README "Source tags"); a row's conf defaults to its best source.
SOURCE_CONF = {"wdb": 1.0, "db2": 1.0, "obs": 0.95, "scan": 0.9, "frc": 0.8, "rxp": 0.75, "whd": 0.7,
               "whz": 0.7, "fg": 0.6, "classic": 0.6, "fit": 0.6, "frq": 0.5, "inf": 0.5, "note": 0.5}
# Tags that mean "seen on the Forever server by us" (verified=1).
VERIFIED_TAGS = ("wdb", "obs", "scan")
# RestedXP's addon folder inside the WoW client folder (config.json wow_dir), used when reference/rxp is absent.
RXP_ADDON_DIR = ("Interface", "AddOns", "RXPGuides")


def glideconfig():
    """companion/glideconfig.py, config.json's one reader (companion/ goes on sys.path, after everything else)."""
    folder = os.path.join(ROOT, "companion")
    if folder not in sys.path:
        sys.path.append(folder)
    import glideconfig as module
    return module


def rxp_files(folder):
    """The RestedXP files the knowledge layer reads from one RXPGuides folder (installed addon or reference/rxp):
    *.toc, DB/**/*.lua, and the Forever guides Guides/forever/**/*.lua (any case). Sorted absolute paths."""
    if not folder or not os.path.isdir(folder):
        return []
    out = set(glob.glob(os.path.join(folder, "*.toc")))
    out.update(glob.glob(os.path.join(folder, "DB", "**", "*.lua"), recursive=True))
    gdir = os.path.join(folder, "Guides")
    if os.path.isdir(gdir):
        for name in os.listdir(gdir):
            if name.lower() == "forever":
                out.update(glob.glob(os.path.join(gdir, name, "**", "*.lua"), recursive=True))
    return sorted(p for p in out if os.path.isfile(p))


def rxp_version(folder):
    """'## Version' from RXPGuides.toc in `folder`, or None."""
    p = os.path.join(folder or "", "RXPGuides.toc")
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.lower().startswith("## version:"):
                    return line.split(":", 1)[1].strip() or None
    except OSError:
        pass
    return None


def rxp_fingerprint(folder):
    """{"version", "hash", "files"} identifying one RestedXP install: version from the TOC plus a hash over
    (relative path, size, mtime) of rxp_files(folder). None when the folder doesn't exist."""
    if not folder or not os.path.isdir(folder):
        return None
    import hashlib
    h = hashlib.sha1()
    files = rxp_files(folder)
    for p in files:
        st = os.stat(p)
        h.update(("%s|%d|%d\n" % (os.path.relpath(p, folder).replace(os.sep, "/"), st.st_size,
                                    int(st.st_mtime))).encode("utf-8"))
    return {"version": rxp_version(folder), "hash": h.hexdigest(), "files": len(files)}


class BuildContext:
    """Paths every loader may need. All optional inputs are checked with os.path.exists by loaders.

    RestedXP's addon files (DB/forever/*.lua, GuideLoader.lua, ...) are read through rxp(*parts): the repo copy
    reference/rxp/ (cloud checkout) first, else the installed addon <wow_dir>/Interface/AddOns/RXPGuides/ (the
    player's PC has no reference/ folder). Never join reference paths by hand."""

    def __init__(self, root=ROOT, glide_db=None, reference=None, data=None, live=None, log=print):
        self.root = root
        self.glide_db = glide_db or os.path.join(root, "data", "glide.db")
        self.reference = reference or os.path.join(root, "reference")
        self.data = data or os.path.join(root, "data")
        self.live = live or os.path.join(root, "data", "live")
        self.seed = os.path.join(self.data, "seed", "seed.json.gz")
        self.archive_sv = os.path.join(self.data, "archive", "sv")
        self.archive_wdb = os.path.join(self.data, "archive", "wdb")
        self.db2_dir = os.path.join(self.data, "db2")          # CSV exports from wow.tools.local (optional)
        self.log = log
        self.notes = []                                          # loaders append human-readable notes
        self.cache = {}                                          # loaders share parsed inputs here

    def rxp_roots(self):
        """RestedXP folders in resolution order: [(kind, folder)] with kind 'reference' (reference/rxp, the repo
        copy), then 'addon' (<wow_dir>/Interface/AddOns/RXPGuides) when config.json names an existing client."""
        roots = [("reference", os.path.join(self.reference, "rxp"))]
        wow = self.wow_dir()
        if wow:
            roots.append(("addon", os.path.join(wow, *RXP_ADDON_DIR)))
        return roots

    def rxp(self, *parts):
        """A RestedXP file or folder: reference/rxp/<parts> when it exists, else <wow_dir>/Interface/AddOns/
        RXPGuides/<parts> when that exists, else the (missing) reference path, so callers' existence checks fail."""
        roots = self.rxp_roots()
        for _, folder in roots:
            p = os.path.join(folder, *parts)
            if os.path.exists(p):
                return p
        return os.path.join(roots[0][1], *parts)

    def rxp_available(self, *parts):
        """True when <parts> (no parts: any RestedXP folder) exists in reference/rxp or in the installed addon."""
        return any(os.path.exists(os.path.join(folder, *parts)) for _, folder in self.rxp_roots())

    def rxp_source(self):
        """(kind, folder) of the first RestedXP folder that exists ('reference' or 'addon'), else (None, None)."""
        for kind, folder in self.rxp_roots():
            if os.path.isdir(folder):
                return kind, folder
        return None, None

    def glide(self):
        """Read-only connection to the merged glide.db (source of most entities)."""
        c = sqlite3.connect("file:%s?mode=ro" % self.glide_db.replace("\\", "/"), uri=True)
        c.row_factory = sqlite3.Row
        return c

    def has_glide(self):
        return os.path.exists(self.glide_db)

    def load_seed(self):
        """data/seed/seed.json.gz parsed once per build (None when missing)."""
        if "seed" not in self.cache:
            seed = None
            if os.path.exists(self.seed):
                with gzip.open(self.seed, "rt", encoding="utf-8") as fh:
                    seed = json.load(fh)
            self.cache["seed"] = seed
        return self.cache["seed"]

    def wow_dir(self):
        """The WoW client folder from config.json (player's PC), or None."""
        d = glideconfig().wow_dir(self.root)
        return d if d and os.path.isdir(d) else None

    def recorder_sv_files(self):
        """Recorder SavedVariables (GlideRecorderDB): archived copies plus the live client's, if present."""
        files = sorted(glob.glob(os.path.join(self.archive_sv, "Glide*.lua")))
        wow = self.wow_dir()
        if wow:
            for acct in glob.glob(os.path.join(wow, "WTF", "Account", "*", "SavedVariables")):
                files += glob.glob(os.path.join(acct, "Glide.lua")) + glob.glob(os.path.join(acct, "GlideRecorder.lua"))
        return files


# ---------------------------------------------------------------------------------------------------
# builders' shared parts: code fingerprints, installing a finished database (build_kb and tools/build_db.py)
# ---------------------------------------------------------------------------------------------------
def content_hash(root, rels):
    """One fingerprint for these files (relative paths, '/' separators): their names and bytes. For code that ships
    with the app: an update copies it again with new mtimes even when it is unchanged, so mtimes would not do."""
    import hashlib
    h = hashlib.sha1()
    for rel in rels:
        h.update(rel.encode("utf-8") + b"\0")
        try:
            with open(os.path.join(root, *rel.split("/")), "rb") as fh:
                h.update(hashlib.sha1(fh.read()).digest())
        except OSError:
            h.update(b"?")
    return h.hexdigest()


def pending_path(out):
    """Where a build goes when `out` stays in use: kb.db -> kb.new.db, glide.db -> glide.new.db. The next
    `build_kb.py --if-stale` (kb) or the companion's next start (companion/kb_refresh.promote, both) installs it."""
    return os.path.splitext(out)[0] + ".new.db"


def rel_path(path):
    """`path` relative to the project root when it is inside it (for messages)."""
    if not path:
        return path
    p = os.path.abspath(path)
    return os.path.relpath(p, ROOT) if p.startswith(ROOT + os.sep) else p


def install(tmp, target, warn, what="database"):
    """Copy the finished build `tmp` next to `target` and rename it over `target` (removing `tmp`), so a reader never
    sees a half-written file and an interrupted copy leaves `target` as it was. Returns the path written: `target`,
    or pending_path(target) when `target` stays in use (Windows: a process holds it open, os.replace raises
    PermissionError), or the staged copy when even that fails. Both fallbacks are reported through `warn`."""
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    staged = target + ".tmp-%d" % os.getpid()
    try:
        shutil.copyfile(tmp, staged)
    except BaseException:
        _remove_quiet(staged)            # a partial copy is never left behind; the target is untouched
        raise
    os.remove(tmp)
    for _attempt in range(10):
        try:
            os.replace(staged, target)
            return target
        except PermissionError:          # Windows: the old file is open without delete sharing
            time.sleep(0.5)
    alt = pending_path(target)
    try:
        os.replace(staged, alt)
    except OSError as e:
        warn("could not replace %s nor write %s (%s): the new %s is at %s" % (
            rel_path(target), rel_path(alt), e, what, rel_path(staged)))
        return staged
    warn("%s is in use (the companion has it open), so the new %s was saved as %s." % (
        rel_path(target), what, rel_path(alt)))
    return alt


def _remove_quiet(path):
    try:
        os.remove(path)
    except OSError:
        pass


def create_schema(conn):
    with open(SCHEMA, encoding="utf-8") as fh:
        script = fh.read()
    try:
        conn.executescript(script)
        return
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e):
            raise
    # schema applied to an existing database: run statement by statement, skipping columns that exist
    buf = ""
    for line in script.splitlines(True):
        buf += line
        if sqlite3.complete_statement(buf):
            try:
                conn.execute(buf)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e):
                    raise
            buf = ""


def open_rw(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=MEMORY")
    conn.execute("PRAGMA synchronous=OFF")
    return conn


_SQL_ERRORS = set()


def sql_error_once(sql, e):
    """Print a failed KB query once per distinct error: the coach still gets [] (it never sees the exception), but
    a schema slip (a renamed column, a missing table) shows up in the log instead of silently returning nothing."""
    key = "%s: %s" % (type(e).__name__, e)
    if key in _SQL_ERRORS or len(_SQL_ERRORS) > 200:
        return
    _SQL_ERRORS.add(key)
    print("[kb] query failed (%s): %s" % (key, " ".join(sql.split())[:200]), flush=True)


class KB:
    """Read-only KB for the engine (one connection per thread)."""

    def __init__(self, path=DEFAULT_KB):
        self.path = path
        self.ok = os.path.exists(path)
        self._local = threading.local()

    def conn(self):
        if not self.ok:
            return None
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect("file:%s?mode=ro" % self.path.replace("\\", "/"), uri=True, check_same_thread=False)
            c.row_factory = sqlite3.Row
            self._local.c = c
        return c

    def q(self, sql, *args):
        c = self.conn()
        if not c:
            return []
        try:
            return [dict(r) for r in c.execute(sql, args).fetchall()]
        except sqlite3.Error as e:
            sql_error_once(sql, e)
            return []

    def one(self, sql, *args):
        r = self.q(sql, *args)
        return r[0] if r else None

    def meta(self, key, default=None):
        r = self.one("SELECT value FROM meta WHERE key=?", key)
        return r["value"] if r else default

    # ---- coordinates (see module docstring for the world-axis convention) ----
    def bounds(self, ui_map):
        """map_bounds row for a UiMap (dict) or None when no conversion is known."""
        return self.one("SELECT * FROM map_bounds WHERE ui_map=?", ui_map)

    def to_map(self, ui_map, wx, wy):
        """World (wx, wy) -> (x, y) percent on ui_map, or (None, None) when the map has no bounds."""
        b = self.bounds(ui_map)
        return world_to_map(b, wx, wy) if b else (None, None)

    def to_world(self, ui_map, x, y):
        """(x, y) percent on ui_map -> (continent, wx, wy), or (None, None, None)."""
        b = self.bounds(ui_map)
        if not b:
            return None, None, None
        wx, wy = map_to_world(b, x, y)
        return b.get("continent"), wx, wy


def open_ro(path=DEFAULT_KB):
    return KB(path)


def schema_version(kb):
    """The schema version a kb.db was built with (meta 'schema_version'; 1 when it has none)."""
    try:
        v = int(kb.meta("schema_version") or 1)
    except (TypeError, ValueError):
        v = 0                             # unreadable stamp: never matches
    return v


# ---------------------------------------------------------------------------------------------------
# coordinates
# ---------------------------------------------------------------------------------------------------
def world_to_map(b, wx, wy):
    """b: map_bounds row (mapping with wx0, wy0, wx1, wy1). Returns (x, y) in percent, unclamped."""
    if b is None or wx is None or wy is None:
        return None, None
    dx, dy = b["wy1"] - b["wy0"], b["wx1"] - b["wx0"]
    if not dx or not dy:
        return None, None
    return (wy - b["wy0"]) / dx * 100.0, (wx - b["wx0"]) / dy * 100.0


def map_to_world(b, x, y):
    """Inverse of world_to_map: (x, y) percent -> (wx, wy) yards."""
    if b is None or x is None or y is None:
        return None, None
    return b["wx0"] + (b["wx1"] - b["wx0"]) * y / 100.0, b["wy0"] + (b["wy1"] - b["wy0"]) * x / 100.0


def map_size_yd(b):
    """(width_yd, height_yd) of a map from its bounds: x spans wy, y spans wx."""
    if b is None:
        return None, None
    return abs(b["wy1"] - b["wy0"]), abs(b["wx1"] - b["wx0"])


# ---------------------------------------------------------------------------------------------------
# provenance helpers
# ---------------------------------------------------------------------------------------------------
def merge_src(a, b):
    """'fg,frq' + 'obs' -> 'fg,frq,obs' (ordered, unique)."""
    out = []
    for part in (a or "").split(",") + (b or "").split(","):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return ",".join(out)


def src_conf(src, default=0.3):
    """Confidence of a comma list of source tags: the best tag wins."""
    best = None
    for part in (src or "").split(","):
        c = SOURCE_CONF.get(part.strip())
        if c is not None and (best is None or c > best):
            best = c
    return default if best is None else best


def is_verified(src):
    return 1 if any(t.strip() in VERIFIED_TAGS for t in (src or "").split(",")) else 0


def clean_name(s):
    """Names from client caches can carry trailing NUL-separated garbage; keep the first field."""
    if s is None:
        return None
    s = str(s).split("\x00", 1)[0]
    s = "".join(ch for ch in s if ch >= " " or ch == "\t").strip()
    return s or None


# WoW's text codes in quest text: $B line break, $N name, $C class, $R race, $G<male>:<female>; and the like
_GENDER = re.compile(r"\$[Gg]\s*([^:;$]*):([^;$]*);")
_NAMED = {"n": "adventurer", "c": "adventurer", "r": "traveler"}
_CODE = re.compile(r"\$([A-Za-z])(?:[^\s;$]*;)?")


def wow_text(text):
    """Quest text with WoW's $ codes resolved to plain words (the model would otherwise read or repeat them)."""
    if not text or "$" not in text:
        return text
    t = _GENDER.sub(lambda m: m.group(1).strip(), text)
    t = re.sub(r"\$[Bb]", " ", t)
    t = _CODE.sub(lambda m: _NAMED.get(m.group(1).lower(), ""), t)
    return " ".join(t.split())


def table_cols(conn, table):
    return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)]


def insert_rows(conn, table, rows, cols=None, verb="INSERT"):
    """Bulk insert dict rows (or tuples matching `cols`). Unknown dict keys are dropped. Returns count."""
    rows = list(rows)
    if not rows:
        return 0
    if isinstance(rows[0], dict):
        have = set(table_cols(conn, table))
        if not cols:
            cols, seen = [], set()
            for r in rows:
                for c in r:
                    if c in have and c not in seen:
                        seen.add(c)
                        cols.append(c)
        data = [[r.get(c) for c in cols] for r in rows]
    else:
        data = rows
    conn.executemany("%s INTO %s(%s) VALUES(%s)" % (verb, table, ",".join(cols), ",".join("?" * len(cols))), data)
    return len(data)


def set_meta(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)",
                 (key, value if isinstance(value, str) else jdump(value)))


def jdump(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str) if obj is not None else None


def jload(text, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def upsert(conn, table, row, keys):
    """Insert or update by key columns; src is merged, higher conf wins for other columns when both set."""
    where = " AND ".join("%s=?" % k for k in keys)
    cursor = conn.execute("SELECT * FROM %s WHERE %s" % (table, where), [row[k] for k in keys])
    cur = cursor.fetchone()
    if cur is None:
        cols = list(row)
        conn.execute("INSERT INTO %s(%s) VALUES(%s)" % (table, ",".join(cols), ",".join("?" * len(cols))),
                     [row[c] for c in cols])
        return "insert"
    cur = dict(zip([d[0] for d in cursor.description], cur))     # works with or without sqlite3.Row
    new_conf = row.get("conf", 0) or 0
    old_conf = cur.get("conf", 0) or 0
    upd = {}
    for k, v in row.items():
        if k in keys or v is None:
            continue
        if k == "src":
            upd[k] = merge_src(cur.get("src"), v)
        elif k == "conf":
            upd[k] = max(new_conf, old_conf)
        elif cur.get(k) is None or new_conf >= old_conf:
            upd[k] = v
    if upd:
        conn.execute("UPDATE %s SET %s WHERE %s" % (table, ",".join("%s=?" % k for k in upd), where),
                     list(upd.values()) + [row[k] for k in keys])
    return "update"
