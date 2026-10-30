"""The engine's console lines. The desktop app shows stdout on its Logs page and keeps it on disk
(userData/logs/engine-YYYYMMDD.log), and it reads some lines itself (GLIDE_READY), so a line's text is part of the
interface: log() only adds the usual "[tag] " prefix, nothing else.

log_once(site, e) is for the places that swallow an error on purpose (an optional extra, a best-effort save): they
still carry on as before, but the first error at each site is now on the record instead of vanishing."""
import os
import threading

_lock = threading.Lock()
_seen = set()
MAX_SITES = 200           # a runaway caller can't grow the set forever


def line(text):
    """One console line, exactly as given."""
    print(text, flush=True)


def log(tag, msg):
    """A status line: "[tag] msg"."""
    line("[%s] %s" % (tag, msg))


def log_once(site, e):
    """An error swallowed at `site` (e.g. "overlay.save"): logged the first time only, with where it was raised."""
    with _lock:
        if site in _seen or len(_seen) >= MAX_SITES:
            return
        _seen.add(site)
    tb = e.__traceback__
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    where = " (%s:%d)" % (os.path.basename(tb.tb_frame.f_code.co_filename), tb.tb_lineno) if tb is not None else ""
    log(site, "ignored error: %s: %s%s" % (type(e).__name__, e, where))
