"""Movement speeds: GetUnitSpeed plateaus in the live logs and the recorded trail."""
import collections
import math

from knowledge.mechanics.fit.common import as_int, median_of, rounded


SPEED_CANDS = {"run": 7.0, "walk": 2.5, "swim": 4.722, "ghost x1.25": 8.75, "stealth x0.5": 3.5, "sprint x1.5": 10.5,
               "mount x1.6": 11.2, "mount x2": 14.0}


def _snap(v10):
    """GetUnitSpeed*10 floored -> the speed: a known candidate inside [v/10, (v+1)/10) or the bin middle."""
    lo, hi = v10 / 10.0, (v10 + 1) / 10.0
    for name, s in SPEED_CANDS.items():
        if lo <= s < hi:
            return s, name
    return round((lo + hi) / 2, 3), None


def speed_section(conn, live, now):
    """The movement speed section -> (section, overrides)."""
    sizes = dict(live.maps)
    ghost = collections.defaultdict(list)
    dead_at = {}
    for s, t, ev in conn.execute("SELECT session, t, event FROM obs_events WHERE event IN ('death','unghost') ORDER BY session, t"):
        if ev == "death":
            dead_at[s] = t
        elif s in dead_at:
            ghost[s].append((dead_at.pop(s), t))
    for s, t in dead_at.items():
        ghost[s].append((t, t + 3600))
    speeds = []
    prev = None
    for row in conn.execute("SELECT session, t, ui_map, x, y, combat FROM obs_trail ORDER BY session, t"):
        if prev and prev[0] == row[0] and prev[2] == row[2] and row[2] in sizes and not row[5] and not prev[5]:
            dt = row[1] - prev[1]
            in_ghost = any(a - 1 <= prev[1] <= b + 1 for a, b in ghost.get(row[0], ()))
            if 2 <= dt <= 4 and not in_ghost:
                w, h = sizes[row[2]]
                d = math.hypot((row[3] - prev[3]) / 100.0 * w, (row[4] - prev[4]) / 100.0 * h)
                speeds.append(d / dt)
        prev = row
    hist = collections.Counter(int(v * 4) for v in speeds if 0.25 <= v < 15)
    mode = None
    if hist:
        cands = [(sum(hist.get(b + j, 0) for j in (-1, 0, 1)), b) for b in hist if 16 <= b <= 48]
        if cands:
            mode = (max(cands)[1] + 0.5) / 4
    near = [v for v in speeds if mode and abs(v - mode) <= 0.15 * mode]
    trail = {"n_segments": len(speeds), "mode": rounded(mode, 2), "median_near_mode": rounded(median_of(near), 3), "n_near_mode": len(near),
             "maps_with_size": {str(k): v for k, v in sorted(sizes.items())},
             "note": "3 s trail points out of combat, ghost windows excluded; integer timestamps add +-1 s jitter"}
    # GetUnitSpeed plateaus from the live logs
    plate = collections.defaultdict(collections.Counter)
    pos = []
    for idx in range(len(live.frames)):
        t, f, c = live.frames[idx]
        sp = as_int(c.get("sp"))
        if sp is None or sp <= 0:
            continue
        state = "taxi" if c.get("tx") == "1" else "ghost" if c.get("d") == "2" else "mounted" if c.get("mo") == "1" else "alive"
        plate[state][sp] += 1
        if idx and sp == 70:
            t0, f0, c0 = live.frames[idx - 1]
            if f0 == f and as_int(c0.get("sp")) == 70 and c0.get("mp") == c.get("mp") and 0.8 <= t - t0 <= 1.3:
                m = as_int(c.get("mp"))
                if m in sizes and as_int(c.get("px")) is not None and as_int(c0.get("px")) is not None:
                    w, h = sizes[m]
                    d = math.hypot((as_int(c["px"]) - as_int(c0["px"])) / 10000.0 * w, (as_int(c["py"]) - as_int(c0["py"])) / 10000.0 * h)
                    pos.append(d / (t - t0))
    plateaus = {}
    for state, cnt in plate.items():
        rows = []
        for v, n in cnt.most_common(6):
            if n >= 5:
                s, name = _snap(v)
                rows.append({"sp10": v, "frames": n, "yd_s": s, "label": name})
        plateaus[state] = rows
    live_pos = {"n": len(pos), "median": rounded(median_of(pos), 3),
                "note": "1 s frame pairs both at GetUnitSpeed 7.0: position change / time (checks the map sizes)"}
    ov = {}

    def top(state):
        rows = plateaus.get(state) or []
        return rows[0] if rows else None

    run = top("alive")
    if run and run["sp10"] == 70 and run["frames"] >= 100:
        ov["travel.run_speed"] = {"value": 7.0, "status": "forever-verified", "updated": now,
                                  "evidence": "GetUnitSpeed 7.0 in %d moving frames; trail fit mode %s yd/s (n=%d); "
                                              "1 s position deltas median %s yd/s (n=%d)" % (
                                                  run["frames"], trail["mode"], len(speeds), live_pos["median"], len(pos))}
    if run and run["sp10"] == 70 and len(pos) >= 1000 and trail["median_near_mode"] and \
            abs(trail["median_near_mode"] - live_pos["median"]) <= 0.02 * live_pos["median"]:
        ratio = round(live_pos["median"] / 7.0, 3)
        ov["travel.plane_speed_ratio"] = {
            "value": ratio, "status": "forever-fitted", "updated": now,
            "evidence": "running at GetUnitSpeed 7.0 covers %.3f yd/s of map distance (1 s live pairs n=%d; 3 s "
                        "trail segments %.3f yd/s n=%d): slopes and turns; map sizes from the client (Z pages)" % (
                            live_pos["median"], len(pos), trail["median_near_mode"], trail["n_near_mode"])}
    g = top("ghost")
    if g and g["frames"] >= 30:
        ov["travel.ghost_speed"] = {"value": g["yd_s"], "status": "forever-verified", "updated": now,
                                    "evidence": "GetUnitSpeed while a ghost: %d frames at %.1f..%.1f yd/s (x1.25 run)" % (
                                        g["frames"], g["sp10"] / 10.0, (g["sp10"] + 1) / 10.0)}
    tx = top("taxi")
    if tx and tx["frames"] >= 30:
        ov["travel.taxi_speed"] = {"value": tx["yd_s"], "status": "forever-verified", "updated": now,
                                   "evidence": "GetUnitSpeed on a flight path: %d frames at %.1f..%.1f yd/s "
                                               "(instantaneous; one flight observed)" % (
                                                   tx["frames"], tx["sp10"] / 10.0, (tx["sp10"] + 1) / 10.0)}
    return {"trail": trail, "get_unit_speed": plateaus, "live_positions": live_pos}, ov
