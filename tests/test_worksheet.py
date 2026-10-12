import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from mathcad15_mcp.dsl import parse
from mathcad15_mcp.worksheet import GAP, Worksheet, WorksheetError, error_names, rows

SHEET = """\
# Расчёт балки
text: Однопролётная балка.
L := 6 m
q := 12 kN/m
M := q*L^2/8
M = ? kN*m
plot: x=t, y=sin(t), t=0..10
"""

PICTURE = ('<region region-id="900" left="300" top="40" width="50" height="50" align-x="300" align-y="40" '
           'show-border="false" show-highlight="false" is-protected="true" z-order="0" background-color="inherit" '
           'tag="keep-me"><picture><png pixel-width="1" pixel-height="1" item-idref="77"/></picture></region>')


def built(text=SHEET) -> Worksheet:
    ws = Worksheet.new()
    ws.replace(1, 0, parse(text))
    return ws


def no_overlap(ws):
    col = [r for r in ws.regions if r.left < 200]
    for a, b in zip(col, col[1:]):
        assert b.top >= a.bottom + GAP - 0.01, (a.xml[:80], b.xml[:80])


def test_new_is_empty_and_valid():
    ws = Worksheet.new()
    assert ws.regions == []
    ET.fromstring(ws.to_xml().encode("utf-8"))


def test_build_from_dsl_is_valid_xml():
    ws = built()
    kinds = [r.kind for r in ws.regions]
    assert kinds == ["text", "text", "math", "math", "math", "math", "math", "plot"]
    root = ET.fromstring(ws.to_xml().encode("utf-8"))
    assert root.tag.endswith("worksheet")
    ids = [r.id for r in ws.regions]
    assert len(set(ids)) == len(ids)
    no_overlap(ws)


def test_roundtrip_text_is_stable():
    xml = built().to_xml()
    assert Worksheet.parse(xml).to_xml() == xml


def test_plot_item_stored():
    ws = Worksheet.parse(built().to_xml())
    plot = ws.regions[-1]
    assert plot.kind == "plot"
    iid = int(ET.fromstring(plot.xml.replace("<region ", "<region xmlns='x' ", 1))[0].get("item-idref"))
    assert iid in ws.items()
    assert b"tree" in ws.items()[iid]


def test_digest_is_dropped():
    xml = built().to_xml().replace("<worksheet", '<?validation-md5-digest abc?>\n<worksheet', 1)
    assert "validation-md5-digest" not in Worksheet.parse(xml).to_xml()


def test_replace_keeps_foreign_xml():
    ws = built()
    xml = ws.to_xml().replace("<regions>", "<regions>" + PICTURE, 1)
    ws = Worksheet.parse(xml)
    before = {r.id: r.xml[r.xml.index(">"):] for r in ws.regions}
    n = len(ws.regions)
    m_idx = next(i for i, r in enumerate(ws.regions, 1) if r.kind == "math")
    ws.replace(m_idx, m_idx, parse("L := 7.5 m"))
    after = {r.id: r.xml[r.xml.index(">"):] for r in ws.regions}
    assert len(ws.regions) == n
    assert after[900] == before[900]
    pic = ws.by_id()[900]
    assert pic.a["tag"] == "keep-me" and pic.left == 300
    changed = [k for k in before if k in after and before[k] != after[k]]
    assert changed == []
    assert any("7.5" in r.xml for r in ws.regions)


def test_insert_shifts_below():
    ws = built()
    third = ws.regions[2]
    t0 = third.top
    ws.replace(3, 2, parse("a := 1\nb := 2"))
    assert ws.regions[2].xml.count("<ml:id") and "a" in ws.regions[2].xml
    assert ws.by_id()[third.id].top > t0
    no_overlap(ws)


def test_delete_pulls_up():
    ws = built()
    gone, nxt = ws.regions[2], ws.regions[3]
    t0 = nxt.top
    ws.replace(3, 3, [])
    assert gone.id not in ws.by_id()
    assert abs(ws.by_id()[nxt.id].top - (t0 - gone.height - GAP)) < 0.01


def test_append_at_end():
    ws = built()
    n, last = len(ws.regions), ws.regions[-1]
    ws.replace(n + 1, n, parse("x := 1"))
    assert len(ws.regions) == n + 1
    assert ws.regions[-1].top >= last.bottom + GAP - 0.01


def test_bad_range():
    ws = built()
    with pytest.raises(WorksheetError):
        ws.replace(0, 1, [])
    with pytest.raises(WorksheetError):
        ws.replace(3, len(ws.regions) + 1, [])


def test_plan_replays_with_measured_sizes():
    ws = built()
    plan = ws.replace(3, 4, parse("A := [1, 2; 3, 4]\nB := A^-1"))
    saved = Worksheet.parse(ws.to_xml())
    for rid in plan.new_ids:            # Mathcad measured taller regions than estimated
        r = saved.by_id()[rid]
        r.set(height=r.height + 20.0)
    saved.apply(plan)
    no_overlap(saved)


def test_rows_side_by_side():
    ws = built("a := 1\nb := 2")
    a, b = ws.regions
    b.set(top=a.top, align_y=a.align_y, left=200.0)
    assert [[r.id for r in row] for row in rows(ws.regions)] == [[a.id, b.id]]


def test_load_paths(tmp_path):
    d = tmp_path / "папка с пробелом"
    d.mkdir()
    p = d / "лист σ.xmcd"
    built().save(str(p))
    assert len(Worksheet.load(str(p)).regions) == 8
    bad = d / "bad.xmcd"
    bad.write_bytes(b"\xd0\xcf\x11\xe0binary")
    with pytest.raises(WorksheetError):
        Worksheet.load(str(bad))
    with pytest.raises(WorksheetError):
        Worksheet.parse("<html/>")


def test_error_names_by_position_and_order():
    ws = built("a := 1/0\nb := c\nd := 1")
    a, b, _ = ws.regions
    for r, err in ((a, "Деление на ноль."), (b, "Эта переменная не определена.")):
        r.xml = r.xml.replace("<math ", f'<math error="{err}" ', 1)
    com = [SimpleNamespace(x_pt=b.left, y_pt=b.top, error="bad_variable\tdoc_mc_c\t"),
           SimpleNamespace(x_pt=500.0, y_pt=500.0, error="divide by zero"),
           SimpleNamespace(x_pt=0.0, y_pt=0.0, error="")]
    assert error_names(ws, com) == {b.id: "bad_variable\tdoc_mc_c\t", a.id: "divide by zero"}
