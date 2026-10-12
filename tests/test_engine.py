import os

import pytest

from mathcad15_mcp import render
from mathcad15_mcp.dsl import parse
from mathcad15_mcp.engine import MathcadTimeout
from mathcad15_mcp.worksheet import Worksheet, error_names

pytestmark = pytest.mark.mathcad

SHEET = """\
# Проверка движка
L := 6 m
q := 12 kN/m
M := q*L^2/8
M = ? kN*m
σ := M/Wx
z := 3 - 7i
z = ?
A := [1, 2; 3, 4]
A^-1 = ?
"""


def write(dir_, name, text):
    ws = Worksheet.new()
    ws.replace(1, 0, parse(text))
    p = os.path.join(str(dir_), name)
    ws.save(p)
    return p


def test_calc_save_results_and_errors(engine, tmp_path):
    d = tmp_path / "лист с пробелом"
    d.mkdir()
    src = write(d, "вход.xmcd", SHEET)
    res = engine.calc_save(src, str(d / "out.xmcd"))
    ws = Worksheet.parse(res.xml)
    names = error_names(ws, res.com)
    out = render.sheet("t", ws, names)
    assert "M = ? kN*m  → 54 kN·m" in out
    assert "z = ?  → 3 - 7i" in out
    assert "A^-1 = ?  → [-2, 1; 1.5, -0.5]" in out
    assert "✗ Wx:" in out
    assert "1 ошибка" in out.splitlines()[0]


def test_export_formats(engine, tmp_path):
    src = write(tmp_path, "e.xmcd", "x := 2\ny := x^2\ny = ?")
    for fmt in ("rtf", "html", "xmcdz"):
        out = engine.export(src, str(tmp_path / f"e.{fmt}"), fmt)
        assert os.path.getsize(out) > 100


def test_export_pdf_via_word(engine, tmp_path):
    src = write(tmp_path, "p.xmcd", "# PDF\nx := 2\nx = ?")
    try:
        out = engine.export(src, str(tmp_path / "p.pdf"), "pdf")
    except Exception as e:  # no Word on this machine
        pytest.skip(str(e))
    with open(out, "rb") as fh:
        assert fh.read(5) == b"%PDF-"


def test_endless_loop_times_out(engine, tmp_path):
    engine.run(lambda app: None)
    if not engine.owned:
        pytest.skip(f"Mathcad PID {engine.pid} запущен не этим процессом — зацикливать чужой экземпляр нельзя")
    src = write(tmp_path, "loop.xmcd", "f(x) :=\n    while x > 0:\n        x <- x + 1\n    x\nf(1) = ?")
    with pytest.raises(MathcadTimeout):
        engine.calc_save(src, str(tmp_path / "loop_out.xmcd"), timeout=8)
    src2 = write(tmp_path, "after.xmcd", "x := 1\nx = ?")
    res = engine.calc_save(src2, str(tmp_path / "after_out.xmcd"))
    assert "x = ?  → 1" in render.sheet("a", Worksheet.parse(res.xml))
