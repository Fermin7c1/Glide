"""The desktop app's Settings page schema as the page gets it: desktop/src/renderer/settings-schema.ts compiled with
the project's TypeScript (the tests' shared compile, desktop_ts.py) and imported under Node (functions in it come out as "fn <name>").

    schema()    [tab, ...] (settings-schema.ts SCHEMA), or None without node and desktop/node_modules
    keys()      every field's key ("companion.llm.quest_briefings", "app:overlay.opacity", ...), or None
    specials()  the groups that draw their own panel ("model-picker", "keys", ...), or None
"""
import json
import os
import subprocess

import desktop_ts

_cache = {}


def schema():
    if "schema" in _cache:
        return _cache["schema"]
    node = desktop_ts.node
    if not node or not desktop_ts.available():
        _cache["schema"] = None
        return None
    mjs = os.path.join(desktop_ts.renderer(), "renderer", "settings-schema.js")
    code = ("import(%s).then(m => console.log(JSON.stringify(m.SCHEMA, "
            "(k, v) => typeof v === 'function' ? 'fn ' + (v.name || 'anon') : v)))" % json.dumps(desktop_ts.url(mjs)))
    r = subprocess.run([node, "--input-type=module", "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, "importing the schema failed: %s" % r.stderr[-2000:]
    _cache["schema"] = json.loads(r.stdout)
    return _cache["schema"]


def _groups():
    for tab in schema() or ():
        for g in tab.get("groups") or ():
            yield g


def keys():
    if schema() is None:
        return None
    return {f["key"] for g in _groups() for f in g.get("fields") or () if f.get("key")}


def specials():
    if schema() is None:
        return None
    return {g["special"] for g in _groups() if g.get("special")}
