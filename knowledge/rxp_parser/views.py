"""Player-specific views of a parsed guide: the step tags that apply, which steps RestedXP shows under the
player's RestedXP settings, and RestedXP's live step numbering.

One rule for everyone: the live coach (analyzer/route/steps.py Route) and the build's step timing
(sources/live_logs.py StepMaps) both number steps with visible_steps() / rxp_numbering() below."""
import re
from dataclasses import dataclass, field

from knowledge.rxp_parser.conditions import cond_applies
from knowledge.rxp_parser.maps import dungeon_tag
from knowledge.values import as_float


def step_flags(step, **player):
    """Effective step flags for a player (cls=, race=, faction=, ...): RXP keeps, per tag, the first
    occurrence whose `<<` condition applies."""
    f = step.get("flags") or {}
    cf = f.get("cond_flags") or ()
    out = {k: v for k, v in f.items() if k != "cond_flags"}
    for t in {x["tag"] for x in cf}:
        out.pop(t, None)
    for x in cf:
        if x["tag"] not in out and cond_applies(x["cond"], **player):
            out[x["tag"]] = x["value"]
    return out


def rxp_index_map(guide, **player):
    """RXP numbers only the steps whose `step << cond` applies: returns {rxp_step_number: idx}. This is the
    numbering before the player's RestedXP settings; rxp_numbering() also leaves out the steps they hide."""
    out, n = {}, 0
    for st in guide["steps"]:
        if cond_applies(st.get("cond"), **player):
            n += 1
            out[n] = st["idx"]
    return out


# ------------------------------------------------------------------------------------------------
# RestedXP settings
# ------------------------------------------------------------------------------------------------

def _dungeon_set(v):
    """A dungeon selection in any of its forms -> "all" or a frozenset of RestedXP tags. None / False / empty:
    none; True / "all": every dungeon (the desktop app's on/off switch); a dict (RestedXP's profile, tag -> on)
    or a list of names as anyone spells them ('Stockades', 'rfc')."""
    if v is None or v is False:
        return frozenset()
    if v is True or v == "all":
        return "all"
    if isinstance(v, str):
        v = [v]
    if isinstance(v, dict):
        v = [k for k, on in v.items() if on]
    return frozenset(dungeon_tag(str(d)) or str(d).strip().upper() for d in v)


@dataclass(frozen=True)
class RxpSettings:
    """The RestedXP options that decide which steps it shows. The defaults are RestedXP's own (its AceDB profile
    defaults: all off, season 0, phase 6, XP rate 1), what a RestedXP nobody has configured numbers by.

    One vocabulary, two spellings in: from_profile() takes RestedXP's names (the SavedVariables RXPSettings
    profile: soloSelfFound, enableGroupQuests, ...), from_options() Glide's (config.json "rxp", the addon's page
    X as companion/state.py rxp_settings() reads it, player dicts: ssf, group_quests, ...)."""
    ssf: bool = False
    group_quests: bool = False
    hardcore: bool = False
    season: float = 0.0
    phase: float = 6.0
    xprate: float = 1.0
    dungeons: object = field(default=frozenset())        # "all" or a frozenset of RestedXP dungeon tags
    beta: bool = False
    hardcore_server: bool = False                        # a Hardcore realm (Forever has none)

    @classmethod
    def from_profile(cls, profile):
        """RestedXP's names, as its SavedVariables profile has them. AceDB saves only values that differ from
        the defaults, so a missing key is the default."""
        if isinstance(profile, cls):
            return profile
        p = profile or {}
        return cls(ssf=bool(p.get("soloSelfFound")), group_quests=bool(p.get("enableGroupQuests")),
                   hardcore=bool(p.get("hardcore")), season=as_float(p.get("season"), 0.0),
                   phase=as_float(p.get("phase"), 6.0), xprate=as_float(p.get("xprate"), 1.0),
                   dungeons=_dungeon_set(p.get("dungeons")), beta=bool(p.get("enableBetaFeatures")),
                   hardcore_server=bool(p.get("hardcore_server")))

    @classmethod
    def from_options(cls, options):
        """Glide's names (OPTION_KEYS); a missing or None value is the default (0 / "" phase and XP rate too)."""
        if isinstance(options, cls):
            return options
        o = options or {}
        return cls(ssf=bool(o.get("ssf")), group_quests=bool(o.get("group_quests")),
                   hardcore=bool(o.get("hardcore")), season=as_float(o.get("season") or 0, 0.0),
                   phase=as_float(o.get("phase") or 6, 6.0), xprate=as_float(o.get("xprate") or 1, 1.0),
                   dungeons=_dungeon_set(o.get("dungeons")))

    def key(self):
        """Hashable, for caches keyed on the settings."""
        d = self.dungeons if self.dungeons == "all" else tuple(sorted(self.dungeons))
        return (self.ssf, self.group_quests, self.hardcore, self.season, self.phase, self.xprate, d, self.beta,
                self.hardcore_server)

    def dungeon_on(self, tag):
        return self.dungeons == "all" or tag in self.dungeons


# Glide's names for the options a player dict / config.json "rxp" / page X may carry (from_options)
OPTION_KEYS = ("ssf", "group_quests", "hardcore", "dungeons", "season", "phase", "xprate")
# RestedXP's names in its SavedVariables profile (from_profile)
PROFILE_KEYS = ("soloSelfFound", "hardcore", "enableGroupQuests", "season", "xprate", "phase", "dungeons",
                "enableBetaFeatures")


# ------------------------------------------------------------------------------------------------
# the guide's own dungeon rules
# ------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class GuideDungeons:
    """A guide's say over its dungeon steps (RestedXP DungeonCheck, IsDungeonEnabled): `.enabledungeon [!]X`
    turns dungeon X on (off) whatever the player selected; else a `#disabledungeons` header turns it off; else
    the player's selection decides."""
    disabled: bool = False
    override: tuple = ()                    # ((tag, on), ...) in guide order; a later line wins

    def enabled(self, tag, settings):
        on = None
        for t, v in self.override:
            if t == tag:
                on = v
        if on is not None:
            return on
        return False if self.disabled else settings.dungeon_on(tag)


NO_GUIDE_DUNGEONS = GuideDungeons()
_RE_FIRST_STEP = re.compile(r"(?m)^\s*step\b")
_RE_HDR_TAG = re.compile(r"^\s*#(\S+)[^<]*(?:<<\s*(.*?)\s*)?$")


def guide_dungeons(guide, applies=None, **player):
    """GuideDungeons of a guide for a player. guide: {"text": the raw guide text (its header is read), "steps":
    [{"cond", "actions": [{"verb", "args", "cond"}]}]}. RestedXP keys the overrides by the guide's group, set as
    it parses that group's guides; here a guide carries its own `.enabledungeon` lines (on the real guides every
    dungeon a guide overrides is overridden in that same guide). An older kb.db has the lines as unknown verbs
    (args raw only): read the same way."""
    ok = applies or (lambda c: cond_applies(c, **player))
    disabled = False
    for line in _RE_FIRST_STEP.split(guide.get("text") or "", maxsplit=1)[0].splitlines():
        m = _RE_HDR_TAG.match(line.split("--", 1)[0])
        if m and m.group(1) == "disabledungeons" and (not m.group(2) or ok(m.group(2))):
            disabled = True
    override = []
    for st in guide.get("steps") or ():
        acts = [a for a in st.get("actions") or () if a.get("verb") == "enabledungeon"]
        if not acts or not ok(st.get("cond")):
            continue
        for a in acts:
            g = a.get("args") or {}
            raw = str(g.get("raw") or "").strip()
            tag = g.get("dungeon") if "enabled" in g else dungeon_tag(raw.lstrip("!"))
            if tag and not g.get("unknown") and ok(a.get("cond")):
                override.append((tag, bool(g["enabled"]) if "enabled" in g else not raw.startswith("!")))
    return GuideDungeons(disabled, tuple(override))


# ------------------------------------------------------------------------------------------------
# RestedXP's stepLogic
# ------------------------------------------------------------------------------------------------
def _on(v):
    """Lua truth: RestedXP tests a tag with `if step.tag`, so any value but nil / false is on (even "false")."""
    return v is not None and v is not False


def _given(v):
    return v is not None and v is not True and v != ""


_RE_XPRATE = re.compile(r"^([<>]?)\s*(\d+\.?\d*)-?(\d*\.?\d*)")
_RE_RANGE = re.compile(r"(\d+)-(\d+)")


def step_shown(flags, settings=None, dungeons=None):
    """RestedXP's addon.IsStepShown (RXPGuides.lua addon.stepLogic; on Forever no loremaster mode and no fresh
    accounts) for one step's effective flags (step_flags) under RxpSettings (or a dict of Glide's names) and the
    guide's GuideDungeons. RestedXP Forever leaves the steps it hides out of the guide: they take no number."""
    f = flags or {}
    s = settings if isinstance(settings, RxpSettings) else RxpSettings.from_options(settings)
    gd = dungeons or NO_GUIDE_DUNGEONS
    if (_on(f.get("softcoreserver")) and s.hardcore_server) or \
            (_on(f.get("hardcoreserver")) and not s.hardcore_server):
        return False
    if (_on(f.get("softcore")) and s.hardcore) or (_on(f.get("hardcore")) and not s.hardcore):     # HardcoreCheck
        return False
    if (_on(f.get("ssf")) and not s.ssf) or (_on(f.get("ah")) and s.ssf):                          # AHCheck
        return False
    if (_on(f.get("group")) and not s.group_quests) or (_on(f.get("solo")) and s.group_quests):    # GroupCheck
        return False
    som = s.season == 1                                                                            # SeasonCheck
    if (som and _on(f.get("era"))) or (_on(f.get("som")) and not som) or \
            (som and s.phase > 2 and _on(f.get("era/som"))):
        return False
    if _given(f.get("season")):
        if not any(as_float(x, None) == s.season for x in re.split(r"[,;\s]+", str(f["season"])) if x):
            return False
    if _given(f.get("xprate")):                                                                    # XpRateCheck
        m = _RE_XPRATE.match(str(f["xprate"]))
        if m:
            op, a1, a2 = m.groups()
            if op == "<":
                lo, hi = 0.0, float(a1) - 1e-4
            elif op == ">":
                lo, hi = float(a1) + 1e-4, 0xfff
            else:
                lo, hi = as_float(a1, 1.0), as_float(a2, 0xfff)
            if s.xprate < lo or s.xprate > hi:
                return False
    if _given(f.get("phase")):                                                                     # PhaseCheck
        m = _RE_RANGE.match(str(f["phase"]))
        lo, hi = (float(m.group(1)), float(m.group(2))) if m else (as_float(f["phase"], None), 0xffff)
        if lo is None or not lo <= s.phase <= hi:
            return False
    if _on(f.get("disabled")):                                                                     # DungeonCheck
        return False
    d, dskip = f.get("dungeon"), f.get("dungeonskip")
    if _on(dskip) and gd.enabled(dskip, s):
        return False
    if _on(d) and not (d != dskip and gd.enabled(d, s)):
        return False
    if _on(f.get("beta")) and not s.beta:                                                          # BetaVersionCheck
        return False
    if _on(f.get("questguide")):                         # LoremasterCheck: no loremaster mode below gameVersion 50000
        return False
    return True


def visible_steps(guide, settings=None, applies=None, **player):
    """[(step, effective flags, shown)] for every step whose `step << cond` applies to the player (RestedXP
    parses only those), in guide order; `shown` per step_shown under the settings and the guide's own dungeon
    rules. guide: {"text", "steps": [{"idx", "cond", "flags"[, "actions"]}]}. applies: an optional (cached)
    cond -> bool for this player; the default evaluates cond_applies."""
    ok = applies or (lambda c: cond_applies(c, **player))
    s = settings if isinstance(settings, RxpSettings) else RxpSettings.from_options(settings)
    gd = guide_dungeons(guide, applies=ok, **player)
    out = []
    for st in guide["steps"]:
        cond = st.get("cond")
        if (cond and cond.get("never")) or not ok(cond):
            continue
        fl = st.get("flags") or {}
        flags = step_flags(st, **player) if fl.get("cond_flags") else dict(fl)
        out.append((st, flags, step_shown(flags, s, gd)))
    return out


def rxp_numbering(guide, settings=None, applies=None, **player):
    """RestedXP's live step number -> idx for a player under their RestedXP settings: {n: idx}."""
    out, n = {}, 0
    for st, _flags, shown in visible_steps(guide, settings, applies=applies, **player):
        if shown:
            n += 1
            out[n] = st["idx"]
    return out
