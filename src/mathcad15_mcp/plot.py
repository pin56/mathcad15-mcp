"""X-Y plot binary blob (Mathcad 15 `<binaryContent>` item) <-> DSL expression AST.

The blob is a serialized object stream; only the expression tree after the
`04 'tree'` header is generated, everything else comes from a template saved by
Mathcad 15 itself (data/plot_template.bin). Format notes: see the design spec.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from importlib import resources

from .expr import Id, Node, Op, Real

CLS = 0x32
ROOT, PAIR, COMMA, CALL, NEG, PARENS, SQRT = "19c1", "9fc1", "0ac3", "12ce", "954b", "8e70", "9a7b"
LEAF, EMPTY = "020f", "000f"
NAME, NUM, AUTO = 0x24, 0x34, 0x36
PRE = {NEG, PARENS, SQRT, "9d7b", "1340"}           # one 00 byte after the record header
CLOSE = {"06ca": 1}                                  # byte after a subtree (non-root), default 0
BIN_OPS = {"plus": "89c7", "minus": "88c7", "mult": "06ca", "div": "07fb", "pow": "05cc",
           "indexer": "04fd", "matcol": "34fd"}
READ_OPS = {v: k for k, v in BIN_OPS.items()} | {"05fc": "pow"}
GREEK = dict(zip("abgdezhqiklmnxoprstufcywABGDEZHQIKLMNXOPRSTUFCYW",
                 "αβγδεζηθικλμνξοπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"))
_REAL = re.compile(r"\d+(\.\d*)?|\.\d+")
_IMAG = re.compile(r"(\d+(?:\.\d*)?|\.\d+)([ij])")


@dataclass
class PlotSpec:
    xs: list
    ys: list
    xlim: tuple | None = None
    ylim: tuple | None = None


class PlotError(Exception):
    pass


@lru_cache(maxsize=1)
def template() -> bytes:
    return resources.files("mathcad15_mcp").joinpath("data/plot_template.bin").read_bytes()


# ================================================================ build

def _leaf(low, s):
    if len(s) > 200:
        raise PlotError(f"слишком длинное имя или число: {s[:20]}…")
    return ("leaf", low, s)


def _chain(items):
    a = items[0]
    for x in items[1:]:
        a = (COMMA, [a, x])
    return a


def _number(v: str):
    try:
        d = Decimal(v)
    except InvalidOperation:
        raise PlotError(f"не число: {v}") from None
    s = format(abs(d), "f")
    leaf = _leaf(NUM, s)
    return (NEG, [leaf]) if v.startswith("-") else leaf


def _tree(n: Node):
    from .mathml import to_dsl
    k = n.kind
    if k == "id":
        name = n.attrs["name"] + (f".{n.attrs['sub']}" if n.attrs.get("sub") else "")
        return _leaf(NAME, name)
    if k == "real":
        return _number(n.attrs["v"])
    if k == "parens":
        return (PARENS, [_tree(n.args[0])])
    if k == "call" and n.args[0].kind == "id" and len(n.args) > 1:
        return (CALL, [_tree(n.args[0]), (PARENS, [_chain([_tree(a) for a in n.args[1:]])])])
    if k == "op":
        op = n.attrs["op"]
        if op == "neg":
            return (NEG, [_tree(n.args[0])])
        if op == "sqrt":
            return (SQRT, [_tree(n.args[0])])
        if op in BIN_OPS and len(n.args) == 2:
            a, b = n.args
            rhs = _chain([_tree(x) for x in b.args]) if op == "indexer" and b.kind == "seq" else _tree(b)
            return (BIN_OPS[op], [_tree(a), rhs])
    raise PlotError(f"в графике можно только имена, числа, + - * / ^, индексы, столбцы, sqrt и вызовы "
                    f"функций; не поддержано: {to_dsl(n)}")


def _limit(n: Node):
    return _leaf(AUTO, "0") if n.kind == "ph" else _tree(n)


def _axis(exprs, lim):
    lo, hi = (_leaf(AUTO, "0"), _leaf(AUTO, "0")) if lim is None else (_limit(lim[0]), _limit(lim[1]))
    empty = ("empty",)
    return (PAIR, [(PAIR, [(PAIR, [hi, lo]), (PAIR, [empty, empty])]), _chain(exprs) if exprs else empty])


def _esc(v: int) -> bytes:
    if v > 0xff:
        raise PlotError("график слишком сложный (больше 250 узлов)")
    return bytes([0x40, v]) if v >= 0x40 else bytes([v])


def _serialize(root) -> tuple[bytes, int]:
    out = bytearray()
    nid = [6]

    def rec(n, par, side):
        my = nid[0]
        nid[0] += 1
        head = _esc(my) + _esc(CLS)
        if n[0] == "empty":
            out.extend(head + bytes.fromhex(EMPTY) + b"\0\0" + bytes([side]) + _esc(par) + b"\0\0\0")
            return
        if n[0] == "leaf":
            _, low, s = n
            out.extend(head + bytes.fromhex(LEAF) + b"\0\0" + bytes([side | low]) + _esc(par) + b"\0\0")
            out.extend(bytes([len(s) + 1, len(s)]) if low == NAME else bytes([len(s) + 1, 1, len(s)]))
            out.extend(s.encode("utf-16-le") + b"\0")
            return
        typ, kids = n
        out.extend(head + bytes.fromhex(typ) + b"\0\0" + bytes([side]) + _esc(par))
        if typ in PRE:
            out.append(0)
        if len(kids) == 1:
            rec(kids[0], my, 0x80)
        else:
            rec(kids[0], my, 0x40)
            rec(kids[1], my, 0x80)
        if par != 5:
            out.append(CLOSE.get(typ, 0))

    rec(root, 5, 0x80)
    return bytes(out), nid[0]


def build_blob(xs: list[Node], ys: list[Node], xlim=None, ylim=None) -> bytes:
    """Uncompressed blob for `<binaryContent><item>` (caller gzips + base64)."""
    if not xs or not ys:
        raise PlotError("нужны трассы x и y")
    y2 = _axis([], None)
    root = (ROOT, [(PAIR, [_axis([_tree(y) for y in ys], ylim), y2]), _axis([_tree(x) for x in xs], xlim)])
    tree, nxt = _serialize(root)
    tpl = template()
    i, j = tpl.find(b"\x04tree"), tpl.find(b"\x0fd2_graph_format")
    return tpl[:i + 22] + tree + _esc(nxt) + b"\x00\x02\x00\x00\x00" + tpl[j:]


# ================================================================ read

class _Bad(Exception):
    pass


class _Reader:
    def __init__(self, b: bytes, pos: int, cls: int, wide: bool):
        self.b, self.pos, self.cls, self.wide = b, pos, cls, wide
        self.next = None

    def byte(self):
        v = self.b[self.pos]
        self.pos += 1
        return v

    def esc(self):
        v = self.byte()
        return self.byte() if v == 0x40 else v

    def header(self):
        rid, cls = self.esc(), self.esc()
        typ = self.b[self.pos:self.pos + 2].hex()
        self.pos += 4
        rel = self.byte()
        return rid, cls, typ, rel, self.esc()

    def child_next(self, par) -> bool:
        save = self.pos
        try:
            rid, cls, _, _, p = self.header()
            return rid == self.next and cls == self.cls and p == par
        except IndexError:
            return False
        finally:
            self.pos = save

    def node(self, root=False):
        rid, cls, typ, rel, _ = self.header()
        if self.next is None:
            self.next = rid
        if rid != self.next or cls != self.cls:
            raise _Bad("record")
        self.next += 1
        if typ in (LEAF, EMPTY):
            if self.byte() or self.byte():
                raise _Bad("leaf")
            n = self.byte()
            if not n:
                return ("empty",)
            low = rel & 0x3f
            if low != NAME:
                self.byte()
            ln = self.byte()
            if ln + 1 != n:
                raise _Bad("len")
            w = 2 if self.wide else 1
            raw = self.b[self.pos:self.pos + ln * w]
            self.pos += ln * w
            if self.byte():
                raise _Bad("nul")
            s = raw.decode("utf-16-le") if self.wide else re.sub(
                r"\\(.)", lambda m: GREEK.get(m[1], m[1]), raw.decode("latin-1"))
            return ("leaf", low, s)
        if typ in PRE:
            self.byte()
        kids = []
        while self.child_next(rid):
            kids.append(self.node())
        if not root:
            self.byte()
        return (typ, kids)


def _ast(t) -> Node:
    if t[0] == "empty":
        return Node("ph")
    if t[0] == "leaf":
        _, low, s = t
        if low == NAME:
            name, _, sub = s.partition(".")
            if not name:
                raise _Bad("name")
            return Id(name, sub or None)
        if _REAL.fullmatch(s):
            return Real(s)
        m = _IMAG.fullmatch(s)
        if m:
            return Node("imag", [], {"v": m[1], "symbol": m[2]})
        raise _Bad("number")
    typ, kids = t
    if typ == NEG and len(kids) == 1:
        a = _ast(kids[0])
        return Real("-" + a.attrs["v"]) if a.kind == "real" else Op("neg", a)
    if typ == PARENS and len(kids) == 1:
        return Node("parens", [_ast(kids[0])])
    if typ == SQRT and len(kids) == 1:
        return Op("sqrt", _ast(kids[0]))
    if typ == CALL and len(kids) == 2 and kids[0][0] == "leaf" and kids[1][0] == PARENS:
        return Node("call", [_ast(kids[0]), *[_ast(a) for a in _unchain(kids[1][1][0])]])
    if typ in READ_OPS and len(kids) == 2:
        op = READ_OPS[typ]
        if op == "indexer" and kids[1][0] == COMMA:
            return Op(op, _ast(kids[0]), Node("seq", [_ast(a) for a in _unchain(kids[1])]))
        return Op(op, _ast(kids[0]), _ast(kids[1]))
    raise _Bad(f"type {typ}")


def _unchain(t) -> list:
    items = []
    while t[0] == COMMA:
        items.append(t[1][1])
        t = t[1][0]
    return [t, *reversed(items)]


def _pair(t):
    if t[0] != PAIR or len(t[1]) != 2:
        raise _Bad("pair")
    return t[1]


def _read_axis(t):
    head, expr = _pair(t)
    lims, _markers = _pair(head)
    hi, lo = _pair(lims)
    exprs = [] if expr[0] == "empty" else [_ast(e) for e in _unchain(expr)]
    auto = [x[0] == "leaf" and x[1] == AUTO for x in (lo, hi)]
    if all(auto):
        return exprs, None
    return exprs, tuple(Node("ph") if a else _ast(x) for a, x in zip(auto, (lo, hi)))


def read_blob(blob: bytes) -> PlotSpec | None:
    """Plot spec from an uncompressed blob, or None if the plot is not a plain X-Y plot we can express."""
    i = blob.find(b"\x04tree")
    if i < 0 or len(blob) < i + 23:
        return None
    try:
        root = _Reader(blob, i + 22, blob[i + 11], blob[i + 5] >= 0x1b).node(root=True)
        if root[0] != ROOT or len(root[1]) != 2:
            return None
        yaxis, y2 = _pair(root[1][0])
        ys, ylim = _read_axis(yaxis)
        xs, xlim = _read_axis(root[1][1])
        if _read_axis(y2)[0] or not xs or not ys:
            return None
        return PlotSpec(xs, ys, xlim, ylim)
    except (_Bad, IndexError, UnicodeDecodeError):
        return None
