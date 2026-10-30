#!/usr/bin/env python3
"""Build data/seed/seed.json.gz from community WoW Forever datasets (cloud-side, needs lupa).

Every source is kept separate so build_db.py can merge them with explicit precedence and
provenance. Sources (all cloned from GitHub; their licenses: THIRD_PARTY_NOTICES.md):
  fg       prezus/ForeverGuide Data/*.lua   Questie Classic + Forever overlay (quests, npcs, objects)
  (alcaras/forever-ref's QuestieDB copy, collect observations, Wowhead harvests and maps were dropped on
   2026-10-30: that repository publishes no license, and Wowhead's terms forbid redistributing its data)
  rxp      RestedXP/RXPGuides free Forever guides (raw text; CC BY-NC-SA 4.0)
  fqm      Swafox/ForeverQuestMarker Data/*.lua  quest ID sets: datamined new-in-Forever (Forever client QuestV2
           vs Classic Era, via wago.tools), community-confirmed new, changed Classic quests (optional)
  ids      quest-ID sweep list: union of every known Forever/Classic quest ID
"""
import glob, gzip, json, os, re, sys
import lua_sandbox

SRC = sys.argv[1] if len(sys.argv) > 1 else "/home/claude"
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), "..", "data", "seed")
os.makedirs(OUT, exist_ok=True)
lua = lua_sandbox.runtime()           # other projects' Lua files run with no way out of Lua (lua_sandbox.py)
load_chunk = lua_sandbox.loader(lua)


def to_py(v):
    """Lua value -> JSON-able Python (tables with only 1..n keys become lists)."""
    if lua.eval("type")(v) != "table":
        return v
    d = {}
    for k, x in v.items():
        d[k] = to_py(x)
    if d and all(isinstance(k, int) for k in d) and sorted(d) == list(range(1, len(d) + 1)):
        return [d[i] for i in range(1, len(d) + 1)]
    return {str(k): x for k, x in d.items()}


def load_ns(path):
    """Run an addon data file with `local _, ns = ...` and return ns."""
    chunk = load_chunk(open(path, encoding="utf-8").read(), "@" + os.path.basename(path))
    if chunk is None:
        raise SystemExit("cannot load " + path)
    ns = lua.table()
    chunk("FG", ns)
    return ns


def packed_db(ns_tbl):
    """ForeverGuide packs each record as a Lua constructor string."""
    dec = lua.eval("function(s) return load('return '..s, 'rec', 't', {})() end")
    out = {}
    for k, s in ns_tbl.items():
        out[str(k)] = to_py(dec(s))
    return out


def js_obj(path):
    txt = open(path, encoding="utf-8").read()
    return json.loads(txt.split("=", 1)[1].strip().rstrip(";"))


seed = {"meta": {}, "sources": {}}
fg = os.path.join(SRC, "prezus_ForeverGuide", "Data")

# --- ForeverGuide ------------------------------------------------------------------------------
q = load_ns(os.path.join(fg, "QuestDB.lua")).QuestDB
seed["sources"]["fg_quests"] = packed_db(q)
seed["sources"]["fg_npcs"] = packed_db(load_ns(os.path.join(fg, "NpcDB.lua")).NpcDB)
seed["sources"]["fg_objects"] = packed_db(load_ns(os.path.join(fg, "ObjectDB.lua")).ObjectDB)
seed["sources"]["fg_items"] = packed_db(load_ns(os.path.join(fg, "ItemDB.lua")).ItemDB)
zdb = load_ns(os.path.join(fg, "ZoneDB.lua")).ZoneDB
seed["zones"] = {k: to_py(zdb[k]) for k in ("areaToMap", "names", "parent", "mapNames", "mapParent")
                 if zdb[k] is not None}
# areaToMap has [0]=0 plus a 1..4 array part; normalise to str keys
a2m = {}
for k, v in zdb.areaToMap.items():
    a2m[str(k)] = v
seed["zones"]["areaToMap"] = a2m
idns = load_ns(os.path.join(fg, "ForeverQuestIDs.lua"))
new_ranges = to_py(idns.ForeverNewQuestIDRanges)
seed["meta"]["fg_counts"] = to_py(idns.ForeverQuestCounts)

# --- RestedXP free Forever guides (raw text) ---------------------------------------------------
rxp = {}
for p in sorted(glob.glob(os.path.join(SRC, "RestedXP_RXPGuides", "Guides", "[Ff]orever", "**", "*.lua"),
                          recursive=True)):
    rxp[os.path.relpath(p, os.path.join(SRC, "RestedXP_RXPGuides", "Guides"))] = open(p, encoding="utf-8").read()
seed["sources"]["rxp_free"] = rxp

# --- ForeverQuestMarker quest ID sets (optional) -----------------------------------------------
B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def fqm_sets(path):
    """{set name: sorted ids} from ForeverQuestMarker's generated Data files (bitset segments, LSB first)."""
    out = {}
    for f in sorted(glob.glob(os.path.join(path, "Data", "*.lua"))):
        text = open(f, encoding="utf-8").read()
        for m in re.finditer(r"ns\.Data\.Sets\.(\w+)\s*=\s*\{(.*?)\n\}", text, re.S):
            name, body = m.group(1), m.group(2)
            ids = set()
            for seg in re.finditer(r"\{\s*(\d+),\s*\"([A-Za-z0-9+/]*)\"\s*\}", body):
                start, bits = int(seg.group(1)), seg.group(2)
                for ci, ch in enumerate(bits):
                    v = B64.index(ch)
                    for b in range(6):
                        if v >> b & 1:
                            ids.add(start + ci * 6 + b)
            loose = re.search(r"ids\s*=\s*\{([^}]*)\}", body)
            if loose:
                ids.update(int(x) for x in re.findall(r"\d+", loose.group(1)))
            out[name] = sorted(ids)
    meta = {}
    mp = os.path.join(path, "Data", "Meta.lua")
    if os.path.exists(mp):
        mt = open(mp, encoding="utf-8").read()
        for k in ("questieDB", "foreverBuild", "eraBuild"):
            mm = re.search(k + r'\s*=\s*"([^"]+)"', mt)
            if mm:
                meta[k] = mm.group(1)
    return out, meta


fqm_dir = os.path.join(SRC, "Swafox_ForeverQuestMarker")
fqm, fqm_meta = fqm_sets(fqm_dir) if os.path.isdir(fqm_dir) else ({}, {})
seed["sources"]["fqm"] = {"sets": fqm, "meta": fqm_meta}

# --- quest ID sweep list -----------------------------------------------------------------------
ids = set()
for r in new_ranges:
    ids.update(range(r[0], r[1] + 1))
for k, _rec in seed["sources"]["fg_quests"].items():
    ids.add(int(k))
lode = open(os.path.join(SRC, "danielcosta42_lodestar", "ForeverData.lua"), encoding="utf-8").read()
m = re.search(r'ns\.foreverUnknown\s*=\s*"([0-9,]+)"', lode)
lode_ids = [int(x) for x in m.group(1).split(",")] if m else []
ids.update(lode_ids)
for g in rxp.values():
    ids.update(int(x) for x in re.findall(r"\.(?:accept|turnin|complete)\s+(\d+)", g))
ids.update(fqm.get("datamined") or ())
removed = {int(k) for k, r in seed["sources"]["fg_quests"].items() if r.get("removed")}
seed["ids"] = {"all": sorted(ids), "removed_in_forever": sorted(removed),
               "forever_new": sorted(set(lode_ids) | {int(k) for k, r in seed["sources"]["fg_quests"].items()
                                                      if r.get("forever")}
                                     | set(fqm.get("datamined") or ()) | set(fqm.get("community") or ())),
               "forever_changed": sorted(set(fqm.get("changed") or ()))}
seed["meta"]["counts"] = {k: len(v) for k, v in seed["sources"].items() if hasattr(v, "__len__")}
seed["meta"]["counts"]["ids_all"] = len(ids)

path = os.path.join(OUT, "seed.json.gz")
with gzip.open(path, "wt", encoding="utf-8") as fh:
    json.dump(seed, fh, separators=(",", ":"))
print(path, os.path.getsize(path), json.dumps(seed["meta"]["counts"]))

# --- addon sweep list: compressed as ranges (Lua) ----------------------------------------------
srt = sorted(ids - removed)
ranges, start, prev = [], srt[0], srt[0]
for i in srt[1:]:
    if i == prev + 1:
        prev = i
        continue
    ranges.append((start, prev)); start = prev = i
ranges.append((start, prev))
newset = set(seed["ids"]["forever_new"])
lua_out = os.path.join(os.path.dirname(__file__), "..", "addon", "Glide", "Data", "QuestIDs.lua")
with open(lua_out, "w", encoding="utf-8", newline="\n") as fh:
    fh.write("-- Generated by tools/build_seed.py. Every known Classic/Forever quest ID (%d), as ranges.\n" % len(srt))
    fh.write("local _, ns = ...\nns.QuestIDRanges = {\n")
    for i in range(0, len(ranges), 12):
        fh.write("".join("{%d,%d}," % r for r in ranges[i:i + 12]) + "\n")
    fh.write("}\n-- IDs believed new in Forever (scan these first)\nns.ForeverNewIDs = {\n")
    nl = sorted(newset)
    for i in range(0, len(nl), 20):
        fh.write(",".join(str(x) for x in nl[i:i + 20]) + ",\n")
    fh.write("}\n")
print(lua_out, len(ranges), "ranges,", len(newset), "new")
