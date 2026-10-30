"""The engine <-> knowledge seam: one holder of the current knowledge pack (KnowledgeHandle) that the coach, the app
and the rules read, swapped as one; the pack built with the coach's geo; the engine's reads of kb.db (places,
landmarks, step links, NPCs, the lookahead fallback, the prompts' quest rows and the dashboard's quest names) through
knowledge.api methods only; glide.db only without a knowledge pack.

Run: python tests/test_knowledge_seam.py   (prints OK; the real-KB part skips without data/kb.db)
"""
import os
import re
import sqlite3
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
sys.path.append(ROOT)       # after companion/: `knowledge` is the package

from app import DEFAULTS  # noqa: E402
from coach import Coach  # noqa: E402
from glidedb import GlideDB  # noqa: E402
from knowledge_layer import KnowledgeHandle  # noqa: E402
import rules as rules_mod  # noqa: E402
from knowledge import store  # noqa: E402

COMPANION = os.path.join(ROOT, "companion")


def tiny_kb(path):
    """An empty kb.db (every table, no rows): enough for a KnowledgePack that opens (and answers little)."""
    c = store.open_rw(path)
    store.create_schema(c)
    store.set_meta(c, "built", os.path.basename(path))
    c.commit()
    c.close()


tmp = tempfile.mkdtemp(prefix="glide_seam_")
kb1, kb2 = os.path.join(tmp, "kb.db"), os.path.join(tmp, "kb.next.db")
tiny_kb(kb1)
tiny_kb(kb2)

# ---- one holder: the coach's handle is what the app and the rules read; a swap replaces pack and api together ----
c = Coach(dict(DEFAULTS["llm"]), GlideDB(os.path.join(tmp, "no-glide.db")), lambda m: None, kb=kb1, offline_ok=True)
h = c.kb_handle
assert isinstance(h, KnowledgeHandle) and c.pack is not None and c.pack is h.pack and c.api is h.api, c.kb_reason
assert c.pack.geo is c.geo, "the pack is built with the coach's geo, not given it afterwards"
r = rules_mod.Rules(dict(DEFAULTS["rules"]))
r.kb_fn = h
first = c.pack
assert r.kb_fn() is first
assert c.swap_kb(kb2) is None
assert c.pack is not first and h.pack is c.pack and r.kb_fn() is c.pack, "every reader sees the new pack"
assert c.pack.kb.path == kb2 and c.pack.geo is c.geo and c.kb_reason is None
assert c.geo._pack() is c.pack, "the geo reads places from the current pack"
# a coach built without __init__ (tests do that) still has a handle behind pack / api / kb_reason
bare = Coach.__new__(Coach)
assert bare.pack is None
bare.pack = first
assert bare.kb_handle.pack is first and bare.api is None
# the app keeps no knowledge state of its own: pack and kb_reason are views of the coach's handle
src = open(os.path.join(COMPANION, "app.py"), encoding="utf-8").read()
assert "self.kb_handle = self.coach.kb_handle" in src and not re.search(r"self\.(pack|kb|kb_reason) = ", src)
# no raw SQL against kb.db in the engine: it goes through knowledge.api (glide.db's own reader is glidedb.py; geo.py
# keeps glide.db's queries for a companion without a knowledge base)
for name in ("briefing.py", "reminders.py", "coach.py", "app.py", "rules.py", "prompt_context.py"):
    text = open(os.path.join(COMPANION, name), encoding="utf-8").read()
    assert not re.search(r"\.kb\.(q|one|bounds)\(|pack\.kb\b|NpcIndex\(self\.pack\.kb", text), name
# glide.db is read at runtime only where there is no knowledge pack (the documented fallbacks below) or by the
# simulator; everything else reads kb.db through the pack. Each <x>.k.<read> in companion/ must be listed here.
import ast  # noqa: E402
GLIDE_READS = {("app.py", "on_new_knowledge", "reopen"), ("app.py", "_quest_basics", "quest"), ("app.py", "start", "ok"),
               ("coach.py", "_rxp_upcoming", "rxp_upcoming"), ("coach.py", "_quest_name", "quest"),
               ("coach_briefings.py", "_near_quest", "quest"), ("geo.py", "map_name", "map_name"),
               ("geo.py", "areas", "q"), ("geo.py", "landmarks", "q"), ("health.py", "checks", "ok"),
               ("prompt_context.py", "game_context", "quest")}
found = set()
for name in sorted(os.listdir(COMPANION)):
    if not name.endswith(".py") or name in ("glidedb.py", "sim.py"):
        continue
    tree = ast.parse(open(os.path.join(COMPANION, name), encoding="utf-8").read())

    def walk(node, func, name=name):
        for ch in ast.iter_child_nodes(node):
            f = ch.name if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
            if isinstance(ch, ast.Attribute):
                v = ch.value
                if (isinstance(v, ast.Attribute) and v.attr == "k") or (
                        name == "prompt_context.py" and isinstance(v, ast.Name) and v.id == "knowledge"):
                    found.add((name, f, ch.attr))
            walk(ch, f)
    walk(tree, None)
assert found <= GLIDE_READS, "glide.db read outside the no-pack fallbacks: %s" % sorted(found - GLIDE_READS)
print("handle OK")

# ---- schema stamp: the build writes meta schema_version; a pack refuses another one, and the KB counts as stale ----
import contextlib  # noqa: E402
import io  # noqa: E402
from knowledge import build_kb  # noqa: E402
from knowledge.api import KnowledgePack  # noqa: E402
from knowledge.analyzer.kbdata import ConnKB  # noqa: E402
assert KnowledgePack(kb1).ok, "a kb.db without a stamp (built before it existed) counts as schema 1"
assert store.SCHEMA_VERSION == 1
kb3 = os.path.join(tmp, "kb.future.db")
tiny_kb(kb3)
c3 = sqlite3.connect(kb3)
store.set_meta(c3, "schema_version", str(store.SCHEMA_VERSION + 1))
c3.commit()
c3.close()
p3 = KnowledgePack(kb3)
assert not p3.ok and p3.reason == "rebuild needed (schema 2, engine expects 1)", p3.reason
assert p3.status() == {"ok": False, "reason": p3.reason}
assert c.swap_kb(kb3) == p3.reason and c.pack.kb.path == kb2, "the coach keeps the old pack and says why"
st = build_kb.staleness(kb3, inputs=[])
assert st["stale"] and "schema 2" in st["reason"], st
assert "SCHEMA_VERSION" in open(os.path.join(ROOT, "knowledge", "build_kb.py"), encoding="utf-8").read()
# the build adapter no longer swallows a failed query silently: it is printed once per distinct error
out = io.StringIO()
with contextlib.redirect_stdout(out):
    ck = ConnKB(sqlite3.connect(":memory:"))
    assert ck.q("SELECT nope FROM no_such_table_seam") == [] and ck.q("SELECT nope FROM no_such_table_seam") == []
assert out.getvalue().count("no such table: no_such_table_seam") == 1, out.getvalue()
print("schema stamp OK")

# ---- the quest rows from kb.db: GlideDB.quest()'s shape; no glide.db read while a pack exists (R34 fixture) ----
sys.path.append(os.path.join(ROOT, "tests", "kb"))
import fixture  # noqa: E402
import prompt_context  # noqa: E402
from app import App  # noqa: E402
from state import GameState  # noqa: E402
fx_kb, _summary, fx_root, _run = fixture.build(fixture.tmpdir("seam_"))
fx_glide = GlideDB(os.path.join(fx_root, "data", "glide.db"))
fx_pack = KnowledgePack(fx_kb)
assert fx_pack.ok, fx_pack.reason
fx_quests = sorted(fx_pack.data.quests)
assert len(fx_quests) >= 6
for qid in fx_quests:
    for near, deep in ((1426, False), (None, True)):
        old, new = fx_glide.quest(qid, near_map=near, detail=deep), fx_pack.quest_detail(qid, near_map=near, detail=deep)
        assert set(old) == set(new), (qid, sorted(old), sorted(new))
        for key in ("id", "name", "level", "min_level", "xp", "objective_text", "details", "giver", "turn_in",
                    "giver_at", "turn_in_at"):
            assert old.get(key) == new.get(key), (qid, key, old.get(key), new.get(key))
        assert [sorted(o) for o in old.get("objectives", ())] == [sorted(o) for o in new.get("objectives", ())], qid
        for a, b in zip(old.get("objectives", ()), new.get("objectives", ())):
            assert a.get("need") == b.get("need") and a.get("target") == b.get("target"), (qid, a, b)
            if a.get("where"):
                assert set(a["where"]) == set(b["where"]) == {"map", "x", "y", "spawn_points"}, (qid, a, b)
                assert a["where"]["map"] == b["where"]["map"], (qid, a, b)
assert fx_pack.quest_detail(99999999) == {"id": 99999999} and fx_pack.quest_detail("90002")["id"] == 90002
assert fx_pack.quest_basics(90002) == {"name": "Fixture Boars", "level": 6} and fx_pack.quest_basics(99999999) == {}


class NoGlide(GlideDB):
    """glide.db's reader that fails every read: with a knowledge pack nothing may use it."""
    def _no(self, *a, **kw):
        raise AssertionError("glide.db read with a knowledge pack present")
    q = quest = map_name = entity = spawn_summary = rxp_upcoming = _no


cg = Coach(dict(DEFAULTS["llm"]), NoGlide(os.path.join(fx_root, "data", "glide.db")), lambda m: None, kb=fx_kb,
           offline_ok=True)
assert cg.pack is not None, cg.kb_reason
fx_s = {"map": 1426, "faction": "Alliance", "zone": "Dun Morogh", "guide": "5-8 Dun Morogh", "step": 5,
        "steps": [{"index": 5, "text": "", "elements": [{"tag": "goto", "map": 1426, "x": 44.0, "y": 60.0},
                                                         {"tag": "complete", "quest": 90002}]},
                  {"index": 6, "text": "", "elements": [{"tag": "turnin", "quest": 90002},
                                                         {"tag": "accept", "quest": 90003}]}],
        "quests": {90002: {"done": False, "obj": [[3, 8]]}, 90004: {"done": False, "obj": [[0, 6]]}}}
for deep in (False, True):
    ctx = prompt_context.game_context(fx_s, None, cg.k, cg.geo, cg.pack, deep=deep)
    assert ctx["quests"][90002]["name"] == "Fixture Boars" and ctx["quests"][90002]["objectives"][0]["where"]
    assert set(ctx["quests"]) == {90002, 90003, 90004}
assert cg._quest_name(90004) == "Fixture Wolves" and cg._rxp_upcoming(fx_s) is not None
app = App.__new__(App)
app.k, app.coach, app.kb_handle, app.state = cg.k, cg, cg.kb_handle, GameState()
ds = {**fx_s, "steps": [dict(st, elements=[dict(e) for e in st["elements"]]) for st in fx_s["steps"]]}
app._describe_places(ds, None)
assert ds["steps"][0]["elements"][1]["name"] == "Fixture Boars" and ds["quests"][90004]["level"] == 8, ds
app.state.summary = lambda: fx_s
assert "Fixture Wolves" in app.vocabulary()
# and without a pack, the same calls read glide.db
c0 = Coach(dict(DEFAULTS["llm"]), fx_glide, lambda m: None, kb=None, offline_ok=True)
assert c0.pack is None
ctx0 = prompt_context.game_context(fx_s, None, c0.k, c0.geo, None)
assert ctx0["quests"][90002]["name"] == "Fixture Boars" and c0._quest_name(90004) == "Fixture Wolves"
print("quest rows OK")

# ---- the engine's reads, on the real knowledge base ----
KB = os.path.join(ROOT, "data", "kb.db")
GLIDE = os.path.join(ROOT, "data", "glide.db")
if not (os.path.exists(KB) and os.path.exists(GLIDE)):
    print("SKIP: engine reads (no data/kb.db or data/glide.db); the rest passed")
    raise SystemExit(77)
from knowledge_layer import load_knowledge_layer, open_pack  # noqa: E402
kb, api, why = load_knowledge_layer(KB)
pack, why = open_pack(api, kb)
assert pack is not None, why
g = sqlite3.connect("file:%s?mode=ro" % GLIDE.replace("\\", "/"), uri=True)
for um in (1426, 1429, 1411):
    old = [tuple(x) for x in g.execute("SELECT name, x1, y1, x2, y2 FROM map_areas WHERE ui_map=? AND name IS NOT NULL",
                                       (um,))]
    assert [tuple(a.values()) for a in pack.map_areas(um)] == old, "the kb's sub-zone boxes are glide.db's"
marks = pack.landmarks(1426)
assert marks and all(m["name"] and m["x"] is not None for m in marks)
assert any(m["subname"] == "Innkeeper" for m in marks), "innkeepers are landmarks"
assert pack.map_name(1426) == "Dun Morogh" and pack.map_name(None) is None
assert pack.continent_of(1426) == (kb.bounds(1426) or {}).get("continent") is not None
up = pack.rxp_upcoming("05-11 Dun Morogh", "RestedXP Forever Guide (A)", "Alliance")
assert up and {u["verb"] for u in up} <= {"accept", "turnin", "complete"} and up == sorted(up, key=lambda u: u["step"])
assert pack.rxp_upcoming("99-99 Nowhere") == []
npcs = pack.friendly_npcs(1426, "Alliance")
assert npcs and all(n["subname"] for n in npcs)
assert pack.quest_name(179) and pack.quest_name(None) is None
print("engine reads OK")
print("OK")
