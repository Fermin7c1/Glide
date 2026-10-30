#!/usr/bin/env python3
"""Import client DB2 tables exported as CSV by wow.tools.local (or wago.tools) into the knowledge base.

Put the exports in data/db2/ (file name = table name). Every file is optional; whatever is present is
imported, nothing is ever estimated without its data. Array columns may be written `Name[0]` or `Name_0`
(both are accepted). Expected tables and the columns used:

  QuestXP.csv            ID (= quest level), Difficulty_0 .. Difficulty_9
                         -> quest_xp_table (src db2): full quest XP per level and difficulty index
  TaxiNodes.csv          ID, Name_lang, Pos_0, Pos_1, Pos_2, ContinentID, Flags, MountCreatureID_0 (Horde),
                         MountCreatureID_1 (Alliance)
                         -> poi kind 'flight' (wx = Pos_0 = world X/north, wy = Pos_1 = world Y/west;
                            faction from Flags bit 0x1 Alliance / 0x2 Horde, else from the mount ids)
  TaxiPath.csv           ID, FromTaxiNode, ToTaxiNode, Cost
  TaxiPathNode.csv       PathID, NodeIndex, ContinentID, Loc_0, Loc_1, Loc_2, Delay
                         -> flight_path (src db2) for direct hops: cost; time_s = path length / flight speed,
                            with the speed fitted from hops that also have a RestedXP time (src rxp) and the
                            fixed Delay seconds added; no time when nothing to fit against
  SkillLine.csv          ID, DisplayName_lang, CategoryID (6 weapon, 7 class, 9 secondary, 11 profession)
  SkillLineAbility.csv   SkillLine, Spell, RaceMask, ClassMask, AcquireMethod
  SkillRaceClassInfo.csv SkillID, RaceMask, ClassMask, MinLevel  (skill availability; kept in rxp_meta-style
                         meta 'db2:skill_race_class')
                         -> spell rows kind weapon|profession|riding|racial|class with class/race when the
                            mask names exactly one
  SpellName.csv          ID, Name_lang                     -> spell.name for spells already in the KB
  Spell.csv              ID, NameSubtext_lang ("Rank 2")   -> spell.rank
  SpellLevels.csv        SpellID, BaseLevel, SpellLevel    -> spell.level where unknown
  AreaPOI.csv            ID, Name_lang, Pos_0, Pos_1, ContinentID -> poi kind 'landmark' (towns, camps, landmarks)
  AreaTable.csv          ID, AreaName_lang (or ZoneName), ParentAreaID, ExplorationLevel
                         -> area names / new areas; level_min/level_max = range of ExplorationLevel over the
                            area and its sub-areas
  UiMap.csv              ID, Name_lang, ParentUiMapID, Type -> map names / new maps
  UiMapAssignment.csv    UiMapID, MapID, OrderIndex, UiMin_0, UiMin_1, UiMax_0, UiMax_1, Region_0..Region_5
                         (minX, minY, minZ, maxX, maxY, maxZ) -> map_bounds (src db2, conf 1) for the
                            assignment covering the largest part of each map

    python tools/db2_import.py [--db2 data/db2]               # report what the folder contains
    python tools/db2_import.py --kb data/kb.db [--db2 ...]    # import into an existing KB file
knowledge/sources/db2.py runs the import during build_kb when data/db2/ exists.
"""
import argparse
import collections
import csv
import json
import os
import re
import sqlite3

from knowledge.values import as_float, as_int
from knowledge.vocab import BIT_CLASSES, BIT_RACES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_KIND = {6: "weapon", 7: "class", 9: "profession", 11: "profession"}
TABLES = ("AreaPOI", "QuestXP", "TaxiNodes", "TaxiPath", "TaxiPathNode", "SkillLine", "SkillLineAbility", "SkillRaceClassInfo",
          "SpellName", "Spell", "SpellLevels", "AreaTable", "UiMap", "UiMapAssignment")


def _norm(h):
    return re.sub(r"\[(\d+)\]", r"_\1", (h or "").strip().lstrip("﻿"))


def read_csv(path):
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        rd = csv.reader(fh)
        header = [_norm(h) for h in next(rd, [])]
        return [dict(zip(header, row)) for row in rd]


def inum(v):
    """A CSV cell as an int: '12', '12.0' and '1e3' all read ('' and junk -> None)."""
    return as_int(v, loose=True)


def single(mask, bits):
    mask = inum(mask)
    if not mask or mask < 0:
        return None
    hits = [name for bit, name in bits.items() if mask & bit]
    return hits[0] if len(hits) == 1 else None


def available(folder):
    if not folder or not os.path.isdir(folder):
        return {}
    have = {}
    for f in os.listdir(folder):
        stem, ext = os.path.splitext(f)
        if ext.lower() == ".csv":
            for t in TABLES:
                if stem.lower() == t.lower():
                    have[t] = os.path.join(folder, f)
    return have


def import_dir(conn, folder, log=print):
    """Import every known CSV in `folder` into an open KB connection. Returns counts per table."""
    have = available(folder)
    if not have:
        return {"skipped": "no DB2 CSV exports in %s" % folder}
    data = {t: read_csv(p) for t, p in have.items()}
    res = {"files": sorted(have)}
    if "QuestXP" in data:
        res["quest_xp_table"] = quest_xp(conn, data["QuestXP"])
    if "TaxiNodes" in data:
        res["taxi_nodes"] = taxi_nodes(conn, data["TaxiNodes"])
    if "TaxiPath" in data:
        res["taxi_paths"] = taxi_paths(conn, data["TaxiPath"], data.get("TaxiPathNode"), data.get("TaxiNodes"))
    if "SkillLineAbility" in data:
        res["skill_spells"] = skill_spells(conn, data["SkillLineAbility"], data.get("SkillLine"))
    if "SkillRaceClassInfo" in data:
        rows = [{k: r.get(k) for k in ("SkillID", "RaceMask", "ClassMask", "MinLevel", "Flags")} for r in data["SkillRaceClassInfo"]]
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('db2:skill_race_class', ?)", (json.dumps(rows),))
        res["skill_race_class"] = len(rows)
    res["spell_text"] = spell_text(conn, data.get("SpellName"), data.get("Spell"), data.get("SpellLevels"))
    if "AreaTable" in data:
        res["areas"] = areas(conn, data["AreaTable"])
    if "UiMap" in data:
        res["ui_maps"] = ui_maps(conn, data["UiMap"])
    if "UiMapAssignment" in data:
        res["map_bounds"] = assignments(conn, data["UiMapAssignment"])
    if "AreaPOI" in data:
        res["landmarks"] = area_pois(conn, data["AreaPOI"])
    return res


def area_pois(conn, rows):
    """AreaPOI (named places on the world map: towns, camps, landmarks) -> poi kind 'landmark' (world position;
    finalize places it on its map)."""
    n = 0
    for r in rows:
        pid, name = inum(r.get("ID")), (r.get("Name_lang") or "").strip()
        wx, wy, cont = as_float(r.get("Pos_0")), as_float(r.get("Pos_1")), inum(r.get("ContinentID"))
        if pid is None or not name or wx is None or cont is None:
            continue
        conn.execute("""INSERT OR REPLACE INTO poi(kind, id, name, faction, continent, wx, wy, src, conf)
                        VALUES('landmark', ?, ?, NULL, ?, ?, ?, 'db2', 1.0)""", (pid, name, cont, wx, wy))
        n += 1
    return n


# ------------------------------------------------------------------------------------------------
def quest_xp(conn, rows):
    n = 0
    for r in rows:
        lvl = inum(r.get("ID"))
        if lvl is None:
            continue
        vals = [inum(r.get("Difficulty_%d" % i)) for i in range(10)]
        conn.execute("INSERT OR REPLACE INTO quest_xp_table VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [lvl] + vals + ["db2"])
        n += 1
    return n


def _node_faction(r):
    flags = inum(r.get("Flags"))
    if flags is not None and flags & 3:
        return {1: "Alliance", 2: "Horde", 3: "Both"}[flags & 3]
    h, a = inum(r.get("MountCreatureID_0")), inum(r.get("MountCreatureID_1"))
    if h or a:
        return "Both" if h and a else ("Horde" if h else "Alliance")
    return None


def taxi_nodes(conn, rows):
    n = 0
    for r in rows:
        nid = inum(r.get("ID"))
        if nid is None:
            continue
        name = r.get("Name_lang") or r.get("Name")
        if (name or "").strip().lower().startswith(("zz", "transport", "test", "[ph]", "unused")):
            continue                          # retired nodes and boat / zeppelin transports, not flight masters
        cur = conn.execute("SELECT name, faction FROM poi WHERE kind='flight' AND id=?", (nid,)).fetchone()
        wx, wy, cont = as_float(r.get("Pos_0")), as_float(r.get("Pos_1")), inum(r.get("ContinentID"))
        if cur:
            conn.execute("""UPDATE poi SET name=COALESCE(name, ?), faction=COALESCE(faction, ?), continent=?, wx=?, wy=?,
                            src=CASE WHEN src LIKE '%db2%' THEN src ELSE src || ',db2' END, conf=1.0
                            WHERE kind='flight' AND id=?""", (name, _node_faction(r), cont, wx, wy, nid))
        else:
            conn.execute("""INSERT INTO poi(kind, id, name, faction, continent, wx, wy, src, conf)
                            VALUES('flight',?,?,?,?,?,?,'db2',1.0)""", (nid, name, _node_faction(r), cont, wx, wy))
        n += 1
    return n


def taxi_paths(conn, paths, nodes_rows, taxi_rows):
    length, delay = collections.defaultdict(float), collections.defaultdict(float)
    if nodes_rows:
        by_path = collections.defaultdict(list)
        for r in nodes_rows:
            by_path[inum(r.get("PathID"))].append((inum(r.get("NodeIndex")) or 0, as_float(r.get("Loc_0")), as_float(r.get("Loc_1")),
                                                   as_float(r.get("Loc_2")) or 0.0, as_float(r.get("Delay")) or 0.0))
        for pid, pts in by_path.items():
            pts.sort()
            for p, q in zip(pts, pts[1:]):
                if None not in (p[1], p[2], q[1], q[2]):
                    length[pid] += ((q[1] - p[1]) ** 2 + (q[2] - p[2]) ** 2 + (q[3] - p[3]) ** 2) ** 0.5
            delay[pid] = sum(p[4] for p in pts)
    fac = {}
    for r in taxi_rows or []:
        fac[inum(r.get("ID"))] = _node_faction(r)
    if not fac:
        fac = dict(conn.execute("SELECT id, faction FROM poi WHERE kind='flight'"))
    hops = []
    for r in paths:
        pid, a, b = inum(r.get("ID")), inum(r.get("FromTaxiNode")), inum(r.get("ToTaxiNode"))
        if a is None or b is None:
            continue
        hops.append((pid, a, b, inum(r.get("Cost"))))
    # flight speed fitted from hops that RestedXP timed
    ratios = []
    for pid, a, b, _ in hops:
        if length.get(pid):
            t = conn.execute("SELECT time_s FROM flight_path WHERE from_id=? AND to_id=? AND src LIKE '%rxp%'", (a, b)).fetchone()
            if t and t[0] and t[0] > delay.get(pid, 0):
                ratios.append(length[pid] / (t[0] - delay.get(pid, 0)))
    speed = sorted(ratios)[len(ratios) // 2] if ratios else None
    n = 0
    for pid, a, b, cost in hops:
        factions = [side for side in ("Alliance", "Horde")
                    if all(fac.get(x) in (side, "Both", None) for x in (a, b))]
        est = round(length[pid] / speed + delay.get(pid, 0), 1) if speed and length.get(pid) else None
        for faction in factions:
            cur = conn.execute("SELECT src FROM flight_path WHERE faction=? AND from_id=? AND to_id=?", (faction, a, b)).fetchone()
            if cur:
                conn.execute("""UPDATE flight_path SET cost=?, src=CASE WHEN src LIKE '%db2%' THEN src ELSE src || ',db2' END
                                WHERE faction=? AND from_id=? AND to_id=?""", (cost, faction, a, b))
            else:
                conn.execute("INSERT INTO flight_path VALUES(?,?,?,?,?,?,?)",
                             (faction, a, b, est, cost, "db2" if est is None else "db2,fit", 0.9 if est is None else 0.7))
            n += 1
    return {"rows": n, "fitted_speed_yds": round(speed, 2) if speed else None, "speed_samples": len(ratios)}


def skill_spells(conn, abil, lines):
    cat, name = {}, {}
    for r in lines or []:
        sid = inum(r.get("ID"))
        cat[sid] = inum(r.get("CategoryID"))
        name[sid] = r.get("DisplayName_lang") or r.get("DisplayName") or ""
    n = 0
    for r in abil:
        spell, line = inum(r.get("Spell")), inum(r.get("SkillLine"))
        if not spell:
            continue
        nm = name.get(line, "")
        kind = "riding" if re.search(r"riding|piloting", nm, re.I) else \
            "racial" if nm.lower().startswith("racial") else SKILL_KIND.get(cat.get(line))
        if kind is None:
            continue
        cls, race = single(r.get("ClassMask"), BIT_CLASSES), single(r.get("RaceMask"), BIT_RACES)
        cur = conn.execute("SELECT kind, class, race, src FROM spell WHERE id=?", (spell,)).fetchone()
        if cur:
            conn.execute("""UPDATE spell SET kind=COALESCE(kind, ?), class=COALESCE(class, ?), race=COALESCE(race, ?),
                            src=CASE WHEN src LIKE '%db2%' THEN src ELSE src || ',db2' END WHERE id=?""",
                         (kind, cls, race, spell))
        else:
            conn.execute("INSERT INTO spell(id, kind, class, race, src) VALUES(?,?,?,?,'db2')", (spell, kind, cls, race))
        n += 1
    return n


def spell_text(conn, names, spells, levels):
    ids = {r[0] for r in conn.execute("SELECT id FROM spell")}
    out = {"names": 0, "ranks": 0, "levels": 0}
    for r in names or []:
        sid = inum(r.get("ID"))
        if sid in ids and (r.get("Name_lang") or r.get("Name")):
            conn.execute("UPDATE spell SET name=? WHERE id=? AND name IS NULL", (r.get("Name_lang") or r.get("Name"), sid))
            out["names"] += 1
    for r in spells or []:
        sid = inum(r.get("ID"))
        m = re.search(r"(\d+)", r.get("NameSubtext_lang") or "")
        if sid in ids and m:
            conn.execute("UPDATE spell SET rank=COALESCE(rank, ?) WHERE id=?", (int(m.group(1)), sid))
            out["ranks"] += 1
    for r in levels or []:
        sid = inum(r.get("SpellID"))
        lvl = inum(r.get("BaseLevel")) or inum(r.get("SpellLevel"))
        if sid in ids and lvl:
            conn.execute("UPDATE spell SET level=COALESCE(level, ?) WHERE id=?", (lvl, sid))
            out["levels"] += 1
    return out


def areas(conn, rows):
    info = {}
    for r in rows:
        aid = inum(r.get("ID"))
        if aid:
            info[aid] = (r.get("AreaName_lang") or r.get("ZoneName"), inum(r.get("ParentAreaID")), inum(r.get("ExplorationLevel")))
    kids = collections.defaultdict(list)
    for aid, (_, parent, _) in info.items():
        if parent:
            kids[parent].append(aid)

    def levels(aid, depth=0):
        own = [info[aid][2]] if info[aid][2] else []
        if depth > 6:
            return own
        for k in kids.get(aid, []):
            own += levels(k, depth + 1)
        return own
    n = 0
    for aid, (name, parent, _) in info.items():
        lv = levels(aid)
        cur = conn.execute("SELECT 1 FROM area WHERE area_id=?", (aid,)).fetchone()
        if cur:
            conn.execute("""UPDATE area SET name=COALESCE(name, ?), parent=COALESCE(parent, ?), level_min=?, level_max=?
                            WHERE area_id=?""", (name, parent or None, min(lv) if lv else None, max(lv) if lv else None, aid))
        else:
            conn.execute("INSERT INTO area(area_id, name, parent, level_min, level_max) VALUES(?,?,?,?,?)",
                         (aid, name, parent or None, min(lv) if lv else None, max(lv) if lv else None))
        n += 1
    return n


def ui_maps(conn, rows):
    n = 0
    for r in rows:
        um = inum(r.get("ID"))
        if not um:
            continue
        name, parent = r.get("Name_lang") or r.get("Name"), inum(r.get("ParentUiMapID"))
        if conn.execute("SELECT 1 FROM map WHERE ui_map=?", (um,)).fetchone():
            conn.execute("UPDATE map SET name=COALESCE(name, ?), parent=COALESCE(parent, ?) WHERE ui_map=?", (name, parent, um))
        else:
            conn.execute("INSERT INTO map(ui_map, name, parent) VALUES(?,?,?)", (um, name, parent))
        n += 1
    return n


def assignment_bounds(r):
    """World corners of a UiMap from one UiMapAssignment row: (instance, wx0, wy0, wx1, wy1) or None."""
    u0, v0, u1, v1 = (as_float(r.get(k)) for k in ("UiMin_0", "UiMin_1", "UiMax_0", "UiMax_1"))
    reg = [as_float(r.get("Region_%d" % i)) for i in range(6)]
    if None in (u0, v0, u1, v1) or None in reg or u1 == u0 or v1 == v0:
        return None
    min_x, min_y, _, max_x, max_y, _ = reg

    def wy(u):
        return max_y - (u - u0) / (u1 - u0) * (max_y - min_y)

    def wx(v):
        return max_x - (v - v0) / (v1 - v0) * (max_x - min_x)
    return inum(r.get("MapID")), wx(0.0), wy(0.0), wx(1.0), wy(1.0)


def assignments(conn, rows):
    best = {}
    for r in rows:
        um = inum(r.get("UiMapID"))
        b = assignment_bounds(r)
        if not um or not b:
            continue
        area = abs((as_float(r.get("UiMax_0")) - as_float(r.get("UiMin_0"))) * (as_float(r.get("UiMax_1")) - as_float(r.get("UiMin_1"))))
        if um not in best or area > best[um][0]:
            best[um] = (area, b)
    for um, (_, (inst, wx0, wy0, wx1, wy1)) in best.items():
        conn.execute("INSERT OR REPLACE INTO map_bounds VALUES(?,?,?,?,?,?,?,?)",
                     (um, inst, round(wx0, 3), round(wy0, 3), round(wx1, 3), round(wy1, 3), "db2", 1.0))
    return len(best)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Import wow.tools.local DB2 CSV exports into the Glide KB")
    ap.add_argument("--db2", default=os.path.join(ROOT, "data", "db2"))
    ap.add_argument("--kb", help="KB file to import into (default: only report what is in --db2)")
    a = ap.parse_args(argv)
    have = available(a.db2)
    if not a.kb:
        print(json.dumps({t: {"file": p, "rows": len(read_csv(p))} for t, p in have.items()} or
                         {"skipped": "no DB2 CSV exports in %s" % a.db2}, indent=1))
        return 0
    conn = sqlite3.connect(a.kb)
    res = import_dir(conn, a.db2)
    conn.commit()
    conn.close()
    print(json.dumps(res, indent=1, default=str))
    return 0

