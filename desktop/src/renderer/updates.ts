// Updates in the window: announced once with a toast, then offered by the footer button (Download, then Restart to
// update; the download's progress shows in the footer's activity), with the check's state on the version number's tooltip.
import { taskBar } from "./activity.js";
import { call, glide } from "./bridge.js";
import { $, toast } from "./dom.js";
import { live } from "./state.js";
import { refreshStatus } from "./status.js";
import { UpdateStatus } from "./types.js";
import { showWhatsNew, wnUpdateLine } from "./whats-new.js";

export function onUpdate(s: UpdateStatus): void {
  const was = live.update.state;
  live.update = s;
  if (was !== s.state && s.state === "available") {
    refreshStatus();
    toast(`Glide ${s.version} is out. Download it from the footer when it suits you.`);
  }
  if (was !== s.state && s.state === "ready") {
    refreshStatus();
    toast(`Glide ${s.version} is downloaded. Restart to update when it suits you (footer button).`);
  }
  showFooterButton(s);
  taskBar();
  wnUpdateLine();
  $("app-ver").title = s.state === "none" ? `What's new. Up to date (checked ${new Date(s.checkedAt!).toLocaleTimeString()})`
    : s.state === "disabled" ? (s.error || "automatic updates are off") : s.state === "error" ? `update check failed: ${s.error}. Click to retry`
    : s.state === "checking" ? "checking for updates…" : "click to check for updates";
}

function showFooterButton(s: UpdateStatus): void {
  const ub = $("sb-update");
  ub.hidden = s.state !== "ready" && s.state !== "available";
  ub.dataset.act = s.state === "available" ? "download" : "install";
  if (s.state !== "ready") (ub as HTMLButtonElement).disabled = false;      // only an install in progress keeps it off
  ub.querySelector("use")?.setAttribute("href", s.state === "available" ? "#i-download" : "#i-rotate-cw");
  $("sb-update-l").textContent = s.state === "available" ? `Download Glide ${s.version || ""}` : `Restart to update to ${s.version || ""}`;
  ub.title = s.state === "available" ? `A newer version, Glide ${s.version || ""}, is out (you have ${s.current || "?"}). Click to download it; you choose when to install.`
    : `Glide ${s.version || ""} is downloaded. Click to install it: Glide closes, updates and opens again.`;
}

export function wireUpdates(): void {
  glide.on("update:status", onUpdate);
  glide.on("app:notice", (m) => toast(String(m)));
  $("app-ver").onclick = () => showWhatsNew();
  $("wn-check").onclick = () => call("update:check");
  $("sb-update").onclick = () => {
    const ub = $<HTMLButtonElement>("sb-update");
    if (ub.dataset.act === "download") return call("update:download");
    ub.disabled = true;                          // one click: Glide is on its way to restarting
    $("sb-update-l").textContent = "Restarting…";
    return call("update:install");
  };
}
