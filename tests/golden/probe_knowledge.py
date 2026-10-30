"""Knowledge golden master: for a fixed spread of RestedXP steps across the guides (every 4th guide, three points
along each), the knowledge layer's full answer for that moment: the coach's knowledge block, the dashboard panel,
the tool list, the resolved guide and step, and any error; then every third of those moments again under two other
RestedXP settings (self-found off, group quests off), since the settings decide which steps exist and how they are
numbered. Deterministic for a given data/kb.db, which is opened read-only."""
TIER = "machine"     # golden.py tier: reads data/kb.db
import json
import os
import sqlite3
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
RACE = {"Alliance": "Human", "Horde": "Orc"}
RACES = {"Human", "Dwarf", "NightElf", "Gnome", "Orc", "Undead", "Tauren", "Troll"}
KB = os.path.join(ROOT, "data", "kb.db")
# besides the default (DEFAULT_RXP: self-found, group quests on), one setting flipped at a time
SETTINGS = ({"ssf": False, "group_quests": True, "hardcore": False}, {"ssf": True, "group_quests": False, "hardcore": False})


def samples():
    db = sqlite3.connect("file:%s?mode=ro" % KB, uri=True)     # never creates or writes it
    guides = db.execute("""SELECT key, name, grp, faction, level_min, level_max, default_for FROM rxp_guide
                           WHERE faction IN ('Alliance', 'Horde') ORDER BY key""").fetchall()
    out = []
    for key, name, grp, fac, lo, hi, default_for in guides[::4]:
        idxs = [r[0] for r in db.execute("SELECT idx FROM rxp_step WHERE guide_key=? ORDER BY idx", (key,))]
        if not idxs:
            continue
        for frac in (0.1, 0.5, 0.9):
            idx = idxs[min(len(idxs) - 1, int(len(idxs) * frac))]
            lv = int((lo or 1) + ((hi or lo or 1) - (lo or 1)) * frac)
            first = (default_for or "").split(",")[0].strip()
            race = first if first in RACES else RACE[fac]       # default_for can be a description, not a race
            out.append({"guide": name, "guide_group": grp, "step": idx, "level": max(1, lv), "xp": 100,
                        "xp_max": 1000, "class": "WARRIOR", "race": race, "faction": fac, "zone": None})
    return out


def answer(pack, s):
    """The knowledge layer's full answer for one moment."""
    rec = {"in": s}
    try:
        ctx = pack.context(s, coach_flags={})
        rec["error"] = ctx.error
        rec["guide_key"] = getattr(ctx, "guide_key", None)
        rec["idx"] = getattr(ctx, "idx", None)
        rec["block"] = pack.block(ctx)
        rec["panel"] = pack.panel(s)
    except Exception as e:  # noqa: BLE001 - an error is an output too
        rec["raised"] = "%s: %s" % (type(e).__name__, e)
    return rec


def run():
    if not os.path.exists(KB):
        return [{"skipped": "no data/kb.db on this machine (build-kb.cmd builds it)"}]
    sys.path.insert(0, os.path.join(ROOT, "companion"))
    sys.path.insert(0, ROOT)
    from coach import load_knowledge_layer
    kb, api, why = load_knowledge_layer(KB)
    pack = api.KnowledgePack(kb) if hasattr(api, "KnowledgePack") else None
    recs = []
    if pack is None:
        return [{"error": "no knowledge pack"}]
    recs.append({"tools": [t.get("name") for t in pack.tool_definitions()]})
    moments = samples()
    recs += [answer(pack, s) for s in moments]
    for rxp in SETTINGS:
        other = api.KnowledgePack(kb, rxp=rxp)
        recs += [dict(answer(other, s), rxp=rxp) for s in moments[::3]]
    return recs


if __name__ == "__main__":                 # python probe_knowledge.py OUT.json
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        json.dump(run(), fh, indent=1, default=str, ensure_ascii=False)
