// RestedXP's `<<` step conditions, evaluated for the race, faction and class picked in the Guide & Route Explorer
// (rxp-browser.ts) as RestedXP's applies() in GuideLoader.lua would for a character. No DOM: tests/test_rxp_conditions.py
// compiles this file alone and runs it under Node.

export type Faction = "Alliance" | "Horde";

/** The races whose faction is fixed. Skyborne (WoW Forever) joins either faction, so it isn't here: the race picker
 * offers it under both, and its value carries the faction ("Skyborne@Alliance"). */
const RACE_FACTION: Record<string, Faction> = {
  Human: "Alliance", Dwarf: "Alliance", NightElf: "Alliance", Gnome: "Alliance", Draenei: "Alliance",
  Orc: "Horde", Scourge: "Horde", Undead: "Horde", Tauren: "Horde", Troll: "Horde", BloodElf: "Horde",
};
const EITHER_FACTION = new Set(["Skyborne"]);
const CLASSES = ["WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK", "DRUID", "DEATHKNIGHT"];

// The classes each race plays (knowledge/vocab.py RACE_CLASSES), and each faction's (FACTION_CLASSES): with only a
// race or only a class picked, a step shows when one of the characters the pick allows would see it
const RACE_CLASSES: Record<string, string[]> = {
  Human: ["WARRIOR", "PALADIN", "ROGUE", "PRIEST", "MAGE", "WARLOCK"],
  Dwarf: ["WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN"],
  NightElf: ["WARRIOR", "HUNTER", "ROGUE", "PRIEST", "DRUID"],
  Gnome: ["WARRIOR", "ROGUE", "MAGE", "WARLOCK"],
  Orc: ["WARRIOR", "HUNTER", "ROGUE", "SHAMAN", "WARLOCK"],
  Undead: ["WARRIOR", "ROGUE", "PRIEST", "MAGE", "WARLOCK"],
  Tauren: ["WARRIOR", "HUNTER", "SHAMAN", "DRUID"],
  Troll: ["WARRIOR", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE"],
  Skyborne: ["WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK", "DRUID"],
};
const FACTION_CLASSES: Record<Faction, string[]> = {
  Alliance: ["WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "MAGE", "WARLOCK", "DRUID"],
  Horde: ["WARRIOR", "HUNTER", "ROGUE", "PRIEST", "MAGE", "WARLOCK", "DRUID", "SHAMAN"],
};
/** Every race picker value (racePick), Skyborne once per faction. */
const RACE_PICKS = ["Human", "Dwarf", "NightElf", "Gnome", "Skyborne@Alliance", "Orc", "Undead", "Tauren", "Troll", "Skyborne@Horde"];

/** The classes a race picker value can play ("Skyborne@Horde": Skyborne's, less the classes the Horde lacks). */
function classesOf(raceValue: string): string[] {
  const [name] = raceValue.split("@");
  const { faction } = racePick(raceValue);
  const own = RACE_CLASSES[name] || CLASSES;
  return faction ? own.filter((c) => FACTION_CLASSES[faction].includes(c)) : own;
}

/** A race picker value ("Human", "Skyborne@Alliance", or "" for every race) -> the race token RestedXP's conditions
 * use ("Undead" is "Scourge" there) and the faction it plays. */
export function racePick(value: string): { race: string; faction: Faction | "" } {
  const [name, side] = (value || "").split("@");
  if (!name) return { race: "", faction: "" };
  const faction: Faction | "" = EITHER_FACTION.has(name) ? (side === "Alliance" || side === "Horde" ? side : "")
    : RACE_FACTION[name] || "";
  return { race: name === "Undead" ? "Scourge" : name, faction };
}

/** Whether a step with this `<<` condition shows for the picked race (racePick's value) and class ("" for all).
 * With both picked, as RestedXP decides for that character. With one left at "all", whether any character the other
 * pick allows would see it: "<< !Warlock" shows for a Human with every class (a Human Warrior sees it), as does
 * "<< Warlock" (a Human Warlock does); with neither picked, every step shows. */
export function conditionApplies(raw: string | null | undefined, raceValue: string, clsValue: string): boolean {
  if (!raw || !raw.trim()) return true;
  const cls = (clsValue || "").toUpperCase();
  if (!raceValue && !cls) return true;   // "All Races" + "All Classes": show everything
  if (raceValue && cls) return appliesTo(raw, raceValue, cls);
  if (raceValue) return classesOf(raceValue).some((c) => appliesTo(raw, raceValue, c));
  return RACE_PICKS.filter((r) => classesOf(r).includes(cls)).some((r) => appliesTo(raw, r, cls));
}

/** RestedXP's applies() for one character: this race picker value and this class. */
function appliesTo(raw: string, raceValue: string, cls: string): boolean {
  const { race, faction } = racePick(raceValue);

  const evalLit = (lit: string): boolean => {
    if (lit === "TRUE") return true;
    if (lit === "FALSE") return false;
    const e = lit === "Undead" ? "Scourge" : lit === "Haranir" ? "Harronir" : lit;
    const up = e.toUpperCase() === "DK" ? "DEATHKNIGHT" : e.toUpperCase();
    if (/^\d+$/.test(e)) return true;
    if (up === "CLASSIC" || up === "FOREVER" || e === "enUS") return true;
    if (CLASSES.includes(up)) return !cls || up === cls;
    if (e === "Alliance" || e === "Horde") return !faction || e === faction;
    if (e in RACE_FACTION || EITHER_FACTION.has(e)) return !race || e === race;
    return true;
  };

  const evalExpr = (expr: string): boolean => {
    let s = expr;
    let prev = "";
    while (s !== prev && /(!?)\(\s*([^()]*?)\s*\)/.test(s)) {
      prev = s;
      s = s.replace(/(!?)\(\s*([^()]*?)\s*\)/g, (_m, neg, inner) => (evalExpr(inner) !== (neg === "!") ? "TRUE" : "FALSE"));
    }
    for (const branch of s.split("/")) {
      const tokens = branch.match(/!?[A-Za-z0-9]+/g);
      if (!tokens || !tokens.length) continue;
      let allOk = true;
      for (const tok of tokens) {
        const neg = tok.startsWith("!");
        const lit = neg ? tok.slice(1) : tok;
        if (evalLit(lit) === neg) { allOk = false; break; }
      }
      if (allOk) return true;
    }
    return false;
  };
  return evalExpr(raw);
}
