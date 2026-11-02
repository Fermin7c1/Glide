// The route on the Guides map: where each step's pin goes, the legs between consecutive stops (the same legs the
// engine's routes command works out, companion/rxp_routes.py), each leg's drawable pieces on one map (the engine's
// walked, flown or jumped segments, else a straight guess), clipped at the map's edge, and the words a leg's card
// says. No DOM: tests/test_rxp_route.py compiles this file (with rxp-geometry.ts) and runs it under Node.
import type { RxpGuideStep, RxpRouteLeg, RxpRouteSegment } from "../shared/ipc.js";
import { between, clipToBox, type Maps, yards, yardsText } from "./rxp-geometry.js";

/** The goto modes RestedXP walks through (its arrow points at them); "dynamic" ones are not part of the route. */
const WALK_MODES = new Set(["wp", "arrive", "path", "noarrow", "lowprio"]);

/** One place a step's route passes: `wi` is the waypoint's index in the step's own waypoints (-1: the step's own
 * x/y); `path` marks RestedXP's hidden path points (its arrow walks through them; they are not where things happen). */
export interface Stop { wi: number; um: number; x: number; y: number; path: boolean }

/** The step's stops in order (its walk-mode waypoints, else its own position), as the routes command reads them. */
export function stopsOf(st: RxpGuideStep): Stop[] {
  const out: Stop[] = [];
  (st.waypoints || []).forEach((w, wi) => {
    if (w.mode && WALK_MODES.has(w.mode) && w.ui_map && w.x != null && w.y != null) {
      out.push({ wi, um: w.ui_map, x: w.x, y: w.y, path: w.mode === "path" });
    }
  });
  if (!out.length && st.ui_map && st.x != null && st.y != null) out.push({ wi: -1, um: st.ui_map, x: st.x, y: st.y, path: false });
  return out;
}

/** Where the step's pin goes: its first stop that isn't a path point (where the step happens), else its first stop,
 * else its own position. */
export function pinStop(st: RxpGuideStep): Stop | null {
  const s = stopsOf(st);
  const real = s.find((p) => !p.path) || s[0];
  if (real) return real;
  return st.ui_map && st.x != null && st.y != null ? { wi: -1, um: st.ui_map, x: st.x, y: st.y, path: false } : null;
}

export type LegKind = "walk" | "fly" | "hs" | "deathskip";
/** A leg between two consecutive stops, as the page plans it: `from`/`to` are guide step numbers (st.idx), `a`/`b`
 * waypoint indexes (-1: the step's own position), `kind` the travel the steps between say (.fly, .hs, .deathskip). */
export interface PlanLeg { key: string; from: number; a: number; to: number; b: number; kind: LegKind; pa: Stop; pb: Stop; pos: number }

export const legKey = (from: number, a: number, to: number, b: number) => `${from}:${a}>${to}:${b}`;

const SPECIAL: LegKind[] = ["fly", "hs", "deathskip"];

/** The legs between consecutive stops of the steps listed (in the page's order), the engine's own plan
 * (rxp_routes.Routes.plan): inside a step from stop to stop, between steps from the last stop of one to the first of
 * the next step with a position, the kind from the verbs of the earlier step and the position-less steps between.
 * `pos` is the position (in `steps`) of the step the leg arrives at. */
export function planLegs(steps: RxpGuideStep[]): PlanLeg[] {
  const legs: PlanLeg[] = [];
  let prev: { idx: number; stop: Stop } | null = null;
  let kind: LegKind = "walk";
  steps.forEach((st, pos) => {
    const verbs = new Set((st.actions || []).map((a) => a.verb));
    const stops = stopsOf(st);
    if (!stops.length) {
      for (const v of SPECIAL) if (verbs.has(v)) kind = v;
      return;
    }
    if (prev) legs.push({ key: legKey(prev.idx, prev.stop.wi, st.idx, stops[0].wi), from: prev.idx, a: prev.stop.wi, to: st.idx,
      b: stops[0].wi, kind, pa: prev.stop, pb: stops[0], pos });
    for (let k = 1; k < stops.length; k++) {
      legs.push({ key: legKey(st.idx, stops[k - 1].wi, st.idx, stops[k].wi), from: st.idx, a: stops[k - 1].wi, to: st.idx,
        b: stops[k].wi, kind: "walk", pa: stops[k - 1], pb: stops[k], pos });
    }
    prev = { idx: st.idx, stop: stops[stops.length - 1] };
    kind = "walk";
    for (const v of SPECIAL) if (verbs.has(v)) kind = v;
  });
  return legs;
}

export type Pt = [number, number];
export type DrawMode = "walk" | "straight" | "fly" | "hearth" | "death" | "boat";
/** One drawable piece of a leg on one map: runs of points inside the map (percent), and where it crosses the
 * edge: `enter` is the point where it comes in from outside, `exit` where it leaves. */
export interface Piece { mode: DrawMode; runs: Pt[][]; enter: Pt | null; exit: Pt | null; seg: RxpRouteSegment | null }

/** The parts of a polyline inside the map (0..100 both ways), with the points where it comes in and goes out. */
export function clipPolyline(pts: Pt[]): { runs: Pt[][]; enter: Pt | null; exit: Pt | null } {
  const runs: Pt[][] = [];
  let cur: Pt[] | null = null;
  let enter: Pt | null = null, exit: Pt | null = null;
  const inside = (p: Pt) => p[0] >= 0 && p[0] <= 100 && p[1] >= 0 && p[1] <= 100;
  if (pts.length === 1) return inside(pts[0]) ? { runs: [[pts[0]]], enter: null, exit: null } : { runs: [], enter: null, exit: null };
  for (let i = 1; i < pts.length; i++) {
    const [x1, y1] = pts[i - 1], [x2, y2] = pts[i];
    const c = clipToBox(x1, y1, x2, y2);
    if (!c) { cur = null; continue; }
    const a: Pt = [c[0], c[1]], b: Pt = [c[2], c[3]];
    const cutA = !inside(pts[i - 1]), cutB = !inside(pts[i]);
    if (!cur || cutA) {
      cur = [a];
      runs.push(cur);
      if (cutA && !enter) enter = a;
    }
    cur.push(b);
    if (cutB) { exit = b; cur = null; }
  }
  return { runs: runs.filter((r) => r.length > 1), enter, exit };
}

/** A jump (hearth, deathskip, boat) drawn as a gentle arc from `a` to `b`, in map percent (bent in screen space). */
export function arcPoints(a: Pt, b: Pt, n = 16): Pt[] {
  const ax = a[0] * 10.02, ay = a[1] * 6.68, bx = b[0] * 10.02, by = b[1] * 6.68;
  const dx = bx - ax, dy = by - ay, len = Math.hypot(dx, dy);
  if (!len) return [a, b];
  const bend = Math.min(len * 0.22, 120);
  const cx = (ax + bx) / 2 + (dy / len) * bend, cy = (ay + by) / 2 - (dx / len) * bend;
  const out: Pt[] = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n, u = 1 - t;
    out.push([(u * u * ax + 2 * u * t * cx + t * t * bx) / 10.02, (u * u * ay + 2 * u * t * cy + t * t * by) / 6.68]);
  }
  return out;
}

const JUMPS = new Set(["hearth", "death", "boat"]);
const KIND_MODE: Record<LegKind, DrawMode> = { walk: "straight", fly: "fly", hs: "hearth", deathskip: "death" };

/** A leg's pieces on map `um`: the engine's segments where it has worked the leg out (walk along the walking map, fly
 * along the flown curve, hearth / deathskip / boat as an arc), else one straight guess between the two stops (its
 * kind from the guide: an arc for a hearth or deathskip). Points outside the map are clipped at its edge. */
export function legPieces(leg: PlanLeg, eng: RxpRouteLeg | undefined, um: number, maps: Maps | null | undefined): Piece[] {
  const out: Piece[] = [];
  const add = (mode: DrawMode, pts: Pt[] | null | undefined, seg: RxpRouteSegment | null) => {
    if (!pts || pts.length < 2) return;
    const line = JUMPS.has(mode) ? arcPoints(pts[0], pts[pts.length - 1]) : pts;
    const c = clipPolyline(line);
    if (c.runs.length) out.push({ mode, runs: c.runs, enter: c.enter, exit: c.exit, seg });
  };
  if (eng && eng.segments && eng.segments.length) {
    for (const seg of eng.segments) add(seg.mode, seg.maps?.[String(um)] as Pt[] | undefined, seg);
    return out;
  }
  const a = between(maps, leg.pa.um, um, leg.pa.x, leg.pa.y), b = between(maps, leg.pb.um, um, leg.pb.x, leg.pb.y);
  if (a && b) add(KIND_MODE[leg.kind], [[a.x, a.y], [b.x, b.y]], null);
  return out;
}

/** "40 s", "3 min", "1 h 05 min". */
export function durText(s: number | null | undefined): string {
  if (s == null || !isFinite(s) || s <= 0) return "";
  if (s < 60) return `${Math.max(5, Math.round(s / 5) * 5)} s`;
  const m = Math.round(s / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

const MODE_VERB: Record<DrawMode, string> = { walk: "Walk", straight: "Walk", fly: "Fly", hearth: "Hearth", death: "Deathskip", boat: "Boat" };

/** What a leg's card says: a headline (how you travel, how far, how long) and lines (the directions, the places
 * passed, how it was worked out). `zoneOf` names a map. */
export function legInfo(leg: PlanLeg, eng: RxpRouteLeg | undefined, maps: Maps | null | undefined,
    zoneOf: (um: number) => string): { head: string; lines: string[]; approx: boolean } {
  const lines: string[] = [];
  if (!eng) {
    const yd = leg.pa.um === leg.pb.um ? yards(maps, leg.pa.um, leg.pb.x - leg.pa.x, leg.pb.y - leg.pa.y) : null;
    const how = KIND_MODE[leg.kind] === "straight" ? "Walk" : MODE_VERB[KIND_MODE[leg.kind]];
    const head = [how, yardsText(yd) || "", leg.pa.um !== leg.pb.um ? `${zoneOf(leg.pa.um)} → ${zoneOf(leg.pb.um)}` : ""].filter(Boolean).join(" · ");
    lines.push("Working out the way… a straight line until then.");
    return { head, lines, approx: true };
  }
  const segs = eng.segments || [];
  const main = segs.find((s) => s.mode === "fly") || segs.find((s) => JUMPS.has(s.mode)) || segs[0];
  const mode = (main?.mode || eng.mode) as DrawMode;
  const head = [MODE_VERB[mode] || "Walk", yardsText(eng.yd) || "", durText(eng.s) ? `about ${durText(eng.s)}` : ""].filter(Boolean).join(" · ");
  for (const s of segs) {
    if (s.mode === "fly") {
      lines.push(`Fly ${s.from ? `from ${s.from} ` : ""}to ${s.to || "the next flight master"}${s.hops && s.hops > 1 ? ` (${s.hops} hops)` : ""}${durText(s.s) ? `, ${durText(s.s)}` : ""}${s.approx ? " (the flight's path is a guess)" : ""}.`);
    } else if (s.mode === "hearth") {
      lines.push(`Hearthstone${s.to ? ` to ${s.to}` : ""}.`);
    } else if (s.mode === "death") {
      lines.push(`Die and come back at the spirit healer${s.to ? ` (${s.to})` : ""}.`);
    } else if (s.mode === "boat") {
      lines.push(`Take the boat or zeppelin${s.to ? ` to ${s.to}` : ""}.`);
    } else if (s.mode === "walk") {
      const dirs = (s.dirs || []).map((d) => `${d.go} ${yardsText(d.yd).replace("~", "")}${d.to ? ` to ${d.to}` : ""}`);
      if (dirs.length) lines.push(`Go ${dirs.join(", then ")}.`);
      if (s.through && s.through.length) lines.push(`Through ${s.through.join(", ")}.`);
      if (s.swim_yd && s.swim_yd > 20) lines.push(`Swims ${yardsText(s.swim_yd).replace("~", "about ")}.`);
      if (s.source) lines.push(`Routed over ${/grid/i.test(s.source) ? "the terrain grid (rougher than the walking map)" : "the walking map"}.`);
    } else if (s.mode === "straight") {
      lines.push(`Straight line, not routed${s.why ? `: ${s.why}` : ""}.`);
    }
  }
  return { head, lines, approx: !!eng.approx };
}

/** The convex outline around a cluster of points (map percent, outlined in screen space), in order. */
export function hull(pts: Pt[]): Pt[] {
  const p = pts.map(([x, y]) => [x * 10.02, y * 6.68] as Pt).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (p.length < 3) return pts.slice();
  const cross = (o: Pt, a: Pt, b: Pt) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower: Pt[] = [], upper: Pt[] = [];
  for (const q of p) { while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], q) <= 0) lower.pop(); lower.push(q); }
  for (let i = p.length - 1; i >= 0; i--) {
    const q = p[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], q) <= 0) upper.pop();
    upper.push(q);
  }
  return lower.slice(0, -1).concat(upper.slice(0, -1)).map(([x, y]) => [x / 10.02, y / 6.68] as Pt);
}

/** Timeline ticks laid out without overlapping: each tick (its x in pixels and its importance) gets row 0 or 1, the
 * first with `gap` pixels free around it; the more important ticks are placed first, and a tick with no room in
 * either row is left out (-1). */
export function layoutTicks(ticks: { x: number; rank: number }[], gap: number): number[] {
  const rows = ticks.map(() => -1);
  const order = ticks.map((_t, i) => i).sort((a, b) => ticks[b].rank - ticks[a].rank || a - b);
  const placed: number[][] = [[], []];
  for (const i of order) {
    for (let r = 0; r < 2; r++) {
      if (placed[r].every((j) => Math.abs(ticks[j].x - ticks[i].x) >= gap)) { placed[r].push(i); rows[i] = r; break; }
    }
  }
  return rows;
}
