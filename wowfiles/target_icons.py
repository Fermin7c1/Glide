"""Icons for the Guides page's targets, from the player's own client: which image stands for a creature, an item, a
spell or an NPC's role (tools/extract_maps.py, stage "icons", -> data/gamedata/target_icons.json and
data/db2/Creature.csv; read back by IconBook for the guide facts, knowledge/api/explorer.py).

The client has no portrait images of creatures (the game draws portraits live from 3D models), so a creature is shown
by what the client does have:
  beasts       the hunter-pet family icon (CreatureFamily.IconFileID: the boar for a Mottled Boar)
  other types  one fitting icon per creature type (TYPE_ICONS: candidate paths, the first the client has wins)
  items        the item's own icon (Item.IconFileDataID)
  roles        the minimap tracking icons and the quest-giver marks (NAMED: vendor, trainer, flight master, ...)
A creature's type and family come from Creature.db2 (the knowledge build fills kb creature.type / family from the
CSV). Every table is read with the .dbd that knows its layout and then checked before it is trusted: creatures
against CREATURE_ANCHORS (starting-zone creatures whose type and family never change), items and families by their
icons being files the client really has. A table that fails its check is left out (the page keeps its glyphs) and the
refresh says why; nothing is guessed. Nothing here is shipped: the images only ever come from the player's install.
"""
import csv
import json
import os

from .db2 import DB2Error, read_db2
from .dbdefs import dbd_text

# the tables, by path and by FileDataID (the root of a classic client keeps few file names; the ids are the community
# listfile's, wowdev/wow-listfile)
TABLES = {"Creature": ("DBFilesClient/Creature.db2", 841631), "CreatureFamily": ("DBFilesClient/CreatureFamily.db2", 1351351),
          "Item": ("DBFilesClient/Item.db2", 841626)}

CREATURE_TYPES = {1: "Beast", 2: "Dragonkin", 3: "Demon", 4: "Elemental", 5: "Giant", 6: "Undead", 7: "Humanoid",
                  8: "Critter", 9: "Mechanical", 10: "Not specified", 11: "Totem", 12: "Non-combat Pet",
                  13: "Gas Cloud"}
# CreatureFamily ids of classic (hunter and warlock pets) -> English names for kb creature.family; an id not listed is
# kept as its number, so the icon still resolves
FAMILIES = {1: "Wolf", 2: "Cat", 3: "Spider", 4: "Bear", 5: "Boar", 6: "Crocolisk", 7: "Carrion Bird", 8: "Crab",
            9: "Gorilla", 11: "Raptor", 12: "Tallstrider", 15: "Felhunter", 16: "Voidwalker", 17: "Succubus",
            19: "Doomguard", 20: "Scorpid", 21: "Turtle", 23: "Imp", 24: "Bat", 25: "Hyena", 26: "Owl",
            27: "Wind Serpent", 28: "Remote Control"}
# creature id -> (CreatureType, CreatureFamily): starting-zone creatures (Mottled Boar, Stonetusk Boar, Young Wolf,
# Timber Wolf, Forest Spider, Young Thistle Boar, Adult Plainstrider, Durotar Tiger, Kobold Vermin, Defias Thug, Vile
# Familiar, Mindless Zombie). They find the columns when the .dbd doesn't know the layout, and check the read either way
CREATURE_ANCHORS = {3098: (1, 5), 113: (1, 5), 299: (1, 1), 69: (1, 1), 30: (1, 3), 1984: (1, 5), 2956: (1, 12),
                    3121: (1, 2), 6: (7, 0), 38: (7, 0), 3101: (3, 0), 1501: (6, 0)}
ANCHORS_PRESENT = 6          # at least this many anchors in the table...
ANCHORS_AGREE = 0.9          # ...and this share of them right
ICONS_REAL = 0.9             # share of a table's icon ids that must be files in the client

ICON_DIR = "interface/icons/"
# one icon per creature type that has no family icon: (path, FileDataID) candidates, the first the client has wins
TYPE_ICONS = {
    "Beast": [("Interface/Icons/Ability_Hunter_BeastTaming", 132164), ("Interface/Icons/Ability_Hunter_Pet_Wolf", 132203)],
    "Humanoid": [("Interface/Icons/INV_Misc_Head_Human_01", 134166),
                 ("Interface/Icons/Spell_Holy_PrayerOfHealing02", 135943)],
    "Undead": [("Interface/Icons/INV_Misc_Bone_HumanSkull_01", 133730), ("Interface/Icons/Spell_Shadow_RaiseDead", 136187)],
    "Demon": [("Interface/Icons/Spell_Shadow_Metamorphosis", 136172), ("Interface/Icons/Spell_Shadow_SummonImp", 136218)],
    "Elemental": [("Interface/Icons/Spell_Nature_EarthElemental_Totem", 136024), ("Interface/Icons/Spell_Fire_Fire", 135805)],
    "Dragonkin": [("Interface/Icons/INV_Misc_Head_Dragon_01", 134153),
                  ("Interface/Icons/INV_Misc_Head_Dragon_Black", 134154)],
    "Giant": [("Interface/Icons/Spell_Nature_Earthquake", 136025), ("Interface/Icons/Ability_Warrior_Rampage", 132352)],
    "Mechanical": [("Interface/Icons/INV_Misc_Gear_01", 134063), ("Interface/Icons/Trade_Engineering", 136243)],
    "Critter": [("Interface/Icons/Spell_Magic_PolymorphChicken", 135996), ("Interface/Icons/INV_Misc_Food_14", 133970)],
}
# what an NPC does for the player, and the quest marks: the gossip window's and the minimap tracking icons
NAMED = {
    "vendor": [("Interface/GossipFrame/VendorGossipIcon", 132060), ("Interface/Minimap/Tracking/Food", 136457)],
    "repair": [("Interface/Minimap/Tracking/Repair", 136465), ("Interface/Icons/Trade_BlackSmithing", 136241)],
    "trainer": [("Interface/Minimap/Tracking/Class", 136455), ("Interface/GossipFrame/TrainerGossipIcon", 132058)],
    "profession": [("Interface/Minimap/Tracking/Profession", 136463),
                   ("Interface/GossipFrame/TrainerGossipIcon", 132058)],
    "flight": [("Interface/Minimap/Tracking/FlightMaster", 136456), ("Interface/GossipFrame/TaxiGossipIcon", 132057)],
    "innkeeper": [("Interface/Minimap/Tracking/Innkeeper", 136458), ("Interface/GossipFrame/BinderGossipIcon", 132052)],
    "banker": [("Interface/Minimap/Tracking/Banker", 136453), ("Interface/GossipFrame/BankerGossipIcon", 132050)],
    "stable": [("Interface/Minimap/Tracking/StableMaster", 136466), ("Interface/Icons/Ability_Hunter_BeastCall", 132161)],
    "mailbox": [("Interface/Minimap/Tracking/Mailbox", 136459)],
    "auctioneer": [("Interface/Minimap/Tracking/Auctioneer", 136452)],
    "quest_available": [("Interface/GossipFrame/AvailableQuestIcon", 132049)],
    "quest_turnin": [("Interface/GossipFrame/ActiveQuestIcon", 132048)],
    "elite": [("Interface/Tooltips/EliteNameplateIcon", 237678)],
    "rare": [("Interface/Tooltips/RareEliteNameplateIcon", 1080968)],
}
# a few item icons that never change (Hearthstone: INV_Misc_Rune_01): they check the Item table's icon column
ITEM_ANCHORS = {6948: 134414}
FAMILY_ANCHORS = {5: 132184, 1: 132203}       # Boar: Ability_Hunter_Pet_Boar, Wolf: Ability_Hunter_Pet_Wolf


class IconsError(Exception):
    pass


def type_name(tid):
    return CREATURE_TYPES.get(tid) if tid else None


def family_name(fid):
    """kb creature.family for a CreatureFamily id: its English name, else the id as text (0 / None: no family)."""
    return (FAMILIES.get(fid) or str(fid)) if fid else None


def family_id(name):
    """The CreatureFamily id behind kb creature.family (family_name's inverse), or None."""
    if not name:
        return None
    for k, v in FAMILIES.items():
        if v.lower() == str(name).lower():
            return k
    return int(name) if str(name).isdigit() else None


def _ints(rows):
    return [k for k, v in rows[0].items() if isinstance(v, int)] if rows else []


def _read(data, name):
    """(rows named by the .dbd, "dbd") or (unnamed rows, "unnamed") when the .dbd doesn't know the layout."""
    try:
        return read_db2(data, dbd_text(name, data)), "dbd"
    except DB2Error:
        return read_db2(data), "unnamed"


def creatures(data):
    """Creature.db2 bytes -> ({id: (type, family, classification or None)}, how, "hits/present"). Raises IconsError when
    the read doesn't agree with CREATURE_ANCHORS."""
    rows, how = _read(data, "Creature")
    cols = ("CreatureType", "CreatureFamily", "Classification") if how == "dbd" else None
    if cols is None or not rows or any(c not in rows[0] for c in cols):
        how, cols = "anchors", _anchor_columns(rows)
    out = {}
    for r in rows:
        cid = r.get("ID")
        if isinstance(cid, int) and cid > 0:
            out[cid] = tuple(r.get(c) if c else None for c in cols)
    present = [c for c in CREATURE_ANCHORS if c in out]
    hits = sum(out[c][:2] == CREATURE_ANCHORS[c] for c in present)
    if len(present) < ANCHORS_PRESENT or hits < ANCHORS_AGREE * len(present):
        raise IconsError("Creature: %d of %d known creatures check out (%d in the table)"
                         % (hits, len(CREATURE_ANCHORS), len(present)))
    return out, how, "%d/%d" % (hits, len(present))


def _anchor_columns(rows):
    """(type column, family column, None) of an unnamed Creature read, voted by the anchors."""
    by_id = {r.get("ID"): r for r in rows if r.get("ID") in CREATURE_ANCHORS}
    ints = [k for k in _ints(rows) if k != "ID"]

    def best(pos, only_beasts):
        votes = {k: sum(r.get(k) == CREATURE_ANCHORS[cid][pos] for cid, r in by_id.items()
                        if not only_beasts or CREATURE_ANCHORS[cid][1]) for k in ints}
        return max(votes, key=lambda k: votes[k]) if votes and max(votes.values()) else None
    t = best(0, False)
    f = best(1, True)
    if not t or not f or t == f:
        raise IconsError("Creature: no columns match the known creatures")
    return (t, f, None)


def _icon_column(rows, exists, preferred, what):
    """The column holding icon file ids: the .dbd's when its values are real files, else the int column whose
    non-zero values are the most often real files. -> (column, share real). Raises IconsError."""
    def share(col):
        vals = [r.get(col) for r in rows if isinstance(r.get(col), int) and r.get(col) > 0]
        if len(vals) < max(3, len(rows) // 4):
            return 0.0
        return sum(1 for v in vals if exists(v)) / len(vals)
    if preferred and rows and preferred in rows[0]:
        s = share(preferred)
        if s >= ICONS_REAL:
            return preferred, s
    cands = [(share(k), k) for k in _ints(rows) if k != "ID"]
    s, k = max(cands) if cands else (0.0, None)
    if not k or s < ICONS_REAL:
        raise IconsError("%s: no column of icons the client has (best %.0f%%)" % (what, 100 * s))
    return k, s


def icon_map(data, name, preferred, exists, anchors):
    """A table's {id: icon file id} (Item: IconFileDataID, CreatureFamily: IconFileID), checked against the client's
    files and the anchors (an anchor in the table must have its known icon). -> (map, how, share real)."""
    rows, how = _read(data, name)
    col, s = _icon_column(rows, exists, preferred if how == "dbd" else None, name)
    out = {r["ID"]: r[col] for r in rows if isinstance(r.get("ID"), int) and isinstance(r.get(col), int)
           and r[col] > 0 and exists(r[col])}
    wrong = [k for k, v in anchors.items() if k in out and out[k] != v]
    if wrong:
        raise IconsError("%s: the icon of %s isn't the known one" % (name, ", ".join(map(str, wrong))))
    return out, how if col == preferred else "values", round(s, 3)


def resolve(cands, fdid_of, exists):
    """The first of these (path, FileDataID) the client has -> (file id, path), else (None, None). The path's own id
    (the root's name table) wins over the listfile's."""
    for p, known in cands:
        for fid in (fdid_of(p + ".blp"), known):
            if fid and exists(fid):
                return fid, p
    return None, None


def build(client, log=print, keep=None):
    """Everything the stage writes, read from an open client (casc.Casc or a stand-in with read_fdid, fdid_of, has):
    -> (target_icons dict, {creature id: (type, family, classification)} or {}). Each table that can't be read or
    doesn't check out is left out and named in "problems". keep (a dict) gets each table's bytes."""
    exists = client.has
    out = {"version": getattr(client, "version", None), "items": {}, "families": {}, "types": {}, "named": {},
           "raw": [], "how": {}, "problems": []}
    tables = {}
    for name, (path, known) in TABLES.items():
        fid = client.fdid_of(path) or known
        try:
            tables[name] = client.read_fdid(fid)
            if keep is not None:
                keep[name] = tables[name]
        except Exception as e:  # noqa: BLE001 - missing or encrypted table: that part is left out
            out["problems"].append("%s: %s" % (name, e))
    cre = {}
    if "Creature" in tables:
        try:
            cre, how, hit = creatures(tables["Creature"])
            out["how"]["creatures"] = {"how": how, "anchors": hit, "rows": len(cre)}
        except (DB2Error, IconsError) as e:
            out["problems"].append(str(e))
    for name, col, key, anchors in (("Item", "IconFileDataID", "items", ITEM_ANCHORS),
                                    ("CreatureFamily", "IconFileID", "families", FAMILY_ANCHORS)):
        if name not in tables:
            continue
        try:
            m, how, s = icon_map(tables[name], name, col, exists, anchors)
            out[key] = {str(k): v for k, v in m.items()}
            out["how"][key] = {"how": how, "real": s, "rows": len(m)}
        except (DB2Error, IconsError) as e:
            out["problems"].append(str(e))
    for tname, cands in TYPE_ICONS.items():
        fid, _p = resolve(cands, client.fdid_of, exists)
        if fid:
            out["types"][tname] = fid
    for key, cands in NAMED.items():
        fid, p = resolve(cands, client.fdid_of, exists)
        if fid:
            out["named"][key] = fid
            if not p.lower().startswith(ICON_DIR):
                out["raw"].append(fid)          # a UI texture, shown whole (the icons lose their baked-in border)
    out["raw"] = sorted(set(out["raw"]))
    for p in out["problems"]:
        log("icons: %s" % p)
    return out, cre


def fixed_icons(book):
    """The icons every guide may show (families, creature types, roles): pre-decoded by the refresh."""
    return sorted(set(book.get("families", {}).values()) | set(book.get("types", {}).values())
                  | set(book.get("named", {}).values()))


TRIM = 4                    # px of a 64 px icon's baked-in border; the game shows the inner 56 px
MAX_SIDE = 256              # an "icon" bigger than this is no icon: not decoded


def write_icon_png(client, fid, path, trim=True):
    """Decode file `fid` (a BLP) from the client into the PNG at `path` (written atomically); trim: cut the icon's
    border, as the game's own frames do (Interface/Icons only; a UI texture is shown whole). Raises on a file that
    isn't a small BLP texture."""
    from .blp import decode_blp, write_png      # numpy, loaded on the first icon
    data = client.read_fdid(fid)
    if data[:4] != b"BLP2":
        raise IconsError("file %s is not a texture" % fid)
    rgba = decode_blp(data)
    if max(rgba.shape[:2]) > MAX_SIDE:
        raise IconsError("file %s is %dx%d, not an icon" % (fid, rgba.shape[1], rgba.shape[0]))
    if trim and rgba.shape[0] > 2 * TRIM and rgba.shape[1] > 2 * TRIM:
        rgba = rgba[TRIM:-TRIM, TRIM:-TRIM]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())      # the engine and a Guides helper may decode the same icon at once
    write_png(tmp, rgba)
    try:
        os.replace(tmp, path)
    except OSError:
        os.remove(tmp)
        if not os.path.exists(path):        # Windows: another process wrote it first and is still reading it
            raise


def write_creature_csv(path, cre):
    """data/db2/Creature.csv for the knowledge build (wowfiles/db2_import: kb creature.type / family / rank)."""
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ID", "CreatureType", "CreatureFamily", "Classification"])
        for cid in sorted(cre):
            t, f, c = cre[cid]
            w.writerow([cid, t if t is not None else "", f if f is not None else "", c if c is not None else ""])
    os.replace(tmp, path)


class IconBook:
    """The icon file ids the guide facts hand to the page (data/gamedata/target_icons.json and spell_icons.json, when
    they are from the client build in manifest.json). Every lookup answers a file id or None."""

    def __init__(self, book=None, spells=None):
        self.book = book or {}
        self.spells = spells or {}

    @classmethod
    def load(cls, gamedata):
        def read(name):
            try:
                with open(os.path.join(gamedata, name), encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, ValueError):
                return {}
        version = read("manifest.json").get("version")
        book, spells = read("target_icons.json"), read("spell_icons.json")
        return cls(book if book.get("version") == version else {},
                   spells.get("icons") if spells.get("version") == version else {})

    def __bool__(self):
        return bool(self.book or self.spells)

    def item(self, iid):
        return self.book.get("items", {}).get(str(iid))

    def spell(self, sid):
        return self.spells.get(str(sid))

    def named(self, key):
        return self.book.get("named", {}).get(key)

    def creature(self, ctype, family):
        """A creature's icon from kb creature.type and .family: the family's, else the type's."""
        fid = family_id(family)
        return (self.book.get("families", {}).get(str(fid)) if fid else None) \
            or self.book.get("types", {}).get(ctype or "")
