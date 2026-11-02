"""What the Guides page needs beyond the parsed guide, worked out by the engine with the coach's own rules
(rxp_import.guide_detail calls these):

- include_expanded(): a guide's `#include` steps replaced by the steps they name, over the same guides the knowledge
  build reads, so a step's number is the one kb.db (and the coach) gives it;
- visibility(): which steps RestedXP shows for every character a player can make (race pick x class, from
  knowledge/vocab.py) under every combination of the three settings the page toggles (solo self-found, group
  quests, hardcore), as compact masks: the page reads them and evaluates no `<<` condition of its own;
- map_geometry(): each zone's size in yards and its rectangle on its parent map (the continent), from kb.db.

Standard library and the knowledge layer only; reads kb.db read-only and never writes anything.
"""
import glob
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from knowledge import store as kstore  # noqa: E402
from knowledge.rxp_parser.conditions import cond_applies  # noqa: E402
from knowledge.rxp_parser.guide import parse_guide  # noqa: E402
from knowledge.rxp_parser.includes import _include_entry, _include_target, expand_includes  # noqa: E402
from knowledge.rxp_parser.views import RxpSettings, guide_dungeons, step_flags, step_shown  # noqa: E402
from knowledge.vocab import CLASSES, FACTION_CLASSES, FACTION_RACES, RACE_CLASSES  # noqa: E402

# the RestedXP options the coach assumes until the addon says otherwise (knowledge/api/guides.py DEFAULT_RXP)
DEFAULT_RXP = {"ssf": True, "group_quests": True, "hardcore": False}
RXP_KEYS = ("ssf", "group_quests", "hardcore", "dungeons", "season", "phase", "xprate")
# the settings the page toggles, one bit each: combination i has ssf = i & 1, group quests = i & 2, hardcore = i & 4
TOGGLES = ("ssf", "group_quests", "hardcore")


# ------------------------------------------------------------------------------------------------
# the knowledge base
# ------------------------------------------------------------------------------------------------
def kb_path(root):
    """The newest knowledge base: data/kb.db, or a background refresh's data/kb.next-<t>.db waiting to replace it
    (build_kb writes it whole, then renames it). None without one."""
    data = os.path.join(root, "data")
    cands = [p for p in [os.path.join(data, "kb.db")] + glob.glob(os.path.join(data, "kb.next-*.db"))
             if os.path.isfile(p)]
    if not cands:
        return None
    return max(cands, key=lambda p: (os.path.getmtime(p), p))


def _connect(path):
    conn = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(path, sql, args=()):
    """Rows of one read-only query, [] without the database or the table."""
    if not path:
        return []
    try:
        conn = _connect(path)
        try:
            return [dict(r) for r in conn.execute(sql, args)]
        finally:
            conn.close()
    except sqlite3.Error:
        return []


def map_names(path):
    """{name: ui_map} of kb.db's maps (the client's own list, Forever's maps included), a zone first over a
    sub-map of the same name, as the knowledge build's loader names them."""
    out = {}
    for r in _rows(path, "SELECT ui_map, name, parent FROM map ORDER BY parent = 0, ui_map"):
        if r["name"] and isinstance(r["ui_map"], int):
            out.setdefault(r["name"], r["ui_map"])
    return out


# ------------------------------------------------------------------------------------------------
# #include
# ------------------------------------------------------------------------------------------------
def include_expanded(text, corpus, map_ids=None):
    """parse_guide(text) with its `#include` steps expanded as the knowledge build expands them (tools/build_db.py
    stores every guide, knowledge/rxp_parser/loader.py runs expand_includes over all of them): only the guides an
    include names are parsed, every guide of that name, so resolving by name sees the candidates the build sees.
    corpus: [guide text] (the free guides and the added ones). Steps are renumbered; an included step carries
    flags.included_from / included_idx."""
    parsed = parse_guide(text or "", map_ids=map_ids)
    if not parsed.get("key") or not any(_include_entry(s)[0] for s in parsed.get("steps") or ()):
        return parsed
    by_name = {}
    for t in corpus:
        name = _header_name(t)
        if name:
            by_name.setdefault(name, []).append(t)
    pool, seen, todo = {parsed["key"]: parsed}, {text}, [parsed]
    while todo:
        g = todo.pop()
        for st in g.get("steps") or ():
            inc = _include_entry(st)[0]
            if not inc:
                continue
            for t in by_name.get(_include_target(inc)[1], ()):
                if t in seen:
                    continue
                seen.add(t)
                p = parse_guide(t, map_ids=map_ids)
                if p.get("key") and p["key"] not in pool:
                    pool[p["key"]] = p
                    todo.append(p)
    expand_includes(pool)
    return parsed


def _header_name(text):
    for line in (text or "").splitlines():
        s = line.split("--", 1)[0].strip()
        if s.startswith("#name"):
            return s[5:].strip() or None
        if s.startswith("step"):
            return None
    return None


# ------------------------------------------------------------------------------------------------
# which steps show, for whom
# ------------------------------------------------------------------------------------------------
def players():
    """Every character a player can make, as the page's pickers name them: "Human/MAGE", "Skyborne@Horde/SHAMAN"
    (Skyborne joins either faction and plays that faction's classes)."""
    out = []
    for faction in ("Alliance", "Horde"):
        for race in FACTION_RACES[faction] + ("Skyborne",):
            pick = "Skyborne@%s" % faction if race == "Skyborne" else race
            for cls in CLASSES:
                if cls in RACE_CLASSES[race] and cls in FACTION_CLASSES[faction]:
                    out.append((pick, cls, race, faction))
    return out


def rxp_options(cfg_rxp):
    """Glide's RestedXP options for the page: the coach's defaults, then config.json "companion": {"rxp": ...}."""
    o = dict(DEFAULT_RXP)
    o.update({k: v for k, v in (cfg_rxp or {}).items() if k in RXP_KEYS and v is not None})
    return o


def setting_combos(options):
    """The 8 RxpSettings the page can toggle to, combination i as TOGGLES says; the rest (season, phase, XP rate,
    dungeons) as the options give them."""
    out = []
    for i in range(8):
        o = dict(options)
        o.update({k: bool(i & (1 << b)) for b, k in enumerate(TOGGLES)})
        out.append(RxpSettings.from_options(o))
    return out


def _hex(bits, n):
    return format(bits, "0%dx" % ((n + 3) // 4))


def visibility(guide, options=None):
    """Which steps RestedXP shows, for every character and settings combination, by the rules the coach numbers
    steps with (knowledge/rxp_parser/views.py visible_steps): a step is shown for a character when its `step <<`
    condition applies (never for a condition nobody meets) and step_shown() passes its effective tags under the
    settings and the guide's own dungeon rules.

    guide: {"text", "steps"} (parse_guide's, includes expanded). Returns
      {"players": ["Human/WARRIOR", ...], "settings": [{"ssf", "group_quests", "hardcore"} x 8],
       "masks": [mask], "steps": [index into masks per step], "actions": {(step idx, action pos): player mask}}
    A mask is hex: 8 chunks (one per settings combination, in order) of len(players) bits each, bit p for players[p]
    (chunk = hex of sum(1 << p), zero-padded to ceil(players / 4) digits). An action mask (one chunk, no settings)
    is there only for an action with its own `<<` condition."""
    pl = players()
    n = len(pl)
    combos = setting_combos(rxp_options(options))
    steps = guide.get("steps") or ()
    who = [{"cls": cls, "race": race, "faction": faction} for _pick, cls, race, faction in pl]
    bits = [[0] * 8 for _ in steps]
    act_bits = {}
    for p, player in enumerate(who):
        memo = {}

        def ok(c, player=player, memo=memo):
            raw = c.get("raw") if isinstance(c, dict) else c
            if raw is None:
                return True
            if raw not in memo:
                memo[raw] = cond_applies(c, **player)
            return memo[raw]
        gd = guide_dungeons(guide, applies=ok, **player)
        shown_memo = {}
        for i, st in enumerate(steps):
            cond = st.get("cond")
            for ai, a in enumerate(st.get("actions") or ()):
                if (a.get("cond") or {}).get("raw"):
                    act_bits[(i, ai)] = act_bits.get((i, ai), 0) | (int(ok(a["cond"])) << p)
            if (cond and cond.get("never")) or not ok(cond):
                continue
            fl = st.get("flags") or {}
            flags = step_flags(st, **player) if fl.get("cond_flags") else fl
            key = repr(sorted(flags.items(), key=lambda kv: kv[0]))
            if key not in shown_memo:
                shown_memo[key] = [step_shown(flags, s, gd) for s in combos]
            for s, shown in enumerate(shown_memo[key]):
                if shown:
                    bits[i][s] |= 1 << p
    masks, index, step_masks = [], {}, []
    for b in bits:
        m = "".join(_hex(x, n) for x in b)
        if m not in index:
            index[m] = len(masks)
            masks.append(m)
        step_masks.append(index[m])
    return {"players": ["%s/%s" % (pick, cls) for pick, cls, _r, _f in pl],
            "settings": [{k: bool(i & (1 << b)) for b, k in enumerate(TOGGLES)} for i in range(8)],
            "masks": masks, "steps": step_masks,
            "actions": {k: _hex(v, n) for k, v in act_bits.items()}}


def player_mask(cond):
    """Who a `<<` condition applies to, as an action mask (one chunk of players bits), or None without one."""
    raw = cond.get("raw") if isinstance(cond, dict) else cond
    if not raw:
        return None
    pl = players()
    bits = 0
    for p, (_pick, cls, race, faction) in enumerate(pl):
        if cond_applies(cond, cls=cls, race=race, faction=faction):
            bits |= 1 << p
    return _hex(bits, len(pl))


def numbering(vis, player, combo):
    """RestedXP's step number -> position in the guide's steps, read from visibility()'s masks for one character
    (index into vis["players"]) and settings combination: what the page shows, for the parity test."""
    n = len(vis["players"])
    w = (n + 3) // 4
    out, k = {}, 0
    for i, m in enumerate(vis["steps"]):
        chunk = int(vis["masks"][m][combo * w:(combo + 1) * w], 16)
        if chunk >> player & 1:
            k += 1
            out[k] = i
    return out


# ------------------------------------------------------------------------------------------------
# map geometry
# ------------------------------------------------------------------------------------------------
def map_geometry(path, maps):
    """{ui_map: {"name", "parent", "w_yd", "h_yd", "rect"}} for these maps and their parents, from kb.db: the size in
    yards (map.width_yd / height_yd, else from map_bounds), and rect = [x0, y0, x1, y1], where the map's corners
    (0, 0) and (100, 100) fall on its parent's map in percent (both maps' bounds on one continent), else None. {}
    without a knowledge base."""
    want = sorted({m for m in maps if isinstance(m, int)})
    if not path or not want:
        return {}
    info = {r["ui_map"]: r for r in _rows(path, "SELECT ui_map, name, parent, width_yd, height_yd FROM map "
                                                "WHERE ui_map IN (%s)" % ",".join("?" * len(want)), want)}
    parents = sorted({r["parent"] for r in info.values() if isinstance(r.get("parent"), int) and r["parent"] > 0}
                     - set(want))
    if parents:
        info.update({r["ui_map"]: r for r in _rows(path, "SELECT ui_map, name, parent, width_yd, height_yd FROM map "
                                                         "WHERE ui_map IN (%s)" % ",".join("?" * len(parents)),
                                                   parents)})
    every = sorted(set(want) | set(info))
    bounds = {r["ui_map"]: r for r in _rows(path, "SELECT * FROM map_bounds WHERE ui_map IN (%s)"
                                                  % ",".join("?" * len(every)), every)}
    out = {}
    for um in every:
        r, b = info.get(um) or {}, bounds.get(um)
        if not r and not b:
            continue
        w, h = r.get("width_yd"), r.get("height_yd")
        if not (w and h) and b:
            w, h = kstore.map_size_yd(b)
        par = r.get("parent")
        parent = par if isinstance(par, int) and par > 0 else None
        pb = bounds.get(parent) if parent else None
        rect = None
        if b and pb and b.get("continent") == pb.get("continent"):
            x0, y0 = kstore.world_to_map(pb, *kstore.map_to_world(b, 0, 0))
            x1, y1 = kstore.world_to_map(pb, *kstore.map_to_world(b, 100, 100))
            if x0 is not None and y0 is not None and x1 is not None and y1 is not None:
                rect = [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]
        out[um] = {"name": r.get("name"), "parent": parent,
                   "w_yd": round(w, 1) if w else None, "h_yd": round(h, 1) if h else None, "rect": rect}
    return out
