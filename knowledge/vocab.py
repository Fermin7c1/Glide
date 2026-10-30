"""The game's classes, races and factions, named once: the class and race bitmasks (quest and spell masks in the
client's tables and the knowledge base) both ways, which races and classes go together, and Undead's two names;
and the kinds of the coach's own lines in the engine's logs. Standard library only, like knowledge/values.py.

Undead's game token is "Scourge" (ChrRaces' internal name, UnitRace()'s file token, what masks read back as);
Glide and RestedXP's guides say "Undead". Both names look up bit 16; glide_race() and race_token() turn one into
the other."""

CLASSES = ("WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK", "DRUID")
FACTIONS = ("Alliance", "Horde")
UNDEAD, UNDEAD_TOKEN = "Undead", "Scourge"
# what the coach said to the player, as the engine's feed messages name it ({"kind": ...}): the line a feedback mark
# is stored with (sources/flags.py) and the coach lines an eval replays (eval/run_eval.py). The engine's own copy is
# companion/msg_kinds.COACH_SAID (the knowledge layer doesn't import the engine); tests/test_helpers.py keeps them equal.
COACH_SAID = frozenset(("callout", "answer", "review", "briefing", "reminder"))

# class / race bits: bit (id - 1) of ChrClasses / ChrRaces; a mask with no bits set means "everyone"
CLASS_BITS = {"WARRIOR": 1, "PALADIN": 2, "HUNTER": 4, "ROGUE": 8, "PRIEST": 16, "SHAMAN": 64, "MAGE": 128,
              "WARLOCK": 256, "DRUID": 1024}
BIT_CLASSES = {bit: name for name, bit in CLASS_BITS.items()}
_RACE_TOKEN_BITS = {"Human": 1, "Orc": 2, "Dwarf": 4, "NightElf": 8, UNDEAD_TOKEN: 16, "Tauren": 32, "Gnome": 64,
                    "Troll": 128}
BIT_RACES = {bit: name for name, bit in _RACE_TOKEN_BITS.items()}          # 16 -> "Scourge" (the game token)
RACE_BITS = dict(_RACE_TOKEN_BITS, **{UNDEAD: 16})                          # either Undead name -> 16
FACTION_RACE_MASKS = {"Alliance": 77, "Horde": 178}                         # each faction's Classic races
SKYBORNE_BITS = {"Alliance": 65536, "Horde": 131072}                        # Forever's Skyborne, one bit per side

# Glide's race names per faction (Skyborne joins either), and the classes each race and faction can play
FACTION_RACES = {"Alliance": ("Human", "Dwarf", "NightElf", "Gnome"),
                 "Horde": ("Orc", UNDEAD, "Tauren", "Troll")}
HORDE_RACES = FACTION_RACES["Horde"] + (UNDEAD_TOKEN,)                       # a Horde race under either name
FACTION_CLASSES = {
    "Alliance": ("WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "MAGE", "WARLOCK", "DRUID"),
    "Horde": ("WARRIOR", "HUNTER", "ROGUE", "PRIEST", "MAGE", "WARLOCK", "DRUID", "SHAMAN"),
}
RACE_CLASSES = {
    "Human": {"WARRIOR", "PALADIN", "ROGUE", "PRIEST", "MAGE", "WARLOCK"},
    "Dwarf": {"WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN"},
    "NightElf": {"WARRIOR", "HUNTER", "ROGUE", "PRIEST", "DRUID"},
    "Gnome": {"WARRIOR", "ROGUE", "MAGE", "WARLOCK"},
    "Orc": {"WARRIOR", "HUNTER", "ROGUE", "SHAMAN", "WARLOCK"},
    UNDEAD: {"WARRIOR", "ROGUE", "PRIEST", "MAGE", "WARLOCK"},
    "Tauren": {"WARRIOR", "HUNTER", "SHAMAN", "DRUID"},
    "Troll": {"WARRIOR", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE"},
    "Skyborne": {"WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK", "DRUID"},
}


def glide_race(race):
    """Glide's name for a race: "Scourge" (the game token) -> "Undead"; anything else as it is."""
    return UNDEAD if race == UNDEAD_TOKEN else race


def race_token(race):
    """The game's token for a race: "Undead" -> "Scourge"; anything else as it is."""
    return UNDEAD_TOKEN if race == UNDEAD else race
