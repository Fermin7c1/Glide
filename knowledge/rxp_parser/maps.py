"""Zone names <-> uiMapIDs (Classic UiMap ids, the same table RXP keeps in addon.mapId; Forever maps added)."""
from knowledge.rxp_parser.text import WS, lua_int


ZONE_UIMAP = {
    "Durotar": 1411, "Mulgore": 1412, "The Barrens": 1413, "Kalimdor": 1414, "Eastern Kingdoms": 1415,
    "Alterac Mountains": 1416, "Arathi Highlands": 1417, "Badlands": 1418, "Blasted Lands": 1419,
    "Tirisfal Glades": 1420, "Silverpine Forest": 1421, "Western Plaguelands": 1422,
    "Eastern Plaguelands": 1423, "Hillsbrad Foothills": 1424, "The Hinterlands": 1425, "Dun Morogh": 1426,
    "Searing Gorge": 1427, "Burning Steppes": 1428, "Elwynn Forest": 1429, "Deadwind Pass": 1430,
    "Duskwood": 1431, "Loch Modan": 1432, "Redridge Mountains": 1433, "Stranglethorn Vale": 1434,
    "Swamp of Sorrows": 1435, "Westfall": 1436, "Wetlands": 1437, "Teldrassil": 1438, "Darkshore": 1439,
    "Ashenvale": 1440, "Thousand Needles": 1441, "Stonetalon Mountains": 1442, "Desolace": 1443,
    "Feralas": 1444, "Dustwallow Marsh": 1445, "Tanaris": 1446, "Azshara": 1447, "Felwood": 1448,
    "Un'Goro Crater": 1449, "Moonglade": 1450, "Silithus": 1451, "Winterspring": 1452,
    "Stormwind City": 1453, "Orgrimmar": 1454, "Ironforge": 1455, "Thunder Bluff": 1456, "Darnassus": 1457,
    "Undercity": 1458, "Alterac Valley": 1459, "Warsong Gulch": 1460, "Arathi Basin": 1461,
    "Mount Hyjal": 2482, "Zephras Isle": 2521, "Darkspear Islands": 2524, "Riverglades": 2548,
    "Shen'dralas": 2652, "Azeroth": 947,
}
ZONE_ALIASES = {"StormwindClassic": 1453}       # RXP map-name aliases seen in guides


class MapNames:
    def __init__(self, extra=None):
        self.by_name = dict(ZONE_UIMAP)
        self.by_name.update(ZONE_ALIASES)
        self.by_id = {}
        for n, i in ZONE_UIMAP.items():
            self.by_id.setdefault(i, n)
        for n, i in (extra or {}).items():
            if n and isinstance(i, int):
                self.by_name.setdefault(n, i)
                self.by_id.setdefault(i, n)

    def ui_map(self, token):
        if token is None:
            return None
        t = token.strip(WS)
        if t in self.by_name:
            return self.by_name[t]
        return lua_int(t)

    def name(self, ui_map):
        return self.by_id.get(ui_map)


DEFAULT_MAPS = MapNames()


# RestedXP's dungeon tags (DB/forever/db.lua dungeonList) and the other spellings its GetDungeonName accepts
# (alternateNames). `.dungeon X` / `.enabledungeon X` go through GetDungeonName: strupper, then the tag or its
# alias, so 'Mara', 'Stockades' and 'MARA', 'STOCKS' are one dungeon; settings and overrides are keyed by the tag.
DUNGEON_TAGS = frozenset((
    "RFC", "DM", "WC", "SFK", "BFD", "STOCKS", "GNOMER", "RFK", "SM", "RFD", "ULDA", "ZF", "MARA", "ST", "BRD", "DME",
    "SCHOLO", "STRAT", "LBRS", "HOT", "ROL", "ES", "COD", "TDC", "KS", "AP", "BH", "TST"))
DUNGEON_ALIASES = {
    "DEADMINES": "DM", "VC": "DM", "STOCKADES": "STOCKS", "TEMPLE OF ATAL'HAKKAR": "ST", "DMW": "DME", "DMN": "DME",
    "BMH": "BH", "SHT": "TST", "SH": "TDC", "DC": "TDC", "ESW": "ES", "AI": "AP"}


def dungeon_tag(name):
    """RestedXP's dungeon tag for a name as a guide spells it ('Stockades' -> 'STOCKS'), None when RXP knows no
    such dungeon (GetDungeonName returns nothing: RXP reports a parse error and sets no step flag)."""
    if not isinstance(name, str):
        return None
    u = name.strip(WS).upper()
    return u if u in DUNGEON_TAGS else DUNGEON_ALIASES.get(u)
