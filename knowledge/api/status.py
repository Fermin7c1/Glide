"""What the companion's Status page asks the knowledge layer about the files behind it, without the companion
reaching into the build (knowledge.build_kb) or the store itself.

    kb_staleness(root, kb=None, glide=None, ignore=())   build_kb.staleness() of kb.db against its inputs now
    rxp_source(root)                                      where RestedXP's guides are read from, and their version
"""
import os


def kb_staleness(root, kb=None, glide=None, ignore=()):
    """Whether kb.db (`kb`, default data/kb.db) is older than its inputs under `root` (`glide`: the glide.db in use
    when it isn't data/glide.db; `ignore`: files that change while the companion runs, such as its own session log).
    The dict build_kb.staleness() returns: stale, reason, built, changes."""
    from knowledge import build_kb
    return build_kb.staleness(kb, inputs=build_kb.collect_inputs(root, glide=glide, ignore=ignore), root=root)


def rxp_source(root):
    """{"kind", "path", "version"}: where the knowledge build reads RestedXP from under `root` (store.BuildContext:
    a reference copy, the installed addon, or kind "missing"/"none") and that folder's version."""
    from knowledge import store
    kind, path = store.BuildContext(root=root).rxp_source()[:2]
    return {"kind": kind, "path": path,
            "version": store.rxp_version(path) if path and os.path.isdir(path) else None}
