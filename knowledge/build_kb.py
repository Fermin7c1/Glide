#!/usr/bin/env python3
"""Build data/kb.db from scratch: glide.db + RestedXP files + observations + other lanes' modules -> kb.db.

    python3 knowledge/build_kb.py [--out PATH] [--only a,b] [--skip a,b] [--force] [-q] [--json]
    python3 knowledge/build_kb.py --if-stale   build only when kb.db is missing or out of date (start-glide.cmd)
    python3 knowledge/build_kb.py --check      print whether a rebuild is needed and why; build nothing
                                               (exit status 0 = up to date, 1 = rebuild needed)

The database is built in the local temp dir, copied next to the target and atomically renamed over it
(the engine may hold the old file open read-only). On Windows the rename fails while the engine has
kb.db open: the new KB is then saved as kb.new.db and the next `--if-stale` run (start-glide.cmd runs one
before starting the engine) moves it over kb.db. Each loader runs inside a savepoint: a loader that
raises is rolled back and recorded in meta (`loader:<name>` = {"error": ...}). If one of the store
lane's own loaders failed, the old kb.db is kept (the new file is left as kb.failed.db) unless --force.
A per-loader timing table is printed at the end (also in meta `loader:<name>` `_s`). Every loader is the repo's own
code (REQUIRED_LOADERS): one that fails, or cannot even be imported, fails the build the same way (kb.failed.db,
the old kb.db kept). A build without RestedXP guides (rxp_guide) or step briefs (step_brief) is never installed
either: the coach would have nothing to brief. A loader whose data source is missing still just reports "skipped".

Staleness (--if-stale, --check): kb.db is rebuilt when it is missing, unreadable, a partial (--only/--skip)
build, or older than any input in STALE_INPUTS (data/glide.db, data/params.json, knowledge/heuristics/rules.json,
knowledge/glossary.json, knowledge/schema.sql, the newest knowledge/**/*.py and wowfiles/**/*.py, the newest
data/live/*.jsonl, the newest data/archive/sv/*.lua); also when one of them differs from what the build
recorded in meta `inputs` (path + mtime per input, snapshot taken when the build started), e.g. a file copied in
with an older timestamp (shipped code and text files count as unchanged while their content hash matches); and when its build recorded a loader error, unless a kb.failed.db next to it already failed from the same inputs.

RestedXP's files come from reference/rxp/ or, when that is absent (the player's PC), from the installed addon
<wow_dir>/Interface/AddOns/RXPGuides/ (store.BuildContext.rxp); the build prints which (meta `rxp_source`).

Order: glide_entities, rxp_parser (parser lane), seed_items, rxp_db, db2, observations, live_logs
(companion logs -> obs_step_time), flags (feedback marks -> eval_flag), quest_xp, map_bounds, travel_edges,
params (mechanics lane), heuristics (heuristics lane), analyzer (analyzer lane, when it exposes
route.load), community, finalize.
"""
import argparse
import glob
import importlib
import json
import os
import pathlib
import sqlite3
import stat
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from knowledge import store  # noqa: E402

STORE_LOADERS = ("glide_entities", "seed_items", "rxp_db", "db2", "observations", "live_logs", "flags", "quest_xp",
                 "map_bounds", "travel_edges", "finalize")
# Loaders whose failure fails the build: the store lane's and the other in-repo lanes' (parser, mechanics params,
# heuristics, analyzer, community). Before, an import error or exception in the parser or the analyzer was only
# "skipped" and a KB without guides or briefs was installed and called up to date.
REQUIRED_LOADERS = STORE_LOADERS + ("rxp_parser", "params", "heuristics", "analyzer", "community")
# tables a usable KB cannot be without, and the loader that fills each (checked only when that loader ran)
REQUIRED_TABLES = (("rxp_guide", "rxp_parser"), ("step_brief", "analyzer"))

# Inputs of the staleness check: (key, pattern relative to the project root); a glob stands for its newest match.
STALE_INPUTS = (
    ("glide.db", "data/glide.db"),
    ("params.json", "data/params.json"),
    ("rules.json", "knowledge/heuristics/rules.json"),
    ("glossary.json", "knowledge/glossary.json"),
    ("schema.sql", "knowledge/schema.sql"),
    ("code", "knowledge/**/*.py"),
    ("game file code", "wowfiles/**/*.py"),         # the DB2 and client-cache readers the loaders import
    ("live logs", "data/live/*.jsonl"),
    ("recorder SV", "data/archive/sv/*.lua"),
    ("community intel", "knowledge/community/intel/*.jsonl"),
    ("community sources", "knowledge/community/sources.json"),
    ("client map tables", "data/db2/*.csv"),
)
MTIME_SLACK = 2.0       # seconds: a recorded mtime still "matches" (FAT/exFAT copies round timestamps to 2 s)
# inputs that ship with the app (small text files): also fingerprinted by content, because an app update copies
# them again with new mtimes (and the release build's checkout gives every file a new one) even when unchanged
HASHED_INPUTS = {"params.json", "rules.json", "glossary.json", "schema.sql", "code", "game file code",
                 "community intel", "community sources"}


# ------------------------------------------------------------------------------------------------
# steps provided by other lanes (in the repo: an import error fails the build like any loader error)
# ------------------------------------------------------------------------------------------------
def _parser(conn, ctx):
    return importlib.import_module("knowledge.rxp_parser").load(conn, ctx)


def _params(conn, ctx):
    mod = importlib.import_module("knowledge.mechanics.params")
    cols = ["key", "value", "unit", "classic_value", "status", "evidence", "updated"]
    rows = []
    for r in mod.to_rows():
        if isinstance(r, dict):
            rows.append([r.get(c) if not isinstance(r.get(c), (dict, list)) else store.jdump(r.get(c)) for c in cols])
        else:
            r = list(r) + [None] * (len(cols) - len(r))
            rows.append(r[:len(cols)])
    rows = [[v if v is None or isinstance(v, (int, float, str)) else store.jdump(v) for v in r] for r in rows]
    store.insert_rows(conn, "param", rows, cols, verb="INSERT OR REPLACE")
    return {"param": len(rows)}


def _heuristics(conn, ctx):
    res = {}
    mod = importlib.import_module("knowledge.heuristics.engine")
    out = mod.load(conn, ctx)             # loads rules.json -> heuristic and glossary.json -> glossary
    res["engine"] = out if isinstance(out, (dict, list, str, int)) or out is None else str(out)
    if not conn.execute("SELECT COUNT(*) FROM glossary").fetchone()[0]:
        res["glossary"] = _glossary_file(conn)
    res["heuristic_rows"] = conn.execute("SELECT COUNT(*) FROM heuristic").fetchone()[0]
    res["glossary_rows"] = conn.execute("SELECT COUNT(*) FROM glossary").fetchone()[0]
    return res


def _glossary_file(conn):
    path = os.path.join(HERE, "glossary.json")
    if not os.path.exists(path):
        return "no knowledge/glossary.json yet"
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    rows = []
    items = data.items() if isinstance(data, dict) else enumerate(data if isinstance(data, list) else [])
    for k, v in items:
        if isinstance(v, dict):
            term = v.get("term") or (k if isinstance(k, str) else None)
            rows.append((term, v.get("definition") or v.get("def") or v.get("text"), store.jdump(v.get("aliases"))))
        elif isinstance(k, str):
            rows.append((k, v if isinstance(v, str) else store.jdump(v), None))
    rows = [r for r in rows if r[0]]
    store.insert_rows(conn, "glossary", rows, ["term", "definition", "aliases"], verb="INSERT OR REPLACE")
    return len(rows)


def _analyzer(conn, ctx):
    return importlib.import_module("knowledge.analyzer.route").load(conn, ctx)


def steps():
    def src(name):
        return lambda conn, ctx: importlib.import_module("knowledge.sources." + name).load(conn, ctx)
    return [("glide_entities", src("glide_entities")), ("rxp_parser", _parser), ("seed_items", src("seed_items")),
            ("rxp_db", src("rxp_db")), ("db2", src("db2")), ("observations", src("observations")),
            ("live_logs", src("live_logs")), ("flags", src("flags")),
            ("quest_xp", src("quest_xp")), ("map_bounds", src("map_bounds")),
            ("travel_edges", src("travel_edges")), ("params", _params), ("heuristics", _heuristics),
            ("analyzer", _analyzer), ("community", src("community")), ("finalize", src("finalize"))]


# ------------------------------------------------------------------------------------------------
def build(out=None, ctx=None, only=None, skip=(), force=False, quiet=False, glide=None, ignore=()):
    """Build the KB at `out` (default data/kb.db). glide: read this glide.db instead of data/glide.db (a staged
    data/glide.next.db). Returns the summary dict (also stored in meta)."""
    out = os.path.abspath(out or store.DEFAULT_KB)
    log = (lambda *a: None) if quiet else (lambda *a: print("[build_kb]", *a, flush=True))
    ctx = ctx or store.BuildContext(log=log, glide_db=glide if glide and os.path.exists(glide) else None)
    t0 = time.time()
    inputs = collect_inputs(ctx.root, glide=glide, ignore=ignore)   # snapshot before any loader reads them
    rxp_kind, rxp_dir = ctx.rxp_source()
    log("RestedXP files: %s" % _rxp_line(ctx, rxp_kind, rxp_dir))
    tmp = os.path.join(tempfile.gettempdir(), "glide_kb_%d_%d.db" % (os.getpid(), int(t0 * 1000) % 100000))
    for p in (tmp, tmp + "-journal"):
        if os.path.exists(p):
            os.remove(p)
    conn = store.open_rw(tmp)
    store.create_schema(conn)
    summary = {"loaders": {}, "errors": [], "out": out, "rxp_source": {"kind": rxp_kind, "path": rxp_dir},
               "rxp_fingerprint": store.rxp_fingerprint(rxp_dir)}
    for name, fn in steps():
        if (only and name not in only and name != "finalize") or name in skip:
            continue
        t = time.time()
        conn.commit()
        conn.execute("SAVEPOINT loader")
        try:
            res = fn(conn, ctx)
            _sql_quiet(conn, "RELEASE SAVEPOINT loader")      # fails harmlessly if the loader committed itself
        except Exception as e:  # a broken loader must not take the build down with it
            if not _sql_quiet(conn, "ROLLBACK TO SAVEPOINT loader"):
                conn.rollback()
            _sql_quiet(conn, "RELEASE SAVEPOINT loader")
            res = {"error": "%s: %s" % (type(e).__name__, e), "trace": traceback.format_exc()[-2000:]}
            summary["errors"].append(name)
            log("ERROR in %s:\n%s" % (name, res["trace"]))
        if not isinstance(res, dict):
            res = {"result": res}
        res["_s"] = round(time.time() - t, 2)
        summary["loaders"][name] = res
        store.set_meta(conn, "loader:" + name, res)
        conn.commit()
        log("%-15s %6.2fs  %s" % (name, res["_s"], _short(res)))
    summary["built"] = time.strftime("%Y-%m-%d %H:%M:%S")
    summary["build_s"] = round(time.time() - t0, 2)
    store.set_meta(conn, "built", summary["built"])
    store.set_meta(conn, "schema_version", str(store.SCHEMA_VERSION))    # KnowledgePack checks it at open
    store.set_meta(conn, "build_s", str(summary["build_s"]))
    store.set_meta(conn, "notes", ctx.notes)
    store.set_meta(conn, "inputs", {"root": ctx.root, "items": inputs})     # what --if-stale compares against
    store.set_meta(conn, "build_args", {"only": sorted(only) if only else None,
                                        "skip": sorted(skip) if skip else None, "force": bool(force)})
    store.set_meta(conn, "rxp_source", summary["rxp_source"])
    store.set_meta(conn, "rxp_fingerprint", summary["rxp_fingerprint"])   # --if-stale: RestedXP updated?
    conn.commit()
    ran = set(summary["loaders"])
    summary["empty"] = [t for t, loader in REQUIRED_TABLES
                        if loader in ran and not conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]]
    conn.execute("ANALYZE")             # planner statistics for the companion's read-only queries
    conn.commit()
    conn.close()

    own_errors = [e for e in summary["errors"] if e in REQUIRED_LOADERS]
    target = out
    if (own_errors or summary["empty"]) and not force:
        target = failed_path(out)
        why = ["loaders failed (%s)" % ", ".join(own_errors)] if own_errors else []
        why += ["no rows in %s" % ", ".join(summary["empty"])] if summary["empty"] else []
        _warn("%s: keeping %s, wrote %s" % ("; ".join(why), _rel(out), _rel(target)))
    written = _install(tmp, target, log)
    summary["written"] = written
    summary["installed"] = written == out
    if summary["installed"]:
        _remove_quiet(pending_path(out))     # an older kb.new.db from a build while the companion had kb.db open
    log("built %s in %.1fs (%d loader errors)" % (written, time.time() - t0, len(summary["errors"])))
    return summary


def failed_path(out):
    """Where a build that must not be installed goes (next to its target)."""
    return os.path.join(os.path.dirname(out), "kb.failed.db")


def _sql_quiet(conn, sql):
    try:
        conn.execute(sql)
        return True
    except sqlite3.OperationalError:
        return False


def _install(tmp, target, log, warn=None):
    """store.install (staged copy renamed over `target`; kb.new.db when `target` stays in use, the companion holding
    it open), plus how that kb.new.db gets installed. Returns the path written."""
    warn = warn or _warn
    written = store.install(tmp, target, warn, what="knowledge base")
    if written == pending_path(target):
        warn("The companion keeps using the old one until it is restarted: restart it with start-glide.cmd (that "
             "installs %s first), or close the companion and run build-kb.cmd again." % os.path.basename(written))
    return written


def _warn(*a):
    print("[build_kb]", *a, flush=True)


def _remove_quiet(path):
    try:
        os.remove(path)
    except OSError:
        pass


_rel = store.rel_path


def _t(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts is not None else "?"


def _rxp_line(ctx, kind, folder):
    if kind == "reference":
        return "%s (repo copy)" % folder
    if kind == "addon":
        return "%s (installed RestedXP addon)" % folder
    return ("NOT FOUND (no %s, no RXPGuides addon under config.json wow_dir: %s); flight times, graveyards, "
            "dangerous mobs and trainer spells will be missing" % (os.path.join(ctx.reference, "rxp"),
                                                                    ctx.wow_dir() or "not set or not found"))


# ------------------------------------------------------------------------------------------------
# kb.new.db (a build that could not replace kb.db while the companion had it open)
# ------------------------------------------------------------------------------------------------
pending_path = store.pending_path


def install_pending(out, log, warn=None):
    """Move kb.new.db over kb.db when it is newer (the companion that held kb.db must be closed by now).
    Returns 'installed', 'in use' (kb.db still open; it stays pending), 'older' (an obsolete leftover, removed)
    or None (nothing pending)."""
    warn = warn or _warn
    new = pending_path(out)
    if not os.path.exists(new):
        return None
    if os.path.exists(out) and os.path.getmtime(new) <= os.path.getmtime(out):
        _remove_quiet(new)
        return "older"
    try:
        os.replace(new, out)
    except PermissionError:
        warn("%s is still in use (is the companion running?): %s stays waiting" % (_rel(out), _rel(new)))
        return "in use"
    log("installed %s (built %s) as %s" % (_rel(new), _t(os.path.getmtime(out)), _rel(out)))
    return "installed"


# ------------------------------------------------------------------------------------------------
# staleness (--if-stale / --check)
# ------------------------------------------------------------------------------------------------
def collect_inputs(root=ROOT, glide=None, ignore=()):
    """The inputs the staleness check compares with kb.db: one dict per STALE_INPUTS entry with key, pattern,
    path (the newest match relative to root, '/' separators; None when nothing matches), mtime (epoch seconds,
    None when nothing matches), t (mtime as local time) and n (number of matching files).
    glide: a staged glide.db (data/glide.next.db) standing in for data/glide.db: its mtime is used under the
    glide.db path, which is what it becomes when the companion promotes it. ignore: files left out (the live
    companion's own session log, still being written)."""
    out = []
    skip = {os.path.normcase(os.path.abspath(p)) for p in ignore or ()}
    for key, pattern in STALE_INPUTS:
        files = []
        if key == "glide.db" and glide and os.path.exists(glide):
            st = os.stat(glide)
            files.append((st.st_mtime, pattern))
            out.append({"key": key, "pattern": pattern, "path": pattern, "mtime": st.st_mtime,
                        "t": _t(st.st_mtime), "n": 1})
            continue
        for p in glob.glob(os.path.join(root, *pattern.split("/")), recursive=True):
            if os.path.normcase(os.path.abspath(p)) in skip:
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                files.append((st.st_mtime, os.path.relpath(p, root).replace(os.sep, "/")))
        newest = max(files) if files else (None, None)      # ties broken by path: the same pick every run
        item = {"key": key, "pattern": pattern, "path": newest[1], "mtime": newest[0],
                "t": _t(newest[0]) if newest[0] is not None else None, "n": len(files)}
        if key in HASHED_INPUTS and files:
            item["hash"] = store.content_hash(root, sorted(f[1] for f in files))
        out.append(item)
    return out


def _read_meta(path, keys):
    """{key: value} for `keys` from the KB's meta table (read-only), or (None, error text)."""
    try:
        uri = pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"
        c = sqlite3.connect(uri, uri=True)
        try:
            rows = c.execute("SELECT key, value FROM meta WHERE key IN (%s)" % ",".join("?" * len(keys)),
                             list(keys)).fetchall()
        finally:
            c.close()
    except sqlite3.Error as e:
        return None, "%s: %s" % (type(e).__name__, e)
    return dict(rows), None


def staleness(kb_path=None, inputs=None, root=ROOT):
    """Does kb_path (default data/kb.db) need a rebuild? `inputs` defaults to collect_inputs(root).

    Stale when kb.db is missing, unreadable, without a build record, of another schema (store.SCHEMA_VERSION), a
    partial build (--only/--skip), or when an input is newer than kb.db ('newer'), or differs from the input the
    build recorded in meta `inputs` ('changed': other file or mtime off by more than MTIME_SLACK; 'gone': no longer
    exists), or when its build recorded a loader error and no kb.failed.db beside it failed from these same inputs
    ('errors'). Returns
    {stale, reason, changes: [{key, pattern, path, mtime, why, was_path, was_mtime}], kb, kb_mtime, built,
    inputs}."""
    kb_path = os.path.abspath(kb_path or store.DEFAULT_KB)
    inputs = collect_inputs(root) if inputs is None else inputs
    d = {"stale": True, "reason": None, "changes": [], "kb": kb_path, "kb_mtime": None, "built": None,
         "inputs": inputs}
    if not os.path.exists(kb_path):
        d["reason"] = "it does not exist yet"
        return d
    d["kb_mtime"] = os.path.getmtime(kb_path)
    meta, err = _read_meta(kb_path, ("built", "inputs", "build_args", "rxp_source", "rxp_fingerprint",
                                     "schema_version"))
    if meta is None:
        d["reason"] = "cannot read it (%s)" % err
        return d
    d["built"] = meta.get("built")
    if not d["built"]:
        d["reason"] = "it has no build record (meta 'built' missing: an interrupted or foreign file)"
        return d
    if str(meta.get("schema_version") or 1) != str(store.SCHEMA_VERSION):
        d["reason"] = "it has schema %s, this code reads schema %s" % (meta.get("schema_version") or 1,
                                                                       store.SCHEMA_VERSION)
        return d
    args = store.jload(meta.get("build_args"), {}) or {}
    if isinstance(args, dict) and (args.get("only") or args.get("skip")):
        d["reason"] = "it is a partial build (%s)" % ", ".join(
            "--%s %s" % (k, ",".join(args[k])) for k in ("only", "skip") if args.get(k))
        return d
    # built without the RestedXP data files (flight times, graveyards, dangerous mobs) but they are available now:
    # e.g. a build made where config.json's wow_dir did not resolve
    rxp_rec = store.jload(meta.get("rxp_source"), None) if "rxp_source" in meta else None
    if isinstance(rxp_rec, dict) and rxp_rec.get("kind") in (None, "missing", "none") \
            and store.BuildContext(root=root).rxp_source()[0] not in (None, "missing", "none"):
        d["reason"] = "it was built without the RestedXP data files, which are available now"
        d["changes"].append({"key": "rxp_source", "pattern": None, "path": None, "mtime": None, "why": "available",
                             "was_path": rxp_rec.get("path"), "was_mtime": None})
        return d
    # RestedXP itself changed (an addon update: new version, DB files or Forever guides), or the KB predates the
    # RestedXP fingerprint (built by an older build_kb)
    cur_fp = store.rxp_fingerprint(store.BuildContext(root=root).rxp_source()[1])
    if cur_fp is not None and "rxp_source" in meta:
        was = store.jload(meta.get("rxp_fingerprint"), None) if "rxp_fingerprint" in meta else None
        if not isinstance(was, dict) or was.get("hash") != cur_fp["hash"]:
            if not isinstance(was, dict):
                d["reason"] = "it was built before RestedXP updates were tracked"
            elif was.get("version") != cur_fp.get("version"):
                d["reason"] = "RestedXP was updated (%s -> %s)" % (was.get("version") or "?", cur_fp.get("version") or "?")
            else:
                d["reason"] = "RestedXP's data or guide files changed (%s)" % (cur_fp.get("version") or "same version")
            d["changes"].append({"key": "rxp_fingerprint", "pattern": None, "path": None, "mtime": None,
                                 "why": "changed", "was_path": (was or {}).get("version"), "was_mtime": None})
            return d
    d["changes"] = _input_changes(inputs, meta.get("inputs"), d["kb_mtime"])
    d["stale"] = bool(d["changes"])
    if d["stale"]:
        d["reason"] = "%d input%s changed since it was built (%s)" % (
            len(d["changes"]), "" if len(d["changes"]) == 1 else "s", d["built"])
        return d
    # a loader failed in the build that made it (an older build_kb, or --force): rebuild, but only once for these
    # inputs; when that rebuild fails too, its kb.failed.db records the same inputs and this stops asking
    errs = _loader_errors(kb_path)
    if errs and not _failed_with(failed_path(kb_path), inputs, cur_fp):
        d["stale"] = True
        d["reason"] = "its build had loader errors (%s)" % ", ".join(errs)
        d["changes"].append({"key": "loader_errors", "pattern": None, "path": None, "mtime": None, "why": "errors",
                             "was_path": None, "was_mtime": None})
    return d


def _input_changes(inputs, recorded_json, kb_mtime):
    """The inputs that differ from the ones a build recorded (meta `inputs`) or are newer than its file."""
    rec = store.jload(recorded_json, {}) or {}
    recorded = {r.get("key"): r for r in (rec.get("items") or [] if isinstance(rec, dict) else [])
                if isinstance(r, dict)}
    changes = []
    for i in inputs:
        r = recorded.get(i["key"])
        why = None
        if r is not None and i.get("hash") and i.get("hash") == r.get("hash"):
            continue                     # same content, only copied again (an app update): not a change
        if i["mtime"] is not None and i["mtime"] > kb_mtime:
            why = "newer"
        elif r is not None:
            if i["path"] is None and r.get("path"):
                why = "gone"
            elif i["path"] is not None and (i["path"] != r.get("path") or r.get("mtime") is None
                                            or abs(i["mtime"] - r["mtime"]) > MTIME_SLACK):
                why = "changed"
        if why:
            changes.append({"key": i["key"], "pattern": i["pattern"], "path": i["path"] or r.get("path"),
                            "mtime": i["mtime"], "why": why, "was_path": r.get("path") if r else None,
                            "was_mtime": r.get("mtime") if r else None})
    return changes


def _loader_errors(path):
    """Names of the loaders recorded with an error in the KB's meta (`loader:<name>` = {"error": ...})."""
    try:
        uri = pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"
        c = sqlite3.connect(uri, uri=True)
        try:
            rows = c.execute("SELECT key, value FROM meta WHERE key LIKE 'loader:%'").fetchall()
        finally:
            c.close()
    except sqlite3.Error:
        return []
    return sorted(k.split(":", 1)[1] for k, v in rows if isinstance(store.jload(v, None), dict)
                  and store.jload(v, None).get("error"))


def _failed_with(failed, inputs, cur_fp):
    """Did a build from exactly these inputs already fail (kb.failed.db next to the KB records them)?"""
    if not os.path.exists(failed):
        return False
    meta, _ = _read_meta(failed, ("inputs", "rxp_fingerprint"))
    if not meta or not meta.get("inputs"):
        return False
    if cur_fp is not None and (store.jload(meta.get("rxp_fingerprint"), None) or {}).get("hash") != cur_fp["hash"]:
        return False
    return not _input_changes(inputs, meta["inputs"], os.path.getmtime(failed))


def describe(d):
    """Human-readable lines for a staleness() decision."""
    name = _rel(d["kb"])
    if not d["stale"]:
        have = [i for i in d["inputs"] if i["mtime"] is not None]
        newest = max(have, key=lambda i: i["mtime"]) if have else None
        return ["%s is up to date (built %s; %d inputs checked, newest %s %s)" % (
            name, d["built"], len(have), newest["path"], _t(newest["mtime"])) if newest else
            "%s is up to date (built %s; no inputs found)" % (name, d["built"])]
    lines = ["%s needs a rebuild: %s" % (name, d["reason"])]
    for ch in d["changes"]:
        if ch["key"] in ("rxp_source", "rxp_fingerprint", "loader_errors"):
            continue                                   # the reason line already says what changed
        if ch["why"] == "newer":
            note = ""
        elif ch["why"] == "gone":
            note = "removed since the build"
        elif ch["was_path"] and ch["was_path"] != ch["path"]:
            note = "newest %s was %s" % (ch["pattern"], ch["was_path"])
        else:
            note = "the build used the %s copy" % _t(ch["was_mtime"])
        lines.append(("  %-7s %-42s %s  %s" % (ch["why"], ch["path"], _t(ch["mtime"]) if ch["mtime"] else "-",
                                                note)).rstrip())
    return lines


# ------------------------------------------------------------------------------------------------
def timing_table(summary):
    """Compact per-loader timing table (lines) for the end of a build."""
    loaders = summary.get("loaders") or {}
    total = summary.get("build_s") or sum((r.get("_s") or 0) for r in loaders.values()) or 0
    lines = ["%-15s %7s %5s  %s" % ("loader", "time", "share", "result")]
    skipped = 0
    for name, res in loaders.items():
        s = res.get("_s") or 0
        if "error" in res:
            st = "ERROR " + str(res["error"])
        elif "skipped" in res:
            st, skipped = "skipped: %s" % res["skipped"], skipped + 1
        else:
            st = "ok"
        st = st.replace("\n", " ")
        lines.append("%-15s %6.1fs %4.0f%%  %s" % (name, s, 100.0 * s / total if total else 0,
                                                   st if len(st) <= 80 else st[:77] + "..."))
    lines.append("%-15s %6.1fs %5s  %d loaders, %d skipped, %d errors -> %s" % (
        "total", total, "", len(loaders), skipped, len(summary.get("errors") or []), _rel(summary.get("written"))))
    return lines


def _short(res):
    items = []
    for k, v in res.items():
        if k in ("_s", "trace"):
            continue
        if isinstance(v, dict):
            v = v.get("rows", v.get("skipped", "{..}"))
        elif isinstance(v, list):
            v = "[%d]" % len(v)
        items.append("%s=%s" % (k, v))
    s = " ".join(items)
    return s if len(s) < 300 else s[:297] + "..."


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", help="output path (default data/kb.db)")
    ap.add_argument("--only", help="comma list of loaders to run (finalize always runs)")
    ap.add_argument("--skip", help="comma list of loaders to skip")
    ap.add_argument("--force", action="store_true", help="replace kb.db even if a store loader failed")
    ap.add_argument("--if-stale", action="store_true",
                    help="build only when kb.db is missing or older than one of its inputs (start-glide.cmd)")
    ap.add_argument("--check", action="store_true",
                    help="print whether a rebuild is needed and why, build nothing (exit 1 = rebuild needed)")
    ap.add_argument("--json", action="store_true", help="print the full build summary as JSON at the end")
    ap.add_argument("--against", help="--if-stale/--check: judge this KB (default: --out) instead; the companion's "
                                      "background refresh builds data/kb.next.db when data/kb.db is stale")
    ap.add_argument("--glide", help="read this glide.db (e.g. data/glide.next.db) instead of data/glide.db")
    ap.add_argument("--ignore", action="append", default=[],
                    help="leave this input file out of the staleness check (the running session's own log)")
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args(argv)
    out = os.path.abspath(a.out or store.DEFAULT_KB)
    say = (lambda *x: None) if a.quiet else (lambda *x: print("[build_kb]", *x, flush=True))
    if a.check or a.if_stale:
        pend = pending_path(out)
        if not a.check:
            install_pending(out, say)
        elif os.path.exists(pend) and (not os.path.exists(out) or os.path.getmtime(pend) > os.path.getmtime(out)):
            print("[build_kb] %s (built %s) is waiting to replace %s: the next --if-stale run installs it" % (
                _rel(pend), _t(os.path.getmtime(pend)), _rel(out)))
        d = staleness(os.path.abspath(a.against) if a.against else out,
                      inputs=collect_inputs(ROOT, glide=a.glide, ignore=a.ignore))
        for line in describe(d):
            if a.check:
                print("[build_kb]", line)       # --check always prints its verdict, even with -q
            else:
                say(line)
        if a.check:
            return 1 if d["stale"] else 0
        if not d["stale"]:
            return 0
        say("rebuilding %s (about a minute or two)..." % _rel(out))
    summary = build(out, only=set(a.only.split(",")) if a.only else None,
                    skip=set(a.skip.split(",")) if a.skip else set(), force=a.force, quiet=a.quiet,
                    glide=a.glide, ignore=a.ignore)
    for line in timing_table(summary):
        say(line)
    if a.json:
        print(json.dumps({"built": summary["built"], "build_s": summary["build_s"], "written": summary["written"],
                          "installed": summary["installed"], "errors": summary["errors"],
                          "rxp_source": summary["rxp_source"],
                          "loaders": {k: {kk: vv for kk, vv in v.items() if kk != "trace"}
                                      for k, v in summary["loaders"].items()}},
                         indent=1, default=str)[:20000])
    return 1 if summary["errors"] or not summary["installed"] else 0


if __name__ == "__main__":
    sys.exit(main())
