// The WoW client folder and the Glide addon: finding the install, reading versions, installing / updating.
//
// config.json "wow_dir" is a client flavor folder, e.g. C:\Games\World of Warcraft\_classic_beta_; the addon goes
// to <wow_dir>\Interface\AddOns\Glide. The engine reads SavedVariables and Cache\WDB from the same folder.
import { execFile } from "child_process";
import fs from "fs";
import path from "path";
import type { AddonStatus, WowClient } from "../shared/ipc";

export function tocVersion(addonDir: string): string | null {
  try {
    const m = fs.readFileSync(path.join(addonDir, "Glide.toc"), "utf8").match(/^##\s*Version:\s*(.+)$/m);
    return m ? m[1].trim() : null;
  } catch {
    return null;
  }
}

/** A flavor folder looks like a client: it has a Wow*.exe, Interface or WTF. */
function isFlavor(dir: string): boolean {
  try {
    return fs.readdirSync(dir).some((f) => /^wow.*\.exe$/i.test(f) || f === "Interface" || f === "WTF");
  } catch {
    return false;
  }
}

function regQuery(key: string, value: string): Promise<string | null> {
  return new Promise((resolve) => {
    execFile("reg", ["query", key, "/v", value], { windowsHide: true }, (err, out) => {
      if (err) return resolve(null);
      const m = out.match(new RegExp(`${value}\\s+REG_\\w+\\s+(.+)`));
      resolve(m ? m[1].trim() : null);
    });
  });
}

/** Client flavor folders found on this PC: [{path, flavor}], most likely first. */
export async function detectWow(): Promise<WowClient[]> {
  const roots = new Set<string>();
  for (const k of ["HKLM\\SOFTWARE\\WOW6432Node\\Blizzard Entertainment\\World of Warcraft",
                   "HKLM\\SOFTWARE\\Blizzard Entertainment\\World of Warcraft"]) {
    const p = await regQuery(k, "InstallPath");
    if (p) roots.add(path.resolve(p.replace(/[\\/]_\w+_[\\/]?$/, "")));
  }
  for (const drive of ["C", "D", "E", "F", "G"]) {
    for (const rel of ["Program Files (x86)\\World of Warcraft", "Program Files\\World of Warcraft",
                       "World of Warcraft", "Blizzard\\World of Warcraft", "Games\\World of Warcraft",
                       "Battle.net\\World of Warcraft"]) {
      const p = `${drive}:\\${rel}`;
      if (fs.existsSync(p)) roots.add(p);
    }
  }
  const out: WowClient[] = [];
  for (const root of roots) {
    let subs: string[] = [];
    try {
      subs = fs.readdirSync(root).filter((d) => /^_\w+_$/.test(d));
    } catch {
      continue;
    }
    for (const d of subs) {
      const p = path.join(root, d);
      if (isFlavor(p)) out.push({ path: p, flavor: d.replace(/^_|_$/g, "") });
    }
  }
  // Forever is a classic client; beta and PTR clients first since that's where it runs before launch
  const rank = (f: string) => (/beta/.test(f) ? 0 : /classic/.test(f) ? 1 : /ptr/.test(f) ? 2 : 3);
  return out.sort((a, b) => rank(a.flavor) - rank(b.flavor));
}

export function status(engineHome: string, wowDir: string | null): AddonStatus {
  const bundled = tocVersion(path.join(engineHome, "addon", "Glide"));
  const wowOk = !!wowDir && fs.existsSync(wowDir) && isFlavor(wowDir);
  const addonsDir = wowDir ? path.join(wowDir, "Interface", "AddOns") : null;
  const installed = addonsDir ? tocVersion(path.join(addonsDir, "Glide")) : null;
  const rxp = !!addonsDir && fs.existsSync(path.join(addonsDir, "RXPGuides"));
  return { wowDir, wowOk, addonsDir, bundled, installed, rxp, upToDate: !!installed && installed === bundled };
}

/** Copy the bundled addon into the game. The old folder is replaced as a whole (the addon's settings and recordings
 * live in WTF\...\SavedVariables, not here). */
export function install(engineHome: string, wowDir: string): string[] {
  const src = path.join(engineHome, "addon", "Glide");
  if (!fs.existsSync(path.join(src, "Glide.toc"))) throw new Error(`the bundled addon is missing (${src})`);
  if (!isFlavor(wowDir)) throw new Error(`${wowDir} doesn't look like a WoW client folder (no Wow*.exe, Interface or WTF)`);
  const addons = path.join(wowDir, "Interface", "AddOns");
  fs.mkdirSync(addons, { recursive: true });
  const dst = path.join(addons, "Glide");
  const tmp = path.join(addons, `.Glide.installing`);
  fs.rmSync(tmp, { recursive: true, force: true });
  fs.cpSync(src, tmp, { recursive: true });
  fs.rmSync(dst, { recursive: true, force: true });
  fs.renameSync(tmp, dst);
  return [`Glide ${tocVersion(dst) || "?"} installed in ${dst}`];
}
