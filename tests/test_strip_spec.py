#!/usr/bin/env python3
"""The strip registry (companion/strip_spec.py) covers everything that crosses the strip, both ways:
- the real addon, run under Lua 5.1 with tests/wowmock and a character that has a bit of everything (a target,
  a corpse, a flight, training, talents, quest rewards, a vendor treasure...), sends only registered core keys,
  pages, page keys and ring event kinds;
- the engine (companion/*.py) and knowledge/sources/live_logs.py read only registered keys and pages: found in
  their source (the dicts named core/page, .core, .pages.get("<id>")), and while GameState, its summary and the
  rules take in the addon's frames;
- addon Coach.lua's switches are the registry's coach flags, the list hash is the same on both sides, and the
  committed docs/STRIP.md is what the registry generates.
What is registered but never sent (or never read) here is printed as a warning, not a failure."""
import ast
import glob
import os
import sys

from lupa import lua51

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import protocol  # noqa: E402
import rules as rules_mod  # noqa: E402
import strip_spec as spec  # noqa: E402
import wowmock  # noqa: E402
from session_book import SessionBook  # noqa: E402
from state import GameState  # noqa: E402

# ---- the addon, with a bit of everything ----------------------------------------------------------------------
L = lua51.LuaRuntime(unpack_returned_tuples=True)
L.execute(wowmock.MOCK)
L.execute(r"""
function hooksecurefunc(name, fn) local orig = _G[name]; _G[name] = function(...) orig(...); fn(...) end end
function TakeTaxiNode() end
function TaxiNodeName() return "Crossroads, The Barrens" end
""")
ns = L.table()
for f in ("Core.lua", "Codec.lua", "State.lua", "Gear.lua", "Extras.lua", "Coach.lua", "Strip.lua", "Minimap.lua",
          "Panel.lua"):
    src = open(os.path.join(ROOT, "addon", "Glide", f), encoding="utf-8").read()
    L.eval("function(src, name, a, ns) local f, e = loadstring(src, name) if not f then error(e) end return f(a, ns) end")(
        src, "@" + f, "Glide", ns)
G = L.globals()
G.ns = ns
L.execute(r"""
local ITEM_LINK = { [2361] = "|Hitem:2361|h", [1] = "|Hitem:1|h" }
C_Item.GetItemInfo = function(x) return GetItemInfo(ITEM_LINK[x] or x) end
ns.VENDOR_TREASURES = { { 2521, 42.3, 23.8, "Test Vendor", "G", "N", { 2361, 1 } } }   -- where the player stands
RXP.settings = { profile = { soloSelfFound = true, enableGroupQuests = false } }
RXP.arrowFrame = { element = { x = 42.12, y = 23.48, zone = 2521 } }
table.insert(RXP.currentGuide.steps[5].elements, { tag = "fly", location = "Crossroads" })
RXP.currentGuide.next = "14-20 The Barrens"
RXP.defaultSpellList = { WARRIOR = { [1] = { 6673, 772 }, [4] = { 99999 } } }
C_Spell = { GetSpellInfo = function(id) if id ~= 99999 then return { name = "Spell " .. id } end end,
            GetSpellSubtext = function() return "Rank 1" end, RequestLoadSpellData = function() end }
RXPCData.flightPaths = { [55] = true }
RXPCData.stepSkip = { [3] = true, [4] = true }
function GetTaxiMapID() return 1414 end
C_TaxiMap = { GetAllTaxiNodes = function() return { { nodeID = 23, state = 0, name = "Orgrimmar" },
                                                     { nodeID = 25, state = 1, name = "Crossroads" } } end }
function UnitCharacterPoints() return 1 end
function GetNumTalentTabs() return 1 end function GetNumTalents() return 2 end
function GetTalentInfo(t, i) return "Talent " .. i, nil, 1, i, 0, 5 end
function GetNumSkillLines() return 2 end
function GetSkillLineInfo(i) return ({ "Swords", "Defense" })[i], false, nil, 35, nil, nil, 40 end
function UnitIsTapDenied() return false end
function UnitIsPlayer() return false end
function InCombatLockdown() return false end
function UnitPosition() return 100, 200, 12.5, 1 end
C_TooltipInfo.GetUnit = function() return { lines = { { type = 8, leftText = "Boar slain: 3/10" } } } end
C_DeathInfo = { GetCorpseMapPosition = function() return { GetXY = function() return 0.62, 0.36 end } end }
DEAD, SICK, TAXI = false, false, false
function UnitIsDeadOrGhost() return DEAD end
function UnitOnTaxi() return TAXI end
C_UnitAuras.GetPlayerAuraBySpellID = function() if SICK then return { expirationTime = NOW + 300 } end end
function GetNumQuestChoices() return 1 end
function GetQuestItemLink(kind, i) return "|Hitem:1|h" end
function GetQuestID() return 92461 end
BAG = { { itemID = 4540, stackCount = 12 } }
C_Container.GetContainerNumSlots = function(b) return b == 0 and #BAG or 0 end
C_Container.GetContainerItemInfo = function(b, s) if b == 0 then return BAG[s] end end
C_Item.GetItemInfoInstant = function(id) return id, "", "", "", 0, 0, 0 end
C_Item.GetItemSpell = function() return "Food", 1 end
""")
G.fire("ADDON_LOADED", "Glide")
G.fire("PLAYER_LOGIN")
G.fire("TAXIMAP_OPENED")
G.fire("PLAYER_TARGET_CHANGED")
G.fire("QUEST_COMPLETE")
ns.ExtrasFlush()
ns.ExtrasPoll(5)                                               # Extras' poll (RestedXP settings, skips, vendor)
L.execute("table.insert(ns.pageHooks, function(b) BUILDERS = b end)")
ns.RefreshPages()
ns.RefreshPages()                                              # buffs: two agreeing reads

sent_core, sent_pages, sent_events = set(), {}, set()          # sent_pages: page id -> keys


def core_now():
    core, _ = protocol.parse_payload(ns.Core() + "|")
    sent_core.update(core)
    for k in ("e1", "e2", "e3"):
        if core.get(k):
            sent_events.add(core[k].split(":", 2)[1])
    return core


def pages_now():
    ns.RefreshPages()
    out = {}
    for pid, fn in L.execute("local o = {} for id, fn in pairs(BUILDERS) do o[id] = fn end return o").items():
        page = protocol.parse_kv("pg=%s;%s" % (pid, fn()))
        sent_pages.setdefault(pid, set()).update(page)
        out[pid] = page
    return out


# every ring event the addon raises, each read off the core right after
EVENTS = [("QUEST_ACCEPTED", 92461), ("QUEST_TURNED_IN", 92461, 450, 0), ("QUEST_REMOVED", 92461),
          ("PLAYER_LEVEL_UP", 8), ("PLAYER_DEAD",), ("PLAYER_ALIVE",), ("PLAYER_UNGHOST",), ("HEARTHSTONE_BOUND",),
          ("ZONE_CHANGED_NEW_AREA",), ("UNIT_SPELLCAST_SUCCEEDED", "player", "g", 8690),
          ("UNIT_SPELLCAST_SUCCEEDED", "player", "g", 6673), ("LEARNED_SPELL_IN_SKILL_LINE", 772)]
for ev in EVENTS:
    G.fire(*ev)
    core_now()
L.execute("TAXI = true")
G.fire("PLAYER_CONTROL_LOST")
G.TakeTaxiNode(1)
core_now()
L.execute("TAXI = false")
L.execute("C_TaxiMap.GetAllTaxiNodes = function() return { { nodeID = 80, state = 1, name = 'Ratchet' } } end")
G.fire("TAXIMAP_OPENED")
ns.ExtrasFlush()
core_now()
G.SlashCmdList.GLIDE("mark hello")
core_now()
assert "fpn" not in sent_events, "a new flight path is no ring event (0.15.3): list F carries it"
L.execute('ns.ReportError("Extras.lua:12: a test error")')     # core er and page E
frames = [(core_now(), pages_now())]
L.execute("DEAD = true")
frames.append((core_now(), pages_now()))
L.execute("DEAD = false; SICK = true; TAXI = true")
frames.append((core_now(), pages_now()))
L.execute("SICK = false; TAXI = false")

# ---- what it sends is registered -----------------------------------------------------------------------------
problems = []
problems += ["core key %r is not in strip_spec.CORE" % k for k in sorted(sent_core - set(spec.CORE_BY_NAME))]
problems += ["ring event %r is not in strip_spec.EVENTS" % k for k in sorted(sent_events - set(spec.EVENT_BY_KIND))]
for pid, keys in sorted(sent_pages.items()):
    allowed = spec.page_keys(pid)
    if allowed is None:
        problems.append("page %r is not in strip_spec.PAGES" % pid)
    else:
        problems += ["page %s key %r is not registered" % (pid, k) for k in sorted(keys - allowed)]
assert not problems, "\n".join(problems)
families = {spec.page_spec(pid).id for pid in sent_pages}
assert len(families) >= 17 and len(sent_core) >= 55 and len(sent_events) >= 14, (families, len(sent_core), sent_events)

# ---- what the engine reads is registered ---------------------------------------------------------------------
READ_CORE, READ_PAGES, READ_PAGE_KEYS = set(), set(), {}       # READ_PAGE_KEYS: page id (None = any page) -> keys
READ_EVENTS, WHERE = set(), {}


def _str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _strs(node):
    """String literals of a constant or a tuple/list/set of them."""
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return [s for s in (_str(e) for e in node.elts) if s is not None]
    s = _str(node)
    return [s] if s is not None else []


def classify(e, env):
    """What a dict expression holds: ("core",), ("pages",) or ("page", id or None); None for anything else."""
    if isinstance(e, ast.BoolOp) and isinstance(e.op, ast.Or):
        return classify(e.values[0], env)
    if isinstance(e, ast.Name):
        return env.get(e.id) or {"core": ("core",), "page": ("page", None)}.get(e.id)
    if isinstance(e, ast.Attribute):
        return {"core": ("core",), "pages": ("pages",)}.get(e.attr)
    if isinstance(e, ast.Call):
        f = e.func
        if isinstance(f, ast.Name) and f.id == "getattr" and len(e.args) >= 2:
            return {"core": ("core",), "pages": ("pages",)}.get(_str(e.args[1]))
        if isinstance(f, ast.Attribute) and f.attr == "get" and e.args and _str(e.args[0]):
            if classify(f.value, env) == ("pages",):
                return ("page", _str(e.args[0]))
    if isinstance(e, ast.Subscript):
        k = _str(e.slice)
        if k == "page":
            return ("page", None)
        if k and classify(e.value, env) == ("pages",):
            return ("page", k)
    return None


def note_read(kind, key, where):
    if kind[0] == "core":
        READ_CORE.add(key)
    elif kind[0] == "page":
        READ_PAGE_KEYS.setdefault(kind[1], set()).add(key)
    WHERE.setdefault((kind[0], kind[1] if len(kind) > 1 else None, key), where)


FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def walk_scope(scope):
    """The nodes of one function (or the module) without those of the functions inside it: a name means one thing
    per function (b is page B in one, a buff in the next)."""
    todo = list(ast.iter_child_nodes(scope))
    while todo:
        n = todo.pop()
        yield n
        if not isinstance(n, FUNCS):
            todo.extend(ast.iter_child_nodes(n))


EVENT_READERS = ("state.py", "app.py", "rules.py")     # where `kind` is a ring event kind


def scan(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    scopes = [tree] + [n for n in ast.walk(tree) if isinstance(n, FUNCS)]
    for fn in scopes:
        env = {}
        for n in walk_scope(fn):                       # names bound to a core or page dict in this function
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                c = classify(n.value, env)
                if c:
                    env[n.targets[0].id] = c
        for n in walk_scope(fn):
            where = "%s:%d" % (os.path.relpath(path, ROOT), getattr(n, "lineno", 0))
            c = None
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                attr, args = n.func.attr, n.args
                if attr == "get" and args and _str(args[0]) is not None:
                    c = classify(n.func.value, env)
                    if c and c[0] in ("core", "page"):
                        note_read(c, _str(args[0]), where)
                    elif c == ("pages",):
                        READ_PAGES.add(_str(args[0]))
                    elif isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "lists":
                        READ_PAGES.add(_str(args[0]))
                if attr == "startswith" and isinstance(n.func.value, ast.Name) and n.func.value.id in ("pid", "pg"):
                    READ_PAGES.update(_strs(args[0]))
            elif isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load) and _str(n.slice) is not None:
                c = classify(n.value, env)
                if c and c[0] in ("core", "page") and _str(n.slice) != "page":
                    note_read(c, _str(n.slice), where)
            elif isinstance(n, ast.Compare) and len(n.ops) == 1:
                left = n.left.value if isinstance(n.left, ast.Subscript) else n.left
                lits = _strs(n.comparators[0])
                if isinstance(left, ast.Name) and left.id in ("pid", "pg"):
                    READ_PAGES.update(lits)
                elif isinstance(left, ast.Name) and left.id == "kind" and os.path.basename(path) in EVENT_READERS and isinstance(n.ops[0], (ast.Eq, ast.In)):
                    READ_EVENTS.update(x for x in lits if x.islower())    # (an upper-case kind is a gear list)
                elif isinstance(n.ops[0], ast.In) and _str(n.left):
                    c = classify(n.comparators[0], env)
                    if c and c[0] in ("core", "page"):
                        note_read(c, _str(n.left), where)


ENGINE = sorted(glob.glob(os.path.join(ROOT, "companion", "*.py")))
ENGINE = [p for p in ENGINE if os.path.basename(p) not in ("strip_spec.py", "sim.py")]     # sim writes, not reads
for path in ENGINE + [os.path.join(ROOT, "knowledge", "sources", "live_logs.py")]:
    scan(path)
READ_PAGES.update(GameState().list_chunks)
READ_PAGES.update(GameState.LIST_KEYS)
READ_EVENTS.update(rules_mod.EVENT_TEXT)
READ_EVENTS.update(rules_mod.SILENT_EVENTS)


class Watched(dict):
    """A core or page dict that notes every key the engine looks up."""

    def __init__(self, data, kind):
        super().__init__(data)
        self.kind = kind

    def get(self, k, *a):
        note_read(self.kind, k, "GameState run")
        return super().get(k, *a)

    def __getitem__(self, k):
        note_read(self.kind, k, "GameState run")
        return super().__getitem__(k)

    def __contains__(self, k):
        note_read(self.kind, k, "GameState run")
        return super().__contains__(k)


state, rules, book = GameState(), rules_mod.Rules({}), SessionBook()
rules.started = True
for core, pages in frames:
    for _pid, page in sorted(pages.items()):
        # page reads count for any page: the list code asks every list's first page for pl and v
        events = state.update(Watched(core, ("core",)), Watched(page, ("page", None)))
        rules.check(state, events)
        book.observe(state, 0)
summary = state.summary()
assert summary["training"] and summary["vendor_treasure"] and summary["talents"] and summary["rxp_settings"], summary
assert state.gear["R"] and summary["skills"]["ranks"] and summary["arrow"].get("arrow"), (state.gear, summary["arrow"])

problems = ["engine reads core key %r (%s), not in strip_spec.CORE" % (k, WHERE.get(("core", None, k)))
            for k in sorted(READ_CORE - set(spec.CORE_BY_NAME))]
problems += ["engine reads page %r, not in strip_spec.PAGES" % p for p in sorted(READ_PAGES)
             if spec.page_spec(p) is None and p not in spec.PAGE_BY_ID]
any_page = set().union(*(spec.page_keys(p.id + ("0" if p.indexed else "")) for p in spec.PAGES))
for pid, keys in sorted(READ_PAGE_KEYS.items(), key=lambda kv: kv[0] or ""):
    allowed = any_page if pid is None else spec.page_keys(pid)
    if allowed is None:
        problems.append("engine reads page %r, not in strip_spec.PAGES" % pid)
        continue
    problems += ["engine reads page %s key %r (%s), not registered" % (pid or "*", k, WHERE.get(("page", pid, k)))
                 for k in sorted(keys - allowed)]
problems += ["engine reads ring event %r, not in strip_spec.EVENTS" % k
             for k in sorted(READ_EVENTS - set(spec.EVENT_BY_KIND))]
assert not problems, "\n".join(problems)
assert {"l", "x", "st", "d", "cl", "gv", "e1", "cf"} <= READ_CORE, READ_CORE         # the scan found the readers
assert {"G", "B", "S", "Q", "W", "P", "D", "X"} <= {p[:1] for p in READ_PAGES}, READ_PAGES

# ---- the twins ------------------------------------------------------------------------------------------------
lua_flags = [(f.key, int(f.bit)) for f in ns.COACH_FLAGS.values()]
assert lua_flags == [(f.name, f.bit) for f in spec.COACH_FLAGS], lua_flags
# the cf decoder: no key or junk (older addon) = every switch on; a mask turns on exactly its bits
assert all(spec.coach_flags(None).values()) and set(spec.coach_flags(None)) == {f.name for f in spec.COACH_FLAGS}
assert [k for k, on in spec.coach_flags(1 | 4 | 128).items() if on] == ["coach", "combat", "voice"]
assert not any(spec.coach_flags(0).values())
assert GameState().coach_flags() == spec.coach_flags(None)
for text in ("", "1,2,3", "6673:190:120:mk:Battle Shout", "été," * 40):
    assert int(ns.Hash(text.encode("utf-8"))) == protocol.list_hash(text), text
assert int(ns.PROTO) == protocol.PROTO
with open(spec.DOC, encoding="utf-8") as fh:
    assert fh.read() == spec.markdown(), "docs/STRIP.md is out of date: python companion/strip_spec.py --write"

# ---- registered, but not seen here (warnings) -----------------------------------------------------------------
read_any = READ_CORE | set().union(*READ_PAGE_KEYS.values())
warn = ["core %s" % k for k in spec.CORE_BY_NAME if k not in sent_core]
warn += ["page %s" % p.id for p in spec.PAGES if p.id not in families]
warn += ["page %s key %s" % (p.id, k.name) for p in spec.PAGES for k in p.keys
         if p.id in families and not any(k.name in v for pid, v in sent_pages.items() if pid[:1] == p.id[:1])]
warn += ["event %s" % e for e in spec.EVENT_BY_KIND if e not in sent_events]
print("registered but never sent in this run:", ", ".join(warn) or "none")
unread = ["core %s" % k for k in spec.CORE_BY_NAME if k not in READ_CORE]
unread += ["event %s" % e for e in spec.EVENT_BY_KIND if e not in READ_EVENTS]
unread += ["page %s key %s" % (p.id, k.name) for p in spec.PAGES for k in p.keys if k.name not in read_any]
print("registered but never read by the engine:", ", ".join(unread) or "none")
print("OK")
