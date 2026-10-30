"""The size and shape of what the API returns: the token estimate the budgets use, the fact ids collected from a
result, trimming a tool result and fitting the knowledge block into their budgets."""
import copy
import json
import re

from knowledge.api.values import cut, uniq

BLOCK_TOKENS = 2500             # knowledge block for live callouts (JSON chars / 4)
RESULT_TOKENS = 1500            # one tool result
FACT_RE = re.compile(r"^(q|c|i|p|h|s|g|n|poi):(.+)$", re.I)


def jsonable(obj):
    """obj after a JSON round trip: sets become sorted lists, anything else unknown becomes a string."""
    def default(o):
        if isinstance(o, (set, frozenset)):
            return sorted(o, key=str)
        return str(o)
    return json.loads(json.dumps(obj, default=default))


def approx_tokens(obj):
    """Token estimate the budgets use: compact JSON length / 4."""
    return len(json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str)) // 4


def _shrink(o, n_list, n_str, key=None):
    if isinstance(o, dict):
        return {k: _shrink(v, n_list, n_str, k) for k, v in o.items()}
    if isinstance(o, list):
        keep = n_list * 3 if key == "facts" else n_list
        return [_shrink(v, n_list, n_str) for v in o[:keep]]
    if isinstance(o, str) and len(o) > n_str and key not in ("fact", "id"):
        return cut(o, n_str)
    return o


def trim_result(obj, max_tokens=RESULT_TOKENS):
    """Shrink a JSON-able result until approx_tokens <= max_tokens: shorter lists, then shorter strings. The
    result stays valid JSON (never cut mid-way); "truncated": true says something was dropped."""
    if approx_tokens(obj) <= max_tokens:
        return obj
    o = jsonable(obj)
    for n_list, n_str in ((10, 400), (8, 300), (6, 200), (4, 160), (3, 120), (2, 90), (1, 60)):
        o = _shrink(o, n_list, n_str)
        if approx_tokens(o) <= max_tokens:
            break
    if isinstance(o, dict):
        o["truncated"] = True
        if approx_tokens(o) > max_tokens:                # last resort: scalars only
            o = {k: v for k, v in o.items() if not isinstance(v, (dict, list)) or k == "facts"}
            o["truncated"] = True
    return o


def collect_facts(obj, out=None):
    """Every "fact" / "id" value that looks like a fact id, depth-first, in order."""
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("fact", "id") and isinstance(v, str) and FACT_RE.match(v):
                out.append(v)
            elif k != "facts":
                collect_facts(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_facts(v, out)
    return out


def fit_block(block, budget_tokens=BLOCK_TOKENS):
    """Shrink the knowledge block until approx_tokens <= budget: alternatives, co-located work, rationales and
    notes go first; the step's kind, XP, quests and why codes go last. The facts list is rebuilt from what is left."""
    if approx_tokens(block) <= budget_tokens:
        return block
    b = copy.deepcopy(block)
    st = b.get("step") if isinstance(b.get("step"), dict) else {}
    heur = b.get("heuristics") or []
    pfacts = [f for f in b.get("facts") or [] if f.startswith(("p:", "poi:"))]
    steps = [
        lambda: b.__setitem__("community", (b.get("community") or [])[:1]) if b.get("community") else None,
        lambda: st.__setitem__("alternatives", (st.get("alternatives") or [])[:1]),
        lambda: st.pop("co_located", None),
        lambda: [h.__setitem__("rationale", cut(h.get("rationale"), 70)) for h in heur if h.get("rationale")],
        lambda: st.__setitem__("rxp_notes", (st.get("rxp_notes") or [])[:1]),
        lambda: st.pop("alternatives", None),
        lambda: pfacts.clear(),
        lambda: st.__setitem__("risks", (st.get("risks") or [])[:1]),
        lambda: [h.pop("rationale", None) for h in heur],
        lambda: (b.get("next") or {}).pop("quests", None),
        lambda: st.pop("enables", None),
        lambda: st.__setitem__("quests", (st.get("quests") or [])[:4]),
        lambda: b.__setitem__("heuristics", heur[:3]),
        lambda: st.__setitem__("live", (st.get("live") or [])[:1]),
        lambda: (b.get("target") or {}).pop("abilities", None),
        lambda: st.__setitem__("quests", (st.get("quests") or [])[:2]),
        lambda: st.pop("rxp_notes", None),
        lambda: b.pop("next", None),
        lambda: st.pop("risks", None),
        lambda: b.__setitem__("heuristics", [{"id": h.get("id"), "advice": h.get("advice")} for h in heur[:1]]),
        lambda: b.__setitem__("target", {k: v for k, v in (b.get("target") or {}).items()
                                         if k in ("name", "fact", "rank", "level", "known")} or None),
        lambda: b.__setitem__("pace", {k: v for k, v in (b.get("pace") or {}).items()
                                       if k in ("xp_to_level", "eta_next_level_s")} or None),
        lambda: st.__setitem__("quests", [{"name": q.get("name"), "fact": q.get("fact"),
                                           "xp_at_level": q.get("xp_at_level")} for q in (st.get("quests") or [])[:1]]),
        lambda: [st.pop(k, None) for k in ("live", "time_s", "xp_per_min", "guide", "exp_level", "level_now")],
        lambda: b.pop("heuristics", None),
        lambda: b.pop("target", None),
    ]
    for f in steps:
        b = {k: v for k, v in b.items() if v is not None}
        b["facts"] = uniq(collect_facts(b) + pfacts)
        if approx_tokens(b) <= budget_tokens:
            return b
        f()
    b = {k: v for k, v in b.items() if v is not None}
    b["facts"] = uniq(collect_facts(b) + pfacts)
    b["truncated"] = True                     # the core (step kind, number, XP, why, fact ids) is always kept
    return b
