"""Glide strip protocol (must match addon/Glide/Codec.lua). The whole wire format, frame to keys, is written down in
docs/STRIP.md, generated from companion/strip_spec.py.

Cells are row-major; cell 0 is magenta, cell 1 yellow (the binary strip, addon >= 0.14.1) or cyan (the old 6-bit
strip). Four 6-bit symbols carry three bytes:
  [version][seq][len hi][len lo][payload ...][fletcher16 hi][fletcher16 lo]

Binary strip: each symbol is drawn as two cells holding three bits each, one per channel, fully off or fully on.
Windows Auto HDR (and similar SDR->HDR processing) boosts and clips mid levels, but off and on stay apart. The grid
has twice the rows for the same capacity, and the on/off threshold per channel comes from the marker cells of the
same frame, so it follows the processing as it changes. The old 6-bit strip (one cell per symbol, 2 bits per colour
channel at levels 0/85/170/255) is still read; under HDR its 64 colours land on ~41.
"""

from typing import Literal

PROTO = 1


class Image:
    """BGRA or RGB pixel buffer with (x, y) -> (r, g, b)."""

    def __init__(self, width, height, data, bgra=True):
        self.w, self.h, self.data, self.bgra = width, height, data, bgra
        self.stride = 4 if bgra else 3

    def px(self, x, y):
        i = (y * self.w + x) * self.stride
        d = self.data
        if self.bgra:
            return d[i + 2], d[i + 1], d[i]
        return d[i], d[i + 1], d[i + 2]


def _near(c, target, tol=60):
    return all(abs(a - b) <= tol for a, b in zip(c, target))


MAGENTA, CYAN, YELLOW = (255, 0, 255), (0, 255, 255), (255, 255, 0)
SIX, BINARY = 1, 2          # strip kinds: 6-bit cells (cyan marker), 3-bit on/off cells (yellow marker)


def locate(img, max_x=None, max_y=None):
    """Find the strip: returns (x0, y0, cell, kind) or None."""
    max_x = min(img.w, max_x or img.w)
    max_y = min(img.h, max_y or img.h)
    for y in range(max_y):
        for x in range(max_x):
            if not _near(img.px(x, y), MAGENTA):
                continue
            run = 1
            while x + run < img.w and _near(img.px(x + run, y), MAGENTA):
                run += 1
            if run > 12:
                continue
            # run measured along the top row of the cell; cyan or yellow must follow
            if x + run < img.w:
                nxt = img.px(x + run, y)
                if _near(nxt, CYAN):
                    return x, y, run, SIX
                if _near(nxt, YELLOW):
                    return x, y, run, BINARY
    return None


def read_symbols(img, x0, y0, cell, cols, rows):
    syms = []
    off = cell // 2
    for i in range(2, cols * rows):
        c, r = i % cols, i // cols
        x, y = x0 + c * cell + off, y0 + r * cell + off
        if x >= img.w or y >= img.h:
            break
        rr, gg, bb = img.px(x, y)
        q = lambda v: min(3, max(0, int(v / 85.0 + 0.5)))
        syms.append(q(rr) * 16 + q(gg) * 4 + q(bb))
    return syms


def read_binary(img, x0, y0, cell, cols, rows):
    """Binary strip (rows = the configured rows; drawn as 2 * rows): two 3-bit cells per symbol, high bits first."""
    off = cell // 2
    mag = img.px(x0 + off, y0 + off)
    yel = img.px(x0 + cell + off, y0 + off)
    # "on" level per channel as this frame shows it (red is on in both markers); the cut is halfway to black
    cut = ((mag[0] + yel[0]) / 4.0, yel[1] / 2.0, mag[2] / 2.0)
    syms = []
    hi = None
    for i in range(2, cols * rows * 2):
        c, r = i % cols, i // cols
        x, y = x0 + c * cell + off, y0 + r * cell + off
        if x >= img.w or y >= img.h:
            break
        px = img.px(x, y)
        v = (px[0] > cut[0]) * 4 + (px[1] > cut[1]) * 2 + (px[2] > cut[2])
        if hi is None:
            hi = v
        else:
            syms.append(hi * 8 + v)
            hi = None
    return syms


def symbols_to_bytes(syms):
    out = bytearray()
    for i in range(0, len(syms) - 3, 4):
        v = (syms[i] << 18) | (syms[i + 1] << 12) | (syms[i + 2] << 6) | syms[i + 3]
        out += bytes(((v >> 16) & 255, (v >> 8) & 255, v & 255))
    return bytes(out)


def fletcher16(data):
    a = b = 0
    for x in data:
        a = (a + x) % 255
        b = (b + a) % 255
    return b * 256 + a


def list_hash(text):
    """The addon's change check for a list it sends over several pages (State.lua ns.Hash): a 16-bit hash of the
    joined text's UTF-8 bytes. The companion takes a list only when its chunks hash to what the addon sent."""
    h = 0
    for b in text.encode("utf-8"):
        h = (h * 31 + b) % 65521
    return h


class OtherVersion:
    """A frame that reads cleanly (its length and checksum hold) but starts with another strip version than PROTO:
    the game runs an addon newer or older than this engine. It is falsy like an unreadable frame, so a reader that
    takes only good frames skips it; the capture loop counts it so the Status page can say what is wrong."""

    def __init__(self, version):
        self.version = version

    def __bool__(self) -> Literal[False]:    # typed so a type checker narrows `if res:` to a good frame
        return False

    def __repr__(self):
        return "OtherVersion(%d)" % self.version


def decode_bytes(raw):
    """-> (seq, payload str); OtherVersion(v) for a clean frame of another strip version; None if the frame is
    invalid."""
    if len(raw) < 6:
        return None
    n = raw[2] * 256 + raw[3]
    if 4 + n + 2 > len(raw):
        return None
    body = raw[:4 + n]
    if fletcher16(body) != raw[4 + n] * 256 + raw[5 + n]:
        return None
    if raw[0] != PROTO:
        # version 0 with nothing in it is what an all-black grid reads as (its checksum is 0 too): not a frame
        return OtherVersion(raw[0]) if raw[0] and n else None
    return raw[1], body[4:].decode("utf-8", "replace")


def decode_image(img, cols, rows, loc=None):
    loc = loc or locate(img)
    if not loc:
        return None, None
    x0, y0, cell = loc[:3]
    if strip_kind(loc) == BINARY:
        syms = read_binary(img, x0, y0, cell, cols, rows)
    else:
        syms = read_symbols(img, x0, y0, cell, cols, rows)
    return decode_bytes(symbols_to_bytes(syms)), loc


def strip_kind(loc):
    return loc[3] if len(loc) > 3 else SIX


def strip_rows(loc, rows):
    """Rows drawn on screen: the binary strip has two cells per symbol."""
    return rows * 2 if strip_kind(loc) == BINARY else rows


GAUGE_W, GAUGE_H = 200, 3
GAUGE_COLORS = ((0, 255, 0), (0, 0, 255), (255, 0, 0))     # player health, player power, target health
FIT_MARGIN = 16                 # px around the strip and gauges, so a pixel of drift doesn't cut the edge off
MAX_CAPTURE = (1600, 400)       # the largest corner ever grabbed (cell 8 needs about 790 x 160)


def fit_size(loc, cols, rows, base=(0, 0)):
    """The capture size (w, h) that holds the whole strip and its gauges, given where the marker was found and its
    cell size: the strip is drawn in physical pixels (cols x 2*rows cells of `cell` px, then three 200 x 3 gauges),
    so a bigger /glide cell or a strip pushed right/down needs a bigger grab. Never below `base` (the configured
    size), never above MAX_CAPTURE."""
    x0, y0, cell = loc[:3]
    w = x0 + max(cols * cell, GAUGE_W) + FIT_MARGIN
    h = y0 + strip_rows(loc, rows) * cell + 3 * GAUGE_H + FIT_MARGIN
    return min(MAX_CAPTURE[0], max(base[0], w)), min(MAX_CAPTURE[1], max(base[1], h))


def read_gauges(img, loc, rows):
    """Fill % of the three StatusBar gauges drawn under the grid (addon >= 0.6.0)."""
    if not loc:
        return None
    x0, y0, cell = loc[:3]
    gy = y0 + strip_rows(loc, rows) * cell
    out = []
    for i, col in enumerate(GAUGE_COLORS):
        y = gy + i * GAUGE_H + GAUGE_H // 2
        if y >= img.h or x0 + GAUGE_W > img.w:
            return None
        n = sum(1 for x in range(x0, x0 + GAUGE_W) if _near(img.px(x, y), col, 90))
        out.append(round(n * 100.0 / GAUGE_W))
    return {"hp": out[0], "power": out[1], "target_hp": out[2]}


def parse_kv(text):
    out = {}
    for part in text.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out


def parse_payload(payload):
    """'core|page' -> (core dict, page dict)."""
    core, _, page = payload.partition("|")
    return parse_kv(core), parse_kv(page)


def encode_to_symbols(seq, payload, capacity):
    """Python twin of Codec.lua Encode (used by the simulator and tests)."""
    data = payload.encode("utf-8")[:capacity - 6]
    raw = bytearray([PROTO, seq % 256, len(data) // 256, len(data) % 256]) + data
    s = fletcher16(raw)
    raw += bytes((s // 256, s % 256))
    while len(raw) % 3:
        raw.append(0)
    syms = []
    for i in range(0, len(raw), 3):
        v = (raw[i] << 16) | (raw[i + 1] << 8) | raw[i + 2]
        syms += [(v >> 18) & 63, (v >> 12) & 63, (v >> 6) & 63, v & 63]
    return syms


def render(syms, cols, rows, cell, pad=(5, 7), size=None, binary=False):
    """Draw a strip into an RGB Image (simulator/tests)."""
    lv = (0, 85, 170, 255)
    shown = rows * 2 if binary else rows
    w = size[0] if size else pad[0] + cols * cell + 10
    h = size[1] if size else pad[1] + shown * cell + 10
    buf = bytearray([20, 20, 30] * (w * h))
    for i in range(cols * shown):
        if i == 0:
            col = MAGENTA
        elif i == 1:
            col = YELLOW if binary else CYAN
        elif binary:
            k = i - 2
            s = syms[k // 2] if k // 2 < len(syms) else 0
            v = (s >> 3) & 7 if k % 2 == 0 else s & 7
            col = (255 * (v >> 2), 255 * ((v >> 1) & 1), 255 * (v & 1))
        else:
            s = syms[i - 2] if i - 2 < len(syms) else 0
            col = (lv[(s >> 4) & 3], lv[(s >> 2) & 3], lv[s & 3])
        c, r = i % cols, i // cols
        for yy in range(cell):
            for xx in range(cell):
                x, y = pad[0] + c * cell + xx, pad[1] + r * cell + yy
                j = (y * w + x) * 3
                buf[j:j + 3] = bytes(col)
    return Image(w, h, bytes(buf), bgra=False)
