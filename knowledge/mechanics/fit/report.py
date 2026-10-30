"""The entry point: every section fitted, the report assembled, and the files it writes."""
import json
import os
import time

from knowledge.mechanics.fit.inputs import Live, input_paths, load_seed
from knowledge.mechanics.fit.kills import kill_overrides, kill_section
from knowledge.mechanics.fit.levels import level_section
from knowledge.mechanics.fit.live_only import (death_section, downtime_section, hearth_section, rested_section,
                                               training_section)
from knowledge.mechanics.fit.quests import quest_section
from knowledge.mechanics.fit.speed import speed_section
from knowledge.mechanics.fit.ttk import ttk_section


def run(glide_conn, live_dir=None, seed_path=None, out_dir=None, write=True):
    """Fit everything; returns the report dict (overrides under report['overrides'])."""
    now = time.strftime("%Y-%m-%d")
    paths = input_paths(glide_conn, live_dir, seed_path, out_dir)
    live = Live(paths["live"])
    seed = load_seed(paths["seed"])
    report = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
              "inputs": {"glide_db": paths["db"], "live_dir": paths["live"], "live_files": len(live.files),
                         "live_frames": len(live.frames), "sim_files_skipped": live.sim_files,
                         "seed": bool(seed)}}
    overrides = {}
    kill, gk, lk = kill_section(glide_conn, live)
    report["kill_xp"] = kill
    overrides.update(kill_overrides(kill, now))
    lvl, ov = level_section(glide_conn, live, now)
    report["level_xp"] = lvl
    overrides.update(ov)
    q, ov = quest_section(glide_conn, live, seed, now)
    report["quest_xp"] = q
    overrides.update(ov)
    sp, ov = speed_section(glide_conn, live, now)
    report["speed"] = sp
    overrides.update(ov)
    ttk, ov = ttk_section(glide_conn, gk, live, lk, now)
    report["ttk"] = ttk
    overrides.update(ov)
    if live.ok():
        for name, fn in (("rested", rested_section), ("hearth", hearth_section), ("death", death_section)):
            sec, ov = fn(live, now)
            report[name] = sec
            overrides.update(ov)
        report["training"], ov = training_section(live)
        overrides.update(ov)
        report["downtime"] = downtime_section(live)
    report["overrides"] = overrides
    report["summary"] = _summary(report)
    if write:
        os.makedirs(paths["out"], exist_ok=True)
        _write_json(os.path.join(paths["out"], "mechanics_fit.json"), report)
        _write_json(os.path.join(paths["out"], "params.json"),
                    {"generated": report["generated"], "source": "knowledge/mechanics/fit.py", "params": overrides})
    return report


def _summary(rep):
    k = rep["kill_xp"]
    s = {"kills": k["n_kills"], "kills_clean": k["n_clean"], "classic_agreement": k["classic_agreement"],
         "best_hypothesis": k["best"], "best_agreement": k["best_rate"],
         "anchors_l14": k["anchors_l14"]["fit"], "overrides": sorted(rep["overrides"])}
    lv = rep.get("level_xp", {})
    s["level_table_verified"] = lv.get("verified_levels")
    sp = rep.get("speed", {}).get("trail", {})
    s["trail_run_speed"] = sp.get("median_near_mode")
    return s


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False, sort_keys=False, default=str)
    os.replace(tmp, path)
