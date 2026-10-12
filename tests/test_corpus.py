"""Round-trip every <math> region of the Mathcad 15 sample worksheets: XML -> DSL -> XML."""
import collections
import glob
import os
import re
import xml.etree.ElementTree as ET

import pytest

from mathcad15_mcp.dsl import parse
from mathcad15_mcp.expr import Node
from mathcad15_mcp.mathml import Unsupported, _ln, from_xml, parse_math, region_dsl

MATHCAD_DIR = r"C:\Program Files (x86)\Mathcad\Mathcad 15"
NS = {"ml": "http://schemas.mathsoft.com/math30", "u": "http://schemas.mathsoft.com/units10",
      "p": "http://schemas.mathsoft.com/provenance10"}


def _maths():
    for f in glob.glob(MATHCAD_DIR + r"\**\*.xmcd", recursive=True):
        s = open(f, encoding="utf-8", errors="replace").read()
        for m in re.findall(r"<math[ >].*?</math>", s, re.S):
            head = m[:m.index(">")]
            add = "".join(f' xmlns:{k}="{v}"' for k, v in NS.items() if f"xmlns:{k}=" not in head)
            yield f, ET.fromstring("<math" + add + m[5:])


def _norm(n: Node) -> Node:
    """Drop what the DSL doesn't carry on purpose: parens, quoting, display attributes."""
    if n.kind == "parens":
        return _norm(n.args[0])
    args = [_norm(a) for a in n.args]
    a = {k: v for k, v in n.attrs.items() if k not in ("quoted", "xattrs", "oattrs")}
    if n.kind == "lam" and n.attrs.get("oattrs", {}).get("direction"):
        a["dir"] = n.attrs["oattrs"]["direction"]
    if n.kind == "op" and a["op"] == "neg" and args[0].kind in ("real", "imag") \
            and not args[0].attrs["v"].startswith("-"):
        return Node(args[0].kind, [], {**args[0].attrs, "v": "-" + args[0].attrs["v"]})
    if n.kind == "program" and len(args) == 1:
        return args[0]
    return Node(n.kind, args, a)


@pytest.mark.skipif(not os.path.isdir(MATHCAD_DIR), reason="no Mathcad 15 samples")
def test_corpus_math_round_trip():
    ok = raw = 0
    bad: collections.Counter = collections.Counter()
    first: dict[str, str] = {}
    for f, m in _maths():
        kids = [c for c in m if _ln(c)]
        try:
            orig = from_xml(kids[0]) if len(kids) == 1 else None
            d = region_dsl(orig) if orig is not None else None
        except Unsupported:
            d = None
        if d is None:
            raw += 1                    # shown as 'raw: …' — allowed, but must stay rare
            continue
        try:
            rs = parse(d)
            assert len(rs) == 1, f"{len(rs)} regions"
            same = repr(_norm(parse_math(rs[0].xml))) == repr(_norm(orig))
            key = "ok" if same else "mismatch"
        except Exception as e:
            key = f"{type(e).__name__}: {str(e).splitlines()[0][:60]}"
        if key == "ok":
            ok += 1
        else:
            bad[key] += 1
            first.setdefault(key, f"{os.path.basename(f)}: {d[:200]}")
    total = ok + raw + sum(bad.values())
    assert total > 15000, total
    assert not bad, "\n".join(f"{k} ×{n}  e.g. {first[k]}" for k, n in bad.most_common(10))
    assert ok / total >= 0.95, (ok, raw, total)
