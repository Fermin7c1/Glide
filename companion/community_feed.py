"""The community feed: the community scan's notes, fetched from feed/ in the public Fermin7c1/Glide-releases repo so
every player gets new notes without an app update. The engine only reads the feed; it never writes it.

    <feed_url>manifest.json   {"format": 1, "version", "file", "sha256", "bytes", "content", "notes"}
    <feed_url><file>          gzip of {"format": 1, "version", "notes": [note, ...], "sources": [source, ...]}

format: 1 (anything else is refused). file: the feed file's name, a plain name in the same folder
(community-<first 16 hex of sha256>.json.gz). sha256: the hex SHA-256 of that file's bytes, which the download must
match. version: when it was made (ISO time, shown on the coach screen). notes: one object per note in the
knowledge/community/README.md note format (every note ever written, a later version of a note replacing the earlier
one); sources: sources.json's "sources". bytes, content (a hash of the notes and sources alone) and notes in the
manifest are informational.

Every community.feed_minutes the engine reads the manifest. A feed it hasn't got (another sha256) is downloaded,
checked against the hash, kept as data/live/community_feed.json.gz, and its notes are written into kb.db's community
tables (the rows knowledge/sources/community.py builds), so the coach, its tools and the Community panel have them at
once. A kb.db the background refresh rebuilt gets them again when it's swapped in (apply). Notes this install
didn't have make the feed "fresh" until the coach screen says the player has seen them (POST /community/seen): the
screen celebrates their landing.

state() is the coach screen's community_feed: {"version", "notes", "checked", "fresh": {"count", "at", "titles"} or
None, "error"}.
"""
import hashlib
import json
import os
import sqlite3
import threading
import time
import urllib.request
import zlib

import jsonfile
from log import log, log_once

FORMAT = 1
MAX_BYTES = 8 * 1024 * 1024          # a feed bigger than this is refused (it is ~0.1 MB today)
MAX_JSON = 64 * 1024 * 1024          # ... and one that inflates past this (a gzip bomb)
TEXT_MAX = {"id": 120, "title": 300, "claim": 1000, "detail": 6000, "kind": 20, "status": 20, "seen": 20,
            "published": 20}         # note fields, as text of at most this many characters
TIMEOUT_S = 20
FIRST_CHECK_S = 20                   # the first check waits this long after the engine starts


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    """Redirects stay on https (a feed is never fetched over http, a file: URL or anything else)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise ValueError("the feed redirected to a non-https address")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_HttpsOnly)


def _get(url, limit):
    if not url.lower().startswith("https://"):
        raise ValueError("the community feed is fetched over https only (%s)" % url[:80])
    req = urllib.request.Request(url, headers={"User-Agent": "Glide-coach", "Cache-Control": "no-cache"})
    with _opener.open(req, timeout=TIMEOUT_S) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("%s is larger than %d bytes" % (url, limit))
    return data


class Feed:
    """The feed for one engine. kb_path: a function answering the kb.db in use; live_dir: where the feed is kept;
    on_landed(count, titles): new notes arrived (the app's chat line)."""

    def __init__(self, kb_path, live_dir, url, minutes, on_landed=None, fetch=_get):
        self.kb_path, self.url, self.minutes = kb_path, url.rstrip("/") + "/", minutes
        self.file = os.path.join(live_dir, "community_feed.json.gz")
        self.state_file = os.path.join(live_dir, "community_feed.json")
        self.on_landed, self.fetch = on_landed, fetch
        self.lock = threading.Lock()
        try:
            self.st = jsonfile.read(self.state_file)
        except FileNotFoundError:
            self.st = {}
        except (OSError, ValueError) as e:              # a damaged state: start over (the feed is fetched again)
            log_once("community_feed.state", e)
            self.st = {}
        self.error = None

    # ---- what the coach screen shows ------------------------------------------------------------------------------
    def state(self):
        with self.lock:
            return {"version": self.st.get("version"), "notes": self.st.get("notes"), "checked": self.st.get("checked"),
                    "fresh": self.st.get("fresh"), "error": self.error}

    def seen(self):
        """The player has seen the new notes (the coach screen opened Community)."""
        with self.lock:
            if self.st.get("fresh"):
                self.st["fresh"] = None
                self._save()
        return self.state()

    # ---- the check ---------------------------------------------------------------------------------------------------
    def start(self):
        threading.Thread(target=self._loop, name="community-feed", daemon=True).start()

    def _loop(self):
        time.sleep(FIRST_CHECK_S)
        while True:
            try:
                self.check()
            except Exception as e:  # noqa: BLE001 - offline, GitHub down, a bad feed: try again next time
                self.error = str(e)[:200]
                log_once("community_feed.check", e)
            time.sleep(max(5, self.minutes()) * 60)

    def check(self):
        """Read the manifest; download, verify and apply a feed this install hasn't got. Returns what happened."""
        m = json.loads(self.fetch(self.url + "manifest.json", 64 * 1024))
        if not isinstance(m, dict) or m.get("format") != FORMAT or not m.get("file") or not m.get("sha256"):
            raise ValueError("the feed's manifest isn't one this engine reads (format %s)" % m.get("format"))
        with self.lock:
            self.st["checked"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            have = self.st.get("sha256")
        self.error = None
        if have == m["sha256"] and os.path.exists(self.file):
            self._save()
            return "current"
        if "/" in m["file"] or "\\" in m["file"] or ".." in m["file"]:
            raise ValueError("bad feed file name %r" % m["file"])
        data = self.fetch(self.url + m["file"], MAX_BYTES)
        if hashlib.sha256(data).hexdigest() != m["sha256"]:
            raise ValueError("the downloaded feed doesn't match the manifest's hash")
        feed = self._parse(data)
        tmp = self.file + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, self.file)
        new = self.apply(feed)
        with self.lock:
            self.st.update(sha256=m["sha256"], version=feed.get("version"), notes=len(feed["notes"]))
            if new:
                old = (self.st.get("fresh") or {}).get("ids") or []
                ids = old + [n["id"] for n in new if n["id"] not in old]
                titles = [n.get("title") or n.get("claim") for n in new][:3]
                self.st["fresh"] = {"count": len(ids), "ids": ids[-200:], "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                    "titles": titles}
            self._save()
        log("community", "feed %s: %d notes, %d new" % (feed.get("version"), len(feed["notes"]), len(new)))
        if new and self.on_landed:
            self.on_landed(len(new), [n.get("title") or n.get("claim") for n in new])
        return "new" if new else "updated"

    @staticmethod
    def _parse(data):
        inflate = zlib.decompressobj(16 + zlib.MAX_WBITS)            # gzip, inflated at most MAX_JSON bytes
        raw = inflate.decompress(data, MAX_JSON)
        if inflate.unconsumed_tail or not inflate.eof:
            raise ValueError("the feed inflates to more than %d MB, or is cut short" % (MAX_JSON // 2 ** 20))
        feed = json.loads(raw)
        if not isinstance(feed, dict) or feed.get("format") != FORMAT or not isinstance(feed.get("notes"), list):
            raise ValueError("the feed isn't one this engine reads")
        feed["version"] = str(feed.get("version") or "")[:40]
        feed["notes"] = [Feed._clean(n) for n in feed["notes"] if isinstance(n, dict)]
        feed["notes"] = [n for n in feed["notes"] if n.get("id") and (n.get("claim") or n.get("title"))]
        feed["sources"] = [s for s in feed.get("sources") or [] if isinstance(s, dict) and s.get("id")]
        return feed

    @staticmethod
    def _clean(n):
        """A note with its text fields as plain text of a sane length (a feed's other shapes never reach kb.db)."""
        out = dict(n)
        for k, limit in TEXT_MAX.items():
            v = out.get(k)
            out[k] = None if v is None or isinstance(v, (dict, list)) else str(v)[:limit]
        return out

    # ---- into kb.db ------------------------------------------------------------------------------------------------
    def apply(self, feed=None):
        """Write the feed's notes into the kb.db in use (the kept feed when none is given). Returns the notes that kb.db
        didn't have, superseded and contradicted ones left out."""
        if feed is None:
            if not os.path.exists(self.file):
                return []
            try:
                with open(self.file, "rb") as fh:
                    feed = self._parse(fh.read())
            except (OSError, ValueError) as e:
                log_once("community_feed.kept", e)
                return []
        path = self.kb_path()
        if not path or not os.path.exists(path):
            return []
        from knowledge.sources import community
        notes = {str(n["id"]): n for n in feed["notes"]}
        rows, _ = community.note_rows(notes)
        conn = sqlite3.connect(path, timeout=15)
        try:
            have = {r[0] for r in conn.execute("SELECT id FROM community_note")}
            with conn:
                community.write(conn, rows, community.source_rows(feed["sources"]))
        finally:
            conn.close()
        return [notes[r["id"]] for r in rows if r["id"] not in have and r["status"] not in ("superseded", "contradicted")]

    def _save(self):
        jsonfile.write_atomic(self.state_file, self.st)
