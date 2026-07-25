from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any

from EAI_assets.asset_resolver import controller_path, ensure_controller_assets_for_paths


_DYNAMIC_MODULE_PREFIX = "eai_selected_controller_"


def _install_controller_namespaces(relative_path: str) -> None:
    controller_dir = Path(controller_path(""))
    relative_parts = Path(relative_path).parts
    packages = [("EAI_assets.controller", controller_dir)]
    if len(relative_parts) > 1:
        packages.append((f"EAI_assets.controller.{relative_parts[0]}", controller_dir / relative_parts[0]))

    for package_name, package_path in packages:
        if package_name in sys.modules:
            continue
        spec = ModuleSpec(package_name, loader=None, is_package=True)
        spec.submodule_search_locations = [str(package_path)]
        module = ModuleType(package_name)
        module.__package__ = package_name
        module.__path__ = [str(package_path)]
        module.__spec__ = spec
        sys.modules[package_name] = module
        parent_name, _, child_name = package_name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, child_name, module)


@lru_cache(maxsize=None)
def _load_controller_module(relative_path: str) -> Any:
    module_path = Path(controller_path(relative_path))
    if not module_path.exists():
        ensure_controller_assets_for_paths([str(module_path)])

    _install_controller_namespaces(relative_path)
    module_name = _DYNAMIC_MODULE_PREFIX + "_".join(module_path.with_suffix("").parts[-4:])
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load controller module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def load_controller_attr(relative_path: str, attr_name: str) -> Any:
    module = _load_controller_module(relative_path)
    return getattr(module, attr_name)


def clear_controller_module_cache() -> None:
    _load_controller_module.cache_clear()
    for module_name in tuple(sys.modules):
        if module_name.startswith(_DYNAMIC_MODULE_PREFIX):
            sys.modules.pop(module_name, None)
