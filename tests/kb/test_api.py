"""knowledge/api.py tests (no API calls): the knowledge block on a synthetic live state, every tool through
dispatch() (good and bad arguments, with and without a guide), the FACTS verifier, the guide-key resolver, the
fuzzy search, block trimming, decay_info and the companion's turnin_decay_soon trigger, and the degrade path
without kb.db.

Run: python3 tests/kb/test_api.py   (prints OK)
"""
import json
import os
import sys
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
sys.path.append(os.path.join(ROOT, "companion"))   # after ROOT: `knowledge` stays the package

from knowledge import api  # noqa: E402
from knowledge import store  # noqa: E402

KB_PATH = os.path.join(ROOT, "data", "kb.db")
DM_KEY = "RestedXP Forever Guide (A)||5-11 Dun Morogh"
FLAGS = {"coach": True, "route": True, "combat": True, "buffs": True, "gear": True, "pace": True, "upkeep": True,
         "voice": True}


def summary(**over):
    """Warrior level 7 on RestedXP step 5 of 5-11 Dun Morogh, in Kharanos, fighting a Crag Boar."""
    s = {"name": "Testwarrior", "class": "WARRIOR", "race": "Dwarf", "faction": "Alliance", "level": 7, "xp": 2000,
         "xp_max": 4500, "rested": 300, "hp": 80, "power": 10, "power_type": "rage", "combat": True, "dead": 0,
         "map": 1426, "x": 46.5, "y": 53.0, "zone": "Dun Morogh", "subzone": "Kharanos",
         "guide": "05-11 Dun Morogh", "guide_group": "RestedXP Forever Guide (A)", "step": 5,
         "steps": [{"index": 5, "text": "0/1 Sit near the campfire",
                    "elements": [{"tag": "goto", "map": 1426, "x": 46.7, "y": 53.9},
                                 {"tag": "complete", "quest": 96608, "done": False}]}],
         "quests": {96608: {"done": False, "obj": [(0, 1)]}, 384: {"done": True, "obj": []}},
         "target": {"npc": "1125", "name": "Crag Boar", "level": 6, "hp": 70, "reaction": 2, "dead": False},
         "coach_flags": dict(FLAGS), "xp_per_hour": 9000, "ttl_seconds": 1000, "session_minutes": 30,
         "on_step_seconds": 40, "buffs": [], "bind": "Thunderbrew Distillery", "hearth_cd": 0, "kills": 12}
    s.update(over)
    return s


def test_resolver(pack):
    assert pack.guide_key("05-11 Dun Morogh", "RestedXP Forever Guide (A)", "Alliance") == DM_KEY
    assert pack.guide_key("5-11 Dun Morogh", "RestedXP Forever Guide (A)", "Alliance") == DM_KEY
    assert pack.guide_key("05-11 Dun Morogh", None, "Alliance") == DM_KEY            # non-dungeon group preferred
    assert pack.guide_key("05-11 Dun Morogh", "RestedXP Forever Dungeon Guide (A)", "Alliance") == \
        "RestedXP Forever Dungeon Guide (A)||5-11 Dun Morogh"
    assert pack.guide_key(DM_KEY) == DM_KEY                                            # a key resolves to itself
    assert pack.guide_key("99-99 Nowhere", "RestedXP Forever Guide (A)") is None
    assert api.resolve_guide_key(KB_PATH, "05-11 Dun Morogh", "RestedXP Forever Guide (A)") == DM_KEY
    ctx = pack.context(summary())
    assert ctx.guide_key == DM_KEY and ctx.rxp_step_no == 5 and ctx.error is None, (ctx.guide_key, ctx.error)
    assert ctx.route.idx_to_rxp[ctx.idx] == 5
    # the S0 page wins when the number points at a step without its quests: step 2 (a grind) shown while S0 lists
    # The Great Outdoors -> the nearest step touching it (step 4 accepts it)
    ctx2 = pack.context(summary(step=2, steps=[{"index": 2, "elements": [{"tag": "complete", "quest": 96608}]}]))
    assert ctx2.rxp_step_no == 4, ctx2.rxp_step_no
    # loose guide names from the model pick the class's own guide (no Mage AoE guide for a warrior)
    assert pack.guide_loose(ctx, "Elwynn Forest") == "RestedXP Forever Guide (A)||6-11 Elwynn Forest"
    print("resolver OK")


def test_block(pack):
    ctx = pack.context(summary(), triggers=["step_changed"])
    t0 = time.time()
    b = pack.block(ctx, ["step_changed"])
    cold = time.time() - t0
    tok = api.approx_tokens(b)
    assert tok <= api.BLOCK_TOKENS, tok
    st = b["step"]
    assert st["kind"] == "complete" and st["n"] == 5 and "why" in st and isinstance(st["why"], list), st
    q = st["quests"][0]
    assert q["name"] == "The Great Outdoors" and q["fact"] == "q:96608" and q["xp_at_level"] == 410, q
    assert {"role", "decay_at_level", "turnin_step"} <= set(q), q
    assert st["exp_level"] < st["level_now"] and st.get("live"), st         # 7.44 vs the plan's 5.72
    assert b["next"]["n"] == 6 and b["next"]["kind"] == "turnin", b["next"]
    pace = b["pace"]
    assert pace["benchmark_xph"] > 0 and pace["xp_to_level"] == 2500 and pace["eta_next_level_s"] > 0, pace
    assert pace["pending_turnin_xp"] > 0, pace                              # Beer Basted Boar Ribs is complete
    assert len(b["heuristics"]) >= 1 and all(h["id"].startswith("h:") and h["advice"] for h in b["heuristics"])
    assert all(len(h.get("rationale") or "") <= 140 for h in b["heuristics"])
    tgt = b["target"]
    assert tgt["name"] == "Crag Boar" and tgt["fact"] == "c:1125" and tgt["rank"] == "normal" and tgt["level"] == 6
    assert tgt["kill_xp"] > 0, tgt
    assert {"q:96608", "c:1125"} <= set(b["facts"]) and any(f.startswith("s:") for f in b["facts"])
    ok, bad = pack.verify_facts(b["facts"])
    assert not bad, bad                                                      # every id in the block verifies
    t0 = time.time()
    pack.block(ctx, ["step_changed"])
    assert time.time() - t0 < max(0.05, cold), "the step part is cached"
    # dashboard panel: the last block's compact brief and pace, plus why_text for the chips
    p = pack.panel(summary())
    assert p["why"]["kind"] == "complete" and p["why"]["plan"]["exp_level"] == st["exp_level"] and p["pace"], p
    assert "why_text" in p["why"] and p["why"]["next"]["n"] == 6 and not p.get("stale"), p
    assert pack.panel(summary(step=9))["stale"] is True
    # muted combat commentary drops combat heuristics
    muted = pack.context(summary(coach_flags=dict(FLAGS, combat=False)), triggers=["step_changed"])
    hm = pack.block(muted, ["step_changed"]).get("heuristics") or []
    assert all("warrior." not in h["id"] for h in hm), hm
    # a friendly target gets no card; no guide -> a note, still pace and heuristics
    assert "target" not in pack.block(pack.context(summary(target={"npc": "1698", "name": "Innkeeper Belm",
                                                                       "level": 30, "reaction": 5})))
    nb = pack.block(pack.context(summary(guide=None, guide_group=None)))
    assert "step" not in nb and nb.get("note") and nb.get("pace"), nb
    assert pack.panel(summary(guide=None))["why"] == {"unavailable": nb["note"]}
    print("block OK (%d tokens, %.3f s cold)" % (tok, cold))


def test_fit_block(pack):
    b = pack.block(pack.context(summary()), ["step_changed"])
    small = api.fit_block(b, 120)
    assert api.approx_tokens(small) <= 120 or small.get("truncated"), api.approx_tokens(small)
    assert small["step"]["kind"] == "complete" and "why" in small["step"], small
    assert set(small["facts"]) <= set(b["facts"])
    r = api.trim_result({"rows": [{"text": "x" * 500, "fact": "q:1"} for _ in range(60)], "facts": ["q:1"]}, 300)
    assert api.approx_tokens(r) <= 300 and r["truncated"] and r["facts"] == ["q:1"], api.approx_tokens(r)
    print("fit OK")


GOOD = {
    "quest_info": {"name": "Beer Basted Boar Ribs"}, "creature_info": {"name": "crag boars"},
    "step_brief": {"rxp_step_no": 8}, "why_this_step": {}, "skip_impact": {"name": "Beer Basted Boar Ribs"},
    "what_if": {"variant": "grind_here", "minutes": 10}, "alternatives_here": {"radius": 500},
    "eta": {"target_level": 9}, "kill_xp": {"mob_level": 7}, "quest_xp": {"quest": "384", "at_level": 12},
    "path_time": {"target": "Ironforge"}, "walk_route": {"target": "Kharanos"}, "route_lookahead": {"n": 4}, "guide_summary": {},
    "heuristics": {"topic": "hearth"}, "community": {"query": "xp"}, "glossary": {"term": "deathskip"}, "session_review": {},
    "param": {"key": "xp.kill"}, "search": {"text": "crag boar"}, "recent_log": {"n": 3},
}
BAD = [{}, {"id": "zzz", "name": 12345, "mob_level": "abc", "target_level": None, "variant": "bogus", "n": -5,
            "radius": "far", "quest": [], "key": {}, "text": "", "term": "", "target": "", "rxp_step_no": 99999,
            "guide": "No Such Guide", "at_level": "x", "elite": "maybe", "topic": 7, "tags": "combat"},
       "not json", None, {"variant": {"do_dungeon": "Gnomeregan"}}, {"variant": "hold_turnin"}]


def test_tools(pack):
    names = api.tool_names()
    assert names == ["quest_info", "creature_info", "step_brief", "why_this_step", "skip_impact", "what_if",
                     "alternatives_here", "eta", "kill_xp", "quest_xp", "path_time", "walk_route", "route_lookahead",
                     "guide_summary", "heuristics", "community", "glossary", "session_review", "param", "search",
                     "recent_log"]
    defs = api.tool_definitions()
    assert len(defs) == len(names) and all(d["input_schema"]["type"] == "object" and len(d["description"]) > 40
                                           for d in defs)
    assert len(json.dumps(defs)) < 16000                         # ~4k tokens of tool definitions at most
    recent = [{"t": time.time() - 20, "type": "message", "kind": "callout", "text": "Sit at the campfire."}]
    ctx = pack.context(summary(), recent=recent, book={"deaths_session": 1, "visits": [
        {"guide": "05-11 Dun Morogh", "step": 4, "t0": time.time() - 200, "t1": time.time() - 60}]})
    empty = pack.context({})                                     # no live state at all
    noguide = pack.context(summary(guide=None, guide_group=None))
    results = {}
    for n in names:
        r = api.dispatch(n, GOOD[n], ctx)
        assert isinstance(r, dict), (n, r)
        assert not r.get("error") and not r.get("unavailable"), (n, r)
        assert isinstance(r.get("facts"), list), (n, r)
        assert api.approx_tokens(r) <= api.RESULT_TOKENS, (n, api.approx_tokens(r))
        results[n] = r
        for bad in BAD:
            for c in (ctx, empty, noguide):
                rb = api.dispatch(n, bad, c)
                assert isinstance(rb, dict), (n, bad, rb)
    assert api.dispatch("no_such_tool", {}, ctx)["error"].startswith("unknown tool")
    assert api.dispatch("kill_xp", {"mob_level": 7}, None) == {"unavailable": "no live context"}
    # a few facts in the good results
    assert results["kill_xp"]["xp"] == 80 and results["kill_xp"]["status"]                  # 5*7+45 at level 7
    assert results["quest_info"]["fact"] == "q:384" and results["quest_info"]["xp_full"] == 625
    assert results["creature_info"]["fact"] == "c:1125"
    assert results["step_brief"]["n"] == 8 and results["step_brief"]["quests"]
    assert results["why_this_step"]["current"] and results["why_this_step"]["n"] == 5
    assert results["skip_impact"]["delta"]["time_s"] < 0 and results["skip_impact"]["lost_quests"]
    assert results["what_if"]["delta"]["time_s"] > 0                                       # grinding costs time
    assert results["recent_log"]["entries"][0]["text"] == "Sit at the campfire."
    assert results["session_review"]["live"]["deaths_session"] == 1
    assert results["glossary"]["fact"] == "g:deathskip"
    assert api.dispatch("quest_info", {"id_or_name": "q:384"}, ctx)["fact"] == "q:384"       # alias + id form
    assert api.dispatch("path_time", {"target": "step 40"}, ctx)["dist_yd"] > 0
    assert api.dispatch("path_time", {"target": "50.1,49.5"}, ctx)["dist_yd"] > 0
    assert api.dispatch("what_if", {"variant": "do_dungeon", "dungeon": "Gnomeregan"}, ctx)["dungeon_guides"]
    assert api.dispatch("step_brief", {"rxp_step_no": 3, "guide": "Elwynn Forest"}, ctx)["guide"] == \
        "6-11 Elwynn Forest"
    print("tools OK (%d tools)" % len(names))


def test_search_and_facts(pack):
    ctx = pack.context(summary())
    res = api.dispatch("search", {"text": "crag boar"}, ctx)["results"]
    assert res[0]["kind"] == "creature" and res[0]["name"] == "Crag Boar" and res[0]["fact"] == "c:1125", res
    assert len(res) <= 10
    ok, bad = pack.verify_facts(["q:96608", "c:1125", "i:2886", "p:xp.kill.base_mul", "h:route.stack-objectives",
                                 "poi:flight:6", "s:5-11 Dun Morogh/6", "s:%s/6" % DM_KEY, "g:deathskip",
                                 "q:99999999", "c:0", "p:no.such.param", "h:no-such-rule", "s:5-11 Dun Morogh/99999",
                                 "poi:flight:999999", "bogus", "q:abc"])
    assert ok == ["q:96608", "c:1125", "i:2886", "p:xp.kill.base_mul", "h:route.stack-objectives", "poi:flight:6",
                  "s:5-11 Dun Morogh/6", "s:%s/6" % DM_KEY, "g:deathskip"], ok
    assert bad == ["q:99999999", "c:0", "p:no.such.param", "h:no-such-rule", "s:5-11 Dun Morogh/99999",
                   "poi:flight:999999", "bogus", "q:abc"], bad
    assert api.verify_facts(KB_PATH, ["q:1x"]) == ([], ["q:1x"])
    print("search + verifier OK")


def test_decay_and_rules(pack):
    # The Great Outdoors: quest level 6, full XP until level 11, reduced from 12
    d = pack.decay_info(96608, 11)
    assert d["decay_at_level"] == 12 and d["xp_after_level_up"] < d["xp_now"] and d["fact"] == "q:96608", d
    assert pack.decay_info(96608, 7)["xp_after_level_up"] == pack.decay_info(96608, 7)["xp_now"]
    assert pack.decay_info(99999999, 7) is None and pack.decay_info(None, 7) is None
    from rules import Rules
    from state import GameState
    r = Rules({})
    r.kb_fn = lambda: pack

    def kb_triggers(**s):
        out = []
        r._kb_triggers(dict({"level": 11, "xp": 7800, "xp_max": 8800, "quests": {96608: {"done": True, "obj": []}}},
                            **s), out)
        return [t for t in out if t.kind == "turnin_decay_soon"]
    t = kb_triggers()
    assert len(t) == 1 and t[0].priority == 2, t
    f = t[0].facts
    assert f["quest"] == "The Great Outdoors" and f["xp_after_level_up"] < f["xp_now"] and f["fact"] == "q:96608", f
    assert kb_triggers() == []                                  # 10-minute cooldown per quest
    r.decay_told.clear()
    assert kb_triggers(xp=5000) == []                           # not within 15% of levelling
    assert kb_triggers(level=7, xp=5300, xp_max=5400) == []     # decays at 12, not at the next level (8)
    assert kb_triggers(quests={96608: {"done": False, "obj": [(0, 1)]}}) == []
    r.kb_fn = None
    assert kb_triggers() == []                                  # silent without the knowledge base
    # wired into Rules.check() with a real GameState
    r2 = Rules({})
    r2.kb_fn = lambda: pack
    st = GameState()
    core = {"t": "1000", "l": "11", "x": "7800", "X": "8800", "cl": "WARRIOR", "st": "5", "qn": "1", "qh": "6087", "an": "0"}
    st.update(core, {"pg": "Q0", "q": "96608:1:"})
    trs = r2.check(st, [])
    assert [x.kind for x in trs if x.kind == "turnin_decay_soon"] == ["turnin_decay_soon"], trs
    print("decay + rules OK")


def test_missing_kb():
    pack = api.KnowledgePack(os.path.join(ROOT, "data", "no-such-kb.db"))
    assert not pack.ok and "build-kb" in pack.reason
    ctx = pack.context(summary())
    assert pack.block(ctx) == {"unavailable": pack.reason}
    assert pack.panel(summary()) == {"why": None, "pace": None}
    assert pack.decay_info(96608, 11) is None and pack.status()["ok"] is False
    assert pack.verify_facts(["q:96608"]) == ([], ["q:96608"])
    for n in api.tool_names():
        r = api.dispatch(n, GOOD[n], ctx)
        assert isinstance(r, dict) and (r.get("unavailable") or r.get("error") or n in ("recent_log", "glossary")), \
            (n, r)
    none = api.KnowledgePack(None)
    assert not none.ok and none.block(none.context({})) == {"unavailable": none.reason}
    print("missing kb OK")


if __name__ == "__main__":
    if not os.path.exists(KB_PATH) or not store.open_ro(KB_PATH).one("SELECT key FROM rxp_guide WHERE key=?", DM_KEY):
        test_missing_kb()
        print("SKIP: needs data/kb.db with %s (the missing-KB part passed)" % DM_KEY)
        raise SystemExit(77)
    P = api.KnowledgePack(store.open_ro(KB_PATH))
    assert P.ok, P.reason
    P.warm()
    st = P.status()
    assert st["guides"] > 100 and st["briefs"] > 1000, st
    test_resolver(P)
    test_block(P)
    test_fit_block(P)
    test_tools(P)
    test_search_and_facts(P)
    test_decay_and_rules(P)
    test_missing_kb()
    print("OK")
