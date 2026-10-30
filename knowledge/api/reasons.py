"""Plain meaning of the analyzer's reason codes (knowledge/analyzer/README.md): dashboard tooltips, tool results."""
WHY_TEXT = {
    "hub_turnin": "several turn-ins in one place",
    "stacks_objectives": "objectives of 2+ quests in the same place",
    "enables_chain": "unlocks a follow-up the route takes later",
    "on_the_way": "on the way: little detour",
    "level_gate": "RestedXP level gate",
    "xp_gate": "RestedXP grinds to an XP target here",
    "grind_filler": "grinding needed to reach the XP target",
    "hearth_cycle": "hearthstone use or bind",
    "flight_unlock": "discovers a flight path",
    "class_quest": "class quest",
    "training_stop": "trainer visit",
    "vendor_supplies": "vendor / supplies",
    "deathskip_travel": "die on purpose to respawn near the next step",
    "dungeon_run": "dungeon objectives",
    "optional": "optional in RestedXP",
    "last_chance_before_zone_change": "last quest work here before the route leaves the zone",
    "skipped": "does not apply for you right now",
}
WHY_NOT_TEXT = {
    "far_objectives": "objectives far away", "low_drop_rate": "low drop rate", "elite": "elite",
    "group": "needs a group", "long_chain": "long chain", "out_of_level": "wrong level",
    "class_restricted": "class only", "low_xp_per_min": "low XP per minute",
    "routed_later": "the route does it later", "unknown_objective_location": "objective location unknown",
}


def why_text(code):
    """Plain meaning of a why code ('level_gate:7' -> 'RestedXP level gate 7')."""
    base, _, arg = str(code).partition(":")
    t = WHY_TEXT.get(base)
    if t is None:
        return str(code)
    if arg and base in ("level_gate", "xp_gate"):
        return "%s %s" % (t, arg)
    if arg and base == "skipped":
        return "%s (%s)" % (t, arg.replace("_", " "))
    return t
