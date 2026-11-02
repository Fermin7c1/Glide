"""What the knowledge base knows about one guide on the Guides page: `rxp_import.py facts <id> <index> [CLASS|-]
[RACE|-]` (the desktop app's addon:rxpFacts). The guide is picked and parsed the way `rxp_import.guide_detail` does
it, so the step numbers are the ones the page shows; knowledge.api.explorer.guide_facts does the rest, read-only,
over the newest knowledge base (data/kb.db, or the background refresh's data/kb.next-*.db still waiting for the next
engine start). Without a knowledge base the answer still holds each quest's steps and the prep list."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)            # the knowledge package, as rxp_import.guide_detail reaches it


def kb_paths(root):
    """data/kb.db and the builds waiting to replace it."""
    import kb_refresh
    data = os.path.join(root, "data")
    return [os.path.join(data, "kb.db")] + kb_refresh.staged(data, "kb")


def newest_kb(root):
    """(store.KB or None, reason): the newest knowledge base of kb_paths() with this version's schema."""
    from knowledge.api import explorer
    return explorer.newest_kb(kb_paths(root))


def parsed_guide(root, iid, index, wow_dir=None):
    """guide `index` of import `iid` (or of RestedXP's free guides) parsed as guide_detail parses it, with its text."""
    import rxp_import as R
    if str(iid) == R.FREE_ID:
        guides = [{"group": g["groups"][0], "name": g["name"], "text": g["text"]} for g in R.free_guides(root, wow_dir)[1]]
    else:
        rec = R.load_store(root)["imports"].get(str(iid))
        guides = (rec.get("guides") if isinstance(rec, dict) else None) or []
    idx = int(index)
    if idx < 0 or idx >= len(guides) or not isinstance(guides[idx], dict):
        raise R.ImportProblem("Guide not found in that import.")
    from knowledge.rxp_parser.guide import parse_guide
    text = guides[idx].get("text") or ""
    parsed = parse_guide(text)
    steps = parsed.get("steps") or ()
    if any(st.get("x") is None and st.get("wx") is not None for st in steps):
        maps = {st.get("ui_map") for st in steps} | {(a.get("args") or {}).get("ui_map") for st in steps
                                                     for a in st.get("actions") or ()}
        R.place_world_points(parsed, R._map_bounds(root, maps))     # noqa: SLF001 - guide_detail's own bounds
    parsed["text"] = text
    return parsed


def faction_of(race, guide_faction=None):
    if race:
        from knowledge.vocab import FACTION_RACES, HORDE_RACES, glide_race
        r = glide_race(race) or race
        if r in HORDE_RACES or race in HORDE_RACES:
            return "Horde"
        if r in FACTION_RACES["Alliance"] or race in FACTION_RACES["Alliance"]:
            return "Alliance"
    return guide_faction


def facts(root, iid, index, cls=None, race=None, wow_dir=None):
    """guide_facts for one guide of the Guides page (see knowledge/api/explorer.py for the answer's shape)."""
    from knowledge.api import explorer
    cls = None if cls in (None, "", "-") else str(cls).upper()
    race = None if race in (None, "", "-") else str(race)
    guide = parsed_guide(root, iid, index, wow_dir)
    kb, reason = newest_kb(root)
    out = explorer.guide_facts(kb, guide, cls, race, faction_of(race, guide.get("faction")))
    if kb is None and reason:
        out["kb"]["reason"] = reason
    return out
