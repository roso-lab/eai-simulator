from __future__ import annotations

import importlib
from typing import Any


_EXPORTS = {
    "WAREHOUSE_CFG": ("EAI_assets.scene.warehouse", "WAREHOUSE_CFG"),
    "AIRS_CFG": ("EAI_assets.scene.airs", "AIRS_CFG"),
    "FACTORY_CFG": ("EAI_assets.scene.factory", "FACTORY_CFG"),
    "DESERT_CFG": ("EAI_assets.scene.desert", "DESERT_CFG"),
    "HOSPITAL_CFG": ("EAI_assets.scene.hospital", "HOSPITAL_CFG"),
    "RMUC2024_CFG": ("EAI_assets.scene.rmuc2024", "RMUC2024_CFG"),
    "CITY_CFG": ("EAI_assets.scene.benchmark", "CITY_CFG"),
    "WAREHOUSE_NVD_CFG": ("EAI_assets.scene.warehouse_nvd", "WAREHOUSE_NVD_CFG"),
    "RESTAURANT_CFG": ("EAI_assets.scene.restaurant", "RESTAURANT_CFG"),
    "RESTAURANT_BENCHMARK_CFG": (
        "EAI_assets.scene.restaurant_benchmark",
        "RESTAURANT_BENCHMARK_CFG",
    ),
    "MODERN_HOUSE_NVD_CFG": (
        "EAI_assets.scene.modern_house_nvd",
        "MODERN_HOUSE_NVD_CFG",
    ),
    "FACTORY_BENCHMARK_CFG": ("EAI_assets.scene.benchmark", "FACTORY_BENCHMARK_CFG"),
    "ROOM06_CFG": ("EAI_assets.scene.room06", "ROOM06_CFG"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORTS[name]
    value = getattr(importlib.import_module(module_name), attr_name)
    globals()[name] = value
    return value
