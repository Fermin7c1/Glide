"""Stat weights WoWSims measures on each level-60 character's own gear and talents (wowsims.py runs it, simbuild.py
builds the requests): the coach's gear advice at 60 uses them (gear.Gear.set_sim), and so does the addon's upgrade
line on item tooltips (addon Compare.lua, from Data/SimWeights.lua).

Every POLL_S the worker reads the game state. A character WoWSims sims (simbuild.simmable) is weighed again when its
gear (an enchant or random suffix too), talents or spec changed and stayed so for SETTLE_S (not while a fishing pole
is in hand), or when a newer WoWSims came in: the newest release that runs (WoWSims.ensure), the rotation it gives the
spec, simbuild.stat_weights (with WoWSims' item database when item_db_fn has it loaded: what it doesn't know of the
gear's enchants and suffixes is left out). The result, per character name, goes to data/sim_weights.json and to
<wow>/Interface/AddOns/Glide/Data/SimWeights.lua (read at the next login or /reload; it is written again whenever it
differs, since an addon update puts back the empty one), and the chat says so the first time and after a new
WoWSims. A character that can't be weighed (WoWSims can't run, a sim failed) is tried again after RETRY_S, and after
GIVE_UP failures only with another WoWSims.

data/sim_weights.json: {"chars": {name: {"name", "class", "spec", "dps", "per_ref", "w", "wowsims", "t", "key"}}}.
"""
import json
import os
import threading
import time

import jsonfile
import simbuild
from log import log, log_once

POLL_S = 15
SETTLE_S = 120
RETRY_S = 600
GIVE_UP = 3
FISHING_POLE = (2, 20)                                       # item class, subclass
ITERATIONS = 10000
SEED = 1
HANDOFF = os.path.join("Interface", "AddOns", "Glide", "Data", "SimWeights.lua")


def lua_string(s):
    """A Lua string literal for any text."""
    out = []
    for ch in str(s):
        if ch in "\\\"":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ord(ch) < 32:
            out.append("\\%03d" % ord(ch))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def handoff_text(chars):
    """Data/SimWeights.lua for the addon: ns.simWeights[name] = {class, spec, dps, perRef, wowsims, w = {CODE = EP}}."""
    rows = []
    for name in sorted(chars):
        e = chars[name]
        w = ", ".join("%s = %s" % (code, repr(float(v))) for code, v in sorted(e["w"].items()) if code.isalnum())
        rows.append("    [%s] = { class = %s, spec = %s, dps = %s, perRef = %s, wowsims = %s,\n        w = { %s } },"
                    % (lua_string(name), lua_string(e["class"]), lua_string(e["spec"]), repr(float(e["dps"])),
                       repr(float(e["per_ref"])), lua_string(e.get("wowsims") or ""), w))
    return ("-- Written by the Glide app (companion/sim_weights.py): the stat weights WoWSims (wowsims.com/forever)\n"
            "-- measured on each level-60 character's own gear and talents, read at login and /reload by Compare.lua.\n"
            "-- The app rewrites this file.\n"
            "local _, ns = ...\n"
            "ns.simWeights = {\n" + "".join(r + "\n" for r in rows) + "}\n")


class SimWeights:
    """The worker: start() polls in its own thread; tick() is one poll (the tests call it). item_db_fn -> WoWSims'
    item database (item_db.ItemDB: research.ItemDBSource) or None; sim_fn(request, tag) -> DPS runs each sim (the
    sim queue's sim, sim_queue.SimQueue.sim, in the background; None: ws.sim, at once)."""

    def __init__(self, home, ws, state_fn, gear, wow_dir_fn, enabled_fn, emit=None, now=time.time, item_db_fn=None,
                 sim_fn=None):
        self.path = os.path.join(home, "data", "sim_weights.json")
        self.ws, self.state_fn, self.gear, self.wow_dir_fn = ws, state_fn, gear, wow_dir_fn
        self.enabled_fn, self.emit, self._now, self.item_db_fn = enabled_fn, emit, now, item_db_fn
        self.sim_fn = sim_fn
        self.chars = self._load()
        self.pending, self.pending_t = None, 0.0        # what changed, and since when (settling)
        self.failed = {}                                 # key -> {"t", "tag", "n"}: when, on which WoWSims, how often
        self.status = {"state": "idle", "error": None}

    def _load(self):
        try:
            d = jsonfile.read(self.path)
        except (OSError, ValueError):
            return {}
        chars = d.get("chars") if isinstance(d, dict) else None
        return {k: v for k, v in (chars or {}).items()
                if isinstance(v, dict) and isinstance(v.get("w"), dict) and v.get("class")}

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        jsonfile.write_atomic(self.path, {"chars": self.chars}, ensure_ascii=False, indent=1)

    def write_handoff(self):
        """The weights into the addon's Data/SimWeights.lua, when the Glide addon is installed and it differs."""
        wow = self.wow_dir_fn()
        if not wow or not os.path.isdir(os.path.join(wow, "Interface", "AddOns", "Glide")):
            return False
        path = os.path.join(wow, HANDOFF)
        text = handoff_text(self.chars)
        try:
            with open(path, encoding="utf-8") as fh:
                if fh.read() == text:
                    return False
        except OSError:
            pass
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".part", "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(path + ".part", path)
        return True

    def start(self):
        def loop():
            while True:
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001 - the next poll tries again
                    log_once("sim_weights.tick", e)
                time.sleep(POLL_S)
        threading.Thread(target=loop, daemon=True, name="sim-weights").start()
        return self

    @staticmethod
    def character(st, s):
        """What WoWSims sims of the player now, or None (gear or talents not in yet)."""
        talents = st.talents()
        worn = st.gear.get("W") or []
        if not talents or not worn or any((it.get("cls"), it.get("sub")) == FISHING_POLE for it in worn):
            return None
        return {"name": s.get("name"), "class": str(s.get("class") or "").upper(), "race": s.get("race"),
                "faction": s.get("faction"), "talents": talents, "worn": worn}

    @staticmethod
    def key(char):
        """What the weights depend on: how they're measured, class, race, talents, the items worn with their enchants
        and random suffixes (0: none, or an addon that doesn't send them), and whether a ring's enchant counts."""
        return json.dumps([simbuild.VERSION, char["class"], char["race"], char["faction"],
                           simbuild.talents_string(char["talents"]),
                           sorted((str(it.get("key")), it.get("id"), simbuild.as_id(it.get("enchant")),
                                   simbuild.as_id(it.get("suffix"))) for it in char["worn"]),
                           simbuild.enchanter(char.get("skills"))])

    def _item_db(self):
        """WoWSims' item database for the request (what it doesn't know of the gear's enchants and suffixes is left
        out), or None (not loaded yet: everything known is sent)."""
        try:
            return self.item_db_fn() if self.item_db_fn else None
        except Exception as e:  # noqa: BLE001 - weighed without it
            log_once("sim_weights.item_db", e)
            return None

    def tick(self):
        try:
            self.write_handoff()                         # (an addon update puts the empty file back)
        except OSError as e:
            log_once("sim_weights.handoff", e)
        st = self.state_fn()
        s = (st.summary() if st is not None else None) or {}
        name = s.get("name")
        if not name:
            return
        mine = self.chars.get(name)
        if not (mine and mine.get("class") == str(s.get("class") or "").upper()):
            mine = None
        self.gear.set_sim(mine)
        if not self.enabled_fn() or not simbuild.simmable(s):
            return
        char = self.character(st, s)
        if char is None:
            return
        key, now = self.key(char), self._now()
        f = self.failed.get(key)
        if f and (now - f["t"] < RETRY_S or (f["n"] >= GIVE_UP and f["tag"] == self.ws.current())):
            return
        if key != self.pending:
            self.pending, self.pending_t = key, now
        if mine is not None and mine.get("key") == key:
            # weighed as it is: again only for a newer WoWSims (ensure looks at most once a day, and after a check
            # that failed not before its own RETRY_S)
            if self.ws.ensure() in (None, mine.get("wowsims")):
                return
        elif mine is not None and now - self.pending_t < SETTLE_S:
            return                                   # (gear or talents changing: weighed once they settle)
        self.weigh(char, key)                        # (the first time: at once; the addon's lists come in whole)

    def weigh(self, char, key):
        """Weigh the character now: the newest WoWSims that runs, its rotation for the spec. -> the entry, or None."""
        self.status = {"state": "running", "error": None}
        before = self.chars.get(char["name"])
        try:
            tag = self.ws.ensure()
            if not tag:
                raise simbuild.SimError("WoWSims couldn't be downloaded or doesn't run yet")
            conf = simbuild.CLASSES[char["class"]]
            spec = simbuild.spec_of(char["class"], char["talents"])
            rotation = json.loads(self.ws.file(conf["apl"][spec], tag).decode("utf-8"))
            db, run = self._item_db(), self.sim_fn or self.ws.sim
            res = simbuild.stat_weights(char["class"], lambda req: run(req, tag),
                                        lambda bonus: simbuild.request(char, rotation, ITERATIONS, SEED, bonus, db))
        except Exception as e:  # noqa: BLE001 - tried again after RETRY_S
            n = (self.failed.get(key) or {}).get("n", 0) + 1
            self.failed[key] = {"t": self._now(), "tag": self.ws.current(), "n": n}
            self.status = {"state": "error", "error": str(e)[:300]}
            log("sim_weights", "couldn't weigh %s's gear: %s" % (char["name"], e))
            return None
        entry = dict(res, name=char["name"], spec=spec, wowsims=tag, t=self._now(), key=key,
                     **{"class": char["class"]})
        self.chars[char["name"]] = entry
        self._save()
        try:
            self.write_handoff()
        except OSError as e:
            log_once("sim_weights.handoff", e)
        self.gear.set_sim(entry)
        self.status = {"state": "ready", "error": None}
        log("sim_weights", "%s (%s): %.0f DPS, %s (WoWSims %s)" % (char["name"], spec, entry["dps"],
                                                                    json.dumps(entry["w"]), tag))
        if self.emit and (before is None or before.get("wowsims") != tag):
            self.emit({"kind": "info", "speak": False, "text": tell(entry)})
        return entry


# a new WoWSims is tried on this before it's used: WoWSims' own Arcane preset (its pre-raid gear and talents)
SMOKE_GEAR = [(1, 226935), (2, 22403), (3, 226936), (15, 272411), (5, 14152), (9, 226929), (10, 226930), (6, 18405),
              (7, 226933), (8, 226931), (11, 272406), (12, 12543), (13, 22268), (14, 13968), (16, 22383),
              (17, 19315), (18, 13938)]
SMOKE_TALENTS = "050205223101311531-5500003-003"
# ... and the request's enchant and random suffix fields in it: Arcanum of Focus on the head, a Master's Cloak of Magic
SMOKE_EXTRA = {1: {"enchant": 2544}, 15: {"id": 10249, "suffix": 12734}}


def talent_list(string):
    """The addon's talent list for a WoWSims talent string (each tree's talents four to a row: the order is all the
    string keeps)."""
    out = []
    for tab, run in enumerate(string.split("-"), 1):
        for i, ch in enumerate(run):
            out.append({"pos": "%d%d%d" % (tab, i // 4 + 1, i % 4 + 1), "rank": int(ch), "max": 5, "name": ""})
    return out


def smoke(ws, tag):
    """Raises unless the release sims Glide's request for WoWSims' own Arcane preset, with an enchant and a random
    suffix as Glide sends them (SMOKE_EXTRA)."""
    worn = [dict({"key": str(s), "id": i}, **SMOKE_EXTRA.get(s, {})) for s, i in SMOKE_GEAR]
    char = {"name": "Glide", "class": "MAGE", "race": "Human", "faction": "Alliance",
            "talents": talent_list(SMOKE_TALENTS), "worn": worn}
    rotation = json.loads(ws.file(simbuild.CLASSES["MAGE"]["apl"]["Arcane"], tag).decode("utf-8"))
    if not ws.sim(simbuild.request(char, rotation, 200, 1), tag) > 0:
        raise simbuild.SimError("it simmed no DPS")


def tell(entry):
    """The chat line for new weights: the main ones, in the reference stat's points."""
    w = entry["w"]
    parts = []
    for code, label in (("IN", "intellect"), ("CS", "1% crit"), ("HS", "1% hit"), ("SI", "spirit"), ("MR", "mp5")):
        if w.get(code):
            parts.append("%s %s" % (label, ("%.2f" % w[code]).rstrip("0").rstrip(".")))
    return ("WoWSims %s weighed your %s gear (%.0f DPS on its raid boss). Worth in spell power: %s. The coach's gear "
            "advice uses them, and item tooltips in the game after your next /reload." % (
                entry.get("wowsims") or "", entry.get("spec") or "", entry["dps"], ", ".join(parts) or "none"))
