"""Community intelligence -> community_note, community_source.

Inputs (written by the daily community scan, see knowledge/community/README.md):
  knowledge/community/sources.json        registry of every source: data repos, sites, forums, video channels,
                                           Discord servers (metadata only), with how they can be read
  knowledge/community/intel/*.jsonl       one note per line; files are read in name order (dates), and a later line
                                           with the same id replaces an earlier one (status updates, corrections)
Notes are claims from the community, not verified facts: `official` marks Blizzard sources, `status` tracks
corroboration. The coach cites them as n:<id> and says when something is only reported.
"""
import glob
import json
import os

from knowledge import store

KINDS = {"patch", "tip", "route", "mechanic", "class", "dungeon", "addon", "data", "news", "warning"}
STATUSES = {"reported", "corroborated", "official", "contradicted", "superseded"}


def folder(ctx):
    return os.path.join(ctx.root, "knowledge", "community")


def read_notes(base):
    """{id: note} with later files / lines winning; bad lines are counted, never fatal."""
    notes, bad = {}, 0
    for path in sorted(glob.glob(os.path.join(base, "intel", "*.jsonl"))):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    n = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                if not isinstance(n, dict) or not n.get("id") or not (n.get("claim") or n.get("title")):
                    bad += 1
                    continue
                notes[str(n["id"])] = n
    return notes, bad


def _levels(n):
    lv = n.get("levels")
    if isinstance(lv, (list, tuple)) and len(lv) == 2:
        try:
            return int(lv[0]), int(lv[1])
        except (TypeError, ValueError):
            pass
    return None, None


NOTE_COLS = ["id", "seen", "published", "kind", "title", "claim", "detail", "level_min", "level_max", "zones", "classes",
             "factions", "tags", "sources", "official", "confidence", "status", "supersedes"]
SOURCE_COLS = ["id", "kind", "medium", "name", "url", "access", "status", "first_seen", "last_checked", "notes"]


def note_rows(notes):
    """{id: note} (read_notes) -> community_note rows; a note another one supersedes is marked superseded."""
    superseded = set()
    for n in notes.values():
        superseded.update(str(x) for x in n.get("supersedes") or ())
    rows = []
    for nid, n in notes.items():
        lo, hi = _levels(n)
        status = n.get("status") if n.get("status") in STATUSES else ("official" if n.get("official") else "reported")
        if nid in superseded and status != "contradicted":
            status = "superseded"
        rows.append({"id": nid, "seen": n.get("seen"), "published": n.get("published"),
                     "kind": n.get("kind") if n.get("kind") in KINDS else "news", "title": n.get("title"),
                     "claim": n.get("claim"), "detail": n.get("detail"), "level_min": lo, "level_max": hi,
                     "zones": store.jdump(n.get("zones") or []), "classes": store.jdump(n.get("classes") or []),
                     "factions": store.jdump(n.get("factions") or []), "tags": store.jdump(n.get("tags") or []),
                     "sources": store.jdump(n.get("sources") or []), "official": 1 if n.get("official") else 0,
                     "confidence": n.get("confidence"), "status": status,
                     "supersedes": store.jdump(n.get("supersedes") or [])})
    return rows, len(superseded & set(notes))


def source_rows(sources):
    """sources.json's "sources" -> community_source rows (entries without an id skipped)."""
    return [{k: (store.jdump(v) if isinstance(v, (list, dict)) else v) for k, v in
             {"id": s["id"], "kind": s.get("kind"), "medium": s.get("medium"), "name": s.get("name"),
              "url": s.get("url"), "access": s.get("access"), "status": s.get("status"),
              "first_seen": s.get("first_seen"), "last_checked": s.get("last_checked"),
              "notes": s.get("notes")}.items()}
            for s in sources or [] if isinstance(s, dict) and s.get("id")]


def write(conn, note_rows_, source_rows_):
    """The rows into community_note / community_source, replacing those with the same id (the build, and the
    engine's community feed on a live kb.db)."""
    store.insert_rows(conn, "community_note", note_rows_, NOTE_COLS, verb="INSERT OR REPLACE")
    store.insert_rows(conn, "community_source", source_rows_, SOURCE_COLS, verb="INSERT OR REPLACE")


def load(conn, ctx):
    base = folder(ctx)
    if not os.path.isdir(base):
        return {"skipped": "no knowledge/community folder"}
    notes, bad = read_notes(base)
    rows, superseded = note_rows(notes)
    srcs = []
    reg = os.path.join(base, "sources.json")
    if os.path.exists(reg):
        try:
            with open(reg, encoding="utf-8") as fh:
                srcs = source_rows(json.load(fh).get("sources"))
        except (OSError, ValueError) as e:
            ctx.notes.append("community sources.json unreadable: %s" % e)
    write(conn, rows, srcs)
    return {"notes": len(rows), "bad_lines": bad, "superseded": superseded, "sources": len(srcs),
            "official": sum(r["official"] for r in rows)}
