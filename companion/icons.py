"""Spell icons for the dashboard's buffs, read from the local game client (read-only) on first use.

    icon_png(spell_id) -> PNG bytes, or None (no client, unknown spell, encrypted file)

The spell -> icon FileDataID map is data/gamedata/spell_icons.json, written by the game-file refresh
(tools/extract_maps.py, stage "spells", with wowfiles/spells.py) like the map data: re-read whenever the client build changes, and readable
even when a patch changed the table's layout. Until the refresh has run it is read here, the same way. Each icon is
decoded once from its BLP and cached as data/gamedata/icons/<fileDataID>.png (a new build clears them), its baked-in
border trimmed the way the game's own buff frames do. Nothing is shipped with Glide: the images only ever come from
the player's own install.
"""
import json
import os
import threading
import time

import health
import jsonfile
from log import log

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "gamedata")
ICON_DIR = os.path.join(OUT, "icons")
MAP_PATH = os.path.join(OUT, "spell_icons.json")
TRIM = 4                               # px of the 64 px icon's border; the game shows the inner 56 px
IDLE_CLOSE_S = 60                      # the client index stays open this long after the last icon read

_lock = threading.Lock()
_casc, _casc_t, _map, _failed = None, 0.0, None, set()
status = {"state": "unread"}           # for the Status page: "ok" (how, count), "error" (why), "unread"


def _client():
    global _casc, _casc_t
    if _casc is None:
        from wowfiles import casc       # the game-file readers (ROOT is on the path: app.py)
        wow, product = casc.install_of(health.wow_dir(ROOT))
        casc.load_default_keys()
        _casc = casc.Casc(wow, product)
        threading.Thread(target=_closer, daemon=True).start()
    _casc_t = time.time()
    return _casc


def with_client(fn):
    """fn(the open client index) under the icons' lock: sounds.py reads the same index."""
    with _lock:
        return fn(_client())


def _closer():
    global _casc
    while True:
        time.sleep(10)
        with _lock:
            if _casc is not None and time.time() - _casc_t > IDLE_CLOSE_S:
                _casc = None
                return


def _spell_map():
    global _map
    if _map is not None:
        return _map
    try:
        m = json.load(open(MAP_PATH, encoding="utf-8"))
        manifest = json.load(open(os.path.join(OUT, "manifest.json"), encoding="utf-8"))
        if m.get("version") == manifest.get("version"):
            _map = m["icons"]
            status.update(state="ok", how=m.get("how", "dbd"), spells=len(_map), version=m.get("version"))
            return _map
    except (OSError, ValueError, KeyError):
        pass
    from wowfiles.spells import SPELLMISC_FDID, spell_icons
    c = _client()
    icons, how, _hit = spell_icons(c.read_fdid(SPELLMISC_FDID))
    jsonfile.write_atomic(MAP_PATH, {"version": c.version, "how": how, "icons": icons}, separators=(",", ":"))
    _map = icons
    status.update(state="ok", how=how, spells=len(icons), version=c.version)
    return _map


def icon_png(spell_id):
    """PNG bytes of a spell's icon, or None."""
    key = str(spell_id)
    if not key.isdigit() or "map" in _failed:
        return None
    with _lock:
        try:
            fd = _spell_map().get(key)
        except Exception as e:  # noqa: BLE001 - no client, unreadable table: no icons, the dashboard falls back
            log("icons", "spell table unavailable: %s" % e)
            status.update(state="error", why=str(e))
            _failed.add("map")
            return None
        if not fd or fd in _failed:
            return None
        p = os.path.join(ICON_DIR, "%d.png" % fd)
        if not os.path.exists(p):
            try:
                from wowfiles.blp import decode_blp, write_png     # numpy, loaded on the first icon
                rgba = decode_blp(_client().read_fdid(fd))
                if rgba.shape[0] > 2 * TRIM and rgba.shape[1] > 2 * TRIM:
                    rgba = rgba[TRIM:-TRIM, TRIM:-TRIM]
                os.makedirs(ICON_DIR, exist_ok=True)
                write_png(p + ".tmp", rgba)
                os.replace(p + ".tmp", p)
            except Exception as e:  # noqa: BLE001 - encrypted or missing file: remember, fall back
                log("icons", "icon %s for spell %s: %s" % (fd, key, e))
                _failed.add(fd)
                return None
        with open(p, "rb") as fh:
            return fh.read()
