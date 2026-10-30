"""Read the data tables out of addon Lua files without running them.

RestedXP's DB files are plain data assignments wrapped in a little code (`if addon.game ~= ... then
return end`, locale branches, helper functions). `assignments(src)` finds every `<lvalue> = { ... }`
statement anywhere in the file and parses the table constructor; code around it is ignored, and a table
that contains code it cannot read (functions, expressions) is reported in `errors` instead of guessed.

Values: strings, numbers, booleans, nil, nested tables. A call with a single string argument
(`L"text"`, `L("text")`, the localisation idiom) yields the string; any other expression yields None.
Tables with exactly the keys 1..n become lists; everything else becomes a dict (keys keep their Lua
type: int or str).

`saved_variables(src)` / `read_saved_variables(path)` read a WoW SavedVariables file instead: every top-level
`Name = value` statement, all of it (an error raises LuaError), nil kept where the game wrote one inside a table
(`nil, -- [2]` holds an array position), and bytes that aren't UTF-8 kept as they are (RXPGuides keeps its imported
guides deflate-compressed in strings). tools/svparse.py is this reader under its old name.
"""
import re

# Strings are written "plain run, then (escape, plain run)*": the same strings as "(escape | plain char)*",
# without trying the escape alternative at every character (much faster on multi-MB SavedVariables files).
_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<lcomment>--\[(?P<e1>=*)\[.*?\](?P=e1)\])
  | (?P<comment>--[^\n]*)
  | (?P<lstr>\[(?P<e2>=*)\[.*?\](?P=e2)\])
  | (?P<str>"[^"\\\n]*(?:\\.[^"\\\n]*)*"|'[^'\\\n]*(?:\\.[^'\\\n]*)*')
  | (?P<num>0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>==|~=|<=|>=|\.\.\.|\.\.|::|[{}\[\]=,;().:#+\-*/%^<>&|~])
""", re.X | re.S)

_ESC = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v", "\\": "\\", '"': '"',
        "'": "'", "\n": "\n"}
_BINOPS = {"+", "-", "*", "/", "%", "^", "..", "==", "~=", "<", ">", "<=", ">=", "and", "or", "&", "|", "~"}
_KEYWORDS = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "goto", "if", "in",
             "local", "nil", "not", "or", "repeat", "return", "then", "true", "until", "while"}


class LuaError(ValueError):
    pass


_ESCAPE = re.compile(r"\\(?:(\d{1,3})|x([0-9a-fA-F\s]{2})|z\s*|(.))", re.S)


def _escape(m):
    dec, hx, ch = m.groups()
    if dec is not None:
        return chr(int(dec) & 0xFF)
    if hx is not None:
        try:
            return chr(int(hx, 16))         # int() takes " F" too, as the reader always did
        except ValueError:                  # two blanks
            return "x" + hx
    if ch is None:                      # \z skips the line break and indent after it
        return ""
    return _ESC.get(ch, ch)


def unescape(body):
    if "\\" not in body:
        return body
    text = _ESCAPE.sub(_escape, body)
    try:  # decimal/hex escapes are bytes: re-decode them as UTF-8 when they form valid sequences
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def tokenize(src):
    toks, pos, n = [], 0, len(src)
    while pos < n:
        m = _TOKEN.match(src, pos)
        if not m:
            raise LuaError("bad character at %d: %r" % (pos, src[pos:pos + 20]))
        kind = m.lastgroup
        if kind == "e1" or kind == "e2":       # inner groups can be lastgroup; recover the outer one
            kind = "lcomment" if m.group("lcomment") else "lstr"
        pos = m.end()
        if kind in ("ws", "comment", "lcomment"):
            continue
        toks.append((kind, m.group(kind), m.start()))
    return toks


class _Parser:
    def __init__(self, toks, keep_nil=False):
        self.t = toks
        self.i = 0
        self.keep_nil = keep_nil            # SavedVariables: a nil in a table is a value (an array position)

    def peek(self, k=0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else (None, None, -1)

    def take(self, value=None):
        if self.i >= len(self.t):
            raise LuaError("unexpected end of input")
        tok = self.t[self.i]
        if value is not None and tok[1] != value:
            raise LuaError("expected %r, got %r at %d" % (value, tok[1], tok[2]))
        self.i += 1
        return tok

    # -- expressions --------------------------------------------------------------------------
    def expr(self):
        v = self.simple()
        while True:
            kind, val, _ = self.peek()
            if (kind == "op" and val in _BINOPS) or (kind == "name" and val in ("and", "or")):
                self.take()
                self.simple()
                v = None
            else:
                return v

    def simple(self):
        kind, val, pos = self.peek()
        if kind is None:
            raise LuaError("unexpected end of input")
        if kind == "op" and val == "{":
            return self.table()
        if kind == "op" and val in ("-", "#") or (kind == "name" and val == "not"):
            self.take()
            v = self.simple()
            if val == "-" and isinstance(v, (int, float)) and not isinstance(v, bool):
                return -v
            return None
        if kind == "str":
            self.take()
            return unescape(val[1:-1])
        if kind == "lstr":
            self.take()
            body = val[val.index("[", 1) + 1:val.rindex("]", 0, len(val) - 1)]
            return body[1:] if body.startswith("\n") else body
        if kind == "num":
            self.take()
            if val.lower().startswith("0x"):
                return int(val, 16)
            f = float(val)
            return int(f) if f.is_integer() and not any(ch in val for ch in ".eE") else f
        if kind == "name":
            if val in ("true", "false", "nil"):
                self.take()
                return {"true": True, "false": False, "nil": None}[val]
            if val == "function":
                raise LuaError("function value at %d" % pos)
            if val in _KEYWORDS:
                raise LuaError("unexpected keyword %r at %d" % (val, pos))
            return self.suffixed()
        if kind == "op" and val == "(":
            self.take()
            v = self.expr()
            self.take(")")
            return v
        if kind == "op" and val == "...":
            self.take()
            return None
        raise LuaError("unexpected %r at %d" % (val, pos))

    def suffixed(self):
        """name ( .name | [expr] | :name args | args )* -> a single-string call's argument, else None."""
        self.take()
        result = None
        while True:
            kind, val, _ = self.peek()
            if kind == "op" and val == ".":
                self.take()
                self.take()
                result = None
            elif kind == "op" and val == "[":
                self.take()
                self.expr()
                self.take("]")
                result = None
            elif kind == "op" and val == ":":
                self.take()
                self.take()
                result = self.args()
            elif kind in ("str", "lstr") or (kind == "op" and val in ("(", "{")):
                result = self.args()
            else:
                return result

    def args(self):
        kind, val, _ = self.peek()
        if kind in ("str", "lstr"):
            return self.simple()
        if val == "{":
            self.table()
            return None
        self.take("(")
        vals = []
        if self.peek()[1] != ")":
            vals.append(self.expr())
            while self.peek()[1] == ",":
                self.take()
                vals.append(self.expr())
        self.take(")")
        return vals[0] if len(vals) == 1 and isinstance(vals[0], str) else None

    def table(self):
        self.take("{")
        d, arr = {}, 1
        while True:
            kind, val, _ = self.peek()
            if kind == "op" and val == "}":
                self.take()
                break
            if kind == "op" and val == "[":
                self.take()
                k = self.expr()
                self.take("]")
                self.take("=")
                v = self.expr()
                if k is not None and (v is not None or self.keep_nil):
                    d[k] = v
            elif kind == "name" and self.peek(1)[1] == "=" and self.peek(1)[0] == "op":
                self.take()
                self.take("=")
                v = self.expr()
                if v is not None or self.keep_nil:
                    d[val] = v
            else:
                v = self.expr()
                if v is not None or self.keep_nil:
                    d[arr] = v
                arr += 1
            if self.peek()[1] in (",", ";"):
                self.take()
        if d and all(isinstance(k, int) and not isinstance(k, bool) for k in d) \
                and sorted(d) == list(range(1, len(d) + 1)):
            return [d[k] for k in range(1, len(d) + 1)]
        return d


def _lvalue(toks, eq):
    """Text of the assignment target that ends right before token index `eq` (or None)."""
    parts, j = [], eq - 1
    while j >= 0:
        kind, val, _ = toks[j]
        if kind == "op" and val == "]" and j >= 2 and toks[j - 2][1] == "[":
            key = toks[j - 1]
            parts.append("[%s]" % (key[1] if key[0] != "str" else '"%s"' % unescape(key[1][1:-1])))
            j -= 3
            continue
        if kind == "name" and val not in _KEYWORDS:
            parts.append(val)
            j -= 1
            if j >= 0 and toks[j][1] == "." and toks[j][0] == "op":
                parts.append(".")
                j -= 1
                continue
            break
        break
    if not parts or parts[-1].startswith("[") or parts[-1] == ".":
        return None
    return "".join(reversed(parts))


def assignments(src):
    """{lvalue: value} for every `lvalue = {table}` in the source (first assignment of a name wins:
    with locale branches the enUS one comes first), plus '__errors__': [(lvalue, message)]."""
    toks = tokenize(src)
    out, errors = {}, []
    i = 0
    while i < len(toks):
        kind, val, _ = toks[i]
        if kind == "op" and val == "=" and i + 1 < len(toks) and toks[i + 1][1] == "{" and toks[i + 1][0] == "op":
            lv = _lvalue(toks, i)
            p = _Parser(toks)
            p.i = i + 1
            if lv:
                try:
                    v = p.table()
                    if lv not in out:
                        out[lv] = v
                    i = p.i
                    continue
                except LuaError as e:
                    errors.append((lv, str(e)))
        i += 1
    out["__errors__"] = errors
    return out


def read_file(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return assignments(fh.read())


def saved_variables(src):
    """{global name: value} for every top-level assignment of a SavedVariables file (module docstring)."""
    p = _Parser(tokenize(src), keep_nil=True)
    out = {}
    while p.i < len(p.t):
        kind, name, pos = p.take()
        if kind != "name":
            raise LuaError("expected a global name at %d, got %r" % (pos, name))
        p.take("=")
        out[name] = p.expr()
    return out


def read_saved_variables(path):
    with open(path, encoding="utf-8", errors="surrogateescape") as fh:
        return saved_variables(fh.read())


# ------------------------------------------------------------------------------------------------
# the recorder's SavedVariables (docs/RECORDER.md)
# ------------------------------------------------------------------------------------------------
RECORDER_SCHEMAS = (1,)        # GlideRecorderDB.schema values this code reads (Core.lua rec.SCHEMA)


def _version(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return None


def _project_addon_version():
    import os
    toc = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "addon", "Glide", "Glide.toc")
    try:
        with open(toc, encoding="utf-8-sig") as fh:
            m = re.search(r"^##\s*Version:\s*(\S+)", fh.read(), re.M)
    except OSError:
        return None
    return m.group(1) if m else None


def recorder_db(path, warn=None):
    """GlideRecorderDB from a SavedVariables file ({} when the file has none); raises LuaError like
    read_saved_variables. warn(text) hears of a layout this code doesn't know: a `schema` it doesn't read, or sessions
    recorded by an addon newer than the project's (their new keys are read as far as this code knows them)."""
    db = read_saved_variables(path).get("GlideRecorderDB") or {}
    if not isinstance(db, dict):
        return {}
    if warn:
        schema = db.get("schema")
        if schema is not None and schema not in RECORDER_SCHEMAS:
            warn("%s: GlideRecorderDB schema %r is not one this code knows (%s): read as schema %d" % (
                path, schema, ", ".join(map(str, RECORDER_SCHEMAS)), RECORDER_SCHEMAS[-1]))
        mine = _version(_project_addon_version())
        newer = sorted({str(s.get("addon")) for s in (db.get("sessions") or {}).values() if isinstance(s, dict)
                        and mine and (_version(s.get("addon")) or ()) > mine})
        if newer:
            warn("%s: sessions recorded by Glide %s, newer than this project's addon (%s)" % (
                path, ", ".join(newer), ".".join(map(str, mine))))
    return db


# The tokens that can hide a brace: strings (escapes included), comments, long brackets, with _TOKEN's rules.
# re.split on it cuts a file into code and these pieces at C speed (the groups: the piece, then the two "="
# runs of the long brackets), so only the braces in the code are looked at one by one. The strings are written
# "plain run, then (escape, plain run)*", as in _TOKEN.
_HIDES = re.compile(r"""(--\[(=*)\[.*?\]\2\]|--[^\n]*|\[(=*)\[.*?\]\3\]
                         |"[^"\\\n]*(?:\\.[^"\\\n]*)*"|'[^'\\\n]*(?:\\.[^'\\\n]*)*')""", re.S | re.X)
_BRACE = re.compile(r"[{}]")


def _assigns(gap, name):
    """Does this text (from a brace to the next "{") end with `name =` or `["name"] =`?"""
    t = tokenize(gap)
    if len(t) >= 2 and t[-1][1] == "=" and t[-2][0] == "name":
        return t[-2][1] == name
    return len(t) >= 4 and t[-1][1] == "=" and t[-2][1] == "]" and t[-3][0] == "str" and t[-4][1] == "[" \
        and unescape(t[-3][1][1:-1]) == name


def saved_variable_field(src, name, key):
    """saved_variables(src).get(name)[key] when that is a table, without parsing the rest of the file.

    A brace scan (strings, escapes and comments respected) finds each top-level `name = {` and, directly
    inside it, the `["key"] = {` (or `key = {`) table; only the text right before those braces and the
    key's table go through the tokenizer and parser. The last assignment wins, as in saved_variables().
    None when there is no such table. Unbalanced braces or an unclosed string raise LuaError; other damage
    outside the span is not looked at."""
    depth, in_target, start, found = 0, False, None, None
    prev, off = 0, 0                                    # prev: where the text before the next field starts
    parts = _HIDES.split(src)
    for i in range(0, len(parts), 4):
        code = parts[i]
        if "{" in code or "}" in code:
            for m in _BRACE.finditer(code):
                pos = off + m.start()
                if m.group() == "{":
                    if depth == 0:
                        in_target = _assigns(src[prev:pos], name)
                        if in_target:
                            found = None                # a later assignment replaces the whole value
                    elif depth == 1 and in_target:
                        start = pos if _assigns(src[prev:pos], key) else None
                    depth += 1
                else:
                    depth -= 1
                    if depth < 0:
                        raise LuaError("unbalanced '}' at %d" % pos)
                    if depth == 1 and start is not None:
                        found, start = (start, pos + 1), None
                if depth <= 1:
                    prev = pos + 1
        if '"' in code or "'" in code:                  # a quote the string pattern couldn't close
            raise LuaError("unclosed string near %d" % (off + code.find('"' if '"' in code else "'")))
        off += len(code) + (len(parts[i + 1]) if i + 1 < len(parts) else 0)
    if depth:
        raise LuaError("unexpected end of input")
    if found is None:
        return None
    return _Parser(tokenize(src[found[0]:found[1]]), keep_nil=True).table()


def read_saved_variable_field(path, name, key):
    with open(path, encoding="utf-8", errors="surrogateescape") as fh:
        return saved_variable_field(fh.read(), name, key)
