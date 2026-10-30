"""Loader 8 (optional): client DB2 tables exported as CSV (wow.tools.local) in data/db2/.

Runs wowfiles/db2_import.import_dir when the folder exists; see that module's docstring for the expected
files and columns. Without the folder it returns {"skipped": ...}: nothing is estimated in its place.
Runs before quest_xp and map_bounds so QuestXP and UiMapAssignment data take precedence over fits.
"""
import os

from wowfiles import db2_import


def load(conn, ctx):
    if not os.path.isdir(ctx.db2_dir):
        return {"skipped": "no %s folder (export DB2 tables as CSV there; see wowfiles/db2_import.py)" % ctx.db2_dir}
    return db2_import.import_dir(conn, ctx.db2_dir, log=ctx.log)
