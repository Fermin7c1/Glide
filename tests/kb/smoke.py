"""The report-only "smoke" tier: checks against the player's real data (data/kb.db, data/glide.db, data/live, the
installed RestedXP addon), which changes with every guide update and play session. A smoke check never fails
the run: a broken expectation is printed as a FINDING for someone to look at, missing data as NOT RUN.

    py -3 tests/run_all.py --smoke          every tests/**/smoke_*.py
    py -3 tests/kb/smoke_store.py           one of them

A smoke file calls run(title, [(name, fn), ...]); fn raises AssertionError (a finding) or NotRun(reason)."""
import os
import sys
import traceback

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
KB = os.path.join(ROOT, "data", "kb.db")
GLIDE = os.path.join(ROOT, "data", "glide.db")
LIVE = os.path.join(ROOT, "data", "live")


class NotRun(Exception):
    """The real data this check reads is not here."""


def need(*paths):
    for p in paths:
        if not os.path.exists(p):
            raise NotRun("no %s" % os.path.relpath(p, ROOT))


def run(title, checks):
    """Run every check, print what each found; always exits 0."""
    found = skipped = 0
    for name, fn in checks:
        try:
            fn()
            print("  ok        %s" % name, flush=True)
        except NotRun as e:
            skipped += 1
            print("  NOT RUN   %s: %s" % (name, e), flush=True)
        except AssertionError as e:
            found += 1
            where = traceback.extract_tb(e.__traceback__)[-1]          # a bare assert: show its line
            print("  FINDING   %s: %s" % (name, str(e)[:600] or "line %d: %s" % (where.lineno, where.line)), flush=True)
        except Exception as e:  # noqa: BLE001 - report-only: a crash is a finding too
            found += 1
            print("  FINDING   %s: %s: %s\n%s" % (name, type(e).__name__, e,
                                                  "".join(traceback.format_exc().splitlines(True)[-4:])), flush=True)
    print("smoke %s: %d checks, %d findings, %d not run" % (title, len(checks), found, skipped))
    sys.exit(0)
