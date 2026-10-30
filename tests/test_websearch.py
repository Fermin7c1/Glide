#!/usr/bin/env python3
"""Web search: Claude models get Anthropic's server-side web_search tool on typed questions (spoken ones only when
switched on), its citations become the answer's web sources, searches are counted and priced, a long search's
pause_turn is continued, and an organization without web search falls back to answering without it. Other
providers get the local web_search tool when TAVILY_API_KEY / BRAVE_API_KEY is set.

Run: python3 tests/test_websearch.py   (prints OK)
"""
import http.server
import json
import os
import sys
import threading

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
from glidedb import GlideDB  # noqa: E402
from state import GameState  # noqa: E402
import coach  # noqa: E402
import websearch  # noqa: E402

KB = os.path.join(ROOT, "data", "kb.db")
if not os.path.exists(KB):
    print("SKIP: no data/kb.db")
    raise SystemExit(77)

SEARCH_BLOCKS = [{"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
                  "input": {"query": "wow forever dungeon quest xp"}},
                 {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
                  "content": [{"type": "web_search_result", "url": "https://www.wowhead.com/forever/news/1",
                               "title": "Forever hotfixes", "encrypted_content": "abc", "page_age": "2 days"}]}]


class A(http.server.BaseHTTPRequestHandler):
    bodies, mode = [], "search"

    def log_message(self, *a):
        pass

    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        A.bodies.append(b)
        srv = [t for t in b.get("tools") or () if str(t.get("type", "")).startswith("web_search")]
        code = 200
        if A.mode == "no_filter" and any(t.get("type") == "web_search_20260209" for t in srv):
            out, code = {"type": "error", "error": {"type": "invalid_request_error",
                                                   "message": "tools.21: web_search_20260209 is not available"}}, 400
        elif A.mode == "disabled" and srv:
            out, code = {"type": "error", "error": {"type": "invalid_request_error",
                                                   "message": "web search is not enabled for this organization"}}, 400
        elif A.mode == "pause" and len(A.bodies) == 1:
            out = {"content": SEARCH_BLOCKS[:1], "stop_reason": "pause_turn", "usage": {"input_tokens": 5,
                                                                                         "output_tokens": 2}}
        else:
            out = {"content": SEARCH_BLOCKS + [
                {"type": "text", "text": "Dungeon quests give half the bonus XP on Forever now.",
                 "citations": [{"type": "web_search_result_location", "url": "https://www.wowhead.com/forever/news/1",
                                "title": "Forever hotfixes", "cited_text": "dungeon quest bonus reduced"}]},
                {"type": "text", "text": "\nFACTS: web:wowhead.com"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 100, "output_tokens": 20, "server_tool_use": {"web_search_requests": 1}}}
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), A)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["ANTHROPIC_API_KEY"] = "test"
for k in ("TAVILY_API_KEY", "BRAVE_API_KEY"):
    os.environ.pop(k, None)
msgs = []
c = coach.Coach({"api_url": "http://127.0.0.1:%d/v1/messages" % port, "fast_model": "claude-haiku-4-5-20251001",
                 "deep_model": "claude-opus-5-5"}, GlideDB("/x.db"), msgs.append, kb=KB, offline_ok=True)
gs = GameState()
for page in ({"pg": "B", "cl": "WARRIOR", "ra": "Dwarf", "fa": "Alliance", "nm": "T"},
             {"pg": "G", "gn": "05-11 Dun Morogh", "gg": "RestedXP Forever Guide (A)"}, {}):
    gs.update({"t": "1000", "l": "7", "x": "2000", "X": "4500", "cl": "WARRIOR", "mp": "1426", "px": "4650",
               "py": "5300", "st": "5"}, page)
c.state = gs

res = c.answer_once("did they change dungeon quest xp on forever?", state=gs)       # typed
ans = res["answer"]
body = A.bodies[-1]
web_tools = [t for t in body["tools"] if str(t.get("type")).startswith("web_search")]
assert web_tools == [{"type": "web_search_20260209", "name": "web_search", "max_uses": 2}], body["tools"][-2:]
assert ans["text"] == "Dungeon quests give half the bonus XP on Forever now.", ans
assert ans["web_sources"] == [{"url": "https://www.wowhead.com/forever/news/1", "title": "Forever hotfixes"}], ans
assert "web search" in ans["model"] and ans["facts"] == ["web:wowhead.com"] and not ans.get("facts_unknown"), ans
assert c.stats["web_searches"] == 1 and c.stats["cost_usd"] > 0.01, c.stats
# spoken questions: off by default, and the callouts' tool list matches the spoken one (one cached prefix)
A.bodies.clear()
c.answer_once("anything new?", state=gs, voice=True)
assert not any(str(t.get("type", "")).startswith("web_search") for t in A.bodies[-1]["tools"])
c.cfg["web_search_voice"] = True
A.bodies.clear()
c.answer_once("anything new?", state=gs, voice=True)
assert any(t.get("type") == "web_search_20250305" for t in A.bodies[-1]["tools"]), "Haiku 4.5: the basic tool"
# a long search pauses: the paused assistant turn is sent back unchanged
A.mode, A.bodies = "pause", []
res = c.answer_once("what changed in the last patch?", state=gs)
assert len(A.bodies) == 2 and A.bodies[1]["messages"][-1] == {"role": "assistant", "content": SEARCH_BLOCKS[:1]}
assert res["answer"]["text"].startswith("Dungeon quests"), res
# the API refuses the filtering tool: the same request goes again with the basic one, and stays basic
A.mode, A.bodies = "no_filter", []
res = c.answer_once("what changed in the last patch?", state=gs)
kinds = [[t["type"] for t in b["tools"] if str(t.get("type")).startswith("web_search")] for b in A.bodies]
assert kinds[:2] == [["web_search_20260209"], ["web_search_20250305"]] and c.llm.web_basic, kinds
assert res["answer"]["text"].startswith("Dungeon quests"), res
assert {"type": "web_search_20250305", "name": "web_search", "max_uses": 2} in c._tools(True, "claude-opus-5-5")
c.llm.web_basic = False
# web search disabled for the organization: answered without it, and not offered again this session
A.mode, A.bodies = "disabled", []
msgs.clear()
res = c.answer_once("what changed?", state=gs)
assert res["answer"] and c.llm.web_off and any("Web search is off" in m.get("text", "") for m in msgs), (res, msgs)
assert not any(str(t.get("type", "")).startswith("web_search") for t in A.bodies[-1]["tools"])
assert not any(str(t.get("type", "")).startswith("web_search") for t in c._tools(True, "claude-opus-5-5"))
c.llm.web_off = None
c.cfg["web_search"] = False
assert not any(str(t.get("type", "")).startswith("web_search") for t in c._tools(c.web_for(False), "claude-x"))
srv.shutdown()

# ---- other providers: the local tool, only with a search API key ---------------------------------------------
assert websearch.backend() is None and not any(t.get("name") == "web_search" for t in c._tools(True, "gemini:x"))
os.environ["TAVILY_API_KEY"] = "tv-test"
assert websearch.backend() == "tavily" and websearch.LOCAL_DEF in c._tools(True, "gemini:gemini-2.5-flash")


class FakeResp:
    def __init__(self, d):
        self.d = d

    def read(self):
        return json.dumps(self.d).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


seen = []


def opener(req, timeout=10):
    seen.append((req.full_url, req.headers.get("Authorization"), json.loads(req.data)))
    return FakeResp({"results": [{"title": "Forever patch notes", "url": "https://forums.example/forever/1",
                                  "content": "Dungeon quest bonus XP reduced by 50%."}]})


r = websearch.search("forever dungeon quest xp", opener=opener)
assert seen[0][0] == "https://api.tavily.com/search" and seen[0][1] == "Bearer tv-test" and \
    seen[0][2]["query"] == "forever dungeon quest xp", seen
assert r["results"][0]["url"] == "https://forums.example/forever/1" and "50%" in r["results"][0]["text"], r
os.environ.pop("TAVILY_API_KEY")
assert websearch.search("x")["error"].startswith("no web search backend")
assert websearch.citations([{"type": "text", "citations": [{"url": "u1", "title": "t"}, {"url": "u1"}]}]) == \
    [{"url": "u1", "title": "t"}]
print("OK")
