// Interactive RestedXP Guide & Map Browser: browse imported guides, filter by race/class/mode as RestedXP does
// in-game, scrub through a visual step timeline, and walk the route over the zone map with clickable waypoints,
// directional arrows, objectives, and guide commentary.
import type { RxpGuideAction, RxpGuideDetail, RxpGuideStep, RxpGuideSummary, RxpImport } from "../shared/ipc.js";
import { call, quiet } from "./bridge.js";
import { $, esc } from "./dom.js";
import { currentPage, show } from "./nav.js";
import { live } from "./state.js";
import { conditionApplies } from "./rxp-conditions.js";

const MAP_W = 1002;
const MAP_H = 668;
const mapX = (v: number) => +(v * 10.02).toFixed(1);
const mapY = (v: number) => +(v * 6.68).toFixed(1);

// RestedXP's in-game guideTextColors (Themes.lua)
const RXP_COLORS: Record<string, string> = {
  RXP_FRIENDLY_: "#00ff25",
  RXP_ENEMY_: "#ff5722",
  RXP_LOOT_: "#00bcd4",
  RXP_WARN_: "#fcdc00",
  RXP_PICK_: "#db2eef",
  RXP_BUY_: "#2ecc40",
};

interface CatalogEntry extends RxpGuideSummary {
  packId: string;
}

interface VisibleStep {
  step: RxpGuideStep;
  rxpNum: number;           // 1-based step number as RestedXP numbers it
  dimmed: boolean;          // shown only because "Show all class/race steps" is on
  kind: "accept" | "turnin" | "combat" | "travel" | "hearth" | "death" | "step";
  x: number | null;
  y: number | null;
  ui_map: number | null;
  zone: string | null;
}

const cache = new Map<string, RxpGuideDetail>();
let catalog: CatalogEntry[] = [];
let currentGuide: RxpGuideDetail | null = null;
let visibleSteps: VisibleStep[] = [];
let activeIdx = 0;          // 0-based index into visibleSteps
let activeWpIdx = 0;        // sub-waypoint index within the active step
let activeMapId: number | null = null;
let pinnedStepIdx: number | null = null;  // clicked element on the map
let playTimer: number | null = null;
let playMs = 1600;
const checkedObjs = new Set<string>();

// Filter state (matches RestedXP's in-game player & profile checks)
const filter = {
  group: "",
  search: "",
  stepSearch: "",
  race: "",                 // the race picker's value: "Human", ..., or "Skyborne@Alliance" / "Skyborne@Horde"
  cls: "",
  ssf: false,
  groupQuests: true,
  hardcore: false,
  showAllCond: false,
  showDone: false,          // the pins of the steps before the current one ("Done steps")
  follow: false,            // keep the current step in the middle of the map ("Follow step")
};

// ---- the map's zoom and pan: the layer holding the image and the drawing, scaled and moved together -------------
const MAX_ZOOM = 8;
const view = { z: 1, fx: 0, fy: 0, map: null as number | null };   // fx, fy: the layer's offset as a share of the stage

function stageSize(): { w: number; h: number } {
  const r = $("rxpb-map-stage").getBoundingClientRect();
  return { w: r.width || 1, h: r.height || 1 };
}

/** Put the layer where `view` says, kept over the stage (no empty edge), and keep pins at their size on screen. */
function applyView(): void {
  view.z = Math.min(MAX_ZOOM, Math.max(1, view.z));
  view.fx = Math.min(0, Math.max(1 - view.z, view.fx));
  view.fy = Math.min(0, Math.max(1 - view.z, view.fy));
  const { w, h } = stageSize();
  $("rxpb-map-layer").style.transform = `translate(${(view.fx * w).toFixed(1)}px, ${(view.fy * h).toFixed(1)}px) scale(${view.z})`;
  $("rxpb-map-svg").style.setProperty("--inv", String(1 / view.z));
}

/** Zoom by `factor` keeping the stage point (sx, sy) (pixels; default the middle) where it is. */
function zoomAt(factor: number, sx?: number, sy?: number): void {
  const { w, h } = stageSize();
  const px = sx ?? w / 2, py = sy ?? h / 2;
  const z0 = view.z, z1 = Math.min(MAX_ZOOM, Math.max(1, z0 * factor));
  view.fx = (px - (px - view.fx * w) * (z1 / z0)) / w;
  view.fy = (py - (py - view.fy * h) * (z1 / z0)) / h;
  view.z = z1;
  applyView();
}

/** The whole zone again. */
function resetView(): void {
  view.z = 1;
  view.fx = view.fy = 0;
  applyView();
}

/** Put map point (x, y) (percent) in the middle, zooming in first when the whole zone is shown. */
function centerOn(x: number, y: number): void {
  if (view.z < 2) view.z = 2.5;
  view.fx = 0.5 - (x / 100) * view.z;
  view.fy = 0.5 - (y / 100) * view.z;
  applyView();
}

function followStep(): void {
  const cur = visibleSteps[activeIdx];
  if (filter.follow && cur && cur.ui_map === activeMapId && cur.step.x != null && cur.step.y != null) centerOn(cur.step.x, cur.step.y);
}

/** Evaluate RestedXP's `<<` condition string against the picked race (with its faction) and class. */
function condApplies(raw: string | null | undefined): boolean {
  return conditionApplies(raw, filter.race, filter.cls);
}

function stepFlagsShown(flags: Record<string, unknown>): boolean {
  const on = (k: string) => flags[k] !== undefined && flags[k] !== false && flags[k] !== null;
  if ((on("softcore") && filter.hardcore) || (on("hardcore") && !filter.hardcore)) return false;
  if ((on("ssf") && !filter.ssf) || (on("ah") && filter.ssf)) return false;
  if ((on("group") && !filter.groupQuests) || (on("solo") && filter.groupQuests)) return false;
  if (on("disabled") || on("questguide")) return false;
  return true;
}

/** Convert WoW / RestedXP markup (`|cRXP_WARN_...|r`, `|cffRRGGBB...|r`, `|T...|t`, `\n`) into safe HTML. */
function formatRxpText(raw: string | null | undefined): string {
  if (!raw) return "";
  const s = String(raw).replace(/\\n/g, "\n").replace(/\|T[^|]*\|t/g, "").replace(/\|A[^|]*\|a/g, "")
    .replace(/\|H[^|]*\|h(.*?)\|h/g, "$1").replace(/npc:(.*?):\d+/g, "$1").replace(/::\d+/g, "");
  const out: string[] = [];
  let openSpans = 0;
  const re = /\|c(RXP_[A-Z]+_|[0-9a-fA-F]{8}|n[A-Za-z0-9_]+:)|\|r/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(s)) !== null) {
    if (m.index > last) out.push(esc(s.slice(last, m.index)));
    if (m[0] === "|r") {
      if (openSpans > 0) { out.push("</span>"); openSpans--; }
    } else {
      const code = m[1];
      let color = RXP_COLORS[code] || "#f2c24f";
      if (/^[0-9a-fA-F]{8}$/.test(code)) color = "#" + code.slice(2);
      out.push(`<span style="color:${color};font-weight:550">`);
      openSpans++;
    }
    last = re.lastIndex;
  }
  if (last < s.length) out.push(esc(s.slice(last)));
  while (openSpans > 0) { out.push("</span>"); openSpans--; }
  return out.join("").replace(/\n/g, "<br>");
}

function classifyStep(st: RxpGuideStep): VisibleStep["kind"] {
  const verbs = new Set((st.actions || []).map((a) => a.verb));
  if (verbs.has("deathskip")) return "death";
  if (verbs.has("hs") || verbs.has("home")) return "hearth";
  if (verbs.has("fly") || verbs.has("fp")) return "travel";
  if (verbs.has("turnin")) return "turnin";
  if (verbs.has("accept")) return "accept";
  if (verbs.has("complete") || verbs.has("kill") || verbs.has("collect") || verbs.has("mob")) return "combat";
  return "step";
}

function actionBadge(a: RxpGuideAction): { cls: string; sym: string; label: string } | null {
  switch (a.verb) {
    case "accept":
      return { cls: "ab-accept", sym: "!", label: a.quest_name ? `Accept: ${a.quest_name}` : `Accept quest #${a.quest_id || "?"}` };
    case "turnin":
      return { cls: "ab-turnin", sym: "?", label: a.quest_name ? `Turn in: ${a.quest_name}` : `Turn in quest #${a.quest_id || "?"}` };
    case "complete":
      return { cls: "ab-combat", sym: "◎", label: (a.quest_name ? `${a.quest_name}` : `Quest #${a.quest_id || "?"}`) + (a.qty ? ` (×${a.qty})` : "") };
    case "collect":
      return { cls: "ab-loot", sym: "◆", label: `Collect ${a.qty && a.qty > 1 ? `${a.qty}× ` : ""}${a.entity_name || `item #${a.entity_id || "?"}`}` };
    case "kill":
    case "mob":
      return { cls: "ab-combat", sym: "⚔", label: `Target: ${(a.units && a.units.join(", ")) || a.entity_name || "mob"}` };
    case "target":
    case "unitscan":
      return { cls: "ab-friendly", sym: "⌖", label: `${a.verb === "unitscan" ? "Scan" : "NPC"}: ${(a.units && a.units.join(", ")) || a.entity_name || ""}` };
    case "hs":
      return { cls: "ab-hearth", sym: "⌂", label: `Hearthstone${a.location ? ` → ${a.location}` : ""}` };
    case "home":
      return { cls: "ab-hearth", sym: "⌂", label: `Set Hearthstone${a.location ? `: ${a.location}` : ""}` };
    case "fly":
      return { cls: "ab-travel", sym: "✦", label: `Fly to ${a.location || "destination"}` };
    case "fp":
      return { cls: "ab-travel", sym: "✦", label: `Get flight path${a.location ? `: ${a.location}` : ""}` };
    case "train":
      return { cls: "ab-train", sym: "📖", label: a.entity_name ? `Train ${a.entity_name}` : `Train spell #${a.entity_id || "?"}` };
    case "vendor":
    case "buy":
      return { cls: "ab-buy", sym: "◈", label: a.verb === "buy" ? `Buy ${a.qty || 1}× ${a.entity_name || `item #${a.entity_id || "?"}`}` : "Vendor / resupply" };
    case "deathskip":
      return { cls: "ab-death", sym: "☠", label: "Deathskip to Spirit Healer" };
    case "xp":
      return { cls: "ab-xp", sym: "★", label: `Grind to level ${a.level || "?"}${a.xp ? ` (+${a.xp} XP)` : ""}` };
    default:
      return null;
  }
}

function stepHeadline(st: RxpGuideStep): string {
  for (const a of st.actions || []) {
    if (a.text) return a.text.split("\n")[0];
  }
  for (const a of st.actions || []) {
    const b = actionBadge(a);
    if (b) return b.label;
  }
  if (st.zone && st.x != null && st.y != null) return `Go to ${st.zone} (${st.x.toFixed(1)}, ${st.y.toFixed(1)})`;
  return `Step ${st.idx}`;
}

function rebuildVisibleSteps(): void {
  if (!currentGuide) { visibleSteps = []; return; }
  const out: VisibleStep[] = [];
  let rxpNum = 0;
  // Carry forward the last known zone/waypoint when a step has no .goto of its own, so the timeline marker stays placed
  let lastMap: number | null = currentGuide.zones?.[0]?.ui_map ?? null;
  let lastZone: string | null = currentGuide.zones?.[0]?.name ?? null;
  let lastX: number | null = null;
  let lastY: number | null = null;
  for (const st of currentGuide.steps) {
    const condOk = condApplies(st.cond) && stepFlagsShown(st.flags || {});
    if (!condOk && !filter.showAllCond) continue;
    if (condOk) rxpNum++;
    if (st.ui_map != null) { lastMap = st.ui_map; lastZone = st.zone || lastZone; }
    if (st.x != null && st.y != null) { lastX = st.x; lastY = st.y; }
    out.push({
      step: st,
      rxpNum: condOk ? rxpNum : st.idx,
      dimmed: !condOk,
      kind: classifyStep(st),
      x: st.x ?? lastX,
      y: st.y ?? lastY,
      ui_map: st.ui_map ?? lastMap,
      zone: st.zone ?? lastZone,
    });
  }
  visibleSteps = out;
  if (activeIdx >= visibleSteps.length) activeIdx = Math.max(0, visibleSteps.length - 1);
  const cur = visibleSteps[activeIdx];
  if (cur && cur.ui_map != null) activeMapId = cur.ui_map;
  else if (!activeMapId && currentGuide.zones.length) activeMapId = currentGuide.zones[0].ui_map;
}

function mapImageUrl(uiMap: number | null): string | null {
  if (!uiMap) return null;
  if (live.engineUrl) return `${live.engineUrl}/map/${uiMap}.png`;
  return currentGuide?.mapImages?.[uiMap] || null;
}

/** Render the interactive zone map, route trail, directional arrows, clickable pins, and RXP Arrow widget. */
function renderMap(): void {
  const wrap = $("rxpb-map-stage");
  if (!wrap || !currentGuide) return;
  const cur = visibleSteps[activeIdx];
  const mapId = activeMapId ?? cur?.ui_map ?? currentGuide.zones[0]?.ui_map ?? null;
  const zoneName = currentGuide.zones.find((z) => z.ui_map === mapId)?.name || cur?.zone || "Zone Map";

  // Zone tabs (when a guide crosses multiple zones)
  const zoneCounts = new Map<number, number>();
  for (const vs of visibleSteps) {
    if (vs.step.ui_map != null) zoneCounts.set(vs.step.ui_map, (zoneCounts.get(vs.step.ui_map) || 0) + 1);
  }
  $("rxpb-zones").innerHTML = currentGuide.zones.map((z) => {
    const cnt = zoneCounts.get(z.ui_map) || 0;
    return `<button type="button" class="rxpb-ztab${z.ui_map === mapId ? " on" : ""}" data-map="${z.ui_map}">${esc(z.name)} <span>${cnt}</span></button>`;
  }).join("");
  $("rxpb-zones").querySelectorAll<HTMLButtonElement>("button").forEach((b) => {
    b.onclick = () => {
      activeMapId = Number(b.dataset.map);
      // If current step isn't in this zone, jump to the first step in this zone
      const firstInZone = visibleSteps.findIndex((vs) => vs.step.ui_map === activeMapId);
      if (firstInZone >= 0 && visibleSteps[activeIdx]?.step.ui_map !== activeMapId) {
        selectStep(firstInZone, false);
      } else {
        renderMap();
      }
    };
  });

  if (view.map !== mapId) {
    view.map = mapId;
    resetView();
  }

  const imgUrl = mapImageUrl(mapId);
  const imgEl = $<HTMLImageElement>("rxpb-map-img");
  if (imgUrl) {
    if (imgEl.getAttribute("src") !== imgUrl) imgEl.src = imgUrl;
    imgEl.style.display = "block";
    wrap.classList.remove("no-img");
  } else {
    imgEl.style.display = "none";
    wrap.classList.add("no-img");
  }

  // Collect steps that have coordinates in this zone
  const zoneSteps = visibleSteps
    .map((vs, idx) => ({ vs, idx }))
    .filter(({ vs }) => vs.ui_map === mapId && vs.step.x != null && vs.step.y != null);

  const svg: string[] = [];
  svg.push(`<defs>
    <marker id="rxpb-arr-done" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="4.5" markerHeight="4.5" orient="auto-start-reverse">
      <path d="M 0 1.5 L 9 5 L 0 8.5 z" fill="rgba(242,194,79,0.55)"/>
    </marker>
    <marker id="rxpb-arr-cur" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 1 L 10 5 L 0 9 z" fill="#ffd877"/>
    </marker>
  </defs>`);

  // Subtle coordinate grid when no map texture is loaded
  if (!imgUrl) {
    for (let g = 10; g < 100; g += 10) {
      svg.push(`<line x1="${mapX(g)}" y1="0" x2="${mapX(g)}" y2="${MAP_H}" class="rxpb-grid"/>`);
      svg.push(`<line x1="0" y1="${mapY(g)}" x2="${MAP_W}" y2="${mapY(g)}" class="rxpb-grid"/>`);
    }
  }

  // Steps before the current one are hidden unless "Done steps" is on (a busy hub is otherwise a pile of old pins);
  // the step the player pinned stays
  const shownSteps = zoneSteps.filter(({ idx }) => filter.showDone || idx >= activeIdx || idx === pinnedStepIdx);

  // An arrowhead at (x, y) pointing along (dx, dy), in a body that keeps its size on screen at any zoom
  const arrowhead = (x: number, y: number, dx: number, dy: number, cls: string) => {
    if (!dx && !dy) return "";
    const deg = (Math.atan2(dy, dx) * 180) / Math.PI;
    return `<g transform="translate(${x.toFixed(1)},${y.toFixed(1)}) rotate(${deg.toFixed(1)})"><g class="rxpb-body">`
      + `<path class="rxpb-arrowhead ${cls}" d="M 7 0 L -5 -5.5 L -2 0 L -5 5.5 Z"/></g></g>`;
  };
  const midArrows = (pts: [number, number][], cls: string) => pts.slice(1).map(([x2, y2], i) => {
    const [x1, y1] = pts[i];
    return Math.hypot(x2 - x1, y2 - y1) > 18 ? arrowhead((x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1, cls) : "";
  }).join("");

  // 1. Future route path (steps after activeIdx in this zone)
  const futurePts = zoneSteps.filter((z) => z.idx >= activeIdx).map((z) => `${mapX(z.vs.step.x!)},${mapY(z.vs.step.y!)}`);
  if (futurePts.length > 1) {
    svg.push(`<polyline class="rxpb-path-future" points="${futurePts.join(" ")}"/>`);
  }

  // 2. Completed route path (steps up to activeIdx in this zone), with "Done steps" on
  const pastSteps = zoneSteps.filter((z) => z.idx <= activeIdx);
  if (filter.showDone && pastSteps.length > 1) {
    const pts = pastSteps.map((z) => [mapX(z.vs.step.x!), mapY(z.vs.step.y!)] as [number, number]);
    const pastPts = pts.map(([x, y]) => `${x},${y}`).join(" ");
    svg.push(`<polyline class="rxpb-path-case" points="${pastPts}"/>`);
    svg.push(`<polyline class="rxpb-path-done" points="${pastPts}"/>`);
    svg.push(midArrows(pts, "done"));
  }

  // 3. Active leg (from previous waypoint in this zone to the current step's waypoint)
  let prevX: number | null = null;
  let prevY: number | null = null;
  for (let i = activeIdx - 1; i >= 0; i--) {
    const p = visibleSteps[i];
    if (p.ui_map === mapId && p.step.x != null && p.step.y != null) {
      prevX = p.step.x;
      prevY = p.step.y;
      break;
    }
  }
  const curWps = (cur?.step.waypoints || []).filter((w) => !w.ui_map || w.ui_map === mapId);
  const targetWp = curWps[activeWpIdx] || curWps[0] || (cur && cur.ui_map === mapId && cur.step.x != null ? { x: cur.step.x, y: cur.step.y! } : null);

  if (prevX != null && prevY != null && targetWp && (Math.hypot(targetWp.x - prevX, targetWp.y - prevY) > 0.2)) {
    const [x1, y1, x2, y2] = [mapX(prevX), mapY(prevY), mapX(targetWp.x), mapY(targetWp.y)];
    const legPts = `${x1},${y1} ${x2},${y2}`;
    svg.push(`<polyline class="rxpb-leg-case" points="${legPts}"/>`);
    svg.push(`<polyline class="rxpb-leg-walk" points="${legPts}"/>`);
    svg.push(`<polyline class="rxpb-leg-flow" points="${legPts}"/>`);
    // where the leg starts (the last step, whose pin may be hidden) and an arrowhead short of the target pin
    svg.push(`<g transform="translate(${x1},${y1})"><g class="rxpb-body"><circle class="rxpb-leg-start" r="4.5"/></g></g>`);
    const len = Math.hypot(x2 - x1, y2 - y1);
    const back = Math.min(len / 2, 22);
    svg.push(arrowhead(x2 - ((x2 - x1) / len) * back, y2 - ((y2 - y1) / len) * back, x2 - x1, y2 - y1, ""));
  }

  // 4. Multi-waypoint chain within the active step itself
  if (curWps.length > 1) {
    const pts = curWps.map((w) => [mapX(w.x), mapY(w.y)] as [number, number]);
    svg.push(`<polyline class="rxpb-step-chain" points="${pts.map(([x, y]) => `${x},${y}`).join(" ")}"/>`);
    svg.push(midArrows(pts, "chain"));
  }

  // 5. Grinding loops / lines (.loop / .line) on the active step
  for (const lp of cur?.step.loops || []) {
    if ((!lp.ui_map || lp.ui_map === mapId) && lp.points.length > 1) {
      const pts = lp.points.map(([x, y]) => `${mapX(x)},${mapY(y)}`).join(" ");
      const tag = lp.kind === "loop" ? "polygon" : "polyline";
      svg.push(`<${tag} class="rxpb-loop" points="${pts}"/>`);
    }
  }

  // Nothing to draw: say why rather than leave a bare map
  if (!zoneSteps.length) {
    const why = currentGuide.world_only && !currentGuide.placed
      ? "Glide can't place this guide on the map yet: it needs the zone data in its knowledge base, which builds while the coach runs."
      : "No waypoints in this zone for the steps shown.";
    svg.push(`<text class="rxpb-map-note" x="${MAP_W / 2}" y="${MAP_H / 2}" text-anchor="middle">${esc(why)}</text>`);
  }

  // 6. Step waypoint pins (clickable!), each body keeping its size on screen at any zoom
  for (const { vs, idx } of shownSteps) {
    const isCur = idx === activeIdx;
    const isPinned = idx === pinnedStepIdx;
    const isDone = idx < activeIdx;
    const cx = mapX(vs.step.x!);
    const cy = mapY(vs.step.y!);
    const stateCls = isCur ? "is-cur" : isDone ? "is-done" : "is-next";
    const sym = vs.kind === "accept" ? "!" : vs.kind === "turnin" ? "?" : vs.kind === "combat" ? "⚔"
      : vs.kind === "travel" ? "✦" : vs.kind === "hearth" ? "⌂" : vs.kind === "death" ? "☠" : String(vs.rxpNum);
    const r = isCur ? 15 : isPinned ? 13 : 10.5;
    svg.push(`<g class="rxpb-pin k-${vs.kind} ${stateCls}${isPinned ? " is-pinned" : ""}" data-step="${idx}" transform="translate(${cx},${cy})">
      <title>Step ${vs.rxpNum}: ${esc(stepHeadline(vs.step))} (${vs.step.x!.toFixed(1)}, ${vs.step.y!.toFixed(1)})</title>
      <g class="rxpb-body">${isCur ? `<circle class="rxpb-pin-pulse" r="22"/>` : ""}<circle r="${r}"/>
      <text dy="0.35em">${esc(sym)}</text></g>
    </g>`);
  }

  // 7. Sub-waypoints of the active step (when a step has >1 .goto)
  if (curWps.length > 1) {
    curWps.forEach((w, wi) => {
      const cx = mapX(w.x), cy = mapY(w.y);
      const on = wi === activeWpIdx;
      svg.push(`<g class="rxpb-subwp${on ? " on" : ""}" data-wp="${wi}" transform="translate(${cx},${cy})">
        <title>Sub-waypoint ${wi + 1}: (${w.x.toFixed(1)}, ${w.y.toFixed(1)}) ${esc(w.text || "")}</title>
        <g class="rxpb-body"><circle r="${on ? 11 : 8}"/>
        <text dy="0.35em">${String.fromCharCode(65 + (wi % 26))}</text></g>
      </g>`);
    });
  }

  $("rxpb-map-svg").innerHTML = svg.join("");
  $("rxpb-map-svg").querySelectorAll<SVGGElement>(".rxpb-pin").forEach((g) => {
    g.onclick = (e) => {
      e.stopPropagation();
      const idx = Number(g.dataset.step);
      pinnedStepIdx = idx;
      selectStep(idx, true);
    };
  });
  $("rxpb-map-svg").querySelectorAll<SVGGElement>(".rxpb-subwp").forEach((g) => {
    g.onclick = (e) => {
      e.stopPropagation();
      activeWpIdx = Number(g.dataset.wp);
      pinnedStepIdx = activeIdx;
      renderMap();
      renderActiveStep();
    };
  });

  // Update the RestedXP Waypoint Arrow HUD widget
  const arrowHud = $("rxpb-arrow-hud");
  if (targetWp) {
    let deg = 0;
    let distText = `${targetWp.x.toFixed(1)}, ${targetWp.y.toFixed(1)}`;
    if (prevX != null && prevY != null && (prevX !== targetWp.x || prevY !== targetWp.y)) {
      deg = Math.atan2(targetWp.x - prevX, -(targetWp.y - prevY)) * (180 / Math.PI);
      const approxYds = Math.round(Math.hypot((targetWp.x - prevX) * 35, (targetWp.y - prevY) * 24));
      distText = `${targetWp.x.toFixed(1)}, ${targetWp.y.toFixed(1)} · ~${approxYds} yd`;
    }
    arrowHud.hidden = false;
    arrowHud.innerHTML = `<div class="rxpb-arrow-wheel"><svg viewBox="0 0 36 36" style="transform:rotate(${deg.toFixed(1)}deg)">
        <path d="M18 3 L30 30 L18 23 L6 30 Z" fill="#00ff25" stroke="#081a0b" stroke-width="2"/>
      </svg></div>
      <div class="rxpb-arrow-txt">
        <b>${esc(zoneName)}</b>
        <span>${esc(distText)}</span>
      </div>`;
  } else {
    arrowHud.hidden = true;
  }

  renderInspector();
}

/** Render the floating map inspector card when a map pin is clicked/selected. */
function renderInspector(): void {
  const box = $("rxpb-inspector");
  const idx = pinnedStepIdx ?? activeIdx;
  const vs = visibleSteps[idx];
  if (!vs || pinnedStepIdx === null) {
    box.hidden = true;
    return;
  }
  const st = vs.step;
  const lines = (st.actions || []).filter((a) => a.raw_text || a.text || a.verb !== "goto");
  const badges = lines.map(actionBadge).filter(Boolean) as { cls: string; sym: string; label: string }[];
  const instructions = (st.actions || []).filter((a) => a.raw_text || a.text)
    .map((a) => `<div class="rxpb-ins-line">${formatRxpText(a.raw_text || a.text)}</div>`).join("");
  const coord = st.x != null && st.y != null ? `${st.zone || vs.zone || ""} (${st.x.toFixed(1)}, ${st.y.toFixed(1)})` : (st.zone || vs.zone || "No waypoint");
  box.hidden = false;
  box.innerHTML = `<div class="rxpb-ins-head">
      <span class="rxpb-step-pill">Step ${vs.rxpNum}</span>
      <span class="rxpb-ins-coord">${esc(coord)}</span>
      ${st.cond ? `<span class="rxpb-cond-pill">&lt;&lt; ${esc(st.cond)}</span>` : ""}
      <button type="button" class="rxpb-ins-close" id="rxpb-ins-close" title="Close details">×</button>
    </div>
    ${badges.length ? `<div class="rxpb-badges">${badges.map((b) => `<span class="rxpb-badge ${b.cls}"><b>${esc(b.sym)}</b> ${esc(b.label)}</span>`).join("")}</div>` : ""}
    ${instructions ? `<div class="rxpb-ins-body">${instructions}</div>` : ""}
    ${st.comments ? `<div class="rxpb-comment">💬 ${esc(st.comments)}</div>` : ""}`;
  const closeBtn = document.getElementById("rxpb-ins-close");
  if (closeBtn) closeBtn.onclick = () => { pinnedStepIdx = null; box.hidden = true; renderMap(); };
}

/** Render the timeline scrubber track with event ticks and playback controls. */
function renderTimeline(): void {
  const n = visibleSteps.length;
  const slider = $<HTMLInputElement>("rxpb-scrub");
  slider.max = String(Math.max(0, n - 1));
  slider.value = String(activeIdx);
  slider.disabled = n <= 1;

  const cur = visibleSteps[activeIdx];
  $("rxpb-tl-pos").textContent = n ? `Step ${cur ? cur.rxpNum : activeIdx + 1} of ${n}` : "No steps";
  $("rxpb-tl-zone").textContent = cur?.zone ? `${cur.zone}${cur.step.x != null ? ` (${cur.step.x.toFixed(1)}, ${cur.step.y!.toFixed(1)})` : ""}` : "";
  $<HTMLButtonElement>("rxpb-prev").disabled = activeIdx <= 0;
  $<HTMLButtonElement>("rxpb-next").disabled = activeIdx >= n - 1;
  $<HTMLButtonElement>("rxpb-play").innerHTML = playTimer ? "⏸ Pause" : "▶ Walk route";

  // Build color-coded event ticks along the timeline track
  const ticks: string[] = [];
  if (n > 1) {
    let prevZone = visibleSteps[0]?.zone;
    visibleSteps.forEach((vs, i) => {
      const pct = ((i / (n - 1)) * 100).toFixed(2);
      if (vs.zone && vs.zone !== prevZone) {
        prevZone = vs.zone;
        ticks.push(`<i class="tl-tick tl-zone" style="left:${pct}%" data-idx="${i}" title="Zone: ${esc(vs.zone)} (Step ${vs.rxpNum})"></i>`);
      }
      if (vs.kind !== "step") {
        ticks.push(`<i class="tl-tick tl-${vs.kind}${i === activeIdx ? " on" : ""}" style="left:${pct}%" data-idx="${i}" title="Step ${vs.rxpNum}: ${esc(stepHeadline(vs.step))}"></i>`);
      }
    });
  }
  $("rxpb-tl-ticks").innerHTML = ticks.join("");
  $("rxpb-tl-ticks").querySelectorAll<HTMLElement>(".tl-tick").forEach((el) => {
    el.onclick = () => selectStep(Number(el.dataset.idx), true);
  });
}

/** Render the RestedXP in-game CurrentStepFrame + header + #next guide chain button. */
function renderActiveStep(): void {
  if (!currentGuide) return;
  $("rxpb-win-title").textContent = currentGuide.displayname || currentGuide.name;
  $("rxpb-win-sub").textContent = [currentGuide.group, currentGuide.subgroup].filter(Boolean).join(" · ");

  // Next guide button (#next in RXP)
  const nextBtn = $<HTMLButtonElement>("rxpb-next-guide");
  const nextEntry = (currentGuide.next_list || []).find((n) => n.name && condApplies(n.cond));
  if (nextEntry && nextEntry.name) {
    const targetName = nextEntry.name.trim();
    const match = catalog.find((c) => c.name.toLowerCase() === targetName.toLowerCase()
      || c.name.toLowerCase().endsWith(targetName.toLowerCase()));
    if (match) {
      nextBtn.hidden = false;
      nextBtn.textContent = `Next: ${match.name} →`;
      nextBtn.onclick = () => openGuide(match.packId, match.index);
    } else {
      nextBtn.hidden = true;
    }
  } else {
    nextBtn.hidden = true;
  }

  const box = $("rxpb-active-step");
  const vs = visibleSteps[activeIdx];
  if (!vs) {
    box.innerHTML = `<div class="rxpb-empty">No steps match the current character filter.</div>`;
    return;
  }
  const st = vs.step;
  const flagPills: string[] = [];
  if (st.flags?.sticky) flagPills.push(`<span class="rxpb-flag">Sticky</span>`);
  if (st.flags?.optional) flagPills.push(`<span class="rxpb-flag">Optional</span>`);
  if (st.flags?.completewith) flagPills.push(`<span class="rxpb-flag">Complete with next</span>`);
  if (st.cond) flagPills.push(`<span class="rxpb-cond-pill">&lt;&lt; ${esc(st.cond)}</span>`);

  // Step elements (instructions, objectives, actions)
  const rows: string[] = [];
  const visibleActions = (st.actions || []).filter((a) => condApplies(a.cond) || filter.showAllCond);
  visibleActions.forEach((a, ai) => {
    const b = actionBadge(a);
    const hasText = !!(a.raw_text || a.text);
    if (!b && !hasText && a.verb !== "goto" && a.verb !== "link") return;
    if (a.verb === "goto" && !hasText) return;    // pure coordinate lines are shown in the waypoint pills below
    const objKey = `${currentGuide!.id}:${currentGuide!.index}:${st.idx}:${ai}`;
    const done = checkedObjs.has(objKey);
    const isCheckable = a.verb !== "text" || a.kind === "+";
    const condTag = a.cond ? ` <span class="rxpb-cond-pill">&lt;&lt; ${esc(a.cond)}</span>` : "";
    const commentTag = a.comment ? `<div class="rxpb-line-comment">-- ${esc(a.comment)}</div>` : "";
    const urlLink = a.url ? ` <a href="${esc(a.url)}" class="rxpb-ext" data-url="${esc(a.url)}">[Video / Link ↗]</a>` : "";
    rows.push(`<div class="rxpb-obj-row${done ? " done" : ""}" data-obj="${esc(objKey)}">
      ${isCheckable ? `<input type="checkbox" class="rxpb-chk" ${done ? "checked" : ""} aria-label="Complete objective">` : `<span class="rxpb-bullet">•</span>`}
      <div class="rxpb-obj-body">
        ${b ? `<span class="rxpb-badge ${b.cls}"><b>${esc(b.sym)}</b> ${esc(b.label)}</span>` : ""}
        ${hasText ? `<div class="rxpb-obj-text">${formatRxpText(a.raw_text || a.text)}${urlLink}${condTag}</div>` : `${urlLink}${condTag}`}
        ${commentTag}
      </div>
    </div>`);
  });

  // Clickable sub-waypoint coordinate pills
  const wps = st.waypoints || [];
  const wpPills = wps.map((w, wi) => `<button type="button" class="rxpb-wp-pill${wi === activeWpIdx ? " on" : ""}" data-wp="${wi}">
    📍 ${esc(w.zone || st.zone || "Zone")} <b>${w.x.toFixed(1)}, ${w.y.toFixed(1)}</b>${wps.length > 1 ? ` <em>(${String.fromCharCode(65 + (wi % 26))})</em>` : ""}
  </button>`).join("");

  box.innerHTML = `<div class="rxpb-cs-head">
      <span class="rxpb-cs-num">Step ${vs.rxpNum}</span>
      <div class="rxpb-cs-flags">${flagPills.join("")}</div>
    </div>
    <div class="rxpb-cs-rows">${rows.length ? rows.join("") : `<div class="rxpb-obj-text dim">Travel / transition step</div>`}</div>
    ${wpPills ? `<div class="rxpb-wp-bar">${wpPills}</div>` : ""}
    ${st.comments ? `<div class="rxpb-comment">💬 ${esc(st.comments)}</div>` : ""}`;

  box.querySelectorAll<HTMLElement>(".rxpb-obj-row").forEach((row) => {
    const chk = row.querySelector<HTMLInputElement>(".rxpb-chk");
    if (!chk) return;
    row.onclick = (e) => {
      if ((e.target as HTMLElement).tagName === "A") return;
      const key = row.dataset.obj!;
      if (checkedObjs.has(key)) checkedObjs.delete(key);
      else checkedObjs.add(key);
      renderActiveStep();
      renderStepList();
    };
  });
  box.querySelectorAll<HTMLAnchorElement>(".rxpb-ext").forEach((a) => {
    a.onclick = (e) => { e.preventDefault(); if (a.dataset.url) call("shell:open", a.dataset.url); };
  });
  box.querySelectorAll<HTMLButtonElement>(".rxpb-wp-pill").forEach((btn) => {
    btn.onclick = () => {
      activeWpIdx = Number(btn.dataset.wp);
      const wp = wps[activeWpIdx];
      if (wp?.ui_map) activeMapId = wp.ui_map;
      renderActiveStep();
      renderMap();
    };
  });
}

/** Render the scrollable RestedXP step list below the active step frame. */
function renderStepList(scrollActive = false): void {
  const listEl = $("rxpb-step-list");
  if (!listEl) return;
  const q = filter.stepSearch.trim().toLowerCase();
  const items: string[] = [];
  visibleSteps.forEach((vs, i) => {
    const st = vs.step;
    const badges = (st.actions || []).map(actionBadge).filter(Boolean) as { cls: string; sym: string; label: string }[];
    const summaryText = st.text || badges.map((b) => b.label).join(" · ") || stepHeadline(st);
    if (q) {
      const hay = `${vs.rxpNum} ${summaryText} ${st.zone || ""} ${st.comments || ""} ${badges.map((b) => b.label).join(" ")}`.toLowerCase();
      if (!hay.includes(q)) return;
    }
    const on = i === activeIdx;
    const done = i < activeIdx;
    const coord = st.x != null && st.y != null ? `${st.zone || ""} ${st.x.toFixed(1)}, ${st.y.toFixed(1)}` : (st.zone || "");
    items.push(`<li class="rxpb-sitem k-${vs.kind}${on ? " on" : ""}${done ? " done" : ""}${vs.dimmed ? " dimmed" : ""}" data-idx="${i}">
      <div class="rxpb-sitem-top">
        <span class="rxpb-snum">${vs.rxpNum}</span>
        <span class="rxpb-stitle">${formatRxpText((st.actions || []).find((a) => a.raw_text)?.raw_text || summaryText)}</span>
        ${coord ? `<span class="rxpb-scoord">${esc(coord)}</span>` : ""}
      </div>
      ${badges.length ? `<div class="rxpb-sbadges">${badges.slice(0, 4).map((b) => `<span class="rxpb-badge mini ${b.cls}"><b>${esc(b.sym)}</b> ${esc(b.label)}</span>`).join("")}</div>` : ""}
    </li>`);
  });
  listEl.innerHTML = items.length ? items.join("") : `<li class="rxpb-empty">No steps match "${esc(filter.stepSearch)}".</li>`;
  listEl.querySelectorAll<HTMLLIElement>(".rxpb-sitem").forEach((li) => {
    li.onclick = () => selectStep(Number(li.dataset.idx), false);
  });
  if (scrollActive) {
    // scroll the list itself (scrollIntoView would also scroll the page around it)
    const activeLi = listEl.querySelector<HTMLElement>(".rxpb-sitem.on");
    if (activeLi) {
      const top = activeLi.offsetTop - listEl.offsetTop, bottom = top + activeLi.offsetHeight;
      if (top < listEl.scrollTop) listEl.scrollTo({ top, behavior: "smooth" });
      else if (bottom > listEl.scrollTop + listEl.clientHeight) listEl.scrollTo({ top: bottom - listEl.clientHeight, behavior: "smooth" });
    }
  }
}

export function selectStep(idx: number, scrollList = true): void {
  if (!visibleSteps.length) return;
  activeIdx = Math.max(0, Math.min(visibleSteps.length - 1, idx));
  activeWpIdx = 0;
  const vs = visibleSteps[activeIdx];
  if (vs && vs.ui_map != null) activeMapId = vs.ui_map;
  renderTimeline();
  renderActiveStep();
  renderStepList(scrollList);
  renderMap();
  followStep();
}

function stopPlay(): void {
  if (playTimer !== null) {
    window.clearInterval(playTimer);
    playTimer = null;
  }
  const btn = document.getElementById("rxpb-play");
  if (btn) btn.innerHTML = "▶ Walk route";
}

function togglePlay(): void {
  if (playTimer !== null) {
    stopPlay();
    return;
  }
  if (activeIdx >= visibleSteps.length - 1) activeIdx = 0;
  selectStep(activeIdx, true);
  playTimer = window.setInterval(() => {
    if (currentPage() !== "guides") { stopPlay(); return; }
    if (activeIdx < visibleSteps.length - 1) {
      selectStep(activeIdx + 1, true);
    } else {
      stopPlay();
    }
  }, playMs);
  renderTimeline();
}

let openSeq = 0;            // the latest openGuide call: an older one that answers later is dropped
let wantGuide: { packId: string; index: number } | null = null;   // browseGuide's pick, opened once the list loads

export async function openGuide(packId: string, index: number): Promise<void> {
  stopPlay();
  pinnedStepIdx = null;
  const seq = ++openSeq;
  const key = `${packId}:${index}`;
  let g = cache.get(key) || null;
  if (!g) {
    $("rxpb-win-title").textContent = "Loading guide…";
    g = (await call("addon:rxpImports", packId, index)) as RxpGuideDetail | null;
    if (g) cache.set(key, g);
  }
  if (seq !== openSeq) return;
  if (!g) {
    $("rxpb-win-title").textContent = "Couldn't open that guide";
    $("rxpb-win-sub").textContent = "Try another, or restart the coach and try again";
    return;
  }
  currentGuide = g;
  activeIdx = 0;
  activeWpIdx = 0;
  activeMapId = g.zones?.[0]?.ui_map ?? null;

  // Sync the group & guide dropdowns: the group picker moves only when the guide isn't in the group shown (a guide
  // RestedXP files under both factions, such as Zephras Isle, stays in the one the player picked it from)
  const grpSel = $<HTMLSelectElement>("rxpb-group");
  const inGroup = (grp: string) => catalog.some((c) => c.packId === packId && c.index === index && c.group === grp);
  if (filter.group && !inGroup(filter.group)) {
    const entry = catalog.find((c) => c.packId === packId && c.index === index);
    filter.group = entry ? entry.group : "";
    grpSel.value = filter.group;
  }
  populateGuideSelect();
  $<HTMLSelectElement>("rxpb-guide").value = key;

  rebuildVisibleSteps();
  renderTimeline();
  renderActiveStep();
  renderStepList(false);
  renderMap();
}

function populateGuideSelect(): void {
  const grpSel = $<HTMLSelectElement>("rxpb-group");
  const guideSel = $<HTMLSelectElement>("rxpb-guide");
  const q = filter.search.trim().toLowerCase();
  const filtered = catalog.filter((c) => {
    if (filter.group && c.group !== filter.group) return false;
    if (q && !`${c.name} ${c.subgroup || ""} ${c.group}`.toLowerCase().includes(q)) return false;
    return true;
  });

  // Group by subgroup for familiar RestedXP menu hierarchy
  const bySub = new Map<string, CatalogEntry[]>();
  for (const c of filtered) {
    const sub = c.subgroup || c.group || "Guides";
    if (!bySub.has(sub)) bySub.set(sub, []);
    bySub.get(sub)!.push(c);
  }
  const html: string[] = [];
  for (const [sub, items] of bySub) {
    html.push(`<optgroup label="${esc(sub)}">`);
    for (const item of items) {
      const val = `${item.packId}:${item.index}`;
      html.push(`<option value="${esc(val)}">${esc(item.name)} (${item.steps} steps)</option>`);
    }
    html.push(`</optgroup>`);
  }
  guideSel.innerHTML = html.join("") || `<option value="">No matching guides</option>`;
  if (currentGuide) {
    const curKey = `${currentGuide.id}:${currentGuide.index}`;
    if (Array.from(guideSel.options).some((o) => o.value === curKey)) guideSel.value = curKey;
  }
  grpSel.value = filter.group;
}

export async function updateRxpBrowser(imports: RxpImport[]): Promise<void> {
  const sec = $("rxpb-block");
  if (!sec) return;
  const entries: CatalogEntry[] = [];
  for (const imp of imports) {
    const items = imp.items || imp.guides.map((name, index) => ({
      index, group: "RestedXP", subgroup: null, name, next: null, faction: null, defaultFor: null, version: null, steps: 0,
    }));
    for (const it of items) entries.push({ ...it, packId: imp.id });
  }
  // each group in level order, so RestedXP's free 1-20 guides come before a bought string's 20-60 ones
  const groupOrder = new Map<string, number>();
  entries.forEach((e) => { if (!groupOrder.has(e.group)) groupOrder.set(e.group, groupOrder.size); });
  const startLevel = (name: string) => { const m = /^\s*0?(\d+)\s*-/.exec(name); return m ? Number(m[1]) : 999; };
  catalog = entries
    .map((e, i) => ({ e, i }))
    .sort((a, b) => groupOrder.get(a.e.group)! - groupOrder.get(b.e.group)! || startLevel(a.e.name) - startLevel(b.e.name) || a.i - b.i)
    .map((x) => x.e);
  $("rxpb-empty").hidden = catalog.length > 0;
  if (!catalog.length) {
    sec.hidden = true;
    return;
  }
  sec.hidden = false;
  const groups = Array.from(new Set(catalog.map((c) => c.group)));
  const grpSel = $<HTMLSelectElement>("rxpb-group");
  grpSel.innerHTML = `<option value="">All guide groups (${catalog.length} guides)</option>`
    + groups.map((g) => `<option value="${esc(g)}">${esc(g)} (${catalog.filter((c) => c.group === g).length})</option>`).join("");
  if (filter.group && !groups.includes(filter.group)) filter.group = "";
  populateGuideSelect();

  const want = wantGuide;
  wantGuide = null;
  if (want && catalog.some((c) => c.packId === want.packId && c.index === want.index)) {
    await openGuide(want.packId, want.index);
  } else if (!currentGuide || !catalog.some((c) => c.packId === currentGuide!.id && c.index === currentGuide!.index)) {
    // Prefer a starter 1-6 / 1-10 / 1-11 guide by default if present, else the first guide
    const starter = catalog.find((c) => /^0?1-\d+/.test(c.name)) || catalog[0];
    if (starter) await openGuide(starter.packId, starter.index);
  }
}

/** The Guides page was shown: the guides added in the app and RestedXP's free ones, read fresh. */
export async function refreshGuides(): Promise<void> {
  await updateRxpBrowser(((await quiet("addon:rxpImports")) as RxpImport[]) || []);
}

/** Open a guide on the Guides page (the Addon page's "Browse guides"). */
export function browseGuide(packId: string, index: number): void {
  wantGuide = { packId, index };
  show("guides");               // the page loads its list (refreshGuides), then opens this guide
}

/** Zoom with the wheel, pan by dragging, the whole zone on double-click; the view buttons and toggles. */
function wireMapView(): void {
  const stage = $("rxpb-map-stage");
  const onMapItself = (e: Event) => !(e.target as HTMLElement).closest(".rxpb-map-tools, .rxpb-inspector, .rxpb-arrow-hud");
  stage.addEventListener("wheel", (e) => {
    if (!onMapItself(e)) return;
    e.preventDefault();
    const r = stage.getBoundingClientRect();
    zoomAt(e.deltaY < 0 ? 1.25 : 1 / 1.25, e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  let drag: { x: number; y: number; fx: number; fy: number; moved: boolean } | null = null;
  let justDragged = false;
  stage.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || !onMapItself(e)) return;
    drag = { x: e.clientX, y: e.clientY, fx: view.fx, fy: view.fy, moved: false };
  });
  window.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 4) return;
    if (!drag.moved) { drag.moved = true; stage.classList.add("dragging"); }
    const { w, h } = stageSize();
    view.fx = drag.fx + dx / w;
    view.fy = drag.fy + dy / h;
    applyView();
  });
  window.addEventListener("pointerup", () => {
    if (drag?.moved) justDragged = true;       // the click that ends a drag doesn't pick a pin
    drag = null;
    stage.classList.remove("dragging");
  });
  stage.addEventListener("click", (e) => { if (justDragged) { e.stopPropagation(); justDragged = false; } }, true);
  stage.addEventListener("dblclick", (e) => { if (onMapItself(e)) resetView(); });
  $("rxpb-zoom-in").onclick = () => zoomAt(1.5);
  $("rxpb-zoom-out").onclick = () => zoomAt(1 / 1.5);
  $("rxpb-zoom-reset").onclick = resetView;
  $<HTMLInputElement>("rxpb-follow").onchange = (e) => {
    filter.follow = (e.target as HTMLInputElement).checked;
    followStep();
  };
  $<HTMLInputElement>("rxpb-showdone").onchange = (e) => {
    filter.showDone = (e.target as HTMLInputElement).checked;
    renderMap();
  };
  new ResizeObserver(() => applyView()).observe(stage);
}

/** On the Guides page, away from a text box: left/right step, Space walks the route or stops, +/- zoom, 0 whole zone. */
function wireKeys(): void {
  document.addEventListener("keydown", (e) => {
    if (currentPage() !== "guides" || e.ctrlKey || e.altKey || e.metaKey || !visibleSteps.length) return;
    // a text box, a select, a checkbox or the timeline slider handles its own keys (the slider steps on arrows);
    // Space on a focused button presses it
    const el = e.target as HTMLElement;
    if (el.closest("input, select, textarea, [contenteditable]")) return;
    if (e.key === " " && el.closest("button, a, label")) return;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      stopPlay();
      selectStep(activeIdx + (e.key === "ArrowRight" ? 1 : -1), true);
    } else if (e.key === " ") {
      togglePlay();
    } else if (e.key === "+" || e.key === "=") {
      zoomAt(1.5);
    } else if (e.key === "-") {
      zoomAt(1 / 1.5);
    } else if (e.key === "0") {
      resetView();
    } else {
      return;
    }
    e.preventDefault();
  });
}

export function wireRxpBrowser(): void {
  wireMapView();
  wireKeys();
  $("rxpb-to-addon").onclick = () => show("addon");
  $("rxpb-group").onchange = (e) => {
    filter.group = (e.target as HTMLSelectElement).value;
    populateGuideSelect();
    const first = $<HTMLSelectElement>("rxpb-guide").value;
    if (first && first.includes(":")) {
      const [pid, idx] = first.split(":");
      openGuide(pid, Number(idx));
    }
  };
  $("rxpb-guide").onchange = (e) => {
    const v = (e.target as HTMLSelectElement).value;
    if (v && v.includes(":")) {
      const [pid, idx] = v.split(":");
      openGuide(pid, Number(idx));
    }
  };
  $("rxpb-search").oninput = (e) => {
    filter.search = (e.target as HTMLInputElement).value;
    populateGuideSelect();
  };
  $("rxpb-step-search").oninput = (e) => {
    filter.stepSearch = (e.target as HTMLInputElement).value;
    renderStepList(false);
  };
  const refilter = () => {
    rebuildVisibleSteps();
    renderTimeline();
    renderActiveStep();
    renderStepList(false);
    renderMap();
  };
  $("rxpb-race").onchange = (e) => { filter.race = (e.target as HTMLSelectElement).value; refilter(); };
  $("rxpb-class").onchange = (e) => { filter.cls = (e.target as HTMLSelectElement).value; refilter(); };
  $("rxpb-ssf").onchange = (e) => { filter.ssf = (e.target as HTMLInputElement).checked; refilter(); };
  $("rxpb-grp").onchange = (e) => { filter.groupQuests = (e.target as HTMLInputElement).checked; refilter(); };
  $("rxpb-hc").onchange = (e) => { filter.hardcore = (e.target as HTMLInputElement).checked; refilter(); };
  $("rxpb-allcond").onchange = (e) => { filter.showAllCond = (e.target as HTMLInputElement).checked; refilter(); };

  $("rxpb-scrub").oninput = (e) => {
    stopPlay();
    selectStep(Number((e.target as HTMLInputElement).value), true);
  };
  $("rxpb-prev").onclick = () => { stopPlay(); selectStep(activeIdx - 1, true); };
  $("rxpb-next").onclick = () => { stopPlay(); selectStep(activeIdx + 1, true); };
  $("rxpb-play").onclick = togglePlay;
  $("rxpb-speed").onchange = (e) => {
    playMs = Number((e.target as HTMLSelectElement).value) || 1600;
    if (playTimer !== null) { stopPlay(); togglePlay(); }
  };
}
