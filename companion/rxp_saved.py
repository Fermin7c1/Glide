"""RestedXP's saved levelling history (WTF/.../SavedVariables/RXPGuides.lua), read from disk.

RestedXP's levelling tracker keeps, per character and level: when the level started and finished (/played seconds),
XP from quests, mobs and group members, and deaths. Its account file also keeps level splits for every character.
WoW writes these files on logout and /reload only, so this is history, not live data: the current level's numbers
come from the game. Reads are cached on the files' modification times.
"""
import glob
import os
import re
import sys

from log import log_once

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_cache = {}


def _lua():
    """knowledge/sources/_lua.py, the Lua table reader the knowledge build uses."""
    if ROOT not in sys.path:
        sys.path.append(ROOT)
    from knowledge.sources import _lua
    return _lua


def _read(path):
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None
    hit = _cache.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        data = _lua().read_file(path)
    except Exception as e:  # noqa: BLE001 - a damaged file: no history
        log_once("rxp_saved.read", e)
        data = None
    _cache[path] = (mt, data)
    return data


def _sum(d):
    tot = 0
    for v in (d or {}).values() if isinstance(d, dict) else ():
        if isinstance(v, dict):
            tot += sum(x for x in v.values() if isinstance(x, (int, float))) if "xp" not in v else (v.get("xp") or 0)
        elif isinstance(v, (int, float)):
            tot += v
    return int(tot)


def _profile_of(tracking, name):
    """The tracker profile of character `name` (profiles are keyed "Name - Realm")."""
    if not isinstance(tracking, dict) or not name:
        return None
    keys = tracking.get("profileKeys") or {}
    profiles = tracking.get("profiles") or {}
    for k, v in keys.items():
        if isinstance(k, str) and k.split(" - ")[0].lower() == name.lower():
            return profiles.get(v)
    return None


def character_levels(wow_dir, name):
    """[{"level", "minutes", "quest_xp", "mob_xp", "group_xp", "deaths"}] for the character's finished levels, oldest
    first, or None when RestedXP has no record of it."""
    if not wow_dir or not name:
        return None
    paths = glob.glob(os.path.join(wow_dir, "WTF", "Account", "*", "*", "*", "SavedVariables", "RXPGuides.lua"))
    best = None
    for p in sorted(paths, key=lambda x: os.path.getmtime(x), reverse=True):
        d = _read(p) or {}
        prof = _profile_of(d.get("RXPCTrackingData"), name)
        if prof and isinstance(prof.get("levels"), list):
            best = prof["levels"]
            break
    if best is None:
        return None
    out = []
    for i, e in enumerate(best):
        if not isinstance(e, dict):
            continue
        ts = e.get("timestamp") or {}
        a, b = ts.get("started"), ts.get("finished")
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or b <= a:
            continue
        out.append({"level": i + 1, "minutes": round((b - a) / 60.0, 1), "quest_xp": _sum(e.get("quests")),
                    "mob_xp": _sum(e.get("mobs")), "group_xp": int(e.get("groupExperience") or 0),
                    "deaths": int(e.get("deaths") or 0)})
    return out


_LEVEL = re.compile(r"Level\s+(\d+)")


def personal_bests(wow_dir):
    """{level: {"minutes", "character"}}: the fastest time any of the account's characters spent in each level,
    from RestedXP's level splits."""
    out = {}
    if not wow_dir:
        return out
    for p in glob.glob(os.path.join(wow_dir, "WTF", "Account", "*", "SavedVariables", "RXPGuides.lua")):
        d = _read(p) or {}
        try:
            splits = d["RXPDB"]["profiles"]["global"]["reports"]["splits"]
        except (KeyError, TypeError):
            continue
        for key, v in (splits or {}).items():
            if not isinstance(v, dict):
                continue
            char = str(key).split("|")[0]
            for x in ((v.get("history") or {}).get("levels") or ()) if isinstance(v.get("history"), dict) else ():
                if not isinstance(x, dict):
                    continue
                m = _LEVEL.search(str(x.get("text") or ""))
                dur = x.get("duration")
                if not m or not isinstance(dur, (int, float)) or dur <= 0:
                    continue
                lv = int(m.group(1)) - 1                    # "Level N" = the time it took to reach N
                if lv >= 1 and (lv not in out or dur / 60.0 < out[lv]["minutes"]):
                    out[lv] = {"minutes": round(dur / 60.0, 1), "character": char}
    return out


def history(wow_dir, name):
    """{"levels": [...with "best_minutes", "best_character" where known], "bests": {level: {...}}} or None."""
    levels = character_levels(wow_dir, name)
    bests = personal_bests(wow_dir)
    for e in levels or ():
        b = bests.get(e["level"])
        if b and b["character"].lower() != (name or "").lower():
            e["best_minutes"], e["best_character"] = b["minutes"], b["character"]
        elif b:
            e["best_minutes"] = b["minutes"]
    if not levels and not bests:
        return None
    return {"levels": levels or [], "bests": bests}
