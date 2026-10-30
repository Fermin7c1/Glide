#!/usr/bin/env python3
"""Online push-to-talk engines (OpenAI-style multipart and Deepgram) against a mock server, and the fallback to the
local engine when the online one fails."""
import http.server
import json
import os
import sys
import tempfile
import threading
import wave

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import ptt  # noqa: E402


class H(http.server.BaseHTTPRequestHandler):
    seen = []
    fail = False

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        H.seen.append((self.path, self.headers.get("authorization"), self.headers.get("content-type"), body))
        if H.fail:
            self.send_response(429)
            self.end_headers()
            self.wfile.write(b'{"error":"rate limited"}')
            return
        out = ({"results": {"channels": [{"alternatives": [{"transcript": "where is Marshal Dughan"}]}]}}
               if self.path.startswith("/v1/listen") else {"text": "how far to Goldshire"})
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
ptt.ONLINE["openai"] = ("OpenAI", "http://127.0.0.1:%d/v1/audio/transcriptions" % port, "OPENAI_API_KEY", "gpt-4o-mini-transcribe")
ptt.ONLINE["deepgram"] = ("Deepgram", "http://127.0.0.1:%d/v1/listen" % port, "DEEPGRAM_API_KEY", "nova-3")

wav = os.path.join(tempfile.mkdtemp(), "q.wav")
with wave.open(wav, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes(b"\x00\x00" * 1600)

# no key: refuses to start (the companion then uses local speech)
os.environ.pop("OPENAI_API_KEY", None)
try:
    ptt.OnlineSTT("openai")
    raise AssertionError("expected a missing-key error")
except ValueError as e:
    assert "OPENAI_API_KEY" in str(e)

os.environ["OPENAI_API_KEY"] = "sk-test"
s = ptt.OnlineSTT("openai")
assert s.transcribe(wav, "World of Warcraft leveling question. Names: Goldshire") == "how far to Goldshire"
path, auth, ctype, body = H.seen[-1]
assert auth == "Bearer sk-test" and ctype.startswith("multipart/form-data; boundary=")
assert b'name="model"\r\n\r\ngpt-4o-mini-transcribe' in body and b'filename="question.wav"' in body and b"RIFF" in body
assert b"Names: Goldshire" in body

os.environ["DEEPGRAM_API_KEY"] = "dg-test"
d = ptt.OnlineSTT("deepgram")
assert d.transcribe(wav, "x. Names: Marshal Dughan, Goldshire") == "where is Marshal Dughan"
path, auth, ctype, body = H.seen[-1]
assert auth == "Token dg-test" and ctype == "audio/wav" and body.startswith(b"RIFF")
assert "model=nova-3" in path and "keyterm=Marshal+Dughan" in path and "keyterm=Goldshire" in path, path

# a failing online engine falls back to the local one for that question
H.fail = True
try:
    s.transcribe(wav)
    raise AssertionError("expected an HTTP error")
except RuntimeError as e:
    assert "429" in str(e)


class Local:
    name = "local"

    def transcribe(self, path, prompt=""):
        return "what next"


got, status = [], []
p = ptt.PushToTalk.__new__(ptt.PushToTalk)
p.cfg, p.on_question, p.on_status, p.prompt_fn = {}, got.append, status.append, None
p.stt, p.fallback = s, Local()
p._handle(wav)
assert got == ["what next"], (got, status)
assert any("failed" in m["text"] and "using local" in m["text"] for m in status), status
srv.shutdown()
print("ptt online: ok")
