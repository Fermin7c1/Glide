// Settings > General > First-run setup: walk through the new-player setup again, starting from what's saved.
import { $, icon } from "../dom.js";
import { Group } from "../settings-schema.js";
import { openSetup } from "../setup.js";

export function renderSetupAgain(_g: Group, host: HTMLElement): void {
  host.innerHTML = `<div class="field"><div class="lab">Run setup again<small>Your game, talking to the coach, its voice and AI help, one step at a time. It starts from your current settings.</small></div>
    <div class="ctl"><button class="btn" id="setup-again">${icon("flag")}Run setup again</button></div></div>`;
  $("setup-again").onclick = () => openSetup();
}
