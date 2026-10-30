"""Time to kill: recorded fights against the combat model, per class."""
import collections
import math

from knowledge.mechanics import combat as C
from knowledge.mechanics.fit.common import median_of, rounded


def _fights_glide(conn, gkills):
    by_s = collections.defaultdict(list)
    for k in gkills:
        by_s[k["session"]].append(k)
    ev = collections.defaultdict(list)
    for s, t, e in conn.execute("SELECT session, t, event FROM obs_events WHERE event IN ('combat','ooc','death') ORDER BY session, t"):
        ev[s].append((t, e))
    out = []
    for s, evs in ev.items():
        start = None
        died = False
        for t, e in evs:
            if e == "combat":
                start, died = t, False
            elif e == "death":
                died = True
            elif e == "ooc" and start is not None:
                ks = [k for k in by_s.get(s, []) if start <= k["t"] <= t + 1]
                if ks and not died:
                    out.append({"src": "glide", "session": s, "start": start, "end": t, "kills": ks})
                start = None
    return out


def _fights_live(live, lkills):
    """Fights from the 1 Hz combat flag, paired with live-only kills (kill frame inside the window)."""
    by_t = {k["t"]: k for k in lkills}
    out = []
    cur = None                      # {"start", "file", "died", "kills"}
    for idx in range(len(live.frames)):
        t, f, c = live.frames[idx]
        if cur and cur["file"] != f:
            cur = None
        inc = c.get("c") == "1"
        if inc and cur is None:
            cur = {"start": t, "file": f, "died": False, "kills": []}
        if cur is None:
            continue
        if c.get("d") in ("1", "2"):
            cur["died"] = True
        k = by_t.get(int(t))
        if k:
            cur["kills"].append(k)
        if not inc:
            if cur["kills"] and not cur["died"]:
                out.append({"src": "live", "session": live.files[f], "start": cur["start"], "end": t,
                            "kills": cur["kills"]})
            cur = None
    return out


def ttk_section(conn, gkills, live, lkills, now):
    """The time-to-kill section -> (section, overrides)."""
    fights = _fights_glide(conn, gkills) + _fights_live(live, lkills)
    rows = []
    for fgt in fights:
        ks = [k for k in fgt["kills"] if not k["partial"]]
        if not ks or len(ks) != len(fgt["kills"]):
            continue
        k0 = ks[0]
        cls = (k0.get("cls") or "").upper()
        if not cls or k0["pl"] is None:
            continue
        last = max(k["t"] for k in ks)
        dur = max(1.0, last - fgt["start"])
        per = dur / len(ks)
        model = []
        for k in ks:
            ml = k["ml"] if k["ml"] is not None else (k["ent_min"] if k["ent_min"] is not None else k["pl"])
            model.append(C.time_to_kill_s(cls, k["pl"], ml, 1 if k["elite"] else 0, calibrated=False))
        m = sum(model) / len(model)
        rows.append({"src": fgt["src"], "cls": cls, "pl": k0["pl"], "n": len(ks), "ttk": per, "model": m,
                     "ratio": per / m, "dl": (k0["ml"] - k0["pl"]) if k0["ml"] is not None else None})
    by_cls = collections.defaultdict(list)
    for r in rows:
        by_cls[r["cls"]].append(r)
    summary, ov = {}, {}
    for cls, rs in sorted(by_cls.items()):
        lv = collections.defaultdict(list)
        for r in rs:
            lv[r["pl"]].append(r)
        ratios = sorted(r["ratio"] for r in rs)
        single = [r for r in rs if r["n"] == 1]
        q1 = ratios[len(ratios) // 4]
        q3 = ratios[(3 * len(ratios)) // 4]
        xs = [r["pl"] for r in rs]
        ys = [math.log(r["ratio"]) for r in rs]
        slope = None
        if len(set(xs)) > 1:
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
        summary[cls] = {
            "fights": len(rs), "single_kill_fights": len(single), "levels": [min(xs), max(xs)],
            "ttk_median_s": rounded(median_of([r["ttk"] for r in rs]), 1), "ratio_median": rounded(median_of(ratios)), "ratio_iqr": [rounded(q1), rounded(q3)],
            "log_ratio_slope_per_level": rounded(slope, 4),
            "by_level": {str(k): {"n": len(v), "ttk_median_s": rounded(median_of([r["ttk"] for r in v]), 1),
                                  "model_s": rounded(median_of([r["model"] for r in v]), 1),
                                  "mob_minus_player": rounded(median_of([r["dl"] for r in v
                                                                         if r["dl"] is not None]), 1)}
                         for k, v in sorted(lv.items())},
            "sources": dict(collections.Counter(r["src"] for r in rs))}
        if len(rs) >= 40:
            scale = round(median_of(ratios), 2)
            ov["combat.ttk_scale.%s" % cls] = {
                "value": scale, "status": "forever-fitted", "updated": now,
                "evidence": "%d fights (%s), levels %d-%d: median observed/model %.2f (IQR %.2f-%.2f); combat start "
                            "to kill; observed TTK grows %+.1f%%/level faster than the model, so do not extrapolate far "
                            "above %d; this player's gear/skill" % (
                                len(rs), ", ".join("%s %d" % kv for kv in sorted(summary[cls]["sources"].items())),
                                min(xs), max(xs), scale, q1, q3, 100 * (math.exp(slope or 0) - 1), max(xs))}
    return {"by_class": summary, "n_fights": len(rows),
            "note": "TTK = (last kill - combat start)/kills; fights with deaths or partial-XP kills excluded"}, ov
