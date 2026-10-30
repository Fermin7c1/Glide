"""python -m knowledge.mechanics.fit [glide.db]: fit and write data/mechanics_fit.json and data/params.json."""
import json
import os
import sqlite3
import sys

from knowledge.mechanics import params as P
from knowledge.mechanics.fit.report import run

db = sys.argv[1] if len(sys.argv) > 1 else os.path.join(P.ROOT, "data", "glide.db")
conn = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
rep = run(conn)
print(json.dumps(rep["summary"], indent=1))
