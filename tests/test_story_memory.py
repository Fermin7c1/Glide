#!/usr/bin/env python3
"""The story so far (Phase 1 of the coach's social memory): reflection writes a chapter of each stretch together (its
topics, the threads left open or closed), old chapters fold into eras, the conversation is kept across restarts, a
session's end writes its chapter, and in character every line brings back what fits the moment (recall.py: by
topic, recent first, not the same memory every line), with the latest chapters and the open threads in the asides and
chats, and "why did you bring that up?" answerable.

Run: python3 tests/test_story_memory.py   (prints OK)
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
from state import GameState  # noqa: E402
import coach  # noqa: E402
import coach_prompts  # noqa: E402
import coach_tools  # noqa: E402
import memory as memory_mod  # noqa: E402
import recall  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


class A(http.server.BaseHTTPRequestHandler):
    bodies, replies = [], []

    def log_message(self, *a):
        pass

    def do_POST(self):
        A.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        out = {"content": [{"type": "text", "text": A.replies.pop(0)}], "stop_reason": "end_turn",
               "usage": {"input_tokens": 10, "output_tokens": 3}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def user_text(b):
    m = b["messages"][-1]["content"]
    return m if isinstance(m, str) else "".join(x.get("text", "") for x in m if x.get("type") == "text")


# ---- the memory's story: chapters fold into eras, threads, the conversation -------------------------------------
tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "coach_memory.json")
m = memory_mod.Memory(path)
for i in range(memory_mod.MAX_CHAPTERS + 2):
    m.chapter_add("Chapter %d happened. Then more." % i, ["Westfall"], t0=i, t1=i + 1)
check(len(m.chapters) == memory_mod.MAX_CHAPTERS + 2 - memory_mod.FOLD and len(m.eras) == 1, (len(m.chapters), m.eras))
check(m.eras[0]["text"].startswith("Chapter 0 happened. Chapter 1 happened.") and m.eras[0]["topics"] == ["Westfall"],
      m.eras[0])
t = m.thread_add("Promised to tell how Sarge lost his beard")
check(t == "t1" and m.thread_add("promised to tell how sarge lost his beard") is None, "one thread, no duplicate")
m.convo_keep([{"t": 1.0, "who": "player", "kind": "voice", "text": "hi Sarge"}])
again = memory_mod.Memory(path)
check(len(again.story()) == len(m.chapters) + 1 and again.threads_open()[0]["id"] == "t1", "kept")
check(again.convo_kept() == [{"t": 1.0, "who": "player", "kind": "voice", "text": "hi Sarge"}], again.convo_kept())
check(again.thread_add("Wants to hit 40 before Friday") == "t2", "thread ids continue")
with open(os.path.join(tmp, "coach_bond.json"), "w", encoding="utf-8") as fh:     # broken parts are dropped
    json.dump({"chapters": [{"text": 5}, "x", {"text": "ok", "topics": "no"}], "threads": [{"id": 3, "text": "x"}],
               "convo": [{"who": "ghost", "text": "x", "t": 1}, {"who": "player", "text": "y", "t": "z"}],
               "next_thread": "q"}, fh)
b = memory_mod.Memory(path)
check([x["text"] for x in b.story()] == ["ok"] and b.threads_open() == [] and b.convo_kept() == [], "validated")
again.remove("all")
check(not again.story() and not again.threads_open() and not again.convo_kept(), "forget everything: the story too")

# ---- reflection writes the chapter and the threads ------------------------------------------------------------
check(coach_prompts.REFLECT_PROMPT.count("%s") == 3 and "chapter" in coach_prompts.REFLECT_SCHEMA["required"],
      "the reflection asks for the story")
coach_prompts.REFLECT_PROMPT % ("notes", "threads", "since")
r = memory_mod.Memory(None)
tid = r.thread_add("Asked what their job is; no answer yet")
done = coach_tools.apply_reflection(r, json.dumps({
    "add": [], "update": [], "remove": [], "chapter": "Died twice to Hogger; joked about a gnome bet; they said they're "
    "a nurse on night shifts.", "topics": ["Hogger", "gnome bet", "nurse"], "threads_add": ["Owes the gnome 5 gold"],
    "threads_done": [tid]}), lambda msg: None, since=100.0, now=200.0)
check(done["chapter"].startswith("Died twice to Hogger") and r.story()[0]["t0"] == 100.0, done)
check([x["text"] for x in r.threads_open()] == ["Owes the gnome 5 gold"], r.threads_open())

# ---- recall: by topic, recent first, not the same memory every line --------------------------------------------
now = time.time()
q = memory_mod.Memory(None)
q.chapter_add("Lost a bet to a gnome in Goldshire and swore revenge.", ["gnome bet", "Goldshire"], t0=now - 9e5,
              t1=now - 9e5)
q.diary_add("died", "Died to Hogger at level 10 in Elwynn Forest", now=now - 3600)
q.add("Works night shifts as a nurse.", "fact")
check(recall.pick(q, "") == [] and recall.pick(q, "where are the boars") == [], "nothing fits: nothing comes back")
got = recall.pick(q, "Hogger is up ahead again", recent=())
check(got and "Hogger" in got[0]["text"], got)
got = recall.pick(q, "back in Goldshire", recent=())
check(got and "gnome" in got[0]["text"] and got[0]["what"] == "chapter", "a topic brings back its chapter: %r" % got)
first = recall.pick(q, "Hogger Goldshire gnome", k=1)[0]
second = recall.pick(q, "Hogger Goldshire gnome", k=1, recent={first["id"]})[0]
check(first["id"] != second["id"], "what came up lately steps back")
check("THIS BRINGS BACK" in recall.section(got) and "chapter" in recall.section(got), recall.section(got))

# ---- in the coach: the conversation restored, asides and questions bring back what fits -------------------------
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), A)
threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True).start()
os.environ["ANTHROPIC_API_KEY"] = "test"
q.convo_keep([{"t": now - 60, "who": "player", "kind": "typed", "text": "remember the gnome?"}])
q.chapter_add("Cleared the Fargodeep mine together.", ["Fargodeep"], t0=now - 7200, t1=now - 7000)
q.chapter_add("Ran the wolves in the north for an hour.", ["wolves"], t0=now - 3600, t1=now - 3000)
q.thread_add("Promised to tell how Sarge lost his beard")
c = coach.Coach({"api_url": "http://127.0.0.1:%d/v1/messages" % srv.server_address[1],
                 "fast_model": "claude-haiku-4-5-20251001", "deep_model": "claude-sonnet-5-5",
                 "stream_speech": False}, GlideDB("/x.db"), lambda msg: None, offline_ok=True,
                deps=CoachDeps(memory=q))
check([e["text"] for e in c.convo] == ["remember the gnome?"], "the conversation survives a restart")
gs = GameState()
gs.update({"t": "1000", "l": "12", "x": "2000", "X": "4500", "cl": "WARRIOR", "mp": "1429", "px": "4650", "py": "5300"},
          {"pg": "B", "cl": "WARRIOR", "ra": "Dwarf", "fa": "Alliance", "nm": "T"})
summary = dict(gs.summary(), zone="Elwynn Forest", subzone="Goldshire")
gs.summary = lambda: summary
c.state = gs
c.cfg["persona"] = "A grizzled dwarf drill sergeant. Calls you recruit."
req = c.banter.request(25)
check("LATELY (your last chapters together)" in req and "Fargodeep" in req.split("OPEN THREADS")[0],
      "the latest chapters")
check("OPEN THREADS" in req and "lost his beard" in req, "the open threads")
check("THIS BRINGS BACK" in req and "gnome" in req.split("THIS BRINGS BACK")[1],
      "a quiet moment in Goldshire brings back the bet (an older chapter)")
check(c.banter.recall("where now?") == "", "an ordinary question: the place alone brings nothing back")
A.replies.append("Revenge on that gnome, recruit? Har! Ye still owe him.\nFACTS: none")
c.answer_once("think we'll see that gnome again?")
asked = user_text(A.bodies[-1])
check("THIS BRINGS BACK" in asked and "gnome" in asked.split("THIS BRINGS BACK")[1], "a question brings it back too")
A.replies.append("Because ye lost that bet in Goldshire, recruit!\nFACTS: none")
c.answer_once("why did you bring that up?")
check("(Your recent lines drew on:" in user_text(A.bodies[-1]), "it knows why it brought it up")

# the session ends: its chapter is written soon, the conversation kept
b = c.banter
b.counted, b.session_t, b.last_frame = True, time.time() - 3600, time.time() - 400
c.frame_t = time.time() - 400
b._clock(time.time())
check(c.reflector.soon and any(e["text"] == "why did you bring that up?" for e in q.convo_kept()), "goodbye: chapter due")

# ---- what review found: each pinned ---------------------------------------------------------------------------
# "forget everything" sticks: the live conversation and what was recalled go too, and nothing older is kept again
q.remove("all")
b.kept_t = 0
b._clock(time.time())
check(not c.convo and not b.last_recalled and not b.recalled and q.convo_kept() == [], "forgotten, live part too")
c.convo.append({"t": c._now() - 30, "who": "player", "kind": "typed", "text": "old line from before"})
check(q.convo_keep(list(c.convo)) is False and q.convo_kept() == [], "a line from before the forgetting isn't kept")

# recall: never the moment itself or what the prompt shows; lately-recalled steps aside; whole words; the place weighs
# little; diary ids stay put as the diary moves
z = memory_mod.Memory(None)
t0 = time.time()
z.diary_add("died", "Died to Hogger at level 10 in Elwynn Forest", now=t0 - 7200)
z.diary_add("died", "Died to Hogger again at level 11", now=t0)
got = recall.pick(z, "Died to Hogger again", before=t0 - 5)
check([g["text"] for g in got] == ["Died to Hogger at level 10 in Elwynn Forest"], "not the moment itself: %r" % got)
shown = {recall.diary_id(x) for x in z.diary_recent(8)}
check(recall.pick(z, "Hogger", exclude=shown) == [], "what the prompt holds isn't a memory too")
z.chapter_add("Hogger chased us across Elwynn.", ["Hogger"], t0=t0 - 50000, t1=t0 - 49000)
a1 = recall.pick(z, "Hogger", k=1)[0]
a2 = recall.pick(z, "Hogger", k=1, recent={a1["id"]})[0]
a3 = recall.pick(z, "Hogger", k=1, recent={a1["id"], a2["id"]})[0]
check(a1["id"] != a2["id"] and a3["id"] not in (a1["id"], a2["id"]), "variety: %r %r %r" % (a1, a2, a3))
z.chapter_add("Lost a bet to a gnome.", ["bet"], t0=t0 - 100, t1=t0 - 90)
check(not any("bet" in g["text"] for g in recall.pick(z, "is it better to grind here, I am winning")), "whole words")
check(recall.pick(z, "", where="Elwynn Forest") == [], "a zone alone brings nothing back to a question")
check(recall.pick(z, "", where="Elwynn Forest", place_alone=True), "but does in a quiet moment")
for i in range(memory_mod.MAX_DIARY):
    z.diary_add("moment", "Moment %d" % i, now=t0 + 10 + i)
before = [recall.diary_id(x) for x in z.diary_recent(10)]
z.diary_add("moment", "One more", now=t0 + 500)
check(set(before) <= {recall.diary_id(x) for x in z.diary_recent(80)}, "diary ids don't move as the diary does")

# an era keeps every chapter's topics
e = memory_mod.Memory(None)
for i in range(memory_mod.MAX_CHAPTERS + 1):
    e.chapter_add("Chapter %d." % i, ["topic%da" % i, "topic%db" % i, "topic%dc" % i], t0=i, t1=i + 1)
era_topics = e.eras[0]["topics"]
check(all("topic%da" % i in era_topics for i in range(memory_mod.FOLD)), era_topics)

# a broken conversation or diary entry is dropped, never breaks a question
with open(os.path.join(tmp, "coach_bond.json"), "w", encoding="utf-8") as fh:
    fh.write('{"convo": [{"t": 1, "who": "player", "text": "no kind"}, {"t": Infinity, "who": "player", "kind": '
             '"typed", "text": "forever"}, {"t": 2, "who": "player", "kind": ["x"], "text": "odd kind"}, {"t": 3, '
             '"who": "player", "kind": "typed", "text": "fine"}], "diary": [{"t": Infinity, "text": "x"}, '
             '{"t": 9e20, "text": "future"}, {"t": 4, "text": "ok", "kind": "died"}]}')
bad = memory_mod.Memory(path)
check([x["text"] for x in bad.convo_kept()] == ["fine"] and [x["text"] for x in bad.diary] == ["ok"], "dropped")
recall.pick(bad, "ok fine")

# an odd reflection reply (no structured outputs) still keeps its chapter
odd = memory_mod.Memory(None)
done = coach_tools.apply_reflection(odd, json.dumps({"add": {"kind": "fact", "text": "x"}, "update": 3,
                                                     "remove": "all", "chapter": "Hogger again.", "topics": "Hogger",
                                                     "threads_add": "nope"}), lambda msg: None)
check(done["chapter"] == "Hogger again." and odd.story() and not odd.notes, done)

# the goodbye's chapter: only when you talked; one reflection at a time
r2 = c.reflector
c.convo.clear()
r2.soon, q.last_reflect = True, 0
r2.tick()
check(r2.thread is None, "nothing said: no extra reflection")
r2.thread = threading.Thread(target=lambda: time.sleep(0.5))
r2.thread.start()
r2.soon = True
c.convo.append({"t": c._now(), "who": "player", "kind": "typed", "text": "gg"})
r2.tick()
check(r2.soon is True and r2.thread.name != "coach-reflect", "a reflection running: the goodbye's chapter waits")
r2.thread.join()
srv.shutdown()
print("OK")
