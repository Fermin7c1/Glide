"""The desktop app's Settings page schema as the page gets it: desktop/src/renderer/settings-schema.ts compiled with
the project's TypeScript into a temp folder and imported under Node (functions in it come out as "fn <name>").

    schema()    [tab, ...] (settings-schema.ts SCHEMA), or None without node and desktop/node_modules
    keys()      every field's key ("companion.llm.quest_briefings", "app:overlay.opacity", ...), or None
    specials()  the groups that draw their own panel ("model-picker", "keys", ...), or None
"""
import json
import os
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DESK = os.path.join(HERE, "..", "desktop")
TSC = os.path.join(DESK, "node_modules", "typescript", "bin", "tsc")
SRC = os.path.join(DESK, "src", "renderer", "settings-schema.ts")
_cache = {}


def schema():
    if "schema" in _cache:
        return _cache["schema"]
    node = shutil.which("node")
    if not node or not os.path.exists(TSC):
        _cache["schema"] = None
        return None
    tmp = tempfile.mkdtemp(prefix="glide-schema-")
    try:
        r = subprocess.run([node, TSC, SRC, "--target", "ES2023", "--module", "ES2022", "--skipLibCheck",
                            "--outDir", tmp], capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, "tsc failed on settings-schema.ts: %s" % (r.stdout + r.stderr)[-2000:]
        mjs = os.path.join(tmp, "settings-schema.mjs")
        os.replace(os.path.join(tmp, "settings-schema.js"), mjs)
        code = ("import(%s).then(m => console.log(JSON.stringify(m.SCHEMA, "
                "(k, v) => typeof v === 'function' ? 'fn ' + (v.name || 'anon') : v)))"
                % json.dumps("file:///" + mjs.replace("\\", "/").lstrip("/")))
        r = subprocess.run([node, "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, "importing the schema failed: %s" % r.stderr[-2000:]
        _cache["schema"] = json.loads(r.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
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
