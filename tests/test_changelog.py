#!/usr/bin/env python3
"""What's new (desktop/changelog.json): newest first, with a title and plain items, and the file ships with the app
(package.json build.files). package.json's version is the floor the automatic releases count up from
(desktop/scripts/next-release.js), so the newest entry is that version or a later patch: a hand-raised version
needs its entry."""
import json
import os

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
pkg = json.load(open(os.path.join(ROOT, "desktop", "package.json"), encoding="utf-8"))
cl = json.load(open(os.path.join(ROOT, "desktop", "changelog.json"), encoding="utf-8"))
vs = cl["versions"]
key = lambda v: tuple(int(x) for x in v["version"].split("."))  # noqa: E731
assert vs and key(vs[0]) >= key(pkg) and any(key(v) == key(pkg) for v in vs), \
    "add an entry for %s at the top of desktop/changelog.json (What's new)" % pkg["version"]
assert [key(v) for v in vs] == sorted((key(v) for v in vs), reverse=True), "newest first, no duplicates"
assert len({v["version"] for v in vs}) == len(vs)
for v in vs:
    assert v.get("title") and v.get("date") and v.get("items") and all(isinstance(i, str) and i for i in v["items"]), v
assert "changelog.json" in pkg["build"]["files"], "the notes must ship with the app"
print("changelog: ok")
