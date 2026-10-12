"""Text of a computed value: `<result>` (math30) -> '54 kN·m', '[1, 2; 3, 4]', '3 - 7i'."""
from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from fractions import Fraction
from functools import lru_cache
from importlib import resources

MAX_ROWS, MAX_COLS = 6, 8


@lru_cache(maxsize=1)
def symbols() -> dict[str, str]:
    """Mathcad unit name ('kilonewton') -> symbol ('kN')."""
    return json.loads(resources.files("mathcad15_mcp").joinpath("data/units.json").read_text("utf-8"))


def _ln(e: ET.Element) -> str:
    return e.tag.rsplit("}", 1)[-1] if isinstance(e.tag, str) else ""


def num(s: str) -> str:
    """6 significant digits, exponent without '+'/leading zeros: 1.5e-5, 54000, 0.0015."""
    s = (s or "").strip()
    try:
        v = float(s)
    except ValueError:
        return s
    if v == 0:
        return "0"
    if math.isinf(v) or math.isnan(v):
        return s
    t = f"{v:.6g}"
    if "e" in t:
        m, e = t.split("e")
        t = f"{m}e{int(e)}"
    return t


def _complex(e: ET.Element) -> str:
    parts = {_ln(c): (c.text or "") for c in e}
    re_, im = parts.get("real", "0"), parts.get("imag", "0")
    try:
        a, b = float(re_), float(im)
    except ValueError:
        return f"{re_} + {im}i"
    if b == 0:
        return num(re_)
    if a == 0:
        return num(im) + "i"
    return f"{num(re_)} {'-' if b < 0 else '+'} {num(str(abs(b)))}i"


def _matrix(e: ET.Element) -> str:
    rows, cols = int(e.get("rows", "0")), int(e.get("cols", "0"))
    cells = list(e)
    if rows * cols != len(cells):
        return f"{{матрица {rows}×{cols}}}"

    def cell(i, j):
        c = cells[j * rows + i]
        return f"{{{c.get('rows')}×{c.get('cols')}}}" if _ln(c) == "matrix" else value(c)

    r_show, c_show = min(rows, MAX_ROWS), min(cols, MAX_COLS)
    lines = []
    for i in range(r_show):
        row = [cell(i, j) for j in range(c_show)]
        if c_show < cols:
            row.append("…")
        lines.append(", ".join(row))
    if r_show < rows:
        lines.append("…")
    s = "[" + "; ".join(lines) + "]"
    if r_show < rows or c_show < cols:
        s += f" ({rows}×{cols})"
    return s


def _power(p: Fraction) -> str:
    if p == 1:
        return ""
    return f"^{p}" if p.denominator == 1 else f"^({p})"


def unit(mono: ET.Element) -> str:
    """<unitMonomial> -> 'kg·m/s^2', 'J/(kg·K)', '1/s'."""
    pos, neg = [], []
    for r in mono:
        name = r.get("unit", "?")
        sym = symbols().get(name, name)
        try:
            p = Fraction(int(r.get("power-numerator", "1")), int(r.get("power-denominator", "1")))
        except (ValueError, ZeroDivisionError):
            p = Fraction(1)
        if p > 0:
            pos.append(sym + _power(p))
        elif p < 0:
            neg.append(sym + _power(-p))
    s = "·".join(pos)
    if neg:
        d = neg[0] if len(neg) == 1 else "(" + "·".join(neg) + ")"
        s = (s or "1") + "/" + d
    return s


def value(e: ET.Element) -> str:
    k = _ln(e)
    if k == "real":
        return num(e.text)
    if k == "imag":
        return num(e.text) + "i"
    if k == "complex":
        return _complex(e)
    if k == "str":
        return '"' + (e.text or "") + '"'
    if k == "id":
        return (e.text or "").strip()
    if k == "matrix":
        return _matrix(e)
    if k == "unitedValue":
        kids = list(e)
        v = value(kids[0]) if kids else "?"
        u = next((unit(c) for c in kids[1:] if _ln(c) == "unitMonomial"), "")
        return f"{v} {u}" if u else v
    if k == "result":
        kids = list(e)
        return value(kids[0]) if kids else "?"
    return f"<{k}>"


def result_text(elem: ET.Element) -> str:
    """`<result>` element (or any value element inside it) -> display text."""
    return value(elem)
