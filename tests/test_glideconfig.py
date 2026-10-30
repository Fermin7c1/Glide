#!/usr/bin/env python3
"""config.json has one reader and writer (companion/glideconfig.py) for the engine, the knowledge build and the
tools. Readers never raise and take Notepad's BOM (the knowledge build and the tools used to fail on one); a write
re-reads the file, merges the way the desktop app does (None removes a key) and leaves an unreadable file alone;
two writers in the engine don't lose each other's change. The Status page's knowledge checks go through
knowledge.api.status, not the build itself.

Run: python3 tests/test_glideconfig.py   (prints OK)
"""
import json
import os
import shutil
import sys
import tempfile
import threading

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
sys.path.append(ROOT)
import glideconfig  # noqa: E402
import settings  # noqa: E402
from knowledge import store  # noqa: E402

tmp = tempfile.mkdtemp(prefix="glide-glideconfig-")
p = os.path.join(tmp, "config.json")


def write(text, encoding="utf-8"):
    with open(p, "w", encoding=encoding) as fh:
        fh.write(text)


try:
    # no file: the defaults, no WoW folder, nothing to complain about
    logged = []
    assert glideconfig.load(tmp, log=logged.append) == settings.defaults() and not logged
    assert glideconfig.wow_dir(tmp) is None and glideconfig.read(tmp) == ({}, None)

    # a BOM is fine for every reader, the knowledge build's included
    wow = os.path.join(tmp, "_classic_")
    os.makedirs(wow)
    write(json.dumps({"wow_dir": wow, "companion": {"llm": {"lookahead_steps": 500}}}), encoding="utf-8-sig")
    assert glideconfig.wow_dir(tmp) == wow
    assert store.BuildContext(root=tmp).wow_dir() == wow
    assert glideconfig.load(tmp)["llm"]["lookahead_steps"] == 80, "clamped"

    # unreadable, or not an object, or a section of the wrong kind: the defaults and a reason, never an exception
    for bad in ('{"wow_dir": "C:/WoW",, }', "[1, 2]", '{"companion": [1]}', '{"wow_dir": 5}'):
        write(bad)
        logged = []
        assert glideconfig.load(tmp, log=logged.append) == settings.defaults(), bad
        assert glideconfig.wow_dir(tmp) is None and store.BuildContext(root=tmp).wow_dir() is None, bad
        if bad.startswith("{\"wow_dir\": \"C") or bad.startswith("["):
            assert logged and "[config] can't read" in logged[0] and p in logged[0], logged

    # patch: re-read, merge like the app's deepMerge (None removes, objects merge), the rest of the file kept
    write(json.dumps({"wow_dir": "C:/WoW", "companion": {"llm": {"fast_model": "a", "web_search": True},
                                                         "ptt": {"key": "F8"}}}), encoding="utf-8-sig")
    out = glideconfig.patch((), {"companion": {"llm": {"web_search": None, "deep_model": "b"}}, "wow_dir": "D:/WoW"},
                            root=tmp)
    want = {"wow_dir": "D:/WoW", "companion": {"llm": {"fast_model": "a", "deep_model": "b"}, "ptt": {"key": "F8"}}}
    assert out == want and json.load(open(p, encoding="utf-8")) == want, out
    assert open(p, encoding="utf-8").read().endswith("}\n")
    glideconfig.patch(("companion", "voice"), {"output": "windows"}, root=tmp)
    assert json.load(open(p, encoding="utf-8"))["companion"]["voice"] == {"output": "windows"}
    assert not [f for f in os.listdir(tmp) if f.endswith(".tmp")], "no temporary file left behind"

    # ... and an unreadable file raises and stays as it is
    write('{"wow_dir": "C:/WoW",, }')
    try:
        glideconfig.patch(("companion", "llm"), {"fast_model": "x"}, root=tmp)
        raise SystemExit("saved over an unreadable config.json")
    except ValueError as e:
        assert "can't be read" in str(e)
    assert open(p, encoding="utf-8").read() == '{"wow_dir": "C:/WoW",, }'

    # two of the engine's writers at once (a dashboard model pick, the app saving a setting): both changes land
    os.remove(p)
    threads = [threading.Thread(target=lambda i=i: [glideconfig.patch(("companion", "s%d" % i), {"n": n}, root=tmp)
                                                    for n in range(30)]) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert json.load(open(p, encoding="utf-8"))["companion"] == {"s%d" % i: {"n": 29} for i in range(4)}

    # the tools read the WoW folder through it too (their command lines are unchanged)
    tools_src = {n: open(os.path.join(ROOT, "tools", n), encoding="utf-8").read()
                 for n in ("build_db.py", "build_vendor_treasures.py", "extract_maps.py")}
    for n, src in tools_src.items():
        assert "glideconfig" in src and 'os.path.join(ROOT, "config.json")' not in src, n

    # the Status page doesn't import the knowledge build or the store itself: knowledge.api.status does
    health_src = open(os.path.join(ROOT, "companion", "health.py"), encoding="utf-8").read()
    assert "knowledge.build_kb" not in health_src and "knowledge.store" not in health_src
    sys.path.insert(0, ROOT)
    from knowledge.api import status as kstatus
    st = kstatus.kb_staleness(tmp)
    assert st.get("stale") and isinstance(st.get("reason"), str), st         # no kb.db in tmp: stale, with a reason
    assert kstatus.rxp_source(tmp)["kind"] in (None, "missing", "none"), kstatus.rxp_source(tmp)   # nothing there
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
