"""RestedXP guide strings added from the app, so a guide you bought reaches the coach and RestedXP without the import
window in the game.

RestedXP sells a guide as an import string for one Battle.net account: base64 chunks encrypted with a key RestedXP
makes from the account's BattleTag, each a zlib stream whose checksum the string carries. RestedXP saves that key in
its account SavedVariables (WTF/Account/<account>/SavedVariables/RXPGuides.lua, RXPData.cache) the first time it
runs on the account, so adding a string here works for exactly the accounts that already run RestedXP on this PC,
the same check RestedXP's importer makes. Glide reads the key only from there and never asks for a BattleTag.

Adding a string:
  1. the string is decoded with each account's saved key; the one whose checksums match is the account it was sold to
     (no match: "this string is for another Battle.net account");
  2. the guide texts go to data/rxp_imports/imports.json, which the knowledge build (tools/build_db.py) reads like
     RestedXP's own cache, so the coach knows the route at its next refresh;
  3. the decrypted guide texts also go to <wow_dir>/Interface/AddOns/Glide/Data/RXPImports.lua, and at the next login
     the Glide addon (RXPImport.lua) loads them straight into RestedXP (ImportGuide) without running RestedXP's
     in-game string decryptor. The decrypted texts no longer carry RestedXP's account check and every account on the
     PC shares that folder, so each pack carries its buyer account's key check (key_check) and the addon loads it only
     into the account whose RestedXP key matches.
The addon notes each hand-off in GlideRecorderDB.rxpImports[id] on the account that took it; once the buyer's
account has noted it done (note_for: another account's note never counts), its guides leave the addon's file. An
addon install replaces the addon's folder (and that file with it), so the app syncs again after installing; an addon
older than HANDOFF_FORMAT (it reads encrypted strings) keeps the file it has until it's updated.

Run by the desktop app (`python companion/rxp_import.py add|status|sync|guide`, the string on stdin for add), answering
one JSON object on stdout. Guide texts are yours and stay on this PC, like RestedXP's own cache.
"""
import base64
import glob
import hashlib
import json
import os
import re
import sys
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import jsonfile  # noqa: E402
from log import log_once  # noqa: E402

STORE = os.path.join("data", "rxp_imports", "imports.json")
HANDOFF = os.path.join("Interface", "AddOns", "Glide", "Data", "RXPImports.lua")
# one encrypted chunk: <checksum><mode><base64>%  (RestedXP's LoadImportBuffer; mode ":" is the only one it reads)
_CHUNK = re.compile(r"(-?\d+)(\D)([A-Za-z0-9+/=]+)%", re.ASCII)
_LUA_ESC = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
MAX_LEN = 64 << 20
INCOMPLETE = ("That guide string is incomplete. Download the guide file from RestedXP again, or copy all of its string "
              "again.")


class ImportProblem(Exception):
    """A string Glide can't add, with the reason as the player reads it."""


# ------------------------------------------------------------------------------------------------
# the string and its key (RestedXP GuideLoader.lua: ImportString, CheckDataIntegrity, ReadCacheData, A32)
# ------------------------------------------------------------------------------------------------
def clean(text):
    """The string as RestedXP reads it: from its first digit to its last, whitespace (a line break from the paste)
    removed."""
    s = re.sub(r"\s+", "", text or "")
    s = re.sub(r"^\D+", "", s, flags=re.ASCII)
    return re.sub(r"\D+$", "", s, flags=re.ASCII)


def parse(text):
    """{"count", "version", "chunks": [(checksum, mode, base64)]} of an import string; ImportProblem when it isn't
    one."""
    s = clean(text)
    if not s:
        raise ImportProblem("That isn't a RestedXP guide string. Choose the guide file you downloaded from RestedXP, "
                            "or paste its whole string.")
    if len(s) > MAX_LEN:
        raise ImportProblem("That's too long to be a RestedXP guide string.")
    count = re.match(r"^(\d+)\|", s, re.ASCII)
    version = re.search(r"\|(\d+)$", s, re.ASCII)
    if not count or not version or not re.search(r"\|(\d+):", s, re.ASCII):
        raise ImportProblem(INCOMPLETE)
    chunks = [(int(m.group(1)), m.group(2), m.group(3)) for m in _CHUNK.finditer(s)]
    if not chunks:
        raise ImportProblem(INCOMPLETE)
    return {"count": int(count.group(1)), "version": int(version.group(1)), "chunks": chunks, "string": s}


def key_from_battletag(tag):
    """The 16-number key RestedXP makes from a BattleTag (ReadCacheData): the tag's last 16 bytes in lower case,
    right-aligned, then mixed in place."""
    raw = bytes(b + 32 if 65 <= b <= 90 else b for b in str(tag).encode("utf-8"))   # Lua's lower(): ASCII only
    raw = raw[-16:]
    k = 16 - len(raw)
    buf = [0] * 16
    for i in range(16):
        j = (i - k) & 0xF
        buf[i] = raw[j] if j < len(raw) else 0
    for i in range(16):
        buf[-i & 0xF] = buf[(15 - i) & 0xF] ^ buf[(13 - i) & 0xF] ^ buf[(12 - i) & 0xF] ^ buf[(10 - i) & 0xF]
    return buf


def key_from_cache(v):
    """The key from RXPData.cache: the 16 numbers RestedXP keeps there (keys 0..15), or the BattleTag it sometimes
    leaves there instead. None when it holds neither."""
    if isinstance(v, str) and v:
        return key_from_battletag(v)
    if isinstance(v, dict):
        try:
            key = [int(v[i] if i in v else v[str(i)]) for i in range(16)]
        except (KeyError, TypeError, ValueError):
            return None
    elif isinstance(v, list) and len(v) == 16:
        key = [int(x) if isinstance(x, (int, float)) else -1 for x in v]
    else:
        return None
    return key if all(0 <= x <= 255 for x in key) else None


def _rc4(key, data):
    """RestedXP's stream cipher: the 256-entry schedule from the 16-number key, then the keystream XORed in."""
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i & 0xF]) & 0xFF
        s[i], s[j] = s[j], s[i]
    out = bytearray(len(data))
    i = j = 0
    for n, b in enumerate(data):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out[n] = b ^ s[(s[i] + s[j]) & 0xFF]
    return bytes(out)


def _chunk(key, checksum, mode, b64):
    """One chunk's plain bytes, or None when this key isn't the one it was made with."""
    if mode != ":":
        return None
    try:
        data = base64.b64decode(b64, validate=True)
    except ValueError:
        return None
    z = _rc4(key, data)
    want = checksum % 4294967296
    # the zlib header, and the Adler-32 at the end: RestedXP's own "account mismatch" test
    if len(z) < 8 or z[0] & 0x0F != 8 or z[0] > 0x78 or z[1] & 0x20 or (z[0] * 256 + z[1]) % 31:
        return None
    if int.from_bytes(z[-4:], "big") != want:
        return None
    try:
        plain = zlib.decompress(z)
    except zlib.error:
        return None
    return plain if zlib.adler32(plain) == want else None


def decode(parsed, key):
    """The guide texts in a parsed string, with this key; None when the key doesn't open it."""
    texts = []
    for checksum, mode, b64 in parsed["chunks"]:
        plain = _chunk(key, checksum, mode, b64)
        if plain is None:
            return None
        texts += [t.decode("utf-8", "replace") for t in plain.split(b"\0") if t]
    return texts


_STEP_LINE = re.compile(r"^\s*step\b", re.M)
FREE_ID = "rxp-free"        # the Guide & Route Explorer's id for RestedXP's free guides (an import's id is hex)
_REGISTER = re.compile(r"RXPGuides\.RegisterGuide\(\[\[")
_GROUP_LINE = re.compile(r"^\s*#group[ \t]+([^\r\n]+)", re.M)


def guide_meta(text):
    """(group, name) from a guide's #group and #name lines (a "<< condition" after the group dropped)."""
    def first(tag):
        m = re.search(r"^\s*#%s[ \t]+([^\r\n]+)" % tag, text, re.M)
        return m.group(1).split("<<")[0].strip() if m else None
    return first("group"), first("name")


def guide_summary(index, g):
    """Lightweight summary of one stored guide dict for the guide browser's list."""
    text = (g.get("text") if isinstance(g, dict) else "") or ""
    m_step = _STEP_LINE.search(text)
    hdr = text[:m_step.start()] if m_step else text

    def tag(name):
        m = re.search(r"^\s*#%s[ \t]+([^\r\n]+)" % name, hdr, re.M)
        return m.group(1).split("--")[0].split("<<")[0].strip() if m else None

    group = (g.get("group") if isinstance(g, dict) else None) or tag("group") or "RestedXP"
    name = (g.get("name") if isinstance(g, dict) else None) or tag("name") or "?"
    subgroup = tag("subgroup")
    nxt = tag("next")
    default_for = tag("defaultfor")
    ver = tag("version")
    faction = None
    if re.search(r"<<\s*Alliance\b|\(A\)|\bAlliance\b", hdr + " " + group):
        faction = "Alliance"
    elif re.search(r"<<\s*Horde\b|\(H\)|\bHorde\b", hdr + " " + group):
        faction = "Horde"
    return {
        "index": int(index),
        "group": group,
        "subgroup": subgroup,
        "name": name,
        "next": nxt,
        "faction": faction,
        "defaultFor": default_for,
        "version": int(ver) if ver and ver.isdigit() else None,
        "steps": len(_STEP_LINE.findall(text)),
        # the forms the addon puts on the strip (page G gn / gg), so the Guides page finds the guide the character plays
        "rxp_name": page_text(affix_name(name)),
        "rxp_group": page_text(group),
    }


def affix_name(name):
    """RestedXP's own name for a guide: '5-11 Dun Morogh' -> '05-11 Dun Morogh' (knowledge.rxp_parser.guide.affix_name)."""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from knowledge.rxp_parser.guide import affix_name as affix
    return affix(name)


def page_text(s):
    """A text as the addon's clean() puts it on a page: ; = | and newlines -> ',' and cut to 40 bytes
    (knowledge.sources.live_logs.page_text)."""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from knowledge.sources.live_logs import page_text as page
    return page(s)


def free_guides(root, wow_dir):
    """(source, [{"groups", "name", "text", "file"}]): RestedXP's free Forever guides, which come with the RestedXP
    addon instead of in a bought string (the starter zones to about level 20, Zephras Isle, the Mage AoE routes and
    their dungeon variants). They are read where the knowledge build reads them (tools/build_db.py free_guide_texts):
    the installed addon's Guides/forever, else the seed snapshot. A guide RestedXP files under one group per faction
    ("#group ... (A) << Alliance" and "(H) << Horde") lists every group."""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from knowledge import store as kstore
    files, source = [], None
    rxp_dir = os.path.join(wow_dir, *kstore.RXP_ADDON_DIR) if wow_dir else None
    if rxp_dir and os.path.isdir(rxp_dir):
        for p in kstore.rxp_files(rxp_dir):
            rel = os.path.relpath(p, os.path.join(rxp_dir, "Guides")).replace(os.sep, "/")
            if rel.lower().startswith("forever/"):
                try:
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        files.append((rel, fh.read()))
                except OSError as e:
                    log_once("rxp_import.free_guides", e)
        source = "RestedXP %s" % (kstore.rxp_version(rxp_dir) or "addon") if files else None
    seed = os.path.join(root, "data", "seed", "seed.json.gz")
    if not files and os.path.exists(seed):
        try:
            import gzip
            with gzip.open(seed, "rt", encoding="utf-8") as fh:
                files = sorted(((json.load(fh).get("sources") or {}).get("rxp_free") or {}).items())
            source = "Glide's copy" if files else None
        except (OSError, ValueError) as e:
            log_once("rxp_import.free_seed", e)
    out = []
    for rel, text in files:
        for chunk in _REGISTER.split(text)[1:]:
            body = chunk.split("]])", 1)[0]
            m_step = _STEP_LINE.search(body)
            hdr = body[:m_step.start()] if m_step else body
            groups = []
            for g in _GROUP_LINE.findall(hdr):
                g = g.split("--")[0].split("<<")[0].strip()
                if g and g not in groups:
                    groups.append(g)
            name = guide_meta(body)[1]
            if name and groups:
                out.append({"groups": groups, "name": name, "text": body, "file": rel})
    return source, out


def free_catalog(root, wow_dir, taken=()):
    """The Explorer's entry for RestedXP's free guides, shaped like an import (status()), or None when there are none.
    A free guide whose group and name a bought one also has (`taken`: {(group, name)}) is left out."""
    source, guides = free_guides(root, wow_dir)
    items = []
    for i, g in enumerate(guides):
        for grp in g["groups"]:
            if (grp, g["name"]) in taken:
                continue
            item = guide_summary(i, {"group": grp, "name": g["name"], "text": g["text"]})
            item["faction"] = "Alliance" if "(A)" in grp else "Horde" if "(H)" in grp else item["faction"]
            items.append(item)
    if not items:
        return None
    return {"id": FREE_ID, "free": True, "source": source, "added": None, "account": None,
            "guides": [it["name"] for it in items], "items": items, "rxp": "done", "found": None, "error": None}


def _entity_names(root, qids, npcs, items, objects=()):
    """Best-effort {quests: {id: name}, npcs: {id: name}, items: {id: name}, objects: {id: name}}: from the newest
    knowledge base first (kb.db quest, creature, item and gameobject names, through knowledge.api), then
    data/glide.db for what it lacks."""
    out = {"quests": {}, "npcs": {}, "items": {}, "objects": {}}
    if not (qids or npcs or items or objects):
        return out
    try:
        import rxp_facts
        from knowledge.api import explorer
        kb, _ = rxp_facts.newest_kb(root)
        if kb is not None:
            try:
                got = explorer.entity_names(kb, qids, npcs, items, objects)
            finally:
                kb.close()
            for k in out:
                out[k].update(got.get(k) or {})
    except Exception as e:  # noqa: BLE001 - names are a nicety: glide.db below still answers
        log_once("rxp_import.entity_names", e)
    db_path = os.path.join(root, "data", "glide.db")
    want = {"quests": [i for i in qids if int(i) not in out["quests"]],
            "npcs": [i for i in npcs if int(i) not in out["npcs"]],
            "items": [i for i in items if int(i) not in out["items"]]}
    if not os.path.exists(db_path) or not any(want.values()):
        return out
    import sqlite3
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)
    except sqlite3.Error:
        return out
    try:
        if want["quests"]:
            ph = ",".join("?" * len(want["quests"]))
            for qid, name in conn.execute("SELECT id, name FROM quests WHERE id IN (%s) AND name IS NOT NULL" % ph,
                                          tuple(want["quests"])):
                out["quests"][int(qid)] = name
        for kind, bucket in (("npc", "npcs"), ("item", "items")):
            ids = want[bucket]
            if ids:
                ph = ",".join("?" * len(ids))
                for eid, name in conn.execute(
                        "SELECT id, name FROM entities WHERE kind = ? AND id IN (%s) AND name IS NOT NULL" % ph,
                        (kind, *ids)):
                    out[bucket][int(eid)] = name
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return out


def _map_bounds(root, maps):
    """{ui_map: map_bounds row} for these maps from the newest kb.db (the knowledge build's world <-> map transforms,
    from the game client's own map data), or {} without a knowledge base."""
    import rxp_explorer
    path = rxp_explorer.kb_path(root)
    maps = sorted(m for m in maps if isinstance(m, int))
    if not maps or not path:
        return {}
    import sqlite3
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
        conn.row_factory = sqlite3.Row
        try:
            return {r["ui_map"]: dict(r) for r in conn.execute(
                "SELECT * FROM map_bounds WHERE ui_map IN (%s)" % ",".join("?" * len(maps)), maps)}
        finally:
            conn.close()
    except sqlite3.Error as e:
        log_once("rxp_import.map_bounds", e)
        return {}


def place_world_points(parsed, bounds):
    """Map positions (percent) for what a guide gives only in world coordinates: RestedXP's Forever guides write
    `.goto 1411/1,-4251.46,-607.35` (map, continent, world), and the parser keeps those as wx/wy with x/y empty. As
    the knowledge build's last step does for its copy (knowledge/sources/finalize.py): the step's x/y, an action's
    args x/y, and a .loop/.line's points from its wpoints, the columns' convention detected once for the guide. A
    point that falls off its map stays unplaced. Changes `parsed` in place; returns how many positions it placed."""
    from knowledge.sources import finalize

    def map_of(args):
        return finalize._map_of(args)       # noqa: SLF001 - the knowledge build's own reading of an action's map
    samples = []
    for st in parsed.get("steps") or ():
        if st.get("x") is None and st.get("wx") is not None and st.get("wy") is not None and st.get("ui_map") in bounds:
            samples.append((st["ui_map"], st["wx"], st["wy"]))
        for a in st.get("actions") or ():
            args = a.get("args") or {}
            um = map_of(args)
            if um not in bounds:
                continue
            if isinstance(args.get("wx"), (int, float)) and isinstance(args.get("wy"), (int, float)) and args.get("x") is None:
                samples.append((um, args["wx"], args["wy"]))
            for p in args.get("wpoints") or ():
                if isinstance(p, list) and len(p) == 2 and None not in p:
                    samples.append((um, p[0], p[1]))
    if not samples:
        return 0
    swap, _, _ = finalize.detect_swap(samples[:2000], bounds)
    placed = 0

    def put(um, wx, wy):
        x, y = finalize._convert(bounds[um], wx, wy, swap)     # noqa: SLF001
        if x is None or y is None or not finalize._on_map((x, y)):     # noqa: SLF001
            return None
        return round(x, 2), round(y, 2)
    for st in parsed.get("steps") or ():
        if st.get("x") is None and st.get("wx") is not None and st.get("wy") is not None and st.get("ui_map") in bounds:
            xy = put(st["ui_map"], st["wx"], st["wy"])
            if xy:
                st["x"], st["y"] = xy
                placed += 1
        for a in st.get("actions") or ():
            args = a.get("args") or {}
            um = map_of(args)
            if um not in bounds:
                continue
            if isinstance(args.get("wx"), (int, float)) and isinstance(args.get("wy"), (int, float)) and args.get("x") is None:
                xy = put(um, args["wx"], args["wy"])
                if xy:
                    args["x"], args["y"] = xy
                    placed += 1
            if isinstance(args.get("wpoints"), list) and not args.get("points"):
                pts = [put(um, p[0], p[1]) if isinstance(p, list) and len(p) == 2 and None not in p else None
                       for p in args["wpoints"]]
                if any(pts):
                    args["points"] = [list(p) if p else [None, None] for p in pts]
                    placed += 1
    return placed


def expanded_guide(root, iid, guides, text, wow_dir=None, map_ids=None):
    """A guide's text parsed for the Guides page, its #include steps expanded over every guide the knowledge build
    reads (the free ones and the added ones), so the steps and their numbers (idx) are kb.db's. `guides`: the guides
    of import `iid` (the free ones for FREE_ID). guide_detail and the facts command (rxp_facts) both parse through
    here, so their step numbers agree."""
    import rxp_explorer as X
    corpus = [g["text"] for g in free_guides(root, wow_dir)[1]] if str(iid) != FREE_ID else [g["text"] for g in guides]
    for rec in load_store(root)["imports"].values():
        corpus += [g.get("text") or "" for g in (rec.get("guides") if isinstance(rec, dict) else None) or ()
                   if isinstance(g, dict)]
    if map_ids is None:
        map_ids = X.map_names(X.kb_path(root))
    return X.include_expanded(text, corpus, map_ids)


def guide_detail(root, iid, index, wow_dir=None):
    """Full parsed guide (steps, waypoints, loops, lines, actions, commentary) for the interactive guide browser:
    guide `index` of an added string, or of RestedXP's free guides (iid FREE_ID)."""
    if str(iid) == FREE_ID:
        guides = [{"group": g["groups"][0], "name": g["name"], "text": g["text"]} for g in free_guides(root, wow_dir)[1]]
    else:
        rec = load_store(root)["imports"].get(str(iid))
        guides = (rec.get("guides") if isinstance(rec, dict) else None) or []
    idx = int(index)
    if idx < 0 or idx >= len(guides) or not isinstance(guides[idx], dict):
        raise ImportProblem("Guide not found in that import.")
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    import rxp_explorer as X
    from knowledge.rxp_parser.maps import DEFAULT_MAPS
    text = guides[idx].get("text") or ""
    kb = X.kb_path(root)
    map_ids = X.map_names(kb)
    parsed = expanded_guide(root, iid, guides, text, wow_dir, map_ids)
    # RestedXP's Forever guides give positions in world coordinates: put them on their maps (kb.db's map bounds)
    steps_in: list = parsed.get("steps") or []
    maps_used = {st.get("ui_map") for st in steps_in}
    maps_used |= {(a.get("args") or {}).get("ui_map") for st in steps_in for a in st.get("actions") or ()}
    world_only = sum(1 for st in parsed.get("steps") or () if st.get("x") is None and st.get("wx") is not None)
    placed = place_world_points(parsed, _map_bounds(root, maps_used)) if world_only else 0
    qids, npcs, items = set(), set(), set()
    for st in parsed.get("steps") or ():
        for a in st.get("actions") or ():
            if a.get("quest_id"):
                qids.add(int(a["quest_id"]))
            if a.get("entity_kind") == "npc" and a.get("entity_id"):
                npcs.add(int(a["entity_id"]))
            elif a.get("entity_kind") == "item" and a.get("entity_id"):
                items.add(int(a["entity_id"]))
    names = _entity_names(root, qids, npcs, items)
    kb_names = {v: k for k, v in map_ids.items()}
    try:
        import glideconfig
        cfg_rxp = (glideconfig.read(root)[0].get("companion") or {}).get("rxp")
    except Exception as e:  # noqa: BLE001 - an unreadable config: the coach's defaults
        log_once("rxp_import.guide_detail.config", e)
        cfg_rxp = None
    vis = X.visibility({"text": text, "steps": list(steps_in)}, cfg_rxp if isinstance(cfg_rxp, dict) else None)
    steps_out = []
    zones_seen = {}
    for si, st in enumerate(steps_in):
        actions_out = []
        wps = []
        loops = []
        for ai, a in enumerate(st.get("actions") or []):
            verb = a.get("verb")
            if not verb:
                continue
            act_vis = vis["actions"].get((si, ai))
            args = a.get("args") or {}
            um = args.get("ui_map")
            zname = (DEFAULT_MAPS.name(um) or kb_names.get(um)) if um else (args.get("zone") or st.get("zone"))
            if um and zname:
                zones_seen[int(um)] = zname
            if verb in ("goto", "groundgoto", "flygoto", "waypoint", "pin", "questgoto", "treasure", "rare"):
                if args.get("x") is not None and args.get("y") is not None:
                    wps.append({"x": args["x"], "y": args["y"], "zone": zname, "ui_map": um,
                                "radius": args.get("radius"), "mode": args.get("mode") or verb,
                                "text": a.get("text") or args.get("tooltip"), "act": len(actions_out)})
            elif verb in ("loop", "line") and args.get("points"):
                pts = [p for p in args["points"] if isinstance(p, list) and len(p) == 2 and p[0] is not None and p[1] is not None]
                if pts:
                    loops.append({"kind": verb, "zone": zname, "ui_map": um, "points": pts, "range": args.get("range")})
            qid = a.get("quest_id")
            ekind, eid = a.get("entity_kind"), a.get("entity_id")
            ename = (names["npcs"].get(int(eid)) if ekind == "npc" and eid else
                     names["items"].get(int(eid)) if ekind == "item" and eid else None) or args.get("name")
            units = [u.get("name") for u in (args.get("units") or ()) if isinstance(u, dict) and u.get("name")]
            actions_out.append({
                "line": a.get("line"),
                "verb": verb,
                "kind": args.get("kind"),
                "text": a.get("text"),
                "raw_text": args.get("text_raw") or a.get("text"),
                "comment": args.get("comment"),
                "cond": (a.get("cond") or {}).get("raw"),
                "quest_id": qid,
                "quest_name": names["quests"].get(int(qid)) if qid else None,
                "entity_kind": ekind,
                "entity_id": eid,
                "entity_name": ename,
                "units": units or None,
                "qty": a.get("qty"),
                "zone": zname,
                "ui_map": um,
                "x": args.get("x"),
                "y": args.get("y"),
                "location": args.get("location"),
                "url": args.get("url") if verb == "link" else None,
                "level": args.get("level") if verb in ("xp", "maxlevel", "skill") else None,
                "xp": args.get("xp") if verb in ("xp", "maxlevel") else None,
            })
            if act_vis is not None:
                actions_out[-1]["vis"] = act_vis
            more_q = [int(x) for x in args.get("quest_ids") or () if isinstance(x, int) and not isinstance(x, bool)]
            if len(more_q) > 1:
                actions_out[-1]["quest_ids"] = more_q      # a multi-quest verb (.turninmultiple 101,200): every quest
        if st.get("ui_map") and st.get("zone"):
            zones_seen[int(st["ui_map"])] = st["zone"]
        flags_in = st.get("flags")
        flags = {k: v for k, v in flags_in.items() if k != "cond_flags"} if isinstance(flags_in, dict) else {}
        steps_out.append({
            "idx": st["idx"],
            "line": st["line"],
            "text": st.get("text"),
            "comments": st.get("comments"),
            "cond": (st.get("cond") or {}).get("raw"),
            "flags": flags,
            "zone": st.get("zone"),
            "ui_map": st.get("ui_map"),
            "x": st.get("x"),
            "y": st.get("y"),
            "waypoints": wps,
            "loops": loops,
            "actions": actions_out,
            "vis": vis["steps"][si],
        })
    return {
        "id": str(iid),
        "index": idx,
        "name": parsed.get("name") or guides[idx].get("name") or "?",
        "displayname": parsed.get("displayname"),
        "group": parsed.get("grp") or guides[idx].get("group") or "RestedXP",
        "subgroup": parsed.get("subgroup"),
        "next": parsed.get("next"),
        "next_list": [{"name": n.get("name"), "group": n.get("group"), "cond": (n.get("cond") or {}).get("raw"),
                       "vis": X.player_mask(n.get("cond"))} for n in (parsed.get("next_list") or ())],
        "level_min": parsed.get("level_min"),
        "level_max": parsed.get("level_max"),
        "faction": parsed.get("faction"),
        "default_for": parsed.get("default_for"),
        "version": parsed.get("version"),
        "cond": (parsed.get("cond") or {}).get("raw"),
        "comments": parsed.get("comments"),
        "zones": [{"ui_map": k, "name": v} for k, v in zones_seen.items()],
        "steps": steps_out,
        # steps RestedXP placed only in world coordinates, and whether they could be put on the map: without the
        # knowledge base's map bounds (data/kb.db) the map has nothing to draw, and the Explorer says why
        "world_only": world_only,
        "placed": placed,
        # which steps show, for whom (rxp_explorer.visibility): the page reads these instead of `<<` conditions
        "players": vis["players"],
        "settings": vis["settings"],
        "vis_masks": vis["masks"],
        "defaults": {k: bool(v) for k, v in X.rxp_options(cfg_rxp if isinstance(cfg_rxp, dict) else None).items()
                     if k in X.TOGGLES},
        # each zone's size in yards and its rectangle on its continent (kb.db), {} without a knowledge base
        "maps": {str(k): v for k, v in X.map_geometry(kb, set(zones_seen)).items()},
    }


# ------------------------------------------------------------------------------------------------
# the player's files
# ------------------------------------------------------------------------------------------------
def _lua():
    if ROOT not in sys.path:
        sys.path.append(ROOT)
    from knowledge.sources import _lua
    return _lua


def account_keys(wow_dir):
    """[(account folder, key)] of every account whose RestedXP saved its key on this PC."""
    out = []
    if not wow_dir:
        return out
    for p in sorted(glob.glob(os.path.join(wow_dir, "WTF", "Account", "*", "SavedVariables", "RXPGuides.lua"))):
        try:
            data = _lua().read_file(p) or {}
        except Exception as e:  # noqa: BLE001 - a damaged file: that account has no key here
            log_once("rxp_import.account_keys", e)
            continue
        rxp_data = data.get("RXPData")
        key = key_from_cache(rxp_data.get("cache")) if isinstance(rxp_data, dict) else None
        if key:
            out.append((os.path.basename(os.path.dirname(os.path.dirname(p))), key))
    return out


def key_check(key):
    """A short check value of an account's RestedXP key (Adler-32 of its 16 numbers). The addon computes the same
    from RestedXP's key for whoever logs in, so a pack goes only into the account it was bought for: the decrypted
    guides carry no account check of their own, and every account on the PC shares the AddOns folder."""
    return zlib.adler32(bytes(key))


def handed_over(wow_dir):
    """{id: {account folder: {"t", "ok", "err", "account"}}}: what the Glide addon noted in GlideRecorderDB.rxpImports
    on each account (WoW writes it at logout and /reload). note_for() picks the one that counts."""
    out = {}
    if not wow_dir:
        return out
    for p in glob.glob(os.path.join(wow_dir, "WTF", "Account", "*", "SavedVariables", "Glide.lua")):
        try:
            data = _lua().read_file(p) or {}
        except Exception as e:  # noqa: BLE001 - a damaged file: nothing noted
            log_once("rxp_import.handed_over", e)
            continue
        db = data.get("GlideRecorderDB")
        notes = db.get("rxpImports") if isinstance(db, dict) else None
        for k, v in (notes.items() if isinstance(notes, dict) else ()):
            if not isinstance(v, dict):
                continue
            acct = os.path.basename(os.path.dirname(os.path.dirname(p)))
            out.setdefault(str(k), {})[acct] = dict(v, account=acct)
    return out


def note_for(notes, rec):
    """The note that says whether RestedXP has this import: the one from the account it was bought for
    (rec["account"]). Another account's note never counts, so it can't take the pack out of the addon's file before
    the buyer's account has it. A record from before the account was kept: the best note (taken, then newest)."""
    notes = notes or {}
    acct = rec.get("account") if isinstance(rec, dict) else None
    if acct:
        return notes.get(acct) or {}
    best = {}
    for v in notes.values():
        if not best or (bool(v.get("ok")), v.get("t") or 0) > (bool(best.get("ok")), best.get("t") or 0):
            best = v
    return best


def _store_path(root):
    return os.path.join(root, STORE)


def load_store(root):
    """{"imports": {id: {"added", "account", "string", "count", "guides": [{"group", "name", "text"}]}}}."""
    try:
        data = jsonfile.read(_store_path(root))
    except (OSError, ValueError):
        return {"imports": {}}
    if not isinstance(data, dict) or not isinstance(data.get("imports"), dict):
        return {"imports": {}}
    return data


def guide_texts(root):
    """{"group||name": {"text", "how", "t"}} of every added guide, for the knowledge build."""
    out = {}
    for rec in load_store(root)["imports"].values():
        for g in rec.get("guides") or ():
            if isinstance(g, dict) and g.get("text"):
                out["%s||%s" % (g.get("group") or "?", g.get("name") or len(g["text"]))] = {
                    "text": g["text"], "how": "import", "t": rec.get("added") or 0}
    return out


def _lua_string(s):
    """s as a Lua string literal (quotes, backslashes and control bytes escaped; 3-digit \\ddd so a digit after
    a control byte is never read as part of the escape)."""
    out = []
    for ch in str(s):
        esc = _LUA_ESC.get(ch)
        if esc is not None:
            out.append(esc)
        else:
            o = ord(ch)
            out.append("\\%03d" % o if o < 32 or o == 127 else ch)
    return '"%s"' % "".join(out)


def handoff_lua(pending, checks=None):
    """The addon's Data/RXPImports.lua for these imports ([(id, record)]): the decrypted guide texts, each pack with
    its buyer account's key check (`k`, key_check(); checks: {id: check}). A pack without one isn't written: the addon
    could not tell which account may have it."""
    checks = checks or {}
    lines = ["-- Written by the Glide app (companion/rxp_import.py): the RestedXP guides you added in the app,",
             "-- already decrypted for RestedXP at your next login (RXPImport.lua). The app rewrites this file.",
             "local _, ns = ...", "ns.rxpImports = {"]
    for iid, rec in pending:
        texts = [g.get("text") for g in (rec.get("guides") or ())
                 if isinstance(g, dict) and isinstance(g.get("text"), str) and g.get("text")]
        if not texts or checks.get(iid) is None:
            continue
        body = ",\n        ".join(_lua_string(t) for t in texts)
        lines.append("    { id = %s, n = %d, k = %d, guides = {\n        %s,\n    } },"
                     % (_lua_string(iid), len(texts), checks[iid], body))
    lines.append("}")
    return "\n".join(lines) + "\n"


HANDOFF_FORMAT = (0, 17, 2)     # the first addon that loads decrypted guides (RXPImport.lua reads `guides`)


def _addon_version(wow_dir):
    """The installed Glide addon's version as a tuple ((0, 17, 2)), or None without one."""
    try:
        with open(os.path.join(wow_dir, "Interface", "AddOns", "Glide", "Glide.toc"), encoding="utf-8-sig") as fh:
            m = re.search(r"^##\s*Version:\s*(\d+(?:\.\d+)*)", fh.read(), re.M)
    except OSError:
        return None
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def sync(root, wow_dir):
    """Write the addon's Data/RXPImports.lua with the strings it hasn't handed to RestedXP yet. Returns the path
    written, or None (no WoW folder, or no Glide addon there)."""
    if not wow_dir:
        return None
    path = os.path.join(wow_dir, HANDOFF)
    if not os.path.isdir(os.path.dirname(os.path.dirname(path))):
        return None
    inst = _addon_version(wow_dir)
    if inst is not None and inst < HANDOFF_FORMAT:
        return None         # an older addon reads the encrypted strings its file has: leave it until it's updated
    done = handed_over(wow_dir)
    pending = [(k, v) for k, v in sorted(load_store(root)["imports"].items(), key=lambda kv: kv[1].get("added") or 0)
               if not note_for(done.get(k), v).get("ok")]
    keys = dict(account_keys(wow_dir))
    checks = {k: key_check(keys[v["account"]]) for k, v in pending if v.get("account") in keys}
    text = handoff_lua(pending, checks)
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read() == text:
                return path
    except OSError:
        if not pending:
            return None                 # an addon older than the file, and nothing waiting: leave its folder alone

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return path


def status(root, wow_dir):
    """[{"id", "added", "account", "guides": [names], "items": [guide_summary], "rxp": "waiting"|"done"|"failed",
    "found", "error"}], newest first: each added string and whether RestedXP has it yet. Last, when there are any,
    RestedXP's free guides as one more entry ({"id": FREE_ID, "free": True, "source", ...}, free_catalog), so the
    Explorer lists the levels a bought string leaves to them."""
    done = handed_over(wow_dir)
    out = []
    for iid, rec in load_store(root)["imports"].items():
        note = note_for(done.get(iid), rec)
        state = "done" if note.get("ok") else "failed" if note.get("err") else "waiting"
        raw_guides = rec.get("guides") or ()
        out.append({"id": iid, "added": rec.get("added"), "account": rec.get("account"),
                    "guides": [g.get("name") or "?" for g in raw_guides],
                    "items": [guide_summary(i, g) for i, g in enumerate(raw_guides)],
                    "rxp": state, "found": note.get("ok"), "error": note.get("err")})
    out.sort(key=lambda r: r.get("added") or 0, reverse=True)
    try:
        free = free_catalog(root, wow_dir, {(it["group"], it["name"]) for r in out for it in r["items"]})
    except Exception as e:  # noqa: BLE001 - the added guides still list without them
        log_once("rxp_import.free_catalog", e)
        free = None
    if free:
        out.append(free)
    return out


def add(root, wow_dir, text, now=None):
    """Add one import string: decode it with the account key RestedXP saved on this PC, keep its guides for the
    coach, and queue it for RestedXP at the next login. {"ok", "id", "account", "guides", "again", "handoff"};
    ImportProblem when it can't."""
    if not wow_dir or not os.path.isdir(wow_dir):
        raise ImportProblem("Set the WoW folder first: Glide reads your RestedXP account there.")
    parsed = parse(text)
    keys = account_keys(wow_dir)
    if not keys:
        raise ImportProblem("RestedXP hasn't saved your account on this PC yet. Once you've been in the game with "
                            "RestedXP on this account, Glide can add your guides from here.")
    account, texts = None, None
    for acct, key in keys:
        texts = decode(parsed, key)
        if texts is not None:
            account = acct
            break
    if texts is None:
        raise ImportProblem("This string is for a different Battle.net account than the ones that play RestedXP on "
                            "this PC. Check that you copied the string RestedXP gave this account.")
    guides = []
    for t in texts:
        group, name = guide_meta(t)
        guides.append({"group": group, "name": name, "text": t})
    if not guides:
        raise ImportProblem("That string opened, but there's no guide in it.")
    iid = hashlib.sha1(parsed["string"].encode("ascii")).hexdigest()[:16]
    store = load_store(root)
    again = iid in store["imports"]
    store["imports"][iid] = {"added": (store["imports"].get(iid) or {}).get("added") or int(now or time.time()),
                             "account": account, "string": parsed["string"], "count": parsed["count"],
                             "version": parsed["version"], "guides": guides}
    jsonfile.write_atomic(_store_path(root), store)
    try:
        handoff = sync(root, wow_dir)
    except OSError:
        handoff = None
    return {"ok": True, "id": iid, "account": account, "again": again, "handoff": bool(handoff),
            "guides": [g["name"] or "?" for g in guides]}


def main(argv=None):
    """add (the string on stdin) | status | sync | guide <id> <index> | facts <id> <index> [CLASS|-] [RACE|-] | icons
    (FileDataIDs on stdin, {"fdids": [...]}), for the desktop app: one JSON object on stdout."""
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else ""
    if cmd == "routes" and len(argv) >= 3:      # walking routes between the steps (rxp_routes.py; JSON on stdin)
        return __import__("rxp_routes").main(argv)
    import glideconfig
    wow_dir = glideconfig.wow_dir(ROOT)
    try:
        if cmd == "add":
            out = add(ROOT, wow_dir, sys.stdin.read())
        elif cmd == "status":
            out = {"ok": True, "imports": status(ROOT, wow_dir)}
        elif cmd == "sync":
            out = {"ok": True, "handoff": bool(sync(ROOT, wow_dir))}
        elif cmd == "guide" and len(argv) >= 3:
            out = {"ok": True, "guide": guide_detail(ROOT, argv[1], int(argv[2]), wow_dir)}
        elif cmd == "facts" and len(argv) >= 3:
            import rxp_facts
            more = list(argv[3:5]) + [None] * (2 - len(argv[3:5]))
            out = {"ok": True, "facts": rxp_facts.facts(ROOT, argv[1], int(argv[2]), more[0], more[1], wow_dir)}
        elif cmd == "icons":                # the Guides page's game icons, decoded from the client into the cache
            import icons
            want = (json.loads(sys.stdin.read() or "{}") or {}).get("fdids") or []
            got = icons.ensure(want)
            out = {"ok": True, "ready": sorted(f for f, p in got.items() if p),
                   "failed": sorted(f for f, p in got.items() if not p)}
        else:
            out = {"ok": False, "error": "usage: rxp_import.py add|status|sync|guide|facts|icons"}
    except ImportProblem as e:
        out = {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 - the app shows the reason, whatever it is
        out = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    sys.stdout.write(json.dumps(out) + "\n")
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
