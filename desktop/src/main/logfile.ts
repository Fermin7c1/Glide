// The Logs page's lines on disk: everything the coach prints (its tracebacks too) and the app's own lines go to
// userData/logs/engine-YYYYMMDD.log, a file per day (local date), the newest KEEP_DAYS kept. The in-memory log the
// page shows is gone when the app closes; this is what is left to look at after a crash, or a problem reported later.
//
// Writes are queued and appended in batches off the UI thread (fs.promises); a failed write (disk full, folder
// removed) is counted and dropped, never thrown at the app. flushSync() writes what is still queued at quit.
import fs from "fs";
import path from "path";

export const KEEP_DAYS = 7;
const NAME = /^engine-\d{8}\.log$/;
const BATCH_MS = 250;
const MAX_QUEUED = 20_000;          // a disk that stalls can't grow the queue without bound

const pad = (n: number, w = 2) => String(n).padStart(w, "0");
const dayOf = (d: Date) => `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}`;
const timeOf = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`;

export interface LogFileOptions {
  keep?: number;                    // files kept (KEEP_DAYS)
  now?: () => Date;                 // the clock (tests)
}

export class LogFile {
  /** Lines that could not be written (and were dropped). */
  failures = 0;
  private queue: { day: string; text: string }[] = [];
  private timer: NodeJS.Timeout | null = null;
  private writing: Promise<void> | null = null;
  private pruned = "";              // the day the old files were last cleared for

  constructor(readonly dir: string, private opts: LogFileOptions = {}) {}

  private now(): Date {
    return this.opts.now ? this.opts.now() : new Date();
  }

  /** The file a day's lines go to. */
  fileFor(day: string): string {
    return path.join(this.dir, `engine-${day}.log`);
  }

  /** Queue one line (stamped with the time now). */
  write(text: string): void {
    const d = this.now();
    if (this.queue.length >= MAX_QUEUED) {
      this.queue.shift();
      this.failures++;
    }
    this.queue.push({ day: dayOf(d), text: `${timeOf(d)} ${text}\n` });
    if (!this.timer && !this.writing) this.timer = setTimeout(() => this.drain(), BATCH_MS);
  }

  /** A line that stands out: a coach start with what it ran (version, folders, Python), the coach ready. */
  header(title: string, ...parts: string[]): void {
    this.write(["==== " + title, ...parts].join(" | "));
  }

  /** Write everything queued so far (resolves when it is on disk or dropped). */
  async flush(): Promise<void> {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    while (this.writing || this.queue.length) await (this.writing ?? this.drain());
  }

  /** At quit: what is still queued, written before the process goes. */
  flushSync(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    for (const [day, text] of this.take()) {
      try {
        fs.mkdirSync(this.dir, { recursive: true });
        fs.appendFileSync(this.fileFor(day), text, "utf8");
      } catch {
        this.failures += text.split("\n").length - 1;
      }
    }
  }

  /** The queue as one text per day, emptied. */
  private take(): [string, string][] {
    const byDay = new Map<string, string>();
    for (const l of this.queue) byDay.set(l.day, (byDay.get(l.day) ?? "") + l.text);
    this.queue = [];
    return [...byDay];
  }

  private drain(): Promise<void> {
    this.timer = null;
    const batch = this.take();
    this.writing = (async () => {
      for (const [day, text] of batch) {
        try {
          await fs.promises.mkdir(this.dir, { recursive: true });
          if (this.pruned !== day) {
            this.pruned = day;
            await this.prune();
          }
          await fs.promises.appendFile(this.fileFor(day), text, "utf8");
        } catch {
          this.failures += text.split("\n").length - 1;
        }
      }
    })().finally(() => {
      this.writing = null;
      if (this.queue.length && !this.timer) this.timer = setTimeout(() => this.drain(), BATCH_MS);
    });
    return this.writing;
  }

  /** Keep the newest files (the names sort by date); the one being written to counts as one of them. */
  private async prune(): Promise<void> {
    const keep = this.opts.keep ?? KEEP_DAYS;
    const files = (await fs.promises.readdir(this.dir)).filter((f) => NAME.test(f));
    const current = path.basename(this.fileFor(this.pruned));
    if (!files.includes(current)) files.push(current);
    files.sort();
    for (const f of files.slice(0, Math.max(0, files.length - keep))) {
      try {
        await fs.promises.unlink(path.join(this.dir, f));
      } catch {
        // in use or gone already: the next day tries again
      }
    }
  }
}
