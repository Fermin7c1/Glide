// What the Guides page says aloud while it walks a guide with Narrate on: each step's instruction as spoken English
// (RestedXP's markup gone, names instead of ids), and the way to the next step when there is a real way to go: a
// walk of some distance, a flight, a hearth, a deathskip, a boat or a new zone. Routes come from the engine
// (addon:rxpRoutes, companion/rxp_routes.py) when it has worked them out; otherwise a straight bearing and yards from
// the zone's size (rxp-geometry.ts), and with no size known just where to go.
// No DOM: tests/test_rxp_narration.py compiles this file (and rxp-geometry.ts) and runs it under Node.
import type { RxpGuideAction, RxpGuideStep, RxpRouteLeg, RxpRouteSegment } from "../shared/ipc.js";
import { bearingDeg, between, Maps, yards } from "./rxp-geometry.js";

/** A move shorter than this many yards to the next stop is not worth saying (the same camp, the next NPC over). */
export const TRANSITION_MIN_YD = 50;
/** Without the zone's size, a move shorter than this share of the map (percent) is left unsaid. */
export const TRANSITION_MIN_PCT = 2;

/** Names the engine's knowledge base knows, for actions the guide gives only by id. */
export interface NarrationNames {
  quests?: Record<string, string | null | undefined>;
}

const COMPASS = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"];

/** The 8-way compass word for a bearing (degrees clockwise from north). */
export function compassWord(deg: number): string {
  return COMPASS[Math.floor((((deg % 360) + 360) % 360 + 22.5) / 45) % 8];
}

/** The routes' compass words ("northeast") as spoken here ("north-east"). */
function goWord(go: string): string {
  return go.replace(/^(north|south)(east|west)$/, "$1-$2");
}

/** Yards rounded the way a person says them: to 10 under 100, to 50 under 1000, then to 100. */
export function roundYards(yd: number): number {
  if (yd < 100) return Math.max(10, Math.round(yd / 10) * 10);
  if (yd < 1000) return Math.round(yd / 50) * 50;
  return Math.round(yd / 100) * 100;
}

/** "about 300 yards", "about 1,200 yards". */
export function spokenYards(yd: number): string {
  return `about ${roundYards(yd).toLocaleString("en-US")} yards`;
}

/** RestedXP's text as plain words: no colour codes, textures, links, ">>" prefixes or brackets round item names. */
export function cleanRxpText(raw: string | null | undefined): string {
  if (!raw) return "";
  let s = String(raw).replace(/\\n/g, "\n")
    .replace(/\|T[^|]*\|t/g, "").replace(/\|A[^|]*\|a/g, "")
    .replace(/\|H[^|]*\|h(.*?)\|h/g, "$1")
    .replace(/\|c(?:RXP_[A-Z]+_|[0-9a-fA-F]{8}|n[A-Za-z0-9_]+:)/g, "").replace(/\|r/g, "")
    .replace(/npc:(.*?):\d+/g, "$1").replace(/::\d+/g, "")
    .replace(/\[([^\]]*)\]/g, "$1")
    .replace(/\bquest\s*#?\d+\b/gi, "the quest").replace(/#\d+/g, "");
  s = s.split("\n").map((l) => l.replace(/^\s*>+\s*/, "").trim()).filter(Boolean).map(sentence).join(" ");
  return s.replace(/\s+/g, " ").trim();
}

/** One line as a sentence: a full stop unless it already ends in one. */
function sentence(s: string): string {
  const t = s.replace(/\s+/g, " ").trim().replace(/[,;:]+$/, "");
  return !t ? "" : /[.!?]$/.test(t) ? t : `${t}.`;
}

const questName = (a: RxpGuideAction, names?: NarrationNames | null) =>
  a.quest_name || (a.quest_id != null ? names?.quests?.[String(a.quest_id)] : null) || null;
const unitName = (a: RxpGuideAction) => (a.units && a.units.find(Boolean)) || a.entity_name || null;

/** What an action with no text of its own says, from its verb and the names known; "" when only an id is known. */
function verbLine(a: RxpGuideAction, names?: NarrationNames | null): string {
  const q = questName(a, names);
  const qty = a.qty && a.qty > 1 ? `${a.qty} ` : "";
  switch (a.verb) {
    case "accept": return q ? `Accept ${q}` : "";
    case "turnin": return q ? `Turn in ${q}` : "";
    case "complete": return q ? `Finish ${q}` : "";
    case "collect": return a.entity_name ? `Collect ${qty}${a.entity_name}` : "";
    case "buy": return a.entity_name ? `Buy ${qty}${a.entity_name}` : "";
    case "train": return a.entity_name ? `Train ${a.entity_name}` : "";
    case "fly": return a.location ? `Fly to ${a.location}` : "Take a flight";
    case "fp": return a.location ? `Get the flight path at ${a.location}` : "Get the flight path";
    case "hs": return a.location ? `Hearth to ${a.location}` : "Use your hearthstone";
    case "home": return a.location ? `Set your hearthstone in ${a.location}` : "Set your hearthstone here";
    case "deathskip": return "Die and run back from the spirit healer";
    case "xp": return a.level ? `Grind to level ${a.level}` : "";
    case "vendor": return "Sell your junk";
    default: return "";
  }
}

/** The step's instruction as spoken English: its text lines (each once), then what its verbs add that the text
 * doesn't already say; with neither, whom to talk to or what to kill. `actions` are the ones the picked character
 * gets (the page filters them); "" when there's nothing to say (a bare waypoint). */
export function stepLine(st: Pick<RxpGuideStep, "actions" | "text">, actions?: RxpGuideAction[] | null,
    names?: NarrationNames | null): string {
  const acts = actions || st.actions || [];
  const parts: string[] = [];
  const seen = new Set<string>();
  // a line is said once; so is a line starting as one already said (RestedXP's variants of one instruction for
  // different characters: "Kill Mottled Boars. Loot them until you have 35 copper..." and "...60 copper...")
  const add = (t: string) => {
    const s = sentence(t);
    const k = s.toLowerCase(), head = k.split(/(?<=[.!?])\s/)[0];
    if (s && !seen.has(k) && !seen.has(`head:${head}`)) { seen.add(k); seen.add(`head:${head}`); parts.push(s); }
  };
  for (const a of acts) {
    const t = cleanRxpText(a.raw_text || a.text);
    if (t) add(t);
  }
  const said = () => parts.join(" ").toLowerCase();
  for (const a of acts) {
    if (a.raw_text || a.text) continue;
    const t = verbLine(a, names);
    if (!t) continue;
    // a name the text already says ("Turn in Sarkoth" after "Talk to Hana'zua. Turn in Sarkoth.") isn't said twice
    const key = (questName(a, names) || a.entity_name || a.location || "").toLowerCase();
    if (key && said().includes(key)) continue;
    add(t);
  }
  if (!parts.length) {
    const talk = acts.find((a) => (a.verb === "target" || a.verb === "unitscan") && unitName(a));
    const kill = acts.find((a) => (a.verb === "mob" || a.verb === "kill") && unitName(a));
    if (talk) add(`Talk to ${unitName(talk)}`);
    else if (kill) add(`Kill ${unitName(kill)}`);
    else if (!acts.length && st.text) add(cleanRxpText(st.text));
  }
  return parts.join(" ");
}

/** Whom the step goes to: its first NPC to talk to (a target, else a friendly name in its text), else null. */
export function stepWho(st: Pick<RxpGuideStep, "actions">, actions?: RxpGuideAction[] | null): string | null {
  const acts = actions || st.actions || [];
  const t = acts.find((a) => a.verb === "target" && unitName(a));
  if (t) return unitName(t);
  for (const a of acts) {
    const m = /\|cRXP_FRIENDLY_(.*?)\|r/.exec(a.raw_text || "");
    if (m && m[1].trim()) return cleanRxpText(m[1]).replace(/\.$/, "");
  }
  return null;
}

/** How a step leaves for the next: its .fly / .hs / .deathskip, with where it goes when the guide says. */
export function stepVia(st: Pick<RxpGuideStep, "actions">, actions?: RxpGuideAction[] | null):
    { mode: "fly" | "hearth" | "death"; to: string | null } | null {
  for (const a of actions || st.actions || []) {
    if (a.verb === "fly") return { mode: "fly", to: a.location || null };
    if (a.verb === "hs") return { mode: "hearth", to: a.location || null };
    if (a.verb === "deathskip") return { mode: "death", to: null };
  }
  return null;
}

/** A stop on a map: where one step ends or the next begins, and whom it's at. */
export interface NarrationStop {
  ui_map: number | null;
  x: number | null;
  y: number | null;
  zone?: string | null;
  who?: string | null;
}

export interface TransitionInput {
  from: NarrationStop;
  to: NarrationStop;
  /** the engine's legs from `from` to `to`, in order (empty or null: not worked out yet) */
  legs?: RxpRouteLeg[] | null;
  /** how the step (or the steps between) leaves: used when there's no route */
  via?: { mode: "fly" | "hearth" | "death"; to: string | null } | null;
  /** the step's own instruction already said the flight, hearth or deathskip: don't say it again */
  viaSaid?: boolean;
  maps?: Maps | null;
}

const JUMPS = new Set(["fly", "hearth", "death", "boat"]);

/** The jump's words: "Take the flight to Orgrimmar", "Hearth to Razor Hill", "Die and run back from the graveyard". */
function jumpLine(mode: string, to: string | null): string {
  if (mode === "fly") return to ? `Take the flight to ${to}` : "Take the flight";
  if (mode === "hearth") return to ? `Hearth to ${to}` : "Use your hearthstone";
  if (mode === "death") return "Die and run back from the graveyard";
  if (mode === "boat") return to ? `Take the boat to ${to}` : "Take the boat";
  return "";
}

/** Directions as a person gives them: the way that covers most of it, or the first two turns ("north, then east"). */
function directionsOf(dirs: { go: string; yd: number }[]): string {
  const merged: { go: string; yd: number }[] = [];
  for (const d of dirs) {
    if (!d.go) continue;
    const last = merged[merged.length - 1];
    if (last && last.go === d.go) last.yd += d.yd;
    else merged.push({ go: d.go, yd: d.yd });
  }
  if (!merged.length) return "";
  const total = merged.reduce((k, d) => k + d.yd, 0);
  const main = merged.reduce((a, b) => (b.yd > a.yd ? b : a));
  if (merged.length === 1 || main.yd >= total * 0.6) return goWord(main.go);
  // the turns long enough to notice, the first two
  const big = merged.filter((d) => d.yd >= total * 0.2).slice(0, 2);
  return big.length === 2 ? `${goWord(big[0].go)}, then ${goWord(big[1].go)}` : goWord(main.go);
}

/** "Head north-east about 300 yards, past Razor Hill, to Kaltunk": one walking stretch in words. */
function walkLine(go: string, yd: number | null, past: string | null, dest: string | null, zone: string | null): string {
  let s = go ? `Head ${go}` : "Head";
  if (yd != null) s += ` ${spokenYards(yd)}`;
  if (past) s += `, past ${past}`;
  if (zone) s += `${past || yd != null ? "," : ""} into ${zone}`;
  if (dest) s += `${past || zone ? "," : ""} to ${dest}`;
  if (!go && yd == null && !past && !zone) s = dest ? `Make your way to ${dest}` : "";
  return s;
}

/** The walk part of a worked-out leg in words, "" when it is too short to mention. */
function legWalkLine(segs: RxpRouteSegment[], dest: string | null, zone: string | null): string {
  const walks = segs.filter((g) => g.mode === "walk" || g.mode === "straight");
  const yd = walks.reduce((k, g) => k + (g.yd || 0), 0);
  if (yd <= TRANSITION_MIN_YD && !zone) return "";
  const dirs = walks.flatMap((g) => g.dirs || []);
  const through = walks.flatMap((g) => g.through || []).filter(Boolean);
  // a landmark passed on the way: not where it starts or ends, not the destination or the zone itself
  const ends = new Set([through[0], through[through.length - 1], dest, zone, dirs[dirs.length - 1]?.to]
    .filter(Boolean).map((s) => String(s).toLowerCase()));
  const past = through.slice(1, -1).find((a) => !ends.has(a.toLowerCase())) || null;
  const where = dest || dirs[dirs.length - 1]?.to || null;
  return walkLine(directionsOf(dirs), yd > TRANSITION_MIN_YD ? yd : null, past, where, zone);
}

/** What to say for the way from one step to the next, "" when it isn't worth saying: the same spot or NPC, a stop
 * without a position, a few steps away. Says a flight, hearth, deathskip or boat, a new zone, and walks of more than
 * TRANSITION_MIN_YD yards: with the engine's route its directions, yards and a landmark, else a straight bearing and
 * yards (no number without the zone's size). */
export function transitionLine(t: TransitionInput): string {
  const { from, to } = t;
  const sameWho = !!(from.who && to.who && from.who.toLowerCase() === to.who.toLowerCase());
  const zone = to.zone && from.zone && to.zone !== from.zone ? to.zone : null;
  const dest = to.who || null;

  const legs = (t.legs || []).filter(Boolean);
  if (legs.length) {
    const segs = legs.flatMap((l) => l.segments || []);
    const jump = segs.find((g) => JUMPS.has(g.mode));
    if (jump) {
      const j = t.viaSaid ? "" : jumpLine(jump.mode, jump.to || t.via?.to || (jump.mode === "death" ? null : to.zone) || null);
      // the walk after it lands, when there's one worth saying
      const w = legWalkLine(segs.slice(segs.indexOf(jump) + 1), dest, null);
      return finish([j && w ? `${j}, then ${w[0].toLowerCase()}${w.slice(1)}` : j || w]);
    }
    if (sameWho) return "";
    return finish([legWalkLine(segs, dest, zone)]);
  }

  // no route worked out: the guide's own .fly / .hs / .deathskip, else a straight line
  if (t.via && !t.viaSaid) return finish([jumpLine(t.via.mode, t.via.to)]);
  if (t.via) return "";
  if (sameWho) return "";
  if (from.x == null || from.y == null || to.x == null || to.y == null || from.ui_map == null || to.ui_map == null) {
    return zone ? finish([`Head into ${zone}${dest ? `, to ${dest}` : ""}`]) : "";
  }
  // the next stop in the map of the step before (or the other way round), for a bearing and yards on one map
  let um = from.ui_map, dx: number | null = null, dy: number | null = null;
  const p = between(t.maps, to.ui_map, from.ui_map, to.x, to.y);
  if (p) { dx = p.x - from.x; dy = p.y - from.y; }
  else {
    const q = between(t.maps, from.ui_map, to.ui_map, from.x, from.y);
    if (q) { um = to.ui_map; dx = to.x - q.x; dy = to.y - q.y; }
  }
  if (dx == null || dy == null) return zone ? finish([`Head into ${zone}${dest ? `, to ${dest}` : ""}`]) : "";
  const yd = yards(t.maps, um, dx, dy);
  if (yd != null ? yd <= TRANSITION_MIN_YD && !zone : Math.hypot(dx, dy) <= TRANSITION_MIN_PCT && !zone) return "";
  const go = compassWord(bearingDeg(t.maps, um, dx, dy));
  return finish([walkLine(go, yd != null && yd > TRANSITION_MIN_YD ? yd : null, null, dest, zone)]);
}

function finish(parts: string[]): string {
  return parts.filter(Boolean).map(sentence).join(" ");
}
