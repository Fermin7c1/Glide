"""Turn map coordinates into directions a player can use: sub-zone, nearby NPC landmark,
distance in yards and direction relative to where the character is facing."""
import math

from log import log_once

COMPASS = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]


def compass(bearing_deg):
    return COMPASS[int(((bearing_deg % 360) + 22.5) // 45) % 8]


def relative_label(rel):
    """rel: degrees clockwise from where the player faces, in (-180, 180]."""
    a = abs(rel)
    side = "right" if rel > 0 else "left"
    if a < 25:
        return "straight ahead"
    if a < 65:
        return "ahead to your " + side
    if a < 115:
        return "to your " + side
    if a < 155:
        return "behind you to the " + side
    return "behind you"


class AreaRaster:
    """The exact sub-zone shapes from the client's explored-area overlays (data/gamedata/areas.npz, written by
    tools/extract_maps.py): one AreaTable-ID per pixel of the 1002 x 668 world map of each zone."""

    def __init__(self, root):
        import json
        import os
        self.maps, self.names, self.size = {}, {}, (1002, 668)
        p = os.path.join(root, "data", "gamedata", "areas.npz")
        if not os.path.exists(p):
            return
        try:
            import numpy as np
            z = np.load(p)
            self.maps = {int(k[1:]): z[k] for k in z.files}
            meta = json.load(open(os.path.join(root, "data", "gamedata", "areas.json"), encoding="utf-8"))
            self.names = {int(k): v for k, v in (meta.get("names") or {}).items() if v}
            self.size = tuple(meta.get("size") or self.size)
        except Exception as e:  # noqa: BLE001 - numpy missing or a broken file: the boxes are used instead
            log_once("geo.areas", e)
            self.maps = {}

    def at(self, ui_map, x, y):
        a = self.maps.get(ui_map)
        if a is None or x is None or y is None:
            return None
        px, py = int(x / 100.0 * self.size[0]), int(y / 100.0 * self.size[1])
        if 0 <= py < a.shape[0] and 0 <= px < a.shape[1]:
            aid = int(a[py, px])
            return self.names.get(aid) if aid else None
        return None


class Geo:
    """knowledge: the glide.db reader (glidedb.GlideDB), read only while there is no knowledge pack; pack_fn: () ->
    the current knowledge pack (api.KnowledgePack) or None, where sub-zones, landmarks and map names come from."""

    def __init__(self, knowledge, pack_fn=None, root=None):
        import os
        self.k = knowledge
        self.pack_fn = pack_fn
        self._src = None                  # the pack the caches below were read from
        self._areas = {}
        self._marks = {}
        self.raster = AreaRaster(root or os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                        "..")))

    # ------------------------------------------------------------------------------------------
    def _pack(self):
        """The current knowledge pack, or None; the caches are emptied when it changes (a background rebuild)."""
        try:
            pack = self.pack_fn() if self.pack_fn else None
        except Exception as e:  # noqa: BLE001
            log_once("geo.pack", e)
            pack = None
        if pack is not self._src:
            self._src, self._areas, self._marks = pack, {}, {}
        return pack

    def map_name(self, ui_map):
        pack = self._pack()
        return pack.map_name(ui_map) if pack is not None else self.k.map_name(ui_map)

    def areas(self, ui_map):
        pack = self._pack()
        if ui_map not in self._areas:
            if pack is not None:
                self._areas[ui_map] = pack.map_areas(ui_map)
            else:                         # no knowledge base: glide.db's copy of the same boxes
                self._areas[ui_map] = [dict(r) for r in self.k.q(
                    "SELECT name, x1, y1, x2, y2 FROM map_areas WHERE ui_map=? AND name IS NOT NULL", ui_map)]
        return self._areas[ui_map]

    def area_at(self, ui_map, x, y):
        """The sub-zone the point is in: the exact shape when the client's map data was extracted, else the smallest
        box containing it, else (nearest box, its compass side)."""
        exact = self.raster.at(ui_map, x, y)
        if exact:
            return exact, None
        best, near = None, None
        for a in self.areas(ui_map):
            if a["x1"] <= x <= a["x2"] and a["y1"] <= y <= a["y2"]:
                size = (a["x2"] - a["x1"]) * (a["y2"] - a["y1"])
                if best is None or size < best[0]:
                    best = (size, a["name"])
            else:
                cx, cy = (a["x1"] + a["x2"]) / 2, (a["y1"] + a["y2"]) / 2
                d = math.hypot(cx - x, cy - y)
                if near is None or d < near[0]:
                    near = (d, a["name"], cx, cy)
        if best:
            return best[1], None
        if near and near[0] < 15:
            # direction of the point as seen from the sub-zone's centre
            return near[1], compass(math.degrees(math.atan2(x - near[2], -(y - near[3]))))
        return None, None

    def landmarks(self, ui_map, faction=None):
        key = (ui_map, faction)
        pack = self._pack()
        if key not in self._marks:
            if pack is not None:
                rows = pack.landmarks(ui_map)
            else:                         # no knowledge base: glide.db's NPCs and spawn points
                rows = self.k.q("""
                    SELECT DISTINCT e.name, e.subname, e.friendly, s.x, s.y FROM spawns s
                    JOIN entities e ON e.kind = s.kind AND e.id = s.entity_id
                    WHERE s.ui_map = ? AND s.kind = 'npc' AND s.x IS NOT NULL AND e.name IS NOT NULL
                      AND ((e.subname IS NOT NULL AND e.friendly IS NOT NULL)
                           OR EXISTS (SELECT 1 FROM quest_entities q WHERE q.kind = 'npc' AND q.entity_id = e.id))""",
                    ui_map)
            marks = []
            for r in rows:
                if faction and r["friendly"] in ("A", "H") and r["friendly"] != faction[0].upper():
                    continue                      # the other faction's town NPCs aren't useful landmarks
                marks.append((r["name"], r["subname"], r["x"], r["y"]))
            self._marks[key] = marks
        return self._marks[key]

    def landmark_near(self, ui_map, x, y, faction=None, max_dist=3.0):
        best = None
        for name, sub, lx, ly in self.landmarks(ui_map, faction):
            d = math.hypot(lx - x, ly - y)
            if d <= max_dist and (best is None or d < best[0]):
                best = (d, name, sub)
        if not best:
            return None
        label = best[1] + (" (%s)" % best[2] if best[2] else "")
        return ("at " if best[0] < 0.8 else "near ") + label

    # ------------------------------------------------------------------------------------------
    def describe(self, ui_map, x, y, player=None, faction=None, with_landmark=True):
        """-> dict(text, area, landmark, yards, direction, relative). `player`: dict with map, x, y,
        facing (radians, WoW convention) and map_size (width, height in yards)."""
        out = {}
        if ui_map is None or x is None or y is None:
            return {"text": None}
        area, side = self.area_at(ui_map, x, y)
        if area:
            out["area"] = area if not side else "%s of %s" % (side, area)
        if with_landmark:
            lm = self.landmark_near(ui_map, x, y, faction)
            if lm:
                out["landmark"] = lm
        if player and player.get("map") == ui_map and player.get("x") is not None:
            dx, dy = x - player["x"], y - player["y"]
            size = player.get("map_size")
            if size:
                ex, ny = dx / 100.0 * size[0], -dy / 100.0 * size[1]
                out["yards"] = int(round(math.hypot(ex, ny) / 10.0) * 10)
            else:
                ex, ny = dx * 1.5, -dy          # zone maps are ~3:2
            bearing = math.degrees(math.atan2(ex, ny))
            out["direction"] = compass(bearing)
            if player.get("facing") is not None:
                heading = (360 - math.degrees(player["facing"])) % 360   # WoW facing is counter-clockwise
                rel = (bearing - heading + 180) % 360 - 180
                out["relative"] = relative_label(rel)
        elif player and player.get("map") and player.get("map") != ui_map:
            name = self.map_name(ui_map)
            if name:
                out["zone"] = name
        out["text"] = self.text(out)
        return out

    @staticmethod
    def text(d):
        parts = []
        if d.get("zone"):
            parts.append(d["zone"])
        place = ", ".join(p for p in (d.get("area"), d.get("landmark")) if p)
        if place:
            parts.append(place)
        if d.get("yards") is not None or d.get("direction"):
            dist = ("%d yd " % d["yards"]) if d.get("yards") is not None else ""
            if d.get("yards") is not None and d["yards"] < 15:
                parts.append("right here")
            else:
                rel = (" (%s)" % d["relative"]) if d.get("relative") else ""
                parts.append("%s%s%s" % (dist, d.get("direction", ""), rel))
        return " · ".join(parts) or None
