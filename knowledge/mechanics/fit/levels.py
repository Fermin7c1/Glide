"""The XP table: recorded level-ups and XP bars against Classic's table."""
import collections
import math

from knowledge.mechanics import params as P
from knowledge.mechanics.fit.common import as_int


BRIEF_TABLE = [400, 900, 1400, 2100, 2800, 3600, 4500, 5400, 6500, 7600, 8700, 9800, 11000, 12300, 13600, 15000,
               16400, 17800, 19300, 20800, 22400, 24000, 25500, 27200, 28900, 30500, 32200, 33900, 35900, 37800, 39700,
               41600, 43600, 45600, 47600, 49800, 51800, 53900, 56100, 58300, 60500, 62800, 65100, 67500, 69900, 72400,
               74900, 77400, 80000, 82600, 85300, 88100, 90800, 93700, 96600, 99500, 102500, 105500, 108600]


def _formula_xp(level):
    diff = 0 if level <= 28 else 1 if level == 29 else 3 if level == 30 else 6 if level == 31 else 5 * (level - 30)
    v = (8 * level + diff) * (45 + 5 * level)
    return int(math.floor(v / 100.0 + 0.5)) * 100


def level_section(conn, live, now):
    """The XP table section -> (section, overrides)."""
    obs = collections.defaultdict(collections.Counter)
    for lv, mx, n in conn.execute("SELECT lvl, max, count(*) FROM obs_xp WHERE max IS NOT NULL GROUP BY lvl, max"):
        obs[lv][mx] += n
    lobs = collections.defaultdict(collections.Counter)
    for _, _, c in live.frames:
        lv, mx = as_int(c.get("l")), as_int(c.get("X"))
        if lv and mx:
            lobs[lv][mx] += 1
    rows, ov = [], {}
    table = [P.classic("xp.level.%d" % lv) for lv in range(1, 60)]
    for lv in range(1, 60):
        seen = collections.Counter(obs.get(lv, {})) + collections.Counter(lobs.get(lv, {}))
        if not seen:
            continue
        val, n = seen.most_common(1)[0]
        cls = table[lv - 1]
        rows.append({"level": lv, "observed": dict(seen), "classic": cls, "brief_table": BRIEF_TABLE[lv - 1],
                     "agree": val == cls and len(seen) == 1, "n_obs_xp": sum(obs.get(lv, {}).values()),
                     "n_live": sum(lobs.get(lv, {}).values())})
        if len(seen) == 1 and n >= 3:
            ov["xp.level.%d" % lv] = {
                "value": val, "status": "forever-verified", "updated": now,
                "evidence": "UnitXPMax at level %d = %d (%d obs_xp rows, %d live frames)%s" % (
                    lv, val, sum(obs.get(lv, {}).values()), sum(lobs.get(lv, {}).values()),
                    "" if val == cls else "; differs from Classic %d" % cls)}
    shape = {"sum_1_59": sum(table), "monotone": all(b > a for a, b in zip(table, table[1:])),
             "formula_mismatch": [lv for lv in range(1, 60) if _formula_xp(lv) != table[lv - 1]],
             "brief_sum": sum(BRIEF_TABLE),
             "brief_differs_from_classic_at": [lv for lv in range(1, 60) if BRIEF_TABLE[lv - 1] != table[lv - 1]][:5],
             "brief_contradicted_by_obs": [r["level"] for r in rows if r["brief_table"] not in r["observed"]]}
    return {"rows": rows, "shape": shape, "verified_levels": sorted(int(k.split(".")[-1]) for k in ov)}, ov
