"""Loader: glide.db rxp_guides -> kb.db rxp_guide / rxp_step / rxp_action."""
import os
import sqlite3
import time

from knowledge import store
from knowledge.rxp_parser.guide import parse_guide
from knowledge.rxp_parser.includes import expand_includes


def load(conn, ctx):
    """glide.db rxp_guides -> rxp_guide / rxp_step / rxp_action in `conn`. Never raises per guide."""
    t0 = time.time()
    if not os.path.exists(getattr(ctx, "glide_db", "") or ""):
        return {"skipped": "glide.db not found"}
    try:
        src = ctx.glide()
    except sqlite3.Error as e:
        return {"skipped": "glide.db unreadable: %s" % e}
    try:
        rows = [dict(r) for r in src.execute("SELECT guide_key, name, grp, source, text FROM rxp_guides")]
        extra_maps = {}                                    # names of maps not in ZONE_UIMAP (Forever additions)
        try:
            for r in src.execute("SELECT ui_map, name, parent FROM maps ORDER BY parent = 0, ui_map"):
                if r["name"]:
                    extra_maps.setdefault(r["name"], r["ui_map"])
        except sqlite3.Error:
            pass
    except sqlite3.Error as e:
        return {"skipped": "glide.db rxp_guides unreadable: %s" % e}
    finally:
        src.close()
    store.create_schema(conn)
    errors, guides, warnings = [], {}, 0
    for r in rows:
        try:
            g = parse_guide(r["text"] or "", source=r["source"], map_ids=extra_maps)
            if g["key"] != r["guide_key"]:
                g["warnings"].append("key %r differs from glide.db key %r (glide.db key kept)" % (g["key"], r["guide_key"]))
            g["key"] = r["guide_key"]
            g["text"] = r["text"]
            warnings += len(g["warnings"])
            guides[r["guide_key"]] = g
        except Exception as e:
            errors.append({"guide": r["guide_key"], "error": "%s: %s" % (type(e).__name__, e)})
    try:
        inc = expand_includes(guides)
    except Exception as e:
        inc = {"resolved": 0, "unresolved": [], "notes": []}
        errors.append({"guide": None, "error": "include expansion: %s: %s" % (type(e).__name__, e)})
    jd = store.jdump
    grows, srows, arows = [], [], []
    for key, g in guides.items():
        try:
            gr = (key, g["name"], g["grp"], g["subgroup"], g["next"], g["level_min"], g["level_max"],
                  g["faction"], g["default_for"], g["season"], g["source"], g["text"])
            sr, ar = [], []
            for s in g["steps"]:
                sr.append((key, s["idx"], s["text"], jd(s["flags"]), jd(s["cond"]), s["ui_map"], s["x"], s["y"],
                           s["continent"], s["wx"], s["wy"], s["zone"], s["comments"]))
                for a in s["actions"]:
                    ar.append((key, s["idx"], a["line"], a["verb"], jd(a["args"]), a["quest_id"],
                               a["entity_kind"], a["entity_id"], a["qty"], a["text"], jd(a["cond"])))
            grows.append(gr)
            srows += sr
            arows += ar
        except Exception as e:
            errors.append({"guide": key, "error": "%s: %s" % (type(e).__name__, e)})
    keys = [(k,) for k in guides]
    conn.executemany("DELETE FROM rxp_action WHERE guide_key=?", keys)
    conn.executemany("DELETE FROM rxp_step WHERE guide_key=?", keys)
    conn.executemany("DELETE FROM rxp_guide WHERE key=?", keys)
    conn.executemany("INSERT INTO rxp_guide(key,name,grp,subgroup,next,level_min,level_max,faction,default_for,"
                     "season,source,text) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", grows)
    conn.executemany("INSERT INTO rxp_step(guide_key,idx,text,flags,cond,ui_map,x,y,continent,wx,wy,zone,comments)"
                     " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", srows)
    conn.executemany("INSERT INTO rxp_action(guide_key,idx,line,verb,args,quest_id,entity_kind,entity_id,qty,"
                     "text,cond) VALUES(?,?,?,?,?,?,?,?,?,?,?)", arows)
    conn.commit()
    return {"guides": len(grows), "steps": len(srows), "actions": len(arows), "errors": errors,
            "warnings": warnings, "includes_resolved": inc["resolved"], "includes_by_name": inc["notes"],
            "includes_unresolved": inc["unresolved"], "seconds": round(time.time() - t0, 2)}
