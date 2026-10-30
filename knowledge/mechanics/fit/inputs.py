"""The fit's inputs: where they are, the companion live logs (Live) and the community seed."""
import bisect
import collections
import glob
import gzip
import json
import os

from knowledge.mechanics import params as P
from knowledge.mechanics.fit.common import as_int


def input_paths(conn, live_dir, seed_path, out_dir):
    """Where the inputs are: data/ next to the glide.db the connection reads (else the repo's), unless given."""
    db = None
    try:
        for row in conn.execute("PRAGMA database_list"):
            if row[1] == "main" and row[2]:
                db = row[2]
    except Exception:  # noqa: BLE001 (any driver error: fall back to the repo layout)
        db = None
    data = os.path.dirname(db) if db else os.path.join(P.ROOT, "data")
    return {"db": db, "data": data,
            "live": live_dir or os.path.join(data, "live"),
            "seed": seed_path or os.path.join(data, "seed", "seed.json.gz"),
            "out": out_dir or data}


class Live:
    """Companion live logs: core frames, aura/target/map pages, event ring (non-simulated files only)."""

    def __init__(self, live_dir):
        self.files, self.sim_files = [], []
        self.frames = []          # (t, file_idx, core dict)
        self.apages = []          # (t, page index, [(spell, name, exp, dur)])
        self.tclass = collections.defaultdict(collections.Counter)   # target name -> classification
        self.maps = {}            # ui_map -> (w, h)
        self.events = []          # (t, kind, arg, core)
        self.ft, self.at, self.epochs = [], [], []
        if not live_dir or not os.path.isdir(live_dir):
            return
        for path in sorted(glob.glob(os.path.join(live_dir, "*.jsonl"))):
            self._load(path)
        self.frames.sort(key=lambda f: f[0])
        self.apages.sort(key=lambda a: a[0])
        self.ft = [f[0] for f in self.frames]
        self.at = [a[0] for a in self.apages]
        # aura epochs: a new one on another log file, class change, level drop, a >5 min gap (relog) or a
        # death (dying removes buffs such as Well Fed even though their expiry is later)
        prev = None
        for t, f, c in self.frames:
            lv, cl = as_int(c.get("l")), c.get("cl") or None
            if (prev is None or f != prev[1] or (cl and prev[3] and cl != prev[3]) or (lv and prev[2] and lv < prev[2])
                    or t - prev[0] > 300 or (c.get("d") in ("1", "2") and prev[4] == "0")):
                self.epochs.append(t)
            prev = (t, f, lv or (prev[2] if prev else None), cl or (prev[3] if prev else None), c.get("d"))

    def _load(self, path):
        frames, apages, tcl, maps = [], [], [], {}
        sim = False
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            return
        with fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                t = d.get("t")
                c = d.get("core")
                if c:
                    if c.get("l") == "1" and c.get("r") == "1200":
                        sim = True
                    frames.append((t, c))
                    continue
                pg = d.get("page")
                if not pg:
                    continue
                kind = str(pg.get("pg", ""))
                if kind.startswith("A"):
                    auras = []
                    for part in str(pg.get("a") or "").split(","):
                        b = part.split(":")
                        if len(b) >= 5:
                            auras.append((b[0], ":".join(b[4:]), as_int(b[1]) or 0, as_int(b[2]) or 0))
                    apages.append((t, as_int(kind[1:]) or 0, auras))
                elif kind == "T" and pg.get("tn"):
                    tcl.append((pg.get("tn"), pg.get("tc")))
                elif kind == "Z" and as_int(pg.get("mm")) and as_int(pg.get("mw")):
                    maps[as_int(pg.get("mm"))] = (float(pg.get("mw")), float(pg.get("mh")))
        name = os.path.basename(path)
        if sim:
            self.sim_files.append(name)
            return
        if not frames:
            return
        idx = len(self.files)
        self.files.append(name)
        seen = set()
        for t, c in frames:
            self.frames.append((t, idx, c))
            for k in ("e1", "e2", "e3"):
                e = c.get(k)
                if e and e not in seen:
                    seen.add(e)
                    parts = e.split(":", 2)
                    if len(parts) == 3:
                        self.events.append((t, parts[1], parts[2], c))
        self.apages.extend(apages)
        for n, tc in tcl:
            self.tclass[n][tc] += 1
        self.maps.update(maps)

    def ok(self):
        return bool(self.frames)

    def window(self, t0, t1):
        i = bisect.bisect_left(self.ft, t0)
        j = bisect.bisect_right(self.ft, t1)
        return range(i, j)

    def _frame_at(self, t):
        i = bisect.bisect_right(self.ft, t) - 1
        return self.frames[i][2] if i >= 0 else {}

    def auras_at(self, t, lookback=3600):
        """{spell: name} active at time t: the latest non-empty version of each aura page (A0..A[an-1])
        published before t in the current aura epoch, minus entries whose expiry has passed. In combat the
        addon publishes an empty page with hash 0 (= unknown), so the list used is the one from before the
        fight; `an` comes from the latest frame whose aura hash is known."""
        e = bisect.bisect_right(self.epochs, t) - 1
        start = max(self.epochs[e] if e >= 0 else 0, t - lookback)
        npages = None
        i = bisect.bisect_right(self.ft, t) - 1
        while i >= 0 and self.frames[i][0] >= start:
            c = self.frames[i][2]
            if c.get("ah") not in (None, "", "0"):
                npages = as_int(c.get("an"))
                break
            i -= 1
        i = bisect.bisect_right(self.at, t) - 1
        latest = {}
        while i >= 0 and self.apages[i][0] >= start:
            pt, idx, auras = self.apages[i]
            if auras and idx not in latest and (npages is None or idx < max(1, npages)):
                latest[idx] = auras
            i -= 1
        tm = int(t) % 1000000
        out = {}
        for auras in latest.values():
            for spell, name, exp, dur in auras:
                if exp and dur:
                    x = exp + 1000000 if exp < tm - 500000 else exp
                    if x <= tm or (x - dur) > tm + 2:
                        continue
                out[spell] = name
        return out

    def aura_names_between(self, t0, t1):
        """Every aura name on pages published in [t0, t1] (permanent auras included)."""
        i = bisect.bisect_left(self.at, t0)
        j = bisect.bisect_right(self.at, t1)
        return {name for _, _, auras in self.apages[i:j] for _, name, _, _ in auras}

    def covered(self, t, slack=10):
        """True if a live frame exists within `slack` seconds of t."""
        i = bisect.bisect_left(self.ft, t - slack)
        return i < len(self.ft) and self.ft[i] <= t + slack

    def classification(self, name):
        c = self.tclass.get(name)
        return c.most_common(1)[0][0] if c else None


def load_seed(path):
    """The community seed (seed.json.gz), or None when it is missing or unreadable."""
    if not path or not os.path.exists(path):
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None
