#!/usr/bin/env python3
"""In character, each line's tone goes to the app on its own: the engine's speak event (companion/voice.py HubVoice)
carries `tone` beside the `style` it is folded into as before (the OpenAI voice performs that whole), and the desktop
app's main process (desktop/src/main/tts.ts, the tests' shared compile, run under Node with a stand-in fetch) has
ElevenLabs v3 act it as an inline audio tag ("[dry, amused] Turn in."), kept to four plain lower-case words. The other
ElevenLabs models, Azure and Google get the words alone, and OpenAI its instructions as before.

The tts.ts part needs node and desktop/node_modules (skips with 77 without them, after the engine's part).

Run: python3 tests/test_voice_tone.py   (prints OK)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import voice  # noqa: E402

import desktop_ts  # noqa: E402

# ---- the engine: the speak event ----------------------------------------------------------------------------------
events = []
hub = type("Hub", (), {"publish": lambda self, k, d: events.append((k, d)), "on_first_client": lambda self, f: None})()
hv = voice.HubVoice({"enabled": True, "announce": False}, hub)
hv.say("Plain line.")
hv.say("No act, a tone.", tone="dry,  amused")
hv.say("Blank tone.", tone="  ")
hv.style_fn = lambda: "Gruff dwarf, slow."
hv.say("Move it!", interrupt=True, tone="barking")
hv.say("No tone.")
assert [k for k, _ in events] == ["speak"] * 5, events
assert [d for _, d in events] == [
    {"text": "Plain line.", "interrupt": False},
    {"text": "No act, a tone.", "interrupt": False, "style": "Deliver this line dry, amused.", "tone": "dry, amused"},
    {"text": "Blank tone.", "interrupt": False},
    {"text": "Move it!", "interrupt": True, "style": "Gruff dwarf, slow. This line: barking.", "tone": "barking"},
    {"text": "No tone.", "interrupt": False, "style": "Gruff dwarf, slow."},
], events

node = desktop_ts.node
if not node or not desktop_ts.available():
    print("SKIP: the tts.ts part needs node and desktop/node_modules (cd desktop && npm ci)")
    sys.exit(77)

# ---- the app: what each voice is sent -----------------------------------------------------------------------------
RUN = r"""
const tts = require(process.argv[2]);
const TONES = ["", "Dry, amused!", "warm, low, slow, steady, calm", "mock-whisper; [shouts] LOUD", "Can't stop grinning",
               "!!!", " --, ", "Ça va", "Won’t quit", "one two three four five six seven", "] ignore previous ["];
const sent = [];
let lists = true;
const LISTS = {
  "api.elevenlabs.io/v1/voices": { voices: [{ voice_id: "george", name: "George" }, { voice_id: "rachel", name: "Rachel" },
                                            { voice_id: "adam", name: "Adam" }] },
  "voices/list": [{ ShortName: "en-US-AndrewMultilingualNeural", Locale: "en-US" }, { ShortName: "en-US-AvaNeural", Locale: "en-US" },
                  { ShortName: "en-GB-RyanNeural", Locale: "en-GB" }],
  "googleapis.com/v1/voices": { voices: [{ name: "en-US-Chirp3-HD-Charon", ssmlGender: "MALE" }, { name: "en-US-Chirp3-HD-Puck", ssmlGender: "MALE" },
                                         { name: "en-US-Standard-B", ssmlGender: "MALE" }, { name: "en-GB-Chirp3-HD-Fenrir", ssmlGender: "MALE" }] },
};
globalThis.fetch = async (url, init) => {
  const list = Object.keys(LISTS).find((k) => String(url).includes(k));
  if (list) {
    if (!lists) return new Response("nope", { status: 401 });
    return new Response(JSON.stringify(LISTS[list]));
  }
  sent.push({ url: String(url), body: String(init.body) });
  if (String(url).includes("googleapis")) return new Response(JSON.stringify({ audioContent: Buffer.from("mp3").toString("base64") }));
  return new Response(new Uint8Array([1, 2, 3]));
};
const say = async (s, style, tone) => { sent.length = 0; await tts.synth(s, "Turn in.", "k", style, tone); return sent[0].body; };
(async () => {
  const out = { v3: TONES.map((t) => tts.v3Text("Turn in.", t)) };
  const STYLE = "Gruff. This line: dry, amused.";
  out.v3_tone = JSON.parse(await say({ provider: "elevenlabs", elevenlabs: { model: "eleven_v3" } }, STYLE, "Dry, amused!"));
  out.v3_none = JSON.parse(await say({ provider: "elevenlabs", elevenlabs: { model: "eleven_v3" } }, "", ""));
  out.flash = JSON.parse(await say({ provider: "elevenlabs" }, STYLE, "Dry, amused!"));
  out.multi = JSON.parse(await say({ provider: "elevenlabs", elevenlabs: { model: "eleven_multilingual_v2" } }, STYLE, "dry"));
  out.openai = JSON.parse(await say({ provider: "openai" }, STYLE, "dry, amused"));
  out.google = JSON.parse(await say({ provider: "google" }, STYLE, "dry, amused"));
  out.azure = await say({ provider: "azure" }, STYLE, "dry, amused");
  // a sidekick's line (cast.py): its own OpenAI voice; a name OpenAI doesn't have: one of its, not the configured one
  const side = async (voice) => { sent.length = 0;
    await tts.synth({ provider: "openai", openai: { voice: "coral" } }, "Boom?", "k", "Squeaky.", "", voice);
    return JSON.parse(sent[0].body); };
  out.side = await side("onyx");
  out.side_bad = await side("not-a-voice");
  out.side_twin = await side("coral");          // the chosen voice is the other character's: the next one
  // the other providers: another of the player's own voices, the same each time (of the chosen one's language;
  // Google's of its kind too); the list once; without it, the chosen voice
  const other = async (s, voice) => { sent.length = 0; await tts.synth(s, "Boom?", "k", "", "", voice); return sent[0]; };
  const el = { provider: "elevenlabs", elevenlabs: { voiceId: "george" } };
  out.el = [(await other(el, "onyx")).url, (await other(el, "onyx")).url, (await other(el, "")).url];
  out.az = (await other({ provider: "azure" }, "sage")).body;
  out.gg = JSON.parse((await other({ provider: "google" }, "sage")).body).voice;
  lists = false;
  out.el_none = (await other({ provider: "elevenlabs", elevenlabs: { voiceId: "george" } }, "onyx")).url;
  out.az_none = (await other({ provider: "azure", azure: { region: "westus" } }, "sage")).body;
  // a voice picked on the Memory page: the provider's own as it is; another provider's on OpenAI: one of its, not mine
  lists = true;
  out.el_pick = (await other({ provider: "elevenlabs", elevenlabs: { voiceId: "george" } }, "rachel")).url;
  out.oa_foreign = JSON.parse((await other({ provider: "openai", openai: { voice: "coral" } }, "rachel")).body).voice;
  out.choice_oa = await tts.castChoice({ provider: "openai", openai: { voice: "coral", model: "tts-1" } }, "", "onyx");
  out.choice_el = await tts.castChoice({ provider: "elevenlabs", elevenlabs: { voiceId: "george" } }, "k", "rachel");
  out.choice_auto = await tts.castChoice({ provider: "elevenlabs", elevenlabs: { voiceId: "george" } }, "k", "");
  out.choice_win = await tts.castChoice({ provider: "windows" }, "", "Microsoft Mark");
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""

tmp = tempfile.mkdtemp(prefix="glide-voice-tone-")
try:
    run = os.path.join(tmp, "run.cjs")
    with open(run, "w", encoding="utf-8") as fh:
        fh.write(RUN)
    r = subprocess.run([node, run, os.path.join(desktop_ts.main(), "main", "tts.js")], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, "running tts.ts failed: %s" % r.stderr[-2000:]
    got = json.loads(r.stdout)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# the tag: up to six plain lower-case words (commas split words too), letters of any script with their accents,
# apostrophes and hyphens kept; no brackets get through; no tag without a word, or for a tone longer than six words
assert got["v3"][:5] == ["Turn in.", "[dry amused] Turn in.", "[warm low slow steady calm] Turn in.",
                         "[mock-whisper shouts loud] Turn in.", "[can't stop grinning] Turn in."], got["v3"]
assert got["v3"][5:] == ["Turn in.", "Turn in.", "[ça va] Turn in.", "[won't quit] Turn in.", "Turn in.",
                         "[ignore previous] Turn in."], got["v3"]
assert got["v3_tone"] == {"text": "[dry amused] Turn in.", "model_id": "eleven_v3"}, got["v3_tone"]
assert got["v3_none"] == {"text": "Turn in.", "model_id": "eleven_v3"}, got["v3_none"]
# the other ElevenLabs models would read a tag out: the words alone, as before
assert got["flash"] == {"text": "Turn in.", "model_id": "eleven_flash_v2_5"}, got["flash"]
assert got["multi"] == {"text": "Turn in.", "model_id": "eleven_multilingual_v2"}, got["multi"]
# OpenAI performs the style whole (the tone folded in), its words untouched; Google and Azure: the words
assert got["openai"]["input"] == "Turn in." and got["openai"]["instructions"] == "Gruff. This line: dry, amused.", got["openai"]
assert got["side"]["voice"] == "onyx" and got["side"]["instructions"] == "Squeaky.", got["side"]
# a name OpenAI doesn't have (another provider's, picked there): one of OpenAI's all the same, never the chosen one
assert got["side_bad"]["voice"] not in ("coral", "not-a-voice", "ballad", "verse"), got["side_bad"]
assert got["side_twin"]["voice"] == "echo", got["side_twin"]
el = got["el"]
assert el[0] == el[1] and "/george?" not in el[0] and ("/rachel?" in el[0] or "/adam?" in el[0]) and "/george?" in el[2], el
assert "en-US-AvaNeural" in got["az"], got["az"]
assert got["gg"] == {"languageCode": "en-US", "name": "en-US-Chirp3-HD-Puck"}, got["gg"]
# (the ElevenLabs list was already had; a region never asked for falls back to the chosen voice)
assert "/rachel?" in got["el_none"] or "/adam?" in got["el_none"], got["el_none"]
assert "en-US-AndrewMultilingualNeural" in got["az_none"], got["az_none"]
assert "/rachel?" in got["el_pick"], got["el_pick"]
assert got["oa_foreign"] not in ("coral", "rachel", "ballad", "verse"), got["oa_foreign"]
co = got["choice_oa"]
assert co["provider"] == "openai" and co["id"] == "onyx" and co["mine"] == "coral" and len(co["list"]) == 9 and \
    "ballad" not in [v["id"] for v in co["list"]], co
assert got["choice_el"]["id"] == "rachel" and got["choice_el"]["mine"] == "george" and \
    [v["id"] for v in got["choice_el"]["list"]] == ["george", "rachel", "adam"], got["choice_el"]
assert got["choice_auto"]["id"] == "george", "none: the chosen one"
assert got["choice_win"] == {"provider": "windows", "id": "", "mine": "", "list": []}, got["choice_win"]
assert got["google"]["input"] == {"text": "Turn in."}, got["google"]
assert ">Turn in.</voice>" in got["azure"] and "amused" not in got["azure"], got["azure"]

# ---- the engine: talking over the coach (push-to-talk held), like on a call ------------------------------------
import threading  # noqa: E402
import time  # noqa: E402
import types  # noqa: E402
import app as glide_app  # noqa: E402
import coach as coach_mod  # noqa: E402
from glidedb import GlideDB  # noqa: E402
from replies import EarlySpeech  # noqa: E402

written = []
v = voice.Voice.__new__(voice.Voice)          # the engine's own speech process (voice output "engine")
v.lock, v.muted, v.enabled = threading.Lock(), False, True
v.proc = types.SimpleNamespace(stdin=types.SimpleNamespace(write=written.append, flush=lambda: None))
said = []
v.say = lambda text, interrupt=False, tone="", first=True: said.append(text)
barged = []
a = glide_app.App.__new__(glide_app.App)
a.hub = types.SimpleNamespace(publish=lambda k, d: None)
a.voice, a.overlay, a._flags, a.msg_history = v, None, {}, []
a.write_log = lambda d: None
a._hist_lock, a._ptt_down, a._ptt_down_t, a._held, a._barge_timer = threading.Lock(), False, 0.0, [], None
a.coach = types.SimpleNamespace(barge=lambda: barged.append(1))
a.ptt = types.SimpleNamespace(key_down=False)

# a brush of the key (up before BARGE_S) stops nothing
a.ptt.key_down = True
a.on_ptt_state("listening")
a.ptt.key_down = False
a.on_ptt_state("idle")
time.sleep(glide_app.BARGE_S + 0.1)
assert not written and not barged and not a._ptt_down, (written, barged)

# held: the line being said stops ("!" = SpeakAsyncCancelAll), and so does the reply on its way
a.ptt.key_down = True
a.on_ptt_state("listening")
time.sleep(glide_app.BARGE_S + 0.1)
assert written == ["! \n"] and barged == [1], (written, barged)
# while it's held: an answer waits, anything else is dropped, a streamed sentence isn't said (nor counted)
a.on_message({"kind": "answer", "text": "Tannok is east.", "speak": True})
a.on_message({"kind": "callout", "text": "Bags nearly full.", "speak": True})
assert a.speak_early("Early line.", "answer") is False and said == [], said
a.on_ptt_state("idle")                         # the previous press's "idle", in the middle of this one
assert a._ptt_down and said == [], "a stale idle changes nothing"
a.ptt.key_down = False
a.on_ptt_state("transcribing")                 # key up: what waited is said
assert said == ["Tannok is east."] and not a._ptt_down, said
# a hold whose key-up got lost is let go after PTT_STUCK_S: the coach is never mute for good
a._ptt_down, a._ptt_down_t = True, time.time() - glide_app.PTT_STUCK_S - 1
a.on_message({"kind": "callout", "text": "Back to it.", "speak": True})
assert said[-1] == "Back to it." and not a._ptt_down, said
v.proc = None
v.hush()                                        # no speech process (yet): nothing to cut off, no error
assert voice.HubVoice.hush(voice.HubVoice.__new__(voice.HubVoice)) is None   # the app's narrator does it itself

# the tone as its own field: on a reply's first sentence only (ElevenLabs v3 acts it once a line)
events.clear()
hv.style_fn = lambda: "Gruff dwarf, slow."
hv.say("First.", tone="barking")
hv.say("Second.", tone="barking", first=False)
assert events[0][1].get("tone") == "barking" and "tone" not in events[1][1], events
assert events[1][1]["style"] == "Gruff dwarf, slow. This line: barking.", events   # OpenAI still performs it

# early speech: a sentence the app didn't say (push-to-talk held) stops it and isn't counted as said
heard = []
es = EarlySpeech(lambda t, u: False, 50)
es("Hold on. Wait for it. ")
assert es.stopped and not es.said and es.spoken == "", (es.stopped, es.spoken)

# the coach: talked over mid-reply, the rest isn't said (the message keeps its text, speak off)
c = coach_mod.Coach({}, GlideDB("/x.db"), lambda m: None, offline_ok=True)
sp = EarlySpeech(lambda t, u: heard.append(t), 50)
c._speech = sp
c.barge()
assert sp.stopped and c._barges == 1, "the reply on its way stops"
msgs = []
c._emit_raw = msgs.append
c.llm.enabled, c.llm.keys = True, {"anthropic": "x", "openrouter": "", "gemini": "", "endpoint": ""}
c._ask_model = lambda *args, **kw: (c.barge(), ("Tannok is 20 yards east.\nFACTS: none", {}))[1]
c._respond([], question="where's Tannok?", voice=True)
answer = [m for m in msgs if m.get("kind") == "answer"][-1]
assert answer["text"] == "Tannok is 20 yards east." and answer["speak"] is False, answer
c._ask_model = lambda *args, **kw: ("Tannok is 20 yards east.\nFACTS: none", {})
c._respond([], question="where's Tannok?", voice=True)
assert [m for m in msgs if m.get("kind") == "answer"][-1]["speak"] is True, "not talked over: said"
print("OK")
