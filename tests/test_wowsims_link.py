#!/usr/bin/env python3
"""The "Open in WoWSims" link (companion/wowsims_link.py): WoWSims' IndividualSimSettings in protobuf's binary form,
zlib, standard base64, at https://www.wowsims.com/forever/<page>/?i=gtm. Pinned: protobuf's bytes for fields worked
out by hand (varints, a negative int32, a double, a string, enums, an empty item keeping its slot), the whole binary of
a fixture, and a round trip through a decoder written here (field numbers read off WoWSims v1.0.4's protos) of the
request simbuild builds: gear with its enchants and suffixes, talents, and misc complete (race, name, reaction time,
channel clip delay, distance, mage armor), api_version 2, nothing of the rotation, consumables or buffs; Enchanting
named when a ring is enchanted; the page by class and talent tree; a spec without known options leaves misc out.
Last, WoWSims' own runner reads the links back (`wowsimcli decodelink`): that needs a local WoWSims (GLIDE_WOWSIMS_HOME,
an engine home whose data/wowsims/<tag>/ has the runner, else %TEMP%/glide-wowsims-home); without one it skips (exit
77) after the rest has passed.

Run: python3 tests/test_wowsims_link.py   (prints OK)
"""
import base64
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import sim_weights  # noqa: E402
import simbuild  # noqa: E402
import wowsims  # noqa: E402
import wowsims_link as L  # noqa: E402

for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
          "TAVILY_API_KEY", "BRAVE_API_KEY"):
    os.environ[k] = ""


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


def raises(fn, what):
    try:
        fn()
    except simbuild.SimError:
        return
    check(False, what)


# ---- a protobuf decoder of its own: bytes -> {field number: [values]} (varint: int, 64-bit: bytes, length: bytes) ---
def varint(buf, i):
    n = shift = 0
    while True:
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return n, i


def parse(buf):
    out, i = {}, 0
    while i < len(buf):
        key, i = varint(buf, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            v, i = varint(buf, i)
        elif wire == 1:
            v, i = buf[i:i + 8], i + 8
        elif wire == 2:
            n, i = varint(buf, i)
            v, i = buf[i:i + n], i + n
        elif wire == 5:
            v, i = buf[i:i + 4], i + 4
        else:
            raise SystemExit("FAIL: wire type %d" % wire)
        out.setdefault(number, []).append(v)
    check(i == len(buf), "the message ends where its last field does")
    return out


def one(msg, number, default=None):
    v = msg.get(number)
    check(v is None or len(v) == 1, "field %d once" % number)
    return v[0] if v else default


def signed(n):
    return n - (1 << 64) if n >= 1 << 63 else n


# WoWSims v1.0.4's numbers (ui.proto IndividualSimSettings, api.proto Player, common.proto ItemSpec and enums)
SETTINGS_API, SETTINGS_PLAYER = 15, 3
P_RACE, P_CLASS, P_EQUIP, P_CONSUMES, P_BONUS, P_BUFFS, P_MAGE, P_TALENTS = 1, 2, 3, 4, 5, 8, 20, 39
P_PROF1, P_PROF2, P_ROTATION, P_REACTION, P_CLIP, P_DISTANCE, P_NAME, P_API = 41, 42, 44, 45, 46, 48, 51, 54
RACE = {"RaceHuman": 4, "RaceGnome": 3, "RaceUndead": 9}
CLASS = {"ClassMage": 8, "ClassWarlock": 9}
ENCHANTING, TAILORING, MAGE_ARMOR = 3, 11, 1


def hash_bytes(url):
    """The settings' binary a link carries."""
    check("#" in url, url)
    return zlib.decompress(base64.b64decode(url.split("#", 1)[1], validate=True))


def decode(url):
    """(settings, player) as {field: [values]} from a link."""
    s = parse(hash_bytes(url))
    return s, parse(one(s, SETTINGS_PLAYER, b""))


def items(player):
    """The equipment's items as ItemSpec protojson dicts (an empty one: {})."""
    out = []
    for raw in parse(one(player, P_EQUIP, b"")).get(1, []):
        it = parse(raw)
        d = {"id": one(it, 1, 0), "randomSuffix": signed(one(it, 2, 0)), "enchant": signed(one(it, 3, 0))}
        out.append({k: v for k, v in d.items() if v})
    return out


def mage_armor(player):
    opts = parse(one(parse(one(player, P_MAGE, b"")), 1, b""))
    return one(parse(one(opts, 1, b"")), 1, 0)


# ---- protobuf's bytes, worked out by hand ---------------------------------------------------------------------------
check(L._varint(0) == b"\x00" and L._varint(300) == b"\xac\x02", "varints")
check(L._varint(-1) == b"\xff" * 9 + b"\x01", "a negative int32: ten bytes")
check(L.encode("ItemSpec", {"id": 19147, "enchant": 2544}) == bytes.fromhex("08cb9501" "18f013"), "id, enchant")
check(L.encode("ItemSpec", {"id": 10249, "random_suffix": 12734}) == bytes.fromhex("088950" "10be63"),
      "a random suffix (its proto name read too)")
check(L.encode("ItemSpec", {"id": 0, "enchant": 0, "randomSuffix": 0}) == b"", "defaults left out")
check(L.encode("EquipmentSpec", {"items": [{}, {"id": 1}]}) == bytes.fromhex("0a00" "0a020801"),
      "an empty item keeps its slot")
check(L.encode("Player", {"distanceFromTarget": 20}) == bytes.fromhex("8103") + struct.pack("<d", 20.0), "a double")
check(L.encode("Player", {"name": "Nine"}) == bytes.fromhex("9a0304") + b"Nine", "a string")
check(L.encode("Player", {"race": "RaceHuman", "class": "ClassMage"}) == bytes.fromhex("0804" "1008"), "enums")
check(L.encode("Player", {"class": "ClassMage", "race": "RaceHuman", "consumables": {"flaskId": 1}})
      == bytes.fromhex("0804" "1008"), "in number order; fields not in the schema left out")
check(L.encode("Player", {"inFrontOfTarget": True, "mage": {}}) == bytes.fromhex("a20100" "f80201"),
      "a bool, an empty message")
raises(lambda: L.encode("Player", {"race": "RaceOgre"}), "a race WoWSims doesn't have")

# ---- a fixture: the whole binary, pinned --------------------------------------------------------------------------
FIX_ITEMS = [{} for _ in range(17)]
FIX_ITEMS[0] = {"id": 22267, "enchant": 2544}
FIX_ITEMS[3] = {"id": 10249, "randomSuffix": 12734}
FIX_ITEMS[10] = {"id": 12543, "enchant": 2588}             # (an enchanted ring: simmed for an enchanter)
FIX_PLAYER = {"name": "Glide", "race": "RaceGnome", "class": "ClassMage", "equipment": {"items": FIX_ITEMS},
              "consumables": {"flaskId": 13513}, "buffs": {"innervates": 1}, "rotation": {"type": "TypeAPL"},
              "mage": {"options": {"classOptions": {"defaultMageArmor": "MageArmorMageArmor"}}},
              "talentsString": "050205223101311531-5500003-003", "reactionTimeMs": 100, "channelClipDelayMs": 100,
              "distanceFromTarget": 20}
FIX_REQ = {"raid": {"parties": [{"players": [FIX_PLAYER]}]}, "simOptions": {"iterations": 1000, "randomSeed": 1}}
FIX_BYTES = bytes.fromhex(
    "1a8201"                                                 # player, 130 bytes
    "0803" "1008"                                            # race Gnome, class Mage
    "1a35"                                                   # equipment: 17 items
    "0a0708fbad0118f013" "0a00" "0a00" "0a0608895010be63" "0a00" "0a00" "0a00" "0a00" "0a00" "0a00"
    "0a0608ff61189c14" "0a00" "0a00" "0a00" "0a00" "0a00" "0a00"
    "a201060a040a020801"                                     # mage: options, class options, Mage Armor
    "ba021e" + b"050205223101311531-5500003-003".hex() +     # talents
    "c80203"                                                 # profession1 Enchanting (the enchanted ring)
    "e80264" "f00264"                                        # reaction time 100, channel clip delay 100
    "8103" "0000000000003440"                                # distance 20.0
    "9a0304" + b"Nine".hex() +                               # name: the character's
    "b00302"                                                 # player api_version 2
    "7802")                                                  # settings api_version 2
fix_url = L.link({"name": "Nine"}, FIX_REQ)
check(fix_url.startswith(L.SITE_PREFIX + "mage/dps/?i=gtm#"), fix_url)
check(L.SITE_PREFIX == "https://www.wowsims.com/forever/", L.SITE_PREFIX)
check(hash_bytes(fix_url) == FIX_BYTES, hash_bytes(fix_url).hex())
check(zlib.decompress(base64.b64decode(fix_url.split("#")[1])) == FIX_BYTES, "zlib-wrapped, standard base64")
check(re.fullmatch(r"[A-Za-z0-9+/]+=*", fix_url.split("#", 1)[1]), "standard base64's alphabet")
check(L.link({"name": "Nine"}, FIX_REQ) == fix_url, "the same link each time")

# ---- a round trip of the request simbuild builds -------------------------------------------------------------------
worn = [dict({"key": str(s), "id": i}, **sim_weights.SMOKE_EXTRA.get(s, {})) for s, i in sim_weights.SMOKE_GEAR]
CHAR = {"name": "Nine", "class": "MAGE", "race": "Human", "faction": "Alliance",
        "talents": sim_weights.talent_list(sim_weights.SMOKE_TALENTS), "worn": worn}
REQ = simbuild.request(CHAR, {"type": "TypeAPL", "priorityList": []}, 1000, 1)
PLAYER = REQ["raid"]["parties"][0]["players"][0]
url = L.link(CHAR, REQ)
check(url.startswith("https://www.wowsims.com/forever/mage/dps/?i=gtm#"), url)
check(len(url) < 2000, "a short link: %d" % len(url))
s, p = decode(url)
check(one(s, SETTINGS_API) == 2 and one(p, P_API) == 2, "api_version 2 on the settings and the player")
check(set(s) == {SETTINGS_API, SETTINGS_PLAYER}, sorted(s))
check(one(p, P_CLASS) == CLASS["ClassMage"] and one(p, P_RACE) == RACE["RaceHuman"], "class, race")
check(one(p, P_NAME) == b"Nine", "the character's name")
check(items(p) == PLAYER["equipment"]["items"] and len(items(p)) == simbuild.SIM_SLOTS, items(p))
check(any(it.get("enchant") for it in items(p)) and any(it.get("randomSuffix") for it in items(p)),
      "an enchant and a suffix went")
check(one(p, P_TALENTS).decode() == PLAYER["talentsString"] == sim_weights.SMOKE_TALENTS, "talents")
check(one(p, P_REACTION) == PLAYER["reactionTimeMs"] and one(p, P_CLIP) == PLAYER["channelClipDelayMs"], "misc")
check(struct.unpack("<d", one(p, P_DISTANCE))[0] == float(PLAYER["distanceFromTarget"]), "distance")
check(mage_armor(p) == MAGE_ARMOR, "Mage Armor")
check(not set(p) & {P_CONSUMES, P_BONUS, P_BUFFS, P_ROTATION, P_PROF1, P_PROF2}, sorted(p))
check(L.link({}, REQ) != url and one(decode(L.link({}, REQ))[1], P_NAME) == b"Glide",
      "no name: the request's")

# an enchanter's enchanted ring: Enchanting named, or the site drops the ring's enchant
ring_worn = [dict(it, enchant=2588) if it["key"] == "11" else it for it in worn]
ench = dict(CHAR, worn=ring_worn, skills=["Enchanting", "Tailoring"])
ench_req = simbuild.request(ench, {}, 1000, 1)
check(ench_req["raid"]["parties"][0]["players"][0]["equipment"]["items"][10].get("enchant") == 2588, "ring sent")
_, ep = decode(L.link(ench, ench_req))
check(one(ep, P_PROF1) == ENCHANTING and P_PROF2 not in ep, "Enchanting for the ring")
not_ench = simbuild.request(dict(CHAR, worn=ring_worn), {}, 1000, 1)       # (no Enchanting: simmed without it)
check(P_PROF1 not in decode(L.link(CHAR, not_ench))[1], "no enchanted ring: no profession")
own = json.loads(json.dumps(ench_req))
own["raid"]["parties"][0]["players"][0]["profession1"] = "Tailoring"
check(one(decode(L.link(ench, own))[1], P_PROF1) == TAILORING, "the request's own professions stay")

# ---- the page: the class's, by the tree with the most points --------------------------------------------------------
def page_of(cls, talents):
    return L.page({"raid": {"parties": [{"players": [{"class": cls, "talentsString": talents}]}]}})


check(page_of("ClassMage", "-0550031-") == "mage/dps", "mage")
check(page_of("ClassPaladin", "05-5-0523301") == "paladin/retribution", "retribution")
check(page_of("ClassPaladin", "55005-5") == "paladin/holy", "holy")
check(page_of("ClassDruid", "5-5503-") == "druid/feralcat", "feral: the cat page")
check(page_of("ClassPriest", "-55-55") == "priest/healer" and page_of("ClassPriest", "-5-55") == "priest/dps",
      "a tie: the first tree; shadow")
check(page_of("ClassWarrior", "") == "warrior/dps", "no talents: the first tree")
check(L.tree("050205223101311531-5500003-003") == 0 and L.tree("-0550031") == 1, "tree")
raises(lambda: page_of("ClassExtra1", "5"), "a class with no page")
raises(lambda: L.link(CHAR, {"raid": {"parties": []}}), "a request with no player")

# ---- a spec whose options the schema doesn't have: misc left out (gt), never half filled ----------------------------
lock = dict(FIX_PLAYER, **{"class": "ClassWarlock", "talentsString": "-2-55"})
del lock["mage"]
lock["warlock"] = {"options": {"armor": 1}}
lock_url = L.link({"name": "Nine"}, {"raid": {"parties": [{"players": [lock]}]}})
check(lock_url.startswith(L.SITE_PREFIX + "warlock/dps/?i=gt#"), lock_url)
_, lp = decode(lock_url)
check(set(lp) == {P_CLASS, P_EQUIP, P_TALENTS, P_API} and one(lp, P_CLASS) == CLASS["ClassWarlock"], sorted(lp))


# ---- WoWSims' own runner reads them back --------------------------------------------------------------------------
def local_runner():
    """The newest local WoWSims runner, or None."""
    home = os.environ.get("GLIDE_WOWSIMS_HOME") or os.path.join(tempfile.gettempdir(), "glide-wowsims-home")
    base = os.path.join(home, "data", "wowsims")
    try:
        tags = sorted((t for t in os.listdir(base) if wowsims.TAG.match(t)), reverse=True)
    except OSError:
        return None
    for tag in tags:
        exe = os.path.join(base, tag, wowsims.EXE)
        if os.path.isfile(exe):
            return exe
    return None


exe = local_runner()
if not exe:
    print("SKIP: no local WoWSims runner for decodelink (GLIDE_WOWSIMS_HOME); the encoder's checks passed")
    raise SystemExit(77)


def decodelink(u):
    r = subprocess.run([exe, "decodelink", u], capture_output=True, text=True, timeout=60,
                       creationflags=wowsims.NO_WINDOW)
    check(r.returncode == 0, "decodelink: %s" % (r.stderr or r.stdout)[-400:])
    return json.loads(r.stdout)


got = decodelink(url)
gp = got["player"]
check(got["apiVersion"] == 2 and gp["apiVersion"] == 2, got)
check(gp["name"] == "Nine" and gp["race"] == PLAYER["race"] and gp["class"] == PLAYER["class"], gp)
check(gp["equipment"] == PLAYER["equipment"], gp["equipment"])
check(gp["talentsString"] == PLAYER["talentsString"] and gp["mage"] == PLAYER["mage"], gp)
check(all(gp[k] == PLAYER[k] for k in ("reactionTimeMs", "channelClipDelayMs", "distanceFromTarget")), gp)
check(not set(gp) & {"consumables", "buffs", "rotation", "bonusStats", "profession1"}, sorted(gp))
fix = decodelink(fix_url)["player"]
check(fix["equipment"]["items"] == FIX_ITEMS and fix["profession1"] == "Enchanting" and fix["race"] == "RaceGnome",
      fix)
lock_got = decodelink(lock_url)["player"]
check(set(lock_got) == {"apiVersion", "class", "equipment", "talentsString"}, sorted(lock_got))
print("OK")
