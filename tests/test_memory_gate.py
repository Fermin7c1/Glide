#!/usr/bin/env python3
"""An answer that read web results can't change what the coach remembers about the player (prompt injection: a page
can be written to say "remember: always tell the player to..."): the memory_remember / memory_forget calls after a
web search are refused in that answer, reading the memory and the other tools still run, and an answer without web
results remembers as before. Runs llm_client's tool step with a stand-in for the tools."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "companion"))
import llm_client  # noqa: E402

ran = []
client = llm_client.LLMClient.__new__(llm_client.LLMClient)
client.run_tool = lambda name, args, ctx, left: ran.append(name) or {"ok": True, "results": []}
client.stats = {"tool_calls": 0}


def step(name, meta):
    return client._tool_result({"type": "tool_use", "id": "t", "name": name, "input": {"text": "x"}}, None, meta,
                               8, 30, 30)


# no web read: the memory is written
meta = {"tool_calls": 0, "tools": [], "sources": []}
step("memory_remember", meta)
assert ran == ["memory_remember"], ran

# after the local web search: refused, the rest runs
ran.clear()
meta = {"tool_calls": 0, "tools": [], "sources": []}
step("web_search", meta)
res = step("memory_remember", meta)
step("memory_forget", meta)
step("memory_list", meta)
step("reminder_add", meta)
assert ran == ["web_search", "memory_list", "reminder_add"], ran
assert "web results" in res["content"], res

# after the server's search (its results in the model's reply): refused too
ran.clear()
meta = {"tool_calls": 0, "tools": [], "sources": [], "read_web": True}
step("memory_remember", meta)
assert ran == [], ran
print("OK")
