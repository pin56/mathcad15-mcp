import os
import sys

import anyio
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
TOOLS = {"mc_read", "mc_write", "mc_edit", "mc_calc", "mc_export", "mc_help"}


def test_stdio_initialize_list_call(tmp_path):
    env = {**os.environ, "PYTHONPATH": SRC, "PYTHONIOENCODING": "utf-8"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "mathcad15_mcp"], env=env)

    async def go():
        async with Client(params) as c:
            assert "mc_help" in (c.instructions or "")
            tools = (await c.list_tools()).tools
            assert {t.name for t in tools} == TOOLS
            assert all(t.output_schema is None and t.description for t in tools)
            r = await c.call_tool("mc_help", {"topic": "plot"})
            assert not r.is_error and "plot: x=t" in r.content[0].text
            r = await c.call_tool("mc_read", {"path": str(tmp_path / "нет такого.xmcd")})
            assert r.is_error and "файл не найден" in r.content[0].text
            r = await c.call_tool("mc_write", {"path": str(tmp_path / "x.xmcd"), "text": "x := (1 +"})
            assert r.is_error and "ошибка DSL" in r.content[0].text and "строка 1" in r.content[0].text
            r = await c.call_tool("mc_edit", {"path": str(tmp_path / "x.xmcd")})
            assert r.is_error  # start/end are required

    anyio.run(go)


def test_schemas_stay_small():
    from mathcad15_mcp.server import mcp

    async def go():
        async with Client(mcp) as c:
            return (await c.list_tools()).tools

    tools = anyio.run(go)
    text = "".join(str(t.input_schema) + t.description for t in tools)
    assert "'title'" not in text and "anyOf" not in text
    assert len(text) < 3500
