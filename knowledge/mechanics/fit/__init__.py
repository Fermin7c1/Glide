"""Fit mechanics parameters from recorded observations.

run(glide_conn, live_dir=None, seed_path=None, out_dir=None, write=True) -> report dict

Inputs: data/glide.db (obs_kills, obs_xp, obs_quests, obs_trail, obs_events, sessions, entities,
quests), the companion live logs data/live/*.jsonl (1 Hz core frames: level/XP/XPmax/rested pool/
speed/target level/kill count+XP/hearth cooldown/durability/dead state; pages: auras (A), map size (Z),
target classification (T)) and the community seed data/seed/seed.json.gz (raw quest XP). Simulated
companion runs (--sim: level 1 with a 1200 rested pool) are skipped.

Sections: kill_xp (hypotheses, rounding, ZD, rested, Well Fed, elites, README level-14 anchors,
live-only kills), level_xp, quest_xp, speed, ttk, rested, hearth, death, training, downtime.
Writes data/mechanics_fit.json (full report) and data/params.json (only overrides with strong
evidence) when write=True. Standard library only.

Modules: inputs (paths, live logs, seed), kills, levels, quests, speed, ttk, live_only (rested, hearth, death,
training, downtime), report (run), common (small helpers); `python -m knowledge.mechanics.fit` runs it.
"""
from knowledge.mechanics.fit.report import run

__all__ = ["run"]
