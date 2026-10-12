import os
import re

import pytest

from mathcad15_mcp import help
from mathcad15_mcp.dsl import parse
from mathcad15_mcp.engine import MathcadUnavailable
from mathcad15_mcp.ops import OpError, Ops, _defined_names, _definition, regions_arg
from mathcad15_mcp.worksheet import Worksheet


class NoMathcad:
    owned = False

    def calc_save(self, *a, **k):
        raise MathcadUnavailable("Mathcad 15 не запускается (тест)")

    def export(self, *a, **k):
        raise MathcadUnavailable("Mathcad 15 не запускается (тест)")


@pytest.fixture
def offline():
    return Ops(NoMathcad())


def test_regions_arg():
    assert regions_arg(None) is None and regions_arg("") is None
    assert regions_arg("3-5") == {3, 4, 5}
    assert regions_arg("1, 3,7-8") == {1, 3, 7, 8}
    assert regions_arg("9-7") == {7, 8, 9}
    assert regions_arg(4) == {4} and regions_arg([2, 3]) == {2, 3}
    with pytest.raises(OpError, match="3-8"):
        regions_arg("три")


def test_definition_lookup_covers_functions():
    ws = Worksheet.new()
    ws.replace(1, 0, parse("# T\nf(x) := x^2\ng(a, b) :== a*b\nM.max := 3\nσ := 1\nM.max = ?"))
    assert _definition(ws, "f( x )") == (2, "f(x)", ":=")
    assert _definition(ws, "g(a,b)") == (3, "g(a, b)", ":==")
    assert _definition(ws, "M.max") == (4, "M.max", ":=")
    assert _definition(ws, "σ")[0] == 5
    assert _definition(ws, "z") is None
    assert _defined_names(ws) == "f(x), g(a, b), M.max, σ"


def test_write_without_mathcad_keeps_estimated_layout(offline, tmp_path):
    p = str(tmp_path / "новый лист.xmcd")
    out = offline.write(p, "# Лист\nx := 2\nx = ?")
    assert out.splitlines()[0].startswith("новый лист.xmcd — 3 региона")
    assert "без пересчёта" in out.splitlines()[0]
    assert len(Worksheet.load(p).regions) == 3
    with pytest.raises(OpError, match="уже существует"):
        offline.write(p, "x := 1")


def test_read_without_mathcad_shows_file(offline, tmp_path):
    p = str(tmp_path / "a.xmcd")
    offline.write(p, "x := 2\ny := x^2")
    out = offline.read(p, regions="2")
    assert out.splitlines()[1] == "[2] y := x^2"
    assert "результаты из файла" in out.splitlines()[0]


@pytest.mark.parametrize("call,frag", [
    (lambda o, d: o.write(os.path.join(d, "a.pdf"), "x := 1"), "только .xmcd"),
    (lambda o, d: o.write(os.path.join(d, "a.xmcd"), "  \n"), "пустой"),
    (lambda o, d: o.write(os.path.join(d, "a.xmcd"), "x := (1 +"), "ошибка DSL, файл не изменён"),
    (lambda o, d: o.read(os.path.join(d, "нет.xmcd")), "файл не найден"),
    (lambda o, d: o.edit(os.path.join(d, "b.xmcd"), 5, 5, "x := 1"), "регион"),
    (lambda o, d: o.calc(os.path.join(d, "b.xmcd"), save=True), "без set"),
    (lambda o, d: o.calc(os.path.join(d, "b.xmcd"), values={"zz": "1"}), "определены: x, y"),
    (lambda o, d: o.calc(os.path.join(d, "b.xmcd"), values={"x": "1"}), "нужен Mathcad"),
    (lambda o, d: o.export(os.path.join(d, "b.xmcd"), format="doc"), "формат doc"),
    (lambda o, d: o.export(os.path.join(d, "b.xmcd"), out=os.path.join(d, "b.xmcd")), "совпадает"),
])
def test_errors_are_op_errors(offline, tmp_path, call, frag):
    offline.write(str(tmp_path / "b.xmcd"), "x := 2\ny := x^2")
    with pytest.raises(OpError) as e:
        call(offline, str(tmp_path))
    assert frag in str(e.value)


def test_edit_refuses_non_xmcd(offline, tmp_path):
    p = tmp_path / "old.mcd"
    p.write_bytes(b"\0")
    with pytest.raises(OpError, match="mc_export"):
        offline.edit(str(p), 1, 1, "x := 1")


# ------------------------------------------------------------ with Mathcad

SHEET = """\
# Балка
L.b := 6 m
q := 12 kN/m
M := q*L.b^2/8
M = ? kN*m
σ := M/W.x
σ = ? MPa
"""


@pytest.mark.mathcad
def test_write_edit_calc_export(engine, tmp_path):
    ops = Ops(engine)
    p = str(tmp_path / "балка.xmcd")

    out = ops.write(p, SHEET)
    assert out.splitlines()[0] == "балка.xmcd — 7 регионов, 2 ошибки"
    assert "[5] M = ? kN*m  → 54 kN·m" in out
    assert "[6] σ := M/W.x  ✗ W.x:" in out

    out = ops.edit(p, 6, 5, "W.x := 400 cm^3")                     # insert before [6]
    assert "0 ошибок" in out.splitlines()[0]
    assert "[8] σ = ? MPa  → 135 MPa" in out
    assert ops.read(p, errors_only=True).splitlines()[1:] == ["(нет подходящих регионов)"]

    before = open(p, "rb").read()
    out = ops.calc(p, values={"L.b": "8 m"}, get=["M = ? kN*m", "σ"])
    lines = out.splitlines()
    assert "изменено: L.b := 8 m; файл не изменён" in lines[0]
    assert lines[1:] == ["M = ? kN*m  → 96 kN·m", "σ = ?  → 2.4e8 Pa"]
    assert open(p, "rb").read() == before

    ops.calc(p, values={"q": "10 kN/m"}, save=True)
    assert "[5] M = ? kN*m  → 45 kN·m" in ops.read(p, regions="5")

    out = ops.edit(p, 6, 8, "")                                     # delete W.x, σ, σ = ?
    assert out.splitlines()[0] == "балка.xmcd — 5 регионов, 0 ошибок"

    for fmt in ("html", "rtf"):
        res = ops.export(p, format=fmt)
        assert re.fullmatch(rf".*балка\.{fmt} — {fmt}, \d+ КБ", res)


@pytest.mark.mathcad
def test_help_examples_recalc_clean(engine, tmp_path):
    ops = Ops(engine)
    for name in help.TOPICS:
        for j, block in enumerate(re.findall(r"```\n(.*?)\n```", help.TOPICS[name], re.S)):
            out = ops.write(str(tmp_path / f"{name}{j}.xmcd"), block)
            assert ", 0 ошибок" in out.splitlines()[0], out
            assert "⚠" not in out, out
