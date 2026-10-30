#!/usr/bin/env python3
"""Refresh the community data snapshot (data/seed/seed.json.gz) from the data repositories in
knowledge/community/sources.json, and report what changed.

  python tools/refresh_community.py --cache <dir> [--force]

For every source with "kind": "git" and an "adapter" (the folder name build_seed.py reads, e.g.
"alcaras_forever-ref"), the repository is shallow-cloned (or fetched) into <cache>/<adapter>. When any pinned
revision moved (or --force), build_seed.py rebuilds the snapshot from <cache>, and data/seed/seed_report.json
records the revisions, the per-source counts and the differences from the previous snapshot. build_seed.py
also regenerates addon/Glide/Data/QuestIDs.lua (the quest-ID sweep list), which then has to be copied to the
game's AddOns folder too.

Git sources without an adapter are only tracked (revision and date), so a new data project shows up in the
report before anyone writes a reader for it. Needs git and lupa (pip install lupa). Standard library otherwise.
"""
import argparse
import gzip
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
REGISTRY = os.path.join(ROOT, "knowledge", "community", "sources.json")
SEED = os.path.join(ROOT, "data", "seed", "seed.json.gz")
REPORT = os.path.join(ROOT, "data", "seed", "seed_report.json")


def log(*a):
    print("[refresh]", *a, flush=True)


def git(args, cwd=None, timeout=600):
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), (r.stderr or r.stdout).strip()[-400:]))
    return r.stdout.strip()


def sync_repo(url, dest):
    """Shallow clone or fetch+reset; returns (sha, commit date)."""
    if os.path.isdir(os.path.join(dest, ".git")):
        git(["fetch", "--depth", "1", "origin"], cwd=dest)
        git(["reset", "--hard", "FETCH_HEAD"], cwd=dest)
    else:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        git(["clone", "--depth", "1", url, dest])
    return git(["rev-parse", "HEAD"], cwd=dest), git(["log", "-1", "--format=%cs"], cwd=dest)


def load_seed(path):
    if not os.path.exists(path):
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def summarize(seed):
    if not seed:
        return {}
    ids = seed.get("ids") or {}
    return {"counts": (seed.get("meta") or {}).get("counts") or {},
            "forever_new": len(ids.get("forever_new") or []), "all": len(ids.get("all") or []),
            "forever_changed": len(ids.get("forever_changed") or [])}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", required=True, help="folder for the repository clones")
    ap.add_argument("--force", action="store_true", help="rebuild even when no revision moved")
    ap.add_argument("--registry", default=REGISTRY)
    args = ap.parse_args()
    with open(args.registry, encoding="utf-8") as fh:
        reg = json.load(fh)
    prev_report = {}
    if os.path.exists(REPORT):
        with open(REPORT, encoding="utf-8") as fh:
            prev_report = json.load(fh)
    prev_revs = {k: v.get("sha") for k, v in (prev_report.get("repos") or {}).items()}
    repos, moved, failed = {}, [], {}
    for s in reg.get("sources") or []:
        if s.get("kind") != "git" or s.get("status") == "dead":
            continue
        name = s.get("adapter") or s["id"]
        try:
            sha, date = sync_repo(s["url"], os.path.join(args.cache, name))
        except Exception as e:  # noqa: BLE001 - one bad repo never stops the others
            failed[s["id"]] = str(e)
            log("FAILED", s["id"], e)
            continue
        repos[s["id"]] = {"sha": sha, "date": date, "adapter": s.get("adapter"), "url": s["url"]}
        if prev_revs.get(s["id"]) != sha:
            moved.append(s["id"])
        log("%-34s %s %s%s" % (s["id"], sha[:10], date, "  (moved)" if prev_revs.get(s["id"]) != sha else ""))
    used_moved = [m for m in moved if repos[m].get("adapter")]
    rebuilt = False
    before = summarize(load_seed(SEED))
    if used_moved or args.force or not os.path.exists(SEED):
        r = subprocess.run([sys.executable, os.path.join(HERE, "build_seed.py"), args.cache,
                            os.path.dirname(SEED)], capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            log("build_seed failed:", (r.stderr or r.stdout)[-2000:])
            failed["build_seed"] = (r.stderr or r.stdout)[-2000:]
        else:
            rebuilt = True
            log(r.stdout.strip()[-600:])
    after = summarize(load_seed(SEED)) if rebuilt else before
    delta = {}
    for k in set(before.get("counts") or {}) | set(after.get("counts") or {}):
        a, b = (before.get("counts") or {}).get(k), (after.get("counts") or {}).get(k)
        if a != b:
            delta[k] = [a, b]
    for k in ("forever_new", "all", "forever_changed"):
        if before.get(k) != after.get(k):
            delta["ids_" + k] = [before.get(k), after.get(k)]
    report = {"checked": time.strftime("%Y-%m-%d %H:%M:%S"), "rebuilt": rebuilt,
              "built": time.strftime("%Y-%m-%d %H:%M:%S") if rebuilt else prev_report.get("built"),
              "repos": repos, "moved": moved, "moved_with_adapter": used_moved, "failed": failed,
              "counts": after.get("counts"), "delta": delta}
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    log("report:", json.dumps({k: report[k] for k in ("rebuilt", "moved", "failed", "delta")}))
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
