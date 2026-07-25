from __future__ import annotations

import importlib
from typing import Any


_EXPORTS = {
    "WAREHOUSE_CFG": ("EAI_assets.scene.warehouse", "WAREHOUSE_CFG"),
    "GARDEN_CFG": ("EAI_assets.scene.garden", "GARDEN_CFG"),
    "AIRS_CFG": ("EAI_assets.scene.airs", "AIRS_CFG"),
    "FACTORY_CFG": ("EAI_assets.scene.factory", "FACTORY_CFG"),
    "DESERT_CFG": ("EAI_assets.scene.desert", "DESERT_CFG"),
    "HOSPITAL_CFG": ("EAI_assets.scene.hospital", "HOSPITAL_CFG"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORTS[name]
    value = getattr(importlib.import_module(module_name), attr_name)
    globals()[name] = value
    return value
