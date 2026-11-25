"""Reminders the player asks for: "remind me to repair when I'm near a repair vendor", "remind me to buy Ice Cold
Milk", "when I get to Ironforge remind me to train", "remind me in 20 minutes to check the auction house".

Each reminder has what to do and when it applies:
  npc    - near a friendly NPC of a role (repair, vendor, food, ammo, reagents, innkeeper, trainer, class trainer,
           flight master, banker, stable master), or one that sells a named item
  place  - in a zone / sub-zone / area whose name matches (Ironforge, Goldshire, Thelsamar ...)
  level  - at or above a level
  time   - after N minutes
  none   - no condition: only the periodic nudges

The coach says a reminder when its condition becomes true (once per arrival: it re-arms after you leave), and
nudges about open ones every `every_min` minutes (default 20; 0 = never), never in combat, on a taxi or dead.
Repair reminders close themselves when durability goes back up; everything else is closed by saying it's done
("done with the repair reminder", "clear reminders"), from the dashboard, or by the coach's reminder_done tool.
Kept in data/live/reminders.json across restarts.

Glide places some itself: a reminder with a "source" belongs to that part of the engine, which puts its own in place of
the ones it placed before (replace). "pursuit" (rules._pursuit): the vendor and auction house steps of the next move
toward the next best-in-slot piece, near the vendor (an npc condition with the vendor's "npc" id and "name") and in
its town or an auction house city (a place condition with one place or a list of them), replaced when the plan changes;
each for the character whose move it is ("char": never said to another) and held while gear talk is muted.
"""
import json
import math
import os
import re
import threading
import time

import jsonfile
import settings
from log import log_once

ROLES = {   # role -> (regex on the NPC's subtitle, words that name the role in requests and in speech)
    "repair": (r"armor|armour|weapon(?!\s+master)|smith|gunsmith|bowyer|shieldcrafter",
               "a repair vendor", ("repair", "armorer", "blacksmith", "weaponsmith")),
    "vendor": (r"general goods|general supplies|trade goods|trade supplies|merchant|vendor|supplies|food|drink|"
               r"baker|butcher|innkeeper|bartender|grocer|tavern|vintner|reagent|bowyer|gunsmith|fisherman",
               "a vendor", ("vendor", "merchant", "sell", "shop", "general goods", "junk")),
    "food": (r"food|drink|baker|butcher|innkeeper|bartender|grocer|tavern|vintner|general goods|cook",
             "a food and drink vendor", ("food", "drink", "water", "bread", "milk")),
    "ammo": (r"bowyer|gunsmith|arrow|bullet|ammo|general goods|general supplies",
             "an ammo vendor", ("arrows", "bullets", "ammo", "ammunition")),
    "reagents": (r"reagent", "a reagent vendor", ("reagent", "reagents")),
    "innkeeper": (r"^innkeeper$", "an innkeeper", ("innkeeper", "inn", "hearth", "hearthstone")),
    "trainer": (r"trainer", "a trainer", ("trainer", "train", "training")),
    "class_trainer": (None, "your class trainer", ("class trainer",)),
    "flight": (r"gryphon master|wind rider master|hippogryph master|flight master|bat handler",
               "a flight master", ("flight master", "flight path", "gryphon", "wind rider", "flight")),
    "bank": (r"^banker$", "a banker", ("bank", "banker")),
    "stable": (r"stable master", "a stable master", ("stable",)),
}
CLASS_TRAINER = "%s trainer"
NEAR_YD = float(settings.default("llm.reminder_near_yd"))     # "near an NPC" (the coach passes its setting)
REARM_S = 120                     # a condition must have been false this long before it fires again
DEFAULT_EVERY_MIN = 20
REPAIR_DONE_RISE = 15             # durability points up from its low since the reminder = repaired


def _norm(s):
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", (s or "").lower()).split())


def role_from_words(text):
    """The NPC role a phrase names ("repair", "buy arrows", "at the innkeeper"), or None."""
    t = " %s " % _norm(text)
    if re.search(r"\bclass trainer\b", t):
        return "class_trainer"
    for role in ("repair", "flight", "innkeeper", "bank", "stable", "reagents", "ammo", "food", "trainer", "vendor"):
        for w in ROLES[role][2]:
            if re.search(r"\b%s\b" % re.escape(w), t):
                return role
    return None


class Reminders:
    """The reminder list (thread-safe) with its JSON file, and the checks that decide when to speak."""

    def __init__(self, path=None, every_min=DEFAULT_EVERY_MIN):
        self.path = path
        self.every_min = every_min
        self.lock = threading.Lock()
        self.items = []
        self.next_id = 1
        if path and os.path.exists(path):
            try:
                d = json.load(open(path, encoding="utf-8"))
                self.items = [r for r in d.get("items", []) if isinstance(r, dict) and not r.get("done")]
                self.next_id = max([int(d.get("next_id", 1))] + [r["id"] + 1 for r in self.items])
            except (OSError, ValueError, KeyError, TypeError):
                pass

    # ---- the list ------------------------------------------------------------------------------
    def save(self):
        if not self.path:
            return
        try:
            jsonfile.write_atomic(self.path, {"next_id": self.next_id, "items": self.items}, indent=1)
        except OSError:
            pass

    def add(self, what, when=None, every_min=None, now=None, durability=None, level=None, source=None):
        """what: the thing to do. when: {"kind": "npc"|"place"|"level"|"time"|"none", "role", "item", "npc", "name",
        "place", "level", "minutes"}; missing: inferred from `what` ("repair" -> near a repair vendor, "buy X" -> near
        a vendor selling X). source: the part of the engine that placed it (None: the player). Returns the reminder
        (a copy)."""
        r = _make(what, when, every_min, time.time() if now is None else now, durability, source)
        with self.lock:
            r["id"] = self.next_id
            self.next_id += 1
            self.items.append(r)
            self.save()
        return dict(r)

    def replace(self, source, specs, now=None, char=None):
        """Put a source's reminders (one the engine places itself, e.g. "pursuit") in place of the ones it placed
        before: specs [{"what", "when", "every_min"}]. One that is already there (the same what and when, for the
        same character) stays as it is, armed or not; the source's others go; the player's own are never touched.
        char: the character key ("Name-Realm" or "Name") they are for: check says them to no other. -> (added,
        removed)."""
        now = time.time() if now is None else now
        want = [_make(sp.get("what"), sp.get("when"), sp.get("every_min"), now, None, source)
                for sp in specs or () if isinstance(sp, dict)]
        for r in want:
            if char:
                r["char"] = str(char)
        with self.lock:
            mine = [r for r in self.items if r.get("source") == source]
            have = {_same(r): r for r in mine}
            keep = {_same(r) for r in want if _same(r) in have}
            removed = [r for r in mine if _same(r) not in keep]
            added = []
            for r in want:
                if _same(r) in keep or any(_same(a) == _same(r) for a in added):
                    continue
                r["id"] = self.next_id
                self.next_id += 1
                added.append(r)
            if removed or added:
                self.items = [r for r in self.items if r not in removed] + added
                self.save()
        return [dict(r) for r in added], [dict(r) for r in removed]

    def remove(self, key=None):
        """key: an id, words of the reminder ("repair"), or None / "all" for every one. -> removed reminders."""
        with self.lock:
            if key in (None, "all", "*"):
                gone, self.items = self.items, []
            else:
                k = str(key).strip().lower()
                gone = [r for r in self.items if str(r["id"]) == k.lstrip("#")]
                if not gone and _norm(k):
                    words = [w for w in _norm(k).split() if w not in ("the", "a", "an", "my", "reminder", "to")]
                    gone = [r for r in self.items
                            if words and all(w in _norm(r["what"] + " " + describe_when(r)) for w in words)]
                self.items = [r for r in self.items if r not in gone]
            if gone:
                self.save()
        return gone

    def list(self):
        with self.lock:
            return [dict(r) for r in self.items]

    # ---- deciding what to say --------------------------------------------------------------------
    def check(self, env, now=None, held=None):
        """env: Env (what's around the player now). held(reminder) -> True: not looked at now, its state left as it
        is (a kind of talk the player muted). A reminder placed for another character (for_other) is left the same
        way. -> [(reminder, "condition"|"periodic"|"done", detail)]."""
        now = time.time() if now is None else now
        out, changed = [], False
        with self.lock:
            for r in list(self.items):
                if for_other(r, env) or (held is not None and held(r)):
                    continue
                w = r["when"]
                # repair reminders close themselves once durability goes back up
                if w.get("role") == "repair" and env.durability is not None:
                    low = min(r.get("dur_low", env.durability), env.durability)
                    if low != r.get("dur_low"):
                        r["dur_low"], changed = low, True
                    if env.durability - low >= REPAIR_DONE_RISE and env.durability >= 90:
                        self.items.remove(r)
                        out.append((dict(r), "done", "repaired"))
                        changed = True
                        continue
                met, detail = self._met(r, env, now)
                if not met:
                    if not r.get("armed") and now - (r.get("false_since") or now) >= REARM_S:
                        r["armed"], changed = True, True
                    if r.get("false_since") is None or r.get("_was_met"):
                        r["false_since"], changed = now, True
                    r["_was_met"] = False
                    if r.get("every_min") and not env.busy and now - r["last_said"] >= r["every_min"] * 60:
                        r["last_said"], changed = now, True
                        out.append((dict(r), "periodic", None))
                    continue
                r["_was_met"] = True
                if r.get("armed") and not env.busy:
                    r.update(armed=False, last_said=now, fired=r.get("fired", 0) + 1)
                    changed = True
                    out.append((dict(r), "condition", detail))
                    if w["kind"] in ("time", "level") and not r.get("every_min"):
                        self.items.remove(r)          # nothing left to watch for
            if changed:
                self.save()
        return out

    @staticmethod
    def _met(r, env, now):
        w = r["when"]
        k = w.get("kind")
        if k == "npc":
            if w.get("npc") or w.get("name"):                  # (one NPC: a vendor the coach's next move names)
                hit = env.nearest(w.get("role"), w.get("item"), npc=w.get("npc"), name=w.get("name"))
            else:
                hit = env.nearest(w.get("role"), w.get("item"))
            return (hit is not None and hit["yards"] <= env.near_yd), hit
        if k == "place":
            places = w.get("place")
            if isinstance(places, list):                       # (any of them: the one it is said with)
                hit = next((p for p in places if env.in_place(p)), None)
                return hit is not None, hit
            return env.in_place(places), None
        if k == "level":
            return bool(env.level and w.get("level") and env.level >= w["level"]), None
        if k == "time":
            return now >= (w.get("at") or 0), None
        return False, None


def _buy_item(what):
    m = re.match(r"(?:buy|get|pick up|grab|restock|stock up on)\s+(?:some\s+|more\s+|a\s+|an\s+|\d+\s+)*(.+)$",
                 what.strip(), re.I)
    if not m:
        return None
    item = m.group(1).strip(" .")
    if role_from_words(item) in ("ammo", "food", "reagents") and len(item.split()) <= 1:
        return None               # "buy arrows" / "buy food": a kind of vendor, not one item
    return item


def _clean(d):
    return {k: v for k, v in d.items() if v not in (None, "", [])}


def _make(what, when, every_min, now, durability=None, source=None):
    """A new reminder (its id still None): the condition inferred from `what` when none is given (Reminders.add)."""
    what = " ".join(str(what or "").split()).strip(" .") or "?"
    when = dict(when or {})
    kind = when.get("kind")
    if not kind or kind == "auto":
        item = when.get("item") or _buy_item(what)
        role = when.get("role") or role_from_words(what)
        if item:
            when.update(kind="npc", item=item, role=role if role not in (None, "vendor") else None)
        elif role:
            when.update(kind="npc", role=role)
        else:
            when["kind"] = "none"
    if when["kind"] == "level":
        when["level"] = int(when.get("level") or 0) or None
    if when["kind"] == "time":
        when["at"] = now + float(when.get("minutes") or 10) * 60
    if when["kind"] == "npc" and when.get("role") not in ROLES and not (when.get("item") or when.get("npc")
                                                                       or when.get("name")):
        when["role"] = role_from_words(when.get("role") or what) or "vendor"
    if when["kind"] == "place" and isinstance(when.get("place"), (list, tuple)):
        when["place"] = [str(p) for p in when["place"] if p] or None
    r = {"id": None, "what": what, "when": _clean(when), "created": now,
         "every_min": DEFAULT_EVERY_MIN if every_min is None else max(0, int(every_min)),
         "last_said": now, "armed": True, "false_since": now, "fired": 0}
    if when.get("role") == "repair" and durability is not None:
        r["dur_low"] = durability
    if source:
        r["source"] = str(source)
    return r


def _same(r):
    """What makes two reminders one (Reminders.replace): what to do and when, and the character it is for."""
    return r.get("what"), json.dumps(r.get("when") or {}, sort_keys=True), r.get("char")


def char_key(s):
    """The character key of a game state summary (bis.char_key's rule: "Name-Realm" when the realm is known, its
    spaces left out, else "Name"); "" without a name."""
    name = str((s or {}).get("name") or "")
    realm = str((s or {}).get("realm") or "").replace(" ", "")
    return "%s-%s" % (name, realm) if name and realm else name


def for_other(r, env):
    """Whether a reminder placed for one character (its "char") is another's than the one playing (env.s): not said
    to them, its state left as it is. One for no character, or with no one known to be playing, is anyone's."""
    want = r.get("char")
    if not want:
        return False
    me = char_key(getattr(env, "s", None))
    if not me:
        return False
    # (a key without the realm matches the same name on any realm, as the planner's "Name" does)
    return want != me and want.split("-")[0] != me and me.split("-")[0] != want


def describe_when(r):
    """'near a repair vendor' / 'in Ironforge' / 'at level 10' / 'in 12 minutes' / ''."""
    w = r.get("when") or {}
    k = w.get("kind")
    if k == "npc":
        if w.get("name"):
            return "near %s" % w["name"]
        if w.get("item"):
            return "near a vendor that sells %s" % w["item"]
        return "near %s" % ROLES.get(w.get("role"), (None, "a vendor"))[1]
    if k == "place":
        places = w.get("place")
        if isinstance(places, list):
            return "in " + (" or ".join(places) if len(places) < 3 else "%s or %s" % (", ".join(places[:-1]),
                                                                                     places[-1]))
        return "in %s" % places
    if k == "level":
        return "at level %s" % w.get("level")
    if k == "time":
        left = max(0, int(round(((w.get("at") or 0) - time.time()) / 60.0)))
        return "in %d minute%s" % (left, "" if left == 1 else "s")
    return ""


def speech(r, why, detail=None):
    """What the coach says for a reminder event."""
    what = r["what"]
    what = what[0].upper() + what[1:] if what else what
    if why == "done":
        return "Reminder done: %s." % r["what"]
    if why == "condition":
        w = r["when"]
        if w.get("kind") == "npc" and detail:
            where = "%s%s is %d yards %s" % (detail["name"], " (%s)" % detail["subname"] if detail.get("subname") else "",
                                             int(round(detail["yards"] / 5.0) * 5) or 5, detail.get("direction") or "away")
            return "Reminder: %s. %s." % (what, where)
        if w.get("kind") == "place":
            return "Reminder, you're in %s: %s." % (detail if isinstance(detail, str) else w.get("place"), what)
        if w.get("kind") == "level":
            return "Reminder for level %s: %s." % (w.get("level"), what)
        return "Reminder: %s." % what
    cond = describe_when(r)
    return "Still to do: %s%s." % (what, " (%s)" % cond if cond else "")


# ------------------------------------------------------------------------------------------------------
# what's around the player (built from the live state + kb.db)
# ------------------------------------------------------------------------------------------------------
def _compass(dx, dy):
    """Bearing from map deltas (x east, y south) -> compass word."""
    deg = (math.degrees(math.atan2(dx, -dy)) + 360) % 360
    return ("north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest")[int((deg + 22.5) // 45) % 8]


class NpcIndex:
    """Friendly NPC spawn points per (ui_map, role / item) from the knowledge pack, cached."""

    def __init__(self, pack, data=None):
        self.pack, self.data = pack, data        # api.KnowledgePack (friendly_npcs, vendors_selling)
        self.cache = {}

    def points(self, ui_map, faction, role=None, item=None, player_class=None, npc=None, name=None):
        """npc / name: one NPC (its creature id, else its name), whatever its role."""
        key = (ui_map, faction, role, (item or "").lower(), player_class, npc, (name or "").lower())
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        rows = []
        try:
            if item and not (npc or name):
                rows = self.pack.vendors_selling(item, ui_map, faction)
            else:
                rows = self.pack.friendly_npcs(ui_map, faction)
        except Exception as e:  # noqa: BLE001
            log_once("reminders.npcs", e)
            rows = []
        if npc or name:
            rows = [r for r in rows if (npc and r.get("id") == npc)
                    or (name and _norm(r.get("name")) == _norm(name))]
        elif not item:
            if role == "class_trainer":
                pat = re.compile(r"^%s$" % re.escape(CLASS_TRAINER % (player_class or "?")), re.I)
            else:
                pat = re.compile(ROLES.get(role, ROLES["vendor"])[0], re.I)
            rows = [r for r in rows if pat.search(r.get("subname") or "")]
        self.cache[key] = rows
        return rows


class Env:
    """The player's surroundings for Reminders.check: nearest NPCs of a role, place names, level, busy."""

    def __init__(self, summary, geo_me, index, data=None, area_fn=None, near_yd=NEAR_YD):
        s = summary or {}
        self.s = s
        self.me = geo_me                # {"map", "x", "y"} or None
        self.index = index
        self.data = data                # knowledge.analyzer kbdata (dist_yd) or None
        self.area_fn = area_fn          # (ui_map, x, y) -> sub-zone name or None
        self.near_yd = near_yd
        self.level = s.get("level")
        self.durability = s.get("durability")
        self.busy = bool(s.get("combat") or s.get("on_taxi") or s.get("dead"))

    def _yards(self, x, y):
        m = self.me
        if self.data is not None:
            try:
                d = self.data.dist_yd((m["map"], m["x"], m["y"]), (m["map"], x, y))
                if d is not None:
                    return d
            except Exception as e:  # noqa: BLE001 - the straight-line guess below
                log_once("reminders.dist", e)
        return math.hypot(x - m["x"], y - m["y"]) * 40.0        # ~40 yd per map percent on a mid-size zone

    def nearest(self, role=None, item=None, npc=None, name=None):
        """The nearest NPC of a role, or that sells an item, or one NPC (npc: its id, name: its name) -> {"name",
        "subname", "yards", "direction"} or None."""
        if not self.me or self.index is None:
            return None
        if npc or name:
            pts = self.index.points(self.me["map"], self.s.get("faction"), npc=npc, name=name)
        else:
            pts = self.index.points(self.me["map"], self.s.get("faction"), role, item, self.s.get("class"))
        if item and not pts and role:          # nobody here is known to sell it: any vendor of that kind
            pts = self.index.points(self.me["map"], self.s.get("faction"), role, None, self.s.get("class"))
        best = None
        for p in pts:
            d = self._yards(p["x"], p["y"])
            if best is None or d < best[0]:
                best = (d, p)
        if best is None:
            return None
        d, p = best
        return {"name": p["name"], "subname": p.get("subname"), "yards": d,
                "direction": _compass(p["x"] - self.me["x"], p["y"] - self.me["y"])}

    def places(self):
        out = [self.s.get("zone"), self.s.get("subzone"), self.s.get("standing_in")]
        if self.me and self.area_fn:
            try:
                out.append(self.area_fn(self.me["map"], self.me["x"], self.me["y"]))
            except Exception as e:  # noqa: BLE001
                log_once("reminders.area", e)
        return [_norm(p) for p in out if p]

    def in_place(self, place):
        want = _norm(place)
        if not want:
            return False
        return any(want == p or (len(want) >= 4 and (want in p or p in want) and len(p) >= 4) for p in self.places())


# ------------------------------------------------------------------------------------------------------
# "remind me ..." without the AI (offline, or a model without tools)
# ------------------------------------------------------------------------------------------------------
_REQ = re.compile(r"^(?:hey\s+|coach(?:\s*,\s*|\s+)|please\s+|can you\s+|could you\s+)*remind me\s+(?:to\s+|about\s+)?(.+)$", re.I)
_WHEN = re.compile(r"^(.*?)\s*,?\s+(when|once|next time|if|as soon as|whenever)\s+(.+)$", re.I)
_END_TIME = re.compile(r"^(.+?)\s+in\s+(\d+)\s*(?:min|mins|minute|minutes)$", re.I)
_END_LEVEL = re.compile(r"^(.+?)\s+(?:at|by|on)\s+(?:level|lvl)\s+(\d{1,2})$", re.I)
_END_AT = re.compile(r"^(.+?)\s+(?:at|near|by)\s+(?:the|a|an|my)\s+(.+)$", re.I)
_LEAD = re.compile(r"^(?:when|once|next time|as soon as|whenever)\s+(?:i(?:'m| am)?\s+)?(get to|reach|am in|arrive "
                   r"(?:at|in)|hit|ding|in|at|near|see|pass)\s+(.+?)\s*,?\s*remind me\s+(?:to\s+|about\s+)?(.+)$",
                   re.I)
_IN_FIRST = re.compile(r"^in\s+(\d+)\s*(?:min|mins|minute|minutes)\s+(?:to\s+|about\s+)?(.+)$", re.I)


def parse_request(text, known_places=None):
    """'remind me to repair when I'm near a repair vendor' -> (what, when) or None. known_places: lower-case
    zone/area names to recognise a place."""
    t = " ".join((text or "").split()).strip(" .!?")
    m = _LEAD.match(t)
    if m:
        verb, cond, what = m.group(1), m.group(2), m.group(3)
        return what.strip(" ."), _cond("%s %s" % (verb, cond), what, known_places)
    m = _REQ.match(t)
    if not m:
        return None
    body = m.group(1)
    f = _IN_FIRST.match(body)
    if f:
        return f.group(2).strip(" ."), {"kind": "time", "minutes": int(f.group(1))}
    w = _WHEN.match(body)
    if w and not re.match(r"^(buy|get)\b", w.group(3), re.I):
        what, cond = w.group(1), w.group(2) + " " + w.group(3)
        return what.strip(" ."), _cond(cond, what, known_places)
    f = _END_TIME.match(body)
    if f:
        return f.group(1).strip(" ."), {"kind": "time", "minutes": int(f.group(2))}
    f = _END_LEVEL.match(body)
    if f:
        return f.group(1).strip(" ."), {"kind": "level", "level": int(f.group(2))}
    f = _END_AT.match(body)
    if f and role_from_words(f.group(2)):
        return f.group(1).strip(" ."), {"kind": "npc", "role": role_from_words(f.group(2))}
    return body.strip(" ."), {"kind": "auto"}


def _cond(cond, what, known_places):
    c = _norm(cond)
    m = re.search(r"\bin (\d+)\s*(?:min|minute|minutes|m)\b", c)
    if m:
        return {"kind": "time", "minutes": int(m.group(1))}
    m = re.search(r"\b(?:level|lvl|hit|ding|reach)\s+(?:level\s+)?(\d{1,2})\b", c)
    if m:
        return {"kind": "level", "level": int(m.group(1))}
    role = role_from_words(c)
    near = re.search(r"\b(near|next to|close to|by|at|see|pass|find|visit|talk to)\b", c)
    if role and (near or "vendor" in c or "trainer" in c or "master" in c or "innkeeper" in c or "banker" in c):
        return {"kind": "npc", "role": role}
    m = re.search(r"\b(?:get to|reach|arrive (?:at|in)|am in|i m in|i'm in|in|at|to)\s+(?:the\s+)?([a-z' ]{3,40})$", c)
    if m:
        place = m.group(1).strip()
        if not known_places or any(place == p or place in p for p in known_places):
            return {"kind": "place", "place": place.title()}
    if role:
        return {"kind": "npc", "role": role}
    return {"kind": "auto"}
