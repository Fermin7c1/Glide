"""RestedXP parser golden master: every guide in data/glide.db parsed (knowledge/rxp_parser.parse_guide), the include
expansion, and which steps four sample characters see (rxp_index_map). Per guide: step and action counts and a hash
of the full parsed structure, so a difference names the guide without storing megabytes of parse trees."""
TIER = "machine"     # golden.py tier: reads data/glide.db
import copy
import hashlib
import json
import os
import sqlite3
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
PLAYERS = [dict(cls="WARRIOR", race="Human", faction="Alliance", level=10),
           dict(cls="MAGE", race="Gnome", faction="Alliance", level=30),
           dict(cls="HUNTER", race="Orc", faction="Horde", level=20),
           dict(cls="PRIEST", race="Undead", faction="Horde", level=45)]


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=repr).encode("utf-8")).hexdigest()[:16]


def run():
    sys.path.insert(0, ROOT)
    from knowledge import rxp_parser as P
    db = sqlite3.connect("file:%s?mode=ro" % os.path.join(ROOT, "data", "glide.db"), uri=True)
    rows = db.execute("SELECT guide_key, source, text FROM rxp_guides ORDER BY guide_key").fetchall()
    guides, out = {}, {}
    for key, source, text in rows:
        try:
            g = P.parse_guide(text, source=source)
        except Exception as e:  # noqa: BLE001 - an error is an output too
            out[key] = {"raised": "%s: %s" % (type(e).__name__, e)}
            continue
        guides[key] = g
        steps = g.get("steps") or [] if isinstance(g, dict) else []
        out[key] = {"steps": len(steps), "actions": sum(len(s.get("actions") or []) for s in steps),
                    "parsed": digest(g)}
    try:
        expanded = copy.deepcopy(guides)       # expanded in place; the index maps below use the parsed guides
        report = P.expand_includes(expanded)
        out["_includes"] = {"guides": digest(expanded), "report": digest(report)}
    except Exception as e:  # noqa: BLE001
        out["_includes"] = "raised %s: %s" % (type(e).__name__, e)
    for key, g in guides.items():
        maps = {}
        for p in PLAYERS:
            try:
                maps["%s/%s" % (p["cls"], p["race"])] = digest(P.rxp_index_map(g, **p))
            except Exception as e:  # noqa: BLE001
                maps["%s/%s" % (p["cls"], p["race"])] = "raised %s" % type(e).__name__
        out.setdefault("_index_maps", {})[key] = maps
    return out


if __name__ == "__main__":                 # python probe_parser.py OUT.json
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        json.dump(run(), fh, indent=1, sort_keys=True, ensure_ascii=False)
