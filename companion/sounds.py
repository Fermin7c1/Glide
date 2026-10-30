"""Game sounds for the coach screen, read from the local game client (read-only) on first use, like the buff icons.

    sound_ogg(name) -> Ogg bytes, or None (no client, unknown sound, unreadable file)

Only "levelup" so far: the game's level-up fanfare, which the coach screen plays when new community notes land. The
player granted it as an exception to "no Blizzard assets" (2026-10-29), on the icons' terms: it only ever comes from
the player's own install and is never shipped with Glide. The sound kit's file is found in the client's
SoundKitEntry table (the kit's rows, the column holding an Ogg file), else the file 1.15 clients use, and cached as
data/gamedata/sounds/<name>-<fileDataID>.ogg.
"""
import glob
import os
import threading

import icons
from log import log

SOUND_DIR = os.path.join(icons.OUT, "sounds")
SOUNDKITENTRY_FDID = 1237435
KITS = {"levelup": (888, 567431)}       # name -> (sound kit, its file in 1.15 clients)

_lock = threading.Lock()
_failed = set()


def _find(client, kit, fallback):
    """The FileDataID of the kit's Ogg file."""
    from wowfiles.db2 import read_db2
    try:
        for r in read_db2(client.read_fdid(SOUNDKITENTRY_FDID)):
            if r.get("_relation") != kit:
                continue
            for v in r.values():
                if isinstance(v, int) and v > 1000 and v != kit and client.has(v) \
                        and client.read_fdid(v, 4)[:4] == b"OggS":
                    return v
    except Exception as e:  # noqa: BLE001 - a table this reader doesn't know: the known file
        log("sounds", "SoundKitEntry unreadable (%s): using file %d" % (e, fallback))
    return fallback


def sound_ogg(name):
    """Ogg bytes of a named game sound, or None."""
    if name not in KITS or name in _failed:
        return None
    with _lock:
        kept = glob.glob(os.path.join(SOUND_DIR, "%s-*.ogg" % name))
        if not kept:
            try:
                kit, fallback = KITS[name]

                def read(c):
                    fd = _find(c, kit, fallback)
                    return fd, c.read_fdid(fd)
                fd, data = icons.with_client(read)
                if data[:4] != b"OggS":
                    raise ValueError("file %d isn't an Ogg sound" % fd)
                os.makedirs(SOUND_DIR, exist_ok=True)
                path = os.path.join(SOUND_DIR, "%s-%d.ogg" % (name, fd))
                with open(path + ".tmp", "wb") as fh:
                    fh.write(data)
                os.replace(path + ".tmp", path)
                kept = [path]
            except Exception as e:  # noqa: BLE001 - no client, encrypted file: the screen goes without the sound
                log("sounds", "%s: %s" % (name, e))
                _failed.add(name)
                return None
        with open(kept[0], "rb") as fh:
            return fh.read()
