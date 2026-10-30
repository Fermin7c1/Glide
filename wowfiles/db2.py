#!/usr/bin/env python3
"""Read client DB2 tables (WDC3 / WDC4 / WDC5) into dicts, with column names from WoWDBDefs .dbd files.

    from wowfiles.db2 import read_db2
    rows = read_db2(open("AreaTable.db2", "rb").read(), "tools/dbd/AreaTable.dbd")   # [{"ID": 12, ...}]

Handles the field storage kinds (plain, bitpacked, signed, common data, pallet, pallet arrays), inline and
non-inline IDs, the copy table, the relationship map (foreign key column) and strings. Sections whose data is
missing (encrypted with a key we don't have: all zero) are skipped. Sparse (offset map) tables are read too.
Format: wowdev.wiki/DB2.
"""
import re
import struct


class DB2Error(Exception):
    pass


# ---------------------------------------------------------------------------------------------- .dbd
def parse_dbd(text):
    """-> (columns {name: type}, [layout dict {"layouts": set, "fields": [...]}])."""
    cols, layouts, cur = {}, [], None
    mode = None
    for raw in text.splitlines():
        ln = raw.strip()
        if ln == "COLUMNS":
            mode = "cols"
            continue
        if not ln:
            if mode == "cols":
                mode = None
            cur = None
            continue
        if mode == "cols":
            m = re.match(r"(\w+)(?:<[^>]*>)?\s+(\w+)\??", ln)
            if m:
                cols[m.group(2)] = m.group(1)
            continue
        if ln.startswith("LAYOUT"):
            if cur is None:
                cur = {"layouts": set(), "builds": [], "fields": []}
                layouts.append(cur)
            cur["layouts"].update(x.strip().upper() for x in ln[6:].split(","))
            continue
        if ln.startswith("BUILD"):
            if cur is None:
                cur = {"layouts": set(), "builds": [], "fields": []}
                layouts.append(cur)
            cur["builds"].append(ln[5:].strip())
            continue
        if ln.startswith("COMMENT"):
            continue
        if cur is None:
            continue
        m = re.match(r"(?:\$([\w,]+)\$)?(\w+)(?:<(u?)(\d+)>)?(?:\[(\d+)\])?", ln)
        if not m:
            continue
        ann = set((m.group(1) or "").split(",")) - {""}
        cur["fields"].append({"name": m.group(2), "ann": ann, "unsigned": m.group(3) == "u",
                              "bits": int(m.group(4)) if m.group(4) else None,
                              "array": int(m.group(5)) if m.group(5) else 1})
    return cols, layouts


def layout_hash(data):
    """The layout hash in a DB2 file's header (which .dbd LAYOUT describes it)."""
    if data[:4] not in (b"WDC3", b"WDC4", b"WDC5"):
        raise DB2Error("unsupported DB2 format %r" % data[:4])
    pos = 4 + (4 + 128 if data[:4] == b"WDC5" else 0)
    return struct.unpack_from("<6I", data, pos)[5]


def has_layout(dbd_text, h):
    """Does this .dbd describe layout hash h?"""
    want = "%08X" % h
    return any(want in lay["layouts"] for lay in parse_dbd(dbd_text)[1])


def _layout(layouts, layout_hash):
    h = "%08X" % layout_hash
    for lay in layouts:
        if h in lay["layouts"]:
            return lay
    raise DB2Error("layout %s not in the .dbd (update tools/dbd from WoWDBDefs)" % h)


# ---------------------------------------------------------------------------------------------- WDC
def generic_layout(fsi):
    """A layout from the file's own field storage info, for a table whose .dbd doesn't know its layout hash (a game
    patch changed it): every field unnamed ("f0", "f1", ...), unsigned, plain arrays split into 32-bit (or 16/8-bit)
    elements. Enough to find columns by their values (tools/extract_maps.py, stage "spells")."""
    fields = []
    for fi, (_ob, size_bits, _add, kind, _v1, _v2, v3) in enumerate(fsi):
        f = {"name": "f%d" % fi, "ann": set(), "unsigned": True, "bits": None, "array": 1}
        if kind == 0:
            esize = 32 if size_bits % 32 == 0 else 16 if size_bits % 16 == 0 else 8
            f["bits"], f["array"] = esize, max(1, size_bits // esize)
        elif kind in (1, 5):
            f["bits"] = size_bits
        elif kind == 4:
            f["array"] = v3
        fields.append(f)
    return {"layouts": set(), "builds": [], "fields": fields}


def read_db2(data, dbd_text_or_path=None, keep_missing=False):
    """Rows as dicts named by the .dbd; with no .dbd (None), named f0, f1, ... from the file's own field info."""
    if dbd_text_or_path is None:
        cols, layouts = {}, None
    else:
        if "\n" not in dbd_text_or_path:
            dbd_text_or_path = open(dbd_text_or_path, encoding="utf-8").read()
        cols, layouts = parse_dbd(dbd_text_or_path)
    magic = data[:4]
    if magic not in (b"WDC3", b"WDC4", b"WDC5"):
        raise DB2Error("unsupported DB2 format %r" % magic)
    pos = 4
    if magic == b"WDC5":
        pos += 4 + 128                                   # version, schema string
    (rec_count, field_count, rec_size, str_size, _table_hash, layout_hash, _min_id, _max_id, _locale, flags,
     id_index, total_fields, _bitpacked_off, _lookup_cols, fsi_size, common_size, pallet_size,
     section_count) = struct.unpack_from("<IIIIIIIIIHHIIIIIII", data, pos)
    pos += 4 * 16 + 4
    if layouts is not None:
        lay = _layout(layouts, layout_hash)
    sections = []
    for _ in range(section_count):
        (tact, f_off, s_recs, s_str, off_end, idl_size, rel_size, offmap_n, copy_n) = \
            struct.unpack_from("<QIIIIIIII", data, pos)
        pos += 40
        sections.append(dict(tact=tact, off=f_off, recs=s_recs, strs=s_str, off_end=off_end, idl=idl_size,
                             rel=rel_size, offmap=offmap_n, copy=copy_n))
    pos += 4 * field_count                               # field structure (size, offset) - fsi has what we need
    fsi = []
    for i in range(fsi_size // 24):
        fsi.append(struct.unpack_from("<HHIIIII", data, pos + 24 * i))
    pos += fsi_size
    if layouts is None:
        lay = generic_layout(fsi)
    pallet_start = pos
    pos += pallet_size
    common_start = pos
    # pallet / common data per field
    pal, com = {}, {}
    p_off = c_off = 0
    for fi, (_ob, _sb, add, kind, v1, _v2, _v3) in enumerate(fsi):
        if kind in (3, 4):
            pal[fi] = pallet_start + p_off
            p_off += add
        elif kind == 2:
            d = {}
            for j in range(add // 8):
                k, v = struct.unpack_from("<II", data, common_start + c_off + 8 * j)
                d[k] = v
            com[fi] = (d, v1)
            c_off += add
    # the inline fields of the layout, in record order
    inline = [f for f in lay["fields"] if "noninline" not in f["ann"]]
    if len(inline) != field_count:
        raise DB2Error("layout has %d inline fields, file has %d" % (len(inline), field_count))
    noninline_id = next((f for f in lay["fields"] if "noninline" in f["ann"] and "id" in f["ann"]), None)
    relation = next((f for f in lay["fields"] if "noninline" in f["ann"] and "relation" in f["ann"]), None)
    if layouts is None and any(sec["rel"] for sec in sections):
        relation = {"name": "_relation"}             # unnamed read: the foreign key column, as "_relation"
    sparse = bool(flags & 1)

    rows = []
    total_rec_bytes = sum(s["recs"] for s in sections) * rec_size
    str_base = 0        # this section's string table's start in the combined table
    rec_base = 0        # this section's records' start in the combined record block
    strings = b"".join(data[s["off"] + s["recs"] * rec_size:s["off"] + s["recs"] * rec_size + s["strs"]]
                       if not sparse else b"" for s in sections)
    for s in sections:
        sp = s["off"]
        if sparse:
            rec_block_end = s["off_end"]
        else:
            rec_block_end = sp + s["recs"] * rec_size + s["strs"]
        q = rec_block_end
        ids = list(struct.unpack_from("<%dI" % (s["idl"] // 4), data, q)) if s["idl"] else []
        q += s["idl"]
        copies = [struct.unpack_from("<II", data, q + 8 * j) for j in range(s["copy"])]
        q += 8 * s["copy"]
        offmap = [struct.unpack_from("<IH", data, q + 6 * j) for j in range(s["offmap"])]
        q += 6 * s["offmap"]
        rel = {}
        if s["rel"]:
            n, _mn, _mx = struct.unpack_from("<III", data, q)
            for j in range(n):
                fk, ri = struct.unpack_from("<II", data, q + 12 + 8 * j)
                rel[ri] = fk
            q += s["rel"]
        offmap_ids = list(struct.unpack_from("<%dI" % s["offmap"], data, q)) if sparse and s["offmap"] else []
        missing = s["tact"] != 0 and not any(data[sp:sp + min(64, s["recs"] * rec_size or 64)])
        if missing and not keep_missing:
            rec_base += s["recs"] * rec_size
            str_base += s["strs"]
            continue
        n_recs = len(offmap) if sparse else s["recs"]
        for r in range(n_recs):
            if sparse:
                ro, rl = offmap[r]
                rec = data[ro:ro + rl]
                rec_abs = None
            else:
                ro = sp + r * rec_size
                rec = data[ro:ro + rec_size]
                rec_abs = rec_base + r * rec_size
            if not any(rec):
                continue
            row = {}
            for fi, f in enumerate(inline):
                row[f["name"]] = _field(data, rec, fi, f, fsi[fi], pal, com, cols, rec_abs, total_rec_bytes,
                                        strings, ids[r] if r < len(ids) else None, sparse)
            if noninline_id is not None:
                row[noninline_id["name"]] = (offmap_ids[r] if sparse else ids[r]) if (sparse or ids) else None
            elif "id" not in {a for f in inline for a in f["ann"]} and ids:
                row["ID"] = ids[r]
            if relation is not None:
                row[relation["name"]] = rel.get(r)
            rows.append(row)
        if copies:
            by_id = {row.get(noninline_id["name"] if noninline_id else "ID"): row for row in rows}
            for new_id, old_id in copies:
                if old_id in by_id:
                    nr = dict(by_id[old_id])
                    nr[noninline_id["name"] if noninline_id else "ID"] = new_id
                    rows.append(nr)
        rec_base += s["recs"] * rec_size
        str_base += s["strs"]
    return rows


def _bits(rec, bit_off, nbits):
    b0 = bit_off >> 3
    chunk = rec[b0:b0 + ((bit_off & 7) + nbits + 7) // 8]
    v = int.from_bytes(chunk, "little") >> (bit_off & 7)
    return v & ((1 << nbits) - 1)


def _field(data, rec, fi, f, info, pal, com, cols, rec_abs, total_rec_bytes, strings, rid, sparse):
    off_bits, size_bits, _add, kind, v1, v2, v3 = info
    ctype = cols.get(f["name"], "int")
    n = f["array"]
    if kind == 0:
        vals = []
        esize = (f["bits"] or 32) // 8 if ctype != "float" else 4
        if ctype in ("string", "locstring"):
            esize = 4
        base = off_bits // 8
        for j in range(n):
            o = base + j * esize
            raw = rec[o:o + esize]
            if ctype == "float":
                v = struct.unpack("<f", raw)[0]
            elif ctype in ("string", "locstring"):
                rel = struct.unpack("<I", raw)[0]
                if sparse:
                    end = rec.find(b"\0", o)
                    v = rec[o:end].decode("utf-8", "replace")
                else:
                    sp = rec_abs + o + rel - total_rec_bytes
                    end = strings.find(b"\0", sp)
                    v = strings[sp:end].decode("utf-8", "replace") if 0 <= sp < len(strings) and rel else ""
            else:
                v = int.from_bytes(raw, "little", signed=not f["unsigned"])
            vals.append(v)
        return vals[0] if n == 1 else vals
    if kind in (1, 5):
        v = _bits(rec, off_bits, size_bits)
        if kind == 5 and v & (1 << (size_bits - 1)):
            v -= 1 << size_bits
        return _as(v, ctype, f)
    if kind == 2:
        d, default = com[fi]
        return _as(d.get(rid, default) if rid is not None else default, ctype, f)
    if kind == 3:
        idx = _bits(rec, off_bits, size_bits)
        return _as(struct.unpack_from("<I", data, pal[fi] + 4 * idx)[0], ctype, f)
    if kind == 4:
        idx = _bits(rec, off_bits, size_bits)
        return [_as(struct.unpack_from("<I", data, pal[fi] + 4 * (idx * v3 + j))[0], ctype, f) for j in range(v3)]
    raise DB2Error("storage kind %d" % kind)


def _as(v, ctype, f):
    if ctype == "float":
        return struct.unpack("<f", struct.pack("<I", v & 0xFFFFFFFF))[0]
    if not f["unsigned"] and f["bits"] and v >= 1 << (f["bits"] - 1) and v < 1 << f["bits"]:
        return v - (1 << f["bits"])
    if not f["unsigned"] and not f["bits"] and v >= 1 << 31 and v < 1 << 32:
        return v - (1 << 32)
    return v
