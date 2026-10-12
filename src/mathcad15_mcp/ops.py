"""What the six tools do, without MCP: paths in, compact text out, OpError for the model.

Writing goes in two passes: our estimated layout -> Mathcad recalculates and saves a temp
copy (real region sizes, results, rendering images) -> the edited block is restacked with
the measured sizes and that copy is written to the target file.
"""
from __future__ import annotations

import os
import re
import tempfile

from . import render
from .dsl import DslError, parse
from .engine import FORMATS, Engine, EngineError, MathcadTimeout, MathcadUnavailable
from .mathml import _lhs_dsl, from_xml
from .mathml import _ln as ml_ln
from .worksheet import Plan, Worksheet, WorksheetError, child, error_names

NO_CALC = "без пересчёта: {}"


class OpError(Exception):
    pass


def regions_arg(spec) -> set[int] | None:
    """'3-8', '1,3,7-9', 5 or [1, 2] -> set of 1-based numbers."""
    if spec is None or spec == "":
        return None
    if isinstance(spec, int):
        return {spec}
    if isinstance(spec, (list, tuple)):
        return {int(x) for x in spec}
    out = set()
    for part in str(spec).replace(" ", "").split(","):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise OpError(f"regions: «{spec}» — нужно вида 3-8 или 1,3,7-9")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        out.update(range(min(a, b), max(a, b) + 1))
    return out


def _path(p: str, must_exist=True) -> str:
    if not p or not str(p).strip():
        raise OpError("path: пустой путь")
    p = os.path.abspath(os.path.expanduser(str(p).strip().strip('"')))
    if must_exist and not os.path.isfile(p):
        raise OpError(f"файл не найден: {p}")
    return p


def _ext(p: str) -> str:
    return os.path.splitext(p)[1].lower().lstrip(".")


def _dsl(text: str) -> list:
    try:
        return parse(text)
    except DslError as e:
        raise OpError(f"ошибка DSL, файл не изменён — {e}\n(синтаксис: mc_help)") from None


def _load(path: str) -> Worksheet:
    if _ext(path) != "xmcd":
        raise OpError(f"{os.path.basename(path)}: править можно только .xmcd — "
                      f"сначала mc_export(path, out='….xmcd')")
    try:
        return Worksheet.load(path)
    except WorksheetError as e:
        raise OpError(str(e)) from None


def _write_atomic(ws: Worksheet, path: str):
    tmp = path + ".mc15tmp"
    ws.save(tmp)
    os.replace(tmp, path)


def _defs(ws: Worksheet):
    """(display number, lhs DSL, ':=' or ':==') for top-level definitions."""
    for n, r in enumerate(ws.regions, 1):
        m = child(ws.elem(r), "math") if r.kind == "math" else None
        d = next((c for c in m if ml_ln(c) in ("define", "globalDefine")), None) if m is not None else None
        if d is None:
            continue
        try:
            yield n, _lhs_dsl(from_xml(d).args[0]), ":=" if ml_ln(d) == "define" else ":=="
        except Exception:
            continue


def _key(name: str) -> str:
    return re.sub(r"\s+", "", str(name))


def _definition(ws: Worksheet, name: str) -> tuple[int, str, str] | None:
    """(display number, lhs, op) of the first top-level definition of `name` ('L', 'M.max', 'f(x)')."""
    return next((d for d in _defs(ws) if _key(d[1]) == _key(name)), None)


def _defined_names(ws: Worksheet, limit=25) -> str:
    names = list(dict.fromkeys(lhs for _, lhs, _ in _defs(ws)))
    return (", ".join(names[:limit]) + (", …" if len(names) > limit else "")) or "нет"


class Ops:
    def __init__(self, engine: Engine | None = None):
        self.engine = engine or Engine()

    # ------------------------------------------------------------ helpers

    def _calc(self, ws: Worksheet, tmp: str) -> tuple[Worksheet, dict[int, str]]:
        src = os.path.join(tmp, "in.xmcd")
        ws.save(src)
        res = self.engine.calc_save(src, os.path.join(tmp, "out.xmcd"))
        try:
            saved = Worksheet.parse(res.xml)
        except WorksheetError as e:
            raise OpError(f"Mathcad сохранил нечитаемый файл: {e}") from None
        return saved, error_names(saved, res.com)

    def _convert(self, path: str) -> Worksheet:
        """A non-.xmcd sheet (.mcd) as a Worksheet, through a Mathcad SaveAs."""
        with tempfile.TemporaryDirectory(prefix="mc15_") as tmp:
            try:
                res = self.engine.calc_save(path, os.path.join(tmp, "conv.xmcd"))
                return Worksheet.parse(res.xml)
            except (EngineError, WorksheetError) as e:
                raise OpError(str(e)) from None

    def _commit(self, ws: Worksheet, plans: list[Plan], path: str, what: str) -> str:
        name = os.path.basename(path)
        with tempfile.TemporaryDirectory(prefix="mc15_") as tmp:
            try:
                saved, errs = self._calc(ws, tmp)
            except MathcadUnavailable as e:
                _write_atomic(ws, path)
                return render.sheet(name, ws, note=NO_CALC.format(e))
            except MathcadTimeout as e:
                raise OpError(f"{e}\n{what}: файл не записан (лист зацикливается — проверьте программы/циклы)") from None
            except EngineError as e:
                raise OpError(f"{e}\n{what}: файл не записан") from None
        for p in reversed(plans):
            saved.apply(p)
        _write_atomic(saved, path)
        return render.sheet(name, saved, errs)

    # ------------------------------------------------------------ tools

    def read(self, path: str, regions=None, errors_only: bool = False) -> str:
        path = _path(path)
        only = regions_arg(regions)
        name = os.path.basename(path)
        with tempfile.TemporaryDirectory(prefix="mc15_") as tmp:
            try:
                res = self.engine.calc_save(path, os.path.join(tmp, "out.xmcd"))
            except MathcadUnavailable as e:
                try:
                    ws = Worksheet.load(path)
                except WorksheetError as we:
                    raise OpError(f"{e}; а без Mathcad читается только .xmcd: {we}") from None
                return render.sheet(name, ws, only=only, errors_only=errors_only,
                                    note=NO_CALC.format(f"{e} — результаты из файла"))
            except EngineError as e:
                raise OpError(str(e)) from None
        try:
            ws = Worksheet.parse(res.xml)
        except WorksheetError as e:
            raise OpError(str(e)) from None
        return render.sheet(name, ws, error_names(ws, res.com), only=only, errors_only=errors_only)

    def write(self, path: str, text: str, overwrite: bool = False) -> str:
        path = _path(path, must_exist=False)
        if _ext(path) != "xmcd":
            raise OpError("mc_write создаёт только .xmcd; в pdf/rtf/html — потом mc_export")
        if os.path.exists(path) and not overwrite:
            raise OpError(f"{path} уже существует — overwrite=true, чтобы заменить, или mc_edit")
        regions = _dsl(text or "")
        if not regions:
            raise OpError("text пустой — нечего писать")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ws = Worksheet.new()
        plan = ws.replace(1, 0, regions)
        return self._commit(ws, [plan], path, "mc_write")

    def edit(self, path: str, start: int, end: int, text: str = "") -> str:
        path = _path(path)
        ws = _load(path)
        regions = _dsl(text) if text and text.strip() else []
        try:
            plan = ws.replace(int(start), int(end), regions)
        except WorksheetError as e:
            raise OpError(str(e)) from None
        return self._commit(ws, [plan], path, "mc_edit")

    def calc(self, path: str, values: dict | None = None, get: list | None = None, save: bool = False) -> str:
        """values: {name: DSL expression} (the tool's `set`)."""
        path = _path(path)
        name = os.path.basename(path)
        if _ext(path) == "xmcd":
            ws = _load(path)
        elif save:
            raise OpError(f"{name}: save только для .xmcd — сначала mc_export(path, out='….xmcd')")
        else:
            ws = self._convert(path)
        set_, get = dict(values or {}), [g for g in (get or []) if str(g).strip()]
        if save and not set_:
            raise OpError("save=true без set — нечего сохранять")

        # set: replace the right side of each name's first definition, top-down
        found, missing = [], []
        for nm, expr in set_.items():
            hit = _definition(ws, str(nm))
            (found.append((hit[0], str(nm), str(expr))) if hit else missing.append(str(nm)))
        if missing:
            raise OpError(f"set: нет определения {', '.join(missing)} в листе (определены: {_defined_names(ws)}); "
                          f"новые регионы — mc_edit")
        plans = []
        for _, nm, expr in sorted(found):
            n, lhs, op = _definition(ws, nm)
            regs = _dsl(f"{lhs} {op} {expr}")
            if len(regs) != 1 or regs[0].kind != "math":
                raise OpError(f"set {nm}: «{expr}» — нужно одно выражение")
            plans.append(ws.replace(n, n, regs))

        # get: temporary evaluations at the very end, dropped before saving
        tmp_ids = []
        if get:
            lines = "\n".join(str(g) if "?" in str(g) else f"{g} = ?" for g in get)
            n = len(ws.regions)
            tmp_ids = ws.replace(n + 1, n, _dsl(lines)).new_ids

        with tempfile.TemporaryDirectory(prefix="mc15_") as tmp:
            try:
                saved, errs = self._calc(ws, tmp)
            except MathcadUnavailable as e:
                raise OpError(f"mc_calc нужен Mathcad: {e}") from None
            except EngineError as e:
                raise OpError(str(e)) from None

        got = [ln for rid in tmp_ids if rid in saved.by_id() for ln in render.region_text(saved, rid, errs)]
        saved.remove(set(tmp_ids))
        if save:
            for p in reversed(plans):
                saved.apply(p)
            _write_atomic(saved, path)
        changed = ", ".join(f"{nm} := {expr}" for _, nm, expr in sorted(found))
        note = ("изменено: " + changed + ("; сохранено" if save else "; файл не изменён")) if found else "пересчитан"
        listing = render.sheet(name, saved, errs, note=note, errors_only=bool(get), results_only=not get)
        head, *rest = listing.split("\n")
        if get:
            rest = [r for r in rest if r != "(нет подходящих регионов)"]
            return "\n".join([head] + got + (["ошибки в листе:"] + rest if rest else []))
        return listing

    def export(self, path: str, out: str | None = None, format: str | None = None) -> str:
        path = _path(path)
        fmt = (format or (_ext(out) if out else "")).lower().lstrip(".")
        if not fmt:
            raise OpError("укажите out с расширением или format: pdf, rtf, html, xmcd, xmcdz")
        if fmt not in FORMATS and fmt != "pdf":
            raise OpError(f"формат {fmt}: доступны pdf, {', '.join(FORMATS)}")
        out = _path(out, must_exist=False) if out else os.path.splitext(path)[0] + "." + fmt
        if os.path.normcase(out) == os.path.normcase(path):
            raise OpError("out совпадает с исходным файлом")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        try:
            self.engine.export(path, out, fmt)
        except EngineError as e:
            raise OpError(str(e)) from None
        return f"{out} — {fmt}, {max(1, os.path.getsize(out) // 1024)} КБ"

