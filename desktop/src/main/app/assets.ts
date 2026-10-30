// The files the app's windows load, found from the compiled code (dist/main/app/*.js).
import path from "path";
import { pathToFileURL } from "url";

const DIST = path.join(__dirname, "..", "..");

/** The renderer's pages and scripts (dist/renderer). */
export const RENDERER = path.join(DIST, "renderer");
/** Glide's own pages (file:// URLs): the only ones its windows show, and the only senders its IPC answers. */
export const OWN_PAGES = ["index.html", "overlay.html"].map((f) => pathToFileURL(path.join(RENDERER, f)).href);
export const isOwnPage = (url: string) => OWN_PAGES.includes(String(url || "").split(/[?#]/)[0]);
/** The one preload script every window gets (dist/main/preload.js). */
export const PRELOAD = path.join(DIST, "main", "preload.js");
/** The app icon (desktop/assets/icon.png; inside app.asar when installed). */
export const ICON = path.join(DIST, "..", "assets", "icon.png");
