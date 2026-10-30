// The Addon page: the WoW client folder (config.json wow_dir: typed, browsed or found) and the Glide addon in it,
// with what to do next.
import { call } from "./bridge.js";
import { $, esc, icon, toast } from "./dom.js";
import { live } from "./state.js";
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
}
