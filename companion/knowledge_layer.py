"""Loading the knowledge layer (the package ROOT/knowledge, data/kb.db) from companion/ and opening a KnowledgePack
over it. Every failure is a reason string: the companion runs without the knowledge layer."""
import importlib
import os
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
KB_PATH = os.path.join(ROOT, "data", "kb.db")


def knowledge_package(root=ROOT):
    """(knowledge.api module, None) or (None, reason). The package is ROOT/knowledge; ROOT goes on sys.path (after
    companion/) when it isn't there yet."""
    if "knowledge" not in sys.modules:
        folder = os.path.join(root, "knowledge")
        if not os.path.exists(os.path.join(folder, "__init__.py")):
            return None, "knowledge package missing (%s)" % folder
        if root not in sys.path:
            sys.path.append(root)
    try:
        return importlib.import_module("knowledge.api"), None
    except Exception as e:  # noqa: BLE001 - the companion runs without the knowledge layer
        return None, "knowledge layer failed to load: %s: %s" % (type(e).__name__, e)


def load_knowledge_layer(path=None):
    """(store.KB, knowledge.api module, None) or (None, None, reason) for data/kb.db (or `path`)."""
    path = path or KB_PATH
    if not os.path.exists(path):
        return None, None, "knowledge base missing: run build-kb.cmd"
    api, why = knowledge_package()
    if api is None:
        return None, None, why
    try:
        kb = importlib.import_module("knowledge.store").open_ro(path)
    except Exception as e:  # noqa: BLE001
        return None, None, "cannot open %s: %s" % (path, e)
    if not kb.ok:
        return None, None, "cannot open %s" % path
    return kb, api, None


def open_pack(api, kb, rxp=None, geo=None):
    """A KnowledgePack over an open kb.db (rxp: RestedXP settings; geo: the companion's geo.Geo, for directions)
    -> (pack, None) or (None, why it failed)."""
    try:
        pack = api.KnowledgePack(kb, rxp=rxp, geo=geo)
    except Exception as e:  # noqa: BLE001
        return None, "knowledge pack failed: %s" % e
    if not pack.ok:
        return None, pack.reason
    return pack, None


class KnowledgeHandle:
    """The one holder of the current knowledge layer: the pack (or None), the knowledge.api module and why there is
    no pack. The coach, the app and the rules all read it; a background rebuild's switch replaces the three at once
    (one tuple assignment), so no reader ever sees the new pack with the old module. Calling the handle gives the
    pack (rules.kb_fn)."""

    def __init__(self, pack=None, api=None, reason=None):
        self._now: tuple[Any, Any, Any] = (pack, api, reason)

    @property
    def pack(self):
        return self._now[0]

    @property
    def api(self):
        return self._now[1]

    @property
    def reason(self):
        return self._now[2]

    def set(self, pack, api, reason=None):
        self._now = (pack, api, reason)

    def __call__(self):
        return self._now[0]


def handle_view(part):
    """A property of an object with a `kb_handle` KnowledgeHandle that reads (and sets) one part of it: "pack",
    "api" or "reason". Objects built without __init__ (tests) get an empty handle on first use."""
    i = ("pack", "api", "reason").index(part)

    def handle(obj):
        h = obj.__dict__.get("kb_handle")
        if h is None:
            h = obj.__dict__["kb_handle"] = KnowledgeHandle()
        return h

    def get(obj):
        return handle(obj)._now[i]

    def put(obj, value):
        h = handle(obj)
        now = list(h._now)
        now[i] = value
        h._now = tuple(now)
    return property(get, put)
