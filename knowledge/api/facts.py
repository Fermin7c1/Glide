"""KnowledgePack, grounding: whether a fact id names a usable knowledge row, the glossary and community notes."""
import re
import time

from knowledge import store
from knowledge.api._layer import AP, E, IMPORT_ERROR, MP, RP
from knowledge.api.community import note_out
from knowledge.api.guides import norm_guide_name
from knowledge.api.results import FACT_RE
from knowledge.api.values import as_int


class FactsMixin:
    """Fact verification (the coach's FACTS lines), the glossary and the community panel."""

    # --------------------------------------------------------------------------------------------- grounding
    def glossary_rows(self):
        rows = self._glossary
        if rows is None:
            rows = [{"term": r["term"], "definition": r["definition"], "aliases": store.jload(r.get("aliases"), []) or []}
                    for r in (self.kb.q("SELECT term, definition, aliases FROM glossary") if self.ok else [])
                    if r.get("term")]
            if not rows and not IMPORT_ERROR:
                rows = [{"term": t["term"], "definition": t.get("definition"), "aliases": t.get("aliases") or []}
                        for t in E.load_glossary() if t.get("term")]
            self._glossary = rows
        return rows

    def _guide_keys_named(self, name):
        d = self.data
        if name in d.guide_rows:
            return [name]
        n = norm_guide_name(name)
        return [k for k, r in d.guide_rows.items() if norm_guide_name(r.get("name")) == n
                or norm_guide_name(RP.affix_name(r.get("name") or "")) == n]

    def fact_exists(self, fid):
        """True when a fact id names a usable knowledge row."""
        if not self.ok or not isinstance(fid, str):
            return False
        m = FACT_RE.match(fid.strip())
        if not m:
            return False
        kind, rest = m.group(1).lower(), m.group(2).strip()
        kb = self.kb
        try:
            if kind in ("q", "c", "i"):
                if not re.match(r"^\d+$", rest):
                    return False
                n = int(rest)
                if kind == "q":
                    r = kb.one("SELECT name, hidden, removed FROM quest WHERE id=?", n)
                    return bool(r and r.get("name") and not r.get("hidden") and not r.get("removed"))
                r = kb.one("SELECT name FROM %s WHERE id=?" % ("creature" if kind == "c" else "item"), n)
                return bool(r and r.get("name"))
            if kind == "p":
                if kb.one("SELECT 1 AS x FROM param WHERE key=?", rest):
                    return True
                for reg in (MP, AP):
                    try:
                        reg.info(rest)
                        return True
                    except KeyError:
                        pass
                return False
            if kind == "h":
                return any(r.get("id") == rest for r in self.rules())
            if kind == "n":
                return bool(kb.one("SELECT 1 AS x FROM community_note WHERE id=? AND status != 'contradicted'", rest))
            if kind == "g":
                t = rest.lower()
                return any(t == r["term"].lower() or t in [a.lower() for a in r["aliases"]] for r in self.glossary_rows())
            if kind == "poi":
                m2 = re.match(r"^(\w+):(\d+)$", rest)
                return bool(m2 and kb.one("SELECT 1 AS x FROM poi WHERE kind=? AND id=?", m2.group(1), int(m2.group(2))))
            if kind == "s":
                gk, _, idx = rest.rpartition("/")
                i = as_int(idx) if re.match(r"^\d+$", idx.strip()) else None
                if i is None or not gk:
                    return False
                return any(kb.one("SELECT 1 AS x FROM rxp_step WHERE guide_key=? AND idx=?", k, i)
                           for k in self._guide_keys_named(gk.strip()))
        except Exception:  # noqa: BLE001
            return False
        return False

    def community_recent(self, limit=8):
        """Newest community notes for the dashboard panel (cached 60 s), plus the last scan date."""
        if not self.ok:
            return None
        hit = getattr(self, "_comm_cache", None)
        if hit and time.time() - hit[0] < 60:
            return hit[1]
        try:
            rows = self.kb.q("SELECT * FROM community_note WHERE status NOT IN ('superseded', 'contradicted') "
                             "ORDER BY COALESCE(published, seen) DESC, official DESC LIMIT ?", int(limit))
            total = self.kb.one("SELECT COUNT(*) AS n, MAX(seen) AS last FROM community_note") or {}
            out = {"notes": [note_out(r, full=True) for r in rows], "total": total.get("n") or 0,
                   "last_scan": total.get("last")}
        except Exception:  # noqa: BLE001
            out = None
        self._comm_cache = (time.time(), out)
        return out

    def verify_facts(self, ids):
        known, unknown = [], []
        for f in ids or ():
            (known if self.fact_exists(f) else unknown).append(f)
        return known, unknown
