"""The rest of the knowledge layer, imported defensively: the API never fails at import. When something here
does not import, IMPORT_ERROR says why, the names are None and KnowledgePack answers {"unavailable": reason}."""
try:                                                     # stdlib only; never fail at import
    from knowledge import rxp_parser as RP
    from knowledge.analyzer import alternatives as A
    from knowledge.analyzer import params as AP
    from knowledge.analyzer import review as V
    from knowledge.analyzer import route as R
    from knowledge.analyzer import simulate as S
    from knowledge.analyzer.kbdata import get_data, run_time
    from knowledge.analyzer.model import CUM, LEVEL_XP, MAX_LEVEL, decay_level, level_f, total_xp
    from knowledge.heuristics import engine as E
    from knowledge.mechanics import combat as MC
    from knowledge.mechanics import params as MP
    from knowledge.mechanics import travel as MT
    from knowledge.mechanics import nav as NAV
    from knowledge.mechanics import taxi as TX
    from knowledge.mechanics import xp as MX
    IMPORT_ERROR = None
except Exception as _e:  # noqa: BLE001
    IMPORT_ERROR = "%s: %s" % (type(_e).__name__, _e)
    RP = A = AP = V = R = S = E = MC = MP = MT = NAV = TX = MX = None
    get_data = run_time = decay_level = level_f = total_xp = CUM = LEVEL_XP = MAX_LEVEL = None

try:
    from knowledge.sources import live_logs as LL
except Exception:  # noqa: BLE001 - GuideResolver has a fallback
    LL = None

__all__ = ["A", "AP", "CUM", "E", "IMPORT_ERROR", "LEVEL_XP", "LL", "MAX_LEVEL", "MC", "MP", "MT", "MX", "NAV", "R",
           "RP", "S", "TX", "V", "decay_level", "get_data", "level_f", "run_time", "total_xp"]
