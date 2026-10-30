"""Loader 1: data/glide.db (merged community + recorder + client-cache data, tools/build_db.py) -> kb.

Tables: map, area, area_box, quest, quest_objective, quest_prereq, quest_chain, quest_giver, quest_poi,
creature, creature_spawn, gameobject, gameobject_spawn, trainer (from creature subnames such as
"Warrior Trainer" / "Journeyman Tailor" plus recorder TRAINER_SHOW visits), poi kind 'inn' (Innkeeper).

Provenance: glide.db's `sources` becomes `src`, `field_sources` becomes quest.field_src (json), conf is
the best source's confidence (store.SOURCE_CONF: wdb 1.0, obs 0.95, scan 0.9,
fg 0.6). Rows that only exist because the ID sweep found them (the server answered nothing)
get src 'scan' and conf 0.3.

Extra fields read straight from the client quest cache (data/archive/wdb/*/questcache.wdb, plus the
live client cache when config.json points at it): QuestInfoID (quest type), RewardXPDifficulty and
RewardXPMultiplier (quest XP table column / multiplier), at fixed offsets verified against the records
wowfiles/wdb.py already decodes (a record that wdb.py rejects is skipped, never guessed).
"""
import collections
import glob
import os
import re
import statistics
import struct

from knowledge import store
from knowledge.values import as_int
from knowledge.vocab import BIT_CLASSES
from wowfiles import wdb

ROOT = store.ROOT

# glide.db objective type -> (kb type, target_kind). Numeric strings are Mainline QuestObjectiveType values
# the wdb decoder didn't name (10/19/20 area triggers, 6/7 reputation, 8 money).
OBJ_TYPES = {"monster": ("kill", "npc"), "kill": ("kill", "npc"), "item": ("collect", "item"),
             "object": ("interact", "object"), "event": ("event", None), "talkto": ("talk", "npc"),
             "10": ("explore", "area"), "19": ("explore", "area"), "20": ("explore", "area")}
INFO_TYPE = {1: "elite", 41: "pvp", 62: "raid", 81: "dungeon", 84: "escort", 85: "dungeon", 88: "raid", 89: "raid"}
SIDES = {"alliance": "Alliance", "horde": "Horde", "both": "Both"}
NEAR_CONF = 0.5          # recorder "near" sightings: the player's position when the nameplate showed up


def as_list(v):
    if v is None:
        return []
    if isinstance(v, dict):
        return [v[k] for k in sorted(v, key=lambda x: int(x) if str(x).lstrip("-").isdigit() else 0)]
    return v if isinstance(v, list) else [v]


def load(conn, ctx):
    if not ctx.has_glide():
        return {"skipped": "no glide.db at %s" % ctx.glide_db}
    g = ctx.glide()
    seed = ctx.load_seed() or {}
    res = {}
    res["map"] = load_maps(conn, g, seed)
    res["area"] = load_areas(conn, g)
    spawn_idx = {}
    res["creature"], res["gameobject"] = load_entities(conn, g, seed)
    res["creature_spawn"], res["gameobject_spawn"] = load_spawns(conn, g, spawn_idx)
    wdbx = wdb_quest_extras(ctx)
    res["wdb_quest_extras"] = len(wdbx)
    res["quest"] = load_quests(conn, g, seed, wdbx)
    res["quest_objective"] = load_objectives(conn, g, seed, spawn_idx)
    res["quest_prereq"] = load_prereqs(conn, g, seed)
    res["quest_chain"] = load_chains(conn)
    res["quest_giver"] = load_givers(conn, g, spawn_idx)
    res["quest_poi"] = load_quest_pois(conn, g)
    res["trainer"], res["inn"] = load_trainers(conn, g, spawn_idx)
    ctx.cache["spawn_idx"] = spawn_idx
    g.close()
    return res


# ------------------------------------------------------------------------------------------------
# maps and areas
# ------------------------------------------------------------------------------------------------
def load_maps(conn, g, seed):
    smaps = seed.get("maps") or {}
    rows, how = [], collections.Counter()
    seen = set()
    for r in g.execute("SELECT * FROM maps"):
        um = r["ui_map"]
        seen.add(um)
        sm = smaps.get(str(um)) or {}
        rows.append(_map_row(um, r["name"], r["parent"], r["area_id"], sm, how))
    for k, sm in smaps.items():        # maps the seed knows that glide.db didn't copy
        um = as_int(k)
        if um is not None and um not in seen:
            rows.append(_map_row(um, sm.get("n"), sm.get("p"), sm.get("area"), sm, how))
    store.insert_rows(conn, "map", rows)
    return {"rows": len(rows), "continent_from": dict(how)}


def _map_row(um, name, parent, area_id, sm, how):
    mtype = sm.get("t")
    cont = None
    if mtype in (0, 1):                 # cosmic / world maps span several instances
        how["world:null"] += 1
    elif sm.get("map") is not None:     # instance (continent) id the UiMap is assigned to, when the seed has it
        cont = as_int(sm.get("map"))
        how["seed"] += 1
    elif parent in (1414, 1415):
        cont = 1 if parent == 1414 else 0
        how["parent"] += 1
    else:
        how["unknown"] += 1
    return {"ui_map": um, "name": name, "parent": parent, "area_id": area_id or None, "continent": cont}


def load_areas(conn, g):
    zones = {r["area_id"]: dict(r) for r in g.execute("SELECT * FROM zones") if r["area_id"]}
    boxes = collections.defaultdict(list)
    box_rows = []
    for r in g.execute("SELECT * FROM map_areas"):
        boxes[r["area_id"]].append(dict(r))
        box_rows.append({"area_id": r["area_id"], "ui_map": r["ui_map"], "x1": r["x1"], "y1": r["y1"],
                         "x2": r["x2"], "y2": r["y2"]})
    rows = []
    for aid in sorted(set(zones) | set(boxes)):
        if aid is None or aid <= 0:
            continue
        z = zones.get(aid) or {}
        bx = boxes.get(aid) or []
        ui_map = z.get("ui_map") or None
        if ui_map is None and bx:
            ui_map = bx[0]["ui_map"]
        same = [b for b in bx if b["ui_map"] == ui_map]      # bounds only in the area's own map coordinates
        name = z.get("name") or (bx[0]["name"] if bx else None)
        row = {"area_id": aid, "name": name, "ui_map": ui_map, "parent": z.get("parent") or None,
               "x1": None, "y1": None, "x2": None, "y2": None}
        if same:
            row.update(x1=min(b["x1"] for b in same), y1=min(b["y1"] for b in same),
                       x2=max(b["x2"] for b in same), y2=max(b["y2"] for b in same))
        rows.append(row)
    store.insert_rows(conn, "area", rows)
    store.insert_rows(conn, "area_box", box_rows)
    return {"rows": len(rows), "with_bounds": sum(1 for r in rows if r["x1"] is not None), "boxes": len(box_rows)}


# ------------------------------------------------------------------------------------------------
# creatures / objects and their spawns
# ------------------------------------------------------------------------------------------------
def load_entities(conn, g, seed):
    src = seed.get("sources") or {}
    member = {
        "npc": [("fg", src.get("fg_npcs") or {})],
        "object": [("fg", src.get("fg_objects") or {})],
    }
    obs_seen = collections.defaultdict(set)
    for kind, eid in g.execute("SELECT DISTINCT kind, entity_id FROM spawns WHERE source='obs'"):
        obs_seen[kind].add(eid)
    crows, orows = [], []
    for r in g.execute("SELECT * FROM entities"):
        kind, eid = r["kind"], r["id"]
        tags = [r["source"]] if r["source"] else []
        for tag, book in member.get(kind, []):
            if str(eid) in book:
                tags.append(tag)
        if eid in obs_seen[kind]:
            tags.append("obs")
        s = store.merge_src("", ",".join(tags))
        name = store.clean_name(r["name"])
        if kind == "npc":
            rank = r["rank"]
            friendly = r["friendly"] if r["friendly"] in ("A", "H", "AH") else None
            crows.append({"id": eid, "name": name, "subname": store.clean_name(r["subname"]),
                          "min_level": r["min_level"], "max_level": r["max_level"], "rank": rank,
                          "type": None, "reaction": None,    # 'AH' also tags hostile beasts: not a reaction
                          "friendly_to": friendly, "src": s or "fg", "conf": store.src_conf(s),
                          "verified": store.is_verified(s)})
        else:
            orows.append({"id": eid, "name": name, "type": None, "src": s or "fg"})
    store.insert_rows(conn, "creature", crows)
    store.insert_rows(conn, "gameobject", orows)
    return len(crows), len(orows)


def load_spawns(conn, g, spawn_idx):
    """One row per (entity, map, point, source); identical points from the same source are aggregated (n)."""
    crow, orow = [], []
    for r in g.execute("""SELECT kind, entity_id, ui_map, round(x, 2) AS x, round(y, 2) AS y, accuracy, source,
                                 COUNT(*) AS n
                          FROM spawns WHERE entity_id IS NOT NULL AND x IS NOT NULL AND y IS NOT NULL
                          GROUP BY kind, entity_id, ui_map, round(x, 2), round(y, 2), accuracy, source"""):
        conf = NEAR_CONF if r["accuracy"] == "near" else store.src_conf(r["source"])
        spawn_idx.setdefault((r["kind"], r["entity_id"]), []).append(
            (r["ui_map"], r["x"], r["y"], r["source"], r["accuracy"]))
        if r["kind"] == "npc":
            crow.append((r["entity_id"], r["ui_map"], r["x"], r["y"], r["n"], r["source"], conf))
        else:
            orow.append((r["entity_id"], r["ui_map"], r["x"], r["y"], r["source"], conf))
    store.insert_rows(conn, "creature_spawn", crow, ["creature_id", "ui_map", "x", "y", "n", "src", "conf"])
    store.insert_rows(conn, "gameobject_spawn", orow, ["object_id", "ui_map", "x", "y", "src", "conf"])
    return len(crow), len(orow)


def single_location(points, prefer_map=None, spread=1.5):
    """(ui_map, x, y, src) when an entity's known points describe one place, else None.
    points: [(ui_map, x, y, src, accuracy)]. Exact sightings beat database points; 'near' ones are ignored."""
    pts = [p for p in points if p[4] != "near" and p[0] is not None]
    if not pts:
        return None
    exact = [p for p in pts if p[4] == "exact"]
    if exact:
        pts = exact
    maps = collections.Counter(p[0] for p in pts)
    if prefer_map in maps:
        m = prefer_map
    elif len(maps) == 1:
        m = next(iter(maps))
    else:
        return None
    pts = [p for p in pts if p[0] == m]
    mx = statistics.median(p[1] for p in pts)
    my = statistics.median(p[2] for p in pts)
    if any(abs(p[1] - mx) > spread or abs(p[2] - my) > spread for p in pts):
        return None
    srcs = collections.Counter(p[3] for p in pts)
    return m, round(mx, 2), round(my, 2), srcs.most_common(1)[0][0]


# ------------------------------------------------------------------------------------------------
# quests
# ------------------------------------------------------------------------------------------------
def wdb_quest_extras(ctx):
    """quest id -> {info, diff, mult, qtype, money} from client quest caches (records wdb.py accepts)."""
    files = sorted(glob.glob(os.path.join(ctx.archive_wdb, "**", "questcache.wdb"), recursive=True))
    wow = ctx.wow_dir()
    if wow:
        files += glob.glob(os.path.join(wow, "Cache", "WDB", "*", "questcache.wdb"))
    out = {}
    for path in files:
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if data[:4] != b"TSQW":
            continue
        for rid, p in wdb.records(data):
            try:
                wdb.parse_quest(p)                       # layout check: the text block must close exactly
                qid, qtype = struct.unpack_from("<ii", p, 0)
                info = struct.unpack_from("<i", p, 28)[0]
                diff = struct.unpack_from("<i", p, 40)[0]
                mult = struct.unpack_from("<f", p, 44)[0]
                money = struct.unpack_from("<i", p, 48)[0]
            except Exception:
                continue
            if qid != rid or not 0 <= diff <= 9 or not 0 <= info < 1000 or not 0 < mult < 20:
                continue
            out[rid] = {"info": info, "diff": diff, "mult": round(mult, 4), "qtype": qtype, "money": money}
    return out


def quest_type(info, whd_type, suggested, classes):
    if info in INFO_TYPE:
        return INFO_TYPE[info]
    if whd_type in INFO_TYPE:
        return INFO_TYPE[whd_type]
    if suggested and suggested > 1:
        return "group"
    if classes and classes > 0 and classes & (classes - 1) == 0 and classes in BIT_CLASSES:
        return "class"
    return "normal"


def load_quests(conn, g, seed, wdbx):
    forever_new = set((seed.get("ids") or {}).get("forever_new") or [])
    rows, types = [], collections.Counter()
    for r in g.execute("SELECT * FROM quests"):
        qid = r["id"]
        fs = store.jload(r["field_sources"], {}) or {}
        src = r["sources"] or ""
        empty = not src
        if empty:
            src = "scan"                                   # in the ID sweep; the server answered nothing
        x = wdbx.get(qid) or {}
        info = x.get("info")
        t = quest_type(info, None, None, r["classes"])
        types[t] += 1
        xp_src = fs.get("xp") if r["xp"] is not None else None
        rows.append({
            "id": qid, "name": r["name"], "level": r["level"], "min_level": r["min_level"],
            "side": SIDES.get((r["side"] or "").lower()), "races": r["races"], "classes": r["classes"],
            "zone_area": r["zone_area"], "zone_name": r["zone_name"], "ui_map": r["ui_map"],
            "xp": r["xp"], "xp_src": xp_src, "xp_conf": store.SOURCE_CONF.get(xp_src) if xp_src else None,
            "money": r["money"] if r["money"] is not None else x.get("money"),
            "next_quest": r["next_id"], "type": t, "suggested_group": None,
            "is_forever": 1 if (r["is_forever"] or qid in forever_new) else 0, "in_client": r["in_client"],
            "objective_text": r["objective_text"], "details": r["details"],
            "xp_difficulty": x.get("diff"), "xp_multiplier": x.get("mult"), "quest_info": info,
            "removed": r["removed"], "hidden": r["hidden"], "field_src": store.jdump(fs) if fs else None,
            "src": src, "conf": 0.3 if empty else store.src_conf(src),
            "verified": 0 if empty else store.is_verified(src)})
    store.insert_rows(conn, "quest", rows)
    return {"rows": len(rows), "with_xp": sum(1 for r in rows if r["xp"] is not None),
            "no_data": sum(1 for r in rows if r["conf"] == 0.3), "types": dict(types)}


def load_objectives(conn, g, seed, spawn_idx):
    items = (seed.get("sources") or {}).get("fg_items") or {}
    qmap = dict(conn.execute("SELECT id, ui_map FROM quest"))
    rows, kinds, drop_known = [], collections.Counter(), 0
    for r in g.execute("SELECT * FROM quest_objectives"):
        typ = (r["type"] or "").strip()
        kb_type, target_kind = OBJ_TYPES.get(typ, ("other", None))
        kinds[kb_type] += 1
        row = {"quest_id": r["quest_id"], "idx": r["idx"], "type": kb_type, "target_kind": target_kind,
               "target_id": r["target_id"] if target_kind else None, "amount": r["amount"], "text": r["text"],
               "src": r["source"], "conf": store.src_conf(r["source"])}
        if kb_type == "collect" and r["target_id"]:
            dk, di = drop_source(items.get(str(r["target_id"])), qmap.get(r["quest_id"]), spawn_idx)
            if dk:
                row.update(drop_from_kind=dk, drop_from_id=di)
                drop_known += 1
        rows.append(row)
    store.insert_rows(conn, "quest_objective", rows, verb="INSERT OR REPLACE")
    return {"rows": len(rows), "types": dict(kinds), "collect_with_drop_from": drop_known}


def drop_source(item, quest_map, spawn_idx):
    """The one creature/object the seed says drops a quest item; with several, the one spawning on the
    quest's map (when exactly one does). Returns (kind, id) or (None, None)."""
    if not item:
        return None, None
    cands = [("npc", as_int(i)) for i in as_list(item.get("npc"))] + [("object", as_int(i)) for i in as_list(item.get("obj"))]
    cands = [c for c in cands if c[1]]
    if len(cands) == 1:
        return cands[0]
    if quest_map:
        here = [c for c in cands if any(p[0] == quest_map for p in spawn_idx.get(c, []))]
        if len(here) == 1:
            return here[0]
    return None, None


def load_prereqs(conn, g, seed):
    src = seed.get("sources") or {}
    fgq = src.get("fg_quests") or {}

    def tags(q, p, kind):
        f = fgq.get(str(q)) or {}
        out = []
        keys = {"pre": (("pre", "fg", f),), "pregroup": (("pregroup", "fg", f),),
                "breadcrumb": (("breadcrumbs", "fg", f),), "exclusive": (("excl", "fg", f),)}
        for key, tag, rec in keys.get(kind, ()):
            if p in [as_int(x) for x in as_list(rec.get(key))]:
                out.append(tag)
        return ",".join(out)

    rows = {}
    for q, p, kind in g.execute("SELECT quest_id, pre_id, kind FROM quest_prereqs WHERE pre_id IS NOT NULL"):
        s = tags(q, p, kind) or "fg"
        rows[(q, p, kind)] = {"quest_id": q, "pre_id": p, "kind": kind, "src": s, "conf": store.src_conf(s)}
    n_excl = 0
    for book, tag in ((fgq, "fg"),):          # exclusive groups: not migrated by build_db
        for q, rec in book.items():
            for x in as_list(rec.get("excl")):
                q_, x_ = as_int(q), as_int(x)
                if not q_ or not x_:
                    continue
                key = (q_, x_, "exclusive")
                if key in rows:
                    rows[key]["src"] = store.merge_src(rows[key]["src"], tag)
                    rows[key]["conf"] = store.src_conf(rows[key]["src"])
                else:
                    rows[key] = {"quest_id": q_, "pre_id": x_, "kind": "exclusive", "src": tag,
                                 "conf": store.SOURCE_CONF[tag]}
                    n_excl += 1
    store.insert_rows(conn, "quest_prereq", list(rows.values()))
    return {"rows": len(rows), "exclusive": n_excl,
            "kinds": dict(collections.Counter(k[2] for k in rows))}


def load_chains(conn):
    nxt = {q: n for q, n in conn.execute("SELECT id, next_quest FROM quest WHERE next_quest IS NOT NULL AND next_quest > 0")}
    targets = set(nxt.values())
    rows = []
    for start in sorted(q for q in nxt if q not in targets):
        seen, q, pos = set(), start, 1
        while q and q not in seen and pos <= 60:
            seen.add(q)
            rows.append((start, q, pos))
            q, pos = nxt.get(q), pos + 1
    store.insert_rows(conn, "quest_chain", rows, ["chain_id", "quest_id", "pos"], verb="INSERT OR IGNORE")
    return {"rows": len(rows), "chains": len({r[0] for r in rows})}


# ------------------------------------------------------------------------------------------------
# quest givers / enders and quest points
# ------------------------------------------------------------------------------------------------
def load_givers(conn, g, spawn_idx):
    qmap = dict(conn.execute("SELECT id, ui_map FROM quest"))
    givers = {}          # (qid, role, kind, eid) -> row
    for r in g.execute("SELECT * FROM quest_entities WHERE role IN ('start','end')"):
        key = (r["quest_id"], r["role"], r["kind"], r["entity_id"])
        cur = givers.get(key)
        if cur:
            cur["src"] = store.merge_src(cur["src"], r["source"])
            cur["name"] = cur["name"] or store.clean_name(r["name"])
        else:
            givers[key] = {"quest_id": key[0], "role": key[1], "kind": key[2], "entity_id": key[3],
                           "name": store.clean_name(r["name"]), "ui_map": None, "x": None, "y": None,
                           "src": r["source"], "pos_src": None}
    by_qr = collections.defaultdict(list)
    for key in givers:
        by_qr[key[:2]].append(key)

    def set_pos(row, m, x, y, src):
        if row["x"] is None or store.SOURCE_CONF.get(src, 0) > store.SOURCE_CONF.get(row["pos_src"], 0):
            row.update(ui_map=m, x=x, y=y, pos_src=src)

    orphan, attached = [], 0
    for r in g.execute("SELECT * FROM quest_points WHERE role IN ('start','end') AND x IS NOT NULL"):
        qid, role = r["quest_id"], r["role"]
        if r["kind"] and r["entity_id"]:
            key = (qid, role, r["kind"], r["entity_id"])
            row = givers.get(key)
            if row is None:
                row = givers[key] = {"quest_id": qid, "role": role, "kind": r["kind"], "entity_id": r["entity_id"],
                                     "name": None, "ui_map": None, "x": None, "y": None, "src": r["source"],
                                     "pos_src": None}
                by_qr[(qid, role)].append(key)
            else:
                row["src"] = store.merge_src(row["src"], r["source"])
            set_pos(row, r["ui_map"], r["x"], r["y"], r["source"])
            attached += 1
            continue
        cands = [k for k in by_qr.get((qid, role), []) if k[2] in ("npc", "object")]
        pick = None
        if len(cands) == 1:
            pick = cands[0]
        elif cands:            # several: the one whose known spawn is next to the point
            best = None
            for k in cands:
                for p in spawn_idx.get((k[2], k[3]), []):
                    if p[0] == r["ui_map"]:
                        d = abs(p[1] - r["x"]) + abs(p[2] - r["y"])
                        if d < 3 and (best is None or d < best[0]):
                            best = (d, k)
            pick = best[1] if best else None
        if pick:
            set_pos(givers[pick], r["ui_map"], r["x"], r["y"], r["source"])
            attached += 1
        else:
            orphan.append(r)
    filled = 0
    for key, row in givers.items():
        if row["x"] is None and key[2] in ("npc", "object"):
            loc = single_location(spawn_idx.get((key[2], key[3]), []), prefer_map=qmap.get(key[0]))
            if loc:
                row.update(ui_map=loc[0], x=loc[1], y=loc[2], pos_src="spawn:" + loc[3])
                filled += 1
    rows = []
    for row in givers.values():
        row["conf"] = store.src_conf(row["src"])
        rows.append(row)
    for r in orphan:      # a position without a known entity: keep it, entity unknown
        rows.append({"quest_id": r["quest_id"], "role": r["role"], "kind": r["kind"], "entity_id": None,
                     "name": None, "ui_map": r["ui_map"], "x": r["x"], "y": r["y"], "src": r["source"],
                     "pos_src": r["source"], "conf": store.src_conf(r["source"])})
    store.insert_rows(conn, "quest_giver", rows)
    return {"rows": len(rows), "points_attached": attached, "positions_from_spawns": filled,
            "positions_unattached": len(orphan),
            "with_position": sum(1 for r in rows if r["x"] is not None)}


def load_quest_pois(conn, g):
    rows = []
    for r in g.execute("""SELECT quest_id, role, ui_map, round(x, 2) AS x, round(y, 2) AS y, source, COUNT(*) AS n
                          FROM quest_points WHERE role NOT IN ('start','end') AND x IS NOT NULL
                          GROUP BY quest_id, role, ui_map, round(x, 2), round(y, 2), source"""):
        rows.append((r["quest_id"], r["role"], r["ui_map"], r["x"], r["y"], r["n"], r["source"],
                     store.src_conf(r["source"])))
    store.insert_rows(conn, "quest_poi", rows, ["quest_id", "role", "ui_map", "x", "y", "n", "src", "conf"])
    return len(rows)


# ------------------------------------------------------------------------------------------------
# trainers and inns (subnames are what the client shows under the NPC's name)
# ------------------------------------------------------------------------------------------------
CLASS_WORDS = {"warrior": "WARRIOR", "paladin": "PALADIN", "hunter": "HUNTER", "rogue": "ROGUE", "priest": "PRIEST",
               "shaman": "SHAMAN", "mage": "MAGE", "warlock": "WARLOCK", "druid": "DRUID"}
PROF_WORDS = ("alchemist", "alchemy", "blacksmith", "enchanter", "enchanting", "engineer", "engineering",
              "leatherworker", "leatherworking", "tailor", "tailoring", "herbalism", "mining", "skinning", "cooking",
              "fishing", "first aid")


def trainer_kind(sub):
    """(kind, class) from a subname, or None when the NPC is not a trainer."""
    s = (sub or "").strip().lower()
    if not s:
        return None
    m = re.match(r"^(\w+) trainer$", s)
    if m and m.group(1) in CLASS_WORDS:
        return "class", CLASS_WORDS[m.group(1)]
    if "pet trainer" in s or s == "hunter beast trainer":
        return "pet", "HUNTER"
    if s == "demon trainer":
        return "pet", "WARLOCK"
    if s == "portal trainer":
        return "class", "MAGE"
    if "riding" in s and ("instructor" in s or "trainer" in s):
        return "riding", None
    if s == "weapon master":
        return "weapon", None
    if (s.endswith("trainer") or re.match(r"^(journeyman|expert|artisan|master) ", s)) and \
            any(w in s for w in PROF_WORDS):
        return "profession", None
    return None


def load_trainers(conn, g, spawn_idx):
    visits = {}
    for r in g.execute("SELECT key, json FROM obs_misc WHERE kind='trainers'"):
        d = store.jload(r["json"], {}) or {}
        pos = d.get("pos") or {}
        if as_int(r["key"]) and pos.get("x") is not None:
            visits[as_int(r["key"])] = (as_int(pos.get("m")), pos.get("x"), pos.get("y"), store.clean_name(d.get("n")))
    trainers, inns = [], []
    for cid, name, sub, src, conf, friendly in conn.execute(
            "SELECT id, name, subname, src, conf, friendly_to FROM creature WHERE subname IS NOT NULL OR id IN (%s)"
            % ",".join(str(i) for i in visits or [0])).fetchall():
        tk = trainer_kind(sub)
        if tk is None and cid not in visits and (sub or "").strip().lower() != "innkeeper":
            continue
        loc = None
        if cid in visits:
            v = visits[cid]
            loc, src = (v[0], v[1], v[2], "obs"), store.merge_src(src, "obs")
        else:
            l = single_location(spawn_idx.get(("npc", cid), []))
            loc = (l[0], l[1], l[2], l[3]) if l else None
        if (sub or "").strip().lower() == "innkeeper":
            inns.append({"kind": "inn", "id": cid, "name": name,
                         "faction": {"A": "Alliance", "H": "Horde", "AH": "Both"}.get(friendly),
                         "ui_map": loc[0] if loc else None, "x": loc[1] if loc else None, "y": loc[2] if loc else None,
                         "src": src, "conf": conf})
            continue
        trainers.append({"npc_id": cid, "name": name or (visits.get(cid) or (None,) * 4)[3],
                         "class": tk[1] if tk else None, "kind": tk[0] if tk else None,
                         "ui_map": loc[0] if loc else None, "x": loc[1] if loc else None, "y": loc[2] if loc else None,
                         "src": src, "conf": conf})
    store.insert_rows(conn, "trainer", trainers)
    store.insert_rows(conn, "poi", inns, verb="INSERT OR REPLACE")
    return {"rows": len(trainers), "kinds": dict(collections.Counter(t["kind"] for t in trainers)),
            "with_position": sum(1 for t in trainers if t["x"] is not None)}, len(inns)
