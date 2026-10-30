// Online text-to-speech for the coach's voice: OpenAI, ElevenLabs, Azure Speech and Google Cloud. The window asks
// for audio (tts:synth) and plays it; Windows voices are spoken in the window itself (speechSynthesis). Keys come from
// the encrypted key store or the environment and never reach the window.

import type { SpeechSettings, TtsProvider, Voice } from "../shared/ipc";

export const TTS_KEYS: Record<Exclude<TtsProvider, "windows">, string> = {
  openai: "OPENAI_API_KEY", elevenlabs: "ELEVENLABS_API_KEY", azure: "AZURE_SPEECH_KEY", google: "GOOGLE_TTS_API_KEY",
};

export const OPENAI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse"];

const TIMEOUT = 15000;

function xml(s: string): string {
  return s.replace(/[<>&'"]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", "'": "&apos;", '"': "&quot;" }[c]!));
}

async function ok(r: Response, what: string): Promise<Response> {
  if (!r.ok) throw new Error(`${what} ${r.status}: ${(await r.text()).slice(0, 200)}`);
  return r;
}

/** A provider's answer as JSON, or an error with its status and the start of its body. */
async function json<T>(url: string, what: string, init: RequestInit): Promise<T> {
  return (await (await ok(await fetch(url, init), what)).json()) as T;
}

/** A provider's answer as audio bytes. */
async function audio(url: string, what: string, init: RequestInit): Promise<Buffer> {
  return Buffer.from(await (await ok(await fetch(url, init), what)).arrayBuffer());
}

type Online = Exclude<TtsProvider, "windows">;
type Synth = (s: SpeechSettings, text: string, key: string, signal: AbortSignal) => Promise<Buffer>;
type VoiceList = (key: string, signal: AbortSignal, region?: string) => Promise<Voice[]>;

const SYNTH: Record<Online, Synth> = {
  async openai(s, text, key, signal) {
    const o = s.openai || {};
    const model = o.model || "gpt-4o-mini-tts";
    const body: Record<string, unknown> = { model, voice: o.voice || "coral", input: text, response_format: "mp3" };
    if (o.instructions && model.includes("gpt")) body.instructions = o.instructions;
    return audio("https://api.openai.com/v1/audio/speech", "OpenAI", {
      method: "POST", signal, headers: { authorization: `Bearer ${key}`, "content-type": "application/json" },
      body: JSON.stringify(body) });
  },
  async elevenlabs(s, text, key, signal) {
    const e = s.elevenlabs || {};
    const voice = e.voiceId || "JBFqnCBsd6RMkjVDRZzb";     // "George", a default ElevenLabs voice
    return audio(`https://api.elevenlabs.io/v1/text-to-speech/${encodeURIComponent(voice)}?output_format=mp3_44100_128`, "ElevenLabs", {
      method: "POST", signal, headers: { "xi-api-key": key, "content-type": "application/json" },
      body: JSON.stringify({ text, model_id: e.model || "eleven_flash_v2_5" }) });
  },
  async azure(s, text, key, signal) {
    const a = s.azure || {};
    const region = (a.region || "eastus").trim();
    const voice = a.voice || "en-US-AndrewMultilingualNeural";
    const ssml = `<speak version="1.0" xml:lang="en-US"><voice name="${xml(voice)}">${xml(text)}</voice></speak>`;
    return audio(`https://${encodeURIComponent(region)}.tts.speech.microsoft.com/cognitiveservices/v1`, "Azure", {
      method: "POST", signal, headers: { "Ocp-Apim-Subscription-Key": key, "content-type": "application/ssml+xml",
                                         "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3", "user-agent": "glide" },
      body: ssml });
  },
  async google(s, text, key, signal) {
    const voice = s.google?.voice || "en-US-Chirp3-HD-Charon";
    const lang = voice.split("-").slice(0, 2).join("-");
    const d = await json<{ audioContent?: string }>(`https://texttospeech.googleapis.com/v1/text:synthesize?key=${encodeURIComponent(key)}`, "Google", {
      method: "POST", signal, headers: { "content-type": "application/json" },
      body: JSON.stringify({ input: { text }, voice: { languageCode: lang, name: voice }, audioConfig: { audioEncoding: "MP3" } }) });
    if (!d.audioContent) throw new Error("Google: no audio in the answer");
    return Buffer.from(d.audioContent, "base64");
  },
};

// OpenAI's voices are a fixed list (voices() answers it without a key); the others are asked for theirs
const VOICE_LISTS: Record<Exclude<Online, "openai">, VoiceList> = {
  async elevenlabs(key, signal) {
    const d = await json<{ voices?: { voice_id: string; name: string; labels?: Record<string, string> }[] }>(
      "https://api.elevenlabs.io/v1/voices", "ElevenLabs", { signal, headers: { "xi-api-key": key } });
    return (d.voices || []).map((v) => ({ id: v.voice_id, name: [v.name, v.labels?.accent, v.labels?.gender].filter(Boolean).join(" · ") }));
  },
  async azure(key, signal, region) {
    const d = await json<{ ShortName: string; LocalName: string; Locale: string; Gender: string }[]>(
      `https://${encodeURIComponent(region || "eastus")}.tts.speech.microsoft.com/cognitiveservices/voices/list`, "Azure",
      { signal, headers: { "Ocp-Apim-Subscription-Key": key } });
    return d.filter((v) => v.Locale.startsWith("en-")).map((v) => ({ id: v.ShortName, name: `${v.ShortName} · ${v.Gender}` }));
  },
  async google(key, signal) {
    const d = await json<{ voices?: { name: string; ssmlGender: string }[] }>(
      `https://texttospeech.googleapis.com/v1/voices?languageCode=en&key=${encodeURIComponent(key)}`, "Google", { signal });
    return (d.voices || []).map((v) => ({ id: v.name, name: `${v.name} · ${v.ssmlGender.toLowerCase()}` }));
  },
};

/** MP3 audio for `text` from an online provider. */
export async function synth(s: SpeechSettings, text: string, key: string): Promise<Buffer> {
  const p = s.provider;
  if (!key) throw new Error(`${p}: no ${TTS_KEYS[p as Online]} (Settings > AI models & keys)`);
  const signal = AbortSignal.timeout(TIMEOUT);
  if (p && Object.hasOwn(SYNTH, p)) return SYNTH[p as Online](s, text, key, signal);
  throw new Error(`unknown voice provider ${p}`);
}

/** The voices a provider offers: [{id, name}] (OpenAI: its fixed list). */
export async function voices(provider: TtsProvider, key: string, region?: string): Promise<Voice[]> {
  const signal = AbortSignal.timeout(TIMEOUT);
  if (provider === "openai") return OPENAI_VOICES.map((v) => ({ id: v, name: v }));
  if (!key) throw new Error(`no ${TTS_KEYS[provider as Online]} set`);
  if (Object.hasOwn(VOICE_LISTS, provider)) return VOICE_LISTS[provider as keyof typeof VOICE_LISTS](key, signal, region);
  return [];
}
