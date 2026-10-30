// Where things live.
//
// Dev (npm start): the engine is the project folder itself (companion/, knowledge/, tools/, data/, config.json),
// so the app runs on the same data as start-glide.cmd.
//
// Installed: the engine code ships read-only in resources/engine. The app keeps a writable engine home
// (default %LOCALAPPDATA%\Glide\engine) with data/ and config.json; on first run and after each update the code
// folders there are replaced with the shipped ones. data/ and config.json are never touched by an update.
import { app } from "electron";
import fs from "fs";
import path from "path";

export const isDev = !app.isPackaged;

/** The project folder in dev (desktop/dist/main -> project root). */
export const devRoot = path.resolve(__dirname, "..", "..", "..");

/** The engine as shipped: the project folder in dev, resources/engine when installed. */
export const shippedEngine = isDev ? devRoot : path.join(process.resourcesPath, "engine");

// GLIDE_ENGINE_HOME overrides it (a clean first-run test without touching the real install)
export const defaultHome = process.env.GLIDE_ENGINE_HOME
  || (isDev ? devRoot : path.join(app.getPath("home"), "AppData", "Local", "Glide", "engine"));

/** App-level settings (engine home override, overlay bounds, window state): userData/app-settings.json. */
export const appSettingsPath = () => path.join(app.getPath("userData"), "app-settings.json");
export const secretsPath = () => path.join(app.getPath("userData"), "secrets.json");
/** Extra Python packages installed from the app (faster-whisper): userData/pylib, on the engine's PYTHONPATH. */
export const pylibDir = () => path.join(app.getPath("userData"), "pylib");
/** The Logs page on disk, a file per day (logfile.ts): userData/logs. */
export const logsDir = () => path.join(app.getPath("userData"), "logs");

/** Whisper speech models: ones the app downloaded (userData/models) first, then the ones shipped with it. */
export const userModelsDir = () => path.join(app.getPath("userData"), "models");
export const whisperDirs = () => [userModelsDir(),
  isDev ? path.join(devRoot, "desktop", "build", "models") : path.join(process.resourcesPath, "models")];

const CODE_DIRS = ["companion", "knowledge", "tools", "wowfiles", "addon"];

/** A project checkout (has .git): its code is used as it is, never replaced by the shipped copy. */
export function isCheckout(home: string): boolean {
  return fs.existsSync(path.join(home, ".git")) || path.resolve(home) === path.resolve(devRoot);
}

/** Make `home` a runnable engine: code folders from the shipped copy (when missing or from another app version),
 * the shipped community seed when the home has none. Returns what was done, for the log. Throws (replacing nothing)
 * when the home has code folders but no .engine-version stamp: Glide only replaces code it installed itself. */
export function syncEngine(home: string, version: string): string[] {
  const done: string[] = [];
  fs.mkdirSync(path.join(home, "data", "live"), { recursive: true });
  if (isCheckout(home)) return done;
  const stamp = path.join(home, ".engine-version");
  const have = fs.existsSync(stamp) ? fs.readFileSync(stamp, "utf8").trim() : "";
  const found = CODE_DIRS.filter((d) => fs.existsSync(path.join(home, d)));
  if (!have && found.length) {
    // code folders Glide didn't put there (no stamp): someone's own copy, never replace it
    throw new Error(`${home} already has ${found.join(", ")} folders that Glide didn't install, so Glide won't replace `
      + "them. Choose an empty folder as the engine home (Settings > General > Data), or use a git checkout of the project.");
  }
  if (have !== version || !CODE_DIRS.every((d) => fs.existsSync(path.join(home, d)))) {
    fs.writeFileSync(stamp, "installing");     // an interrupted install is still Glide's own: replaced next time
    for (const d of CODE_DIRS) {
      const dst = path.join(home, d);
      fs.rmSync(dst, { recursive: true, force: true });
      fs.cpSync(path.join(shippedEngine, d), dst, { recursive: true });
    }
    fs.writeFileSync(stamp, version);
    done.push(`engine code ${have || "(none)"} -> ${version}`);
  }
  const seed = path.join(home, "data", "seed", "seed.json.gz");
  const shippedSeed = path.join(shippedEngine, "data", "seed", "seed.json.gz");
  if (fs.existsSync(shippedSeed) && (!fs.existsSync(seed) || fs.statSync(shippedSeed).mtimeMs > fs.statSync(seed).mtimeMs)) {
    fs.mkdirSync(path.dirname(seed), { recursive: true });
    fs.cpSync(path.join(shippedEngine, "data", "seed"), path.join(home, "data", "seed"), { recursive: true });
    done.push("community seed data installed");
  }
  return done;
}

/** The Python that runs the engine: the bundled runtime when installed (or fetched in dev with `npm run python`),
 * else the system's `py -3` / `python`. */
export function pythonCommand(): { cmd: string; args: string[]; bundled: boolean } {
  const bundled = isDev
    ? path.join(devRoot, "desktop", "build", "python", "python.exe")
    : path.join(process.resourcesPath, "python", "python.exe");
  if (fs.existsSync(bundled)) return { cmd: bundled, args: [], bundled: true };
  return process.platform === "win32" ? { cmd: "py", args: ["-3"], bundled: false } : { cmd: "python3", args: [], bundled: false };
}
