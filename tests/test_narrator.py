#!/usr/bin/env python3
"""The narrator (desktop/src/renderer/narrator.ts), compiled with the project's TypeScript beside stand-ins for the
bridge and the page, and run under Node with a stand-in speechSynthesis and Audio whose lines end after a set time
(on a virtual clock: no real time passes).

The coach's lines behave as they always have: Windows voice lines go straight to speechSynthesis in order, an
interrupting line cancels what's being said, muted says nothing, online voices fetch every line's audio at once and
play them in order. The Guides page's tagged lines (speakAndWait) resolve when they've been spoken, at once when
muted, and when cancel(tag) or stop() drops them; cancel(tag) never drops a coach line, not even one speechSynthesis
had queued behind the tagged line.

Needs node and desktop/node_modules (skips with 77 without them)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import desktop_ts

HERE = os.path.dirname(os.path.abspath(__file__))
DESK = os.path.join(HERE, "..", "desktop")
SRC = os.path.join(DESK, "src", "renderer", "narrator.ts")

node = desktop_ts.node
if not node or not desktop_ts.available():
    print("SKIP: needs node and desktop/node_modules (cd desktop && npm ci)")
    sys.exit(77)

STUBS = {
    "bridge.ts": """
export const glide: { invoke(ch: string, ...a: unknown[]): Promise<{ data: Uint8Array; mime: string }> } =
  (window as any).glide;
export const call = (..._a: unknown[]) => Promise.resolve(undefined);
export const ipcError = (e: unknown) => String(e);
""",
    "dom.ts": "export const toast = (..._a: unknown[]) => undefined;\n",
    "types.ts": """
export type Obj = any;
export interface SpeechSettings { provider?: string; voiceName?: string; rate?: number; volume?: number }
""",
}

# the stand-ins and the scenarios; every line takes DUR ms to say
RUN = r"""
// virtual time: the timers fire in the order they fall due, each as soon as nothing else is left to run, so the
// lines' DUR and the waits take no real time and the measured times are exact (a loaded machine can't stretch them).
// The clock starts at the real time (the narrator compares Date.now() with 0 for its once-a-minute notes).
let now = Date.now(), seq = 0, pumping = false;
const timers = [];
const pump = () => {
  pumping = false;
  if (!timers.length) return;
  timers.sort((a, b) => a.at - b.at || a.seq - b.seq);
  const t = timers.shift();
  now = Math.max(now, t.at);
  t.fn(...t.args);
  schedule();
};
const realImmediate = setImmediate;
const schedule = () => { if (!pumping && timers.length) { pumping = true; realImmediate(pump); } };
globalThis.setTimeout = (fn, ms = 0, ...args) => {
  const t = { at: now + Math.max(0, Number(ms) || 0), seq: seq++, fn, args };
  timers.push(t); schedule(); return t;
};
globalThis.clearTimeout = (t) => { const i = timers.indexOf(t); if (i >= 0) timers.splice(i, 1); };
Date.now = () => now;

const DUR = 80;
const log = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
globalThis.window = globalThis;
globalThis.document = { getElementById: () => null };
class Utterance { constructor(text) { this.text = text; this.voice = null; this.rate = 1; this.volume = 1; } }
globalThis.SpeechSynthesisUtterance = Utterance;
const synth = {
  q: [], cur: null, speaking: false, pending: false,
  getVoices: () => [{ name: "Microsoft Zira" }],
  addEventListener() {}, removeEventListener() {},
  upd() { this.speaking = !!this.cur; this.pending = this.q.length > 0; },
  speak(u) { log.push("speak:" + u.text); this.q.push(u); this.upd(); if (!this.cur) this.next(); },
  next() {
    const u = this.q.shift(); this.cur = u || null; this.upd(); if (!u) return;
    log.push("start:" + u.text);
    u._t = setTimeout(() => {
      if (this.cur !== u) return;
      log.push("end:" + u.text); this.cur = null; this.upd(); if (u.onend) u.onend({}); this.next();
    }, DUR);
  },
  cancel() {
    const all = [this.cur, ...this.q].filter(Boolean);
    if (this.cur) clearTimeout(this.cur._t);
    this.q = []; this.cur = null; this.upd(); log.push("cancel");
    for (const u of all) setTimeout(() => { if (u.onerror) u.onerror({}); }, 0);   // Chromium: an error each, later
  },
};
globalThis.speechSynthesis = synth;
const dec = new TextDecoder();
globalThis.Blob = class { constructor(parts) { this.text = dec.decode(parts[0]); } };
const urls = new Map();
let nurl = 0;
URL.createObjectURL = (b) => { const u = "blob:" + (++nurl); urls.set(u, b.text); return u; };
URL.revokeObjectURL = () => {};
globalThis.Audio = class {
  constructor(url) { this.text = urls.get(url); this.paused = false; }
  play() {
    log.push("play:" + this.text);
    setTimeout(() => { if (!this.paused) { log.push("ended:" + this.text); if (this.onended) this.onended(); } }, DUR);
    return Promise.resolve();
  }
  pause() { if (this.paused) return; this.paused = true; log.push("pause:" + this.text); if (this.onpause) this.onpause(); }
};
globalThis.glide = { invoke: (ch, text) => { log.push("synth:" + text); return sleep(10).then(() => ({ data: new TextEncoder().encode(text), mime: "audio/mpeg" })); } };

const { speech } = await import(process.argv[2]);
const WIN = { provider: "windows", voiceName: "Microsoft Zira" }, ONLINE = { provider: "openai" };
const out = {};
const take = (name) => { out[name] = log.splice(0); };
const mark = (p, what) => p.then(() => log.push(what));

// the coach, Windows voice: in order; an interrupting line cancels; muted says nothing
speech.configure(false, WIN);
speech.say("a"); speech.say("b");
await sleep(DUR * 3); take("coach_win");
speech.say("a"); await sleep(10); speech.say("c", true);
await sleep(DUR * 2); take("coach_interrupt");
speech.configure(true, WIN);
speech.say("x");
let t = Date.now(); await speech.speakAndWait("muted line", "g"); out.muted_ms = Date.now() - t;
out.muted_can = speech.canSpeak();
await sleep(20); take("coach_muted");
speech.configure(false, WIN);
out.can = speech.canSpeak();
t = Date.now(); await speech.speakAndWait("", "g"); out.empty_ms = Date.now() - t;

// a tagged line waits for the coach's line, then resolves at its own end
speech.say("coach1");
t = Date.now();
await mark(speech.speakAndWait("guide1", "g"), "resolved:guide1"); out.tagged_ms = Date.now() - t;
take("tagged_win");

// cancel(tag) while it speaks, with a coach line queued behind it: the coach line is still said
let p = mark(speech.speakAndWait("guide2", "g"), "resolved:guide2");
const q2 = mark(speech.speakAndWait("guide3", "g"), "resolved:guide3");
await sleep(10); speech.say("coach2");
await sleep(20); t = Date.now(); speech.cancel("g"); await p; await q2; out.cancel_ms = Date.now() - t;
await sleep(DUR * 2); take("cancel_win");
// another tag's cancel leaves the coach alone
speech.say("coach3"); await sleep(10); speech.cancel("g");
await sleep(DUR * 2); take("cancel_other_win");

// the coach, online voice: every line fetched at once, played in order
speech.configure(false, ONLINE);
speech.say("o1"); speech.say("o2");
await sleep(DUR * 3); take("coach_online");
// tagged online lines: cancel stops the one playing, drops the queued one, the coach's next line plays
speech.say("o3");
p = mark(speech.speakAndWait("og1", "g"), "resolved:og1");
const q = mark(speech.speakAndWait("og2", "g"), "resolved:og2");
speech.say("o4");
await sleep(DUR + DUR / 2); speech.cancel("g"); await p; await q;
await sleep(DUR * 2); take("cancel_online");
// stop() (the coach's interrupt, mute) lets a waiting page go on
p = mark(speech.speakAndWait("og5", "g"), "resolved:og5");
await sleep(30); speech.stop(); await p; take("stop_online");
speech.configure(false, WIN);
p = mark(speech.speakAndWait("guide6", "g"), "resolved:guide6");
await sleep(20); speech.configure(true, WIN); await p; await sleep(10); take("mute_win");
console.log(JSON.stringify(out));
"""

tmp = tempfile.mkdtemp(prefix="glide-narrator-")
try:
    with open(SRC, encoding="utf-8") as fh:
        files = {"narrator.ts": fh.read(), **STUBS}
    # narrator.ts beside its stand-ins, compiled once per content (the tests' shared compiles, desktop_ts.py)
    out = desktop_ts.compiled("narrator", files, ["narrator.ts", "--target", "ES2023", "--module", "ES2022",
                                                  "--moduleResolution", "bundler", "--lib", "ES2023,DOM", "--strict",
                                                  "--skipLibCheck"])
    run = os.path.join(tmp, "run.mjs")
    with open(run, "w", encoding="utf-8") as fh:
        fh.write(RUN)
    mod = desktop_ts.url(os.path.join(out, "narrator.js"))
    r = subprocess.run([node, run, mod], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, "running the narrator failed: %s" % r.stderr[-2000:]
    got = json.loads(r.stdout)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# the coach's lines, as before
assert got["coach_win"] == ["speak:a", "start:a", "speak:b", "end:a", "start:b", "end:b"], got["coach_win"]
assert got["coach_interrupt"] == ["speak:a", "start:a", "cancel", "speak:c", "start:c", "end:c"], got["coach_interrupt"]
assert got["coach_muted"] == ["cancel"] and got["muted_ms"] < 20 and got["muted_can"] is False and got["can"] is True, got
assert got["empty_ms"] < 20, got
assert got["coach_online"] == ["synth:o1", "synth:o2", "play:o1", "ended:o1", "play:o2", "ended:o2"], got["coach_online"]

# a tagged line waits its turn and resolves at its end
assert got["tagged_win"] == ["speak:coach1", "start:coach1", "end:coach1", "speak:guide1", "start:guide1",
                             "end:guide1", "resolved:guide1"], got["tagged_win"]
assert got["tagged_ms"] >= 2 * 80 - 5, got["tagged_ms"]

# cancel: at once, the tagged lines dropped, the coach line said in full
c = got["cancel_win"]
assert c[:4] == ["speak:guide2", "start:guide2", "speak:coach2", "cancel"], c
assert "end:guide2" not in c and "speak:guide3" not in c and "end:coach2" in c, c
assert c.index("resolved:guide2") < c.index("end:coach2") and "resolved:guide3" in c, c
assert got["cancel_ms"] < 20, got["cancel_ms"]
assert got["cancel_other_win"] == ["speak:coach3", "start:coach3", "end:coach3"], got["cancel_other_win"]

c = got["cancel_online"]
c = [x for x in c if not x.startswith("synth:")]
assert c[:4] == ["play:o3", "ended:o3", "play:og1", "pause:og1"] and c[-2:] == ["play:o4", "ended:o4"], c
assert sorted(c[4:-2]) == ["resolved:og1", "resolved:og2"] and "play:og2" not in c, c
assert "resolved:og5" in got["stop_online"], got["stop_online"]
assert "resolved:guide6" in got["mute_win"] and "end:guide6" not in got["mute_win"], got["mute_win"]
print("OK")
