// The Guides page's game icons (glide://icon/<fileDataID>.png): images the engine decoded from the player's own WoW
// client into data/gamedata/icons/ (wowfiles/target_icons.py). The refresh decodes the creature family, creature type
// and role icons ahead; an item or spell icon is decoded the first time a guide shows it: the misses of one moment are
// batched into a single `rxp_import.py icons` run (its own short process, so it works with the coach stopped), and
// every request waits for the batch that answers it. An icon that can't be had answers null: the page keeps its glyph.
// No Electron here, so tests/test_game_icons.py runs it under Node.
import fs from "fs";
import path from "path";

export const ICON_BATCH = 400;            // icons per decode run (rxp_import.py icons decodes at most this many)
const BATCH_DELAY_MS = 40;                // misses arriving this close together share one run
const BROKEN_MS = 5 * 60 * 1000;          // after a failed run (no client, no Python), misses answer null this long

/** fileDataID -> the icons it decoded (rejects when the run itself failed). */
export type IconDecoder = (fdids: number[]) => Promise<number[]>;

export class GameIcons {
  private failed = new Set<number>();
  private waiting = new Map<number, ((p: string | null) => void)[]>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private running = false;
  private brokenUntil = 0;
  runs = 0;

  /** dirs: the folders holding data/gamedata/icons (the engine home first); decode: one helper run. */
  constructor(private dirs: () => string[], private decode: IconDecoder, private now = () => Date.now(),
    private delayMs = BATCH_DELAY_MS) {}

  /** The cached PNG of this icon, or null. */
  find(fdid: number): string | null {
    for (const d of this.dirs()) {
      const p = path.join(d, "data", "gamedata", "icons", `${fdid}.png`);
      if (fs.existsSync(p)) return p;
    }
    return null;
  }

  /** The PNG of this icon, decoding it first when it isn't cached yet; null when it can't be had. */
  get(fdid: number): Promise<string | null> {
    if (!Number.isInteger(fdid) || fdid <= 0 || fdid >= 2 ** 31) return Promise.resolve(null);
    const p = this.find(fdid);
    if (p) return Promise.resolve(p);
    if (this.failed.has(fdid) || this.now() < this.brokenUntil) return Promise.resolve(null);
    return new Promise((resolve) => {
      const w = this.waiting.get(fdid);
      if (w) w.push(resolve);
      else this.waiting.set(fdid, [resolve]);
      this.schedule();
    });
  }

  private schedule(): void {
    if (this.timer || this.running) return;
    this.timer = setTimeout(() => { this.timer = null; void this.flush(); }, this.delayMs);
  }

  private async flush(): Promise<void> {
    const ids = [...this.waiting.keys()].slice(0, ICON_BATCH);
    if (!ids.length) return;
    this.running = true;
    this.runs++;
    let ready: Set<number> | null = null;
    try {
      ready = new Set(await this.decode(ids));
    } catch {
      this.brokenUntil = this.now() + BROKEN_MS;
    }
    for (const id of ids) {
      const p = this.find(id);
      if (!p && ready) this.failed.add(id);
      for (const r of this.waiting.get(id) || []) r(p);
      this.waiting.delete(id);
    }
    this.running = false;
    if (this.waiting.size) {
      if (this.now() < this.brokenUntil) {
        for (const [id, rs] of this.waiting) { rs.forEach((r) => r(this.find(id))); }
        this.waiting.clear();
      } else this.schedule();
    }
  }
}

/** glide://icon/<fileDataID>.png -> the id, else null. */
export function iconId(host: string, pathname: string): number | null {
  const m = host === "icon" ? /^\/(\d{1,10})\.png$/.exec(pathname) : null;
  return m ? Number(m[1]) : null;
}
