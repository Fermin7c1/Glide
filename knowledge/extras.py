"""Player extras (addon 0.10 pages) -> facts for the coach: training, talent points, flight paths, consumables,
corpse run. Pure functions over the knowledge base (`kb` = store.KB or anything with .q(sql, *args)).

Inputs come from companion/state.py GameState.summary():
  training      {"missing": [spell ids], "for_level": int, "checked": int}  (RestedXP's training list, addon-checked)
  flight_paths  [taxi node ids]                                              (known to this character)
  consumables   [{"kind", "id", "count", "req", "name"}]
  corpse        {"x", "y"} map % on the player's map (while dead / a ghost)
"""
import math
import re

try:
    from knowledge.mechanics import death as MD
except Exception:  # noqa: BLE001 - extras must work without mechanics
    MD = None

KIND_LABEL = {"f": "food", "d": "drink", "b": "food_and_drink", "h": "bandage", "p": "healing_potion",
              "m": "mana_potion", "x": "poison", "w": "weapon_stone_or_oil", "a": "ammo", "s": "soul_shard",
              "r": "reagent"}
# RestedXP's training list names these on one shared comment ("cooking/FA"); name them one by one
SPELL_NAMES = {2550: "Cooking", 2551: "Cooking", 3273: "First Aid", 3274: "First Aid (journeyman)",
               2575: "Mining", 2368: "Herbalism", 7620: "Fishing", 8613: "Skinning"}
FP_NEAR_YD = 80                  # an unknown flight master this close is worth a detour of seconds


def _rank_split(note):
    """'heroic strike r2' -> ('heroic strike', 2); 'rend 2' -> ('rend', 2); 'charge' -> ('charge', 1)."""
    n = (note or "").strip().lower()
    m = re.match(r"^(.*?)\s+(?:r|rank\s*)?(\d+)$", n)
    if m and m.group(1):
        return m.group(1).strip(), int(m.group(2))
    return n, 1


def _title(note, sid):
    if sid in SPELL_NAMES:
        return SPELL_NAMES[sid]
    base, rank = _rank_split(note)
    if not base:
        return "spell %s" % sid
    name = " ".join(w[:1].upper() + w[1:] for w in base.split())
    return name if rank == 1 and not re.search(r"\d$", (note or "").strip()) else "%s (rank %d)" % (name, rank)


def training(kb, cls, race, level, tr):
    """{"missing_now": [{"spell", "level", "kind"}], "superseded": n, "next": [{"level", "spells"}]} or None.
    missing_now: on RestedXP's training list at or below the level the addon checked, not known, and not replaced
    by a higher rank the character already has (skipped ranks are not worth buying)."""
    if not tr or tr.get("for_level") is None or not cls:
        return None
    upto = int(tr["for_level"])
    missing = set(tr.get("missing") or ())
    rows = kb.q("SELECT id, level, kind, notes FROM spell WHERE (class = ? OR ',' || race || ',' LIKE ?) "
                "AND level <= ? ORDER BY level, id", cls, "%%,%s,%%" % (race or "-"), upto + 2)
    if not rows:
        return None
    known_rank = {}
    for r in rows:
        if r["level"] <= upto and r["id"] not in missing:
            base, rank = _rank_split(r["notes"])
            known_rank[base] = max(known_rank.get(base, 0), rank)
    now, superseded = [], 0
    for r in rows:
        if r["level"] > upto or r["id"] not in missing:
            continue
        base, rank = _rank_split(r["notes"])
        if known_rank.get(base, 0) > rank:
            superseded += 1
            continue
        now.append({"spell": _title(r["notes"], r["id"]), "level": r["level"], "kind": r["kind"] or "class"})
    nxt = {}
    for r in rows:
        if upto < r["level"] <= upto + 2:
            nxt.setdefault(r["level"], []).append(_title(r["notes"], r["id"]))
    out = {"missing_now": now, "next": [{"level": lv, "spells": sp} for lv, sp in sorted(nxt.items())]}
    if superseded:
        out["lower_ranks_skipped"] = superseded
    if level is not None and int(level) != upto:
        out["note"] = "checked at level %d" % upto
    return out


def _yards(map_size, x1, y1, x2, y2):
    if not map_size:
        return None
    w, h = map_size
    return math.hypot((x2 - x1) / 100.0 * w, (y2 - y1) / 100.0 * h)


def flights(kb, faction, known_ids, ui_map=None, x=None, y=None, map_size=None):
    """{"known": [names], "unknown_nearby": [{"name", "yards"}], "unknown_on_this_map": [names]} or None."""
    if known_ids is None:
        return None
    rows = kb.q("SELECT id, name, faction, ui_map, x, y FROM poi WHERE kind = 'flight'")
    by_id = {r["id"]: r for r in rows}
    known = set(known_ids)
    names = [(by_id[i]["name"] if i in by_id else "node %d" % i) for i in sorted(known)]
    out = {"known": names}
    if ui_map is not None:
        here, near = [], []
        for r in rows:
            if r["id"] in known or r["ui_map"] != ui_map or (faction and r["faction"] not in (faction, "Both", "Neutral", None)):
                continue
            here.append(r["name"])
            if x is not None and r["x"] is not None:
                d = _yards(map_size, x, y, r["x"], r["y"])
                if d is not None and d <= FP_NEAR_YD:
                    near.append({"name": r["name"], "id": r["id"], "yards": int(round(d))})
        if here:
            out["unknown_on_this_map"] = here
        if near:
            out["unknown_nearby"] = sorted(near, key=lambda n: n["yards"])
    return out


def consumables(items, level=None):
    """{"food": ["Tough Jerky x12"], ..., "counts": {"food": 12}} or None. Items the player is too low to use
    say so ("(level 15)") and are left out of the counts."""
    if items is None:
        return None
    out, counts = {}, {}
    for it in items:
        label = KIND_LABEL.get(it.get("kind"), it.get("kind"))
        txt = "%s x%d" % (it.get("name") or "item %s" % it.get("id"), it.get("count") or 0)
        usable = not (level and it.get("req") and int(it["req"]) > int(level))
        if not usable:
            txt += " (level %d)" % int(it["req"])
        out.setdefault(label, []).append(txt)
        if usable:
            counts[label] = counts.get(label, 0) + int(it.get("count") or 0)
            if label == "food_and_drink":
                counts["food"] = counts.get("food", 0) + int(it.get("count") or 0)
                counts["drink"] = counts.get("drink", 0) + int(it.get("count") or 0)
    out["counts"] = counts
    return out


def corpse(level, x, y, corpse_xy, map_size=None):
    """Corpse run facts: {"yards", "corpse_run_s", "spirit_healer": {...best_res}} or None."""
    if not corpse_xy or x is None:
        return None
    d = _yards(map_size, x, y, corpse_xy["x"], corpse_xy["y"])
    out = {"corpse_at": "%.1f,%.1f" % (corpse_xy["x"], corpse_xy["y"])}
    if d is None:
        return out
    out["yards"] = int(round(d))
    if MD is not None and level:
        try:
            b = MD.best_res(int(level), d)
            out.update({"corpse_run_total_s": b["corpse_s"], "spirit_res_total_s": b["spirit_s"],
                        "res_sickness_s": b["sickness_s"], "spirit_durability_loss": b["durability_spirit"],
                        "faster": b["faster"]})
        except Exception:  # noqa: BLE001
            pass
    return out
