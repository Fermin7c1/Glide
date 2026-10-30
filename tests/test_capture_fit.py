#!/usr/bin/env python3
"""The capture follows the strip's size: a /glide cell bigger than the default corner holds is read in full once the
grab is resized to protocol.fit_size(), and the configured size is the least it grabs.

Run: python3 tests/test_capture_fit.py   (prints OK)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "companion"))
import protocol  # noqa: E402
from protocol import Image  # noqa: E402

COLS, ROWS, BASE = 96, 8, (420, 60)


def crop(img, w, h):
    """The top-left w x h of an RGB Image (what a smaller grab would have seen)."""
    rows = b"".join(img.data[y * img.w * 3:(y * img.w + min(w, img.w)) * 3] for y in range(min(h, img.h)))
    return Image(min(w, img.w), min(h, img.h), rows, bgra=False)


CAP = (COLS * ROWS - 2) // 4 * 3
payload = ("t=1;l=7|pg=B;nm=Testwarrior;q=" + "abcdefghij" * 60)[:CAP - 6]     # a full page, like the real ones
syms = protocol.encode_to_symbols(5, payload, CAP)
for cell in (3, 4, 6, 8):
    full = protocol.render(syms, COLS, ROWS, cell, pad=(2, 3), size=(1000, 300), binary=True)
    seen = crop(full, *BASE)                          # the default 420 x 60 grab
    loc = protocol.locate(seen)
    assert loc and loc[:3] == (2, 3, cell), (cell, loc)
    w, h = protocol.fit_size(loc, COLS, ROWS, BASE)
    assert w >= 2 + COLS * cell and h >= 3 + 2 * ROWS * cell + 9, (cell, w, h)
    res, _ = protocol.decode_image(crop(full, w, h), COLS, ROWS, loc)
    assert res and res[1] == payload, (cell, res)
    if cell >= 4:                                     # too big for the default grab: it really needed the resize
        assert protocol.decode_image(seen, COLS, ROWS, loc)[0] is None, cell
    else:                                             # the default cell: the width holds, a margin is added below
        assert w == BASE[0] and h == 3 + 2 * ROWS * cell + 9 + protocol.FIT_MARGIN, (w, h)
assert protocol.fit_size((0, 0, 40, protocol.BINARY), COLS, ROWS, BASE) == protocol.MAX_CAPTURE, "capped"
print("OK")
