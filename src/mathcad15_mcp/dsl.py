"""Worksheet DSL: text -> list of regions (and region XML -> DSL for text/plot).

One statement is one region. Indented lines form blocks (programs, Given
constraints, continuation of text/raw). See help.py for the user-facing syntax.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from .expr import Id, Node, ParseError, Parser, Real, Tok, strip_lit, tokenize
from .mathml import ML, Unsupported, region_dsl, to_dsl, to_xml

WS = "http://schemas.mathsoft.com/worksheet30"
U = "http://schemas.mathsoft.com/units10"

MAX_TRACES = 16
P_ATTRS = ('margin-left="inherit" margin-right="inherit" text-indent="inherit" text-align="inherit" '
           'list-style-type="inherit" tabs="inherit"')
HEADINGS = {1: "Heading 1", 2: "Heading 2", 3: "Heading 3"}


class DslError(Exception):
    def __init__(self, line: int, text: str, msg: str, col: int | None = None):
        self.line, self.text, self.msg, self.col = line, text, msg, col
        s = f"строка {line}: {msg}\n  {text}"
        if col is not None:
            s += "\n  " + " " * col + "^"
        super().__init__(s)


@dataclass
class PlotSpec:
    xs: list
    ys: list
    xlim: tuple | None = None
    ylim: tuple | None = None


@dataclass
class Region:
    kind: str  # math | text | plot | raw
    xml: str   # math: inner of <math>; text: <text> element; raw: whole <region>; plot: ""
    dsl: str
    line: int
    plot: PlotSpec | None = None


@dataclass
class Line:
    no: int
    indent: int
    text: str
    body: list = field(default_factory=list)


# ================================================================ lines

def _balance(s: str) -> int:
    depth, q = 0, None
    for i, c in enumerate(s):
        if q:
            if c == q:
                q = None
            continue
        if c in "\"`":
            q = c
        elif c == "/" and s.startswith("//", i):
            break
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
    return depth


_PLAIN = re.compile(r"(#{1,3}\s|text:|raw:|#{1,3}$)")


def _lines(text: str) -> list[Line]:
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, i = [], 0
    while i < len(raw):
        s = raw[i].expandtabs(4)
        no = i + 1
        i += 1
        body = s.strip()
        if not body or body.startswith("//"):
            continue
        indent = len(s) - len(s.lstrip())
        if not _PLAIN.match(body):
            while _balance(body) > 0 and i < len(raw):
                body += " " + raw[i].strip()
                i += 1
        out.append(Line(no, indent, body))
    return out


def _group(lines: list[Line]) -> list[Line]:
    """Attach deeper-indented lines to the header above them (recursively)."""
    out, i = [], 0
    while i < len(lines):
        h = lines[i]
        j = i + 1
        while j < len(lines) and lines[j].indent > h.indent:
            j += 1
        out.append(Line(h.no, h.indent, h.text, _group(lines[i + 1:j])))
        i = j
    return out


def _flat(lines: list[Line]) -> list[Line]:
    out = []
    for ln in lines:
        out.append(ln)
        out += _flat(ln.body)
    return out


# ================================================================ token helpers

def _toks(ln: Line, text: str | None = None) -> list[Tok]:
    try:
        return tokenize(ln.text if text is None else text)[:-1]
    except ParseError as e:
        raise DslError(ln.no, ln.text, e.msg, e.pos)


def _top_idx(toks: list[Tok], *values: str) -> list[int]:
    depth, out = 0, []
    for k, t in enumerate(toks):
        if t.type == "op":
            if t.value in "([":
                depth += 1
            elif t.value in ")]":
                depth -= 1
            elif depth == 0 and t.value in values:
                out.append(k)
    return out


def _expr(ln: Line, toks: list[Tok], what="выражение", top=True) -> Node:
    if not toks:
        raise DslError(ln.no, ln.text, f"ожидалось {what}", len(ln.text))
    end = toks[-1].pos + max(len(toks[-1].value), 1)
    p = Parser(toks + [Tok("end", "", end)])
    try:
        n = p.top() if top else p.expr(0)
        if not top and p.tok.type != "end":
            raise ParseError(f"лишнее {p.tok.value!r}", p.tok.pos)
    except ParseError as e:
        raise DslError(ln.no, ln.text, e.msg, e.pos)
    return strip_lit(n)


def _word(t: Tok | None, *words) -> bool:
    return t is not None and t.type == "id" and not t.quoted and t.sub is None and t.value in words


def _lhs(ln: Line, toks: list[Tok]) -> Node:
    n = _expr(ln, toks, "имя слева", top=False)
    if n.kind == "call" and n.args[0].kind == "id" and len(n.args) > 1:
        return Node("fn", list(n.args))
    return n


# ================================================================ statements

def _rhs(ln: Line, toks: list[Tok], body: list[Line] | None = None) -> Node:
    """Expression, `E = ? [unit]` (eval) or `E | cmd -> ?` (symbolic)."""
    arrows = _top_idx(toks, "->")
    if arrows:
        k = arrows[0]
        tail = toks[k + 1:]
        if tail and not (len(tail) == 1 and tail[0].value == "?"):
            raise DslError(ln.no, ln.text, "после -> только ?", tail[0].pos)
        parts, start = [], 0
        for b in _top_idx(toks[:k], "|") + [k]:
            parts.append(toks[start:b])
            start = b + 1
        args = [_expr(ln, parts[0])] + [_expr(ln, p, "команда") for p in parts[1:]]
        return Node("symeval", args)
    evals = [k for k in _top_idx(toks, "=") if k + 1 < len(toks) and toks[k + 1].type == "op" and toks[k + 1].value == "?"]
    if evals:
        k = evals[-1]
        head, unit = toks[:k], toks[k + 2:]
        if len(head) == 1 and _word(head[0], "program") and body:
            x = _program(body)
        else:
            x = _expr(ln, head)
        return Node("eval", [x] + ([_expr(ln, unit, "единица")] if unit else []))
    return _expr(ln, toks)


def _math(ln: Line) -> Node:
    toks = _toks(ln)
    body = ln.body
    defs = _top_idx(toks, ":=", ":==")
    if defs:
        k = defs[0]
        kind = "define" if toks[k].value == ":=" else "gdefine"
        lhs = _lhs(ln, toks[:k])
        rt = toks[k + 1:]
        if not rt:
            if not body:
                raise DslError(ln.no, ln.text, "пустая правая часть (для программы — тело с отступом ниже)",
                               len(ln.text))
            return Node(kind, [lhs, _program(body)])
        _no_body(ln)
        return Node(kind, [lhs, _rhs(ln, rt)])
    if len(toks) == 1 and _word(toks[0], "program"):
        if not body:
            raise DslError(ln.no, ln.text, "program без тела")
        return _program(body)
    n = _rhs(ln, toks, body)
    if not (n.kind == "eval" and body):
        _no_body(ln)
    return n


def _no_body(ln: Line):
    if ln.body:
        b = ln.body[0]
        raise DslError(b.no, b.text, "лишний отступ: блок допустим после `:=`, Given, program, for/while/if/else/try")


def _program(lines: list[Line]) -> Node:
    stmts, i = [], 0
    while i < len(lines):
        ln = lines[i]
        toks = _toks(ln)
        if _is_block_kw(toks) and _word(toks[0], "try"):
            body = _colon_body(ln, toks, 1)
            if i + 1 >= len(lines) or not _word((_toks(lines[i + 1]) or [None])[0], "on"):
                raise DslError(ln.no, ln.text, "после try: нужен блок `on error:`")
            on = lines[i + 1]
            ot = _toks(on)
            if not (len(ot) >= 2 and _word(ot[1], "error")):
                raise DslError(on.no, on.text, "ожидалось `on error:`")
            stmts.append(Node("try", [body, _colon_body(on, ot, 2)]))
            i += 2
            continue
        stmts.append(_pstmt(ln, toks))
        i += 1
    return _wrap(stmts)


def _wrap(stmts: list[Node]) -> Node:
    return stmts[0] if len(stmts) == 1 else Node("program", stmts)


def _colon_body(ln: Line, toks: list[Tok], k: int) -> Node:
    """Statement body after a `:` at toks[k]: inline remainder or indented block."""
    if k >= len(toks) or toks[k].value != ":" or toks[k].type != "op":
        raise DslError(ln.no, ln.text, "ожидалось ':'", toks[k].pos if k < len(toks) else len(ln.text))
    rest = ln.text[toks[k].pos + 1:].strip()
    if rest and ln.body:
        raise DslError(ln.no, ln.text, "либо тело в той же строке, либо блоком с отступом")
    if rest:
        sub = Line(ln.no, ln.indent + 1, rest)
        return _pstmt(sub, _toks(sub))
    if not ln.body:
        raise DslError(ln.no, ln.text, "пустое тело блока", len(ln.text))
    return _program(ln.body)


def _is_block_kw(toks: list[Tok]) -> bool:
    """`for/while/if/else/try ... :` statements; `if(c, a, b)` stays a function call."""
    t0 = toks[0] if toks else None
    if _word(t0, "return", "break", "continue", "on"):
        return len(toks) > 1 or t0.value != "return"
    return _word(t0, "for", "while", "if", "else", "try") and bool(_top_idx(toks, ":"))


def _pstmt(ln: Line, toks: list[Tok]) -> Node:
    t0 = toks[0] if toks else None
    if not _is_block_kw(toks):
        t0 = None
    if _word(t0, "for"):
        if len(toks) < 4 or toks[1].type != "id" or not _word(toks[2], "in"):
            raise DslError(ln.no, ln.text, "ожидалось `for i in a..b:`", t0.pos)
        colon = _top_idx(toks, ":")
        if not colon:
            raise DslError(ln.no, ln.text, "ожидалось ':'", len(ln.text))
        rng = _expr(ln, toks[3:colon[0]], "диапазон")
        var = Id(toks[1].value, toks[1].sub)
        return Node("for", [var, rng, _colon_body(ln, toks, colon[0])])
    if _word(t0, "while", "if"):
        colon = _top_idx(toks, ":")
        if not colon:
            raise DslError(ln.no, ln.text, "ожидалось ':'", len(ln.text))
        cond = _expr(ln, toks[1:colon[0]], "условие")
        return Node("while" if t0.value == "while" else "if", [cond, _colon_body(ln, toks, colon[0])])
    if _word(t0, "else"):
        return Node("else", [_colon_body(ln, toks, 1)])
    if _word(t0, "return"):
        _no_body(ln)
        return Node("return", [_expr(ln, toks[1:])])
    if _word(t0, "break", "continue") and len(toks) == 1:
        _no_body(ln)
        return Node(t0.value)
    if _word(t0, "on"):
        raise DslError(ln.no, ln.text, "`on error:` без try:")
    arrows = _top_idx(toks, "<-")
    if arrows:
        k = arrows[0]
        lhs = _lhs(ln, toks[:k])
        rt = toks[k + 1:]
        if not rt:
            if not ln.body:
                raise DslError(ln.no, ln.text, "пустая правая часть <-", len(ln.text))
            return Node("local", [lhs, _program(ln.body)])
        _no_body(ln)
        return Node("local", [lhs, _expr(ln, rt)])
    _no_body(ln)
    return _expr(ln, toks)


# ================================================================ plot

def _plot(ln: Line) -> list[Region]:
    rest_at = ln.text.index(":") + 1
    toks = _toks(ln, " " * rest_at + ln.text[rest_at:])
    keys = [k for k in range(len(toks) - 1)
            if toks[k].type == "id" and not toks[k].quoted and toks[k].sub is None and toks[k + 1].value == "="
            and (k == 0 or (toks[k - 1].value == "," and k - 1 in _top_idx(toks, ",")))]
    if not keys or keys[0] != 0:
        raise DslError(ln.no, ln.text, "ожидалось plot: x=…, y=…", toks[0].pos if toks else len(ln.text))
    vals = {}
    for n, k in enumerate(keys):
        end = keys[n + 1] - 1 if n + 1 < len(keys) else len(toks)
        name = toks[k].value
        if name in vals:
            raise DslError(ln.no, ln.text, f"ключ {name} повторяется", toks[k].pos)
        vals[name] = (toks[k + 2:end], toks[k].pos)
    for need in ("x", "y"):
        if need not in vals:
            raise DslError(ln.no, ln.text, f"нет ключа {need}=")
    regions = []

    def traces(key):
        n = _expr(ln, vals[key][0], f"трассы {key}")
        items = n.args if n.kind == "seq" else [n]
        if len(items) > MAX_TRACES:
            raise DslError(ln.no, ln.text, f"трасс больше {MAX_TRACES}", vals[key][1])
        return items

    def lim(key):
        if key not in vals:
            return None
        n = _expr(ln, vals[key][0], f"предел {key}")
        if n.kind != "range" or len(n.args) != 2:
            raise DslError(ln.no, ln.text, f"{key}=нижний..верхний", vals[key][1])
        return tuple(n.args)

    for name, (vt, pos) in vals.items():
        if name in ("x", "y", "xlim", "ylim"):
            continue
        rng = _expr(ln, vt, f"диапазон {name}")
        if rng.kind != "range":
            raise DslError(ln.no, ln.text, f"{name}=a..b или {name}=a, шаг..b", pos)
        if len(rng.args) == 2:
            rng = Node("range", [rng.args[0], _step(*rng.args), rng.args[1]])
        d = Node("define", [Id(name), rng])
        regions.append(Region("math", to_xml(d), region_dsl(d), ln.no))
    spec = PlotSpec(traces("x"), traces("y"), lim("xlim"), lim("ylim"))
    regions.append(Region("plot", "", plot_dsl(spec), ln.no, spec))
    return regions


def _num(n: Node) -> float | None:
    if n.kind == "real":
        try:
            return float(n.attrs["v"])
        except ValueError:
            return None
    return None


def _step(a: Node, b: Node) -> Node:
    fa, fb = _num(a), _num(b)
    if fa is not None and fb is not None:
        return Real(f"{fa + (fb - fa) / 200:.6g}")
    diff = Node("parens", [Node("op", [b, a], {"op": "minus"})])
    return Node("op", [a, Node("op", [diff, Real(200)], {"op": "div"})], {"op": "plus"})


def plot_dsl(spec: PlotSpec) -> str:
    s = "plot: x=" + ", ".join(to_dsl(x) for x in spec.xs) + ", y=" + ", ".join(to_dsl(y) for y in spec.ys)
    for key, lim in (("xlim", spec.xlim), ("ylim", spec.ylim)):
        if lim:
            s += f", {key}={to_dsl(Node('range', list(lim)))}"
    return s


# ================================================================ text / raw

def text_xml(style: str, content: str) -> str:
    lock = "true" if len(content) > 70 else "false"
    return (f'<text use-page-width="false" push-down="false" lock-width="{lock}">'
            f'<p style="{style}" {P_ATTRS}>{escape(content)}</p></text>')


def text_dsl(text_elem: ET.Element) -> str:
    """<text> element -> one DSL line per paragraph."""
    ps = [e for e in text_elem.iter() if e.tag.rsplit("}", 1)[-1] == "p"]
    out = []
    for p in ps or [text_elem]:
        content = " ".join("".join(p.itertext()).split())
        style = p.get("style", "Normal")
        prefix = {"Heading 1": "# ", "Title": "# ", "Heading 2": "## ", "Subtitle": "## ",
                  "Heading 3": "### "}.get(style, "text: ")
        if content or prefix != "text: ":
            out.append(prefix + content)
    return "\n".join(out) or "text:"


def _raw(ln: Line) -> Region:
    x = ln.text[4:].strip()
    if ln.body:
        x += "\n" + "\n".join(b.text for b in _flat(ln.body))
    wrap = f'<w xmlns="{WS}" xmlns:ml="{ML}" xmlns:u="{U}">{x}</w>'
    try:
        root = ET.fromstring(wrap)
    except ET.ParseError as e:
        raise DslError(ln.no, ln.text, f"raw: некорректный XML ({e})")
    kids = list(root)
    if len(kids) == 1 and kids[0].tag == f"{{{WS}}}region":
        return Region("raw", x, "raw: " + x, ln.no)
    if len(kids) == 1 and kids[0].tag == f"{{{WS}}}math":
        m = re.match(r"\s*<math\b[^>]*>(.*)</math>\s*$", x, re.S)
        x = m.group(1) if m else x
        kids = list(kids[0])
    if not kids or any(not k.tag.startswith(f"{{{ML}}}") for k in kids):
        raise DslError(ln.no, ln.text, "raw: ожидалось <ml:…>, <math>…</math> или <region>…</region>")
    return Region("math", x, "raw: " + x, ln.no)


# ================================================================ entry

def _top(lines: list[Line]) -> list[Region]:
    out = []
    for ln in lines:
        s = ln.text
        m = re.match(r"(#{1,3})(\s+|$)(.*)", s)
        if m:
            content = " ".join([m.group(3)] + [b.text for b in _flat(ln.body)]).strip()
            style = HEADINGS[len(m.group(1))]
            out.append(Region("text", text_xml(style, content), f"{m.group(1)} {content}", ln.no))
            continue
        if s.startswith("text:"):
            content = " ".join([s[5:].strip()] + [b.text for b in _flat(ln.body)]).strip()
            out.append(Region("text", text_xml("Normal", content), f"text: {content}", ln.no))
            continue
        if s.startswith("raw:"):
            out.append(_raw(ln))
            continue
        if re.match(r"plot\s*:", s):
            if ln.body:
                raise DslError(ln.body[0].no, ln.body[0].text, "у plot: нет блока")
            out += _plot(ln)
            continue
        toks = _toks(ln)
        if len(toks) == 1 and _word(toks[0], "Given"):
            given = Id("Given")
            out.append(Region("math", to_xml(given), "Given", ln.no))
            out += _top(ln.body)
            continue
        if _is_block_kw(toks) and not _top_idx(toks, ":=", ":==") and not (len(toks) == 1 and toks[0].value != "return"):
            raise DslError(ln.no, ln.text, f"`{toks[0].value}` допустим только внутри программы (тело с отступом)")
        if _top_idx(toks, "<-") and not _top_idx(toks, ":=", ":=="):
            raise DslError(ln.no, ln.text, "`<-` только внутри программы; для определения — `:=`; "
                                           "для сравнения с отрицательным — `x < -1`")
        n = _math(ln)
        try:
            d = region_dsl(n)
        except Unsupported:
            d = s
        out.append(Region("math", to_xml(n, True), d, ln.no))
    return out


def parse(text: str) -> list[Region]:
    """DSL text -> regions in order. Raises DslError with a 1-based line number."""
    return _top(_group(_lines(text)))
