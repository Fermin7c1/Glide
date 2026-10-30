// The desktop app's own JSON stores without Electron (test_desktop_stores.py runs this): app-settings.json and
// secrets.json. A broken one used to read as {} and then be saved whole, so a stray byte in secrets.json lost every
// saved API key. node desktop_stores_check.cjs <dist/main>
const Module = require("module");
const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const dist = path.resolve(process.argv[2]);
const userData = fs.mkdtempSync(path.join(os.tmpdir(), "glide-stores-"));
const electron = new Proxy({
  app: { getPath: () => userData, getVersion: () => "1.0.0", isPackaged: false, getAppPath: () => path.dirname(dist),
         on() {}, whenReady: () => new Promise(() => {}), requestSingleInstanceLock: () => true },
  ipcMain: { handle() {}, on() {} },
  // a stand-in for DPAPI: reversible, so the test can read what was saved
  safeStorage: { isEncryptionAvailable: () => true, encryptString: (s) => Buffer.from("enc:" + s),
                 decryptString: (b) => b.toString().replace(/^enc:/, "") },
}, { get: (t, k) => (k in t ? t[k] : new Proxy(function () {}, { get: () => () => {} })) });
const resolve = Module._resolveFilename;
Module._resolveFilename = function (req, ...a) { return req === "electron" ? "electron" : resolve.call(this, req, ...a); };
require.cache.electron = { id: "electron", filename: "electron", loaded: true, exports: electron };

const store = require(path.join(dist, "store.js"));
const secrets = require(path.join(dist, "secrets.js"));
const SETTINGS = path.join(userData, "app-settings.json"), SECRETS = path.join(userData, "secrets.json");
const bads = (base) => fs.readdirSync(userData).filter((f) => f.startsWith(base + ".") && f.endsWith(".bad"));

try {
  // secrets.json: keys save and read back
  secrets.setKey("ANTHROPIC_API_KEY", "sk-one");
  secrets.setKey("GROQ_API_KEY", "gsk-two");
  assert.deepStrictEqual(secrets.secretEnv(), { ANTHROPIC_API_KEY: "sk-one", GROQ_API_KEY: "gsk-two" });
  assert.strictEqual(secrets.secretsProblem(), null);
  // broken: setting a key refuses (it would have saved only that key), the file stays, a copy is kept
  const good = fs.readFileSync(SECRETS, "utf8");
  const broken = good.slice(0, -5);
  fs.writeFileSync(SECRETS, broken);
  assert.throws(() => secrets.setKey("GEMINI_API_KEY", "AIza-three"), /secrets\.json can't be read .*left as it is/);
  assert.strictEqual(fs.readFileSync(SECRETS, "utf8"), broken);
  assert.strictEqual(bads("secrets.json").length, 1);
  assert.strictEqual(fs.readFileSync(path.join(userData, bads("secrets.json")[0]), "utf8"), broken);
  assert.match(secrets.secretsProblem(), /secrets\.json can't be read/);
  assert.strictEqual(bads("secrets.json").length, 1, "one copy per content");
  assert.deepStrictEqual(secrets.secretEnv(), {});          // the coach starts without them, the log says why
  // fixed: works again, nothing lost
  fs.writeFileSync(SECRETS, good);
  secrets.setKey("GEMINI_API_KEY", "AIza-three");
  assert.deepStrictEqual(Object.keys(secrets.secretEnv()).sort(), ["ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"]);
  // a BOM is fine
  fs.writeFileSync(SECRETS, "﻿" + fs.readFileSync(SECRETS, "utf8"));
  assert.strictEqual(secrets.secretsProblem(), null);
  assert.strictEqual(secrets.secretEnv().GROQ_API_KEY, "gsk-two");

  // app-settings.json: missing is the defaults, and saves
  let s = store.loadAppSettings();
  assert.strictEqual(store.appSettingsProblem(), null);
  assert.strictEqual(s.closeToTray, false);
  s.closeToTray = true;
  assert.strictEqual(store.saveAppSettings(s), true);
  assert.strictEqual(store.loadAppSettings().closeToTray, true);
  // broken: the defaults for this run, nothing saved over it, a copy kept, a message
  const text = fs.readFileSync(SETTINGS, "utf8");
  fs.writeFileSync(SETTINGS, text + "}");
  s = store.loadAppSettings();
  assert.strictEqual(s.closeToTray, false);
  assert.match(store.appSettingsProblem(), /app-settings\.json can't be read .*restart Glide/);
  s.zoom = 2;
  assert.strictEqual(store.saveAppSettings(s), false);
  assert.strictEqual(fs.readFileSync(SETTINGS, "utf8"), text + "}");
  assert.strictEqual(bads("app-settings.json").length, 1);
  // the settings store keeps working for this run (window bounds, mute...) without writing
  const { settings } = require(path.join(dist, "app", "settings.js"));
  settings.load();
  settings.merge({ muted: true });
  assert.strictEqual(settings.current.muted, true);
  assert.strictEqual(fs.readFileSync(SETTINGS, "utf8"), text + "}");
  // fixed, next start: read and saved as usual
  fs.writeFileSync(SETTINGS, text);
  assert.strictEqual(store.loadAppSettings().closeToTray, true);
  assert.strictEqual(store.appSettingsProblem(), null);
  console.log("ok");
} finally {
  fs.rmSync(userData, { recursive: true, force: true });
}
