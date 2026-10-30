"""Mechanics tests: Classic sanity, Forever-fitted behaviour, parameter registry, the fit on the fixture recordings
(tests/kb/fixture.py; on the player's own data/glide.db: the report-only tests/kb/smoke_mechanics.py).

Run: python3 tests/kb/test_mechanics.py   (prints OK)
"""
import json
import os
import shutil
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import fixture  # noqa: E402

from knowledge.mechanics import combat, death, economy, fit, params, rested, travel, xp  # noqa: E402

# Forever behaviour as fitted from the recordings, set explicitly so these tests do not depend on data/params.json
FOREVER = {"xp.kill.low_base": "mob", "xp.kill.rounding": "half_even", "rested.bonus_ratio": 0.5,
           "xp.mod.well_fed": 0.05}


def test_classic_xp():
    with params.classic_scope():
        assert xp.level_xp(1) == 400
        # The task brief listed 108600 for level 59. That table (8700 at 11, 9800 at 12, ..., total 2,549,900) is
        # a later expansion's reduced table. Classic/vanilla needs 209800 at 59 (total 4,084,700), and Forever
        # reports UnitXPMax 8800 at 11 and 10100 at 12 (live logs), i.e. the vanilla table.
        assert xp.level_xp(59) == 209800
        assert xp.level_xp(60) == 0
        assert xp.total_xp(1, 60) == 4084700
        table = [xp.level_xp(lv) for lv in range(1, 60)]
        assert table[:9] == [400, 900, 1400, 2100, 2800, 3600, 4500, 5400, 6500]
        assert table[9:12] == [7600, 8800, 10100]
        assert all(b > a for a, b in zip(table, table[1:]))
        for lv in range(1, 60):     # Classic formula, rounded half-up to 100
            diff = 0 if lv <= 28 else 1 if lv == 29 else 3 if lv == 30 else 6 if lv == 31 else 5 * (lv - 30)
            v = (8 * lv + diff) * (45 + 5 * lv)
            assert table[lv - 1] == (v + 50) // 100 * 100, lv
        assert xp.xp_to_reach(1, 0, 3) == 1300
        assert xp.xp_to_reach(1, 100, 2) == 300
        assert xp.xp_to_reach(5, 0, 5) == 0

        assert xp.kill_xp(1, 1) == 50
        assert xp.kill_xp(1, 2) == 52            # 52.5 rounds to the even 52 (observed on Forever too)
        # 115 * (1 - 4/8) = 57.5 -> 58 with round-half-to-even (an integer floor would give 57)
        assert xp.kill_xp(14, 10) in (57, 58)
        assert xp.kill_xp(14, 10) == 58
        assert xp.kill_xp(14, 9) == 43
        assert xp.kill_xp(14, 11, elite=True) == 144
        assert xp.gray_level(20) == 13
        assert xp.kill_xp(20, 13) == 0 and xp.kill_xp(20, 12) == 0 and xp.kill_xp(20, 14) > 0
        assert xp.gray_level(5) == 0 and xp.kill_xp(5, 1) > 0
        assert xp.gray_level(6) == 1 and xp.kill_xp(6, 1) == 0
        assert xp.gray_level(40) == 31 and xp.gray_level(59) == 47 and xp.gray_level(60) == 51
        assert [xp.zero_difference(lv) for lv in (1, 7, 8, 9, 10, 11, 12, 15, 16, 19, 20, 29, 30, 39, 40, 44, 45,
                                                 49, 50, 54, 55, 59, 60)] == \
            [5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 11, 11, 12, 12, 13, 13, 14, 14, 15, 15, 16, 16, 17]
        for pl, ml in ((10, 10), (10, 12), (10, 8), (30, 33), (45, 40)):
            assert xp.kill_xp(pl, ml, elite=True) == 2 * xp.kill_xp(pl, ml) or \
                abs(xp.kill_xp(pl, ml, elite=True) - 2 * xp.kill_xp(pl, ml)) <= 1
            assert xp.kill_xp(pl, ml, rested=True) == 2 * xp.kill_xp(pl, ml)
        assert xp.kill_xp(10, 10, elite=True) == 190
        assert xp.kill_xp(10, 20) == xp.kill_xp(10, 14) == 114     # level difference capped at +4
        assert xp.kill_xp(60, 60) == 0                                # no XP at the cap
        # Classic group split: equal levels, 2 players share 1.0x, 3 players 1.166x
        assert xp.kill_xp(10, 10, group_size=2) == 47
        assert xp.kill_xp(10, 10, group_size=3) == 36
        # xp computed for the level 12 member (105 * 6/8 = 78.75 -> 79), level 10 share 79 * 10/22 -> 35
        assert xp.kill_xp(10, 10, group_levels=[10, 12]) == 35

        assert xp.quest_xp(1000, 10, 15) == 1000
        assert xp.quest_xp(1000, 10, 16) == 800
        assert xp.quest_xp(1000, 10, 17) == 600
        assert xp.quest_xp(1000, 10, 18) == 400
        assert xp.quest_xp(1000, 10, 19) == 200
        assert xp.quest_xp(1000, 10, 20) == 100
        assert xp.quest_xp(1000, 10, 30) == 100
        assert xp.quest_xp(None, 10, 10) is None
        assert xp.quest_xp(910, 12, 8) == 900 and xp.quest_xp(405, 6, 6) == 410    # server rounding of raw XP
        assert xp.round_quest_xp(97) == 95 and xp.round_quest_xp(590) == 600 and xp.round_quest_xp(1130) == 1150
        f = [xp.quest_xp_factor(10, p) for p in range(1, 40)]
        assert all(b <= a for a, b in zip(f, f[1:])) and f[0] == 1.0 and f[-1] == 0.1


def test_forever_kill_xp():
    with params.classic_scope(FOREVER):
        # lower-level mobs: base from the mob level (observed at 1-12)
        assert xp.kill_xp(2, 1) == 40 and xp.kill_xp(3, 1) == 30 and xp.kill_xp(3, 2) == 44
        assert xp.kill_xp(5, 2) == 22 and xp.kill_xp(11, 9) == 64 and xp.kill_xp(12, 10) == 71
        # x.5 cases round half to even
        assert xp.kill_xp(1, 2) == 52 and xp.kill_xp(5, 6) == 74 and xp.kill_xp(6, 8) == 82
        assert xp.kill_xp(4, 6) == 72 and xp.kill_xp(12, 11) == 88
        # README level-14 observations: rested doubling on the mob-level base
        assert xp.kill_xp(14, 10) == 48
        assert xp.kill_xp(14, 10, rested=True) == 96
        assert xp.kill_xp(14, 9, rested=True) == 68
        assert xp.kill_xp(14, 11, elite=True, rested=True) == 250
        # rested pool (client GetXPExhaustion): bonus = min(xp, pool/2), pool drops by 2*bonus
        d = xp.kill_xp_detail(2, 3, rest_pool=236)
        assert d["xp"] == 116 and d["bonus"] == 58 and d["pool_after"] == 120
        assert xp.kill_xp(2, 3, rest_pool=120) == 116
        assert xp.kill_xp(2, 2, rest_pool=4) == 57
        assert xp.kill_xp(6, 6, rest_pool=70) == 110
        assert xp.kill_xp(6, 5, rest_pool=74) == 93
        # Well Fed (1248422): +5% on the rounded kill XP, rounded half to even
        assert xp.kill_xp(8, 8, xp_mod=0.05) == 89 and xp.kill_xp(8, 9, xp_mod=0.05) == 93
        assert xp.kill_xp(8, 10, xp_mod=0.05) == 99 and xp.kill_xp(9, 10, xp_mod=0.05) == 99
        assert xp.kill_xp(9, 9, xp_mod=0.05) == 94 and xp.kill_xp(9, 8, xp_mod=0.05) == 75
        assert xp.kills_to_level(1, 0, 1) == 8
        assert xp.kills_to_level(2, 0, 3) == 16                    # 900 / 58
        assert xp.kills_to_level(2, 0, 3, rest_pool=236) == 14     # 116 + 116 + 60, then 11 x 58


def test_other_mechanics():
    with params.classic_scope():
        for lv in range(1, 11):
            assert death.res_sickness_s(lv) == 0
        assert death.res_sickness_s(11) == 60 and death.res_sickness_s(15) == 300
        assert death.res_sickness_s(20) == 600 == death.res_sickness_s(60)
        assert death.durability_loss() == 0.10 and abs(death.durability_loss(spirit_res=True) - 0.35) < 1e-12
        assert death.death_cost_s(30, 120.0) < death.death_cost_s(30, None, spirit_res=True)
        assert travel.distance_yd(3000, 2000, 0, 0, 100, 0) == 3000
        assert abs(travel.distance_yd(3000, 2000, 0, 0, 100, 100) - (3000 ** 2 + 2000 ** 2) ** 0.5) < 1e-9
        assert travel.run_speed_yds() == 7.0
        assert abs(travel.run_speed_yds(mounted=True, level=40) - 11.2) < 1e-9
        assert travel.run_speed_yds(mounted=True, level=39) == 7.0
        assert abs(travel.run_speed_yds(mounted=True, level=60) - 14.0) < 1e-9
        assert abs(travel.run_time_s(700) - 100.0) < 1e-9
        assert travel.hearth_cooldown_s() == 3600
        assert travel.taxi_time_s(3200) > 100
        assert abs(rested.accrual_fraction(8) - 0.05) < 1e-12
        assert abs(rested.accrual_fraction(8, resting=False) - 0.0125) < 1e-12
        assert rested.accrual_fraction(10000) == rested.cap_fraction() == 1.5
        assert rested.accrual_xp(10, 8) == 380 and rested.bonus_capacity(236) == 118
        assert rested.kill_bonus(58, 120) == (58, 4)
        assert economy.spell_cost(10) == 300 and economy.spell_cost(20) == 2400
        assert economy.training_cost("WARRIOR", 4) == 200 and economy.training_cost("WARRIOR", 5) == 0
        assert economy.mount_cost(39) == 0 and economy.mount_cost(40) == 1000000
        assert economy.mount_cost(60, epic=True) == 10000000
        assert combat.aggro_radius_yd(10, 10) == 20 and combat.aggro_radius_yd(10, 60) == 45
        assert combat.aggro_radius_yd(60, 1) == 5
        assert combat.time_to_kill_s("WARRIOR", 10, 12) > combat.time_to_kill_s("WARRIOR", 10, 10) > 0
        assert combat.time_to_kill_s("WARRIOR", 10, 10, rank=1) > combat.time_to_kill_s("WARRIOR", 10, 10)
        assert combat.downtime_s("HUNTER", 20, 5) < combat.downtime_s("WARRIOR", 20, 5)
    with params.classic_scope({"death.durability.min_level": 10}):
        assert death.durability_loss(True, level=9) == 0.0 and death.durability_loss(level=10) == 0.10


def test_params():
    rows = params.all()
    assert len(rows) > 100
    for r in rows:
        assert r["status"] in params.STATUSES, r
        assert r["key"] and r["evidence"] is not None
    conn = sqlite3.connect(":memory:")
    with open(os.path.join(ROOT, "knowledge", "schema.sql"), encoding="utf-8") as fh:
        conn.executescript(fh.read())
    conn.executemany("INSERT INTO param(key, value, unit, classic_value, status, evidence, updated) "
                     "VALUES (?,?,?,?,?,?,?)", params.to_rows())
    assert conn.execute("SELECT count(*) FROM param").fetchone()[0] == len(rows)
    assert conn.execute("SELECT count(*) FROM param WHERE status IS NULL").fetchone()[0] == 0
    for v, c in conn.execute("SELECT value, classic_value FROM param"):
        json.loads(v)
        if c is not None:
            json.loads(c)
    assert json.loads(conn.execute("SELECT classic_value FROM param WHERE key='xp.level.11'").fetchone()[0]) == 8800

    saved = params.overrides()
    try:
        params.reset()
        res = params.load_overrides({"travel.run_speed": {"value": 7.5, "status": "forever-fitted", "evidence": "t"},
                                     "no.such.key": 1})
        assert "no.such.key" in res["ignored"] and res["applied"] == ["travel.run_speed"]
        assert params.get("travel.run_speed") == 7.5 and params.status("travel.run_speed") == "forever-fitted"
        assert params.classic("travel.run_speed") == 7.0
        with params.scoped({"travel.run_speed": 3.0}):
            assert params.get("travel.run_speed") == 3.0
            with params.classic_scope():
                assert params.get("travel.run_speed") == 7.0
            assert params.get("travel.run_speed") == 3.0
        assert params.get("travel.run_speed") == 7.5
        try:
            with params.scoped({"no.such.key": 1}):
                pass
            raise AssertionError("scoped accepted an unknown key")
        except KeyError:
            pass
    finally:
        params.reset()
        params.load_overrides(saved)
    assert params.overrides() == saved

    path = os.path.join(ROOT, "data", "params.json")
    if os.path.exists(path):          # whatever fit.py wrote must be loadable and well-formed
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for key, ov in data["params"].items():
            assert key in params.keys(), key
            assert ov["status"] in ("forever-fitted", "forever-verified"), key
            assert ov.get("evidence"), key


def test_fit():
    """fit.run on the fixture's glide.db and companion log (tests/kb/fixture.py): kills made with the Forever
    formulas (mob-level base, half-even rounding, the rested pool, Well Fed) must come out as exactly that. The fit
    on the player's own recordings is the report-only tests/kb/smoke_mechanics.py."""
    tmp = fixture.tmpdir("fit-")
    try:
        root, run = fixture.make_root(tmp)
        db = os.path.join(root, "data", "glide.db")
        conn = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
        before = params.overrides()
        rep = fit.run(conn, write=False)
        conn.close()
        assert params.overrides() == before          # the fit uses scoped parameters only
        assert not os.path.exists(os.path.join(root, "data", "params.json")), "write=False wrote params.json"
        k = rep["kill_xp"]
        assert k["n_kills"] == len(run["kills"]), k["n_kills"]
        ids = {h["id"] for h in k["hypotheses"]}
        assert {"classic", "classic_rested", "classic_ml+1", "classic_flat", "classic_zdfit", "forever"} <= ids
        assert k["best"] == "forever" and k["best_rate"] == 1.0, (k["best"], k["best_rate"])
        assert k["best_rate"] > k["classic_agreement"] + 0.3
        assert k["anchors_l14"]["fit"]["mobbase_rested"] == 3 and k["anchors_l14"]["fit"]["classic"] == 0
        assert k["zd"]["verified_through"] >= 7, k["zd"]["verified_through"]
        assert rep["level_xp"]["rows"] and set(range(5, 11)) <= set(rep["level_xp"]["verified_levels"])
        turned = sum(1 for q in run["quests"].values() if q["turned_t"])
        assert rep["quest_xp"]["vs_glide_db"]["n"] == rep["quest_xp"]["vs_glide_db"]["exact"] == turned
        sp = rep["speed"]["trail"]
        assert sp["n_segments"] > 100 and abs(sp["median_near_mode"] - fixture.RUN_YDS) < 0.2, sp
        assert rep["ttk"]["by_class"]["WARRIOR"]["fights"] == len(run["kills"])
        json.dumps(rep, default=str)
        ov = rep["overrides"]
        assert ov["xp.kill.low_base"]["value"] == "mob" and ov["xp.kill.rounding"]["value"] == "half_even"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_classic_xp()
    test_forever_kill_xp()
    test_other_mechanics()
    test_params()
    test_fit()
    print("OK")
