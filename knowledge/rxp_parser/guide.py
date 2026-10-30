"""One guide's text -> the parsed guide (header, steps, actions), following GuideLoader.lua's line pipeline."""
import re

from knowledge.rxp_parser.conditions import and_cond, parse_cond
from knowledge.rxp_parser.maps import DEFAULT_MAPS, MapNames
from knowledge.rxp_parser.text import NONSPACE, SPACE, WS, lua_int, lua_num, plain
from knowledge.rxp_parser.verbs import KNOWN_VERBS, STEP_SIDE_EFFECTS, parse_args

_RE_LINES = re.compile(r"\r\n|\r|\n")
_RE_STEPCOND = re.compile(r"<<" + SPACE + r"*(.+)")                          # line:match("<<%s*(.+)")
_RE_LINECOND = re.compile(SPACE + r"*<<" + SPACE + r"*(.+)")                    # "%s*<<%s*(.+)"
_RE_HDRCOND = re.compile(r"(.*?)" + SPACE + r"*<<" + SPACE + r"*(.+)")          # "(.-)%s*<<%s*(.+)"
_RE_TAG = re.compile(r"#(" + NONSPACE + r"+)" + SPACE + r"*(=?)" + SPACE + r"*(.*)")  # "^#(%S+)%s*(=?)%s*(.*)"
_RE_TEXT = re.compile(SPACE + r"*>>" + SPACE + r"*(.*)")                        # "%s*>>%s*(.*)"
_RE_VERB = re.compile(r"\.(" + NONSPACE + r"+)" + SPACE + r"*(.*)")               # "^%.(%S+)%s*(.*)"
_RE_LEVELS = re.compile(r"^" + SPACE + r"*([0-9]{1,2})-([0-9]{1,2})(?![0-9])")


def _split_comment(raw):
    i = raw.find("--")
    if i < 0:
        return raw, None
    return raw[:i], raw[i:]


def _level_range(*names):
    for n in names:
        m = _RE_LEVELS.match(n or "")
        if m:
            return int(m.group(1)), int(m.group(2))
    return None, None


def affix_name(name):
    """RXP's internal guide name: '5-11 Dun Morogh' -> '05-11 Dun Morogh' (GuideLoader addon.affix)."""
    def fix(m):
        smax = m.group(2)
        if len(smax) == 1:
            smax = "0" + smax
        return "0" + m.group(1) + "-" + smax
    return re.sub(r"^([0-9])-([0-9][0-9]?)", fix, name or "", count=1)


def parse_next(raw, own_group=None):
    """`#next` raw value(s) -> [{"name","group","cond"}].

    Lines ('\\n'-joined when the header had several `#next` lines) may end in `<< cond` (applies to every
    entry of that line); entries are ';'-separated; `Group\\Name` selects another group (RXP takes the group
    up to the last backslash), otherwise the guide's own group. `own_group` is the group name, or for guides
    with conditional `#group` lines a list of (group, cond_raw): an entry without an explicit group is then
    emitted once per group whose condition can hold together with the line's, with both conditions AND-ed.
    """
    groups = own_group if isinstance(own_group, list) else [(own_group, None)]
    out = []
    for line in (raw or "").split("\n"):
        line = line.strip(WS)
        if not line:
            continue
        cond_raw = None
        m = _RE_HDRCOND.match(line)
        if m:
            line, cond_raw = m.group(1), m.group(2)
        for part in line.split(";"):
            part = part.strip(WS)
            if not part:
                continue
            g = re.match(r"^" + SPACE + r"*(.+)\\" + SPACE + r"*", part)
            if g:
                out.append({"name": part[g.end():].strip(WS), "group": g.group(1), "cond": parse_cond(cond_raw)})
                continue
            for grp, gcond in groups:
                c = and_cond(cond_raw, gcond)
                if c is None or not c.get("never") or len(groups) == 1:
                    out.append({"name": part, "group": grp, "cond": c})
    return out


def _new_step(idx, lineno, code, comment, warnings):
    st = {"idx": idx, "line": lineno, "text": None, "flags": {}, "cond": None,
          "ui_map": None, "x": None, "y": None, "continent": None, "wx": None, "wy": None, "zone": None,
          "comments": None, "actions": [], "_c": [], "_t": [], "_tags": []}
    m = _RE_STEPCOND.search(code)
    rest = code[4:m.start()] if m else code[4:]
    if m:
        st["cond"] = parse_cond(m.group(1))
    if rest.strip(WS):
        warnings.append("line %d: text after 'step' ignored: %r" % (lineno, rest.strip(WS)))
    if comment:
        st["_c"].append(comment)
    return st


def _set_flag(step, tag, value, cond_raw, lineno):
    step["_tags"].append((tag, True if value == "" else value, cond_raw, lineno))


def _tag_flags(tags):
    """Ordered tag lines -> flags. RXP keeps the first applicable occurrence of a tag (step[tag] set once).

    flags[tag] = first unconditional value; every occurrence of a tag that is conditional or repeated is
    also listed, in source order, in flags["cond_flags"] so a player-specific view can be resolved.
    """
    flags, by_tag = {}, {}
    for tag, value, cond_raw, lineno in tags:
        occ = by_tag.setdefault(tag, [])
        if not any(v == value and c == cond_raw for v, c, _ in occ):     # identical repeats carry nothing
            occ.append((value, cond_raw, lineno))
    cond_flags = []
    for tag, occ in by_tag.items():
        unc = [v for v, c, _ in occ if c is None]
        if unc:
            flags[tag] = unc[0]
        if len(occ) > 1 or any(c is not None for _, c, _ in occ):
            cond_flags += [{"tag": tag, "value": v, "cond": parse_cond(c) if c else None, "line": ln}
                           for v, c, ln in occ]
    if cond_flags:
        flags["cond_flags"] = sorted(cond_flags, key=lambda x: x["line"])
    return flags


def _step_line(step, lineno, code, comment, maps, warnings):
    line, cond_raw = code, None
    m = _RE_LINECOND.search(line)
    if m:
        cond_raw, line = m.group(1), line[:m.start()]
    mt = _RE_TAG.match(line)
    if mt and mt.group(1):
        tag, assign, value = mt.groups()
        if assign == "=":
            value = {"fn": value}
        _set_flag(step, tag, value, cond_raw, lineno)
        if comment:
            step["_c"].append(comment)
        return
    text = None
    mx = _RE_TEXT.search(line)
    if mx:
        if mx.group(1) != "":
            text = mx.group(1)
        line = line[:mx.start()]
    mv = _RE_VERB.match(line)
    verb = kind = None
    if mv:
        verb, raw = mv.group(1), mv.group(2)
        args = parse_args(verb, raw, text, maps)
        if verb not in KNOWN_VERBS:
            args["unknown_verb"] = True
            warnings.append("line %d: invalid function call .%s" % (lineno, verb))
    elif text is not None:
        verb, kind, args = "text", ">>", {"raw": line}
    elif line[:1] == "+":
        verb, kind, text, args = "text", "+", line[1:], {"raw": ""}
    elif line[:1] == "*":
        verb, kind, text, args = "text", "*", line[1:], {"raw": ""}
    elif line == "":
        if cond_raw is not None:            # standalone `<< cond` inside a step: whole-step condition
            step["cond"] = and_cond(step["cond"], cond_raw)
            step["flags"]["cond_line"] = cond_raw
            warnings.append("line %d: standalone '<< %s' applied to the step (RXP ignores it)" % (lineno, cond_raw))
        if comment:
            step["_c"].append(comment)
        return
    else:
        args = {"raw": line, "ignored": True}
        warnings.append("line %d: ignored by RXP grammar: %r" % (lineno, line[:80]))
    if kind:
        args["kind"] = kind
    act = {"line": lineno, "verb": verb, "args": args, "quest_id": None, "entity_kind": None,
           "entity_id": None, "qty": None, "text": None, "cond": parse_cond(cond_raw) if cond_raw else None}
    if text is not None:
        p = plain(text)
        act["text"] = p or None
        if text != p:
            args["text_raw"] = text
        if verb != "macro" and p and not (kind == "+" and p.isdigit()):
            step["_t"].append(p)
    for key, field in (("_q", "quest_id"), ("_k", "entity_kind"), ("_e", "entity_id"), ("_n", "qty")):
        if key in args:
            act[field] = args.pop(key)
    if act["entity_id"] is None and act["entity_kind"] is not None and verb not in ("target", "mob", "unitscan"):
        act["entity_kind"] = None
    if comment:
        args["comment"] = comment[2:].strip(WS)
    # functions.lua side effects on the step (addon.step.X = ...)
    side = None
    if verb in STEP_SIDE_EFFECTS:
        side = STEP_SIDE_EFFECTS[verb]
    elif verb in ("dungeon", "profession") and args.get(verb) and not args.get("unknown"):
        # .dungeon stores RestedXP's tag (canonical: 'Stockades' -> 'STOCKS'), the key its settings use
        side = (verb + ("skip" if args.get("skip") else ""), args[verb])
    elif verb == "dungeon" and args.get("unknown"):
        warnings.append("line %d: invalid dungeon name %r (RXP sets no dungeon flag)" % (lineno, args.get("dungeon")))
    if side:
        _set_flag(step, side[0], side[1], cond_raw, lineno)
    step["actions"].append(act)


def _finish_step(step, maps):
    step["text"] = "\n".join(step.pop("_t")) or None
    step["comments"] = "\n".join(x.strip(WS) for x in step.pop("_c")) or None
    flags = _tag_flags(step.pop("_tags"))
    flags.update(step["flags"])                  # direct flags (cond_line)
    step["flags"] = flags
    for a in step["actions"]:
        if a["verb"] in ("goto", "groundgoto", "flygoto") and "error" not in a["args"]:  # RXP drops bad gotos
            g = a["args"]
            step["ui_map"] = g.get("ui_map")
            for k in ("x", "y", "continent", "wx", "wy"):
                step[k] = g.get(k)
            z = g.get("zone") or ""
            step["zone"] = maps.name(step["ui_map"]) or \
                (z if z and "/" not in z and lua_num(z) is None else None)
            break


def parse_guide(text, source=None, map_ids=None):
    """Guide text -> dict. Pure.

    {"key": "grp||name", "name", "grp", "subgroup", "next" (raw `#next` value(s), '\\n'-joined, `<< cond` kept),
     "level_min", "level_max", "faction", "default_for", "season", "source", "steps": [...],
     "next_list": [{"name","group","cond"}], "cond" (guide `<<` lines), "version", "rxp_name" (RXP's internal
     name, e.g. '05-11 Dun Morogh'), "displayname", "forever_ok" (RXP would load it on Forever),
     "header": [{"tag","value","cond","line"}], "comments" (header `--` lines), "warnings"}

    step: {"idx" (1-based, every `step` line counts), "line", "text" (plain `>>`/`+`/`*` texts, '\\n'-joined),
           "flags" (step tags: unconditional values; "cond_flags": ordered [{"tag","value","cond","line"}] for
           tags that are conditional or repeated; first applicable wins, see step_flags()),
           "cond", "ui_map","x","y","continent","wx","wy","zone" (first valid goto; world form `m/c,A,B` gives
           wx = B, wy = A per store.py's axis convention), "comments", "actions"}
    action: {"line" (1-based source line), "verb" ("text" for >>/+/* lines, None for lines RXP ignores),
             "args" (per verb, always "raw"; "comment" = trailing `--` comment; "text_raw" = text with markup),
             "quest_id","entity_kind","entity_id","qty","text","cond"}
    """
    maps = MapNames(map_ids) if map_ids else DEFAULT_MAPS
    warnings, header, hdr_conds, hdr_comments = [], [], [], []
    steps, step = [], None
    for lineno, rawline in enumerate(_RE_LINES.split(text or ""), 1):
        code, comment = _split_comment(rawline)
        code = code.strip(WS)
        if not code:
            if comment:
                (step["_c"] if step else hdr_comments).append(comment)
            continue
        if code[:4] == "step":
            if step:
                _finish_step(step, maps)
            step = _new_step(len(steps) + 1, lineno, code, comment, warnings)
            steps.append(step)
            continue
        if step is None:
            if comment:
                hdr_comments.append(comment)
            cond = None
            m = _RE_HDRCOND.match(code)
            if m:
                if m.group(1) == "":
                    hdr_conds.append(m.group(2))
                    continue
                code, cond = m.group(1), m.group(2)
            mt = _RE_TAG.match(code)
            if mt and mt.group(1):
                header.append({"tag": mt.group(1), "value": mt.group(3), "cond": cond, "line": lineno})
            else:
                warnings.append("line %d: header line ignored: %r" % (lineno, code[:80]))
            continue
        _step_line(step, lineno, code, comment, maps, warnings)
    if step:
        _finish_step(step, maps)

    def first(tag):                 # first line wins (RXP keeps the first applicable `#tag`)
        return next((h["value"] for h in header if h["tag"] == tag), None)

    name = first("name")
    grp = first("group")
    gcond = None
    if hdr_conds:     # RXP: first `<<` line = enabledFor, last one decides skipGuide -> both must hold
        gcond = and_cond(hdr_conds[0], hdr_conds[-1])
    nexts = []
    for h in header:
        if h["tag"] == "next":
            nexts.append(h["value"] + (" << " + h["cond"] if h["cond"] else ""))
    next_raw = "\n".join(nexts) or None
    displaynames = [h["value"] for h in header if h["tag"] == "displayname"]
    lmin, lmax = _level_range(name, *displaynames)
    version = lua_int(first("version"))
    out = {
        "key": "%s||%s" % (grp, name) if grp and name else None,
        "name": name, "grp": grp, "subgroup": first("subgroup"), "next": next_raw,
        "level_min": lmin, "level_max": lmax,
        "faction": gcond["faction"] if gcond else None,
        "default_for": first("defaultfor"), "season": first("season"), "source": source,
        "steps": steps,
        "next_list": parse_next(next_raw, [(h["value"], h["cond"]) for h in header if h["tag"] == "group"]
                                if sum(h["tag"] == "group" for h in header) > 1 else grp),
        "cond": gcond, "version": version if version is not None else 0,
        "rxp_name": affix_name(name) if name else None,
        "displayname": displaynames[0] if displaynames else None,
        "header": [{k: (parse_cond(v) if k == "cond" and v else v) for k, v in h.items()} for h in header],
        "comments": "\n".join(c.strip(WS) for c in hdr_comments) or None,
        "warnings": warnings,
    }
    if out["default_for"]:
        out["default_for_cond"] = parse_cond(out["default_for"])
    # ParseGuide's check at the first step: a game-tagged guide loads on Forever only if #forever or #classic
    tags = {h["tag"].lower() for h in header if h["cond"] is None}
    has_game = bool(tags & {"classic", "tbc", "wotlk", "df", "retail", "cata"})
    out["forever_ok"] = bool(name and grp) and (not has_game or bool(tags & {"forever", "classic"}))
    if not name:
        warnings.append("guide has no #name")
    if not grp:
        warnings.append("guide has no #group")
    return out
