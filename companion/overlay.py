"""Small always-on-top overlay with the current step and the latest coach line (tkinter, stdlib).

Normally click-through (the mouse goes to the game). Hold the drag key (default Alt) to interact:
  Alt + left-drag     move it
  Alt + mouse wheel   make it wider / narrower
  Alt + right-click   collapse to the step line only / expand again
Position, width and collapsed state are saved to data/live/overlay.json.
"""
import json
import queue
import sys

import settings
from log import log_once

VK = {"ALT": 0x12, "SHIFT": 0x10, "CTRL": 0x11}
BG, BG_EDIT = "#0d0f14", "#1b2a3a"


class Overlay:
    @property
    def opt(self):
        """The overlay settings, read through settings_manifest.json (defaults, ranges)."""
        return settings.Settings("overlay", self.cfg)

    def __init__(self, cfg, state_file=None):
        import tkinter as tk
        self.tk = tk
        self.cfg = cfg
        self.q = queue.Queue()
        self.state_file = state_file
        saved = {}
        try:
            saved = json.load(open(state_file, encoding="utf-8")) if state_file else {}
        except FileNotFoundError:
            pass                                # first run: the configured place
        except Exception as e:  # noqa: BLE001 - a damaged file: the configured place
            log_once("overlay.load", e)
        self.x, self.y = saved.get("x", self.opt.get("x")), saved.get("y", self.opt.get("y"))
        self.width = saved.get("width", self.opt.get("width"))
        self.collapsed = saved.get("collapsed", False)
        self.drag_vk = VK.get(str(self.opt.get("drag_key")).upper(), 0x12)
        self.editing = False
        self.hwnd = None
        self._drag = None
        self.last_msg = ("", "#e6e9ef")

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", self.opt.get("alpha"))
        self.root.configure(bg=BG, highlightthickness=1, highlightbackground=BG)
        font = ("Segoe UI", self.opt.get("font_size"))
        self.step = tk.Label(self.root, text="Glide: waiting for game…", fg="#5cc8ff", bg=BG,
                             font=(font[0], font[1] - 1), anchor="w", justify="left")
        self.step.pack(fill="x", padx=8, pady=(6, 0))
        self.msg = tk.Label(self.root, text="", fg="#e6e9ef", bg=BG, font=font, anchor="w", justify="left")
        self.hint = tk.Label(self.root, text="Alt+drag move · Alt+wheel width · Alt+right-click collapse",
                             fg="#8a93a6", bg=BG_EDIT, font=(font[0], 8), anchor="w")
        self._layout()
        self.root.update_idletasks()

        # Bind on the toplevel only: its bindtag is part of every child label's bindtags, so these fire
        # once for a click anywhere on the overlay. Binding the labels too fired each handler twice,
        # and a right-click on the text collapsed and re-expanded in the same click.
        for seq, fn in (("<ButtonPress-1>", self._press), ("<B1-Motion>", self._motion),
                        ("<ButtonRelease-1>", self._release), ("<ButtonRelease-3>", self._toggle_collapse),
                        ("<MouseWheel>", self._wheel)):
            self.root.bind(seq, fn)

        if sys.platform == "win32":
            try:
                import ctypes
                self.u32 = ctypes.windll.user32
                self.hwnd = self.u32.GetParent(self.root.winfo_id())
                self._set_style(clickthrough=True)
            except Exception as e:  # noqa: BLE001 - no click-through window style
                log_once("overlay.hwnd", e)
                self.hwnd = None
        self.stop = False
        self.root.after(50, self._poll)

    # ------------------------------------------------------------------------------------------
    def _layout(self):
        self.step.config(wraplength=self.width - 16)
        self.msg.config(wraplength=self.width - 16)
        self.msg.pack_forget()
        self.hint.pack_forget()
        if not self.collapsed:
            self.msg.pack(fill="both", expand=True, padx=8, pady=(2, 6))
        if self.editing:
            self.hint.pack(fill="x", padx=0, pady=0)
        self.root.update_idletasks()
        h = self.root.winfo_reqheight()
        self.root.geometry("%dx%d+%d+%d" % (self.width, max(h, 28), self.x, self.y))

    def _set_style(self, clickthrough):
        if not self.hwnd:
            return
        GWL_EXSTYLE = -20
        WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80000, 0x20, 0x80, 0x08000000
        style = self.u32.GetWindowLongW(self.hwnd, GWL_EXSTYLE) | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        style = style | WS_EX_TRANSPARENT if clickthrough else style & ~WS_EX_TRANSPARENT
        self.u32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style)
        # SWP_NOMOVE|NOSIZE|NOZORDER|NOACTIVATE|FRAMECHANGED
        self.u32.SetWindowPos(self.hwnd, 0, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0004 | 0x0010 | 0x0020)

    def _set_editing(self, on):
        if on == self.editing:
            return
        self.editing = on
        self._set_style(clickthrough=not on)
        color = BG_EDIT if on else BG
        for w in (self.root, self.step, self.msg):
            w.configure(bg=color)
        self.root.configure(highlightbackground="#5cc8ff" if on else BG)
        self.root.configure(cursor="fleur" if on else "")
        self._layout()
        if not on:
            self._save()

    def _save(self):
        if not self.state_file:
            return
        try:
            with open(self.state_file, "w", encoding="utf-8") as fh:
                json.dump({"x": self.x, "y": self.y, "width": self.width, "collapsed": self.collapsed}, fh)
        except Exception as e:  # noqa: BLE001
            log_once("overlay.save", e)

    # ------------------------------------------------------------------------------------------
    def _press(self, e):
        if self.editing:
            self._drag = (e.x_root - self.x, e.y_root - self.y)

    def _motion(self, e):
        if self.editing and self._drag:
            self.x, self.y = e.x_root - self._drag[0], e.y_root - self._drag[1]
            self.root.geometry("+%d+%d" % (self.x, self.y))

    def _release(self, _e):
        self._drag = None
        self._save()

    def _wheel(self, e):
        if self.editing:
            self.width = max(260, min(1400, self.width + (40 if e.delta > 0 else -40)))
            self._layout()

    def _toggle_collapse(self, e):
        if getattr(e, "serial", None) is not None and e.serial == getattr(self, "_last_toggle", None):
            return                                # same click delivered twice
        self._last_toggle = getattr(e, "serial", None)
        if self.editing:
            self.collapsed = not self.collapsed
            self._layout()
            self._save()

    # ------------------------------------------------------------------------------------------
    def set_step(self, text):
        self.q.put(("step", text))

    def set_message(self, text, color="#e6e9ef"):
        self.q.put(("msg", (text, color)))

    def _poll(self):
        if self.stop:
            self.root.destroy()
            return
        if self.hwnd:
            held = bool(self.u32.GetAsyncKeyState(self.drag_vk) & 0x8000)
            if not held and self._drag:
                held = True                       # keep dragging until the button is released
            self._set_editing(held)
        changed = False
        try:
            while True:
                kind, v = self.q.get_nowait()
                if kind == "step":
                    self.step.config(text=v)
                else:
                    self.msg.config(text=v[0], fg=v[1])
                changed = True
        except queue.Empty:
            pass
        if changed and not self._drag:
            self._layout()
        self.root.after(50, self._poll)

    def run(self):
        self.root.mainloop()
