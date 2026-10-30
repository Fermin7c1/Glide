// Settings > Voice > Coach voice: the narrator's voice service and its voice, speed and volume, a sample, and the
// voice the last line was spoken with. Saved to the app settings as each control changes.
import { updateAppSettings } from "../app-settings.js";
import { call } from "../bridge.js";
import { $, esc, icon, toast } from "../dom.js";
import { speech, voiceLastLine } from "../narrator.js";
import { Group } from "../settings-schema.js";
import { live } from "../state.js";
import { Obj, SpeechSettings, TtsProvider } from "../types.js";

const PROVIDERS: [string, string, string][] = [   // id, label, key
  ["windows", "Windows voices (offline, free)", ""],
  ["openai", "OpenAI (gpt-4o-mini-tts)", "OPENAI_API_KEY"],
  ["elevenlabs", "ElevenLabs", "ELEVENLABS_API_KEY"],
  ["azure", "Azure Speech (neural voices)", "AZURE_SPEECH_KEY"],
  ["google", "Google Cloud Text-to-Speech", "GOOGLE_TTS_API_KEY"],
];
const SAMPLE = "Next: turn in Kobold Camp Cleanup to Marshal McBride, 40 yards north.";
// the providers whose voices are listed from the account: where the pick is stored in their settings
const VOICE_LIST: Record<string, [string, string]> = { elevenlabs: ["elevenlabs", "voiceId"], azure: ["azure", "voice"], google: ["google", "voice"] };

const field = (label: string, help: string, ctl: string) =>
  `<div class="field"><div class="lab">${label}${help ? `<small>${help}</small>` : ""}</div><div class="ctl">${ctl}</div></div>`;
const keyNote = (env: string) => env ? `<span class="help">Needs <code>${esc(env)}</code> under AI models and keys.</span>` : "";
const options = (list: [string, string][], cur: string) =>
  list.map(([m, l]) => `<option value="${m}" ${m === cur ? "selected" : ""}>${l}</option>`).join("");
/** A voice picker filled by Load voices; until then it shows the saved voice (or the provider's default). */
const voiceList = (help: string, id: string, shown: string) =>
  field("Voice", help, `<select id="sv-voicelist"><option value="${esc(id)}">${esc(shown)}</option></select><button class="btn" id="sv-load">Load voices</button>`);

/** The controls only this provider has. */
function providerFields(prov: string, sp: SpeechSettings): string {
  if (prov === "windows") {
    const vs = speech.voices();
    return field("Voice", "Windows voices on this PC. More: Windows Settings > Time &amp; language > Speech > Add voices.",
      `<select id="sv-voice"><option value="">Windows default</option>${vs.map((v) => `<option ${v.name === sp.voiceName ? "selected" : ""}>${esc(v.name)}</option>`).join("")}</select>`);
  }
  if (prov === "openai") {
    const o = sp.openai || {};
    return field("Model", "", `<select id="sv-o-model">${["gpt-4o-mini-tts", "tts-1", "tts-1-hd"].map((m) => `<option ${m === (o.model || "gpt-4o-mini-tts") ? "selected" : ""}>${m}</option>`).join("")}</select>`)
      + field("Voice", "", `<select id="sv-o-voice"></select>`)
      + field("Style", "gpt-4o-mini-tts only: how to speak.", `<input type="text" class="wide" id="sv-o-instr" value="${esc(o.instructions || "")}" placeholder="Calm, concise game coach. Speak quickly.">`);
  }
  if (prov === "elevenlabs") {
    const e = sp.elevenlabs || {};
    return voiceList("Voices in your ElevenLabs account.", e.voiceId || "", e.voiceId || "default (George)")
      + field("Model", "Flash is fastest; Multilingual sounds best.", `<select id="sv-e-model">${options([["eleven_flash_v2_5", "Flash v2.5 (fast)"], ["eleven_turbo_v2_5", "Turbo v2.5"], ["eleven_multilingual_v2", "Multilingual v2"]], e.model || "eleven_flash_v2_5")}</select>`);
  }
  if (prov === "azure") {
    const a = sp.azure || {};
    return field("Region", "Your Speech resource's region, e.g. eastus, westeurope.", `<input type="text" id="sv-a-region" value="${esc(a.region || "eastus")}" spellcheck="false">`)
      + voiceList("", a.voice || "", a.voice || "en-US-AndrewMultilingualNeural");
  }
  if (prov === "google") {
    const g = sp.google || {};
    return voiceList("Chirp 3 HD voices sound the most natural.", g.voice || "", g.voice || "en-US-Chirp3-HD-Charon");
  }
  return "";
}

export function renderVoices(g: Group, host: HTMLElement): void {
  const sp: SpeechSettings = live.appSettings.speech || {};
  const prov = sp.provider || "windows";
  const save = (patch: Obj) => updateAppSettings({ speech: patch });
  host.innerHTML = field("Voice service", "Online services sound more natural and cost a little per line; if one fails, that line uses the Windows voice.",
      `<select id="sv-prov">${PROVIDERS.map(([id, l]) => `<option value="${id}" ${id === prov ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>${keyNote(PROVIDERS.find((p) => p[0] === prov)![2])}`)
    + providerFields(prov, sp)
    + field("Speed", "", `<input type="range" id="sv-rate" min="0.5" max="2" step="0.05" value="${sp.rate ?? 1}"><span class="rangeval">${sp.rate ?? 1}</span>`)
    + field("Volume", "", `<input type="range" id="sv-vol" min="0" max="1" step="0.05" value="${sp.volume ?? 1}"><span class="rangeval">${sp.volume ?? 1}</span>`)
    + field("Try it", "The coach uses this voice when Spoken by is set to this app.", `<button class="btn" id="sv-test">${icon("volume-2")}Play a sample</button>`)
    + field("Last line spoken with", "What Glide asked Windows for on its last line. If you hear another voice while this shows your choice, it isn't Glide: WoW's Text to Speech (Options > Accessibility) or Windows Narrator use the default voice.", `<span id="sv-last" class="help">${esc(voiceLastLine())}</span>`);

  $("sv-prov").onchange = async (e) => { await save({ provider: (e.target as HTMLSelectElement).value }); renderVoices(g, host); };
  const range = (id: string, key: string) => $(id).addEventListener("change", () => {
    ($(id).nextElementSibling as HTMLElement).textContent = $<HTMLInputElement>(id).value;
    save({ [key]: Number($<HTMLInputElement>(id).value) });
  });
  range("sv-rate", "rate");
  range("sv-vol", "volume");
  $("sv-test").onclick = () => speech.say(SAMPLE, true);
  if (prov === "windows") $("sv-voice").onchange = (e) => save({ voiceName: (e.target as HTMLSelectElement).value });
  if (prov === "openai") {
    call("tts:voices", "openai").then((vs) => {
      $("sv-o-voice").innerHTML = (vs || []).map((v) => `<option ${v.id === (sp.openai?.voice || "coral") ? "selected" : ""}>${esc(v.id)}</option>`).join("");
    });
    const o = () => save({ openai: { model: $<HTMLSelectElement>("sv-o-model").value, voice: $<HTMLSelectElement>("sv-o-voice").value, instructions: $<HTMLInputElement>("sv-o-instr").value } });
    ["sv-o-model", "sv-o-voice", "sv-o-instr"].forEach((id) => $(id).addEventListener("change", o));
  }
  if (VOICE_LIST[prov]) bindVoiceList(prov, sp as Obj, save);
  if (prov === "elevenlabs") $("sv-e-model").onchange = (e) => save({ elevenlabs: { ...(sp.elevenlabs || {}), model: (e.target as HTMLSelectElement).value } });
  if (prov === "azure") $("sv-a-region").onchange = (e) => save({ azure: { ...(sp.azure || {}), region: (e.target as HTMLInputElement).value.trim() } });
  if (prov === "windows" && !speech.voices().length && window.speechSynthesis)
    speechSynthesis.addEventListener("voiceschanged", () => renderVoices(g, host), { once: true });
}

/** The account's voice list: Load voices fills it, a pick saves it in the provider's own settings. */
function bindVoiceList(prov: string, sp: Obj, save: (patch: Obj) => Promise<void>): void {
  const [section, k] = VOICE_LIST[prov];
  $("sv-voicelist").onchange = (e) => save({ [section]: { ...(sp[section] || {}), [k]: (e.target as HTMLSelectElement).value } });
  $("sv-load").onclick = async () => {
    const vs = await call("tts:voices", prov as TtsProvider);
    if (!vs) return;
    const cur = (sp[section] || {})[k] || "";
    $("sv-voicelist").innerHTML = vs.map((v) => `<option value="${esc(v.id)}" ${v.id === cur ? "selected" : ""}>${esc(v.name)}</option>`).join("");
    toast(`${vs.length} voices`);
  };
}
