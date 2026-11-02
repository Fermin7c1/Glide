// Which RestedXP steps the Guides page shows, and their numbers, read from the masks the engine sends with each
// guide (companion/rxp_explorer.py visibility: the coach's own rules, knowledge/rxp_parser). The page evaluates no
// `<<` condition itself. No DOM: tests/test_rxp_conditions.py compiles this file alone and runs it under Node.

export type Faction = "Alliance" | "Horde";

/** The races whose faction is fixed. Skyborne (WoW Forever) joins either faction, so it isn't here: the race picker
 * offers it under both, and its value carries the faction ("Skyborne@Alliance"). */
const RACE_FACTION: Record<string, Faction> = {
  Human: "Alliance", Dwarf: "Alliance", NightElf: "Alliance", Gnome: "Alliance",
  Orc: "Horde", Undead: "Horde", Scourge: "Horde", Tauren: "Horde", Troll: "Horde",
};

/** A race picker value ("Human", "Skyborne@Alliance", or "" for every race) -> the race token RestedXP's conditions
 * use ("Undead" is "Scourge" there) and the faction it plays. */
export function racePick(value: string): { race: string; faction: Faction | "" } {
  const [name, side] = (value || "").split("@");
  if (!name) return { race: "", faction: "" };
  const faction: Faction | "" = name === "Skyborne" ? (side === "Alliance" || side === "Horde" ? side : "")
    : RACE_FACTION[name] || "";
  return { race: name === "Undead" ? "Scourge" : name, faction };
}

/** The settings combination the engine's masks are ordered by: solo self-found 1, group quests 2, hardcore 4. */
export function comboOf(ssf: boolean, groupQuests: boolean, hardcore: boolean): number {
  return (ssf ? 1 : 0) | (groupQuests ? 2 : 0) | (hardcore ? 4 : 0);
}

/** One step as the page lists it: its position in the guide's steps, RestedXP's number for the picked character
 * (null for a step only another character gets, shown dimmed under "Show all"). */
export interface ShownStep {
  pos: number;
  num: number | null;
  dimmed: boolean;
}

/** The engine's masks for one guide: players ["Human/WARRIOR", "Skyborne@Horde/MAGE", ...] and the distinct masks
 * (hex: 8 chunks, one per settings combination, of one bit per player). */
export class Visibility {
  readonly n: number;
  private readonly width: number;
  private readonly bits: Uint8Array[];
  private readonly picks: { race: string; cls: string }[];

  constructor(readonly players: string[], masks: string[]) {
    this.n = players.length;
    this.width = Math.ceil(this.n / 4);
    this.picks = players.map((p) => { const [race, cls] = p.split("/"); return { race, cls }; });
    this.bits = masks.map((m) => this.decode(m, 8));
  }

  private decode(hex: string, chunks: number): Uint8Array {
    const out = new Uint8Array(chunks * this.n);
    for (let c = 0; c < chunks; c++) {
      const chunk = hex.slice(c * this.width, (c + 1) * this.width);
      // the chunk is one number, most significant digit first: digit d (from the right) holds players 4d..4d+3
      for (let d = 0; d < chunk.length; d++) {
        const v = parseInt(chunk[chunk.length - 1 - d], 16) || 0;
        for (let b = 0; b < 4; b++) {
          const p = d * 4 + b;
          if (p < this.n && (v >> b) & 1) out[c * this.n + p] = 1;
        }
      }
    }
    return out;
  }

  /** The characters a pick allows: a race picks its classes, a class the races that play it, no pick everyone.
   * A bare "Skyborne" is either faction's. */
  pick(race: string, cls: string): number[] {
    const r = race || "", c = (cls || "").toUpperCase();
    const out: number[] = [];
    this.picks.forEach((p, i) => {
      if (r && p.race !== r && !(r === "Skyborne" && p.race.startsWith("Skyborne@"))) return;
      if (c && p.cls !== c) return;
      out.push(i);
    });
    return out;
  }

  /** Whether a step with this mask shows for any of these characters under this settings combination. */
  shown(mask: number, combo: number, who: number[]): boolean {
    const b = this.bits[mask];
    if (!b) return true;
    const base = combo * this.n;
    return who.some((p) => b[base + p] === 1);
  }

  /** A step no character gets under any settings combination (RestedXP never loads it: `step << skip`, another
   * game's or season's steps). */
  never(mask: number): boolean {
    const b = this.bits[mask];
    return !!b && !b.some((x) => x === 1);
  }

  /** Whether an action with its own `<<` condition (a one-chunk player mask) applies to any of these characters;
   * an action without one always does. */
  actionShown(hex: string | null | undefined, who: number[]): boolean {
    if (!hex) return true;
    const b = this.decode(hex, 1);
    return who.some((p) => b[p] === 1);
  }

  /** The steps listed for these characters and settings, numbered as RestedXP numbers them (exact when `who` is one
   * character; with several, the steps any of them gets, counted in turn). With `showAll`, the steps only other
   * characters get come too, dimmed and unnumbered; steps nobody gets never do. */
  list(stepMasks: (number | undefined)[], combo: number, who: number[], showAll: boolean): ShownStep[] {
    const out: ShownStep[] = [];
    let n = 0;
    stepMasks.forEach((m, pos) => {
      if (m === undefined) { out.push({ pos, num: ++n, dimmed: false }); return; }
      if (this.never(m)) return;
      if (this.shown(m, combo, who)) out.push({ pos, num: ++n, dimmed: false });
      else if (showAll) out.push({ pos, num: null, dimmed: true });
    });
    return out;
  }
}

// ---- the live character (the light stream's name, class, race, faction, guide, guide_group, step) ----------------

const PICK_RACES = ["Human", "Dwarf", "NightElf", "Gnome", "Orc", "Undead", "Tauren", "Troll"];
const PICK_CLASSES = ["WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK", "DRUID"];

/** The race picker's value for the game's race (UnitRace's file name: "Scourge", "NightElf", "Skyborne") and faction;
 * "" for a race the picker doesn't have. */
export function liveRacePick(race: string | null | undefined, faction: string | null | undefined): string {
  const r = (race || "").replace(/\s+/g, "").toLowerCase();
  if (!r) return "";
  if (r === "scourge" || r === "undead") return "Undead";
  if (r === "skyborne") return faction === "Alliance" || faction === "Horde" ? `Skyborne@${faction}` : "";
  return PICK_RACES.find((p) => p.toLowerCase() === r) || "";
}

/** The class picker's value for the game's class ("MAGE"), "" for one it doesn't have. */
export function liveClassPick(cls: string | null | undefined): string {
  const c = (cls || "").trim().toUpperCase();
  return PICK_CLASSES.includes(c) ? c : "";
}

/** A guide name for matching, as the engine's norm_guide_name: case, spacing and the level range's zero padding
 * ignored ("05-11  Dun Morogh" == "5-11 dun morogh"); a character cut in half by the strip's 40 bytes is dropped. */
export function normGuideName(s: string | null | undefined): string {
  const t = (s || "").replace(/�+$/, "").replace(/\s+/g, " ").trim().toLowerCase();
  return t.replace(/^0*(\d+)\s*-\s*0*(\d+)/, "$1-$2");
}

/** A guide on the page, as far as finding the live one needs. */
export interface GuideRef {
  name: string;
  group: string;
  faction: "Alliance" | "Horde" | null;
  steps: number;
  rxp_name?: string;
  rxp_group?: string;
}

/** The page's guide the character plays (the addon's guide name and group, RestedXP's forms cut to 40 bytes), or
 * null: the same name, its group when one has it, then as the engine resolves a name (live_logs.Guides.resolve) the
 * character's faction first, a levelling guide before a dungeon one, the longer first. */
export function liveGuide<T extends GuideRef>(guides: T[], guide: string | null | undefined, group: string | null | undefined,
  faction: string | null | undefined): T | null {
  const want = normGuideName(guide);
  if (!want) return null;
  let found = guides.filter((c) => normGuideName(c.rxp_name ?? c.name) === want || normGuideName(c.name) === want);
  const grp = normGuideName(group);
  const inGroup = found.filter((c) => normGuideName(c.rxp_group ?? c.group) === grp);
  if (grp && inGroup.length) found = inGroup;
  const rank = (c: T) => [c.faction && faction && c.faction !== faction ? 1 : 0, /dungeon/i.test(c.group) ? 1 : 0, -c.steps];
  let best: T | null = null;
  for (const c of found) {
    if (!best) { best = c; continue; }
    const a = rank(c), b = rank(best);
    const k = a.findIndex((v, i) => v !== b[i]);
    if (k >= 0 && a[k] < b[k]) best = c;
  }
  return best;
}

/** The position in the guide's steps of RestedXP's step `n` for one character (its index in the masks' players) under
 * a settings combination: the steps that character gets, counted as RestedXP counts them. A number past the end is
 * the last step; null when the character gets no step or isn't among the players. */
export function liveStepPos(v: Visibility, stepMasks: (number | undefined)[], player: number, combo: number, n: number): number | null {
  if (player < 0 || player >= v.n || !(n >= 1)) return null;
  const listed = v.list(stepMasks, combo, [player], false);
  if (!listed.length) return null;
  return listed[Math.min(n, listed.length) - 1].pos;
}
