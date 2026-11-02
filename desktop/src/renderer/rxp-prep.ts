// "Before you start" on the Guides page, DOM-free (tested under Node by tests/test_rxp_prep.py): the guide's
// purchases and bank trips (the facts' prep list, read from the guide's own steps) as the few words of a chip, where
// each is needed, and copper as the game writes it.
import type { RxpFactsPrep } from "../shared/ipc.js";

export const PREP_GLYPH: Record<RxpFactsPrep["kind"], string> = { buy: "◈", ah: "⚖", bank_out: "⇡", bank_in: "⇣" };
const DO: Record<RxpFactsPrep["kind"], [string, string]> = { buy: ["Buy", ""], ah: ["Buy", " at the Auction House"],
  bank_out: ["Take", " from the bank"], bank_in: ["Put", " in the bank"] };

/** Copper as the game writes it: "1g 2s 5c", "25c". */
export function money(copper: number | null | undefined): string {
  if (copper == null || !(copper > 0)) return "";
  const g = Math.floor(copper / 10000), s = Math.floor((copper % 10000) / 100), c = Math.round(copper % 100);
  return [g ? `${g}g` : "", s ? `${s}s` : "", c ? `${c}c` : ""].filter(Boolean).join(" ");
}

const nameOf = (p: RxpFactsPrep) => p.name || `item #${p.item_id}`;

/** The chip's words: "10× Refreshing Spring Water" (no count when the guide gives none). */
export function prepLabel(p: RxpFactsPrep): string {
  return `${p.qty && p.qty > 1 ? `${p.qty}× ` : ""}${nameOf(p)}`;
}

/** Where it's needed, in a word or two: "for step 15", "for the next guide", or "" when the guide doesn't say. `num`
 * gives RestedXP's number of a step for the picked character (null when that character doesn't get it). */
export function prepNeed(p: RxpFactsPrep, num: (src: number) => string | null): string {
  if (p.for_idx != null) { const n = num(p.for_idx); return n ? `for step ${n}` : ""; }
  return p.for_guide ? "for the next guide" : "";
}

/** The chip's card: what to do and where, who sells it and for how much (with the knowledge base), what it's for. */
export function prepTitle(p: RxpFactsPrep, num: (src: number) => string | null, questName?: (q: number) => string | null): string {
  const at = num(p.idx);
  const lines = [`${DO[p.kind][0]} ${prepLabel(p)}${DO[p.kind][1]}${at ? ` at step ${at}` : ""}.`];
  const v = p.vendors?.[0];
  if (p.kind === "buy" && v?.name) lines.push(`${v.name}${v.subname ? ` <${v.subname}>` : ""} sells it${money(v.price ?? p.price) ? ` for ${money(v.price ?? p.price)}` : ""}.`);
  const n = p.for_idx != null ? num(p.for_idx) : null;
  const q = p.quest_id != null ? questName?.(p.quest_id) : null;
  const need = n ? `Needed at step ${n}` : p.for_guide ? "Needed in the next guide" : "";
  if (need || q) lines.push(`${need || "For"}${q ? `${need ? ": " : " "}${q}` : ""}.`);
  return lines.join("\n");
}

/** The items the picked characters get, in step order: `shown` says whether the step and the action are theirs (the
 * engine's masks). The same item got again at the same step, or again for the same need (the guide offers it at two
 * steps in a row), counts once. */
export function prepShown(prep: RxpFactsPrep[], shown: (p: RxpFactsPrep) => boolean): RxpFactsPrep[] {
  const seen = new Set<string>();
  return prep.filter((p) => {
    const need = p.for_idx != null ? `s${p.for_idx}` : p.for_guide ? `g${p.for_guide}` : `at${p.idx}`;
    const k = `${p.kind}:${p.item_id}:${p.qty ?? ""}:${need}`;
    if (seen.has(k) || !shown(p)) return false;
    seen.add(k);
    return true;
  }).sort((a, b) => a.idx - b.idx);
}
