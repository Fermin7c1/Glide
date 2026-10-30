"""Which .dbd (table layout definition) reads a client table: the bundled tools/dbd, else one fetched from WoWDBDefs.

A game patch can change a table's layout before tools/dbd knows it. Then the current definition is fetched from
WoWDBDefs (where tools/dbd comes from) and kept in data/gamedata/dbd, so the map data and the spell list keep being
read until the next Glide update brings the new layout along.
"""
import os

from .db2 import has_layout, layout_hash

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLED = os.path.join(ROOT, "tools", "dbd")                  # shipped with Glide (tests point these elsewhere)
CACHE = os.path.join(ROOT, "data", "gamedata", "dbd")         # fetched after a patch
DBD_URL = "https://raw.githubusercontent.com/wowdev/WoWDBDefs/master/definitions/%s.dbd"


def _fetch(url):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Glide-to-60"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8")


FETCH = _fetch          # tests swap this out


def log(*a):
    print("[maps]", *a, flush=True)


def dbd_text(name, data):
    """The .dbd text for table `name` that knows the layout of `data`: the bundled tools/dbd, else a copy fetched
    earlier (data/gamedata/dbd), else fetched now from WoWDBDefs and kept. If none knows it, the bundled one (reading
    then fails with the missing layout hash, which the refresh reports)."""
    h = layout_hash(data)
    cache = os.path.join(CACHE, name + ".dbd")
    bundled = open(os.path.join(BUNDLED, name + ".dbd"), encoding="utf-8").read()
    if has_layout(bundled, h):
        return bundled
    try:
        kept = open(cache, encoding="utf-8").read()
        if has_layout(kept, h):
            return kept
    except OSError:
        pass
    try:
        fresh = FETCH(DBD_URL % name)
    except Exception as e:  # noqa: BLE001 - offline, GitHub down: report and fall back
        log("%s: layout %08X is new and WoWDBDefs couldn't be reached (%s)" % (name, h, e))
        return bundled
    if not has_layout(fresh, h):
        log("%s: layout %08X is new and WoWDBDefs doesn't know it yet" % (name, h))
        return bundled
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache, "w", encoding="utf-8") as fh:
        fh.write(fresh)
    log("%s: layout %08X is new; fetched its definition from WoWDBDefs" % (name, h))
    return fresh
