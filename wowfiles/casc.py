#!/usr/bin/env python3
"""Read-only reader for the game's local CASC storage (World of Warcraft\\Data): files by FileDataID or by name.

Nothing is written to the game folder. Pure Python (zlib); encrypted BLTE chunks (TACT keys) are not decoded:
such files raise CascEncrypted.

    from wowfiles.casc import Casc
    c = Casc(r"D:\\Blizzard\\World of Warcraft", product="wow_classic_beta")
    data = c.read_name("DBFilesClient/AreaTable.db2")       # or c.read_fdid(1353545)

Layout (wowdev.wiki "CASC"): .build.info -> build config (Data/config/xx/yy/<key>) -> encoding and root keys;
Data/data/*.idx map the first 9 bytes of an encoding key to (archive, offset, size) in Data/data/data.NNN;
the encoding table maps content keys to encoding keys; the root table maps FileDataIDs (and name hashes) to
content keys. Every blob is BLTE-framed (N raw, Z zlib, F nested, E encrypted).
"""
import glob
import os
import struct
import zlib


# CASC root locale flags, by the client's text locale (Config.wtf textLocale)
LOCALE_MASK = {"enUS": 0x2, "koKR": 0x4, "frFR": 0x10, "deDE": 0x20, "zhCN": 0x40, "esES": 0x80, "zhTW": 0x100,
               "enGB": 0x200, "enCN": 0x400, "enTW": 0x800, "esMX": 0x1000, "ruRU": 0x2000, "ptBR": 0x4000,
               "itIT": 0x8000, "ptPT": 0x10000}


class CascError(Exception):
    pass


class CascEncrypted(CascError):
    pass


# ---------------------------------------------------------------------------------------------- Jenkins hashlittle2
def _rot(x, k):
    return ((x << k) | (x >> (32 - k))) & 0xFFFFFFFF


def hashlittle2(data, pc=0, pb=0):
    """Bob Jenkins' lookup3 hashlittle2 -> (c, b) as used for CASC name hashes."""
    length = len(data)
    a = b = c = (0xDEADBEEF + length + pc) & 0xFFFFFFFF
    c = (c + pb) & 0xFFFFFFFF
    i = 0
    while length > 12:
        a = (a + struct.unpack_from("<I", data, i)[0]) & 0xFFFFFFFF
        b = (b + struct.unpack_from("<I", data, i + 4)[0]) & 0xFFFFFFFF
        c = (c + struct.unpack_from("<I", data, i + 8)[0]) & 0xFFFFFFFF
        a = (a - c) & 0xFFFFFFFF; a ^= _rot(c, 4); c = (c + b) & 0xFFFFFFFF  # noqa: E702
        b = (b - a) & 0xFFFFFFFF; b ^= _rot(a, 6); a = (a + c) & 0xFFFFFFFF  # noqa: E702
        c = (c - b) & 0xFFFFFFFF; c ^= _rot(b, 8); b = (b + a) & 0xFFFFFFFF  # noqa: E702
        a = (a - c) & 0xFFFFFFFF; a ^= _rot(c, 16); c = (c + b) & 0xFFFFFFFF  # noqa: E702
        b = (b - a) & 0xFFFFFFFF; b ^= _rot(a, 19); a = (a + c) & 0xFFFFFFFF  # noqa: E702
        c = (c - b) & 0xFFFFFFFF; c ^= _rot(b, 4); b = (b + a) & 0xFFFFFFFF  # noqa: E702
        length -= 12
        i += 12
    if length == 0:
        return c, b
    tail = data[i:] + b"\0" * (12 - length)
    a = (a + struct.unpack_from("<I", tail, 0)[0]) & 0xFFFFFFFF
    b = (b + struct.unpack_from("<I", tail, 4)[0]) & 0xFFFFFFFF
    c = (c + struct.unpack_from("<I", tail, 8)[0]) & 0xFFFFFFFF
    c ^= b; c = (c - _rot(b, 14)) & 0xFFFFFFFF  # noqa: E702
    a ^= c; a = (a - _rot(c, 11)) & 0xFFFFFFFF  # noqa: E702
    b ^= a; b = (b - _rot(a, 25)) & 0xFFFFFFFF  # noqa: E702
    c ^= b; c = (c - _rot(b, 16)) & 0xFFFFFFFF  # noqa: E702
    a ^= c; a = (a - _rot(c, 4)) & 0xFFFFFFFF  # noqa: E702
    b ^= a; b = (b - _rot(a, 14)) & 0xFFFFFFFF  # noqa: E702
    c ^= b; c = (c - _rot(b, 24)) & 0xFFFFFFFF  # noqa: E702
    return c, b


def name_hash(path):
    """The root's 64-bit name hash of a file path (upper case, backslashes)."""
    c, b = hashlittle2(path.replace("/", "\\").upper().encode("ascii"))
    return (c << 32) | b


# ---------------------------------------------------------------------------------------------- BLTE
# The community's list of TACT keys (wowdev/TACTKeys) isn't shipped: it's fetched when first needed into
# data/gamedata (not in the repository), refreshed weekly, and the readers work without it (encrypted chunks read
# as zeros, as before).
TACT_KEYS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "gamedata", "tactkeys.txt")
TACT_KEYS_URL = "https://raw.githubusercontent.com/wowdev/TACTKeys/master/WoW.txt"
TACT_KEYS_MAX = 8 * 1024 * 1024
KEYS = {}               # TACT key name (8 bytes, as in the chunk) -> 16-byte key; see load_keys()
MISSING = {}            # key name hex -> number of chunks that needed it (zero-filled)


def fetch_keys(path=TACT_KEYS, max_age_s=7 * 86400):
    """Download the TACT key list to `path` when it's missing or older than max_age_s. True when a list is there."""
    import time
    import urllib.request
    try:
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_s:
            return True
        req = urllib.request.Request(TACT_KEYS_URL, headers={"User-Agent": "Glide"})
        with urllib.request.urlopen(req, timeout=20) as r:      # noqa: S310 - a fixed https URL
            data = r.read(TACT_KEYS_MAX + 1)
        if len(data) > TACT_KEYS_MAX:
            raise ValueError("the TACT key list is larger than expected")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "wb") as fh:
            fh.write(data)
        os.replace(path + ".tmp", path)
        return True
    except (OSError, ValueError):
        return os.path.exists(path)        # offline: an older list still serves


def load_default_keys():
    """The community TACT keys (fetched when needed). Returns how many were loaded (0 without a list)."""
    return load_keys(TACT_KEYS) if fetch_keys() else 0


def load_keys(path):
    """TACT keys from a text file of 'KEYNAME KEY' hex lines (wowdev TACTKeys format). Returns how many."""
    n = 0
    for ln in open(path, encoding="utf-8", errors="replace"):
        p = ln.replace(";", " ").split()
        if len(p) >= 2 and len(p[0]) == 16 and len(p[1]) == 32:
            try:
                KEYS[bytes.fromhex(p[0])[::-1]] = bytes.fromhex(p[1])
                n += 1
            except ValueError:
                pass
    return n


def blte_decode(data, want=None, zero_fill=True):
    """Decode a BLTE blob. Encrypted chunks are decrypted with a known TACT key (KEYS), else zero-filled (DB2
    readers treat those sections as missing) or, with zero_fill=False, raise CascEncrypted."""
    if data[:4] != b"BLTE":
        raise CascError("not BLTE")
    hsize = struct.unpack_from(">I", data, 4)[0]
    if hsize == 0:
        return _chunk(data[8:], 0, None, zero_fill)
    flags = data[8]
    count = int.from_bytes(data[9:12], "big")
    if flags not in (0x0F, 0x10):
        raise CascError("BLTE flags %#x" % flags)
    entry = 24 if flags == 0x0F else 40
    pos, out = hsize, []
    for i in range(count):
        csize, dsize = struct.unpack_from(">II", data, 12 + i * entry)
        out.append(_chunk(data[pos:pos + csize], i, dsize, zero_fill))
        pos += csize
        if want is not None and sum(len(x) for x in out) >= want:
            break
    return b"".join(out)


def _chunk(c, index=0, dsize=None, zero_fill=True):
    mode = c[:1]
    if mode == b"N":
        return c[1:]
    if mode == b"Z":
        return zlib.decompress(c[1:])
    if mode == b"F":
        return blte_decode(c[1:], zero_fill=zero_fill)
    if mode == b"E":
        klen = c[1]
        kname = bytes(c[2:2 + klen])
        p = 2 + klen
        ivlen = c[p]
        iv = bytearray(c[p + 1:p + 1 + ivlen])
        p += 1 + ivlen
        kind = c[p:p + 1]
        key = KEYS.get(kname)
        if key is not None and kind == b"S":
            for k in range(4):
                iv[k] ^= (index >> (8 * k)) & 0xFF
            plain = salsa20_xor(bytes(c[p + 1:]), key, bytes(iv[:8]).ljust(8, b"\0"))
            return _chunk(plain, index, dsize, zero_fill)
        MISSING[kname[::-1].hex().upper()] = MISSING.get(kname[::-1].hex().upper(), 0) + 1
        if zero_fill and dsize is not None:
            return b"\0" * dsize
        raise CascEncrypted("encrypted BLTE chunk (key %s)" % kname[::-1].hex().upper())
    raise CascError("BLTE chunk mode %r" % mode)


def salsa20_xor(data, key16, nonce8):
    """Salsa20/20 with a 16-byte key ("expand 16-byte k"), the cipher TACT uses."""
    def qr(y, a, b, c, d):
        y[b] ^= _rot((y[a] + y[d]) & 0xFFFFFFFF, 7)
        y[c] ^= _rot((y[b] + y[a]) & 0xFFFFFFFF, 9)
        y[d] ^= _rot((y[c] + y[b]) & 0xFFFFFFFF, 13)
        y[a] ^= _rot((y[d] + y[c]) & 0xFFFFFFFF, 18)
    k = struct.unpack("<4I", key16)
    n = struct.unpack("<2I", nonce8)
    const = struct.unpack("<4I", b"expand 16-byte k")
    out = bytearray(len(data))
    for blk in range(0, len(data), 64):
        ctr = blk // 64
        st = [const[0], k[0], k[1], k[2], k[3], const[1], n[0], n[1], ctr & 0xFFFFFFFF, ctr >> 32, const[2],
              k[0], k[1], k[2], k[3], const[3]]
        x = list(st)
        for _ in range(10):
            qr(x, 0, 4, 8, 12); qr(x, 5, 9, 13, 1); qr(x, 10, 14, 2, 6); qr(x, 15, 3, 7, 11)  # noqa: E702
            qr(x, 0, 1, 2, 3); qr(x, 5, 6, 7, 4); qr(x, 10, 11, 8, 9); qr(x, 15, 12, 13, 14)  # noqa: E702
        ks = struct.pack("<16I", *[(x[i] + st[i]) & 0xFFFFFFFF for i in range(16)])
        seg = data[blk:blk + 64]
        out[blk:blk + len(seg)] = bytes(a ^ b for a, b in zip(seg, ks))
    return bytes(out)


# ---------------------------------------------------------------------------------------------- storage
class Casc:
    def __init__(self, wow_dir, product="wow_classic_beta", locale_mask=0x2):     # 0x2 = enUS (LOCALE_MASK)
        self.wow_dir = wow_dir
        self.data_dir = os.path.join(wow_dir, "Data")
        self.locale_mask = locale_mask
        self.build = self._build_info(product)
        self.config = self._config(self.build["Build Key"])
        self.version = self.build.get("Version")
        self._load_indices()
        self._load_encoding()
        self._load_root()

    # .build.info ---------------------------------------------------------------------------
    def _build_info(self, product):
        lines = open(os.path.join(self.wow_dir, ".build.info"), encoding="utf-8").read().splitlines()
        head = [h.split("!")[0] for h in lines[0].split("|")]
        rows = [dict(zip(head, ln.split("|"))) for ln in lines[1:] if ln.strip()]
        for r in rows:
            if r.get("Product") == product:
                return r
        raise CascError("product %s not in .build.info (%s)" % (product, [r.get("Product") for r in rows]))

    def _config(self, key):
        p = os.path.join(self.data_dir, "config", key[:2], key[2:4], key)
        out = {}
        for ln in open(p, encoding="utf-8"):
            if "=" in ln and not ln.startswith("#"):
                k, v = ln.split("=", 1)
                out[k.strip()] = v.split()
        return out

    # data/*.idx ----------------------------------------------------------------------------
    def _load_indices(self):
        best = {}
        for p in glob.glob(os.path.join(self.data_dir, "data", "*.idx")):
            n = os.path.basename(p)[:-4]
            bucket, ver = int(n[:2], 16), int(n[2:], 16)
            if bucket not in best or ver > best[bucket][0]:
                best[bucket] = (ver, p)
        self.index = {}
        for _ver, p in best.values():
            d = open(p, "rb").read()
            hsize = struct.unpack_from("<I", d, 0)[0]
            (ver, _b, _x, size_len, off_len, key_len, off_bits) = struct.unpack_from("<HBBBBBB", d, 8)
            pos = (8 + hsize + 0x0F) & ~0x0F
            esize = struct.unpack_from("<I", d, pos)[0]
            pos += 8
            rec = key_len + off_len + size_len
            mask = (1 << off_bits) - 1
            for i in range(pos, pos + esize - rec + 1, rec):
                k = d[i:i + key_len]
                v = int.from_bytes(d[i + key_len:i + key_len + off_len], "big")
                sz = int.from_bytes(d[i + key_len + off_len:i + rec], "little")
                if k not in self.index:
                    self.index[k] = (v >> off_bits, v & mask, sz)
        self._files = {}

    def _archive(self, n):
        f = self._files.get(n)
        if f is None:
            f = self._files[n] = open(os.path.join(self.data_dir, "data", "data.%03d" % n), "rb")
        return f

    def read_ekey(self, ekey, want=None):
        hit = self.index.get(bytes(ekey[:9]))
        if hit is None:
            raise CascError("encoding key %s not in the local storage" % bytes(ekey).hex())
        arc, off, size = hit
        f = self._archive(arc)
        f.seek(off + 0x1E)
        return blte_decode(f.read(size - 0x1E), want)

    # encoding -------------------------------------------------------------------------------
    def _load_encoding(self):
        ekey = bytes.fromhex(self.config["encoding"][1])
        d = self.read_ekey(ekey)
        if d[:2] != b"EN":
            raise CascError("bad encoding file")
        ckey_len, ekey_len = d[3], d[4]
        ce_page_kb, = struct.unpack_from(">H", d, 5)
        ce_pages, = struct.unpack_from(">I", d, 9)
        espec_size, = struct.unpack_from(">I", d, 18)
        pos = 22 + espec_size
        pages_start = pos + ce_pages * (ckey_len + 16)
        page_size = ce_page_kb * 1024
        self.encoding = {}
        for p in range(ce_pages):
            i, end = pages_start + p * page_size, pages_start + (p + 1) * page_size
            while i < end:
                n = d[i]
                if n == 0:
                    break
                ck = d[i + 6:i + 6 + ckey_len]
                self.encoding[ck] = d[i + 6 + ckey_len:i + 6 + ckey_len + ekey_len]
                i += 6 + ckey_len + ekey_len * n

    def read_ckey(self, ckey, want=None):
        e = self.encoding.get(bytes(ckey))
        if e is None:
            raise CascError("content key %s not in the encoding table" % bytes(ckey).hex())
        return self.read_ekey(e, want)

    # root -----------------------------------------------------------------------------------
    def _load_root(self):
        d = self.read_ckey(bytes.fromhex(self.config["root"][0]))
        self.fdid, self.names = {}, {}
        pos, version = 0, 0
        if d[:4] == b"TSFM":
            hsize, ver = struct.unpack_from("<II", d, 4)
            if hsize == 0x18:                     # header with size + version
                version, pos = ver, hsize
            else:
                version, pos = 0, 12
        n = len(d)
        while pos + 12 <= n:
            if version >= 2:
                cnt, locale, f1, f2 = struct.unpack_from("<IIII", d, pos)
                f3 = d[pos + 16]
                content = f1 | f2 | (f3 << 17)
                pos += 17
            else:
                cnt, content, locale = struct.unpack_from("<III", d, pos)
                pos += 12
            deltas = struct.unpack_from("<%di" % cnt, d, pos)
            pos += 4 * cnt
            ckeys = pos
            pos += 16 * cnt
            has_names = not (content & 0x10000000) if version else True
            hashes = None
            if has_names:
                hashes = pos
                pos += 8 * cnt
            if not (locale & self.locale_mask or locale == 0xFFFFFFFF or locale == 0) or content & 0x80:
                continue                          # other locales / low-violence variants
            fid = -1
            for j in range(cnt):
                fid += deltas[j] + 1
                ck = d[ckeys + 16 * j:ckeys + 16 * j + 16]
                self.fdid[fid] = ck
                if hashes is not None:
                    self.names[struct.unpack_from("<Q", d, hashes + 8 * j)[0]] = fid
        self.root_version = version

    # public -----------------------------------------------------------------------------------
    def has(self, fdid):
        ck = self.fdid.get(fdid)
        return ck is not None and ck in self.encoding and bytes(self.encoding[ck][:9]) in self.index

    def read_fdid(self, fdid, want=None):
        ck = self.fdid.get(fdid)
        if ck is None:
            raise CascError("FileDataID %s not in the root" % fdid)
        return self.read_ckey(ck, want)

    def fdid_of(self, path):
        return self.names.get(name_hash(path))

    def read_name(self, path, want=None):
        fid = self.fdid_of(path)
        if fid is None:
            raise CascError("%s not in the root's name table" % path)
        return self.read_fdid(fid, want)


def install_of(wow_dir):
    """(World of Warcraft folder, product) for the configured wow_dir, which names the game flavour folder (e.g.
    ...\\World of Warcraft\\_classic_beta_, whose .flavor.info says the product); product None when it doesn't."""
    w, product = wow_dir, None
    if w and os.path.basename(os.path.normpath(w)).startswith("_"):
        try:
            lines = open(os.path.join(w, ".flavor.info"), encoding="utf-8").read().split()
            product = lines[-1] if len(lines) >= 2 else None
        except OSError:
            product = None
        w = os.path.dirname(os.path.normpath(w))            # config points at _classic_beta_: its parent
    return w, product


def main(argv):
    """python tools/casc.py <World of Warcraft folder> [product]: what the client's storage holds."""
    import time
    t = time.time()
    c = Casc(argv[0], argv[1] if len(argv) > 1 else "wow_classic_beta")
    print("version %s | root v%d: %d files, %d names | encoding %d | index %d | %.1fs" % (
        c.version, c.root_version, len(c.fdid), len(c.names), len(c.encoding), len(c.index), time.time() - t))
    for name in ("DBFilesClient/AreaTable.db2", "DBFilesClient/UiMap.db2", "DBFilesClient/Map.db2"):
        fid = c.fdid_of(name)
        print(name, fid, c.has(fid) if fid else None)
