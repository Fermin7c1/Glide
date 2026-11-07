// The coach says when a Glide update is out (and again when it's downloaded), then reminds the player every 10 minutes
// while it waits: to download it, then to restart. Quiet while it downloads; a newer version starts over with its own
// announcement. Pure (the voice and the timers are passed in), so tests run it under Node (tests/test_update_voice.py).
import type { UpdateStatus } from "./types.js";

export const REMIND_MS = 10 * 60 * 1000;

export interface UpdateVoiceDeps {
  say(text: string, style: string): void;
  /** the coach says it in its own voice, with what's new (the engine's POST /announce): true when it took it */
  announce?(what: Waiting, version: string, title: string, notes: string[]): Promise<boolean>;
  /** the voice's acting direction when the coach plays a character, else "" (read each time a line is said) */
  style(): string | Promise<string>;
  setInterval(fn: () => void, ms: number): unknown;
  clearInterval(handle: unknown): void;
}

type Waiting = "available" | "ready";

export const updateLines = {
  available: (v: string) => `A Glide update is out: version ${v}. Download it from the footer button when it suits you.`,
  ready: (v: string) => `Glide ${v} is downloaded. Restart to update from the footer button when it suits you.`,
  remindAvailable: (v: string) => `Reminder: Glide ${v} is still waiting to be downloaded, footer button.`,
  remindReady: (v: string) => `Reminder: restart Glide to finish updating to ${v}, footer button.`,
};

/** " What's new: <first item>; <second item>, and more." from the notes ('' without any). */
export function whatsNew(notes: string[]): string {
  const firsts = notes.slice(0, 2).map((n) => n.split(/(?<=[.!?:;])\s/)[0].replace(/[.:;]+$/, ""));
  return firsts.length ? ` What's new: ${firsts.join("; ")}${notes.length > 2 ? ", and more" : ""}.` : "";
}

export class UpdateVoice {
  private state = "";                  // the last state seen
  private version = "";                // the version the announcements so far were about
  private said = new Set<Waiting>();   // announced for this version
  private timer: unknown = null;
  private gen = 0;                     // bumped by stop(): a line whose style lookup outlived its state is dropped

  constructor(private deps: UpdateVoiceDeps) {}

  onStatus(s: UpdateStatus): void {
    const st = String(s.state || "");
    const v = String(s.version || "");
    // the updater re-checks every 30 minutes (and on a click) while an update waits: "checking" then the same
    // answer again. That changes nothing: the reminders go on, the announcement isn't repeated
    if (st === "checking") return;
    if (st === "error") {                          // a failed re-check: quiet until the next answer, said stays said
      this.stop();
      this.state = st;
      return;
    }
    if (st !== "available" && st !== "ready" && st !== "downloading") {   // none, idle, disabled
      this.stop();
      this.said.clear();
      this.version = "";
      this.state = st;
      return;
    }
    if (v && v !== this.version) {                 // a newer version: start over
      this.stop();
      this.said.clear();
      this.version = v;
      this.state = "";
    }
    if (st === this.state) return;                 // the updater re-broadcasts its status
    this.state = st;
    this.stop();
    if (st === "downloading") return;              // paused while it downloads
    const ver = this.version;
    if (!this.said.has(st)) {
      this.said.add(st);
      this.announce(st, ver, s.title || "", s.notes || []);
    }
    const remind = st === "available" ? updateLines.remindAvailable : updateLines.remindReady;
    this.timer = this.deps.setInterval(() => this.speak(remind(ver)), REMIND_MS);
  }

  /** The announcement: the coach's, with what's new (deps.announce), else the plain line with its first items. */
  private announce(st: Waiting, ver: string, title: string, notes: string[]): void {
    const gen = this.gen;
    const plain = () => { if (gen === this.gen) this.speak(updateLines[st](ver) + whatsNew(notes)); };
    if (!this.deps.announce) { plain(); return; }
    void this.deps.announce(st, ver, title, notes).catch(() => false).then((took) => { if (!took) plain(); });
  }

  private speak(text: string): void {
    const gen = this.gen;
    let style: string | Promise<string>;
    try {
      style = this.deps.style();
    } catch {
      style = "";
    }
    void Promise.resolve(style).catch(() => "").then((x) => {
      if (gen === this.gen) this.deps.say(text, String(x || ""));     // the state moved on meanwhile: not said
    });
  }

  private stop(): void {
    this.gen++;
    if (this.timer !== null) this.deps.clearInterval(this.timer);
    this.timer = null;
  }
}
