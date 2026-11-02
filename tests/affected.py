#!/usr/bin/env python3
"""Which tests a change can affect: for the fast loop while iterating (tests/run_all.py --changed). The full run stays
the check before a commit and on CI.

Each test depends on what it reads, found statically and over-approximated (a test may be picked that didn't need
to run, never the other way round as far as these rules see):
  - the Python it imports, transitively (every import statement anywhere in a file, resolved against the folders
    the tests and the engine put on sys.path; a name found in several folders counts for all of them);
  - the files and folders it names: os.path.join / pathlib chains folded from ROOT, HERE, __file__ and module
    constants (a chain with an unknown part depends on the whole folder before it), string literals that are repo
    paths, and file names unique enough to find;
  - what those files pull in: a named .py file's imports (a script it runs), a .ts/.js/.mjs/.cjs file's relative
    imports, a tsconfig's include/files (tsc -p compiles all of them), a folder's whole contents.
A change to the runner, this file, the requirements, the linters' or TypeScript's settings, or to code no test
reaches picks every test.

    py -3 tests/affected.py                 the tests affected by the changes since origin/main (and uncommitted)
    py -3 tests/affected.py --base HEAD~3   ... since another commit
    py -3 tests/affected.py --why FILE      the tests that depend on FILE
    python3 tests/affected.py --trace [words]   checks the map against what each test really opens (Linux, strace)"""
import ast
import glob
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
PY_PATHS = ("companion", "", "tools", "tests", "tests/kb", "tests/golden")   # where `import x` may find x
# a test whose tool reads the whole repository (ruff check .)
WHOLE_REPO = ("tests/test_lint.py",)
EVERYTHING = ("tests/run_all.py", "tests/affected.py", "requirements.txt", "requirements-dev.txt", "pyproject.toml",
              ".luacheckrc", "desktop/package.json", "desktop/package-lock.json")
CODE = (".py", ".ts", ".js", ".mjs", ".cjs", ".lua", ".toc", ".xml", ".html", ".css", ".json", ".sql")
_SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "release", "out"}
_JS_IMPORT = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(?\s*|\brequire\s*\(\s*)["']([^"']+)["']""")
_RUNS = ("run", "Popen", "check_output", "check_call", "call", "spawnSync", "execFileSync")
_STR = re.compile(r"""["'`]([^"'`\n]{1,200})["'`]""")


def repo_files():
    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True,
                             text=True, encoding="utf-8", check=True).stdout.splitlines()
        return sorted(f for f in out if os.path.isfile(os.path.join(ROOT, f)))
    except (OSError, subprocess.CalledProcessError):
        found = []
        for d, subs, files in os.walk(ROOT):
            subs[:] = [s for s in subs if s not in _SKIP_DIRS]
            found += [os.path.relpath(os.path.join(d, f), ROOT).replace(os.sep, "/") for f in files]
        return sorted(found)


class Repo:
    def __init__(self):
        self.files = repo_files()
        self.fileset = set(self.files)
        self.dirs = {os.path.dirname(f) for f in self.files}
        for d in list(self.dirs):
            while d:
                d = os.path.dirname(d)
                self.dirs.add(d)
        self.by_base = {}
        for f in self.files:
            self.by_base.setdefault(os.path.basename(f), []).append(f)
        self._deps = {}

    def norm(self, p):
        """repo-relative form of a path, or None outside the repository"""
        p = os.path.normpath(p).replace(os.sep, "/")
        if p.startswith("/") or re.match(r"^[A-Za-z]:", p):
            rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
            p = rel
        if p == ".":
            return ""
        return None if p.startswith("..") else p

    # ---- Python -------------------------------------------------------------------------------------------------
    def py_module(self, name, level, here):
        parts = [x for x in name.split(".") if x] if name else []
        if level:
            base = os.path.dirname(here)
            for _ in range(level - 1):
                base = os.path.dirname(base)
            bases = [base]
        else:
            bases = PY_PATHS
        out = []
        for base in bases:
            for k in range(len(parts), 0, -1):
                stem = "/".join(([base] if base else []) + parts[:k])
                hit = [c for c in (stem + ".py", stem + "/__init__.py") if c in self.fileset]
                if hit:
                    out += hit
                    out += [i for j in range(1, k) for i in ["/".join(([base] if base else []) + parts[:j])
                                                                + "/__init__.py"] if i in self.fileset]
                    break
        return out

    def py_imports(self, rel, tree):
        out = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    out.update(self.py_module(a.name, 0, rel))
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                out.update(self.py_module(mod, node.level, rel))
                for a in node.names:
                    out.update(self.py_module(mod + "." + a.name if mod else a.name, node.level, rel))
            elif isinstance(node, ast.Call) and isinstance(node.args[0] if node.args else None, ast.Constant):
                fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if fn in ("import_module", "__import__") and isinstance(node.args[0].value, str):
                    out.update(self.py_module(node.args[0].value, 0, rel))
        return out

    def py_paths(self, rel, tree):
        """files and folders a Python file names (folded path expressions and path-like string literals)"""
        env0 = {"__file__": [rel], "ROOT": [""]}
        found = set()
        env = env0   # the scope ev() reads; one() switches it

        def ev(n):
            """-> list of possible repo paths (str), or None when unknown"""
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                return [n.value]
            if isinstance(n, ast.Name):
                return env.get(n.id)
            if isinstance(n, ast.Attribute) and n.attr in ("ROOT", "HERE"):   # smoke.ROOT, fixture.HERE ...
                return [""] if n.attr == "ROOT" else None
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):         # pathlib: a / "b"
                a, b = ev(n.left), ev(n.right)
                return [x + "/" + y for x in a for y in b] if a is not None and b is not None else partial(a)
            if isinstance(n, ast.Call):
                fn = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
                if fn in ("dirname", "parent") and n.args:
                    a = ev(n.args[0])
                    return [os.path.dirname(x) for x in a] if a is not None else None
                if fn in ("abspath", "normpath", "realpath", "Path", "resolve", "fspath", "str") and n.args:
                    return ev(n.args[0])
                if fn == "join" and n.args and not isinstance(n.func.value if isinstance(n.func, ast.Attribute)
                                                              else None, ast.Constant):
                    acc = [""]
                    for i, a in enumerate(n.args):
                        v = None if isinstance(a, ast.Starred) else ev(a)
                        if v is None:
                            partial(acc if i else None)
                            return None
                        acc = [x + "/" + y if x else y for x in acc for y in v]
                    return acc
            if isinstance(n, ast.Attribute) and n.attr == "parent":
                a = ev(n.value)
                return [os.path.dirname(x) for x in a] if a is not None else None
            return None

        def partial(prefixes):
            """a path with an unknown tail: any file of the folder before it (not the repository root itself)"""
            for p in prefixes or ():
                q = self.norm(p)
                if q and q in self.dirs:
                    found.add(q + "/*")
            return None

        def folder(vals, cut_glob=False):
            for v in vals or ():
                if cut_glob and re.search(r"[*?\[]", v):
                    v = os.path.dirname(re.split(r"[*?\[]", v)[0])
                q = self.norm(v)
                if q and q in self.dirs:
                    found.add(q + "/*")
                elif q == "" and os.path.basename(rel).startswith("test_"):   # a test going through the repo
                    found.add("/*")

        def visit(parent, env):
            for node in ast.iter_child_nodes(parent):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    a = node.args   # a function's own names: its parameters hide the module's constants
                    hidden = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg] if x}
                    visit(node, {k: v for k, v in env.items() if k not in hidden})
                    continue
                one(node, env)
                visit(node, env)

        def one(node, scope):
            nonlocal env
            env = scope
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                v = ev(node.value)
                if v is not None:
                    env[node.targets[0].id] = v
            if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and isinstance(node.iter,
                                                                                              (ast.Tuple, ast.List)):
                vals = [ev(e) for e in node.iter.elts]   # for folder in ("companion", "knowledge"): each of them
                if all(v is not None for v in vals):
                    env[node.target.id] = [x for v in vals for x in v]
            if isinstance(node, (ast.Call, ast.BinOp)):
                for v in ev(node) or ():
                    self._literal(v, found)
                    q = self.norm(v)
                    if q and q in self.dirs and re.search(r"(^|/)(fixtures|snapshots)(/|$)", q):
                        found.add(q + "/*")   # a test's fixture folder: what is in it is the test's input
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                self._literal(node.value, found)
                if "/" in node.value and "*" in node.value and " " not in node.value:   # "wowfiles/**/*.py"
                    folder([node.value], cut_glob=True)
                if "import " in node.value:   # python -c "import kb_refresh; ...": the code it runs
                    for a, b in re.findall(r"\bfrom\s+([\w.]+)\s+import\b|\bimport\s+([\w., ]+)", node.value):
                        for m in ([a] if a else [x.strip() for x in b.split(",")]):
                            found.update(self.py_module(m.split(" ")[0], 0, rel))
            if isinstance(node, ast.Call):
                fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if fn in ("listdir", "scandir", "walk", "glob", "iglob", "copytree") and node.args:   # a whole folder
                    folder(ev(node.args[0]), cut_glob=True)
                if fn in ("iterdir", "glob", "rglob") and isinstance(node.func, ast.Attribute):   # pathlib
                    folder(ev(node.func.value))
                if fn in ("import_module", "__import__") and node.args:   # import_module("pkg." + name)
                    a = node.args[0]
                    pre = (a.left.value if isinstance(a, ast.BinOp) and isinstance(a.left, ast.Constant) else
                           a.values[0].value if isinstance(a, ast.JoinedStr) and a.values
                           and isinstance(a.values[0], ast.Constant) else None)
                    if isinstance(pre, str) and pre.strip("."):   # every module of that package
                        pkg = "/" + pre.strip(".").replace(".", "/") + "/__init__.py"
                        for f in self.py_module(pre.strip("."), 0, rel):
                            if ("/" + f).endswith(pkg):
                                found.add(os.path.dirname(f) + "/")
            # a folder on a command line (pyright companion, luacheck addon/Glide): the tool reads all of it
            if isinstance(node, ast.Call) and (node.func.attr if isinstance(node.func, ast.Attribute) else
                                               getattr(node.func, "id", "")) in _RUNS:
                for arg in [e for a in node.args for e in (a.elts if isinstance(a, (ast.List, ast.Tuple)) else [a])]:
                    for v in ev(arg) or ():
                        q = self.norm(v) if isinstance(v, str) else None
                        if q and q in self.dirs:
                            found.add(q + "/")
        for node in tree.body:   # the module's constants first: functions above them use them too
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                v = ev(node.value)
                if v is not None:
                    env0[node.targets[0].id] = v
        visit(tree, env0)
        return found

    def _literal(self, s, found):
        s = s.strip()
        if not s or len(s) > 200 or "\n" in s:
            return
        for cand in {s, s.replace("\\", "/")}:
            q = self.norm(cand)
            if q and q in self.fileset:
                found.add(q)
        base = os.path.basename(s.replace("\\", "/"))
        if "." in base.strip(".") and base in self.by_base and len(self.by_base[base]) <= 4:
            found.update(self.by_base[base])
        stem, ext = os.path.splitext(base)
        if ext == ".js":   # a compiled file named from its dist/ path: the .ts it is built from
            found.update(f for f in self.by_base.get(stem + ".ts", ()) if f.startswith("desktop/src/"))

    # ---- JavaScript / TypeScript -----------------------------------------------------------------------------------
    def js_deps(self, rel, text):
        out = set()
        for spec in _JS_IMPORT.findall(text):
            if not spec.startswith("."):
                continue
            stem = os.path.normpath(os.path.join(os.path.dirname(rel), spec)).replace(os.sep, "/")
            base = re.sub(r"\.(js|mjs|cjs)$", "", stem)
            for c in (stem, base + ".ts", base + ".d.ts", base + ".js", base + ".mjs", base + ".cjs",
                      stem + "/index.ts", stem + "/index.js"):
                if c in self.fileset:
                    out.add(c)
                    break
        for s in _STR.findall(text):   # paths and file names in a test helper script
            if rel.startswith("tests/"):
                self._literal(s, out)
        return out

    def tsconfig_deps(self, rel):
        try:
            text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
            cfg = json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.M))
        except (OSError, ValueError):
            return {os.path.dirname(rel) + "/"}
        out = set()
        base = os.path.dirname(rel)
        for pat in (cfg.get("files") or []) + (cfg.get("include") or []):
            q = self.norm(os.path.join(base, pat.split("*")[0]))
            if q is None:
                continue
            out.add(q if q in self.fileset else q.rstrip("/") + "/")
        return out

    # ---- per file --------------------------------------------------------------------------------------------------
    def direct(self, rel):
        """what one file pulls in directly: files, and folders as 'dir/'"""
        if rel in self._deps:
            return self._deps[rel]
        self._deps[rel] = set()
        out = set()
        path = os.path.join(ROOT, rel)
        try:
            text = open(path, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            text = ""
        if rel.endswith(".py"):
            try:
                tree = ast.parse(text)
            except SyntaxError:
                tree = None
            if tree is not None:
                out |= self.py_imports(rel, tree)
                out |= self.py_paths(rel, tree)   # what a test names, and the data files engine code reads
        elif rel.endswith((".ts", ".js", ".mjs", ".cjs")):
            out |= self.js_deps(rel, text)
        elif re.search(r"(^|/)tsconfig[^/]*\.json$", rel):
            out |= self.tsconfig_deps(rel)
        out.discard(rel)
        self._deps[rel] = out
        return out

    def closure(self, rel):
        seen, todo = set(), [rel]
        while todo:
            f = todo.pop()
            if f in seen:
                continue
            seen.add(f)
            if f.endswith("/*"):   # a folder read as data (listed, globbed, hashed, copied): its files only
                continue
            if f.endswith("/"):   # a whole folder: the code in it and what that code pulls in (not other tests)
                todo += [x for x in self.files if x.startswith(f) and x.endswith((".py", ".ts", ".js", ".mjs", ".cjs"))
                         and not os.path.basename(x).startswith("test_")]
                continue
            todo += self.direct(f)
        return seen


def tests_list():
    found = glob.glob(os.path.join(HERE, "**", "test_*.py"), recursive=True)
    return sorted(os.path.relpath(p, ROOT).replace(os.sep, "/") for p in found if "__pycache__" not in p)


CACHE = os.path.join(HERE, ".affected-cache.json")   # each file's direct dependencies, by its size and mtime (gitignored)


def dependency_map(repo=None):
    repo = repo or Repo()
    key = hashlib.sha1("\n".join(repo.files).encode()).hexdigest()   # names resolve against the files there are
    try:
        with open(CACHE, encoding="utf-8") as f:
            old = json.load(f)
        old = old["files"] if old.get("key") == key else {}
    except (OSError, ValueError, AttributeError, KeyError):
        old = {}
    stamps = {}
    for f in repo.files:
        try:
            st = os.stat(os.path.join(ROOT, f))
            stamps[f] = [st.st_size, st.st_mtime_ns]
        except OSError:
            continue
        if f in old and old[f][0] == stamps[f]:
            repo._deps[f] = set(old[f][1])
    deps = {t: ({"/*"} if t in WHOLE_REPO else repo.closure(t)) for t in tests_list()}
    try:
        with open(CACHE + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"key": key, "files": {k: [stamps[k], sorted(v)] for k, v in repo._deps.items() if k in stamps}},
                      f)
        os.replace(CACHE + ".tmp", CACHE)
    except OSError:
        pass
    return deps, repo


def covers(deps, f):
    """f is one of deps, or under a folder of them ("dir/": its code and what that imports, "dir/*": its files)"""
    if f in deps:
        return True
    for d in deps:
        if d.endswith("/*"):
            d = d[:-1]
        if d.endswith("/") and (d == "/" or f.startswith(d)):
            return True
    return False


def selects_all(f):
    """a change to f runs every test (the runner, the requirements, the linters' and TypeScript's settings)"""
    return f in EVERYTHING or bool(re.search(r"(^|/)tsconfig[^/]*\.json$", f))


def affected(changed, deps=None, repo=None):
    """-> (tests to run, why: {test: [changed files]}, changed files no test reaches); every test when a change
    can't be scoped"""
    if deps is None:
        deps, repo = dependency_map(repo)
    every = sorted(deps)
    why, unreached = {}, []
    for f in changed:
        if selects_all(f):
            return every, {t: [f] for t in every}, []
        hit = [t for t, d in deps.items() if covers(d, f)]
        for t in hit:
            why.setdefault(t, []).append(f)
        if not hit:
            unreached.append(f)
    code = [f for f in unreached if f.endswith(CODE) and not f.startswith(("docs/", ".github/", "data/"))
            and not f.endswith(".md")]
    if code:   # code no test reaches: the rules can't tell, so everything
        return every, {t: code for t in every}, unreached
    return sorted(why), why, unreached


def changed_files(base="origin/main"):
    """files changed since the merge base with `base`, staged, unstaged and untracked"""
    def git(*a):
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                              check=True).stdout
    mb = git("merge-base", "HEAD", base).strip()
    files = set(git("diff", "--name-only", mb).splitlines()) | set(git("ls-files", "-o", "--exclude-standard")
                                                                    .splitlines())
    return sorted(f for f in files if f)


# a change to one of these re-checks the whole engine (pyright's own settings, its version, or the test itself)
PYRIGHT_ALL = ("tests/test_types.py", "pyproject.toml", "pyrightconfig.json", "requirements.txt",
               "requirements-dev.txt")


def pyright_files(changed, repo=None):
    """For the fast loop: the engine files (companion/*.py) whose type check a change can alter, i.e. the changed ones
    and every engine file that imports one of them, directly or not. pyright's report for a file depends only on that
    file and what it imports, and the rest passed on origin/main. None: check the whole engine (a change to its
    settings or to a file that is gone). [] when no engine Python file can be affected."""
    repo = repo or Repo()
    py = [f for f in changed if f.endswith((".py", ".pyi"))]
    if any(f in PYRIGHT_ALL for f in changed) or any(f not in repo.fileset for f in py):
        return None
    if not py:
        return []
    want = set(py)
    out = []
    for f in repo.files:
        if not (f.startswith("companion/") and f.endswith(".py")):
            continue
        seen, todo = set(), [f]
        while todo:   # the Python it pulls in (over-approximated: also the .py files it names)
            x = todo.pop()
            if x in seen:
                continue
            seen.add(x)
            if x.endswith("/"):
                todo += [y for y in repo.files if y.startswith(x) and y.endswith(".py")]
            elif x.endswith(".py"):
                todo += [d for d in repo.direct(x) if d.endswith((".py", "/")) and not d.endswith("/*")]
        if seen & want:
            out.append(f)
    return out


def trace(words=(), jobs=4):
    """The static map checked against what the tests really read: each test runs under strace -f (Linux; its
    children too: node, tsc, pyright, lua), and every repository file it opens must be one of its dependencies here.
    Prints the misses, exit 1 when there is one. Rerun it after a big change to the tests or to the rules above."""
    import shutil
    import tempfile
    from concurrent.futures import ThreadPoolExecutor
    if not shutil.which("strace"):
        print("strace is needed (Linux): apt install strace")
        return 2
    deps, repo = dependency_map()
    tests = [t for t in sorted(deps) if not words or any(w in t for w in words)]
    # a call that failed (= -1 ENOENT ...) read nothing; one strace split in two (<unfinished ...>) counts as read
    opened = re.compile(r'\bopen(?:at2?)?\((?:[^,"]*, )?"([^"]+)"(?!.*= -1 )')
    env = dict(os.environ, ANTHROPIC_API_KEY="", GEMINI_API_KEY="", GOOGLE_API_KEY="", OPENROUTER_API_KEY="",
               OPENAI_API_KEY="")

    def one(t):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "trace")
            r = subprocess.run(["strace", "-f", "-qq", "-s", "4096", "-e", "trace=open,openat,openat2", "-o", log, sys.executable,
                                os.path.join(ROOT, t)], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            with open(log, encoding="utf-8", errors="replace") as f:
                text = f.read()
        files = set()
        for path in opened.findall(text):
            path = path.encode("latin-1", "backslashreplace").decode("unicode_escape", "replace")
            q = repo.norm(path if os.path.isabs(path) else os.path.join(ROOT, path))
            m = re.match(r"^(.*?)__pycache__/([^/.]+)\.[^/]*\.pyc$", q or "")
            if m:   # an import reads the module's compiled copy: the module
                q = m.group(1) + m.group(2) + ".py"
            if q in repo.fileset and not q.startswith(("tests/.", ".git/")):
                files.add(q)
        return t, r.returncode, files

    total = missed = 0
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for t, code, files in pool.map(one, tests):
            miss = sorted(f for f in files if f != t and not covers(deps[t], f) and not selects_all(f))
            total += len(files)
            missed += len(miss)
            print("%-40s exit %-3d %4d files read, %d not in the map%s" % (
                t, code, len(files), len(miss), "".join("\n    " + m for m in miss)), flush=True)
    print("\n%d (test, repository file) reads, %d not in the static map (%.2f%% covered)" % (
        total, missed, 100.0 * (total - missed) / max(total, 1)))
    return 1 if missed else 0


def main(argv):
    base = "origin/main"
    if "--trace" in argv:
        return trace([a for a in argv if not a.startswith("--")])
    if "--why" in argv:
        f = argv[argv.index("--why") + 1].replace("\\", "/")
        deps, _ = dependency_map()
        print("\n".join(t for t, d in sorted(deps.items()) if covers(d, f)) or "(no test reaches %s)" % f)
        return 0
    if "--base" in argv:
        base = argv[argv.index("--base") + 1]
    changed = changed_files(base)
    tests, why, unreached = affected(changed)
    for t in tests:
        print("%-40s %s" % (t, ", ".join(why.get(t, [])[:3])))
    if unreached:
        print("no test reaches: " + ", ".join(unreached))
    print("%d of %d tests for %d changed files" % (len(tests), len(tests_list()), len(changed)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
