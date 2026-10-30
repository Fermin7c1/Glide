"""RestedXP's levelling talent builds and the character's next pick.

RestedXP ships a talent order per class (Guides/Talents/classic-<class>.lua: a step per level from the build's
minimum level, each with `.talent tab,tier,column,rank`). It only loads them on Classic Era, never on Forever, so Glide
reads the files itself. Every pick is checked against the character's real talents (addon 0.13 list N: position,
rank, max rank, name): a talent Forever doesn't have at that spot, under another name or with fewer ranks, is
reported as not matching instead of suggested.
"""
import os
import re
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if ROOT not in sys.path:
    sys.path.append(ROOT)       # the knowledge package: its values module (stdlib only)
from knowledge.values import as_int  # noqa: E402

_GUIDE = re.compile(r"addon\.talents\.RegisterGuide\(\s*\[(=*)\[(.*?)\]\1\]\s*\)", re.S)
_TALENT = re.compile(r"^\.talent\s+(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*(\d+))?")
_LABEL = re.compile(r"^level\s*(?:--\s*(.*))?$")
_RANK = re.compile(r"\s*\(Rank\s*\d+\)\s*$", re.I)


def parse(text):
    """One RegisterGuide text -> {"name", "next", "min", "max", "order", "hardcore", "steps": [{"optional",
    "picks": [{"pos": "tab tier column" e.g. "213", "rank", "label"}]}]}, or None without a #name."""
    g = {"steps": []}
    step = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _LABEL.match(line)
        if m:
            step = {"optional": False, "picks": [], "label": (m.group(1) or "").strip()}
            g["steps"].append(step)
            continue
        if step is None:
            t = re.match(r"^#(\S+)\s*(.*)$", line)
            if t and t.group(1) not in g:
                g[t.group(1)] = t.group(2).strip()
            continue
        if line.startswith("#optional"):
            step["optional"] = True
            continue
        t = _TALENT.match(line)
        if t:
            label = line.split("--", 1)[1].strip() if "--" in line else step["label"]
            step["picks"].append({"pos": "%s%s%s" % t.group(1, 2, 3), "rank": int(t.group(4) or 1),
                                  "label": _RANK.sub("", label)})
    if not g.get("name"):
        return None
    return {"name": g["name"], "next": g.get("next") or None, "min": as_int(g.get("minLevel"), 10),
            "max": as_int(g.get("maxLevel"), 60), "order": as_int(g.get("order"), 99), "hardcore": "hardcore" in g, "steps": [s for s in g["steps"] if s["picks"]]}


def parse_file(text):
    return [b for b in (parse(m.group(2)) for m in _GUIDE.finditer(text or "")) if b]


_cache = {}


def builds(wow_dir, cls):
    """RestedXP's builds for a class (e.g. "WARRIOR") from the installed addon; [] when it isn't there."""
    if not wow_dir or not cls:
        return []
    path = os.path.join(wow_dir, "Interface", "AddOns", "RXPGuides", "Guides", "Talents",
                        "classic-%s.lua" % str(cls).lower())
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return []
    hit = _cache.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            out = parse_file(fh.read())
    except OSError:
        out = []
    _cache[path] = (mt, out)
    return out


def _chain(all_builds, b):
    """The build and the ones it continues into (#next), in order."""
    by = {x["name"]: x for x in all_builds}
    out, seen = [], set()
    while b is not None and b["name"] not in seen:
        seen.add(b["name"])
        out.append(b)
        b = by.get(b["next"]) if b.get("next") else None
    return out


def _plan(chain):
    """[(level, step)] for a chain of builds: each build's steps from its own minimum level."""
    out, last = [], 0
    for b in chain:
        for i, st in enumerate(b["steps"]):
            lv = b["min"] + i
            if lv > last:
                out.append((lv, st))
                last = lv
    return out


def _agreement(plan, have, level):
    """How many of the character's spent points this plan explains up to its level."""
    need = {}
    for lv, st in plan:
        if lv > level:
            break
        if st["optional"]:
            continue
        for p in st["picks"]:
            need[p["pos"]] = max(need.get(p["pos"], 0), p["rank"])
    return sum(min(have.get(pos, 0), r) for pos, r in need.items())


def next_pick(all_builds, talents, level):
    """The next talent the build has the character take -> {"build", "talent", "rank", "level", "due",
    "matches_forever"} (talent may be "A or B" for a choice), {"build", "done": True} when the build is complete, or
    None (below level 10, no builds, no talent list from the addon)."""
    if not all_builds or talents is None or not level or level < 10:
        return None
    by_pos = {t["pos"]: t for t in talents}
    have = {t["pos"]: t["rank"] for t in talents if t["rank"]}
    starts = [b for b in all_builds if not any(x.get("next") == b["name"] for x in all_builds)] or all_builds
    best = None
    for b in starts:
        plan = _plan(_chain(all_builds, b))
        if not plan:
            continue
        score = (_agreement(plan, have, level), -b["hardcore"], -b["order"])     # Forever has no hardcore mode
        if best is None or score > best[0]:
            best = (score, b, plan)
    if best is None:
        return None
    _, start, plan = best
    for lv, st in plan:
        picks = [p for p in st["picks"] if have.get(p["pos"], 0) < p["rank"]]
        if st["optional"] and len(picks) < len(st["picks"]):
            continue                                  # one of the choices is taken
        if not picks:
            continue
        ok = all(p["pos"] in by_pos and by_pos[p["pos"]]["max"] >= p["rank"]
                 and _norm(by_pos[p["pos"]]["name"]) == _norm(p["label"]) for p in picks)
        names = [by_pos[p["pos"]]["name"] if p["pos"] in by_pos else p["label"] for p in picks]
        return {"build": start["name"], "talent": " or ".join(names), "rank": picks[0]["rank"], "level": lv,
                "due": lv <= level, "matches_forever": ok}
    return {"build": start["name"], "done": True}


def _norm(s):
    return re.sub(r"[^a-z]", "", (s or "").lower())


def describe(p):
    """'Cruelty, rank 3' style text for a pick (None for nothing to say)."""
    if not p or p.get("done"):
        return None
    return "%s, rank %d" % (p["talent"], p["rank"])
