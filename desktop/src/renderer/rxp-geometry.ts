// Map geometry for the Guides page: yards and compass bearings between map points, and moving a point from one map
// to its continent or to a neighbouring zone, from the sizes and rectangles the engine reads from the knowledge base
// (companion/rxp_explorer.py map_geometry). Map points are in percent of the map (0..100 both ways), as RestedXP
// writes them. No DOM: tests/test_rxp_geometry.py compiles this file alone and runs it under Node.

/** One map as the engine describes it: its size in yards, its parent map (the continent), and where its corners
 * (0, 0) and (100, 100) fall on the parent, in the parent's percent. Unknown parts are null. */
export interface MapGeo {
  name?: string | null;
  parent?: number | null;
  w_yd?: number | null;
  h_yd?: number | null;
  rect?: [number, number, number, number] | null;
}
export type Maps = Record<string, MapGeo | undefined>;

// every map image the game draws is 1002 x 668: a percent of x is 1.5 times a percent of y on screen
const PX_W = 10.02, PX_H = 6.68;

function geo(maps: Maps | null | undefined, um: number | null | undefined): MapGeo | null {
  return maps && um != null ? maps[String(um)] || null : null;
}

/** Yards between two points of a map that are (dx, dy) percent apart, or null when the map's size isn't known
 * (no knowledge base yet): better no figure than a guessed one. */
export function yards(maps: Maps | null | undefined, um: number | null | undefined, dx: number, dy: number): number | null {
  const g = geo(maps, um);
  if (!g || !g.w_yd || !g.h_yd) return null;
  return Math.hypot((dx * g.w_yd) / 100, (dy * g.h_yd) / 100);
}

/** The compass bearing (degrees clockwise from north, the top of the map) of a move of (dx, dy) percent: in yards
 * when the map's size is known, else in the map image's own pixels (also square on the ground). */
export function bearingDeg(maps: Maps | null | undefined, um: number | null | undefined, dx: number, dy: number): number {
  const g = geo(maps, um);
  const kx = g && g.w_yd && g.h_yd ? g.w_yd : PX_W, ky = g && g.w_yd && g.h_yd ? g.h_yd : PX_H;
  const deg = (Math.atan2(dx * kx, -dy * ky) * 180) / Math.PI;
  return (deg + 360) % 360;
}

/** A point of map `um` on its parent map (the continent), or null when the map has no rectangle there. */
export function toParent(maps: Maps | null | undefined, um: number | null | undefined, x: number, y: number):
    { um: number; x: number; y: number } | null {
  const g = geo(maps, um);
  if (!g || g.parent == null || !g.rect) return null;
  const [x0, y0, x1, y1] = g.rect;
  return { um: g.parent, x: x0 + (x / 100) * (x1 - x0), y: y0 + (y / 100) * (y1 - y0) };
}

/** A point of the parent map in map `um`'s percent (it may fall outside 0..100), or null. */
export function fromParent(maps: Maps | null | undefined, um: number | null | undefined, x: number, y: number):
    { x: number; y: number } | null {
  const g = geo(maps, um);
  if (!g || !g.rect) return null;
  const [x0, y0, x1, y1] = g.rect;
  if (x1 === x0 || y1 === y0) return null;
  return { x: ((x - x0) / (x1 - x0)) * 100, y: ((y - y0) / (y1 - y0)) * 100 };
}

/** A point of map `fromUm` in map `toUm`'s percent, through the parent map both lie on; null when they share none
 * (another continent, a dungeon, no knowledge base). The same map gives the point back. */
export function between(maps: Maps | null | undefined, fromUm: number | null | undefined, toUm: number | null | undefined,
    x: number, y: number): { x: number; y: number } | null {
  if (fromUm == null || toUm == null) return null;
  if (fromUm === toUm) return { x, y };
  const a = geo(maps, fromUm), b = geo(maps, toUm);
  if (b && b.parent === fromUm) return fromParent(maps, toUm, x, y);        // the continent to one of its zones
  const up = toParent(maps, fromUm, x, y);
  if (!up) return null;
  if (up.um === toUm) return { x: up.x, y: up.y };                           // a zone to its continent
  if (!a || !b || a.parent !== b.parent) return null;
  return fromParent(maps, toUm, up.x, up.y);
}

/** The part of the segment (x1, y1)-(x2, y2) inside the map (0..100 both ways), or null when it misses it. */
export function clipToBox(x1: number, y1: number, x2: number, y2: number): [number, number, number, number] | null {
  let t0 = 0, t1 = 1;
  const dx = x2 - x1, dy = y2 - y1;
  const edges: [number, number][] = [[-dx, x1], [dx, 100 - x1], [-dy, y1], [dy, 100 - y1]];
  for (const [p, q] of edges) {
    if (p === 0) {
      if (q < 0) return null;
    } else {
      const t = q / p;
      if (p < 0) { if (t > t1) return null; if (t > t0) t0 = t; }
      else { if (t < t0) return null; if (t < t1) t1 = t; }
    }
  }
  return [x1 + t0 * dx, y1 + t0 * dy, x1 + t1 * dx, y1 + t1 * dy];
}

/** "~420 yd" for a distance in yards (rounded to 10 above 100), "" when unknown. */
export function yardsText(yd: number | null): string {
  if (yd == null || !isFinite(yd)) return "";
  const r = yd >= 100 ? Math.round(yd / 10) * 10 : Math.round(yd);
  return `~${r} yd`;
}
