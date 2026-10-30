#!/usr/bin/env python3
"""The desktop app saves config.json through the running engine (POST /settings {"save": patch}), so the engine's
own writes (dashboard model picks) and the app's never cross: the patch merges as the app's config:set did (None
removes a key), lands even in a simulated run, goes with live changes in one request, and a bad one is refused.
The app falls back to writing the file itself only when no engine takes it (desktop/src/main/app/config.ts).

Run: python3 tests/test_settings_save.py   (prints OK)
"""
import json
import os
import shutil
import sys
import tempfile
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import app as glide_app  # noqa: E402

tmp = tempfile.mkdtemp(prefix="glide-settings-save-")
p = os.path.join(tmp, "config.json")
glide_app.ROOT = tmp
announced = []


def fake_app(sim):
    coach = types.SimpleNamespace(cfg={"fast_model": "a-model", "deep_model": "b-model"},
                                  announce_models=announced.append, describe_models=lambda: "models")
    return types.SimpleNamespace(args=types.SimpleNamespace(sim=sim), coach=coach)


try:
    with open(p, "w", encoding="utf-8-sig") as fh:
        json.dump({"wow_dir": "C:/WoW", "companion": {"llm": {"fast_model": "a-model", "web_search": True}}}, fh)
    # the app's Settings save: a patch of the whole file
    a = fake_app(sim=True)
    out = glide_app.App.on_settings(a, {"save": {"companion": {"llm": {"web_search": None}, "ptt": {"key": "F9"}},
                                                 "wow_dir": "D:/WoW"}})
    assert out == {"saved": True}, out
    assert json.load(open(p, encoding="utf-8")) == {"wow_dir": "D:/WoW", "companion": {
        "llm": {"fast_model": "a-model"}, "ptt": {"key": "F9"}}}
    assert a.coach.cfg == {"fast_model": "a-model", "deep_model": "b-model"}, "a save alone changes nothing live"

    # with a live change in the same request: both
    a = fake_app(sim=False)
    out = glide_app.App.on_settings(a, {"save": {"companion": {"voice": {"rate": 2}}}, "fast_model": "c-model"})
    assert out == {"fast_model": "c-model", "saved": True}, out
    saved = json.load(open(p, encoding="utf-8"))
    assert saved["companion"]["voice"] == {"rate": 2} and saved["companion"]["llm"]["fast_model"] == "c-model", saved
    assert a.coach.cfg["fast_model"] == "c-model" and announced, announced

    # the old flat body still works and still says whether it saved
    assert glide_app.App.on_settings(fake_app(sim=True), {"deep_model": "d-model"}) == {"deep_model": "d-model",
                                                                                        "saved": False}
    # a bad patch is refused (POST /settings answers 400 with the reason) and the file stays as it is
    before = open(p, encoding="utf-8").read()
    for bad in ({"save": [1]}, {"save": "x"}):
        try:
            glide_app.App.on_settings(fake_app(sim=False), bad)
            raise SystemExit("took %r" % bad)
        except ValueError:
            pass
    with open(p, "w", encoding="utf-8") as fh:
        fh.write('{"wow_dir": ,}')
    try:
        glide_app.App.on_settings(fake_app(sim=False), {"save": {"wow_dir": "E:/WoW"}})
        raise SystemExit("saved over an unreadable config.json")
    except ValueError:
        pass
    assert open(p, encoding="utf-8").read() == '{"wow_dir": ,}' and before

    # the app sends its saves there while the coach runs, and writes the file itself only otherwise
    ts = open(os.path.join(ROOT, "desktop", "src", "main", "app", "config.ts"), encoding="utf-8").read()
    ipc = open(os.path.join(ROOT, "desktop", "src", "main", "app", "ipc.ts"), encoding="utf-8").read()
    assert 'JSON.stringify({ save: patch })' in ts and "saved === true" in ts
    assert "await saveConfig(patch, engine)" in ipc and "updateConfig(" not in ipc
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("OK")
