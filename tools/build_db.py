#!/usr/bin/env python3
"""Build data/glide.db (SQLite) from every quest-data source we have.

Inputs (all optional except the seed):
  data/seed/seed.json.gz                 community datasets (build_seed.py)
  data/archive/wdb/**.wdb + live Cache/WDB   what the server sent the client (wdb.py)
  data/archive/sv/*.lua + live SavedVariables/Glide.lua (or old GlideRecorder.lua)   recorder sessions
  data/archive/sv/RXPGuides*.lua + live RXPGuides.lua   RestedXP's own guide cache (best effort)

Field precedence for quests, highest first:
  wdb (server) > recorder (seen in game) > scan (API) > forever-ref collect > Wowhead >
  ForeverGuide > QuestieDB
Usage:
  python build_db.py [--wow "D:\\Blizzard\\World of Warcraft\\_classic_beta_"] [--out data/glide.db]
"""
import argparse, glob, gzip, json, os, re, sqlite3, sys, tempfile, time, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.append(ROOT)
from knowledge import store as kstore  # noqa: E402  (RestedXP file list / fingerprint, shared with build_kb)
from knowledge.sources import _lua  # noqa: E402  (the SavedVariables reader)
from knowledge.values import as_float, as_int  # noqa: E402
from wowfiles import wdb  # noqa: E402

PRIORITY = ["frq", "fg", "whd", "whz", "frc", "scan", "obs", "wdb"]   # low -> high
# Every repo module this builder runs (itself, what it imports, and config.json's reader with its imports): part of
# the input fingerprint by content, so a change to any of them rebuilds glide.db and an app update that only copies
# them again does not. tests/test_build_install.py checks the list against what an import really loads.
CODE = ("tools/build_db.py", "knowledge/__init__.py", "knowledge/store.py", "knowledge/sources/__init__.py",
        "knowledge/sources/_lua.py", "knowledge/values.py", "wowfiles/__init__.py", "wowfiles/wdb.py", "companion/glideconfig.py",
        "companion/jsonfile.py", "companion/settings.py")


def log(*a):
    print("[build_db]", *a, flush=True)


def wow_dir_config():
    """The WoW folder config.json names (wow_dir), or None."""
    return kstore.glideconfig().wow_dir(ROOT)


def as_list(v):
    if v is None:
        return []
    if isinstance(v, dict):
        return [v[k] for k in sorted(v, key=lambda x: int(x) if str(x).isdigit() else 0)]
    return v if isinstance(v, list) else [v]


def kv(v):
    """(key, value) pairs of a Lua table that may have become a list (keys 1..n)."""
    if isinstance(v, dict):
        return list(v.items())
    if isinstance(v, list):
        return [(str(i + 1), x) for i, x in enumerate(v)]
    return []


def pts(v):
    """Only the [x, y] points of a spawn list (drops extra keys such as n=total)."""
    return [p for p in as_list(v) if isinstance(p, list) and len(p) >= 2
            and all(isinstance(c, (int, float)) for c in p[:2])]


# ------------------------------------------------------------------------------------------------
# inputs
# ------------------------------------------------------------------------------------------------
def find_live(wow):
    out = {"wdb": None, "sv": [], "rxp_sv": []}
    if not wow or not os.path.isdir(wow):
        return out
    c = os.path.join(wow, "Cache", "WDB")
    out["wdb"] = c if os.path.isdir(c) else None
    for acct in glob.glob(os.path.join(wow, "WTF", "Account", "*", "SavedVariables")):
        out["sv"] += glob.glob(os.path.join(acct, "GlideRecorder.lua")) + glob.glob(os.path.join(acct, "Glide.lua"))
        out["rxp_sv"] += glob.glob(os.path.join(acct, "RXPGuides.lua"))
    return out


def load_wdb(folders):
    res = {"quests": {}, "npcs": {}, "objects": {}, "builds": {}}
    for f in folders:
        if not f or not os.path.isdir(f):
            continue
        d = wdb.decode_folder(f)
        for k in ("quests", "npcs", "objects"):
            res[k].update(d[k])
        if d["failures"]:
            log("wdb: %d records skipped in %s (layout drift?)" % (len(d["failures"]), f))
    return res


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def newer(a, b):
    """Guide-text candidates {"text", "_t"}: a replaces b when it is newer (ties: the longer text). The newest
    copy wins, never just the longest: an update that shortens a guide must replace the old version."""
    if b is None:
        return True
    ta, tb = a.get("_t") or 0, b.get("_t") or 0
    if ta != tb:
        return ta > tb
    return len(a.get("text") or "") > len(b.get("text") or "")


def load_sessions(files):
    sessions, rxp_texts, rxp_steps = {}, {}, {}
    for f in files:
        try:
            g = _lua.recorder_db(f, warn=log)
        except Exception as e:
            log("cannot parse %s: %s" % (f, e))
            continue
        for sid, s in (g.get("sessions") or {}).items():
            size = len(json.dumps(s, default=str))
            if sid not in sessions or size > sessions[sid][0]:
                sessions[sid] = (size, s, f)
        f_t = _mtime(f)
        for k, v in (g.get("rxp") or {}).items():
            if isinstance(v, dict) and v.get("text"):
                v = dict(v, _t=as_float(v.get("t")) or f_t)       # capture time (server clock), else the file's
                if newer(v, rxp_texts.get(k)):
                    rxp_texts[k] = v
        for k, v in (g.get("rxpSteps") or {}).items():
            rxp_steps[k] = v
    return {k: v[1] for k, v in sessions.items()}, {k: v[2] for k, v in sessions.items()}, rxp_texts, rxp_steps


def rxp_from_own_sv(files):
    """Best effort: RXPGuides' SavedVariables keep imported guides deflate-compressed."""
    out = {}
    for f in files:
        try:
            data = _lua.read_saved_variables(f)
        except Exception as e:
            log("cannot parse %s: %s" % (f, e))
            continue

        def walk(v, f_t):                    # f_t: this file's time, passed down rather than read from the loop
            if isinstance(v, dict):
                blob = v.get("groupOrContent")
                if isinstance(blob, str) and len(blob) > 100:
                    for enc in ("latin-1", "utf-8"):
                        try:
                            txt = zlib.decompress(blob.encode(enc, "surrogateescape"), -15).decode("utf-8", "replace")
                            name = re.search(r"#name\s+([^\r\n]+)", txt)
                            group = re.search(r"#group\s+([^\r\n]+)", txt)
                            key = "%s||%s" % (group.group(1) if group else "?", name.group(1) if name else len(txt))
                            cand = {"text": txt, "how": "rxp-sv", "_t": f_t}
                            if newer(cand, out.get(key)):
                                out[key] = cand
                            break
                        except Exception:
                            continue
                for x in v.values():
                    walk(x, f_t)
            elif isinstance(v, list):
                for x in v:
                    walk(x, f_t)
        walk(data, _mtime(f))
    return out


# ------------------------------------------------------------------------------------------------
# quests
# ------------------------------------------------------------------------------------------------
def side_from_races(r):
    r = as_int(r)
    if not r or r < 0 or r >= 2 ** 63:
        return None
    alliance, horde = 1 | 4 | 8 | 64 | 1024 | 65536, 2 | 16 | 32 | 128 | 256 | 512 | 131072
    if r & alliance and not r & horde:
        return "alliance"
    if r & horde and not r & alliance:
        return "horde"
    return "both"


SIDE = {1: "alliance", 2: "horde", 3: "both", 0: "both"}


class QuestBook:
    def __init__(self):
        self.q = {}       # id -> {field: (value, source)}
        self.ents = {}    # (qid, role) -> {(kind, id): {name, source}}
        self.objs = {}    # qid -> (source_rank, [objective dicts])
        self.pre = {}     # qid -> set((pre, kind))
        self.points = []  # (qid, role, kind, entity, ui_map, x, y, source)

    def set(self, qid, src, **fields):
        qid = as_int(qid)
        if not qid:
            return
        rec = self.q.setdefault(qid, {})
        rank = PRIORITY.index(src)
        for k, v in fields.items():
            if v is None or v == "" or v == []:
                continue
            old = rec.get(k)
            if old is None or PRIORITY.index(old[1]) <= rank:
                rec[k] = (v, src)
        rec.setdefault("_src", (set(), src))[0].add(src)

    def ent(self, qid, role, kind, eid, src, name=None):
        eid = as_int(eid)
        if not eid:
            return
        d = self.ents.setdefault((as_int(qid), role), {})
        cur = d.get((kind, eid))
        if not cur or (name and not cur.get("name")):
            d[(kind, eid)] = {"name": name or (cur or {}).get("name"), "source": src}

    def objectives(self, qid, src, items):
        rank = PRIORITY.index(src)
        cur = self.objs.get(as_int(qid))
        if items and (not cur or cur[0] <= rank):
            self.objs[as_int(qid)] = (rank, items, src)


def ingest_frq(book, seed):
    for qid, r in (seed["sources"].get("frq_quests") or {}).items():
        book.set(qid, "frq", name=r.get("n"), level=r.get("lv"), min_level=r.get("rl"), races=r.get("races"),
                 side=side_from_races(r.get("races")), zone_area=r.get("zone"), next_id=r.get("next"),
                 objective_text=r.get("obj"), classes=r.get("classes"))
        for role, key in (("start", "start"), ("end", "end")):
            for k, kind in (("c", "npc"), ("o", "object"), ("i", "item")):
                for eid in as_list((r.get(key) or {}).get(k)):
                    book.ent(qid, role, kind, eid, "frq")
        for p in as_list(r.get("pre")):
            book.pre.setdefault(as_int(qid), set()).add((as_int(p), "pre"))
        objs = []
        for k, kind in (("c", "monster"), ("i", "item"), ("o", "object")):
            for o in as_list((r.get("objs") or {}).get(k)):
                tid = o[0] if isinstance(o, list) else o
                objs.append({"type": kind, "target": as_int(tid), "amount": None, "text": None})
        book.objectives(qid, "frq", objs)


def ingest_fg(book, seed):
    for qid, r in seed["sources"]["fg_quests"].items():
        txt = r.get("text")
        book.set(qid, "fg", name=r.get("n"), level=r.get("lvl"), min_level=r.get("req"), races=r.get("races"),
                 classes=r.get("classes"), side=side_from_races(r.get("races")), zone_area=r.get("zone"),
                 xp=r.get("xp"), next_id=r.get("next"),
                 objective_text=" ".join(as_list(txt)) if txt else None,
                 is_forever=1 if r.get("forever") else None, removed=1 if r.get("removed") else None,
                 hidden=1 if r.get("hidden") else None)
        for role, keys in (("start", (("snpc", "npc"), ("sobj", "object"), ("sitem", "item"))),
                           ("end", (("enpc", "npc"), ("eobj", "object")))):
            for k, kind in keys:
                for eid in as_list(r.get(k)):
                    book.ent(qid, role, kind, eid, "fg")
        for role, k in (("start", "fstart"), ("end", "ffin")):
            for m, pp in kv((r.get(k) or {}).get("spm")):
                for p in pts(pp):
                    book.points.append((as_int(qid), role, None, None, as_int(m), p[0], p[1], "fg"))
        for p in as_list(r.get("pre")):
            book.pre.setdefault(as_int(qid), set()).add((as_int(p), "pre"))
        for p in as_list(r.get("pregroup")):
            book.pre.setdefault(as_int(qid), set()).add((as_int(p), "pregroup"))
        for p in as_list(r.get("breadcrumbs")):
            book.pre.setdefault(as_int(qid), set()).add((as_int(p), "breadcrumb"))
        objs = []
        for k, kind in (("kill", "monster"), ("item", "item"), ("obj", "object")):
            for o in as_list(r.get(k)):
                o = as_list(o)
                objs.append({"type": kind, "target": as_int(o[0]) if o else None,
                             "amount": as_int(o[1]) if len(o) > 1 and isinstance(o[1], (int, float)) else None,
                             "text": None})
        for fo in as_list(r.get("fobj")):
            objs.append({"type": fo.get("kind"), "target": None, "amount": None, "text": fo.get("text")})
            for m, pp in kv(fo.get("spm")):
                for p in pts(pp):
                    book.points.append((as_int(qid), "objective", None, None, as_int(m), p[0], p[1], "fg"))
        book.objectives(qid, "fg", objs)


def ingest_wowhead(book, seed):
    src = seed["sources"]
    for r in src.get("whz_list") or ():
        book.set(r["id"], "whz", name=r.get("name"), level=r.get("level"), min_level=r.get("reqlevel"),
                 side=SIDE.get(r.get("side")), xp=r.get("xp"), money=r.get("money"), zone_area=r.get("category"))
    for qid, r in (src.get("whz_info") or {}).items():
        book.set(qid, "whz", objective_text=r.get("obj"))
        for role, key in (("start", "start"), ("end", "end")):
            v = r.get(key)
            if isinstance(v, list) and len(v) >= 2:
                book.ent(qid, role, "object" if v[0] == "object" else v[0], v[1], "whz")
        for pin in as_list(r.get("pins")):
            for p in pts(pin.get("pts")):
                book.points.append((as_int(qid), "objective", None, None, 2521 if pin.get("zone") == 16593 else None,
                                    p[0], p[1], "whz"))
    for dung in src.get("whd") or ():
        for r in as_list(dung.get("quests")):
            book.set(r["id"], "whd", name=r.get("n"), level=r.get("lv"), min_level=r.get("rl"),
                     side=SIDE.get(r.get("side")), xp=r.get("xp"), money=r.get("money"), zone_area=dung.get("zone"),
                     details=r.get("desc"))
            for role in ("start", "end"):
                for e in as_list(r.get(role)):
                    if isinstance(e, list) and len(e) >= 2:
                        book.ent(r["id"], role, e[0], e[1], "whd", e[2] if len(e) > 2 else None)


def ingest_frc(book, seed):
    for qid, r in ((seed["sources"].get("frc") or {}).get("quests") or {}).items():
        book.set(qid, "frc", name=r.get("t"), level=r.get("lv"), details=r.get("txt"), objective_text=r.get("obj"),
                 xp=r.get("xp"), money=r.get("money"))
        for role, ek, pk in (("start", "giver", "gpos"), ("end", "ender", "epos")):
            e = r.get(ek)
            if isinstance(e, dict) and e.get("id"):
                kind = "object" if e.get("k") == "GameObject" else "npc"
                book.ent(qid, role, kind, e["id"], "frc", e.get("n"))
            p = r.get(pk)
            if isinstance(p, dict) and p.get("m"):
                book.points.append((as_int(qid), role, None, None, p["m"], p.get("x"), p.get("y"), "frc"))


def ingest_sessions(book, sessions):
    for _sid, s in sessions.items():
        for qid, q in kv(s.get("quests")):
            turned = q.get("turned") or {}
            book.set(qid, "obs", name=q.get("t"), level=q.get("lvl"), details=q.get("details"),
                     objective_text=q.get("objText"), progress_text=q.get("progText"),
                     completion_text=q.get("doneText"), xp=turned.get("xp") or q.get("xpOffer"),
                     money=turned.get("money") or q.get("moneyOffer"))
            for role, key in (("start", "giver"), ("end", "ender")):
                for e in as_list(q.get(key)):
                    book.ent(qid, role, e.get("k", "npc"), e.get("id"), "obs", e.get("n"))
                    if e.get("x") is not None:
                        book.points.append((as_int(qid), role, e.get("k"), e.get("id"), e.get("m"), e.get("x"),
                                            e.get("y"), "obs"))
            for i, pts in enumerate(as_list(q.get("prog"))):
                for p in as_list(pts):
                    book.points.append((as_int(qid), "objective%d" % (i + 1), None, None, p.get("m"), p.get("x"),
                                        p.get("y"), "obs"))
            for p in as_list(q.get("wp")):
                book.points.append((as_int(qid), "waypoint", None, None, p.get("m"), p.get("x"), p.get("y"), "obs"))
            objs = []
            mobs = q.get("mobs") or {}
            for i, o in enumerate(as_list(q.get("objectives"))):
                m = dict(kv(dict(kv(mobs)).get(str(i + 1)) or dict(kv(mobs)).get(i + 1)))
                target = max(m, key=lambda k: m[k]) if m else None
                objs.append({"type": o.get("type"), "target": as_int(target), "amount": o.get("need"),
                             "text": o.get("text")})
            book.objectives(qid, "obs", objs)
        for npc, g in kv(s.get("gossip")):
            for qid, _ in kv(g.get("avail")):
                book.ent(qid, "start", g.get("k", "npc"), npc, "obs", g.get("n"))
        for qid, r in kv(s.get("scan")):
            if not isinstance(r, dict) or not r.get("t"):
                continue
            book.set(qid, "scan", name=r.get("t"), level=r.get("l"), ui_map=r.get("m"),
                     side={0: "both", 1: "alliance", 2: "horde"}.get(r.get("f")))
            objs = [{"type": o[1] if len(o) > 1 else None, "target": None,
                     "amount": o[2] if len(o) > 2 else None, "text": o[0] if o else None}
                    for o in as_list(r.get("o")) if isinstance(o, list)]
            book.objectives(qid, "scan", objs)


def ingest_wdb(book, w):
    for qid, r in w["quests"].items():
        book.set(qid, "wdb", name=r.get("name"), level=r.get("level"), min_level=r.get("min_level"),
                 side=r.get("side"), next_id=r.get("next_in_chain"), objective_text=r.get("objective"),
                 details=r.get("details"), completion_text=r.get("completion_log"),
                 zone_area=r.get("zone_or_sort"))
        objs = [{"type": o["type"], "target": o["target"], "amount": o["amount"], "text": o.get("text")}
                for o in r.get("objectives", [])]
        if objs:
            book.objectives(qid, "wdb", objs)


# ------------------------------------------------------------------------------------------------
# RestedXP guides
# ------------------------------------------------------------------------------------------------
STEP_VERBS = re.compile(r"^\.(\w+)\s*(.*)$")


def parse_rxp(text):
    """Guide text -> (meta, steps). Each step: dict(filters, lines=[(verb, args, comment)])."""
    meta, steps, cur = {}, [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("--") and not line.startswith("--["):
            continue
        if line.startswith("#") and cur is None:
            m = re.match(r"#(\w+)\s*(.*)", line)
            if m:
                meta.setdefault(m.group(1), m.group(2).strip())
            continue
        if line.startswith("step"):
            cur = {"filters": line[4:].replace("<<", "").strip(), "lines": []}
            steps.append(cur)
            continue
        if cur is None:
            continue
        comment = None
        if "--" in line:
            line, comment = line.split("--", 1)
            line = line.strip()
        text_part = None
        if ">>" in line:
            line, text_part = [x.strip() for x in line.split(">>", 1)]
        filt = None
        if "<<" in line:
            line, filt = [x.strip() for x in line.split("<<", 1)]
        m = STEP_VERBS.match(line)
        if m:
            cur["lines"].append({"verb": m.group(1), "args": m.group(2).strip(), "text": text_part,
                                 "filter": filt, "comment": comment and comment.strip()})
        elif line.startswith("#"):
            cur["lines"].append({"verb": "#" + line[1:].split()[0], "args": line[1:].partition(" ")[2],
                                 "text": None, "filter": filt, "comment": None})
        else:
            cur["lines"].append({"verb": "text", "args": "", "text": line, "filter": filt, "comment": None})
    return meta, steps


def parse_goto(args):
    """'.goto 1429/0,-136.48,-8933.47' (world) or '.goto Elwynn Forest,48.2,42.1' (zone %)."""
    parts = [p.strip() for p in args.split(",")]
    if len(parts) < 3:
        return {}
    try:
        a, b = float(parts[1]), float(parts[2])
    except ValueError:
        return {}
    m = re.match(r"^(\d+)/(\d+)$", parts[0])
    if m:
        return {"ui_map": int(m.group(1)), "wy": a, "wx": b}
    if parts[0].isdigit():
        return {"ui_map": int(parts[0]), "x": a, "y": b}
    return {"zone": parts[0], "x": a, "y": b}


# ------------------------------------------------------------------------------------------------
# database
# ------------------------------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE quests(id INTEGER PRIMARY KEY, name TEXT, level INT, min_level INT, side TEXT, races INT, classes INT,
  zone_area INT, zone_name TEXT, ui_map INT, xp INT, money INT, next_id INT, objective_text TEXT, details TEXT,
  progress_text TEXT, completion_text TEXT, is_forever INT, removed INT, hidden INT, in_client INT,
  sources TEXT, field_sources TEXT);
CREATE TABLE quest_entities(quest_id INT, role TEXT, kind TEXT, entity_id INT, name TEXT, source TEXT);
CREATE TABLE quest_objectives(quest_id INT, idx INT, type TEXT, target_id INT, amount INT, text TEXT, source TEXT);
CREATE TABLE quest_prereqs(quest_id INT, pre_id INT, kind TEXT);
CREATE TABLE quest_points(quest_id INT, role TEXT, kind TEXT, entity_id INT, ui_map INT, x REAL, y REAL, source TEXT);
CREATE TABLE entities(kind TEXT, id INT, name TEXT, min_level INT, max_level INT, friendly TEXT, rank INT,
  subname TEXT, zone_area INT, source TEXT, PRIMARY KEY(kind, id));
CREATE TABLE spawns(kind TEXT, entity_id INT, ui_map INT, x REAL, y REAL, accuracy TEXT, source TEXT);
CREATE TABLE zones(area_id INT PRIMARY KEY, name TEXT, ui_map INT, parent INT);
CREATE TABLE maps(ui_map INT PRIMARY KEY, name TEXT, parent INT, area_id INT);
CREATE TABLE map_areas(ui_map INT, area_id INT, name TEXT, x1 REAL, y1 REAL, x2 REAL, y2 REAL);
CREATE TABLE rxp_guides(guide_key TEXT PRIMARY KEY, name TEXT, grp TEXT, next TEXT, source TEXT, text TEXT);
CREATE TABLE rxp_steps(guide_key TEXT, step INT, line INT, filters TEXT, verb TEXT, args TEXT, quest_id INT,
  ui_map INT, zone TEXT, x REAL, y REAL, wx REAL, wy REAL, text TEXT, line_filter TEXT, comment TEXT);
CREATE TABLE sessions(id TEXT PRIMARY KEY, char TEXT, realm TEXT, class TEXT, race TEXT, faction TEXT,
  level_start INT, build TEXT, started INT, ended INT, file TEXT);
CREATE TABLE obs_quests(session TEXT, quest_id INT, offered_t INT, accepted_t INT, accepted_lvl INT,
  turned_t INT, turned_lvl INT, xp INT, money INT, removed_t INT, rxp_guide TEXT, rxp_step INT);
CREATE TABLE obs_xp(session TEXT, t INT, lvl INT, xp INT, max INT, rest INT);
CREATE TABLE obs_kills(session TEXT, t INT, npc_id INT, name TEXT, xp INT, rest INT, mob_level INT, lvl INT,
  ui_map INT, x REAL, y REAL);
CREATE TABLE obs_trail(session TEXT, t INT, ui_map INT, x REAL, y REAL, combat INT);
CREATE TABLE obs_events(session TEXT, t INT, event TEXT, lvl INT, ui_map INT, x REAL, y REAL, data TEXT);
CREATE TABLE obs_misc(session TEXT, kind TEXT, key TEXT, json TEXT);
CREATE TABLE scan(quest_id INT PRIMARY KEY, title TEXT, level INT, ui_map INT, answered INT, session TEXT);
CREATE VIEW v_quests AS
  SELECT q.*, (SELECT group_concat(name, ' / ') FROM quest_entities e WHERE e.quest_id=q.id AND e.role='start') AS givers,
              (SELECT group_concat(name, ' / ') FROM quest_entities e WHERE e.quest_id=q.id AND e.role='end') AS enders
  FROM quests q;
CREATE INDEX i_qe ON quest_entities(quest_id); CREATE INDEX i_qo ON quest_objectives(quest_id);
CREATE INDEX i_qp ON quest_points(quest_id); CREATE INDEX i_sp ON spawns(kind, entity_id);
CREATE INDEX i_rs ON rxp_steps(quest_id); CREATE INDEX i_ent_name ON entities(name);
"""


def input_fingerprint(files, dirs=(), root=ROOT):
    """sha1 over (path, size, mtime) of every input file (and every file under `dirs`) plus the content of this
    builder's code (CODE, under `root`), so glide.db is only rebuilt when something it reads or runs changed."""
    import hashlib
    paths = set(p for p in files if p)
    for d in dirs:
        if d and os.path.isdir(d):
            paths.update(glob.glob(os.path.join(d, "**", "*"), recursive=True))
    h = hashlib.sha1()
    for p in sorted(paths):
        try:
            st = os.stat(p)
        except OSError:
            continue
        if os.path.isfile(p):
            h.update(("%s|%d|%d\n" % (os.path.abspath(p), st.st_size, int(st.st_mtime))).encode("utf-8"))
    h.update(("code|%s\n" % kstore.content_hash(root, CODE)).encode("utf-8"))
    return h.hexdigest()


def read_meta(db_path, key):
    try:
        c = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)
        try:
            r = c.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        finally:
            c.close()
        return r[0] if r else None
    except sqlite3.Error:
        return None


def gather_inputs(args):
    """Where every input is: the WoW folder's live files, the archive, RestedXP's addon guides; the output path and
    the fingerprint of the inputs."""
    wow = args.wow or wow_dir_config()
    live = find_live(wow)
    log("wow dir:", wow or "(none)", "| live sv:", len(live["sv"]), "| live wdb:", bool(live["wdb"]))

    wdb_dirs = sorted(glob.glob(os.path.join(ROOT, "data", "archive", "wdb", "*"))) + [live["wdb"]]
    sv_files = sorted(glob.glob(os.path.join(ROOT, "data", "archive", "sv", "Glide*.lua"))) + live["sv"]
    rxp_sv_files = sorted(glob.glob(os.path.join(ROOT, "data", "archive", "sv", "RXPGuides*.lua"))) + live["rxp_sv"]
    rxp_dir = os.path.join(wow, *kstore.RXP_ADDON_DIR) if wow else None
    rxp_ver = kstore.rxp_version(rxp_dir) if rxp_dir else None
    addon_guides = [p for p in kstore.rxp_files(rxp_dir)
                    if os.path.relpath(p, rxp_dir).replace(os.sep, "/").lower().startswith("guides/")]
    out = args.out or os.path.join(ROOT, "data", "glide.db")
    fingerprint = input_fingerprint([os.path.join(ROOT, "data", "seed", "seed.json.gz")] + sv_files + rxp_sv_files
                                    + addon_guides + ([os.path.join(rxp_dir, "RXPGuides.toc")] if rxp_dir else []),
                                    wdb_dirs)
    return {"wdb_dirs": wdb_dirs, "sv_files": sv_files, "rxp_sv_files": rxp_sv_files, "rxp_dir": rxp_dir,
            "rxp_ver": rxp_ver, "addon_guides": addon_guides, "out": out, "fingerprint": fingerprint}


def up_to_date(args, out, fingerprint):
    """True (and says so) when the output, or the --compare database, was built from these very inputs."""
    for cur in [out] + ([args.compare] if getattr(args, "compare", None) else []):
        if not args.force and os.path.exists(cur) and read_meta(cur, "input_fingerprint") == fingerprint:
            log("glide.db is up to date (no input changed since %s); --force rebuilds anyway" % read_meta(cur, "built"))
            return True
    return False


def free_guide_texts(inputs, seed):
    """(source label, [(path, text, time)]) of RestedXP's free guides: the installed addon's files, else the seed's
    snapshot."""
    rxp_dir, addon_guides = inputs["rxp_dir"], inputs["addon_guides"]
    if addon_guides:
        free_src = "addon %s" % (inputs["rxp_ver"] or "?")
        free_guides = [(os.path.relpath(p, os.path.join(rxp_dir, "Guides")).replace(os.sep, "/"),
                        open(p, encoding="utf-8", errors="replace").read(), _mtime(p)) for p in addon_guides]
    else:
        free_src = "seed snapshot"
        free_guides = [(path, text, 0) for path, text in seed["sources"]["rxp_free"].items()]
    log("RestedXP %s | free guide files (starter zones, dungeons) from %s: %d; premium guides follow below"
        % (inputs["rxp_ver"] or "(not installed)", free_src, len(free_guides)))
    return free_src, free_guides


def quest_book(seed, sessions, w):
    """Every quest source merged by field precedence (PRIORITY)."""
    book = QuestBook()
    ingest_frq(book, seed)
    ingest_fg(book, seed)
    ingest_wowhead(book, seed)
    ingest_frc(book, seed)
    ingest_sessions(book, sessions)
    ingest_wdb(book, w)
    return book


def write_quests(db, book, seed):
    """quests (one row per known quest, plus the client's quests nobody has data for yet), quest_entities,
    quest_objectives, quest_prereqs and quest_points. Returns the number of quest rows."""
    zones = seed["zones"]
    a2m = {as_int(k): v for k, v in zones["areaToMap"].items()}
    names = {as_int(k): v for k, v in (zones.get("names") or {}).items()}
    in_client = set(seed["ids"]["all"]) - set(seed["ids"]["removed_in_forever"])
    rows = []
    for qid, rec in book.q.items():
        v = {k: x[0] for k, x in rec.items() if k != "_src"}
        fs = {k: x[1] for k, x in rec.items() if k != "_src"}
        za = as_int(v.get("zone_area"))
        um = v.get("ui_map") or (a2m.get(za) if za and za > 0 else None)
        rows.append((qid, v.get("name"), as_int(v.get("level")), as_int(v.get("min_level")), v.get("side"),
                     as_int(v.get("races")), as_int(v.get("classes")), za, names.get(za), um, as_int(v.get("xp")),
                     as_int(v.get("money")), as_int(v.get("next_id")), v.get("objective_text"), v.get("details"),
                     v.get("progress_text"), v.get("completion_text"), v.get("is_forever"), v.get("removed"),
                     v.get("hidden"), 1 if qid in in_client else 0, ",".join(sorted(rec["_src"][0])),
                     json.dumps(fs, separators=(",", ":"))))
    known = set(book.q)
    for qid in sorted(in_client - known):   # in the client but no data yet: the scan fills these
        rows.append((qid,) + (None,) * 19 + (1, "", "{}"))
    db.executemany("INSERT INTO quests VALUES (%s)" % ",".join("?" * 23), rows)

    db.executemany("INSERT INTO quest_entities VALUES (?,?,?,?,?,?)",
                   [(q, role, kind, eid, d["name"], d["source"]) for (q, role), ents in book.ents.items()
                    for (kind, eid), d in ents.items()])
    db.executemany("INSERT INTO quest_objectives VALUES (?,?,?,?,?,?,?)",
                   [(q, i + 1, str(o.get("type")) if o.get("type") is not None else None, o.get("target"),
                     o.get("amount"), o.get("text"), src) for q, (_, objs, src) in book.objs.items()
                    for i, o in enumerate(objs)])
    db.executemany("INSERT INTO quest_prereqs VALUES (?,?,?)",
                   [(q, p, k) for q, s in book.pre.items() for p, k in s if p])
    db.executemany("INSERT INTO quest_points VALUES (?,?,?,?,?,?,?,?)", book.points)
    return len(rows)


def write_entities(db, seed, w, sessions):
    """entities (NPCs and objects, the first source to name a field wins) and their spawns, then the names of the
    quest_entities rows that had none."""
    a2m = {as_int(k): v for k, v in seed["zones"]["areaToMap"].items()}
    ents, spawns = {}, []

    def put(kind, eid, src, **f):
        eid = as_int(eid)
        if not eid:
            return
        cur = ents.setdefault((kind, eid), {"source": src})
        for k, x in f.items():
            if x is not None and cur.get(k) is None:
                cur[k] = x

    src = seed["sources"]
    for k, r in w["npcs"].items():
        put("npc", k, "wdb", name=r.get("name"))
    for k, r in w["objects"].items():
        put("object", k, "wdb", name=r.get("name"))
    for _sid, s in sessions.items():
        for kind, book_ in (("npc", s.get("npcs")), ("object", s.get("objects"))):
            for eid, e in kv(book_):
                put(kind, eid, "obs", name=e.get("n"), min_level=e.get("lvl"))
                for p in as_list(e.get("at")):
                    spawns.append((kind, as_int(eid), p.get("m"), p.get("x"), p.get("y"), "exact", "obs"))
                for p in as_list(e.get("near")):
                    spawns.append((kind, as_int(eid), p.get("m"), p.get("x"), p.get("y"), "near", "obs"))
    for eid, r in ((src.get("frc") or {}).get("npcs") or {}).items():
        put("npc", eid, "frc", name=r.get("n"), min_level=r.get("lmin"), max_level=r.get("lmax"))
        for p in as_list(r.get("pos")):
            spawns.append(("npc", as_int(eid), p.get("m"), p.get("x"), p.get("y"), "exact", "frc"))
    for eid, r in (src.get("whz_npcpos") or {}).items():
        put("npc", eid, "whz", name=r.get("_n"))
        for area, pp in r.items():
            if area.startswith("_"):
                continue
            for p in pts(pp):
                spawns.append(("npc", as_int(eid), a2m.get(as_int(area)) or (2521 if area == "16593" else None),
                               p[0], p[1], "db", "whz"))
    for kind, table in (("npc", "fg_npcs"), ("object", "fg_objects")):
        for eid, r in src[table].items():
            put(kind, eid, "fg", name=r.get("n"), min_level=r.get("min"), max_level=r.get("max"),
                friendly=r.get("f"), rank=r.get("rank"), zone_area=r.get("zone"))
            got_map = set()
            for m, pp in kv(r.get("spm")):
                got_map.add(as_int(m))
                for p in pts(pp):
                    spawns.append((kind, as_int(eid), as_int(m), p[0], p[1], "db", "fg"))
            for area, pp in kv(r.get("sp")):
                m = a2m.get(as_int(area))
                if m in got_map:
                    continue
                for p in pts(pp):
                    spawns.append((kind, as_int(eid), m, p[0], p[1], "db", "fg"))
    for kind, table in (("npc", "frq_npcs"), ("object", "frq_objects")):
        for eid, r in (src.get(table) or {}).items():
            put(kind, eid, "frq", name=r.get("n"), min_level=r.get("lmin"), max_level=r.get("lmax"),
                friendly=r.get("friendly"), rank=r.get("rank"), subname=r.get("sub"), zone_area=r.get("zone"))
            for area, pp in kv(r.get("spawns")):
                m = a2m.get(as_int(area))
                for p in pts(pp):
                    spawns.append((kind, as_int(eid), m, p[0], p[1], "db", "frq"))
    db.executemany("INSERT INTO entities VALUES (?,?,?,?,?,?,?,?,?,?)",
                   [(k, i, e.get("name"), as_int(e.get("min_level")), as_int(e.get("max_level")), e.get("friendly"),
                     as_int(e.get("rank")), e.get("subname"), as_int(e.get("zone_area")), e["source"])
                    for (k, i), e in ents.items()])
    db.executemany("INSERT INTO spawns VALUES (?,?,?,?,?,?,?)", spawns)
    # fill entity names for quest_entities
    db.execute("""UPDATE quest_entities SET name=(SELECT name FROM entities e WHERE e.kind=quest_entities.kind
                  AND e.id=quest_entities.entity_id) WHERE name IS NULL""")


def write_zones(db, seed):
    """zones, maps and the sub-zone boxes of each map."""
    zones = seed["zones"]
    a2m = {as_int(k): v for k, v in zones["areaToMap"].items()}
    names = {as_int(k): v for k, v in (zones.get("names") or {}).items()}
    db.executemany("INSERT INTO zones VALUES (?,?,?,?)",
                   [(as_int(k), names.get(as_int(k)), a2m.get(as_int(k)), as_int((zones.get("parent") or {}).get(k)))
                    for k in set(list(zones["areaToMap"]) + list(zones.get("names") or {})) if as_int(k) is not None])
    db.executemany("INSERT INTO maps VALUES (?,?,?,?)",
                   [(as_int(k), m.get("n"), m.get("p"), m.get("area")) for k, m in seed.get("maps", {}).items()])
    # sub-zone bounding boxes per map (0-100 map percent) for "where is this" descriptions
    db.executemany("INSERT INTO map_areas VALUES (?,?,?,?,?,?,?)",
                   [(as_int(k), a[0], names.get(as_int(a[0])), a[1] * 100, a[2] * 100, a[3] * 100, a[4] * 100)
                    for k, m in seed.get("maps", {}).items() for a in m.get("areas", []) if len(a) >= 5])


def write_rxp_guides(db, free_guides, rxp_texts, rxp_steps_sv):
    """rxp_guides and rxp_steps (plus the steps the Glide addon saw RestedXP parse). Returns the number of guides.

    Every source offers guide texts with a time; per guide the newest wins:
      free Forever guides from the installed addon (file mtime = when this RestedXP version was installed),
      else from the seed snapshot (time 0: only when the addon isn't readable);
      texts the Glide addon captured in game (capture time) and RestedXP's own compressed cache (file time)."""
    guides, best = {}, {}

    def offer(meta, steps, source, body, t):
        key = "%s||%s" % ((meta.get("group") or "?").split("<<")[0].strip() or "?", meta.get("name") or source)
        cand = {"text": body, "_t": t}
        if newer(cand, best.get(key)):
            best[key] = cand
            guides[key] = (meta, steps, source, body)

    for path, text, t in free_guides:
        for chunk in re.split(r"RXPGuides\.RegisterGuide\(\[\[", text)[1:]:
            body = chunk.split("]])", 1)[0]
            meta, steps = parse_rxp(body)
            offer(meta, steps, "rxp-free:" + path, body, t)
    for _key, v in rxp_texts.items():
        meta, steps = parse_rxp(v["text"])
        offer(meta, steps, "owned:" + str(v.get("how")), v["text"], v.get("_t") or 0)
    n_owned = sum(1 for g in guides.values() if g[2].startswith("owned:"))
    log("RestedXP guides: %d in total: %d premium (your account's guides from RestedXP's cache and in-game captures), "
        "%d free (starter zones and dungeons shipped with the addon)" % (len(guides), n_owned, len(guides) - n_owned))
    srows, grows = [], []
    for key, (meta, steps, source, body) in guides.items():
        meta["group"] = (meta.get("group") or "").split("<<")[0].strip() or None
        key = "%s||%s" % (meta.get("group") or "?", meta.get("name") or key)
        grows.append((key, meta.get("name"), meta.get("group"), meta.get("next"), source, body))
        for si, st in enumerate(steps, 1):
            for li, ln in enumerate(st["lines"], 1):
                qid = None
                if ln["verb"] in ("accept", "turnin", "complete", "daily", "dailyturnin"):
                    m = re.match(r"(\d+)", ln["args"])
                    qid = int(m.group(1)) if m else None
                g = parse_goto(ln["args"]) if ln["verb"] in ("goto", "waypoint") else {}
                srows.append((key, si, li, st["filters"], ln["verb"], ln["args"], qid, g.get("ui_map"), g.get("zone"),
                              g.get("x"), g.get("y"), g.get("wx"), g.get("wy"), ln["text"], ln["filter"],
                              ln["comment"]))
    db.executemany("INSERT INTO rxp_guides VALUES (?,?,?,?,?,?)", grows)
    db.executemany("INSERT INTO rxp_steps VALUES (%s)" % ",".join("?" * 16), srows)
    if rxp_steps_sv:
        db.executemany("INSERT INTO obs_misc VALUES (?,?,?,?)",
                       [(None, "rxp_parsed_steps", k, json.dumps(v)) for k, v in rxp_steps_sv.items()])
    return len(guides)


def write_sessions(db, sessions, session_files):
    """sessions and what the recorder observed in them: obs_quests, obs_xp, obs_kills, obs_trail, obs_events,
    obs_misc (taxi, trainers, vendors, gossip, loot) and the quest scan."""
    for sid, s in sessions.items():
        c = s.get("char") or {}
        db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (sid, c.get("name"), c.get("realm"), c.get("class"), c.get("race"), c.get("faction"),
                    c.get("level"), s.get("build"), s.get("started"), s.get("ended"),
                    os.path.basename(session_files.get(sid, ""))))
        for qid, q in kv(s.get("quests")):
            a, t = q.get("accepted") or {}, q.get("turned") or {}
            rx = t.get("rxp") or {}
            if a or t:
                db.execute("INSERT INTO obs_quests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (sid, as_int(qid), (q.get("offeredAt") or {}).get("t"), a.get("t"), a.get("lvl"),
                            t.get("t"), t.get("lvl"), t.get("xp"), t.get("money"), q.get("removed"),
                            rx.get("guide"), rx.get("step")))
        db.executemany("INSERT INTO obs_xp VALUES (?,?,?,?,?,?)",
                       [(sid, x.get("t"), x.get("lvl"), x.get("xp"), x.get("max"), x.get("rest"))
                        for x in as_list(s.get("xp"))])
        db.executemany("INSERT INTO obs_kills VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       [(sid, k.get("t"), k.get("id"), k.get("n"), k.get("xp"), k.get("rest"), k.get("ml"),
                         k.get("lvl"), k.get("m"), k.get("x"), k.get("y")) for k in as_list(s.get("kills"))])
        db.executemany("INSERT INTO obs_trail VALUES (?,?,?,?,?,?)",
                       [(sid, *p[:5]) for p in as_list(s.get("trail")) if isinstance(p, list) and len(p) >= 5])
        db.executemany("INSERT INTO obs_events VALUES (?,?,?,?,?,?,?,?)",
                       [(sid, e.get("t"), e.get("e"), e.get("lvl"), e.get("m"), e.get("x"), e.get("y"),
                         json.dumps({k: v for k, v in e.items() if k not in ("t", "e", "lvl", "m", "x", "y")}))
                        for e in as_list(s.get("events"))])
        for kind in ("taxi", "trainers", "vendors", "gossip", "loot"):
            val = s.get(kind) or {}
            items = val.items() if isinstance(val, dict) else enumerate(val)
            db.executemany("INSERT INTO obs_misc VALUES (?,?,?,?)",
                           [(sid, kind, str(k), json.dumps(v, default=str)) for k, v in items])
        for qid, r in kv(s.get("scan")):
            if not isinstance(r, dict):
                continue
            db.execute("INSERT OR REPLACE INTO scan VALUES (?,?,?,?,?,?)",
                       (as_int(qid), r.get("t"), r.get("l"), r.get("m"), r.get("ok"), sid))


def build(args):
    """Build glide.db from every source (see the module docstring), unless no input changed since the last build."""
    t0 = time.time()
    inputs = gather_inputs(args)
    out, fingerprint = inputs["out"], inputs["fingerprint"]
    if up_to_date(args, out, fingerprint):
        return
    seed = json.load(gzip.open(os.path.join(ROOT, "data", "seed", "seed.json.gz"), "rt", encoding="utf-8"))
    free_src, free_guides = free_guide_texts(inputs, seed)
    w = load_wdb(inputs["wdb_dirs"])
    sessions, session_files, rxp_texts, rxp_steps_sv = load_sessions(inputs["sv_files"])
    for k, v in rxp_from_own_sv(inputs["rxp_sv_files"]).items():
        if newer(v, rxp_texts.get(k)):
            rxp_texts[k] = v
    log("wdb quests:", len(w["quests"]), "| sessions:", len(sessions), "| owned RXP guide texts:", len(rxp_texts))
    book = quest_book(seed, sessions, w)

    # build in the local temp dir (SQLite can't lock files on some synced/mounted folders), then copy
    tmp = os.path.join(tempfile.gettempdir(), "glide_build_%d.db" % os.getpid())
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)
    n_quests = write_quests(db, book, seed)
    write_entities(db, seed, w, sessions)
    write_zones(db, seed)
    n_guides = write_rxp_guides(db, free_guides, rxp_texts, rxp_steps_sv)
    write_sessions(db, sessions, session_files)

    meta = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "wdb_quests": len(w["quests"]),
            "sessions": len(sessions), "rxp_guides": n_guides, "quests": n_quests,
            "seed_counts": json.dumps(seed["meta"]["counts"]), "rxp_version": inputs["rxp_ver"] or "",
            "rxp_free_source": free_src, "input_fingerprint": fingerprint}
    db.executemany("INSERT INTO meta VALUES (?,?)", [(k, str(v)) for k, v in meta.items()])
    db.commit()
    db.close()
    # staged copy renamed over the target (as build_kb does): the engine may be reading the old glide.db, and an
    # interrupted copy must not leave a torn one
    written = kstore.install(tmp, out, log, what="quest database")
    if written == kstore.pending_path(out):
        log("The companion installs %s over %s when it next starts." % (os.path.basename(written),
                                                                        os.path.basename(out)))
    elif written == out:
        try:
            os.remove(kstore.pending_path(out))     # an older glide.new.db from a build while glide.db was in use
        except OSError:
            pass
    log("wrote %s in %.1fs: %s" % (written, time.time() - t0, json.dumps(meta)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--wow", help="WoW Forever client folder (the one containing Cache and WTF)")
    ap.add_argument("--out")
    ap.add_argument("--force", action="store_true", help="rebuild even when no input changed")
    ap.add_argument("--compare", help="also up to date when this glide.db has the same inputs (the companion's "
                                      "background refresh writes data/glide.next.db and compares with data/glide.db)")
    build(ap.parse_args())
