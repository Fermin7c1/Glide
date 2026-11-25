"""The "Open in WoWSims" link: the WoWSims Forever site's own share link (its exporter's link.ts) for the setup Glide
simmed, so the player opens the same character there and sees the number Glide said. WoWSims is MIT and asks for a
visible link back to the project: this is it, with its credit (TEXT, CREDIT).

    https://www.wowsims.com/forever/<page>/?i=gtm#<base64(zlib(IndividualSimSettings))>

- The hash is WoWSims' IndividualSimSettings (proto/ui.proto) in protobuf's binary form, zlib-wrapped deflate, then
  standard base64 (ui/sim/state/sim_links.ts encodeLinkHash; `wowsimcli decodelink` reads the same). The encoder is a
  few lines of the standard library, no protobuf dependency: each message is a table of the fields Glide fills
  (SCHEMA), by their protojson names as simbuild.request writes them.
- "i" lists what the page loads from it (ui/sim/constants/sim_settings.ts): g gear, t talents, m misc. The site sets
  every misc field from a link that lists m (race, name, professions, reaction time, distance, the spec's options:
  ui/sim/player/player.ts fromProto), so all of them are filled from the request, and a spec whose options aren't in
  SCHEMA leaves m out (gt). The rotation, consumables, buffs and encounter aren't sent: the page keeps its own, which
  are WoWSims' presets for the spec, as the request has them.
- api_version 2 on the settings and on the player (common.proto ProtoVersion current_version_number): without it a
  link reads as version 0 and the site migrates it as an old one; when the site raises the version, it migrates
  Glide's links the same way.
- A ring's enchant counts on the site only for an enchanter (as in simbuild): a request with an enchanted ring was
  simmed for one, so the link names Enchanting as a profession unless the request names professions itself.
- The page is the class's, by the talent tree with the most points (WoWSims' exporter, Flavors/Forever/Flavor.lua).

Pure: no files, no network.
"""
import base64
import struct
import zlib

from simbuild import SimError

SITE_PREFIX = "https://www.wowsims.com/forever/"            # the exporter's prelink (wowsims.com answers 301 to it)
TEXT = "Open in WoWSims"
CREDIT = "WoWSims (wowsims.com/forever)"
API_VERSION = 2                                              # common.proto ProtoVersion current_version_number
CATEGORIES = "gtm"                                           # gear, talents, misc (WoWSims' SIM_CATEGORY_KEYS)

INT32, DOUBLE, STRING, BOOL = "int32", "double", "string", "bool"
REPEATED = True

# message -> {protojson name: (field number, type: a scalar above, an enum of ENUMS or a message of SCHEMA[, REPEATED])},
# the fields Glide fills, as WoWSims v1.0.4 numbers them (proto/ui.proto, api.proto, common.proto, mage.proto)
SCHEMA = {
    "IndividualSimSettings": {"player": (3, "Player"), "apiVersion": (15, INT32)},
    "Player": {"race": (1, "Race"), "class": (2, "Class"), "equipment": (3, "EquipmentSpec"), "mage": (20, "Mage"),
               "talentsString": (39, STRING), "profession1": (41, "Profession"), "profession2": (42, "Profession"),
               "reactionTimeMs": (45, INT32), "channelClipDelayMs": (46, INT32), "inFrontOfTarget": (47, BOOL),
               "distanceFromTarget": (48, DOUBLE), "name": (51, STRING), "apiVersion": (54, INT32),
               "prof1Rank": (59, INT32), "prof2Rank": (60, INT32), "reactionTimeMaxMs": (62, INT32)},
    "EquipmentSpec": {"items": (1, "ItemSpec", REPEATED)},
    "ItemSpec": {"id": (1, INT32), "randomSuffix": (2, INT32), "enchant": (3, INT32)},
    "Mage": {"options": (1, "Mage.Options")},
    "Mage.Options": {"classOptions": (1, "MageOptions")},
    "MageOptions": {"defaultMageArmor": (1, "MageArmor")},
}
ENUMS = {
    "Race": {"RaceUnknown": 0, "RaceBloodElf": 1, "RaceDwarf": 2, "RaceGnome": 3, "RaceHuman": 4, "RaceNightElf": 5,
             "RaceOrc": 6, "RaceTauren": 7, "RaceTroll": 8, "RaceUndead": 9, "RaceHighOrderSkyborne": 10,
             "RaceWindshaperSkyborne": 11},
    "Class": {"ClassUnknown": 0, "ClassWarrior": 1, "ClassPaladin": 2, "ClassHunter": 3, "ClassRogue": 4,
              "ClassPriest": 5, "ClassShaman": 7, "ClassMage": 8, "ClassWarlock": 9, "ClassDruid": 11},
    "Profession": {"ProfessionUnknown": 0, "Alchemy": 1, "Blacksmithing": 2, "Enchanting": 3, "Engineering": 4,
                   "Herbalism": 5, "Inscription": 6, "Leatherworking": 8, "Mining": 9, "Skinning": 10,
                   "Tailoring": 11},
    "MageArmor": {"MageArmorNone": 0, "MageArmorMageArmor": 1, "MageArmorFrostArmor": 2},
}

# the player's fields each category carries (player.ts toProto; the class and api_version always go); misc also
# carries the spec's options, the Player oneof field named in SPECS
PLAYER_FIELDS = {"g": ("equipment",), "t": ("talentsString",),
                 "m": ("name", "race", "profession1", "profession2", "prof1Rank", "prof2Rank", "reactionTimeMs",
                       "reactionTimeMaxMs", "channelClipDelayMs", "inFrontOfTarget", "distanceFromTarget")}
SPECS = ("mage",)                                            # spec options SCHEMA encodes
RING_SLOTS = (10, 11)                                        # WoWSims' ItemSlotFinger1, ItemSlotFinger2

# WoWSims' class -> its page by talent tree, in the game's tab order (the exporter's simPages; a feral druid opens
# the cat page: talents don't say bear)
PAGES = {
    "ClassWarrior": ("warrior/dps", "warrior/dps", "warrior/protection"),
    "ClassPaladin": ("paladin/holy", "paladin/protection", "paladin/retribution"),
    "ClassHunter": ("hunter/dps",) * 3,
    "ClassRogue": ("rogue/dps",) * 3,
    "ClassPriest": ("priest/healer", "priest/healer", "priest/dps"),
    "ClassShaman": ("shaman/elemental", "shaman/enhancement", "shaman/restoration"),
    "ClassMage": ("mage/dps",) * 3,
    "ClassWarlock": ("warlock/dps",) * 3,
    "ClassDruid": ("druid/balance", "druid/feralcat", "druid/restoration"),
}


# ---- protobuf's binary form -----------------------------------------------------------------------------------------
def _varint(n):
    """A varint (a negative int32 as its 64-bit two's complement: ten bytes, as protobuf writes it)."""
    n = int(n)
    if n < 0:
        n += 1 << 64
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(number, wire):
    return _varint(number << 3 | wire)


def _camel(key):
    """A proto field name in its protojson form (random_suffix -> randomSuffix); a protojson name stays."""
    head, *rest = str(key).split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def _enum(kind, value):
    if isinstance(value, bool):
        raise SimError("not a WoWSims %s: %r" % (kind, value))
    if isinstance(value, int):
        return value
    if value in ENUMS[kind]:
        return ENUMS[kind][value]
    raise SimError("WoWSims has no %s %r" % (kind, value))


def _field(number, kind, value):
    """One field's bytes; b"" for a scalar at its default (proto3 leaves those out). A message always goes, even
    empty (an empty item keeps its slot's place)."""
    if kind in SCHEMA:
        body = encode(kind, value or {})
        return _tag(number, 2) + _varint(len(body)) + body
    if kind in ENUMS:
        n = _enum(kind, value)
        return _tag(number, 0) + _varint(n) if n else b""
    if kind == INT32:
        n = int(value or 0)
        return _tag(number, 0) + _varint(n) if n else b""
    if kind == BOOL:
        return _tag(number, 0) + b"\x01" if value else b""
    if kind == DOUBLE:
        x = float(value or 0)
        return _tag(number, 1) + struct.pack("<d", x) if x else b""
    if kind == STRING:
        raw = str(value or "").encode("utf-8")
        return _tag(number, 2) + _varint(len(raw)) + raw if raw else b""
    raise SimError("no encoding for %s" % kind)


def encode(message, value):
    """A message of SCHEMA in protobuf's binary form, from its protojson dict (keys it doesn't have are left out),
    fields in number order."""
    fields = SCHEMA[message]
    out = []
    for key, v in (value or {}).items():
        spec = fields.get(_camel(key))
        if spec is None or v is None:
            continue
        number, kind = spec[0], spec[1]
        if len(spec) > 2 and spec[2]:
            out.append((number, b"".join(_field(number, kind, x) for x in v or ())))
        else:
            out.append((number, _field(number, kind, v)))
    return b"".join(b for _, b in sorted(out, key=lambda nb: nb[0]))


# ---- the link -------------------------------------------------------------------------------------------------------
def _player(req):
    try:
        player = req["raid"]["parties"][0]["players"][0]
    except (KeyError, IndexError, TypeError):
        raise SimError("the request holds no player") from None
    if not isinstance(player, dict):
        raise SimError("the request holds no player")
    return {_camel(k): v for k, v in player.items()}


def tree(talents_string):
    """The talent tree with the most points in a WoWSims talent string (the first on a tie; 0 without talents)."""
    pts = [sum(int(c) for c in run if c.isdigit()) for run in str(talents_string or "").split("-")]
    return pts.index(max(pts)) if pts and max(pts) > 0 else 0


def page(req):
    """The site's page for the request's player: "mage/dps"."""
    player = _player(req)
    pages = PAGES.get(str(player.get("class")))
    if pages is None:
        raise SimError("WoWSims has no page for %s" % (player.get("class") or "this class"))
    return pages[min(tree(player.get("talentsString")), len(pages) - 1)]


def _enchanted_ring(player):
    items = ((player.get("equipment") or {}).get("items")) or []
    return any(i < len(items) and isinstance(items[i], dict) and items[i].get("enchant") for i in RING_SLOTS)


def settings(char, req):
    """(the IndividualSimSettings protojson the link carries, its categories "gtm" or "gt") for a request
    (simbuild.request's RaidSimRequest) of a character (char: its "name" names the player on the page; the request's
    otherwise)."""
    player = _player(req)
    out = {"apiVersion": API_VERSION, "class": player.get("class")}
    spec = next((s for s in SPECS if player.get(s) is not None), None)
    cats = CATEGORIES if spec else CATEGORIES.replace("m", "")
    for cat in cats:
        for k in PLAYER_FIELDS[cat]:
            if player.get(k) is not None:
                out[k] = player[k]
    if spec is not None:
        out[spec] = player[spec]
        name = (char or {}).get("name")
        if isinstance(name, str) and name.strip():
            out["name"] = name.strip()
        if not (out.get("profession1") or out.get("profession2")) and _enchanted_ring(player):
            out["profession1"] = "Enchanting"
    return {"apiVersion": API_VERSION, "player": out}, cats


def link(char, req):
    """The WoWSims site URL that opens the character's setup as Glide simmed it: req = simbuild.request(char, ...).
    Raises simbuild.SimError for a request with no player, or a class or value WoWSims has no page or name for."""
    body, cats = settings(char, req)
    raw = encode("IndividualSimSettings", body)
    hashed = base64.b64encode(zlib.compress(raw, 9)).decode("ascii")
    return "%s%s/?i=%s#%s" % (SITE_PREFIX, page(req), cats, hashed)
