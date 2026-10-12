"""Expression AST and the DSL expression parser (Pratt).

The AST mirrors Mathcad's math30 XML closely so that compiling to XML and
decompiling back are both simple tree walks (see mathml.py).

Node kinds:
  id(name, sub?)  real(v)  imag(v, symbol)  str(v)  ph (placeholder)
  op(op, oattrs) args      -- <ml:apply><ml:OP/>args</ml:apply>
  call  args=[callee, *args]
  fix(fixity) args=[operator, *operands]
  parens [e]  seq [...]  range [a, b] | [a, step, b]
  matrix(rows, cols) args column-major
  lam(op, oattrs) args=[var, body, *extra]  -- integral/derivative/summation/product/limit
  statements (built by dsl.py): define, gdefine, eval, symeval, fn, program, local,
  for, while, if, else, try, return, break, continue
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Node:
    kind: str
    args: list = field(default_factory=list)
    attrs: dict = field(default_factory=dict)

    def __repr__(self):
        a = " ".join(f"{k}={v!r}" for k, v in self.attrs.items() if k != "oattrs")
        inner = ", ".join(map(repr, self.args))
        return f"{self.kind}{'{' + a + '}' if a else ''}({inner})"


class ParseError(Exception):
    def __init__(self, msg: str, pos: int = 0):
        super().__init__(msg)
        self.msg, self.pos = msg, pos


def Id(name, sub=None):
    return Node("id", [], {"name": name, **({"sub": sub} if sub else {})})


def Real(v):
    return Node("real", [], {"v": str(v)})


def Op(op, *args, **oattrs):
    return Node("op", list(args), {"op": op, **({"oattrs": oattrs} if oattrs else {})})


# ---------------------------------------------------------------- tables

SOLVER_ATTRS = ('auto-method="true" method="{m}" derivative-estimation="central" '
                'variable-estimation="tangent" linear-check="false" multistart="false" evolutionary="false"')
# XML element name -> default attribute string written when generating
SOLVERS = {
    "Find": SOLVER_ATTRS.format(m="levenberg"),
    "Minerr": SOLVER_ATTRS.format(m="levenberg"),
    "Maximize": SOLVER_ATTRS.format(m="conjugate"),
    "Minimize": SOLVER_ATTRS.format(m="conjugate"),
    "polyroots": 'method="laguerre"',
    "Polyroots": 'method="companion-matrix"',
    "Odesolve": 'method="adams/bdf (auto)"',
    "Pdesolve": 'method="5-pt-diff"',
    "numol": 'method="5-pt-diff"',
    "genfit": 'method="optimized levenberg-marquardt"',
}
# DSL function name -> XML operator element (unary unless noted)
FUNC_OPS = {
    "sqrt": "sqrt", "abs": "absval", "det": "determinant", "vsum": "vectorSum",
    "vectorize": "vectorize", "transpose": "transpose", "conj": "conjugate",
    "not": "not", "cross": "crossProduct", "nthroot": "nthRoot",
}
OP_FUNCS = {v: k for k, v in FUNC_OPS.items()}
# DSL name -> lambda operator element
LAM_OPS = {"integral": "integral", "deriv": "derivative", "sum": "summation",
           "prod": "product", "limit": "limit"}
LAM_NAMES = {v: k for k, v in LAM_OPS.items()}
FIXITIES = ("infix", "prefix", "postfix", "tree")
RESERVED_CALLS = set(FUNC_OPS) | set(LAM_OPS) | set(SOLVERS) | set(FIXITIES)
KEYWORDS = {"and", "or"}

BINOPS = {  # token -> (xml op, binding power)
    "or": ("or", 2), "and": ("and", 3),
    "=": ("equal", 4), "==": ("equal", 4), "!=": ("notEqual", 4), "<": ("lessThan", 4),
    ">": ("greaterThan", 4), "<=": ("lessOrEqual", 4), ">=": ("greaterOrEqual", 4),
    "+": ("plus", 5), "-": ("minus", 5), "*": ("mult", 7), "/": ("div", 7), "^": ("pow", 8),
}
UNICODE_OPS = {"≤": "<=", "≥": ">=", "≠": "!=", "·": "*", "×": "*", "÷": "/", "←": "<-", "→": "->"}
BP_RANGE, BP_NEG, BP_POSTFIX = 1, 6, 9

# ---------------------------------------------------------------- lexer

ID_EXTRA = "_∞°%$€£¥¤∇ℏ"
NUM_RE = re.compile(r"(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")
SUB_RE = re.compile(r"[\w']+")
SYMBOLS = [":==", ":=", "<-", "->", "..", "^<", "<=", ">=", "!=", "==",
           "=", "<", ">", "+", "-", "*", "/", "^", "!", "(", ")", "[", "]", ",", ";", "|", "?", ":"]


@dataclass
class Tok:
    type: str  # num imag str id op end
    value: str
    pos: int
    sub: str | None = None
    quoted: bool = False


def _idstart(c):
    return c.isalpha() or c in ID_EXTRA


def _idcont(c):
    return c.isalnum() or c in ID_EXTRA or c == "'"


def tokenize(text: str) -> list[Tok]:
    toks, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == "/" and text.startswith("//", i):
            break
        m = NUM_RE.match(text, i)
        if m and (c.isdigit() or (c == "." and i + 1 < n and text[i + 1].isdigit() and not text.startswith("..", i))):
            # "1..5": the number regex would eat "1." -- stop before a range operator
            s = m.group(0)
            if "." in s and text.startswith("..", i + s.index(".")) and m.group(2) is None:
                s = s[: s.index(".")]
            j = i + len(s)
            if j < n and text[j] in "ij" and not (j + 1 < n and _idcont(text[j + 1])):
                toks.append(Tok("imag", s, i, sub=text[j]))
                i = j + 1
            else:
                toks.append(Tok("num", s, i))
                i = j
            continue
        if c == '"':
            j, buf = i + 1, []
            while True:
                if j >= n:
                    raise ParseError("незакрытая строка", i)
                if text[j] == '"':
                    if j + 1 < n and text[j + 1] == '"':
                        buf.append('"')
                        j += 2
                        continue
                    break
                buf.append(text[j])
                j += 1
            toks.append(Tok("str", "".join(buf), i))
            i = j + 1
            continue
        if c == "`" or _idstart(c):
            if c == "`":
                j = text.find("`", i + 1)
                if j < 0:
                    raise ParseError("незакрытый `", i)
                name, j, quoted = text[i + 1:j], j + 1, True
            else:
                j = i + 1
                while j < n and _idcont(text[j]):
                    j += 1
                name, quoted = text[i:j], False
            sub = None
            if j + 1 < n and text[j] == "." and text[j + 1] != ".":
                if text[j + 1] == "`":
                    k = text.find("`", j + 2)
                    if k < 0:
                        raise ParseError("незакрытый `", j)
                    sub, j = text[j + 2:k], k + 1
                else:
                    m2 = SUB_RE.match(text, j + 1)
                    if m2:
                        sub, j = m2.group(0), m2.end()
            typ = "op" if (name in KEYWORDS and not quoted and sub is None) else "id"
            toks.append(Tok(typ, name, i, sub=sub, quoted=quoted))
            i = j
            continue
        if c in UNICODE_OPS:
            toks.append(Tok("op", UNICODE_OPS[c], i))
            i += 1
            continue
        for s in SYMBOLS:
            if text.startswith(s, i):
                toks.append(Tok("op", s, i))
                i += len(s)
                break
        else:
            raise ParseError(f"неожиданный символ {c!r}", i)
    toks.append(Tok("end", "", n))
    return toks


# ---------------------------------------------------------------- parser

class Parser:
    def __init__(self, toks: list[Tok]):
        self.toks, self.i = toks, 0

    @property
    def tok(self) -> Tok:
        return self.toks[self.i]

    def next(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def at(self, value, typ="op") -> bool:
        return self.tok.type == typ and self.tok.value == value

    def expect(self, value):
        if not self.at(value):
            raise ParseError(f"ожидалось {value!r}, найдено {self.tok.value or 'конец строки'!r}", self.tok.pos)
        return self.next()

    # --- Pratt core
    def expr(self, rbp=0) -> Node:
        left = self.nud(self.next(), rbp)
        while True:
            t = self.tok
            lbp = self.lbp(t, left)
            if lbp <= rbp:
                return left
            left = self.led(self.next(), left)

    def lbp(self, t: Tok, left: Node) -> int:
        if t.type == "id":
            return 7 if left.kind in ("real", "imag") and left.attrs.get("lit") else 0
        if t.type != "op":
            return 0
        v = t.value
        if v in BINOPS:
            return BINOPS[v][1]
        if v == "..":
            return BP_RANGE
        if v in ("!", "[", "(", "^<"):
            return BP_POSTFIX
        return 0

    def nud(self, t: Tok, rbp: int) -> Node:
        if t.type == "num":
            return Node("real", [], {"v": t.value, "lit": True})
        if t.type == "imag":
            return Node("imag", [], {"v": t.value, "symbol": t.sub, "lit": True})
        if t.type == "str":
            return Node("str", [], {"v": t.value})
        if t.type == "id":
            n = Id(t.value, t.sub)
            if t.quoted:
                n.attrs["quoted"] = True
            return n
        v = t.value
        if v == "-":
            operand = self.expr(max(rbp, BP_NEG))
            if operand.kind == "real" and operand.attrs.get("lit") and not operand.attrs["v"].startswith("-"):
                return Node("real", [], {"v": "-" + operand.attrs["v"], "lit": True})
            if operand.kind == "imag" and operand.attrs.get("lit") and not operand.attrs["v"].startswith("-"):
                return Node("imag", [], {"v": "-" + operand.attrs["v"], "symbol": operand.attrs["symbol"], "lit": True})
            return Op("neg", operand)
        if v == "+":
            return self.expr(max(rbp, BP_NEG))
        if v == "?":
            return Node("ph")
        if v == "(":
            e = self.expr(0)
            self.expect(")")
            return Node("parens", [e])
        if v == "[":
            return self.matrix(t)
        raise ParseError(f"неожиданный {v or 'конец строки'!r}", t.pos)

    def led(self, t: Tok, left: Node) -> Node:
        if t.type == "id":  # implicit multiplication after a number: 6 m
            self.i -= 1
            return Op("mult", self._unlit(left), self.expr(7))
        v = t.value
        if v in BINOPS:
            op, bp = BINOPS[v]
            right = self.expr(bp - 1 if v == "^" else bp)
            return Op(op, self._unlit(left), right)
        if v == "..":
            return Node("range", [left, self.expr(BP_RANGE)])
        if v == "!":
            return Op("factorial", left)
        if v == "^<":
            col = self.expr(4)
            self.expect(">")
            return Op("matcol", left, col)
        if v == "[":
            idx = self.list_until("]")
            if not idx:
                raise ParseError("пустой индекс", t.pos)
            return Op("indexer", left, idx[0] if len(idx) == 1 else Node("seq", idx))
        if v == "(":
            args = self.list_until(")")
            return make_call(left, args, t.pos)
        raise ParseError(f"неожиданный {v!r}", t.pos)

    @staticmethod
    def _unlit(n: Node) -> Node:
        return n

    def list_until(self, close) -> list[Node]:
        items = []
        if self.at(close):
            self.next()
            return items
        while True:
            items.append(self.expr(0))
            if self.at(","):
                self.next()
                continue
            self.expect(close)
            return items

    def matrix(self, t: Tok) -> Node:
        rows, row = [], []
        while True:
            row.append(self.expr(0))
            if self.at(","):
                self.next()
            elif self.at(";"):
                self.next()
                rows.append(row)
                row = []
            else:
                self.expect("]")
                rows.append(row)
                break
        ncol = len(rows[0])
        if any(len(r) != ncol for r in rows):
            raise ParseError("в строках матрицы разное число элементов", t.pos)
        cells = [rows[r][c] for c in range(ncol) for r in range(len(rows))]
        return Node("matrix", cells, {"rows": len(rows), "cols": ncol})

    def top(self) -> Node:
        """Comma list at top level: `a, s..b` is a stepped range, otherwise a sequence."""
        items = [self.expr(0)]
        while self.at(","):
            self.next()
            items.append(self.expr(0))
        if self.tok.type != "end":
            raise ParseError(f"лишнее {self.tok.value!r}", self.tok.pos)
        return group_items(items)


def group_items(items: list[Node]) -> Node:
    if len(items) == 1:
        return items[0]
    if len(items) == 2 and items[1].kind == "range" and len(items[1].args) == 2:
        return Node("range", [items[0], *items[1].args])
    return Node("seq", items)


def _plain_name(n: Node) -> str | None:
    if n.kind == "id" and not n.attrs.get("quoted") and "sub" not in n.attrs:
        return n.attrs["name"]
    return None


def make_call(callee: Node, args: list[Node], pos: int = 0) -> Node:
    name = _plain_name(callee)

    def need(lo, hi=None):
        hi = lo if hi is None else hi
        if not lo <= len(args) <= hi:
            want = str(lo) if lo == hi else f"{lo}..{hi}"
            raise ParseError(f"{name}: нужно аргументов {want}, дано {len(args)}", pos)

    if name in FUNC_OPS:
        op = FUNC_OPS[name]
        if op == "nthRoot":
            need(2)
            return Op("nthRoot", args[1], args[0])
        if op == "crossProduct":
            need(2)
            return Op(op, *args)
        need(1)
        return Op(op, args[0])
    if name in LAM_OPS:
        op = LAM_OPS[name]
        if op == "limit":
            need(3, 4)
            oattrs = {}
            if len(args) == 4:
                d = args[3]
                if d.kind != "str" or d.attrs["v"] not in ("+", "-"):
                    raise ParseError('limit: направление "+" или "-"', pos)
                oattrs = {"direction": "right" if d.attrs["v"] == "+" else "left"}
            return Node("lam", [args[1], args[0], args[2]], {"op": op, **({"oattrs": oattrs} if oattrs else {})})
        if op == "derivative":
            need(2, 3)
        else:
            need(2, 4)
            if len(args) == 3:
                raise ParseError(f"{name}: пределы задаются парой (нижний, верхний)", pos)
        if args[1].kind != "id":
            raise ParseError(f"{name}: второй аргумент — переменная", pos)
        return Node("lam", [args[1], args[0], *args[2:]], {"op": op})
    if name in SOLVERS:
        return Node("op", list(args), {"op": name, "solver": True})
    if name in FIXITIES:
        if len(args) < 2:
            raise ParseError(f"{name}(оператор, операнды...)", pos)
        return Node("fix", list(args), {"fixity": name})
    return Node("call", [callee, *args])


def parse_expr(text: str) -> Node:
    """Parse one DSL expression (top-level comma list allowed)."""
    return strip_lit(Parser(tokenize(text)).top())


def strip_lit(n: Node) -> Node:
    n.attrs.pop("lit", None)
    for a in n.args:
        if isinstance(a, Node):
            strip_lit(a)
    return n
