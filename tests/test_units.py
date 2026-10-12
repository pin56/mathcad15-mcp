import xml.etree.ElementTree as ET

from mathcad15_mcp.units import num, result_text

ML = "http://schemas.mathsoft.com/math30"
U = "http://schemas.mathsoft.com/units10"


def res(inner: str) -> str:
    return result_text(ET.fromstring(f'<result xmlns="{ML}" xmlns:ml="{ML}">{inner}</result>'))


def united(v: str, *units) -> str:
    refs = "".join(f'<unitReference unit="{u}"' + (f' power-numerator="{p}"' if p != 1 else "") + "/>"
                   for u, p in units)
    return f'<unitedValue><ml:real>{v}</ml:real><unitMonomial xmlns="{U}">{refs}</unitMonomial></unitedValue>'


def test_num():
    assert num("54") == "54"
    assert num("0.0015") == "0.0015"
    assert num("1.5e-5") == "1.5e-5"
    assert num("123456789") == "1.23457e8"
    assert num("-2.5") == "-2.5"
    assert num("0") == "0"
    assert num("abc") == "abc"


def test_real_and_united():
    assert res("<ml:real>42</ml:real>") == "42"
    assert res(united("54", ("kilonewton", 1), ("meter", 1))) == "54 kN·m"
    assert res(united("54000", ("joule", 1))) == "54000 J"
    assert res(united("9.81", ("meter", 1), ("second", -2))) == "9.81 m/s^2"
    assert res(united("3", ("second", -1))) == "3 1/s"
    assert res(united("1", ("joule", 1), ("kilogram", -1), ("kelvin", -1))) == "1 J/(kg·K)"


def test_fractional_power():
    inner = (f'<unitedValue><ml:real>2</ml:real><unitMonomial xmlns="{U}">'
             f'<unitReference unit="meter" power-numerator="1" power-denominator="2"/></unitMonomial></unitedValue>')
    assert res(inner) == "2 m^(1/2)"


def test_complex():
    assert res("<ml:complex><ml:real>3</ml:real><ml:imag>-7</ml:imag></ml:complex>") == "3 - 7i"
    assert res("<ml:complex><ml:real>0</ml:real><ml:imag>2</ml:imag></ml:complex>") == "2i"
    assert res("<ml:complex><ml:real>1.5</ml:real><ml:imag>0</ml:imag></ml:complex>") == "1.5"


def test_matrix_column_major():
    m = '<ml:matrix rows="2" cols="2"><ml:real>1</ml:real><ml:real>3</ml:real><ml:real>2</ml:real><ml:real>4</ml:real></ml:matrix>'
    assert res(m) == "[1, 2; 3, 4]"


def test_matrix_truncated():
    cells = "".join(f"<ml:real>{i}</ml:real>" for i in range(100))
    s = res(f'<ml:matrix rows="10" cols="10">{cells}</ml:matrix>')
    assert s.endswith("(10×10)")
    assert s.count(";") == 6 and "…" in s


def test_nested_matrix_and_strings():
    inner = '<ml:matrix rows="1" cols="2"><ml:real>1</ml:real><ml:real>2</ml:real></ml:matrix>'
    m = f'<ml:matrix rows="2" cols="1">{inner}<ml:str>abc</ml:str></ml:matrix>'
    assert res(m) == '[{1×2}; "abc"]'
    assert res("<ml:id>ƒ(any1) → any1^2</ml:id>") == "ƒ(any1) → any1^2"
