import os
import winreg

import pytest


def _has_mathcad() -> bool:
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Mathcad.Application"))
        return True
    except OSError:
        return False


def pytest_collection_modifyitems(config, items):
    if _has_mathcad() and not os.environ.get("MC15_SKIP_COM"):
        return
    skip = pytest.mark.skip(reason="нужен установленный Mathcad 15 (COM Mathcad.Application)")
    for item in items:
        if "mathcad" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def engine():
    from mathcad15_mcp.engine import Engine
    eng = Engine(timeout=120)
    yield eng
    eng.close()
