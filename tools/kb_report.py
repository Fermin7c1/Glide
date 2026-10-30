#!/usr/bin/env python3
"""Report what the knowledge base (data/kb.db) holds, how complete it is and what is still unknown.

    python tools/kb_report.py [--db PATH] [--json]

Sections: build (when, how long, freshness, RestedXP source, loader errors), table counts, loader summaries
(meta loader:<name>), parameters by status with the forever-verified list, coverage (quests with XP, creatures
with spawns, guides with briefs, maps with bounds, what is verified on Forever), observations (recorded sessions
and what they hold), and the top unknowns: quests in routed guides (the guides the analyzer briefed) without
XP, and creatures named by quest objectives (kill targets, drop sources) without any spawn.
Read-only, standard library only; tables or meta keys an older KB lacks are reported as missing, never fatal.
"""
import argparse
import json
import os
import pathlib
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DEFAULT_DB = os.path.join(ROOT, "data", "kb.db")
QUEST_VERBS = ("accept", "turnin", "complete")      # rxp_action verbs that mean a guide does the quest
LOADER_ORDER = ("glide_entities", "rxp_parser", "seed_items", "rxp_db", "db2", "observations", "live_logs", "flags",
                "quest_xp", "map_bounds", "travel_edges", "params", "heuristics", "analyzer", "finalize")
OBS_TABLES = ("obs_session", "obs_kill", "obs_xp", "obs_trail", "obs_death", "obs_gossip", "obs_loot", "obs_respawn",
              "obs_step_time", "quest_xp_obs", "eval_flag")
TOP = 10


def connect(path):
    """Read-only connection (URI form handles spaces and drive letters: 'Glide to 60', C:\\...)."""
    return sqlite3.connect(pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro", uri=True)


def _jload(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _frac(n, of):
    return {"n": n, "of": of, "pct": round(100.0 * n / of, 1) if n is not None and of else None}


def _natural(s):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", s or "")]


class Report:
    def __init__(self, conn):
        self.c = conn
        self.tables = {r[0] for r in self.rows("SELECT name FROM sqlite_master WHERE type='table'")}

    def rows(self, sql, *args):
        try:
            return self.c.execute(sql, args).fetchall()
        except sqlite3.Error:
            return []

    def val(self, sql, *args):
        r = self.rows(sql, *args)
        return r[0][0] if r else None

    def has(self, *tables):
        return all(t in self.tables for t in tables)

    def meta(self, key):
        if not self.has("meta"):
            return None
        v = self.val("SELECT value FROM meta WHERE key=?", key)
        return _jload(v) if v is not None else None

    # ------------------------------------------------------------------------------------------------
    def build(self, path):
        loaders = self.loaders()
        b = {"built": self.meta("built"), "build_s": self.meta("build_s"), "rxp_source": self.meta("rxp_source"),
             "errors": [k for k, v in loaders.items() if v["status"] == "error"],
             "skipped": [k for k, v in loaders.items() if v["status"] == "skipped"]}
        inputs = self.meta("inputs")
        if isinstance(inputs, dict):
            b["inputs"] = [{k: i.get(k) for k in ("key", "path", "t", "n")} for i in inputs.get("items") or []
                           if isinstance(i, dict)]
        try:                                         # is it older than its inputs? (knowledge/build_kb.py rules)
            if ROOT not in sys.path:
                sys.path.insert(0, ROOT)
            from knowledge import build_kb
            d = build_kb.staleness(path)
            b["fresh"] = {"stale": d["stale"], "reason": d["reason"],
                          "changes": [{k: ch.get(k) for k in ("why", "path")} for ch in d["changes"]]}
        except Exception as e:  # noqa: BLE001 - informational only
            b["fresh"] = {"stale": None, "reason": "not checked (%s: %s)" % (type(e).__name__, e)}
        return b

    def counts(self):
        return {t: self.val("SELECT COUNT(*) FROM \"%s\"" % t) for t in sorted(self.tables)
                if not t.startswith("sqlite_")}

    def loaders(self):
        out = {}
        found = {k[len("loader:"):]: _jload(v) for k, v in
                 self.rows("SELECT key, value FROM meta WHERE key LIKE 'loader:%'")}
        for name in list(LOADER_ORDER) + sorted(set(found) - set(LOADER_ORDER)):
            res = found.get(name)
            if not isinstance(res, dict):
                continue
            status = "error" if "error" in res else "skipped" if "skipped" in res else "ok"
            out[name] = {"s": res.get("_s"), "status": status,
                         "summary": res.get("error") or res.get("skipped") or short(res)}
        return out

    def params(self):
        if not self.has("param"):
            return {"missing": "no param table"}
        by = dict(self.rows("SELECT COALESCE(status, '?'), COUNT(*) FROM param GROUP BY 1 ORDER BY 1"))
        ver = [{"key": k, "value": v, "unit": u, "evidence": e} for k, v, u, e in
               self.rows("SELECT key, value, unit, evidence FROM param WHERE status='forever-verified'")]
        ver.sort(key=lambda r: _natural(r["key"]))
        return {"total": sum(by.values()), "by_status": by, "forever_verified": ver}

    def routed_guides_sql(self):
        """SQL for the guides that have analyzer routes (guide_brief); all guides with steps when none do."""
        if self.val("SELECT COUNT(*) FROM guide_brief"):
            return "SELECT DISTINCT guide_key FROM guide_brief", "guides with analyzer briefs (guide_brief)"
        return "SELECT DISTINCT guide_key FROM rxp_step", "all guides with steps (no guide_brief rows)"

    def routed_quests_sql(self):
        rg, _ = self.routed_guides_sql()
        return ("SELECT DISTINCT quest_id FROM rxp_action WHERE quest_id IS NOT NULL AND verb IN (%s) "
                "AND guide_key IN (%s)" % (",".join("'%s'" % v for v in QUEST_VERBS), rg))

    def coverage(self):
        cov = {}
        nq, nc = self.val("SELECT COUNT(*) FROM quest"), self.val("SELECT COUNT(*) FROM creature")
        cov["quests_with_xp"] = _frac(self.val("SELECT COUNT(*) FROM quest WHERE xp IS NOT NULL"), nq)
        rq = self.routed_quests_sql()
        cov["routed_quests_with_xp"] = _frac(
            self.val("SELECT COUNT(*) FROM (%s) r JOIN quest q ON q.id = r.quest_id WHERE q.xp IS NOT NULL" % rq),
            self.val("SELECT COUNT(*) FROM (%s)" % rq))
        cov["quests_verified"] = _frac(self.val("SELECT COUNT(*) FROM quest WHERE verified=1"), nq)
        cov["creatures_with_spawns"] = _frac(
            self.val("SELECT COUNT(*) FROM creature WHERE id IN (SELECT creature_id FROM creature_spawn)"), nc)
        cov["creatures_verified"] = _frac(self.val("SELECT COUNT(*) FROM creature WHERE verified=1"), nc)
        cov["guides_with_briefs"] = _frac(self.val("SELECT COUNT(DISTINCT guide_key) FROM guide_brief"),
                                          self.val("SELECT COUNT(*) FROM rxp_guide"))
        cov["steps_with_briefs"] = _frac(
            self.val("SELECT COUNT(*) FROM (SELECT DISTINCT guide_key, idx FROM step_brief)"),
            self.val("SELECT COUNT(*) FROM rxp_step"))
        cov["maps_with_bounds"] = _frac(self.val("SELECT COUNT(*) FROM map WHERE ui_map IN (SELECT ui_map FROM map_bounds)"),
                                        self.val("SELECT COUNT(*) FROM map"))
        cov["maps_with_bounds"]["by_src"] = dict(self.rows("SELECT COALESCE(src, '?'), COUNT(*) FROM map_bounds "
                                                           "GROUP BY 1 ORDER BY 2 DESC"))
        return cov

    def observations(self):
        obs = {"counts": {t: self.val("SELECT COUNT(*) FROM %s" % t) if self.has(t) else None for t in OBS_TABLES}}
        chars = []
        for char, cls, lo, hi, play, n in self.rows(
                """SELECT char, class, MIN(level_start), MAX(level_end), SUM(play_s), COUNT(*) FROM obs_session
                   GROUP BY char, class ORDER BY SUM(play_s) DESC"""):
            chars.append({"char": char, "class": cls, "levels": [lo, hi], "play_h": round((play or 0) / 3600.0, 2),
                          "sessions": n})
        obs["characters"] = chars
        obs["play_h"] = round(sum(c["play_h"] for c in chars), 2)
        obs["kills_with_ttk"] = self.val("SELECT COUNT(*) FROM obs_kill WHERE ttk_s IS NOT NULL")
        obs["quests_with_observed_xp"] = self.val("SELECT COUNT(DISTINCT quest_id) FROM quest_xp_obs")
        return obs

    def unknowns(self, top=TOP):
        rg, rg_label = self.routed_guides_sql()
        verbs = ",".join("'%s'" % v for v in QUEST_VERBS)
        quest_rows = self.rows(
            """WITH rq AS (SELECT a.quest_id, COUNT(DISTINCT a.guide_key) ng, MAX(a.verb = 'turnin') turned_in,
                                  MIN(a.guide_key) g
                           FROM rxp_action a WHERE a.quest_id IS NOT NULL AND a.verb IN (%s)
                           AND a.guide_key IN (%s) GROUP BY a.quest_id)
               SELECT rq.quest_id, q.name, q.level, q.type, rq.ng, rq.turned_in, rq.g, q.id IS NULL
               FROM rq LEFT JOIN quest q ON q.id = rq.quest_id WHERE q.xp IS NULL
               ORDER BY rq.ng DESC, rq.turned_in DESC, q.level, rq.quest_id""" % (verbs, rg))
        quests = [{"id": i, "name": n, "level": lv, "type": t, "guides": ng, "turned_in": bool(ti), "example_guide": g,
                   "not_in_kb": bool(nk)} for i, n, lv, t, ng, ti, g, nk in quest_rows]
        crea_rows = self.rows(
            """WITH rq AS (%s),
               ref AS (SELECT quest_id, target_id AS cid FROM quest_objective
                       WHERE target_kind = 'npc' AND target_id IS NOT NULL
                       UNION SELECT quest_id, drop_from_id FROM quest_objective
                       WHERE drop_from_kind = 'npc' AND drop_from_id IS NOT NULL)
               SELECT ref.cid, cr.name, COUNT(DISTINCT ref.quest_id), COUNT(DISTINCT rq.quest_id),
                      COALESCE(MIN(rq.quest_id), MIN(ref.quest_id)), GROUP_CONCAT(DISTINCT q.type)
               FROM ref LEFT JOIN rq ON rq.quest_id = ref.quest_id LEFT JOIN creature cr ON cr.id = ref.cid
                    LEFT JOIN quest q ON q.id = ref.quest_id
               WHERE NOT EXISTS (SELECT 1 FROM creature_spawn s WHERE s.creature_id = ref.cid)
               GROUP BY ref.cid ORDER BY 4 DESC, 3 DESC, ref.cid""" % self.routed_quests_sql())
        creatures = [{"id": i, "name": n, "quests": nq, "routed_quests": nrq, "example_quest": ex,
                      "quest_types": sorted(set((ty or "").split(","))) if ty else []}
                     for i, n, nq, nrq, ex, ty in crea_rows]
        an = self.meta("analyzer")
        return {"routed": rg_label,
                "quests_without_xp": {"total": len(quests), "top": quests[:top]},
                "creatures_without_spawns": {"total": len(creatures),
                                             "in_routed_quests": sum(1 for c in creatures if c["routed_quests"]),
                                             "top": creatures[:top]},
                "analyzer": an.get("unknowns") if isinstance(an, dict) else None}

    def report(self, path):
        return {"db": os.path.abspath(path), "size_mb": round(os.path.getsize(path) / 1048576.0, 1),
                "build": self.build(path), "counts": self.counts(), "loaders": self.loaders(),
                "params": self.params(), "coverage": self.coverage(), "observations": self.observations(),
                "unknowns": self.unknowns()}


def short(res, limit=110):
    """'k=v ...' from a loader result: numbers, row counts of nested results, list lengths, short strings."""
    items = []
    for k, v in res.items():
        if k.startswith("_") or k in ("trace", "error", "skipped") or isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            items.append("%s=%s" % (k, v))
        elif isinstance(v, str):
            if k == "from" or len(v) <= 40:
                items.append("%s=%s" % (k, v))
        elif isinstance(v, dict):
            if isinstance(v.get("rows"), (int, float)):
                items.append("%s=%s" % (k, v["rows"]))
            elif "skipped" in v:
                items.append("%s=skipped" % k)
            else:
                num = next(((kk, vv) for kk, vv in v.items() if isinstance(vv, (int, float))
                            and not isinstance(vv, bool)), None)
                if num:
                    items.append("%s.%s=%s" % (k, num[0], num[1]))
        elif isinstance(v, list):
            items.append("%s=[%d]" % (k, len(v)))
    s = " ".join(items)
    return s if len(s) <= limit else s[:limit - 3] + "..."


# ------------------------------------------------------------------------------------------------
# text output
# ------------------------------------------------------------------------------------------------
def _n(v):
    return "-" if v is None else "{:,}".format(v) if isinstance(v, int) else str(v)


def _pct(f):
    return "%s / %s" % (_n(f["n"]), _n(f["of"])), ("%5.1f%%" % f["pct"]) if f.get("pct") is not None else "    -"


def _guide(g):
    return (g or "").replace("RestedXP ", "")


def text(r):
    out = []
    w = out.append
    b = r["build"]
    w("Glide knowledge base  %s  (%.1f MB)" % (r["db"], r["size_mb"]))
    rx = b.get("rxp_source") or {}
    w("  built %s in %s s | RestedXP files: %s | loader errors: %s" % (
        b.get("built") or "?", b.get("build_s") or "?",
        "%s (%s)" % (rx.get("path"), rx.get("kind")) if isinstance(rx, dict) and rx.get("kind") else
        "not recorded" if not rx else "not found",
        ", ".join(b["errors"]) or "none"))
    fr = b.get("fresh") or {}
    if fr.get("stale") is None:
        w("  freshness: %s" % fr.get("reason"))
    elif fr["stale"]:
        w("  freshness: needs a rebuild: %s" % fr["reason"])
        for ch in fr.get("changes", [])[:6]:
            w("    %-7s %s" % (ch["why"], ch["path"]))
    else:
        w("  freshness: up to date with its inputs")
    if b.get("inputs"):
        w("  inputs at build: " + ", ".join("%s %s" % (i["path"], (i.get("t") or "")[5:16]) for i in b["inputs"]
                                            if i.get("path")))

    counts = r["counts"]
    w("")
    w("Tables (%d tables, %s rows)" % (len(counts), _n(sum(v or 0 for v in counts.values()))))
    items = sorted(counts.items(), key=lambda kv: (-(kv[1] or 0), kv[0]))
    for i in range(0, len(items), 3):
        w("  " + "".join("%-18s %9s    " % (k, _n(v)) for k, v in items[i:i + 3]).rstrip())

    w("")
    w("Loaders              time  result")
    for name, l in r["loaders"].items():
        summ = l["summary"] if l["status"] == "ok" else "%s: %s" % (l["status"].upper(), l["summary"])
        summ = str(summ).replace("\n", " ")
        w("  %-16s %6s  %s" % (name, "%.1fs" % l["s"] if isinstance(l["s"], (int, float)) else "-",
                               summ if len(summ) <= 100 else summ[:97] + "..."))

    p = r["params"]
    w("")
    if "missing" in p:
        w("Parameters: %s" % p["missing"])
    else:
        w("Parameters: %d (%s)" % (p["total"], ", ".join("%s %d" % kv for kv in p["by_status"].items())))
        w("  forever-verified (%d):" % len(p["forever_verified"]))
        for v in p["forever_verified"]:
            ev = (v["evidence"] or "").replace("\n", " ")
            w("    %-22s %8s %-20s %s" % (v["key"], v["value"], (v["unit"] or "")[:20], ev[:70] + ("..." if len(ev) > 70 else "")))

    c = r["coverage"]
    w("")
    w("Coverage")
    labels = (("quests_with_xp", "quests with XP"), ("routed_quests_with_xp", "quests in routed guides with XP"),
              ("quests_verified", "quests verified on Forever"), ("creatures_with_spawns", "creatures with spawns"),
              ("creatures_verified", "creatures verified on Forever"), ("guides_with_briefs", "guides with briefs"),
              ("steps_with_briefs", "guide steps with briefs"), ("maps_with_bounds", "maps with bounds"))
    for key, label in labels:
        f = c.get(key)
        if not f:
            continue
        nn, pc = _pct(f)
        extra = ""
        if key == "maps_with_bounds" and f.get("by_src"):
            extra = "  (%s)" % ", ".join("%s %d" % kv for kv in f["by_src"].items())
        w("  %-34s %17s  %s%s" % (label, nn, pc, extra))

    o = r["observations"]
    w("")
    w("Observations: %s recorded sessions, %.1f h played, %s kills with time-to-kill, %s quests with observed XP" % (
        _n(o["counts"].get("obs_session")), o["play_h"], _n(o["kills_with_ttk"]), _n(o["quests_with_observed_xp"])))
    for ch in o["characters"]:
        w("  %-14s %-8s %-7s %2d session%s %5.1f h" % (ch["char"], ch["class"], "L%s-%s" % tuple(ch["levels"]),
                                                       ch["sessions"], "s" if ch["sessions"] != 1 else " ",
                                                       ch["play_h"]))
    w("  " + "  ".join("%s %s" % (t, _n(v)) for t, v in o["counts"].items() if t != "obs_session"))

    u = r["unknowns"]
    w("")
    w("Unknowns (routed guides = %s)" % u["routed"])
    q = u["quests_without_xp"]
    w("  quests in routed guides without XP: %d%s" % (q["total"], " (top %d by number of guides)" % len(q["top"])
                                                       if q["top"] else ""))
    for x in q["top"]:
        w("    %6s  %-34s %-4s %-9s %2d guides%s  e.g. %s" % (
            x["id"], (x["name"] or "(not in kb)")[:34], "L%s" % x["level"] if x["level"] else "", x["type"] or "",
            x["guides"], "" if x["turned_in"] else " (no turn-in)", _guide(x["example_guide"])))
    cr = u["creatures_without_spawns"]
    w("  creatures named by quest objectives without spawns: %d (%d in routed quests)%s" % (
        cr["total"], cr["in_routed_quests"], "; top %d" % len(cr["top"]) if cr["top"] else ""))
    for x in cr["top"]:
        w("    %6s  %-34s %2d quests (%d routed)  e.g. quest %s %s" % (
            x["id"], (x["name"] or "(not in kb)")[:34], x["quests"], x["routed_quests"], x["example_quest"],
            "[%s]" % ",".join(x["quest_types"]) if x["quest_types"] else ""))
    if u.get("analyzer"):
        w("  analyzer counts: " + ", ".join("%s %s" % kv for kv in u["analyzer"].items()))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Report what the Glide knowledge base (data/kb.db) holds")
    ap.add_argument("--db", default=DEFAULT_DB, help="KB file (default data/kb.db)")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    a = ap.parse_args(argv)
    if not os.path.exists(a.db):
        msg = "no knowledge base at %s (build it with build-kb.cmd or python knowledge/build_kb.py)" % a.db
        print(json.dumps({"error": msg}) if a.json else msg)
        return 1
    conn = connect(a.db)
    try:
        rep = Report(conn)
        if not rep.has("meta"):
            msg = "%s is not a Glide knowledge base (no meta table)" % a.db
            print(json.dumps({"error": msg}) if a.json else msg)
            return 1
        r = rep.report(a.db)
    finally:
        conn.close()
    print(json.dumps(r, indent=1, ensure_ascii=False, default=str) if a.json else text(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
