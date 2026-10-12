import xml.etree.ElementTree as ET

import pytest

from mathcad15_mcp.dsl import DslError, parse, text_dsl
from mathcad15_mcp.mathml import parse_math, region_dsl

SHEET = """\
# Расчёт балки
text: Однопролётная балка, равномерная нагрузка.
L := 6 m          // пролёт
q := 12 kN/m
g :== 9.81 m/s^2
M := q*L^2/8
M = ? kN*m
f(x, y) := x*y
e := x^2 - 1 | factor -> ?
x^3 - 2 | solve, x | float, 5 -> ?
l := length(v) = ?
"""


def kinds(rs):
    return [r.kind for r in rs]


def roundtrip(r):
    return region_dsl(parse_math(r.xml))


def test_basic_sheet():
    rs = parse(SHEET)
    assert kinds(rs) == ["text", "text"] + ["math"] * 9
    assert [r.line for r in rs] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    assert 'style="Heading 1"' in rs[0].xml and "Расчёт балки" in rs[0].xml
    assert [r.dsl for r in rs[2:]] == [
        "L := 6 m", "q := 12 kN/m", "g :== 9.81 m/s^2", "M := q*L^2/8", "M = ? kN*m", "f(x, y) := x*y",
        "e := x^2 - 1 | factor -> ?", "x^3 - 2 | solve, x | float, 5 -> ?", "l := length(v) = ?"]
    for r in rs[2:]:
        assert roundtrip(r) == r.dsl


def test_xml_shapes():
    rs = parse("M = ? kN*m\nf(x) := x\ng :== 1")
    assert rs[0].xml.startswith("<ml:eval>") and "<ml:unitOverride>" in rs[0].xml
    assert rs[1].xml.startswith("<ml:define><ml:function>")
    assert rs[2].xml.startswith("<ml:globalDefine>")


def test_given_block():
    rs = parse("x := 1\nGiven\n    x^2 + y^2 = 6\n    x = y\nsol := Find(x, y)\nsol = ?")
    assert [r.dsl for r in rs] == ["x := 1", "Given", "x^2 + y^2 = 6", "x = y", "sol := Find(x, y)", "sol = ?"]
    assert rs[1].xml == '<ml:id xml:space="preserve">Given</ml:id>'
    assert "<ml:equal/>" in rs[2].xml


PROG = """\
f(v) :=
    s <- 0
    for i in 0..last(v):
        if v[i] < 0: continue
        s <- s + v[i]
    while s > 100:
        s <- s/2
    try:
        s <- 1/s
    on error:
        return 0
    s
"""


def test_program():
    rs = parse(PROG)
    assert len(rs) == 1
    x = rs[0].xml
    for tag in ("<ml:program>", "<ml:localDefine>", "<ml:for>", "<ml:ifThen>", "<ml:continue/>",
                "<ml:while>", "<ml:tryCatch>", "<ml:return>"):
        assert tag in x, tag
    back = roundtrip(rs[0])
    assert parse(back)[0].xml == x
    assert back.startswith("f(v) :=\n    s <- 0\n    for i in 0..last(v):\n        if v[i] < 0:\n            continue")


def test_program_eval_and_else():
    rs = parse("program = ?\n    if a > 0:\n        1\n    else:\n        -1")
    assert rs[0].xml.startswith("<ml:eval><ml:program><ml:ifThen>")
    assert "<ml:otherwise>" in rs[0].xml
    assert rs[0].dsl.splitlines()[0] == "program = ?"


def test_single_statement_body_is_unwrapped():
    rs = parse("g(x) :=\n    for i in 1..3:\n        x <- x*i")
    assert rs[0].xml.count("<ml:program>") == 0
    assert "<ml:for>" in rs[0].xml


def test_plot_with_range_variable():
    rs = parse("plot: x=t, y=sin(t), 0.5*cos(2 t), t=0..10, ylim=-1..1")
    assert kinds(rs) == ["math", "plot"]
    assert rs[0].dsl == "t := 0, 0.05..10"
    p = rs[1].plot
    assert len(p.xs) == 1 and len(p.ys) == 2 and p.xlim is None
    assert rs[1].dsl == "plot: x=t, y=sin(t), 0.5*cos(2 t), ylim=-1..1"


def test_plot_symbolic_step():
    rs = parse("plot: x=t, y=t, t=0..L")
    assert rs[0].dsl == "t := 0, 0 + (L - 0)/200..L"


def test_raw_math_and_region():
    rs = parse('raw: <ml:id xml:space="preserve">a</ml:id>\n'
               'raw: <region region-id="1" left="1" top="1"><math><ml:id>b</ml:id></math></region>')
    assert kinds(rs) == ["math", "raw"]
    assert rs[0].xml == '<ml:id xml:space="preserve">a</ml:id>'


def test_text_continuation():
    rs = parse("text: первая часть\n    вторая часть")
    assert rs[0].dsl == "text: первая часть вторая часть"


def test_text_dsl_from_xml():
    e = ET.fromstring('<text xmlns="http://schemas.mathsoft.com/worksheet30"><p style="Heading 2">A  b</p>'
                      '<p style="Normal">c</p></text>')
    assert text_dsl(e) == "## A b\ntext: c"


def test_multiline_matrix_continuation():
    rs = parse("A := [1, 2;\n      3, 4]")
    assert rs[0].dsl == "A := [1, 2; 3, 4]"


@pytest.mark.parametrize("text,line,frag", [
    ("x := 1\ny := (2 + \n", 2, ""),
    ("a := 1\nb := 2 +", 2, "ожидалось"),
    ("x <- 1", 1, "<-"),
    ("for i in 1..3:\n    x", 1, "внутри программы"),
    ("f(x) :=", 1, "пустая"),
    ("a := 1\n    b := 2", 2, "отступ"),
    ("e | factor -> 5", 1, "->"),
    ("plot: y=sin(t)", 1, "x="),
    ("raw: <ml:id>a", 1, "XML"),
    ("f(x) :=\n    try:\n        1", 2, "on error"),
])
def test_errors(text, line, frag):
    with pytest.raises(DslError) as e:
        parse(text)
    assert e.value.line == line
    assert frag in str(e.value)
