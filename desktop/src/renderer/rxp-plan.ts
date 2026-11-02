// Time, XP and hazards on the Guides page, DOM-free (tested under Node by tests/test_rxp_plan.py): the coach's plan
// for the picked class (the facts' step_brief figures) read as moments in play, never as a table: how long and how
// much XP between two steps, how far to the next level ("45 min to 23"), which stretches of the guide are dangerous,
// and a hazard said in a line ("Hogger, level 11 elite: patrols the road").
import type { RxpFactsHazard, RxpFactsPlanStep } from "../shared/ipc.js";

export type PlanSteps = Record<string, RxpFactsPlanStep>;

/** A duration said roughly: "under a minute", "8 min", "1 h 10 min", "3 h". */
export function roughDur(s: number): string {
  if (!(s >= 60)) return "under a minute";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60), r = m % 60;
  return r ? `${h} h ${String(r).padStart(2, "0")} min` : `${h} h`;
}

const level = (p: RxpFactsPlanStep | undefined, after = true) => (p ? (after ? p.lvl1 ?? p.lvl0 : p.lvl0 ?? p.lvl1) : null);

export interface Span { s: number; xp: number; lvlA: number | null; lvlB: number | null; steps: number }

/** From the step at list position `a` to the one at `b` (the steps listed, `order` their st.idx): the time and XP of
 * the steps after a up to b, and the level on leaving a and on finishing b. Null when the plan knows none of them. */
export function span(plan: PlanSteps, order: number[], a: number, b: number): Span | null {
  if (a === b) return null;
  const lo = Math.min(a, b), hi = Math.max(a, b);
  let s = 0, xp = 0, known = 0;
  for (let i = lo + 1; i <= hi; i++) {
    const p = plan[String(order[i])];
    if (!p) continue;
    known++;
    s += p.time_s || 0;
    xp += p.xp_now || 0;
  }
  if (!known) return null;
  return { s, xp, lvlA: level(plan[String(order[lo])]), lvlB: level(plan[String(order[hi])]), steps: hi - lo };
}

/** The next level-up after the step at list position `pos`: the level, where (list position) and how long until. */
export function nextDing(plan: PlanSteps, order: number[], pos: number): { level: number; pos: number; s: number } | null {
  const now = level(plan[String(order[pos])]);
  if (now == null) return null;
  const want = Math.floor(now) + 1;
  let s = 0;
  for (let i = pos + 1; i < order.length; i++) {
    const p = plan[String(order[i])];
    if (!p) continue;
    s += p.time_s || 0;
    const l = level(p);
    if (l != null && l >= want) return { level: want, pos: i, s };
  }
  return null;
}

/** "45 min to 23": the time from this step to the next level, or "" when the guide doesn't reach it. */
export function dingText(d: { level: number; s: number } | null): string {
  return d ? `${roughDur(d.s)} to ${d.level}` : "";
}

const grouped = (n: number) => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");

/** What lies between two steps, in a few words: "+1 h 10 min · level 21 → 23", or with no level gained
 * "+12 min · +1,800 XP" ("−" when scrubbing back; "under a minute" for a short one). */
export function spanText(sp: Span | null, back = false): string {
  if (!sp) return "";
  const sign = back ? "−" : "+";
  const a = sp.lvlA != null ? Math.floor(sp.lvlA) : null, b = sp.lvlB != null ? Math.floor(sp.lvlB) : null;
  const t = sp.s < 60 ? "under a minute" : `${sign}${roughDur(sp.s)}`;
  if (a != null && b != null && a !== b) return `${t} · level ${back ? b : a} → ${back ? a : b}`;
  return sp.xp > 0 ? `${t} · ${sign}${grouped(sp.xp)} XP` : t;
}

/** A hazard in a line: "Hogger, level 11 elite: patrols the road, calls for help". */
export function hazardLine(h: RxpFactsHazard): string {
  const notes = (h.note || "").split(" / ").map((x) => x.trim()).filter(Boolean);
  const rank = notes.find((x) => /^(elite|rare elite|rare|boss)$/i.test(x));
  const rest = notes.filter((x) => x !== rank).map((x) => x[0].toLowerCase() + x.slice(1));
  const who = `${h.name || "Something dangerous"}${h.level ? `, level ${h.level}` : ""}${rank ? ` ${rank.toLowerCase()}` : h.elite && h.why !== "quest" ? " elite" : ""}`;
  return rest.length ? `${who}: ${rest.join(", ")}` : who;
}

/** Where the hazard is, as the player meets it: on the way, the step's own target, or the quest itself. */
export function hazardWhere(h: RxpFactsHazard): string {
  if (h.why === "kill") return "The step's own target";
  if (h.why === "quest") return "The quest itself";
  return h.dist_yd != null ? `Within ${Math.round(h.dist_yd)} yd of the way` : "Near the way";
}

/** Whether the step is a dangerous one: a hazard there, or the plan's risk at or over `minRisk`. */
export function isDangerous(src: number, hazards: Record<string, RxpFactsHazard[]>, plan: PlanSteps | null, minRisk = 0.6): boolean {
  return (hazards[String(src)] || []).length > 0 || (plan?.[String(src)]?.risk ?? 0) >= minRisk;
}

/** The dangerous stretches of the listed steps: runs of list positions (a gap of one safe step joins two runs), each
 * with the hazards met there, the most frequent first. */
export function dangerRuns(order: number[], hazards: Record<string, RxpFactsHazard[]>, plan: PlanSteps | null, minRisk = 0.6):
  { from: number; to: number; hz: RxpFactsHazard[] }[] {
  const runs: { from: number; to: number; hz: RxpFactsHazard[] }[] = [];
  order.forEach((src, i) => {
    if (!isDangerous(src, hazards, plan, minRisk)) return;
    const last = runs[runs.length - 1];
    if (last && i - last.to <= 2) last.to = i; else runs.push({ from: i, to: i, hz: [] });
  });
  for (const r of runs) {
    const count = new Map<string, { h: RxpFactsHazard; n: number }>();
    for (let i = r.from; i <= r.to; i++) {
      for (const h of hazards[String(order[i])] || []) {
        const k = `${h.creature_id ?? ""}:${h.name ?? ""}:${h.quest_id ?? ""}`;
        const c = count.get(k);
        if (c) c.n++; else count.set(k, { h, n: 1 });
      }
    }
    r.hz = [...count.values()].sort((a, b) => b.n - a.n).map((c) => c.h);
  }
  return runs;
}
