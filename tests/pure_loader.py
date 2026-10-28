"""Load pure modules of the integration without homeassistant.

`logic.py` loads directly (no imports); `rooms.py` and `rooms_logic.py`
import the package constants with a relative import, which needs a
package context: stub parents are registered in sys.modules and removed
again, so the real package (imported by the controller tests with
homeassistant installed) is unaffected.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

BASE = Path(__file__).parent.parent / "custom_components" / "heat_controller"

_STUBS: list[str] = []


def load_pure(name: str):
    """Load `custom_components/heat_controller/<name>.py` standalone."""
    for parent, path in (
        ("custom_components", BASE.parent),
        ("custom_components.heat_controller", BASE),
    ):
        if parent in sys.modules:
            continue
        stub = types.ModuleType(parent)
        stub.__path__ = [str(path)]  # type: ignore[attr-defined]
        sys.modules[parent] = stub
        _STUBS.append(parent)

    if f"custom_components.heat_controller.const" not in sys.modules:
        _load(f"custom_components.heat_controller.const", BASE / "const.py")
    return _load(
        f"custom_components.heat_controller.{name}",
        BASE / f"{name}.py",
        package="custom_components.heat_controller",
    )


def _load(name: str, path: Path, package: str | None = None):
    if name in sys.modules:  # pragma: no cover - reused module
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    _STUBS.append(name)
    if package is not None:
        module.__package__ = package
    spec.loader.exec_module(module)
    return module


def unload_pure() -> None:
    """Remove the standalone modules and their stub parents."""
    while _STUBS:
        sys.modules.pop(_STUBS.pop(), None)
