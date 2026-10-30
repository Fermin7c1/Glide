"""Which Glide addon version the companion expects, and which one is installed / running.

The expected version is read from the project's own addon/Glide/Glide.toc (## Version), so
updating the addon in the project updates the expectation. The installed copy is the TOC in the WoW
client's AddOns folder; the running copy is what the addon reports in-game (core key "gv"). Below those, the strip
itself has a version (protocol.PROTO, the first byte of every frame): an addon that speaks another one can't be read
at all, so strip_mismatch says that instead of "no game data".
"""
import os
import re

import glideconfig

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))


def toc_version(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            m = re.search(r"^##\s*Version:\s*(\S+)", fh.read(), re.M)
            return m.group(1) if m else None
    except OSError:
        return None


def expected():
    return toc_version(os.path.join(ROOT, "addon", "Glide", "Glide.toc"))


def _wow_dir(wow_dir):
    return wow_dir or glideconfig.wow_dir(ROOT)


def installed(wow_dir=None):
    wow_dir = _wow_dir(wow_dir)
    if not wow_dir:
        return None
    return toc_version(os.path.join(wow_dir, "Interface", "AddOns", "Glide", "Glide.toc"))


def status(running, frames, exp=None, inst=None, app=False):
    """-> dict(expected, installed, running, ok, problem, fix, kind). kind says what is wrong: "installed" (the
    AddOns folder has another version) or "running" (the game still runs another version). app: run by the desktop
    app, which installs the addon (its Addon page), so the fix says to use that; run on its own (a developer's
    checkout), the fix names the project's addon folder."""
    exp = exp or expected()
    out = {"expected": exp, "installed": inst, "running": running, "ok": True, "problem": None, "fix": None,
           "kind": None}
    if frames < 10:                      # no game data yet: nothing to judge
        out["ok"] = None
        return out
    if inst and exp and inst != exp:
        out.update(ok=False, kind="installed", problem="the AddOns folder has Glide %s, the %s has %s" % (
                       inst, "app" if app else "project", exp),
                   fix=("install the update on the Glide app's Addon page, then fully restart WoW" if app else
                        "copy addon\\Glide from the project into the game's Interface\\AddOns folder, "
                        "then fully restart WoW"))
    elif running != exp:
        # the version the game reports is read from the TOC when WoW starts, so a /reload doesn't change it
        out.update(ok=False, kind="running",
                   problem="the game is running Glide %s, expected %s" % (running or "an older addon", exp),
                   fix="fully exit and restart WoW (a /reload doesn't load new addon files)")
    return out


def strip_mismatch(sent, expect, app=False):
    """The addon's frames read cleanly but carry strip version `sent`, and this engine reads `expect`
    (protocol.PROTO) -> dict(sent, expected, newer, title, problem, fix, action). newer: the addon is newer than the
    engine (so the engine needs the update). app: wording for the desktop app's players, as in status()."""
    newer = sent > expect
    me = "Glide app" if app else "engine"
    out = {"sent": sent, "expected": expect, "newer": newer, "action": None,
           "title": ("The Glide addon doesn't match this app" if app else
                     "Glide addon speaks strip version %d, the engine expects %d" % (sent, expect)),
           "problem": "the game's Glide addon sends strip version %d and this %s reads version %d" % (sent, me, expect)}
    if newer:
        out["fix"] = ("update the Glide app, or install its addon on the Addon page and fully restart WoW" if app else
                      "update the project's companion to match the addon, or copy addon\\Glide from the project into "
                      "the game's Interface\\AddOns folder and fully restart WoW")
    else:
        out["fix"] = ("install the update on the Glide app's Addon page, then fully restart WoW" if app else
                      "copy addon\\Glide from the project into the game's Interface\\AddOns folder, then fully "
                      "restart WoW")
        # under the app its own addon check offers the install (R19: one owner per check)
        out["action"] = None if app else "addon_install"
    return out
