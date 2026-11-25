#!/usr/bin/env python3
"""A sim's "Open in WoWSims" on the coach screen (CoachMessage.link, desktop/src/shared/engine.ts).

The page (companion/static/dashboard.js) shows the link, with WoWSims' credit beside it, only when its url starts with
https://www.wowsims.com/forever/ (a lookalike host, another site, plain http, a script or a url that would break out
of the attribute shows nothing). In a browser it opens in a new tab; in the app's frame the page opens nothing itself
and asks the app ({glide: "open", url}), and the app (desktop/src/renderer/coach.ts) opens it through shell:open, only
a WoWSims link and only when the message comes from the coach page (its frame, the engine's origin).

Two halves, each skipped without what it needs (the test exits 77 when neither can run):
- the app: coach.ts compiled with the project's TypeScript beside stand-ins and run under Node (node and
  desktop/node_modules);
- the page: served by the real companion/dashboard.py and driven in headless Edge (tests/coach_link_cdp.mjs) at
  2560x1440, on its own and in a frame (Edge or Chrome, and node).

Run: python3 tests/test_coach_link.py   (prints OK)"""
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

import desktop_ts

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
RENDERER = os.path.join(ROOT, "desktop", "src", "renderer")
SHARED = os.path.join(ROOT, "desktop", "src", "shared")
sys.path.insert(0, os.path.join(ROOT, "companion"))
sys.path.insert(0, os.path.join(HERE, "golden"))
from probe_dashboard import edge  # noqa: E402

read = lambda *p: open(os.path.join(ROOT, *p), encoding="utf-8").read()  # noqa: E731

GOOD = ("https://www.wowsims.com/forever/mage/dps/?i=gtm#eJzjYmFgYGBkYBRgEmBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBg"
        "YGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGB+/A==")
CREDIT = "WoWSims (wowsims.com/forever)"
# urls neither side accepts
BAD = ["https://evil.example/forever/mage/dps/", "https://www.wowsims.com.evil.example/forever/mage/dps/",
       "http://www.wowsims.com/forever/mage/dps/", "https://www.wowsims.com/foreverx/mage/dps/",
       "https://www.wowsims.com/forever", "https://wowsims.com/forever/mage/dps/",
       "javascript:alert(1)//https://www.wowsims.com/forever/", 'https://www.wowsims.com/forever/x"><img src=x>',
       "https://www.wowsims.com/forever/mage/dps/ x", "https://www.wowsims.com/forever/mage/dps/\nx",
       "https://www.wowsims.com/forever/" + "A" * 5000]

# both sides check the same pattern
pattern = re.search(r"const WOWSIMS_LINK = (/.*/);", read("companion", "static", "dashboard.js")).group(1)
assert "export const WOWSIMS_LINK = %s;" % pattern in read("desktop", "src", "renderer", "coach.ts"), pattern
assert "link?: CoachLink;" in read("desktop", "src", "shared", "engine.ts")
assert "export interface CoachLink { text: string; url: string; credit: string }" in read("desktop", "src", "shared",
                                                                                          "engine.ts")
ran = []

# ---- the app: a message from the coach page opens a WoWSims link through shell:open, and nothing else -------------

STUBS = {
    "bridge.ts": """
export const glide: { invoke(ch: string, ...a: unknown[]): Promise<unknown>; on(ch: string, fn: (p: any) => void): () => void }
  = (window as any).glide;
export const call = (...a: unknown[]) => { (globalThis as any).calls.push(a); return Promise.resolve(undefined); };
export const ipcError = (e: unknown) => String(e);
""",
    "dom.ts": """
export const toast = (..._a: unknown[]) => undefined;
const els: Record<string, any> = {};
export function $<T = any>(id: string): T {
  return (els[id] ??= { id, hidden: true, className: "", textContent: "", title: "", disabled: false, style: {},
                        contentWindow: id === "dash" ? (globalThis as any).frameWindow : undefined,
                        classList: { add() {}, remove() {} }, removeAttribute() {} }) as T;
}
""",
    "narrator.ts": "export const speech = { stop() {}, bargeIn() {}, say(..._a: unknown[]) {} };\n",
    "session-bar.ts": "export const sessionBar = (_s: unknown) => undefined;\n",
    "state.ts": "export const live: any = (globalThis as any).live;\n",
    "status.ts": "export const refreshStatus = () => undefined;\n",
    "types.ts": """
export type { LiteInitEvent, PttEvent, SpeakEvent } from "./engine.js";
export interface EngineStatus { state: string; detail: string; port: number; url: string | null; token: string }
""",
}

RUN = r"""
const handlers = [];
globalThis.window = globalThis;
globalThis.addEventListener = (name, fn) => { if (name === "message") handlers.push(fn); };
globalThis.glide = { invoke: () => Promise.resolve(), on: () => () => {} };
globalThis.calls = [];
const frame = { coachPage: true }, other = { coachPage: false };
globalThis.frameWindow = frame;
globalThis.live = { engineState: "running", engineToken: "t", engineUrl: "http://127.0.0.1:5123" };
const { wireCoach } = await import(process.argv[2]);
const [good, bad] = JSON.parse(process.argv[3]);
wireCoach();
const ORIGIN = "http://127.0.0.1:5123";
const send = (data, source = frame, origin = ORIGIN) => {
  const n = calls.length;
  for (const h of handlers) h({ data, source, origin });
  return calls.slice(n);
};
const out = { handlers: handlers.length };
out.good = send({ glide: "open", url: good });
out.bad = bad.map((url) => send({ glide: "open", url }));
out.otherWindow = send({ glide: "open", url: good }, other);
out.otherOrigin = send({ glide: "open", url: good }, frame, "http://127.0.0.1:5124");
out.voice = send({ glide: "voice", url: good });
out.notString = [send({ glide: "open", url: { toString: () => good } }), send({ glide: "open" }), send(null), send(good)];
live.engineUrl = null;                           // the coach stopped
out.stopped = send({ glide: "open", url: good });
console.log(JSON.stringify(out));
"""

if desktop_ts.node and desktop_ts.available():
    files = dict(STUBS)
    for name, folder in (("coach.ts", RENDERER), ("engine.ts", SHARED)):
        with open(os.path.join(folder, name), encoding="utf-8") as fh:
            files[name] = fh.read()
    out = desktop_ts.compiled("coach-link", files, ["coach.ts", "--target", "ES2023", "--module", "ES2022",
                                                   "--moduleResolution", "bundler", "--lib", "ES2023,DOM", "--strict",
                                                   "--skipLibCheck"])
    tmp = tempfile.mkdtemp(prefix="glide-coach-link-")
    try:
        run = os.path.join(tmp, "run.mjs")
        with open(run, "w", encoding="utf-8") as fh:
            fh.write(RUN)
        r = subprocess.run([desktop_ts.node, run, desktop_ts.url(os.path.join(out, "coach.js")), json.dumps([GOOD, BAD])],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert r.returncode == 0, "running the Coach page failed: %s" % r.stderr[-2000:]
        app = json.loads(r.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert app["handlers"] == 1, app
    assert app["good"] == [["shell:open", GOOD]], "a WoWSims link from the coach page opens through shell:open: %s" % app
    assert all(c == [] for c in app["bad"]), "no other url opens: %s" % list(zip(BAD, app["bad"]))
    assert app["otherWindow"] == [] and app["otherOrigin"] == [], "only from the coach page itself: %s" % app
    assert app["voice"] == [] and app["notString"] == [[], [], [], []] and app["stopped"] == [], app
    ran.append("app")
else:
    print("skipped the app half: needs node and desktop/node_modules (cd desktop && npm ci)")

# ---- the page: the link and its credit, only for WoWSims; a click opens it in a tab, or asks the app ---------------

exe = edge()
if exe and shutil.which("node"):
    import dashboard

    init = json.load(open(os.path.join(HERE, "golden", "fixtures", "dashboard_init.json"), encoding="utf-8"))
    link = lambda url: {"text": "Open in WoWSims", "url": url, "credit": CREDIT}  # noqa: E731
    messages = [{"kind": "answer", "speak": True, "t": 1791300000, "link": link(GOOD),
                 "text": "WoWSims: Eye of the Beast over your Briarwood Reed, about +8 DPS on your gear."}]
    messages += [{"kind": "answer", "t": 1791300001 + i, "text": "bad %d" % i, "link": link(url)}
                 for i, url in enumerate(BAD)]
    messages += [{"kind": "callout", "t": 1791300100, "text": "No link on this one."},
                 {"kind": "answer", "t": 1791300101, "text": "a link with no credit", "link": {"text": "", "url": GOOD}}]
    hub = dashboard.Hub()
    hub.snapshot, hub.messages = init["state"], messages
    tmp = tempfile.mkdtemp(prefix="glide-coach-link-page-")
    srv = dashboard.serve(hub, 0, {("POST", "/ask"): dashboard.ask_route(lambda q: None)},
                          layout_path=os.path.join(tmp, "layout.json"), token="page-token")
    url = "http://127.0.0.1:%d/#t=page-token" % srv.server_address[1]

    class Parent(http.server.BaseHTTPRequestHandler):
        """The page that frames the coach page, from another origin (as the app's window does)."""
        def do_GET(self):
            body = b"<!doctype html><meta charset=utf-8><title>frame</title><body style='margin:0'>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    parent = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Parent)
    threading.Thread(target=parent.serve_forever, daemon=True).start()
    try:
        args = ["node", os.path.join(HERE, "coach_link_cdp.mjs"), exe, os.path.join(tmp, "profile"), url,
                "http://127.0.0.1:%d/" % parent.server_address[1], str(len(messages))]
        if os.environ.get("GLIDE_SHOTS"):              # a folder for screenshots of the chat (2560x1440, 1280x720)
            args.append(os.environ["GLIDE_SHOTS"])
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=120)
        assert r.returncode == 0 and r.stdout.strip(), "the browser run failed: %s" % (r.stderr or r.stdout)[-2000:]
        page = json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        srv.shutdown()
        parent.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    assert page["errors"] == [], page["errors"]
    for where in ("top", "framed"):
        shown = page[where]
        assert len(shown or []) == len(messages), (where, len(shown or []), page.get("frame"), page["errors"])
        first, last = shown[0], shown[-1]
        assert len(first["links"]) == 1 and first["anchors"] == 1, (where, first)
        a = first["links"][0]
        assert a["href"] == GOOD and a["text"] == "Open in WoWSims", (where, a)
        assert a["target"] == "_blank" and "noopener" in a["rel"].split(), "never an inline navigation: %s" % a
        assert a["credit"] == CREDIT and a["beside"], "the credit beside the link: %s" % a
        assert first["text"].endswith("on your gear. Open in WoWSims" + CREDIT), first["text"]
        assert a["color"] == "rgb(242, 194, 79)" and a["fontSize"] == "15px" and a["creditSize"] == "12px", (
            "styled like the message's other links, the credit small: %s" % a)
        for m in shown[1:1 + len(BAD)]:
            assert m["links"] == [] and m["credits"] == 0 and m["anchors"] == 0, "no link but WoWSims': %s" % m
        assert shown[1 + len(BAD)]["anchors"] == 0 and shown[1 + len(BAD)]["credits"] == 0
        assert len(last["links"]) == 1 and last["links"][0]["text"] == "Open in WoWSims" and \
            last["links"][0]["credit"] == CREDIT, "a link with no text or credit still says what and whose: %s" % last
    assert page["overflow"] <= 0, "the chat doesn't scroll sideways at 2560x1440: %s" % page["overflow"]
    # on its own the page leaves the click to the browser (a new tab), and asks no one
    assert page["topClick"] == {"prevented": False, "at": True}, page["topClick"]
    # in a frame it opens nothing itself (no new window, the frame stays) and asks the app, a middle click too
    assert page["frameClick"] == {"prevented": True, "at": True}, page["frameClick"]
    assert page["frameAux"] == {"prevented": True}, page["frameAux"]
    assert page["got"] == [{"data": {"glide": "open", "url": GOOD}, "fromFrame": True}] * 2, page["got"]
    assert page["newPages"] == {"top": 0, "frame": 0}, page["newPages"]
    ran.append("page")
else:
    print("skipped the page half: needs Edge (or Chrome) and node")

if not ran:
    print("SKIP: needs node (and desktop/node_modules, or Edge)")
    sys.exit(77)
print("OK")
