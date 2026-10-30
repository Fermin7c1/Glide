#!/usr/bin/env python3
"""Which RestedXP steps a player sees, and so RestedXP's step numbers (R18).

Guides spell dungeons 'Mara', 'Ulda', 'Gnomer', 'Stockades' beside 'MARA', 'ULDA', 'GNOMER'; RestedXP's
`.dungeon` reads them through GetDungeonName (strupper + an alias table, 'STOCKADES' -> 'STOCKS') and keys its
settings by that tag. The parser kept the spelling, so a dungeon selected as 'MARA' never matched a 'Mara' step
(6,519 disagreements on the real guides with any dungeon on). The parser now stores the tag.

Two copies of RestedXP's step rule (the live coach's route/steps.py and the build's sources/live_logs.py) with two
numbering loops and three settings vocabularies are now one: rxp_parser.step_shown / visible_steps /
rxp_numbering over RxpSettings. They differed for Dungeon-group guides (the coach turned every dungeon and group
quests on there, the build none): both now follow RestedXP's DungeonCheck, the guide's own `.enabledungeon`
lines and `#disabledungeons` header over the player's selection. Checked step by step on a table of flags, then
as numberings on the fixture corpus and, when it is around, the real data/kb.db."""
import dataclasses
import json
import os
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "kb"))
from knowledge import rxp_parser as P  # noqa: E402
from knowledge import store  # noqa: E402
from knowledge.analyzer import route as R  # noqa: E402
from knowledge.analyzer.route import steps as S  # noqa: E402
from knowledge.sources import live_logs as LL  # noqa: E402

# ---------------------------------------------------------------- dungeon tags at parse time
for name, tag in (("Mara", "MARA"), ("MARA", "MARA"), ("Ulda", "ULDA"), ("Gnomer", "GNOMER"),
                  ("Stockades", "STOCKS"), ("stocks", "STOCKS"), ("Deadmines", "DM"), ("VC", "DM"),
                  ("Temple of Atal'Hakkar", "ST"), (" rfc ", "RFC")):
    assert P.dungeon_tag(name) == tag, (name, P.dungeon_tag(name), tag)
assert P.dungeon_tag("Nowhere") is None and P.dungeon_tag(None) is None

TEXT = """#name 1-2 Testland
#group Test Guide (A)
step
    .dungeon Stockades
step
    .dungeon !Mara
step
    .dungeon Nowhere
step
    .enabledungeon !rfc
    .enabledungeon Gnomer
"""
g = P.parse_guide(TEXT)
st = g["steps"]
assert st[0]["flags"] == {"dungeon": "STOCKS"}, st[0]["flags"]
assert st[1]["flags"] == {"dungeonskip": "MARA"}, st[1]["flags"]
assert st[2]["flags"] == {}, "an unknown dungeon sets no flag (RestedXP reports a parse error)"
assert any("invalid dungeon name" in w for w in g["warnings"]), g["warnings"]
en = [a["args"] for a in st[3]["actions"]]
assert [(a["dungeon"], a["enabled"]) for a in en] == [("RFC", False), ("GNOMER", True)], en
assert not any(a.get("unknown_verb") for a in en), "enabledungeon is a RestedXP verb"

# ---------------------------------------------------------------- one settings vocabulary
RS = P.RxpSettings
assert RS.from_profile(LL.DEFAULT_SETTINGS) == RS() == RS.from_options({}) == RS.from_options(None), \
    "live_logs' defaults, Glide's unset options and RxpSettings() are all RestedXP's own defaults"
prof = {"soloSelfFound": True, "enableGroupQuests": True, "hardcore": True, "season": 0, "phase": 4, "xprate": 1.5,
        "dungeons": {"MARA": True, "STOCKS": True, "RFC": False}, "enableBetaFeatures": True}
glide = {"ssf": True, "group_quests": True, "hardcore": True, "season": 0, "phase": 4, "xprate": 1.5,
         "dungeons": ["Mara", "Stockades"]}
assert RS.from_profile(prof) == dataclasses.replace(RS.from_options(glide), beta=True)    # Glide has no beta option
assert RS.from_options(glide).dungeons == frozenset({"MARA", "STOCKS"})
assert RS.from_options({"dungeons": True}).dungeons == "all" == RS.from_options({"dungeons": "all"}).dungeons
assert RS.from_options({"dungeons": False}).dungeons == frozenset()
page_x = {"ssf": True, "group_quests": False}                       # companion/state.py rxp_settings()
assert RS.from_options(page_x) == RS(ssf=True)
assert tuple(LL.SETTING_KEYS) == P.PROFILE_KEYS and tuple(R.norm_player({}).keys())[3:] == \
    ("hardcore", "season", "phase", "xprate", "ssf", "group_quests", "dungeons")
# settings_key (the caches' key) is derived from the same normalisation and keeps unset apart from off
assert R.settings_key({"group_quests": False}) != R.settings_key({})
assert R.settings_key({"dungeons": ["Stockades"]}) == R.settings_key({"dungeons": ["STOCKS"]})
assert R.settings_key({"dungeons": True}) == R.settings_key({"dungeons": "all"})

# ---------------------------------------------------------------- the rule, flag by flag
OFF, ON = RS(), RS(ssf=True, group_quests=True, hardcore=True)
SOME = RS(dungeons=frozenset({"MARA", "STOCKS"}))
ALL = RS(dungeons="all")
NONE_GD = P.GuideDungeons()
DISABLED = P.GuideDungeons(disabled=True)
OVR = P.GuideDungeons(disabled=True, override=(("RFC", True), ("MARA", False)))
TABLE = [
    # (flags, settings, guide dungeons, shown)
    ({}, OFF, NONE_GD, True),
    ({"disabled": True}, ALL, NONE_GD, False),
    ({"beta": True}, OFF, NONE_GD, False), ({"beta": True}, RS(beta=True), NONE_GD, True),
    ({"som": True}, OFF, NONE_GD, False), ({"som": True}, RS(season=1), NONE_GD, True),
    ({"era": True}, OFF, NONE_GD, True), ({"era": True}, RS(season=1), NONE_GD, False),
    ({"era/som": True}, RS(season=1, phase=3), NONE_GD, False), ({"era/som": True}, RS(season=1, phase=2), NONE_GD, True),
    ({"season": "0,1"}, OFF, NONE_GD, True), ({"season": "2"}, OFF, NONE_GD, False),
    ({"phase": "1-3"}, OFF, NONE_GD, False), ({"phase": "4-6"}, OFF, NONE_GD, True), ({"phase": "5"}, OFF, NONE_GD, True),
    ({"phase": "6"}, RS(phase=5), NONE_GD, False),
    ({"xprate": "<1.49"}, OFF, NONE_GD, True), ({"xprate": ">1.59"}, OFF, NONE_GD, False),
    ({"xprate": ">1.09"}, RS(xprate=1.5), NONE_GD, True), ({"xprate": "1.2-1.6"}, OFF, NONE_GD, False),
    ({"hardcoreserver": True}, OFF, NONE_GD, False), ({"hardcoreserver": True}, RS(hardcore_server=True), NONE_GD, True),
    ({"softcoreserver": True}, OFF, NONE_GD, True), ({"softcoreserver": True}, RS(hardcore_server=True), NONE_GD, False),
    ({"hardcore": True}, OFF, NONE_GD, False), ({"hardcore": True}, ON, NONE_GD, True),
    ({"softcore": True}, OFF, NONE_GD, True), ({"softcore": True}, ON, NONE_GD, False),
    ({"softcore": "false"}, ON, NONE_GD, False),      # Lua truth: a "false" string is a set tag in RestedXP
    ({"ssf": True}, OFF, NONE_GD, False), ({"ssf": True}, ON, NONE_GD, True),
    ({"ah": True}, OFF, NONE_GD, True), ({"ah": True}, ON, NONE_GD, False),
    ({"group": True}, OFF, NONE_GD, False), ({"group": True}, ON, NONE_GD, True),
    ({"solo": True}, OFF, NONE_GD, True), ({"solo": True}, ON, NONE_GD, False),
    ({"questguide": True}, ALL, NONE_GD, False),
    # dungeons: tags as the parser stores them, a selection in any spelling
    ({"dungeon": "MARA"}, OFF, NONE_GD, False), ({"dungeon": "MARA"}, SOME, NONE_GD, True),
    ({"dungeon": "STOCKS"}, RS.from_options({"dungeons": ["Stockades"]}), NONE_GD, True),
    ({"dungeon": "ULDA"}, RS.from_options({"dungeons": ["ulda"]}), NONE_GD, True),
    ({"dungeon": "ULDA"}, SOME, NONE_GD, False), ({"dungeon": "ZF"}, ALL, NONE_GD, True),
    ({"dungeonskip": "MARA"}, OFF, NONE_GD, True), ({"dungeonskip": "MARA"}, SOME, NONE_GD, False),
    ({"dungeonskip": "STOCKS"}, ALL, NONE_GD, False),
    ({"dungeon": "MARA", "dungeonskip": "MARA"}, SOME, NONE_GD, False),     # RestedXP: dungeon == dungeonskip hides
    ({"dungeon": "MARA", "dungeonskip": "WC"}, SOME, NONE_GD, True),
    # the guide's own rules: #disabledungeons over the selection, .enabledungeon over both
    ({"dungeon": "MARA"}, ALL, DISABLED, False), ({"dungeonskip": "MARA"}, ALL, DISABLED, True),
    ({"dungeon": "RFC"}, OFF, OVR, True), ({"dungeonskip": "RFC"}, OFF, OVR, False),
    ({"dungeon": "MARA"}, ALL, OVR, False), ({"dungeon": "WC"}, ALL, OVR, False),
    ({"dungeon": "WC"}, ALL, P.GuideDungeons(override=(("RFC", True),)), True),
    ({"dungeon": "RFC"}, ALL, P.GuideDungeons(override=(("RFC", True), ("RFC", False))), False),   # later line wins
]
for flags, s, gd, want in TABLE:
    got = P.step_shown(flags, s, gd)
    assert got == want, (flags, s, gd, got, want)
    # the two callers agree: the coach with Glide's names, the build with RestedXP's
    p = S.norm_player(dict({"class": "WARRIOR", "race": "Human", "faction": "Alliance"}, **{
        "ssf": s.ssf, "group_quests": s.group_quests, "hardcore": s.hardcore, "season": s.season, "phase": s.phase,
        "xprate": s.xprate, "dungeons": s.dungeons if s.dungeons == "all" else sorted(s.dungeons)}))
    pr = {"soloSelfFound": s.ssf, "enableGroupQuests": s.group_quests, "hardcore": s.hardcore, "season": s.season,
          "phase": s.phase, "xprate": s.xprate, "enableBetaFeatures": s.beta, "hardcore_server": s.hardcore_server,
          "dungeons": {t: True for t in P.DUNGEON_TAGS} if s.dungeons == "all" else {t: True for t in s.dungeons}}
    if not (s.beta or s.hardcore_server):          # the coach has no beta / Hardcore realm options (Forever has none)
        assert S.step_shown(flags, p, gd) == want, ("route", flags, s)
    assert LL.step_shown(flags, pr, gd) == want, ("live_logs", flags, s)

# the guide's rules from its text and steps, the new parse and an older kb.db's unknown-verb rows alike
GTEXT = "#name 1-2 X\n#group G\n#disabledungeons\nstep\n    .enabledungeon RFC\nstep << Horde\n    .enabledungeon BFD\n"
pg = P.parse_guide(GTEXT)
gd = P.guide_dungeons(dict(pg, text=GTEXT), cls="WARRIOR", race="Human", faction="Alliance")
assert gd == P.GuideDungeons(True, (("RFC", True),)), gd
old_rows = [{"cond": None, "actions": [{"verb": "enabledungeon", "args": {"raw": "!Gnomer", "unknown_verb": True},
                                        "cond": None}]}]
assert P.guide_dungeons({"text": "#name X\n", "steps": old_rows}) == P.GuideDungeons(False, (("GNOMER", False),))
assert not P.guide_dungeons({"text": "#disabledungeons << Horde\nstep\n", "steps": []}, faction="Alliance").disabled
print("step rule: ok (%d cases)" % len(TABLE))


# ---------------------------------------------------------------- both callers number alike
PLAYERS = [("WARRIOR", "Dwarf", "Alliance"), ("WARLOCK", "Gnome", "Alliance"), ("WARRIOR", "Human", "Alliance"),
           ("SHAMAN", "Orc", "Horde")]
VARIANTS = [({"ssf": True, "group_quests": True}, {"soloSelfFound": True, "enableGroupQuests": True}),
            ({}, {}),
            ({"dungeons": True, "hardcore": True}, {"dungeons": {t: True for t in P.DUNGEON_TAGS}, "hardcore": True}),
            ({"dungeons": ["Mara", "rfk", "Stockades"], "group_quests": True},
             {"dungeons": {"MARA": True, "RFK": True, "STOCKS": True}, "enableGroupQuests": True})]


def same_numbering(kb_path, keys, players=PLAYERS, variants=VARIANTS):
    """Route (live coach) and StepMaps (build step timing) give every guide the same numbering; returns the
    number of (guide, player, settings) compared."""
    R.clear_caches()
    kb = store.open_ro(kb_path)
    conn = sqlite3.connect("file:%s?mode=ro" % kb_path.replace("\\", "/"), uri=True)
    maps = LL.StepMaps(conn)
    n = 0
    try:
        for key in keys:
            for cls, race, fac in players:
                for glide, prof in variants:
                    route = R.Route(kb, key, dict(glide, **{"class": cls, "race": race, "faction": fac}))
                    m = maps.index_map(key, cls, race, fac, prof)
                    assert route.rxp_to_idx == m, (key, cls, race, glide, route.rxp_to_idx, m)
                    n += 1
    finally:
        conn.close()
    return n


import fixture  # noqa: E402

tmp = tempfile.mkdtemp(prefix="glide-rxpvis-")
try:
    kb_path, _summary, _root, _run = fixture.build(tmp)
    c = sqlite3.connect(kb_path)
    keys = [r[0] for r in c.execute("SELECT key FROM rxp_guide ORDER BY key")]
    dlm = [json.loads(f or "{}") for (f,) in c.execute("SELECT flags FROM rxp_step WHERE guide_key=?", (fixture.DLM,))]
    c.close()
    assert sorted(keys) == sorted(fixture.GUIDE_FILES), keys
    assert any(f.get("dungeon") == "RFK" for f in dlm), "the fixture's dungeon step"
    n = same_numbering(kb_path, keys)
    # the dungeon guide's RFK step: RestedXP shows it only when RFK is selected (no .enabledungeon for it)
    kb = store.open_ro(kb_path)
    p = {"class": "WARRIOR", "race": "Dwarf", "faction": "Alliance"}
    assert len(R.Route(kb, fixture.DLM, dict(p, dungeons=["RFK"])).steps) == \
        len(R.Route(kb, fixture.DLM, p).steps) + 1
    print("fixture corpus: the coach and the build number alike (%d guide/player/settings)" % n)
finally:
    R.clear_caches()
    shutil.rmtree(tmp, ignore_errors=True)

# stored briefs: a kb.db built before R18 numbered Dungeon-group guides with group quests and every dungeon on
LEGACY = {"group_quests": True, "dungeons": True}
for meta, dungeon_brief in ((None, LEGACY), ({"hardcore": False}, LEGACY), (R.BRIEF_RXP, None)):
    tmp = tempfile.mkdtemp(prefix="glide-rxpvis-")
    try:
        path = os.path.join(tmp, "kb.db")
        c = store.open_rw(path)
        store.create_schema(c)
        R.ensure_schema(c)
        if meta is not None:
            store.set_meta(c, R.BRIEF_RXP_META, meta)
        c.commit()
        c.close()
        R.clear_caches()
        kb = store.open_ro(path)
        dg, pg_ = {"grp": "RestedXP Forever Dungeon Guide (H)"}, {"grp": "RestedXP Forever Guide (H)"}
        want = dict(meta or {"hardcore": False}, **(dungeon_brief or {}))
        assert R.brief_settings(kb, dg) == want, (meta, R.brief_settings(kb, dg))
        assert R.brief_settings(kb, pg_) == (meta or {"hardcore": False}), (meta, R.brief_settings(kb, pg_))
    finally:
        R.clear_caches()
        shutil.rmtree(tmp, ignore_errors=True)

real = os.path.join(ROOT, "data", "kb.db")
skipped = None
if not os.path.exists(real):
    skipped = "the real-KB numbering check (no data/kb.db); the table and fixture parts passed"
else:
    c = sqlite3.connect("file:%s?mode=ro" % real.replace("\\", "/"), uri=True)
    keys = [r[0] for r in c.execute("SELECT key FROM rxp_guide ORDER BY key")]
    c.close()
    if not keys:
        skipped = "the real-KB numbering check (data/kb.db has no RestedXP guides)"
    else:
        n = same_numbering(real, keys, players=[("WARRIOR", "Human", "Alliance"), ("SHAMAN", "Orc", "Horde")],
                           variants=[VARIANTS[0], VARIANTS[3]])
        print("real kb.db: the coach and the build number alike (%d guide/player/settings)" % n)
print("rxp visibility: ok")
if skipped:
    print("SKIP: " + skipped)
    raise SystemExit(77)
