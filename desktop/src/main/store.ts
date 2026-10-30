// Small JSON stores: the app's own settings (userData/app-settings.json) and the engine's config.json.
import fs from "fs";
import path from "path";
import type { AppSettings } from "../shared/ipc";
import { appSettingsPath } from "./paths";

export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
export type Obj = { [k: string]: Json };

/** A JSON file's object: {} when the file is missing or isn't a JSON object (readJsonFile tells the two apart). */
export function readJson(p: string): Obj {
  return readJsonFile(p).value;
}

/** A JSON file's object, and why it couldn't be read when it is there but isn't a JSON object (`bad`); a missing
 * file is just {}. A writer should leave a bad file alone: saving {} plus its change would lose everything in it. */
export function readJsonFile(p: string): { value: Obj; bad?: string } {
  let text: string;
  try {
    text = fs.readFileSync(p, "utf8");
  } catch (e) {
    return (e as NodeJS.ErrnoException).code === "ENOENT" ? { value: {} } : { value: {}, bad: (e as Error).message };
  }
  try {
    const v = JSON.parse(text.replace(/^﻿/, ""));
    return v && typeof v === "object" && !Array.isArray(v) ? { value: v } : { value: {}, bad: "not a JSON object" };
  } catch (e) {
    return { value: {}, bad: (e as Error).message };
  }
}

/** Copy an unreadable file to <name>.<time>.bad next to it, once per content: the copy's path, or null when it
 * couldn't be made. */
export function keepBadCopy(p: string): string | null {
  try {
    const text = fs.readFileSync(p);
    const dir = path.dirname(p), base = path.basename(p);
    const same = fs.readdirSync(dir).filter((f) => f.startsWith(`${base}.`) && f.endsWith(".bad")).sort().reverse()
      .find((f) => fs.readFileSync(path.join(dir, f)).equals(text));
    if (same) return path.join(dir, same);
    const copy = path.join(dir, `${base}.${new Date().toISOString().replace(/[:.]/g, "-")}.bad`);
    fs.writeFileSync(copy, text);
    return copy;
  } catch {
    return null;
  }
}

/** Why a file can't be read, for the player: what happened, where the copy is, what to do. */
export function unreadableMessage(p: string, bad: string, copy: string | null, after: string): string {
  return `${path.basename(p)} can't be read (${bad}), so ${after} and the file was left as it is.`
    + `${copy ? ` A copy is in ${path.basename(copy)}.` : ""} Fix or delete ${p}`;
}

/** Atomic write (temp file + rename), like the engine's own config writer. */
export function writeJson(p: string, v: Json): void {
  fs.mkdirSync(path.dirname(p), { recursive: true });
  const tmp = `${p}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(v, null, 2) + "\n", "utf8");
  fs.renameSync(tmp, p);
}

export const APP_DEFAULTS: Required<Pick<AppSettings, "closeToTray" | "autoStartEngine">> & AppSettings = {
  closeToTray: false,
  autoStartEngine: true,
  overlay: { enabled: false, width: 560, height: 120, collapsed: false, opacity: 0.85, fontSize: 13 },
  speech: { provider: "windows", voiceName: "", rate: 1, volume: 1 },
  engineArgs: { sim: false, noPtt: false, noArchive: false, noKb: false },
};

// app-settings.json was there but unreadable when this run loaded it: nothing is saved over it until the app
// restarts (saving the defaults plus a change would lose every setting in it); the message says why
let appSettingsBad: string | null = null;

/** Why the app's settings aren't being saved this run, or null. */
export const appSettingsProblem = () => appSettingsBad;

export function loadAppSettings(): AppSettings {
  const p = appSettingsPath();
  const file = readJsonFile(p);
  appSettingsBad = file.bad === undefined ? null
    : unreadableMessage(p, file.bad, keepBadCopy(p), "Glide is using its default settings, changes aren't saved")
      + ", then restart Glide.";
  const s = file.value as AppSettings;
  if (!s.overlayOffByDefault) {
    // the overlay is opt-in now: switch it off once on installs that saved the old "on" default; the user's
    // choice in Settings > Overlay (or the tray) counts from here on
    s.overlay = { ...(s.overlay || {}), enabled: false };
    s.overlayOffByDefault = true;
    saveAppSettings(s);
  }
  return {
    ...APP_DEFAULTS, ...s,
    overlay: { ...APP_DEFAULTS.overlay, ...(s.overlay || {}) },
    speech: { ...APP_DEFAULTS.speech, ...(s.speech || {}) },
    engineArgs: { ...APP_DEFAULTS.engineArgs, ...(s.engineArgs || {}) },
  };
}

/** Save the app's settings; false (nothing written) while the file this run loaded is unreadable. */
export function saveAppSettings(s: AppSettings): boolean {
  if (appSettingsBad) return false;
  writeJson(appSettingsPath(), s as unknown as Json);
  return true;
}

/** Deep merge for config.json updates: objects merge, everything else (arrays included) replaces. A null value
 * removes the key, so a setting can go back to the engine's default. */
export function deepMerge(base: Obj, patch: Obj): Obj {
  const out: Obj = { ...base };
  for (const [k, v] of Object.entries(patch)) {
    if (v === null) delete out[k];
    else if (v && typeof v === "object" && !Array.isArray(v) && out[k] && typeof out[k] === "object" && !Array.isArray(out[k]))
      out[k] = deepMerge(out[k] as Obj, v as Obj);
    else out[k] = v;
  }
  return out;
}
