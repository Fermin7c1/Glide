#!/usr/bin/env python3
"""Dashboard "Coach context" panel: settings limits, cost inputs, live apply + config.json save (other keys kept),
and the /settings endpoint."""
import json
import os
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "companion"))
import tuning  # noqa: E402
from dashboard import Hub, post_json, serve  # noqa: E402

# limits
assert tuning.clamp("voice_lookahead_steps", 14) == 14 and tuning.clamp("voice_lookahead_steps", 99) == 30
assert tuning.clamp("lookahead_steps", -3) == 0 and tuning.clamp("lookahead_steps", "x") == 40
assert tuning.input_price("claude-sonnet-5-5") == 2.0 and tuning.input_price("claude-opus-5-5") == 4.0
assert tuning.input_price("claude-haiku-4-5-20251001") == 1.0 and tuning.input_price("claude-fable-5-1") == 10.0

# question rate: per hour over the last hour, at least 10 minutes of observation
now = 10000.0
assert tuning.rate_per_hour([], "voice", now) is None
assert tuning.rate_per_hour([(now - 60, "voice"), (now - 30, "voice"), (now - 20, "typed")], "voice", now) == 12.0
times = [(now - 3000 + i * 300, "voice") for i in range(10)]
assert tuning.rate_per_hour(times, "voice", now) == 12.0


class FakeCoach:
    cfg = {"fast_model": "claude-sonnet-5-5", "deep_model": "claude-opus-5-5", "voice_lookahead_steps": 12}
    look_tokens_per_step = 55.0
    q_rounds = {"voice": 1.5}
    q_times = [(time.time() - 100, "voice")]


snap = tuning.snapshot(FakeCoach(), review_minutes=15)
assert snap["settings"]["voice_lookahead_steps"]["value"] == 12 and snap["settings"]["lookahead_steps"]["value"] == 40
assert snap["tokens_per_step"] == 55.0 and snap["measured"]
v, t = snap["paths"]["voice"], snap["paths"]["typed"]
assert v["model"] == "claude-sonnet-5-5" and v["price_in"] == 2.0 and v["rounds"] == 1.5 and v["rate_measured"], v
assert t["price_in"] == 4.0 and t["rounds"] == 3.0 and t["rate_per_h"] == 0.0 and t["reviews_per_h"] == 4.0, t

# apply: live dict + config.json (everything else in the file kept)
tmp = tempfile.mkdtemp()
cfg_path = os.path.join(tmp, "config.json")
json.dump({"wow_dir": "D:\\WoW", "companion": {"llm": {"fast_model": "claude-sonnet-5-5"}, "rxp": {"ssf": True}}},
          open(cfg_path, "w", encoding="utf-8"))
live = {"fast_model": "claude-sonnet-5-5"}
applied = tuning.apply(live, {"voice_lookahead_steps": "18", "lookahead_steps": 500, "api_url": "evil"}, cfg_path)
assert applied == {"voice_lookahead_steps": 18, "lookahead_steps": 80}, applied
assert live == {"fast_model": "claude-sonnet-5-5", "voice_lookahead_steps": 18, "lookahead_steps": 80}, live
saved = json.load(open(cfg_path, encoding="utf-8"))
assert saved == {"wow_dir": "D:\\WoW", "companion": {"llm": {"fast_model": "claude-sonnet-5-5", "voice_lookahead_steps": 18,
                                                             "lookahead_steps": 80}, "rxp": {"ssf": True}}}, saved
assert [f for f in os.listdir(tmp)] == ["config.json"], os.listdir(tmp)            # no temp file left behind

# /settings endpoint
got = []
hub = Hub()
srv = serve(hub, 0, {("POST", "/settings"): post_json(
    lambda ch: got.append(ch) or dict(tuning.apply(live, ch), saved=False), lambda applied: {"applied": applied})})
port = srv.server_address[1]
req = urllib.request.Request("http://127.0.0.1:%d/settings" % port, data=json.dumps({"voice_lookahead_steps": 5}).encode(),
                             method="POST")
r = json.load(urllib.request.urlopen(req))
assert r == {"ok": True, "applied": {"voice_lookahead_steps": 5, "saved": False}} and live["voice_lookahead_steps"] == 5, r
page = urllib.request.urlopen("http://127.0.0.1:%d/" % port).read().decode()
srv.shutdown()
import desktop_schema  # noqa: E402  (the Settings page schema as the page gets it)
keys = desktop_schema.keys()
if keys is None:
    print("SKIP: the Settings page check (needs node and desktop/node_modules); the rest passed")
    raise SystemExit(77)
assert {"companion.llm.voice_lookahead_steps", "companion.llm.lookahead_steps"} <= keys,     "the lookahead sliders are in the app's Settings"
print("OK")
