"""XP per hour over time, for the dashboard's chart: a sample every 20 s while XP is coming in, the last two hours
kept, saved to data/live/xph_history.json so the chart (and hovering over it) survives a coach restart."""
import json
import os
import threading
import time

import jsonfile

EVERY_S = 20          # one sample per this many seconds
KEEP_S = 2 * 3600     # history kept
SAVE_S = 60           # written to disk at most this often


class XphHistory:
    def __init__(self, path=None):
        self.path, self.lock = path, threading.Lock()
        self.samples, self._saved = [], 0.0          # [[epoch seconds, xp per hour], ...] oldest first
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    rows = json.load(fh).get("samples") or []
                self.samples = [[float(t), int(v)] for t, v in rows if v and v > 0]
            except (OSError, ValueError, TypeError):
                self.samples = []
        self._prune(time.time())

    def _prune(self, now):
        cut = now - KEEP_S
        while self.samples and self.samples[0][0] < cut:
            self.samples.pop(0)

    def add(self, xph, now=None):
        """Record the current XP per hour (ignored when there is none, or a sample is less than EVERY_S old)."""
        if not isinstance(xph, (int, float)) or xph <= 0:
            return
        now = time.time() if now is None else now
        with self.lock:
            if self.samples and now - self.samples[-1][0] < EVERY_S:
                return
            self.samples.append([now, int(xph)])
            self._prune(now)
            if self.path and now - self._saved >= SAVE_S:
                self._saved = now
                self._save()

    def _save(self):
        try:
            jsonfile.write_atomic(self.path, {"samples": [[round(t, 1), v] for t, v in self.samples]})
        except OSError:
            pass

    def snapshot(self):
        """{"samples": [[epoch milliseconds, xp per hour], ...]} for the dashboard."""
        with self.lock:
            return {"samples": [[int(t * 1000), v] for t, v in self.samples]}
