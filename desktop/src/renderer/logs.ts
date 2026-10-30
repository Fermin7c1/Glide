// The Logs page: the coach's output and the app's own lines, filtered as you type. The Coach page shows the last
// few under its Start button too.
import { call, glide } from "./bridge.js";
import { $, esc, toast } from "./dom.js";
import { currentPage } from "./nav.js";
import { LogLine } from "./types.js";

const KEEP = 3000;
let logLines: LogLine[] = [];
let logTimer: number | null = null;

function lineHtml(l: LogLine): string {
  const ts = new Date(l.t).toLocaleTimeString();
  return `<span class="${l.stream}"><span class="ts">${esc(ts)}</span>  ${esc(l.text)}</span>\n`;
}

/** Draw the Logs page (when it shows, or `force`) and the Coach page's tail. */
export function renderLog(force = false): void {
  const q = ($<HTMLInputElement>("log-filter").value || "").toLowerCase();
  const lines = q ? logLines.filter((l) => l.text.toLowerCase().includes(q)) : logLines;
  if (currentPage() === "logs" || force) {
    $("log").innerHTML = lines.slice(-1500).map(lineHtml).join("");
    if ($<HTMLInputElement>("log-follow").checked) $("page-logs").scrollTop = 1e9;
  }
  $("empty-log").innerHTML = logLines.slice(-40).map(lineHtml).join("");
  $("empty-log").scrollTop = 1e9;
}

/** The log so far (at start; new lines arrive as events). */
export function loadLog(lines: LogLine[]): void {
  logLines = lines;
  renderLog();
}

export function wireLogs(): void {
  glide.on("engine:logline", (l: LogLine) => {
    logLines.push(l);
    if (logLines.length > KEEP) logLines = logLines.slice(-KEEP);
    if (logTimer == null) logTimer = window.setTimeout(() => { logTimer = null; renderLog(); }, 150);
  });
  $("log-filter").oninput = () => renderLog();
  $("log-copy").onclick = () => {
    navigator.clipboard.writeText(logLines.map((l) => `${new Date(l.t).toISOString()} [${l.stream}] ${l.text}`).join("\n"));
    toast("Log copied");
  };
  $("log-folder").onclick = async () => {
    const info = await call("app:info");
    if (info) call("shell:open", info.home + "\\data\\live");
  };
  $("log-files").onclick = async () => {
    const problem = await call("app:logs");
    if (problem) toast(`Couldn't open the log folder: ${problem}`, true);
  };
}
