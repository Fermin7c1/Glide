"""One loader per source. Each module exposes `load(conn, ctx) -> dict` (see knowledge/README.md).

Order (knowledge/build_kb.py): glide_entities, [rxp_parser], seed_items, rxp_db, db2, observations,
live_logs, flags, quest_xp, map_bounds, travel_edges, [mechanics params, heuristics], finalize.
"""
