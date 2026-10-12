"""Worksheet -> compact text listing for the model.

    cantilever.xmcd — 12 регионов, 1 ошибка
    [1] # Балка
    [2] L := 6 m  ⚠ L: переопределена встроенная единица
    [4] M = ? kN*m  → 54 kN·m
    [7] σ := M/Wx  ✗ Wx: Эта переменная не определена
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from . import units
from .dsl import plot_dsl
from .mathml import ML, from_xml, region_dsl, to_dsl
from .mathml import _ln as ml_ln
from .plot import read_blob
from .worksheet import Worksheet, child, lname as _local

RAW_MAX = 160
ET.register_namespace("ml", ML)

COM_CODES = {
    "bad_variable": "переменная не определена",
    "divide by zero": "деление на ноль",
    "file open error": "ошибка открытия файла",
    "bad_syntax": "синтаксическая ошибка",
    "bad_dimensions": "несовместимые единицы",
    "bad_argument": "неверный тип аргумента",
    "invalid level": "неверный уровень",
}
WARNINGS = {
    "WarnRedefinedBIUnit": "переопределена встроенная единица",
    "WarnRedefinedBIFunction": "переопределена встроенная функция",
    "WarnRedefinedBIConstant": "переопределена встроенная константа",
    "WarnRedefinedUDFunction": "функция переопределена",
    "WarnRedefinedUDScalar": "переменная переопределена",
}
OTHER = {"picture": "(рисунок)", "png": "(рисунок)", "metafile": "(рисунок)", "bitmap": "(рисунок)",
         "ole": "(OLE-объект)", "component": "(компонент)", "pageBreak": "(разрыв страницы)",
         "solveBlock": "(блок решения)", "empty": "(пусто)"}


def plural(n: int, one: str, few: str, many: str) -> str:
    k = n % 100
    if 11 <= k <= 14:
        w = many
    else:
        w = {1: one, 2: few, 3: few, 4: few}.get(n % 10, many)
    return f"{n} {w}"


def _clean(s: str) -> str:
    return " ".join(s.replace("\\n", " ").split()).rstrip(".")


def _is_code(s: str) -> bool:
    first = re.split(r"[\t ]", s.strip(), maxsplit=1)[0]
    return "_" in first or any(s.startswith(c) for c in COM_CODES)


def _code_text(s: str) -> str:
    """Mathcad error code ('bad_variable\\tdoc_mc_x\\t…', 'bad_dimensions Length Unitless') -> Russian."""
    code = next((c for c in COM_CODES if s.startswith(c)), None) or re.split(r"[\t ]", s.strip(), maxsplit=1)[0]
    text = COM_CODES.get(code, code.replace("_", " "))
    if code == "bad_dimensions":
        dims = s[len(code):].split()
        if len(dims) == 2:
            text += f": {dims[0]} вместо {dims[1]}"
    return text


def com_name(msg: str) -> str:
    """Bad identifier from a COM ErrorMsg: 'bad_variable\\tdoc_mc_Wx\\t…' -> 'Wx'."""
    for p in msg.split("\t")[1:]:
        p = p.strip()
        for pre in ("doc_mc_", "mc_"):
            if p.startswith(pre):  # punctuation is hex-escaped: W_2E_x = W.x, A_27_ = A', a_5F_b = a_b
                return re.sub(r"_([0-9A-F]{2})_", lambda m: chr(int(m.group(1), 16)), p[len(pre):])
    return ""


def error_text(xml_err: str | None, com_msg: str | None) -> str:
    """'Wx: Эта переменная не определена' from the saved XML text + the name from COM."""
    xml_err = (xml_err or "").strip()
    com_msg = (com_msg or "").strip()
    if xml_err:
        text = _code_text(xml_err) if _is_code(xml_err) else _clean(xml_err)
    elif com_msg:
        text = _code_text(com_msg)
    else:
        return ""
    name = com_name(com_msg) or com_name(xml_err)
    return f"{name}: {text}" if name else text


def _short(x: str) -> str:
    x = re.sub(r'\s*xmlns(?::\w+)?="[^"]*"', "", x)
    x = re.sub(r'\s*xml:space="preserve"', "", x)
    x = re.sub(r">\s+<", "><", x).strip()
    return x if len(x) <= RAW_MAX else x[:RAW_MAX] + "…"


def math_dsl(m: ET.Element) -> str:
    """DSL text of a <math> element; 'raw: <xml…>' when the DSL can't express it."""
    kids = [c for c in m if ml_ln(c)]
    if len(kids) == 1:
        try:
            return region_dsl(from_xml(kids[0]))
        except Exception:
            pass
    body = "".join(ET.tostring(k, encoding="unicode") for k in kids
                   if ml_ln(k) not in ("result", "symResult"))
    body = re.sub(r"<(\w+:)?result\b.*?</(\w+:)?result>", "", body, flags=re.S)
    return "raw: " + _short(body)


def math_result(m: ET.Element) -> str:
    r = next((e for e in m.iter() if _local(e) == "result"), None)
    if r is not None:
        return units.value(r)
    s = next((e for e in m.iter() if _local(e) == "symResult"), None)
    if s is not None:
        try:
            return ", ".join(to_dsl(from_xml(k), True) for k in s if ml_ln(k))
        except Exception:
            return "(символьный результат)"
    return ""


def math_warnings(m: ET.Element) -> list[str]:
    out = []
    for e in m.iter():
        w = e.get("warning")
        if not w:
            continue
        lhs = next((c for c in e if ml_ln(c)), None)
        name = ""
        if lhs is not None:
            idel = lhs if ml_ln(lhs) == "id" else next((c for c in lhs.iter() if ml_ln(c) == "id"), None)
            name = (idel.text or "").strip() if idel is not None else ""
            if idel is not None and idel.get("subscript"):
                name += "." + idel.get("subscript")
        text = WARNINGS.get(w, w)
        out.append(f"⚠ {name}: {text}" if name else f"⚠ {text}")
    return out


class _Ctx:
    def __init__(self, ws: Worksheet, com: dict[int, str]):
        self.ws, self.com = ws, com
        self._items = None
        self.errors = 0

    def items(self) -> dict[int, bytes]:
        if self._items is None:
            self._items = self.ws.items()
        return self._items


def _math_line(ctx: _Ctx, reg: ET.Element, m: ET.Element) -> tuple[str, str]:
    """(dsl, tail) for a math region: tail = '  → result  ✗ err  ⚠ warn'."""
    d = math_dsl(m)
    tail = ""
    res = math_result(m)
    if res:
        tail += f"  → {res}"
    rid = int(float(reg.get("region-id", "0")))
    err = error_text(m.get("error"), ctx.com.get(rid))
    if err:
        ctx.errors += 1
        tail += f"  ✗ {err}"
    for w in math_warnings(m):
        tail += "  " + w
    return d, tail


def _paragraphs(e: ET.Element, ctx: _Ctx, out: list[str], tails: list[str]):
    """Walk <text>: one entry per <p>; inline math regions become {dsl → result}."""
    for c in e:
        k = _local(c)
        if k == "p":
            acc: list[str] = []
            _inline(c, ctx, acc, tails)
            content = " ".join("".join(acc).split())
            prefix = {"Heading 1": "# ", "Title": "# ", "Heading 2": "## ", "Subtitle": "## ",
                      "Heading 3": "### "}.get(c.get("style", "Normal"), "text: ")
            if content or prefix != "text: ":
                out.append(prefix + content)
        elif k != "region":
            _paragraphs(c, ctx, out, tails)


def _inline(e: ET.Element, ctx: _Ctx, acc: list[str], tails: list[str]):
    if e.text:
        acc.append(e.text)
    for c in e:
        k = _local(c)
        if k == "region":
            m = child(c, "math")
            if m is not None:
                d, tail = _math_line(ctx, c, m)
                res = re.match(r"\s*→ (.*?)(?=  [✗⚠]|$)", tail)
                acc.append("{" + d.replace("\n", "; ") + (f" → {res.group(1)}" if res else "") + "}")
                tails += [t for t in re.findall(r"[✗⚠] .*?(?=  [✗⚠]|$)", tail)]
            else:
                acc.append("{…}")
        elif k not in ("indexes", "rendering"):
            _inline(c, ctx, acc, tails)
        if c.tail:
            acc.append(c.tail)


def region_lines(ctx: _Ctx, reg: ET.Element) -> list[str]:
    """Lines for one <region> element (area children prefixed '· ')."""
    body = next((c for c in reg if _local(c) not in ("rendering", "indexes")), None)
    k = _local(body) if body is not None else "empty"
    if k == "math":
        d, tail = _math_line(ctx, reg, body)
        lines = d.split("\n")
        lines[0] += tail
        return lines
    if k == "text":
        out: list[str] = []
        tails: list[str] = []
        _paragraphs(body, ctx, out, tails)
        out = out or ["text:"]
        if tails:
            out[-1] += "  " + "  ".join(tails)
        return out
    if k == "plot":
        spec = None
        try:
            iid = int(body.get("item-idref", "-1"))
            blob = ctx.items().get(iid)
            spec = read_blob(blob) if blob else None
        except Exception:
            spec = None
        s = plot_dsl(spec) if spec else "(график)"
        if body.get("error"):
            ctx.errors += 1
            s += f"  ✗ {error_text(body.get('error'), None)}"
        return [s]
    if k == "area":
        name = body.get("name") or ""
        head = "area" + (f" «{name}»" if name else "")
        if body.get("is-collapsed") == "true":
            head += " (свёрнута)"
        if body.get("is-locked") == "true":
            head += " (заблокирована)"
        kids = [c for c in body if _local(c) == "region"]
        kids.sort(key=lambda c: (float(c.get("top", "0")), float(c.get("left", "0"))))
        out = [head]
        for c in kids:
            for i, ln in enumerate(region_lines(ctx, c)):
                out.append(("· " if i == 0 else "  ") + ln)
        return out
    if k == "reference":
        href = next((e.get("href") for e in body.iter() if e.get("href")), "?")
        return [f"(ссылка: {href})"]
    return [OTHER.get(k, f"({k})")]


def region_text(ws: Worksheet, rid: int, errors: dict[int, str] | None = None) -> list[str]:
    """Lines of one top-level region by id (no [n] prefix)."""
    return region_lines(_Ctx(ws, errors or {}), ws.elem(ws.by_id()[rid]))


def sheet(name: str, ws: Worksheet, errors: dict[int, str] | None = None, only: set[int] | None = None,
          errors_only: bool = False, note: str | None = None, results_only: bool = False) -> str:
    """Listing of a worksheet. `errors`: region-id -> COM message (from worksheet.error_names).
    `only`: 1-based display numbers to show; `errors_only`: show only regions with ✗;
    `results_only`: only regions with a result (→) or an error."""
    ctx = _Ctx(ws, errors or {})
    body = []
    for n, r in enumerate(ws.regions, 1):
        before = ctx.errors
        try:
            lines = region_lines(ctx, ws.elem(r))
        except ET.ParseError:
            lines = ["raw: " + _short(r.xml)]
        if only is not None and n not in only:
            continue
        if errors_only and ctx.errors == before:
            continue
        if results_only and ctx.errors == before and not any("→" in ln for ln in lines):
            continue
        pad = " " * (len(str(n)) + 3)
        body.append(f"[{n}] " + lines[0])
        body += [pad + ln for ln in lines[1:]]
    head = f"{name} — {plural(len(ws.regions), 'регион', 'региона', 'регионов')}, " \
           f"{plural(ctx.errors, 'ошибка', 'ошибки', 'ошибок')}"
    if note:
        head += f" ({note})"
    if (only is not None or errors_only or results_only) and not body:
        body.append("(нет подходящих регионов)")
    return "\n".join([head] + body)
