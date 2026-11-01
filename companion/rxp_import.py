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
     in-game string decryptor.
The addon notes each hand-off in GlideRecorderDB.rxpImports[id]; once one is done its guides leave the addon's file.
An addon install replaces the addon's folder (and that file with it), so the app syncs again after installing.

Run by the desktop app (`python companion/rxp_import.py add|status|sync`, the string on stdin for add), answering
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
    }


def _entity_names(root, qids, npcs, items):
    """Best-effort {quests: {id: name}, npcs: {id: name}, items: {id: name}} from data/glide.db when present."""
    out = {"quests": {}, "npcs": {}, "items": {}}
    db_path = os.path.join(root, "data", "glide.db")
    if not os.path.exists(db_path) or not (qids or npcs or items):
        return out
    import sqlite3
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)
    except sqlite3.Error:
        return out
    try:
        if qids:
            ph = ",".join("?" * len(qids))
            for qid, name in conn.execute("SELECT id, name FROM quests WHERE id IN (%s) AND name IS NOT NULL" % ph,
                                          tuple(qids)):
                out["quests"][int(qid)] = name
        for kind, ids, bucket in (("npc", npcs, "npcs"), ("item", items, "items")):
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


def guide_detail(root, iid, index):
    """Full parsed guide (steps, waypoints, loops, lines, actions, commentary) for the interactive guide browser."""
    rec = load_store(root)["imports"].get(str(iid))
    guides = (rec.get("guides") if isinstance(rec, dict) else None) or []
    idx = int(index)
    if idx < 0 or idx >= len(guides) or not isinstance(guides[idx], dict):
        raise ImportProblem("Guide not found in that import.")
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from knowledge.rxp_parser.guide import parse_guide
    from knowledge.rxp_parser.maps import DEFAULT_MAPS
    parsed = parse_guide(guides[idx].get("text") or "")
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
    steps_out = []
    zones_seen = {}
    for st in parsed.get("steps") or ():
        actions_out = []
        wps = []
        loops = []
        for a in st.get("actions") or ():
            verb = a.get("verb")
            if not verb:
                continue
            args = a.get("args") or {}
            um = args.get("ui_map")
            zname = DEFAULT_MAPS.name(um) if um else (args.get("zone") or st.get("zone"))
            if um and zname:
                zones_seen[int(um)] = zname
            if verb in ("goto", "groundgoto", "flygoto", "waypoint", "pin", "questgoto", "treasure", "rare"):
                if args.get("x") is not None and args.get("y") is not None:
                    wps.append({"x": args["x"], "y": args["y"], "zone": zname, "ui_map": um,
                                "radius": args.get("radius"), "mode": args.get("mode") or verb,
                                "text": a.get("text") or args.get("tooltip")})
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
        if st.get("ui_map") and st.get("zone"):
            zones_seen[int(st["ui_map"])] = st["zone"]
        flags = {k: v for k, v in (st.get("flags") or {}).items() if k != "cond_flags"}
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
        })
    return {
        "id": str(iid),
        "index": idx,
        "name": parsed.get("name") or guides[idx].get("name") or "?",
        "displayname": parsed.get("displayname"),
        "group": parsed.get("grp") or guides[idx].get("group") or "RestedXP",
        "subgroup": parsed.get("subgroup"),
        "next": parsed.get("next"),
        "next_list": [{"name": n.get("name"), "group": n.get("group"), "cond": (n.get("cond") or {}).get("raw")}
                      for n in (parsed.get("next_list") or ())],
        "level_min": parsed.get("level_min"),
        "level_max": parsed.get("level_max"),
        "faction": parsed.get("faction"),
        "default_for": parsed.get("default_for"),
        "version": parsed.get("version"),
        "cond": (parsed.get("cond") or {}).get("raw"),
        "comments": parsed.get("comments"),
        "zones": [{"ui_map": k, "name": v} for k, v in zones_seen.items()],
        "steps": steps_out,
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


def handed_over(wow_dir):
    """{id: {"t", "ok", "err", "account"}}: what the Glide addon noted in GlideRecorderDB.rxpImports (WoW writes it
    at logout and /reload). Per id, an account where RestedXP took it wins over one where it failed (a string is for
    one account, so another account of this PC fails on it), then the newest note."""
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
            v = dict(v, account=os.path.basename(os.path.dirname(os.path.dirname(p))))
            cur = out.get(str(k))
            if cur is None or (bool(v.get("ok")), v.get("t") or 0) > (bool(cur.get("ok")), cur.get("t") or 0):
                out[str(k)] = v
    return out


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


def handoff_lua(pending):
    """The addon's Data/RXPImports.lua for these imports ([(id, record)]): the decrypted guide texts."""
    lines = ["-- Written by the Glide app (companion/rxp_import.py): the RestedXP guides you added in the app,",
             "-- already decrypted for RestedXP at your next login (RXPImport.lua). The app rewrites this file.",
             "local _, ns = ...", "ns.rxpImports = {"]
    for iid, rec in pending:
        texts = [g.get("text") for g in (rec.get("guides") or ())
                 if isinstance(g, dict) and isinstance(g.get("text"), str) and g.get("text")]
        if not texts:
            continue
        body = ",\n        ".join(_lua_string(t) for t in texts)
        lines.append("    { id = %s, n = %d, guides = {\n        %s,\n    } },"
                     % (_lua_string(iid), len(texts), body))
    lines.append("}")
    return "\n".join(lines) + "\n"


def sync(root, wow_dir):
    """Write the addon's Data/RXPImports.lua with the strings it hasn't handed to RestedXP yet. Returns the path
    written, or None (no WoW folder, or no Glide addon there)."""
    if not wow_dir:
        return None
    path = os.path.join(wow_dir, HANDOFF)
    if not os.path.isdir(os.path.dirname(os.path.dirname(path))):
        return None
    done = handed_over(wow_dir)
    pending = [(k, v) for k, v in sorted(load_store(root)["imports"].items(), key=lambda kv: kv[1].get("added") or 0)
               if not (done.get(k) or {}).get("ok")]
    text = handoff_lua(pending)
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
    "found", "error"}], newest first: each added string and whether RestedXP has it yet."""
    done = handed_over(wow_dir)
    out = []
    for iid, rec in load_store(root)["imports"].items():
        note = done.get(iid) or {}
        state = "done" if note.get("ok") else "failed" if note.get("err") else "waiting"
        raw_guides = rec.get("guides") or ()
        out.append({"id": iid, "added": rec.get("added"), "account": rec.get("account"),
                    "guides": [g.get("name") or "?" for g in raw_guides],
                    "items": [guide_summary(i, g) for i, g in enumerate(raw_guides)],
                    "rxp": state, "found": note.get("ok"), "error": note.get("err")})
    out.sort(key=lambda r: r.get("added") or 0, reverse=True)
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
    """add (the string on stdin) | status | sync, for the desktop app: one JSON object on stdout."""
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else ""
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
            out = {"ok": True, "guide": guide_detail(ROOT, argv[1], int(argv[2]))}
        else:
            out = {"ok": False, "error": "usage: rxp_import.py add|status|sync|guide"}
    except ImportProblem as e:
        out = {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 - the app shows the reason, whatever it is
        out = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    sys.stdout.write(json.dumps(out) + "\n")
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
