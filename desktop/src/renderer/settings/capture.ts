// Settings > Capture > Check the capture: what the coach sees in the game window (its /calibrate route grabs the
// corner, finds the addon's strip and reads it).
import { $, esc, icon } from "../dom.js";
import { Group } from "../settings-schema.js";
import { coachRunning, engineGet } from "../state.js";
import { Obj } from "../types.js";

export function renderCaptureCheck(_g: Group, host: HTMLElement): void {
  host.innerHTML = `<div class="field"><div class="lab">What the coach sees<small>Grabs the top-left corner of the WoW window the way the coach does, finds the addon's strip in it and reads it. Use it after changing the UI scale, the window mode or the settings below.</small></div>
    <div class="ctl"><button class="btn" id="cc-run">${icon("search")}Check now</button><span id="cc-msg" class="dim"></span></div></div>
    <div id="cc-out"></div>`;
  $("cc-run").onclick = runCaptureCheck;
}

async function runCaptureCheck(): Promise<void> {
  const btn = $<HTMLButtonElement>("cc-run"), msg = $("cc-msg"), out = $("cc-out");
  if (!coachRunning()) { msg.textContent = "Start the coach first; the check uses its capture."; return; }
  btn.disabled = true;
  msg.textContent = "Checking…";
  let r: Obj;
  try {
    r = await (await engineGet("/calibrate")).json();
  } catch {
    r = { ok: false, text: "This coach version can't run the check; restart it after updating." };
  }
  btn.disabled = false;
  msg.innerHTML = `<span class="${r.ok ? "ok" : "warn"}">${icon(r.ok ? "check" : "circle-alert")}${esc(r.text || "")}</span>`;
  if (!r.png) { out.innerHTML = ""; return; }
  out.innerHTML = shotHtml(r);
}

/** The captured corner, enlarged, with a ring where the strip starts, and what was read from it. */
function shotHtml(r: Obj): string {
  const f: Obj = r.fields || {};
  const facts = r.ok ? [["Character", [f.name, f.class].filter(Boolean).join(", ")], ["Level", f.level], ["Zone", f.zone],
    ["Strip", `at ${r.strip.x}, ${r.strip.y}; cells of ${r.strip.cell} px`]].filter((x) => x[1] != null && x[1] !== "") : [];
  return `<div class="cc-shot"><div class="cc-frame" style="aspect-ratio:${r.w}/${r.h}">
      <img src="data:image/png;base64,${r.png}" alt="The captured corner of the WoW window">
      ${r.strip ? `<span class="cc-mark" style="left:${(100 * r.strip.x) / r.w}%;top:${(100 * r.strip.y) / r.h}%"></span>` : ""}</div>
    <small class="dim">The captured area, ${r.w} × ${r.h} px, enlarged.${r.strip ? " The ring marks where the strip starts." : ""}</small></div>
    ${facts.length ? `<dl class="cc-facts">${facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(String(v))}</dd>`).join("")}</dl>` : ""}`;
}
