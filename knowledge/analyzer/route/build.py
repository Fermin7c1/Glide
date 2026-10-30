"""The build_kb loader: briefs for every guide x class -> step_brief and guide_brief, each guide starting from its
predecessor's modelled end, plus the analyzer parameters fitted from recorded play."""
import json
import re
import statistics
import time

from knowledge import rxp_parser as RP
from knowledge import store
from knowledge.analyzer import params as AP
from knowledge.analyzer.kbdata import ConnKB, get_data
from knowledge.analyzer.model import LEVEL_XP
from knowledge.analyzer.route.briefs import analyze, guide_summary
from knowledge.analyzer.route.runtime import BRIEF_RXP, BRIEF_RXP_META, clear_caches, split_level
from knowledge.analyzer.route.steps import DEFAULT_RACE, Route, applies, next_guides
from knowledge.vocab import FACTION_CLASSES, FACTION_RACES, RACE_CLASSES

MODEL_VERSION = "analyzer-1"


GUIDE_BRIEF_DDL = """CREATE TABLE IF NOT EXISTS guide_brief(
  guide_key TEXT, class TEXT, start_level REAL, end_level REAL, time_s REAL, xp_total INT, xp_quests INT,
  xp_kills INT, xp_grind INT, hubs TEXT, notes TEXT, PRIMARY KEY(guide_key, class))"""
STEP_BRIEF_COLS = (("rxp_step_no", "INT"), ("kind", "TEXT"), ("exp_level_after", "REAL"), ("facts", "TEXT"),
                   ("race", "TEXT"))


def ensure_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS step_brief(
      guide_key TEXT, idx INT, class TEXT, exp_level REAL, xp_now INT, xp_unlocked INT,
      time_s REAL, travel_s REAL, kill_s REAL, xp_per_min REAL, risk REAL,
      justification TEXT, alternatives TEXT, deps TEXT, computed TEXT, PRIMARY KEY(guide_key, idx, class))""")
    conn.execute(GUIDE_BRIEF_DDL)
    have = set(store.table_cols(conn, "step_brief"))
    for c, t in STEP_BRIEF_COLS:
        if c not in have:
            conn.execute("ALTER TABLE step_brief ADD COLUMN %s %s" % (c, t))


def fit_params(kb):
    """Fit analyzer.kill.search_s from obs_kill gaps and analyzer.calib.<CLASS> from obs_step_time.
    Returns {key: {"value","status","evidence"}} (strong evidence only)."""
    out = {}
    rows = kb.q("""SELECT k.session, k.t, k.creature_id, k.mob_level, k.player_level, k.ttk_s, s.class
                   FROM obs_kill k LEFT JOIN obs_session s ON s.id = k.session ORDER BY k.session, k.t""")
    from knowledge.mechanics import combat as MC
    resid = []
    for a, b in zip(rows, rows[1:]):
        if a["session"] != b["session"] or not b.get("ttk_s"):
            continue
        gap = b["t"] - a["t"]
        if not 0 < gap <= 90:
            continue
        cls = b.get("class") or "WARRIOR"
        lv = b.get("player_level") or 1
        down = MC.downtime_s(cls, lv, 1)
        resid.append(gap - b["ttk_s"] - down)
    if len(resid) >= 30:
        med = statistics.median(resid)
        val = round(max(2.0, min(30.0, med)), 1)
        out["analyzer.kill.search_s"] = {
            "value": val, "status": "forever-fitted",
            "evidence": "median(kill gap - observed ttk - modelled downtime) over %d consecutive recorded kills "
                        "(gaps <= 90 s): %.1f s" % (len(resid), med)}
    # default drop rate: median best known rate of quest collect objectives (community + recorded loot)
    best = {}
    for r in kb.q("""SELECT o.quest_id, o.idx, MAX(d.rate) rate FROM quest_objective o
                     JOIN creature_drop d ON d.item_id = o.target_id
                     WHERE o.type='collect' AND o.target_kind='item' AND d.rate IS NOT NULL AND d.rate > 0
                     GROUP BY o.quest_id, o.idx"""):
        best[(r["quest_id"], r["idx"])] = r["rate"]
    if len(best) >= 20:
        med = statistics.median(best.values())
        out["analyzer.drop.default_rate"] = {
            "value": round(max(0.3, min(0.9, med)), 2), "status": "unverified",
            "evidence": "median best known drop rate of %d quest collect objectives in the KB (fg/frc/obs mix): "
                        "%.2f" % (len(best), med)}
    return out


def guide_classes(data, key, row):
    """[(class, race, faction)] the guide is analysed for: classes of its faction for which the guide's own
    `<<` condition holds with some race; race = the guide's #defaultfor race when it fits the class."""
    fac = row.get("faction")
    grp = row.get("grp") or ""
    if not fac:
        fac = "Horde" if "(H)" in grp or "Horde" in grp else "Alliance"
    hdr = data.guide_header(key)
    gcond = hdr["cond"]
    races = list(FACTION_RACES[fac])
    skyborne = "Zephras" in (row.get("name") or "") or "Skyborne" in (row.get("default_for") or "")
    if skyborne:
        races = ["Skyborne"] + races
    dfor = RP.parse_cond(row["default_for"]) if row.get("default_for") and row["default_for"] != "none" else None
    out = []
    for cls in FACTION_CLASSES[fac]:
        ok_races = [r for r in races if cls in RACE_CLASSES.get(r, ()) and applies(gcond, cls, r, fac)]
        if not ok_races:
            continue
        pick = None
        if skyborne and "Skyborne" in ok_races:
            pick = "Skyborne"
        if pick is None and dfor:
            pref = [r for r in ok_races if applies(dfor, cls, r, fac)]
            if pref:
                # keep the #defaultfor order ("Dwarf/Gnome" -> Dwarf first when it fits)
                order = re.findall(r"[A-Za-z]+", row["default_for"])
                pref.sort(key=lambda r: order.index(r) if r in order else 99)
                pick = pref[0]
        if pick is None:
            dr = DEFAULT_RACE[fac].get(cls)
            pick = dr if dr in ok_races else ok_races[0]
        out.append((cls, pick, fac))
    return out



def chained_start(route, preds, rows=None):
    """(level, xp, source) a guide starts from in load(): the modelled end of its predecessor (same group
    preferred, then the highest end) when that is at or above the guide's nominal start, else the nominal
    start (level_min, 0). RXP's .xp targets assume the surplus a player carries in from the previous guide."""
    lmin = route.guide.get("level_min") or 1
    grp = route.guide.get("grp")
    best = None
    for pk, lv, xp in preds:
        same = 1 if rows is not None and rows.get(pk, {}).get("grp") == grp else 0
        k = (same, lv, xp)
        if best is None or k > best[0]:
            best = (k, pk, lv, xp)
    if best is None:
        return lmin, 0, "nominal"
    _, pk, lv, xp = best
    if lv < lmin:
        return lmin, 0, "nominal (%s ended at %.2f)" % (pk.split("||")[-1], lv + (xp / LEVEL_XP[lv] if LEVEL_XP[lv] else 0))
    return lv, xp, pk


def load(conn, ctx=None):
    """build_kb step: briefs for every guide x class -> step_brief + guide_brief. Returns counts/notes.
    Guides are processed in level order; each starts from its predecessor's modelled end (chained_start)."""
    t0 = time.time()
    ensure_schema(conn)
    kb = ConnKB(conn, path=":build:%d" % id(conn))
    data = get_data(kb)
    AP.reset()                                   # never reuse fitted values stored by an earlier build
    fitted = fit_params(kb)
    AP.apply(fitted)
    from knowledge.analyzer.review import calibration            # model time scale per class from recorded step visits
    cal = calibration(kb)
    for cls, c in sorted(cal.items()):
        fitted["analyzer.calib.%s" % cls] = {
            "value": c["factor"], "status": "forever-fitted",
            "evidence": "recorded step visits: %.0f min played vs %.0f min modelled over %d windows (x%.2f), "
                        "shrunk toward 1 with analyzer.calib.prior_h" % (c["actual_s"] / 60, c["expected_s"] / 60,
                                                                         c["windows"], c["ratio"])}
    AP.apply(fitted)
    clear_caches()
    conn.execute("DELETE FROM step_brief")
    conn.execute("DELETE FROM guide_brief")
    rows = data.guide_rows
    keys = sorted(rows, key=lambda k: (rows[k]["level_min"] or 0, rows[k]["level_max"] or 0, k))
    lctx = {"alt_cache": {}, "later_cache": {}}
    n_briefs = n_guides = n_routes = 0
    errors = []
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    summaries = {}
    preds = {}                   # (guide_key, class) -> [(pred_key, level, xp)] from the preds' modelled ends
    for key in keys:
        row = rows[key]
        g = data.guide(key)
        if not g or not g["steps"]:
            continue
        combos = guide_classes(data, key, row)
        any_rows = False
        for cls, race, fac in combos:
            try:
                player = dict(BRIEF_RXP, **{"class": cls, "race": race, "faction": fac})
                route = Route(kb, key, player, data=data)
                if not route.steps:
                    continue
                lv0, xp0, src = chained_start(route, preds.get((key, cls), ()), rows)
                lctx["alt_cache"] = {}
                briefs = analyze(kb, route, start_level=lv0, start_xp=xp0, with_alternatives=True, ctx=lctx)
                end_lv, end_xp = split_level(briefs[-1]["exp_level_after"]) if briefs else (lv0, xp0)
                for nk in next_guides(kb, key, player, data=data):
                    preds.setdefault((nk, cls), []).append((key, end_lv, end_xp))
                srows = []
                for b in briefs:
                    comp = {"t0": b["_t0"], "xp": [b["_xp"]["quests"], b["_xp"]["kills"], b["_xp"]["grind"]]}
                    if b["_skip"]:
                        comp["skip"] = b["_skip"]
                    srows.append((key, b["idx"], cls, b["exp_level_before"], b["xp_now"], b["xp_unlocked"],
                                  b["time_s"], b["travel_s"], b["kill_s"], b["xp_per_min"], b["risk"],
                                  store.jdump(b["justification"]), store.jdump(b["alternatives"]),
                                  store.jdump(b["deps"]), store.jdump(comp), b["rxp_step_no"], b["kind"],
                                  b["exp_level_after"], store.jdump(b["facts"]), race))
                conn.executemany("""INSERT OR REPLACE INTO step_brief(guide_key, idx, class, exp_level, xp_now,
                    xp_unlocked, time_s, travel_s, kill_s, xp_per_min, risk, justification, alternatives, deps,
                    computed, rxp_step_no, kind, exp_level_after, facts, race)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", srows)
                s = guide_summary(route, briefs)
                s["notes"]["race"] = race
                s["notes"]["start_from"] = src
                summaries[(key, cls)] = s
                conn.execute("""INSERT OR REPLACE INTO guide_brief(guide_key, class, start_level, end_level, time_s,
                    xp_total, xp_quests, xp_kills, xp_grind, hubs, notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                             (key, cls, s["start_level"], s["end_level"], s["time_s"], s["xp_total"], s["xp_quests"],
                              s["xp_kills"], s["xp_grind"], store.jdump(s["hubs"]), store.jdump(s["notes"])))
                n_briefs += len(srows)
                n_routes += 1
                any_rows = True
            except Exception as e:  # noqa: BLE001 - one bad guide must not stop the build
                import traceback
                errors.append({"guide": key, "class": cls, "error": "%s: %s" % (type(e).__name__, e),
                               "trace": traceback.format_exc()[-600:]})
        n_guides += 1 if any_rows else 0
    store.set_meta(conn, "analyzer_params", {k: v for k, v in fitted.items()})
    # the RestedXP options behind every stored brief: why() computes a fresh brief for a player with other ones
    store.set_meta(conn, BRIEF_RXP_META, BRIEF_RXP)
    # analyzer.* parameters next to the mechanics ones, so every "p:<key>" fact resolves from the param table
    prow = [(r["key"], json.dumps(r["value"], separators=(",", ":"), sort_keys=True), r["unit"],
             json.dumps(r["classic_value"], separators=(",", ":"), sort_keys=True), r["status"], r["evidence"],
             r.get("updated")) for r in AP.all()]
    conn.executemany("INSERT OR REPLACE INTO param(key, value, unit, classic_value, status, evidence, updated) "
                     "VALUES(?,?,?,?,?,?,?)", prow)
    report = {"version": MODEL_VERSION, "guides": n_guides, "routes": n_routes, "briefs": n_briefs,
              "unknowns": data.unknowns(), "fitted": {k: v["value"] for k, v in fitted.items()}, "built": now,
              "calibration": cal,
              "errors": errors[:20], "n_errors": len(errors), "seconds": round(time.time() - t0, 1)}
    store.set_meta(conn, "analyzer", report)
    conn.commit()
    return report
