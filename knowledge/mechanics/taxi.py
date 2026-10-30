"""Flight paths as flown: the client's taxi network (data/db2/TaxiNodes.csv, TaxiPath.csv, TaxiPathNode.csv from
tools/extract_maps.py) with the curve each flight follows, so the map can draw a flight where it really goes and the
route planner can chain hops (Stormwind -> Lakeshire goes the way the game routes it).

    net = load(root)                 # cached; None without the csv files
    net.named("LAKESHIRE", continent=0, allowed=ids)   -> node id ('Lakeshire, Redridge')
    net.nearest(continent, wx, wy, allowed=ids)        -> (node id, yards)
    net.route(a, b, allowed=ids)     -> {"nodes": [...], "points": [(X, Y), ...], "yd", "hops"} or None
Node ids are TaxiNodes IDs, the ids the addon's flight path list and the knowledge base use.
"""
import csv
import heapq
import math
import os

_NETS = {}
FLY_SPEED = 32.0        # yd/s, taxi speed used when RestedXP has no time for a hop


class TaxiNet:
    def __init__(self, d):
        self.nodes = {}
        for r in csv.DictReader(open(os.path.join(d, "TaxiNodes.csv"), encoding="utf-8")):
            try:
                self.nodes[int(r["ID"])] = {"id": int(r["ID"]), "name": r["Name_lang"], "cont": int(r["ContinentID"]),
                                           "x": float(r["Pos_0"]), "y": float(r["Pos_1"])}
            except (KeyError, ValueError):
                continue
        pts = {}
        for r in csv.DictReader(open(os.path.join(d, "TaxiPathNode.csv"), encoding="utf-8")):
            try:
                pts.setdefault(int(r["PathID"]), []).append((int(r["NodeIndex"]), float(r["Loc_0"]), float(r["Loc_1"]),
                                                            int(r["ContinentID"])))
            except (KeyError, ValueError):
                continue
        self.paths = {}
        self.out = {}
        for r in csv.DictReader(open(os.path.join(d, "TaxiPath.csv"), encoding="utf-8")):
            try:
                pid, a, b = int(r["ID"]), int(r["FromTaxiNode"]), int(r["ToTaxiNode"])
            except (KeyError, ValueError):
                continue
            p = sorted(pts.get(pid, ()))
            if a not in self.nodes or b not in self.nodes or len(p) < 2:
                continue
            line = [(x, y) for _i, x, y, _c in p]
            yd = sum(math.hypot(line[i][0] - line[i - 1][0], line[i][1] - line[i - 1][1]) for i in range(1, len(line)))
            self.paths[(a, b)] = {"points": line, "yd": yd, "cont": p[0][3]}
            self.out.setdefault(a, []).append(b)

    def named(self, name, continent=None, allowed=None, near=None):
        """The node RestedXP's .fly text means ('LAKESHIRE', 'Ironforge', 'Sentinel Hill'): name or its first part
        matches, on the continent when given, among `allowed` ids first; nearest to `near` (X, Y) on ties."""
        if not name:
            return None
        n = name.strip().lower()
        cands = []
        for nd in self.nodes.values():
            full = (nd["name"] or "").lower()
            first = full.split(",")[0].strip()
            if continent is not None and nd["cont"] != continent:
                continue
            score = 3 if first == n else 2 if full.startswith(n) else 1 if (n in full or first in n) else 0
            if score:
                cands.append((-(score + (1 if allowed and nd["id"] in allowed else 0)),
                              math.hypot(nd["x"] - near[0], nd["y"] - near[1]) if near else 0, nd["id"]))
        return min(cands)[2] if cands else None

    def nearest(self, continent, wx, wy, allowed=None):
        best = None
        for nd in self.nodes.values():
            if nd["cont"] != continent or (allowed is not None and nd["id"] not in allowed):
                continue
            d = math.hypot(nd["x"] - wx, nd["y"] - wy)
            if best is None or d < best[1]:
                best = (nd["id"], d)
        return best or (None, None)

    def route(self, a, b, allowed=None):
        """Cheapest chain of flights a -> b by flown distance, through `allowed` nodes only (the faction's, or the
        ones the character knows). -> {"nodes", "points", "yd", "hops"} or None."""
        if a == b or a not in self.nodes or b not in self.nodes:
            return None
        best = {a: 0.0}
        prev = {}
        pq = [(0.0, a)]
        while pq:
            d, n = heapq.heappop(pq)
            if n == b:
                break
            if d > best.get(n, 1e18):
                continue
            for m in self.out.get(n, ()):
                if allowed is not None and m not in allowed and m != b:
                    continue
                nd = d + self.paths[(n, m)]["yd"] + 30.0          # a small cost per hop: fewer stops on ties
                if nd < best.get(m, 1e18):
                    best[m] = nd
                    prev[m] = n
                    heapq.heappush(pq, (nd, m))
        if b not in best:
            return None
        chain = [b]
        while chain[-1] != a:
            chain.append(prev[chain[-1]])
        chain.reverse()
        pts, yd = [], 0.0
        for i in range(1, len(chain)):
            p = self.paths[(chain[i - 1], chain[i])]
            pts.extend(p["points"] if not pts else p["points"][1:])
            yd += p["yd"]
        return {"nodes": chain, "points": pts, "yd": yd, "hops": len(chain) - 1}

    @staticmethod
    def ahead(points, wx, wy):
        """The part of a flight's line still ahead of a position on it."""
        if not points:
            return []
        k = min(range(len(points)), key=lambda i: (points[i][0] - wx) ** 2 + (points[i][1] - wy) ** 2)
        return [(wx, wy)] + list(points[k + 1:])


def load(root):
    d = os.path.join(root, "data", "db2")
    p = os.path.join(d, "TaxiPathNode.csv")
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return None
    hit = _NETS.get(d)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        net = TaxiNet(d)
    except Exception:  # noqa: BLE001
        net = None
    _NETS[d] = (mt, net)
    return net
