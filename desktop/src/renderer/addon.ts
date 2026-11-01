// The Addon page: the WoW client folder (config.json wow_dir: typed, browsed or found) and the Glide addon in it,
// with what to do next.
import type { RxpImport } from "../shared/ipc.js";
import { call, quiet } from "./bridge.js";
import { $, esc, icon, toast } from "./dom.js";
import { enginePost, live } from "./state.js";
import { refreshStatus } from "./status.js";

export async function refreshAddon(): Promise<void> {
  live.config = (await call("config:get")) || live.config;
  $<HTMLInputElement>("wow-dir").value = live.config.wow_dir || "";
  const s = await call("addon:status");
  if (!s) return;
  $("wow-msg").innerHTML = !s.wowDir ? `<span class="warn">Not set yet. Use Find it or Browse.</span>`
    : s.wowOk ? `<span class="ok">This is a WoW client folder.</span>` : `<span class="bad">This doesn't look like a WoW client folder: no Wow*.exe, Interface or WTF in it.</span>`;
  const flavor = s.wowDir ? String(s.wowDir).split(/[\\/]/).filter(Boolean).pop()!.replace(/^_|_$/g, "") : "";
  // the leaf says what to do; the versions are in the table below
  $("addon-hero").innerHTML = !s.wowOk ? `<b>Set the WoW folder first</b><p>Glide installs its addon there and reads your recordings from it.</p>`
    : s.installed ? `<b>${s.upToDate ? `Glide addon ${esc(s.installed)} is installed and up to date` : `Update the Glide addon to ${esc(s.bundled)}`}</b><p>${s.upToDate ? `In ${esc(flavor)}. Nothing to do.` : `You have ${esc(s.installed)} in ${esc(flavor)}. Update it below, then restart WoW completely.`}</p>`
    : `<b>Install the Glide addon</b><p>Install it below, then restart WoW and enable Glide in the AddOns list.</p>`;
  $("addon-table").innerHTML = `<dt>Comes with this app</dt><dd>${esc(s.bundled || "?")}</dd>
    <dt>Installed in the game</dt><dd>${s.installed ? esc(s.installed) : '<span class="dim">Not installed</span>'}</dd>
    <dt>RestedXP</dt><dd>${s.rxp ? '<span class="ok">Installed</span>' : `<span class="warn">Not found. The coach follows RestedXP's guides, so install it.</span>`}</dd>`;
  const b = $<HTMLButtonElement>("addon-install");
  b.disabled = !s.wowOk;
  b.innerHTML = icon("download") + (s.installed ? (s.upToDate ? "Reinstall addon" : `Update to ${esc(s.bundled)}`) : "Install addon");
  $<HTMLButtonElement>("addon-open").disabled = !s.wowOk;
  $<HTMLButtonElement>("rxp-add").disabled = !s.wowOk;
  addonCurrent = !!s.installed && s.upToDate;
  refreshRxp();
}

// ---- RestedXP guide strings added here (companion/rxp_import.py) ----
let addonCurrent = false;     // the game's Glide addon can hand strings to RestedXP (installed and up to date)

const guideCount = (n: number) => `${n} ${n === 1 ? "guide" : "guides"}`;

/** "1-6 Durotar to 50-60 Silithus (12 guides)": the first and last guide name the pack. */
function packName(names: string[]): string {
  if (names.length <= 1) return names[0] || "A guide";
  return `${names[0]} to ${names[names.length - 1]} (${guideCount(names.length)})`;
}

function rxpState(r: RxpImport): string {
  if (r.rxp === "done") return `<span class="ok">RestedXP has it.</span>`;
  if (r.rxp === "failed") return `<span class="bad">RestedXP couldn't add it: ${esc(r.error || "no reason given")}.</span> <span class="dim">Type /glide rxpimport in the game to try again.</span>`;
  return addonCurrent ? `<span class="warn">RestedXP gets it at your next login or /reload.</span>`
    : `<span class="warn">Install or update the Glide addon above, then RestedXP gets it at your next login.</span>`;
}

async function refreshRxp(): Promise<void> {
  const list = (await quiet("addon:rxpImports")) || [];
  $("rxp-list").innerHTML = list.map((r) => {
    const when = r.added ? new Date(r.added * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "";
    return `<li><span><b>${esc(packName(r.guides))}</b>${when ? ` <span class="dim">added ${esc(when)}</span>` : ""}</span><span>${rxpState(r)}</span></li>`;
  }).join("");
}

async function addRxpGuide(): Promise<void> {
  const box = $<HTMLTextAreaElement>("rxp-string"), btn = $<HTMLButtonElement>("rxp-add");
  if (!box.value.trim()) {
    $("rxp-msg").innerHTML = `<span class="warn">Paste the string RestedXP gave you first.</span>`;
    return;
  }
  btn.disabled = true;
  $("rxp-msg").innerHTML = `<span class="dim">Checking it against your RestedXP account…</span>`;
  const r = await call("addon:rxpImport", box.value);
  btn.disabled = false;
  if (!r) { $("rxp-msg").innerHTML = ""; return; }
  if (!r.ok) { $("rxp-msg").innerHTML = `<span class="bad">${esc(r.error || "It couldn't be added.")}</span>`; return; }
  box.value = "";
  let coach = "The coach learns the route when it starts.";
  if (live.engineUrl) {
    // the knowledge refresh reads the new guides; coaching goes on meanwhile
    try { await enginePost("/refresh", {}); coach = "The coach is learning the route now."; } catch { /* the next start reads it */ }
  }
  const rxp = !r.handoff ? "Install the Glide addon above so RestedXP gets it too."
    : addonCurrent ? "RestedXP gets it at your next login or /reload." : "Update the Glide addon above, then RestedXP gets it at your next login.";
  $("rxp-msg").innerHTML = `<span class="ok">${r.again ? "Added again" : "Added"}: ${esc(guideCount((r.guides || []).length))}.</span> ${coach} ${rxp}`;
  refreshRxp();
}

async function setWowDir(p: string): Promise<void> {
  if (!(await call("config:set", { wow_dir: p || null }))) return;   // not saved: the error toast says why
  refreshAddon();
  if (p && live.engineState === "running") {
    // the coach reads recordings, RestedXP and game files from it: restart it on the new folder; its background
    // refresh then rebuilds what the new folder changes
    toast("WoW folder saved. Restarting the coach to read it; install the addon next if it isn't there.", false, 7000);
    await call("engine:restart");
  } else {
    toast("WoW folder saved.");
  }
}

/** Find it: the clients on this PC, one button each. */
async function listClients(): Promise<void> {
  $("wow-found").innerHTML = `<span class="dim">Looking…</span>`;
  const found = (await call("addon:detect")) || [];
  $("wow-found").innerHTML = found.length ? found.map((f) => `<button class="btn" data-p="${esc(f.path)}">${icon("folder-open")}<b>${esc(f.flavor)}</b>&nbsp;${esc(f.path)}</button>`).join("")
    : `<span class="warn">No WoW install found here. Use Browse.</span>`;
  $("wow-found").querySelectorAll<HTMLButtonElement>("button").forEach((b) => (b.onclick = () => { $("wow-found").innerHTML = ""; setWowDir(b.dataset.p!); }));
}

export function wireAddon(): void {
  $("wow-dir").addEventListener("change", (e) => setWowDir((e.target as HTMLInputElement).value.trim()));
  $("wow-browse").onclick = async () => {
    const p = await call("dialog:folder", "World of Warcraft client folder (e.g. _classic_beta_)", live.config.wow_dir || undefined);
    if (p) setWowDir(p);
  };
  $("wow-detect").onclick = listClients;
  $("addon-install").onclick = async () => {
    const r = await call("addon:install");
    if (r) {
      $("addon-msg").innerHTML = `<span class="ok">${r.done.map(esc).join("<br>")}</span><br>Restart WoW completely to load it.`;
      refreshStatus();
      refreshAddon();
    }
  };
  $("addon-open").onclick = () => live.config.wow_dir && call("shell:open", live.config.wow_dir + "\\Interface\\AddOns");
  $("rxp-add").onclick = addRxpGuide;
}
