#!/usr/bin/env python3
"""Tests that run Node read its output as UTF-8 (regression: the 0.4.16 release build failed in test_rxp_route.py,
where Node's "Walk · ~600 yd" came back as "Walk Â· ~600 yd": Node always writes UTF-8, but on Windows Python decodes
a child's text output with the ANSI code page unless the call names the encoding, and Linux, where both are UTF-8,
never shows it). Every subprocess call in the tests that runs `node` and asks for text names encoding="utf-8"."""
import ast
import glob
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def runs_node(call):
    """The call's first argument is a list starting with a name or string mentioning node ([node, ...], ["node", ...])."""
    if not call.args or not isinstance(call.args[0], ast.List) or not call.args[0].elts:
        return False
    first = call.args[0].elts[0]
    if isinstance(first, ast.Name):
        return first.id.lower() in ("node", "node_exe", "nodejs")
    return isinstance(first, ast.Constant) and isinstance(first.value, str) and os.path.basename(
        first.value).lower() in ("node", "node.exe")


bad = []
for path in sorted(glob.glob(os.path.join(HERE, "**", "*.py"), recursive=True)):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("run", "check_output", "Popen")):
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        text = any(isinstance(kw.get(k), ast.Constant) and kw[k].value is True for k in ("text", "universal_newlines"))
        if runs_node(node) and text and "encoding" not in kw:
            bad.append("%s:%d" % (os.path.relpath(path, HERE), node.lineno))
assert not bad, "Node output read without encoding=\"utf-8\" (garbled on Windows): " + ", ".join(bad)
print("OK")
