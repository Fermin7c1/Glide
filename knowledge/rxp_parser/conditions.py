"""`<<` conditions: RXP's applies() for a player, and the flat class / race / faction form the knowledge base
stores (with the full expression kept when the flat form is not exact)."""
import re

from knowledge.rxp_parser.text import SPACE, WS, lua_num

_RE_LIT = re.compile(r"!?[A-Za-z0-9]+")                                    # "!?[%w%d]+"
_RE_PAREN = re.compile(r"(!?)\(" + SPACE + r"*(.*?)" + SPACE + r"*\)")          # "(!?)%(%s*(.-)%s*%)"

_CLASSES = ("WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK", "DRUID",
            "DEATHKNIGHT", "MONK", "DEMONHUNTER", "EVOKER")
_CLASS_ALIAS = {"DK": "DEATHKNIGHT"}
_RACES = ("Human", "Dwarf", "NightElf", "Gnome", "Orc", "Undead", "Scourge", "Tauren", "Troll", "Skyborne",
          "BloodElf", "Draenei", "Goblin", "Worgen", "Pandaren", "Nightborne", "HighmountainTauren", "VoidElf",
          "LightforgedDraenei", "ZandalariTroll", "KulTiran", "DarkIronDwarf", "Vulpera", "MagharOrc",
          "Mechagnome", "Dracthyr", "EarthenDwarf", "Haranir", "Harronir")
_FACTIONS = ("Alliance", "Horde")
_UNIV_CLASSES = _CLASSES[:9]
_UNIV_RACES = ("Human", "Dwarf", "NightElf", "Gnome", "Orc", "Scourge", "Tauren", "Troll", "Skyborne")
_TRUE, _FALSE = "RXPCONDTRUE", "RXPCONDFALSE"


def _race_token(race):
    """Player race -> RXP's UnitRace() file token ('Undead' -> 'Scourge', 'Haranir' -> 'Harronir')."""
    if not race:
        return None
    r = str(race).replace(" ", "")
    for known in _RACES:
        if known.lower() == r.lower():
            r = known
            break
    return {"Undead": "Scourge", "Haranir": "Harronir"}.get(r, r)


def _class_token(cls):
    if not cls:
        return None
    c = str(cls).replace(" ", "").replace("_", "").upper()
    return _CLASS_ALIAS.get(c, c)


class _Player:
    __slots__ = ("cls", "race", "faction", "level", "game", "season", "sex", "locale", "custom")

    def __init__(self, cls=None, race=None, faction=None, level=None, game="FOREVER", season=0, sex=None,
                 locale="enUS", custom=None):
        self.cls = _class_token(cls)
        self.race = _race_token(race)
        self.faction = str(faction).capitalize() if faction else None
        self.level = level if level is not None else 1
        self.game = (game or "").upper()
        self.season = season
        self.sex = sex
        self.locale = locale
        self.custom = custom


def _lit_true(entry, p):
    """One RXP literal (without '!'), exactly as applies() evaluates it."""
    if entry == _TRUE:
        return True
    if entry == _FALSE:
        return False
    lv = lua_num(entry)
    level = lv if lv is not None else 0xfff
    upper = entry.upper()
    gender = False
    e = entry
    if e == "Undead":
        e = "Scourge"
    elif upper == "DK":
        upper = "DEATHKNIGHT"
    elif (upper == "MALE" and p.sex == 2) or (upper == "FEMALE" and p.sex == 3):
        gender = True
    elif upper == "SOD" and p.season == 2:
        e = p.faction
    elif upper == "DF":
        e = "RETAIL"
    elif p.faction == "Neutral" and not p.custom and e in ("Alliance", "Horde"):
        e = p.faction
    elif e == "Haranir":
        e = "Harronir"
    return bool(gender or (p.cls is not None and upper == p.cls) or (p.race is not None and e == p.race)
                or (p.faction is not None and e == p.faction) or p.level >= level
                or upper == p.game or (p.custom is not None and e == p.custom) or e == p.locale)


def _applies_raw(text, p):
    def sub(m):
        return _TRUE if _applies_raw(m.group(2), p) != (m.group(1) == "!") else _FALSE
    text = _RE_PAREN.sub(sub, text)
    for alt in text.split("/"):
        if not alt:
            continue
        ok = True
        for lit in _RE_LIT.findall(alt):
            neg = lit[0] == "!"
            ok = _lit_true(lit[1:] if neg else lit, p) != neg
            if not ok:
                break
        if ok:
            return True
    return False


def cond_applies(cond, cls=None, race=None, faction=None, level=None, game="FOREVER", season=0, sex=None,
                 locale="enUS"):
    """RXP applies() for a parsed cond dict (or raw string). None -> True.

    cls 'Warrior'/'WARRIOR', race 'Undead'/'Scourge'/'NightElf', faction 'Alliance'/'Horde'.
    """
    if cond is None:
        return True
    raw = cond.get("raw") if isinstance(cond, dict) else cond
    if raw is None:
        return True
    return _applies_raw(raw, _Player(cls, race, faction, level, game, season, sex, locale))


def _classify(tok):
    up = _CLASS_ALIAS.get(tok.upper(), tok.upper())
    if up in _CLASSES:
        return "class"
    if tok in _RACES:
        return "race"
    if tok in _FACTIONS:
        return "faction"
    return "other"


def _alternatives(raw):
    """DNF as lists of literals (as written, '!' kept). Parenthesised groups are not expanded (-> None)."""
    if "(" in raw or ")" in raw:
        return None
    return [_RE_LIT.findall(alt) for alt in raw.split("/") if alt]


_COND_CACHE = {}


def _build_cond(raw):
    alts = _alternatives(raw)
    c = {"raw": raw, "class": [], "not_class": [], "race": [], "not_race": [], "faction": None}
    other, fpos, fneg = [], [], []
    for alt in alts or [_RE_LIT.findall(raw)]:
        for lit in alt:
            neg = lit[0] == "!"
            tok = lit[1:] if neg else lit
            kind = _classify(tok)
            if kind == "faction":
                lst = fneg if neg else fpos
            elif kind == "other":
                lst, tok = other, lit
            else:
                lst = c[("not_" if neg else "") + kind]
            if tok not in lst:
                lst.append(tok)
    fp = set(fpos)
    if len(fp) == 1:
        c["faction"] = fpos[0]
    elif not fp and len(set(fneg)) == 1:
        c["faction"] = "Horde" if fneg[0] == "Alliance" else "Alliance"
    if other:
        c["other"] = other
    # exactness of the flat form vs RXP semantics, over every class x race x faction (Forever context)
    classes = list(_UNIV_CLASSES) + [_class_token(x) for x in c["class"] + c["not_class"]
                                     if _class_token(x) not in _UNIV_CLASSES]
    races = list(_UNIV_RACES) + [_race_token(x) for x in c["race"] + c["not_race"]
                                 if _race_token(x) not in _UNIV_RACES]
    pc = {_class_token(x) for x in c["class"]}
    nc = {_class_token(x) for x in c["not_class"]}
    pr = {_race_token(x) for x in c["race"]}
    nr = {_race_token(x) for x in c["not_race"]}
    exact, any_true = True, False
    for cl in classes:
        for rc in races:
            for fa in _FACTIONS:
                p = _Player(cl, rc, fa)
                real = _applies_raw(raw, p)
                any_true = any_true or real
                flat = ((not pc or cl in pc) and cl not in nc and (not pr or rc in pr) and rc not in nr
                        and (c["faction"] is None or fa == c["faction"])
                        and all((not _lit_true(o[1:], p)) if o[0] == "!" else _lit_true(o, p) for o in other))
                if flat != real:
                    exact = False
    if not exact:
        c["exact"] = False
        c["dnf"] = alts if alts is not None else [[raw]]
    if not any_true:
        c["never"] = True
    return c


def parse_cond(raw):
    """`<<` condition text -> {"raw","class","not_class","race","not_race","faction"[, "other","exact","dnf","never"]}.

    Flat lists use RXP's names as written ("Warrior", "Undead", "Skyborne"); positives in one list are
    alternatives (OR), negatives are all required (AND), categories combine with AND. When that flat form
    is not exactly equivalent to RXP's applies() semantics, "exact": False and "dnf" (OR of AND-lists of
    literals, '!' = not) carry the full expression. "never": True when no class/race/faction combination
    satisfies it under Forever (game FOREVER, season 0), e.g. `step << skip`.
    """
    if raw is None:
        return None
    raw = raw.strip(WS)
    if not raw:
        return None
    c = _COND_CACHE.get(raw)
    if c is None:
        c = _COND_CACHE[raw] = _build_cond(raw)
    return _copy_cond(c)


def _copy_cond(c):
    if c is None:
        return None
    out = {}
    for k, v in c.items():
        if k == "dnf":
            out[k] = [list(x) for x in v]
        elif isinstance(v, list):
            out[k] = list(v)
        else:
            out[k] = v
    return out


def and_cond(a, b):
    """Both conditions must hold. Accepts dicts, raw strings or None; returns a cond dict or None."""
    ra = a.get("raw") if isinstance(a, dict) else a
    rb = b.get("raw") if isinstance(b, dict) else b
    if not ra:
        return parse_cond(rb) if rb else None
    if not rb or ra == rb:
        return parse_cond(ra)
    da, db = _alternatives(ra), _alternatives(rb)
    if da is None or db is None:
        return parse_cond("(%s) (%s)" % (ra, rb))
    if any(not x for x in da):
        return parse_cond(rb)
    if any(not x for x in db):
        return parse_cond(ra)
    alts = []
    for x in da:
        for y in db:
            lits = []
            for lit in x + y:
                if lit not in lits:
                    lits.append(lit)
            alt = " ".join(lits)
            if alt not in alts:
                alts.append(alt)
    return parse_cond("/".join(alts))
