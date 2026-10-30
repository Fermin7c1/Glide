#!/usr/bin/env python3
"""Out of credits / bad key: the coach stops calling the API (no retries), falls back to basic callouts,
and resumes when a question succeeds after topping up. Transient errors back off."""
import json, os, sys, threading, time, http.server
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "companion"))
os.environ["ANTHROPIC_API_KEY"] = "test"
from coach import Coach
from glidedb import GlideDB
from state import GameState
from rules import Trigger

class H(http.server.BaseHTTPRequestHandler):
    mode, hits = "broke", 0
    def log_message(self, *a): pass
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        H.hits += 1
        if H.mode == "broke":
            out, code = {"type": "error", "error": {"type": "invalid_request_error", "message": "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits."}}, 400
        elif H.mode == "overloaded":
            out, code = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}, 529
        else:
            out, code = {"content": [{"type": "text", "text": "Turn in Supplies."}], "usage": {}}, 200
        b = json.dumps(out).encode()
        self.send_response(code); self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

msgs = []
c = Coach({"api_url": "http://127.0.0.1:%d/v1/messages" % port, "fast_model": "F", "deep_model": "D", "busy_retry_s": 0.05},
          GlideDB("/nonexistent.db"), msgs.append)
c.state = GameState()
step = lambda: [Trigger("step_changed", {"from": 1, "to": 2}, 2)]

c._respond(step())
assert H.hits == 1, H.hits                                  # no "retry plain" on a billing 400
assert c.suspended == "out of API credits"
errs = [m for m in msgs if m["kind"] == "error"]
assert len(errs) == 1 and "credits" in errs[0]["text"] and errs[0]["speak"], errs
for _ in range(5):
    c._respond(step())                                      # callouts keep coming, API untouched
assert H.hits == 1, H.hits
assert any(m.get("model", "").startswith("offline") for m in msgs if m["kind"] == "callout")
c._respond([], question="where next?")                      # a question tries once
assert H.hits == 2 and msgs[-1]["kind"] == "answer" and "out of API credits" in msgs[-1]["text"], msgs[-1]
assert len([m for m in msgs if m["kind"] == "error"]) == 1  # not announced again
H.mode = "ok"
c._respond([], question="where next?")                      # topped up: resumes
assert H.hits == 3 and c.suspended is None and msgs[-1]["text"] == "Turn in Supplies."
c._respond(step()); assert H.hits == 4
H.mode = "overloaded"
c._respond(step()); assert H.hits == 6 and c.llm.backoff_until > time.time()   # one quick retry, then back off
assert msgs[-1]["kind"] == "error" and msgs[-1]["text"].startswith("Claude API 529 (F): Overloaded"), msgs[-1]
c._respond(step()); assert H.hits == 6                      # backing off
print("OK")
