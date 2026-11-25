#!/usr/bin/env python3
"""WoWSims' own runner (wowsimcli, the release the engine downloads) on Glide's requests (companion/simbuild.py) with
WoWSims' item database (companion/item_db.py): it takes the worn items' enchants and random suffixes as simbuild sends
them (a suffix it doesn't know fails the whole sim, so simbuild leaves one out; a suffix the link carries negative is
sent without its sign); and EP from weights measured on the same runner (simbuild.stat_weights) screens stat-only items
the way the sim ranks them: three rings come out in the sim's order, and each ring's simmed gain, a suffix's and an
enchant's are within 10% of what EP says.

Needs a local WoWSims: GLIDE_WOWSIMS_HOME (an engine home whose data/wowsims/<tag>/ has the runner, with the Arcane
rotation and the item database kept under files/), else %TEMP%/glide-wowsims-home; without one it skips (exit 77).
Small sims, on 4 threads (GOMAXPROCS): about 20 runs of ITERATIONS.

Run: python3 tests/test_bis_cli.py   (prints OK; -v prints the numbers)
"""
import json
import os
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import item_db  # noqa: E402
import sim_weights  # noqa: E402
import simbuild  # noqa: E402
import wowsims  # noqa: E402

ITERATIONS, SEED = 3000, 7
TOLERANCE = 0.10
VERBOSE = "-v" in sys.argv
APL = simbuild.CLASSES["MAGE"]["apl"]["Arcane"]
DB_FILES = ("assets/database/db.json", "assets/database/leftover_db.json")
for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
          "TAVILY_API_KEY", "BRAVE_API_KEY"):
    os.environ[k] = ""
os.environ["GOMAXPROCS"] = "4"                       # (the runner inherits it: a few cores, not all of them)


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


def say(*a):
    if VERBOSE:
        print(*a)


def local_release():
    """(engine home, tag) of a local WoWSims with the runner, the Arcane rotation and db.json kept, or None."""
    home = os.environ.get("GLIDE_WOWSIMS_HOME") or os.path.join(tempfile.gettempdir(), "glide-wowsims-home")
    base = os.path.join(home, "data", "wowsims")
    try:
        tags = sorted((t for t in os.listdir(base) if wowsims.TAG.match(t)), reverse=True)
    except OSError:
        return None
    for tag in tags:
        need = [wowsims.EXE] + [os.path.join("files", *p.split("/")) for p in (APL, DB_FILES[0])]
        if all(os.path.isfile(os.path.join(base, tag, p)) for p in need):
            return home, tag
    return None


found = local_release()
if not found:
    print("SKIP: no local WoWSims runner with its Arcane rotation and item database (GLIDE_WOWSIMS_HOME)")
    raise SystemExit(77)
HOME, TAG = found
WS = wowsims.WoWSims(HOME)
kept = os.path.join(HOME, "data", "wowsims", TAG, "files")


def kept_file(path):
    try:
        with open(os.path.join(kept, *path.split("/")), "rb") as fh:
            return fh.read()
    except OSError:
        return None


DB = item_db.ItemDB.load(kept_file(DB_FILES[0]), kept_file(DB_FILES[1]), TAG)
ROT = json.loads(kept_file(APL).decode("utf-8"))
_cache = {}


def sim(req):
    """The runner's DPS for a request (each distinct request run once)."""
    k = json.dumps(req, sort_keys=True)
    if k not in _cache:
        _cache[k] = WS.sim(req, TAG)
    return _cache[k]


def worn_with(slot, item=None, **ids):
    """The smoke gear (WoWSims' Arcane preset) with one inventory slot changed: an item id (with "enchant",
    "suffix" as parse_item has them), or None for nothing worn there."""
    worn = [{"key": str(s), "id": i, "stats": {}} for s, i in sim_weights.SMOKE_GEAR if s != slot]
    if item:
        worn.append(dict({"key": str(slot), "id": item, "stats": {}}, **ids))
    return worn


def char(worn):
    return {"name": "Glide", "class": "MAGE", "race": "Human", "faction": "Alliance",
            "talents": sim_weights.talent_list(sim_weights.SMOKE_TALENTS), "worn": worn}


def dps(worn, db=DB):
    return sim(simbuild.request(char(worn), ROT, ITERATIONS, SEED, None, db))


# ---- weights measured on the runner, on one seed ------------------------------------------------------------------
weights = simbuild.stat_weights("MAGE", sim, lambda bonus: simbuild.request(char(worn_with(None)), ROT, ITERATIONS,
                                                                           SEED, bonus, DB))
W, PER = weights["w"], weights["per_ref"]
say("weights", weights)
check(weights["dps"] > 0 and PER > 0 and W["SP"] == 1.0 and W["IN"] > 0, weights)
sim_weights.smoke(WS, TAG)                  # (the request a new WoWSims must run, an enchant and a suffix in it)


def ep_dps(stats):
    """What EP says stats are worth, in DPS."""
    return sum(W.get(code, 0) * v for code, v in stats.items()) * PER


def close(simmed, said, what):
    say("%-40s simmed %+7.2f  EP %+7.2f" % (what, simmed, said))
    check(abs(simmed - said) <= TOLERANCE * abs(simmed), "%s: simmed %+.2f DPS, EP says %+.2f" % (what, simmed, said))


# ---- an enchant: taken (Arcanum of Focus on the head), worth what EP says -------------------------------------------
HEAD = dict(sim_weights.SMOKE_GEAR)[1]
focus = DB.enchant(2544, 1)
check(focus and focus["stats"] == {"SP": 8}, focus)
bare = dps(worn_with(1, HEAD))
enchanted_worn = worn_with(1, HEAD, enchant=2544)
check(simbuild.request(char(enchanted_worn), ROT, db=DB)["raid"]["parties"][0]["players"][0]["equipment"]["items"][0]
      == {"id": HEAD, "enchant": 2544}, "sent")
close(dps(enchanted_worn) - bare, ep_dps(focus["stats"]), "enchant Arcanum of Focus")
check(dps(worn_with(1, HEAD, enchant=99999)) == bare, "an enchant WoWSims doesn't know: simmed without it")


# ---- a random suffix: taken, by its id or without its sign; one it doesn't know left out ---------------------------
def suffix_cloak():
    """A cloak a mage wears with a spell-damage suffix among its options: (item, suffix), the most points first."""
    best = None
    for i, rec in DB.items.items():
        if rec.get("type") != 4 or rec.get("phase") == item_db.PHASE_LATER or not DB.eligible(i, {"class": "MAGE"}):
            continue
        for sid in rec.get("randomSuffixOptions") or ():
            stats = DB.suffix_stats(i, sid)
            if set(stats) == {"SP"} and (best is None or stats["SP"] > best[2]):
                best = (i, sid, stats["SP"])
    return best


cloak, sid, sp = suffix_cloak() or (None, None, 0)
check(cloak and sp >= 10, "a cloak with a spell-damage suffix")
plain = dps(worn_with(15, cloak))
with_suffix = dps(worn_with(15, cloak, suffix=sid))
close(with_suffix - plain, ep_dps(DB.suffix_stats(cloak, sid)), "suffix %d on %s" % (sid, DB.name(cloak)))
check(dps(worn_with(15, cloak, suffix=-sid)) == with_suffix, "a negative suffix: found without its sign")
check(dps(worn_with(15, cloak, suffix=99999)) == plain, "a suffix WoWSims doesn't know: left out, the sim runs")
raw = simbuild.request(char(worn_with(15, cloak)), ROT, ITERATIONS, SEED)
raw["raid"]["parties"][0]["players"][0]["equipment"]["items"][3]["randomSuffix"] = 99999
try:
    WS.sim(raw, TAG)
    check(False, "the runner refuses a suffix it doesn't know (why simbuild leaves one out)")
except simbuild.SimError as e:
    check("99999" in str(e), e)


# ---- three stat-only rings: the sim's order is EP's, each gain within 10% of EP's ---------------------------------
def stat_only_rings():
    """Rings a level-60 Alliance mage can have now with stats EP weighs and nothing else: no effect, set, random
    suffix, hit (the cap bends it), or stats against a kind of mob or in an area; not the unique ring worn. ->
    [(EP in DPS, id)], the best first."""
    worn = dict(sim_weights.SMOKE_GEAR)[11]
    out = []
    for i, rec in DB.items.items():
        sc = (rec.get("scalingOptions") or {}).get("0") or {}
        if (rec.get("type") != item_db.FINGER or i == worn or rec.get("phase") == item_db.PHASE_LATER or
                rec.get("itemEffects") or rec.get("setId") or rec.get("randomSuffixOptions") or
                sc.get("mobTypeStats") or sc.get("areaStats") or rec.get("mobTypeStats") or rec.get("areaStats") or
                not DB.eligible(i, {"class": "MAGE", "level": 60, "faction": "Alliance"})):
            continue
        stats = DB.stats(i)
        if not stats or any(code not in W and code not in ("SA", "AR") for code in stats) or \
                stats.get("HT") or stats.get("HS"):
            continue
        out.append((ep_dps(stats), i))
    return sorted(out, reverse=True)


picked = []
for ep, ring in stat_only_rings():
    if not picked or ep <= 0.8 * picked[-1][0]:          # (far enough apart that the order is the sim's to keep)
        picked.append((ep, ring))
    if len(picked) == 3:
        break
check(len(picked) == 3 and picked[-1][0] > 2, picked)
empty = dps(worn_with(12))
gains = {ring: dps(worn_with(12, ring)) - empty for _, ring in picked}
for ep, ring in picked:
    close(gains[ring], ep, "ring %s %s" % (DB.name(ring), DB.stats(ring)))
check([ring for _, ring in picked] == sorted(gains, key=gains.get, reverse=True),
      "the sim's order: %s" % [(DB.name(r), round(gains[r], 2)) for _, r in picked])
say("%d runs on WoWSims %s" % (len(_cache), TAG))
print("OK")
