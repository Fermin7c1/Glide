"""Loader 9 (runs last): convert positions that exist in only one coordinate form, check provenance, count.

  rxp_step     x, y from (continent, wx, wy) when the step's ui_map has bounds (the parser keeps world-form
               gotos as world coordinates). The world-axis convention of the parser's columns is detected
               (store convention wx = north vs swapped): the reading that lands inside the map wins.
  rxp_action   same for args JSON objects that carry wx/wy but no x/y (keys added: x, y, xy_from 'bounds')
  poi          world-only rows (flight nodes, graveyards) get the map they are on (see pois(): sub-zone boxes
               decide between overlapping map rectangles, a city beats its zone) and x, y; a point outside
               every known map keeps the zone the source named and NULL x, y. Map-only rows get
               continent/wx/wy
  map          continent from map_bounds where still unknown
Never extrapolates: a world point that falls outside every known map keeps x, y NULL.
"""
import json

from knowledge import store

SLACK = 0.5          # percent outside 0..100 still accepted as "on the map" (rounding at the borders)


def load(conn, ctx):
    bounds = {r["ui_map"]: dict(r) for r in conn.execute("SELECT * FROM map_bounds")}
    res = {"map_bounds": len(bounds)}
    res["map_continent"] = conn.execute("""UPDATE map SET continent=(SELECT continent FROM map_bounds b
                                           WHERE b.ui_map=map.ui_map) WHERE continent IS NULL AND ui_map IN
                                           (SELECT ui_map FROM map_bounds WHERE continent IS NOT NULL)""").rowcount
    res["rxp_step"] = rxp_steps(conn, bounds)
    res["rxp_action"] = rxp_actions(conn, bounds)
    res["poi"] = pois(conn, bounds)
    res["provenance"] = {t: conn.execute("SELECT COUNT(*) FROM %s WHERE src IS NULL OR src=''" % t).fetchone()[0]
                         for t in ("quest", "creature", "quest_giver", "creature_spawn", "poi", "item")}
    counts = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
        counts[name] = conn.execute("SELECT COUNT(*) FROM %s" % name).fetchone()[0]
    store.set_meta(conn, "counts", counts)
    res["tables"] = len(counts)
    return res


def _on_map(xy):
    return xy[0] is not None and -SLACK <= xy[0] <= 100 + SLACK and -SLACK <= xy[1] <= 100 + SLACK


def _convert(b, wx, wy, swapped):
    return store.world_to_map(b, wy, wx) if swapped else store.world_to_map(b, wx, wy)


def detect_swap(samples, bounds):
    """samples: [(ui_map, wx, wy)]. True when the columns hold (wy, wx) instead of (wx, wy)."""
    as_is = sum(1 for um, wx, wy in samples if _on_map(_convert(bounds[um], wx, wy, False)))
    swapped = sum(1 for um, wx, wy in samples if _on_map(_convert(bounds[um], wx, wy, True)))
    return swapped > as_is, as_is, swapped


def rxp_steps(conn, bounds):
    have = set(store.table_cols(conn, "rxp_step"))
    if not {"wx", "wy", "x", "y", "ui_map"} <= have:
        return {"skipped": "rxp_step has no world columns"}
    rows = conn.execute("""SELECT rowid, ui_map, wx, wy FROM rxp_step WHERE wx IS NOT NULL AND wy IS NOT NULL
                           AND x IS NULL""").fetchall()
    if not rows:
        return {"world_only": 0}
    usable = [r for r in rows if r[1] in bounds]
    if not usable:
        return {"world_only": len(rows), "converted": 0, "no_bounds": len(rows)}
    swap, a, s = detect_swap([(r[1], r[2], r[3]) for r in usable[:2000]], bounds)
    done = 0
    for rowid, um, wx, wy in usable:
        xy = _convert(bounds[um], wx, wy, swap)
        if _on_map(xy):
            conn.execute("UPDATE rxp_step SET x=?, y=? WHERE rowid=?", (round(xy[0], 2), round(xy[1], 2), rowid))
            done += 1
    return {"world_only": len(rows), "converted": done, "no_bounds": len(rows) - len(usable),
            "columns_swapped": swap, "fit_check": {"as_is_on_map": a, "swapped_on_map": s}}


def _map_of(args):
    for k in ("ui_map", "map", "zone", "mapID", "uimap"):
        v = args.get(k)
        if isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
            return int(v)
    return None


def rxp_actions(conn, bounds):
    """Single positions ({wx, wy} -> + x, y) and world-form point lists ({wpoints: [[wx, wy], ...]} ->
    + points: [[x, y] or null per point]) in rxp_action.args, marked xy_from 'bounds'."""
    rows, lines = [], []
    for rowid, args in conn.execute("""SELECT rowid, args FROM rxp_action
                                       WHERE (args LIKE '%"wx"%' AND args LIKE '%"wy"%') OR args LIKE '%"wpoints"%'"""):
        a = store.jload(args)
        if not isinstance(a, dict) or _map_of(a) not in bounds:
            continue
        if isinstance(a.get("wx"), (int, float)) and isinstance(a.get("wy"), (int, float)) and a.get("x") is None:
            rows.append((rowid, _map_of(a), a))
        elif isinstance(a.get("wpoints"), list) and not a.get("points"):
            lines.append((rowid, _map_of(a), a))
    if not rows and not lines:
        return {"world_only": 0}
    samples = [(um, a["wx"], a["wy"]) for _, um, a in rows[:2000]]
    samples += [(um, p[0], p[1]) for _, um, a in lines for p in a["wpoints"][:20]
                if isinstance(p, list) and len(p) == 2 and None not in p]
    swap, _, _ = detect_swap(samples, bounds)
    done = pts_done = 0
    for rowid, um, a in rows:
        xy = _convert(bounds[um], a["wx"], a["wy"], swap)
        if _on_map(xy):
            a.update(x=round(xy[0], 2), y=round(xy[1], 2), xy_from="bounds")
            conn.execute("UPDATE rxp_action SET args=? WHERE rowid=?", (json.dumps(a, separators=(",", ":")), rowid))
            done += 1
    for rowid, um, a in lines:
        out = []
        for p in a["wpoints"]:
            xy = _convert(bounds[um], p[0], p[1], swap) if isinstance(p, list) and len(p) == 2 and None not in p else (None, None)
            out.append([round(xy[0], 2), round(xy[1], 2)] if _on_map(xy) else None)
        if any(out):
            a.update(points=out, xy_from="bounds")
            conn.execute("UPDATE rxp_action SET args=? WHERE rowid=?", (json.dumps(a, separators=(",", ":")), rowid))
            pts_done += 1
    return {"world_only": len(rows), "converted": done, "world_lines": len(lines), "lines_converted": pts_done,
            "columns_swapped": swap}


def pois(conn, bounds):
    """World-only POIs get the map they are on, in this order: a city map containing the point whose name
    the source's name mentions ("Stormwind, Elwynn" -> Stormwind City); the zone the source named, if it
    contains the point; the smallest map that 'owns' the point (one of its sub-zone boxes contains it; a
    map without boxes owns everything inside its bounds, a continent nothing); the smallest map whose
    bounds contain the point. Map rectangles overlap at zone borders, hence the boxes."""
    boxes = {}
    for um, x1, y1, x2, y2 in conn.execute("SELECT ui_map, x1, y1, x2, y2 FROM area_box"):
        boxes.setdefault(um, []).append((x1, y1, x2, y2))
    parents = {r[0] for r in conn.execute("SELECT DISTINCT parent FROM map WHERE parent IS NOT NULL")}
    by_cont = {}
    for um, b in bounds.items():
        w, h = store.map_size_yd(b)
        by_cont.setdefault(b["continent"], []).append((w * h, um, b))
    for lst in by_cont.values():
        lst.sort()

    huge = {um for um, b in bounds.items() if (lambda wh: wh[0] and wh[1] and wh[0] * wh[1] > 150e6)(
        store.map_size_yd(b))}                         # continent-sized maps (the largest zones are ~70M yd^2)

    def owns(um, xy):
        if (um in parents or um in huge) and um not in boxes:      # continent / world maps
            return False
        if um not in boxes:
            return True
        return any(x1 <= xy[0] <= x2 and y1 <= xy[1] <= y2 for x1, y1, x2, y2 in boxes[um])

    map_name = {r[0]: (r[1] or "").lower() for r in conn.execute("SELECT ui_map, name FROM map")}

    def mentions(poi_name, um):
        city = map_name.get(um, "")
        if not city or not poi_name:
            return False
        for part in poi_name.lower().split(","):
            part = part.strip()
            if part and (city.startswith(part) or part.startswith(city)):
                return True
        return False

    to_map = to_world = outside = 0
    for kind, pid, name, um, cont, wx, wy in conn.execute("""SELECT kind, id, name, ui_map, continent, wx, wy FROM poi
                                                             WHERE wx IS NOT NULL AND wy IS NOT NULL AND x IS NULL""").fetchall():
        if cont is None and um in bounds:
            cont = bounds[um]["continent"]
        inside = []
        for _, m, b in by_cont.get(cont, []) if cont is not None else []:   # smallest first
            xy = store.world_to_map(b, wx, wy)
            if _on_map(xy):
                inside.append((m, xy))
        owners = [(m, xy) for m, xy in inside if owns(m, xy)]
        cities = [(m, xy) for m, xy in owners if m not in boxes and mentions(name, m)]
        named = [(m, xy) for m, xy in inside if m == um]
        pick = (cities or named or owners or inside or [None])[0]
        if pick:
            conn.execute("UPDATE poi SET ui_map=?, x=?, y=? WHERE kind=? AND id=?",
                         (pick[0], round(pick[1][0], 2), round(pick[1][1], 2), kind, pid))
            to_map += 1
        else:
            outside += 1
    for kind, pid, um, x, y in conn.execute("""SELECT kind, id, ui_map, x, y FROM poi WHERE x IS NOT NULL
                                               AND wx IS NULL AND ui_map IS NOT NULL""").fetchall():
        b = bounds.get(um)
        if b:
            wx, wy = store.map_to_world(b, x, y)
            conn.execute("UPDATE poi SET continent=?, wx=?, wy=? WHERE kind=? AND id=?",
                         (b["continent"], round(wx, 2), round(wy, 2), kind, pid))
            to_world += 1
    return {"world_to_map": to_map, "map_to_world": to_world, "world_outside_known_maps": outside}
