// The desktop IPC (contract #4): every channel a window may invoke (InvokeMap: channel -> its arguments and what it
// answers) and every event the main process sends the windows (EventMap), with the shapes they carry. main/app/ipc.ts
// answers with these, main/preload.ts lets exactly these through and the renderer calls them.
//
// TYPE-ONLY: the main process (CommonJS) and the renderer (ES modules) both compile this file into dist/shared, and
// the sandboxed preload can't load a local module at all, so nothing here may exist at run time. Import it with
// `import type` only.

/** Free-form JSON: config.json, the release notes. */
export type Obj = Record<string, any>;

/** The coach process as the main process runs it (main/engine.ts). */
export type EngineRunState = "stopped" | "building" | "starting" | "running" | "stopping" | "crashed";
export interface EngineStatus { state: EngineRunState; detail: string; port: number; url: string | null; token: string }
export interface LogLine { t: number; stream: "out" | "err" | "app"; text: string }

export interface AppInfo {
  version: string; dev: boolean; home: string; defaultHome: string; checkout: boolean;
  python: { cmd: string; args: string[]; bundled: boolean }; configPath: string; electron: string;
}

export type TtsProvider = "windows" | "openai" | "elevenlabs" | "azure" | "google";
export interface SpeechSettings {
  provider?: TtsProvider;
  voiceName?: string;             // Windows voice
  rate?: number;
  volume?: number;
  openai?: { model?: string; voice?: string; instructions?: string };
  elevenlabs?: { voiceId?: string; model?: string };
  azure?: { region?: string; voice?: string };
  google?: { voice?: string };
}
export interface Voice { id: string; name: string }

/** The app's own settings (userData/app-settings.json, main/store.ts). */
export interface AppSettings {
  engineHome?: string;            // overrides paths.defaultHome
  closeToTray?: boolean;          // closing the main window keeps the coach running in the tray (else it quits); a saved
                                  // value is never changed by an update (new installs start with "quit")
  autoStartEngine?: boolean;      // start the coach when the app opens
  overlayOffByDefault?: boolean;  // set once: the overlay became opt-in (0.3.14), installs from before start with it off
  overlay?: { enabled?: boolean; x?: number; y?: number; width?: number; height?: number; collapsed?: boolean;
              opacity?: number; fontSize?: number };
  speech?: SpeechSettings;
  engineArgs?: { sim?: boolean; noPtt?: boolean; noArchive?: boolean; noKb?: boolean };
  window?: { x?: number; y?: number; width?: number; height?: number; maximized?: boolean };
  zoom?: number;                  // main window zoom level (Ctrl + wheel), 0 = 100%
  autoUpdate?: boolean;           // download new versions from GitHub and install them
  muted?: boolean;                // the narrator is muted (footer button, tray, Ctrl+Alt+M); callouts still show
  resumeHidden?: boolean;         // set just before Restart to update: come back in the tray, as before
  lastVersion?: string;           // the version that ran last, to say "updated to x.y.z" once
  notesSeen?: string;             // the newest version whose notes the user has seen (What's new)
  sessionBarOrder?: string[];     // the footer's pieces (their data-sb keys) in the order the user dragged them into
}
/** A window's change to the app settings: objects merge, and a null value removes the key (back to the default). */
export type AppSettingsPatch = { [K in keyof AppSettings]?: AppSettings[K] | null };

export type SpeechState = "off" | "checking" | "ready" | "installing" | "downloading" | "error";
export interface SpeechStatus {
  state: SpeechState;
  modelName: string;
  package?: boolean;
  version?: string;
  model?: string | null;          // folder the model loads from
  detail?: string;
  fetching?: string;              // the model being downloaded (not always the one in use)
  failed?: string;                // why the last ahead-of-time download failed
  gotMB?: number;                 // download progress
  totalMB?: number;
}
export interface WhisperModel { name: string; mb: number; note: string; downloaded: boolean; shipped: boolean }

export type UpdateState = "idle" | "disabled" | "checking" | "none" | "available" | "downloading" | "ready" | "error";
export interface UpdateStatus {
  state: UpdateState;
  current: string;
  version?: string;        // the newer version found / downloaded
  percent?: number;
  error?: string;
  checkedAt?: number;
}

export interface AddonStatus {
  wowDir: string | null;
  wowOk: boolean;
  addonsDir: string | null;
  bundled: string | null;        // version shipped with the app
  installed: string | null;      // version in the game's AddOns folder
  rxp: boolean;                  // RestedXP installed (its guides feed the coach)
  upToDate: boolean;
}
export interface WowClient { path: string; flavor: string }

/** How to get a key, shown under it in Settings: the page that makes keys, the steps, what a key looks like, cost.
 * plan: the badge (free = no card ever, free-tier = free with limits, credit = free starting credit, paid); free = the
 * free or cheapest way to use it. */
export interface KeyGuide { url: string; steps: string[]; looks?: string; cost?: string; plan?: "free" | "free-tier" | "credit" | "paid";
  free?: string; links?: { label: string; url: string }[] }
export interface KnownKey { env: string; label: string; help: string; kind: "model" | "search" | "voice"; guide?: KeyGuide }
/** A key's state for the window: never the key, only whether it is set and its last characters (hint). */
export interface KeyStatus { env: string; saved: boolean; fromEnv: boolean; hint: string }

/** Glide's CPU and memory (the app, the coach and its jobs), sampled while an activity shows. */
export interface Footprint { cpu: number; memMB: number; processes: number }

export interface EndpointTest { ok: boolean; models?: string[]; error?: string }

/** What each channel takes and answers (the renderer gets the answer once the main process's promise settles). */
export interface InvokeMap {
  // the coach process
  "engine:get": () => EngineStatus;
  "engine:log": () => LogLine[];
  "engine:start": () => void;
  "engine:stop": () => void;
  "engine:restart": () => void;
  // the app: its info and settings, the voice, the resource meter, updates and release notes
  "app:info": () => AppInfo;
  "app:settings:get": () => AppSettings;
  "app:settings:set": (patch: AppSettingsPatch) => AppSettings;
  "app:note": (line: string) => void;
  "app:logs": () => string;                   // open the log files' folder (userData/logs): "" or why it couldn't
  "app:changelog": () => Obj;
  "app:mute": (muted: boolean) => boolean;
  "tts:synth": (text: string) => { mime: string; data: Uint8Array };
  "tts:voices": (provider: TtsProvider) => Voice[];
  "res:watch": (on: boolean) => void;
  "update:get": () => UpdateStatus;
  "update:check": () => void;
  "update:install": () => void;
  "update:download": () => void;
  // config.json, API keys and custom endpoints
  "config:get": () => Obj;
  "config:set": (patch: Obj) => Obj;
  "keys:list": () => { known: KnownKey[]; status: KeyStatus[] };
  "keys:set": (env: string, value: string) => KeyStatus[];
  "endpoint:test": (baseUrl: string, keyEnv: string) => EndpointTest;
  // the addon and folders
  "addon:status": () => AddonStatus;
  "addon:detect": () => WowClient[];
  "addon:install": () => { done: string[]; status: AddonStatus };
  "dialog:folder": (title: string, defaultPath?: string) => string | null;
  "data:import": (from: string) => boolean;
  "shell:open": (target: string) => string | void;
  // offline speech recognition
  "speech:status": () => SpeechStatus;
  "speech:install": () => SpeechStatus;
  "speech:models": () => WhisperModel[];
  "speech:download": (name: string) => WhisperModel[];
  "speech:delete": (name: string) => { ok: boolean; models: WhisperModel[] };
  // the overlay
  "overlay:unlock": (on: boolean) => void;
  "overlay:resize": (height: number) => void;
  "overlay:width": (delta: number) => void;
  "overlay:dragStart": () => void;
}
export type InvokeChannel = keyof InvokeMap;
export type InvokeArgs<K extends InvokeChannel> = Parameters<InvokeMap[K]>;
export type InvokeResult<K extends InvokeChannel> = ReturnType<InvokeMap[K]>;

/** What the main process sends the windows, and what each event carries. */
export interface EventMap {
  "engine:state": EngineStatus;
  "engine:logline": LogLine;
  "app:settings": AppSettings;
  "app:notice": string;
  "update:status": UpdateStatus;
  "speech:status": SpeechStatus;
  "res:sample": Footprint;
  "overlay:unlocked": boolean;
  "overlay:held": boolean;
}
export type EventChannel = keyof EventMap;
