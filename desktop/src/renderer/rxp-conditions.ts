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

/** A race picker value ("Human", "Skyborne@Alliance", or "" for every race) -> the race token RestedXP's conditions
 * use ("Undead" is "Scourge" there) and the faction it plays. */
export function racePick(value: string): { race: string; faction: Faction | "" } {
  const [name, side] = (value || "").split("@");
  if (!name) return { race: "", faction: "" };
  const faction: Faction | "" = EITHER_FACTION.has(name) ? (side === "Alliance" || side === "Horde" ? side : "")
    : RACE_FACTION[name] || "";
  return { race: name === "Undead" ? "Scourge" : name, faction };
}

/** Whether a step with this `<<` condition shows for the picked race (racePick's value) and class ("" for all). */
export function conditionApplies(raw: string | null | undefined, raceValue: string, clsValue: string): boolean {
  if (!raw || !raw.trim()) return true;
  const { race, faction } = racePick(raceValue);
  const cls = (clsValue || "").toUpperCase();
  if (!race && !cls) return true;   // "All Races" + "All Classes": show everything

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
