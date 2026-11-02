// Quest chains on the Guides page, DOM-free (tested under Node by tests/test_rxp_quests.py): each quest's steps in
// the guide, the chain a quest belongs to (the knowledge base's follow-ups, else the quest alone), the chain's steps
// in step order, where the chain leaves the map shown, and what skipping a quest loses in the coach's own words.
import type { RxpFactsQuest, RxpGuideFacts, RxpGuideStep } from "../shared/ipc.js";

export type QuestRole = "accept" | "objective" | "turnin";
export type Quests = Record<string, RxpFactsQuest>;

const ACCEPT = new Set(["accept", "acceptmultiple", "daily"]);
const TURNIN = new Set(["turnin", "turninmultiple", "dailyturnin"]);
const OBJECTIVE = new Set(["complete", "collect", "buy", "buyAll", "buyUntilBroke", "addquestitem", "questitemcount",
  "questgoto", "questwaypoint"]);

/** Each quest's accept / objective / turn-in steps read from the guide itself: what the page knows when the engine
 * hasn't answered with the facts (knowledge/api/explorer.py quest_steps is the same rule). */
export function localQuests(steps: RxpGuideStep[]): Quests {
  const out: Quests = {};
  for (const st of steps) {
    for (const a of st.actions || []) {
      const role = ACCEPT.has(a.verb) ? "accept" : TURNIN.has(a.verb) ? "turnin" : OBJECTIVE.has(a.verb) ? "objective" : null;
      if (!role || !a.quest_id) continue;
      for (const qid of a.quest_ids?.length ? a.quest_ids : [a.quest_id]) {
        const q = (out[String(qid)] ||= { steps: { accept: [], objective: [], turnin: [], gate: [] } });
        if (!q.steps[role].includes(st.idx)) q.steps[role].push(st.idx);
      }
    }
  }
  return out;
}

/** The guide's quests: the facts' (with the knowledge base's names and follow-ups), else read from the guide. */
export function questsOf(facts: RxpGuideFacts | null, steps: RxpGuideStep[]): Quests {
  return facts?.quests && Object.keys(facts.quests).length ? facts.quests : localQuests(steps);
}

/** The chain quest `qid` belongs to, in order, as far as this guide holds it: back through the quests leading to it,
 * forward through the follow-ups (quest.next_quest). Without the knowledge base, the quest alone. */
export function chainOf(quests: Quests, qid: number): number[] {
  if (!quests[String(qid)]) return [qid];
  const seen = new Set<number>([qid]);
  const back: number[] = [];
  let cur = qid;
  for (;;) {
    const prev = (quests[String(cur)]?.prev || []).filter((p) => quests[String(p)] && !seen.has(p));
    // several quests can name this one as their follow-up: the way in is the one the guide turns in before it picks
    // this one up (the latest such turn-in), else any
    const takeAt = Math.min(...(quests[String(cur)]?.steps.accept || [Infinity]));
    const turnedIn = (x: number) => Math.min(...(quests[String(x)]?.steps.turnin || []).filter((t) => t <= takeAt), Infinity);
    const before = prev.filter((x) => turnedIn(x) !== Infinity).sort((a, b) => turnedIn(b) - turnedIn(a));
    const p = before[0] ?? prev[0];
    if (p == null) break;
    seen.add(p);
    back.unshift(p);
    cur = p;
  }
  const fwd: number[] = [];
  cur = qid;
  for (;;) {
    const n = quests[String(cur)]?.next;
    if (n == null || seen.has(n) || !quests[String(n)]) break;
    seen.add(n);
    fwd.push(n);
    cur = n;
  }
  return [...back, qid, ...fwd];
}

/** Whether the chain goes on after this guide: its last quest has a follow-up the guide doesn't hold. */
export function continuesAfter(quests: Quests, chain: number[]): boolean {
  const last = quests[String(chain[chain.length - 1])];
  return !!last?.next && !quests[String(last.next)];
}

export interface ChainStop { src: number; quest: number; role: QuestRole }

/** The chain's steps in step order, one per step (a step that turns one in and accepts the next says the turn-in). */
export function chainStops(quests: Quests, chain: number[]): ChainStop[] {
  const by = new Map<number, ChainStop>();
  const rank: Record<QuestRole, number> = { turnin: 3, accept: 2, objective: 1 };
  for (const q of chain) {
    const s = quests[String(q)]?.steps;
    if (!s) continue;
    for (const role of ["accept", "objective", "turnin"] as QuestRole[]) {
      for (const src of s[role] || []) {
        const old = by.get(src);
        if (!old || rank[role] > rank[old.role]) by.set(src, { src, quest: q, role });
      }
    }
  }
  return [...by.values()].sort((a, b) => a.src - b.src);
}

/** Where the chain crosses between maps, seen from map `um`: at each of its steps on this map, the zone the chain
 * came from or goes on to (`at` the step there, `um` the other map). Steps are given in step order with their map. */
export function chainCrossings(stops: { src: number; um: number | null }[], um: number):
  { at: number; dir: "from" | "to"; um: number }[] {
  const out: { at: number; dir: "from" | "to"; um: number }[] = [];
  for (let i = 0; i < stops.length; i++) {
    const s = stops[i];
    if (s.um !== um) continue;
    const prev = stops[i - 1], next = stops[i + 1];
    if (prev && prev.um != null && prev.um !== um) out.push({ at: s.src, dir: "from", um: prev.um });
    if (next && next.um != null && next.um !== um) out.push({ at: s.src, dir: "to", um: next.um });
  }
  return out;
}

const listWords = (xs: string[]) => xs.length <= 1 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;

/** What skipping quest `qid` loses, by the coach's own rule (the facts' lost_if_skipped, over this guide and the
 * next), or null when nothing can be said: no knowledge base, or the quest isn't picked up in this guide. `num`
 * gives RestedXP's number of a step for the picked character (null when that character doesn't get the step). */
export function skipLine(quests: Quests, qid: number, num: (src: number) => string | null): string | null {
  const q = quests[String(qid)];
  if (!q || !q.lost_if_skipped || !q.steps.accept.length) return null;
  const lost = q.lost_if_skipped;
  if (!lost.length) return "Skipping it loses nothing later in this guide or the next.";
  const names = lost.map((x) => {
    const name = x.name || `quest #${x.id}`;
    const at = x.accept != null ? num(x.accept) : null;
    return x.next_guide ? `${name} (next guide)` : at ? `${name} (step ${at})` : name;
  });
  const shown = names.length > 4 ? [...names.slice(0, 3), `${names.length - 3} more`] : names;
  return `Skip it and you lose ${listWords(shown)}.`;
}
