#!/usr/bin/env python3
"""Run every test (tests/**/test_*.py, each in its own process, in parallel) and print a summary; exit 1 if any failed.

    py -3 tests/run_all.py            all of them (in parallel across worker processes)
    py -3 tests/run_all.py -j 4       run up to 4 tests at a time (-j 1 for one by one)
    py -3 tests/run_all.py trash kb   only the ones whose path contains one of the words
    py -3 tests/run_all.py --smoke    the report-only checks on the player's real data instead (tests/**/smoke_*.py,
                                      see tests/kb/smoke.py): they print findings and never fail the run

A test passes with exit code 0. One that cannot run here (no data/kb.db, no node, no browser, ...) prints
"SKIP: <reason>" and exits with SKIP_EXIT (77): the summary lists it apart from the passes and the failures.
tests/golden holds the golden-master probes, run by tests/test_golden.py and golden.py (no test_*.py there).

Needs the packages in requirements-dev.txt (lupa for the addon tests): py -3 -m pip install -r requirements-dev.txt"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKIP_EXIT = 77
# Longest-running tests start first when running in parallel so none is left alone at the tail of the run.
_HEAVY = ("test_golden.py", "test_dashboard_page.py", "test_navmesh.py", "kb/test_rxp_updates.py",
          "test_desktop_main.py", "test_models.py", "test_types.py")


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


def _parse_args(argv):
    smoke = False
    jobs = min(8, os.cpu_count() or 4)
    words = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--smoke":
            smoke = True
        elif a in ("-j", "--jobs") and i + 1 < len(argv):
            jobs = max(1, int(argv[i + 1]))
            i += 1
        elif a.startswith("-j") and a[2:].isdigit():
            jobs = max(1, int(a[2:]))
        elif a.startswith("--jobs=") and a[7:].isdigit():
            jobs = max(1, int(a[7:]))
        else:
            words.append(a.lower())
        i += 1
    return smoke, jobs, words


def _run_one(t):
    name = os.path.relpath(t, HERE).replace(os.sep, "/")
    s = time.time()
    r = subprocess.run([sys.executable, t], cwd=os.path.dirname(HERE), stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    return name, r.returncode, r.stdout, time.time() - s


def main():
    try:
        import lupa  # noqa: F401
    except ImportError:
        print("lupa is missing (the addon tests need it): py -3 -m pip install -r requirements-dev.txt")
        return 1
    smoke, jobs, words = _parse_args(sys.argv[1:])
    tests = collect("smoke_*.py" if smoke else "test_*.py")
    if words:
        tests = [t for t in tests if any(w in os.path.relpath(t, HERE).lower() for w in words)]
    failed, skipped = [], []
    t0 = time.time()
    if smoke:
        for t in tests:
            name, _, stdout, dt = _run_one(t)
            print("%s  %5.1fs" % (name, dt))
            print("\n".join("  " + line for line in stdout.rstrip().splitlines()))
        print("\n%d smoke files run (report only: findings never fail the run), %.0fs" % (len(tests), time.time() - t0))
        return 0

    if jobs > 1 and len(tests) > 1:
        heavy = {h: i for i, h in enumerate(_HEAVY)}
        ordered = sorted(tests, key=lambda t: (heavy.get(os.path.relpath(t, HERE).replace(os.sep, "/"), 999),
                                               tests.index(t)))
    else:
        ordered = tests
    with ThreadPoolExecutor(max_workers=min(jobs, max(1, len(ordered)))) as pool:
        futs = [pool.submit(_run_one, t) for t in ordered]
        for fut in (as_completed(futs) if jobs > 1 else futs):
            name, code, stdout, dt = fut.result()
            status = "ok  " if code == 0 else "SKIP" if code == SKIP_EXIT else "FAIL"
            print("%-38s %s  %5.1fs" % (name, status, dt), flush=True)
            if code == SKIP_EXIT:
                skipped.append((name, skip_reason(stdout)))
            elif code != 0:
                failed.append(name)
                print("    " + "\n    ".join(stdout.strip().splitlines()[-6:]), flush=True)
    print("\n%d tests: %d ok, %d skipped, %d failed, %.0fs" % (
        len(tests), len(tests) - len(failed) - len(skipped), len(skipped), len(failed), time.time() - t0))
    if skipped:
        print("SKIP:")
        for name, why in sorted(skipped):
            print("  %-36s %s" % (name, why))
    if failed:
        print("FAILED: " + ", ".join(sorted(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
