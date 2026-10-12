import re

import pytest

from mathcad15_mcp import help
from mathcad15_mcp.dsl import parse

BLOCKS = [(name, b) for name, t in {"overview": help.OVERVIEW, **help.TOPICS}.items()
          for b in re.findall(r"```\n(.*?)\n```", t, re.S)]


def test_every_topic_has_dsl():
    assert {n for n, _ in BLOCKS} == {"overview", *help.TOPICS}
    assert set(help.TOPICS) == {"syntax", "units", "program", "solve", "plot", "symbolic", "examples"}


@pytest.mark.parametrize("name,block", BLOCKS, ids=[n for n, _ in BLOCKS])
def test_blocks_parse(name, block):
    assert parse(block)


def test_topic_lookup():
    assert help.text() == help.OVERVIEW
    assert help.text(" Units ") == help.TOPICS["units"]
    out = help.text("графики")
    assert out.startswith("нет темы «графики»") and "plot" in out
