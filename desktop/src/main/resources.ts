// Glide's CPU and memory footprint while a long job runs (re-read, rebuild, first build, downloads): the app, the
// coach engine and every process they started (build_db, build_kb, extract_maps, pip), sampled every ~1.5 s by one
// PowerShell process that only runs while the footer asks for it.
import { ChildProcess, spawn } from "child_process";
import { EventEmitter } from "events";
import os from "os";
import type { Footprint } from "../shared/ipc";

const SCRIPT = `
param([int]$root, [int]$me)
$ErrorActionPreference = 'SilentlyContinue'
while ($true) {
  $all = Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, WorkingSetSize, KernelModeTime, UserModeTime
  # ids come back as UInt32; key by [int] so they match $root (a UInt32 key never equals an Int32 one)
  $kids = @{}
  foreach ($p in $all) { $pp = [int]$p.ParentProcessId; if (-not $kids.ContainsKey($pp)) { $kids[$pp] = @() }; $kids[$pp] += $p }
  $byId = @{}; foreach ($p in $all) { $byId[[int]$p.ProcessId] = $p }
  $mem = 0; $n = 0; $times = @(); $queue = New-Object System.Collections.Queue
  if ($byId.ContainsKey($root)) { $queue.Enqueue($byId[$root]) }
  while ($queue.Count) {
    $p = $queue.Dequeue(); $id = [int]$p.ProcessId
    if ($id -ne $me) { $mem += $p.WorkingSetSize; $times += "$($id):$($p.KernelModeTime + $p.UserModeTime)"; $n++ }
    if ($kids.ContainsKey($id)) { foreach ($c in $kids[$id]) { if ([int]$c.ProcessId -ne $id) { $queue.Enqueue($c) } } }
  }
  [Console]::Out.WriteLine("$mem $n $($times -join ' ')")
  Start-Sleep -Milliseconds 1500
}`;


interface Reading { mem: number; processes: number; times: Map<number, number> }

/** One line of the sampler's output: the total working set, the process count and each process's "id:cpu time". */
function parseReading(line: string): Reading | null {
  const [memS, nS, ...pairs] = line.split(/\s+/);
  const mem = Number(memS), processes = Number(nS);
  if (!Number.isFinite(mem)) return null;
  const times = new Map<number, number>();
  for (const pair of pairs) {
    const [id, t] = pair.split(":").map(Number);
    if (Number.isFinite(id) && Number.isFinite(t)) times.set(id, t);
  }
  return { mem, processes, times };
}

export class ResourceSampler extends EventEmitter {
  private proc: ChildProcess | null = null;
  private last: { times: Map<number, number>; t: number } | null = null;

  get running(): boolean {
    return this.proc !== null;
  }

  start(): void {
    if (this.proc || process.platform !== "win32") return;
    this.last = null;
    const p = spawn("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
      `& {${SCRIPT}} -root ${process.pid} -me $PID`], { windowsHide: true, stdio: ["ignore", "pipe", "ignore"] });
    this.proc = p;
    let buf = "";
    p.stdout?.on("data", (d: Buffer) => {
      buf += d.toString("utf8");
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        const r = parseReading(line);
        if (r) this.take(r);
      }
    });
    p.on("exit", () => (this.proc = null));
  }

  /** Turn a reading into a sample: the first one only sets the baseline for the CPU share. */
  private take({ mem, processes, times }: Reading): void {
    const now = Date.now();
    // CPU time per process, in 100 ns units. Sum each process's own growth so a build step that exits doesn't
    // take its past time with it; one started since the last sample counts all of its time. % of the whole machine.
    let pct: number | null = null;
    if (this.last) {
      let used = 0;
      for (const [id, t] of times) used += Math.max(0, t - (this.last.times.get(id) ?? 0));
      pct = Math.max(0, Math.min(100, (used / 1e4 / (now - this.last.t)) * 100 / os.cpus().length));
    }
    this.last = { times, t: now };
    if (pct != null) this.emit("sample", { cpu: Math.round(pct * 10) / 10, memMB: Math.round(mem / 1048576), processes } as Footprint);
  }

  stop(): void {
    if (!this.proc) return;
    try {
      this.proc.kill();
    } catch {
      /* already gone */
    }
    this.proc = null;
  }
}
