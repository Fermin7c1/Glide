#!/usr/bin/env python3
"""Push-to-talk loop with fake keyboard/mic/STT, and the coach's voice-question path with a fake API."""
import json, os, sys, threading, time, http.server
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "companion"))
import ptt

assert ptt.parse_key("F8") == [0x77] and ptt.parse_key("ctrl+q") == [0x11, 0x51] and ptt.parse_key("MOUSE5") == [6]
try:
    ptt.parse_key("F99"); raise SystemExit("bad key accepted")
except ValueError:
    pass

class FakeUser32:
    def __init__(self): self.down = False
    def GetAsyncKeyState(self, k): return 0x8000 if self.down else 0
class FakeRec:
    def __init__(self): self.calls = []
    def start(self): self.calls.append("start")
    def stop(self, path): self.calls.append("stop"); return path
class FakeSTT:
    name = "fake"
    def transcribe(self, path, prompt=""): self.prompt = prompt; return "should I skip the wolf quest"

got, status = [], []
p = ptt.PushToTalk.__new__(ptt.PushToTalk)
p.cfg = {"key": "F8", "beep": False, "min_seconds": 0.2}
p.keys = ptt.parse_key("F8"); p.user32 = FakeUser32(); p.rec = FakeRec(); p.stt = FakeSTT()
p.on_question = got.append; p.on_status = status.append; p.prompt_fn = lambda: "Names: Wolf Den"; p.focus_check = None
states = []; p.on_state = states.append
threading.Thread(target=p._loop, daemon=True).start()
p.user32.down = True; time.sleep(0.1); p.user32.down = False; time.sleep(0.2)     # tap: ignored
assert not got, got
p.user32.down = True; time.sleep(0.5); p.user32.down = False; time.sleep(0.3)     # hold: asks
assert got == ["should I skip the wolf quest"], got
assert p.stt.prompt == "Names: Wolf Den" and status[0]["kind"] == "question", status
# the indicator, as it happens: a tap listens and goes idle; a hold listens, transcribes, then goes idle
assert states == ["listening", "idle", "listening", "transcribing", "idle"], states
print("ptt loop OK", p.rec.calls)

# coach: voice question -> fast model, short answer, urgent (interrupts speech)
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        H.last = b
        out = json.dumps({"content": [{"type": "text", "text": "Skip it; the den is off-route."}], "usage": {}}).encode()
        self.send_response(200); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["ANTHROPIC_API_KEY"] = "test"
from coach import Coach
from glidedb import GlideDB
from state import GameState
msgs = []
c = Coach({"api_url": "http://127.0.0.1:%d/v1/messages" % port, "fast_model": "FAST", "deep_model": "DEEP"},
          GlideDB("/nonexistent.db"), msgs.append)
c.state = GameState()
c.start()
c.ask("should I skip the wolf quest", voice=True)
for _ in range(50):
    if msgs: break
    time.sleep(0.1)
assert H.last["model"] == "FAST" and H.last["max_tokens"] == 600, (H.last["model"], H.last["max_tokens"])
_c = H.last["messages"][0]["content"]
assert "push-to-talk" in (_c if isinstance(_c, str) else "".join(b["text"] for b in _c))
assert msgs[0]["urgent"] and msgs[0]["speak"] and msgs[0]["kind"] == "answer", msgs
c.ask("typed question")
for _ in range(50):
    if len(msgs) > 1: break
    time.sleep(0.1)
assert H.last["model"] == "DEEP" and not msgs[1]["urgent"], (H.last["model"], msgs[1])
print("coach voice path OK")
print("OK")
