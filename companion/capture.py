"""Screen capture of the WoW window's top-left corner (Windows, ctypes GDI, no dependencies).

Works with windowed and borderless-windowed modes (DWM composes the frame). Exclusive
fullscreen may return black: use "Windowed (Fullscreen)" in the game settings.
"""
import ctypes
import sys

from protocol import Image
from log import log_once

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor DPI aware: physical pixels
    except Exception:  # noqa: BLE001 - before Windows 8.1 (no shcore): the older call below
        try:
            user32.SetProcessDPIAware()
        except Exception as e:  # noqa: BLE001 - an old Windows: scaled pixels, the strip may read badly
            log_once("capture.dpi", e)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def find_wow_window(title_hint="World of Warcraft"):
    if not IS_WINDOWS:
        return None
    found = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value.strip() == title_hint or buf.value.startswith(title_hint):
            found.append((buf.value.strip() != title_hint, hwnd))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    # an exact title first: "World of Warcraft - File Explorer" (the game folder open) also starts with the hint
    return min(found, key=lambda f: f[0])[1] if found else None


def client_origin(hwnd):
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    rc = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    return pt.x, pt.y, rc.right - rc.left, rc.bottom - rc.top


class Grabber:
    """Grab a w x h region at (x, y) of the screen into a BGRA Image, reusing GDI objects."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.screen = user32.GetDC(0)
        self.mem = gdi32.CreateCompatibleDC(self.screen)
        self.bmp = gdi32.CreateCompatibleBitmap(self.screen, w, h)
        gdi32.SelectObject(self.mem, self.bmp)
        self.bi = BITMAPINFO()
        self.bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        self.bi.bmiHeader.biWidth = w
        self.bi.bmiHeader.biHeight = -h          # top-down
        self.bi.bmiHeader.biPlanes = 1
        self.bi.bmiHeader.biBitCount = 32
        self.buf = ctypes.create_string_buffer(w * h * 4)

    def grab(self, x, y):
        SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
        gdi32.BitBlt(self.mem, 0, 0, self.w, self.h, self.screen, x, y, SRCCOPY | CAPTUREBLT)
        gdi32.GetDIBits(self.mem, self.bmp, 0, self.h, self.buf, ctypes.byref(self.bi), 0)
        return Image(self.w, self.h, self.buf.raw, bgra=True)

    def close(self):
        gdi32.DeleteObject(self.bmp)
        gdi32.DeleteDC(self.mem)
        user32.ReleaseDC(0, self.screen)


class WowCapture:
    """Find the WoW window and return images of its top-left corner."""

    def __init__(self, w=420, h=60, title="World of Warcraft"):
        self.w, self.h, self.title = w, h, title
        self.hwnd = None
        self.grabber = Grabber(w, h) if IS_WINDOWS else None

    def resize(self, w, h):
        """Grab a w x h corner from now on (the strip grew or shrank: /glide cell, a moved strip)."""
        if (w, h) == (self.w, self.h):
            return False
        if self.grabber is not None:
            self.grabber.close()
            self.grabber = Grabber(w, h)
        self.w, self.h = w, h
        return True

    def frame(self):
        if not IS_WINDOWS:
            raise RuntimeError("live capture needs Windows; use --sim")
        if not self.hwnd or not user32.IsWindow(self.hwnd):
            self.hwnd = find_wow_window(self.title)
            if not self.hwnd:
                return None
        x, y, cw, ch = client_origin(self.hwnd)
        if cw <= 0 or ch <= 0:            # minimised
            return None
        return self.grabber.grab(x, y)


def save_png(img, path):
    """Debug helper: write an Image to PNG (stdlib only)."""
    with open(path, "wb") as fh:
        fh.write(png_bytes(img))


def png_bytes(img):
    """An Image as PNG file bytes (stdlib only)."""
    import struct
    import zlib
    rows = bytearray()
    for y in range(img.h):
        rows.append(0)
        for x in range(img.w):
            rows += bytes(img.px(x, y))

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", img.w, img.h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b"")
    return png
