"""AST <-> Mathcad math30 XML, and AST -> DSL text."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from .expr import (BINOPS, FIXITIES, KEYWORDS, LAM_NAMES, OP_FUNCS, RESERVED_CALLS, SOLVERS,
                   Node, _idcont, _idstart)

ML = "http://schemas.mathsoft.com/math30"
NS = "{" + ML + "}"


class Unsupported(Exception):
    """Construct the DSL cannot express; callers fall back to `raw:`."""


# ================================================================ AST -> XML

def _attrs(d: dict) -> str:
    return "".join(f" {k}={quoteattr(str(v))}" for k, v in d.items())


def _wrap(tag: str, inner: str, attrs: str = "") -> str:
    return f"<ml:{tag}{attrs}>{inner}</ml:{tag}>"


def _args(args: list[Node]) -> str:
    if len(args) == 1:
        return to_xml(args[0], True)
    return _wrap("sequence", "".join(to_xml(a, True) for a in args))


# parens are dropped where Mathcad's layout already delimits the operand
_DROP_IN = {"div": (True, True), "pow": (False, True), "sqrt": (True,), "nthRoot": (True, True),
            "absval": (True,), "determinant": (True,), "vectorSum": (True,), "indexer": (False, True)}


def to_xml(n: Node, drop_parens: bool = False) -> str:
    k, a = n.kind, n.attrs
    if k == "parens":
        if drop_parens:
            return to_xml(n.args[0], True)
        return _wrap("parens", to_xml(n.args[0], True))
    if k == "id":
        sub = f' subscript={quoteattr(a["sub"])}' if a.get("sub") else ""
        return f'<ml:id xml:space="preserve"{sub}>{escape(a["name"])}</ml:id>'
    if k == "real":
        return f"<ml:real>{a['v']}</ml:real>"
    if k == "imag":
        return f'<ml:imag symbol="{a.get("symbol") or "i"}">{a["v"]}</ml:imag>'
    if k == "str":
        return f'<ml:str xml:space="preserve">{escape(a["v"])}</ml:str>'
    if k == "ph":
        return "<ml:placeholder/>"
    if k == "op":
        op = a["op"]
        if a.get("solver") or op in SOLVERS:
            head = f"<ml:{op} {a['xattrs']}/>" if a.get("xattrs") else f"<ml:{op} {SOLVERS[op]}/>"
            return f"<ml:apply>{head}{_args(n.args)}</ml:apply>"
        head = f"<ml:{op}{_attrs(a.get('oattrs', {}))}/>"
        drops = _DROP_IN.get(op, ())
        body = "".join(to_xml(x, i < len(drops) and drops[i]) for i, x in enumerate(n.args))
        return f"<ml:apply>{head}{body}</ml:apply>"
    if k == "call":
        callee, args = n.args[0], n.args[1:]
        return f"<ml:apply>{to_xml(callee)}{_args(args) if args else ''}</ml:apply>"
    if k == "fix":
        return f'<ml:apply fixity="{a["fixity"]}">' + "".join(to_xml(x, True) for x in n.args) + "</ml:apply>"
    if k == "seq":
        return _wrap("sequence", "".join(to_xml(x, True) for x in n.args))
    if k == "range":
        if len(n.args) == 3:
            return _wrap("range", _wrap("sequence", to_xml(n.args[0], True) + to_xml(n.args[1], True))
                         + to_xml(n.args[2], True))
        return _wrap("range", "".join(to_xml(x, True) for x in n.args))
    if k == "matrix":
        return _wrap("matrix", "".join(to_xml(x, True) for x in n.args), f' rows="{a["rows"]}" cols="{a["cols"]}"')
    if k == "lam":
        op = a["op"]
        oattrs = a.get("oattrs") or ({"auto-algorithm": "true"} if op == "integral" else {})
        var, body, extra = n.args[0], n.args[1], n.args[2:]
        s = f"<ml:apply><ml:{op}{_attrs(oattrs)}/>" + _wrap("lambda", _wrap("boundVars", to_xml(var)) + to_xml(body))
        if op == "derivative" and extra:
            s += _wrap("degree", to_xml(extra[0], True))
        elif op == "limit":
            s += to_xml(extra[0], True)
        elif extra:
            s += _wrap("bounds", to_xml(extra[0], True) + to_xml(extra[1], True))
        return s + "</ml:apply>"
    # ---- statements
    if k in ("define", "gdefine", "local"):
        tag = {"define": "define", "gdefine": "globalDefine", "local": "localDefine"}[k]
        return _wrap(tag, to_xml(n.args[0]) + to_xml(n.args[1], True))
    if k == "fn":
        return _wrap("function", to_xml(n.args[0]) + _wrap("boundVars", "".join(to_xml(p) for p in n.args[1:])))
    if k == "eval":
        s = to_xml(n.args[0], True)
        if len(n.args) > 1:
            s += _wrap("unitOverride", to_xml(n.args[1], True))
        return _wrap("eval", s)
    if k == "symeval":
        s = to_xml(n.args[0], True) + "".join(_wrap("command", to_xml(c, True)) for c in n.args[1:])
        return _wrap("symEval", s, ' style="default" hide-keywords="false" hide-lhs="false"')
    if k == "program":
        return _wrap("program", "".join(to_xml(x, True) for x in n.args))
    simple = {"for": "for", "while": "while", "if": "ifThen", "else": "otherwise", "try": "tryCatch",
              "return": "return"}
    if k in simple:
        return _wrap(simple[k], "".join(to_xml(x, True) for x in n.args))
    if k in ("break", "continue"):
        return f"<ml:{k}/>"
    raise ValueError(f"to_xml: unknown node {k}")


# ================================================================ XML -> AST

_ML_TAG = re.compile(r"\{http://schemas\.mathsoft\.com/math\d+\}(.+)")


def _ln(e: ET.Element) -> str | None:
    """Local name of a math20/math30 element, else None."""
    m = _ML_TAG.fullmatch(e.tag) if isinstance(e.tag, str) else None
    return m.group(1) if m else None


def _kids(e: ET.Element) -> list[ET.Element]:
    out = []
    for c in e:
        ln = _ln(c)
        if ln is None or ln in ("symResult", "result"):
            continue
        if ln == "provenance":
            inner = [x for x in c if _ln(x)]
            if inner:
                out.append(inner[-1])
            continue
        out.append(c)
    return out


_STMT = {"define": "define", "globalDefine": "gdefine", "localDefine": "local", "for": "for",
         "while": "while", "ifThen": "if", "otherwise": "else", "tryCatch": "try", "return": "return",
         "program": "program"}
_LEAF_OPS = set(BINOPS[t][0] for t in BINOPS) | {"neg", "factorial", "indexer", "matcol"} | set(OP_FUNCS)


def from_xml(e: ET.Element) -> Node:
    ln = _ln(e)
    if ln == "provenance":
        ks = _kids(e)
        if not ks:
            raise Unsupported("empty provenance")
        return from_xml(ks[-1])
    if ln == "id":
        name = e.text or ""
        a = {"name": name}
        if e.get("subscript"):
            a["sub"] = e.get("subscript")
        return Node("id", [], a)
    if ln == "real":
        v = (e.text or "").strip()
        if e.get("base"):
            try:
                v = str(int(v, int(e.get("base"))))
            except ValueError:
                raise Unsupported("real base")
        if not re.fullmatch(r"-?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", v):
            raise Unsupported(f"real {v!r}")
        return Node("real", [], {"v": v})
    if ln == "imag":
        v = (e.text or "").strip()
        if not re.fullmatch(r"-?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", v):
            raise Unsupported(f"imag {v!r}")
        return Node("imag", [], {"v": v, "symbol": e.get("symbol") or "i"})
    if ln == "str":
        return Node("str", [], {"v": "".join(_str_text(e))})
    if ln == "placeholder":
        return Node("ph")
    if ln == "parens":
        ks = _kids(e)
        if len(ks) != 1:
            raise Unsupported("parens")
        return Node("parens", [from_xml(ks[0])])
    if ln == "sequence":
        return Node("seq", [from_xml(x) for x in _kids(e)])
    if ln == "range":
        ks = _kids(e)
        if len(ks) != 2:
            raise Unsupported("range")
        if _ln(ks[0]) == "sequence":
            s = _kids(ks[0])
            if len(s) != 2:
                raise Unsupported("range step")
            return Node("range", [from_xml(s[0]), from_xml(s[1]), from_xml(ks[1])])
        return Node("range", [from_xml(ks[0]), from_xml(ks[1])])
    if ln == "matrix":
        rows, cols = int(e.get("rows")), int(e.get("cols"))
        cells = [from_xml(x) for x in _kids(e)]
        if len(cells) != rows * cols:
            raise Unsupported("matrix size")
        return Node("matrix", cells, {"rows": rows, "cols": cols})
    if ln == "apply":
        return _apply(e)
    if ln == "function":
        ks = _kids(e)
        if len(ks) != 2 or _ln(ks[1]) != "boundVars":
            raise Unsupported("function")
        return Node("fn", [from_xml(ks[0]), *[from_xml(x) for x in _kids(ks[1])]])
    if ln == "eval":
        ks = _kids(e)
        if not ks or _ln(ks[0]) == "unitOverride":
            raise Unsupported("empty eval")
        args = [from_xml(ks[0])]
        for x in ks[1:]:
            if _ln(x) == "unitOverride":
                u = _kids(x)
                if u:
                    args.append(from_xml(u[0]))
            else:
                raise Unsupported("eval extra")
        return Node("eval", args)
    if ln == "symEval":
        ks = _kids(e)
        if not ks:
            raise Unsupported("empty symEval")
        args = [from_xml(ks[0])]
        for x in ks[1:]:
            if _ln(x) != "command":
                raise Unsupported("symEval child")
            c = _kids(x)
            if len(c) != 1:
                raise Unsupported("command")
            args.append(from_xml(c[0]))
        return Node("symeval", args)
    if ln in ("break", "continue"):
        return Node(ln)
    if ln in _STMT:
        kind = _STMT[ln]
        args = [from_xml(x) for x in _kids(e)]
        want = {"define": 2, "gdefine": 2, "local": 2, "for": 3, "while": 2, "if": 2, "else": 1, "try": 2,
                "return": 1}.get(kind)
        if want is not None and len(args) != want:
            raise Unsupported(f"{ln} arity")
        return Node(kind, args)
    raise Unsupported(f"element {ln}")


def _str_text(e: ET.Element):
    if e.text:
        yield e.text
    for c in e:
        if _ln(c) == "sp":
            yield " "
        if c.tail:
            yield c.tail


def _apply(e: ET.Element) -> Node:
    ks = _kids(e)
    if not ks:
        raise Unsupported("empty apply")
    head, rest = ks[0], ks[1:]
    hn = _ln(head)
    fixity = e.get("fixity")
    if fixity:
        if fixity not in FIXITIES:
            raise Unsupported("fixity")
        return Node("fix", [from_xml(x) for x in ks], {"fixity": fixity})
    is_op = len(head) == 0 and not (head.text or "").strip() and hn not in ("id", "real", "imag", "str", "placeholder")
    if not is_op:
        callee = from_xml(head)
        if len(rest) == 1 and _ln(rest[0]) == "sequence":
            args = [from_xml(x) for x in _kids(rest[0])]
        else:
            args = [from_xml(x) for x in rest]
        return Node("call", [callee, *args])
    if hn in SOLVERS:
        if len(rest) == 1 and _ln(rest[0]) == "sequence":
            args = [from_xml(x) for x in _kids(rest[0])]
        else:
            args = [from_xml(x) for x in rest]
        xattrs = " ".join(f'{k}={quoteattr(v)}' for k, v in head.attrib.items())
        return Node("op", args, {"op": hn, "solver": True, **({"xattrs": xattrs} if xattrs else {})})
    if hn in LAM_NAMES:
        if not rest or _ln(rest[0]) != "lambda":
            raise Unsupported("lambda op")
        lam = _kids(rest[0])
        if len(lam) != 2 or _ln(lam[0]) != "boundVars" or len(_kids(lam[0])) != 1:
            raise Unsupported("lambda")
        var, body = from_xml(_kids(lam[0])[0]), from_xml(lam[1])
        extra = []
        for x in rest[1:]:
            xl = _ln(x)
            if xl in ("bounds", "degree"):
                extra += [from_xml(y) for y in _kids(x)]
            else:
                extra.append(from_xml(x))
        oattrs = dict(head.attrib)
        if hn == "limit" and len(extra) != 1:
            raise Unsupported("limit")
        if hn in ("integral", "summation", "product") and len(extra) not in (0, 2):
            raise Unsupported("bounds")
        if hn == "derivative" and len(extra) > 1:
            raise Unsupported("degree")
        return Node("lam", [var, body, *extra], {"op": hn, **({"oattrs": oattrs} if oattrs else {})})
    if hn in _LEAF_OPS:
        args = [from_xml(x) for x in rest]
        arity = 1 if hn in ("neg", "factorial") or (hn in OP_FUNCS and hn not in ("nthRoot", "crossProduct")) else 2
        if len(args) != arity:
            raise Unsupported(f"{hn} arity {len(args)}")
        oattrs = {k: v for k, v in head.attrib.items() if not (hn == "mult" and k == "style")}
        return Node("op", args, {"op": hn, **({"oattrs": oattrs} if oattrs else {})})
    raise Unsupported(f"operator {hn}")


def parse_math(xml_text: str) -> Node:
    """Parse a standalone math fragment (with or without xmlns declarations)."""
    if "xmlns:ml" not in xml_text[:200]:
        xml_text = re.sub(r"^(\s*<[\w:]+)", rf'\1 xmlns:ml="{ML}"', xml_text, count=1)
    return from_xml(ET.fromstring(xml_text))


# ================================================================ AST -> DSL

SYM = {BINOPS[t][0]: t for t in ("or", "and", "=", "!=", "<", ">", "<=", ">=", "+", "-", "*", "/", "^")}
PREC = {"or": 2, "and": 3, "equal": 4, "notEqual": 4, "lessThan": 4, "greaterThan": 4, "lessOrEqual": 4,
        "greaterOrEqual": 4, "plus": 5, "minus": 5, "mult": 7, "div": 7, "pow": 8}
ID_RE_OK = re.compile(r"[\w']+")


def prec(n: Node) -> int:
    k = n.kind
    if k == "range":
        return 1
    if k == "seq":
        return 0
    if k == "op":
        op = n.attrs["op"]
        if op in PREC:
            return PREC[op]
        if op == "neg":
            return 6
        if op in ("factorial", "indexer", "matcol"):
            return 9
        return 10
    if k in ("real", "imag") and n.attrs["v"].startswith("-"):
        return 6
    if k == "call":
        return 9
    return 10


def _neg_like(n: Node) -> bool:
    return (n.kind == "op" and n.attrs["op"] == "neg") or (n.kind in ("real", "imag") and n.attrs["v"].startswith("-"))


def name_dsl(name: str, sub: str | None = None, call: bool = False) -> str:
    if "`" in name or (sub and "`" in sub):
        raise Unsupported("backtick in name")
    plain = (name and _idstart(name[0]) and all(_idcont(c) for c in name) and name not in KEYWORDS
             and not (call and name in RESERVED_CALLS))
    s = name if plain else "`" + name + "`"
    if sub:
        s += "." + (sub if ID_RE_OK.fullmatch(sub) else "`" + sub + "`")
    return s


def _p(n: Node, need: bool, top=False) -> str:
    s = to_dsl(n, top)
    return f"({s})" if need else s


def to_dsl(n: Node, top: bool = False) -> str:
    """Expression to one-line DSL. `top` allows a bare sequence / stepped range."""
    k, a = n.kind, n.attrs
    if k == "id":
        return name_dsl(a["name"], a.get("sub"))
    if k == "real":
        return a["v"]
    if k == "imag":
        return a["v"] + (a.get("symbol") or "i")
    if k == "str":
        return '"' + a["v"].replace('"', '""') + '"'
    if k == "ph":
        return "?"
    if k == "parens":
        return "(" + to_dsl(n.args[0]) + ")"
    if k == "seq":
        if not top:
            raise Unsupported("nested sequence")
        return ", ".join(to_dsl(x) for x in n.args)
    if k == "range":
        if len(n.args) == 3:
            if not top:
                raise Unsupported("nested stepped range")
            x, s, b = n.args
            return f"{_p(x, prec(x) <= 1)}, {_p(s, prec(s) <= 1)}..{_p(b, prec(b) <= 1)}"
        x, b = n.args
        return f"{_p(x, prec(x) <= 1)}..{_p(b, prec(b) <= 1)}"
    if k == "matrix":
        r, c = a["rows"], a["cols"]
        rows = [", ".join(to_dsl(n.args[j * r + i]) for j in range(c)) for i in range(r)]
        return "[" + "; ".join(rows) + "]"
    if k == "call":
        callee, args = n.args[0], n.args[1:]
        if callee.kind == "id":
            cs = name_dsl(callee.attrs["name"], callee.attrs.get("sub"), call=True)
        else:
            cs = _p(callee, prec(callee) < 9)
        return cs + "(" + ", ".join(to_dsl(x) for x in args) + ")"
    if k == "fix":
        return a["fixity"] + "(" + ", ".join(to_dsl(x) for x in n.args) + ")"
    if k == "lam":
        op = a["op"]
        var, body, extra = n.args[0], n.args[1], n.args[2:]
        parts = [to_dsl(body), to_dsl(var)] + [to_dsl(x) for x in extra]
        if op == "limit":
            d = (a.get("oattrs") or {}).get("direction")
            if d:
                parts.append('"+"' if d == "right" else '"-"')
        return LAM_NAMES[op] + "(" + ", ".join(parts) + ")"
    if k == "op":
        return _op_dsl(n)
    if k in ("program", "define", "gdefine", "local", "for", "while", "if", "else", "try", "return",
             "break", "continue", "eval", "symeval", "fn"):
        raise Unsupported(f"{k} inside expression")
    raise Unsupported(k)


def _right(r: Node, rbp: int) -> str:
    """Right operand parsed by expr(rbp): a leading minus needs no parens there."""
    if r.kind in ("real", "imag") and r.attrs["v"].startswith("-"):
        return to_dsl(r)
    if r.kind == "op" and r.attrs["op"] == "neg":
        x = r.args[0]
        return "-" + _p(x, prec(x) <= max(rbp, 6) or _neg_like(x))
    return _p(r, prec(r) <= rbp)


def _op_dsl(n: Node) -> str:
    op, args = n.attrs["op"], n.args
    if n.attrs.get("solver"):
        return op + "(" + ", ".join(to_dsl(x) for x in args) + ")"
    if op in PREC:
        p = PREC[op]
        l, r = args
        rs = _right(r, p - 1 if op == "pow" else p)
        if op == "pow":
            return f"{_p(l, prec(l) <= 8)}^{rs}"
        ls = _p(l, prec(l) < p or (p > 6 and _neg_like(l)))
        if op == "mult":
            if (l.kind == "real" and not l.attrs["v"].startswith("-") and r.kind == "id") or \
               (l.kind == "real" and not l.attrs["v"].startswith("-") and r.kind == "op"
                    and r.attrs["op"] == "pow" and r.args[0].kind == "id"):
                return f"{ls} {rs}"
            return f"{ls}*{rs}"
        sym = SYM[op]
        if op in ("div", "pow"):
            return f"{ls}{sym}{rs}"
        return f"{ls} {sym} {rs}"
    if op == "neg":
        x = args[0]
        return "-" + _p(x, prec(x) < 6 or _neg_like(x))
    if op == "factorial":
        x = args[0]
        return _p(x, prec(x) < 9) + "!"
    if op == "indexer":
        x, i = args
        idx = ", ".join(to_dsl(y) for y in i.args) if i.kind == "seq" else to_dsl(i)
        return _p(x, prec(x) < 9) + "[" + idx + "]"
    if op == "matcol":
        x, j = args
        return _p(x, prec(x) < 9) + "^<" + _p(j, prec(j) <= 4) + ">"
    if op == "nthRoot":
        return f"nthroot({to_dsl(args[1])}, {to_dsl(args[0])})"
    if op in OP_FUNCS:
        return OP_FUNCS[op] + "(" + ", ".join(to_dsl(x) for x in args) + ")"
    raise Unsupported(f"op {op}")


# ---------------------------------------------------------------- statements -> DSL lines

IND = "    "


def _block(body: Node) -> list[Node]:
    return body.args if body.kind == "program" else [body]


def _stmts(nodes: list[Node], depth: int) -> list[str]:
    out = []
    for s in nodes:
        out += _stmt(s, depth)
    return out


def _stmt(s: Node, depth: int) -> list[str]:
    pad = IND * depth
    k = s.kind
    if k == "local":
        lhs, rhs = s.args
        if rhs.kind == "program":
            return [f"{pad}{_lhs_dsl(lhs)} <-"] + _stmts(rhs.args, depth + 1)
        return [f"{pad}{_lhs_dsl(lhs)} <- {to_dsl(rhs, True)}"]
    if k == "for":
        v, it, body = s.args
        return [f"{pad}for {to_dsl(v)} in {to_dsl(it, True)}:"] + _stmts(_block(body), depth + 1)
    if k == "while":
        return [f"{pad}while {to_dsl(s.args[0])}:"] + _stmts(_block(s.args[1]), depth + 1)
    if k == "if":
        return [f"{pad}if {to_dsl(s.args[0])}:"] + _stmts(_block(s.args[1]), depth + 1)
    if k == "else":
        return [f"{pad}else:"] + _stmts(_block(s.args[0]), depth + 1)
    if k == "try":
        return ([f"{pad}try:"] + _stmts(_block(s.args[0]), depth + 1)
                + [f"{pad}on error:"] + _stmts(_block(s.args[1]), depth + 1))
    if k == "return":
        return [f"{pad}return {to_dsl(s.args[0], True)}"]
    if k in ("break", "continue"):
        return [pad + k]
    if k == "program":
        return [f"{pad}program"] + _stmts(s.args, depth + 1)
    return [pad + to_dsl(s, True)]


def _symeval_tail(n: Node) -> str:
    s = to_dsl(n.args[0], True)
    for c in n.args[1:]:
        s += " | " + (", ".join(to_dsl(x) for x in c.args) if c.kind == "seq" else to_dsl(c))
    return s + " -> ?"


def _lhs_dsl(lhs: Node) -> str:
    if lhs.kind == "fn":
        return name_dsl(lhs.args[0].attrs["name"], lhs.args[0].attrs.get("sub"), call=True) + \
            "(" + ", ".join(to_dsl(p) for p in lhs.args[1:]) + ")"
    return to_dsl(lhs)


def region_dsl(n: Node) -> str:
    """Statement-level node to DSL text (may be multi-line for programs)."""
    k = n.kind
    if k in ("define", "gdefine"):
        op = ":=" if k == "define" else ":=="
        lhs, rhs = n.args
        head = f"{_lhs_dsl(lhs)} {op}"
        if rhs.kind == "program":
            return "\n".join([head] + _stmts(rhs.args, 1))
        if rhs.kind == "symeval":
            if rhs.args[0].kind == "symeval":
                raise Unsupported("chained symEval")
            return f"{head} {_symeval_tail(rhs)}"
        if rhs.kind == "eval":
            unit = (" " + to_dsl(rhs.args[1])) if len(rhs.args) > 1 else ""
            return f"{head} {to_dsl(rhs.args[0], True)} = ?{unit}"
        if rhs.kind in ("if", "else", "for", "while", "try", "local", "return"):
            return "\n".join([head] + _stmt(rhs, 1))
        return f"{head} {to_dsl(rhs, True)}"
    if k == "eval":
        x = n.args[0]
        unit = (" " + to_dsl(n.args[1])) if len(n.args) > 1 else ""
        if x.kind == "program":
            return "\n".join([f"program = ?{unit}"] + _stmts(x.args, 1))
        if x.kind in ("if", "else", "for", "while", "try", "local", "return"):
            return "\n".join([f"program = ?{unit}"] + _stmt(x, 1))
        return f"{to_dsl(x, True)} = ?{unit}"
    if k == "symeval":
        if n.args[0].kind in ("program", "symeval"):
            raise Unsupported("symbolic program or chained symEval")
        return _symeval_tail(n)
    if k == "program":
        return "\n".join(["program"] + _stmts(n.args, 1))
    if k in ("if", "else", "for", "while", "try", "local", "return", "break", "continue"):
        return "\n".join(["program"] + _stmt(n, 1))
    return to_dsl(n, True)
