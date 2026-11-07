#!/usr/bin/env python3
"""Recall by meaning (embeddings.py, llm.recall_meaning): with it on, a memory comes back for what the moment means,
with no word in common ("the big dog-man wrecked us" -> the Hogger chapter). The memories are embedded in the
background (warm: batches, saved compactly in coach_embeddings.json, pruned to what memory holds); a line embeds only
its own words (kept in memory, never on disk), and only once there is something to compare them with. A memory counts
when it stands out from the query's closeness to all of them (some models score any two leveling sentences high).
OpenAI, Gemini (task types, the key in a header) and custom endpoints (nomic prefixes) are asked in their own shapes;
an odd or failing answer gives nothing (never an error), and recall goes by words. Off by default. Against a fake
embeddings server (no paid API).

Run: python3 tests/test_recall_meaning.py   (prints OK)
"""
import http.server
import json
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
from coach_deps import CoachDeps  # noqa: E402
from glidedb import GlideDB  # noqa: E402
import banter  # noqa: E402
import coach  # noqa: E402
import embeddings  # noqa: E402
import memory as memory_mod  # noqa: E402
import recall  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


# a toy meaning: three concepts, each a set of words, and a shared part (`base`) every text has
CONCEPTS = ({"hogger", "gnoll", "dog-man", "wrecked", "chased", "elite"}, {"gnome", "bet", "wager", "gold", "owe"},
            {"murloc", "fish", "lake", "shore"})


class E(http.server.BaseHTTPRequestHandler):
    seen, mode, base = [], ["ok"], [0.05]

    def log_message(self, *a):
        pass

    @staticmethod
    def vec(text):
        ws = set(text.casefold().replace(".", " ").replace(",", " ").split())
        return [float(len(ws & c)) for c in CONCEPTS] + [E.base[0]]

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        E.seen.append((self.path, body, self.headers.get("Authorization") or self.headers.get("x-goog-api-key")))
        if E.mode[0] == "fail":
            self.send_response(500)
            self.end_headers()
            return
        if "batchEmbedContents" in self.path:
            out = {"embeddings": [{"values": E.vec(r["content"]["parts"][0]["text"])} for r in body["requests"]]}
        else:
            texts = body["input"]
            vecs = [E.vec(t) for t in texts]
            if E.mode[0] == "string":
                vecs = ["AAAA" for _ in texts]
            elif E.mode[0] == "null":
                vecs = [None for _ in texts]
            elif E.mode[0] == "short":
                vecs = vecs[:-1]
            out = {"data": [{"index": i, "embedding": v} for i, v in reversed(list(enumerate(vecs)))]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), E)
threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True).start()
base = "http://127.0.0.1:%d" % srv.server_address[1]
cfg = {"endpoints": [{"id": "ollama", "label": "Ollama", "base_url": base + "/v1"}]}

# ---- off by default; an unknown source, or an endpoint without a model, is off too -----------------------------
c = coach.Coach({}, GlideDB("/x.db"), lambda m: None, offline_ok=True)
check(c.embedder is None and c.opt.get("recall_meaning") == "off", "off by default")
check(embeddings.Embedder.from_settings({"recall_meaning": "telepathy"}, {}) is None, "unknown source: off")
check(embeddings.Embedder.from_settings({"recall_meaning": "ep:ollama"}, cfg) is None, "an endpoint needs its model")
check(embeddings.Embedder.from_settings({"recall_meaning": "OpenAI"}, {}).source == "openai", "any case")

# ---- a local endpoint: nothing asked before the warm-up; then by meaning ---------------------------------------
tmp = tempfile.mkdtemp()
m = memory_mod.Memory(os.path.join(tmp, "coach_memory.json"))
m.chapter_add("Hogger chased us across Elwynn, twice.", ["Hogger", "Elwynn"], t0=1, t1=2)
m.chapter_add("Lost a wager to a gnome in Goldshire.", ["gnome bet"], t0=3, t1=4)
m.diary_add("died", "Died to a murloc by the lake")
e = embeddings.Embedder.from_settings({"recall_meaning": "ep:Ollama", "recall_embed_model": "nomic-embed-text"}, cfg,
                                      tmp)
query = "remember when that big dog-man wrecked us"
check(recall.pick(m, query) == [], "by words alone: nothing in common")
check(recall.pick(m, query, embedder=e) == [] and E.seen == [], "not warmed: the line asks for nothing")
check(e.warm([it["text"] for it in recall.items(m)]) and e.warm_ok, "the warm-up")
path, body, _auth = E.seen[-1]
check(path == "/v1/embeddings" and body["model"] == "nomic-embed-text" and len(body["input"]) == 3 and
      all(t.startswith("search_document: ") for t in body["input"]), "the documents, nomic's prefix: %r" % body)
got = recall.pick(m, query, embedder=e)
check(got and got[0]["text"].startswith("Hogger chased us"), "by meaning: the Hogger chapter: %r" % got)
check(E.seen[-1][1]["input"] == ["search_query: " + query], "only the moment's words, as a query: %r" % (E.seen[-1],))
n = len(E.seen)
recall.pick(m, query, embedder=e)
check(len(E.seen) == n, "the query is kept (in memory): no request")

# kept compactly on disk (documents only), pruned to what memory holds
with open(os.path.join(tmp, "coach_embeddings.json"), encoding="utf-8") as fh:
    disk = json.load(fh)
check(disk["source"] == "ep:ollama" and len(disk["vectors"]) == 3 and
      all(isinstance(v, str) for v in disk["vectors"].values()), "three documents, base64: %r" % list(disk))
again = embeddings.Embedder("ep:ollama", "nomic-embed-text", cfg, os.path.join(tmp, "coach_embeddings.json"))
check(len(again.docs) == 3 and not again.queries, "kept on disk; queries are not")
check(embeddings.Embedder("ep:ollama", "other-model", cfg, os.path.join(tmp, "coach_embeddings.json")).docs == {},
      "another model: its vectors don't compare")
again.warm([it["text"] for it in recall.items(m)][:2])
with open(os.path.join(tmp, "coach_embeddings.json"), encoding="utf-8") as fh:
    check(len(json.load(fh)["vectors"]) == 2, "pruned to what memory holds")

# ---- what stands out counts: with a high baseline (every text alike), only the close one ------------------------
E.base[0] = 3.0
h = embeddings.Embedder("ep:ollama", "nomic-embed-text", cfg)
items = recall.items(m)
h.warm([it["text"] for it in items])
sims = recall.closeness(h, query, items, 0.3)
check(len(sims) == 1 and list(sims)[0].startswith("c:"), "only the Hogger chapter stands out: %r" % sims)
E.base[0] = 0.05

# ---- an odd or failing answer: nothing, never an error; a failing query backs off, a failed warm-up doesn't ----
for mode in ("string", "null", "short", "fail"):
    E.mode[0] = mode
    bad = embeddings.Embedder("ep:ollama", "nomic-embed-text", cfg)
    check(bad.query("something %s" % mode) is None and bad.down_until > time.time(), "odd answer (%s): None" % mode)
    w = embeddings.Embedder("ep:ollama", "nomic-embed-text", cfg)
    check(w.warm(["a memory %s" % mode]) is False and w.warm_ok is False and w.down_until == 0.0,
          "a failed warm-up (%s) doesn't stop the queries" % mode)
E.mode[0] = "ok"
k = len(E.seen)
check(bad.query("again") is None and len(E.seen) == k, "backed off: not asked again at once")

# ---- OpenAI and Gemini: their own request shapes and keys -----------------------------------------------------
embeddings.OPENAI_URL = base + "/openai/v1/embeddings"
embeddings.GEMINI_URL = base + "/gemini/%s:batchEmbedContents"
os.environ.pop("OPENAI_API_KEY", None)
check(embeddings.Embedder("openai", "", {}).query("x") is None, "no OPENAI_API_KEY: nothing")
os.environ["OPENAI_API_KEY"] = "test-openai"
check(embeddings.Embedder("openai", "", {}).query("gnome bet") and E.seen[-1][2] == "Bearer test-openai" and
      E.seen[-1][1] == {"model": "text-embedding-3-small", "input": ["gnome bet"]}, E.seen[-1])
os.environ["GEMINI_API_KEY"] = "test-gemini"
g = embeddings.Embedder("gemini", "", {})
check(g.query("murloc lake") and E.seen[-1][0].endswith("gemini-embedding-001:batchEmbedContents") and
      "key=" not in E.seen[-1][0] and E.seen[-1][2] == "test-gemini" and
      E.seen[-1][1]["requests"][0]["taskType"] == "RETRIEVAL_QUERY", E.seen[-1])
g.warm(["Died to a murloc by the lake"])
check(E.seen[-1][1]["requests"][0]["taskType"] == "RETRIEVAL_DOCUMENT", "documents as documents")
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("GEMINI_API_KEY", None)

# ---- in the coach: warmed in the background, by meaning in character, forgotten with everything ------------------
c2 = coach.Coach({"recall_meaning": "ep:ollama", "recall_embed_model": "nomic-embed-text",
                  "endpoints": cfg["endpoints"]}, GlideDB("/x.db"), lambda m: None, offline_ok=True,
                 deps=CoachDeps(memory=m))
c2.cfg["persona"] = "A grizzled dwarf drill sergeant."
check(c2.embedder is not None and c2.embedder.cache_path == os.path.join(tmp, "coach_embeddings.json"), "wired")
c2.banter._warm(time.time())
c2.banter.warming.join(10)
check(c2.banter.warming.name == "coach-embed" and c2.embedder.warm_ok, "warmed in the background")
check("Hogger chased us" in c2.banter.recall("that big dog-man is back"), "a question in character, by meaning")
c2.embedder.warm_ok = False                      # a warm-up that failed is tried again a minute later, not ten
c2.banter.warmed_t = time.time() - banter.WARM_RETRY_S - 1
c2.banter._warm(time.time())
check(c2.banter.warming.is_alive() or c2.banter.warmed_t > time.time() - 5, "retried soon after a failure")
c2.banter.warming.join(10)
m.remove("all")
c2.banter._clock(time.time())
check(not c2.embedder.docs and not os.path.exists(os.path.join(tmp, "coach_embeddings.json")), "forgotten too")
srv.shutdown()
print("OK")
