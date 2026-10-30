#!/usr/bin/env python3
"""Dashboard: the HUD's fixed regions (quest page, minimap, tracker, chat) and the book's sections, each a tab that
docks into the wing when there's room; the saved-layout endpoint (GET/POST /layout) still validates; the map zooms.

Run: python3 tests/test_dashboard_layout.py   (prints OK)
"""
import json
import os
import re
import sys
import tempfile
import urllib.request

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import dashboard  # noqa: E402

PANELS = ["steps", "map", "route", "coach"]                                   # fixed regions of the HUD
BOOK = ["why", "quests", "gear", "levels", "buffs", "community", "session"]   # tabs, docked into the wing when wide
page = dashboard.PAGE
for p in PANELS + BOOK:
    assert len(re.findall(r'<section[^>]*\bdata-panel="%s"' % p, page)) == 1, p
shelf = page.split('id="shelf"')[1].split('data-panel="coach"')[0]
for p in BOOK:
    assert 'data-panel="%s" data-title="' % p in shelf, "%s is a section of the book" % p
dock = page.split("const DOCK_ORDER=")[1].split(";")[0]
assert sorted(re.findall(r"'([a-z]+)'", dock)) == sorted(BOOK), "every book section docks somewhere"
for ctl in ("mz_in", "mz_out", "mz_follow", "mz_reset", "mapview", "wing", "shelf", "plate", "steps", "feed", "xpbarbox"):
    assert 'id="%s"' % ctl in page, ctl
for f in ("tenor-sans.woff2", "geist.woff2", "source-serif-4.woff2"):
    assert "fonts/" + f in page and os.path.exists(os.path.join(ROOT, "companion", "static", "fonts", f)), f

# validation: unknown shapes are rejected, junk ids dropped, duplicates kept once
assert dashboard.clean_layout({"cols": [["map", "steps", "map", "<script>"], ["coach"]], "collapsed": ["gear", 5]}) == \
    {"cols": [["map", "steps"], ["coach"]], "collapsed": ["gear"]}
# the divider position: kept when sensible, dropped otherwise
assert dashboard.clean_layout({"cols": [["steps"], ["coach"]], "split": 0.62})["split"] == 0.62
assert "split" not in dashboard.clean_layout({"cols": [["steps"]], "split": 0.95})
assert "split" not in dashboard.clean_layout({"cols": [["steps"]], "split": True})
# the arrangement (arrange mode): each group's order and which panel sits in which slot; junk dropped, a panel once
arr = dashboard.clean_layout({"arrange": {"lists": {"top": ["pace", "unit", "pace", "<b>"], "Bad": ["x"]},
                                          "slots": {"page": "map", "map": "steps", "chat": "map"}}})["arrange"]
assert arr == {"lists": {"top": ["pace", "unit"]}, "slots": {"page": "map", "map": "steps"}}, arr
for g in ("top", "pace", "foot", "xpline"):
    assert 'data-group="%s"' % g in page, g
assert len(re.findall(r'class="slot" data-slot="(page|chat|book|map|route)"', page)) == 5, "five slots in the middle"
for bad in ([], {"cols": "map"}, {"cols": [[]] * 5}):
    try:
        dashboard.clean_layout(bad)
        raise AssertionError("accepted %r" % (bad,))
    except ValueError:
        pass

tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "live", "dashboard_layout.json")
assert dashboard.load_layout(path) == {}
srv = dashboard.serve(dashboard.Hub(), 0, layout_path=path)
port = srv.server_address[1]
get = lambda: json.load(urllib.request.urlopen("http://127.0.0.1:%d/layout" % port))  # noqa: E731
assert get() == {}
lay = {"cols": [["steps", "why"], ["map", "coach"]], "collapsed": ["why"]}
req = urllib.request.Request("http://127.0.0.1:%d/layout" % port, data=json.dumps(lay).encode(), method="POST")
r = json.load(urllib.request.urlopen(req))
assert r["ok"] and r["layout"] == lay
assert get() == lay and json.load(open(path)) == lay, "kept on disk for the next session"
srv.shutdown()
print("OK")
