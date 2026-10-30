#!/usr/bin/env python3
"""The dashboard page in a real browser (headless Edge, tests/dashboard_page_cdp.mjs), against the real server:
- a finished step's split keeps that step's route model (not the next step's);
- a question is sent with the page's token and reaches the coach;
- Alt+arrow in arrange mode announces both sections by name (not "undefined");
- outside arrange mode a panel moves by its header with the mouse and a top-bar piece by itself (a stat dropped on
  the character frame moves its whole block), all saved, while a drag in a panel's text moves nothing;
- a dropped event stream ends, the page reconnects, and the replayed chat isn't shown twice;
- when the game goes away, only the community notes stay on show (no docked section left open under them).

Skipped without Edge (or Chrome) and node. Run: python3 tests/test_dashboard_page.py   (prints OK)
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
sys.path.insert(0, os.path.join(ROOT, "tests", "golden"))
import dashboard  # noqa: E402
from probe_dashboard import edge  # noqa: E402

exe = edge()
if not exe or not shutil.which("node"):
    print("SKIP: needs Edge (or Chrome) and node")
    sys.exit(77)

init = json.load(open(os.path.join(ROOT, "tests", "golden", "fixtures", "dashboard_init.json"), encoding="utf-8"))
hub = dashboard.Hub()
hub.snapshot, hub.messages = init["state"], init["messages"]
connects = []
_connect = hub.connect
hub.connect = lambda *a: connects.append(1) or _connect(*a)
dashboard.PING_S = 0.3                 # a dropped stream notices quickly
asked = []
tmp = tempfile.mkdtemp()
srv = dashboard.serve(hub, 0, {("POST", "/ask"): dashboard.ask_route(asked.append)},
                      layout_path=os.path.join(tmp, "layout.json"), token="page-token")
url = "http://127.0.0.1:%d/#t=page-token" % srv.server_address[1]      # the app passes the token in the fragment


def state(index, model, seconds):
    """The fixture's state on guide step `index`, with the route model `model` s and `seconds` s on it."""
    s = copy.deepcopy(init["state"])
    steps = [st for st in s["steps"] if st.get("index", -1) >= index]
    assert steps and steps[0]["index"] == index, index
    s["steps"], s["step"], s["on_step_seconds"] = steps, index, seconds
    s["why"] = dict(s["why"], n=index, time_s=model)
    return s


node = subprocess.Popen(["node", os.path.join(ROOT, "tests", "dashboard_page_cdp.mjs"), exe,
                         os.path.join(tmp, "profile"), url],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                        encoding="utf-8")
seen = {}
try:
    while True:
        line = node.stdout.readline()
        if not line:
            break
        if not line.startswith("STEP "):
            continue
        name, _, value = line[5:].strip().partition(" ")
        seen[name] = json.loads(value)
        if name == "ready-steps":
            hub.publish("state", state(76, 90, 30))
        elif name == "played-76":
            hub.publish("state", state(77, 300, 0))
        elif name == "drop":
            with hub.lock:
                hub.clients.clear()        # as publish() does to a client whose queue is full
        elif name == "reconnected":
            hub.publish("message", {"kind": "info", "text": "after the reconnect", "t": 1791300100})
        elif name == "header-drag":
            with open(os.path.join(tmp, "layout.json"), encoding="utf-8") as fh:
                saved = json.load(fh).get("arrange", {})
                seen["saved-slots"], seen["saved-lists"] = saved.get("slots", {}), saved.get("lists", {})
        elif name == "wide":
            hub.publish("state", {"frames": 0, "community": init["state"].get("community")})
        node.stdin.write("go\n")
        node.stdin.flush()
    node.wait(timeout=60)
finally:
    if node.poll() is None:
        node.kill()
    srv.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)

assert "errors" in seen, "the browser run stopped early: %s %s" % (seen, node.stderr.read()[-2000:])
assert seen["errors"] == [], seen["errors"]
assert seen["loaded"]["docked"] > 0, "wide enough to dock sections into the wing"
splits = json.loads(seen["splits"])
assert splits[0]["n"] == 76 and splits[0]["model"] == 90, "the split keeps its own step's model: %s" % splits
assert asked == ["which way?"], asked
assert seen["asked"] and "which way?" in seen["asked"][-1], seen["asked"]
assert "undefined" not in seen["moved"] and seen["moved"].startswith("Moved ") and " after " in seen["moved"], seen["moved"]
drag = seen["header-drag"]
assert not drag["arranging"] and drag["feed"] == "", "a drag in the chat's text moves nothing: %s" % drag
assert drag["map"] == "route" and drag["live"] == "Minimap and Route tracker traded places.", drag
assert seen["saved-slots"].get("route") == "map", "the header drag is saved: %s" % seen["saved-slots"]
assert drag["bar"]["aurasFirst"].startswith("auras,unit"), "a top-bar piece moves by itself: %s" % drag["bar"]
assert drag["bar"]["lifted"].startswith("auras,pace,unit") and drag["bar"]["live"] == "Moved Pace before Character.",     "a stat dropped on the character frame moves the pace block: %s" % drag["bar"]
assert seen["saved-lists"].get("top") == ["auras", "pace", "unit"], "the bar order is saved: %s" % seen["saved-lists"]
assert len(connects) >= 2, "the page reconnected after its stream ended"
assert seen["reconnected"] == seen["drop"], "the replayed chat was added again: %d -> %d" % (seen["drop"], seen["reconnected"])
assert seen["after-message"] == seen["drop"] + 1, "a new message after the reconnect shows"
assert seen["wide"] > 0
assert seen["waiting"]["waiting"] and seen["waiting"]["shown"] == ["community"], seen["waiting"]
print("OK")
