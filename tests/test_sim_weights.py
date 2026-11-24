#!/usr/bin/env python3
"""WoWSims weighing the player's own gear (companion/sim_weights.py) against a fake WoWSims and game state: a
level-60 mage is weighed once its gear and talents are in (and settled after a change), again for a newer WoWSims, not
otherwise; the weights go to data/sim_weights.json, to the addon's Data/SimWeights.lua in the WoW folder (the file the
addon ships, empty, when there are none) and to the coach's gear advice (gear.Gear: the WoWSims profile at 60, its
title, a bag item scored with them); the chat says so the first time and after a new WoWSims; a failure is tried again
later; below 60 or another class: nothing.

Run: python3 tests/test_sim_weights.py   (prints OK)
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import sim_weights  # noqa: E402
import simbuild  # noqa: E402
from gear import Gear  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


class FakeWS:
    """WoWSims: DPS 600 + 0.52 per spell damage + 0.45 per intellect + 4.7 per 1% crit (bonus stats)."""
    def __init__(self):
        self.tag, self.ensured, self.sims, self.fail = "v1.0.4", 0, 0, None

    def ensure(self, force=False):
        self.ensured += 1
        return self.tag

    def current(self):
        return self.tag

    def file(self, path, tag=None):
        assert path.startswith("ui/specs/mage/dps/apls/"), path
        return b'{"type": "TypeAPL", "priorityList": []}'

    def sim(self, req, tag=None):
        self.tries = getattr(self, "tries", 0) + 1
        if self.fail:
            raise simbuild.SimError(self.fail)
        self.sims += 1
        bs = req["raid"]["parties"][0]["players"][0].get("bonusStats")
        s, p = (bs["stats"], bs["pseudoStats"]) if bs else ([0] * 39, [0] * 40)
        return 600 + 0.52 * s[5] + 0.45 * s[3] + 4.7 * p[15]


class FakeState:
    def __init__(self):
        self.level, self.cls = 60, "MAGE"
        self.gear = {"W": [{"key": str(s), "id": i, "loc": "INVTYPE_HEAD" if s == 1 else "", "stats": {"IN": 20}}
                           for s, i in sim_weights.SMOKE_GEAR], "I": [], "R": []}
        self.tal = sim_weights.talent_list(sim_weights.SMOKE_TALENTS)

    def summary(self):
        return {"name": "Nine", "class": self.cls, "race": "Skyborne", "faction": "Alliance", "level": self.level}

    def talents(self):
        return self.tal


tmp = tempfile.mkdtemp(prefix="glide-sim-weights-")
try:
    home, wow = os.path.join(tmp, "engine"), os.path.join(tmp, "wow")
    os.makedirs(os.path.join(wow, "Interface", "AddOns", "Glide", "Data"))
    clock, said = [1000.0], []
    ws, st, gear = FakeWS(), FakeState(), Gear({"kind": "Speedrun"})
    on = [True]
    sw = sim_weights.SimWeights(home, ws, lambda: st, gear, lambda: wow, lambda: on[0], emit=said.append,
                                now=lambda: clock[0])

    # the file the addon ships is what the app writes when no character has weights
    shipped = open(os.path.join(ROOT, "addon", "Glide", "Data", "SimWeights.lua"), encoding="utf-8").read()
    check(sim_weights.handoff_text({}) == shipped, "the shipped file is the empty handoff")

    # ---- a level-60 mage seen the first time: weighed at once ----------------------------------------------------------
    sw.tick()
    e = sw.chars.get("Nine")
    check(e and e["spec"] == "Arcane" and e["dps"] == 600 and e["per_ref"] == 0.52 and e["w"]["IN"] == 0.865 and
          e["w"]["CS"] == 9.038 and e["w"]["SP"] == 1.0 and e["wowsims"] == "v1.0.4" and e["class"] == "MAGE", e)
    check(ws.sims == 1 + len(simbuild.CLASSES["MAGE"]["weigh"]), ws.sims)
    saved = json.load(open(os.path.join(home, "data", "sim_weights.json"), encoding="utf-8"))
    check(saved["chars"]["Nine"]["w"] == e["w"], "kept on disk")
    lua = open(os.path.join(wow, sim_weights.HANDOFF), encoding="utf-8").read()
    check('["Nine"] = { class = "MAGE", spec = "Arcane", dps = 600.0, perRef = 0.52, wowsims = "v1.0.4"' in lua and
          "IN = 0.865" in lua and "CS = 9.038" in lua, lua)
    check(len(said) == 1 and "WoWSims v1.0.4 weighed your Arcane gear" in said[0]["text"] and
          "Worth in spell power: intellect 0.86, 1% crit 9.04." in said[0]["text"] and "hit" not in said[0]["text"],
          said)

    # ---- the coach's gear advice uses them at 60 -----------------------------------------------------------------
    check(gear.sim is e and gear.sim_version > 0, "handed to the coach")
    prof = gear.weights("MAGE", 60)
    check(prof["kind"] == "sim" and prof["title"] == "WoWSims v1.0.4 (Arcane, your gear)" and prof["w"] is e["w"], prof)
    check(gear.weights("MAGE", 59)["kind"] != "sim" and gear.weights("WARRIOR", 60)["kind"] != "sim", "only at 60, this class")
    item = {"loc": "INVTYPE_HEAD", "stats": {"IN": 10, "SP": 5, "CS": 1}}
    check(abs(gear.score(item, prof) - (8.65 + 5 + 9.038)) < 0.1, gear.score(item, prof))
    off = Gear({"kind": "Speedrun", "sims": False})
    off.set_sim(e)
    check(off.weights("MAGE", 60)["kind"] != "sim", "switched off in Settings: the shipped weights")

    # ---- unchanged: not weighed again; a newer WoWSims: weighed again, and said -------------------------------------
    n = ws.sims
    sw.tick()
    check(ws.sims == n, "nothing changed")
    ws.tag = "v1.0.5"
    sw.tick()
    check(ws.sims > n and sw.chars["Nine"]["wowsims"] == "v1.0.5" and len(said) == 2, "a newer WoWSims")

    # ---- gear changed: weighed once it settles, silently -------------------------------------------------------------
    st.gear["W"] = st.gear["W"][1:]
    n = ws.sims
    sw.tick()
    check(ws.sims == n, "settling")
    clock[0] += sim_weights.SETTLE_S + 1
    sw.tick()
    check(ws.sims > n and len(said) == 2, "weighed after the change settled, no chat line")

    # ---- a failure: tried again after RETRY_S, the old weights kept --------------------------------------------------
    st.tal = sim_weights.talent_list("0502052001001-55500001230133031-003")      # (Fire)
    ws.fail = "unknown field"
    sw.tick()
    clock[0] += sim_weights.SETTLE_S + 1
    sw.tick()
    check(sw.status["state"] == "error" and sw.chars["Nine"]["spec"] == "Arcane", sw.status)
    ws.fail = None
    n = ws.sims
    sw.tick()
    check(ws.sims == n, "not before RETRY_S")
    clock[0] += sim_weights.RETRY_S + 1
    sw.tick()
    check(sw.chars["Nine"]["spec"] == "Fire", "tried again later")

    # ---- failing every time: given up after GIVE_UP tries on this WoWSims, tried again on another -------------------
    st.tal = sim_weights.talent_list("203205-5-053500011121331251")             # (Frost)
    ws.fail = "the reference stat added no DPS"
    sw.tick()
    clock[0] += sim_weights.SETTLE_S + 1
    for _ in range(sim_weights.GIVE_UP):
        sw.tick()
        clock[0] += sim_weights.RETRY_S + 1
    n = ws.tries
    sw.tick()
    check(ws.tries == n and sw.chars["Nine"]["spec"] == "Fire", "given up on v1.0.5")
    ws.fail, ws.tag = None, "v1.0.6"
    sw.tick()
    check(sw.chars["Nine"]["spec"] == "Frost" and sw.chars["Nine"]["wowsims"] == "v1.0.6", "a new WoWSims: again")

    # ---- a fishing pole in hand: not weighed --------------------------------------------------------------------------
    pole = {"key": "16", "id": 6256, "loc": "INVTYPE_2HWEAPON", "cls": 2, "sub": 20, "stats": {}}
    st.gear["W"] = [it for it in st.gear["W"] if it["key"] not in ("16", "17")] + [pole]
    n = ws.sims
    sw.tick()
    clock[0] += sim_weights.SETTLE_S + 1
    sw.tick()
    check(ws.sims == n, "fishing")
    st.gear["W"] = [it for it in st.gear["W"] if it is not pole]

    # ---- off, below 60, another class: nothing weighed; another character's weights aren't this one's -------------
    for change in (lambda: on.__setitem__(0, False), lambda: setattr(st, "level", 59), lambda: setattr(st, "cls", "PRIEST")):
        st.level, st.cls, on[0] = 60, "MAGE", True
        st.gear["W"] = st.gear["W"][1:]
        change()
        n = ws.sims
        clock[0] += sim_weights.SETTLE_S + 1
        sw.tick()
        sw.tick()
        check(ws.sims == n, "nothing weighed")
    check(gear.sim is None, "a priest named Nine isn't the mage")

    # ---- an addon update put the empty file back: written again at the next poll; no addon folder: nothing written ---
    open(os.path.join(wow, sim_weights.HANDOFF), "w").write(shipped)
    sw.tick()
    check('["Nine"]' in open(os.path.join(wow, sim_weights.HANDOFF)).read(), "written again")
    check(sw.write_handoff() is False, "unchanged: not rewritten")
    shutil.rmtree(os.path.join(wow, "Interface"))
    check(sw.write_handoff() is False, "no Glide addon installed")
    check(sim_weights.lua_string('a"b\\c\n') == '"a\\"b\\\\c\\n"', "names are escaped")

    # ---- a restart: the weights come back from disk -----------------------------------------------------------------
    back = sim_weights.SimWeights(home, ws, lambda: st, Gear({}), lambda: None, lambda: True)
    check(back.chars["Nine"]["spec"] == "Frost", back.chars)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
