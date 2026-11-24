#!/usr/bin/env python3
"""The core's byte budget (docs/STRIP.md, Frame): the core goes in every frame and one page rides along in what it
leaves, so a core longer than 442 bytes (the strip's 573 bytes at its defaults, minus 6 of framing, the "|" and a
124-byte page) leaves the largest page no room. Optional core keys (lg: the generation of Data/Live.lua the UI
loaded) go last, and only into a core that stays within 442 bytes; one left out of a frame goes into the next frame
whatever its size (so long as the frame holds it whole), so it's never held back for long and a page still gets its
room every other frame. Checked on the real addon (State.lua's core and pages, Strip.lua's frames, Codec.lua's bytes,
read back with the engine's companion/protocol.py) under Lua 5.1 with the mocked WoW API, at the edges of the budget,
and on the longest core in the player's logs with every optional key at its longest. Needs lupa."""
import os
import sys

from lupa import lua51

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import protocol  # noqa: E402
import wowmock  # noqa: E402

CAPACITY, PAGE = 573, 124                     # the default strip (96 x 8 cells), the longest page with its "pg=<id>;"
BUDGET = CAPACITY - 6 - 1 - PAGE              # 442
# every optional key at its longest: lg, a generation of 7 digits (a write every 15 s for four years)
LONGEST = {"lg": 9999999}
EXTRA = sum(len(";%s=%s" % kv) for kv in LONGEST.items())
# the longest core in the player's logs (data/live/20261109-202756.jsonl, 443 of 235,049 frames' cores; the median is
# 361 and 14 are over 431): on a flight, with a target, three ring events
WORST = ("gv=0.17.4;gk=7;cf=216;t=289980;l=46;x=70954;X=123200;r=6168;h=-1;m=-1;pt=MANA;cl=MAGE;c=0;d=0;mp=1431;"
         "px=7745;py=4436;f=168;sp=303;g=497810;b=10;du=64;hs=3570;rs=0;mo=1;tx=1;kc=0;kx=0;tg=2409;tl=55;th=-1;tr=7;"
         "td=0;st=98;qn=3;qh=975;an=3;ah=19881;ak=1;sw=558208;sa=2;wn=15;wh=5312;in=9;ih=38054;rn=0;rh=0;rq=0;tp=;"
         "aw=1431:88.5:41.0;wz=0;fd=Nethergarde Keep  Blasted Lands;tt=0;tq=0;e1=19:cst:1295678;"
         "e2=18:zone:Scarlet Raven Tavern;e3=17:alive:")
assert len(WORST) == 443, len(WORST)

L = lua51.LuaRuntime(unpack_returned_tuples=True)
L.execute(wowmock.MOCK)
ns = L.table()
for f in ("Data\\Live.lua", "Core.lua", "Codec.lua", "State.lua", "Gear.lua", "Strip.lua"):
    src = open(os.path.join(ROOT, "addon", "Glide", *f.split("\\")), encoding="utf-8").read()
    L.eval("function(src, name, a, ns) local f, e = loadstring(src, name) if not f then error(e) end return f(a, ns) end")(
        src, "@" + f, "Glide", ns)
G = L.globals()
G.ns = ns
L.execute(r"""
-- every frame the strip encodes: its payload, the capacity it was given and the symbols it paints
FRAMES = {}
local encode = ns.Encode
ns.Encode = function(seq, payload, cap)
  local syms, n = encode(seq, payload, cap)
  FRAMES[#FRAMES + 1] = { payload = payload, cap = cap, syms = syms }
  return syms, n
end
-- one page only, as long as a page gets: whether it rides along says whether the core left it room
table.insert(ns.pageHooks, function(b)
  for k in pairs(b) do b[k] = nil end
  b.Y = function() return "y=" .. ("x"):rep(200) end
end)
""")
G.fire("ADDON_LOADED", "Glide")
G.fire("PLAYER_LOGIN")
ns.RefreshPages()
big = ns.NextPage()
assert big.startswith("pg=Y;") and len(big) == PAGE, (len(big), big[:20])


def frame():
    """One strip frame -> (core text, core dict, page text); the frame's bytes read back whole by the engine."""
    G.tick()
    f = G.FRAMES[len(G.FRAMES)]
    assert f.cap == CAPACITY, f.cap
    payload = f.payload
    assert len(payload.encode("utf-8")) <= CAPACITY - 6, len(payload)
    got = protocol.decode_bytes(protocol.symbols_to_bytes(list(f.syms.values())))
    assert got and got[1] == payload, "the frame carries the payload whole"
    core, _, page = payload.partition("|")
    return core, protocol.parse_payload(payload)[0], page


# the shipped Live.lua (generation 0): no lg at all, so the strip is what it was
assert ns.live.gen == 0
for _ in range(3):
    core, kv, page = frame()
    assert "lg" not in kv and page == big, core[-40:]

# a core of exactly n bytes before its optional keys: three ring events whose text fills the difference (ids of
# three digits first, so they keep their length)
for _ in range(100):
    ns.Event("zone", "")


def core_of(n):
    gen = ns.live.gen
    ns.live.gen = 0
    ns.Event("zone", ""); ns.Event("zone", ""); ns.Event("zone", "")
    base = len(ns.Core())
    d = n - base
    assert 0 <= d <= 120, (n, base)
    for k in (min(d, 40), min(max(d - 40, 0), 40), max(d - 80, 0)):
        ns.Event("zone", "a" * k)
    assert len(ns.Core()) == n, (len(ns.Core()), n)
    ns.live.gen = gen


ns.live.gen = LONGEST["lg"]
# a short core: the optional keys in every frame, last, and the largest page beside them
core, kv, page = frame()
assert core.endswith(";lg=9999999") and kv["lg"] == "9999999" and page == big, core[-40:]
assert len(core) < BUDGET - 50, len(core)

# the core is BUDGET bytes with them: still every frame, and the page fits exactly
core_of(BUDGET - EXTRA)
for _ in range(3):
    core, kv, page = frame()
    assert len(core) == BUDGET and kv["lg"] == "9999999" and page == big, (len(core), len(page))

# a byte more: left out of one frame (the page fits), in the next whatever its size (the page waits), and so on
for n in (BUDGET - EXTRA + 1, BUDGET):
    core_of(n)
    seen = [frame() for _ in range(6)]
    for i, (core, kv, page) in enumerate(seen):
        if "lg" in kv:
            assert core.endswith(";lg=9999999") and len(core) == n + EXTRA and page == "", (n, i, len(core))
            assert i == 0 or "lg" not in seen[i - 1][1], "never two frames running past the budget"
        else:
            assert len(core) == n and page == big, (n, i, len(core), len(page))
            assert i == 0 or "lg" in seen[i - 1][1], "never left out of two frames running"
    assert sum("lg" in kv for _, kv, _ in seen) == 3, n

# the player's longest core: with every optional key at its longest it would pass the budget, so they're left out
# (that core leaves a page only 123 bytes even without them), and go in the next frame
assert len(WORST) + EXTRA > BUDGET
ns.live.gen = 0
ns.CoreOptional("")                                         # (no optional keys: nothing held back)
ns.live.gen = LONGEST["lg"]
assert ns.CoreOptional(WORST) == WORST, "left out"
assert ns.CoreOptional(WORST) == WORST + ";lg=9999999", "in the next frame"
assert ns.CoreOptional(WORST) == WORST, "and out of the one after"
# a core no frame could hold whole with them never gets them (the encoder would cut lg's value)
huge = "x" * (CAPACITY - 7 - EXTRA + 1)
assert ns.CoreOptional(huge) == huge and ns.CoreOptional(huge) == huge and ns.CoreOptional(huge) == huge
print("OK")
