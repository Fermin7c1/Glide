"""Which icon each spell shows, from the client's SpellMisc table: for the dashboard's buff icons (companion/icons.py)
and the game-file refresh's "spells" stage (tools/extract_maps.py, -> data/gamedata/spell_icons.json)."""
from .db2 import DB2Error, read_db2
from .dbdefs import dbd_text

SPELLMISC_FDID = 1003144
# long-standing buffs and their icons (Battle Shout, Power Word: Fortitude, Mark of the Wild, Arcane Intellect,
# Blessing of Might, Thorns, Renew, Devotion Aura): they find the spell and icon columns when the .dbd doesn't know
# the table's layout, and check the result either way
SPELL_ICON_ANCHORS = {6673: 132333, 1243: 135987, 1126: 136078, 1459: 135932, 19740: 135906, 467: 136104,
                      139: 135953, 465: 135893}
ANCHORS_NEEDED = 5


def spell_icons(data):
    """SpellMisc bytes -> ({"spell id": icon FileDataID}, "dbd" | "anchors", anchors matched). Raises DB2Error when
    neither the .dbd nor the anchors make sense of the table."""

    def collect(rows, key, icon, diff):
        out = {}
        for r in rows:                  # one row per spell and difficulty; the base row (difficulty 0) wins
            sid, fd = r.get(key), r.get(icon)
            if isinstance(sid, int) and sid > 0 and isinstance(fd, int) and fd > 0 \
                    and (str(sid) not in out or (diff and not r.get(diff))):
                out[str(sid)] = fd
        return out, sum(out.get(str(k)) == v for k, v in SPELL_ICON_ANCHORS.items())

    try:
        icons, hit = collect(read_db2(data, dbd_text("SpellMisc", data)),
                             "SpellID", "SpellIconFileDataID", "DifficultyID")
        if hit >= ANCHORS_NEEDED:
            return icons, "dbd", hit
    except DB2Error:
        pass
    rows = read_db2(data)               # unnamed: f0, f1, ..., ID, _relation
    scalar = [k for k, v in rows[0].items() if isinstance(v, int)] if rows else []
    votes = {}
    for r in rows:
        for k in scalar:
            want = SPELL_ICON_ANCHORS.get(r.get(k))
            if want is not None:
                for v in scalar:
                    if v != k and r.get(v) == want:
                        votes[(k, v)] = votes.get((k, v), 0) + 1
    if not votes:
        raise DB2Error("SpellMisc: no column pair matches the known buff icons")
    (key, icon), _n = max(votes.items(), key=lambda kv: kv[1])
    icons, hit = collect(rows, key, icon, None)
    if hit < ANCHORS_NEEDED:
        raise DB2Error("SpellMisc: only %d of %d known buff icons found" % (hit, len(SPELL_ICON_ANCHORS)))
    return icons, "anchors", hit
