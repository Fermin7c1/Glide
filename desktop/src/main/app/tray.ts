// The tray icon and its menu: the coach's state and controls, the narrator's mute, the overlay, updates and Quit.
// The menu is rebuilt whenever something it shows changes.
import { Menu, MenuItemConstructorOptions, nativeImage, Tray } from "electron";
import fs from "fs";
import { Engine } from "../engine";
import { Updater } from "../updater";
import { ICON } from "./assets";
import { Overlay } from "./overlay";
import { settings } from "./settings";

export const OVERLAY_SHORTCUT = "CommandOrControl+Alt+O";
export const MUTE_SHORTCUT = "CommandOrControl+Alt+M";

/** What the menu's items do; the app wires them to the coach, the windows and the updater. */
export interface TrayActions {
  showMain(): void;
  startEngine(): void;
  stopEngine(): void;
  restartEngine(): void;
  setMuted(muted: boolean): void;
  showOverlay(on: boolean): void;
  installUpdate(): void;
  quit(): void;
}

export class AppTray {
  private tray: Tray | null = null;

  constructor(private engine: Engine, private updater: Updater, private overlay: Overlay, private actions: TrayActions) {}

  get exists(): boolean {
    return this.tray !== null;
  }

  create(): void {
    this.tray = new Tray(this.icon());
    this.tray.on("click", () => this.actions.showMain());
    this.update();
  }

  /** One representation per display scale, so the icon stays sharp at 125% / 150% / 200%. */
  private icon(): Electron.NativeImage {
    const img = nativeImage.createEmpty();
    if (fs.existsSync(ICON)) {
      const src = nativeImage.createFromPath(ICON);
      for (const scale of [1, 1.25, 1.5, 2]) {
        const px = Math.round(16 * scale);
        img.addRepresentation({ scaleFactor: scale, buffer: src.resize({ width: px, height: px, quality: "best" }).toPNG() });
      }
    } else {
      this.engine.note(`tray icon missing (${ICON})`);
    }
    return img;
  }

  update(): void {
    if (!this.tray) return;
    const st = this.engine.state;
    this.tray.setToolTip(`Glide: coach ${st}`);
    this.tray.setContextMenu(Menu.buildFromTemplate(this.menu()));
  }

  private menu(): MenuItemConstructorOptions[] {
    const a = this.actions, st = this.engine.state;
    return [
      { label: "Open Glide", click: a.showMain },
      { type: "separator" },
      { label: `Coach: ${st}`, enabled: false },
      st === "running" || st === "starting" || st === "building"
        ? { label: "Stop coach", click: () => a.stopEngine() }
        : { label: "Start coach", click: () => a.startEngine() },
      { label: "Restart coach", click: () => a.restartEngine() },
      { type: "separator" },
      { label: "Mute narrator", type: "checkbox", checked: !!settings.current.muted, accelerator: "Ctrl+Alt+M",
        click: (item) => a.setMuted(item.checked) },
      { type: "separator" },
      { label: "Show overlay", type: "checkbox", checked: settings.current.overlay?.enabled === true,
        click: (m) => a.showOverlay(m.checked) },
      { label: `Move / resize overlay (${OVERLAY_SHORTCUT.replace("CommandOrControl", "Ctrl")})`, type: "checkbox",
        checked: this.overlay.unlocked, enabled: !!this.overlay.win, click: (m) => this.overlay.setUnlocked(m.checked) },
      { type: "separator" },
      this.updateItem(),
      { label: "Quit", click: () => a.quit() },
    ];
  }

  /** Download, install, or check: whichever the updater's state calls for. */
  private updateItem(): MenuItemConstructorOptions {
    const s = this.updater.status;
    if (s.state === "ready") return { label: `Restart to update to ${s.version}`, click: () => this.actions.installUpdate() };
    if (s.state === "available") return { label: `Download Glide ${s.version}`, click: () => this.updater.download() };
    return { label: s.state === "downloading" ? `Downloading update ${s.version} (${s.percent ?? 0}%)` : "Check for updates",
             enabled: s.state !== "downloading", click: () => this.updater.check() };
  }
}
