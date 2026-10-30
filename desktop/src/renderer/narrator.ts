// The narrator: speaks the coach's lines ("speak" events on the engine's event stream) with the voice picked in
// Settings > Voice.
//
// Windows voices are spoken here (speechSynthesis); online providers (OpenAI, ElevenLabs, Azure, Google) return MP3
// from the main process, which is played in order. Each line's audio is requested as soon as it's queued, so the
// next one is usually ready when the current one ends. A failed request speaks that line with the Windows voice.
import { call, glide, ipcError } from "./bridge.js";
import { toast } from "./dom.js";
import { Obj, SpeechSettings } from "./types.js";

interface Line { text: string; audio: Promise<Blob | null> }

class Narrator {
  private provider = "windows";
  private voiceName = "";
  private rate = 1;
  private volume = 1;
  private queue: Line[] = [];
  private playing: HTMLAudioElement | null = null;
  private muted = false;                      // the narrator is muted (footer button, tray, Ctrl+Alt+M)
  private gen = 0;                            // bumped by stop(): stale plays don't start
  lastVoice = "";                             // the voice the last line asked Windows for (Settings > Voice, Logs)
  lastVoiceAt = 0;
  private lastError = 0;

  /** Take the voice settings (Settings > Voice, the coach page's slider, mute). Muting stops what's being said. */
  configure(muted: boolean, sp: SpeechSettings): void {
    if (muted && !this.muted) this.stop();
    this.muted = muted;
    this.provider = sp.provider || "windows";
    this.voiceName = sp.voiceName || "";
    this.rate = Number(sp.rate ?? 1);
    this.volume = Number(sp.volume ?? 1);
  }

  voices(): SpeechSynthesisVoice[] {
    return window.speechSynthesis ? speechSynthesis.getVoices() : [];
  }

  /** The Windows voices, once Chromium has loaded them (empty for a moment after start: speaking then would use
   * the default voice instead of the chosen one). Waits for voiceschanged, three seconds at most. */
  private voicesReady(): Promise<SpeechSynthesisVoice[]> {
    if (!window.speechSynthesis) return Promise.resolve([]);
    const now = speechSynthesis.getVoices();
    if (now.length) return Promise.resolve(now);
    return new Promise((resolve) => {
      const done = () => { speechSynthesis.removeEventListener("voiceschanged", done); clearTimeout(t); resolve(speechSynthesis.getVoices()); };
      const t = setTimeout(done, 3000);
      speechSynthesis.addEventListener("voiceschanged", done);
    });
  }

  /** The chosen voice: by exact name, else by its first part ("Microsoft Zira"), as Windows may relabel it. */
  private pickVoice(list: SpeechSynthesisVoice[]): SpeechSynthesisVoice | undefined {
    if (!this.voiceName) return undefined;
    const exact = list.find((x) => x.name === this.voiceName);
    if (exact) return exact;
    const stem = this.voiceName.split(" - ")[0].trim().toLowerCase();
    return list.find((x) => x.name.toLowerCase().startsWith(stem));
  }

  private async windowsSay(text: string): Promise<void> {
    if (!window.speechSynthesis) return;
    const gen = this.gen;
    const list = await this.voicesReady();
    if (gen !== this.gen) return;                 // stopped or interrupted while the voices loaded
    return new Promise((resolve) => {
      const u = new SpeechSynthesisUtterance(text);
      const v = this.pickVoice(list);
      if (v) u.voice = v;
      this.noteVoice(v ? v.name : `the Windows default${this.voiceName ? ` (${this.voiceName} not found among ${list.length} voices)` : ""}`);
      u.rate = this.rate;
      u.volume = this.volume;
      u.onend = u.onerror = () => resolve();
      speechSynthesis.speak(u);
    });
  }

  /** Log the voice in use: once per session and whenever it changes, so a wrong voice can be traced. */
  private noteVoice(name: string): void {
    if (name !== this.lastVoice) call("app:note", `[voice] speaking with ${name}`);
    this.lastVoice = name;
    this.lastVoiceAt = Date.now();
    const el = document.getElementById("sv-last");
    if (el) el.textContent = voiceLastLine();
  }

  say(text: string, interrupt = false): void {
    if (!text || this.muted) return;
    if (interrupt) this.stop();
    if (this.provider === "windows") {
      this.windowsSay(text);
      return;
    }
    const audio = glide.invoke("tts:synth", text)
      .then((r) => new Blob([r.data as BlobPart], { type: r.mime }))
      .catch((e) => {
        if (Date.now() - this.lastError > 60_000) {             // say why once a minute, not on every line
          this.lastError = Date.now();
          toast(`Voice (${this.provider}) failed, using the Windows voice: ${ipcError(e)}`, true, 9000);
        }
        return null;
      });
    this.queue.push({ text, audio });
    if (this.queue.length === 1 && !this.playing) this.next();
  }

  /** Speak the first queued line (its online audio, or the Windows voice when that failed), then the next. */
  private async next(): Promise<void> {
    const line = this.queue[0];
    if (!line) return;
    const gen = this.gen;
    const blob = await line.audio;
    if (gen !== this.gen) return;
    if (!blob) await this.windowsSay(line.text);
    else await this.play(blob);
    if (gen !== this.gen) return;
    this.queue.shift();
    this.next();
  }

  private async play(blob: Blob): Promise<void> {
    const url = URL.createObjectURL(blob);
    const a = new Audio(url);
    a.volume = Math.max(0, Math.min(1, this.volume));
    a.playbackRate = this.rate;
    a.preservesPitch = true;
    this.noteVoice(`${this.provider} (online voice)`);
    this.playing = a;
    await new Promise<void>((resolve) => {
      a.onended = a.onerror = a.onpause = () => resolve();
      a.play().catch(() => resolve());
    });
    URL.revokeObjectURL(url);
    this.playing = null;
  }

  stop(): void {
    this.gen++;
    this.queue = [];
    if (this.playing) {
      this.playing.pause();
      this.playing = null;
    }
    window.speechSynthesis?.cancel();
  }
}

export const speech = new Narrator();

/** The narrator's voice for the status bar: the provider and its model or voice, with the same defaults as main/tts.ts. */
export function speakLabel(sp: Obj): string {
  const p = sp.provider || "windows";
  if (p === "openai") return `OpenAI ${sp.openai?.model || "gpt-4o-mini-tts"}, ${sp.openai?.voice || "coral"}`;
  if (p === "elevenlabs") return `ElevenLabs ${sp.elevenlabs?.model || "eleven_flash_v2_5"}`;
  if (p === "azure") return `Azure ${(sp.azure?.voice || "en-US-AndrewMultilingualNeural").replace(/^[a-z]{2}-[A-Z]{2}-/, "")}`;
  if (p === "google") return `Google ${(sp.google?.voice || "en-US-Chirp3-HD-Charon").replace(/^[a-z]{2}-[A-Z]{2}-/, "")}`;
  const v = String(sp.voiceName || "").replace(/^Microsoft\s+/, "").split(" - ")[0].trim();
  return v ? `Windows, ${v}` : "Windows voice";
}

/** "Last line spoken with" (Settings > Voice): the voice and the time. */
export function voiceLastLine(): string {
  if (!speech.lastVoice) return "nothing spoken yet this session";
  return `${speech.lastVoice}, ${new Date(speech.lastVoiceAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
}
