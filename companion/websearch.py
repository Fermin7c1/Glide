"""Web search for the coach, when the knowledge base doesn't cover a question.

Claude models (Anthropic direct) use Anthropic's own server-side web search tool: the search runs inside the API
call, results come back with citations, $10 per 1,000 searches plus the tokens. Other providers (Gemini direct,
OpenRouter) get a local `web_search` tool that the companion runs itself, through a search API whose key you set
as an environment variable (never written to a file):
    TAVILY_API_KEY   tavily.com (returns page extracts; preferred)
    BRAVE_API_KEY    api.search.brave.com (titles + snippets)
Without either key those models don't get web search.
"""
import json
import os
import urllib.parse
import urllib.request

SERVER_TOOL = "web_search_20250305"
DESCRIPTION = ("Search the web when the knowledge base and the other tools don't have the answer: a WoW Forever "
               "change, a bug or exploit the community found, a current event, an item / spell / quest detail the "
               "KB doesn't know. Prefer Forever-specific sources (Wowhead's Forever pages, the official Forever "
               "forums and dev notes, r/classicwow / Forever threads); Classic Era sources may be outdated for "
               "Forever. Returns titles, URLs and text extracts.")
LOCAL_DEF = {"name": "web_search", "description": DESCRIPTION,
             "input_schema": {"type": "object", "required": ["query"], "properties": {
                 "query": {"type": "string", "description": "search terms, e.g. 'wow forever dungeon quest xp nerf'"}}}}


def backend():
    """'tavily' | 'brave' | None for the local tool."""
    if os.environ.get("TAVILY_API_KEY", "").strip():
        return "tavily"
    if os.environ.get("BRAVE_API_KEY", "").strip():
        return "brave"
    return None


# The newer server tool filters results with code before they reach the model (fewer tokens, better answers); only
# these models have it. Others, and any API that refuses it, use SERVER_TOOL.
FILTERED_TOOL = "web_search_20260209"
FILTERED_MODELS = ("opus-5", "opus-4-6", "opus-4-7", "opus-4-8", "sonnet-5", "sonnet-4-6", "fable-5")


def server_tool(max_uses=2, model=None, basic=False):
    m = (model or "").lower()
    kind = FILTERED_TOOL if not basic and any(v in m for v in FILTERED_MODELS) else SERVER_TOOL
    return {"type": kind, "name": "web_search", "max_uses": int(max_uses)}


def search(query, n=5, timeout=12, opener=None):
    """Run the local web search -> {"results": [{"title", "url", "text"}], "backend"} or {"error"}."""
    q = " ".join(str(query or "").split())[:300]
    if not q:
        return {"error": "empty query"}
    kind = backend()
    if kind is None:
        return {"error": "no web search backend: set TAVILY_API_KEY or BRAVE_API_KEY"}
    op = opener or urllib.request.urlopen
    try:
        if kind == "tavily":
            body = json.dumps({"query": q, "max_results": n, "search_depth": "basic", "include_answer": False})
            req = urllib.request.Request("https://api.tavily.com/search", data=body.encode("utf-8"), method="POST",
                                         headers={"content-type": "application/json",
                                                  "authorization": "Bearer " + os.environ["TAVILY_API_KEY"].strip()})
            with op(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            res = [{"title": x.get("title"), "url": x.get("url"), "text": (x.get("content") or "")[:900]}
                   for x in d.get("results") or []][:n]
        else:
            url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": q, "count": n})
            req = urllib.request.Request(url, headers={"accept": "application/json",
                                                       "x-subscription-token": os.environ["BRAVE_API_KEY"].strip()})
            with op(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            res = []
            for x in ((d.get("web") or {}).get("results") or [])[:n]:
                extra = " ".join(x.get("extra_snippets") or [])
                res.append({"title": x.get("title"), "url": x.get("url"),
                            "text": ((x.get("description") or "") + " " + extra).strip()[:900]})
    except Exception as e:  # noqa: BLE001
        return {"error": "web search failed: %s" % e}
    return {"backend": kind, "query": q, "results": res}


def citations(content):
    """[{"url", "title"}] cited in a Claude response's text blocks (server web search), de-duplicated."""
    out, seen = [], set()
    for b in content or ():
        if not isinstance(b, dict):
            continue
        for c in b.get("citations") or ():
            u = c.get("url")
            if u and u not in seen:
                seen.add(u)
                out.append({"url": u, "title": c.get("title")})
    return out
