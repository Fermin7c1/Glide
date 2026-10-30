// Settings > Overlay > Position: how to move the overlay, and a button that unlocks it.
import { call } from "../bridge.js";
import { $, icon } from "../dom.js";
import { Group } from "../settings-schema.js";

export function renderOverlayUnlock(_g: Group, host: HTMLElement): void {
  host.innerHTML = `<div class="field"><div class="lab">Move it<small>Hold Alt with the mouse over the overlay and drag, in game too. The mouse wheel changes its width; right-click collapses it. Ctrl+Alt+O unlocks it without holding a key.</small></div>
      <div class="ctl"><button class="btn" id="ov-unlock">${icon("maximize")}Unlock overlay</button></div></div>`;
  $("ov-unlock").onclick = () => call("overlay:unlock", true);
}
