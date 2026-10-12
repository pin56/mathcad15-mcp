import pytest

from mathcad15_mcp.expr import parse_expr
from mathcad15_mcp.mathml import ML, Unsupported, parse_math, region_dsl, to_dsl, to_xml


def xml(text):
    return to_xml(parse_expr(text), True)


def test_to_xml_basic():
    assert xml("6 m") == ('<ml:apply><ml:mult/><ml:real>6</ml:real>'
                          '<ml:id xml:space="preserve">m</ml:id></ml:apply>')
    assert xml("M.max") == '<ml:id xml:space="preserve" subscript="max">M</ml:id>'
    assert xml("3i") == '<ml:imag symbol="i">3</ml:imag>'
    assert xml('"a<b"') == '<ml:str xml:space="preserve">a&lt;b</ml:str>'


def test_parens_dropped_in_div_kept_in_mult():
    assert "<ml:parens>" not in xml("(a + b)/(c + d)")
    assert "<ml:parens>" in xml("(a + b)*c")
    assert xml("(a + b)^(2)").count("<ml:parens>") == 1


def test_matrix_and_range():
    assert xml("[1, 2; 3, 4]").startswith('<ml:matrix rows="2" cols="2"><ml:real>1</ml:real><ml:real>3</ml:real>')
    assert xml("0, 0.1..1") == ("<ml:range><ml:sequence><ml:real>0</ml:real><ml:real>0.1</ml:real></ml:sequence>"
                                "<ml:real>1</ml:real></ml:range>")


def test_call_with_sequence():
    assert xml("f(a, b)") == ('<ml:apply><ml:id xml:space="preserve">f</ml:id><ml:sequence>'
                              '<ml:id xml:space="preserve">a</ml:id><ml:id xml:space="preserve">b</ml:id>'
                              '</ml:sequence></ml:apply>')


def test_integral_xml():
    s = xml("integral(x^2, x, 0, 1)")
    assert s.startswith('<ml:apply><ml:integral auto-algorithm="true"/><ml:lambda><ml:boundVars>')
    assert "<ml:bounds><ml:real>0</ml:real><ml:real>1</ml:real></ml:bounds>" in s


def test_solver_attrs():
    assert xml("Find(x)").startswith('<ml:apply><ml:Find auto-method="true" method="levenberg"')


ROUND = [
    "6 m", "-x^2", "a^-1*b", "a*-b*c", "A^<j>", "M.max", "v[i, j]", "transpose(A)", "n!", "[1, 2; 3, 4]",
    "3i", '"say ""hi"""', "integral(x^2, x, 0, 1)", "Find(x, y)", "sqrt(x)", "nthroot(x, 3)",
    "0, 0.1..10", "1..n", "a - (b - c)", "(a - b) - c", "a/(b*c)", "(a^b)^c", "a^b^c", "f(x)(y)",
    "12 kN/m", "x < -1", "a = b and c != d", "not(a) or b", "deriv(f(x), x, 2)", 'limit(1/x, x, 0, "-")',
    "sum(i^2, i, 1, n)", "prod(k, k)", "`sqrt`(x)", "`T+`", "x.`a b`", "σ·Δp", "infix(`∠`, a, b)",
    "(-a)^2", "-(a + b)", "2^-x", "-a*b", "?", "polyroots(v)", "abs(x - 1)", "M[0, 1]",
]


@pytest.mark.parametrize("text", ROUND)
def test_dsl_roundtrip_through_xml(text):
    n = parse_expr(text)
    x = to_xml(n, True)
    back = parse_math(x)
    s = to_dsl(back, True)
    assert to_xml(parse_expr(s), True) == x, s


def test_from_xml_unwraps_provenance_and_ignores_result():
    frag = (f'<ml:eval xmlns:ml="{ML}"><ml:provenance><ml:id>x</ml:id></ml:provenance>'
            f'<result xmlns="{ML}"><ml:real>5</ml:real></result></ml:eval>')
    assert region_dsl(parse_math(frag)) == "x = ?"


def test_math20_namespace_accepted():
    frag = '<ml:apply xmlns:ml="http://schemas.mathsoft.com/math20"><ml:plus/><ml:id>a</ml:id><ml:real>1</ml:real></ml:apply>'
    assert to_dsl(parse_math(frag)) == "a + 1"


def test_unsupported_raises():
    with pytest.raises(Unsupported):
        parse_math(f'<ml:mixed xmlns:ml="{ML}"><ml:real>1</ml:real></ml:mixed>')


def test_program_region_dsl():
    frag = (f'<ml:define xmlns:ml="{ML}"><ml:function><ml:id>f</ml:id><ml:boundVars><ml:id>n</ml:id>'
            '</ml:boundVars></ml:function><ml:program><ml:localDefine><ml:id>s</ml:id><ml:real>0</ml:real>'
            '</ml:localDefine><ml:for><ml:id>i</ml:id><ml:range><ml:real>1</ml:real><ml:id>n</ml:id></ml:range>'
            '<ml:localDefine><ml:id>s</ml:id><ml:apply><ml:plus/><ml:id>s</ml:id><ml:id>i</ml:id></ml:apply>'
            '</ml:localDefine></ml:for><ml:id>s</ml:id></ml:program></ml:define>')
    assert region_dsl(parse_math(frag)) == "f(n) :=\n    s <- 0\n    for i in 1..n:\n        s <- s + i\n    s"


def test_symeval_region_dsl():
    frag = (f'<ml:symEval xmlns:ml="{ML}"><ml:id>e</ml:id><ml:command><ml:sequence><ml:id>solve</ml:id>'
            '<ml:id>x</ml:id></ml:sequence></ml:command><ml:command><ml:id>float</ml:id></ml:command>'
            '<ml:symResult><ml:real>1</ml:real></ml:symResult></ml:symEval>')
    assert region_dsl(parse_math(frag)) == "e | solve, x | float -> ?"
