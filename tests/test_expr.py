import pytest

from mathcad15_mcp.expr import ParseError, parse_expr


def r(text):
    return repr(parse_expr(text))


def test_implicit_mult_after_number():
    assert r("6 m") == "op{op='mult'}(real{v='6'}(), id{name='m'}())"
    assert r("12 kN/m") == ("op{op='div'}(op{op='mult'}(real{v='12'}(), id{name='kN'}()), id{name='m'}())")


def test_neg_binds_looser_than_pow():
    assert r("-x^2") == "op{op='neg'}(op{op='pow'}(id{name='x'}(), real{v='2'}()))"
    assert r("-2") == "real{v='-2'}()"
    assert r("-2^2") == "op{op='neg'}(op{op='pow'}(real{v='2'}(), real{v='2'}()))"


def test_neg_in_exponent_and_product():
    assert r("a^-1*b") == "op{op='mult'}(op{op='pow'}(id{name='a'}(), real{v='-1'}()), id{name='b'}())"
    assert r("a*-b*c") == ("op{op='mult'}(op{op='mult'}(id{name='a'}(), op{op='neg'}(id{name='b'}())), "
                           "id{name='c'}())")


def test_postfix_forms():
    assert r("A^<j>") == "op{op='matcol'}(id{name='A'}(), id{name='j'}())"
    assert r("M.max") == "id{name='M' sub='max'}()"
    assert r("v[i, j]") == "op{op='indexer'}(id{name='v'}(), seq(id{name='i'}(), id{name='j'}()))"
    assert r("transpose(A)") == "op{op='transpose'}(id{name='A'}())"
    assert r("n!") == "op{op='factorial'}(id{name='n'}())"
    assert r("x'") == "id{name=\"x'\"}()"


def test_matrix_column_major():
    n = parse_expr("[1, 2; 3, 4]")
    assert (n.attrs["rows"], n.attrs["cols"]) == (2, 2)
    assert [c.attrs["v"] for c in n.args] == ["1", "3", "2", "4"]


def test_literals():
    assert r("3i") == "imag{v='3' symbol='i'}()"
    assert r("2.5j") == "imag{v='2.5' symbol='j'}()"
    assert r('"a ""q"" b"') == "str{v='a \"q\" b'}()"
    assert r("?") == "ph()"
    assert r(".5") == "real{v='.5'}()"
    assert r("1e-3") == "real{v='1e-3'}()"


def test_lambda_ops():
    assert r("integral(x^2, x, 0, 1)") == ("lam{op='integral'}(id{name='x'}(), op{op='pow'}(id{name='x'}(), "
                                          "real{v='2'}()), real{v='0'}(), real{v='1'}())")
    assert r("deriv(f(x), x)").startswith("lam{op='derivative'}(id{name='x'}(), call(")
    n = parse_expr('limit(1/x, x, 0, "+")')
    assert n.attrs["oattrs"] == {"direction": "right"}


def test_solvers_and_funcs():
    assert r("Find(x, y)") == "op{op='Find' solver=True}(id{name='x'}(), id{name='y'}())"
    assert r("sqrt(x)") == "op{op='sqrt'}(id{name='x'}())"
    assert r("nthroot(x, 3)") == "op{op='nthRoot'}(real{v='3'}(), id{name='x'}())"
    assert r("`sqrt`(x)") == "call(id{name='sqrt' quoted=True}(), id{name='x'}())"


def test_ranges_and_sequences():
    assert r("0, 0.1..10") == "range(real{v='0'}(), real{v='0.1'}(), real{v='10'}())"
    assert r("1..5") == "range(real{v='1'}(), real{v='5'}())"
    assert r("a, b, c").startswith("seq(")


def test_comparisons_and_logic():
    assert r("x < -1") == "op{op='lessThan'}(id{name='x'}(), real{v='-1'}())"
    assert r("a = b and c ≠ d").startswith("op{op='and'}(op{op='equal'}")


def test_unicode_and_cyrillic_names():
    assert r("σ_max·Δp") == "op{op='mult'}(id{name='σ_max'}(), id{name='Δp'}())"
    assert r("длина.1") == "id{name='длина' sub='1'}()"


@pytest.mark.parametrize("text,pos", [("a + ", 4), ("(a", 2), ("a $$ b", 2), ("f(x", 3), ("[1, 2; 3]", 0)])
def test_errors_have_position(text, pos):
    with pytest.raises(ParseError) as e:
        parse_expr(text)
    assert e.value.pos == pos


def test_arity_error():
    with pytest.raises(ParseError, match="sqrt"):
        parse_expr("sqrt(a, b)")
    with pytest.raises(ParseError, match="пределы"):
        parse_expr("integral(x, x, 0)")
