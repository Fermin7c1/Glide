// The desktop app's log files without Electron (test_desktop_logfile.py runs this): main/logfile.js appends each
// line to userData/logs/engine-YYYYMMDD.log, starts a new file each day and keeps the 7 newest, and a write that
// fails is counted, not thrown. node desktop_logfile_check.cjs <dist/main>
const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const dist = path.resolve(process.argv[2]);
const { LogFile, KEEP_DAYS } = require(path.join(dist, "logfile.js"));
const userData = fs.mkdtempSync(path.join(os.tmpdir(), "glide-logfile-"));
const dir = path.join(userData, "logs");
const read = (f) => fs.readFileSync(path.join(dir, f), "utf8");
const logs = () => fs.readdirSync(dir).filter((f) => f.endsWith(".log")).sort();

(async () => {
  try {
    assert.strictEqual(KEEP_DAYS, 7);
    // a file from before the logs folder had anything else in it is left alone
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "notes.txt"), "mine");
    let now = new Date(2026, 9, 1, 23, 59, 58, 5);
    const f = new LogFile(dir, { now: () => now });
    // a header per coach start, then the lines, in order, appended (two flushes, one file)
    f.header("coach start", "app: 0.3.30", "engine home: C:\\Glide\\engine", "python: py -3");
    f.write("[out] Glide companion: dashboard http://127.0.0.1:8800");
    await f.flush();
    f.write("[out] GLIDE_READY port=8800 app=0.3.30");
    f.write("[err] Traceback (most recent call last):");
    await f.flush();
    assert.deepStrictEqual(logs(), ["engine-20261001.log"]);
    assert.strictEqual(read("engine-20261001.log"),
      "23:59:58.005 ==== coach start | app: 0.3.30 | engine home: C:\\Glide\\engine | python: py -3\n" +
      "23:59:58.005 [out] Glide companion: dashboard http://127.0.0.1:8800\n" +
      "23:59:58.005 [out] GLIDE_READY port=8800 app=0.3.30\n" +
      "23:59:58.005 [err] Traceback (most recent call last):\n");
    // a new day, a new file; lines queued across midnight land in their own day's file
    f.write("[app] before midnight");
    now = new Date(2026, 9, 2, 0, 0, 1);
    f.write("[app] after midnight");
    await f.flush();
    assert.deepStrictEqual(logs(), ["engine-20261001.log", "engine-20261002.log"]);
    assert.match(read("engine-20261001.log"), /before midnight\n$/);
    assert.strictEqual(read("engine-20261002.log"), "00:00:01.000 [app] after midnight\n");
    // 8 days of lines: the 7 newest files stay, the oldest goes; other files are never touched
    for (let day = 3; day <= 8; day++) {
      now = new Date(2026, 9, day, 12, 0, 0);
      f.write(`[out] day ${day}`);
      await f.flush();
    }
    assert.deepStrictEqual(logs(), ["engine-20261002.log", "engine-20261003.log", "engine-20261004.log",
      "engine-20261005.log", "engine-20261006.log", "engine-20261007.log", "engine-20261008.log"]);
    assert.strictEqual(read("engine-20261008.log"), "12:00:00.000 [out] day 8\n");
    assert.strictEqual(fs.readFileSync(path.join(dir, "notes.txt"), "utf8"), "mine");
    // a later start (a new LogFile) appends to today's file and prunes too
    now = new Date(2026, 9, 9, 8, 0, 0);
    fs.writeFileSync(path.join(dir, "engine-20260901.log"), "old\n");
    const g = new LogFile(dir, { now: () => now });
    g.write("[app] next start");
    await g.flush();
    assert.strictEqual(logs().length, 7);
    assert.deepStrictEqual(logs().slice(-1), ["engine-20261009.log"]);
    assert.ok(!logs().includes("engine-20260901.log") && !logs().includes("engine-20261002.log"));
    // queued at quit: written synchronously
    g.write("[app] quitting");
    g.flushSync();
    assert.match(read("engine-20261009.log"), /next start\n08:00:00\.000 \[app\] quitting\n$/);
    // the folder can't be written (a file where it should be): nothing thrown, the lost lines counted
    const blocked = path.join(userData, "blocked");
    fs.writeFileSync(blocked, "a file, not a folder");
    const h = new LogFile(blocked, { now: () => now });
    h.write("one");
    h.write("two");
    await h.flush();
    assert.strictEqual(h.failures, 2);
    h.write("three");
    h.flushSync();
    assert.strictEqual(h.failures, 3);
    // the batching timer writes without anyone flushing
    const k = new LogFile(path.join(userData, "timer"), { now: () => now });
    k.write("[app] by itself");
    await new Promise((r) => setTimeout(r, 700));
    assert.match(fs.readFileSync(k.fileFor("20261009"), "utf8"), /by itself/);
    console.log("ok");
  } finally {
    fs.rmSync(userData, { recursive: true, force: true });
  }
})().catch((e) => { console.error(e); process.exit(1); });
