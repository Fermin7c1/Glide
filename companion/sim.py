"""Simulated run through your RestedXP guides, starting at Zephras Isle and following each guide's
#next chain toward 60. Every frame goes through the real codec path:
state -> payload -> symbols -> rendered pixels -> decode -> app. No game needed. Time is compressed
(about 10-20x), so XP/hour reads high."""
import math
import random
import re
import time

import os

import protocol
import versions
import log as console

SPEED = float(os.environ.get("GLIDE_SIM_SPEED", "1"))
ADDON_VERSION = versions.expected()     # the simulated addon is the one this engine expects

# Classic XP needed to go from level N to N+1 (index = N)
XP_TABLE = [0, 400, 900, 1400, 2100, 2800, 3600, 4500, 5400, 6500, 7600, 8800, 10100, 11400, 12900, 14400,
            16000, 17700, 19400, 21300, 23200, 25200, 27300, 29400, 31700, 34000, 36400, 38900, 41400, 44300,
            47400, 50800, 54500, 58600, 62800, 67100, 71600, 76100, 80800, 85700, 90700, 95800, 101000, 106300,
            111800, 117500, 123200, 129100, 135100, 141200, 147500, 153900, 160400, 167100, 173900, 180800,
            187900, 195000, 202300, 209800]
MAX_LEVEL = 60
CLASSES = {"warrior", "hunter", "mage", "priest", "rogue", "druid", "paladin", "shaman", "warlock"}
SIM_CLASS, SIM_FACTION, SIM_RACE = "Warrior", "Alliance", "Skyborne"


def applies(filters):
    """Rough RXP '<< ...' filter check for the simulated Skyborne Alliance warrior."""
    words = re.findall(r"!?[A-Za-z]+", filters or "")
    if not words:
        return True
    pos = [w.lower() for w in words if not w.startswith("!")]
    neg = [w[1:].lower() for w in words if w.startswith("!")]
    if SIM_CLASS.lower() in neg or SIM_FACTION.lower() in neg or SIM_RACE.lower() in neg:
        return False
    cls = [w for w in pos if w in CLASSES]
    if cls and SIM_CLASS.lower() not in cls:
        return False
    if "horde" in pos and "alliance" not in pos:
        return False
    return True


def find_guide(k, name, prefer_group=None):
    name = (name or "").split(";")[0].split("<<")[0].strip()
    if "\\" in name:
        name = name.split("\\")[-1].strip()
    if not name:
        return None
    rows = k.q("""SELECT guide_key, name, grp, next FROM rxp_guides WHERE name=?
                  ORDER BY grp=? DESC, source LIKE 'owned%' DESC,
                  (SELECT count(*) FROM rxp_steps s WHERE s.guide_key=rxp_guides.guide_key) DESC LIMIT 1""",
               name, prefer_group or "")
    return dict(rows[0]) if rows else None


def map_id(k, zone):
    if zone is None:
        return None
    r = k.q("SELECT ui_map FROM maps WHERE name=? ORDER BY ui_map LIMIT 1", zone)
    return r[0]["ui_map"] if r else None


def load_route(k, guide_key):
    rows = k.q("""SELECT step, verb, quest_id, ui_map, zone, x, y, text, filters, line_filter FROM rxp_steps
                  WHERE guide_key=? ORDER BY step, line""", guide_key)
    steps, cur = [], None
    for r in rows:
        if cur is None or r["step"] != cur["i"]:
            cur = {"i": r["step"], "go": None, "acts": [], "text": "", "ok": applies(r["filters"])}
            steps.append(cur)
        if not applies(r["line_filter"]):
            continue
        if r["verb"] == "goto" and r["x"] is not None:
            m = r["ui_map"] or map_id(k, r["zone"])
            if m:
                cur["go"] = (m, r["x"], r["y"])
        elif r["verb"] in ("accept", "turnin", "complete") and r["quest_id"]:
            cur["acts"].append((r["verb"], r["quest_id"]))
            cur["text"] = cur["text"] or (r["text"] or "")
    return [s for s in steps if s["ok"] and (s["go"] or s["acts"])]


def run(app, grid):
    k = app.k
    cap = (grid["cols"] * grid["rows"] - 2) // 4 * 3
    P = {"l": 1, "x": 0, "r": 1200, "h": 100, "m": 100, "c": 0, "mp": 2521, "px": 40.0, "py": 22.0,
         "g": 0, "b": 14, "du": 100, "kc": 0, "kx": 0, "step": 0, "zone": "Zephras Isle"}
    log = {}
    ring, ev, seq, rr = [], [0], [0], [0]
    # simulated buffs: Battle Shout (mine, 2 min) gets recast ~15 s after it drops; Fortitude from a priest
    BUFFS = {"6673": ("Battle Shout", 120, True), "1243": ("Power Word  Fortitude", 1800, False)}
    active = {k: time.time() + v[1] for k, v in BUFFS.items()}
    recast_at = {}

    # simulated gear: a 2H weapon and chest; quest rewards sometimes offer an upgrade, bag drops too
    gear = {"W": ["16:2361:INVTYPE_2HWEAPON:2:5:5:1:1:1:Battleworn Hammer:DP3.5/WS2.9",
                  "5:6125:INVTYPE_CHEST:4:2:5:1:1:1:Brawler Harness:AR30/SA1"], "I": [], "R": []}
    turnins = [0]

    def ghash(kind):
        joined = ",".join(gear[kind])
        return joined, protocol.list_hash(joined)

    def loot_item(level):
        n = random.choice([("Militia Warhammer", "INVTYPE_2HWEAPON", 2, 5, "DP%.1f/ST%d/WS3.3" % (3 + level * 0.55, level // 3)),
                           ("Chainmail Vest", "INVTYPE_CHEST", 4, 3, "AR%d/SA%d/ST%d" % (40 + level * 9, level // 4, level // 5)),
                           ("Linen Cloak", "INVTYPE_CLOAK", 4, 1, "AR%d/SA%d" % (5 + level, level // 6))])
        return "%s:%d:%s:%d:%d:%d:2:%d:1:%s:%s" % ("{key}", 900 + level, n[1], n[2], n[3], level + 3,
                                                 max(1, level - 2), n[0], n[4])

    def buff_tick():
        now = time.time()
        for k in list(active):
            if active[k] <= now:
                del active[k]
                if BUFFS[k][2]:
                    recast_at[k] = now + 15
        for k, t in list(recast_at.items()):
            if t <= now:
                active[k] = now + BUFFS[k][1]
                del recast_at[k]

    def buff_page():
        ents = ["%s:%d:%d:%s:%s" % (k, (int(round(e / 2.0)) * 2) % 1000000, BUFFS[k][1], "m" if BUFFS[k][2] else "",
                                     BUFFS[k][0]) for k, e in sorted(active.items())]
        joined = ",".join(ents)
        return joined, protocol.list_hash(joined)

    def quest_page():
        """The quest log as one chunk and its hash (the engine takes the log only when the core's qh matches)."""
        q = ",".join("%d:%d:%d/%d" % (qid, 1 if v["done"] else 0, v["have"], v["need"]) for qid, v in log.items())[:110]
        return q, protocol.list_hash(q)

    def event(kind, arg=""):
        ev[0] += 1
        ring.insert(0, "%d:%s:%s" % (ev[0], kind, arg))
        del ring[3:]

    def gain(n):
        if P["l"] >= MAX_LEVEL:
            return
        P["x"] += n
        while P["l"] < MAX_LEVEL and P["x"] >= XP_TABLE[P["l"]]:
            P["x"] -= XP_TABLE[P["l"]]
            P["l"] += 1
            event("lvl", P["l"])
        if P["l"] >= MAX_LEVEL:
            P["x"] = 0

    def send(page):
        # the core as State.lua's ns.Core() writes it, in its order: version, gauge mask (no gauges are drawn
        # here: 0), coach switches (all on), then the player
        core = {"gv": ADDON_VERSION, "gk": 0, "cf": 255,
                "t": int(time.time()) % 1000000, "l": P["l"], "x": P["x"],
                "X": XP_TABLE[P["l"]] if P["l"] < MAX_LEVEL else 0,
                "r": P["r"], "h": P["h"], "m": P["m"], "pt": "RAGE", "cl": "WARRIOR", "c": P["c"], "d": 0, "mp": P["mp"],
                "px": int(P["px"] * 100), "py": int(P["py"] * 100), "f": int(P.get("f", 0) * 100), "sp": 70, "g": P["g"], "b": P["b"],
                "du": P["du"], "hs": 0, "rs": 0, "mo": 0, "tx": 0, "kc": P["kc"], "kx": P["kx"],
                "st": P["step"], "qn": 1, "qh": quest_page()[1]}
        buff_tick()
        joined, h = buff_page()
        core.update({"an": 1, "ah": h, "ak": 0})
        for kind, ks in (("W", ("wn", "wh")), ("I", ("in", "ih")), ("R", ("rn", "rh"))):
            joined_g, gh = ghash(kind)
            core[ks[0]], core[ks[1]] = (1 if gear[kind] else 0), gh
            if page.startswith("pg=%s0" % kind):
                page = "pg=%s0;g=%s" % (kind, joined_g)
        if page.startswith("pg=A0"):
            page = "pg=A0;a=" + joined
        for i, e in enumerate(ring[:3]):
            core["e%d" % (i + 1)] = e
        payload = ";".join("%s=%s" % kv for kv in core.items()) + "|" + page
        seq[0] = (seq[0] + 1) % 256
        if SPEED > 1 and seq[0] % 20:          # fast mode: only every 20th frame goes through pixels
            app.attempts += 1
            app.decoded += 1
            app.on_frame(seq[0], payload)
            return
        img = protocol.render(protocol.encode_to_symbols(seq[0], payload, cap), grid["cols"], grid["rows"], 3,
                              binary=True)      # the strip the addon draws (yellow marker), not the old 6-bit one
        res, _ = protocol.decode_image(img, grid["cols"], grid["rows"])
        app.attempts += 1
        if res:
            app.decoded += 1
            app.on_frame(*res)

    def pages(guide, route, si):
        out = ["pg=B;cl=WARRIOR;ra=%s;fa=%s;nm=Simulated;bd=Zephras Isle" % (SIM_RACE, SIM_FACTION),
               "pg=Z;zn=%s;sz=;mm=%s;mw=3000;mh=2000" % (P["zone"], P["mp"]),
               "pg=G;gn=%s;gg=%s;gx=" % (guide["name"], guide["grp"])]
        for j in range(3):
            if si + j < len(route):
                s = route[si + j]
                els = (["go:%s:%.1f:%.1f" % s["go"]] if s["go"] else []) + ["%s:%s" % a for a in s["acts"]]
                out.append("pg=S%d;i=%d;el=%s;tx=%s" % (j, s["i"], ",".join(els)[:70],
                                                      s["text"][:60].replace(";", ",").replace("|", ",")))
            else:
                out.append("pg=S%d;i=" % j)
        out.append("pg=Q0;q=" + quest_page()[0])
        out.append("pg=A0;a=" + buff_page()[0])
        for kind in ("W", "I", "R"):
            if gear[kind]:
                out.append("pg=%s0;g=" % kind)
        return out

    def pump(pl, n):
        for _ in range(n):
            rr[0] += 1
            send(pl[rr[0] % len(pl)])
            time.sleep(0.2 / SPEED)

    guide = find_guide(k, "1-14 Zephras Isle", "RestedXP Forever Guide (A)")
    seen = set()
    while guide and guide["guide_key"] not in seen and P["l"] < MAX_LEVEL:
        seen.add(guide["guide_key"])
        route = load_route(k, guide["guide_key"])
        console.log("sim", "guide %s (%d steps)" % (guide["name"], len(route)))
        for si, step in enumerate(route):
            P["step"] = step["i"]
            pl = pages(guide, route, si)
            if step["go"]:
                m, gx, gy = step["go"]
                if m != P["mp"]:               # new zone: arrive by "flight"
                    event("taxi")
                    P["mp"], P["px"], P["py"] = m, gx + random.uniform(-6, 6), gy + random.uniform(-6, 6)
                    P["zone"] = k.map_name(m) or str(m)
                    event("zone", P["zone"])
                    pl = pages(guide, route, si)
                while True:
                    dx, dy = gx - P["px"], gy - P["py"]
                    d = math.hypot(dx, dy)
                    if d < 0.4:
                        break
                    v = min(d, 0.6)
                    P["f"] = (2 * math.pi - math.atan2(dx * 1.5, -dy)) % (2 * math.pi)   # WoW facing
                    P["px"] += dx / d * v + random.uniform(-0.05, 0.05)
                    P["py"] += dy / d * v + random.uniform(-0.05, 0.05)
                    pump(pl, 1)
            for verb, qid in step["acts"]:
                info = k.quest(qid, detail=False)
                if verb == "accept":
                    need = (info.get("objectives") or [{}])[0].get("need") or 0
                    log[qid] = {"have": 0, "need": min(need or 0, 12), "done": not need}
                    while len(log) > 20:                         # abandon the oldest
                        old = next(iter(log))
                        log.pop(old)
                        event("rem", old)
                    event("acc", qid)
                elif verb == "complete" and qid in log:
                    P["c"] = 1
                    while log[qid]["have"] < log[qid]["need"]:
                        log[qid]["have"] += 1
                        P["kc"] += 1
                        P["kx"] = 40 + P["l"] * 5
                        gain(P["kx"])
                        P["h"] = random.randint(25, 95)
                        pump(pages(guide, route, si), 3)
                    log[qid]["done"] = True
                    P["c"], P["h"] = 0, 100
                elif verb == "turnin":
                    turnins[0] += 1
                    if turnins[0] % 5 == 0:          # reward window with choices for ~2 s
                        gear["R"] = [loot_item(P["l"]).replace("{key}", str(i + 1)) for i in range(2)]
                        pump(pages(guide, route, si), 10)
                        gear["R"] = []
                    if turnins[0] % 7 == 0 and len(gear["I"]) < 3:   # something drops into the bags
                        gear["I"].append(loot_item(P["l"]).replace("{key}", "0.%d" % (len(gear["I"]) + 1)))
                    log.pop(qid, None)
                    xp = info.get("xp") or 100 + P["l"] * 60
                    event("tin", "%s,%s" % (qid, xp))
                    gain(xp)
                pump(pages(guide, route, si), 3)
        guide = find_guide(k, guide.get("next"), guide["grp"])
    console.log("sim", "finished at level %d" % P["l"])
    while True:                                # keep the dashboard alive
        pump(pages(guide or {"name": "", "grp": ""}, [], 0), 1)
        time.sleep(1)
