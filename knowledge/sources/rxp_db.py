"""Loader 3: RestedXP's DB files (DB/forever/*.lua and DB/*.lua) -> kb.

Files are read through ctx.rxp(): reference/rxp/DB (repo copy) when present, else the installed addon's
<wow_dir>/Interface/AddOns/RXPGuides/DB (the player's PC). The result's `from` names the folder used.

  flightData.lua   addon.FPDB[faction][from][to] = seconds, ["name"]    -> flight_path, poi(kind flight)
  db.lua           addon.taxiPos[continent][node] = {wx, wy, flag}       -> poi flight continent/wx/wy/faction
                   addon.mapId, FPbyZone, skipPreReq, emergency*, dungeon* ... -> rxp_meta
  SpiritHealerDB   addon.SpiritHealerWorld[continent] = {id, wx, wy, name} -> poi(kind graveyard)
  dangerousMobs    [faction][zone][mob] = {{MinLevel, MaxLevel, Classification, Movement, Notes, Location}}
                   -> creature_note (every entry), creature danger_notes/movement/abilities (name match),
                      creature_spawn from `.pin Zone,x,y` / `.line Zone,x1,y1,...` when the id is known
  spells.lua       s[CLASS|Race][level] = {spell ids -- comment}       -> spell (name NULL: the files only
                   carry RXP's lower-case comments, kept in notes)
  rares.lua        addon.rares[zone] = {names} (enUS branch)            -> creature rank 4 (2 if elite)
  quest.lua        skipPreReq, questCompleteItems, questTurnInItems, questAcceptItems -> rxp_meta
  DB/questDB.lua   quest-log UI code: no quest names/ids; its hearth turn-in `locations` -> poi(kind hearth)
  Talents.lua      the talent-guide engine only (builds are registered by separate guide files that are not
                   in reference/): talent_build stays empty
  Tips.lua         logic only (reads emergencyItems/emergencySpells/dangerousMobs): nothing to load
World coordinates use the store convention (wx = world X/north, wy = world Y/west), which is also how
SpiritHealerWorld and taxiPos name them.
"""
import collections
import os
import re

from knowledge import store
from knowledge.values import as_int
from knowledge.sources import _lua

CONF = store.SOURCE_CONF["rxp"]
FLAG_FACTION = {1: "Alliance", 2: "Horde", 3: "Both"}
RACES = {"Dwarf", "Gnome", "Human", "NightElf", "Orc", "Troll", "Tauren", "Scourge", "Draenei", "BloodElf"}
PROFESSION_WORDS = ("cooking", "first aid", "fa", "fishing", "mining", "herbalism", "skinning", "alchemy",
                    "blacksmithing", "enchanting", "engineering", "leatherworking", "tailoring")
TABLE_LOADED_ELSEWHERE = {"addon.FPDB", "addon.SpiritHealerWorld", "dangerousMobs", "addon.dangerousMobs"}
DATA_LOCALS = {"dungeonList", "alternateNames", "professionGuides", "professionGuideIndex", "locations", "HSloc"}


def load(conn, ctx):
    # every file through ctx.rxp: reference/rxp/DB (repo copy) or the installed addon's DB folder (player's PC)
    if not ctx.rxp_available("DB"):
        return {"skipped": "no RestedXP DB folder (reference/rxp/DB, or Interface/AddOns/RXPGuides/DB in the "
                           "config.json wow_dir)"}
    res = {"from": ctx.rxp("DB")}
    files = {}
    for rel in ("forever/db.lua", "forever/quest.lua", "forever/flightData.lua", "forever/SpiritHealerDB.lua",
                "forever/dangerousMobs.lua", "forever/rares.lua", "forever/spells.lua", "shared.lua", "questDB.lua"):
        path = ctx.rxp("DB", *rel.split("/"))
        if os.path.exists(path):
            files[rel] = _lua.read_file(path)
    zones = ZoneIndex(conn, (files.get("forever/db.lua") or {}).get("addon.mapId") or {})
    res["rxp_meta"] = load_meta(conn, files)
    res["flight"] = load_flights(conn, files, zones)
    res["graveyard"] = load_graveyards(conn, files, zones)
    res["dangerous_mobs"] = load_dangerous(conn, files, zones)
    res["spells"] = load_spells(conn, ctx.rxp("DB", "forever", "spells.lua"))
    res["rares"] = load_rares(conn, files)
    res["hearth"] = load_hearth(conn, files, zones)
    res["talents"] = "Talents.lua is the talent-guide engine; no builds embedded (guides are separate files) -> talent_build empty"
    res["tips"] = "Tips.lua is logic only (uses emergencyItems/emergencySpells/dangerousMobs, loaded elsewhere)"
    res["parse_errors"] = {k: v.get("__errors__") for k, v in files.items() if v.get("__errors__")}
    if res["parse_errors"].get("questDB.lua"):   # UI option tables with functions: expected, not data
        res["parse_errors"]["questDB.lua"] = "%d UI tables with functions skipped" % len(res["parse_errors"]["questDB.lua"])
    return res


class ZoneIndex:
    """Zone / map name -> UiMap: RestedXP's addon.mapId first, then the kb map table."""

    def __init__(self, conn, map_id):
        self.names = {}
        for name, um in conn.execute("SELECT name, ui_map FROM map WHERE name IS NOT NULL ORDER BY ui_map DESC"):
            self.names[name.lower()] = um        # lowest UiMap wins for duplicate names (e.g. Zephras Isle)
        for name, um in map_id.items():
            if isinstance(name, str) and as_int(um):
                self.names[name.lower()] = as_int(um)

    def get(self, name):
        if name is None:
            return None
        if isinstance(name, (int, float)) or str(name).strip().isdigit():
            return as_int(name)
        n = str(name).strip().lower()
        if n in self.names:
            return self.names[n]
        n = re.sub(r"\s*\(.*?\)\s*", "", n).strip()           # "The Barrens (Stagnant Oasis)"
        return self.names.get(n)


# ------------------------------------------------------------------------------------------------
def load_meta(conn, files):
    rows, seen = {}, {}
    for rel, d in files.items():
        stem = os.path.splitext(os.path.basename(rel))[0]
        for lv, v in d.items():
            if lv == "__errors__" or lv in TABLE_LOADED_ELSEWHERE or v in ({}, [], None):
                continue
            if not lv.startswith("addon.") and lv not in DATA_LOCALS:      # code helpers, not data
                continue
            if rel == "questDB.lua" and lv not in ("locations", "HSloc"):
                continue
            if rel == "forever/spells.lua" and lv != "addon.HCSpellList":
                continue
            key = lv[6:] if lv.startswith("addon.") else lv
            if rel == "questDB.lua":
                key = "questDB." + key
            js = store.jdump(v)
            if key in seen and seen[key] != js:
                key = "%s.%s" % (stem, key)
            if key in rows:
                continue
            seen[key] = js
            rows[key] = js
    store.insert_rows(conn, "rxp_meta", list(rows.items()), ["key", "json"], verb="INSERT OR REPLACE")
    return {"rows": len(rows), "keys": sorted(rows)}


def load_flights(conn, files, zones):
    fpdb = (files.get("forever/flightData.lua") or {}).get("addon.FPDB") or {}
    dbl = files.get("forever/db.lua") or {}
    taxi = dbl.get("addon.taxiPos") or {}
    by_zone = dbl.get("addon.FPbyZone") or {}
    paths, nodes = [], {}
    for faction, table in fpdb.items():
        if not isinstance(table, dict):
            continue
        for frm, edges in table.items():
            if not isinstance(edges, dict) or as_int(frm) is None:
                continue
            n = nodes.setdefault(as_int(frm), {"name": None, "factions": set()})
            n["name"] = n["name"] or edges.get("name")
            n["factions"].add(faction)
            for to, secs in edges.items():
                if isinstance(to, int) and isinstance(secs, (int, float)):
                    paths.append((faction, as_int(frm), to, float(secs), None, "rxp", CONF))
                    nodes.setdefault(to, {"name": None, "factions": set()})
    pos = {}
    for cont, tbl in taxi.items():
        for node, p in (tbl or {}).items():
            if isinstance(p, dict) and as_int(node) is not None:
                pos[as_int(node)] = (as_int(cont), p.get("wx"), p.get("wy"), FLAG_FACTION.get(p.get("flag")))
                nodes.setdefault(as_int(node), {"name": None, "factions": set()})
    zone_of = {}
    for _faction, tbl in by_zone.items():
        for um, node in (tbl or {}).items():
            zone_of.setdefault(as_int(node), set()).add(as_int(um))
    rows = []
    for node, n in nodes.items():
        cont, wx, wy, fac = pos.get(node, (None, None, None, None))
        if fac is None and n["factions"]:
            fac = "Both" if len(n["factions"]) > 1 else next(iter(n["factions"]))
        um = zones.get(n["name"].rsplit(",", 1)[-1]) if n["name"] and "," in n["name"] else None
        if um is None and len(zone_of.get(node, ())) == 1:
            um = next(iter(zone_of[node]))
        rows.append({"kind": "flight", "id": node, "name": n["name"], "faction": fac, "ui_map": um,
                     "continent": cont, "wx": wx, "wy": wy, "src": "rxp", "conf": CONF})
    store.insert_rows(conn, "poi", rows, verb="INSERT OR REPLACE")
    store.insert_rows(conn, "flight_path", paths, ["faction", "from_id", "to_id", "time_s", "cost", "src", "conf"],
                      verb="INSERT OR REPLACE")
    return {"flight_path": len(paths), "nodes": len(rows), "with_world_pos": sum(1 for r in rows if r["wx"] is not None),
            "fpdb_edges": sum(len([k for k in e if isinstance(k, int)]) for t in fpdb.values() if isinstance(t, dict)
                              for e in t.values() if isinstance(e, dict))}


def load_graveyards(conn, files, zones):
    world = (files.get("forever/SpiritHealerDB.lua") or {}).get("addon.SpiritHealerWorld") or {}
    rows = {}
    for cont, lst in world.items():
        for g in (lst.values() if isinstance(lst, dict) else lst or []):
            if not isinstance(g, dict) or as_int(g.get("id")) is None:
                continue
            name = (g.get("name") or "").strip() or None
            zone = name.split(",")[0] if name else None
            rows[as_int(g["id"])] = {"kind": "graveyard", "id": as_int(g["id"]), "name": name, "faction": None,
                                   "ui_map": zones.get(zone), "continent": as_int(cont), "wx": g.get("wx"),
                                   "wy": g.get("wy"), "src": "rxp", "conf": CONF}
    store.insert_rows(conn, "poi", list(rows.values()), verb="INSERT OR REPLACE")
    return {"rows": len(rows), "with_zone": sum(1 for r in rows.values() if r["ui_map"])}


# ------------------------------------------------------------------------------------------------
# dangerous mobs
# ------------------------------------------------------------------------------------------------
class CreatureNames:
    def __init__(self, conn):
        self.by_name = collections.defaultdict(list)
        for cid, name in conn.execute("SELECT id, name FROM creature WHERE name IS NOT NULL"):
            self.by_name[name.strip().lower()].append(cid)
        self.maps = collections.defaultdict(set)
        for cid, um in conn.execute("SELECT DISTINCT creature_id, ui_map FROM creature_spawn"):
            self.maps[cid].add(um)

    def resolve(self, name, ui_map=None):
        """All ids with that name, narrowed to those spawning on ui_map when that leaves any."""
        ids = self.by_name.get((name or "").strip().lower(), [])
        if ui_map and len(ids) > 1:
            here = [i for i in ids if ui_map in self.maps.get(i, ())]
            if here:
                ids = here
        return ids


_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def parse_location(loc, zones):
    """'.pin Zone,x,y;.line Zone,x1,y1,...;.mob Name' -> [(kind, ui_map, [(x, y), ...])]."""
    out = []
    for part in re.split(r"[;\r\n]+", loc or ""):
        part = part.strip()
        m = re.match(r"^\.(pin|line)\s+([^,]+),(.*)$", part)
        if not m:
            continue
        nums = [float(x) for x in _NUM.findall(m.group(3))]
        pts = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
        pts = [p for p in pts if 0 <= p[0] <= 100 and 0 <= p[1] <= 100]
        if pts:
            out.append((m.group(1), zones.get(m.group(2)), pts))
    return out


def abilities_from(notes):
    parts = [p.strip(" .") for p in re.split(r"[,;./]|\band\b", notes or "") if p.strip(" .")]
    return [p for p in parts if len(p.split()) <= 6]


def load_dangerous(conn, files, zones):
    data = (files.get("forever/dangerousMobs.lua") or {}).get("dangerousMobs") or {}
    names = CreatureNames(conn)
    notes, spawns = [], []
    per_creature = collections.defaultdict(lambda: {"notes": [], "movement": [], "abilities": [], "min": [], "max": []})
    matched_entries = 0
    for faction, zones_tbl in data.items():
        for zone, mobs in (zones_tbl or {}).items():
            zone_map = zones.get(zone)
            for mob, entries in (mobs or {}).items():
                for e in (entries.values() if isinstance(entries, dict) else entries or []):
                    if not isinstance(e, dict):
                        continue
                    mob_name = (e.get("Name") or (mob if isinstance(mob, str) else "")).strip() or None
                    locs = parse_location(e.get("Location"), zones)
                    loc_map = next((l[1] for l in locs if l[1]), zone_map)
                    ids = names.resolve(mob_name, loc_map) if mob_name else ([mob] if isinstance(mob, int) else [])
                    cid = ids[0] if len(ids) == 1 else None
                    notes.append({"name": mob_name, "zone": zone, "faction": faction, "creature_id": cid,
                                  "min_level": as_int(e.get("MinLevel")), "max_level": as_int(e.get("MaxLevel")),
                                  "classification": e.get("Classification"), "movement": e.get("Movement"),
                                  "notes": e.get("Notes"), "location": e.get("Location"), "src": "rxp"})
                    if ids:
                        matched_entries += 1
                    for i in ids:
                        pc = per_creature[i]
                        for key, val in (("notes", e.get("Notes")), ("movement", e.get("Movement"))):
                            if val and val.strip() and val.strip() not in pc[key]:
                                pc[key].append(val.strip())
                        for a in abilities_from(e.get("Notes")):
                            if a not in pc["abilities"]:
                                pc["abilities"].append(a)
                        pc["min"].append(as_int(e.get("MinLevel")))
                        pc["max"].append(as_int(e.get("MaxLevel")))
                    if cid:
                        for kind, um, pts in locs:
                            if um is None:
                                continue
                            mv = e.get("Movement") if kind == "pin" else "line:%s" % (e.get("Movement") or "path")
                            for x, y in pts:
                                spawns.append((cid, um, x, y, 1, mv, "rxp", CONF))
    store.insert_rows(conn, "creature_note", notes)
    store.insert_rows(conn, "creature_spawn", spawns, ["creature_id", "ui_map", "x", "y", "n", "movement", "src", "conf"])
    upd = 0
    for cid, pc in per_creature.items():
        mins = [m for m in pc["min"] if m is not None]
        maxs = [m for m in pc["max"] if m is not None]
        conn.execute("""UPDATE creature SET danger_notes=?, movement=?, abilities=?,
                        min_level=COALESCE(min_level, ?), max_level=COALESCE(max_level, ?),
                        src=CASE WHEN src LIKE '%rxp%' THEN src ELSE src || ',rxp' END WHERE id=?""",
                     (" | ".join(pc["notes"]) or None, " | ".join(pc["movement"]) or None,
                      store.jdump(pc["abilities"]) if pc["abilities"] else None,
                      min(mins) if mins else None, max(maxs) if maxs else None, cid))
        upd += 1
    return {"creature_note": len(notes), "entries_matched": matched_entries, "creatures_updated": upd,
            "spawns_from_locations": len(spawns)}


# ------------------------------------------------------------------------------------------------
# spells, rares, hearth locations
# ------------------------------------------------------------------------------------------------
def parse_spells(path):
    """[(key, level, spell_id, comment)] from spells.lua, keeping the per-line comments."""
    out, key, level = [], None, None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            s = raw.strip()
            if not s or s.startswith("--"):
                continue
            m = re.match(r'^s\["(\w+)"\]\s*=\s*\{', s)
            if m:
                key, level = m.group(1), None
                continue
            code, _, comment = s.partition("--")
            m = re.match(r"^\[(\d+)\]\s*=\s*\{(.*)$", code.strip())
            if m:
                level, code = int(m.group(1)), m.group(2)
            if key is None or level is None:
                continue
            for sid in re.findall(r"\b(\d+)\b", code):
                out.append((key, level, int(sid), comment.strip() or None))
    return out


def spell_kind(key, comment):
    c = (comment or "").lower()
    if "riding" in c or "piloting" in c:
        return "riding"
    if any(re.search(r"\b%s\b" % re.escape(w), c) for w in PROFESSION_WORDS):
        return "profession"
    return "racial" if key in RACES else "class"


def load_spells(conn, path):
    if not os.path.exists(path):
        return {"skipped": "no spells.lua"}
    rows = {}
    for key, level, sid, comment in parse_spells(path):
        kind = spell_kind(key, comment)
        rank = re.search(r"\br(\d+)\b", comment or "")
        cur = rows.get(sid)
        if cur is None:
            cur = rows[sid] = {"id": sid, "name": None, "rank": int(rank.group(1)) if rank else None,
                               "class": None, "race": None, "level": level, "cost": None, "kind": kind,
                               "notes": comment, "src": "rxp"}
        if key in RACES:
            cur["race"] = store.merge_src(cur["race"], key)
        else:
            cur["class"] = store.merge_src(cur["class"], key)
        cur["level"] = min(cur["level"], level)
    store.insert_rows(conn, "spell", list(rows.values()), verb="INSERT OR REPLACE")
    kinds = collections.Counter(r["kind"] for r in rows.values())
    return {"rows": len(rows), "kinds": dict(kinds)}


def load_rares(conn, files):
    rares = (files.get("forever/rares.lua") or {}).get("addon.rares") or {}
    names = CreatureNames(conn)
    zones = ZoneIndex(conn, (files.get("forever/db.lua") or {}).get("addon.mapId") or {})
    matched, missing = 0, []
    for zone, lst in rares.items():
        for name in (lst if isinstance(lst, list) else list((lst or {}).values())):
            ids = names.resolve(name, zones.get(zone))
            if not ids:
                missing.append(name)
                continue
            matched += 1
            for cid in ids:
                conn.execute("""UPDATE creature SET rank=CASE WHEN rank IN (2, 4) THEN rank WHEN rank IN (1, 3) THEN 2
                                ELSE 4 END, src=CASE WHEN src LIKE '%rxp%' THEN src ELSE src || ',rxp' END WHERE id=?""",
                             (cid,))
    return {"names": matched + len(missing), "matched": matched, "unmatched": len(missing)}


def load_hearth(conn, files, zones):
    locs = (files.get("questDB.lua") or {}).get("locations") or []
    rows = []
    for i, loc in enumerate(locs if isinstance(locs, list) else list(locs.values()), 1):
        if not isinstance(loc, dict):
            continue
        m = re.match(r"^\.goto\s+([^,]+),\s*(-?[\d.]+),\s*(-?[\d.]+)", loc.get("z") or "")
        rows.append({"kind": "hearth", "id": i, "name": loc.get("name"), "faction": None,
                     "ui_map": zones.get(m.group(1)) if m else None, "x": float(m.group(2)) if m else None,
                     "y": float(m.group(3)) if m else None, "src": "rxp", "conf": CONF})
    store.insert_rows(conn, "poi", rows, verb="INSERT OR REPLACE")
    return len(rows)
