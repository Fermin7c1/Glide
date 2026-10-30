"""Loader 7: observed movement -> travel_edge rows and the observed run-speed distribution.

obs_trail has one point every ~3 s while the character moves (a point is skipped when it moved < 0.25%).
A segment is two consecutive points of one session on the same map, both out of combat, at most
MAX_GAP_S apart. Its length in yards needs the map size (map.width_yd/height_yd, from the client or the
fitted bounds); without it the edge keeps dist_yd NULL. Segments faster than MAX_SPEED (taxi, teleport,
map-position jumps) are dropped. Edges are aggregated by rounding both endpoints to 1-percent cells
(x1, y1, x2, y2 are the cell centres); segments that stay inside one cell only feed the speed statistics.
The speed summary goes to meta 'run_speed' (yards/second, all moving segments of 2..7 s).
"""
import collections
import statistics

from knowledge import store

MAX_GAP_S = 10
MAX_SPEED = 30.0        # yd/s; ground mounts are ~ 11-14, anything above is a taxi or a teleport


def load(conn, ctx):
    trail = conn.execute("SELECT session, t, ui_map, x, y, combat FROM obs_trail ORDER BY session, t").fetchall()
    if not trail:
        return {"skipped": "no obs_trail rows"}
    size = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT ui_map, width_yd, height_yd FROM map WHERE width_yd > 0 AND height_yd > 0")}
    agg = collections.defaultdict(lambda: [0, 0.0, 0, 0.0])     # key -> [n, sum_t, n_dist, sum_dist]
    speeds, segs, dropped, no_size, self_loops = [], 0, 0, set(), 0
    for p, c in zip(trail, trail[1:]):
        if p[0] != c[0] or p[2] != c[2] or p[2] is None or p[5] or c[5]:
            continue
        dt = c[1] - p[1]
        if dt <= 0 or dt > MAX_GAP_S or None in (p[3], p[4], c[3], c[4]):
            continue
        dist = None
        if p[2] in size:
            w, h = size[p[2]]
            dist = (((c[3] - p[3]) * w / 100.0) ** 2 + ((c[4] - p[4]) * h / 100.0) ** 2) ** 0.5
            if dist / dt > MAX_SPEED:
                dropped += 1
                continue
            if 2 <= dt <= 7 and dist >= 3:
                speeds.append(dist / dt)
        else:
            no_size.add(p[2])
        segs += 1
        key = (p[2], round(p[3]), round(p[4]), round(c[3]), round(c[4]))
        if key[1:3] == key[3:5]:
            self_loops += 1
            continue
        a = agg[key]
        a[0] += 1
        a[1] += dt
        if dist is not None:
            a[2] += 1
            a[3] += dist
    rows = [(k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]),
             round(a[3] / a[2], 1) if a[2] else None, round(a[1] / a[0], 2), a[0], "obs") for k, a in agg.items()]
    store.insert_rows(conn, "travel_edge", rows, ["ui_map", "x1", "y1", "x2", "y2", "dist_yd", "time_s", "n_obs", "src"])
    summary = speed_summary(speeds)
    store.set_meta(conn, "run_speed", summary)
    return {"rows": len(rows), "segments": segs, "within_one_cell": self_loops, "dropped_too_fast": dropped,
            "maps_without_size": sorted(no_size), "run_speed": summary}


def speed_summary(speeds):
    if not speeds:
        return {"n": 0}
    s = sorted(speeds)

    def pct(q):
        return round(s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))], 2)
    return {"n": len(s), "unit": "yd/s", "p10": pct(0.1), "p25": pct(0.25), "median": pct(0.5), "p75": pct(0.75),
            "p90": pct(0.9), "mean": round(statistics.fmean(s), 2),
            "note": "straight-line distance between trail points ~3 s apart: curves and stops lower the "
                    "low percentiles; the upper percentiles approach the true run speed"}
