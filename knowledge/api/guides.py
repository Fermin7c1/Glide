"""Which RestedXP guide the live state names, and who the player is (class, race, faction, RestedXP settings)."""
import re
import threading

from knowledge import store
from knowledge.api._layer import LL, RP
from knowledge.vocab import HORDE_RACES, glide_race

# The RestedXP options assumed until the addon's page X (or config.json "rxp") says otherwise. Not RestedXP's own
# defaults (rxp_parser.RxpSettings(): all off, what the stored briefs are built with): solo self-found and group
# quests on is how the player's characters are set up in RestedXP's SavedVariables, so the coach numbers their
# steps right before page X arrives.
DEFAULT_RXP = {"ssf": True, "group_quests": True, "hardcore": False}
RXP_KEYS = ("ssf", "group_quests", "hardcore", "dungeons", "season", "phase", "xprate")     # rxp_parser.OPTION_KEYS


def norm_guide_name(s):
    """'05-11  Dun Morogh' == '5-11 dun morogh': case, spacing and the range's zero padding ignored."""
    s = re.sub(r"\s+", " ", (s or "").strip()).lower()
    return re.sub(r"^0*(\d+)\s*-\s*0*(\d+)", r"\1-\2", s)


class _FallbackGuides:
    """Name -> key resolver when sources/live_logs is not importable (same normalisation)."""

    def __init__(self, kb):
        self.rows, self.by = {}, {}
        for r in kb.q("SELECT key, name, grp, faction FROM rxp_guide"):
            self.rows[r["key"]] = r
            forms = {norm_guide_name(r["name"]), norm_guide_name(RP.affix_name(r["name"] or ""))}
            forms.add(norm_guide_name(RP.affix_name(r["name"] or "").encode("utf-8")[:40].decode("utf-8", "ignore")))
            for f in forms:
                self.by.setdefault(f, []).append(r["key"])

    def resolve(self, name, group=None, faction=None):
        keys = list(self.by.get(norm_guide_name(name)) or [])
        if group and len(keys) > 1:
            g = norm_guide_name(group)
            keys = [k for k in keys if norm_guide_name(self.rows[k]["grp"]) == g] or keys
        if not keys:
            return None

        def fac(k):
            f = self.rows[k]["faction"] or ("Horde" if "(H)" in (self.rows[k]["grp"] or "") else
                                           "Alliance" if "(A)" in (self.rows[k]["grp"] or "") else None)
            return bool(faction and f and f != faction)
        return min(keys, key=lambda k: (fac(k), "dungeon" in (self.rows[k]["grp"] or "").lower(), k))


class GuideResolver:
    """(G page name, group, faction) -> rxp_guide.key, cached. Handles '05-11 Dun Morogh' vs '5-11 Dun Morogh'
    and names cut to 40 bytes by the addon."""

    def __init__(self, kb):
        self.kb = kb
        self._g = None
        self._cache = {}
        self._lock = threading.Lock()

    def _impl(self):
        if self._g is None:
            with self._lock:
                if self._g is None:
                    g = None
                    if LL is not None:
                        try:
                            g = LL.Guides(self.kb.conn())
                        except Exception:  # noqa: BLE001
                            g = None
                    self._g = g if g is not None else _FallbackGuides(self.kb)
        return self._g

    def resolve(self, name, group=None, faction=None):
        if not name:
            return None
        key = (str(name), str(group or ""), str(faction or ""))
        if key in self._cache:
            return self._cache[key]
        gk = None
        if "||" in str(name) and self.kb.one("SELECT 1 AS x FROM rxp_guide WHERE key=?", str(name)):
            gk = str(name)
        if gk is None:
            try:
                gk = self._impl().resolve(str(name), group or None, faction=faction or None)
            except Exception:  # noqa: BLE001
                gk = None
        if len(self._cache) > 512:
            self._cache.clear()
        self._cache[key] = gk
        return gk


def resolve_guide_key(kb, name, group=None, faction=None):
    """One-shot resolver (the pack keeps a cached GuideResolver)."""
    if isinstance(kb, str):
        kb = store.open_ro(kb)
    return GuideResolver(kb).resolve(name, group, faction)


def player_from(summary, rxp=None):
    """{"class", "race", "faction"} + RestedXP settings from a GameState.summary(); None when the class is unknown."""
    s = summary or {}
    cls = s.get("class")
    if not cls:
        return None
    p = dict(DEFAULT_RXP)
    p.update({k: v for k, v in (rxp or {}).items() if k in RXP_KEYS and v is not None})
    # what RestedXP itself has set (addon 0.13) decides which steps it shows: it wins over the config
    p.update({k: v for k, v in (s.get("rxp_settings") or {}).items() if k in RXP_KEYS and v is not None})
    p["class"] = str(cls).upper()
    race = s.get("race")
    if isinstance(race, str) and race.strip():
        p["race"] = glide_race({"night elf": "NightElf", "nightelf": "NightElf", "scourge": "Scourge",
                                "undead": "Undead"}.get(race.strip().lower(), race.strip()))
    fac = s.get("faction")
    if isinstance(fac, str) and fac.strip():
        p["faction"] = fac.strip().capitalize()
    else:
        p["faction"] = "Horde" if (p.get("race") in HORDE_RACES or "(H)" in (s.get("guide_group") or "")) \
            else "Alliance"
    return p
