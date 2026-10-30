"""Dashboard golden master: the coach dashboard served by the real companion/dashboard.py with a frozen game state
(tests/golden/fixtures/dashboard_init.json, captured once from a demo run), rendered in headless Edge at 2560x1440,
1920x1080 and 1150x800 with the clock frozen and animations off (dash_cdp.mjs). Recorded: every region's text, the
docked columns, the tabs, the middle's slots, the auras, the minimap marks, script errors."""
TIER = "portable"     # golden.py tier: a recorded fixture (needs Edge or Chrome and node)
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))


def edge():
    for p in (os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
              os.path.join(os.environ.get("ProgramFiles", ""), "Microsoft", "Edge", "Application", "msedge.exe")):
        if os.path.exists(p):
            return p
    return shutil.which("msedge") or shutil.which("chrome")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run():
    exe = edge()
    if not exe or not shutil.which("node"):
        return {"skipped": "needs Edge (or Chrome) and node"}
    sys.path.insert(0, os.path.join(ROOT, "companion"))
    import dashboard
    init = json.load(open(os.path.join(HERE, "fixtures", "dashboard_init.json"), encoding="utf-8"))
    hub = dashboard.Hub()
    hub.snapshot, hub.messages = init["state"], init["messages"]
    tmp = tempfile.mkdtemp()
    port = free_port()
    srv = dashboard.serve(hub, port, {("POST", "/ask"): dashboard.ask_route(lambda q: None)},
                          layout_path=os.path.join(tmp, "layout.json"))
    try:
        out = os.path.join(tmp, "dash.json")
        r = subprocess.run(["node", os.path.join(HERE, "dash_cdp.mjs"), exe, os.path.join(tmp, "profile"),
                            "http://127.0.0.1:%d/" % port, out], capture_output=True, text=True, timeout=180)
        if r.returncode or not os.path.exists(out):
            raise RuntimeError("dash_cdp failed: %s" % (r.stderr or r.stdout)[-2000:])
        return json.load(open(out, encoding="utf-8"))
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":                 # python probe_dashboard.py OUT.json
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        json.dump(run(), fh, indent=1, sort_keys=True, ensure_ascii=False)
