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
