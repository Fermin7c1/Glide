#!/usr/bin/env python3
"""The coach's update lines (desktop/src/renderer/update-voice.ts), compiled with the project's TypeScript and run under
Node on a virtual clock (no real time passes).

When an update is out the coach says so once, then reminds the player every 10 minutes to download it; quiet while it
downloads; once downloaded it says so once and reminds every 10 minutes to restart. A re-broadcast status is never
said twice, a re-check ("checking") changes nothing, a failed one pauses the reminders, nothing waiting (none, idle)
clears them, a newer version starts over with its own
announcement, and the character's voice direction goes with every line when the coach plays one.

Needs node and desktop/node_modules (skips with 77 without them)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import desktop_ts

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "desktop", "src", "renderer", "update-voice.ts")

node = desktop_ts.node
if not node or not desktop_ts.available():
    print("SKIP: needs node and desktop/node_modules (cd desktop && npm ci)")
    sys.exit(77)

STUBS = {"types.ts": """
export interface UpdateStatus { state: string; current: string; version?: string }
"""}

RUN = r"""
let now = 0, seq = 0;
const timers = new Map();
const setI = (fn, ms) => { const id = ++seq; timers.set(id, { fn, ms, at: now + ms }); return id; };
const clearI = (id) => { timers.delete(id); };
const flush = () => new Promise((r) => setImmediate(r));
// move the clock to t, firing the intervals that fall due in order
const advance = async (t) => {
  for (;;) {
    let best = null;
    for (const [id, x] of timers) if (x.at <= t && (!best || x.at < best[1].at)) best = [id, x];
    if (!best) break;
    now = best[1].at; best[1].at += best[1].ms; best[1].fn(); await flush();
  }
  now = t; await flush();
};
const { UpdateVoice, REMIND_MS } = await import(process.argv[2]);
const MIN = 60000;
const run = async (styleOf, steps) => {
  const log = [];
  const v = new UpdateVoice({
    say: (text, style) => log.push({ t: Math.round(now / MIN), text, style }),
    style: styleOf, setInterval: setI, clearInterval: clearI,
  });
  now = 0; timers.clear();
  for (const [t, s] of steps) { await advance(t * MIN); if (s) v.onStatus(s); await flush(); }
  return { log, timers: timers.size };
};
const A = (ver) => ({ state: "available", current: "0.3.40", version: ver });
const S = (state, ver) => ({ state, current: "0.3.40", version: ver });
const out = { remind_ms: REMIND_MS };
out.cycle = await run(() => "", [
  [0, S("checking")], [0, A("0.4.0")], [1, A("0.4.0")], [9, A("0.4.0")], [21, null],
  [22, S("downloading", "0.4.0")], [23, S("downloading", "0.4.0")], [40, S("ready", "0.4.0")],
  [41, S("ready", "0.4.0")], [61, null],
]);
out.cleared = await run(() => "", [[0, A("0.4.0")], [5, S("checking", "0.4.0")], [6, S("none")], [40, null]]);
out.newer = await run(() => "", [[0, A("0.4.0")], [12, A("0.4.1")], [23, null]]);
out.after_none = await run(() => "", [[0, A("0.4.0")], [1, S("none")], [2, A("0.4.0")], [3, null]]);
out.style = await run(async () => "gravelly pirate, slow", [[0, A("0.4.0")], [10, null]]);
out.style_throws = await run(() => { throw new Error("x"); }, [[0, A("0.4.0")]]);
// the updater re-checks every 30 minutes while the update waits: "checking", then the same answer
out.recheck = await run(() => "", [[0, A("0.4.0")], [30, S("checking", "0.4.0")], [30, A("0.4.0")], [41, null]]);
// a failed re-check: quiet, then the same answer again: reminders resume, the announcement isn't repeated
out.error = await run(() => "", [[0, A("0.4.0")], [15, S("error")], [45, A("0.4.0")], [56, null]]);
// a line whose style lookup (an IPC round trip) is still out when the state moves on is not said
{
  const pending = [], log = [];
  now = 0; timers.clear();
  const v = new UpdateVoice({ say: (text) => log.push(text), style: () => new Promise((r) => pending.push(r)),
                              setInterval: setI, clearInterval: clearI });
  v.onStatus(A("0.4.0"));
  v.onStatus(S("downloading", "0.4.0"));
  for (const r of pending) r("");
  await flush();
  out.stale = log;
}
console.log(JSON.stringify(out));
"""

tmp = tempfile.mkdtemp(prefix="glide-update-voice-")
try:
    with open(SRC, encoding="utf-8") as fh:
        files = {"update-voice.ts": fh.read(), **STUBS}
    out = desktop_ts.compiled("update-voice", files, ["update-voice.ts", "--target", "ES2023", "--module", "ES2022",
                                                      "--moduleResolution", "bundler", "--lib", "ES2023", "--strict",
                                                      "--skipLibCheck"])
    run = os.path.join(tmp, "run.mjs")
    with open(run, "w", encoding="utf-8") as fh:
        fh.write(RUN)
    r = subprocess.run([node, run, desktop_ts.url(os.path.join(out, "update-voice.js"))], capture_output=True,
                       text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, "running update-voice failed: %s" % r.stderr[-2000:]
    got = json.loads(r.stdout)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

assert got["remind_ms"] == 600000, got["remind_ms"]

ANNOUNCE = "A Glide update is out: version 0.4.0. Download it from the footer button when it suits you."
REMIND_DL = "Reminder: Glide 0.4.0 is still waiting to be downloaded, footer button."
READY = "Glide 0.4.0 is downloaded. Restart to update from the footer button when it suits you."
REMIND_RS = "Reminder: restart Glide to finish updating to 0.4.0, footer button."

# announced once (re-broadcasts at 1 and 9 minutes say nothing and don't move the reminder), reminded at 10 and 20,
# quiet while downloading (22 to 40), ready said once at 40, then reminded at 50 and 60
said = [(x["t"], x["text"]) for x in got["cycle"]["log"]]
assert said == [(0, ANNOUNCE), (10, REMIND_DL), (20, REMIND_DL), (40, READY), (50, REMIND_RS), (60, REMIND_RS)], said
assert all(x["style"] == "" for x in got["cycle"]["log"]), got["cycle"]["log"]
assert len({ANNOUNCE, REMIND_DL, READY, REMIND_RS}) == 4

# checking / none clear the reminder: nothing after the announcement, no timer left
assert [x["t"] for x in got["cleared"]["log"]] == [0] and got["cleared"]["timers"] == 0, got["cleared"]

# a newer version starts over: its own announcement, its reminders 10 minutes after it
said = [(x["t"], x["text"]) for x in got["newer"]["log"]]
assert said == [(0, ANNOUNCE), (10, REMIND_DL), (12, ANNOUNCE.replace("0.4.0", "0.4.1")),
                (22, REMIND_DL.replace("0.4.0", "0.4.1"))], said

# after "none" the same version found again is announced again (nothing was waiting in between)
assert [x["t"] for x in got["after_none"]["log"]] == [0, 2], got["after_none"]

# the character's voice direction goes with every line; a failing lookup still speaks, plainly
assert [x["style"] for x in got["style"]["log"]] == ["gravelly pirate, slow"] * 2, got["style"]
assert [(x["text"], x["style"]) for x in got["style_throws"]["log"]] == [(ANNOUNCE, "")], got["style_throws"]

# a re-check while the update waits is no news: the reminders go on every 10 minutes, nothing is announced again
said = [(x["t"], x["text"]) for x in got["recheck"]["log"]]
assert said == [(0, ANNOUNCE), (10, REMIND_DL), (20, REMIND_DL), (30, REMIND_DL), (40, REMIND_DL)], said

# a failed re-check pauses the reminders; the next good answer resumes them without announcing again
said = [(x["t"], x["text"]) for x in got["error"]["log"]]
assert said == [(0, ANNOUNCE), (10, REMIND_DL), (55, REMIND_DL)], said

# a line whose style was still being looked up when the download started is dropped, not said late
assert got["stale"] == [], got["stale"]
print("OK")
