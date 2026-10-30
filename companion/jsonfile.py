"""The engine's JSON files on disk (layout, reminders, memory, model catalog, XP/hour history, icon map, config.json),
written through a temporary file next to them so a crash or a reader never sees half a file."""
import json
import os
import tempfile


def read(path):
    """A JSON file's content. Tolerates the BOM Notepad may save; raises OSError or ValueError when it can't be read
    (a writer must then leave the file alone, not save over it)."""
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def write_atomic(path, data, end="", **dump_options):
    """Write `data` as JSON to `path` (json.dump options as given, then `end`), creating its folder when needed.
    The temporary file has a name of its own, so two writers never share one."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, **dump_options)
            fh.write(end)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
