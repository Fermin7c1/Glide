#!/usr/bin/env python3
"""The community feed (companion/community_feed.py), with the network replaced:
- a feed and manifest in the format its docstring gives: a new feed is downloaded, checked against its hash, kept,
  and its notes written into kb.db's community tables; the notes kb.db didn't have mark the feed fresh;
- the same manifest again changes nothing; a download that doesn't match the hash is refused and kb.db untouched;
- a rebuilt kb.db (the background refresh) gets the kept feed's notes again, without marking them fresh;
- POST /community/seen's seen() clears fresh; a superseded note is never counted as new;
- a gzip bomb, a plain-http feed and oddly shaped note fields are refused or cleaned.

Run: python3 tests/test_community_feed.py   (prints OK)
"""
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
sys.path.insert(0, ROOT)
import community_feed  # noqa: E402
from knowledge.sources import community  # noqa: E402


def build(base):
    """(feed bytes, manifest) in community_feed.py's format, from a knowledge/community folder."""
    notes, bad = community.read_notes(base)
    assert not bad
    with open(os.path.join(base, "sources.json"), encoding="utf-8") as fh:
        sources = json.load(fh)["sources"]
    body = {"notes": [notes[k] for k in sorted(notes)], "sources": sources}
    content = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    data = gzip.compress(json.dumps({"format": 1, "version": "2026-10-29T22:00:00Z", **body}).encode(), mtime=0)
    sha = hashlib.sha256(data).hexdigest()
    return data, {"format": 1, "version": "2026-10-29T22:00:00Z", "file": "community-%s.json.gz" % sha[:16],
                  "sha256": sha, "bytes": len(data), "content": content, "notes": len(body["notes"])}


def note(nid, title, **kw):
    return {"id": nid, "seen": "2026-10-29", "kind": "tip", "title": title, "claim": title + ".",
            "sources": [{"medium": "forum", "url": "https://example.org/" + nid}], **kw}


def kb(path, ids=()):
    """A kb.db with the community tables (knowledge/schema.sql's) holding notes `ids`."""
    c = sqlite3.connect(path)
    schema = open(os.path.join(ROOT, "knowledge", "schema.sql"), encoding="utf-8").read()
    for table in ("community_note", "community_source"):        # the build's own tables
        start = schema.index("CREATE TABLE IF NOT EXISTS %s(" % table)
        c.execute(schema[start:schema.index(";", start)])
    rows, _ = community.note_rows({i: note(i, "old " + i) for i in ids})
    community.write(c, rows, [])
    c.commit()
    c.close()


def ids(path):
    c = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in c.execute("SELECT id FROM community_note"))
    finally:
        c.close()


tmp = tempfile.mkdtemp()
try:
    # a feed made from a knowledge/community folder
    base = os.path.join(tmp, "community")
    os.makedirs(os.path.join(base, "intel"))
    with open(os.path.join(base, "intel", "2026-10-28.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(note("a", "Shipped note")) + "\n" + json.dumps(note("b", "First take")) + "\n")
    with open(os.path.join(base, "intel", "2026-10-29.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(note("b", "Corrected take")) + "\n" + json.dumps(note("c", "Brand new")) + "\n"
                 + json.dumps(note("d", "Replaces a", supersedes=["a"])) + "\n"
                 + json.dumps(note("e", "Already wrong", status="contradicted")) + "\n")
    with open(os.path.join(base, "sources.json"), "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"id": "forum", "kind": "forum", "url": "https://example.org"}]}, fh)
    data, manifest = build(base)
    assert manifest["notes"] == 5 and manifest["file"].startswith("community-") and manifest["bytes"] == len(data)
    again, m2 = build(base)
    assert m2["content"] == manifest["content"], "the same notes give the same content hash"
    body = json.loads(gzip.decompress(data))
    assert [n["id"] for n in body["notes"]] == ["a", "b", "c", "d", "e"] and body["notes"][1]["title"] == "Corrected take"

    served = {"manifest.json": json.dumps(manifest).encode(), manifest["file"]: data}
    fetched = []

    def fetch(url, limit):
        name = url.rsplit("/", 1)[1]
        fetched.append(name)
        return served[name]

    live = os.path.join(tmp, "live")
    os.makedirs(live)
    path = os.path.join(tmp, "kb.db")
    kb(path, ["a"])                                           # the install shipped with note a
    landed = []
    f = community_feed.Feed(lambda: path, live, "https://feed.example/feed", lambda: 30,
                            on_landed=lambda n, titles: landed.append((n, titles)), fetch=fetch)
    assert f.check() == "new"
    assert ids(path) == ["a", "b", "c", "d", "e"], ids(path)
    st = f.state()
    # new: b, c, d (a was there; e is contradicted, a is now superseded by d)
    assert st["fresh"]["count"] == 3 and st["notes"] == 5 and st["version"] == manifest["version"], st
    assert landed and landed[0][0] == 3, landed
    assert os.path.exists(os.path.join(live, "community_feed.json.gz"))
    c = sqlite3.connect(path)
    assert c.execute("SELECT status FROM community_note WHERE id='a'").fetchone()[0] == "superseded"
    assert c.execute("SELECT COUNT(*) FROM community_source").fetchone()[0] == 1
    c.close()

    # the same manifest: nothing downloaded, nothing new
    fetched.clear()
    assert f.check() == "current" and fetched == ["manifest.json"], fetched

    # a feed that doesn't match its hash is refused, kb.db untouched
    bad = dict(manifest, sha256="0" * 64, file="community-bad.json.gz")
    served.update({"manifest.json": json.dumps(bad).encode(), "community-bad.json.gz": gzip.compress(b"{}")})
    try:
        f.check()
        raise AssertionError("a mismatched feed was taken")
    except ValueError as e:
        assert "hash" in str(e), e
    assert ids(path) == ["a", "b", "c", "d", "e"]

    # a rebuilt kb.db (shipped notes only) gets the kept feed again, and nothing turns fresh again
    f.seen()
    assert f.state()["fresh"] is None
    os.remove(path)
    kb(path, ["a"])
    f.apply()
    assert ids(path) == ["a", "b", "c", "d", "e"] and f.state()["fresh"] is None

    # a new engine on the same live folder remembers the feed it has (no re-download of an unchanged feed)
    served["manifest.json"] = json.dumps(manifest).encode()
    fetched.clear()
    f2 = community_feed.Feed(lambda: path, live, "https://feed.example/feed/", lambda: 30, fetch=fetch)
    assert f2.check() == "current" and fetched == ["manifest.json"], fetched

    # a gzip bomb (a small download that inflates past the limit) is refused; so is a feed over plain http
    bomb = gzip.compress(b"[" + b" " * (community_feed.MAX_JSON + 10) + b"]", 9)
    try:
        community_feed.Feed._parse(bomb)
        raise AssertionError("a gzip bomb was inflated")
    except ValueError as e:
        assert "inflates" in str(e), e
    try:
        community_feed._get("http://example.org/feed/manifest.json", 100)
        raise AssertionError("an http feed was fetched")
    except ValueError as e:
        assert "https" in str(e), e
    # a note's odd shapes never reach kb.db: an object as a title, an endless claim
    odd = community_feed.Feed._clean({"id": "x", "title": {"a": 1}, "claim": "c" * 5000})
    assert odd["title"] is None and len(odd["claim"]) == 1000, odd

    # a manifest of another format is refused
    served["manifest.json"] = json.dumps(dict(manifest, format=99)).encode()
    try:
        f2.check()
        raise AssertionError("a format-99 manifest was taken")
    except ValueError:
        pass
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
