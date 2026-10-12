"""stdio MCP server: six tools over Ops, compact text in and out."""
from __future__ import annotations

import logging
import traceback
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import help as dsl_help
from .engine import EngineError
from .ops import OpError, Ops

INSTRUCTIONS = """\
Mathcad 15: листы .xmcd пишутся и читаются на компактном DSL — перед первым mc_write прочтите mc_help().
Каждый ответ — лист текстом: [n] регион DSL, → результат, ✗ ошибка, ⚠ предупреждение.
Новый лист — mc_write; готовый — mc_read; правка регионов по номерам [n] — mc_edit;
подставить исходные данные и получить значения — mc_calc (файл не меняется без save=true);
pdf/rtf/html — mc_export. Пути — абсолютные."""

log = logging.getLogger("mathcad15_mcp")
ops = Ops()
mcp = MCPServer("mathcad15", instructions=INSTRUCTIONS, log_level="WARNING")

Path = Annotated[str, Field(description="Абсолютный путь к .xmcd")]


def _run(fn, *args, **kw) -> str:
    try:
        return fn(*args, **kw)
    except (OpError, EngineError) as e:
        raise ToolError(str(e)) from None
    except Exception as e:  # noqa: BLE001 - the model gets the text, the log gets the traceback
        log.error("%s", traceback.format_exc())
        raise ToolError(f"внутренняя ошибка сервера: {type(e).__name__}: {e}") from None


def _ro(title: str) -> ToolAnnotations:
    return ToolAnnotations(title=title, read_only_hint=True, open_world_hint=False)


def _rw(title: str, destructive: bool) -> ToolAnnotations:
    return ToolAnnotations(title=title, read_only_hint=False, destructive_hint=destructive, open_world_hint=False)


@mcp.tool("mc_read", structured_output=False, annotations=_ro("Прочитать лист"),
          description="Открыть лист (.xmcd, .mcd), пересчитать и показать регионы [n] как DSL "
                      "с результатами и ошибками.")
def mc_read(path: Path,
            regions: Annotated[str, Field(description='Только эти номера: "3-8", "1,4,7-9"')] = "",
            errors_only: Annotated[bool, Field(description="Только регионы с ошибками")] = False) -> str:
    return _run(ops.read, path, regions, errors_only)


@mcp.tool("mc_write", structured_output=False, annotations=_rw("Создать лист", True),
          description="Создать .xmcd из DSL (синтаксис: mc_help) и пересчитать; ответ как у mc_read.")
def mc_write(path: Path,
             text: Annotated[str, Field(description="Лист на DSL, одна инструкция — один регион")],
             overwrite: Annotated[bool, Field(description="Заменить существующий файл")] = False) -> str:
    return _run(ops.write, path, text, overwrite)


@mcp.tool("mc_edit", structured_output=False, annotations=_rw("Править регионы", True),
          description="Заменить регионы [start..end] регионами из DSL text и пересчитать. "
                      "Вставка перед start: end = start-1 (в конец: start = последний+1). "
                      "Пустой text — удалить. Остальные регионы не меняются, нижние сдвигаются.")
def mc_edit(path: Path,
            start: Annotated[int, Field(description="Первый заменяемый регион [n]")],
            end: Annotated[int, Field(description="Последний заменяемый регион [n], включительно")],
            text: Annotated[str, Field(description="Новые регионы на DSL")] = "") -> str:
    return _run(ops.edit, path, start, end, text)


@mcp.tool("mc_calc", structured_output=False, annotations=_rw("Пересчитать с данными", False),
          description="Подставить исходные данные, пересчитать и вернуть значения и все ошибки. "
                      "set меняет правую часть первого определения имени. Файл не меняется без save.")
def mc_calc(path: Path,
            set: Annotated[dict,
                           Field(description='{"L": "7.5 m", "f(x)": "x^3"} — имя: новое выражение DSL')] = None,
            get: Annotated[list[str],
                           Field(description='Что вычислить: ["M", "σ = ? MPa"]')] = None,
            save: Annotated[bool, Field(description="Записать изменения set в файл")] = False) -> str:
    return _run(ops.calc, path, values=set, get=get, save=save)


@mcp.tool("mc_export", structured_output=False, annotations=_rw("Экспорт", False),
          description="Пересчитать и сохранить лист как pdf (через Word), rtf, html, xmcd или xmcdz.")
def mc_export(path: Path,
              out: Annotated[str, Field(description="Файл результата; формат по расширению")] = "",
              format: Annotated[str, Field(description="pdf, rtf, html, xmcd, xmcdz — если out без "
                                                       "расширения или не задан (тогда рядом с path)")] = "") -> str:
    return _run(ops.export, path, out, format)


@mcp.tool("mc_help", structured_output=False, annotations=_ro("Справка по DSL"),
          description="Справка по DSL листов: без темы — обзор; темы syntax, units, program, solve, plot, "
                      "symbolic, examples.")
def mc_help(topic: Annotated[str, Field(description="Тема")] = "") -> str:
    return dsl_help.text(topic)


def _compact_schemas():
    """Drop pydantic's auto titles from the input schemas: fewer tokens for small local models."""
    def strip(node):
        if isinstance(node, dict):
            node.pop("title", None)
            for k, v in node.items():
                strip(v) if k != "properties" else [strip(p) for p in v.values()]
        elif isinstance(node, list):
            for v in node:
                strip(v)
    try:
        tools = mcp._tool_manager.list_tools()
    except AttributeError:  # private API moved: keep the titles
        return
    for t in tools:
        strip(t.parameters)


_compact_schemas()


def main():
    try:
        mcp.run("stdio")
    finally:
        ops.engine.close()
