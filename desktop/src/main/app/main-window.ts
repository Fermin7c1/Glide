// The main window: the coach console, settings, addon and logs (renderer/index.html). It remembers its size and
// place and its zoom, opens web links in the default browser, and hides instead of closing while the tray keeps
// the coach running.
import { BrowserWindow, shell } from "electron";
import fs from "fs";
import path from "path";
import type { EventChannel, EventMap } from "../../shared/ipc";
import { ICON, isOwnPage, PRELOAD, RENDERER } from "./assets";
import { settings } from "./settings";

// Zoom like a browser: Ctrl + mouse wheel, Ctrl+= / Ctrl+- / Ctrl+0. It covers the whole window, the coach
// dashboard included, and is remembered (app settings "zoom", a Chromium zoom level: 0 = 100%, each step ~10%).
const ZOOM_STEP = 0.5, ZOOM_MIN = -3, ZOOM_MAX = 5;

export class MainWindow {
  win: BrowserWindow | null = null;

  /** hideOnClose: whether closing the window should only hide it (close to tray, and not quitting). */
  constructor(private hideOnClose: () => boolean) {}

  /** Open and usable (not closed). */
  get alive(): boolean {
    return !!this.win && !this.win.isDestroyed();
  }

  /** Hidden in the tray, minimized or gone: how a silent update restart should bring Glide back. */
  get outOfSight(): boolean {
    const w = this.win;
    return !w || w.isDestroyed() || !w.isVisible() || w.isMinimized();
  }

  send<K extends EventChannel>(channel: K, payload: EventMap[K]): void {
    if (this.win && !this.win.isDestroyed()) this.win.webContents.send(channel, payload);
  }

  create(): void {
    const b = settings.current.window || {};
    // after a silent update restart, come back the way Glide was: in the tray when the window was hidden
    const startHidden = !!settings.current.resumeHidden && !!settings.current.closeToTray;
    if (settings.current.resumeHidden) {
      settings.current.resumeHidden = false;
      settings.save();
    }
    const win = new BrowserWindow({
      show: !startHidden,
      x: b.x, y: b.y, width: b.width || 1400, height: b.height || 900, minWidth: 900, minHeight: 600,
      title: "Glide", backgroundColor: "#0c0d12", icon: fs.existsSync(ICON) ? ICON : undefined,
      autoHideMenuBar: true,
      webPreferences: { preload: PRELOAD, contextIsolation: true, sandbox: true,
                        backgroundThrottling: false },    // speech keeps working while the window is hidden
    });
    this.win = win;
    if (b.maximized) win.maximize();
    win.loadFile(path.join(RENDERER, "index.html"));
    setupZoom(win);
    openLinksOutside(win);
    const saveBounds = () => {
      if (win.isDestroyed()) return;
      settings.current.window = { ...win.getNormalBounds(), maximized: win.isMaximized() };
      settings.save();
    };
    win.on("resize", debounce(saveBounds, 800));
    win.on("move", debounce(saveBounds, 800));
    win.on("close", (e) => {
      if (this.hideOnClose()) {
        e.preventDefault();
        win.hide();
      }
    });
    win.on("closed", () => (this.win = null));
  }

  show(): void {
    if (!this.win) this.create();
    this.win?.show();
    this.win?.focus();
  }
}

/** Links from the dashboard (step videos, web sources) open in the default browser. */
function openLinksOutside(win: BrowserWindow): void {
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  // the window only ever shows Glide's own page: a link opens in the browser (never another scheme: a file, an
  // app's handler), and a file dropped on the window doesn't replace it
  win.webContents.on("will-navigate", (e, url) => {
    if (isOwnPage(url)) return;
    e.preventDefault();
    if (/^https?:\/\//.test(url)) shell.openExternal(url);
  });
}

function setZoom(w: BrowserWindow, level: number): void {
  const z = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Math.round(level * 2) / 2));
  w.webContents.setZoomLevel(z);
  if (settings.current.zoom !== z) {
    settings.current.zoom = z;
    settings.save();
  }
}

function setupZoom(w: BrowserWindow): void {
  w.webContents.on("did-finish-load", () => w.webContents.setZoomLevel(settings.current.zoom ?? 0));
  w.webContents.on("zoom-changed", (_e, dir) => setZoom(w, w.webContents.getZoomLevel() + (dir === "in" ? ZOOM_STEP : -ZOOM_STEP)));
  w.webContents.on("before-input-event", (e, input) => {
    if (input.type !== "keyDown" || !input.control || input.alt) return;
    const k = input.key;
    if (k === "=" || k === "+") setZoom(w, w.webContents.getZoomLevel() + ZOOM_STEP);
    else if (k === "-" || k === "_") setZoom(w, w.webContents.getZoomLevel() - ZOOM_STEP);
    else if (k === "0") setZoom(w, 0);
    else return;
    e.preventDefault();
  });
}

function debounce<T extends unknown[]>(fn: (...a: T) => void, ms: number) {
  let t: NodeJS.Timeout | null = null;
  return (...a: T) => {
    if (t) clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}
