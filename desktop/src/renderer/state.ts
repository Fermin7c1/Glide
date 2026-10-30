// What the window currently knows, shared by its pages: the coach (engine) and its streamed status, the app's
// settings and config.json, and the speech and update status from the main process. Each value has one writer
// (named below); the others read it.
import { AppSettings, EngineRunState, NavBuild, Obj, Refresh, Reminder, SpeechStatus, UpdateStatus } from "./types.js";

export const live = {
  engineUrl: null as string | null,            // the coach's HTTP server while it runs (coach.ts)
  engineToken: "",                             // this run's token, which the coach asks for on every POST (coach.ts)
  engineState: "stopped" as EngineRunState,       // coach.ts
  appSettings: {} as AppSettings,              // app-settings.ts
  config: {} as Obj,                           // config.json as last read (Settings, Addon)
  refresh: null as Refresh | null,                 // the engine's refresher status, from the state stream (every second)
  nav: null as NavBuild | null,                     // the walking map build's status, from the same stream
  reminders: [] as Reminder[],                      // the open reminders, from the same stream
  speech: {} as Partial<SpeechStatus>,         // offline speech recognition (settings/whisper.ts)
  update: {} as Partial<UpdateStatus>,         // the updater (updates.ts)
};

/** The coach's HTTP server, for the pages that talk to it directly (the routes are the dashboard's). Each request
 * carries this run's token (the coach refuses POSTs and its memory without it). */
const tokenHeader = () => ({ "X-Glide-Token": live.engineToken });
export const engineGet = (path: string) => fetch(`${live.engineUrl}${path}`, { headers: tokenHeader() });
export const enginePost = (path: string, body: unknown, headers?: Record<string, string>) =>
  fetch(`${live.engineUrl}${path}`, { method: "POST", headers: { ...headers, ...tokenHeader() }, body: JSON.stringify(body) });
export const JSON_HEADERS = { "content-type": "application/json" };

/** The coach is up and answering (not only started). */
export const coachRunning = () => !!live.engineUrl && live.engineState === "running";
