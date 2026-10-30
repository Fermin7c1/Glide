#!/usr/bin/env python3
"""Buff tracking: verified buff pages -> state -> rules (drop/expiring/watch) -> coach WATCH directive."""
import json, os, sys, threading, time, http.server
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "companion"))
from state import GameState
from rules import Rules

def h(s):
    x = 0
    for b in s.encode():
        x = (x * 31 + b) % 65521
    return x

st, rules = GameState(), Rules({"buffs": {"min_duration": 120, "warn_before": 30}})
T = 500000
def frame(buffs, combat=0, t=T, ak=0, ev=None):
    joined = ",".join(buffs)
    core = {k: str(v) for k, v in {"t": t, "l": 10, "x": 1, "X": 100, "c": combat, "an": 1, "ah": h(joined), "ak": ak, "st": 3}.items()}
    if ev:
        core["e1"] = ev
    ring = st.update(core, {"pg": "A0", "a": joined})
    trs = rules.check(st, ring)
    assert not [x for x in trs if x.kind == "cst"], trs          # cast events are never callouts
    return [tr for tr in trs if tr.kind.startswith("buff")]

bs = "6673:%d:120:m:Battle Shout" % (T + 100)
fort = "1243:%d:1800::Power Word  Fortitude" % (T + 1700)
mh = "mh:%d:0:m:Main-hand enchant" % (T + 900)
assert frame([bs, fort, mh]) == []
b = st.summary()["buffs"]
assert b[0]["name"] == "Battle Shout" and b[0]["remaining"] == 100 and b[0]["mine"], b
# Battle Shout gets close to expiring -> warning (out of combat)
tr = frame([bs, fort, mh], t=T + 75)
assert tr and tr[0].kind == "buff_expiring" and tr[0].facts["buff"] == "Battle Shout", tr
assert frame([bs, fort, mh], t=T + 80) == []           # warned once
# the list read in combat is frozen -> no drop; the first list out of combat counts, after a 4 s grace
assert frame([fort, mh], combat=1, t=T + 101) == []
assert frame([fort, mh], combat=0, t=T + 105) == []
tr = frame([fort, mh], combat=0, t=T + 109)
assert tr and tr[0].kind == "buffs_dropped" and tr[0].facts["buffs"] == ["Battle Shout"], tr
# recast in combat: the addon's cast event refreshes it although the aura list stays frozen
assert frame([fort, mh], combat=1, t=T + 110, ev="1:cst:6673") == []
b = {x["name"]: x for x in st.summary()["buffs"]}
assert b["Battle Shout"]["remaining"] == 120, b
assert frame([fort, mh], combat=0, t=T + 112) == []               # stale list right after combat: still counts as up
bs2 = "6673:%d:120:m:Battle Shout" % (T + 230)
assert frame([bs2, fort, mh], t=T + 114) == [] and not st.buff_override  # list caught up
# expired, recast within the grace period -> no reminder
assert frame([fort, mh], t=T + 231) == []
assert frame(["6673:%d:120:m:Battle Shout" % (T + 353), fort, mh], t=T + 233) == []
assert frame(["6673:%d:120:m:Battle Shout" % (T + 353), fort, mh], t=T + 240) == []
# expiry warning: 30 s here (config), allowed in combat
tr = frame(["6673:%d:120:m:Battle Shout" % (T + 353), fort, mh], combat=1, t=T + 330)
assert tr and tr[0].kind == "buff_expiring", tr
fort, mh = "1243:%d:1800::Power Word  Fortitude" % (T + 1700), "mh:%d:0:m:Main-hand enchant" % (T + 900)
assert frame([fort, mh], t=T + 400) == [] and frame([fort, mh], t=T + 405)[0].kind == "buffs_dropped"
# someone else's Fortitude dropping is ignored by default
assert frame([mh], t=T + 410) == [] and frame([mh], t=T + 415) == []
# hidden aura data (combat) must not look like everything dropped
assert frame([], ak=1, combat=1, t=T + 415) == []
# watched short buff (e.g. 30 s) gets reminded when it drops
rules.watch("Sharpen Blade")
frame([mh, "999:%d:30:m:Sharpen Blade" % (T + 440)], t=T + 420)
assert frame([mh], t=T + 425) == []
tr = frame([mh], t=T + 429)
assert tr and tr[0].facts["buffs"] == ["Sharpen Blade"], tr
print("rules OK")

# coach: WATCH directive is applied and stripped from the spoken text
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        out = json.dumps({"content": [{"type": "text", "text": "Will do.\nWATCH: Battle Shout"}], "usage": {}}).encode()
        self.send_response(200); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["ANTHROPIC_API_KEY"] = "test"
from coach import Coach, CoachDeps
from glidedb import GlideDB
msgs, watched = [], []
c = Coach({"api_url": "http://127.0.0.1:%d/v1/messages" % port, "fast_model": "F", "deep_model": "D"},
          GlideDB("/nonexistent.db"), msgs.append,
          deps=CoachDeps(on_watch=lambda n, on: watched.append((n, on)), watched_fn=lambda: set()))
c.state = st
c.start()
c.ask("remind me to reapply battle shout", voice=True)
for _ in range(50):
    if any(m["kind"] == "answer" for m in msgs): break
    time.sleep(0.1)
ans = [m for m in msgs if m["kind"] == "answer"][0]
assert ans["text"] == "Will do." and watched == [("Battle Shout", True)], (ans, watched)
print("coach WATCH OK")
print("OK")
