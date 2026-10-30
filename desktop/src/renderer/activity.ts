// The activity in the session bar: whatever long job is running (first build, re-read, walking map, speech model,
// update download), its stage, progress and elapsed time, with Glide's CPU and memory while it runs.
import { glide } from "./bridge.js";
import { $, icon } from "./dom.js";
import { mapName } from "./settings/walk-map.js";
import { live } from "./state.js";
import { refreshStatus } from "./status.js";
import { Footprint } from "./types.js";

const REFRESH_STAGES: [string, string][] = [["maps", "game files"], ["glide.db", "quest database"], ["kb.db", "knowledge base"]];
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
let refreshSeen = "";                    // the last refresh run noticed finishing (so "done" shows once)
let taskHoldUntil = 0;

type Show = (label: string, meta: string, pct: number | null, cls?: string) => void;

export function taskBar(): void {
  showActivity();
  watchResources(!$("sb-task").hidden);
}

function showActivity(): void {
  const box = $("sb-task"), bar = $("sb-bar"), fill = $("sb-fill");
  const set: Show = (label, meta, pct, cls = "") => {
    box.hidden = false;
    box.className = `sb-task ${cls}`;
    $("sb-task-label").textContent = label;
    $("sb-task-meta").textContent = meta;
    box.title = `${label} (${meta})`;
    bar.classList.toggle("busy", pct == null);
    fill.style.width = pct == null ? "" : `${Math.max(2, Math.min(100, pct))}%`;
  };
  if (live.engineState === "building") return set("Building the knowledge base", "first start, a minute or two", null);
  if (refreshActivity(set) || walkMapActivity(set) || speechActivity(set) || updateActivity(set)) return;
  box.hidden = true;
}

/** A re-read or rebuild running, or one that just finished (held a few seconds, longer when a stage failed). */
function refreshActivity(set: Show): boolean {
  const r = live.refresh;
  const now = Date.now() / 1000;
  if (r && r.state && !["waiting", "done"].includes(r.state)) {
    const i = Math.max(0, REFRESH_STAGES.findIndex((s) => s[0] === r.state));
    set(`${r.force ? "Rebuilding" : "Re-reading"}: ${REFRESH_STAGES[i][1]}`,
      `${i + 1}/3  ${r.started ? mmss(now - r.started) : "0:00"}`, ((i + 0.5) / 3) * 100);
    return true;
  }
  if (r && r.state === "done" && r.finished) {
    const key = String(r.finished);
    const failed = (["maps", "glide", "kb"] as const).filter((k) => r[k] === "failed");
    if (key !== refreshSeen) {
      refreshSeen = key;
      taskHoldUntil = Date.now() + (failed.length ? 30000 : 8000);
      refreshStatus();
    }
    if (Date.now() < taskHoldUntil) {
      if (failed.length) set(`Re-read failed: ${failed.join(", ")}`, "see Logs", 100, "failed");
      else set(`${r.force ? "Rebuild" : "Re-read"} finished`, `in ${Math.round(r.seconds || 0)} s`, 100, "done");
      return true;
    }
  }
  return false;
}

function walkMapActivity(set: Show): boolean {
  const nv = live.nav;
  if (!nv || nv.state !== "building") return false;
  set("Building the walking map", nv.tiles ? `${mapName(nv.map!)}: ${nv.tile} of ${nv.tiles}` : "starting",
    nv.tiles ? (100 * nv.tile) / nv.tiles : null);
  return true;
}

function speechActivity(set: Show): boolean {
  const sp = live.speech;
  if (sp.fetching) {
    set(`Downloading the speech model ${sp.fetching || sp.modelName || ""}`, sp.totalMB
        ? `${sp.gotMB || 0} of ${sp.totalMB} MB` : `${sp.gotMB || 0} MB`,
      sp.totalMB ? (100 * (sp.gotMB || 0)) / sp.totalMB : null);
    return true;
  }
  if (sp.state === "installing") {
    set("Installing speech recognition", "runs by itself", null);
    return true;
  }
  return false;
}

function updateActivity(set: Show): boolean {
  const u = live.update;
  if (u.state !== "downloading") return false;
  set(`Downloading Glide ${u.version || ""}`, `${Math.round(u.percent || 0)}%`, Number(u.percent) || 0);
  return true;
}

// CPU and memory of Glide (app + coach + its jobs), sampled by the main process only while an activity shows
let resWatching = false;
function watchResources(on: boolean): void {
  if (on === resWatching) return;
  resWatching = on;
  if (!on) $("sb-res").textContent = "";
  glide.invoke("res:watch", on).catch(() => undefined);
}

function showFootprint(s: Footprint): void {
  if (!resWatching) return;
  const mem = s.memMB >= 1024 ? `${(s.memMB / 1024).toFixed(1)} GB` : `${s.memMB} MB`;
  $("sb-res").innerHTML = `<span title="CPU used by Glide and its jobs, as a share of all ${navigator.hardwareConcurrency} logical cores (like Task Manager)">${icon("cpu")}<b>${s.cpu < 10 ? s.cpu.toFixed(1) : Math.round(s.cpu)}%</b></span><span title="Memory used by Glide and its jobs">${icon("memory-stick")}<b>${mem}</b></span>`;
  $("sb-res").title = `CPU and memory used by Glide: the app, the coach and the jobs it runs (${s.processes} processes)`;
}

export function wireActivity(): void {
  glide.on("res:sample", showFootprint);
  window.setInterval(taskBar, 1000);
}
