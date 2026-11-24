#!/usr/bin/env python3
"""Where items come from (companion/research.py) against fake item data, a small knowledge base and a fake clock:
what's on screen is looked up first (reward choices and bags, then worn items, then the level band, best items
first), sources from the knowledge base and WoWSims merged (the more trusted copy of a place kept; a vendor the kb
lists keeps the reputation WoWSims gives it) and filtered by side (a kb quest marked "Both" settled by its races or
WoWSims), a drop the player saw leads at 0.95 (not a piece taken off, an item had this session or one bought; kept
TTL_S), a world drop isn't the one creature seen dropping it and an item seen from three kinds of creature is one;
whether it can be had follows from every source; facts hold for one side or both, are kept until their ttl, a new
WoWSims tag or knowledge base (or leftover_db.json coming in), or the other faction (what WoWSims said is kept while
its data can't be had), and one that depends on the side isn't kept before any character was seen; an item nothing
knows is asked again only after a day (negative cache, per side); a lookup that keeps failing is given up; the cache
survives a restart (data/research/facts.json, with the last faction); Data/Live.lua's items section (text, now/later,
eviction order, only the side playing); the coach's item_info by name, misheard name or id; switched off, nothing is
looked up. Also WoWSims.data_tag (the release in use, else the latest release's tag, looked up at most once a day),
WoWSims.prune (the data tag in use and the newest other one kept) and ItemDBSource (the item data at that tag, loaded
once, tried again later when it can't be had, leftover_db.json too).

Run: python3 tests/test_research.py   (prints OK)
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import types
import urllib.error

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import item_text  # noqa: E402
import research  # noqa: E402
import wowsims  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


# ---- fakes ---------------------------------------------------------------------------------------------------------
def ws_src(kind, **kw):
    return research.make_src(kind, "ws", **kw)


# id -> (name, ilvl, sources by faction (None: both), avail)
ITEMS = {
    16686: ("Magister's Crown", 62, [ws_src("drop", npc=1853, npc_name="Darkmaster Gandling", zone=2057,
                                            zone_name="Scholomance", instance="Scholomance", difficulty=1)], "now"),
    18405: ("Belt of the Archmage", 62, [ws_src("crafted", profession="Tailoring")], "now"),
    22403: ("Nacreous Shell Necklace", 61, [ws_src("drop", npc=10997, npc_name="Cannon Master Willey",
                                                   zone=2017, zone_name="Stratholme")], "now"),
    19147: ("Ring of Spell Power", 66, [ws_src("drop", npc=11502, npc_name="Ragnaros", zone=2717,
                                               zone_name="Molten Core")], "later"),
    13000: ("Staff of Hale Magefire", 60, [], "nosrc"),
    4000: ("Willow Robe", 20, [ws_src("vendor", npc=5110, npc_name="Deliana", zone=1537, zone_name="Ironforge")],
           "now"),
    # WoWSims' world drop the player was seen looting from one creature; a vendor that needs a reputation; an item
    # WoWSims has no source for that the knowledge base knows
    6423: ("Blackforge Greaves", 40, [ws_src("world")], "now"),
    20041: ("Highlander's Plate Girdle", 60, [ws_src("rep", npc=15127, npc_name="Samuel Hawke", zone=45,
                                                     zone_name="Arathi Highlands", rep_faction="The League of Arathor",
                                                     rep_level="Honored")], "now"),
    3000: ("Notched Shortsword", 10, [], "nosrc"),
}
HORDE = {4000: [ws_src("vendor", npc=5188, npc_name="Mokvar", zone=1637, zone_name="Orgrimmar")],
         20041: []}
QUEST_SIDE = {7100: "Horde"}                     # WoWSims' side of a quest the knowledge base marks "Both"


class FakeDB:
    def __init__(self, tag="v1.0.4"):
        self.tag, self.calls = tag, {}

    def item(self, iid):
        e = ITEMS.get(iid)
        return {"id": iid, "name": e[0], "scalingOptions": {"0": {"ilvl": e[1]}}} if e else None

    def name(self, iid):
        return ITEMS[iid][0] if iid in ITEMS else None

    def band(self, ch):
        return [i for i in ITEMS if ch["level"] >= 55 or i == 4000]

    def sources(self, iid, faction):
        if faction == "Alliance":                    # (a lookup asks each side once: one call counted per lookup)
            self.calls[iid] = self.calls.get(iid, 0) + 1
        if faction == "Horde" and iid in HORDE:
            return [dict(s) for s in HORDE[iid]]
        return [dict(s) for s in ITEMS[iid][2]] if iid in ITEMS else []

    def avail(self, iid, faction, bonding=None, sources=None):
        e = ITEMS.get(iid)
        if not e or e[3] == "later":
            return e[3] if e else "nosrc"
        srcs = self.sources(iid, faction) if sources is None else sources
        return "now" if srcs or bonding == 2 else "nosrc"

    def quest_side(self, qid):
        return QUEST_SIDE.get(qid)


class FakeKB:
    """kb.db's tables the research reads, in memory."""
    def __init__(self):
        self.c = sqlite3.connect(":memory:", check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.executescript("""
            CREATE TABLE creature(id INTEGER PRIMARY KEY, name TEXT, friendly_to TEXT);
            CREATE TABLE creature_spawn(creature_id INT, ui_map INT, x REAL, y REAL);
            CREATE TABLE creature_drop(creature_id INT, item_id INT, rate REAL, n_obs INT, src TEXT, conf REAL);
            CREATE TABLE vendor_item(npc_id INT, item_id INT, price INT, src TEXT, conf REAL);
            CREATE TABLE quest(id INTEGER PRIMARY KEY, name TEXT, side TEXT, races INT);
            CREATE TABLE quest_reward(quest_id INT, item_id INT, qty INT, choice INT, src TEXT);
            CREATE TABLE item(id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO creature VALUES (1853, 'Darkmaster Gandling', NULL), (5110, 'Deliana', 'A'),
                (5188, 'Mokvar', 'H'), (9001, 'Defias Thug', NULL), (9002, 'Defias Pillager', NULL),
                (9003, 'Defias Looter', NULL), (10363, 'General Drakkisath', NULL), (9004, 'Scorched Guardian', NULL),
                (15127, 'Samuel Hawke', 'A'), (9005, 'Kobold Vermin', NULL);
            INSERT INTO creature_spawn VALUES (5110, 1455, 1, 1), (9001, 1436, 1, 1), (9002, 1436, 1, 1),
                (9003, 1436, 1, 1), (9004, 1418, 1, 1), (15127, 1417, 1, 1), (9005, 1429, 1, 1);
            -- a kb drop row for Gandling (fg, less trusted than WoWSims) and one the recorder saw for an old item
            INSERT INTO creature_drop VALUES (1853, 16686, NULL, NULL, 'fg', 0.6), (9001, 2000, 0.1, 3, 'obs', 0.95),
                (9001, 2001, 0.01, 1, 'obs', 0.95), (9002, 2001, 0.01, 1, 'obs', 0.95),
                (9003, 2001, 0.01, 1, 'fg', 0.6), (9004, 6423, 1.0, 1, 'obs', 0.95),
                (9001, 2002, 0.1, 2, 'obs', 0.95), (9002, 2002, 0.1, 2, 'obs', 0.95), (9005, 3000, 0.05, 20, 'fg', 0.6);
            INSERT INTO vendor_item VALUES (5110, 2100, 500, 'fg', 0.6), (5188, 2100, 520, 'fg', 0.6),
                (15127, 20041, NULL, 'frq', 0.6);
            -- one quest a side; "Both" that is one side's by its races (77: the Alliance races) or by WoWSims
            INSERT INTO quest(id, name, side, races) VALUES (5102, 'General Drakkisath''s Demise', 'Alliance', 77),
                (7000, 'Horde Errand', 'Horde', 178), (7001, 'Westfall Chain', 'Both', 77),
                (7100, 'Barrens Chain', 'Both', 0), (7200, 'Anyone''s Errand', NULL, 255);
            INSERT INTO quest_reward VALUES (5102, 2200, 1, 1, 'fg'), (7000, 2200, 1, 1, 'fg'),
                (7001, 2300, 1, 1, 'fg'), (7100, 2400, 1, 1, 'fg'), (7200, 2500, 1, 1, 'fg');
            INSERT INTO item VALUES (2000, 'Old Thug Boots'), (2001, 'Green Belt of the Monkey'), (2100, 'Lesser Robe'),
                (2200, 'Drakkisath Reward'), (2002, 'Defias Gloves'), (2300, 'Staff of Westfall'),
                (2400, 'Crescent Staff'), (2500, 'Plain Ring');
        """)

    def q(self, sql, *args):
        return [dict(r) for r in self.c.execute(sql, args).fetchall()]

    def one(self, sql, *args):
        r = self.q(sql, *args)
        return r[0] if r else None


class FakePack:
    ok = True

    def __init__(self, built="b1"):
        self.kb, self.built = FakeKB(), built

    def status(self):
        return {"ok": True, "built": self.built}

    def map_name(self, ui_map):
        return {1455: "Ironforge", 1436: "Westfall"}.get(ui_map)


def it(iid, name=None, key="1"):
    return {"key": key, "id": iid, "name": name or (ITEMS[iid][0] if iid in ITEMS else "Item %d" % iid)}


class FakeState:
    def __init__(self):
        self.s = {"name": "Nine", "class": "MAGE", "level": 60, "faction": "Alliance", "zone": "Burning Steppes",
                  "target": None}
        self.gear = {"W": [it(18405)], "I": [it(16686)], "R": [it(22403)]}

    def summary(self):
        return dict(self.s)



tmp = tempfile.mkdtemp(prefix="glide-research-")
real_time, budget = research.time, research.TICK_BUDGET_S
try:
    home = os.path.join(tmp, "engine")
    clock, mono = [1000000.0], [0.0]

    def tick_mono():
        mono[0] += 1.0
        return mono[0]
    research.time = types.SimpleNamespace(monotonic=tick_mono, time=real_time.time, sleep=real_time.sleep)
    db, pack, st, on = FakeDB(), FakePack(), FakeState(), [True]
    holder = {"db": db, "pack": pack}
    r = research.Research(home, lambda: holder["db"], lambda: holder["pack"], lambda: st, lambda: on[0],
                          now=lambda: clock[0])

    # ---- the queue: on screen first (reward, bags), then worn, then the band, best items first -----------------------
    research.TICK_BUDGET_S = 2.5                     # (the fake clock: two lookups a poll)
    r.tick()
    check(r.facts(22403) and r.facts(16686) and r.facts(18405) is None, "reward and bag items first")
    r.tick()
    check(r.facts(18405) is not None, "then worn")
    check(r.facts(19147) is not None and r.facts(13000) is None, "then the band, the highest item level first")
    research.TICK_BUDGET_S = 10 ** 6                 # (from here on, all of the queue each poll)
    r.tick()
    check(all(r.facts(i) for i in (16686, 18405, 22403, 19147, 4000)), [i for i in ITEMS if not r.facts(i)])
    check("i:13000" in r.neg and r.facts(13000) is None, "no source, not BoE: the negative cache")

    # ---- merged sources: WoWSims' copy of Gandling (0.8) over the kb's (0.6), names kept ----------------------------
    f = r.facts(16686)
    check(len(f["s"]) == 1 and f["s"][0]["src"] == "ws" and f["s"][0]["conf"] == 0.8 and f["conf"] == 0.8, f)
    check(f["s"][0]["npc_name"] == "Darkmaster Gandling" and f["s"][0]["zone_name"] == "Scholomance", f)
    check(f["src"] == "ws" and f["avail"] == "now" and f["fa"] is None and f["tag"] == "v1.0.4", f)  # (both sides)
    check(set(research.SRC_KEYS) <= set(f["s"][0]), "every Src key")
    check(r.facts(19147)["avail"] == "later", "a later phase")
    check(r.facts(4000)["fa"] == "Alliance", ("a vendor of one side: the fact is that side's", r.facts(4000)))

    # ---- cached: not looked up again until the ttl ----------------------------------------------------------------
    calls = dict(db.calls)
    r.tick()
    check(db.calls == calls, ("looked up again", calls, db.calls))
    clock[0] += research.NEG_TTL_S - 10
    r.tick()
    check(db.calls.get(13000) == calls.get(13000), "the negative cache holds a day")
    clock[0] += research.BAND_S                      # (the band is queued again hourly)
    r.tick()
    check(db.calls.get(13000) == calls.get(13000, 0) + 1, "asked again after a day")
    clock[0] += research.TTL_S
    r.tick()
    check(db.calls[16686] == calls[16686] + 1, "found again after its ttl")

    # ---- the knowledge base: vendors by faction, quest rewards by side, the recorder's drops, world drops ------------
    for iid in (2000, 2001, 2100, 2200):
        r.enqueue("i:%d" % iid, "test", research.PRIO_OTHER)
    r.tick()
    f = r.facts(2000)
    check(f and f["s"][0]["kind"] == "drop" and f["s"][0]["src"] == "obs" and f["s"][0]["conf"] == 0.95 and
          f["s"][0]["npc_name"] == "Defias Thug" and f["s"][0]["zone_name"] == "Westfall", f)
    check(f["name"] == "Old Thug Boots" and f["avail"] == "now", f)
    f = r.facts(2001)
    check(len(f["s"]) == 1 and f["s"][0]["kind"] == "world" and f["s"][0]["npc"] is None and
          f["s"][0]["zone_name"] == "Westfall", ("three kinds of creature: a world drop", f))
    f = r.facts(2100)
    check([s["npc_name"] for s in f["s"]] == ["Deliana"] and f["price"] == 500, ("Alliance vendor only", f))
    f = r.facts(2200)
    check([s["quest"] for s in f["s"]] == [5102] and f["s"][0]["quest_name"] == "General Drakkisath's Demise", f)

    # ---- a drop the player saw: the target died, then a new item in the bags within LOOT_S ------------------------
    st.s["target"] = {"npc": "10363", "name": "General Drakkisath", "dead": True}
    r.tick()
    st.gear["I"] = [it(16686), it(5555, "Drakkisath's Dropped Thing", key="2")]
    clock[0] += 30
    r.tick()
    f = r.facts(5555)
    check(f and f["s"][0]["src"] == "obs" and f["s"][0]["npc"] == 10363 and f["s"][0]["conf"] == 0.95 and
          f["s"][0]["zone_name"] == "Burning Steppes" and f["src"] == "obs", f)
    check(r.seen.get("i:5555") and f["name"] == "Drakkisath's Dropped Thing", r.seen)
    # too late after the kill, or a reward choice: not a drop
    st.gear["R"] = [it(6666, "Quest Prize")]
    st.gear["I"] = [it(16686), it(5555, key="2"), it(6666, "Quest Prize", key="3")]
    clock[0] += 10
    r.tick()
    check(not r.seen.get("i:6666"), "a reward choice isn't a drop")
    st.gear["R"] = []
    st.gear["I"] = [it(16686), it(7777, "Late Thing", key="4")]
    clock[0] += research.LOOT_S
    r.tick()
    check(not r.seen.get("i:7777"), "too long after the kill")
    st.s["target"] = None

    # ---- not drops: the piece taken off for a looted one, an item had this session, one bought (regressions) --------
    st.s["money"] = 1000
    st.gear = {"W": [it(18405), it(8001, "Old Hat", key="1")], "I": [], "R": []}
    clock[0] += 10
    r.tick()
    st.s["target"] = {"npc": "1853", "name": "Darkmaster Gandling", "dead": True}
    clock[0] += 10
    r.tick()                                                         # the kill
    st.gear = {"W": [it(18405), it(8002, "New Hat", key="1")], "I": [it(8001, "Old Hat", key="2")], "R": []}
    clock[0] += 10
    r.tick()                                                         # the looted hat worn at once
    check(not r.seen.get("i:8001") and not r.seen.get("i:8002"), ("a swap after a kill isn't a drop", r.seen))
    st.gear["I"] = []
    clock[0] += 10
    r.tick()                                                         # (the old hat into the bank)
    st.gear["I"] = [it(8001, "Old Hat", key="2")]
    clock[0] += 10
    r.tick()
    check(not r.seen.get("i:8001"), "an item had this session isn't a drop")
    st.s["money"] = 900
    st.gear["I"] = [it(8001, "Old Hat", key="2"), it(8003, "Bought Robe", key="3")]
    clock[0] += 10
    r.tick()
    check(not r.seen.get("i:8003"), "bought after a kill (the money went down): not a drop")
    st.quests = {5102: {}, 7000: {}}
    clock[0] += 10
    r.tick()
    st.quests = {7000: {}}                                           # 5102 turned in: its one reward
    st.gear["I"] = [it(8001, "Old Hat", key="2"), it(8003, "Bought Robe", key="3"), it(8005, "Prize Ring", key="5")]
    clock[0] += 10
    r.tick()
    check(not r.seen.get("i:8005"), "a quest turned in after a kill: its reward isn't a drop")
    st.s["money"] = 950
    st.gear["I"] = [it(8001, "Old Hat", key="2"), it(8003, "Bought Robe", key="3"), it(8005, "Prize Ring", key="5"),
                    it(8004, "Gandling Gift", key="4")]
    clock[0] += 10
    r.tick()
    check(r.seen.get("i:8004") and r.seen["i:8004"][0]["npc"] == 1853, ("a real drop still is", r.seen))
    st.s["target"] = None
    st.gear = {"W": [it(18405)], "I": [it(16686), it(7777, "Late Thing", key="4")], "R": []}
    # a drop seen is kept TTL_S (by then the knowledge base has the recorder's own copy), then dropped
    r._save()
    saved = json.load(open(os.path.join(home, "data", "research", "facts.json"), encoding="utf-8"))
    check(isinstance(saved["seen"]["i:8004"][0].get("t"), (int, float)), saved["seen"]["i:8004"])
    later = research.Research(home, lambda: None, lambda: None, lambda: st, lambda: True,
                              now=lambda: clock[0] + research.TTL_S + 1)
    check("i:8004" not in later.seen and "i:5555" not in later.seen, later.seen)

    # ---- Data/Live.lua's items: the text, now/later/none, the order kept over the cap ------------------------------
    sec = r.items_section()
    check(set(sec[16686]) == {"t", "a", "w", "p"}, sec[16686])
    check(sec[16686]["t"] == item_text.line(r.facts(16686)["s"], pack, None), sec[16686])
    check(sec[18405]["p"] == 0 and sec[16686]["p"] == 1 and sec[19147]["a"] == "l" and sec[16686]["a"] == "n", sec)
    band = [sec[i]["p"] for i in (19147, 22403, 4000)]
    check(band == sorted(band) and band[0] >= research.BAND_P, ("the band by item level", band))
    check(sec[2000]["p"] >= research.OTHER_P and 13000 not in sec and sec[16686]["w"] == 0, sec)

    # ---- the coach's item_info: by name (any case, no apostrophe, misheard), by id; an unknown item ----------------
    for q in ("Magister's Crown", "magisters crown", "Magister's Crwn", "16686", "item 16686"):
        info = r.item_info(q)
        check(info["id"] == 16686 and info["name"] == "Magister's Crown", (q, info))
    check(info["sources"][0] == item_text.line(r.facts(16686)["s"], pack, None) and
          item_text.source_text(r.facts(16686)["s"][0], pack) in info["sources"], info)
    check(info["avail"] == "now" and info["fact_ids"] == ["ws:i:16686"], info)
    check(r.item_info("Old Thug Boots")["fact_ids"] == ["res:i:2000"], "a knowledge base item")
    info = r.item_info("Sword of Nowhere")
    check(info["id"] is None and info["sources"] == [] and info.get("error"), info)
    info = r.item_info("Nacreous Shell Necklace")
    check(info["id"] == 22403 and info["sources"], info)
    # asked before any poll found it: found at once
    r.found.pop("i:18405")
    info = r.item_info("Belt of the Archmage")
    check(info["sources"] and r.facts(18405), "found at once for a question")

    # ---- binds when equipped, no source: kept (the auction house may have it) ----------------------------------------
    r.bond_fn = lambda iid: research.BOE if iid == 13000 else None
    r.neg.pop("i:13000", None)
    r.enqueue("i:13000", "test", research.PRIO_NOW)
    r.tick()
    f = r.facts(13000)
    check(f and f["s"] == [] and f["bond"] == research.BOE and f["src"] == "client", f)
    sec = r.items_section()
    check((13000 in sec) == bool(item_text.line([], pack, research.BOE)), "BoE without a source")
    r.bond_fn = None

    # ---- saved: a restart has what was found -----------------------------------------------------------------------
    path = os.path.join(home, "data", "research", "facts.json")
    d = json.load(open(path, encoding="utf-8"))
    check(d["v"] == 1 and "i:16686" in d["facts"] and "i:5555" in d["seen"], list(d))
    again = research.Research(home, lambda: holder["db"], lambda: holder["pack"], lambda: st, lambda: True,
                              now=lambda: clock[0])
    check(again.facts(16686) == r.facts(16686) and again.item_info("magisters crown")["id"] == 16686, "restart")

    # ---- a new WoWSims tag: found again (the old facts serve meanwhile) ----------------------------------------------
    calls = dict(db.calls)
    holder["db"] = db2 = FakeDB("v1.0.5")
    r.tick()
    check(r.facts(16686)["tag"] == "v1.0.5" and db2.calls.get(16686) == 1, "a new tag")
    check(db.calls == calls, "the old data isn't asked")
    # WoWSims' data can't be had (offline): what it said is kept, even past its ttl
    holder["db"] = None
    clock[0] += research.TTL_S + 1
    r.tick()
    check(r.facts(16686)["tag"] == "v1.0.5" and r.facts(16686)["s"][0]["src"] == "ws", "kept offline")
    holder["db"] = db2
    r.tick()
    check(db2.calls[16686] == 2, "found again once it's back")
    # a knowledge base swap
    holder["pack"] = FakePack("b2")
    r.tick()
    check(r.facts(16686)["kb"] == "b2", r.facts(16686))
    # the other faction: its own vendor
    st.s["faction"] = "Horde"
    r.tick()
    check(r.facts(4000)["s"][0]["npc_name"] == "Mokvar" and r.facts(4000)["fa"] == "Horde", r.facts(4000))
    st.s["faction"] = "Alliance"
    r.tick()

    # ---- a world drop: WoWSims says so, or seen from WORLD_NPCS kinds of creature; one creature seen isn't its source
    for iid in (6423, 2002):
        r.enqueue("i:%d" % iid, "test", research.PRIO_NOW)
    r.tick()
    f = r.facts(6423)
    check([s["kind"] for s in f["s"]] == ["world"] and f["fa"] is None, ("WoWSims' world drop leads", f))
    check(r.items_section()[6423]["t"] == "world drop" and r.item_info("Blackforge Greaves")["sources"] == ["world drop"],
          (r.items_section()[6423], r.item_info("Blackforge Greaves")))
    check(r.facts(2002)["s"][0]["kind"] == "drop", "two kinds of creature seen: still theirs")
    r._saw_drop(2002, {"npc": 9003, "name": "Defias Looter", "zone": "Westfall", "t": clock[0]}, clock[0])
    r.tick()
    f = r.facts(2002)
    check([s["kind"] for s in f["s"]] == ["world"] and f["s"][0]["src"] == "obs" and f["s"][0]["zone_name"] == "Westfall",
          ("the player saw a third kind: a world drop", f))

    # ---- a vendor the knowledge base lists and WoWSims gives a reputation: one source, the reputation kept ------------
    f = r.facts(20041)
    check(len(f["s"]) == 1 and f["s"][0]["kind"] == "rep" and f["s"][0]["rep_level"] == "Honored" and
          f["fa"] == "Alliance", f)
    check(r.items_section()[20041]["t"] == "sold by Samuel Hawke; needs Honored with the League of Arathor",
          r.items_section()[20041])
    kbv = research.make_src("vendor", "kb", npc=15127, npc_name="Samuel Hawke", zone_name="Arathi Highlands")
    obs = research.make_src("vendor", "obs", npc=15127)
    wsr = ws_src("rep", npc=15127, npc_name="Samuel Hawke", rep_faction="The League of Arathor", rep_level="Honored")
    m = research.merge([kbv, obs, wsr])
    check(len(m) == 1 and m[0]["kind"] == "rep" and m[0]["src"] == "obs" and m[0]["rep_level"] == "Honored" and
          m[0]["zone_name"] == "Arathi Highlands", ("the surest copy, a rep source with its blanks filled", m))
    check([s["kind"] for s in research.merge([kbv, obs])] == ["vendor"], "a plain vendor stays one")

    # ---- whether it can be had follows from every source: WoWSims knows the item but no source, the kb knows one ----
    f = r.facts(3000)
    check(f and f["avail"] == "now" and f["s"][0]["npc_name"] == "Kobold Vermin", f)
    check(r.items_section()[3000]["a"] == "n" and r.item_info("Notched Shortsword")["avail"] == "now", "not grey")

    # ---- quests the knowledge base marks "Both" or nothing: one side's by their races, or by WoWSims ----------------
    for iid in (2300, 2400, 2500):
        r.enqueue("i:%d" % iid, "test", research.PRIO_NOW)
    r.tick()
    check(r.facts(2300)["fa"] == "Alliance" and r.facts(2300)["s"][0]["quest"] == 7001, ("races 77", r.facts(2300)))
    check(r.facts(2400) is None and r.neg["i:2400"][1] == "Alliance", ("WoWSims: a Horde quest", r.neg.get("i:2400")))
    check(r.facts(2500)["fa"] is None, ("every race: both sides", r.facts(2500)))
    check(research.quest_sides("Both", 178) == {"Horde"} and research.quest_sides(None, 0) == set(research.SIDES) and
          research.quest_sides("Horde", 77) == {"Horde"} and research.quest_sides("Both", 65613) == {"Alliance"},
          "quest_sides")

    # ---- Data/Live.lua is the account's, written for the side playing; negative caches per side --------------------
    st.s["faction"] = "Horde"
    r.tick()
    sec = r.items_section()
    check(2300 not in sec and 20041 not in sec and 16686 in sec and 6423 in sec, ("the Alliance's stay out", sorted(sec)))
    check(r.facts(20041) is None and r.neg["i:20041"][1] == "Horde", ("nothing on the Horde side", r.neg.get("i:20041")))
    r.enqueue("i:2400", "test", research.PRIO_NOW)
    r.tick()
    check(r.facts(2400)["fa"] == "Horde" and 2400 in r.items_section(), "a Horde character: its quest's reward")
    st.s["faction"] = "Alliance"
    r.tick()
    check(r.facts(20041) and r.facts(20041)["fa"] == "Alliance", "back on the Alliance: not the Horde's negative cache")
    sec = r.items_section()
    check(2300 in sec and 2400 not in sec, sorted(sec))

    # ---- asked before any character was seen: a fact that depends on the side isn't kept ----------------------------
    st0 = types.SimpleNamespace(gear={"W": [], "I": [], "R": []}, s={"target": None})
    st0.summary = lambda: dict(st0.s)
    home0 = os.path.join(tmp, "engine0")
    r0 = research.Research(home0, lambda: holder["db"], lambda: holder["pack"], lambda: st0, lambda: True,
                           now=lambda: clock[0])
    r0.tick()
    info = r0.item_info("4000")
    check(info["id"] == 4000 and info["sources"] and r0.facts(4000) is None, ("answered, not kept", info))
    check(r0.item_info("16686")["sources"] and r0.facts(16686)["fa"] is None, "the same for both: kept")
    check(r0.items_section() == {} or 4000 not in r0.items_section(), "nothing of one side's written")
    st0.s = {"class": "MAGE", "level": 60, "faction": "Horde", "zone": "Orgrimmar", "target": None}
    st0.gear = {"W": [it(4000)], "I": [], "R": []}
    r0.tick()
    check([s["npc_name"] for s in r0.facts(4000)["s"]] == ["Mokvar"] and r0.facts(4000)["fa"] == "Horde",
          ("the Horde character: its own vendor", r0.facts(4000)))
    check(r0.items_section()[4000]["t"] == "sold by Mokvar, Orgrimmar", r0.items_section()[4000])
    # the faction is kept: started again before the character is in, the file is still written for it
    st0.s = {"target": None}
    r1 = research.Research(home0, lambda: holder["db"], lambda: holder["pack"], lambda: st0, lambda: True,
                           now=lambda: clock[0])
    check(r1._faction == "Horde" and r1.items_section()[4000]["t"] == "sold by Mokvar, Orgrimmar", r1._faction)

    # ---- leftover_db.json coming in late: what was found without it is found again ---------------------------------
    holder["db"] = db3 = FakeDB("v1.0.7")
    db3.leftover = False
    r.tick()
    check(r.facts(16686)["tag"] == "v1.0.7/db" and research.data_version(db3) == "v1.0.7/db", r.facts(16686)["tag"])
    r.neg["i:9999"] = [clock[0], None]
    db3.leftover = True
    r.tick()
    check(r.facts(16686)["tag"] == "v1.0.7" and "i:9999" not in r.neg, (r.facts(16686)["tag"], "neg cleared"))
    holder["db"] = db2
    r.tick()

    # ---- a lookup that keeps failing: given up this session, what was known stays ------------------------------------
    class Broken(FakeDB):
        def sources(self, iid, faction):
            raise ValueError("bad record")
    holder["db"] = Broken("v1.0.6")
    for _ in range(research.GIVE_UP + 2):
        r.tick()
    check(r.failed.get("i:16686") == research.GIVE_UP and r.facts(16686)["tag"] == "v1.0.5", r.failed)
    holder["db"] = db2
    r.failed.clear()

    # ---- switched off: nothing looked up (what is queued waits) --------------------------------------------------
    on[0] = False
    r.found.pop("i:18405")
    r.enqueue("i:18405", "test", research.PRIO_NOW)
    calls = dict(db2.calls)
    r.tick()
    check(db2.calls == calls and r.facts(18405) is None and "i:18405" in r.queue, "off: nothing looked up")
    on[0] = True

    # ---- ItemDBSource: WoWSims' item data at data_tag(), loaded once per tag, tried again later ----------------------
    class FakeWS:
        def __init__(self):
            self.tag, self.fail, self.got, self.gone, self.pruned = "v1.0.4", set(), [], set(), []

        def data_tag(self):
            return self.tag

        def prune(self, tag):
            self.pruned.append(tag)

        def file(self, path, tag=None):
            self.got.append((path, tag))
            if path in self.gone:                    # (the release has no such file: GitHub's 404)
                raise wowsims.FetchError("no %s" % path) from urllib.error.HTTPError(path, 404, "Not Found", {}, None)
            if path in self.fail:
                raise wowsims.FetchError("offline")
            items = [{"id": 16686, "name": "Magister's Crown"}] if path.endswith("/db.json") else \
                [{"id": 6540, "name": "Willow Pants"}, {"id": 99, "name": "Magister's Crown"}]
            return json.dumps({"items": items}).encode("utf-8")

    loads = []
    fws = FakeWS()
    src = research.ItemDBSource(fws, load=lambda raw, left, tag: loads.append((raw, left, tag)) or
                                types.SimpleNamespace(tag=tag), now=lambda: clock[0])
    got = src()
    check(got is not None and got.tag == "v1.0.4" and loads[0][1] is not None, loads)
    check([p for p, _ in fws.got] == list(research.DB_FILES) and all(t == "v1.0.4" for _, t in fws.got), fws.got)
    check(src.names == {"magisters crown": 16686, "willow pants": 6540}, src.names)
    check(src() is got and len(loads) == 1, "loaded once per tag")
    check(fws.pruned == ["v1.0.4"], ("a new tag loaded: the older data folders go", fws.pruned))
    fws.tag, fws.fail = "v1.0.5", {research.DB_FILES[0]}
    check(src() is got and src() is got and len(fws.got) == 3, ("kept, tried once", fws.got))
    clock[0] += research.RETRY_S
    fws.fail = {research.DB_FILES[1]}
    check(src().tag == "v1.0.5" and loads[-1][1] is None, "without the leftover file")
    fws.tag = None
    check(src().tag == "v1.0.5", "no tag: the one loaded stays")
    # leftover_db.json cut short (regression: it was never asked again this session): tried again after RETRY_S and the
    # database loaded again with it; one the release doesn't have isn't asked again
    left_loads = []

    def load_left(raw, left, tag):
        left_loads.append(left)
        return types.SimpleNamespace(tag=tag, leftover=left is not None)
    fws2 = FakeWS()
    fws2.fail = {research.DB_FILES[1]}
    src2 = research.ItemDBSource(fws2, load=load_left, now=lambda: clock[0])
    d1 = src2()
    check(d1.leftover is False and research.data_version(d1) == "v1.0.4/db" and "willow pants" not in src2.names, d1)
    fws2.fail = set()
    check(src2() is d1, "not before RETRY_S")
    clock[0] += research.RETRY_S
    d2 = src2()
    check(d2 is not d1 and d2.leftover and research.data_version(d2) == "v1.0.4" and "willow pants" in src2.names,
          (d2, src2.names))
    n = len(fws2.got)
    clock[0] += research.RETRY_S
    check(src2() is d2 and len(fws2.got) == n, "had: not fetched again")
    fws3 = FakeWS()
    fws3.gone = {research.DB_FILES[1]}
    src3 = research.ItemDBSource(fws3, load=load_left, now=lambda: clock[0])
    d3 = src3()
    n = len(fws3.got)
    clock[0] += research.RETRY_S
    check(src3() is d3 and len(fws3.got) == n and research.data_version(d3) == "v1.0.4/db", "a 404: not asked again")
    # names from it reach item_info
    r2 = research.Research(os.path.join(tmp, "engine2"), src, lambda: None, lambda: st, lambda: True,
                           now=lambda: clock[0])
    check(r2._find("Magister's Crown") == 16686 and r2._find("magisters crwn") == 16686, "WoWSims' names")

    # ---- WoWSims.data_tag: the release in use, else the latest one's tag at most once a day --------------------------
    wh = os.path.join(tmp, "engine3")
    asked = []

    def get(url, limit):
        asked.append(url)
        if url == "offline":
            raise OSError("offline")
        return json.dumps({"tag_name": latest[0], "assets": []}).encode("utf-8")
    latest = ["v1.0.4"]
    ws = wowsims.WoWSims(wh, get=get, now=lambda: clock[0])
    check(ws.current() is None and ws.data_tag() == "v1.0.4" and asked == [wowsims.LATEST], asked)
    check(not os.path.exists(os.path.join(wh, "data", "wowsims", "v1.0.4", wowsims.EXE)), "no runner downloaded")
    latest[0] = "v1.0.5"
    check(ws.data_tag() == "v1.0.4" and len(asked) == 1, "once a day")
    state = json.load(open(os.path.join(wh, "data", "wowsims", "state.json"), encoding="utf-8"))
    check(state["data"] == {"tag": "v1.0.4", "t": clock[0]} and state["current"] is None, state)
    again = wowsims.WoWSims(wh, get=get, now=lambda: clock[0])
    check(again.data_tag() == "v1.0.4" and len(asked) == 1, "kept across a restart")
    clock[0] += wowsims.CHECK_S
    check(ws.data_tag() == "v1.0.5" and len(asked) == 2, "a day later")
    clock[0] += wowsims.CHECK_S

    def offline(url, limit):
        asked.append(url)
        raise OSError("offline")
    ws._get = offline
    check(ws.data_tag() == "v1.0.5" and ws.data_tag() == "v1.0.5" and len(asked) == 3, "offline: the last one")
    clock[0] += wowsims.RETRY_S
    ws.data_tag()
    check(len(asked) == 4, "offline: again after RETRY_S")
    # a runner in use: its tag, nothing looked up
    run_dir = os.path.join(wh, "data", "wowsims", "v1.0.3")
    os.makedirs(run_dir)
    open(os.path.join(run_dir, wowsims.EXE), "wb").close()
    ws.state["current"] = "v1.0.3"
    check(ws.data_tag() == "v1.0.3" and len(asked) == 4, "the release in use")
    # a damaged state file
    with open(os.path.join(wh, "data", "wowsims", "state.json"), "w") as fh:
        fh.write('{"data": {"tag": "../evil", "t": 1}}')
    check(wowsims.WoWSims(wh, get=get).state["data"] is None, "a bad data tag")

    # ---- WoWSims.prune: a player who never sims keeps the data tag in use and the newest other one (regression: every
    # release's item database stayed on disk) -----------------------------------------------------------------------
    ph = os.path.join(tmp, "engine4")
    wsp = wowsims.WoWSims(ph, get=get, now=lambda: clock[0])
    base = os.path.join(ph, "data", "wowsims")
    for n, tag in enumerate(("v1.0.4", "v1.0.5", "v1.0.6", "v1.0.7")):
        db_dir = os.path.join(base, tag, "files", "assets", "database")
        os.makedirs(db_dir)
        open(os.path.join(db_dir, "db.json"), "w").close()
        os.utime(os.path.join(base, tag), (1000000 + n, 1000000 + n))
        wsp.prune(tag)
    check(sorted(os.listdir(base)) == ["v1.0.6", "v1.0.7"], ("at most KEEP", os.listdir(base)))
    # simming with a runner: it stays too, and so does the data tag in use
    os.makedirs(os.path.join(base, "v1.0.3"))
    os.utime(os.path.join(base, "v1.0.3"), (999000, 999000))
    wsp.state["current"] = "v1.0.3"
    wsp._prune()
    check(sorted(os.listdir(base)) == ["v1.0.3", "v1.0.6", "v1.0.7"], os.listdir(base))
finally:
    research.time, research.TICK_BUDGET_S = real_time, budget
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
