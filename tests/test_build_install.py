"""R31: both builders install their database the same way and fingerprint the code they run.

store.install (used by knowledge/build_kb.py and tools/build_db.py): the finished build replaces the target by a
staged copy + os.replace, so an interrupted copy leaves the old file whole and no partial file behind; a target that
stays locked (Windows: the engine has it open) gets <name>.new.db instead. tools/build_db.py's fingerprint covers the
content of every repo module it runs (build_db.CODE, checked against what importing it really loads), so a change to
knowledge/store.py rebuilds glide.db while a copy with new mtimes does not; build_kb's staleness inputs include
wowfiles/**/*.py the same way. Temp folders only.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from knowledge import build_kb, store  # noqa: E402
import build_db  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print("FAIL:", msg)


def write(p, text, t=None):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    if t is not None:
        os.utime(p, (t, t))


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def test_install(tmp):
    folder = os.path.join(tmp, "install")
    target = os.path.join(folder, "glide.db")
    write(target, "old")
    build = os.path.join(tmp, "build.db")

    # normal: replaced, the build file and the staged copy are gone
    write(build, "new")
    msgs = []
    check(store.install(build, target, msgs.append) == target and read(target) == "new", "install replaces")
    check(not os.path.exists(build) and sorted(os.listdir(folder)) == ["glide.db"] and not msgs,
          "nothing left behind: %r %r" % (os.listdir(folder), msgs))

    # interrupted copy (a crash, a full disk): the old file stays whole, no partial file, the error is raised
    write(build, "newer")
    real_copy = shutil.copyfile

    def torn(src, dst):
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write("ne")                       # half of it, then the failure
        raise OSError(28, "No space left on device")

    shutil.copyfile = torn
    try:
        store.install(build, target, msgs.append)
        check(False, "an interrupted copy raises")
    except OSError:
        pass
    finally:
        shutil.copyfile = real_copy
    check(read(target) == "new" and sorted(os.listdir(folder)) == ["glide.db"],
          "interrupted copy: target untouched, no partial file %r" % os.listdir(folder))
    check(os.path.exists(build), "interrupted copy: the build is kept")

    # locked target (Windows: open without delete sharing): glide.new.db, the target untouched
    real_replace, real_sleep = os.replace, time.sleep

    def locked(src, dst):
        if os.path.abspath(dst) == os.path.abspath(target):
            raise PermissionError(13, "The process cannot access the file because it is being used", dst)
        return real_replace(src, dst)

    os.replace, time.sleep = locked, (lambda s: None)
    try:
        written = store.install(build, target, msgs.append, what="quest database")
    finally:
        os.replace, time.sleep = real_replace, real_sleep
    pend = os.path.join(folder, "glide.new.db")
    check(written == pend == store.pending_path(target), "locked -> glide.new.db, got %r" % written)
    check(read(target) == "new" and read(pend) == "newer" and sorted(os.listdir(folder)) == ["glide.db", "glide.new.db"],
          "locked: files %r" % os.listdir(folder))
    check(any("in use" in m and "glide.new.db" in m and "quest database" in m for m in msgs), "locked: message %r" % msgs)
    check(build_kb.pending_path is store.pending_path, "build_kb uses the same pending name")


def test_build_db_code_list(tmp):
    """Every repo module build_db runs is in build_db.CODE (imports + config.json's reader, as gather_inputs runs)."""
    code = ("import os, sys; sys.path.insert(0, os.path.join(%r, 'tools')); import build_db; build_db.wow_dir_config(); "
            "root = os.path.normcase(%r); "
            "print('\\n'.join(sorted(os.path.relpath(m.__file__, %r).replace(os.sep, '/') for m in list(sys.modules.values()) "
            "if getattr(m, '__file__', None) and os.path.normcase(os.path.abspath(m.__file__)).startswith(root + os.sep))))"
            % (ROOT, ROOT, ROOT))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp, timeout=120)
    loaded = [x for x in r.stdout.split() if x]
    check(r.returncode == 0 and "tools/build_db.py" in loaded, "import build_db: %r %s" % (loaded, r.stderr[-500:]))
    missing = sorted(set(loaded) - set(build_db.CODE))
    check(not missing, "repo modules build_db runs but does not fingerprint: %s" % missing)
    check(all(os.path.isfile(os.path.join(ROOT, *p.split("/"))) for p in build_db.CODE), "every CODE file exists")


def test_build_db_fingerprint(tmp):
    root = os.path.join(tmp, "dbroot")
    for rel in build_db.CODE:
        dst = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(ROOT, *rel.split("/")), dst)
    seed = os.path.join(root, "seed.json.gz")
    write(seed, "seed", time.time() - 500)
    base = build_db.input_fingerprint([seed], root=root)
    check(build_db.input_fingerprint([seed], root=root) == base, "fingerprint is stable")
    store_py = os.path.join(root, "knowledge", "store.py")
    for rel in build_db.CODE:                     # an app update copies the code again: new mtimes, same bytes
        p = os.path.join(root, *rel.split("/"))
        os.utime(p, (time.time() + 50, time.time() + 50))
    check(build_db.input_fingerprint([seed], root=root) == base, "same code with new mtimes: not stale")
    with open(store_py, "a", encoding="utf-8") as fh:
        fh.write("\n# changed\n")
    check(build_db.input_fingerprint([seed], root=root) != base, "a change to knowledge/store.py makes glide.db stale")
    shutil.copyfile(os.path.join(ROOT, "knowledge", "store.py"), store_py)
    wdb = os.path.join(root, "wowfiles", "wdb.py")
    with open(wdb, "a", encoding="utf-8") as fh:
        fh.write("\n# changed\n")
    check(build_db.input_fingerprint([seed], root=root) != base, "a change to wowfiles/wdb.py makes glide.db stale")


def test_kb_wowfiles_input(tmp):
    root = os.path.join(tmp, "kbroot")
    old = time.time() - 1000
    write(os.path.join(root, "knowledge", "x.py"), "x = 1\n", old)
    write(os.path.join(root, "wowfiles", "db2_import.py"), "y = 1\n", old)
    before = build_kb.collect_inputs(root)
    item = [i for i in before if i["pattern"] == "wowfiles/**/*.py"]
    check(len(item) == 1 and item[0]["n"] == 1 and item[0].get("hash"), "wowfiles/**/*.py is a hashed input %r" % item)
    recorded = store.jdump({"root": root, "items": before})
    kb_mtime = time.time()
    write(os.path.join(root, "wowfiles", "db2_import.py"), "y = 1\n", old + 100)     # recopied, same content
    check(build_kb._input_changes(build_kb.collect_inputs(root), recorded, kb_mtime) == [],
          "same wowfiles content with another mtime: not stale")
    write(os.path.join(root, "wowfiles", "db2_import.py"), "y = 2\n", old + 200)     # changed (older than kb.db)
    ch = build_kb._input_changes(build_kb.collect_inputs(root), recorded, kb_mtime)
    check([c["path"] for c in ch] == ["wowfiles/db2_import.py"], "a wowfiles change makes kb.db stale %r" % ch)


def main():
    tmp = tempfile.mkdtemp(prefix="glide_install_")
    try:
        test_install(tmp)
        test_build_db_code_list(tmp)
        test_build_db_fingerprint(tmp)
        test_kb_wowfiles_input(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print("%d failure(s)" % len(failures))
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
