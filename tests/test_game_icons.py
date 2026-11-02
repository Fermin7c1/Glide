#!/usr/bin/env python3
"""The desktop's glide://icon/<fileDataID>.png (desktop/src/main/game-icons.ts, compiled with the project's
TypeScript into a temp folder and run under Node): a cached icon answers at once; the misses of one moment share a
single decode run (rxp_import.py icons), and every request waits for the run that answers it; an icon that can't be
decoded is remembered and never asked again; a failed run (no client, no Python) answers null for a while instead of
starting a process per icon; only glide://icon/<digits>.png is an icon.

Needs node and desktop/node_modules (skips with 77 without them)."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DESK = os.path.join(HERE, "..", "desktop")
TSC = os.path.join(DESK, "node_modules", "typescript", "bin", "tsc")
SRC = os.path.join(DESK, "src", "main", "game-icons.ts")

node = shutil.which("node")
if not node or not os.path.exists(TSC):
    print("SKIP: needs node and desktop/node_modules (cd desktop && npm ci)")
    sys.exit(77)

SCRIPT = r"""
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { GameIcons, iconId, ICON_BATCH } = require(process.argv[2]);
const home = process.argv[3];
const dir = path.join(home, "data", "gamedata", "icons");
fs.mkdirSync(dir, { recursive: true });
fs.writeFileSync(path.join(dir, "100.png"), "png");
const DECODABLE = new Set([200, 201, 202]);
let clock = 0;
let runs = [], broken = false;
const decode = async (ids) => {
  runs.push(ids.slice());
  await new Promise((r) => setTimeout(r, 20));
  if (broken) throw new Error("no client");
  const ok = ids.filter((i) => DECODABLE.has(i));
  for (const i of ok) fs.writeFileSync(path.join(dir, `${i}.png`), "png");
  return ok;
};
const icons = new GameIcons(() => [home, path.join(home, "nowhere")], decode, () => clock, 5);
(async () => {
  assert.strictEqual(await icons.get(100), path.join(dir, "100.png"));
  assert.strictEqual(runs.length, 0, "a cached icon needs no run");
  for (const bad of [0, -1, 1.5, 2 ** 31, NaN]) assert.strictEqual(await icons.get(bad), null);
  // a burst of misses: one run, each id once, every request answered by it
  const got = await Promise.all([icons.get(200), icons.get(201), icons.get(200), icons.get(300)]);
  assert.deepStrictEqual(got, [path.join(dir, "200.png"), path.join(dir, "201.png"), path.join(dir, "200.png"), null]);
  assert.deepStrictEqual(runs, [[200, 201, 300]]);
  // asked again: the decoded one is cached, the one that can't be decoded is remembered
  assert.ok(await icons.get(201));
  assert.strictEqual(await icons.get(300), null);
  assert.strictEqual(runs.length, 1);
  // a miss arriving while a run is going waits for the next run
  const a = icons.get(202);
  await new Promise((r) => setTimeout(r, 8));
  const b = icons.get(400);
  assert.ok(await a);
  assert.strictEqual(await b, null);
  assert.deepStrictEqual(runs.slice(1), [[202], [400]]);
  // more misses than a run takes: several runs
  runs = [];
  const many = Array.from({ length: ICON_BATCH + 5 }, (_, i) => 10000 + i);
  await Promise.all(many.map((i) => icons.get(i)));
  assert.deepStrictEqual(runs.map((r) => r.length), [ICON_BATCH, 5]);
  // a failed run: null for a while, no process per icon, then tried again
  runs = [];
  broken = true;
  assert.strictEqual(await icons.get(500), null);
  assert.strictEqual(await icons.get(501), null);
  assert.strictEqual(runs.length, 1, "no run while the helper is broken");
  clock += 6 * 60 * 1000;
  broken = false;
  DECODABLE.add(501);
  assert.ok(await icons.get(501), "tried again later");
  assert.strictEqual(icons.find(999), null);
  // the URL
  assert.strictEqual(iconId("icon", "/134414.png"), 134414);
  for (const [h, p] of [["map", "/1.png"], ["icon", "/x.png"], ["icon", "/1.png/../2.png"], ["icon", "/12345678901.png"]]) {
    assert.strictEqual(iconId(h, p), null, `${h}${p}`);
  }
  console.log("ok");
})().catch((e) => { console.error(e); process.exit(1); });
"""

tmp = tempfile.mkdtemp(prefix="glide-game-icons-")
try:
    r = subprocess.run([node, TSC, SRC, "--target", "ES2022", "--module", "commonjs", "--strict", "--skipLibCheck",
                        "--esModuleInterop", "--types", "node", "--outDir", tmp],
                       capture_output=True, text=True, encoding="utf-8", cwd=DESK)
    assert r.returncode == 0, "tsc failed on game-icons.ts: %s" % (r.stdout + r.stderr)[-2000:]
    script = os.path.join(tmp, "check.cjs")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(SCRIPT)
    r = subprocess.run([node, script, os.path.join(tmp, "game-icons.js"), os.path.join(tmp, "home")],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    print(r.stdout + r.stderr)
    assert r.returncode == 0 and "ok" in r.stdout, "game-icons check failed"
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
