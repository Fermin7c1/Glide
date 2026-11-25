// Browser half of tests/test_coach_link.py: the served coach page in headless Edge, on its own and in a frame.
// node coach_link_cdp.mjs <edge.exe> <profile dir> <url> <parent url> <messages expected> [screenshot folder]
// Prints one JSON line: what each message shows, what a click on "Open in WoWSims" does on its own (the browser opens
// it) and in a frame of the parent page (another origin, as the app's window is: the page asks its parent, which gets
// {glide: "open", url}), and the page's script errors.
import { spawn } from "child_process";
import fs from "fs";
import path from "path";

const [edge, prof, url, parent, expected, shots] = process.argv.slice(2);
// port 0: Edge picks a free one and writes it to DevToolsActivePort (no fixed port shared with other runs)
const p = spawn(edge, ["--headless", "--disable-gpu", "--no-first-run", "--hide-scrollbars", "--force-prefers-reduced-motion",
  `--user-data-dir=${prof}`, "--remote-debugging-port=0", "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let ws;
try {
  for (let i = 0; i < 400 && !ws; i++) {                    // up to 20 s for the browser to come up
    await sleep(50);
    try {
      const port = fs.readFileSync(path.join(prof, "DevToolsActivePort"), "utf8").split("\n")[0].trim();
      const t = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((x) => x.type === "page");
      if (t) ws = new WebSocket(t.webSocketDebuggerUrl);
    } catch { /* not up yet */ }
  }
  await new Promise((r) => ws.addEventListener("open", r));
  let id = 0; const wait = {}; const errors = [];
  // a frame from another site runs in its own process: its target attaches (flattened: its own session id)
  let frameSession = null;
  ws.addEventListener("message", (m) => {
    const d = JSON.parse(m.data);
    if (d.id && wait[d.id]) { wait[d.id](d.error ? { error: d.error } : d.result); delete wait[d.id]; }
    if (d.method === "Runtime.exceptionThrown") errors.push(d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text);
    if (d.method === "Target.attachedToTarget" && d.params.targetInfo.type === "iframe") frameSession = d.params.sessionId;
  });
  const send = (method, params = {}, sessionId) => new Promise((r) => { const n = ++id; wait[n] = r;
    ws.send(JSON.stringify({ id: n, method, params, ...(sessionId ? { sessionId } : {}) })); });
  // where: {} (the page), { contextId } (a world in it), or { sessionId } (a frame in its own process)
  const js = async (e, where = {}) => (await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true,
    ...(where.contextId ? { contextId: where.contextId } : {}) }, where.sessionId)).result?.value;
  const until = async (expr, ms = 10000, where) => {
    for (const t0 = Date.now(); Date.now() - t0 < ms; await sleep(20)) if (await js(expr, where)) return true;
    return false;
  };
  const size = (w, h) => send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false });
  // the tabs open (by id; not the browser's own edge:// pages, which come and go by themselves)
  const pages = async () => (await send("Target.getTargets")).targetInfos
    .filter((t) => t.type === "page" && !/^(edge|chrome):/.test(t.url)).map((t) => t.targetId);
  await send("Page.enable"); await send("Runtime.enable");
  await send("Page.addScriptToEvaluateOnNewDocument", { source: "try{localStorage.clear()}catch(e){}" });

  // what each message in the chat shows: its text, its links to WoWSims, the credit beside them
  const shown = `[...document.querySelectorAll('#feed .msg')].map((m) => ({
    text: m.textContent,
    links: [...m.querySelectorAll('a.wowsims')].map((a) => { const r = a.getBoundingClientRect(), c = a.nextElementSibling;
      const cr = c && c.getBoundingClientRect();
      return { href: a.getAttribute('href'), text: a.textContent, target: a.target, rel: a.rel,
               credit: c && c.className === 'credit' ? c.textContent : null,
               beside: !!cr && Math.abs((r.top + r.bottom) / 2 - (cr.top + cr.bottom) / 2) < r.height / 2 && cr.left >= r.right,
               color: getComputedStyle(a).color, fontSize: getComputedStyle(a).fontSize,
               creditSize: c ? getComputedStyle(c).fontSize : null }; }),
    credits: m.querySelectorAll('.credit').length,
    anchors: m.querySelectorAll('a').length }))`;
  const out = { errors };

  // on its own (a browser): at 2560x1440 first
  await size(2560, 1440);
  await send("Page.navigate", { url });
  await until(`document.querySelectorAll('#feed .msg').length >= ${Number(expected)}`, 15000);
  out.top = await js(shown);
  out.overflow = await js("(() => { const f = document.getElementById('feed'); return f.scrollWidth - f.clientWidth; })()");
  if (shots) {
    await js("(() => { const f = document.getElementById('feed'); f.scrollTop = f.scrollHeight; return true; })()");
    for (const [w, h] of [[2560, 1440], [1280, 720]]) {
      await size(w, h);
      await sleep(400);
      const clip = await js("(() => { const r = document.getElementById('feed').getBoundingClientRect(); return { x: r.left, y: r.top, width: r.width, height: r.height, scale: 1 }; })()");
      const shot = await send("Page.captureScreenshot", { format: "png", clip });
      fs.writeFileSync(path.join(shots, `feed-${w}x${h}.png`), Buffer.from(shot.data, "base64"));
    }
    await size(2560, 1440);
  }
  // a click: the page leaves it to the browser (a new tab; held back here, so none opens) and says nothing to a parent
  const before = await pages();
  out.topClick = await js(`new Promise((r) => {
    addEventListener('click', (e) => { r({ prevented: e.defaultPrevented, at: location.href }); e.preventDefault(); }, { once: true });
    document.querySelector('#feed a.wowsims').click(); })`);
  out.topClick.at = out.topClick.at === url;

  // in a frame (the app's Coach page): a click (and a middle click) asks the parent, and opens nothing itself
  await send("Page.navigate", { url: parent });
  await until(`document.readyState === 'complete' && location.href === ${JSON.stringify(parent)}`);
  await send("Target.setAutoAttach", { autoAttach: true, waitForDebuggerOnStart: false, flatten: true });
  await js(`new Promise((r) => {
    window.__got = [];
    addEventListener('message', (e) => __got.push({ data: e.data, fromFrame: e.source === document.querySelector('iframe').contentWindow }));
    const f = document.createElement('iframe');
    f.style.cssText = 'width: 1600px; height: 1000px; border: 0';
    f.onload = () => r(true);
    f.src = ${JSON.stringify(url)};
    document.body.appendChild(f); })`);
  for (let i = 0; i < 100 && !frameSession; i++) await sleep(20);
  let ctx;
  if (frameSession) {
    ctx = { sessionId: frameSession };
    await send("Runtime.enable", {}, frameSession);
  } else {                                                  // the frame shares the page's process
    const frameId = (await send("Page.getFrameTree")).frameTree.childFrames?.[0]?.frame.id;
    ctx = { contextId: (await send("Page.createIsolatedWorld", { frameId, worldName: "probe" })).executionContextId };
  }
  await until(`document.querySelectorAll('#feed .msg').length >= ${Number(expected)}`, 15000, ctx);
  out.frame = { process: frameSession ? "own" : "shared", at: await js("location.href + ' ' + document.readyState", ctx) };
  out.framed = await js(shown, ctx);
  const pagesBefore = await pages();
  out.frameClick = await js(`new Promise((r) => {
    addEventListener('click', (e) => r({ prevented: e.defaultPrevented, at: location.href }), { once: true });
    document.querySelector('#feed a.wowsims').click(); })`, ctx);
  out.frameClick.at = out.frameClick.at === url;
  out.frameAux = await js(`(() => { const e = new MouseEvent('auxclick', { button: 1, bubbles: true, cancelable: true });
    document.querySelector('#feed a.wowsims').dispatchEvent(e); return { prevented: e.defaultPrevented }; })()`, ctx);
  await until("__got.filter((g) => g.data?.glide === 'open').length >= 2", 3000);
  out.got = await js("__got.filter((g) => g.data?.glide !== 'voice')");        // (the page asks for the voice too)
  const after = await pages();
  out.newPages = { top: after.filter((t) => !before.includes(t)).length, frame: after.filter((t) => !pagesBefore.includes(t)).length };
  console.log(JSON.stringify(out));
} finally {
  ws?.close();
  p.kill();
}
