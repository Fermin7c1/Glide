"""Loader 5: final quest.xp reconciliation.

quest.xp is the FULL reward (player at or below quest level + 5). Precedence, highest first:
  1. observed on Forever at a player level <= quest level + 5 (quest_xp_obs)        xp_src obs, xp_conf 1.0
  2. community values already on the row (glide.db field source)                     xp_conf = source conf
  3. observed above that, back-computed through the Classic over-level factor
     (+6: 80%, +7: 60%, +8: 40%, +9: 20%, +10 and more: 10%), only when 2 is missing   xp_src obs, xp_conf 0.7
  4. quest_xp_table[level][xp_difficulty] * xp_multiplier, when the client cache gave the difficulty and
     the table cell is known (QuestXP.csv from DB2: xp_src db2, conf 0.9; otherwise the table is fitted
     from the known rewards: the most common value per (level, difficulty) cell backed by >= 3 quests
     with >= 70% agreement, xp_src fit, conf 0.6)
Disagreements between observations and the raw community values (seed: fg, frc, whz, whd) are reported
(possible Forever changes). Quests whose client-cache RewardXPMultiplier is not 1 (on Forever: dungeon
quests, x1.75 .. x2.675) while the community value equals the plain table value keep that value but get
xp_conf 0.4 and are listed in meta 'quest_xp:multiplier': the community data most likely misses the
Forever multiplier, and no observation says yet how it applies. The table fill skips those quests.
"""
import collections
import math
import statistics

from knowledge import store

OVER_LEVEL = {6: 0.8, 7: 0.6, 8: 0.4, 9: 0.2}


def modern_round(xp):
    """Rounding the Forever server applies to full quest XP (finding, see meta 'quest_xp:rounding'):
    <= 100 to 5, <= 500 to 10, <= 1000 to 25, above to 50 (the last step is not observed yet)."""
    step = 5 if xp <= 100 else 10 if xp <= 500 else 25 if xp <= 1000 else 50
    return int(math.floor(xp / step + 0.5) * step)


def factor(quest_level, player_level):
    d = player_level - quest_level
    return 1.0 if d <= 5 else OVER_LEVEL.get(d, 0.1)


def load(conn, ctx):
    q = {r[0]: {"level": r[1], "xp": r[2], "src": r[3], "conf": r[4], "diff": r[5], "mult": r[6]}
         for r in conn.execute("SELECT id, level, xp, xp_src, xp_conf, xp_difficulty, xp_multiplier FROM quest")}
    full, back, skipped = collections.defaultdict(list), collections.defaultdict(list), 0
    for qid, pl, xp in conn.execute("SELECT quest_id, player_level, xp FROM quest_xp_obs WHERE xp IS NOT NULL"):
        rec = q.get(qid)
        if not rec or not rec["level"] or rec["level"] <= 0 or pl is None:
            skipped += 1
            continue
        f = factor(rec["level"], pl)
        (full if f == 1.0 else back)[qid].append(xp if f == 1.0 else round(xp / f))
    res = {"obs_quests": len(set(full) | set(back)), "obs_unusable": skipped}
    agree, disagree, changed = 0, [], 0
    for qid, vals in full.items():
        rec = q[qid]
        v = collections.Counter(vals).most_common(1)[0][0]
        if rec["xp"] is not None and rec["src"] != "obs":
            if rec["xp"] == v:
                agree += 1
            else:
                disagree.append({"quest": qid, "obs": v, "community": rec["xp"], "src": rec["src"]})
        if rec["xp"] != v or rec["src"] != "obs" or (rec["conf"] or 0) < 1.0:
            conn.execute("UPDATE quest SET xp=?, xp_src='obs', xp_conf=1.0, src=? WHERE id=?",
                         (v, store.merge_src(conn.execute("SELECT src FROM quest WHERE id=?", (qid,)).fetchone()[0], "obs"), qid))
            rec.update(xp=v, src="obs", conf=1.0)
            changed += 1
    community = community_xp(ctx)
    agree_raw, disagree_raw, explained, max_obs = 0, [], 0, 0
    for qid, vals in full.items():
        v = collections.Counter(vals).most_common(1)[0][0]
        max_obs = max(max_obs, v)
        for src_tag, cv in sorted((community.get(qid) or {}).items()):
            if cv == v:
                agree_raw += 1
            else:
                disagree_raw.append({"quest": qid, "obs": v, src_tag: cv})
            explained += 1 if modern_round(cv) == v else 0
    off_grid = sum(1 for d in community.values() for cv in d.values() if modern_round(cv) != cv)
    res["rounding"] = {"rule": "observed = round(community, 5 if <=100, 10 if <=500, 25 if <=1000, else 50)",
                       "explained": explained, "comparisons": agree_raw + len(disagree_raw),
                       "max_observed_xp": max_obs, "community_values_off_that_grid": off_grid,
                       "applied": False}
    store.set_meta(conn, "quest_xp:rounding", res["rounding"])
    res.update(obs_full_value=len(full), agree_with_row_value=agree, disagree_with_row_value=len(disagree),
               community_values_agree=agree_raw, community_values_disagree=len(disagree_raw),
               community_disagreements=disagree_raw[:30], updated_from_obs=changed)
    back_used = 0
    for qid, vals in back.items():
        rec = q[qid]
        if qid in full or (rec["xp"] is not None and (rec["conf"] or 0) >= 0.7):
            continue
        v = int(round(statistics.median(vals)))
        conn.execute("UPDATE quest SET xp=?, xp_src='obs', xp_conf=0.7 WHERE id=?", (v, qid))
        rec.update(xp=v, src="obs", conf=0.7)
        back_used += 1
    res["back_computed"] = back_used
    res["xp_table"] = fill_from_table(conn, q)
    res["multiplier"] = multiplier_check(conn, q)
    total = conn.execute("SELECT COUNT(*) FROM quest").fetchone()[0]
    by_src = dict(conn.execute("SELECT COALESCE(xp_src, '-'), COUNT(*) FROM quest WHERE xp IS NOT NULL GROUP BY 1"))
    with_xp = sum(by_src.values())
    store.set_meta(conn, "quests_with_xp", {"total": total, "with_xp": with_xp, "by_src": by_src})
    res.update(quests=total, with_xp=with_xp, by_src=by_src)
    return res


def community_xp(ctx):
    """quest id -> {source tag: full XP} straight from the seed (glide.db already merged obs over these)."""
    seed = ctx.load_seed() or {}
    src = seed.get("sources") or {}
    out = collections.defaultdict(dict)

    def put(qid, tag, xp):
        try:
            qid, xp = int(qid), int(xp)
        except (TypeError, ValueError):
            return
        out[qid][tag] = xp
    for qid, r in (src.get("fg_quests") or {}).items():
        put(qid, "fg", (r or {}).get("xp"))
    for qid, r in ((src.get("frc") or {}).get("quests") or {}).items():
        put(qid, "frc", (r or {}).get("xp"))
    for r in src.get("whz_list") or []:
        put(r.get("id"), "whz", r.get("xp"))
    for dung in src.get("whd") or []:
        for r in dung.get("quests") or []:
            put(r.get("id"), "whd", r.get("xp"))
    return out


def _is_one(m):
    return m is None or abs(m - 1.0) < 1e-6


def multiplier_check(conn, q):
    table = {r[0]: list(r[1:11]) for r in conn.execute("SELECT level, d0, d1, d2, d3, d4, d5, d6, d7, d8, d9 FROM quest_xp_table")}
    types = dict(conn.execute("SELECT id, type FROM quest"))
    rows, suspect = [], []
    for qid, rec in q.items():
        if _is_one(rec["mult"]):
            continue
        base = (table.get(rec["level"]) or [None] * 10)[rec["diff"]] if rec["diff"] is not None else None
        rows.append(qid)
        if rec["xp"] is not None and rec["src"] != "obs" and base and abs(rec["xp"] - base) <= 0.02 * base:
            conn.execute("UPDATE quest SET xp_conf=MIN(COALESCE(xp_conf, 1), 0.4) WHERE id=?", (qid,))
            suspect.append({"quest": qid, "type": types.get(qid), "xp": rec["xp"], "mult": rec["mult"],
                            "xp_times_mult": int(round(rec["xp"] * rec["mult"]))})
    info = {"quests_with_multiplier": len(rows),
            "types": dict(collections.Counter(types.get(x) for x in rows)),
            "community_value_equals_table_base": len(suspect), "examples": suspect[:12]}
    store.set_meta(conn, "quest_xp:multiplier", dict(info, quests=suspect))
    return info


def fill_from_table(conn, q):
    cols = ["d%d" % i for i in range(10)]
    table, src = {}, None
    for r in conn.execute("SELECT level, %s, src FROM quest_xp_table" % ",".join(cols)):
        table[r[0]] = list(r[1:11])
        src = r[11]
    fitted = 0
    if not table:
        cells = collections.defaultdict(collections.Counter)
        for rec in q.values():
            if rec["xp"] is not None and rec["diff"] is not None and rec["level"] and rec["level"] > 0 \
                    and rec["mult"] is not None and _is_one(rec["mult"]) and (rec["conf"] or 0) >= 0.6:
                cells[(rec["level"], rec["diff"])][rec["xp"]] += 1
        for (lvl, d), cnt in cells.items():
            v, n = cnt.most_common(1)[0]
            total = sum(cnt.values())
            if total >= 3 and n / total >= 0.7:
                table.setdefault(lvl, [None] * 10)[d] = v
                fitted += 1
        if table:
            src = "fit"
            store.insert_rows(conn, "quest_xp_table", [[lvl] + vals + ["fit"] for lvl, vals in sorted(table.items())],
                              ["level"] + cols + ["src"])
    if not table:
        return {"cells": 0, "filled": 0}
    conf = 0.9 if src == "db2" else 0.6
    filled, off = 0, []
    for qid, rec in q.items():
        lvl, d = rec["level"], rec["diff"]
        if lvl is None or d is None or lvl not in table or table[lvl][d] is None or not _is_one(rec["mult"]):
            continue                         # x-multiplier quests: how the multiplier applies is not observed yet
        est = int(table[lvl][d])
        if rec["xp"] is None:
            conn.execute("UPDATE quest SET xp=?, xp_src=?, xp_conf=? WHERE id=?", (est, src, conf, qid))
            rec.update(xp=est, src=src, conf=conf)
            filled += 1
        elif est and abs(rec["xp"] - est) > max(10, 0.1 * est) and rec["src"] != src:
            off.append({"quest": qid, "xp": rec["xp"], "src": rec["src"], "table": est})
    return {"src": src, "levels": len(table), "cells_fitted": fitted, "filled": filled,
            "known_xp_off_table_by_10pct": len(off), "examples_off": off[:15]}
