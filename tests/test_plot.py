import base64
import glob
import gzip
import os
import re

import pytest

from mathcad15_mcp import plot
from mathcad15_mcp.dsl import DslError, parse, plot_dsl

MATHCAD_DIR = r"C:\Program Files (x86)\Mathcad\Mathcad 15"


def spec_of(text):
    return [r for r in parse(text) if r.kind == "plot"][0].plot


def test_template_tree_reserializes_byte_exact():
    tpl = plot.template()
    i, j = tpl.find(b"\x04tree"), tpl.find(b"\x0fd2_graph_format")
    root = plot._Reader(tpl, i + 22, tpl[i + 11], True).node(root=True)
    tree, nxt = plot._serialize(root)
    assert tpl[:i + 22] + tree + plot._esc(nxt) + b"\x00\x02\x00\x00\x00" + tpl[j:] == tpl


def test_template_reads_as_spec():
    s = plot.read_blob(plot.template())
    assert plot_dsl(s) == "plot: x=X, y=Y, ylim=-0.0005..0.0005"


@pytest.mark.parametrize("text", [
    "plot: x=t, y=sin(t), 0.5*cos(2 t)",
    "plot: x=X[i], y=X[i]^2, xlim=0..5",
    "plot: x=t, y=-t^2, (t + 1)/2, sqrt(t), M^<1>, A[i, j], f(t, 2), ylim=-1..1, xlim=?..10",
    "plot: x=x.data, y=σ·Δp, ylim=-1.5..?",
])
def test_build_read_roundtrip(text):
    s = spec_of(text)
    back = plot.read_blob(plot.build_blob(s.xs, s.ys, s.xlim, s.ylim))
    assert plot_dsl(back) == plot_dsl(s)


def test_numbers_written_in_plain_decimal():
    s = spec_of("plot: x=t, y=1e-3*t, ylim=-2E2..1e+3")
    back = plot.read_blob(plot.build_blob(s.xs, s.ys, s.xlim, s.ylim))
    assert plot_dsl(back) == "plot: x=t, y=0.001 t, ylim=-200..1000"


def test_unsupported_expression_is_dsl_error():
    with pytest.raises(DslError) as e:
        parse("plot: x=t, y=abs(t)")
    assert "не поддержано" in str(e.value)


def test_garbage_blob_reads_as_none():
    assert plot.read_blob(b"") is None
    assert plot.read_blob(b"\x04tree" + b"\xff" * 40) is None


def _corpus_plots():
    for f in glob.glob(MATHCAD_DIR + r"\**\*.xmcd", recursive=True):
        s = open(f, encoding="utf-8", errors="replace").read()
        items = dict(re.findall(r'<item item-id="(\d+)"[^>]*>(.*?)</item>', s, re.S))
        for iid in re.findall(r'<plot [^>]*item-idref="(\d+)"', s):
            if iid in items:
                yield gzip.decompress(base64.b64decode(items[iid]))


@pytest.mark.skipif(not os.path.isdir(MATHCAD_DIR), reason="no Mathcad 15 samples")
def test_corpus_plots_read_and_rebuild():
    total = readable = 0
    for blob in _corpus_plots():
        total += 1
        s = plot.read_blob(blob)
        if s is None:
            continue
        readable += 1
        try:
            b1 = plot.build_blob(s.xs, s.ys, s.xlim, s.ylim)
        except plot.PlotError:
            continue
        back = plot.read_blob(b1)
        assert back is not None and plot.build_blob(back.xs, back.ys, back.xlim, back.ylim) == b1
    assert total > 500 and readable / total > 0.75, (readable, total)
