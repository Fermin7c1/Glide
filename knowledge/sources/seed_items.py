"""Loader 2: items, drop sources, vendors and quest rewards from the community seed and recorder vendor visits.

ForeverGuide's item table (seed fg_items) only carries: name, npc (creatures that drop it), obj (objects
that contain it), vendors (NPCs that sell it), startq/quests. Class, quality, item level, required level,
prices and stats are not in any repo source; they stay NULL until a DB2 export (tools/db2_import.py) or
the recorder provides them. Never invented.

  item            fg names; ids seen at recorder vendors (buy_price = lowest observed price)
  creature_drop   fg (quest-item sources, rate unknown), frc drops (forever-ref observed loot counts)
  object_loot     fg / frc, same for game objects
  vendor_item     fg (item vendors), frq (npc 'sells'), obs (recorder MERCHANT_SHOW, with price)
  quest_reward    Wowhead seeds (whz: itemrewards/itemchoices; whd: rew/choice)
  poi vendor      recorder vendor positions
"""
import json

from knowledge import store
from knowledge.values import as_int


def as_list(v):
    if v is None:
        return []
    if isinstance(v, dict):
        return list(v.values())
    return v if isinstance(v, list) else [v]


class Merge:
    """Accumulate rows by key; src is merged, the higher-confidence row wins other fields."""

    def __init__(self):
        self.rows = {}

    def put(self, key, row):
        cur = self.rows.get(key)
        if cur is None:
            self.rows[key] = dict(row)
            return
        new_conf, old_conf = row.get("conf") or 0, cur.get("conf") or 0
        for k, v in row.items():
            if k == "src":
                cur["src"] = store.merge_src(cur.get("src"), v)
            elif k == "conf":
                cur["conf"] = max(new_conf, old_conf)
            elif v is not None and (cur.get(k) is None or new_conf > old_conf):
                cur[k] = v


def load(conn, ctx):
    seed = ctx.load_seed()
    items, drops, oloot, vendors, rewards = Merge(), Merge(), Merge(), Merge(), Merge()
    res = {}
    if seed is None:
        res["seed"] = "skipped: no seed.json.gz"
    else:
        src = seed.get("sources") or {}
        for iid, r in (src.get("fg_items") or {}).items():
            i = as_int(iid)
            if not i or not isinstance(r, dict):
                continue
            items.put(i, {"id": i, "name": store.clean_name(r.get("n")), "src": "fg"})
            for n in as_list(r.get("npc")):
                if as_int(n):
                    drops.put((as_int(n), i), {"creature_id": as_int(n), "item_id": i, "src": "fg", "conf": 0.6})
            for o in as_list(r.get("obj")):
                if as_int(o):
                    oloot.put((as_int(o), i), {"object_id": as_int(o), "item_id": i, "src": "fg", "conf": 0.6})
            for v in as_list(r.get("vendors")):
                if as_int(v):
                    vendors.put((as_int(v), i), {"npc_id": as_int(v), "item_id": i, "src": "fg", "conf": 0.6})
        for nid, r in (src.get("frq_npcs") or {}).items():
            for i in as_list((r or {}).get("sells")):
                if as_int(nid) and as_int(i):
                    vendors.put((as_int(nid), as_int(i)), {"npc_id": as_int(nid), "item_id": as_int(i), "src": "frq", "conf": 0.5})
        res["frc_drops"] = frc_drops((src.get("frc") or {}).get("drops") or {}, drops, oloot)
        res["quest_reward_seed"] = seed_rewards(src, rewards)
    res["obs_vendors"] = obs_vendors(conn, ctx, items, vendors)
    store.insert_rows(conn, "item", list(items.rows.values()))
    store.insert_rows(conn, "creature_drop", list(drops.rows.values()))
    store.insert_rows(conn, "object_loot", list(oloot.rows.values()))
    store.insert_rows(conn, "vendor_item", list(vendors.rows.values()))
    store.insert_rows(conn, "quest_reward", list(rewards.rows.values()))
    res.update({"item": len(items.rows), "creature_drop": len(drops.rows), "object_loot": len(oloot.rows),
                "vendor_item": len(vendors.rows), "quest_reward": len(rewards.rows)})
    return res


def frc_drops(frc, drops, oloot):
    """forever-ref observed loot: item -> [["Creature:<id>", <times the item dropped>, <times looted>, name]].
    When the first count exceeds the second it is a stack quantity, so no rate is derived."""
    n = 0
    for iid, lst in frc.items():
        i = as_int(iid)
        for e in as_list(lst):
            if not isinstance(e, list) or len(e) < 3 or not isinstance(e[0], str) or ":" not in e[0]:
                continue
            kind, _, sid = e[0].partition(":")
            sid, got, looted = as_int(sid), as_int(e[1]), as_int(e[2])
            if not sid or not i:
                continue
            rate = round(got / looted, 3) if looted and got is not None and got <= looted else None
            conf = round(0.8 * (looted or 0) / ((looted or 0) + 5), 3)
            row = {"item_id": i, "rate": rate, "n_obs": looted, "src": "frc", "conf": conf}
            if kind == "Creature":
                drops.put((sid, i), dict(row, creature_id=sid))
            elif kind == "GameObject":
                oloot.put((sid, i), dict(row, object_id=sid))
            n += 1
    return n


def _pairs(v):
    out = []
    for e in as_list(v):
        if isinstance(e, list) and e and as_int(e[0]):
            out.append((as_int(e[0]), as_int(e[1]) if len(e) > 1 else None))
        elif as_int(e):
            out.append((as_int(e), None))
    return out


def seed_rewards(src, rewards):
    n = 0
    for r in src.get("whz_list") or []:
        q = as_int(r.get("id"))
        for key, choice in (("itemrewards", 0), ("itemchoices", 1)):
            for item, qty in _pairs(r.get(key)):
                rewards.put((q, item, choice), {"quest_id": q, "item_id": item, "qty": qty, "choice": choice, "src": "whz"})
                n += 1
    for dung in src.get("whd") or []:
        for r in as_list(dung.get("quests")):
            q = as_int((r or {}).get("id"))
            for key, choice in (("rew", 0), ("choice", 1)):
                for item, qty in _pairs((r or {}).get(key)):
                    rewards.put((q, item, choice), {"quest_id": q, "item_id": item, "qty": qty, "choice": choice, "src": "whd"})
                    n += 1
    return n


def obs_vendors(conn, ctx, items, vendors):
    if not ctx.has_glide():
        return "skipped: no glide.db"
    g = ctx.glide()
    n, best_price, pois = 0, {}, {}
    for r in g.execute("SELECT session, key, json FROM obs_misc WHERE kind='vendors'"):
        try:
            d = json.loads(r["json"])
        except ValueError:
            continue
        npc = as_int(r["key"])
        if not npc:
            continue
        for it in as_list(d.get("items")):
            i, p = as_int((it or {}).get("id")), as_int((it or {}).get("p"))
            if not i:
                continue
            vendors.put((npc, i), {"npc_id": npc, "item_id": i, "price": p, "src": "obs", "conf": 0.95})
            if p is not None and (i not in best_price or p < best_price[i]):
                best_price[i] = p
            n += 1
        pos = d.get("pos") or {}
        if pos.get("x") is not None:
            pois[npc] = {"kind": "vendor", "id": npc, "name": store.clean_name(d.get("n")), "faction": None,
                         "ui_map": as_int(pos.get("m")), "x": pos.get("x"), "y": pos.get("y"), "src": "obs", "conf": 0.95}
    g.close()
    for i, p in best_price.items():
        items.put(i, {"id": i, "buy_price": p, "src": "obs"})
    store.insert_rows(conn, "poi", list(pois.values()), verb="INSERT OR REPLACE")
    return {"observations": n, "priced_items": len(best_price), "vendor_poi": len(pois)}
