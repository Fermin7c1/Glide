#!/usr/bin/env python3
"""WoWSims on the player's PC (companion/wowsims.py), with no network: fake releases served from memory and a fake
runner (a script standing in for wowsimcli). The newest release is installed when it runs Glide's request (its zip
checked against the release's checksums), looked up at most once a day; one that doesn't run, or doesn't match its
checksum, is set aside and the last one that ran stays in use; offline, the one in use goes on; rotation files come
from the release's tag, an older release's copy when the newer has none; a sim's DPS, its errors; two releases kept.

Run: python3 tests/test_wowsims.py   (prints OK)
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import zipfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import wowsims  # noqa: E402
from simbuild import SimError  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


# a runner standing in for wowsimcli: argv = [tag, "sim", "--infile", in, "--outfile", out]; DPS 500 + 0.5 per bonus
# spell damage; a request whose player is named "bad" gets an error result, "crash" a failing exit
FAKE = r'''
import json, sys
tag, _, _, inp, _, out = sys.argv[1:7]
req = json.load(open(inp, encoding="utf-8"))
p = req["raid"]["parties"][0]["players"][0]
if p.get("name") == "crash":
    sys.stderr.write("panic: bad request\n"); sys.exit(2)
if p.get("name") == "bad":
    json.dump({"error": {"message": "unknown field \"frob\""}}, open(out, "w")); sys.exit(0)
sp = ((p.get("bonusStats") or {}).get("stats") or [0] * 6)[5]
json.dump({"raidMetrics": {"parties": [{"players": [{"dps": {"avg": 500 + 0.5 * sp, "stdev": 9}}]}]}}, open(out, "w"))
'''

tmp = tempfile.mkdtemp(prefix="glide-wowsims-")
try:
    runner = os.path.join(tmp, "fake_wowsimcli.py")
    with open(runner, "w", encoding="utf-8") as fh:
        fh.write(FAKE)

    def zipped(body=b"MZ fake runner"):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr(wowsims.EXE, body)
        return b.getvalue()

    WEB, CALLS = {}, []

    def release(tag, good_sum=True, asset=True):
        z = zipped(tag.encode())
        name = wowsims.asset_name()
        base = "https://github.com/wowsims/forever/releases/download/%s/" % tag
        WEB[base + name] = z
        WEB[base + "checksums.txt"] = ("%s  %s\n%s  other.zip\n" % (
            hashlib.sha256(z if good_sum else b"x").hexdigest(), name, "0" * 64)).encode()
        assets = [{"name": "checksums.txt", "browser_download_url": base + "checksums.txt"}]
        if asset:
            assets.append({"name": name, "browser_download_url": base + name})
        WEB[wowsims.LATEST] = json.dumps({"tag_name": tag, "assets": assets}).encode()

    def get(url, limit):
        CALLS.append(url)
        if WEB.get("offline"):
            raise urllib.error.URLError("no network")
        if url not in WEB:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return WEB[url]

    clock = [1000.0]
    smoked = []

    def smoke(ws, tag):
        smoked.append(tag)
        if tag == "v1.0.5":
            raise SimError("unknown field \"frob\"")
        if ws.sim({"raid": {"parties": [{"players": [{"name": "Glide"}]}]}}, tag) <= 0:
            raise SimError("no DPS")

    home = os.path.join(tmp, "home")
    ws = wowsims.WoWSims(home, get=get, command=lambda tag: [sys.executable, runner, tag], smoke=smoke,
                         now=lambda: clock[0])
    check(ws.current() is None, "nothing installed yet")

    # ---- the newest release, checked and tried, then in use -----------------------------------------------------------
    release("v1.0.4")
    check(ws.ensure() == "v1.0.4" and smoked == ["v1.0.4"] and os.path.exists(ws.exe()), ws.state)
    with open(ws.exe(), "rb") as fh:
        check(fh.read() == b"v1.0.4", "the runner from its zip")
    check(json.load(open(os.path.join(home, "data", "wowsims", "state.json")))["current"] == "v1.0.4", "kept on disk")
    n = len(CALLS)
    clock[0] += 3600
    check(ws.ensure() == "v1.0.4" and len(CALLS) == n, "looked up at most once a day")

    # ---- a newer one that doesn't run Glide's request: set aside, the last one stays -----------------------------------
    clock[0] += wowsims.CHECK_S
    release("v1.0.5")
    check(ws.ensure() == "v1.0.4" and "v1.0.5" in ws.state["failed"] and
          not os.path.exists(os.path.join(home, "data", "wowsims", "v1.0.5")), ws.state)
    clock[0] += wowsims.CHECK_S
    n = len(smoked)
    check(ws.ensure() == "v1.0.4" and len(smoked) == n, "not tried again")

    # ---- a checksum that doesn't match: tried again after RETRY_S, set aside the third time; no runner for this PC --
    clock[0] += wowsims.CHECK_S
    release("v1.0.6", good_sum=False)
    for i in range(wowsims.MISSES):
        check(ws.ensure() == "v1.0.4", ws.state)
        check(("v1.0.6" in ws.state["failed"]) == (i == wowsims.MISSES - 1), (i, ws.state["failed"]))
        clock[0] += wowsims.RETRY_S
    check("checksum" in ws.state["failed"]["v1.0.6"], ws.state["failed"])
    clock[0] += wowsims.CHECK_S
    release("v1.0.7", asset=False)
    check(ws.ensure() == "v1.0.4" and "no runner" in ws.state["failed"]["v1.0.7"], ws.state["failed"])

    # ---- offline: the one in use goes on, the network isn't asked again before RETRY_S, then the download -----------
    clock[0] += wowsims.CHECK_S
    release("v1.0.8")
    WEB["offline"] = True
    check(ws.ensure() == "v1.0.4" and "v1.0.8" not in ws.state["failed"], "offline")
    n = len(CALLS)
    clock[0] += wowsims.RETRY_S - 60
    check(ws.ensure() == "v1.0.4" and len(CALLS) == n, "not asked again on every use")
    del WEB["offline"]
    clock[0] += 60
    check(ws.ensure() == "v1.0.8", ws.state)
    clock[0] += wowsims.CHECK_S
    release("v1.0.9")
    check(ws.ensure() == "v1.0.9", ws.state)
    kept = sorted(t for t in os.listdir(os.path.join(home, "data", "wowsims")) if t.startswith("v"))
    check(kept == ["v1.0.8", "v1.0.9"], "two releases kept: %r" % kept)

    # ---- files of the repository at the release's tag; an older release's copy when the newer has none ------------
    path = "ui/specs/mage/dps/apls/arcane.apl.json"
    WEB[wowsims.RAW % (wowsims.REPO, "v1.0.8", path)] = b'{"type": "TypeAPL"}'
    check(ws.file(path, "v1.0.8") == b'{"type": "TypeAPL"}', "fetched at the tag")
    n = len(CALLS)
    check(ws.file(path, "v1.0.8") == b'{"type": "TypeAPL"}' and len(CALLS) == n, "kept beside the runner")
    check(ws.file(path) == b'{"type": "TypeAPL"}', "v1.0.9 has none: v1.0.8's copy")
    for bad in ("../state.json", "/etc/passwd", "ui/../../x"):
        try:
            ws.file(bad)
            check(False, "refused: " + bad)
        except SimError:
            pass
    try:
        ws.file("ui/specs/nope.json")
        check(False, "a file no release has")
    except SimError:
        pass

    # ---- simming: DPS, a result with an error, a runner that fails ---------------------------------------------------
    req = {"raid": {"parties": [{"players": [{"name": "Nine", "bonusStats": {"stats": [0, 0, 0, 0, 0, 40]}}]}]}}
    check(ws.sim(req) == 520.0, "DPS from the result")
    for name, want in (("bad", "unknown field"), ("crash", "panic")):
        try:
            ws.sim({"raid": {"parties": [{"players": [{"name": name}]}]}})
            check(False, name)
        except SimError as e:
            check(want in str(e), e)
    check(wowsims.dps({"raidMetrics": {"parties": [{"players": [{"dps": {"avg": 3}}]}]}})["avg"] == 3, "dps()")

    # ---- the first install, when the smoke test's rotation can't be fetched: not set aside, tried again later --------
    flaky = [True]

    def smoke_fetch(w, tag):
        if flaky[0]:
            raise wowsims.FetchError("WoWSims %s has no ui/specs/... (timed out)" % tag)

    clock2 = [5000.0]
    fresh = wowsims.WoWSims(os.path.join(tmp, "home2"), get=get, command=lambda tag: [sys.executable, runner, tag],
                            smoke=smoke_fetch, now=lambda: clock2[0])
    check(fresh.ensure() is None and fresh.state["failed"] == {}, fresh.state)
    flaky[0] = False
    check(fresh.ensure() is None, "not before RETRY_S")
    clock2[0] += wowsims.RETRY_S
    check(fresh.ensure() == "v1.0.9", fresh.state)

    # ---- a download cut short, an address that isn't https; tags that aren't folder names -----------------------------
    class Resp:
        def __init__(self, body, size):
            self.body, self.headers = body, {"Content-Length": str(size)}

        def read(self, n):
            return self.body[:n]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Opener:
        def __init__(self, resp):
            self.resp = resp

        def open(self, req, timeout):
            return self.resp

    real = wowsims._opener
    try:
        wowsims._opener = Opener(Resp(b"abc", 3))
        check(wowsims._get("https://example.com/x.zip", 10) == b"abc", "a whole download")
        for opener, url, want in ((Opener(Resp(b"abc", 9)), "https://example.com/x.zip", "cut short"),
                                  (Opener(Resp(b"abcdef", 6)), "https://example.com/x.zip", "bigger"),
                                  (Opener(Resp(b"abc", 3)), "http://example.com/x.zip", "https only")):
            wowsims._opener = opener
            try:
                wowsims._get(url, 4)
                check(False, want)
            except ValueError as e:
                check(want in str(e), e)
    finally:
        wowsims._opener = real
    check(all(wowsims.TAG.match(t) is None for t in (".", "..", ".x", "a/b", "")) and wowsims.TAG.match("v1.0.4"),
          "tags")

    # ---- a state file from another time, damaged: nothing in use, looked up again ------------------------------------
    with open(os.path.join(home, "data", "wowsims", "state.json"), "w") as fh:
        fh.write('{"current": "../../evil", "failed": [1]}')
    again = wowsims.WoWSims(home, get=get, command=lambda tag: [sys.executable, runner, tag], smoke=smoke)
    check(again.state["current"] is None and again.state["failed"] == {}, again.state)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
