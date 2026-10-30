#!/usr/bin/env python3
"""config.json as the engine reads it at start (app.load_config): a file with a typo (or saved with a BOM by Notepad)
used to stop the coach with a traceback. Now a BOM is fine, and an unreadable file means the defaults plus a clear
log line, with the file itself left as it is."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
import app as glide_app  # noqa: E402

tmp = tempfile.mkdtemp(prefix="glide-config-")
cfg_path = os.path.join(tmp, "config.json")
glide_app.ROOT = tmp
defaults = json.loads(json.dumps(glide_app.DEFAULTS))


def load(text, encoding="utf-8"):
    with open(cfg_path, "w", encoding=encoding) as fh:
        fh.write(text)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cfg = glide_app.load_config()
    return cfg, out.getvalue()


try:
    # no file: the defaults
    assert glide_app.load_config() == defaults

    # a good file merges into the defaults (objects merge, values replace)
    cfg, log = load(json.dumps({"companion": {"grid": {"cols": 64}, "llm": {"briefing_delay_s": 3}}}))
    assert cfg["grid"] == {"cols": 64, "rows": defaults["grid"]["rows"]}, cfg["grid"]
    assert cfg["llm"]["briefing_delay_s"] == 3 and not log, (cfg["llm"], log)

    # saved with a BOM: read as usual
    cfg, log = load(json.dumps({"companion": {"grid": {"rows": 4}}}), encoding="utf-8-sig")
    assert cfg["grid"]["rows"] == 4 and not log, (cfg["grid"], log)

    # a typo: the defaults, a log line naming the file, and the file untouched
    bad = '{"wow_dir": "C:/WoW", "companion": {"llm": {"fast_model": "x",}}'
    cfg, log = load(bad)
    assert cfg == defaults, cfg
    assert "[config] can't read" in log and cfg_path in log, log
    assert open(cfg_path, encoding="utf-8").read() == bad

    # valid JSON that isn't an object
    cfg, log = load("[1, 2]")
    assert cfg == defaults and "[config]" in log, (cfg, log)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("ok")
