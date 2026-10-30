"""Loader 6: world <-> map transforms (map_bounds) and map sizes (map.width_yd / height_yd).

Pairs: glide.db rxp_steps world-form gotos (`.goto 1426/0,A,B`; RestedXP/HereBeDragons order: A = wy, B = wx,
re-parsed from the args string, not from the table's wx/wy columns) paired with the nearest following
`.target` line of the same step (no other goto in between) whose NPC (name or Name::id) has known
spawn points on that UiMap that agree within 1 percent. Fit per map with >= 4 pairs:
    x% = a + b*wy      y% = c + d*wx        (+X is north = map up, +Y is west = map left: b, d < 0)
RANSAC (consensus within 1 percent) seeds the fit, then least squares with outliers > 3 percent removed
until stable. When the client reported the map's size (live pages below) the scale is fixed to it and only
the offsets are fitted (kept unless the residual gets worse); a zone without a client size whose free fit
has the wrong aspect ratio is refitted the same way with the size its continent rectangle implies
(scale 'continent'). conf = clamp(1 - rms/3, 0.3, 0.95), lowered
when few pairs or the aspect ratio (W/H, 1.5 for every UI map texture) is off. Fit statistics per map go
to meta 'map_bounds:fit' (the unconstrained width is compared with the client's as a validation).
Only maps without an exact bound (conf 1: recorder or DB2, below) are fitted: a fit (conf <= 0.95) never
replaces one. GLIDE_MAP_FIT_ALL=1 fits those too, for the statistics only (each with 'exact_err_pct', its
largest corner error against the exact bound in percent of the map size); they are never stored.

Propagation (src inf): the seed's continent maps list each zone's rectangle on the continent (maps.js
'kids'). The continent's own world bounds are solved from the fitted zones of the same instance, and zones
without a direct fit get bounds from their rectangle, only if the fitted zones reproduce through the
continent within 1 percent of their size (validation in meta 'map_bounds:continent').

Exact bounds (src obs, conf 1, override everything): GlideRecorderDB.mapBounds in a recorder
SavedVariables file, {[uiMap] = {instance, x0, y0, x1, y1}} where (x0, y0) = C_Map.GetWorldPosFromMapPos
(uiMap, (0,0)):GetXY() and (x1, y1) the same for (1,1) (GetXY's x is world X = wx). DB2 UiMapAssignment
rows (src db2, from wowfiles/db2_import.py, loaded before this loader) are kept over fits too. The recorder's
are read first, so the fits, the propagation and the rescaling see every exact bound.

Sizes: data/live/*.jsonl pages {"pg":"Z","mm":<ui_map>,"mw":<w>,"mh":<h>} (client values) win; otherwise
the bounds give width = |wy1 - wy0|, height = |wx1 - wx0|.
"""
import collections
import glob
import itertools
import json
import os
import random
import re
import statistics

from knowledge import store
from knowledge.sources import _lua

GOTO_WORLD = re.compile(r"^\s*(\d+)/(\d+)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*(?:,|$)")
ASPECT = 1.5


def load(conn, ctx):
    res = {}
    stats = {}
    live = live_sizes(ctx)
    res["obs_bounds"] = sv_bounds(conn, ctx)
    exact = dict(conn.execute("SELECT ui_map, src FROM map_bounds WHERE conf >= 1"))
    validate = os.environ.get("GLIDE_MAP_FIT_ALL") == "1"
    if ctx.has_glide():
        pairs, pres = collect_pairs(conn, ctx)
        res["pairs"] = pres
        for um, P in sorted(pairs.items()):
            if um in exact and not validate:
                stats[um] = {"n": len(P), "fit": None, "exact": exact[um]}     # a fit could never replace it
                continue
            f = fit(P, size=live.get(um))
            if f is None:
                stats[um] = {"n": len(P), "fit": None}
                continue
            stats[um] = f["stats"]
            if um in exact:                  # GLIDE_MAP_FIT_ALL: statistics only, compared with the exact bound
                stats[um] = dict(f["stats"], exact=exact[um], exact_err_pct=_bounds_err_pct(conn, um, f["bounds"]))
                continue
            cont = collections.Counter(p[5] for p in P).most_common(1)[0][0]
            store.upsert(conn, "map_bounds", dict(f["bounds"], ui_map=um, continent=cont, src="fit",
                                                  conf=f["stats"]["conf"]), ["ui_map"])
        res["continent_check"] = continent_check(conn, pairs)
    else:
        res["pairs"] = {"skipped": "no glide.db"}
    res["live_sizes"] = {str(k): v for k, v in live.items()}
    for um, s in stats.items():
        if um in live and s.get("free_fit_w_yd"):        # validation: the unconstrained fit vs the client's size
            s["free_fit_w_err_pct"] = round(100.0 * (s["free_fit_w_yd"] - live[um][0]) / live[um][0], 2)
    derived = {}
    res["propagated"] = propagate(conn, ctx, stats, derived)
    res["rescaled"] = rescale_off_aspect(conn, pairs if ctx.has_glide() else {}, stats, derived, live)
    res["sizes"] = fill_sizes(conn, live)
    good = {um: s for um, s in stats.items() if s.get("conf") and not s.get("exact")}
    res["fitted_maps"] = len(good)
    res["exact_maps_not_fitted"] = sum(1 for s in stats.values() if s.get("exact") and not s.get("conf"))
    res["fit_rms_pct"] = {str(um): s["rms"] for um, s in sorted(good.items())}
    store.set_meta(conn, "map_bounds:fit", {str(k): v for k, v in sorted(stats.items())})
    res["map_bounds"] = conn.execute("SELECT COUNT(*) FROM map_bounds").fetchone()[0]
    res["by_src"] = dict(conn.execute("SELECT src, COUNT(*) FROM map_bounds GROUP BY src"))
    return res


# ------------------------------------------------------------------------------------------------
# pairs
# ------------------------------------------------------------------------------------------------
def collect_pairs(conn, ctx):
    names = collections.defaultdict(set)
    for cid, name in conn.execute("SELECT id, name FROM creature WHERE name IS NOT NULL"):
        names[name.lower()].add(cid)
    pts = collections.defaultdict(list)
    for cid, um, x, y in conn.execute("""SELECT creature_id, ui_map, x, y FROM creature_spawn
                                         WHERE src != 'rxp' AND NOT (src = 'obs' AND conf < 0.9)"""):   # no 'near' sightings
        pts[(cid, um)].append((x, y))
    g = ctx.glide()
    steps = collections.defaultdict(list)
    for r in g.execute("""SELECT guide_key, step, line, verb, args FROM rxp_steps
                          WHERE verb IN ('goto','target') ORDER BY guide_key, step, line"""):
        steps[(r[0], r[1])].append((r[3], r[4] or ""))
    g.close()
    out = collections.defaultdict(dict)
    stat = collections.Counter()
    for lines in steps.values():
        for i, (verb, args) in enumerate(lines):
            if verb != "goto":
                continue
            m = GOTO_WORLD.match(args)
            if not m:
                continue
            stat["world_gotos"] += 1
            um, cont, wy, wx = int(m.group(1)), int(m.group(2)), float(m.group(3)), float(m.group(4))
            for verb2, args2 in lines[i + 1:]:
                if verb2 == "goto":
                    break                                   # a later goto is nearer to any following target
                if verb2 != "target":
                    continue
                loc = target_location(args2, um, names, pts)
                if loc == "ambiguous":
                    stat["ambiguous_target"] += 1
                    break
                if loc is None:
                    continue
                key = (round(wy, 1), round(wx, 1), loc[2])
                out[um][key] = (wy, wx, loc[0], loc[1], loc[2], cont)
                stat["pairs"] += 1
                break
    pairs = {um: list(d.values()) for um, d in out.items()}
    stat["unique_pairs"] = sum(len(v) for v in pairs.values())
    stat["maps_with_pairs"] = len(pairs)
    return pairs, dict(stat)


def target_location(args, um, names, pts):
    """(x, y, npc) of a target's single known spot on ui_map; None if unknown there; 'ambiguous'."""
    cands = []
    for name in [args] + [a for a in args.split(",") if a.strip() and a != args]:
        name = name.strip().lstrip("+")
        if "::" in name:
            nm, _, idp = name.partition("::")
            ids = {int(idp)} if idp.strip().isdigit() else names.get(nm.strip().lower(), set())
        else:
            ids = names.get(name.lower(), set())
        cands = [(i, pts[(i, um)]) for i in ids if (i, um) in pts]
        if cands:
            break
    if not cands:
        return None
    allp = [p for _, lst in cands for p in lst]
    mx, my = statistics.median(p[0] for p in allp), statistics.median(p[1] for p in allp)
    if any(abs(p[0] - mx) > 1.0 or abs(p[1] - my) > 1.0 for p in allp):
        return "ambiguous"
    return mx, my, cands[0][0] if len(cands) == 1 else -1


# ------------------------------------------------------------------------------------------------
# fit
# ------------------------------------------------------------------------------------------------
def _lsq(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return my - b * mx, b


def _resid(P, a, b, c, d):
    return [((a + b * p[0] - p[2]) ** 2 + (c + d * p[1] - p[3]) ** 2) ** 0.5 for p in P]


def _refine(P, a, b, c, d, reject, fixed_scale=False):
    """Iterate: inliers = residual <= reject; refit (offsets only when the scale is fixed)."""
    keep = None
    for _ in range(20):
        r = _resid(P, a, b, c, d)
        new = [k for k in range(len(P)) if r[k] <= reject]
        if len(new) < 4:
            return None
        if new == keep:
            break
        keep = new
        if fixed_scale:
            a = sum(P[k][2] - b * P[k][0] for k in keep) / len(keep)
            c = sum(P[k][3] - d * P[k][1] for k in keep) / len(keep)
        else:
            fx = _lsq([P[k][0] for k in keep], [P[k][2] for k in keep])
            fy = _lsq([P[k][1] for k in keep], [P[k][3] for k in keep])
            if not fx or not fy:
                return None
            (a, b), (c, d) = fx, fy
    if b >= 0 or d >= 0:
        return None
    ks = set(keep)
    r = [x for k, x in enumerate(_resid(P, a, b, c, d)) if k in ks]
    return (a, b, c, d), keep, r, (sum(x * x for x in r) / len(r)) ** 0.5


def fit(P, seed_tol=1.0, reject=3.0, size=None):
    """P: [(wy, wx, x, y, npc, continent)] -> {'bounds': {...}, 'stats': {...}} or None.
    size: (width_yd, height_yd) reported by the client for this map; when given, the scale is fixed to it
    and only the offsets are fitted, unless that raises the rms residual by more than 0.25 percent."""
    n = len(P)
    if n < 4:
        return None
    idx = list(itertools.combinations(range(n), 2))
    if len(idx) > 3000:
        idx = random.Random(n).sample(idx, 3000)
    best = None
    for i, j in idx:
        pi, pj = P[i], P[j]
        if abs(pi[0] - pj[0]) < 20 or abs(pi[1] - pj[1]) < 20:
            continue
        b = (pi[2] - pj[2]) / (pi[0] - pj[0])
        d = (pi[3] - pj[3]) / (pi[1] - pj[1])
        if b >= 0 or d >= 0:
            continue
        a, c = pi[2] - b * pi[0], pi[3] - d * pi[1]
        r = _resid(P, a, b, c, d)
        inl = sum(1 for x in r if x <= seed_tol)
        score = (inl, -sum(min(x, seed_tol) for x in r))
        if best is None or score > best[0]:
            best = (score, (a, b, c, d))
    if best is None or best[0][0] < 3:
        return None
    free = _refine(P, *best[1], reject)
    if free is None:
        return None
    scale = "fit"
    (a, b, c, d), keep, r, rms = free
    free_w = 100.0 / abs(b)
    if size and size[0] > 0 and size[1] > 0:
        fixed = _refine(P, a, -100.0 / size[0], c, -100.0 / size[1], reject, fixed_scale=True)
        if fixed is not None and fixed[3] <= rms + 0.25:
            (a, b, c, d), keep, r, rms = fixed
            scale = "client"
    w, h = 100.0 / abs(b), 100.0 / abs(d)
    aspect = w / h
    conf = max(0.3, min(0.95, 1.0 - rms / 3.0))
    if len(keep) < 8:
        conf *= 0.85
    if abs(aspect - ASPECT) > 0.05 * ASPECT:
        conf *= 0.6
    stats = {"n": n, "inliers": len(keep), "rms": round(rms, 3), "median": round(statistics.median(r), 3),
             "max": round(max(r), 3), "aspect": round(aspect, 3), "w_yd": round(w, 1), "h_yd": round(h, 1),
             "scale": scale, "free_fit_w_yd": round(free_w, 1), "conf": round(conf, 3)}
    bounds = {"wy0": -a / b, "wy1": (100.0 - a) / b, "wx0": -c / d, "wx1": (100.0 - c) / d}
    return {"bounds": {k: round(v, 3) for k, v in bounds.items()}, "stats": stats}


def _bounds_err_pct(conn, um, b):
    """Largest corner difference between fitted bounds `b` and the stored bounds of `um`, in percent of its size."""
    r = conn.execute("SELECT wx0, wy0, wx1, wy1 FROM map_bounds WHERE ui_map=?", (um,)).fetchone()
    w, h = abs(r[3] - r[1]) or 1.0, abs(r[2] - r[0]) or 1.0
    return round(100.0 * max(abs(b["wy0"] - r[1]) / w, abs(b["wy1"] - r[3]) / w,
                             abs(b["wx0"] - r[0]) / h, abs(b["wx1"] - r[2]) / h), 2)


def continent_check(conn, pairs):
    """Instance ids the guides use per map vs the map table's continent (and fill missing ones)."""
    mism, filled = {}, 0
    cur = dict(conn.execute("SELECT ui_map, continent FROM map"))
    for um, P in pairs.items():
        used = collections.Counter(p[5] for p in P).most_common(1)[0][0]
        if um in cur and cur[um] is None:
            conn.execute("UPDATE map SET continent=? WHERE ui_map=?", (used, um))
            filled += 1
        elif um in cur and cur[um] != used:
            mism[str(um)] = {"map": cur[um], "guides": used}
    return {"mismatch": mism, "filled": filled}


# ------------------------------------------------------------------------------------------------
# propagation through the continent maps' zone rectangles
# ------------------------------------------------------------------------------------------------
def rescale_off_aspect(conn, pairs, stats, derived, live, tol=0.01):
    """Zones whose free fit has a wrong aspect ratio (W/H != 1.5 by more than 1%) and no client size: refit
    with the scale the continent rectangle implies; kept when the residual does not get worse."""
    out = {}
    for um, s in stats.items():
        if not s.get("conf") or s.get("exact") or um in live or um not in derived \
                or abs(s["aspect"] - ASPECT) <= tol * ASPECT:
            continue
        w, h = store.map_size_yd(derived[um])
        f = fit(pairs[um], size=(w, h))
        if f is None or f["stats"]["scale"] != "client":
            out[str(um)] = "kept free fit"
            continue
        f["stats"]["scale"] = "continent"
        f["stats"]["free_aspect"] = s["aspect"]
        cont = collections.Counter(p[5] for p in pairs[um]).most_common(1)[0][0]
        conn.execute("DELETE FROM map_bounds WHERE ui_map=?", (um,))
        store.upsert(conn, "map_bounds", dict(f["bounds"], ui_map=um, continent=cont, src="fit",
                                              conf=f["stats"]["conf"]), ["ui_map"])
        stats[um] = dict(f["stats"], free_fit_w_yd=s.get("w_yd"))
        out[str(um)] = {"free_w_yd": s.get("w_yd"), "w_yd": f["stats"]["w_yd"], "rms": f["stats"]["rms"],
                        "free_rms": s["rms"]}
    return out


def propagate(conn, ctx, stats, derived, max_err_pct=1.0):
    seed = ctx.load_seed() or {}
    smaps = seed.get("maps") or {}
    bounds = {r[0]: {"wx0": r[1], "wy0": r[2], "wx1": r[3], "wy1": r[4], "src": r[5], "conf": r[6]}
              for r in conn.execute("SELECT ui_map, wx0, wy0, wx1, wy1, src, conf FROM map_bounds")}
    cont_of = dict(conn.execute("SELECT ui_map, continent FROM map"))
    report = {}
    for pid, pm in smaps.items():
        pid = int(pid)
        if pm.get("t") != 2 or not pm.get("kids"):
            continue
        inst = cont_of.get(pid)
        kids = {int(k[0]): k[1:5] for k in pm["kids"] if len(k) >= 5 and cont_of.get(int(k[0])) == inst}
        known = [(k, rect) for k, rect in kids.items() if k in bounds and bounds[k]["src"] in ("fit", "obs", "db2")]
        if len(known) < 3:
            report[str(pid)] = {"skipped": "only %d zones with direct bounds" % len(known)}
            continue
        sol = {}
        for axis, lo, hi in (("wy", 0, 2), ("wx", 1, 3)):
            xs, ys, ws = [], [], []
            for k, rect in known:
                wt = bounds[k]["conf"] or 0.5
                xs += [rect[lo], rect[hi]]
                ys += [bounds[k][axis + "0"], bounds[k][axis + "1"]]
                ws += [wt, wt]
            sol[axis] = _wlsq(xs, ys, ws)            # world = P0 + u * S
        if not sol["wy"] or not sol["wx"]:
            continue
        errs = {}
        for k, rect in known:
            pred = _child(sol, rect)
            w = abs(bounds[k]["wy1"] - bounds[k]["wy0"])
            h = abs(bounds[k]["wx1"] - bounds[k]["wx0"])
            e = max(abs(pred["wy0"] - bounds[k]["wy0"]) / w, abs(pred["wy1"] - bounds[k]["wy1"]) / w,
                    abs(pred["wx0"] - bounds[k]["wx0"]) / h, abs(pred["wx1"] - bounds[k]["wx1"]) / h) * 100
            errs[k] = round(e, 2)
        med = statistics.median(errs.values())
        good = [k for k, e in errs.items() if e <= max_err_pct]
        rep = {"zones_used": len(known), "median_err_pct": med, "max_err_pct": max(errs.values()),
               "err_pct": {str(k): v for k, v in sorted(errs.items())}}
        if len(good) >= 3 and len(good) < len(known):          # refit without zones that disagree
            known = [(k, r) for k, r in known if k in good]
            for axis, lo, hi in (("wy", 0, 2), ("wx", 1, 3)):
                sol[axis] = _wlsq([r[lo] for _, r in known] + [r[hi] for _, r in known],
                                  [bounds[k][axis + "0"] for k, _ in known] + [bounds[k][axis + "1"] for k, _ in known],
                                  [bounds[k]["conf"] or 0.5 for k, _ in known] * 2)
            rep["refit_without"] = sorted(set(errs) - set(good))
        if med > max_err_pct:
            rep["skipped"] = "zones disagree with the continent rectangles (median %.2f%%)" % med
            report[str(pid)] = rep
            continue
        pb = {"wy0": sol["wy"][0], "wy1": sol["wy"][0] + sol["wy"][1], "wx0": sol["wx"][0], "wx1": sol["wx"][0] + sol["wx"][1]}
        if pid not in bounds:
            store.upsert(conn, "map_bounds", dict({k: round(v, 3) for k, v in pb.items()}, ui_map=pid, continent=inst,
                                                  src="fit", conf=0.8), ["ui_map"])
        added = []
        for k, rect in kids.items():
            derived[k] = _child(sol, rect)
            if k in bounds:
                continue
            cb = _child(sol, rect)
            store.upsert(conn, "map_bounds", dict({kk: round(v, 3) for kk, v in cb.items()}, ui_map=k, continent=inst,
                                                  src="inf", conf=0.6), ["ui_map"])
            added.append(k)
            stats.setdefault(k, {"derived_from": pid})
        rep["derived"] = added
        report[str(pid)] = rep
    store.set_meta(conn, "map_bounds:continent", report)
    return {k: {kk: vv for kk, vv in v.items() if kk != "err_pct"} for k, v in report.items()}


def _wlsq(us, ys, ws):
    sw = sum(ws)
    mu = sum(w * u for u, w in zip(us, ws)) / sw
    my = sum(w * y for y, w in zip(ys, ws)) / sw
    suu = sum(w * (u - mu) ** 2 for u, w in zip(us, ws))
    if suu <= 0:
        return None
    s = sum(w * (u - mu) * (y - my) for u, y, w in zip(us, ys, ws)) / suu
    return my - s * mu, s


def _child(sol, rect):
    (py, sy), (px, sx) = sol["wy"], sol["wx"]
    return {"wy0": py + rect[0] * sy, "wy1": py + rect[2] * sy, "wx0": px + rect[1] * sx, "wx1": px + rect[3] * sx}


# ------------------------------------------------------------------------------------------------
# exact bounds from the recorder, sizes
# ------------------------------------------------------------------------------------------------
def sv_bounds(conn, ctx):
    files = [f for f in ctx.recorder_sv_files() if _mentions(f, b"mapBounds")]
    if not files:
        return {"skipped": "no GlideRecorderDB.mapBounds in recorder SavedVariables yet"}
    n = 0
    for f in files:
        try:
            # only the mapBounds table is parsed: a full parse of each ~5 MB recorder file took ~0.85 s (the
            # schema warning of _lua.recorder_db comes from build_db, which reads these same files whole)
            mb = _lua.read_saved_variable_field(f, "GlideRecorderDB", "mapBounds") or {}
        except Exception as e:  # a corrupt SV file must not stop the build
            ctx.notes.append("map_bounds: cannot parse %s: %s" % (f, e))
            continue
        items = mb.items() if isinstance(mb, dict) else enumerate(mb, 1)
        for um, v in items:
            if isinstance(v, dict):
                v = [v.get("instance"), v.get("x0"), v.get("y0"), v.get("x1"), v.get("y1")]
            if not isinstance(v, list) or len(v) < 5 or any(not isinstance(x, (int, float)) for x in v[:5]):
                continue
            store.upsert(conn, "map_bounds", {"ui_map": int(um), "continent": int(v[0]), "wx0": v[1], "wy0": v[2],
                                              "wx1": v[3], "wy1": v[4], "src": "obs", "conf": 1.0}, ["ui_map"])
            conn.execute("UPDATE map_bounds SET src='obs' WHERE ui_map=?", (int(um),))
            n += 1
    return {"rows": n, "files": len(files)}


def _mentions(path, needle):
    try:
        with open(path, "rb") as fh:
            return needle in fh.read()
    except OSError:
        return False


def live_sizes(ctx):
    sizes = {}
    for f in sorted(glob.glob(os.path.join(ctx.live, "*.jsonl"))):
        try:
            fh = open(f, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"mw"' not in line:
                    continue
                try:
                    p = json.loads(line).get("page") or {}
                except ValueError:
                    continue
                if p.get("pg") != "Z":
                    continue
                try:
                    um, w, h = int(p.get("mm")), float(p.get("mw")), float(p.get("mh"))
                except (TypeError, ValueError):
                    continue
                if w > 0 and h > 0:
                    sizes[um] = (w, h)
    return sizes


def fill_sizes(conn, live):
    n_live = n_b = 0
    for um, (w, h) in live.items():
        if conn.execute("SELECT 1 FROM map WHERE ui_map=?", (um,)).fetchone():
            conn.execute("UPDATE map SET width_yd=?, height_yd=? WHERE ui_map=?", (w, h, um))
            n_live += 1
    for r in conn.execute("SELECT ui_map, wx0, wy0, wx1, wy1 FROM map_bounds").fetchall():
        if r[0] in live:
            continue
        w, h = store.map_size_yd({"wx0": r[1], "wy0": r[2], "wx1": r[3], "wy1": r[4]})
        conn.execute("UPDATE map SET width_yd=?, height_yd=? WHERE ui_map=?", (round(w, 1), round(h, 1), r[0]))
        n_b += 1
    return {"from_live": n_live, "from_bounds": n_b}
