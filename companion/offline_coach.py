"""The coach without a model (no API key, the AI off or suspended): rule-based callouts for the triggers, and the
current step or the reason for it in plain words."""
import re
import sys

import item_text
from gear import PLURAL_SLOTS, dps_text

# Plain meaning of the analyzer's why codes. The knowledge layer's own (knowledge/api/reasons.py) are used whenever
# it is loaded; this copy (the same words, tests/test_helpers.py checks) is for an engine without it.
WHY_TEXT = {
    "hub_turnin": "several turn-ins in one place", "stacks_objectives": "objectives of 2+ quests in the same place",
    "enables_chain": "unlocks a follow-up the route takes later", "on_the_way": "on the way: little detour",
    "level_gate": "RestedXP level gate", "xp_gate": "RestedXP grinds to an XP target here",
    "grind_filler": "grinding needed to reach the XP target", "hearth_cycle": "hearthstone use or bind",
    "flight_unlock": "discovers a flight path", "class_quest": "class quest", "training_stop": "trainer visit",
    "vendor_supplies": "vendor / supplies", "deathskip_travel": "die on purpose to respawn near the next step",
    "dungeon_run": "dungeon objectives", "optional": "optional in RestedXP",
    "last_chance_before_zone_change": "last quest work here before the route leaves the zone",
    "skipped": "does not apply for you right now",
}


def why_phrases():
    """The why-code phrases: the knowledge layer's when it is loaded (never imported from here: loading it is the
    knowledge_layer module's job), else WHY_TEXT."""
    reasons = sys.modules.get("knowledge.api.reasons")
    return getattr(reasons, "WHY_TEXT", None) or WHY_TEXT


def callout(batch, ctx):
    """A rule-based callout for the trigger batch: at most three short lines, the most pressing first; a talent_check's
    line is never the one cut (it is said once per WoWSims release and talents: the check is told when raised)."""
    kinds = {t.kind: t for t in batch}
    p = ctx["player"]
    lines = []
    if "died" in kinds:
        lines.append("Dead. Release and run back; check the pull that killed you.")
    if "level_up" in kinds:
        lines.append("Level %s. Check new spells at your next trainer stop." % p.get("level"))
    if "low_health_in_combat" in kinds:
        lines.append("Health %s%%. Finish or disengage now." % kinds["low_health_in_combat"].facts.get("hp"))
    turned = sum(t.kind == "quest_turned_in" for t in batch)
    if turned >= 2:                                 # a stop at a quest hub, summed up (Coach._settling)
        lines.append("Turned in %d quests." % turned)
    if "quests_accepted" in kinds:
        f = kinds["quests_accepted"].facts
        lines.append("Picked up %s: %s." % (f.get("count"), ", ".join(f.get("quests") or ()) or "quests"))
    if "step_changed" in kinds or "session_start" in kinds:
        lines.append(step_line(ctx))
    if "stuck_on_step" in kinds:
        lines.append("%s minutes on this step. %s" % (kinds["stuck_on_step"].facts["minutes"], step_line(ctx)))
    if "moving_away_from_waypoint" in kinds:
        f = kinds["moving_away_from_waypoint"].facts
        lines.append("You're moving away from the waypoint: it's %s%s." % (
            ("%d yards " % f["yards"]) if f.get("yards") else "", f.get("relative") or f["direction"]))
    if "gear_upgrade" in kinds:
        for u in reversed(kinds["gear_upgrade"].facts["upgrades"][:2]):
            if u.get("equip_at_level"):
                lines.insert(0, "%s is an upgrade at level %s." % (u["item"], u["equip_at_level"]))
            else:
                lines.insert(0, "Equip %s in your %s%s." % (u["item"], u["slot"],
                                (", replacing " + u["replaces"]) if u.get("replaces") else ""))
    if "vendor_treasure" in kinds:
        f = kinds["vendor_treasure"].facts
        wares = [u["item"] for u in f["upgrades"]] + f["potions"]
        where = " (%d yards %s)" % (f["yards"], f["direction"]) if f.get("yards") is not None else ""
        lines.append("%s%s sells %s, limited stock." % (f["vendor"], where, ", ".join(wares[:3])))
    if "next_upgrade" in kinds:
        lines.append(next_upgrade_line(kinds["next_upgrade"].facts))
    if "enchant_missing" in kinds:
        lines.append(enchant_missing_line(kinds["enchant_missing"].facts))
    if "reward_choice" in kinds:
        r = kinds["reward_choice"].facts["ranked"]
        if r and r[0]["gain"] > 0:
            lines.insert(0, "Take %s, an upgrade for your %s." % (r[0]["item"], r[0]["slot_name"]))
        elif r:
            lines.insert(0, "No reward is an upgrade; take %s." % r[0]["item"])
    if "addon_outdated" in kinds:
        f = kinds["addon_outdated"].facts
        lines.insert(0, "The Glide addon is out of date: %s. To fix it, %s." % (f["problem"], f["fix"]))
    if "buffs_dropped" in kinds:
        lines.append("Rebuff: %s." % ", ".join(kinds["buffs_dropped"].facts["buffs"]))
    if "buff_expiring" in kinds:
        f = kinds["buff_expiring"].facts
        lines.append("%s runs out in %d seconds." % (f["buff"], f["seconds"]))
    if "bags_nearly_full" in kinds:
        lines.append("Bags nearly full; vendor at the next town stop.")
    if "low_durability" in kinds:
        lines.append("Durability low; repair at the next vendor.")
    if "target_tapped" in kinds:
        lines.insert(0, "%s is tapped by someone else; no XP." % (kinds["target_tapped"].facts.get("target") or "Target"))
    if "flight_path_nearby" in kinds:
        f = kinds["flight_path_nearby"].facts
        lines.append("Grab the %s flight path, %s yards away." % (f["flight_path"], f["yards"]))
    if "training_available" in kinds:
        f = kinds["training_available"].facts
        lines.append("Untrained: %s." % ", ".join(f["spells"][:3]))
    if "talent_points_unspent" in kinds:
        n = kinds["talent_points_unspent"].facts["points"]
        tp = p.get("talent_plan") or {}
        if tp.get("due") and tp.get("matches_forever"):
            lines.append("Talent point: %s, rank %d." % (tp["talent"], tp["rank"]))
        else:
            lines.append("%s unspent talent point%s." % (n, "" if n == 1 else "s"))
    if "consumables_low" in kinds:
        f = kinds["consumables_low"].facts
        lines.append("%s left: %s. Restock at the next vendor." % (f["kind"].capitalize(), f["left"]))
    if "quest_turned_in" in kinds and p.get("xp_per_hour"):
        lines.append("Pace %s XP per hour." % p["xp_per_hour"])
    if "turnin_decay_soon" in kinds:
        f = kinds["turnin_decay_soon"].facts
        lines.insert(0, "Turn in %s before you level: it drops from %s to %s XP." % (
            f.get("quest"), f.get("xp_now"), f.get("xp_after_level_up")))
    if "pursuit_got" in kinds:
        lines.insert(0, pursuit_got_line(kinds["pursuit_got"].facts))
    if "next_move" in kinds:
        lines.append(next_move_line(kinds["next_move"].facts))
    if "pursuit_roll" in kinds:
        lines.append(pursuit_roll_line(kinds["pursuit_roll"].facts))
    if "facts_ready" in kinds:
        lines.append(facts_ready_line(kinds["facts_ready"].facts))
    if "fact_corrected" in kinds:
        lines.append(fact_corrected_line(kinds["fact_corrected"].facts))
    if "talent_check" in kinds:
        return " ".join(lines[:2] + [talent_check_line(kinds["talent_check"].facts)])
    return " ".join(lines[:3])


# ---- sims the coach runs for the player (sims.py): the lines said without a model ------------------------------
NO_WOWSIMS = "I can't sim that without WoWSims."


def _signed(d):
    """A DPS change as said: "+8", "-3", "+0.8"."""
    t = dps_text(abs(float(d or 0)))
    return ("-" if float(d or 0) < 0 else "+") + t


def sim_item_line(item, worn, d, about_same=False):
    """An item swap WoWSims simmed on the player's gear: "WoWSims: <item> over your <worn>, about +<d> DPS on your
    gear." ("about the same as yours" within the sims' noise; worn None: no worn item named)."""
    over = " over your %s" % worn if worn else ""
    gain = "about the same as yours" if about_same else "about %s DPS" % _signed(d)
    return "WoWSims: %s%s, %s on your gear." % (item or "that item", over, gain)


def sim_talent_line(build, pct, about_same=False, tree=None):
    """A talent build WoWSims simmed on the player's gear, with its own rotation: "WoWSims' <build> build sims about
    <pct>% higher on your gear, with its own rotation." (lower; "about the same as yours" within the sims' noise). A
    talent string that is no WoWSims build (build None) is simmed with its tree's rotation, and says so."""
    if about_same:
        how = "about the same as yours"
    else:
        how = "about %s%% %s" % (dps_text(abs(float(pct or 0))), "lower" if float(pct or 0) < 0 else "higher")
    if not build:
        return "Those talents sim %s on your gear, with WoWSims' %s rotation." % (how, tree or "own")
    return "WoWSims' %s build sims %s on your gear, with its own rotation." % (build, how)


def talent_check_line(facts):
    """A WoWSims talent build that sims clearly higher than the player's own on their gear (talent_check)."""
    return sim_talent_line(facts.get("build") or "other", facts.get("pct"))


def next_upgrade_line(facts):
    """The best item the character can get now, from Glide's best-in-slot sims (next_upgrade): "Next upgrade: <item>
    (<slot>, about +<dps> DPS): <where it comes from>." A weapon paired with another (a one-hander and an off hand in
    place of a two-hander) names both: "<item> with <other>"; a bonus the number leaves out (cond) says so."""
    src = str(facts.get("source") or "").strip().rstrip(".")
    item = facts.get("item") or "an item"
    if facts.get("with"):
        item += " with %s" % facts["with"]
    cond = ", not counting its bonus against some creatures" if facts.get("cond") else ""
    return "Next upgrade: %s (%s, about +%s DPS%s)%s." % (item, facts.get("slot") or "gear",
                                                          dps_text(facts.get("dps")), cond, (": " + src) if src else "")


def enchant_missing_line(facts):
    """A worn item without an enchant, and the enchant worth most on it (enchant_missing): "Your <slot> has no
    enchant: <enchant> is worth about <dps> DPS." ("have" for bracers, gloves, boots and the like)."""
    slot = facts.get("slot") or "gear"
    return "Your %s %s no enchant: %s is worth about %s DPS." % (slot, "have" if slot in PLURAL_SLOTS else "has",
                                                                facts.get("enchant") or "an enchant",
                                                                dps_text(facts.get("dps")))


# ---- the next move toward the next best-in-slot piece (pursuit.py, rules._pursuit) ---------------------------------
def move_line(text, odds=None, also=None):
    """A move and its odds as said: "<text>, about 1 in 8, usually 3 to 10 runs; that's classic's chance" (odds that
    aren't a chance, "I don't know its price yet", after a semicolon), then what else the run rolls (also: the odds are
    the lead item's): "... The run also rolls the Magister's Mantle". No closing period."""
    text = str(text or "").strip().rstrip(".")
    odds = str(odds or "").strip().rstrip(".")
    if odds:
        text = "%s%s %s" % (text, "," if odds.lower().startswith("about") else ";", odds)
    return text + (". The run also rolls %s" % also if also else "")


def next_move_line(facts):
    """Glide's next move toward the next best-in-slot piece (next_move): "Next: <move>, <odds>." (and "The run also
    rolls <also>."); zoning into the move's place (here): what drops there, "<text>." """
    if facts.get("here"):
        return _cap(str(facts.get("text") or "").rstrip(".")) + "."
    return "Next: %s." % move_line(facts.get("text"), facts.get("odds"), facts.get("also"))


def odds_source(odds):
    """The source a move's odds name, as effort.say_odds names it below 0.9 sure: "classic's chance" (from "...; that's
    classic's chance"), "web, unconfirmed" (from "... (web, unconfirmed)"), "a guess" (from "my guess is ..."); None
    when none is named (the player's own kills, or no chance at all)."""
    text = str(odds or "").strip().rstrip(".")
    if re.match(r"my guess\b", text, re.I) or re.search(r"don'?t know the real chance", text, re.I):
        return "a guess"
    paren = re.search(r"\((web[^()]*)\)$", text, re.I)
    if paren:
        return re.sub(r",\s*with your kills counted$", "", paren.group(1).strip()) or None
    if ";" not in text:
        return None
    return re.sub(r"^that'?s\s+", "", text.rpartition(";")[2].strip(), flags=re.I) or None


TRY_ONE = {"runs": "run", "weeks": "week", "kills": "kill"}


def pursuit_roll_line(facts):
    """A boss of the move died and the item wasn't theirs (pursuit_roll): "No <item>. That's run <n>; it usually takes
    <range> (<src>)." ("week" for a raid, "kill" for a creature: unit), and past the median try "<n> runs without it is
    bad luck, not a bad plan." (unlucky)."""
    item = facts.get("item") or "luck"
    unit = facts.get("unit") if facts.get("unit") in TRY_ONE else "runs"
    n = facts.get("tries") or 1
    line = "No %s. That's %s %s" % (item, TRY_ONE[unit], n)
    if facts.get("range"):
        line += "; it usually takes %s" % facts["range"]
        if facts.get("src"):
            line += " (%s)" % facts["src"]
    line += "."
    if facts.get("unlucky"):
        line += " %s %s without it is bad luck, not a bad plan." % (n, unit if n != 1 else TRY_ONE[unit])
    return line


def pursuit_got_line(facts):
    """The item Glide's move went for is theirs (pursuit_got): "<Item> is yours[; equip it]. Next: <next move>." """
    line = "%s is yours" % _cap(the_item(facts.get("item") or "the item"))
    if facts.get("equip"):
        line += "; equip it"
    nxt = str(facts.get("next") or "").strip().rstrip(".")
    return line + (". Next: %s." % nxt if nxt else ".")


def the_item(name):
    """An item's name with "the" in front ("the Sorcerer's Crown"), unless it has one."""
    name = str(name or "").strip()
    return name if not name or name.lower().startswith("the ") else "the " + name


def _cap(text):
    return text[:1].upper() + text[1:]


def move_act(move):
    """(kind, id) of a move's "act" ("inst:2057" -> ("inst", 2057)); the id as text when it isn't a number."""
    kind, _, arg = str((move or {}).get("act") or "").partition(":")
    return kind, int(arg) if arg.lstrip("-").isdigit() else (arg or None)


def move_items(move):
    """A move's item ids, in the planner's order."""
    return [i for i in (move or {}).get("items") or () if isinstance(i, int) and not isinstance(i, bool)]


STAND_IN = re.compile(r"(?:item\s*)?#?\d+", re.I)        # the planner's "item 12345" when it knows no name


def given_names(move):
    """{item id: name} the planner gave a move ("names", keyed by the id as text), without its "item 12345"
    stand-ins: never an id said as a name."""
    names = (move or {}).get("names")
    out = {}
    for k, v in (names.items() if isinstance(names, dict) else ()):
        if str(k).isdigit() and isinstance(v, str) and v.strip() and not STAND_IN.fullmatch(v.strip()):
            out[int(k)] = v.strip()
    return out


def overlay_line(level, move, name_fn=None, srcs_fn=None):
    """The overlay's one line at 60 with no guide step (app.step_line's fallback): "L60  Next: Scholomance:
    Bonecreeper Stylus" (the planner's short line, "text", else the move in words); None without a move."""
    if not isinstance(move, dict) or not move_items(move):
        return None
    names = given_names(move)
    text = move.get("text") if isinstance(move.get("text"), str) else None
    if not text or (isinstance(move.get("names"), dict) and any(i not in names for i in move_items(move))):
        # (no short line, or it may carry an "item 12345" stand-in: the move in words)
        text = move_text(move, lambda i: names.get(i) or (name_fn(i) if name_fn else None), srcs_fn)
    return "L%s  Next: %s" % (level, _cap(text)) if text else None


def _src(srcs, kinds, **match):
    """The first source of these kinds that matches (a value of None matches anything), else the first of the kinds."""
    hits = [s for s in srcs or () if isinstance(s, dict) and s.get("kind") in kinds]
    return next((s for s in hits if all(v is None or s.get(k) == v for k, v in match.items())), hits[0] if hits else
                None)


def move_place(move, srcs_fn):
    """The names a dungeon or raid move's place goes by (the planner's "place" and "zone", the sources' instance and
    zone names: "Upper Blackrock Spire", "Blackrock Spire"), for zoning in; [] for another move."""
    m = move or {}
    kind, where = move_act(m)
    if kind not in ("inst", "raid"):
        return []
    out = [p for p in (m.get("place"), m.get("zone")) if isinstance(p, str) and p]
    for iid in move_items(m):
        s = _src(srcs_fn(iid) if srcs_fn else [], ("drop", "trash"), zone=where if isinstance(where, int) else None)
        for name in (s or {}).get("instance"), (s or {}).get("zone_name"):
            if name and name not in out:
                out.append(name)
    return list(dict.fromkeys(out))


def _bosses(move, name_fn, srcs_fn):
    """[(item name, boss name or None)] of a dungeon or raid move's items that have a name (the boss: the planner's
    "drops", else the sources')."""
    m = move or {}
    _kind, where = move_act(m)
    drops = {d.get("item"): d.get("npc_name") for d in m.get("drops") or () if isinstance(d, dict)}
    out = []
    for iid in move_items(m):
        name = name_fn(iid) if name_fn else None
        if not name:
            continue
        s = _src(srcs_fn(iid) if srcs_fn else [], ("drop",), zone=where if isinstance(where, int) else None)
        out.append((name, drops.get(iid) or (s or {}).get("npc_name")))
    return out


def _named(pairs, n=2):
    """"the X (Boss) and the Y (Boss)", at most n named, then "and more" (no second number in a line)."""
    parts = [the_item(item) + (" (%s)" % boss if boss else "") for item, boss in pairs[:n]]
    if len(pairs) > len(parts):
        return "%s and more" % ", ".join(parts)
    return " and ".join(parts)


def _plural(name):
    """Whether an item's name is a plural ("Ghostloom Leggings", "Robes of the Exalted"; not "Bonecreeper Stylus"):
    its head word (the one before " of ", else the last) ends in a lone s."""
    head = str(name or "").split(" of ")[0].split()
    w = head[-1].lower() if head else ""
    return w.endswith("s") and not w.endswith(("ss", "us", "is", "'s"))


def _also(names):
    """The other items a run rolls, at most two named: "the X", "the X and the Y", "the X, the Y and more"."""
    named = [the_item(n) for n in names[:2]]
    if len(names) > 2:
        return "%s, %s and more" % (named[0], named[1])
    return " and ".join(named) or None


def move_parts(move, name_fn=None, srcs_fn=None):
    """A pursuit move (pursuit.Planner's plan: {"act", "items", "odds", "why", ...}) in words, without its odds:
    (text, also). text: "Scholomance. The Bonecreeper Stylus drops from Darkmaster Gandling", "Deliana in Ironforge
    sells the Sorcerer's Crown", "the quest <name> rewards the <item>" (one ready to turn in, the move's "done": "turn
    in the quest <name> for the <item>; <who> in <place> takes it"), "the auction house for the <item>"; also, for a
    dungeon or raid run that rolls more than its lead item: the others ("the Magister's Mantle"), said after the odds
    (which are the lead item's), else None. name_fn(item id) -> its name or None; srcs_fn(item id) -> its sources (the
    research's Src list) or []; the planner's own "place", "npc_name" and "drops" fill in what they don't say. (None,
    None) when nothing names its items (never an id)."""
    m = move or {}
    kind, where = move_act(m)
    names = [n for n in (name_fn(i) if name_fn else None for i in move_items(m)) if n]
    if not names:
        return None, None
    srcs = (srcs_fn(move_items(m)[0]) if srcs_fn else None) or []
    things = " and ".join(the_item(n) for n in names[:2]) + (" and more" if len(names) > 2 else "")
    if kind in ("inst", "raid"):
        pairs, place = _bosses(m, name_fn, srcs_fn), (move_place(m, srcs_fn) or [None])[0]
        lead, boss = pairs[0]
        if boss:
            said = "%s %s from %s" % (_cap(the_item(lead)), "drop" if _plural(lead) else "drops", boss)
            return ("%s. %s" % (place, said) if place else said), _also([n for n, _b in pairs[1:]])
        return ("%s for %s" % (place, _named(pairs)) if place else _cap(_named(pairs))), None
    if kind == "mob":
        s = _src(srcs, ("drop", "world"), npc=where if isinstance(where, int) else None) or {}
        who, place = s.get("npc_name") or m.get("npc_name"), s.get("zone_name") or m.get("place")
        return "%s%s drops %s" % (who or "a creature", " in %s" % place if place else "", things), None
    if kind == "vendor":
        s = _src(srcs, ("vendor", "rep"), npc=where if isinstance(where, int) else None) or {}
        who, place = s.get("npc_name") or m.get("npc_name"), s.get("zone_name") or m.get("place")
        if who:
            return "%s%s sells %s" % (who, " in %s" % place if place else "", things), None
        return "a vendor%s sells %s" % (" in %s" % place if place else "", things), None
    if kind == "quest":
        s = _src(srcs, ("quest",), quest=where if isinstance(where, int) else None) or {}
        who, place = m.get("npc_name"), m.get("place")          # (the planner's: who starts it, and where)
        at = " in %s" % place if isinstance(place, str) and place else ""
        if m.get("done"):                                       # (complete: who it is turned in to)
            text = "turn in the quest %s for %s" % (s["quest_name"], things) if s.get("quest_name") else \
                "turn in the quest for %s" % things
            return text + ("; %s%s takes it" % (who, at) if isinstance(who, str) and who else ""), None
        text = "the quest %s rewards %s" % (s["quest_name"], things) if s.get("quest_name") else \
            "a quest rewards %s" % things
        if isinstance(who, str) and who:
            text += "; it starts with %s%s" % (who, at)
        return text, None
    if kind == "ah":
        return "the auction house for %s" % things, None
    if kind == "pvp":
        return "%s, a PvP reward" % things, None
    want = {"rep": ("rep",), "craft": ("crafted",)}.get(kind)
    s = _src(srcs, want) if want else item_text.best(srcs)
    said = item_text.source_text(s) if s else ""
    return ("%s, %s" % (things, said) if said else things), None


def move_text(move, name_fn=None, srcs_fn=None):
    """A pursuit move in words without its odds (move_parts' text: for a run, its lead item); None when nothing names
    its items."""
    return move_parts(move, name_fn, srcs_fn)[0]


def move_say(move, name_fn=None, srcs_fn=None):
    """A pursuit move as said after "Next: ", with its odds and what else the run rolls (move_line); None when nothing
    names its items."""
    text, also = move_parts(move, name_fn, srcs_fn)
    return move_line(text, (move or {}).get("odds"), also) if text else None


def here_text(move, name_fn=None, srcs_fn=None):
    """Zoning into a dungeon or raid move's place: what drops there for the player, "The Bonecreeper Stylus drops from
    Darkmaster Gandling here" or "Here: the X (Boss) and the Y (Boss)"; None for another move or none named."""
    if move_act(move)[0] not in ("inst", "raid"):
        return None
    pairs = _bosses(move, name_fn, srcs_fn)
    if not pairs:
        return None
    if len(pairs) == 1:
        item, boss = pairs[0]
        verb = "drop" if _plural(item) else "drops"
        return "%s %s from %s here" % (_cap(the_item(item)), verb, boss) if boss else \
            "%s %s here" % (_cap(the_item(item)), verb)
    return "Here: " + _named(pairs)


def fact_corrected_line(facts):
    """What to say when a web fact the coach told the player about where an item comes from turned out wrong
    (fact_corrected): "I had <item> wrong: <where it comes from now>." """
    return "I had %s wrong: %s." % (facts.get("item") or "that item", str(facts.get("new") or "").rstrip("."))


def facts_ready_line(facts):
    """What to say when new item facts are in the addon's Data/Live.lua (facts_ready): a /reload shows them, or
    (restart) only a WoW restart does."""
    if facts.get("restart"):
        return "Glide learned where your gear comes from; restart WoW once to see it in the game."
    return "New item facts are in the game after a /reload."


def why_line(ctx):
    """Offline answer to "why": the block's step kind, XP now and unlocked, first reason code and RestedXP note,
    and the plan's level vs yours."""
    k = ctx.get("knowledge") or {}
    st = k.get("step") or {}
    if not st.get("kind"):
        return None
    out = "Why this step: %s step%s%s" % (st["kind"], (", %s XP now" % st["xp_now"]) if st.get("xp_now") else "",
                                          (", unlocks %s XP" % st["unlocks_xp"]) if st.get("unlocks_xp") else "")
    codes = st.get("why") or []
    if codes:
        base, _, arg = str(codes[0]).partition(":")
        out += "; %s%s" % (why_phrases().get(base, base), (" " + arg) if arg and base in ("level_gate", "xp_gate") else "")
    out += "."
    notes = st.get("rxp_notes") or []
    if notes:
        out += " RestedXP: %s" % notes[0].rstrip(".") + "."
    if st.get("exp_level") is not None and st.get("level_now") is not None:
        out += " Plan: level %.1f here, you are %.1f." % (st["exp_level"], st["level_now"])
    return out


def step_line(ctx):
    """The current RestedXP step in a few words: its quests and where to go."""
    cur = next((s for s in ctx["rxp_steps"] if s["which"] == "current"), None)
    if not cur:
        return "No RestedXP step visible."
    parts = []
    for e in cur["elements"]:
        if e.get("quest"):
            q = ctx["quests"].get(e["quest"], {})
            parts.append("%s %s" % (e["tag"], q.get("name") or e["quest"]))
        elif e["tag"] == "goto":
            parts.append("go " + (e.get("where") or e.get("coords") or ""))
    return ("Next: " + ", ".join(parts[:4])) if parts else (cur.get("text") or "")
