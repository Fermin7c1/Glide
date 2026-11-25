"""How much play a best-in-slot item costs: the chance one try gets it, how long a try takes, and how the coach says
it. Pure (no files, no clock unless one isn't given): pursuit.Planner ranks moves with it, the rules and the offline
coach word them with it. Every number comes with its source ("src"), as the plan's rule 1 has it:

    src         what it is                                               conf   said as
    "obs"       the player's own kills, runs, gold or turn-ins           0.95   "by your own kills" (or runs)
    "steps"     the route analyzer's step times                          0.6    "by my route times"
    "classic"   AtlasLootClassic's drop chance (atlasloot_rates)         0.5    "that's classic's chance"
    "ah"        an auction price the player saw (it swings)              0.5    "the auction house had it for 45g 3 days ago"
    "web"       a web page (research's "r:" job), never verified         0.3    "web, unconfirmed"
    "prior"     Glide's own guess (a run 45 minutes, 10 gold an hour)    0.2    "my guess"
    "guess"     an unknown drop chance taken as 1 in 10 (GUESS_Q)        0.1    "that's my guess, nobody lists its chance"
A mix is "<prior's src>+obs" (the player's own count is in, but carries less than the prior, or the kills are under
OBS_KILLS): it is said as the prior's, "with your kills counted". A chance names its source after its last "; " (the
offline coach reads it back from there). Gates and gaps: "unknown" (nothing known), "calendar" (raids not open yet),
"lockout" (the raid's week is used), "pvp" (not estimated), "noprice" (a price nobody knows: research's "v:" job),
"nostanding" (a reputation not read yet), "crafter" (someone else crafts it), "nomats" (the reagents unknown).

The chance per try (drop_chance): a Beta posterior over the player's own deduplicated kills (kill_obs merges the loot
windows of one corpse) with AtlasLoot's classic chance as the prior worth K_CLASSIC kills, else a web rate worth K_WEB,
else unknown (q None: the planner may use GUESS_Q, labelled, and rank never puts it ahead of a known chance with the
same or a larger gain). Times the chance of winning the roll (win_chance: 1/(1 + likely needers), from WoWSims'
classAllowlist, the armor type and the stats, the group kind; a bind-on-equip item in a pick-up group: everyone rolls).
The time per try (try_hours): a dungeon's run (run_minutes: the player's median shrunk toward analyzer.dungeon.run_s
with analyzer.calib.prior_h, as the analyzer's calibration does) plus forming a group (pursuit.form_minutes; half as
long again for 10 players) plus travel, never under 1/ENTRIES_PER_HOUR (Forever's 5 dungeon entries an hour); a raid
not before RAID_OPENS and once a lockout; quests by the analyzer's step times; a vendor, a craft or the auction house by
the gold missing over gold per hour (gold_per_hour: `g` changes, idle gaps and big spends left out); a reputation by
the reputation missing (as of the last reload) over reputation per hour (rep_per_hour, learned from turn-ins).

Odds are said from the geometric distribution (quantiles): "about 1 in 8, usually 3 to 10 runs", the 90% count only
when asked, never a bare mean. A count is the try by which that share of players has the item, the share read to the
whole percent (12.93%: 10 runs reach 74.96%, "3 to 10"). Player text is plain and short, no em dashes; a number below
0.9 conf names its source.
"""
import datetime
import math
import os
import statistics
import sys
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if ROOT not in sys.path:
    sys.path.append(ROOT)       # the knowledge package: its values module (stdlib only) reads the numbers given
from knowledge.values import as_float  # noqa: E402
from log import log_once  # noqa: E402

CONF = {"obs": 0.95, "client": 0.9, "ws": 0.8, "kb": 0.6, "steps": 0.6, "classic": 0.5, "ah": 0.5, "web": 0.3,
        "prior": 0.2, "guess": 0.1}
KNOWN = ("obs", "classic", "web")       # a drop chance some source gave (not a guess)
K_CLASSIC = 20                          # AtlasLoot's classic chance is worth this many of the player's kills
K_WEB = 5                               # a web rate (or the guess, once kills come in) this many
OBS_KILLS = 20                          # kills before a chance is said as the player's own (whatever the prior)
GUESS_Q = 0.1                           # an unknown boss epic's chance, labelled as a guess
SURE_Q = 0.95                           # this likely or more: "every run"
ROUND_SHARE = 0.005                     # a count's share read to the whole percent (quantiles)
KILL_MERGE_S = 300                      # loot windows of one creature this close are one kill (research.KILL_DEDUP_S)

RUN_PRIOR_S = 2700.0                    # analyzer.dungeon.run_s: one dungeon run, entrance to last boss (unverified)
PRIOR_H = 3.0                           # analyzer.calib.prior_h: hours of the player's own runs the prior is worth
MIN_RUN_S, MAX_RUN_S = 300, 4 * 3600    # a visit shorter (zoned in and out) or longer (away inside) isn't a run
FORM_MIN = 20                           # pursuit.form_minutes' default: finding a group (a guess)
BIG_GROUP, BIG_FORM = 10, 1.5           # a group this big (10-player UBRS) takes half as long again to form
ENTRIES_PER_HOUR = 5                    # Forever's dungeon entries an hour (official, Nov 20)
DUNGEON_SIZE, RAID_SIZE = 5, 10
UBRS = "upper blackrock spire"          # item_db's name for the 10-player half of Blackrock Spire
RAID_OPENS = datetime.date(2026, 12, 9)  # Forever's raids open (official); then one roll a week
RAID_RUN_MIN, RAID_FORM_MIN = 180, 30   # a raid night and forming one: guesses until the player has raided
QUEST_PRIOR_S = 900                     # a quest step the analyzer has no time for (a guess)

IDLE_GAP_S = 300                        # gold samples further apart: the time between isn't play
BIG_SPEND_C = 250000                    # a single spend this big (a mount, training) isn't the farming rate
MIN_ACTIVE_S = 900                      # play needed before a gold rate is said
GOLD_PRIOR_CPH = 100000                 # copper an hour before one is learned (10g: the player's 9.5 at level 45)
REP_GAP_S = 3600                        # reputation samples further apart: not one stretch of the same grind
MIN_REP_S = 900

# a standing's start, cumulative from Neutral as the game's barValue is (GetFactionInfo); 1 Hated .. 8 Exalted
REP_THRESHOLDS = {1: -42000, 2: -6000, 3: -3000, 4: 0, 5: 3000, 6: 9000, 7: 21000, 8: 42000}
REP_LEVELS = {"hated": 1, "hostile": 2, "unfriendly": 3, "neutral": 4, "friendly": 5, "honored": 6, "revered": 7,
              "exalted": 8}
UNITS = {"runs": "run", "kills": "kill", "weeks": "week", "tries": "try"}
CRAFTERS = {"Alchemy": "an alchemist", "Blacksmithing": "a blacksmith", "Enchanting": "an enchanter",
            "Engineering": "an engineer", "Leatherworking": "a leatherworker", "Tailoring": "a tailor",
            "Jewelcrafting": "a jewelcrafter", "Inscription": "a scribe", "Cooking": "a cook"}

# who in a group may want an item (WoWSims' Class ids, the game's): the classes at 60 by the armor they wear and the
# kind of stats they use. ItemType 1-10 is armor worn by kind, but a cloak (4) and a neck (2) anyone wears.
CLASS_IDS = (1, 2, 3, 4, 5, 7, 8, 9, 11)      # warrior paladin hunter rogue priest shaman mage warlock druid
ARMOR_WEARERS = {1: {5, 8, 9}, 2: {4, 11}, 3: {3, 7}, 4: {1, 2}}
ARMOR_ITEM_TYPES = (1, 3, 5, 6, 7, 8, 9, 10)
CASTERS = {2, 5, 7, 8, 9, 11}
FIGHTERS = {1, 2, 3, 4, 7, 11}
# the stats that make an item a caster's or a fighter's: WoWSims' Stat indexes (item_db.STAT_CODE) and Glide's codes
CASTER_STATS = {3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 31,
                "IN", "SP", "HL", "AC", "FI", "FR", "HO", "NA", "SH", "PEN", "HS", "CS", "MR", "SI"}
FIGHTER_STATS = {0, 1, 14, 15, 17, 18, 22, 25, 26, "ST", "AG", "AP", "RA", "HT", "CR", "DF", "DG", "PA"}
UNKNOWN_SHARE = 1 / 3                   # an item nothing tells about: a third of the group may want it


# ---------------------------------------------------------------------------------------------------- sources
def base(src):
    """The source a src stands on: "classic+obs" -> "classic"."""
    return str(src or "unknown").split("+")[0]


def conf(src):
    """How sure a number with this src is (CONF; a mix: its prior's); 0 for a gate or a gap."""
    return CONF.get(base(src), 0.0)


def known(src):
    """Whether a drop chance with this src came from somewhere (the player's kills, classic, the web): not a guess."""
    return base(src) in KNOWN


# ---------------------------------------------------------------------------------------------------- chance per try
def _rate(given, npc, item):
    """A chance (0..1) from a number (a percent above 1 is read as one) or a table {npc: {item: chance}} (as
    atlasloot_rates.read gives); None when there is none."""
    r = given
    if isinstance(given, dict):
        import atlasloot_rates
        r = atlasloot_rates.rate(given, npc, item)
    if isinstance(r, bool) or not isinstance(r, (int, float)):
        return None
    r = float(r)
    if 1 < r <= 100:
        r /= 100.0
    return r if 0 < r <= 1 else None


def _counts(obs):
    try:
        kills = max(0, int((obs or {}).get("kills") or 0))
        got = min(kills, max(0, int((obs or {}).get("got") or 0)))
    except (TypeError, ValueError, AttributeError):
        return 0, 0
    return kills, got


def drop_chance(npc, item, obs=None, prior=None, web=None):
    """(q, n_eff, src): the chance one kill of npc gives item. obs {"kills": n, "got": k}: the player's own kills of
    it, deduplicated (kill_obs), and how many dropped it. prior: AtlasLoot's classic chance (a number, or the table
    atlasloot_rates.read gives), worth K_CLASSIC kills; else web: a web rate (number or table), worth K_WEB; else, with
    kills seen, GUESS_Q worth K_WEB ("guess"); else (None, 0, "unknown"). q is the Beta posterior's mean
    (q0*k + got) / (k + kills), n_eff its k + kills. src is the prior's while the kills are fewer than it is worth or
    than OBS_KILLS ("classic+obs" once any are in: a few kills don't make the number the player's own), "obs" from
    there on."""
    kills, got = _counts(obs)
    p0, w0 = _rate(prior, npc, item), _rate(web, npc, item)
    if p0 is not None:
        q0, k, src = p0, K_CLASSIC, "classic"
    elif w0 is not None:
        q0, k, src = w0, K_WEB, "web"
    elif kills:
        q0, k, src = GUESS_Q, K_WEB, "guess"
    else:
        return None, 0, "unknown"
    q = (q0 * k + got) / (k + kills)
    if kills >= max(k, OBS_KILLS):
        src = "obs"
    elif kills:
        src += "+obs"
    return round(q, 4), k + kills, src


def kill_obs(kills, npc, item, merge_s=0):
    """{"kills": n, "got": k} for drop_chance from kills ([{"npc", "t", "loot": [item ids]}]) of npc, and how many
    held item. recorder_data.kills' rows are one per corpse already (its corpses() merged the windows one corpse
    makes): each counts once, rows at the same second as one. Raw loot windows (one per opening of a corpse) want
    merge_s=KILL_MERGE_S: the windows within merge_s of a kill's first window are that kill."""
    try:
        npc_id, item_id = int(npc), int(item)
    except (TypeError, ValueError):
        return {"kills": 0, "got": 0}
    rows = []
    for k in kills or ():
        try:
            if isinstance(k, dict) and int(k.get("npc") or 0) == npc_id:
                rows.append((float(k.get("t") or 0), {int(i) for i in k.get("loot") or () if str(i).isdigit()}))
        except (TypeError, ValueError):
            continue
    rows.sort(key=lambda r: r[0])
    n = got = 0
    start, has = None, False
    for t, loot in rows:
        if start is None or t - start > merge_s:
            if start is not None:
                got += has
            n, start, has = n + 1, t, False
        has = has or item_id in loot
    if start is not None:
        got += has
    return {"kills": n, "got": got}


def likely_needers(item):
    """The classes (WoWSims Class ids) that may want the item: its classAllowlist; else armor worn by kind (ItemType
    1-10 but neck and cloak) goes to the classes wearing that armor at 60, and caster or fighter stats (WoWSims'
    {Stat index: value}, or Glide's {code: value} in "stats") narrow it to the classes using them. None when the item
    tells nothing (not a dict)."""
    if not isinstance(item, dict):
        return None
    allow = {c for c in item.get("classAllowlist") or () if c in CLASS_IDS}
    if allow:
        return allow
    sc = ((item.get("scalingOptions") or {}).get("0") or {}).get("stats") or item.get("stats") or {}
    pairs = sc.items() if isinstance(sc, dict) else enumerate(sc) if isinstance(sc, list) else ()
    stats = {int(k) if str(k).isdigit() else str(k) for k, v in pairs if v}
    who = set(CLASS_IDS)
    if item.get("type") in ARMOR_ITEM_TYPES and item.get("armorType") in ARMOR_WEARERS:
        who = set(ARMOR_WEARERS[item["armorType"]])
    caster, fighter = bool(stats & CASTER_STATS), bool(stats & FIGHTER_STATS)
    if caster != fighter:
        narrowed = who & (CASTERS if caster else FIGHTERS)
        who = narrowed or who
    return who


def _win(p, others):
    """E[1 / (1 + X)] for X ~ Binomial(others, p): the chance of winning a roll among the others who may want it."""
    if p <= 0 or others <= 0:
        return 1.0
    if p >= 1:
        return 1.0 / (others + 1)
    return (1 - (1 - p) ** (others + 1)) / ((others + 1) * p)


def win_chance(item, group="pug", needers=None, size=DUNGEON_SIZE, boe=None):
    """The chance the player wins the item once it drops: 1/(1 + needers) when the count of other likely needers is
    given; else over the size-1 others of a group ("pug" the pursuit.group default, "guild"), each a random class of
    nine, wanting it as likely_needers says (a bind-on-equip item in a pick-up group: everyone rolls to sell it; boe
    None: the item's own "boe", or "bond" 2). "solo": 1."""
    if group == "solo":
        return 1.0
    if isinstance(needers, (set, frozenset, list, tuple)):     # the needers themselves: their count
        needers = len(needers)
    if needers is not None:
        try:
            return round(1.0 / (1 + max(0.0, float(needers))), 4)
        except (TypeError, ValueError):
            pass
    if boe is None:
        boe = isinstance(item, dict) and (bool(item.get("boe")) or item.get("bond") == 2)
    others = max(0, int(size or DUNGEON_SIZE) - 1)
    if boe and group != "guild":
        p = 1.0
    else:
        who = likely_needers(item)
        p = UNKNOWN_SHARE if who is None else len(who) / len(CLASS_IDS)
    return round(_win(p, others), 4)


# ---------------------------------------------------------------------------------------------------- odds in words
def tries_for(q, share):
    """The try by which `share` of players have an item with chance q a try (the share read to the whole percent);
    None for no chance."""
    if q is None or q <= 0:
        return None
    if q >= 1:
        return 1
    target = share - ROUND_SHARE
    if target <= 0:
        return 1
    return max(1, math.ceil(math.log(1 - target) / math.log(1 - q) - 1e-9))


def quantiles(q):
    """(p25, p75, p90) tries of the geometric distribution (tries_for); (None, None, None) for no chance. 13.84%:
    (2, 10, 16), its median 5."""
    if q is None or q <= 0:
        return None, None, None
    return tries_for(q, 0.25), tries_for(q, 0.75), tries_for(q, 0.9)


def _nice(n):
    """A 1-in-n count as a person says it: 7, 15, 40, 110."""
    if n < 20:
        return n
    return int(round(n / 5.0) * 5) if n < 100 else int(round(n / 10.0) * 10)


def _chance(q):
    if q >= 0.55:
        return "%d in 10" % min(9, round(q * 10))
    return "1 in %d" % _nice(max(2, round(1 / q)))


def _span(lo, hi, unit=None):
    one = UNITS.get(unit, unit) if unit else None
    if lo == hi:
        if lo == 1:
            return "the first %s" % one if one else "1"
        return "%d %s" % (lo, unit) if unit else "%d" % lo
    return "%d to %d %s" % (lo, hi, unit) if unit else "%d to %d" % (lo, hi)


# the source a chance names, after a "; " (offline_coach.odds_source reads it back from there); the player's own kills
# (0.95) need no "; ", nor does a chance with no source
ODDS_NOTE = {"classic": "; that's classic's chance", "classic+obs": "; that's classic's chance with your kills counted",
             "web": "; web, unconfirmed", "web+obs": "; web, unconfirmed, with your kills counted",
             "obs": ", by your own kills", "guess": "; that's my guess, nobody lists its chance",
             "guess+obs": "; that's my guess with your kills counted"}


def say_odds(q, src, unit="runs", asked=False):
    """The chance a try, as the coach says it (a clause, no capital or period): "about 1 in 8, usually 3 to 10 runs;
    that's classic's chance". One number and its range (the 25 to 75% tries), the 90% count only when asked, the
    source named below 0.9 conf, after the last "; ". unit: "runs", "kills", "weeks". No chance: "I don't know its
    drop chance yet"."""
    if q is None or q <= 0:
        return "I don't know its drop chance yet"
    one = UNITS.get(unit, unit)
    if q >= SURE_Q:
        text = "every %s" % one
    else:
        p25, p75, p90 = quantiles(q)
        text = "about %s, usually %s" % (_chance(q), _span(p25, p75, unit))
        if asked and p90 and p90 > (p75 or 0):
            text += " (9 in 10 get it by %s %d)" % (one, p90)
    return text + ODDS_NOTE.get(src, ODDS_NOTE.get(base(src), ""))


NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                "twelve")


def say_tries(n, q, unit="runs"):
    """After a try without the item: "That's run 2; it usually takes 3 to 10." Past the median, "Six runs without it
    is bad luck, not a bad plan." is added. No chance known: "That's run 2."."""
    one = UNITS.get(unit, unit)
    n = max(1, int(n or 1))
    text = "That's %s %d" % (one, n)
    p25, p75, _p90 = quantiles(q)
    if p25 is None or p75 is None or q is None or q >= SURE_Q:
        return text + "."
    text += "; it usually takes %s." % (_span(p25, p75) if p75 > 1 else "one")
    median = tries_for(q, 0.5) or 1
    if n > median:
        count = _cap(NUMBER_WORDS[n]) if n < len(NUMBER_WORDS) else "%d" % n     # a sentence starts with a word
        text += " %s %s without it is bad luck, not a bad plan." % (count, unit if n > 1 else one)
    return text


# ---------------------------------------------------------------------------------------------------- time per try
_analyzer: dict = {"mod": None, "tried": False}     # knowledge.analyzer.params, imported once


def priors():
    """{"run_s", "prior_h"}: the analyzer's parameters (knowledge/analyzer/params.py, fitted values when a build
    applied them), else RUN_PRIOR_S and PRIOR_H (no knowledge package on the path)."""
    out = {"run_s": RUN_PRIOR_S, "prior_h": PRIOR_H}
    if not _analyzer["tried"]:
        _analyzer["tried"] = True
        try:
            from knowledge.analyzer import params as AP
            _analyzer["mod"] = AP
        except Exception as e:  # noqa: BLE001 - no knowledge package here: the defaults
            log_once("effort.priors", e)
    AP = _analyzer["mod"]
    if AP is not None:
        out["run_s"] = _f(AP.get("analyzer.dungeon.run_s", RUN_PRIOR_S), RUN_PRIOR_S) or RUN_PRIOR_S
        out["prior_h"] = _f(AP.get("analyzer.calib.prior_h", PRIOR_H), PRIOR_H)
    return out


def _durations(history):
    """Run lengths in seconds from numbers (seconds), (start, end) pairs or dicts ({"s"} seconds, {"min"} minutes,
    {"t0", "t1"} or {"start", "end"})."""
    out = []
    for h in history or ():
        try:
            if isinstance(h, bool):
                continue
            if isinstance(h, (int, float)):
                s = float(h)
            elif isinstance(h, (list, tuple)) and len(h) == 2:
                s = float(h[1]) - float(h[0])
            elif isinstance(h, dict):
                if h.get("s") is not None:
                    s = float(h["s"])
                elif h.get("min") is not None:
                    s = float(h["min"]) * 60
                elif h.get("t0") is not None:
                    s = _f(h.get("t1")) - _f(h.get("t0"))
                else:
                    s = _f(h.get("end")) - _f(h.get("start"))
            else:
                continue
        except (TypeError, ValueError):
            continue
        if MIN_RUN_S <= s <= MAX_RUN_S:
            out.append(s)
    return out


def run_minutes(instance, history=None, prior_s=None, prior_h=None):
    """(minutes, src): one run of the instance (a name or id; every dungeon shares the prior for now). history: the
    player's runs of it (_durations; a visit under MIN_RUN_S or over MAX_RUN_S left out). Their median shrunk toward
    prior_s (analyzer.dungeon.run_s) with prior_h (analyzer.calib.prior_h), as the analyzer's calibration does:
    (h*median + prior_h*prior) / (h + prior_h), h the hours recorded. src "prior" with no runs, "prior+obs" while h is
    under prior_h, else "obs"."""
    p = priors() if prior_s is None or prior_h is None else {}
    prior_min = float(prior_s if prior_s is not None else p["run_s"]) / 60.0
    ph = float(prior_h if prior_h is not None else p["prior_h"])
    runs = _durations(history)
    if not runs:
        return round(prior_min, 1), "prior"
    med, h = statistics.median(runs) / 60.0, sum(runs) / 3600.0
    m = (h * med + ph * prior_min) / (h + ph) if h + ph > 0 else med
    return round(m, 1), ("obs" if h >= ph else "prior+obs")


def group_size(instance):
    """The players a run of the instance takes: 10 for Upper Blackrock Spire (Forever, Nov 17), else 5."""
    return BIG_GROUP if UBRS in str(instance or "").lower() else DUNGEON_SIZE


def form_minutes(instance=None, base_min=FORM_MIN, size=None):
    """Minutes to form a group for the instance (a guess: the pursuit.form_minutes setting); a 10-player group
    BIG_FORM times as long (30 for the default 20)."""
    size = size or group_size(instance)
    m = float(FORM_MIN if base_min is None else base_min)
    return round(m * BIG_FORM) if size >= BIG_GROUP else m


def raid_open(now=None, opens=RAID_OPENS):
    """Whether raids are open on the day of now (local time)."""
    now = time.time() if now is None else now
    return datetime.date.fromtimestamp(now) >= opens


WEAKER = ("noprice", "unknown", "guess", "prior", "web", "ah", "classic", "steps", "kb", "ws", "client", "obs")


def weakest(*srcs):
    """The least sure of these srcs (WEAKER's order; a mix counts as its prior); None ones skipped."""
    got = [s for s in srcs if s]
    if not got:
        return "unknown"
    return min(got, key=lambda s: WEAKER.index(base(s)) if base(s) in WEAKER else 0)


def _gold_hours(missing, gph, gph_src):
    """(hours, src) to make `missing` copper at gph copper an hour (unknown or not positive: GOLD_PRIOR_CPH, "prior")."""
    if missing <= 0:
        return 0.0, None
    if not isinstance(gph, (int, float)) or gph <= 0:
        return missing / GOLD_PRIOR_CPH, "prior"
    return missing / float(gph), gph_src or "obs"


def _f(v, default=0.0):
    """v as a number (not a bool), else default (knowledge.values.as_float)."""
    return as_float(v, default, bool_ok=False)


def rep_value(target):
    """A standing's starting value (REP_THRESHOLDS) from its number (1..8) or name ("Honored"); None if neither."""
    if isinstance(target, str):
        target = REP_LEVELS.get(target.strip().lower(), target)
    try:
        return REP_THRESHOLDS.get(int(target or 0))
    except (TypeError, ValueError):
        return None


def try_hours(kind, **kw):
    """(hours of play one try takes, src) for a source kind; hours None when it can't be said (src says why).

    "inst": instance, history, prior_s (run_minutes), or run_min; form_min (pursuit.form_minutes; size or the
        instance's group_size), group ("solo": no forming), travel_min. Never under 1/ENTRIES_PER_HOUR. src: the run
        time's (forming is always a guess).
    "raid": now, opens (RAID_OPENS: before it None, "calendar"), locked_until (a used lockout: None, "lockout"),
        run_min or instance and history (shrunk toward RAID_RUN_MIN), form_min (RAID_FORM_MIN), travel_min.
    "quest": steps (seconds of each unfinished step of the chain, None for one the analyzer can't time:
        QUEST_PRIOR_S; or just how many steps are left, each guessed), done (ready to turn in: the travel to whom it
        goes, src "obs"), travel_min. src "steps", or "prior" with a step guessed.
    "vendor": price (copper; None or currency: "noprice", only travel counted), price_src, gold (copper on hand; or
        money), gph and gph_src (gold_per_hour), travel_min.
    "rep": target (standing name or 1..8), value (the game's barValue, cumulative from Neutral; one under its
        standing's start is read as within it) or standing, or need (the reputation still missing) instead; rate and
        rate_src (rep_per_hour). None: "nostanding" (not read yet) or "unknown" (no rate yet); 0 hours once reached.
    "crafted": profession, skills (the player's professions, None: unknown) or have (whether the player has the
        profession), mats (copper; None: "nomats"), pattern_h (hours to get the pattern; None: known or free), gold,
        gph, gph_src; without the profession: price (an auction price) as "ah", else None, "crafter".
    "ah": price (copper, None: "noprice"), gold, gph, gph_src, travel_min. src "ah" (or the gold rate's when weaker).
    "pvp": None, "pvp"."""
    if kw.get("gold") is None and kw.get("money") is not None:
        kw["gold"] = kw["money"]
    travel_h = _f(kw.get("travel_min")) / 60.0
    if kind == "pvp":
        return None, "pvp"
    if kind == "inst":
        inst = kw.get("instance")
        if kw.get("run_min") is not None:
            run, src = _f(kw.get("run_min")), kw.get("run_src") or "prior"
        else:
            run, src = run_minutes(inst, kw.get("history"), kw.get("prior_s"), kw.get("prior_h"))
        form = 0.0 if kw.get("group") == "solo" else form_minutes(inst, kw.get("form_min", FORM_MIN), kw.get("size"))
        hours = (run + form) / 60.0 + travel_h
        return round(max(hours, 1.0 / (kw.get("entries") or ENTRIES_PER_HOUR)), 3), src
    if kind == "raid":
        now = time.time() if kw.get("now") is None else _f(kw.get("now"))
        if not raid_open(now, kw.get("opens") or RAID_OPENS):
            return None, "calendar"
        if kw.get("locked_until") and now < _f(kw.get("locked_until")):
            return None, "lockout"
        if kw.get("run_min") is not None:
            run, src = _f(kw.get("run_min")), kw.get("run_src") or "prior"
        else:
            run, src = run_minutes(kw.get("instance"), kw.get("history"), RAID_RUN_MIN * 60.0, kw.get("prior_h"))
        form = _f(kw.get("form_min"), RAID_FORM_MIN)
        return round((run + form) / 60.0 + travel_h, 3), src
    if kind == "quest":
        raw = kw.get("steps")
        steps: list = list(raw) if isinstance(raw, (list, tuple)) else []
        if isinstance(raw, int) and not isinstance(raw, bool):      # a count of steps left, each guessed
            if raw <= 0:
                return round(travel_h, 3), "obs"
            steps = [None] * raw
        if kw.get("done"):
            return round(travel_h, 3), "obs"
        total, guessed = 0.0, 0
        for s in steps or [None]:
            if isinstance(s, (int, float)) and not isinstance(s, bool) and s >= 0:
                total += s
            else:
                total, guessed = total + QUEST_PRIOR_S, guessed + 1
        return round(total / 3600.0 + travel_h, 3), ("prior" if guessed else "steps")
    if kind == "vendor":
        price = kw.get("price")
        if price is None or kw.get("currency") or isinstance(price, bool) or not isinstance(price, (int, float)):
            return round(travel_h, 3), "noprice"
        h, gsrc = _gold_hours(price - _f(kw.get("gold")), kw.get("gph"), kw.get("gph_src"))
        return round(h + travel_h, 3), weakest(kw.get("price_src") or "obs", gsrc)
    if kind == "rep":
        need = kw.get("need")
        if isinstance(need, (int, float)) and not isinstance(need, bool):
            need = float(need)
        else:
            goal = rep_value(kw.get("target"))
            got = kw.get("value")
            start = rep_value(kw.get("standing"))
            if isinstance(got, (int, float)) and not isinstance(got, bool):
                have = float(got)
                if start is not None and start > 0 and have < start:
                    have += start                   # a value within its standing, not the cumulative barValue
            elif start is not None:
                have = float(start)
            else:
                return None, "nostanding"
            if goal is None:
                return None, "unknown"
            need = goal - have
        if need <= 0:
            return 0.0, "obs"
        rate = kw.get("rate")
        if not isinstance(rate, (int, float)) or isinstance(rate, bool) or rate <= 0:
            return None, "unknown"
        return round(need / float(rate) + travel_h, 3), kw.get("rate_src") or "obs"
    if kind == "crafted":
        prof, skills = kw.get("profession"), kw.get("skills")
        can = bool(kw["have"]) if kw.get("have") is not None else bool(skills is not None and prof and prof in skills)
        if can:
            mats = kw.get("mats")
            if not isinstance(mats, (int, float)) or isinstance(mats, bool):
                return None, "nomats"
            h, gsrc = _gold_hours(mats - _f(kw.get("gold")), kw.get("gph"), kw.get("gph_src"))
            pattern = kw.get("pattern_h")
            return round(h + _f(pattern) + travel_h, 3), weakest(kw.get("mats_src") or "obs", gsrc,
                                                                   kw.get("pattern_src") if pattern else None)
        if isinstance(kw.get("price"), (int, float)) and not isinstance(kw.get("price"), bool):
            return try_hours("ah", **kw)
        return None, "crafter"
    if kind == "ah":
        price = kw.get("price")
        if not isinstance(price, (int, float)) or isinstance(price, bool):
            return None, "noprice"
        h, gsrc = _gold_hours(price - _f(kw.get("gold")), kw.get("gph"), kw.get("gph_src"))
        return round(h + travel_h, 3), weakest("ah", gsrc)
    return None, "unknown"


def value(q, d, hours, win=1.0):
    """DPS gained per hour of play from one try: q * win * d / hours (0 when any is unknown or the try can't be had)."""
    if q is None or d is None or not hours or hours <= 0:
        return 0.0
    return q * (1.0 if win is None else win) * d / hours


def rank(entries):
    """Entries ({"value", "d", "src", ...}) best first by value; one whose chance isn't known (known(src) False: a
    guess) is held back until every entry with a known chance and the same or a larger d is placed (then it goes in
    by value again, guesses in their own order)."""
    order = sorted(entries, key=lambda e: -_f(e.get("value")))
    out, held = [], []
    for i, e in enumerate(order):
        (out if known(e.get("src")) else held).append(e)
        rest = [_f(r.get("d")) for r in order[i + 1:] if known(r.get("src"))]
        still = []
        for h in held:
            (still if any(d >= _f(h.get("d")) for d in rest) else out).append(h)
        held = still
    return out


# ---------------------------------------------------------------------------------------------------- rates
def _pairs(samples, key):
    out = []
    for s in samples or ():
        try:
            if isinstance(s, dict):
                t, v = s.get("t"), s.get(key)
            else:
                t, v = s[0], s[1]
            if t is None or v is None or isinstance(v, bool):
                continue
            out.append((float(t), float(v)))
        except (TypeError, ValueError, IndexError, KeyError):
            continue
    out.sort(key=lambda p: p[0])
    return out


def gold_per_hour(samples, idle_s=IDLE_GAP_S, big_spend=BIG_SPEND_C, min_s=MIN_ACTIVE_S):
    """(copper an hour, src) from the player's money over time (samples: (t, copper) pairs or {"t", "g"} dicts, as
    the strip's `g` gives it, a frame at a time). Samples more than idle_s apart are a gap (idle, logged out, the
    engine not running): neither its time nor its change counts (mail, sales come in across it); a single spend over
    big_spend (a mount, training) is left out. Under min_s of play: (None, "unknown"). A rate under 0 is 0
    (try_hours then guesses)."""
    pts = _pairs(samples, "g")
    active = gain = 0.0
    for (t0, g0), (t1, g1) in zip(pts, pts[1:]):
        dt, dg = t1 - t0, g1 - g0
        if dt <= 0 or dt > idle_s or dg < -big_spend:
            continue
        gain += dg
        active += dt
    if active < min_s:
        return None, "unknown"
    return max(0, int(round(gain * 3600.0 / active))), "obs"


def rep_per_hour(samples, gap_s=REP_GAP_S, min_s=MIN_REP_S):
    """(reputation an hour, src) for one faction: from {"gain", "s"} stretches (a grind's rep and its seconds), or
    from (t, value) / {"t", "value"} readings (the recorder's, cumulative), counting the rise between readings at most
    gap_s apart. Under min_s or no rise: (None, "unknown") ("I don't know <faction> rep rates yet")."""
    gain = span = 0.0
    stretches = [s for s in samples or () if isinstance(s, dict) and "gain" in s]
    for s in stretches:
        g, sec = _f(s.get("gain")), _f(s.get("s"), _f(s.get("h")) * 3600.0)
        if sec > 0:
            gain, span = gain + g, span + sec
    if not stretches:
        pts = _pairs(samples, "value")
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            if 0 < t1 - t0 <= gap_s:
                gain, span = gain + max(0.0, v1 - v0), span + (t1 - t0)
    if span < min_s or gain <= 0:
        return None, "unknown"
    return int(round(gain * 3600.0 / span)), "obs"


# ---------------------------------------------------------------------------------------------------- effort in words
def say_hours(h):
    """Hours of play as said: "about 25 minutes", "about an hour", "about 1.5 hours", "about 12 hours"."""
    if h is None:
        return None
    m = h * 60.0
    if m < 1.5:
        return "a minute"
    if m < 10:
        return "about %d minutes" % round(m)
    if m < 50:
        return "about %d minutes" % (round(m / 5.0) * 5)
    if h < 1.25:
        return "about an hour"
    if h < 10:
        half = max(1.5, math.floor(h * 2 + 0.5) / 2.0)      # half up: 1.25 is 1.5, never "1 hours"
        return "about %s hours" % ("%d" % half if half.is_integer() else "%.1f" % half)
    return "about %d hours" % math.floor(h + 0.5)


def money(copper):
    """Copper as said: "45g", "3g 20s", "80s", "15c"."""
    c = int(round(_f(copper)))
    g, s = divmod(c, 10000)
    s //= 100
    if g >= 10:
        return "%dg" % round(c / 10000.0)
    if g:
        return "%dg %ds" % (g, s) if s else "%dg" % g
    return "%ds" % s if s else "%dc" % c


def ago(t, now=None):
    """How long ago t was: "just now", "an hour ago", "5 hours ago", "yesterday", "3 days ago"."""
    now = time.time() if now is None else now
    s = max(0.0, now - _f(t))
    if s < 3600:
        return "just now"
    if s < 2 * 3600:
        return "an hour ago"
    if s < 86400:
        return "%d hours ago" % (s // 3600)
    if s < 2 * 86400:
        return "yesterday"
    return "%d days ago" % (s // 86400)


def _cap(text):
    return text[:1].upper() + text[1:] if text else text


def say_effort(kind, src, hours=None, **kw):
    """What one try takes, as the coach says it (one or two short sentences; the source named below 0.9 conf). kw:
    faction (rep), profession (crafted), price and seen_t and now (ah), price_src (vendor: "web" is said so), gph_src
    (the gold rate's: anything but "obs" is said as the guessed 10g an hour), form_min (inst: forming a group, a
    guess, said apart from the player's own run times), done (quest: ready to turn in). The gates say themselves:
    "Raid gear: earliest December 9, then one roll a week.", "I don't know its price yet.", "I don't know Timbermaw
    rep rates yet; I'll learn them from your turn-ins.", "Ask a tailor or check the auction house.", "I can't estimate
    PvP rewards yet.". The odds of a drop are say_odds'."""
    b = base(src)
    when = say_hours(hours)
    if kind == "pvp" or src == "pvp":
        return "I can't estimate PvP rewards yet."
    if kind == "raid":
        if src == "calendar":
            return "Raid gear: earliest %s %d, then one roll a week." % (RAID_OPENS.strftime("%B"), RAID_OPENS.day)
        if src == "lockout":
            return "Your raid lockout is used; next week brings one more roll."
        note = {"obs": ", by your own raids", "prior+obs": "; that's your raids and my guess"}.get(
            src, "; that's my guess until you've raided")
        return "%s a raid, one roll a week%s." % (_cap(when or "a few hours"), note)
    if kind == "inst":
        if kw.get("form_min") and src == "obs":     # (the run is the player's own; finding a group is a guess)
            return "%s a run by your own runs, plus my guess for finding a group." % _cap(when or "a while")
        note = {"obs": ", by your own runs", "prior": "; that's my guess until you've run it"}.get(
            src, "; that's your runs and my guess")
        group = ", finding a group included" if kw.get("form_min") else ""     # one number: the hours
        return "%s a run%s%s." % (_cap(when or "a while"), group, note)
    if kind == "quest":
        if kw.get("done"):
            return "The quest is ready to turn in."
        if hours == 0:
            return "The quests for it are done."
        note = ", by my route times" if b == "steps" else "; that's my guess"
        return "%s of questing%s." % (_cap(when or "a while"), note)
    if kind == "rep":
        faction = str(kw.get("faction") or "").strip() or "that faction's"
        bare = faction[4:] if faction.lower().startswith("the ") else faction     # "your Argent Dawn standing"
        if src == "nostanding":
            return "I don't know your %s standing yet; it shows after a /reload." % bare
        if src == "unknown" or hours is None:
            return "I don't know %s rep rates yet; I'll learn them from your turn-ins." % faction
        if hours == 0:
            return "You have the %s standing for it, as of your last reload." % bare
        return "%s of %s rep, as of your last reload." % (_cap(when or "a while"), bare)
    if kind == "crafted" and src in ("crafter", "nomats"):
        prof = kw.get("profession")
        if src == "nomats":
            return "You can craft it; I don't know its reagents yet."
        who = CRAFTERS.get(str(prof or ""), "a %s crafter" % prof if prof else "a crafter")
        return "Ask %s or check the auction house." % who
    if src == "noprice":
        return "The auction house may have it." if kind == "ah" else "I don't know its price yet."
    # the money kinds (vendor, ah, crafted): the price or what's missing, one number at a time
    price = kw.get("price")
    price = float(price) if isinstance(price, (int, float)) and not isinstance(price, bool) else None
    farm = max(0.0, _f(hours) - _f(kw.get("travel_min")) / 60.0)
    if price is not None and kw.get("gold") is not None and _f(kw.get("gold")) >= price:
        farm = 0.0
    # (a price the web gave is said so whatever else is weaker: the gold rate's guess makes src "prior")
    web = " (web, unconfirmed)" if b == "web" or base(kw.get("price_src")) == "web" else ""
    if price is not None and kind in ("ah", "crafted"):
        seen = " %s" % ago(kw["seen_t"], kw.get("now")) if kw.get("seen_t") else ""
        head = "The auction house had it for %s%s" % (money(price), seen)
    elif price is not None and kind == "vendor":
        head = "It costs %s%s" % (money(price), web)
    elif kind == "crafted":
        head = "You can craft it"
    else:
        head = ""
    if farm <= 0:
        return head + "; you have the gold." if head else "You have the gold."
    # (the gold rate: the player's own only when its src says so; "unknown" or "prior" is try_hours' 10g guess)
    gsrc = kw.get("gph_src")
    guessed = base(gsrc) != "obs" if gsrc else b == "prior"
    rate = ", at a guessed %s an hour" % money(GOLD_PRIOR_CPH) if guessed else ", at your gold rate"
    what = "the reagents" if kind == "crafted" and price is None else "the gold"
    tail = "%s of farming for %s%s" % (say_hours(farm), what, rate)
    if kind == "vendor":                            # what's missing, not the price too: one number
        return "%s%s." % (_cap(tail), "; its price is from the web, unconfirmed" if web else "")
    return "%s; %s." % (head, tail) if head else _cap(tail) + "."
