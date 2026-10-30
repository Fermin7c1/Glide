#!/usr/bin/env python3
"""Run every test (tests/**/test_*.py, each in its own process) and print a summary; exit 1 if any failed.

    py -3 tests/run_all.py            all of them
    py -3 tests/run_all.py trash kb   only the ones whose path contains one of the words
    py -3 tests/run_all.py --smoke    the report-only checks on the player's real data instead (tests/**/smoke_*.py,
                                      see tests/kb/smoke.py): they print findings and never fail the run

A test passes with exit code 0. One that cannot run here (no data/kb.db, no node, no browser, ...) prints
"SKIP: <reason>" and exits with SKIP_EXIT (77): the summary lists it apart from the passes and the failures.
tests/golden holds the golden-master probes, run by tests/test_golden.py and golden.py (no test_*.py there).

Needs the packages in requirements-dev.txt (lupa for the addon tests): py -3 -m pip install -r requirements-dev.txt"""
import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKIP_EXIT = 77


def collect(pattern):
    """tests/**/<pattern>, sorted by path (tests/ first, then the subfolders)."""
    found = glob.glob(os.path.join(HERE, "**", pattern), recursive=True)
    return sorted((p for p in found if "__pycache__" not in p),
                  key=lambda p: (os.path.dirname(p) != HERE, os.path.relpath(p, HERE).lower()))


def skip_reason(out):
    for line in reversed(out.strip().splitlines()):
        if line.startswith("SKIP:"):
            return line[5:].strip()
    return "(no reason printed)"


def main():
    try:
        import lupa  # noqa: F401
    except ImportError:
        print("lupa is missing (the addon tests need it): py -3 -m pip install -r requirements-dev.txt")
        return 1
    args = sys.argv[1:]
    smoke = "--smoke" in args
    words = [w.lower() for w in args if w != "--smoke"]
    tests = collect("smoke_*.py" if smoke else "test_*.py")
    if words:
        tests = [t for t in tests if any(w in os.path.relpath(t, HERE).lower() for w in words)]
    failed, skipped = [], []
    t0 = time.time()
    for t in tests:
        name = os.path.relpath(t, HERE).replace(os.sep, "/")
        s = time.time()
        r = subprocess.run([sys.executable, t], cwd=os.path.dirname(HERE), stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        if smoke:                                    # report-only: show what each one found
            print("%s  %5.1fs" % (name, time.time() - s))
            print("\n".join("  " + line for line in r.stdout.rstrip().splitlines()))
            continue
        status = "ok  " if r.returncode == 0 else "SKIP" if r.returncode == SKIP_EXIT else "FAIL"
        print("%-38s %s  %5.1fs" % (name, status, time.time() - s), flush=True)
        if r.returncode == SKIP_EXIT:
            skipped.append((name, skip_reason(r.stdout)))
        elif r.returncode != 0:
            failed.append(name)
            print("    " + "\n    ".join(r.stdout.strip().splitlines()[-6:]))
    if smoke:
        print("\n%d smoke files run (report only: findings never fail the run), %.0fs" % (len(tests), time.time() - t0))
        return 0
    print("\n%d tests: %d ok, %d skipped, %d failed, %.0fs" % (
        len(tests), len(tests) - len(failed) - len(skipped), len(skipped), len(failed), time.time() - t0))
    if skipped:
        print("SKIP:")
        for name, why in skipped:
            print("  %-36s %s" % (name, why))
    if failed:
        print("FAILED: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
