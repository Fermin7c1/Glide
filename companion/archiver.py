"""Background archiver (replaces the separate archive.ps1 window): every 20 s, copies the game's
Glide (and old GlideRecorder) / RXPGuides SavedVariables and Cache\\WDB into data\\archive whenever they change.
Same rules as tools\\archive.ps1: every distinct SavedVariables version is kept; WDB goes into one
folder per day, and a smaller file than the day's copy (cache wiped by a patch) starts a new folder."""
import glob
import hashlib
import os
import shutil
import threading
import time


def _hash(path):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Archiver:
    def __init__(self, wow_dir, root, every=20, log=print):
        self.wow, self.every, self.log = wow_dir, every, log
        self.sv_out = os.path.join(root, "data", "archive", "sv")
        self.wdb_out = os.path.join(root, "data", "archive", "wdb")
        self.seen = {}

    def start(self):
        if not self.wow or not os.path.isdir(self.wow):
            self.log("[archiver] WoW folder not found (%s); archiving off" % self.wow)
            return
        os.makedirs(self.sv_out, exist_ok=True)
        os.makedirs(self.wdb_out, exist_ok=True)
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                self.once()
            except Exception as e:  # never let archiving take the companion down
                self.log("[archiver] %s" % e)
            time.sleep(self.every)

    def _changed(self, path):
        h = _hash(path)
        if self.seen.get(path) == h:
            return False
        self.seen[path] = h
        return True

    def once(self):
        pattern = os.path.join(self.wow, "WTF", "Account", "*", "SavedVariables")
        for d in glob.glob(pattern):
            acct = "".join(c if c.isalnum() else "_" for c in os.path.basename(os.path.dirname(d)))
            for name in ("Glide.lua", "GlideRecorder.lua", "RXPGuides.lua"):
                src = os.path.join(d, name)
                if not os.path.isfile(src) or not self._changed(src):
                    continue
                stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(os.path.getmtime(src)))
                dest = os.path.join(self.sv_out, "%s_%s_%s.lua" % (name[:-4], acct, stamp))
                if not os.path.exists(dest):
                    shutil.copy2(src, dest)
                    self.log("[archiver] saved %s" % os.path.basename(dest))
        for src in glob.glob(os.path.join(self.wow, "Cache", "WDB", "*", "*.wdb")):
            if not self._changed(src):
                continue
            day = os.path.join(self.wdb_out, time.strftime("%Y%m%d"))
            dest = os.path.join(day, os.path.basename(src))
            if os.path.exists(dest) and os.path.getsize(dest) > os.path.getsize(src):
                day = os.path.join(self.wdb_out, time.strftime("%Y%m%d-%H%M%S"))
                dest = os.path.join(day, os.path.basename(src))
            os.makedirs(day, exist_ok=True)
            shutil.copy2(src, dest)
