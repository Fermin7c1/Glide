"""The coach without a model (no API key, the AI off or suspended): rule-based callouts for the triggers, and the
current step or the reason for it in plain words."""
import sys

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
