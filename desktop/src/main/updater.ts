// Auto-update from GitHub Releases (the public Fermin7c1/Glide-releases repo; package.json "build.publish").
//
// GitHub Actions builds an installer whenever desktop/package.json's version changes on main and publishes it there
// with latest.yml. The installed app checks at startup and every 30 minutes; a newer version is announced (footer
// button, Status page, tray) and downloads when the user clicks Download (the footer shows the progress).
// It installs only when the user clicks "Restart to update" (footer, Status page or tray): the installer
// runs silently under Glide's own "Updating Glide" window (splash.ts), then Glide starts again. Nothing installs
// unseen, not even on quit. The engine code in the engine home is replaced on the first start of the new version
// (paths.syncEngine); data\ and config.json are kept.
import { app } from "electron";
import { autoUpdater, UpdateInfo } from "electron-updater";
import { EventEmitter } from "events";
import type { UpdateStatus } from "../shared/ipc";

const CHECK_EVERY_MS = 30 * 60 * 1000;

/** An updater error as one short line (electron-updater's messages run on with the HTTP response). */
const firstLine = (e: any, max: number) => String(e?.message || e).split("\n")[0].slice(0, max);

export class Updater extends EventEmitter {
  status: UpdateStatus = { state: "idle", current: app.getVersion() };
  private timer: NodeJS.Timeout | null = null;

  constructor(private log: (m: string) => void) {
    super();
    autoUpdater.autoDownload = false;              // the user clicks Download
    autoUpdater.autoInstallOnAppQuit = false;      // the user starts the install: nothing happens unseen
    autoUpdater.logger = { info: () => undefined, warn: (m: unknown) => log(`[update] ${m}`),
                           error: (m: unknown) => log(`[update] ${m}`), debug: () => undefined } as never;
    autoUpdater.on("checking-for-update", () => this.set({ state: "checking" }));
    autoUpdater.on("update-not-available", () => this.set({ state: "none", checkedAt: Date.now() }));
    autoUpdater.on("update-available", (i: UpdateInfo) => {
      if (this.status.state === "downloading" || this.status.state === "ready") return;
      const fresh = this.status.version !== i.version || this.status.state !== "available";
      this.set({ state: "available", version: i.version, percent: undefined, checkedAt: Date.now() });
      if (fresh) log(`[update] Glide ${i.version} is available: Download gets it`);
    });
    autoUpdater.on("download-progress", (p) => this.set({ state: "downloading", percent: Math.round(p.percent) }));
    autoUpdater.on("update-downloaded", (i: UpdateInfo) => {
      this.set({ state: "ready", version: i.version, percent: 100 });
      log(`[update] Glide ${i.version} is ready: Restart to update installs it`);
    });
    autoUpdater.on("error", (e) => {
      log(`[update] check failed: ${firstLine(e, 200)}`);
      this.set({ state: "error", error: firstLine(e, 300), checkedAt: Date.now() });
    });
  }

  private set(p: Partial<UpdateStatus>): void {
    this.status = { ...this.status, ...p };
    if (p.state && p.state !== "error") delete this.status.error;
    this.emit("status", this.status);
  }

  /** Start checking (installed builds only; a dev run has nothing to update). */
  start(enabled: boolean): void {
    this.stop();
    if (!app.isPackaged) return this.set({ state: "disabled", error: "updates are off in a dev run" });
    if (!enabled) return this.set({ state: "disabled" });
    this.check();
    this.timer = setInterval(() => this.check(), CHECK_EVERY_MS);
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  /** Download the newer version found by the last check (the user clicked Download). */
  download(): void {
    if (!app.isPackaged || this.status.state !== "available") return;
    this.set({ state: "downloading", percent: 0 });
    this.log(`[update] downloading Glide ${this.status.version}`);
    autoUpdater.downloadUpdate().catch((e) => this.set({ state: "error", error: firstLine(e, 300) }));
  }

  check(): void {
    if (!app.isPackaged || this.status.state === "downloading" || this.status.state === "ready") return;
    autoUpdater.checkForUpdates().catch((e) => this.set({ state: "error", error: String(e?.message || e).slice(0, 300) }));
  }

  /** Quit, run the installer silently and start the new version (the app's quit handling stops the coach first;
   * main.ts shows the "Updating Glide" window meanwhile). */
  installNow(): boolean {
    if (this.status.state !== "ready") return false;
    autoUpdater.quitAndInstall(true, true);
    return true;
  }
}
