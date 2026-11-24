"""Where an item comes from, in the player's words: the one line item tooltips in the game show after "Glide: " (addon
Compare.lua, from the Data/Live.lua live_data.py writes) and what the coach's item_info answers. The sources are
item_db Srcs, from WoWSims, the knowledge base, the player's own observations or the web; the text doesn't say which
(the addon adds "(web, unconfirmed)" to a web fact). Every shape it says:
    drops from Darkmaster Gandling, Scholomance       drops from Magmadar or Garr, Molten Core (two bosses, one place)
    drops from Magmadar and 3 others, Molten Core     drops from Strider Clutchmother (no place known)
    drops in Zephras Isle (a drop known by its place only)        drops from enemies (nothing more known)
    from Sothos and Jarien's Heirlooms, Stratholme (a chest or an event)
    drops from trash in Stratholme      drops from trash      world drop
    reward of General Drakkisath's Demise (2-quest chain, ends at Marshal Maxwell, Burning Steppes), with either part
        or neither: (2-quest chain), (ends at Old Man Heming); "a quest reward" for a quest without a name
    sold by Deliana, Ironforge          sold by Deliana          sold by a vendor in Ironforge        sold by a vendor
    sold by Sergeant Major Clate, a PvP vendor                    sold by a PvP vendor
    sold by Samuel Hawke; needs Honored with the League of Arathor
    sold by Meilosh; needs reputation with Timbermaw Hold (no standing known)
    a reputation reward; needs Honored with the Defilers          a reputation reward from the Defilers
    a reputation reward
    made with Tailoring                 crafted                   PvP reward
    no known source; binds when equipped, so the auction house may have it (NO_SOURCE_BOE: not said yet, as R1 can't
        read how an item binds: research.Research.bond_fn stays None, and an item with no known source is silent)

kb, where given, is the knowledge layer (api.KnowledgePack): a quest's chain (quest_chain), where it ends (quest_giver,
the map's name) and an NPC's name WoWSims leaves out. Pure apart from those reads; no kb (or a read that failed,
logged once): the text without them.
"""
import weakref

import item_db
from log import log_once

NO_SOURCE_BOE = "no known source; binds when equipped, so the auction house may have it"
TRUSTED = 0.6                     # a source at least this sure (kb and up) goes before any less sure one
# the source a line names, when there are several: the easiest to act on first
KIND_ORDER = ("vendor", "rep", "quest", "drop", "crafted", "trash", "world", "pvp")

_chains = weakref.WeakKeyDictionary()      # knowledge pack -> {quest id: its place in its chain}


def _data(kb):
    """The pack's tables (KBData), or None when there is no usable knowledge base."""
    if kb is None or not getattr(kb, "ok", True):
        return None
    try:
        return kb.data
    except Exception as e:  # noqa: BLE001 - no knowledge base: the text without it
        log_once("item_text.data", e)
        return None


def _creature_name(kb, npc):
    data = _data(kb)
    if data is None or not npc:
        return None
    try:
        return str((data.creature(npc) or {}).get("name") or "").strip() or None
    except Exception as e:  # noqa: BLE001 - the text without the name
        log_once("item_text.creature", e)
        return None


def _chain_pos(kb, qid):
    """Where the quest is in its chain (1: none before it), from the knowledge base's quest_chain."""
    try:
        pos = _chains.get(kb)
    except TypeError:                       # (a pack that can't be weakly referenced: not cached)
        pos = None
    if pos is None:
        pos = {}
        rows = kb.kb.q("SELECT quest_id, MAX(pos) AS n FROM quest_chain GROUP BY quest_id")
        for r in rows or ():
            if r.get("quest_id") is not None and r.get("n"):
                pos[r["quest_id"]] = int(r["n"])
        try:
            _chains[kb] = pos
        except TypeError:
            pass
    return pos.get(qid) or 1


def _quest_more(kb, qid):
    """" (2-quest chain, ends at Marshal Maxwell, Burning Steppes)": how long the chain to it is and who it ends at,
    as far as the knowledge base knows; "" when it knows neither."""
    data = _data(kb)
    if data is None or not qid:
        return ""
    parts = []
    try:
        n = _chain_pos(kb, qid)
        if n > 1:
            parts.append("%d-quest chain" % n)
        end = data.giver(qid, "end") or {}
        who = str(end.get("name") or "").strip() or None
        if not who and end.get("kind") == "npc":
            who = _creature_name(kb, end.get("entity_id"))
        if who and end.get("kind") in ("npc", "object"):
            where = data.map_name(end["ui_map"]) if end.get("ui_map") else None
            parts.append("ends at %s" % (who + (", " + where if where else "")))
    except Exception as e:  # noqa: BLE001 - a knowledge base without these tables: the text without them
        log_once("item_text.quest", e)
        return ""
    return " (%s)" % ", ".join(parts) if parts else ""


def _at(who, place):
    return "%s, %s" % (who, place) if place else who


def _faction(s):
    """"the League of Arathor" (WoWSims' "The League of Arathor"), or None."""
    faction = s.get("rep_faction")
    if not faction:
        return None
    return "the " + faction[4:] if faction.startswith("The ") else faction


def _standing(s):
    """"Honored with the League of Arathor" ("reputation with ..." when the standing isn't known), or None."""
    faction, level = _faction(s), s.get("rep_level")
    if not faction:
        return None
    return "%s with %s" % (level if level else "reputation", faction)


def source_text(src, kb=None):
    """One source as the player reads it (no "Glide: " in front); "" for a kind it doesn't know."""
    kind = src.get("kind")
    place = src.get("instance") or src.get("zone_name")
    who = src.get("npc_name") or (_creature_name(kb, src.get("npc")) if kind in ("drop", "vendor", "rep") else None)
    if kind == "drop":
        if who:
            return ("drops from " if src.get("npc") else "from ") + _at(who, place)
        return "drops in %s" % place if place else "drops from enemies"
    if kind == "trash":
        return "drops from trash in %s" % place if place else "drops from trash"
    if kind == "world":
        return "world drop"
    if kind == "quest":
        name = src.get("quest_name")
        return ("reward of %s" % name if name else "a quest reward") + _quest_more(kb, src.get("quest"))
    if kind == "vendor":
        if src.get("zone") in item_db.PVP_VENDOR_ZONES or place == item_db.PVP_VENDOR:
            return "sold by %s, a PvP vendor" % who if who else "sold by a PvP vendor"
        if who:
            return "sold by " + _at(who, place)
        return "sold by a vendor in %s" % place if place else "sold by a vendor"
    if kind == "rep":
        need, faction = _standing(src), _faction(src)
        if who:
            return "sold by %s; needs %s" % (who, need) if need else "sold by " + who
        if need and src.get("rep_level"):
            return "a reputation reward; needs " + need
        return "a reputation reward from " + faction if faction else "a reputation reward"
    if kind == "crafted":
        return "made with %s" % src["profession"] if src.get("profession") else "crafted"
    if kind == "pvp":
        return "PvP reward"
    return ""


def _rank(s):
    conf = float(s.get("conf") or 0)
    kind = s.get("kind")
    return (conf < TRUSTED, KIND_ORDER.index(kind) if kind in KIND_ORDER else len(KIND_ORDER), -conf)


def best(srcs):
    """The source a line names: a sure one before a less sure one, then the easiest to act on (KIND_ORDER), then the
    surest. None for none."""
    srcs = [s for s in srcs or () if isinstance(s, dict) and s.get("kind")]
    return min(srcs, key=_rank) if srcs else None


def line(srcs, kb=None, bond=None):
    """The one tooltip text for an item: its best source (with the other bosses that drop it in the same place, when
    several do), or NO_SOURCE_BOE when there is none and it binds when equipped (bond: ItemSparse Bonding), else
    None (an item nobody knows a source for stays silent)."""
    srcs = [s for s in srcs or () if isinstance(s, dict) and s.get("kind")]
    top = best(srcs)
    if top is None:
        return NO_SOURCE_BOE if bond == item_db.BOND_EQUIP else None
    if top["kind"] == "drop" and top.get("npc"):
        place = top.get("instance") or top.get("zone_name")
        names = []
        for s in sorted(srcs, key=_rank):
            if (s.get("kind") == "drop" and s.get("npc") and _rank(s)[0] == _rank(top)[0]
                    and (s.get("instance") or s.get("zone_name")) == place):
                who = s.get("npc_name") or _creature_name(kb, s.get("npc"))
                if who and who not in names:
                    names.append(who)
        if len(names) == 2:
            return "drops from " + _at("%s or %s" % tuple(names), place)
        if len(names) > 2:
            return "drops from " + _at("%s and %d others" % (names[0], len(names) - 1), place)
    for s in sorted(srcs, key=_rank):
        text = source_text(s, kb)
        if text:
            return text
    return NO_SOURCE_BOE if bond == item_db.BOND_EQUIP else None
