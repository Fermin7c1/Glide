"""Step-level `#include` expansion across a corpus of parsed guides."""
import copy
import re

from knowledge.rxp_parser.conditions import and_cond
from knowledge.rxp_parser.text import WS


def _norm_group(g):
    g = re.sub(r"\((A|H)\)", "", g or "")
    g = re.sub(r"\b(Alliance|Horde)\b", "", g)
    return re.sub(r"\s+", " ", g).strip().lower()


def _include_target(raw):
    s = (raw or "").strip(WS)
    target, _, rng = s.partition("@")
    group, sep, name = target.rpartition("\\")
    start, _, end = rng.partition("-")
    return (group.strip(WS) if sep else None), name.strip(WS), (start.strip(WS) or None), (end.strip(WS) or None)


def _labels(step):
    out = []
    f = step["flags"]
    if isinstance(f.get("label"), str):
        out.append(f["label"])
    for cf in f.get("cond_flags", ()):
        if cf["tag"] == "label" and isinstance(cf["value"], str):
            out.append(cf["value"])
    return out


def _resolve_include(raw, own_key, own_group, by_key, by_name):
    group, name, start, end = _include_target(raw)
    key = "%s||%s" % (group or own_group, name)
    note = None
    if key not in by_key or key == own_key:
        cands = [k for k in by_name.get(name, ()) if k != own_key]
        want = _norm_group(group or own_group)
        cands = [k for k in cands if _norm_group(by_key[k]["grp"]) == want]
        if len(cands) != 1:
            return None, start, end, "target %r not found" % key
        note = "resolved %r by name to %r" % (key, cands[0])
        key = cands[0]
    return key, start, end, note


def _slice(steps, start, end):
    """Steps from the first step labelled `start` to the last labelled `end` (inclusive)."""
    if not start:
        return steps, None
    si = next((i for i, s in enumerate(steps) if start in _labels(s)), None)
    if si is None:
        return None, "label %r not found" % start
    if not end:
        return steps[si:], None
    ei = None
    for i in range(len(steps) - 1, si - 1, -1):
        if end in _labels(steps[i]):
            ei = i
            break
    if ei is None:
        return None, "label %r not found" % end
    return steps[si:ei + 1], None


def _include_entry(step):
    f = step["flags"]
    if "include" in f and isinstance(f["include"], str):
        return f["include"], None
    for cf in f.get("cond_flags", ()):
        if cf["tag"] == "include" and isinstance(cf["value"], str):
            return cf["value"], cf["cond"]
    return None, None


def expand_includes(guides):
    """Expand step-level `#include [Group\\]Name[@StartLabel[-EndLabel]]` in every guide of {key: guide}.

    The include step is replaced by copies of the target guide's (fully expanded) steps; a label range is
    inclusive, from the first step labelled StartLabel to the last labelled EndLabel (conditional duplicates
    of a label are kept, with their conditions). Each copied step gets flags.included_from (origin guide
    key) and flags.included_idx (its idx there), and its cond AND-ed with the include step's condition.
    Without a group the including guide's group is used; a target that does not exist under that exact key
    is resolved by name when exactly one other guide of the same group family ((A)/(H) ignored) has it
    (flags.include_note says so). Missing targets, missing labels and cycles keep the include step with
    flags.include_unresolved = reason. Steps are renumbered 1..n. Returns {"resolved","unresolved","notes"}.
    """
    by_name = {}
    for k, g in guides.items():
        by_name.setdefault(g.get("name"), []).append(k)
    stats = {"resolved": 0, "unresolved": [], "notes": []}
    done, busy = {}, set()

    def full(key):
        if key in done:
            return done[key]
        busy.add(key)
        out = []
        own_group = guides[key]["grp"]
        for st in guides[key]["steps"]:
            inc, inc_cond = _include_entry(st)
            if not inc:
                out.append(st)
                continue
            tkey, start, end, note = _resolve_include(inc, key, own_group, guides, by_name)
            sub, err = None, (note if tkey is None else None)
            if tkey is not None and tkey in busy:
                tkey, err = None, "include cycle via %r" % tkey
            if tkey is not None:
                sub, err = _slice(full(tkey), start, end)
            if sub is None:
                bad = copy.deepcopy(st)
                bad["flags"]["include_unresolved"] = err
                stats["unresolved"].append({"guide": key, "include": inc, "reason": err})
                out.append(bad)
                continue
            if note:
                stats["notes"].append({"guide": key, "include": inc, "note": note})
            if st["actions"] or st["text"]:
                keep = copy.deepcopy(st)
                keep["flags"].pop("include", None)
                out.append(keep)
            cond = and_cond(st.get("cond"), inc_cond) if inc_cond else st.get("cond")
            for s in sub:
                c = copy.deepcopy(s)
                if "included_from" not in c["flags"]:
                    c["flags"]["included_from"] = tkey
                    c["flags"]["included_idx"] = s["idx"]
                    if note:
                        c["flags"]["include_note"] = note
                if cond:
                    c["cond"] = and_cond(cond, c.get("cond"))
                out.append(c)
            stats["resolved"] += 1
        for i, s in enumerate(out, 1):
            if s["idx"] != i:                     # shallow copy: never renumber another guide's step object
                s = out[i - 1] = dict(s)
                s["idx"] = i
        busy.discard(key)
        done[key] = out
        return out

    for key in guides:
        if any(_include_entry(s)[0] for s in guides[key]["steps"]):
            full(key)
    for key, steps in done.items():
        guides[key]["steps"] = steps
    return stats
