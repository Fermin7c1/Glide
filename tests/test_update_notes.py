#!/usr/bin/env python3
"""A new Glide, told with what's in it: the release's notes (release.yml: "Glide <v>: <title>", the items, "source:")
reach the app (electron-updater's releaseNotes -> desktop/src/main/release-notes.ts) and go with the update notice to
the engine (POST /announce), where the coach says it in its own voice (in character when it plays one: the fast
model, the one to three things the player would care about), else a plain line with the first items. On the coach
thread's queue, never over another line. Against a fake model server (no paid API).

Run: python3 tests/test_update_notes.py   (prints OK)
"""
import http.server
import json
import os
import subprocess
import sys
import threading
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from glidedb import GlideDB  # noqa: E402
import coach  # noqa: E402
import dashboard  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


NOTES = ["In character, the coach remembers your story: what happened each session, the jokes, and brings them back.",
         "Talk over the coach like on a call: hold push-to-talk and it stops at once.",
         "Fixed: the coach chat follows the newest message again."]

# ---- the engine's route ---------------------------------------------------------------------------------------
got = []
route = dashboard.announce_route(lambda *a: got.append(a))
req = lambda body: types.SimpleNamespace(json=lambda: body)  # noqa: E731
check(route(req({"what": "ready", "version": "0.5.0", "title": "Remembering you", "notes": NOTES + [""]}))[0] == 200,
      "always ok")
check(got == [("ready", "0.5.0", "Remembering you", NOTES)], got)
route(req({"version": "", "notes": NOTES}))
route(req([1, 2]))


def bad():
    raise ValueError("not json")


route(types.SimpleNamespace(json=bad))
check(len(got) == 1, "no version or a bad body: nothing said")
route(req({"what": "weird", "version": "0.5.1", "notes": "x"}))
check(got[-1] == ("available", "0.5.1", "", []), got[-1])

# ---- the coach: queued on its thread, a plain line without a model -------------------------------------------
msgs = []
c = coach.Coach({}, GlideDB("/x.db"), lambda m: msgs.append(m), offline_ok=True)
c.announce_update("available", "0.5.0", "Remembering you", NOTES)
q, args = c.asks.get_nowait()
check(q is coach.ANNOUNCE and args == ("available", "0.5.0", "Remembering you", NOTES), "on the coach thread's queue")
c._announce(*args)
m = msgs[-1]
check(m["kind"] == "callout" and m["speak"] and m["text"].startswith("A Glide update is out: version 0.5.0. What's new:")
      and "the coach remembers your story" in m["text"] and "and more" in m["text"] and "footer button" in m["text"], m)
c._announce("ready", "0.5.0", "", [])
check(msgs[-1]["text"] == "Glide 0.5.0 is downloaded. Restart Glide to update, from the footer button, when it suits "
      "you.", msgs[-1])


# ---- with a model: in its own voice (in character), picking what matters --------------------------------------
class A(http.server.BaseHTTPRequestHandler):
    bodies = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        A.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        text = ("TONE: proud, booming\nOi, recruit! Glide 0.5.0's out: I'll remember yer every blunder now, and ye can "
                "shout over me like a proper sergeant. Grab it from the footer when ye've a moment!\nFACTS: none")
        out = {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
               "usage": {"input_tokens": 10, "output_tokens": 3}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), A)
threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True).start()
os.environ["ANTHROPIC_API_KEY"] = "test"
c2 = coach.Coach({"api_url": "http://127.0.0.1:%d/v1/messages" % srv.server_address[1],
                  "fast_model": "claude-haiku-4-5-20251001", "deep_model": "claude-sonnet-5-5"}, GlideDB("/x.db"),
                 lambda m: msgs.append(m), offline_ok=True)
c2.cfg["persona"] = "A grizzled dwarf drill sergeant. Calls you recruit."
c2._announce("available", "0.5.0", "Remembering you", NOTES)
m = msgs[-1]
check(m["text"].startswith("Oi, recruit! Glide 0.5.0's out") and m.get("tone") == "proud, booming" and m["speak"], m)
body = A.bodies[-1]
user = body["messages"][-1]["content"]
user = user if isinstance(user, str) else "".join(x.get("text", "") for x in user if x.get("type") == "text")
check("GLIDE UPDATE" in user and NOTES[1] in user and "(Remembering you)" in user and "footer button" in user, user)
check("Stay fully in character" in user and body["model"] == "claude-haiku-4-5-20251001", "in character, fast model")
srv.shutdown()

# ---- the app: the release's notes as What's new (release-notes.ts) -------------------------------------------
import desktop_ts  # noqa: E402

if not desktop_ts.node or not desktop_ts.available():
    print("SKIP: the release-notes part needs node and desktop/node_modules (cd desktop && npm ci)")
    sys.exit(77)
mod = os.path.join(desktop_ts.main(), "main", "release-notes.js")
RUN = r"""
const { releaseNotes } = require(process.argv[1]);
const html = "<p>Glide 0.5.0: Remembering you</p>\n<ul>\n<li>The coach remembers &amp; recalls.</li>\n" +
  "<li>Talk <b>over</b> the coach.</li>\n</ul>\n<p>source: 0123456789012345678901234567890123456789</p>";
const md = "Glide 0.5.0: Remembering you\n\n- The coach remembers.\n- Talk over the coach.\n\nsource: abc";
console.log(JSON.stringify({
  html: releaseNotes(html, "0.5.0"), md: releaseNotes(md, "0.5.0"),
  list: releaseNotes([{ version: "0.4.9", note: "- old" }, { version: "0.5.0", note: "- new one" }], "0.5.0"),
  none: releaseNotes(null), plain: releaseNotes("Glide 0.5.0\n\nsource: abc", "0.5.0"),
}));
"""
r = subprocess.run([desktop_ts.node, "-e", RUN, mod], capture_output=True, text=True, encoding="utf-8", timeout=60)
check(r.returncode == 0, r.stderr[-1500:])
out = json.loads(r.stdout)
check(out["html"] == {"title": "Remembering you", "notes": ["The coach remembers & recalls.",
                                                            "Talk over the coach."]}, out["html"])
check(out["md"] == {"title": "Remembering you", "notes": ["The coach remembers.", "Talk over the coach."]}, out["md"])
check(out["list"]["notes"] == ["new one"], out["list"])
check(out["none"] == {"title": "", "notes": []} and out["plain"] == {"title": "", "notes": []}, (out["none"], out["plain"]))

# ---- release.yml writes them: the version's What's new above its "source:" line --------------------------------
with open(os.path.join(ROOT, ".github", "workflows", "release.yml"), encoding="utf-8") as fh:
    wf = fh.read()
check('$head = if ($entry -and $entry.title) { "Glide ${v}: $($entry.title)" }' in wf and
      '"$head`n`n$items`n`nsource: $env:GITHUB_SHA"' in wf, "the release body carries What's new, source last")
print("OK")
