#!/usr/bin/env python3
"""What's new across releases (desktop/scripts/next-release.js, run under Node in a scratch git repository).

Regression: the entry a release build adds to desktop/changelog.json is never committed back, so every installed app
showed its own version's notes and then the last entry committed by hand (0.4.17, then 0.4.1). The build now also
fills in each published release newer than changelog.json's newest entry, from the commits between its source (its
notes' "source: <sha>") and the release before it; a release without a source (from before the notes named one) is
where changelog.json got its entry, and a draft (a run that failed) adds nothing: its commits go to the next release.

Needs node and git (skips with 77 without them)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SCRIPT = os.path.join(ROOT, "desktop", "scripts", "next-release.js")
node, git_exe = shutil.which("node"), shutil.which("git")
if not node or not git_exe:
    print("SKIP: needs node and git")
    sys.exit(77)

tmp = tempfile.mkdtemp(prefix="glide-release-notes-")
desk = os.path.join(tmp, "desktop")


def git(*args):
    # a repo-local identity for the scratch commits (never the global config)
    r = subprocess.run([git_exe, "-c", "user.name=Glide test", "-c", "user.email=test@example.invalid",
                        "-c", "commit.gpgsign=false", *args], cwd=tmp, capture_output=True, text=True,
                       encoding="utf-8")
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


def commit(subject):
    with open(os.path.join(tmp, "log.txt"), "a", encoding="utf-8") as fh:
        fh.write(subject + "\n")
    git("add", "-A")
    git("commit", "-q", "-m", subject)
    return git("rev-parse", "HEAD")


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def release(tag, sha=None, draft=False, date="2026-11-01T10:00:00Z"):
    body = "Glide %s\n\nsource: %s" % (tag[1:], sha) if sha else "Glide %s" % tag[1:]
    return {"tag_name": tag, "draft": draft, "published_at": None if draft else date, "body": body}


try:
    os.makedirs(os.path.join(desk, "scripts"))
    shutil.copy(SCRIPT, os.path.join(desk, "scripts", "next-release.js"))
    git("init", "-q")
    write_json(os.path.join(desk, "package.json"), {"name": "glide-desktop", "version": "0.4.0"})
    write_json(os.path.join(desk, "changelog.json"), {"versions": [
        {"version": "0.4.0", "date": "2026-10-30", "title": "Glide 0.4", "items": ["The 0.4 notes."]}]})
    commit("Glide 0.4.0")
    commit("Map shows the route")
    cl = json.load(open(os.path.join(desk, "changelog.json"), encoding="utf-8"))
    cl["versions"].insert(0, {"version": "0.4.1", "date": "2026-10-30", "title": "By hand", "items": ["Hand notes."]})
    write_json(os.path.join(desk, "changelog.json"), cl)
    commit("Release: clearer What's new notes for 0.4.1")          # 0.4.1's entry, committed by hand (no source)
    commit("Guides show dungeon steps")
    c2 = commit("Docs: the plan")
    commit("Guides map draws flights")                            # a failed run's draft, 0.4.3: no entry of its own
    c3 = commit("CI: faster checks")
    commit("Narrate reads the steps aloud")
    c4 = commit("Knowledge refresh 2026-11-01")
    commit("Steps ahead fade on the map")                          # this build: 0.4.5

    releases = [release("v0.4.4", c4, date="2026-11-02T01:00:00Z"), release("v0.4.3", c3, draft=True),
                release("v0.4.2", c2, date="2026-10-31T09:30:00Z"), release("v0.4.1"), release("v0.4.0")]
    rel_file = os.path.join(tmp, "releases.jsonl")
    with open(rel_file, "w", encoding="utf-8") as fh:              # one compact object per line, as gh --jq writes
        fh.write("\n".join(json.dumps(r) for r in releases) + "\n")
    git("add", "-A")
    git("commit", "-q", "-m", "test data")                         # (not a player's commit: subject filtered below)

    env = dict(os.environ, LATEST="v0.4.4", SINCE=c4, RELEASES=rel_file)
    r = subprocess.run([node, os.path.join(desk, "scripts", "next-release.js")], cwd=desk, env=env,
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "version=0.4.5", r.stdout
    got = json.load(open(os.path.join(desk, "changelog.json"), encoding="utf-8"))["versions"]
    by = {v["version"]: v for v in got}
    assert [v["version"] for v in got] == ["0.4.5", "0.4.4", "0.4.2", "0.4.1", "0.4.0"], [v["version"] for v in got]
    # this build: the commits since the last release
    assert by["0.4.5"]["items"] == ["test data", "Steps ahead fade on the map"], by["0.4.5"]["items"]
    # filled in: 0.4.4 holds the failed 0.4.3 run's commits too; CI and docs left out, a refresh summed up
    assert by["0.4.4"]["items"] == ["Narrate reads the steps aloud", "Guides map draws flights",
                                    "Fresh community notes and quest data."], by["0.4.4"]["items"]
    assert by["0.4.4"]["date"] == "2026-11-02", by["0.4.4"]
    # 0.4.2 counts from where 0.4.1's entry was committed (0.4.1's notes name no source)
    assert by["0.4.2"]["items"] == ["Guides show dungeon steps"], by["0.4.2"]["items"]
    assert by["0.4.2"]["date"] == "2026-10-31" and by["0.4.2"]["title"] == "Fixes and improvements", by["0.4.2"]
    # entries already there are kept as written
    assert by["0.4.1"]["items"] == ["Hand notes."] and by["0.4.0"]["title"] == "Glide 0.4", got

    # no release list (gh failed) and a source this checkout lacks: still a build, with this version's notes
    git("checkout", "-q", "--", ".")
    with open(rel_file, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(release("v0.4.4", "f" * 40)) + "\n")
    r = subprocess.run([node, os.path.join(desk, "scripts", "next-release.js")], cwd=desk,
                       env=dict(os.environ, LATEST="v0.4.4", SINCE=c4, RELEASES=rel_file),
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0 and r.stdout.strip() == "version=0.4.5", (r.stdout, r.stderr)
    got = json.load(open(os.path.join(desk, "changelog.json"), encoding="utf-8"))["versions"]
    assert [v["version"] for v in got] == ["0.4.5", "0.4.4", "0.4.1", "0.4.0"], [v["version"] for v in got]
    assert got[1]["items"] == ["Small fixes and improvements."], got[1]
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("release notes: ok")
