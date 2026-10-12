from mathcad15_mcp import render
from mathcad15_mcp.dsl import parse
from mathcad15_mcp.worksheet import Worksheet

ML = 'xmlns:ml="http://schemas.mathsoft.com/math30"'
HEAD = ('<region region-id="{id}" left="18" top="{top}" width="60" height="12.75" align-x="30" align-y="{ay}" '
        'show-border="false" show-highlight="false" is-protected="true" z-order="0" background-color="inherit" tag="">')


def reg(rid, top, inner):
    return HEAD.format(id=rid, top=top, ay=top + 9) + inner + "</region>"


def sheet_of(*regions) -> Worksheet:
    xml = Worksheet.new().to_xml().replace("<regions>", "<regions>" + "".join(regions), 1)
    return Worksheet.parse(xml)


M_EVAL = reg(1, 20, f'<math optimize="false" disable-calc="false"><ml:eval {ML}><ml:id xml:space="preserve">M</ml:id>'
                    '<ml:unitOverride><ml:apply><ml:mult/><ml:id>kN</ml:id><ml:id>m</ml:id></ml:apply></ml:unitOverride>'
                    '<result xmlns="http://schemas.mathsoft.com/math30"><unitedValue><ml:real>54</ml:real>'
                    '<unitMonomial xmlns="http://schemas.mathsoft.com/units10"><unitReference unit="kilonewton"/>'
                    '<unitReference unit="meter"/></unitMonomial></unitedValue></result></ml:eval></math>'
                    '<rendering item-idref="5"/>')
S_ERR = reg(2, 50, f'<math error="Эта переменная не определена." optimize="false" disable-calc="false">'
                   f'<ml:define {ML}><ml:id>σ</ml:id><ml:apply><ml:div/><ml:id>M</ml:id><ml:id>Wx</ml:id></ml:apply>'
                   '</ml:define></math>')
L_WARN = reg(3, 80, f'<math optimize="false" disable-calc="false"><ml:define warning="WarnRedefinedBIUnit" {ML}>'
                    '<ml:id>L</ml:id><ml:real>6</ml:real></ml:define></math>')
SYM = reg(4, 110, f'<math optimize="false" disable-calc="false"><ml:symEval {ML}><ml:apply><ml:minus/><ml:apply><ml:pow/>'
                  '<ml:id>x</ml:id><ml:real>2</ml:real></ml:apply><ml:real>1</ml:real></ml:apply><ml:command><ml:id>factor</ml:id>'
                  '</ml:command><ml:symResult><ml:apply><ml:mult/><ml:parens><ml:apply><ml:minus/><ml:id>x</ml:id><ml:real>1</ml:real>'
                  '</ml:apply></ml:parens><ml:parens><ml:apply><ml:plus/><ml:id>x</ml:id><ml:real>1</ml:real></ml:apply>'
                  '</ml:parens></ml:apply></ml:symResult></ml:symEval></math>')
INLINE = reg(5, 140, '<text use-page-width="false" push-down="false" lock-width="false"><p style="Normal">Шаг '
                     + HEAD.format(id=51, top=140, ay=149) +
                     f'<math optimize="false" disable-calc="false"><ml:eval {ML}><ml:id>n</ml:id>'
                     '<result xmlns="http://schemas.mathsoft.com/math30"><ml:real>8</ml:real></result></ml:eval></math>'
                     '<rendering item-idref="6"/></region> точек</p></text>')
AREA = ('<region region-id="6" left="0" top="170" width="600" height="60" align-x="0" align-y="170" '
        'show-border="false" show-highlight="false" is-protected="true" z-order="0" background-color="inherit" tag="">'
        '<area is-collapsed="true" name="Константы" top-lock-id="2" bottom-lock-id="3">'
        + reg(61, 20, f'<math><ml:define {ML}><ml:id>g</ml:id><ml:real>9.81</ml:real></ml:define></math>')
        + reg(62, 40, f'<math error="Деление на ноль."><ml:define {ML}><ml:id>k</ml:id><ml:apply><ml:div/><ml:real>1</ml:real>'
                      '<ml:real>0</ml:real></ml:apply></ml:define></math>')
        + '</area></region>')


def test_listing():
    ws = sheet_of(M_EVAL, S_ERR, L_WARN, SYM, INLINE, AREA)
    out = render.sheet("балка.xmcd", ws, {2: "bad_variable\tdoc_mc_Wx\t"})
    lines = out.splitlines()
    assert lines[0] == "балка.xmcd — 6 регионов, 2 ошибки"
    assert lines[1] == "[1] M = ? kN*m  → 54 kN·m"
    assert lines[2] == "[2] σ := M/Wx  ✗ Wx: Эта переменная не определена"
    assert lines[3] == "[3] L := 6  ⚠ L: переопределена встроенная единица"
    assert lines[4] == "[4] x^2 - 1 | factor -> ?  → (x - 1)*(x + 1)"
    assert lines[5] == "[5] text: Шаг {n = ? → 8} точек"
    assert lines[6] == "[6] area «Константы» (свёрнута)"
    assert lines[7] == "    · g := 9.81"
    assert lines[8] == "    · k := 1/0  ✗ Деление на ноль"


def test_filters():
    ws = sheet_of(M_EVAL, S_ERR, L_WARN)
    out = render.sheet("x", ws, errors_only=True).splitlines()
    assert out[1:] == ["[2] σ := M/Wx  ✗ Эта переменная не определена"]
    out = render.sheet("x", ws, only={1, 3}, note="без пересчёта").splitlines()
    assert out[0] == "x — 3 региона, 1 ошибка (без пересчёта)"
    assert [ln[:3] for ln in out[1:]] == ["[1]", "[3]"]


def test_built_sheet_renders_its_dsl():
    text = "# Заголовок\nL := 6 m\nf(x) := x^2\nM = ? kN*m\nplot: x=t, y=sin(t), t=0..10"
    ws = Worksheet.new()
    ws.replace(1, 0, parse(text))
    body = [ln.split("] ", 1)[1] for ln in render.sheet("n", ws).splitlines()[1:]]
    assert body == ["# Заголовок", "L := 6 m", "f(x) := x^2", "M = ? kN*m", "t := 0, 0.05..10",
                    "plot: x=t, y=sin(t)"]


def test_program_continuation_indent():
    ws = Worksheet.new()
    ws.replace(1, 0, parse("f(x) :=\n    if x > 0:\n        x\n    else:\n        -x"))
    out = render.sheet("p", ws).splitlines()
    assert out[1] == "[1] f(x) :="
    assert out[2].startswith("    ") and "if x > 0" in out[2]


def test_old_namespaces():
    xml = Worksheet.new().to_xml()
    xml = xml.replace("worksheet30", "worksheet10").replace("math30", "math10")
    xml = xml.replace("<regions>", "<regions>" + S_ERR.replace("math30", "math10"), 1)
    out = render.sheet("old", Worksheet.parse(xml), {2: "bad_variable\tdoc_mc_Wx"})
    assert out.splitlines()[1] == "[1] σ := M/Wx  ✗ Wx: Эта переменная не определена"


def test_error_codes_and_plural():
    assert render.error_text("bad_variable", None) == "переменная не определена"
    assert render.error_text("bad_dimensions Length Unitless ", None) == "несовместимые единицы: Length вместо Unitless"
    assert render.error_text(None, "divide by zero") == "деление на ноль"
    assert render.error_text("This value has units: Length, \nbut must have units: Unitless.", None) == \
        "This value has units: Length, but must have units: Unitless"
    assert render.com_name("bad_variable\tmc_Index\t\tmc_Index\tvalue_zone") == "Index"
    assert render.com_name("bad_variable\tdoc_mc_σ_2E_adm\t") == "σ.adm"
    assert render.com_name("bad_variable\tdoc_mc_x_2E_y_5F_z\t") == "x.y_z"
    assert render.com_name("bad_variable\tdoc_mc_A_27_\t") == "A'"
    assert [render.plural(n, "ошибка", "ошибки", "ошибок") for n in (1, 2, 5, 11, 21, 112)] == \
        ["1 ошибка", "2 ошибки", "5 ошибок", "11 ошибок", "21 ошибка", "112 ошибок"]
