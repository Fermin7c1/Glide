#!/usr/bin/env python3
"""Decode the WoW client caches (Cache/WDB/<locale>/*.wdb) to JSON.

The client stores what the SERVER sent: quest title, level, objectives (with the creature /
object / item IDs and counts that no addon API exposes), description and log texts; creature and
game-object names. On WoW Forever this is the richest channel for quest data.

Record layout adapted from Lodestar's tools/wdb.py (MIT, (c) 2026 Daniel Costa), verified there on
build 1.60.1.69913. Each record is checked (the text block must close exactly at the record end);
records that don't fit the layout are reported, never guessed.

    python wdb.py <WDB folder or locale folder> [-o out.json]
"""
import json, os, re, struct

HEADER = 24
KINDS = {b"TSQW": "quests", b"BOMW": "npcs", b"BOGW": "objects"}

Q_LEVEL, Q_MINLEVEL, Q_ZONE, Q_NEXT = 8, 16, 24, 36
Q_SPELLS, Q_NOBJ, Q_RACES, Q_OBJ = 64, 436, 440, 488
Q_TEXT_BITS = (9, 12, 12, 9, 10, 8, 10, 8, 11)
TEXT_NAMES = ("title", "objective", "details", "area", "portrait_giver_text", "portrait_giver_name",
              "portrait_turnin_text", "portrait_turnin_name", "completion_log")
OBJ_TYPES = {0: "monster", 1: "item", 2: "object", 3: "talkto", 14: "criteria", 15: "progressbar"}
RACE_ALLIANCE = 1 | 4 | 8 | 64 | 1024
RACE_HORDE = 2 | 16 | 32 | 128 | 256 | 512


def records(data):
    pos = HEADER
    while pos + 8 <= len(data):
        rid, size = struct.unpack_from("<II", data, pos)
        if not rid or not size or pos + 8 + size > len(data):
            break
        yield rid, data[pos + 8:pos + 8 + size]
        pos += 8 + size


def read_bits(p, pos, widths):
    out, bit = [], 0
    for w in widths:
        v = 0
        for _ in range(w):
            v = (v << 1) | ((p[pos + (bit >> 3)] >> (7 - (bit & 7))) & 1)
            bit += 1
        out.append(v)
    return out


def parse_objectives(p, pos, n):
    out = []
    for _ in range(n):
        oid, otype = struct.unpack_from("<Ii", p, pos)
        target, amount = struct.unpack_from("<ii", p, pos + 9)
        end = pos + 41 + 4 * struct.unpack_from("<i", p, pos + 33)[0]
        ln = p[end]
        out.append({"objective_id": oid, "type": OBJ_TYPES.get(otype, otype), "target": target,
                    "amount": amount, "text": p[end + 2:end + 2 + ln].decode("utf-8", "replace")})
        pos = end + 2 + ln
    return out, pos


def parse_quest(p):
    n = struct.unpack_from("<i", p, Q_NOBJ)[0]
    if not 0 <= n <= 20:
        raise ValueError("objective count %d" % n)
    start = Q_OBJ + 12 * struct.unpack_from("<i", p, Q_SPELLS)[0]
    objs, end = parse_objectives(p, start, n)
    sizes = read_bits(p, end, Q_TEXT_BITS)
    body = end + 12
    extra = None
    if sum(sizes) != len(p) - body:
        # Some Forever quests carry an extra block between the objectives and the text sizes
        # (seen: 4-20 zero/flag bytes, or conditional description texts). Find the size block by
        # its invariant: the nine sizes must add up exactly to the rest of the record.
        found = None
        for pos in range(end + 1, len(p) - 12):
            sz = read_bits(p, pos, Q_TEXT_BITS)
            if sz[0] and sum(sz) == len(p) - (pos + 12):
                try:
                    p[pos + 12:pos + 12 + sz[0]].decode("utf-8")
                except UnicodeDecodeError:
                    continue
                found = (pos, sz)
                break
        if not found:
            raise ValueError("text block does not close (%d != %d)" % (sum(sizes), len(p) - body))
        gap = p[end:found[0]]
        strings = [m.decode("utf-8", "replace") for m in re.findall(rb"[\x20-\x7e\xc2-\xf4][\x20-\x7e\x80-\xbf\xc2-\xf4]{15,}", gap)]
        extra = {"gap_bytes": len(gap), "texts": strings} if strings or len(gap) else None
        sizes, body = found[1], found[0] + 12
    texts, o = {}, body
    for name, size in zip(TEXT_NAMES, sizes):
        texts[name] = p[o:o + size].decode("utf-8", "replace")
        o += size
    races = struct.unpack_from("<Q", p, Q_RACES)[0]
    classic = races & 0x7FF
    side = "both"
    if classic and not classic & RACE_ALLIANCE:
        side = "horde"
    elif classic and not classic & RACE_HORDE:
        side = "alliance"
    q = {
        "name": texts["title"] or None,
        "level": struct.unpack_from("<i", p, Q_LEVEL)[0],
        "min_level": struct.unpack_from("<i", p, Q_MINLEVEL)[0],
        "zone_or_sort": struct.unpack_from("<i", p, Q_ZONE)[0],
        "next_in_chain": struct.unpack_from("<i", p, Q_NEXT)[0] or None,
        "races": races, "side": side,
        "objectives": objs,
    }
    q.update({k: v for k, v in texts.items() if k != "title" and v})
    if extra:
        q["extra"] = extra
    return q


def parse_npc(p):
    n = struct.unpack_from("<I", p, 4)[0]
    return {"name": p[15:15 + max(0, n - 1)].decode("utf-8", "replace")}


def parse_object(p):
    return {"name": p[8:p.find(b"\0", 8)].decode("utf-8", "replace")}


def decode_file(path):
    with open(path, "rb") as fh:
        data = fh.read()
    kind = KINDS.get(data[:4])
    if not kind or len(data) < HEADER:
        return None, {}, []
    build = struct.unpack_from("<I", data, 4)[0]
    out, fails = {}, []
    fn = {"quests": parse_quest, "npcs": parse_npc, "objects": parse_object}[kind]
    for rid, p in records(data):
        try:
            out[str(rid)] = fn(p)
        except Exception as e:  # layout drift: report, don't guess
            fails.append("%s %d: %s" % (kind, rid, e))
    return kind, {"build": build, "records": out}, fails


def decode_folder(folder):
    files = []
    for root, _, names in os.walk(folder):
        files += [os.path.join(root, f) for f in names if f.lower().endswith(".wdb")]
    result = {"quests": {}, "npcs": {}, "objects": {}, "builds": {}, "failures": []}
    for path in sorted(files):
        kind, res, fails = decode_file(path)
        if not kind:
            continue
        result[kind].update(res["records"])
        result["builds"][kind] = res["build"]
        result["failures"] += fails
    return result


def main(args):
    """python tools/wdb.py <folder> [-o out.json]: decode a folder of client caches, print a summary."""
    out = None
    if "-o" in args:
        out = args[args.index("-o") + 1]
        args = args[:args.index("-o")]
    res = decode_folder(args[0] if args else ".")
    summary = {k: len(res[k]) for k in ("quests", "npcs", "objects")}
    summary["failures"] = len(res["failures"])
    summary["builds"] = res["builds"]
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)
    print(json.dumps(summary))
    for f in res["failures"][:10]:
        print("  ", f)
